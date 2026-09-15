# -*- coding: utf-8 -*-
"""
`parallel` 真并行的回归测试（HANDOFF 任务清单 §8.27）。

背景
----
`parallel` 的字面承诺是"这些子动作**同时**演"，但 2026-09-15 之前它生成的是
**多条独立的 `self.play(...)`**，也就是顺序播放：

    # 旧行为
    self.play(Create(c1), run_time=2)      # 先演 2 秒
    self.play(Write(t1), run_time=1)       # 再演 1 秒   →  总共 3 秒

真并行应该是**一条** play 带多个动画：

    self.play(Create(c1), run_time=2, Write(t1), run_time=1)   # 同时演，共 2 秒

这不是"优化"，是**语义修复** —— 写的是并行、跑的却是串行，属于契约型静默失效。

测试分两层
----------
1. **静态**：生成的代码里 `self.play(...)` 是不是只有一条（并行），
   以及降级路径（含 `show` / 同元素重复）是否按预期顺序播放。
2. **时长等式**：`_est_action_time()` 的估算**必须等于 manim 实际渲染出来的时长**。
   这一条是关键 —— 它把"渲染端"和"估时端"钉在一起。只断言某一处而不钉等式，
   就会出现"改了一处忘了另一处"，而症状是分镜莫名提前结束或拖一截。
   本文件用 manim 真的把动画跑一遍来量实际时长。
"""

import json
import os
import re
import subprocess
import sys
import textwrap

import pytest

from storyboard import dsl, schema, templates

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = templates.TEMPLATE_REGISTRY


# ==============================================================================
# 工具：把分镜编译成 Manim 源码
# ==============================================================================
def compile_scene(elements, timeline, duration=None):
    scene = {"id": 1, "elements": elements, "timeline": timeline}
    if duration:
        scene["duration"] = duration
    raw = {"title": "parallel 测试", "intent": "propose", "scenes": [scene]}
    sb = schema.validate(raw, REGISTRY)
    from storyboard import renderer
    return renderer.render(sb, use_latex=True)


AXES = {"id": "ax", "kind": "axes", "x_range": [-3, 3, 1], "y_range": [-2, 2, 1]}
TEXT = {"id": "t1", "kind": "text", "content": "你好"}
CIRCLE = {"id": "c1", "kind": "circle", "radius": 1.0, "at": [1, 1]}
DOT = {"id": "d1", "kind": "dot", "at": [0, 0]}


def play_count(src):
    """源码里 self.play( 的出现次数（只数正文，不含 helper）。"""
    return len(re.findall(r"self\.play\(", src))


def parallel_seg(src):
    """
    取出 parallel 那一段生成的代码。

    ⚠️ 不能用 `src.split("# >> parallel")[1]`：外层 `b.act(ac)` 会先打一行
    `# >> parallel`，parallel 分支自己再打一行说明，于是 split 有 3 段，
    `[1]` 拿到的是两行注释之间的空白。第一版就是这么写的，6 个用例集体"失败"
    而实际代码是对的 —— 测试自身的 bug 比被测代码的 bug 更会误导人。
    """
    i = src.index("# >> 真并行") if "# >> 真并行" in src else src.index("# >> 降级为顺序播放")
    return src[i:i + 1200]


# ==============================================================================
# 1. 静态：真并行合成一条 self.play
# ==============================================================================
def test_two_actions_compile_into_one_play():
    """两个子动作 → **一条** self.play（这就是"真并行"的可观测证据）。"""
    src = compile_scene(
        [CIRCLE, TEXT],
        [{"do": "parallel", "actions": [
            {"do": "create", "target": "c1", "run_time": 2},
            {"do": "write", "target": "t1", "run_time": 1},
        ]}],
    )
    # build() 里 show 之类也会产生 play，这里排除元素构建阶段的调用：
    # 只数 parallel 那一段。
    seg = parallel_seg(src)
    assert play_count(seg) == 1, f"parallel 应该只生成一条 self.play，实际：\n{seg[:600]}"
    # 两个动画都要在同一行里
    line = [ln for ln in seg.splitlines() if "self.play(" in ln][0]
    assert "Create(" in line and "Write(" in line, line


def test_each_subaction_keeps_its_own_run_time():
    """
    各自时长必须写在**表达式内部**。

    manim 的 `_setup_animations()` 会把 self.play 的 kwargs 挨个 setattr 到**每个**
    子动画上 —— 所以 run_time 写在外面会被平摊，两个子动作就同长了。
    """
    src = compile_scene(
        [CIRCLE, TEXT],
        [{"do": "parallel", "actions": [
            {"do": "create", "target": "c1", "run_time": 2},
            {"do": "write", "target": "t1", "run_time": 1},
        ]}],
    )
    seg = parallel_seg(src)
    line = [ln for ln in seg.splitlines() if "self.play(" in ln][0]
    # run_time 必须在**构造器括号内**：
    #   Create(_e_c1, run_time=2)      ✓
    #   Create(_e_c1), run_time=2      ✗ 语法错误（位置参数跟在关键字参数后）
    assert "Create(_e_c1, run_time=2)" in line, line
    assert "Write(_e_t1, run_time=1)" in line, line
    # 也不能在外层 self.play 上带 run_time —— manim 会把它 setattr 到每个子动画上
    assert not re.search(r"Create\([^)]*\)\s*,\s*run_time", line), line


def test_nested_parallel_is_flattened():
    """嵌套 parallel 摊平：max(a, max(b,c)) 恒等于 max(a,b,c)。"""
    src = compile_scene(
        [CIRCLE, TEXT, DOT],
        [{"do": "parallel", "actions": [
            {"do": "create", "target": "c1", "run_time": 2},
            {"do": "parallel", "actions": [
                {"do": "write", "target": "t1", "run_time": 1},
                {"do": "fade_in", "target": "d1", "run_time": 3},
            ]},
        ]}],
    )
    seg = parallel_seg(src)
    assert play_count(seg) == 1, seg[:600]
    line = [ln for ln in seg.splitlines() if "self.play(" in ln][0]
    for frag in ("Create(", "Write(", "FadeIn("):
        assert frag in line, f"{frag} 没被摊平进同一条 play：{line}"


# ==============================================================================
# 2. 降级：不能并行时必须**顺序**播放（而不是生成 manim 会报错的代码）
# ==============================================================================
def test_show_forces_sequential_fallback():
    """
    `show` 是即时 add（不是动画），manim 不接受它出现在 self.play 里。
    这种情况整段降级为顺序播放 —— 画面还是对的，只是不并行。
    """
    src = compile_scene(
        [CIRCLE, TEXT],
        [{"do": "parallel", "actions": [
            {"do": "show", "target": "c1"},
            {"do": "write", "target": "t1", "run_time": 1},
        ]}],
    )
    seg = parallel_seg(src)
    assert "降级为顺序播放" in seg, seg[:400]
    assert "self.add(_e_c1)" in seg
    assert play_count(seg) == 1


def test_same_element_twice_falls_back_to_sequential():
    """
    同一个元素被两个子动作碰 → 顺序播放。

    硬塞进同一条 play 里，两个动画谁先生效取决于 manim 内部顺序，结果不确定；
    而"先放大再旋转"和"先旋转再放大"本来就不一样，所以顺序语义才是对的。
    """
    src = compile_scene(
        [CIRCLE, TEXT],
        [{"do": "parallel", "actions": [
            {"do": "scale", "target": "c1", "factor": 2},
            {"do": "rotate", "target": "c1", "angle": 90},
        ]}],
    )
    seg = parallel_seg(src)
    assert "降级为顺序播放" in seg, seg[:400]
    assert play_count(seg) == 2, seg[:400]


def test_multi_target_show_is_expanded_not_broken():
    """`show: {target:[a,b]}` 在 parallel 里要能被接受（拆成两条 add）。"""
    src = compile_scene(
        [CIRCLE, TEXT],
        [{"do": "parallel", "actions": [
            {"do": "show", "target": ["c1", "t1", "t1"]},
        ]}],
    )
    seg = parallel_seg(src)
    assert "self.add(_e_c1)" in seg


# ==============================================================================
# 3. 时长等式：估算 == manim 实际渲染时长（这一条把两端钉死）
# ==============================================================================
PROBE = textwrap.dedent('''
    # -*- coding: utf-8 -*-
    """
    实测两件事（都是"我们对 manim 语义的假设"，一旦 manim 升级改动就会失效）：
      A. 一条 play 里放多个不同时长的动画 → 总时长是 max 还是 sum
      B. `.animate` 链上能不能接 set_run_time()
    """
    import json
    from manim import *

    class Probe(Scene):
        def construct(self):
            a = Square()
            b = Circle()
            c = Triangle()
            self.add(a, b, c)

            t0 = self.renderer.time
            # A：两个不同时长的动画放一条 play
            self.play(Transform(a, Square().scale(1.2), run_time=0.2),
                      Transform(b, Circle().scale(1.2), run_time=0.6))
            t_a = self.renderer.time - t0

            # B：.animate 链 —— 正确的传 run_time 形式（_anim_timed 对 .animate 类动作就是这么做）
            t1 = self.renderer.time
            self.play(c.animate(run_time=0.4).scale(1.2))
            t_b = self.renderer.time - t1

            # C：错误形式（曾用过）—— 用于证明它确实被静默忽略，
            #    这样将来若有人"简化"成 .set_run_time()，这里会立刻指出后果
            t2 = self.renderer.time
            d = Star()
            self.add(d)
            self.play(d.animate.scale(1.2).set_run_time(0.4))
            t_c = self.renderer.time - t2

            print("@@MEASURED " + json.dumps({"a": t_a, "b": t_b, "c": t_c}))
''')


@pytest.mark.skipif(os.environ.get("MSB_SKIP_RENDER_TESTS") == "1",
                    reason="显式跳过渲染类测试")
def test_measured_duration_equals_max_not_sum():
    """
    实测 A：一条 play 带 0.2s / 0.6s 两个动画，总时长应是 **0.6s（max）而不是 0.8s（sum）**。

    这条测的是**我们对 manim 语义的假设本身**，不碰 DSL。
    它一旦红了，说明 manim 改了行为，`_est_action_time()` 的 max 就要重新论证 ——
    而"并行 = max"是整个实现的地基。

    实测 B：`.animate` 链上 `set_run_time()` 是否被接受 —— `_anim_timed()` 对
    scale/rotate/shift 这类动作就是这么注入各自时长的，猜错就会整条渲不出来。
    """
    import tempfile

    tmp = tempfile.mkdtemp(prefix="msb_par_")
    py = os.path.join(tmp, "probe.py")
    with open(py, "w", encoding="utf-8") as f:
        f.write(PROBE)
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "manim", "render", py, "Probe", "-ql",
         "--media_dir", os.path.join(tmp, "media"), "--disable_caching"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=tmp, env=env, timeout=300,
    )
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"@@MEASURED (\{.*\})", out)
    assert m, (
        f"没拿到实测时长（manim 可能直接报错了）。退出码 {r.returncode}，"
        f"输出尾部：\n{out[-1500:]}"
    )
    got = json.loads(m.group(1))
    # 允许一点帧对齐误差（-ql 是 15fps，一帧 0.0667s）
    assert 0.55 <= got["a"] <= 0.75, (
        f"一条 play 里 0.2s + 0.6s 两个动画，实测总时长 {got['a']:.3f}s。"
        f"预期 ~0.6（max）而不是 ~0.8（sum）—— 若接近 0.8 说明 manim 改了语义，"
        f"`_est_action_time()` 的 max 需要重新论证。"
    )
    assert 0.35 <= got["b"] <= 0.55, (
        f"`x.animate(run_time=0.4).scale(1.2)` 实测时长 {got['b']:.3f}s，预期 ~0.4。"
        f"这是 manim 唯一接受的在 .animate 链上指定 run_time 的形式，它变了就说明"
        f"`_anim_timed()` 的注入方式要重做。"
    )
    # C：证明"想当然的写法"确实无声无效 —— 免得有人把它当成更简洁的等价写法改回去
    assert got["c"] > 0.9, (
        f"`x.animate.scale(1.2).set_run_time(0.4)` 实测 {got['c']:.3f}s，"
        f"预期它被静默忽略、仍是默认的 1.0s。"
        f"若这里≈0.4，说明 manim 新版支持了这个写法 —— 那是好消息，"
        f"可以简化 `_anim_timed()`；但要先确认再改。"
    )


def test_animate_actions_get_run_time_in_call_form():
    """
    `.animate` 类动作（scale/rotate/...）在 parallel 里带 run_time 时，
    必须写成 `x.animate(run_time=0.4).scale(2)` ——
    **不能**拼在外层 self.play 上（manim 会 setattr 给每个子动画、抹平各自时长），
    **也不能**接 `.set_run_time(...)`（Mobject 上没有这个方法，会被静默忽略）。
    """
    src = compile_scene(
        [CIRCLE, TEXT],
        [{"do": "parallel", "actions": [
            {"do": "scale", "target": "c1", "factor": 2, "run_time": 0.4},
            {"do": "write", "target": "t1", "run_time": 1.5},
        ]}],
    )
    seg = parallel_seg(src)
    line = [ln for ln in seg.splitlines() if "self.play(" in ln][0]
    assert "_e_c1.animate(run_time=0.4).scale(2)" in line, line
    assert "set_run_time" not in line, f"不该用 set_run_time（会被静默忽略）: {line}"
    assert "Write(_e_t1, run_time=1.5)" in line, line


# ==============================================================================
# 4. 估时函数与渲染端成对
# ==============================================================================
def test_est_time_uses_max_for_parallel():
    """`_est_action_time(parallel)` 取 max —— 与真并行一致。"""
    ac = {"do": "parallel", "actions": [
        {"do": "create", "run_time": 2.0, "target": ["a"]},
        {"do": "write", "run_time": 1.0, "target": ["b"]},
    ]}
    assert dsl._est_action_time(ac) == 2.0


def test_est_time_sum_rule_is_gone():
    """
    防回归：如果哪天有人把 max 改回 sum（或相反），这条会红。

    断言的是"不等于 sum"而不是"等于某个数" —— 因为 sum 与 max 在 2+1 上刚好只差 1，
    容易看错；这里用 3 个子动作把差距拉开。
    """
    ac = {"do": "parallel", "actions": [
        {"do": "create", "run_time": 2.0, "target": ["a"]},
        {"do": "write", "run_time": 1.0, "target": ["b"]},
        {"do": "fade_in", "run_time": 4.0, "target": ["c"]},
    ]}
    assert dsl._est_action_time(ac) == 4.0
    assert dsl._est_action_time(ac) != 7.0, "parallel 的估时必须是 max，不能是 sum"


def test_soft_duration_padding_matches_parallel_max():
    """
    软时长补 wait 的算式：`pad = duration - sum(_est_action_time(每个动作))`。
    parallel 段按 max 参与这个求和 —— 这正是"分镜总长不漂"的关键。
    """
    src = compile_scene(
        [CIRCLE, TEXT],
        [{"do": "parallel", "actions": [
            {"do": "create", "target": "c1", "run_time": 2},
            {"do": "write", "target": "t1", "run_time": 1},
        ]}],
        duration=5.0,
    )
    # 5.0 - max(2, 1) = 3.0，而不是 5.0 - 3 = 2.0
    assert re.search(r"self\.wait\(3\.0\)", src), src[-800:]

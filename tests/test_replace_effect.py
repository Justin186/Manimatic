# -*- coding: utf-8 -*-
"""
`replace` 的四种过渡效果 + **对象身份换绑的哨兵**。

背景（HANDOFF §8.33）：`replace` 连用时，如果每次都拿**原始变量**当替换源，第二次起
源对象已经不在场景里，而 manim 的 `Scene.add_mobjects_from_animations()` 会把
"被动画却不在场景里"的对象**重新加回场景** —— 于是每替换一次就多叠一层旧文字。
这里用**生成代码的文本**把这件事钉死：第二次替换必须用**上一环**的变量。
"""
import re

import pytest

from storyboard import dsl, renderer, schema, templates

REGISTRY = templates.TEMPLATE_REGISTRY


def sb(effect=None, n=1):
    """一个分镜：write 出第一句，再连着 replace n 次（每次都在同一位置换文字）。"""
    elements = [{"id": "info", "kind": "text", "content": "第 1 步",
                 "place": [{"edge": "down", "buff": 1.0}]}]
    timeline = [{"do": "write", "target": "info", "run_time": 0.5}]
    for i in range(n):
        ac = {"do": "replace", "target": "info", "run_time": 0.5,
              "into": {"id": f"i{i + 2}", "kind": "text", "content": f"第 {i + 2} 步",
                       "place": [{"edge": "down", "buff": 1.0}]}}
        if effect is not None:
            ac["effect"] = effect
        timeline.append(ac)
    timeline.append({"do": "wait", "time": 0.5})
    return {"title": "replace 效果", "intent": "propose",
            "scenes": [{"id": 1, "elements": elements, "timeline": timeline}]}


def src(effect=None, n=1):
    """整个生成文件（含注入的 helper）。"""
    return renderer.render(schema.validate(sb(effect, n), REGISTRY), use_latex=False)


def body(effect=None, n=1):
    """只要分镜体（剥掉 HEADER + RUNTIME_HELPER）。"""
    s = src(effect, n)
    marker = "scene 1 | dsl"
    assert marker in s, "找不到分镜体标记，渲染器改了分隔格式？"
    return s.split(marker)[-1]


# ------------------------------------------------------------------ 四种效果
def test_default_is_crossfade():
    """不写 effect 就是交叉淡化（旧的原位淡出、新的原位淡入），不是形变。"""
    s = src()
    assert ("self.play(FadeOut(_e_info, rate_func=_xfade_out), "
            "FadeIn(_e_i2, rate_func=_xfade_in), run_time=0.5)") in s
    assert "ReplacementTransform" not in s


def test_crossfade_halves_never_overlap():
    """
    两半必须**严格先后、零重叠**：旧的淡完了，新的才开始出现。

    这条是用户当场看出来的（原话："淡出没淡出完淡入就来了"）——
    先是全程同时淡（中间 0.4s 两句话糊成一团），改成错开 20% 仍然"没淡完就来了"，
    最后定成各占一半、完全不重叠。所以这里把"任何时刻都不可能同时可见"钉成断言。
    """
    ns = {}
    for fn in ("_xfade_out", "_xfade_in"):
        m = re.search(r"def %s\(t\):\n(?:.*\n)*?    return (.+)\n" % fn, dsl.RUNTIME_HELPER)
        assert m, f"{fn} 没注入到 RUNTIME_HELPER 里"
        # 用同一套语义在本地重算，避免真去 import 生成文件
        ns[fn] = eval(f"lambda t: {m.group(1)}", {"min": min, "max": max})  # noqa: S307
    out, inn = ns["_xfade_out"], ns["_xfade_in"]

    # 端点：旧的 0→1 在前半段走完，新的 0→1 在后半段走完
    assert out(0.0) == 0.0 and out(0.5) == 1.0 and out(1.0) == 1.0
    assert inn(0.0) == 0.0 and inn(0.5) == 0.0 and inn(1.0) == 1.0

    # 零重叠：旧的还没淡完（<1）时，新的必须一点都看不见；反之亦然
    for i in range(101):
        t = i / 100.0
        if out(t) < 1.0:
            assert inn(t) == 0.0, f"t={t} 旧的还没淡完，新的已经出现了（重叠）"
        if inn(t) > 0.0:
            assert out(t) == 1.0, f"t={t} 新的在淡入时旧的还没消失（重叠）"


def test_slide_shifts_both_the_same_way():
    """上滑交叉：两边用**同一个** shift（旧的往上走、新的从下面顶上来）。"""
    s = src("slide")
    assert f"FadeOut(_e_info, shift=UP * {dsl.REPLACE_SLIDE_SHIFT})" in s
    assert f"FadeIn(_e_i2, shift=UP * {dsl.REPLACE_SLIDE_SHIFT})" in s
    assert "FadeOut(_e_info, shift=UP * 0.35), FadeIn(_e_i2, shift=UP * 0.35)" in s


def test_write_fades_old_then_writes_new():
    assert "self.play(FadeOut(_e_info), Write(_e_i2), run_time=0.5)" in src("write")


def test_morph_keeps_old_behaviour():
    assert "self.play(ReplacementTransform(_e_info, _e_i2), run_time=0.5)" in src("morph")


def test_effect_name_is_case_insensitive():
    assert "ReplacementTransform(_e_info, _e_i2)" in src("MORPH")


def test_unknown_effect_is_rejected_with_options():
    with pytest.raises((schema.SchemaError, dsl.DSLError)) as ei:
        src("fade")                      # `fade_in` 是"上屏"动作，不是 replace 的效果
    msg = str(ei.value)
    assert "未知过渡效果" in msg
    for name in dsl.REPLACE_EFFECTS:
        assert name in msg               # 报错要列出可选值，模型才知道改成什么


# ------------------------------------------------------------------ 换绑哨兵
@pytest.mark.parametrize("effect", ["crossfade", "slide", "write", "morph"])
def test_chained_replace_uses_previous_ring(effect):
    """
    连做 3 次替换：第二、三次的**替换源必须是上一环的变量**。

    这是 §8.33 那个 bug 的哨兵 —— 旧写法每次都拿 `_e_info`，于是屏幕上的文字越叠越多。
    """
    b = body(effect, n=3)
    src_of = {
        "morph": lambda a, n: f"ReplacementTransform({a}, {n})",
        "crossfade": lambda a, n: (f"FadeOut({a}, rate_func=_xfade_out), "
                                   f"FadeIn({n}, rate_func=_xfade_in)"),
        "slide": lambda a, n: f"FadeOut({a}, shift=UP * 0.35), FadeIn({n}, shift=UP * 0.35)",
        "write": lambda a, n: f"FadeOut({a}), Write({n})",
    }[effect]
    # 第一环从 info 出发，之后每一环都必须接上一环
    assert src_of("_e_info", "_e_i2") in b
    assert src_of("_e_i2", "_e_i3") in b
    assert src_of("_e_i3", "_e_i4") in b
    # 老写法（每次都从 _e_info 出发）一个字都不许出现 —— 那就是叠影的来源
    assert src_of("_e_info", "_e_i3") not in b
    assert src_of("_e_info", "_e_i4") not in b


def test_effect_stays_idempotent_and_optional():
    """effect 是可选参数：不写就不该出现在规范化结果里（否则第二遍会把"没写"读成"写了"）。"""
    once = schema.validate(sb(), REGISTRY)
    assert "effect" not in once["scenes"][0]["timeline"][1]
    twice = schema.validate(once, REGISTRY)
    assert twice == once

    once = schema.validate(sb("slide"), REGISTRY)
    assert once["scenes"][0]["timeline"][1]["effect"] == "slide"
    assert schema.validate(once, REGISTRY) == once


def test_replace_is_not_parallelisable_so_it_falls_back_safely():
    """replace 在 parallel 里会降级成顺序播放（不能塞进一条 play 的两条动画里）。"""
    elements = [{"id": "a", "kind": "text", "content": "A"},
                {"id": "b", "kind": "text", "content": "B"}]
    timeline = [
        {"do": "write", "target": "a", "run_time": 0.3},
        {"do": "parallel", "actions": [
            {"do": "replace", "target": "a", "into": "b", "run_time": 0.3},
        ]},
        {"do": "wait", "time": 0.3},
    ]
    raw = {"title": "t", "intent": "propose",
           "scenes": [{"id": 1, "elements": elements, "timeline": timeline}]}
    out = renderer.render(schema.validate(raw, REGISTRY), use_latex=False)
    assert "self.play(FadeOut(_e_a, rate_func=_xfade_out), " in out
    assert "FadeIn(_e_b, rate_func=_xfade_in), run_time=0.3)" in out
    assert "ReplacementTransform" not in out      # 默认是交叉淡化，不是形变

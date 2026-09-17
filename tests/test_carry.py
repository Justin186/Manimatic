# -*- coding: utf-8 -*-
"""
`carry`（跨分镜承接）的回归测试。

背景
----
默认每个分镜都从空白画面开始：分镜边界会 `FadeOut(全部) + clear()`，而元素是**每分镜
重新构建**的（`_e_<id> = Table(...)`）。所以同一个东西在下一镜里只能重画一遍 ——
讲连贯过程（数组一直在、指针一步步挪、二分查找）时，观感上等于「从头再来」。

`carry` 就是为这个加的口子。它有两处**必须分开处理**的编排路径，这也是本文件存在的理由：

    render()       一个 Scene 演到底，所有分镜共用同一个 construct() 作用域
                   → 承接元素**一行代码都不能生成**（变量还活着、对象还在屏上）
    render_split() 每个分镜是独立文件、独立 Scene
                   → 承接元素只能重建一次并**立刻 add**（不走入场动画）

统一按一种方式处理都会坏，而且坏得不一样：
    · reuse 模式下重建 → 变量指向一个不在屏上的新对象，后续 move_to 打在空气里；
    · rebuild 模式下不重建 → NameError，整个分镜渲不出来。

还有一条是**规范化幂等**（HANDOFF §8.6 / §8.8）：`carry` 会被展开成「上一镜的完整声明
+ carry: true」，而展开形态是会被喂回校验入口的（replace-scene、validate(validate(x))）。
只认 `{"id":X,"carry":true}` 这个 stub 的话，第二遍就会把规范化自己产出的东西
判成「多写了参数」。这个坑在实现时**当场就踩到了**，所以留了定点用例。
"""

import pytest

from storyboard import dsl, renderer, schema, templates

REG = templates.TEMPLATE_REGISTRY


# ==============================================================================
# 用例脚手架
# ==============================================================================
def sb(second_elements, second_timeline=None):
    """两分镜：分镜 1 建 table + cell_box，分镜 2 按传入的 elements 写。"""
    return {
        "title": "carry 测试",
        "intent": "propose",
        "scenes": [
            {"id": 1, "duration": 4.0,
             "elements": [
                 {"id": "img", "kind": "table", "rows": [["1", "0"], ["0", "1"]]},
                 {"id": "win", "kind": "cell_box", "of": "img",
                  "rows": [1], "cols": [1]},
             ],
             "timeline": [{"do": "create", "target": "img"},
                          {"do": "show", "target": "win"},
                          {"do": "wait", "time": 1.0}]},
            {"id": 2, "duration": 4.0,
             "elements": second_elements,
             "timeline": second_timeline or [
                 {"do": "move_cells", "target": "win", "of": "img",
                  "rows": [2], "cols": [2]},
                 {"do": "wait", "time": 1.0}]},
        ],
    }


CARRY_BOTH = [{"id": "img", "carry": True}, {"id": "win", "carry": True}]


def scene_of(src, sid):
    """取出 render() 产物里某个分镜那一段生成的代码。"""
    marker = f"scene {sid} | dsl"
    assert marker in src, f"源码里找不到 {marker}"
    return src.split(marker)[-1]


def plain_sb():
    """完全没有 carry 的老式分镜（用来盯住"零影响"）。"""
    return {
        "title": "无 carry",
        "intent": "propose",
        "scenes": [
            {"id": 1, "elements": [{"id": "c", "kind": "circle"}],
             "timeline": [{"do": "create", "target": "c"}]},
            {"id": 2, "elements": [{"id": "t", "kind": "text", "content": "hi"}],
             "timeline": [{"do": "write", "target": "t"}]},
        ],
    }


# ==============================================================================
# 1. 正常路径
# ==============================================================================
def test_carry_roundtrip_is_idempotent():
    """承接元素要带上上一镜的**完整声明**（builder 靠它重建），且规范化必须幂等。"""
    once = schema.validate(sb(CARRY_BOTH), REG)
    twice = schema.validate(once, REG)
    assert once == twice, "carry 的规范化不幂等"

    els = once["scenes"][1]["elements"]
    assert [e["id"] for e in els] == ["img", "win"]
    assert all(e.get("carry") for e in els)
    # 关键：不是只剩一个 stub，而是上一镜那份完整声明
    assert els[0]["kind"] == "table" and els[0]["rows"] == [["1", "0"], ["0", "1"]]
    assert els[1]["kind"] == "cell_box" and els[1]["of"] == "img"


def test_reuse_mode_does_not_rebuild_carried_element():
    """一个 Scene 演到底：承接元素**一行代码都不该生成**，直接用上一镜的变量。"""
    src = renderer.render(schema.validate(sb(CARRY_BOTH), REG), use_latex=False)
    assert "_keep = [_e_img, _e_win]" in src, "边界清屏没有排除承接元素"
    assert "self.clear()" not in src, "有 carry 时不能再一刀切 clear()"
    second = scene_of(src, 2)
    assert "_e_img = " not in second, "reuse 模式下重建了承接元素（动作会打在空气里）"
    assert "_e_win = " not in second


def test_rebuild_mode_recreates_and_adds_immediately():
    """拆分渲染：承接元素必须重建并**立刻 add**（不走入场动画），否则就是一片空白。"""
    parts = renderer.render_split(schema.validate(sb(CARRY_BOTH), REG), use_latex=False)
    assert len(parts) == 2
    s2 = parts[1][1]
    assert "_e_img = " in s2, "拆分模式下没有重建承接元素"
    assert "self.add(_e_img)" in s2 and "self.add(_e_win)" in s2
    assert "FadeIn(_e_img" not in s2 and "Create(_e_img" not in s2, "承接元素不该有入场动画"


def test_split_middle_scene_tail_fade_excludes_next_carry():
    """拆分渲染的**中间段**末尾不能把下一镜要承接的元素淡掉（否则两段之间闪一下）。"""
    three = sb(CARRY_BOTH)
    three["scenes"].append({"id": 3, "elements": CARRY_BOTH,
                            "timeline": [{"do": "indicate", "target": "win"}]})
    parts = renderer.render_split(schema.validate(three, REG), use_latex=False)
    mid = parts[1][1]
    assert "_keep = [_e_img, _e_win]" in mid
    assert "self.play(FadeOut(Group(*self.mobjects)), run_time=0.35)" not in mid
    # 最后一段后面没有分镜要承接，仍用原来那段整段淡出
    assert "self.play(FadeOut(Group(*self.mobjects)), run_time=0.35)" in parts[2][1]


def test_carried_element_replaced_last_scene_survives_boundary():
    """
    上一镜**换过脸的**元素被承接时，边界清屏必须留住"屏幕上的那个新对象"。

    背景（2026-09-17，卷积示例现场抓到的）：`replace` 只更新**本镜编译器**里的
    id→变量换绑（`_Builder.alias`），而分镜边界的 `_keep` 是**渲染器**按
    `dsl.var_name(id)` 直接拼的原始变量名。于是

        self.play(FadeOut(_e_tb_out), FadeIn(_e_tb_out1))   # 屏上是 _e_tb_out1
        _keep = [... _e_tb_out]                             # 保留的却是离场的旧对象

    新对象被当成"上一镜的残留"淡掉 —— 装好的内容整块消失，下一镜末尾再 replace
    一次才又出现，看起来就像"这个元素根本没 carry"。修法是 replace 后把原名
    一并重绑（生成 `_e_tb_out = _e_tb_out1`），两边的名字才是同一个东西。
    """
    raw = {
        "title": "carry 一个刚被 replace 过的元素",
        "intent": "propose",
        "scenes": [
            {"id": 1,
             "elements": [{"id": "tb", "kind": "table", "rows": [["", ""], ["", ""]]}],
             "timeline": [
                 {"do": "create", "target": "tb"},
                 {"do": "replace", "target": "tb", "run_time": 0.5,
                  "into": {"id": "tb2", "kind": "table", "rows": [["1", ""], ["", ""]]}},
                 {"do": "wait", "time": 0.5}]},
            {"id": 2,
             "elements": [{"id": "tb", "carry": True}],
             "timeline": [{"do": "indicate", "target": "tb"}]},
        ],
    }
    src = renderer.render(schema.validate(raw, REG), use_latex=False)
    # replace 之后原名必须重绑到新对象上（这一句在分镜 1 的体内）
    assert "_e_tb = _e_tb2" in scene_of(src, 1), "replace 之后没有把原名重绑到新对象"
    # 于是分镜 2 前导的清屏保留的 `_e_tb` 指的就是屏上那个装好数据的新表。
    # ⚠️ 不清 `scene_of(src, 2)`：清屏片段排在 `scene 2 | dsl` 标记**之前**（它是
    # "进这一镜前先做的准备"），按标记切会切掉它 —— 照老用例的写法整份找。
    assert "_keep = [_e_tb]" in src
    assert "_keep = [_e_tb]" in src.split("scene 2 | dsl")[0], "清屏片段不在分镜 2 前导位置"


def test_no_carry_uses_the_original_snippets():
    """
    没有 carry 时，两条路径都必须**原样**沿用改动前那两段代码。

    这是"老分镜零影响"的哨兵：carry 只在 elements 里出现 `carry: true` 时才改变输出，
    其余情况生成的 Manim 源码与改动前逐字节相同。
    """
    sbp = schema.validate(plain_sb(), REG)
    src = renderer.render(sbp, use_latex=False)
    assert ("        if self.mobjects:\n"
            "            self.play(FadeOut(Group(*self.mobjects)), run_time=0.35)\n"
            "            self.clear()\n") in src
    assert "_keep" not in src

    parts = renderer.render_split(sbp, use_latex=False)
    assert "            self.play(FadeOut(Group(*self.mobjects)), run_time=0.35)\n" in parts[0][1]
    assert "_keep" not in parts[0][1]


# ==============================================================================
# 2. 五类必须拦住的错误（每一条拦不住 = 一种静默失效）
# ==============================================================================
def _err(raw, needle):
    with pytest.raises((schema.SchemaError, dsl.DSLError)) as ei:
        schema.validate(raw, REG)
    assert needle in str(ei.value), str(ei.value)
    return str(ei.value)


def test_carry_in_first_scene_is_rejected():
    """分镜 1 没有"上一分镜"可以承接 —— 放过去就是元素凭空消失。"""
    raw = {"title": "x", "intent": "propose",
           "scenes": [{"id": 1, "elements": [{"id": "a", "carry": True}],
                       "timeline": [{"do": "wait", "time": 1.0}]}]}
    _err(raw, "第一个分镜不能写 carry")


def test_carry_of_undeclared_element_is_rejected():
    _err(sb([{"id": "ghost", "carry": True}]), "没有声明过")


def test_carry_of_element_not_on_screen_is_rejected():
    """
    上一镜结束时它必须**真的在屏上**。两种"不在屏上"都要拦：
      · 被 fade_out 掉了；
      · 声明了却从没被任何上屏动作动过（动态元素那个老坑）。
    """
    faded = sb(CARRY_BOTH)
    faded["scenes"][0]["timeline"] = [
        {"do": "create", "target": "img"},
        {"do": "show", "target": "win"},
        {"do": "fade_out", "target": "win"},
        {"do": "wait", "time": 1.0},
    ]
    _err(faded, "并不在屏幕上")

    never = sb(CARRY_BOTH)
    never["scenes"][0]["timeline"] = [{"do": "create", "target": "img"},
                                      {"do": "wait", "time": 1.0}]
    _err(never, "并不在屏幕上")


def test_carry_must_not_restate_params():
    """承接就是"原样还在"：在 carry 里重写参数不会生效，必须报错而不是默默忽略。"""
    _err(sb([{"id": "img", "carry": True, "rows": [["9"]]}]), "多写了")


def test_carry_dependency_must_be_carried_too():
    """只承接 plot 不承接 axes：边界清屏会把 axes 淡掉，画面剩一个飘着的图。"""
    raw = {
        "title": "x", "intent": "propose",
        "scenes": [
            {"id": 1, "elements": [
                {"id": "ax", "kind": "axes", "x_range": [-3, 3, 1],
                 "y_range": [-2, 2, 1]},
                {"id": "p", "kind": "plot", "axes": "ax", "expr": "x**2"}],
             "timeline": [{"do": "create", "target": "ax"},
                          {"do": "create", "target": "p"}]},
            {"id": 2, "elements": [{"id": "p", "carry": True}],
             "timeline": [{"do": "indicate", "target": "p"}]},
        ],
    }
    _err(raw, "没被一起承接")

    # 依赖一起承接就通过
    raw["scenes"][1]["elements"] = [{"id": "ax", "carry": True},
                                    {"id": "p", "carry": True}]
    src = renderer.render(schema.validate(raw, REG), use_latex=False)
    assert "_keep = [_e_ax, _e_p]" in src


def test_expanded_form_is_accepted_but_mismatch_is_rejected():
    """
    展开形态（带 kind 的 carry）必须接受：它是规范化自己的产物，会被喂回校验入口。
    但**内容必须和上一镜逐字段一致** —— 不一致就是"改了参数却不会生效"的静默失效。
    """
    once = schema.validate(sb(CARRY_BOTH), REG)
    # 1) 规范化产物再走一遍：必须通过且完全相同
    assert schema.validate(once, REG) == once

    # 2) 把展开形态里的内容偷偷改掉：必须被拒
    broken = sb(CARRY_BOTH)
    broken["scenes"][1]["elements"] = [
        dict(once["scenes"][1]["elements"][0], rows=[["9", "9"], ["9", "9"]]),
        dict(once["scenes"][1]["elements"][1]),
    ]
    _err(broken, "和上一分镜里的不一致")


# ==============================================================================
# 3. 承接的元素引用了「上一镜的 tracker」（HANDOFF §8.46）
# ==============================================================================
def tracker_sb(second_elements=None):
    """
    两分镜：① 建 axes + tracker `s` + 引用它的点 `P`；② 按传入的 elements 写。

    ⚠️ 分镜 1 必须把 `ax` 也 show 上屏：承接的元素与它依赖的元素都得**真的在屏上**，
    否则会先撞上"承接了个不在屏上的东西"那条错误，把本用例要验的诊断淹掉。
    """
    return {
        "title": "carry + tracker",
        "intent": "propose",
        "scenes": [
            {"id": 1, "duration": 1.0,
             "elements": [
                 {"id": "s", "kind": "tracker", "value": 1.0},
                 {"id": "ax", "kind": "axes", "x_range": [-2, 2, 1],
                  "y_range": [-1, 4, 1]},
                 {"id": "P", "kind": "dot",
                  "at": {"ref": "ax", "x": "$s", "y": "s**2"}},
             ],
             "timeline": [{"do": "show", "target": ["s", "ax", "P"]},
                          {"do": "wait", "time": 1.0}]},
            {"id": 2, "duration": 1.0,
             "elements": ([{"id": "ax", "carry": True}, {"id": "P", "carry": True}]
                          if second_elements is None else second_elements),
             "timeline": [{"do": "wait", "time": 1.0}]},
        ],
    }


def test_carried_element_referencing_prev_tracker_is_explained():
    """
    承接的元素引用了**上一镜的 tracker**、却没承接那个 tracker —— 报错必须看得懂。

    这是唯一一种「声明是从上一镜原样抄来的、名字也没写错，却过不了校验」的形态：
    裸报"表达式 's' 里的 's' 未定义"会把人带去查拼写（照着规格写 few-shot 示例时，
    我们自己就踩了一次，见 HANDOFF §8.46）。
    """
    msg = _err(tracker_sb(), "承接")

    # ① 报的是 carry 的事，不再退化回裸的「未定义」
    assert "tracker" in msg and "'s'" in msg
    assert "未定义" not in msg, "carry 这种形态不该再退化成裸的「未定义」"
    # ② 两条出路都在，且**推荐的（固定值重声明）排在前面**。
    #    顺序反了，模型会挑省事的"把 tracker 也 carry"，把跳变带进成片。
    assert "更稳" in msg and "carry" in msg
    assert msg.index("更稳") < msg.index("也一起承接"), "推荐的出路没有排在前面"
    # ③ 一个点的 x / y 是两条表达式，只该报一条（去重，别浪费回灌预算）
    assert msg.count("两条出路") == 1, "carry 诊断重复报了"

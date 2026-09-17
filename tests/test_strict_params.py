# -*- coding: utf-8 -*-
"""
"多写的参数"必须报错，不能静默丢弃（2026-09-17 修）。

背景
----
`_norm_element` / `_norm_action` 原来都只读自己认识的键，**多写的键被静静丢掉**：
元素侧收进 `out["_dropped"]`（而那个字段除它自己之外没有任何地方读），
动作侧连记都不记。后果是同一种静默失败：

    {"kind": "text", "content": "…", "rotate": 30}   → rotate 不是 text 的参数
    {"do": "create", "target": "x", "duration": 2}   → 想写时长，名字写错

前者让人以为"转过 30 度"、后者让人以为"动画 2 秒"，画面却是原样。
"画面能出、内容是错的"正是本项目最忌讳的一类，所以现在**直接报错**，
并把"这个 kind/do 支持哪些参数"一起回灌给模型（它能一次改对）。

⚠️ 敢改成硬报错的前提是**先量过影响面**（这一步不能省，见下面的"回归"）：
   · 25 个 examples：0 处多写参数；
   · 调用留档 49 条成功回复 / 992 个元素：0 处；
   · 启用后再拿 25 个示例 + 47 条真实回复整体回归：**新规则拒绝 0 份**。
  也就是说它只是把一条"潜伏的静默失败"变成了响亮的报错，没有误伤存量数据。

名单少写一个键的代价是"合法分镜被误拒"（白烧一轮 LLM 重试，比漏报更坏），
所以 `_ACTION_KEYS` 与 `ACTION_SPEC` 的**一一对应**由本文件最后一条测试守着。
"""

import pytest

from storyboard import dsl, schema, templates

REG = templates.TEMPLATE_REGISTRY


def sb(elements, timeline=None):
    return {"title": "t", "intent": "propose",
            "scenes": [{"id": 1, "duration": 4, "elements": elements,
                        "timeline": timeline or [{"do": "wait", "time": 0.5}]}]}


def err(elements, timeline=None):
    with pytest.raises(Exception) as e:
        schema.validate(sb(elements, timeline), REG)
    return str(e.value)


# ==============================================================================
# 元素侧
# ==============================================================================
def test_unknown_element_param_is_rejected():
    msg = err([{"id": "t", "kind": "text", "content": "标题", "rotate": 30}])
    assert "rotate" in msg
    assert "content" in msg, "报错要列出这个 kind 支持的参数（模型才能一次改对）"


def test_unknown_element_param_points_to_place():
    """`rotate` 其实是 place 的键 —— 报错要指路，否则模型只会删掉它、画面还是没转。"""
    msg = err([{"id": "t", "kind": "text", "content": "标题", "rotate": 30}])
    assert "place" in msg


def test_underscore_keys_are_comments():
    """下划线开头的是给人看的注释（examples 在用 `_note` / `_comment`），照旧忽略。"""
    out = schema.validate(sb([{"id": "t", "kind": "text", "content": "标题",
                               "_note": "这一镜的点题句"}]), REG)
    assert out["scenes"][0]["elements"][0]["content"] == "标题"


def test_universal_element_keys_still_ok():
    """place / live 是通用键，不算多写。"""
    schema.validate(sb([{"id": "t", "kind": "text", "content": "标题", "live": True,
                         "place": [{"corner": "ur", "buff": 1.0}]}]), REG)


def test_dropped_is_always_empty_now():
    """`_dropped` 保留只是为了规范化产物形状稳定（幂等测试逐字段比对）。"""
    out = schema.validate(sb([{"id": "t", "kind": "text", "content": "标题"}]), REG)
    assert out["scenes"][0]["elements"][0]["_dropped"] == []


# ==============================================================================
# 动作侧
# ==============================================================================
def test_unknown_action_param_is_rejected():
    msg = err([{"id": "t", "kind": "text", "content": "标题"}],
              [{"do": "create", "target": "t", "duration": 2}])
    assert "duration" in msg
    assert "没有附加参数" in msg or "run_time" in msg


def test_action_extra_params_are_allowed():
    """各自合法的附加参数不能被误拒（这是这张名单最容易写漏的地方）。"""
    schema.validate(sb(
        [{"id": "t", "kind": "text", "content": "标题"},
         {"id": "u", "kind": "text", "content": "另一句"},
         {"id": "ax", "kind": "axes"},
         {"id": "p", "kind": "plot", "axes": "ax", "expr": "x"}],
        [{"do": "create", "target": "t", "run_time": 2, "lag_ratio": 0.3},
         {"do": "fade_in", "target": "u", "shift": "down"},
         {"do": "set_color", "target": "t", "color": "RED"},
         {"do": "stretch", "target": "t", "factor": 1.2, "dim": "y"},
         {"do": "move_along", "target": "t", "path": "p"},
         {"do": "scale", "target": "t", "factor": 0.8},
         {"do": "rotate", "target": "t", "angle": 45},
         {"do": "wait", "time": 1.0}]),
        REG)


def test_move_to_legacy_alias_still_works():
    """`to=` 是代码里**故意容忍**的历史别名（point / to / target_point），别删。"""
    schema.validate(sb([{"id": "t", "kind": "text", "content": "标题"}],
                       [{"do": "write", "target": "t"},
                        {"do": "move_to", "target": "t", "to": [1, 1]},
                        {"do": "move_to", "target": "t", "point": [2, 2]}]),
                    REG)


# ==============================================================================
# 元测试：名单不许漏（漏一个键 = 合法分镜被误拒）
# ==============================================================================
def test_action_keys_cover_every_action():
    """
    `_ACTION_KEYS` 必须与 `ACTION_SPEC` **一一对应**。

    这是本文件最重要的一条：新增一个动作却忘了登记，会让该动作的**每一次使用**
    都报"没有这些参数" —— 报错信息还是对的、指向还是清楚的，但合法分镜全被拒。
    这种错一旦上线，症状是"某个动作永远用不了"，排查起来要翻半天。
    """
    assert set(dsl._ACTION_KEYS) == set(dsl.ACTION_SPEC), (
        f"两边不一致：只在 ACTION_SPEC={sorted(set(dsl.ACTION_SPEC) - set(dsl._ACTION_KEYS))}，"
        f"只在 _ACTION_KEYS={sorted(set(dsl._ACTION_KEYS) - set(dsl.ACTION_SPEC))}"
    )

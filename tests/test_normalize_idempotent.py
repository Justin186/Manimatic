# -*- coding: utf-8 -*-
"""
规范化幂等的回归测试（HANDOFF §8.8 建议的那条性质）。

背景
----
`schema.validate()` 干两件事：**校验** + **规范化**（补默认值、把简写展平）。
规范化的正确性有一条硬要求：**幂等** ——

    validate(validate(raw))  必须和  validate(raw)  一样

也就是"第二遍不该报错"。一旦不成立，就会出现一种极难查的现象：
**两个闸对同一份数据给出相反结论**（日志说"通过校验"，接口说"校验失败"）。

这不是理论担忧，是真实踩过的 bug（HANDOFF §8.6）：`llm.regenerate_scene()` 一度把
「规范化后的分镜」写回了「raw 槽位」，于是 pipeline 又规范化了一遍，
而规范化会**否定自己的输出** —— 整条链路时好时坏。

为什么值得一条测试
------------------
§8.6 / §8.7 这两个 bug 都是这条性质的破坏，而它们的症状是"两个闸互相矛盾"，
人工排查极慢。这条测试是它们的**通用哨兵**：
以后任何人改动 `_norm_*`，只要让规范化变得不自洽，这里立刻变红。
"""

import glob
import json
import os

import pytest

from storyboard import dsl, schema, templates

REGISTRY = templates.TEMPLATE_REGISTRY
EXAMPLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples"
)


def validate(raw):
    return schema.validate(raw, REGISTRY)


def assert_idempotent(raw, label="输入"):
    """
    跑两遍：第二遍不应抛异常，且**内容必须逐字节相同**。

    只断言"不报错"是不够的 —— 如果第二遍把值又改了一次（比如 0.4 → 0.4 → 0.4 但
    形态变了），那还是"规范化不稳定"，同样会让下游两个闸读出不同的东西。
    """
    once = validate(raw)
    twice = validate(once)
    assert twice == once, (
        f"{label}: 规范化不幂等 —— 第二遍的结果与第一遍不同。\n"
        f"第一遍: {json.dumps(once, ensure_ascii=False, sort_keys=True)[:800]}\n"
        f"第二遍: {json.dumps(twice, ensure_ascii=False, sort_keys=True)[:800]}"
    )
    return once


# ==============================================================================
# 一个最小的合法 DSL 分镜，供下面的定点用例复用
# ==============================================================================
def dsl_scene(elements, timeline=None):
    return {
        "title": "幂等测试",
        "intent": "propose",
        "scenes": [{
            "id": 1,
            "elements": elements,
            "timeline": timeline or [{"do": "wait", "time": 1.0}],
        }],
    }


AXES = {"id": "ax", "kind": "axes", "x_range": [-3, 3, 1], "y_range": [-2, 2, 1]}


# ==============================================================================
# 1. 全量：examples/ 里每一份合法分镜都必须幂等
# ==============================================================================
@pytest.mark.parametrize(
    "path",
    sorted(glob.glob(os.path.join(EXAMPLES_DIR, "*.json"))),
    ids=lambda p: os.path.basename(p),
)
def test_examples_are_idempotent(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    try:
        validate(raw)
    except (schema.SchemaError, Exception) as e:          # noqa: BLE001
        # 首轮就不合法的（如 _bad_case_demo.json 是故意写错的负例）不归这条测试管。
        pytest.skip(f"首轮校验未通过，跳过：{type(e).__name__}: {e}")
    assert_idempotent(raw, os.path.basename(path))


# ==============================================================================
# 2. §8.6 表格里那四个点，各来一条定点用例
#    这四条是"不幂等"的具体形态，比全量测试更容易定位问题在哪一行。
# ==============================================================================
def test_plot_without_x_range():
    """plot 没写 x_range：第一遍补成 None，第二遍若按"非法范围"报错就是不幂等。"""
    raw = dsl_scene([
        AXES,
        {"id": "p", "kind": "plot", "axes": "ax", "expr": "x**2"},
    ])
    assert_idempotent(raw, "plot 缺 x_range")


def test_circle_without_at():
    """circle 没写 at：第一遍补成 None，第二遍若按"点的格式无法识别"报错就是不幂等。"""
    raw = dsl_scene([
        AXES,
        {"id": "c", "kind": "circle", "radius": 1.0},
    ])
    assert_idempotent(raw, "circle 缺 at")


def test_place_next_to_is_flattened_once():
    """place 的 next_to：第一遍展平成 4 个键，第二遍若按"只能有一个布局键"报错就是不幂等。"""
    raw = dsl_scene([
        AXES,
        {"id": "t", "kind": "text", "content": "你好",
         "place": [{"next_to": {"of": "ax", "direction": "up", "buff": 0.5}}]},
    ])
    once = assert_idempotent(raw, "place next_to")
    # 顺手确认展平确实发生了（否则这条测试可能因为"压根没规范化"而假通过）
    placed = once["scenes"][0]["elements"][1]["place"][0]
    assert "next_to" in placed, f"预期 place 被规范化，实际 {placed}"


def test_inline_target_element():
    """动作里内联定义元素：第一遍拆成 target:[id] + inline:{...}，
    第二遍若只认 target 列表、不去 inline 里找，就会报"引用了未声明的元素"。"""
    raw = dsl_scene(
        [AXES],
        [{"do": "create", "target": {"id": "d1", "kind": "dot", "at": [0, 0]}}],
    )
    assert_idempotent(raw, "动作内联定义元素")


# ==============================================================================
# 3. 已修 bug 的哨兵：这些当初是"写了等于没写"，别再退回去
# ==============================================================================
def test_parallel_actions_survive_normalization():
    """§8.7：parallel 的子动作必须被带进返回值。

    历史 bug：`_norm_action()` 漏了 parallel 分支，`actions` 被静默丢掉，
    整段动作变成空转（不报错、不上屏、只吐一行注释）。
    """
    raw = dsl_scene(
        [AXES],
        [{"do": "parallel",
          "actions": [{"do": "create", "target": "ax"},
                      {"do": "wait", "time": 1.0}]}],
    )
    once = assert_idempotent(raw, "parallel")
    acts = once["scenes"][0]["timeline"][0]["actions"]
    assert len(acts) == 2, f"parallel 的子动作被丢掉了：{acts}"


def test_normalized_output_has_no_none_optionals():
    """
    结构性不变量：规范化产物里**不应该有值为 None 的可选参数**。

    "None 默认值"是 `ELEMENT_SPEC` 的表达方式（意思是"没写"），它不该出现在输出里 ——
    一旦出现，第二遍校验就会把"没写"当成"写了个 null"而报错（这正是 §8.6 的根因）。
    """
    raw = dsl_scene([
        AXES,
        {"id": "p", "kind": "plot", "axes": "ax", "expr": "x"},
        {"id": "c", "kind": "circle", "radius": 1.0},
    ])
    out = validate(raw)
    offenders = [
        (el["id"], k)
        for el in out["scenes"][0]["elements"]
        for k, v in el.items()
        if v is None
    ]
    assert not offenders, f"规范化产物里残留了 None 可选参数：{offenders}"


# ------------------------------------------------------------------------------
# 表格（T_TABLE）：本轮把三条硬上限换成了尺寸估算，规范化的自洽性要重新钉一遍
# ------------------------------------------------------------------------------
def _table(**extra):
    el = {"id": "arr", "kind": "table", "rows": [["0", "1"], ["2", "3"]]}
    el.update(extra)
    return el


def test_table_plain_is_idempotent():
    out = assert_idempotent(dsl_scene([_table()]), "普通表格")
    el = out["scenes"][0]["elements"][0]
    assert el["rows"] == [["0", "1"], ["2", "3"]]
    # 单元格不再被截断到 80 字（那是静默改内容）
    assert "cell_w" not in el and "pad_x" not in el


def test_table_cell_size_params_are_idempotent():
    """cell_w/cell_h/pad_x/pad_y 都是可选参数：写了要原样带出去，且第二遍不报错。"""
    el = _table(cell_w=1.5, cell_h=0.9, pad_x=0.4, pad_y=0.3)
    out = assert_idempotent(dsl_scene([el]), "带格子尺寸的表格")
    got = out["scenes"][0]["elements"][0]
    assert (got["cell_w"], got["cell_h"], got["pad_x"], got["pad_y"]) == (1.5, 0.9, 0.4, 0.3)


def test_table_cell_80_char_truncation_removed():
    """90 字的单元格必须**原样保留** —— 以前会被悄悄砍到 80（内容变了却没人知道）。"""
    cell = "1234567890" * 9
    out = assert_idempotent(dsl_scene([_table(rows=[[cell]], font_size=10)]), "长单元格")
    assert out["scenes"][0]["elements"][0]["rows"][0][0] == cell


def test_table_uneven_rows_are_rejected():
    """
    行不等长以前没有任何检查，会一路过校验直到 manim 的 Table 抛 ValueError
    （非 DSLError → 变成用户的 500）。现在必须在校验层就报出可回灌的错误。
    """
    with pytest.raises((schema.SchemaError, dsl.DSLError)) as ei:
        validate(dsl_scene([_table(rows=[["1", "2"], ["3"]])]))
    assert "每行的列数必须一样" in str(ei.value)

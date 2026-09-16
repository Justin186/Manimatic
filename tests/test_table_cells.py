# -*- coding: utf-8 -*-
"""
表格的格子尺寸参数、以及"硬上限换成尺寸估算"之后的行为。

两组断言各自盯一件事：

1. **不给新参数时生成代码逐字节不变** —— 老分镜的产物一个字都不能动，
   否则增量渲染缓存全失效、也没法证明这次改动没碰别的路径。
2. 给了参数才走 `_table(...)`；而"放不下"由 `metrics` 估出来**硬拒并回灌**，
   不再靠"最多 8 行 8 列""格号最多 6 个""单元格最多 80 字"这类与尺寸无关的条数上限。
"""
import pytest

from storyboard import dsl, metrics, renderer, schema, templates

REGISTRY = templates.TEMPLATE_REGISTRY


def sb(elements, timeline=None):
    return {"title": "表格测试", "intent": "propose",
            "scenes": [{"id": 1, "elements": elements,
                        "timeline": timeline or [{"do": "create", "target": elements[0]["id"]},
                                                 {"do": "wait", "time": 0.5}]}]}


def table(**extra):
    el = {"id": "arr", "kind": "table", "font_size": 20,
          "rows": [["0", "1"], ["2", "3"]]}
    el.update(extra)
    return el


def full(elements, timeline=None):
    """整个生成文件（含注入的 RUNTIME_HELPER）。"""
    return renderer.render(schema.validate(sb(elements, timeline), REGISTRY), use_latex=False)


def compile_(elements, timeline=None):
    """只要分镜体 —— helper 的 docstring 里也有关键词（h_buff/cell_w），
    不剥掉的话断言会被它们蒙混过去。"""
    s = full(elements, timeline)
    marker = "scene 1 | dsl"
    assert marker in s, "找不到分镜体标记，渲染器改了分隔格式？"
    return s.split(marker)[-1]


def err(elements, timeline=None):
    with pytest.raises((schema.SchemaError, dsl.DSLError)) as ei:
        compile_(elements, timeline)
    return str(ei.value)


# ------------------------------------------------------------------ 代码生成
def test_no_new_params_keeps_old_codegen():
    """没有 cell_w/cell_h/pad_x/pad_y 时，生成的必须还是原来那一行 MobjectTable。"""
    s = compile_([table()])
    assert "MobjectTable([" in s
    assert "_table([" not in s
    assert "h_buff" not in s and "v_buff" not in s
    assert "element_to_mobject" not in s


def test_cell_w_injects_uniform_cells():
    """给了 cell_w 就走 `_table` 统一入口，并把**算好的紧凑留白**显式传下去。"""
    s = compile_([table(cell_w=1.2)])
    assert "_table([" in s and "cell_w=1.2" in s
    assert f"pad_x={metrics.PAD_X_UNIFORM}" in s      # 不是默认的 1.3
    assert "MobjectTable([" not in s


def test_pad_only_does_not_force_uniform_size():
    """只给 pad_x 时不该套定尺框：格子宽度仍由内容决定，只是留白变了。"""
    s = compile_([table(pad_x=0.4)])
    assert "_table([" in s and "pad_x=0.4" in s and "cell_w" not in s
    assert f"pad_y={metrics.PAD_Y_DEFAULT}" in s      # 没要求统一尺寸 → 留白仍是引擎默认


def test_cell_h_and_pad_y_pass_through():
    s = compile_([table(cell_h=0.9, pad_y=0.3)])
    assert "cell_h=0.9" in s and "pad_y=0.3" in s


def test_runtime_helpers_are_injected():
    """三个 helper 必须真的进到生成文件里（不然运行起来就是 NameError）。"""
    s = full([table(cell_w=1.2)])
    assert "def _table_cell(" in s
    assert "def _table(" in s
    assert "def _fit(m, name=" in s


def test_fit_now_carries_element_id():
    """_fit 带上了元素 id：真缩了要打 [WARN] 点名是谁（静默缩小的日子到此为止）。"""
    s = compile_([{"id": "t", "kind": "text", "content": "一句话"}])
    assert "_fit(_e_t, 't')" in s


# ------------------------------------------------------------------ 上限移除
def test_more_than_8_columns_is_allowed_now():
    """10 列单数字：以前被"每行最多 8 列"硬拒，现在按尺寸估算是放得下的。"""
    rows = [list("0123456789"), list("1357924680")]
    out = schema.validate(sb([table(rows=rows, font_size=14, pad_x=0.35)]), REGISTRY)
    assert len(out["scenes"][0]["elements"][0]["rows"][0]) == 10


def test_cell_box_may_span_more_than_6_cells():
    """`[1..8]` 选一整行：以前被"格号最多 6 个"硬拒（跟框会不会出界毫无关系）。"""
    elements = [
        table(rows=[list("0123456789"), list("1357924680")], font_size=14, pad_x=0.35),
        {"id": "win", "kind": "cell_box", "of": "arr",
         "rows": [1, 2], "cols": [1, 2, 3, 4, 5, 6, 7, 8], "color": "BLUE"},
    ]
    out = schema.validate(sb(elements), REGISTRY)
    assert len(out["scenes"][0]["elements"][1]["cols"]) == 8


def test_cell_text_is_no_longer_silently_truncated():
    """单元格最多 80 字那条**是静默改内容**（写 90 字会被砍掉 10 字），必须去掉。"""
    cell = "1234567890" * 9           # 90 个字符
    out = schema.validate(sb([table(rows=[[cell]], font_size=10)]), REGISTRY)
    assert out["scenes"][0]["elements"][0]["rows"][0][0] == cell


def test_rows_must_have_equal_length():
    """
    以前没有这条：行不等长会一路过校验，直到 manim 的 Table 抛
    `ValueError: Not all rows in table have the same length.` —— 非 DSLError，
    穿透校验层变成用户的 500。
    """
    msg = err([table(rows=[["1", "2"], ["3"]])])
    assert "每行的列数必须一样" in msg


# ------------------------------------------------------------------ 出界判定
def test_wide_table_is_rejected_with_actionable_numbers():
    """超宽必须被拒，且报错里要有：估算尺寸 / 可用范围 / 超出多少 / 具体改法。"""
    rows = [[f"单元格{i}{j}" for j in range(10)] for i in range(2)]
    msg = err([table(rows=rows, font_size=30)])
    assert "放不下" in msg
    assert "估算" in msg and "可用 12.40 × 6.60" in msg
    assert "超宽" in msg
    assert "去掉" in msg and "列" in msg          # "去掉 N 列（现在 10 列）"
    assert "font_size 从 30 降到" in msg
    assert "pad_x" in msg                          # 调留白也是一条改法


def test_narrow_table_with_tight_padding_passes():
    """同样 10 列，把字号和留白调小就放得下 —— 证明"能不能过"真的取决于尺寸。"""
    rows = [[f"单元格{i}{j}" for j in range(10)] for i in range(2)]
    out = schema.validate(sb([table(rows=rows, font_size=10, pad_x=0.2)]), REGISTRY)
    assert out["scenes"][0]["elements"][0]["font_size"] == 10


def test_cell_w_too_small_is_rejected():
    """cell_w 比内容还窄 → 格子会被内容撑大、"所有格子一样宽"的承诺就悄悄失效了。"""
    msg = err([table(rows=[["1", "很宽的一格内容"], ["2", "3"]], cell_w=0.5)])
    assert "cell_w" in msg and "定小了" in msg
    assert "第 1 行第 2 列" in msg          # 点名是哪一格
    assert "提到" in msg


def test_long_text_is_rejected_with_wrap_hint():
    """超长 text：报错要给出"拆行 / 降字号"两条可执行改法。"""
    long_text = "这是一段非常长的说明文字，" * 4
    msg = err([{"id": "t", "kind": "text", "content": long_text, "font_size": 30}])
    assert "放不下" in msg
    assert "拆成两行" in msg or "拆短" in msg
    assert "font_size 从 30 降到" in msg


def test_multiline_text_passes_where_single_line_fails():
    """换行真的能救：同样的字，拆成 3 行就放得下了（这也是报错里那条建议）。"""
    seg = "这是一段比较长的说明文字"
    long_one_line = seg * 3
    with pytest.raises((schema.SchemaError, dsl.DSLError)):
        compile_([{"id": "t", "kind": "text", "content": long_one_line, "font_size": 30}])
    out = schema.validate(sb([{"id": "t", "kind": "text", "font_size": 30,
                               "content": seg + "\\n" + seg + "\\n" + seg}]), REGISTRY)
    assert out["scenes"][0]["elements"][0]["content"].count("\\n") == 2


def test_generous_capacity_is_not_rejected():
    """别把正常尺寸的东西也拒了：8 列小数字表（就是二分查找那题的形态）必须过。"""
    rows = [["0", "1", "2", "3", "4", "5", "6", "7"],
            ["1", "3", "5", "7", "9", "11", "13", "15"]]
    out = schema.validate(sb([table(rows=rows, font_size=26)]), REGISTRY)
    assert out["scenes"][0]["elements"][0]["font_size"] == 26

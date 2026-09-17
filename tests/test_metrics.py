# -*- coding: utf-8 -*-
"""
版面估算器的纯函数测试（**不 import manim**，毫秒级）。

这里测的是"算术本身对不对"，不是"估算准不准" —— 准不准由
`test_metrics_calibration.py`（真渲染，默认跳过）用"估算 ≥ 实测"这条不变式盯。
两边分工明确：这边能在每次改代码时跑，那边只在标定漂移时才需要跑。
"""
import os

import pytest

from storyboard import dsl, metrics


def test_budget_matches_frame_and_runtime_fit():
    """
    画面预算 = 帧尺寸 - 边距，且必须与**注入到生成代码里**的那份 _fit 常量一致。

    运行时的边距写在 `dsl.RUNTIME_HELPER` 这个字符串里（生成器不能 import metrics），
    所以只能从字符串里抠出来比 —— 两处漂移的后果是"校验说放得下、渲染时被悄悄缩小"，
    属于最典型的静默失效，必须钉住。
    """
    import re

    assert metrics.BUDGET_W == pytest.approx(12.4, abs=0.01)
    assert metrics.BUDGET_H == pytest.approx(6.6, abs=0.01)
    assert metrics.FRAME_W == pytest.approx(14.2222, abs=0.001)

    mx = float(re.search(r"_FRAME_MARGIN_X = ([\d.]+)", dsl.RUNTIME_HELPER).group(1))
    my = float(re.search(r"_FRAME_MARGIN_Y = ([\d.]+)", dsl.RUNTIME_HELPER).group(1))
    assert metrics.FRAME_W - 2 * mx == pytest.approx(metrics.BUDGET_W, abs=0.01)
    assert metrics.FRAME_H - 2 * my == pytest.approx(metrics.BUDGET_H, abs=0.01)


def test_text_width_is_monotonic():
    a = metrics.est_text_w("中", 30)
    b = metrics.est_text_w("中中", 30)
    c = metrics.est_text_w("中中", 60)
    assert a < b < c


def test_cjk_is_wider_than_digit():
    assert metrics.est_text_w("中", 30) > metrics.est_text_w("8", 30)
    assert metrics.est_text_w("W", 30) > metrics.est_text_w("i", 30)


def test_narrow_lowercase_is_not_priced_like_m():
    """窄字身不能按 m 的系数算 —— 否则一句英文的估算会肥出两倍（实测过 2.08 倍）。"""
    narrow = metrics.est_text_w("iiiiiiiiii", 30)
    wide = metrics.est_text_w("mmmmmmmmmm", 30)
    assert narrow < wide * 0.6


def test_newline_splits_lines():
    """`\\n`（字面量反斜杠 + n，与 rich() 同口径）要拆行：宽取最宽一行、高按行数累加。"""
    two = "第一行很长很长很长\\n短"
    one_line_w = metrics.est_text_w(two, 30)
    assert one_line_w == pytest.approx(metrics.est_text_w("第一行很长很长很长", 30))
    assert metrics.est_text_h(two, 30) > metrics.est_text_h("第一行很长很长很长", 30)
    assert metrics.est_text_h(two, 30) == pytest.approx(
        metrics.est_text_h("第一行很长很长很长", 30) + metrics.LINE_BUFF + metrics.LINE_H * 30, abs=0.01
    )


def test_tex_estimation_strips_commands():
    """`\\lfloor \\log_2 n \\rfloor + 1 = 4` 源码 24 字符，画出来只有 10 个字形左右 ——
    按源码长度估会把本来放得下的公式误判成出界。"""
    src = r"\lfloor \log_2 n \rfloor + 1 = 4"
    assert metrics.est_tex_w(src, 40) < len(src) * metrics.K_TEX_GLYPH * 40 * 0.6
    # 但同一段公式，命令越多"按字符数硬乘"的浪费越明显：剥命令后必须更小
    assert metrics.est_tex_w(r"\frac{1}{2}", 40) < metrics.est_tex_w("1234567890", 40)


def test_table_width_grows_with_columns_and_padding():
    rows = [["0", "1", "2"], ["3", "4", "5"]]
    base = metrics.est_table_size(rows, 24)
    wider = metrics.est_table_size([r + ["6"] for r in rows], 24)
    tighter = metrics.est_table_size(rows, 24, pad_x=0.4)
    assert base["w"] < wider["w"]
    assert tighter["w"] < base["w"]
    assert base["n_cols"] == 3 and base["n_rows"] == 2
    # 默认留白（2026-09-17 从 manim 原生的 1.3/0.8 降到 0.7/0.45：原生值太松，
    # 格子里大半是空白 —— 用户反馈"表格太大、单元格也太大"）
    assert base["pad_x"] == metrics.PAD_X_DEFAULT == 0.7
    assert base["pad_y"] == metrics.PAD_Y_DEFAULT == 0.45


def test_table_cell_w_makes_every_column_equal():
    """给了 cell_w 就是"所有格子一样宽"：表宽 = 列数 × cell_w（与内容无关）。"""
    rows = [["1", "2345678901234"], ["2", "3"]]
    fixed = metrics.est_table_size(rows, 24, cell_w=1.5)
    assert fixed["w"] == pytest.approx(2 * 1.5)
    # need_cell_w 是"内容+留白"的下限，报表错文案要用它
    assert fixed["need_cell_w"] > 1.5


def test_square_table_makes_cells_square():
    """
    `square: true`：格子取正方形（数字网格 / 矩阵 / 卷积核这类"一格一个数"的表）。
    边长 = max(内容宽+pad_x, 内容高+pad_y) —— 校验层估算与渲染端 `_table()` 是同一套逻辑。
    """
    rows = [["1", "2"], ["3", "4"]]
    sq = metrics.est_table_size(rows, 30, square=True)
    plain = metrics.est_table_size(rows, 30)
    assert sq["w"] == pytest.approx(sq["h"]), "2×2 的正方形格子表应当宽高相等"
    assert sq["h"] > plain["h"], "单格被撑成正方形后，整表比自然排版高"
    # ⚠️ 「格子是正方形」不等于「整张表是正方形」：非方阵时宽:高 = 列数:行数。
    # （第一版就写错了这条，被自己抓出来。）
    for rows_, nc, nr in (([["1"]], 1, 1), ([["1"], ["2"]], 1, 2), ([["1", "2"]], 2, 1)):
        e = metrics.est_table_size(rows_, 30, square=True)
        assert e["w"] / nc == pytest.approx(e["h"] / nr), rows_


def test_fits_and_shrink_factor():
    assert metrics.fits(1.0, 1.0)
    # 判定线含 OVERFLOW_SLACK：估算是有意偏大的上界，贴着预算就拒不等于真放不下
    assert metrics.fits(metrics.BUDGET_W, 1.0)
    assert metrics.fits(metrics.BUDGET_W * metrics.OVERFLOW_SLACK, 1.0)
    assert not metrics.fits(metrics.BUDGET_W * metrics.OVERFLOW_SLACK * 1.01, 1.0)
    assert metrics.shrink_factor(metrics.BUDGET_W, metrics.BUDGET_H) == pytest.approx(1.0)
    assert metrics.shrink_factor(metrics.BUDGET_W * 2, 1.0) == pytest.approx(2.0)


def test_overflow_slack_covers_the_measured_upper_bound_margin():
    """
    余量必须够罩住"已知放得下、但会被上界误判"的那个真实反例：

        二分查找那张 8 列表，实际渲染宽 12.03，估算 12.80（实测于 2026-09-16）

    余量太小 → 用户重渲同一个分镜会突然"校验失败"；余量太大 → 真超宽的会被放过去。
    """
    real, est = 12.03, 12.80
    assert est / metrics.BUDGET_W <= metrics.OVERFLOW_SLACK, "余量不够，会误拒那张真实放得下的表"
    assert metrics.fits(est, 2.30)
    assert not metrics.fits(est * 1.6, 2.30)          # 真的超宽（估算是预算的 1.65 倍）必须仍被拒
    assert real <= metrics.BUDGET_W                    # 顺带确认：它其实本来就在预算内


def test_effective_pads_switch_to_tight_when_cell_size_is_requested():
    """
    给了 cell_w 就必须换成紧凑留白：`cell_w` 是"格子最终宽度"，而留白 1.3 意味着
    内容区只剩 cell_w-1.3 —— 想要 1.2 宽的格子将永远做不到（下限是 内容+1.3）。
    """
    assert metrics.effective_pads() == (0.7, 0.45)
    assert metrics.effective_pads(pad_x=0.5) == (0.5, 0.45)
    assert metrics.effective_pads(cell_w=1.2) == (metrics.PAD_X_UNIFORM, 0.45)
    assert metrics.effective_pads(cell_w=1.2, cell_h=0.9) == (metrics.PAD_X_UNIFORM,
                                                              metrics.PAD_Y_UNIFORM)
    assert metrics.effective_pads(cell_w=1.2, pad_x=0.5) == (0.5, 0.45)     # 显式优先
    # 1.2 宽的格子在紧凑留白下是可行的（内容区 0.85），在 1.3 留白下不可能
    rows = [["2026", "8"]]
    assert metrics.est_table_size(rows, 20, cell_w=1.2)["need_cell_w"] <= 1.2


def test_overflow_msg_carries_numbers_and_hints():
    """报错必须说清四件事：估算多大 / 可用多大 / 超了多少 / 怎么改。"""
    msg = metrics.overflow_msg("scene[1].elements[0]", "这张 2×10 的表",
                               20.0, 3.0, ["去掉 4 列（现在 10 列）", "把 font_size 从 24 降到 14"])
    assert "估算 20.00 × 3.00" in msg
    assert "可用 12.40 × 6.60" in msg
    assert "超宽 7.60" in msg
    assert "去掉 4 列" in msg
    assert "font_size 从 24 降到 14" in msg


def test_suggest_helpers():
    assert metrics.suggest_count(10, 20.0) == 6      # 10 列要表宽 20 → 减到 6 列
    assert metrics.suggest_count(3, 5.0) == 3        # 本来就放得下 → 不动
    assert metrics.suggest_font_size(30, 20.0, 3.0) < 30
    assert metrics.suggest_font_size(30, 5.0, 3.0) == 29   # 放得下 → 基本不动（留一点余量）
    assert metrics.suggest_font_size(8, 20.0, 3.0) >= 8    # 不会低于字号下限


def test_budget_line_is_short():
    """这句话要进 system prompt，必须短（prompt 成本见 HANDOFF §8.31）。"""
    line = metrics.budget_line()
    assert 40 < len(line) < 160
    assert "12.4" in line and "6.6" in line


def test_no_manim_dependency():
    """估算器一旦 import manim，整个 pytest 会从 2 秒变成几十秒 —— 钉死这条。"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "storyboard", "metrics.py")
    src = open(path, encoding="utf-8").read()
    # 只看真正的 import 语句行（文档里写着"不 import manim"不算）
    assert "\nimport manim" not in src
    assert "\nfrom manim" not in src

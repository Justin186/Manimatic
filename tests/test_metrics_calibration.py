# -*- coding: utf-8 -*-
"""
估算器标定测试 —— **默认不跑**（要真起 manim 量文字，约十几秒）。

跑法：
    set MSB_CALIBRATE=1 && python -m pytest tests/test_metrics_calibration.py -q

它盯的是一条**不变式**，不是具体数值：

    估算宽度 ≥ 真实渲染宽度   （宁可估大，不能估小）

估小了 = "校验说没事、渲染时字被缩到看不清"，属于静默失败 —— 本项目最忌讳的一类；
估大了顶多让本来就很挤的画面多改一版。所以上界一旦漂移（换字体、换 manim、
换机器），这条测试必须红，提醒重新标定。
"""
import os
import re

import pytest

from storyboard import metrics

pytestmark = pytest.mark.skipif(
    os.environ.get("MSB_CALIBRATE") != "1",
    reason="标定测试要真渲染，默认跳过（MSB_CALIBRATE=1 开启）",
)

CN_FONT = "STZhongsong"
LATIN_FONT = "Times New Roman"
_RUN = re.compile(r"([\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]+)")

# 真实语料：来自 examples/ 与 output/_tasks/ 里模型真写过的 caption，
# 外加每类字符的极端形态（全是数字 / 全是大写 / 全角标点堆叠）。
CORPUS = [
    # ---- 真实字幕（二分查找那题）----
    "有序数组：二分查找只对这种数组有效",
    "目标：找出 15",
    "蓝框里是候选范围，共 8 个数；先看正中间 nums[3] = 7",
    "7 < 15，说明 15 在右边：1、3、5、7 全部淘汰",
    "范围缩到 4~7，中间是 nums[5] = 11",
    "11 < 15，9、11 也被淘汰，范围再砍一半",
    "范围只剩 6~7，中间是 nums[6] = 13",
    "13 < 15，候选里只剩最后一个数 15 了",
    "nums[7] = 15，和目标相等，命中！",
    "这次只比较了 4 次；如果从头逐个找，最坏要看 8 个数",
    # ---- 每类字符的极端形态 ----
    "0123456789012345678901234567890123456789",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
    "WWWWWWWWWWWWWWWW",
    "mmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmm",
    "，。、；：？！（）【】",
    "~~~~~~~~~~~~~~~~~~~~~~~~",
    "中" * 24,
    "汉" * 10 + "abc" + "9" * 8,
    # ---- 混排公式 ----
    "由 $f'(x)=3x^2$ 得驻点 $x=\\pm1$",
    "割补后面积是 $a^2+b^2=c^2$",
    # ---- 多行 ----
    "第一行文字\\n第二行更长一些的文字\\n第三行",
]


def _markup(s: str) -> str:
    """和 dsl.RUNTIME_HELPER._markup 完全同口径（含 &<> 转义）。"""
    def esc(x):
        return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = []
    for part in _RUN.split(str(s)):
        if not part:
            continue
        if not _RUN.fullmatch(part) and part.strip():
            out.append('<span font_family="%s">%s</span>' % (LATIN_FONT, esc(part)))
        else:
            out.append(esc(part))
    return "".join(out)


def _real_w(s: str, fs: int) -> float:
    """真渲一行文字的宽度：`$...$` 段按 `rich()` 的做法用 MathTex 混排。"""
    from manim import DOWN, LEFT, MathTex, MarkupText, VGroup

    def one(ln):
        parts = re.split(r"\$([^$]+)\$", ln)
        if len(parts) == 1:
            return MarkupText(_markup(ln), font=CN_FONT, font_size=fs)
        mobs = []
        for i, p in enumerate(parts):
            if not p:
                continue
            mobs.append(MathTex(p, font_size=round(fs * 1.3)) if i % 2
                        else MarkupText(_markup(p), font=CN_FONT, font_size=fs))
        grp = VGroup(*mobs)
        grp.arrange(__import__("manim").RIGHT, buff=0.12)
        return grp

    rows = [one(ln) for ln in str(s).split("\\n")]
    return max(r.width for r in rows)


@pytest.mark.parametrize("fs", [12, 16, 20, 24, 28, 30, 36, 48, 56, 72])
@pytest.mark.parametrize("s", CORPUS)
def test_estimate_is_upper_bound(s, fs):
    """核心不变式：估算 ≥ 实测。"""
    est = metrics.est_text_w(s, fs)
    real = _real_w(s, fs)
    assert est >= real - 1e-6, (
        f"估算偏小（违反上界）: 实测 {real:.4f} > 估算 {est:.4f}  fs={fs}  {s[:40]!r}\n"
        f"→ 说明字符系数需要重新标定（见 metrics.py 顶部那张表）"
    )


def test_estimate_is_not_absurdly_loose():
    """也不能保守到离谱：真实语料的估算/实测比不该超过 1.7 倍，否则会误杀正常内容。"""
    worst = (0.0, "")
    for fs in (20, 24, 30, 36, 48):
        for s in CORPUS:
            real = _real_w(s, fs)
            if real < 1e-6:
                continue
            r = metrics.est_text_w(s, fs) / real
            if r > worst[0]:
                worst = (r, f"fs={fs} {s[:30]!r}")
    assert worst[0] <= 1.7, f"估算过于保守（{worst[0]:.2f} 倍）: {worst[1]}"


def test_table_estimate_matches_manim():
    """表格估算与真渲染的偏差：同样必须"估 ≥ 实测"，且不能超过 1.35 倍。"""
    from manim import MarkupText, MobjectTable

    rows = [["0", "1", "2", "3", "4", "5", "6", "7"],
            ["1", "3", "5", "7", "9", "11", "13", "15"]]
    for fs in (20, 26, 30, 40):
        # ⚠️ 必须用**渲染器实际会传的**留白建表：生成器会把 effective_pads() 的结果
        # 显式传给 `MobjectTable(h_buff=…, v_buff=…)`，所以标定也得用同一组值 ——
        # 否则就是"拿松留白去对紧留白"，估 ≥ 实测 那条断言会假失败。
        table = MobjectTable([[MarkupText(_markup(c), font=CN_FONT, font_size=fs)
                               for c in r] for r in rows],
                             h_buff=metrics.PAD_X_DEFAULT,
                             v_buff=metrics.PAD_Y_DEFAULT)
        est = metrics.est_table_size(rows, fs)
        assert est["w"] >= table.width - 1e-6, f"表宽估算偏小 fs={fs}: {est['w']:.3f} < {table.width:.3f}"
        assert est["h"] >= table.height - 1e-6, f"表高估算偏小 fs={fs}"
        assert est["w"] / table.width <= 1.35, f"表宽估算过松 fs={fs}: {est['w'] / table.width:.2f}"


def test_formula_estimate_is_upper_bound():
    """LaTeX 公式的估算也必须压得住。"""
    from manim import MathTex

    for s in (r"\lfloor \log_2 n \rfloor + 1 = \lfloor \log_2 8 \rfloor + 1 = 4",
              r"O(\log n)", r"f'(x)=3x^2-3", r"\frac{a+b}{c}"):
        for fs in (30, 40, 52, 56):
            real = MathTex(s, font_size=fs).width
            est = metrics.est_tex_w(s, fs)
            assert est >= real - 1e-6, f"公式估算偏小: {real:.4f} > {est:.4f}  fs={fs} {s}"

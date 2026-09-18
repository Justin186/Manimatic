# -*- coding: utf-8 -*-
"""
字体回退与公式分流的回归测试 —— **不 import manim**（毫秒级）。

背景（HANDOFF §8.72）
--------------------
用户截了一张图：字幕「① 加 0 还是它自己」前面是**两块印着 `2460` / `2461` 的白方块**。

根因不在排版，在字体归属：`_markup()` 把**所有非中文段**都显式指给了 LATIN_FONT，
而 Pango 的规则是"当前 family **没有这个字形**时才去找后备字体" ——
被显式指定之后它不 fallback，直接画 `.notdef`（一个带码点的空心方块）。
Times New Roman 恰好没有 ①②③… 这一整类符号。

三处必须同时正确，且**必须来自同一份数据**（否则就是本项目最贵的失效模式）：
    1. `dsl._markup()`    正文：回退字符不套 span → 走 CN_FONT
    2. `dsl.formula()`    公式：`①` 走 pdflatex 会**整镜渲不出来**，必须改走 ctex
    3. `tex_batch`        预热：分组判据必须与 2 完全一致，否则缓存全落空且静默

所以这个文件盯四件事：
    · 判定本身对（含"不该回退的别回退"）
    · 三处判据同源
    · `metrics` 的定价不低于实测（回退符号是全角，比 K_OTHER 宽 33%~49%）
    · 生成代码里的 helper 真带着替换后的数据（不是 `__PLACEHOLDER__`）
"""
import re

import pytest

from storyboard import dsl, metrics, tex_batch


# ==============================================================================
# 用户截图里那四个白方块，以及同类
# ==============================================================================
# 截图原文（`① 加 0 还是它自己　② 加「下一个」` 里的两个圈数字）
SCREENSHOT_CHARS = "\u2460\u2461"
# 同属"Times 缺、宋体有"的其它高频符号（模型很爱用）
SAME_FAMILY = "\u2462\u2463\u2474\u3220\u25c6\u25b3\u2605\u2208\u2225\u2227"


@pytest.mark.parametrize("ch", list(SCREENSHOT_CHARS + SAME_FAMILY))
def test_screenshot_chars_fall_back_to_cn_font(ch):
    """截图里那些字必须交给中文字体 —— 否则就是 .notdef 白方块。"""
    assert metrics.needs_cn_font(ch), f"U+{ord(ch):04X} 没被判为需回退，又会画成白方块"


@pytest.mark.parametrize("ch", ["A", "z", "0", "9", "=", "(", "\u25a0", "\u25cf",
                                "\u2192", "\u00b1", "\u221a", "\u4e2d", "\uff01"])
def test_chars_times_has_are_not_stolen(ch):
    """
    反向：Times New Roman **有**字形的字符不能被抢走 —— 否则西文风格会漂
    （那正是 2026-09-16 "数字和字母都不是新罗马体"那条的成因）。
    """
    assert not metrics.needs_cn_font(ch), f"{ch!r} 被误判为需回退，西文字体会被换成宋体"


def test_fallback_range_is_the_metric_single_source():
    """码点表只该有一份：`metrics.FALLBACK_RANGES`。"""
    assert dsl._FALLBACK_RANGES == metrics.FALLBACK_RANGES


def test_ctex_ranges_are_derived_not_handwritten():
    """
    公式判据必须是**由构造**并上字形回退判据的结果，而不是两份手写区间。

    第一版就是手写的，然后当场被抓出一个漏网：`∈`(U+2208) 在回退集里、
    却不在公式正则里 —— "正文修好了、公式里仍会炸"。并集由代码算就没这个选项。
    """
    fb = set(metrics._fallback_chars())
    pattern = metrics.FORMULA_TEX_PATTERN
    rx = __import__("re").compile(pattern)
    missing = [c for c in fb if not rx.match(c)]
    assert not missing, ("公式判据漏了字形回退集里的字符（会被交给 pdflatex → 整镜崩）: "
                         + " ".join("U+%04X" % ord(c) for c in sorted(missing)))
    # 且确实**更宽**（否则"更保守"这个设计意图就是空话）
    assert rx.match("\u25a0") and not metrics.needs_cn_font("\u25a0")


# ==============================================================================
# 公式分流：`①` 走 pdflatex 是"整镜渲不出来"，不是"画得难看"
# ==============================================================================
def test_formula_with_circled_number_goes_ctex():
    """
    ⚠️ 这是比白方块更严重的一条：pdflatex 遇到 U+2460 直接
    `Unicode character ① not set up for use with LaTeX`，**整个分镜出不来**。
    """
    assert dsl.needs_ctex("\u2460 + a = b")
    assert dsl.needs_ctex("a + S(b) 表示后继")      # 中文（原来就认）
    assert dsl.needs_ctex("\u25c6 = \u25b3")        # 几何图形


def test_plain_formula_still_takes_the_fast_mathpath():
    """普通公式仍走 MathTex（快路径不能被误伤，否则每屏都慢一截）。"""
    for s in ("1 + 1 = S(0)", r"f'(x)=3x^2-3", r"\frac{a+b}{c}", "x^2 + y^2"):
        assert not dsl.needs_ctex(s), s


def test_formula_judgement_is_shared_with_tex_batch():
    """
    ⚠️ 预热分组必须与 `formula()` 用**同一个** pattern。

    漂移的后果是静默的：预热按 A 算 hash、真渲染按 B 去查 → 缓存全落空，
    只表现为"怎么又慢了"（不会报错）。
    """
    assert tex_batch._CJK is dsl._FORMULA_TEX_RE
    assert dsl._FORMULA_TEX_PATTERN == metrics.FORMULA_TEX_PATTERN


def test_ctex_judgement_is_a_superset_of_the_cn_font_one():
    """
    公式判据**刻意更宽**：正文里误判给宋体只是字形风格差一点，
    公式里误判给 pdflatex 是一镜全废。坏处方向不同，所以宁可更保守。
    """
    for cp in [0x2460, 0x25C6, 0x2605, 0x2208, 0x3220, 0x33A1, 0x2225]:
        ch = chr(cp)
        assert dsl.needs_ctex(ch), f"U+{cp:04X} 在公式里也该走 ctex"
    # 反例：`■`（Times 有字形，正文不必回退）在公式里仍按保守走 ctex
    assert not metrics.needs_cn_font("\u25a0")
    assert dsl.needs_ctex("\u25a0")


# ==============================================================================
# 生成代码里的 helper：数据必须**真的替换进去了**
# ==============================================================================
def test_helper_has_no_leftover_placeholders():
    """占位符没被替换掉的话，生成代码一跑就 NameError —— 而且是整条链路全崩。"""
    assert "__FALLBACK_RANGES__" not in dsl.RUNTIME_HELPER
    assert "__FORMULA_TEX_PATTERN__" not in dsl.RUNTIME_HELPER


def test_helper_judgements_run_and_agree_with_module_level():
    """
    把注入串 exec 起来，确认**运行时**的判定与模块层一致。

    为什么不直接 `import` 生成文件：这里要的是"注入的那段代码自己能不能跑"，
    它能跑起来本身就排除了"占位符没替换 / 语法错误"这类问题。
    """
    import manim  # noqa: F401  —— helper 依赖 manim 的全局名字

    ns = dict(vars(manim))
    ns.update({"ACCENT": "#FFFF00", "PRIMARY": "#58C4DD", "SECONDARY": "#83C167",
               "CN_FONT": "STZhongsong", "LATIN_FONT": "Times New Roman"})
    exec(compile(dsl.RUNTIME_HELPER, "<helper>", "exec"), ns)  # noqa: S102

    for s in ("1 + 1 = S(0)", "\u2460 + a", "a + S(b) 表示", "x^2 + y^2", "\u25c6"):
        assert ns["_needs_ctex"](s) == dsl.needs_ctex(s), s
    for ch in "\u2460\u2461\u25c6A0\u25a0\u4e2d":
        assert ns["_needs_cn_font"](ch) == metrics.needs_cn_font(ch), ch


def test_markup_does_not_wrap_fallback_chars():
    """
    `_markup()` 必须把回退字符留在 span **外面**（交给外层 font=CN_FONT）。

    这是白方块的直接来源：`<span font_family="Times New Roman">①</span>`
    → `.notdef`。断言用"span 里不含回退字符"来钉，不依赖具体字符串形态。
    """
    import manim  # noqa: F401

    ns = dict(vars(manim))
    ns.update({"ACCENT": "#FFFF00", "PRIMARY": "#58C4DD", "SECONDARY": "#83C167",
               "CN_FONT": "STZhongsong", "LATIN_FONT": "Times New Roman"})
    exec(compile(dsl.RUNTIME_HELPER, "<helper>", "exec"), ns)  # noqa: S102
    markup = ns["_markup"]

    out = markup("\u2460 加 0 还是它自己\u3000\u2461 加「下一个」")
    # 每个 span 的内容里都不该出现回退字符
    bodies = re.findall(r'font_family="[^"]*">(.*?)</span>', out)
    assert bodies, f"一个 span 都没有？那说明西文字体整个丢了：{out!r}"
    for body in bodies:
        for ch in SCREENSHOT_CHARS:
            assert ch not in body, f"{ch!r} 被包进 span 了，又会变成白方块：{out!r}"
    # 但数字仍要被包（西文字体不能丢）—— 碎片两边会带上相邻空格，所以用宽松匹配
    assert re.search(r'font_family="[^"]*">[^<]*0[^<]*</span>', out), out
    # 回退字符必须出现在 span **之外**
    assert re.search(r'</span>\u2460', out) or out.startswith("\u2460"), out


# ==============================================================================
# 估算器：回退符号是全角字形，定价不能按普通标点
# ==============================================================================
def test_fallback_chars_are_priced_as_fullwidth():
    """
    ⚠️ 这档原来落进 `K_OTHER = 0.0100`，而实测 0.0132~0.0149 —— **低估 33%~49%**，
    违反本模块"宁大不小"的铁律。表现是校验说放得下、渲染出来贴边甚至出画。
    """
    for ch in "\u2460\u25c6\u2605\u2474\u2208\u2225":
        assert metrics._k(ch) == metrics.K_FALLBACK, ch
    assert metrics.K_FALLBACK >= 0.0149, "低于实测上界 0.0149 就又低估了"
    # 普通标点仍是 K_OTHER（别把整个档位一起抬高，那会误杀正常内容）
    assert metrics._k(",") == metrics.K_OTHER
    assert metrics._k("@") == metrics.K_OTHER


def test_pricing_does_not_depend_on_manim():
    """估算器是纯算术，`needs_cn_font` 也不能把它拖进 manim（有测试钉着这条铁律）。"""
    src = open(metrics.__file__, encoding="utf-8").read()
    assert "\nimport manim" not in src
    assert "\nfrom manim" not in src

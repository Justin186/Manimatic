# -*- coding: utf-8 -*-
"""
版面尺寸估算器 —— **纯算术**：不 import manim、不碰 LaTeX、不做任何 I/O。

================================================================================
为什么需要它
================================================================================
原来"会不会出界"是靠**数条数**判断的：表格最多 8 行 8 列、cell_box 格号最多 6 个、
单元格最多 80 字。这几条与"到底占多大地方"毫无关系 ——
8 列的单字数字表宽 12.03（几乎顶满预算），而 8 列的长公式表能到 30+，两者同罪同罚。
反过来，`[1,2,3,4,5,6,7,8]` 这种格号数组明明一点问题都没有，却会被拒。

真正的判据只有一个：**按内容和字号估出来的渲染尺寸，会不会超出画面可用范围**。
估算偏差的方向必须是**保守（宁大不小）**：估小了 = "校验说没事、渲染时字被缩到看不清"，
属于本项目最忌讳的静默失败；估大了顶多让本来就很挤的画面多改一版。

================================================================================
系数怎么来的（不许凭感觉改）
================================================================================
2026-09-16 在本机（Manim CE 0.21 / MiKTeX / CN_FONT=STZhongsong、LATIN_FONT=Times New Roman）
用 `MarkupText`/`MathTex`/`MobjectTable` 真量出来的，取**字号 8~72 全区间**的上界：

| 字符类别 | 每字符宽度 ÷ font_size 的上界 | 出处（最坏的那个字号） |
|---|---|---|
| 中日韩全角（含全角标点） | 0.0150 | fs=20 → 0.014877 |
| 大写 A-Z | 0.0150 | fs=10（`W`）→ 0.014932 |
| 小写 m w | 0.0126 | fs=12（`m`）→ 0.012442 |
| 小写 i j l f r t | 0.0062 | fs=24 → 0.0042（×小字号量化偏差 1.35） |
| 小写其余 | 0.0068 | fs=24 → 0.0063 |
| 数字 0-9 | 0.0084 | fs=18 → 0.008238 |
| 其它（半角标点/空格/符号） | 0.0100 | 保守值，罩住 `~`（实测 >0.0062）与 `@` `%` |

小字号的上界明显更大（Pango 的字符步进会被量化，字号越小相对误差越大），
所以这几个数是**按全区间取 max** 的，不是取中位数 —— 就是为了"宁大不小"。
`tests/test_metrics_calibration.py` 会用真实渲染复核这条不变式（估算 ≥ 实测）。

表格的格子尺寸公式也是实量的：`格子宽 = 内容宽 + h_buff`，而 manim `Table` 的
默认 `h_buff=1.3` / `v_buff=0.8` —— 也就是说**每个格子凭空比内容宽 1.3 个单位**，
这才是"格子大小被固定死"的真正来源（8 列单数字表光留白就吃掉 10.4 个单位）。
"""
from __future__ import annotations

import re

# ==============================================================================
# 画面预算
# ==============================================================================
# manim 默认 frame_width=14.2222 / frame_height=8.0（见标定探针实测输出）。
# 左右各留 0.9111、上下各留 0.7 作为安全边距 → 12.4 × 6.6。
# ⚠️ 这两个数与 `dsl.RUNTIME_HELPER._fit()` 的默认行为必须一致（有测试盯着）。
FRAME_W, FRAME_H = 14.2222, 8.0
MARGIN_X, MARGIN_Y = 0.9111, 0.7
BUDGET_W = round(FRAME_W - 2 * MARGIN_X, 3)      # 12.4
BUDGET_H = round(FRAME_H - 2 * MARGIN_Y, 3)      # 6.6

# 表格留白默认值。
#
# ⚠️ 2026-09-17 改（用户反馈"表格太大、单元格也太大"）：原来是 manim 的原生默认
# `h_buff=1.3 / v_buff=0.8`，**太松** —— 3 列光留白就吃掉 3.9 个单位，格子里大半是空白。
# 降到 0.7 / 0.45：普通文字格子的留白接近"内容撑出的自然格子"，又不至于挤。
# ⚠️ 改这里的值会改变**所有没写 pad 的表格**的渲染尺寸，所以两处必须同时改：
#   · `dsl._table()` 里那两个兜底字面量（它只是"手工调用别崩"的兜底，规则在这儿）；
#   · `tests/test_metrics_calibration.py` 建 manim 表时要传**同一组** h_buff/v_buff，
#     否则标定变成"拿松留白去对紧留白"，估 ≥ 实测 那条断言会假失败。
PAD_X_DEFAULT = 0.7
PAD_Y_DEFAULT = 0.45
# 但**给了 cell_w/cell_h 的时候不能再按 1.3 算**：cell_w 是"格子的最终宽度"，
# 而留白 1.3 意味着内容区只剩 cell_w-1.3 —— 想要 1.2 宽的格子就永远做不到
# （下限是 内容+1.3，实测会直接报"cell_w 定小了"）。所以一旦要求统一格子尺寸，
# 默认留白就换成紧凑值。这条规则只由 `effective_pads()` 一处实现，
# **校验层和代码生成都走它** —— 两边各写一份的话，"校验说 1.2 放得下、渲染出来 2.5 宽"
# 这种静默不一致迟早发生。
PAD_X_UNIFORM = 0.35
PAD_Y_UNIFORM = 0.25


def effective_pads(cell_w=None, cell_h=None, pad_x=None, pad_y=None) -> tuple[float, float]:
    """实际生效的 (pad_x, pad_y)。规则见上面那段的说明。"""
    px = pad_x if pad_x is not None else (PAD_X_UNIFORM if cell_w is not None else PAD_X_DEFAULT)
    py = pad_y if pad_y is not None else (PAD_Y_UNIFORM if cell_h is not None else PAD_Y_DEFAULT)
    return float(px), float(py)


# 出界判定线 = 预算 × 这个系数。
# 为什么不是 1.0：估算器给的是**有意偏大的上界**（实测 1.05~1.7 倍），
# 所以"估算 > 预算"并不等价于"真的放不下"。2026-09-16 实测过最直接的反例：
# 二分查找那张 8 列表实际渲染宽 12.03（放得下），估算 12.80 —— 差 6.4% 就会被 1.0 的
# 判定线误拒，用户重渲同一个分镜会突然"校验失败"。
# 留着这个余量之后：明显超出的照样硬拒（估算 20 对预算 12.4 → 1.61 倍），
# 边缘情况放行给运行时的 _fit（真缩了会打 WARN，不再静默）。
OVERFLOW_SLACK = 1.15

# ==============================================================================
# 字符宽度系数（每字符宽度 ÷ font_size，取全字号区间上界）
# ==============================================================================
# 逐类实测的上界（字号 8~72，Times New Roman + STZhongsong）：
#   CJK / 全角标点 / 大写字母 → 0.0150（`中` 0.014877 @fs20；`W` 0.014932 @fs10）
#   小写字母按字身宽窄**分三档**（不分档的话，一句 26 个字母的英文会被按最宽的 `m`
#   估出 2.08 倍的浪费，实测过）：
#       窄字身 i j l f r t → 0.0062（fs=24 实测 0.0042，小字号的量化偏差约 1.35 倍）
#       宽字身 m w         → 0.0126（`m` fs=12 实测 0.012442）
#       其余 a b c d e …   → 0.0068（fs=24 实测 0.0063）
#   数字 0-9               → 0.0084（fs=18 实测 0.008238）
#   其它（半角标点/空格/运算符）→ 0.0100（**保守**，见下）
K_CJK = 0.0150
K_UPPER = 0.0150
K_LOWER_WIDE = 0.0126       # m w
K_LOWER_NARROW = 0.0062     # i j l f r t
K_LOWER = 0.0068            # 其余小写
K_DIGIT = 0.0084
# 为什么标点/运算符用一个**偏大**的统一值：这一档里字符的宽度差得离谱
# （`.` 约 0.003，而 `~` 实测 >0.0062、`@`/`%` 更宽），逐字符标定要量近百个字符，
# 而它们在字幕里的占比很小 —— 收益远小于"估小了导致误判为放得下"的风险。
# 所以取一个能罩住最宽标点的值。**这是保守方向，符合本模块的铁律。**
K_OTHER = 0.0100

# 「交给中文字体画」的符号（`FALLBACK_RANGES` 那批）比 K_OTHER 宽得多 —— 它们
# 要么是全角宽度、要么是 CJK 字体里的等宽方块字形，实测 0.0132~0.0149（fs 20）。
#
# ⚠️ 2026-09-18 修（就是 ① 白块那次）：这档符号原来落进 `K_OTHER = 0.0100`，
# 而实测最高 0.0149 —— **低估 33%~49%**，直接违反本模块"宁大不小"的铁律。
# 表现是校验层说"放得下"、渲染出来却贴边甚至出画（`_fit` 再悄悄缩，打 WARN）。
# 取 0.0150 与 K_CJK 同值：它们本来就是同一类字形（宋体全角），没必要分两档。
K_FALLBACK = 0.0150

_NARROW_LOWER = frozenset("ijlfrt")
_WIDE_LOWER = frozenset("mw")

# 命中判定用的扁平集合（只在第一次需要时建一次）。
_FALLBACK_CHARS = None


def _fallback_chars():
    """`FALLBACK_RANGES` 展开成的字符集合（缓存）。"""
    global _FALLBACK_CHARS
    if _FALLBACK_CHARS is None:
        _FALLBACK_CHARS = frozenset(
            chr(cp) for a, b in FALLBACK_RANGES for cp in range(a, b + 1))
    return _FALLBACK_CHARS


def needs_cn_font(ch) -> bool:
    """
    这个字符是否必须交给 CN_FONT 画（Times New Roman 没字形，否则是带码点的白方块）。

    刻意收得**比 `FORMULA_TEX_PATTERN` 窄**：正文里只要"Times 真有这个字形"就不该
    抢去宋体（否则西文风格会漂），而公式那边宁可更保守（见那边注释）。
    """
    return ch in _fallback_chars()

# ------------------------------------------------------------------------------
# 字符分类数据 —— **单一出处**
# ------------------------------------------------------------------------------
# 下面三份数据同时被三个地方读，所以定义在这里（纯数据、不 import manim）：
#   · `dsl.py` 的中英混排 `_markup()`（决定哪个字符交给中文字体画）
#   · `dsl.py` 的 `formula()` 分流（决定走 pdflatex 还是 ctex）
#   · `tex_batch.py` 的预热分组（必须与上一行**同一判据**，否则缓存全落空）
# 为什么强调这一点：本项目最贵的一类 bug 就是"两处规则各写一份、改了一处忘了另一处"
# —— 它不报错，只表现为"怎么又慢了"或"怎么又出方块了"（2026-09-18 的 ① 白块就是）。

# 「Times New Roman 没有字形、必须交给中文字体画」的码点区间。
#
# 怎么来的：2026-09-18 用 GDI `GetGlyphIndicesW` 逐个码点比对
#   「Times New Roman 缺 且 CN_FONT 有」（只在中文语境真会用到的符号块里找）。
# ⚠️ 它依赖**本机字体版本**。换字体/换机器后要重跑标定，
#   `tests/test_glyph_fallback.py` 拿真实渲染核对（越界即回归）。
FALLBACK_RANGES = (
    (0x2196, 0x2199),                                        # ↖↗↘↙
    (0x2208, 0x2208), (0x221D, 0x221D), (0x2220, 0x2220),    # ∈ ∝ ∠
    (0x2223, 0x2223), (0x2225, 0x2225), (0x2227, 0x2228),    # ∣ ∥ ∧ ∨
    (0x222A, 0x222A), (0x222E, 0x222E), (0x2234, 0x2237),    # ∪ ∮ ∴∵∶∷
    (0x223D, 0x223D), (0x224C, 0x224C), (0x2252, 0x2252),    # ∽ ≌ ≒
    (0x2266, 0x2267), (0x226E, 0x226F), (0x2295, 0x2295),    # ≦≧≮≯ ⊕
    (0x2299, 0x2299), (0x22A5, 0x22A5), (0x22BF, 0x22BF),    # ⊙ ⊥ ⊿
    (0x2312, 0x2312),                                        # ⌒
    (0x2460, 0x2469), (0x2474, 0x249B),                      # ①-⑩ ⑴-⒛
    (0x2501, 0x254B), (0x256D, 0x2573),                      # 制表符 ─│┌… ╭╮╯╰…
    (0x2581, 0x258F), (0x2594, 0x2595),                      # 方块元素 ▁▂▃… ▔▕
    (0x25B3, 0x25B3), (0x25BD, 0x25BD), (0x25C6, 0x25C7),    # △▽◆◇
    (0x25CE, 0x25CE), (0x25E2, 0x25E5), (0x2605, 0x2606),    # ◎ ◢◣◤◥ ★☆
    (0x2609, 0x2609),                                        # ☉
    (0x3220, 0x3229), (0x3231, 0x3231), (0x32A3, 0x32A3),    # ㈠-㈩ ㈱ ㊣
    (0x338E, 0x338F), (0x339C, 0x339E), (0x33A1, 0x33A1),    # ㎎㎏ ㎜㎝㎞ ㎡
    (0x33C4, 0x33C4), (0x33CE, 0x33CE), (0x33D1, 0x33D2),    # ㏄ ㏎ ㏑㏒
    (0x33D5, 0x33D5),                                        # ㏕
    (0xFE30, 0xFE31), (0xFE33, 0xFE44), (0xFE49, 0xFE4F),    # ︰︱ ︳︴ ﹉﹊…
)

# `formula()` 该不该改走 ctex(xelatex) 的额外区间（`FALLBACK_RANGES` 之外的部分）。
#
# 为什么不逐码点、只按块：pdflatex 缺的是**整类**符号（圈数字、几何图形、CJK 兼容
# 符号…），按块拦能一并罩住还没被发现的同类字符。**宁可多走一次 ctex，不可漏判**
# —— 漏判的代价是整个分镜渲不出来（`Unicode character ① not set up for use with
# LaTeX`），而多走一次的代价只是慢一点点。
#
# 为什么它比 `FALLBACK_RANGES` 宽（多了 25A0-25BF 这类"Times 有字形"的）：
# 两条判据的**坏处方向不同**。正文里误判给宋体只是字形风格差一点（几乎看不出），
# 公式里误判给 pdflatex 是**一镜全废** —— 所以后者宁可更保守。
_FORMULA_ONLY_RANGES = (
    (0x2E80, 0x9FFF),        # CJK 部首扩展 / 汉字（含 `_has_cjk` 原本那一档）
    (0x3000, 0x303F),        # CJK 标点
    (0xFF00, 0xFFEF),        # 全角形式
    (0x2460, 0x24FF),        # 圈数字 / 带括号数字
    (0x25A0, 0x27BF),        # 几何图形 / 杂项符号 / 装饰符
    (0x3200, 0x33FF),        # CJK 兼容（㈠ ㎎ ㏒…）
    (0xFE30, 0xFE4F),        # CJK 兼容形式
)


def _merge_ranges(*groups):
    """把多组区间并成"互不重叠、已排序"的一份。"""
    flat = sorted((a, b) for g in groups for a, b in g)
    out = []
    for a, b in flat:
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return tuple(out)


def _ranges_to_charclass(ranges):
    """区间 → 正则字符类的内容（如 `\\u2460-\\u2469`）。"""
    parts = []
    for a, b in ranges:
        parts.append("\\u%04x" % a if a == b else "\\u%04x-\\u%04x" % (a, b))
    return "[" + "".join(parts) + "]"


# ⚠️ 公式判据**由构造保证是字形回退判据的超集**（`FALLBACK_RANGES` 被并进来）。
#
# 为什么不手写两份区间（第一版就是手写的，然后**当场被测试抓出一个漏网**：
# `∈`(U+2208) 在回退集里、却不在公式正则里 —— 也就是"正文修好了、公式里仍会炸"）。
# 手写两份区间必须有一个人记得"改一处要同步另一处"，而本项目最贵的 bug 全是
# 这一类遗忘（它不报错，只静默地少拦一个字符）。并集由代码算，就没有这个选项。
FORMULA_TEX_RANGES = _merge_ranges(FALLBACK_RANGES, _FORMULA_ONLY_RANGES)
FORMULA_TEX_PATTERN = _ranges_to_charclass(FORMULA_TEX_RANGES)

# 行高与行距：`rich()` 里多行走 `VGroup(...).arrange(DOWN, buff=0.22)`。
# 实测 fs=30 四行总高 2.2569 = 4×0.3992 + 3×0.22，两个系数都吻合。
LINE_H = 0.0134
LINE_BUFF = 0.22

# LaTeX（`$...$` 段与 formula 元素）：`rich()` 里按 `font_size * 1.3` 排。
# 分子分母的"可见字形数"比源码字符数小得多（`\lfloor` 只画一个符号），
# 所以先剥命令再数字形；每个字形按 0.0075 × tex_size（实测最贵的是 `\log`/`\lfloor`
# 这类算子，0.0056 左右，留 34% 余量）。
K_TEX_GLYPH = 0.0075
TEX_SIZE_FACTOR = 1.3
TEX_LINE_H = 0.0250        # `\frac` 是最高的形态：实测 0.0210/tex_size

_TEX_CMD = re.compile(r"\\[a-zA-Z]+")
_TEX_SEG = re.compile(r"\$([^$]+)\$")
_FULLWIDTH = re.compile(r"[\u2e80-\u9fff\u3000-\u303f\uff00-\uffef\uac00-\ud7af]")


def _k(ch: str) -> float:
    """单个字符的宽度系数（每字符宽度 ÷ font_size）。"""
    if _FULLWIDTH.match(ch):
        return K_CJK
    if "A" <= ch <= "Z":
        return K_UPPER
    if "a" <= ch <= "z":
        if ch in _NARROW_LOWER:
            return K_LOWER_NARROW
        if ch in _WIDE_LOWER:
            return K_LOWER_WIDE
        return K_LOWER
    if "0" <= ch <= "9":
        return K_DIGIT
    # ⚠️ 顺序有讲究：这些符号（①②◆★→∈…）会被交给宋体画，是**全角字形**，
    # 比 K_OTHER 宽 33%~49%（见表）。放在 K_OTHER 之前判，否则又是低估。
    if needs_cn_font(ch):
        return K_FALLBACK
    return K_OTHER


def est_text_w(content: str, font_size: float) -> float:
    """
    一段混排文本的估算宽度（取**最宽的一行**）。支持 `$...$` 行内公式与 `\\n` 换行。
    """
    return max((_line_w(ln, font_size) for ln in _lines(content)), default=0.0)


def est_text_h(content: str, font_size: float) -> float:
    """一段文本的估算高度（行高 × 行数 + 行距）。"""
    lines = _lines(content)
    n = len(lines)
    if n == 0:
        return 0.0
    h = sum(_line_h(ln, font_size) for ln in lines) + LINE_BUFF * (n - 1)
    return h


def est_block_size(content: str, font_size: float) -> tuple[float, float]:
    return est_text_w(content, font_size), est_text_h(content, font_size)


def est_table_size(rows, font_size, cell_w=None, cell_h=None,
                   pad_x=None, pad_y=None, square=False) -> dict:
    """
    表格尺寸估算。返回 dict（不只给宽高，也给"哪一格/哪一列最宽"这类诊断信息，
    报错文案要用它说清"减几列、cell_w 至少要多少"）。

    `cell_w` / `cell_h` 是**统一的格子尺寸**：给了之后每个格子都是这么大，
    所以表宽 = 列数 × cell_w（内容比内容区还宽的情况由 `need_cell_w` 单独报错拦截，
    不会出现"有的格子被撑大"这种不均匀）。
    """
    pad_x, pad_y = effective_pads(cell_w, cell_h, pad_x, pad_y)
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)

    cw = [[est_text_w(str(c), font_size) for c in r] for r in rows]
    ch = [[est_text_h(str(c), font_size) for c in r] for r in rows]
    col_content = [max((cw[r][c] for r in range(len(rows)) if c < len(rows[r])), default=0.0)
                   for c in range(n_cols)]
    row_content = [max(ch[r], default=0.0) for r in range(n_rows)]

    if square:
        # 正方形格子（`square: true`）：每格边长取"内容+留白"里大的那一侧，长宽都按它 ——
        # 数字网格 / 矩阵 / 卷积核这类「一格一个数」的表，格子方的好看。
        # ⚠️ 它和 cell_w/cell_h 表达的是两种意图，同时写时**以 square 为准**（见 _table）。
        s = max(max(col_content + [0.0]) + pad_x, max(row_content + [0.0]) + pad_y)
        col_w, row_h = [s] * n_cols, [s] * n_rows
    else:
        col_w = ([w + pad_x for w in col_content] if cell_w is None
                 else [float(cell_w)] * n_cols)
        row_h = ([h + pad_y for h in row_content] if cell_h is None
                 else [float(cell_h)] * n_rows)

    return {
        "w": sum(col_w),
        "h": sum(row_h),
        "n_rows": n_rows,
        "n_cols": n_cols,
        "pad_x": pad_x,
        "pad_y": pad_y,
        "col_content_w": col_content,
        "row_content_h": row_content,
        # 想保证"所有格子等宽"，cell_w 至少要这么大（内容 + 留白）
        "need_cell_w": (max(col_content) + pad_x) if col_content else 0.0,
        "need_cell_h": (max(row_content) + pad_y) if row_content else 0.0,
        # 最宽的那一格在哪（报错时点名，模型才知道该缩哪一个）
        "widest": max(((cw[r][c], r, c) for r in range(len(rows)) for c in range(len(rows[r]))),
                      default=(0.0, 0, 0)),
    }


def est_rich_size(content: str, font_size: float) -> tuple[float, float]:
    """`rich()` 产物的尺寸（文本块 / 表格单元格走同一个入口，口径一致）。"""
    return est_block_size(content, font_size)


# ==============================================================================
# 判定与文案
# ==============================================================================
def fits(w: float, h: float, budget_w: float = BUDGET_W,
         budget_h: float = BUDGET_H, slack: float = OVERFLOW_SLACK) -> bool:
    """估算尺寸是否可放行（判定线含 OVERFLOW_SLACK 的上界余量，理由见那个常量）。"""
    return w <= budget_w * slack + 1e-9 and h <= budget_h * slack + 1e-9


def shrink_factor(w: float, h: float, budget_w: float = BUDGET_W,
                  budget_h: float = BUDGET_H) -> float:
    """要缩到多少倍才放得下（>=1 表示不用缩）。"""
    return max(1.0, w / budget_w, h / budget_h)


def size_text(w: float, h: float) -> str:
    return f"估算 {w:.2f} × {h:.2f}"


def budget_text(budget_w: float = BUDGET_W, budget_h: float = BUDGET_H) -> str:
    return f"可用 {budget_w:.2f} × {budget_h:.2f}"


def overflow_msg(where: str, what: str, w: float, h: float,
                 hints=(), budget_w: float = BUDGET_W,
                 budget_h: float = BUDGET_H) -> str:
    """
    出界报错文案。必须说清四件事，模型才可能一轮改对：
    **估算多大 / 可用多大 / 超了多少 / 具体怎么改**。
    """
    over = []
    if w > budget_w + 1e-9:
        over.append(f"超宽 {w - budget_w:.2f}")
    if h > budget_h + 1e-9:
        over.append(f"超高 {h - budget_h:.2f}")
    lines = [f"{where}: {what}放不下 —— {size_text(w, h)}，{budget_text(budget_w, budget_h)}"
             f"（{'，'.join(over)}）"]
    hints = [x for x in hints if x]
    if hints:
        lines.append("    改法（任一）：" + "；".join(hints))
    return "\n".join(lines)


def suggest_font_size(font_size: float, w: float, h: float,
                      budget_w: float = BUDGET_W, budget_h: float = BUDGET_H) -> int:
    """把字号降到这个值以内就放得下了（向下取整，留一点余量）。"""
    import math
    k = 1.0 / shrink_factor(w, h, budget_w, budget_h)
    return max(8, int(math.floor(font_size * k * 0.98)))


def suggest_count(n: int, w: float, budget_w: float = BUDGET_W) -> int:
    """这一维减到几份就放得下（列数/行数的建议值，至少 1）。"""
    import math
    if w <= budget_w or n <= 1:
        return n
    return max(1, int(math.floor(n * budget_w / w)))


def budget_line() -> str:
    """给模型看的"画面有多大"一句话（塞进 describe()）。"""
    return (f"画面可用范围约 {BUDGET_W:.1f} × {BUDGET_H:.1f}（宽 × 高）："
            f"一张表 8 列大约只能放到每格 1.5 宽；文字一行超过约 "
            f"{int(BUDGET_W / K_CJK / 20)} 个汉字（font_size 20）就会出界。")


# ==============================================================================
# 内部：文本 → 行 → 宽度
# ==============================================================================
def _lines(content: str):
    """按 `rich()` 的同一口径拆行：它 split 的是字面量 `\\n`（反斜杠 + n），不是真换行。"""
    return str(content).split("\\n")


def _line_w(ln: str, font_size: float) -> float:
    """一行（可能混排）的估算宽度 = 普通段逐字累加 + `$...$` 段按公式估。"""
    total = 0.0
    pos = 0
    for m in _TEX_SEG.finditer(ln):
        total += _plain_w(ln[pos:m.start()], font_size)
        total += est_tex_w(m.group(1), font_size * TEX_SIZE_FACTOR)
        pos = m.end()
    total += _plain_w(ln[pos:], font_size)
    return total


def _plain_w(s: str, font_size: float) -> float:
    return sum(_k(ch) for ch in s) * font_size


def _line_h(ln: str, font_size: float) -> float:
    """一行的高度取"纯文本行高"与"公式行高"里的大者（含 `$...$` 的行会被公式顶高）。"""
    h = LINE_H * font_size
    for m in _TEX_SEG.finditer(ln):
        h = max(h, TEX_LINE_H * font_size * TEX_SIZE_FACTOR)
    return h


def est_tex_w(src: str, tex_font_size: float) -> float:
    """
    估算一段 LaTeX 的宽度：先剥掉命令与花括号，数**可见字形**，再按每字形系数算。

    为什么要剥：`\\lfloor \\log_2 n \\rfloor + 1 = 4` 源码 24 个字符，画出来只有
    10 个字形左右 —— 直接按源码长度估会差不多翻倍，把本来放得下的公式误判成出界。
    每个命令算 1 个字形（它确实要画一个符号）。
    """
    s = _TEX_CMD.sub("X", str(src))
    s = s.replace("{", "").replace("}", "")
    n = len(re.sub(r"\s+", "", s))
    return n * K_TEX_GLYPH * tex_font_size


def est_formula_size(content: str, font_size: float) -> tuple[float, float]:
    """独立 `formula` 元素的尺寸：整段按公式估（它不经过 rich 的混排）。"""
    w = est_tex_w(content, font_size)
    h = TEX_LINE_H * font_size
    # 多行公式（\\ 换行）按多行算
    n = len(re.findall(r"\\\\", str(content))) + 1
    return w, h * n

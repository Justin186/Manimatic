# -*- coding: utf-8 -*-
"""
声明式动画 DSL —— 用「元素 + 时间线」取代「模板枚举」。

================================================================================
为什么要有这一层（这是本次重构的全部动机）
================================================================================

旧架构：LLM 从 9 个固定模板里选一个，再填 params。
    问题 1（死板）：组合空间 = 9 个模板 × 各自几个参数。想要"先在坐标轴上画
                   曲线，再让一个点沿曲线滑动，同时右上角浮出一行公式"——做不到，
                   只能挑最接近的模板，画面必然千篇一律。
    问题 2（简洁）：模板内部布局写死，表现力上限就是那几行代码。

新架构：LLM 输出 elements（元素库里自由取、任意个、任意组合）+ timeline
        （动作白名单自由编排）。
    组合空间 = 元素类型 × 参数 × 元素个数(无上限) × 动作序列(无上限)。
    从"选一道菜"变成"自己配菜"。

================================================================================
自由度上去了，可运行性靠什么保证？五道闸
================================================================================

闸 1  元素由**确定性构建器**生成 —— LLM 只报 kind 和参数，一行 Python 都不是它写的。
      （这一条没变，仍然守住 PROJECT_KNOWLEDGE 里的决策 1）
闸 2  表达式走 **AST 白名单** 校验，运行时在受限命名空间 eval，且 NaN/Inf/异常
      一律兜底成 0 —— 任何数学表达式都不可能让渲染崩掉。
闸 3  参数类型/范围/引用完整性在 schema 层校验：引用了不存在的元素、越界的
      坐标、非法颜色，全部**在渲染前**报错，而不是给你一个"能跑但内容错了"的视频。
      （静默失败仍然是头号敌人）
闸 4  生成后的源码强制 `compile()` 一遍 —— 语法错误为 0 是硬保证。
闸 5  布局护栏：所有文本类元素自动 `_fit()` 防溢出；元素可用 place 原语精确定位，
      不会像自由写坐标那样飘出画面。

================================================================================
两个让"自由度"真正可用的机制
================================================================================

机制 A  `$id` 追踪器引用 + 自动 always_redraw
      任何数值位置写 "$t" 就等于引用名为 t 的 ValueTracker 的当前值。
      构造代码里只要出现 `.get_value()`，该元素自动被 always_redraw 包裹 ——
      LLM 不需要懂 updater，就能做出"动点沿曲线滑、曲线随之生长"这类效果。

机制 B  动作里可以内联定义元素
      {"do":"create","target":{"id":"d","kind":"dot","at":{...}}}
      不必先声明再引用，随手造随手用。
"""

import math
import re

from . import metrics

# 变量前缀：避免元素 id 撞上 Python 关键字或 manim 的全局名
_V = "_e_"


def var_name(eid):
    """
    元素 id → 生成代码里的变量名。

    公开出来是因为**渲染器也要拼这个名字**（分镜边界的清屏片段要按变量名排除承接元素），
    两边各写一份 `"_e_" + id` 迟早会漂移。
    """
    return _V + eid


def needs_3d(storyboard) -> bool:
    """
    整份分镜里有没有用到三维（三维元素，或相机旋转动作）。

    公开出来是给渲染器用的：它据此决定生成的场景继承 `Scene` 还是 `ThreeDScene`。
    **按整份扫，不按单个分镜扫** —— 拆分渲染（render_split）时每个文件只含一个分镜，
    而 `carry` 承接过来的是 `{"id": x, "carry": true}` 这种**没有 kind 的存根**，
    单看那一镜是看不出它是三维元素的；按整份扫则必然在声明它的那一镜里被扫到。
    （多扫到的代价只是"某个纯 2D 分镜也用 ThreeDScene"，无副作用。）
    """
    for sc in (storyboard or {}).get("scenes", []) or []:
        for el in sc.get("elements", []) or []:
            if isinstance(el, dict) and el.get("kind") in _D3_KINDS:
                return True
        for ac in sc.get("timeline", []) or []:
            if isinstance(ac, dict) and ac.get("do") in _D3_ACTIONS:
                return True
    return False


# 构建期依赖：这些参数指向别的元素，构建时必须先生成对方（`_Builder.build()` 用的就是
# 这一份）。carry 校验也读它 —— 承接了一个元素、却没承接它依赖的元素，
# 边界清屏会把依赖清掉，画面就剩个"飘着的图"。
BUILD_DEPS = ("axes", "curve", "of", "path")

# 三维元素 / 动作。用到任何一个，生成的场景基类就要从 `Scene` 换成 `ThreeDScene`
# （见 renderer.HEADER 的 {scene_base}）—— 不换的话相机转不了、
# `rotate_camera` 会直接 AttributeError。
# 同时被 `_layout_errors_for` 用来跳过"2D 版面估算"（3D 物体的屏幕尺寸随视角变，
# 那套系数估不了它）。
_D3_KINDS = frozenset({"three_axes", "surface", "space_curve"})
_D3_ACTIONS = frozenset({"rotate_camera"})

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TRACKER_REF = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")


class DSLError(Exception):
    """DSL 校验/构建错误。渲染前抛出，绝不带到渲染阶段。"""
    pass


# ==============================================================================
# 常量表（LLM 写 JSON 时用小写/缩写，这里翻译成 Manim 常量）
# ==============================================================================

DIRECTIONS = {
    "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
    "ul": "UL", "ur": "UR", "dl": "DL", "dr": "DR",
}

EDGES = {"up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT"}

# 「对齐边」在规范里写的是**边的说法**（left/right/top/bottom，见 place.next_to.aligned
# 与 group.aligned），而 manim 的 `aligned_edge` 收的是**方向常量**（LEFT/UP/...）。
# ⚠️ 2026-09-17 修：这里原来直接 `.upper()`，于是 `aligned: "bottom"` 生成出
# `aligned_edge=BOTTOM` —— 一个不存在的名字：校验层全绿，渲染期才 `NameError`。
# （示例里「让两个分式的分母同底」正是靠 bottom 对齐，一撞就炸。）
# 顺手也认 up/down（和 top/bottom 同义，模型常这么写）。
ALIGNED_EDGES = {
    "left": "LEFT", "right": "RIGHT", "top": "UP", "bottom": "DOWN",
    "up": "UP", "down": "DOWN", "center": "ORIGIN",
}

ANCHORS = {
    "center": "get_center()", "top": "get_top()", "bottom": "get_bottom()",
    "left": "get_left()", "right": "get_right()",
    "ul": "get_corner(UL)", "ur": "get_corner(UR)",
    "dl": "get_corner(DL)", "dr": "get_corner(DR)",
}

# 颜色：Manim 常量白名单 + 任意 hex。写不存在的颜色名是最常见的一类错误。
COLORS = {
    "BLUE", "BLUE_A", "BLUE_B", "BLUE_C", "BLUE_D", "BLUE_E",
    "TEAL", "TEAL_A", "TEAL_B", "TEAL_C", "TEAL_D", "TEAL_E",
    "GREEN", "GREEN_A", "GREEN_B", "GREEN_C", "GREEN_D", "GREEN_E",
    "YELLOW", "YELLOW_A", "YELLOW_B", "YELLOW_C", "YELLOW_D", "YELLOW_E",
    "GOLD", "GOLD_A", "GOLD_B", "GOLD_C", "GOLD_D", "GOLD_E",
    "RED", "RED_A", "RED_B", "RED_C", "RED_D", "RED_E",
    "MAROON", "MAROON_A", "MAROON_B", "MAROON_C", "MAROON_D", "MAROON_E",
    "PURPLE", "PURPLE_A", "PURPLE_B", "PURPLE_C", "PURPLE_D", "PURPLE_E",
    "PINK", "ORANGE", "WHITE", "BLACK",
    "GREY", "GREY_A", "GREY_B", "GREY_C", "GREY_D", "GREY_E", "GREY_BROWN",
    "LIGHT_GREY", "DARK_GREY", "GRAY", "DARK_BLUE", "LIGHT_BROWN",
    # 本项目主题色（在 HEADER 里定义）
    "PRIMARY", "SECONDARY", "ACCENT",
}

HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# 表达式允许的函数/常量（AST 白名单 + 运行时受限命名空间，两处共用）
SAFE_NAMES = {
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "sinh", "cosh", "tanh",
    "exp", "log", "ln", "log2", "log10", "sqrt",
    "abs", "floor", "ceil", "sign",
    "min", "max", "mod", "pow", "round",
    "pi", "e", "tau", "inf",
}


# ==============================================================================
# 运行时 helper：注入到生成的 .py 顶部
#   全部是确定性代码，且处处设兜底 —— 目标是"任何合法 JSON 都能渲染出画面"
# ==============================================================================

RUNTIME_HELPER = '''
import numpy as np
import re as _re

_SAFE_NS = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan,
    "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan, "atan2": np.arctan2,
    "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh,
    "exp": np.exp, "log": np.log, "ln": np.log, "log2": np.log2, "log10": np.log10,
    "sqrt": np.sqrt, "abs": abs, "floor": np.floor, "ceil": np.ceil,
    "sign": np.sign, "min": min, "max": max, "mod": np.mod, "pow": np.power,
    "round": round, "pi": np.pi, "e": np.e, "tau": 2 * np.pi, "inf": np.inf,
}

def _eval_expr(expr, x, **extra):
    """在受限命名空间里求值数学表达式（无 builtins，无法做任何系统调用）。"""
    ns = dict(_SAFE_NS)
    ns["x"] = float(x)
    ns.update(extra)
    return float(eval(expr, {"__builtins__": {}}, ns))


def _safe_eval(expr, x, **extra):
    """
    求值 + 兜底：定义域外（sqrt 负值）、奇点（1/x）、溢出（exp 大数）
    一律返回 0.0 而不是让渲染崩掉。
    这是"可运行性"的最后一道保险：宁可曲线在奇点处画错，也不要整个视频出不来。
    """
    try:
        v = _eval_expr(expr, x, **extra)
    except Exception:
        return 0.0
    if v != v or v in (float("inf"), float("-inf")):   # NaN / Inf
        return 0.0
    if v > 1e6:
        return 1e6
    if v < -1e6:
        return -1e6
    return v


_CJK_RE = _re.compile(r"[\\u4e00-\\u9fff\\u3000-\\u303f\\uff00-\\uffef]")

def _has_cjk(s):
    return bool(_CJK_RE.search(str(s)))


# ---- 中英混排：中文走 CN_FONT，数字/字母走 LATIN_FONT ------------------------
#
# 为什么必须显式切分，而不是只写 `font=CN_FONT` 让 Pango 自己 fallback：
# CN_FONT（华文中宋）**自己就带拉丁字形**，Pango 只会在"当前字体没有这个字形"时
# 才去找后备字体 —— 所以 font=CN_FONT 的 Text 里，数字和字母永远出不了新罗马体，
# 只有宋体自带的西文（2026-09-16 用户实测反馈："Text 的数字和字母都不是新罗马体"）。
#
# 为什么用 MarkupText + <span font_family> 而不是"按中文/西文切几段 Text 再 VGroup 拼"：
# 后者的每段字高、基线各不相同，arrange 只能按外框对齐，中英混排会忽高忽低；
# MarkupText 交给 Pango 排同一行，基线天然一致。
_LATIN_RUN = _re.compile(r"([\\u3000-\\u303f\\u4e00-\\u9fff\\uff00-\\uffef]+)")
_LATIN_FONT_OK = None


def _has_latin_font():
    """
    LATIN_FONT 是否真的注册在 Pango 里。查一次、缓存一次、只警告一次。

    不查的后果是"静默换字体"：Pango 找不到指定的 family 就回退到它自己的默认字体，
    画面照常渲染、一行报错都没有，但字已经不是要求的那个了
    （check_env.py 讲中文字体时点名过这个坑）。
    """
    global _LATIN_FONT_OK
    if _LATIN_FONT_OK is None:
        try:
            import manimpango
            _LATIN_FONT_OK = LATIN_FONT in manimpango.list_fonts()
        except Exception:
            _LATIN_FONT_OK = True      # 查不了就别自作聪明降级
        if not _LATIN_FONT_OK:
            print("[WARN] 西文字体 %r 未注册到 Pango，数字/字母退回 %r"
                  % (LATIN_FONT, CN_FONT))
    return _LATIN_FONT_OK


def _markup_escape(s):
    """Pango 标记里的三个特殊字符。& 必须最先replace，否则会把 &lt; 二次转义。"""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _markup(s):
    """把**非中文**的连续段包进 <span font_family="LATIN_FONT">。"""
    latin = _has_latin_font()
    out = []
    for part in _LATIN_RUN.split(str(s)):
        if not part:
            continue
        if latin and not _LATIN_RUN.fullmatch(part) and part.strip():
            out.append('<span font_family="%s">%s</span>'
                       % (LATIN_FONT, _markup_escape(part)))
        else:
            out.append(_markup_escape(part))
    return "".join(out)


def _text(s, font_size=48, **kw):
    """
    混排文本：中文用 CN_FONT，数字/字母用 LATIN_FONT（默认 Times New Roman）。

    签名刻意和 Text 对齐（首参是字符串、其余走 **kw），因为它还要直接当
    Axes 的 label_constructor 用 —— 刻度数字也是"Text 里的数字"，得一起管住。
    """
    try:
        return MarkupText(_markup(s), font=CN_FONT, font_size=font_size, **kw)
    except Exception:
        # MarkupText 万一挂了也不能让整条片子出不来：退回纯 CN_FONT 的 Text
        return Text(str(s), font=CN_FONT, font_size=font_size, **kw)


_NUMBER_UNIT_BUFF = 0.14


def _number(value, num_decimal_places=2, unit="", font_size=32, color=ACCENT):
    """
    动态数字标签（可带单位）。

    单位是**模型自由写的字符串**，而 Manim 的 `DecimalNumber(unit=...)` 内部用
    MathTex 渲染它 —— 中文必然
    `LaTeX Error: Unicode character 块 (U+5757) not set up for use with LaTeX.`，
    整个分镜直接渲不出来（2026-09-13 实测：分镜 6 的 `"unit": " 块"`）。
    所以含中文的单位改用 Text 拼在数字右边；数字本身仍走 LaTeX（阿拉伯数字没问题）。
    """
    if unit and _has_cjk(unit):
        num = DecimalNumber(value, num_decimal_places=num_decimal_places,
                            font_size=font_size, color=color)
        lab = _text(unit.strip(), font_size=font_size, color=color)
        grp = VGroup(num, lab)
        grp.arrange(RIGHT, buff=_NUMBER_UNIT_BUFF, center=False)
        return grp
    # 纯 LaTeX 单位（m^2 那种上标写法）交给 DecimalNumber 自己排，效果最好
    return DecimalNumber(value, num_decimal_places=num_decimal_places,
                         unit=unit or None, font_size=font_size, color=color)


def _set_number(m, value):
    """
    刷新数字。

    为什么生成的代码不直接写 `m.set_value(v)`：带中文单位的数字外面包了一层
    VGroup（VGroup 没有 set_value）。统一走这里，DSL 就不必分两种形态 ——
    多一种形态就多一处分支，漏一个就是又一条静默失效。

    ⚠️ `arrange(center=False)` 不能省：arrange 默认 `center=True`，内部执行的是
    `self.center()`（即 move_to(ORIGIN)）—— 每帧都会把整块挪回屏幕中心。
    """
    if isinstance(m, VGroup):
        m[0].set_value(value)
        m.arrange(RIGHT, buff=_NUMBER_UNIT_BUFF, center=False)
    else:
        m.set_value(value)


def formula(s, **kw):
    """
    公式渲染三级分流：
      1. 无 LaTeX 环境  → Text 兜底（保证不崩）
      2. 纯 LaTeX       → MathTex（快）
      3. 含中文         → Tex + ctex 模板（xelatex），公式里可直接嵌中文

    ⚠️ 2026-09-17：模板一律带上 xcolor（+ 项目调色板的 \\definecolor），
    这样公式里可以写 `{\\color{ACCENT}\\Delta x}` 给**个别符号**单独上色
    （元素上要同时写 "tex_colors": true，见 §8.49）。
    """
    if not USE_LATEX:
        # 降级也不能把 LaTeX 源码糊在屏幕上：先清理成人能读的记号
        return _text(_plain_tex(s), **kw)
    if not _has_cjk(s):
        return MathTex(s, tex_template=_tex_template(), **kw)
    try:
        return Tex(f"${s}$", tex_template=_ctex_template(), **kw)
    except Exception:
        return _text(_plain_tex(s), **kw)


# 公式内分色要用的调色板名（只列**纯字母**的：xcolor 的 \\definecolor 对下划线
# 名字不保证可用，所以 BLUE_A / GREY_BROWN 这类不往 LaTeX 里搬）。
_TEX_PALETTE = ("PRIMARY", "SECONDARY", "ACCENT", "RED", "ORANGE", "TEAL",
                "GREEN", "GOLD", "PURPLE", "PINK", "BLUE", "YELLOW",
                "WHITE", "GREY", "BLACK")
_TEX_TEMPLATE_CACHE = None
_CTEX_TEMPLATE_CACHE = None


def _tex_template():
    """
    带 xcolor + 项目调色板的 Tex 模板。

    manim 默认模板**没有** xcolor，所以用 `\\color{...}` 的公式会直接编译失败
    （实测：`Undefined control sequence`，整个分镜渲不出来）。颜色值从 manim 的
    同名常量取，保证 LaTeX 里的 ACCENT 和画面其它地方的 ACCENT 是同一个色号。
    """
    global _TEX_TEMPLATE_CACHE
    if _TEX_TEMPLATE_CACHE is not None:
        return _TEX_TEMPLATE_CACHE
    lines = [r"\\usepackage[english]{babel}", r"\\usepackage{amsmath}",
             r"\\usepackage{amssymb}", r"\\usepackage[dvipsnames]{xcolor}"]
    # 色值从**本脚本的 globals** 取：HEADER 里 PRIMARY/SECONDARY/ACCENT 是 hex 字符串，
    # 其余颜色名来自 `from manim import *`（是 ManimColor）。⚠️ 不能用 manim.ACCENT ——
    # 那三个主题色是本项目自己定义的，manim 模块里没有（踩过一次，见 §8.49）。
    for _name in _TEX_PALETTE:
        _c = globals().get(_name)
        if _c is None:
            continue
        try:
            _hex = _c if isinstance(_c, str) else _c.to_hex()
            _hex = str(_hex).lstrip("#").upper()
        except Exception:
            continue
        lines.append(r"\\definecolor{%s}{HTML}{%s}" % (_name, _hex))
    _TEX_TEMPLATE_CACHE = TexTemplate(preamble="\\n".join(lines))
    return _TEX_TEMPLATE_CACHE


def _ctex_template():
    """小一号的三级分流：中文公式也要能分色。ctex 模板是个全局单例，别原地改。"""
    global _CTEX_TEMPLATE_CACHE
    if _CTEX_TEMPLATE_CACHE is not None:
        return _CTEX_TEMPLATE_CACHE
    try:
        _base = TexTemplateLibrary.ctex
        _CTEX_TEMPLATE_CACHE = TexTemplate(
            preamble=_base.preamble + "\\n" + r"\\usepackage{xcolor}",
            body=_base.body)
    except Exception:
        _CTEX_TEMPLATE_CACHE = TexTemplateLibrary.ctex
    return _CTEX_TEMPLATE_CACHE


def _rich_line(s, font_size, **kw):
    """单行混排：用 $...$ 包 LaTeX，其余按中文渲染。"""
    parts = _re.split(r"\\$(.+?)\\$", s)
    if len(parts) == 1:
        return _text(s, font_size=font_size, **kw)
    mobs = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 1:
            # LaTeX 字面比中文字面视觉高度小约 25%，不补偿会明显偏小
            mobs.append(formula(part, font_size=round(font_size * 1.3), **kw))
        else:
            mobs.append(_text(part, font_size=font_size, **kw))
    grp = VGroup(*mobs)
    grp.arrange(RIGHT, buff=0.12)
    return grp


def rich(s, **kw):
    """
    文本混排，支持 \\n 换行。
    例：rich("由 $f'(x)=3x^2$ 得驻点 $x=\\\\pm1$\\n再看单调性")
    """
    fs = kw.pop("font_size", 48)
    lines = str(s).split("\\n")
    rows = VGroup(*[_rich_line(ln, fs, **kw) for ln in lines])
    if len(rows) == 1:
        return rows[0]
    rows.arrange(DOWN, aligned_edge=LEFT, buff=0.22)
    return rows


# ---- 版面护栏（_fit）：只保证"不出画"；安全边距只用来给**居中**内容封顶 --------
# 左右各留 0.9111 → 居中元素容量 = 14.2222 - 2*0.9111 = 12.4
# 上下各留 0.7    → 居中元素容量 = 8.0    - 2*0.7    = 6.6
# ⚠️ 与 storyboard/metrics.py 的 BUDGET_W / BUDGET_H 是同一组数（有测试盯着）
# ⚠️ 这两个数说的是"**居中**内容的容量"，不是"画面有多大" —— 绝不能拿它去判一个
#    `place` 贴边摆放的元素放不放得下（那样会把字缩没，见 _fit 的说明）。
_FRAME_MARGIN_X = 0.9111
_FRAME_MARGIN_Y = 0.7
# 缩放的下限。再小就等于把内容变成看不清的灰块 —— 那种时候"被裁掉一截"反而是
# 更容易被发现、也更容易改的失败方式（作者一眼就知道自己 place 摆错了）。
_FIT_MIN_SCALE = 0.5
_FIT_WARNED = set()


def _fit(m, name="", max_w=None, max_h=None):
    """
    防溢出护栏：只保证元素**不出画**。缩放是最后手段，且有一条硬下限。

    判据为什么是"画面"而不是"12.4 × 6.6"：那两个数是**居中内容**的容量预算
    （超过它校验层就会打回），不是"画面有多大"。元素一旦用 place 贴到画面边上
    （`{"edge":"down","buff":0.5}`、`{"corner":"dr","buff":0.4}` —— 示例里到处都是），
    它本来就落在安全边距里，那是作者的显式排版，不是错误。旧写法把预算当硬线，
    于是"离边越近 → 算出来的可用空间越小 → 缩得越狠"：buff=0.5 缩到 0%（字直接
    消失）、buff=0.6 缩到 48%、buff=0.7 缩到 77%（2026-09-17 用户反馈的
    "有时候渲染出来的字特别小"，实测 buff ≤ 0.7 必命中）。

    现在的口径：**绕元素当前的中心，在画面之内能用多大就用多大**，再拿预算封顶。
    缩放绕中心做、中心不动，所以两侧里更紧的那一侧说了算：

        room = 2 * min(半边长 - 中心, 半边长 + 中心)

    三条性质各自都对得上：
      · 居中元素（中心 = 0）算出来正好是整条边长，再被预算封顶 → 还是 12.4 × 6.6，
        老分镜观感零变化；
      · `to_edge` / `to_corner` 摆出来的元素，room 恰好等于 `自身尺寸 + 2*buff`，
        只要 buff ≥ 0 就恒 ≥ 自身尺寸 → **不会缩**（会缩当且仅当真的出画）；
      · 中心已经跑到画面外时 room ≤ 0 → 缩小**救不回来**（中心不动，元素永远有一截
        在外面），这时只告警不缩 —— 硬缩的结果是把整句缩成 0%。

    最后用 `_FIT_MIN_SCALE` 兜住"贴着画面边、要缩掉一半以上才放得下"的病态输入
    （负 buff、把元素 shift 出画面之类）：宁可留一截在画外，也不把内容缩成灰块。

    告警必须能自证、可执行：原始多大 / 缩到多少 / 画面多大 / 该改哪里。
    逐元素只喊一次 —— `always_redraw` 每帧都会调 _fit，不挡会刷屏。
    """
    fw, fh = config.frame_width, config.frame_height
    c = m.get_center()
    w0, h0 = m.width, m.height

    def _room(pos, frame, budget):
        """
        这个轴上能用多大（<= 0 表示中心已在画面外、缩放无解）。

        `min(room, budget)` 里那个 budget 只对**居中**内容起作用（居中时 room 就是
        整条边长，预算更小），贴边内容由 room 说了算 —— 两者取小的写法保证了
        "居中内容的口径不变"和"贴边内容不被预算误伤"同时成立。
        """
        half = frame / 2
        room = 2 * min(half - pos, half + pos)
        return min(room, budget) if room > 1e-6 else 0.0

    aw = _room(c[0], fw, fw - 2 * _FRAME_MARGIN_X)
    ah = _room(c[1], fh, fh - 2 * _FRAME_MARGIN_Y)
    if max_w is not None and aw > 0:
        aw = min(aw, max_w)
    if max_h is not None and ah > 0:
        ah = min(ah, max_h)

    k = 1.0
    if aw > 0:
        k = max(k, w0 / aw)
    if ah > 0:
        k = max(k, h0 / ah)

    msg = ""
    if k > 1.0001:
        s = max(1.0 / k, _FIT_MIN_SCALE)
        m.scale(s)
        msg = ("元素 %s 超出画面，已自动缩到 %.0f%%（原始 %.2f × %.2f → %.2f × %.2f，"
               "画面 %.2f × %.2f）"
               % (name, 100.0 * s, w0, h0, m.width, m.height, fw, fh))
        if s > 1.0 / k + 1e-9:
            msg += ("；已到 %.0f%% 下限（再小就看不清了），缩完仍有一截在画面外"
                    % (100.0 * _FIT_MIN_SCALE))
    elif aw <= 0 or ah <= 0:
        msg = ("元素 %s 的中心落在画面外（中心 %.2f, %.2f；画面 %.2f × %.2f）—— "
               "绕中心缩小救不回来，请改 place 的 buff / edge，或减小元素尺寸"
               % (name, c[0], c[1], fw, fh))
    if msg and name and name not in _FIT_WARNED:
        _FIT_WARNED.add(name)
        print("[WARN] " + msg)
    return m


# 「淡出淡入」的两半：**严格先后，零重叠**。
#
# 时间轴（t 是整个过渡的进度，两半各自占一半时长）：
#
#     旧的  |████████|                      0 → 0.5 淡出完
#     新的  |        |████████|            0.5 → 1 才淡入
#
# 为什么不是"两半同时淡"（那才是最标准的交叉溶解）：底部字幕是**居中**的，前后
# 两句字数常常不同，重叠的那段时间里两句话叠在一起读不出字 —— 2026-09-16 逐帧看过，
# 1.2s 的过渡里全程叠加糊 0.4s、错开到只重叠 20% 仍然看得出"淡出没淡完新的就来了"
# （用户反馈原话）。所以这里彻底分开：旧的字消失之后，新的才开始出现。
#
# 两个函数在 t=0/1 端点分别取到 0 和 1，所以**收尾状态与"完整演一遍"完全一致**，
# 不影响任何按最终形态做的判断（`_fit` / `cell_box` / keep 判断都在播放前算好）。
# 每半段的时长 = run_time / 2，觉得切换太快就把 run_time 加大（默认 1.6）。
def _xfade_out(t):
    """旧的那一半：前半段淡尽（t ≥ 0.5 之后保持完全消失）。"""
    return min(1.0, t / 0.5)


def _xfade_in(t):
    """新的那一半：后半段才淡入（t ≤ 0.5 期间保持完全不可见）。"""
    return max(0.0, (t - 0.5) / 0.5)


def _table_cell(m, cell_w=None, cell_h=None):
    """
    把单元格内容套进一个**不可见定尺框**：格子尺寸由参数决定，而不是被"这一列里
    最长的那个内容"撑出来（更隐蔽的是 h_buff 默认 1.3 —— 每个格子凭空宽 1.3 个单位，
    8 列光留白就吃掉 10.4，这才是"格子大小被固定死"的真正来源）。

    框是透明的（描边/填充都不透明度 0），只负责提供包围盒 —— manim 的
    `Table.get_cell()` 完全按"该行/列包围盒 + h_buff/2"推算格子边界，网格线取的也是
    相邻列间隙的中点，两者与各列是否等宽无关。所以套框之后 cell_box / move_cells
    仍然严丝合缝（2026-09-16 实测：套 0.9×0.7 的框后 cell(1,1) = 2.2×1.5，
    正好是 0.9+1.3 与 0.7+0.8）。
    """
    w = float(cell_w) if cell_w else 0.0
    h = float(cell_h) if cell_h else 0.0
    if w <= 0 and h <= 0:
        return m
    box = Rectangle(width=max(w, m.width), height=max(h, m.height))
    box.set_stroke(opacity=0)
    box.set_fill(opacity=0)
    return VGroup(box, m.move_to(box.get_center()))


def _table(rows, cell_w=None, cell_h=None, pad_x=None, pad_y=None,
           include_outer_lines=False, square=False):
    """
    建表格。**只有显式给了 cell_*/pad_* 才会绕这一圈**，否则走的是和以前一模一样的一行
    `MobjectTable(...)` —— 生成代码逐字节不变（回归断言靠这条）。

    cell_w / cell_h 是"格子最终尺寸"（含留白）：实际套的透明框尺寸 = cell_w - pad_x。
    再取一次 max(目标, 内容)：校验层已经保证内容不会超过目标（超了会硬拒并回灌），
    这里兜第二次只是为了"估算万一偏小也不裁掉内容"。留白是"内容到格线的距离"，
    manim 的默认值 h_buff=1.3 / v_buff=0.8 偏大，想紧凑就写小一点。

    ⚠️ 这两个 pad 参数由**生成器显式传进来**（`metrics.effective_pads()` 算好的），
    这里的 None 分支只是"手工调用别崩"的兜底 —— 规则不在这里，别在这儿再判一次默认值。
    """
    kw = {}
    if pad_x is not None:
        kw["h_buff"] = float(pad_x)
    if pad_y is not None:
        kw["v_buff"] = float(pad_y)
    if cell_w is None and cell_h is None and not square:
        return MobjectTable(rows, include_outer_lines=include_outer_lines, **kw)
    # 兜底值跟 `metrics.PAD_X_DEFAULT / PAD_Y_DEFAULT` 保持一致（0.7 / 0.45）——
    # 正常路径上生成器总会显式传进来，这里只是手工调用时的兜底。
    px = float(pad_x) if pad_x is not None else 0.7
    py = float(pad_y) if pad_y is not None else 0.45
    max_cw = max((c.width for r in rows for c in r), default=0.0)
    max_ch = max((c.height for r in rows for c in r), default=0.0)
    if square:
        # 正方形格子：所有格子的内容框取"最长宽 / 最高"里大的那个 —— 于是每格边长一样，
        # 长宽相等（`metrics.est_table_size` 里有同名逻辑，两边必须一致）。
        s = max(max_cw + px, max_ch + py)
        sw, sh = s - px, s - py
    else:
        sw = max((float(cell_w) - px) if cell_w else 0.0, max_cw)
        sh = max((float(cell_h) - py) if cell_h else 0.0, max_ch)
    wrapped = [[_table_cell(c, sw, sh) for c in r] for r in rows]
    return MobjectTable(wrapped, include_outer_lines=include_outer_lines, **kw)


def _legend(items, font_size=20, buff=0.16, color=WHITE):
    """图例：items = [[color, "label"], ...]"""
    rows = VGroup()
    for item in items:
        c, lab = item[0], item[1]
        seg = Line(LEFT * 0.2, RIGHT * 0.2, color=c, stroke_width=3)
        lab_m = rich(lab, font_size=font_size, color=color)
        rows.add(VGroup(seg, lab_m).arrange(RIGHT, buff=0.12))
    rows.arrange(DOWN, aligned_edge=LEFT, buff=buff)
    return rows


# 没有 LaTeX 时把命令换成能看懂的符号（键是 LaTeX 命令，值是 Unicode）
_TEX_SYMBOLS = [
    (r"\\times", "×"), (r"\\cdot", "·"), (r"\\div", "÷"), (r"\\pm", "±"), (r"\\mp", "∓"),
    (r"\\leq", "≤"), (r"\\le", "≤"), (r"\\geq", "≥"), (r"\\ge", "≥"),
    (r"\\neq", "≠"), (r"\\ne", "≠"), (r"\\approx", "≈"), (r"\\equiv", "≡"),
    (r"\\infty", "∞"), (r"\\to", "→"), (r"\\rightarrow", "→"), (r"\\Rightarrow", "⇒"),
    (r"\\in", "∈"), (r"\\notin", "∉"), (r"\\subset", "⊂"), (r"\\cup", "∪"),
    (r"\\cap", "∩"), (r"\\forall", "∀"), (r"\\exists", "∃"), (r"\\angle", "∠"),
    (r"\\perp", "⊥"), (r"\\parallel", "∥"), (r"\\sim", "∼"),
    (r"\\sum", "Σ"), (r"\\prod", "Π"), (r"\\int", "∫"), (r"\\partial", "∂"),
    (r"\\nabla", "∇"), (r"\\prime", "′"), (r"\\dots", "…"), (r"\\ldots", "…"), (r"\\cdots", "⋯"),
    (r"\\alpha", "α"), (r"\\beta", "β"), (r"\\gamma", "γ"), (r"\\delta", "δ"),
    (r"\\varepsilon", "ε"), (r"\\epsilon", "ε"), (r"\\theta", "θ"), (r"\\lambda", "λ"),
    (r"\\mu", "μ"), (r"\\pi", "π"), (r"\\rho", "ρ"), (r"\\sigma", "σ"),
    (r"\\tau", "τ"), (r"\\varphi", "φ"), (r"\\phi", "φ"), (r"\\omega", "ω"),
    (r"\\Delta", "Δ"), (r"\\Omega", "Ω"), (r"\\quad", "  "), (r"\\qquad", "   "),
]


def _plain_tex(s):
    """
    没有 LaTeX 时的兜底清理：把命令换成能看懂的符号。

    不做这一步，屏幕上会原样糊着 `0\\times1+1\\times0=0`（2026-09-13 实测截图）——
    "公式退化"可以接受，"把 LaTeX 源码当正文显示"不行。这里是降级路径，
    宁可排得糙，也不能让用户看到看不懂的东西。
    """
    t = str(s)
    for k, v in _TEX_SYMBOLS:            # 长的先替，避免 \\le 吃掉 \\leq
        t = t.replace(k, v)
    t = _re.sub(r"\\\\frac\\{([^{}]*)\\}\\{([^{}]*)\\}", r"(\\1)/(\\2)", t)
    t = _re.sub(r"\\\\sqrt\\{([^{}]*)\\}", r"√(\\1)", t)
    t = _re.sub(r"\\\\text\\{([^{}]*)\\}", r"\\1", t)
    # \\mathbb{R} / \\mathrm{d}x 这类"只换字体"的包裹：直接把内容取出来
    t = _re.sub(r"\\\\(?:mathbb|mathrm|mathbf|mathcal|mathit)\\{([^{}]*)\\}", r"\\1", t)
    t = _re.sub(r"\\^\\{([^{}]*)\\}", r"^\\1", t)
    t = _re.sub(r"_\\{([^{}]*)\\}", r"_\\1", t)
    for dead in ("\\\\left", "\\\\right", "\\\\bigl", "\\\\bigr", "\\\\displaystyle"):
        t = t.replace(dead, " ")
    for thin in ("\\\\,", "\\\\;", "\\\\!", "\\\\ "):     # 细空格命令，直接吃掉
        t = t.replace(thin, "")
    # 剩下的 \\sin / \\cos 之类：去掉反斜杠、留着名字，比整段消失强
    t = _re.sub(r"\\\\([A-Za-z]+)", r"\\1", t)
    for ch in ("{", "}", "$"):
        t = t.replace(ch, "")
    return " ".join(t.split())



def _cells(tbl, rows, cols):
    """
    取表格的若干格（Polygon 的 VGroup）。行/列号从 1 开始，和 manim 的
    Table.get_cell 一致 —— 索引基准刻意跟 manim 对齐，少一层翻译少一次错位。
    """
    return VGroup(*[tbl.get_cell((r, c)) for r in rows for c in cols])


def _cells_center(tbl, rows, cols):
    """若干格的共同中心（滑动窗口就是"挪到这个中心"）。"""
    return _cells(tbl, rows, cols).get_center()


def _cell_box(tbl, rows, cols, color=RED, stroke_width=3.5, buff=0.04):
    """
    正好框住表格若干格的方框。

    为什么要有它：Manim 的 MobjectTable 单元格尺寸是**由内容撑出来的**
    （h_buff=1.3 × 最宽条目），写死坐标的画框必然和网格错位 ——
    2026-09-13 实测：模型用 rect(1.9×1.9) + move_to 去框 2×2 的格子，
    框只盖住了半个格子区域，核的数字浮在框外。位置/大小都从格子算，
    就永远不会错位。
    """
    return SurroundingRectangle(_cells(tbl, rows, cols), color=color,
                                stroke_width=stroke_width, buff=buff)
'''


# ==============================================================================
# 元素规格表：一份 spec，两个用途 —— 校验 + 自动生成给 LLM 的说明文档
# ==============================================================================

# 类型标记：n=数  i=整数  s=字符串  b=布尔  c=颜色  rng=范围数组
#           pt=点  expr=数学表达式  ref=元素id  lp=点数组  ll=字符串二维数组  lst=任意数组
T_NUM, T_INT, T_STR, T_BOOL = "n", "i", "s", "b"
T_COLOR, T_RANGE, T_POINT, T_EXPR = "c", "rng", "pt", "expr"
T_REF, T_POINTS, T_TABLE, T_LIST = "ref", "lp", "ll", "lst"
T_COORD = "coord"   # 数字 / "$tracker" / 数学表达式（比 T_NUM 更宽）

ELEMENT_SPEC = {
    "text": {
        "desc": "中文文字块，支持 $...$ 行内公式与 \\n 换行",
        "params": {
            "content": (T_STR, "", "文字内容，$ 内为 LaTeX"),
            "font_size": (T_INT, 30, "字号 8~72"),
            "color": (T_COLOR, "WHITE", "颜色"),
        },
    },
    "formula": {
        "desc": "独立的 LaTeX 公式（可含中文，走 ctex）",
        "params": {
            "content": (T_STR, "", "LaTeX 源码"),
            "font_size": (T_INT, 40, "字号 8~96"),
            "color": (T_COLOR, "ACCENT", "颜色"),
            "tex_colors": (T_BOOL, False,
                           "true = 整体不上色，改由 content 里的 "
                           "{\\color{ACCENT}\\Delta x} 给个别符号上色"
                           "（名字用颜色表里的纯字母名，如 ACCENT/RED/TEAL）"),
        },
    },
    "axes": {
        "desc": "直角坐标系，其它图形元素的定位基准",
        "params": {
            "x_range": (T_RANGE, [-5, 5, 1], "[min,max,step]"),
            "y_range": (T_RANGE, [-3, 3, 1], "[min,max,step]"),
            "x_length": (T_NUM, 9.0, "横轴像素长度 2~13"),
            "y_length": (T_NUM, 5.2, "纵轴像素长度 1.5~7"),
            "numbers": (T_BOOL, True, "是否显示刻度数字"),
            "numbers_style": (T_STR, "latex", "刻度数字渲染方式：latex（数学字体，需编译）/ text（快）"),
            "tips": (T_BOOL, True, "是否画箭头"),
        },
    },
    "three_axes": {
        "desc": "三维坐标系（xyz 三轴）—— 空间曲面 / 空间曲线 / 空间点的基准，"
                "配合 rotate_camera 转动视角才有立体感",
        "params": {
            "x_range": (T_RANGE, [-4, 4, 1], "[min,max,step]"),
            "y_range": (T_RANGE, [-4, 4, 1], "[min,max,step]"),
            "z_range": (T_RANGE, [-3, 3, 1], "[min,max,step]"),
            "x_length": (T_NUM, 7.0, "x 轴长度 2~11"),
            "y_length": (T_NUM, 7.0, "y 轴长度 2~11"),
            "z_length": (T_NUM, 5.0, "z 轴长度 1.5~8"),
            "numbers": (T_BOOL, True, "是否显示刻度数字"),
            "numbers_style": (T_STR, "latex", "刻度数字渲染方式：latex / text（快）"),
            "phi": (T_NUM, 70.0, "初始俯仰角 0~90（0=平视、90=从正上方俯视）"),
            "theta": (T_NUM, -45.0, "初始水平转角 -360~360"),
        },
    },
    "number_plane": {
        "desc": "带网格的平面（讲向量/复数时用）",
        "params": {
            "x_range": (T_RANGE, [-5, 5, 1], "[min,max,step]"),
            "y_range": (T_RANGE, [-3, 3, 1], "[min,max,step]"),
            "opacity": (T_NUM, 0.35, "网格线不透明度"),
        },
    },
    "plot": {
        "desc": "函数图像，挂在某个 axes 上",
        "params": {
            "axes": (T_REF, None, "所属坐标系 id"),
            "expr": (T_EXPR, "x", "f(x)，如 x**2 / sin(x)/x"),
            "x_range": (T_RANGE, None, "默认沿用 axes 的 x_range；可写 [\"$t0\",\"$t\"] 让它随追踪器生长"),
            "color": (T_COLOR, "PRIMARY", "颜色"),
            "stroke_width": (T_NUM, 3.5, "线宽 0.5~10"),
            "opacity": (T_NUM, 1.0, "不透明度 0~1"),
        },
    },
    "parametric": {
        "desc": "参数曲线 (fx(t), fy(t))",
        "params": {
            "axes": (T_REF, None, "所属坐标系 id"),
            "fx": (T_EXPR, "cos(t)", "x = fx(t)"),
            "fy": (T_EXPR, "sin(t)", "y = fy(t)"),
            "t_range": (T_RANGE, [0, 6.283, 0.05], "[tmin,tmax,step]"),
            "color": (T_COLOR, "YELLOW", "颜色"),
            "stroke_width": (T_NUM, 3.0, "线宽"),
        },
    },
    "surface": {
        "desc": "三维曲面 z = f(x,y)（必须挂在 three_axes 上；用 rotate_camera 转视角）",
        "params": {
            "axes": (T_REF, None, "所属三维坐标系 id（必须是 three_axes）"),
            "expr": (T_EXPR, "x**2 + y**2", "z = f(x,y)，可用 x、y 与数学函数"),
            "u_range": (T_RANGE, [-2, 2, 0.2], "x 的取值范围 [min,max]"),
            "v_range": (T_RANGE, [-2, 2, 0.2], "y 的取值范围 [min,max]"),
            "color": (T_COLOR, "PRIMARY", "面色"),
            "opacity": (T_NUM, 0.85, "不透明度 0.1~1"),
        },
    },
    "space_curve": {
        "desc": "空间曲线 (fx(t), fy(t), fz(t))（必须挂在 three_axes 上）",
        "params": {
            "axes": (T_REF, None, "所属三维坐标系 id（必须是 three_axes）"),
            "fx": (T_EXPR, "cos(t)", "x = fx(t)"),
            "fy": (T_EXPR, "sin(t)", "y = fy(t)"),
            "fz": (T_EXPR, "t", "z = fz(t)"),
            "t_range": (T_RANGE, [0, 6.283, 0.05], "[tmin,tmax,step]"),
            "color": (T_COLOR, "YELLOW", "颜色"),
            "stroke_width": (T_NUM, 3.0, "线宽"),
        },
    },
    "area": {
        "desc": "曲线下的填充面积（定积分几何意义）",
        "params": {
            "axes": (T_REF, None, "所属坐标系 id"),
            "curve": (T_REF, None, "被积曲线 id"),
            "x_range": (T_RANGE, None, "[a,b]"),
            "color": (T_COLOR, "ACCENT", "填充色"),
            "opacity": (T_NUM, 0.42, "填充不透明度"),
        },
    },
    "riemann": {
        "desc": "黎曼矩形（演示「分割—求和—取极限」的动态过程）",
        "params": {
            "axes": (T_REF, None, "所属坐标系 id"),
            "curve": (T_REF, None, "被积曲线 id"),
            "x_range": (T_RANGE, None, "[a,b]"),
            "n": (T_INT, 16, "矩形个数 1~80"),
            "color": (T_COLOR, "PRIMARY", "颜色"),
            "opacity": (T_NUM, 0.55, "填充不透明度"),
            "sample": (T_STR, "left", "left/right/center"),
        },
    },
    "dot": {
        "desc": "一个点",
        "params": {
            "at": (T_POINT, None, "位置：坐标数组 或 {ref:axes,x,y}"),
            "color": (T_COLOR, "YELLOW", "颜色"),
            "radius": (T_NUM, 0.09, "半径 0.02~0.6"),
        },
    },
    "line": {
        "desc": "线段 / 虚线",
        "params": {
            "start": (T_POINT, None, "起点"),
            "end": (T_POINT, None, "终点"),
            "color": (T_COLOR, "WHITE", "颜色"),
            "stroke_width": (T_NUM, 3.0, "线宽"),
            "dashed": (T_BOOL, False, "是否虚线"),
        },
    },
    "arrow": {
        "desc": "带箭头的线段 / 向量",
        "params": {
            "start": (T_POINT, None, "起点"),
            "end": (T_POINT, None, "终点"),
            "color": (T_COLOR, "RED", "颜色"),
            "stroke_width": (T_NUM, 4.0, "线宽"),
            "buff": (T_NUM, 0.0, "两端留白"),
        },
    },
    "circle": {
        "desc": "圆",
        "params": {
            "radius": (T_NUM, 1.2, "半径 0.05~6"),
            "at": (T_POINT, None, "圆心（不给则默认画面中心，由 place 决定）"),
            "color": (T_COLOR, "BLUE", "颜色"),
            "stroke_width": (T_NUM, 3.0, "线宽"),
            "fill_opacity": (T_NUM, 0.0, "填充不透明度"),
        },
    },
    "ellipse": {
        "desc": "椭圆",
        "params": {
            "width": (T_NUM, 3.0, "宽"), "height": (T_NUM, 1.8, "高"),
            "color": (T_COLOR, "TEAL", "颜色"),
            "stroke_width": (T_NUM, 3.0, "线宽"),
            "fill_opacity": (T_NUM, 0.0, "填充不透明度"),
        },
    },
    "rect": {
        "desc": "矩形 / 圆角矩形",
        "params": {
            "width": (T_NUM, 3.0, "宽"), "height": (T_NUM, 1.6, "高"),
            "radius": (T_NUM, 0.15, "圆角半径"),
            "color": (T_COLOR, "WHITE", "颜色"),
            "stroke_width": (T_NUM, 2.5, "线宽"),
            "fill_opacity": (T_NUM, 0.0, "填充不透明度"),
        },
    },
    "polygon": {
        "desc": "多边形（几何题主力）",
        "params": {
            "points": (T_POINTS, None, "顶点数组，每项为点"),
            "color": (T_COLOR, "PRIMARY", "颜色"),
            "stroke_width": (T_NUM, 3.0, "线宽"),
            "fill_opacity": (T_NUM, 0.0, "填充不透明度"),
        },
    },
    "angle": {
        "desc": "角标注（三点：a - vertex - c）",
        "params": {
            "a": (T_POINT, None, "边上一点"), "vertex": (T_POINT, None, "顶点"),
            "c": (T_POINT, None, "另一边上一点"),
            "radius": (T_NUM, 0.55, "弧半径"),
            "color": (T_COLOR, "WHITE", "颜色"),
            "right": (T_BOOL, False, "True 则画直角标记"),
        },
    },
    "brace": {
        "desc": "大括号标注，常配 label 说明区间长度",
        "params": {
            "of": (T_REF, None, "被标注元素 id"),
            "direction": (T_STR, "down", "up/down/left/right"),
            "label": (T_STR, "", "括号旁的说明文字"),
            "font_size": (T_INT, 26, "字号"),
            "color": (T_COLOR, "WHITE", "颜色"),
        },
    },
    "table": {
        # ⚠️ 2026-09-17 改（用户反馈"表格太大、单元格也太大"）：原文写着
        # "想统一/紧凑用 cell_w/cell_h/pad_x/pad_y" —— **紧凑该靠 pad，不该碰 cell_**。
        # 模型照着这句话写出 `cell_w: 3.6`（3 列 → 整表 10.8，画面才 14.22，占了 76%），
        # 而它本意只是想"铺得整整齐齐"。所以要在这儿把分工说死：
        #   · 想让格子一样宽 → 用 pad（内容撑 + 统一留白）；
        #   · cell_w/cell_h 只用于"多镜之间格子尺寸必须不变"（如滑动窗口逐镜对齐）。
        "desc": "表格（可放公式）。**格子尺寸默认由内容撑出**，这也是大多数情况该用的写法；"
                "想紧凑就调小 pad_x/pad_y。⚠️ `cell_w`/`cell_h` 是**写死**格子尺寸的，"
                "只在「跨镜要保证格子大小不变」时才用 —— 拿它去把表格铺满画面会做出一个巨大的表。"
                "⚠️ 尺寸感：画面是 14.2 × 8，**一张表建议不超过 8 宽、4 高**（约占半个画面），"
                "3 列 × 0.5 留白的普通数字表大约 5 宽 —— 表格多半应该是画面的一部分，不是全屏",
        "params": {
            "rows": (T_TABLE, None, "二维数组，如 [[\"x\",\"0\",\"1\"],[\"y\",\"0\",\"1\"]]（每行列数必须一致）"),
            "font_size": (T_INT, 24, "字号"),
            "outer_lines": (T_BOOL, True, "是否画外框"),
            "cell_w": (T_NUM, None, "⚠️ 固定格子宽（含留白）：所有格子都是这个宽度。"
                                    "**它是最终尺寸，不是上限** —— 写 3.6 而有三列，整张表就是 10.8 宽"
                                    "（画面才 14.2）。只在跨镜对齐格子时才写，平时**别写**"),
            "cell_h": (T_NUM, None, "⚠️ 固定格子高（含留白），同 cell_w：它是最终尺寸，写大了整张表就高"),
            "pad_x": (T_NUM, None, "格子左右留白，默认 1.3（偏松：3 列光留白就吃掉 3.9 个单位）。"
                                   "紧凑用 0.4~0.6"),
            "pad_y": (T_NUM, None, "格子上下留白，默认 0.8（偏松）。紧凑用 0.25~0.4"),
            "square": (T_BOOL, False, "格子取**正方形**（边长 = 内容+留白里较大的那一侧）。"
                                      "数字网格、矩阵、卷积核这类「一格一个数」的表开着好看；"
                                      "纯文字的表格别开 —— 那会白白多占高度"),
        },
    },
    "cell_box": {
        "desc": "表格选格框：正好框住 table 的若干格（讲卷积/滑动窗口用它，位置由格子决定，不会错位）",
        "params": {
            "of": (T_REF, None, "所属表格（kind=table）的元素 id"),
            "rows": (T_LIST, None, "行号数组，从 1 开始，如 [1,2]"),
            "cols": (T_LIST, None, "列号数组，从 1 开始，如 [1,2]"),
            "color": (T_COLOR, "RED", "颜色"),
            "stroke_width": (T_NUM, 3.5, "线宽"),
            "buff": (T_NUM, 0.04, "相对格子边界的留白"),
        },
    },
    "legend": {
        "desc": "图例",
        "params": {
            "items": (T_LIST, None, "[[颜色, \"说明\"], ...]"),
            "font_size": (T_INT, 20, "字号"),
        },
    },
    "highlight": {
        "desc": "给某个元素加强调框（用来喊「看这里」）",
        "params": {
            "of": (T_REF, None, "被框住的元素 id"),
            "color": (T_COLOR, "ACCENT", "框颜色"),
            "buff": (T_NUM, 0.22, "留白"),
            "radius": (T_NUM, 0.12, "圆角"),
        },
    },
    "tangent_line": {
        "desc": "曲线在某点的切线（自动算斜率，x 可引用 tracker 实时跟踪）",
        "params": {
            "axes": (T_REF, None, "所属坐标系 id"),
            "expr": (T_EXPR, "x", "曲线表达式 f(x)"),
            "x": (T_COORD, 0.0, "切点的 x 坐标，可写 \"$t\""),
            "length": (T_NUM, 2.5, "切线总长度"),
            "color": (T_COLOR, "RED", "颜色"),
            "stroke_width": (T_NUM, 3.0, "线宽"),
        },
    },
    "normal_line": {
        "desc": "曲线在某点的法线（垂直于切线，x 可引用 tracker）",
        "params": {
            "axes": (T_REF, None, "所属坐标系 id"),
            "expr": (T_EXPR, "x", "曲线表达式 f(x)"),
            "x": (T_COORD, 0.0, "法线足的 x 坐标，可写 \"$t\""),
            "length": (T_NUM, 2.5, "法线总长度"),
            "color": (T_COLOR, "TEAL", "颜色"),
            "stroke_width": (T_NUM, 3.0, "线宽"),
        },
    },
    "number": {
        "desc": "动态数字标签（配合 tracker 实时显示斜率、面积等数值）",
        "params": {
            "value": (T_COORD, 0.0, "数值，可写 \"$t\" 或含 tracker 的表达式，如 \"2*t\""),
            "decimals": (T_INT, 2, "小数位数 0~6"),
            "unit": (T_STR, "", "单位后缀，如 \"\\,m^2\"；中文单位（如 \" 块\"）也支持，会自动改用 Text 排版"),
            "font_size": (T_INT, 32, "字号"),
            "color": (T_COLOR, "ACCENT", "颜色"),
        },
    },
    "tracker": {
        "desc": "数值追踪器。其它元素用 \"$id\" 引用它的当前值，即成为动态元素",
        "params": {"value": (T_NUM, 0.0, "初始值")},
    },
    "group": {
        "desc": "把若干已声明元素打包成一个整体，便于统一动画/定位",
        "params": {
            "items": (T_LIST, None, "元素 id 数组"),
            "direction": (T_STR, "down", "排列方向 up/down/left/right"),
            "buff": (T_NUM, 0.35, "间距"),
            "aligned": (T_STR, "left", "对齐边 left/right/top/bottom"),
        },
    },
}

# `replace` 的过渡效果白名单。默认是 crossfade（交叉淡化）而不是形变 —— 依据是观感：
# 底部字幕这类"同一位置换内容"的场景里，ReplacementTransform 会把旧字**飞向新字的位置
# 再变成新字**，字数一多一少就很别扭（2026-09-16 用户反馈："对这种底部字幕效果看起来
# 不是很好"）。四种效果**对场景的影响完全一致**（旧对象下屏、新对象上屏），
# 所以 `_Builder.act()` 里那句 `self.alias[a] = ...` 换绑对四种都一样成立。
REPLACE_EFFECTS = ("crossfade", "slide", "write", "morph")
REPLACE_EFFECT_DEFAULT = "crossfade"
# slide 的位移量：旧的上移淡出、新的从下方顶上来（同一个 shift 就是"往上走一条"）
REPLACE_SLIDE_SHIFT = 0.35

# 每个动作允许的**附加参数名**（机器可读版）。
#
# ⚠️ 这是 DSL 对外契约的一部分：`ACTION_SPEC` 里 `args` 那句是给人和模型看的散文，
#    这张表是给校验层用的名单，**两者必须一起改**。
#    ⚠️ 改这张表要当成"改契约"来对待：名单少写一个 → 合法的分镜被误拒
#    （会白烧一轮 LLM 重试，比漏报更坏）；多写一个 → 静默失败又回来了。
#
# 为什么必须成表（2026-09-17）：`_norm_action` 原来只按 `do` 分支读它认识的键，
# **多写的键被静静丢掉** —— 模型写 `{"do":"create","target":"x","duration":2}`
# （想说时长但名字写错）时什么都不发生，动画照样用默认时长。
# 要报错就必须先能回答"这个动作允许哪些键"。
#
# `do` / `run_time` / `lag_ratio` / `target` / `inline` / `inline_into` 是所有动作
# 通用的（见 _ACTION_UNIVERSAL），不在这里重复列。
_ACTION_KEYS = {
    "create": (), "write": (), "fade_in": ("shift",), "grow": ("from",),
    "draw_border": (), "show": (), "fade_out": (), "remove": (),
    "indicate": ("color",), "circumscribe": ("color",), "flash": ("color",),
    "wiggle": (), "focus": (), "clear_all": (),
    "transform": ("into",), "replace": ("into", "effect"),
    "shift": ("vector",),
    # point 是正式写法；to / target_point 是代码里**故意容忍**的历史别名，别删
    "move_to": ("point", "to", "target_point"),
    "move_cells": ("of", "rows", "cols"),
    "scale": ("factor",), "rotate": ("angle",),
    "set_color": ("color",), "set_opacity": ("opacity",),
    "set_stroke": ("color", "width"),
    "stretch": ("factor", "dim"),
    "move_along": ("path",), "trace": ("color", "width"),
    "tracker_to": ("to", "rate_func"),
    "rotate_camera": ("theta", "phi", "zoom"),
    "wait": ("time",), "parallel": ("actions",),
}
_ACTION_UNIVERSAL = ("do", "run_time", "lag_ratio", "target", "inline", "inline_into")


ACTION_SPEC = {
    "create": {"desc": "Create：沿路径画出线条图形", "target": True, "run_time": 1.2},
    "write": {"desc": "Write：书写文字/公式", "target": True, "run_time": 1.0},
    "fade_in": {"desc": "FadeIn：淡入（可带 shift 方向）", "target": True, "run_time": 0.6,
                "args": "shift"},
    "grow": {"desc": "GrowFromCenter / GrowFromEdge：生长出现", "target": True, "run_time": 0.8,
             "args": "from"},
    "draw_border": {"desc": "DrawBorderThenFill：先描边再填充", "target": True, "run_time": 1.2},
    "show": {"desc": "直接 add 上屏，无动画（动态元素用这个）", "target": True, "run_time": 0},
    "transform": {"desc": "Transform：把一个元素变形为另一个", "target": True, "run_time": 1.6,
                  "args": "into（目标元素 id，或内联一个元素对象）"},
    "replace": {"desc": "替换式换内容：旧的下屏、新的上屏，默认交叉淡化"
                        "（同一位置换字用它，不要另 create 一个叠上去）",
                "target": True, "run_time": 1.6,
                "args": 'into（目标元素 id，或内联一个元素对象）+ effect'
                        '（可选：crossfade 淡出淡入（默认）/ slide 上滑交叉 / '
                        'write 逐字写出 / morph 形变）'},
    "indicate": {"desc": "Indicate：闪烁强调", "target": True, "run_time": 1.0, "args": "color"},
    "circumscribe": {"desc": "Circumscribe：画圈圈强调", "target": True, "run_time": 1.0,
                     "args": "color"},
    "flash": {"desc": "Flash：闪光", "target": True, "run_time": 0.8, "args": "color"},
    "wiggle": {"desc": "Wiggle：抖动", "target": True, "run_time": 1.0},
    "focus": {"desc": "FocusOn：聚焦光圈", "target": True, "run_time": 0.8},
    "fade_out": {"desc": "FadeOut：淡出", "target": True, "run_time": 0.5},
    "remove": {"desc": "直接移除，无动画", "target": True, "run_time": 0},
    "shift": {"desc": "平移", "target": True, "run_time": 0.8,
              "args": "vector（必须写 vector=[dx,dy]，不是 point）"},
    "move_to": {"desc": "移动到指定点", "target": True, "run_time": 0.8,
                "args": "point=[x,y] / {ref,x,y} / {of,anchor}（写 to= 也认，但请统一写 point）"},
    "move_cells": {"desc": "把 cell_box 挪到另一组格子上并贴合它们（滑动窗口 / "
                           "候选范围缩小都用它；位置和大小都由格子算，不会错位）",
                   "target": True, "run_time": 1.0,
                   "args": "of（表格 id）+ rows/cols（目标格号，行/列号从 1 开始）"},
    "scale": {"desc": "缩放", "target": True, "run_time": 0.7, "args": "factor"},
    "rotate": {"desc": "旋转（角度制）", "target": True, "run_time": 1.0, "args": "angle"},
    "set_color": {"desc": "改颜色", "target": True, "run_time": 0.5, "args": "color"},
    "set_opacity": {"desc": "改不透明度", "target": True, "run_time": 0.5, "args": "opacity"},
    "set_stroke": {"desc": "改描边（颜色/线宽）", "target": True, "run_time": 0.5,
                   "args": "color / width"},
    "stretch": {"desc": "沿某方向拉伸", "target": True, "run_time": 0.8, "args": "factor / dim(x|y)"},
    "move_along": {"desc": "沿某条曲线移动", "target": True, "run_time": 2.0,
                   "args": "path（曲线元素 id）"},
    "trace": {"desc": "给动点加拖尾轨迹（TracedPath）", "target": True, "run_time": 0,
              "args": "color / width"},
    "tracker_to": {"desc": "驱动追踪器变化（带它的元素会自动跟着动）", "target": True, "run_time": 3.0,
                   "args": "to（目标数值）+ rate_func"},
    "rotate_camera": {"desc": "转动三维视角（相机绕场景转，只对含 three_axes 的分镜有意义）",
                      "target": False, "run_time": 2.5,
                      "args": "theta（水平转角 -360~360，负值反向）/ phi（俯仰角 0~90，"
                              "0=平视、90=正上方俯视）/ zoom（缩放 0.1~8）；至少给一个"},
    "wait": {"desc": "停顿", "target": False, "run_time": 0.5, "args": "time"},
    "clear_all": {"desc": "FadeOut 并移除所有元素（一键清屏）", "target": False, "run_time": 0.5},
    "parallel": {"desc": "并行执行多个子动作（每个可有独立 run_time）", "target": False, "run_time": None,
                 "args": "actions（子动作数组）"},
}


# ==============================================================================
# 校验
# ==============================================================================

def _check_expr_ast(expr, where):
    """
    数学表达式走 AST 白名单 —— 比正则靠谱得多，也就能放开更多函数。
    允许：数字、x、白名单函数/常量、四则与幂、正负号、比较、条件表达式、tracker 名。
    """
    import ast
    if not isinstance(expr, str) or not expr.strip():
        raise DSLError(f"{where}: 表达式不能为空")
    if '"' in expr or "'" in expr:
        raise DSLError(f"{where}: 表达式不能含引号（收到 {expr!r}）")
    if len(expr) > 200:
        raise DSLError(f"{where}: 表达式过长（>200 字符）")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise DSLError(f"{where}: 表达式语法错误 {expr!r}（{e}）")

    ok_binop = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id != "x" and node.id not in SAFE_NAMES:
                # 可能是 tracker 名，由上层按已声明集合再判断
                if not _IDENT.match(node.id):
                    raise DSLError(f"{where}: 非法标识符 {node.id!r}")
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise DSLError(f"{where}: 表达式里只能有数字常量")
        elif isinstance(node, ast.Call):
            # 只允许调用白名单里的数学函数：os.system(...) 走 Attribute 会被上一条拦，
            # __import__(...) 这种则在这里被函数名检查拦下
            if not isinstance(node.func, ast.Name) or node.func.id not in SAFE_NAMES:
                raise DSLError(
                    f"{where}: 只允许调用数学函数 {sorted(SAFE_NAMES)}"
                )
        elif isinstance(node, (ast.BinOp,)):
            if not isinstance(node.op, ok_binop):
                raise DSLError(f"{where}: 不支持的运算符")
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.UAdd, ast.USub)):
                raise DSLError(f"{where}: 不支持的一元运算符")
        elif isinstance(node, (ast.IfExp, ast.Compare, ast.Tuple)):
            pass
        elif isinstance(node, (ast.Expression, ast.Load, ast.Add, ast.Sub, ast.Mult,
                               ast.Div, ast.Pow, ast.Mod, ast.FloorDiv, ast.USub,
                               ast.UAdd, ast.keyword, ast.Gt, ast.Lt, ast.GtE,
                               ast.LtE, ast.Eq, ast.NotEq, ast.And, ast.Or)):
            pass
        else:
            raise DSLError(f"{where}: 表达式含不允许的语法 {type(node).__name__}")
    return expr


def _check_color(v, where):
    if not isinstance(v, str):
        raise DSLError(f"{where}: 颜色必须是字符串")
    if v in COLORS:
        return v
    if HEX_COLOR.match(v):
        return v.upper()
    # 不再写 "BLUE/RED/..." 这种带省略号的短名单：它和规格里那份全表对不上，
    # 模型照着报错去猜反而更容易接着猜错。改成"是一个固定集合，去规格里抄"。
    raise DSLError(
        f"{where}: 非法颜色 {v!r}。颜色只能用规格「硬性规则」里列出的那 {len(COLORS)} 个"
        f"常量名之一，或 `#RRGGBB`"
    )


def _as_coord(v, where):
    """
    点坐标的值：数字 / "$tracker" / 数学表达式。

    允许表达式是关键 —— 否则 {ref:ax, x:"$t", y:"t**2"} 这种
    「动点沿抛物线滑动」的需求根本表达不出来（y 得跟着 t 算）。
    """
    if isinstance(v, bool):
        raise DSLError(f"{where}: 这里不接受布尔值")
    if isinstance(v, (int, float)):
        f = float(v)
        if f != f or abs(f) > 1e5:
            raise DSLError(f"{where}: 数值越界（{v}），限制 |v| <= 1e5")
        return f
    if isinstance(v, str):
        if _TRACKER_REF.match(v):
            return v
        # 表达式里也允许混写 $t（等价于直接写 t），写起来更直观
        return _check_expr_ast(_strip_dollar(v), where)
    raise DSLError(f"{where}: 需要数字、\"$追踪器名\" 或数学表达式，收到 {v!r}")


def _strip_dollar(s):
    """把表达式里的 $name 还原成 name —— $ 只是给读 JSON 的人看的提示。"""
    return re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", r"\1", s)


def _check_point(v, where, declared):
    """点：坐标数组 / {ref,x,y} / {ref,x,y,z}（三维）/ {of,anchor}。"""
    if isinstance(v, list):
        if len(v) != 2:
            raise DSLError(f"{where}: 坐标数组必须是 [x, y]")
        return [_as_coord(v[0], f"{where}[0]"), _as_coord(v[1], f"{where}[1]")]
    if isinstance(v, dict):
        if "ref" in v:
            rid = v["ref"]
            if rid not in declared:
                raise DSLError(f"{where}: 引用了未声明的元素 {rid!r}")
            if "x" not in v or "y" not in v:
                raise DSLError(f"{where}: {{ref,x,y}} 缺 x 或 y")
            # x / y 允许是 "$tracker" 或表达式，这样点就能沿着曲线跑
            out = {"ref": rid,
                   "x": _as_coord(v["x"], f"{where}.x"),
                   "y": _as_coord(v["y"], f"{where}.y")}
            # z 是**可选**的：只有 three_axes 上的空间点才写（z=0 也要写出来才算三维点）。
            # 「写了 z、但 ref 指向 2D 的 axes」必须拦下 —— manim 的
            # `Axes.coords_to_point` 用的是 `zip(other_axes, coords[1:], strict=False)`，
            # 第三个坐标会被**静默丢掉**，画出来看着像对、其实全错（见 _check_3d）。
            if v.get("z") is not None:
                out["z"] = _as_coord(v["z"], f"{where}.z")
            return out
        if "of" in v:
            if v["of"] not in declared:
                raise DSLError(f"{where}: 引用了未声明的元素 {v['of']!r}")
            anchor = str(v.get("anchor", "center")).lower()
            if anchor not in ANCHORS:
                raise DSLError(f"{where}: 未知锚点 {anchor!r}，可用 {sorted(ANCHORS)}")
            return v
        raise DSLError(f"{where}: 点必须是 [x,y] 或 {{{{ref,x,y}}}} 或 {{{{of,anchor}}}}")
    raise DSLError(f"{where}: 点的格式无法识别（收到 {type(v).__name__}）")


def _as_num_or_ref(v, where):
    """数值：允许数字，或 \"$tracker\" 追踪器引用。"""
    if isinstance(v, bool):
        raise DSLError(f"{where}: 这里不接受布尔值")
    if isinstance(v, (int, float)):
        f = float(v)
        if f != f or abs(f) > 1e5:
            raise DSLError(f"{where}: 数值越界（{v}），限制 |v| <= 1e5")
        return f
    if isinstance(v, str) and _TRACKER_REF.match(v):
        return v
    raise DSLError(f"{where}: 需要数字或 \"$追踪器名\"，收到 {v!r}")


def _check_range(v, where):
    if not isinstance(v, list) or len(v) < 2:
        raise DSLError(f"{where}: 范围必须是 [min, max] 或 [min, max, step]")
    lo, hi = v[0], v[1]
    if isinstance(lo, str) and _TRACKER_REF.match(lo):
        pass
    else:
        lo = _as_num_or_ref(lo, f"{where}[0]")
    hi = _as_num_or_ref(hi, f"{where}[1]")
    if not (isinstance(lo, str) or isinstance(hi, str)):
        if float(hi) <= float(lo):
            raise DSLError(f"{where}: max 必须大于 min（当前 {lo} -> {hi}）")
        if float(hi) - float(lo) > 500:
            raise DSLError(f"{where}: 跨度过大（{float(hi) - float(lo)}），图像会不可读")
    if len(v) > 2:
        _as_num_or_ref(v[2], f"{where}[2]")
    return v


def _check_cell_ids(v, where):
    """
    格号数组（cell_box 的 rows / cols）。行、列号都**从 1 开始**。

    为什么从 1 开始：manim 的 `Table.get_cell((r, c))` 就是 1-based。DSL 里换成
    0-based 的话，代码里每次取格都要 +1 换算一遍 —— 那是给自己造错位的机会，
    而错位是"画面能出、内容全错"的静默失败。索性跟 manim 对齐，只有一种基准。

    ⚠️ 这里**不再有"最多 6 个"**（2026-09-16 删）：条数和"框会不会出界"毫无关系。
    `[1,2,3,4,5,6,7,8]` 选一整行 8 格是完全正常的写法，以前会被硬拒。
    真正要保证的是"格号落在表格范围内"（`_check_cell_refs._in_range` 管）。
    """
    if not isinstance(v, list) or not v:
        raise DSLError(f"{where}: 需要非空的格号数组，如 [1,2]（行/列号从 1 开始）")
    out = []
    for i, x in enumerate(v):
        if isinstance(x, bool) or not isinstance(x, int):
            raise DSLError(f"{where}[{i}]: 格号必须是整数，收到 {x!r}")
        if x < 1:
            raise DSLError(f"{where}[{i}]: 格号从 1 开始，收到 {x}")
        out.append(x)
    if len(set(out)) != len(out):
        raise DSLError(f"{where}: 格号不能重复（{out}）")
    return sorted(out)


# 每种元素的必填参数（不写就一定会生成出错误代码，必须在校验期拦掉）
REQUIRED = {
    "text": ["content"], "formula": ["content"],
    "plot": ["axes", "expr"], "parametric": ["axes", "fx", "fy"],
    # 三维三个同理：不给 expr/fx/fy/fz 就会拿默认值静默画出一个别的曲面/曲线
    "surface": ["axes", "expr"], "space_curve": ["axes", "fx", "fy", "fz"],
    "area": ["axes", "curve", "x_range"], "riemann": ["axes", "curve", "x_range"],
    "dot": ["at"], "line": ["start", "end"], "arrow": ["start", "end"],
    "polygon": ["points"], "angle": ["a", "vertex", "c"],
    "brace": ["of"], "table": ["rows"], "legend": ["items"],
    "highlight": ["of"], "group": ["items"], "cell_box": ["of", "rows", "cols"],
    "tangent_line": ["axes", "expr"], "normal_line": ["axes", "expr"],
}


def _carry_element(el, declared, trackers, where, prev):
    """
    承接形态：`{"id": "arr", "carry": true}` —— 这个元素**在上一分镜结束时就在屏上**，
    本分镜不要重新画它，也不要在分镜边界把它清掉。

    为什么只允许写 id、不写 kind/参数：内容由上一分镜的声明决定。若允许在这里重写一遍，
    模型改了 rows 却因为"承接"而不重建 —— 改了不生效，正是本项目最忌讳的静默失效。
    要改画面，请在本分镜的 timeline 里用动作（move_to / set_color / transform …）。

    ⚠️ 但**展开形态必须也接受**：本函数会把 stub 展开成"上一镜的完整声明 + carry: true"，
    而规范化产物是会被喂回校验入口的（replace-scene、`validate(validate(x))`）。
    只认 stub 的话第二遍就会把规范化自己产出的东西判成"多写了参数"—— 不幂等，
    与 HANDOFF §8.6 / §8.8 记录的是同一类问题。判据：带 `kind` 就是展开形态。
    """
    eid = el.get("id")
    if not isinstance(eid, str) or not _IDENT.match(eid):
        raise DSLError(
            f"{where}: id 必须是字母/数字/下划线且不能数字开头，收到 {eid!r}"
        )
    if eid in declared:
        raise DSLError(f"{where}: id {eid!r} 重复")
    if not prev:
        raise DSLError(
            f"{where}: 第一个分镜不能写 carry —— 它没有「上一分镜」可以承接，"
            f"元素必须在 elements 里完整声明"
        )
    prev_els = prev.get("elements") or {}
    if eid not in prev_els:
        raise DSLError(
            f"{where}: carry 的 {eid!r} 在**上一个分镜**里没有声明过，承接不到。"
            f"上一分镜声明了: {sorted(prev_els)}"
        )
    src = prev_els[eid]

    if "kind" in el:
        # 展开形态：内容必须和上一分镜**逐字段一致**。不一致就是"改了参数却不会生效"
        # （承接元素在 reuse 模式下根本不会被重建），必须报出来而不是默默忽略。
        mine = {k: v for k, v in el.items() if k != "carry"}
        theirs = {k: v for k, v in src.items() if k != "carry"}
        if mine != theirs:
            diff = sorted(k for k in set(mine) | set(theirs)
                          if mine.get(k) != theirs.get(k))
            raise DSLError(
                f"{where}: carry 元素 {eid!r} 的声明和上一分镜里的不一致"
                f"（{diff}）—— 承接是「原样还在」，在这里重写它的参数不会生效。"
                f"要改画面请在本分镜的 timeline 里用动作（move_to / set_color / transform）"
            )
    else:
        extra = set(el) - {"id", "carry"}
        if extra:
            raise DSLError(
                f"{where}: carry 元素只写 {{\"id\": ..., \"carry\": true}}，"
                f"多写了 {sorted(extra)}。它的内容沿用上一分镜里的声明；"
                f"要改画面请用动作（move_to / set_color / transform）"
            )

    live = prev.get("on_screen") or set()
    if eid not in live:
        raise DSLError(
            f"{where}: carry 的 {eid!r} 在上一分镜结束时**并不在屏幕上**，"
            f"承接过来会是一片空白。上一分镜结束时在屏上的是: {sorted(live)}"
            f"（声明过但没被 create / write / fade_in / grow / draw_border / show "
            f"动过的元素，不算在屏上）"
        )
    out = dict(src)
    out["carry"] = True
    declared.add(eid)
    if out.get("kind") == "tracker":
        trackers.add(eid)
    return out


def _norm_element(el, declared, trackers, where, prev=None):
    """校验并规范化一个元素定义，返回新 dict。"""
    if not isinstance(el, dict):
        raise DSLError(f"{where}: 元素必须是对象")
    if el.get("carry"):
        return _carry_element(el, declared, trackers, where, prev)
    eid = el.get("id")
    if not isinstance(eid, str) or not _IDENT.match(eid):
        raise DSLError(
            f"{where}: id 必须是字母/数字/下划线且不能数字开头，收到 {eid!r}"
        )
    if eid in declared:
        raise DSLError(f"{where}: id {eid!r} 重复")
    kind = el.get("kind")
    if kind not in ELEMENT_SPEC:
        raise DSLError(
            f"{where}: 未知元素类型 {kind!r}。可用: {sorted(ELEMENT_SPEC)}"
        )

    spec = ELEMENT_SPEC[kind]["params"]
    out = {"id": eid, "kind": kind}

    # ⚠️ 多写的参数**必须报错，不能静默丢掉**（2026-09-17 修）。
    #
    # 原来是把它们收进 `out["_dropped"]` 就完事 —— 而那个字段**除它自己之外没有任何
    # 地方读取**，于是模型多写参数时什么都不发生：它以为"把这段文字转了 30 度"，
    # 画面纹丝不动。这是标准的静默失败（"能出片、内容是错的"），本项目最忌讳的一类。
    #
    # 影响面先量过（这才是敢改成硬报错的前提）：
    #   · 25 个 examples 里 **0 处**多写参数；
    #   · 调用留档里 49 条成功回复、992 个元素里也是 **0 处**。
    # 也就是说这条路径目前只是"潜伏"，改成报错不会误伤任何存量数据。
    #
    # 下划线开头的是**给人看的注释**（examples 在用 `_note` / `_comment`），照旧忽略；
    # id/kind/place/live/_dropped 是通用键，不算多写。
    # （`_dropped` 是规范化自己产出的元信息，必须排除，否则第二遍会把它当成
    #   "模型多写的未知参数"再记一次 —— 2026-09-14 由 tests/ 抓出的不幂等形态。）
    _UNIVERSAL = {"id", "kind", "place", "live", "_dropped"}
    unknown = sorted(k for k in el
                     if k not in spec and k not in _UNIVERSAL
                     and not str(k).startswith("_"))
    if unknown:
        raise DSLError(
            f"{where}: {kind} 没有这些参数：{unknown}。"
            f"{kind} 支持的参数是：{'、'.join(spec)}。"
            f"（要调位置 / 大小 / 角度，那是 place 里的键，写成 "
            '{"place":[{"rotate":30}]} 这种形式，见规格「布局」一节；'
            f"要改外观用动作，如 set_color / scale。这些参数不会生效，所以直接报出来。）"
        )

    out["live"] = bool(el.get("live", False))
    for k, v in el.items():
        if k in ("id", "kind", "place", "live", "_dropped"):
            continue
        if k not in spec:
            continue
        t, default, _ = spec[k]
        nv = _norm_value(v, t, f"{where}.{k}", declared)
        # default is None 表示"这个参数是可选的"。可选参数**没写就不写进输出**：
        # 一旦补成 {"x_range": None}，第二遍就会把"没写"读成"写了个 null"而报错
        # （HANDOFF §8.6 的根因之一，tests/ 里有定点用例盯着）。
        if nv is None and default is None:
            continue
        out[k] = nv

    for k in spec:
        if k not in out:
            d = spec[k][1]
            if d is None:
                continue                  # 可选参数没写：不补 None（理由见上）
            out[k] = d
    # 必填检查必须看"原始 JSON 里有没有写"，不能看填充后的值：
    # plot 的 expr 默认值是 "x"，漏写会静默画出 y=x —— 又是一个"能跑但内容错了"。
    for k in REQUIRED.get(kind, []):
        if k not in el or out.get(k) is None:
            raise DSLError(f"{where}: {kind} 缺少必填参数 {k}")

    # 复合字段的内部校验（T_LIST / T_TABLE 只做了粗校验，这里按语义补上）
    if kind == "legend":
        norm = []
        for i, it in enumerate(out["items"]):
            if not (isinstance(it, list) and len(it) >= 2):
                raise DSLError(f"{where}.items[{i}]: 图例项必须是 [颜色, \"说明\"]")
            norm.append([_check_color(it[0], f"{where}.items[{i}][0]"), str(it[1])[:80]])
        out["items"] = norm
    if kind == "group":
        for it in out["items"]:
            if not isinstance(it, str) or it not in declared:
                raise DSLError(f"{where}.items: 引用了未声明的元素 {it!r}")

    if kind == "cell_box":
        # 位置完全由格子决定 —— 允许 place 的话，框会被挪走，又变成"框和数字错位"。
        if "place" in el:
            raise DSLError(
                f"{where}: cell_box 的位置由格子算出来，不能写 place"
                "（要让它滑到别的格子请用 {\"do\":\"move_cells\"} 动作）"
            )
        out["rows"] = _check_cell_ids(out["rows"], f"{where}.rows")
        out["cols"] = _check_cell_ids(out["cols"], f"{where}.cols")

    if kind == "tracker":
        trackers.add(eid)
    if "place" in el:
        out["place"] = _norm_place(el["place"], f"{where}.place", declared)

    # 未知参数现在直接报错（见上面），所以这里恒为空 —— 保留字段是为了让规范化产物的
    # **形状稳定**（幂等测试逐字段比对，少一个键也算"变了"）。
    out["_dropped"] = []
    declared.add(eid)
    return out


def _norm_value(v, t, where, declared):
    # None 原样穿过（不在这里报错，也不转成别的形态）：
    # "这个参数没写"是可选参数的合法状态，由调用方按 ELEMENT_SPEC 的默认值决定
    # 是"补默认值"还是"就保持没写"。在这里统一报错会让所有可选参数都得写一遍。
    if v is None:
        return None
    if t == T_NUM:
        return _as_num_or_ref(v, where)
    if t == T_COORD:
        return _as_coord(v, where)
    if t == T_INT:
        f = _as_num_or_ref(v, where)
        if isinstance(f, str):
            raise DSLError(f"{where}: 这里不能用追踪器")
        return int(f)
    if t == T_STR:
        # 这个上限**不是排版判据**，只用来挡病态输入（几十万字的字符串会把 Text 对象和
        # 渲染时间拖垮）。真正的"放不下"由 _check_layout_fits() 按内容估算来判。
        # 上限抬到 2000 是为了让"500 字的正常长文"能走到估算器那里、拿到一条说清
        # "超宽多少、怎么改"的报错，而不是在这里被静默砍掉一半内容。
        return str(v)[:2000]
    if t == T_BOOL:
        if not isinstance(v, bool):
            raise DSLError(f"{where}: 需要 true/false")
        return v
    if t == T_COLOR:
        return _check_color(v, where)
    if t == T_RANGE:
        return _check_range(v, where)
    if t == T_POINT:
        return _check_point(v, where, declared)
    if t == T_EXPR:
        return _check_expr_ast(_strip_dollar(v), where)
    if t == T_REF:
        if not isinstance(v, str) or v not in declared:
            raise DSLError(f"{where}: 引用了未声明的元素 {v!r}（元素必须先声明后引用）")
        return v
    if t == T_POINTS:
        if not isinstance(v, list) or len(v) < 2:
            raise DSLError(f"{where}: 至少需要 2 个点")
        if len(v) > 12:
            raise DSLError(f"{where}: 点太多（>12）")
        return [_check_point(p, f"{where}[{i}]", declared) for i, p in enumerate(v)]
    if t == T_TABLE:
        # ⚠️ 这里原来有"最多 8 行 / 每行最多 8 列 / 单元格最多 80 字"三条硬上限，
        # 2026-09-16 全部删掉：它们和"占多大地方"无关，真正该问的问题是
        # "按这个字号画出来会不会出界"，那个由 _check_layout_fits() 用 metrics 估算回答。
        # 而且 80 字截断是**静默改内容**（写 90 字会被悄悄砍掉 10 字），属于最坏的一类失效。
        if not isinstance(v, list) or not v:
            raise DSLError(f"{where}: rows 不能为空")
        rows = []
        for r in v:
            if not isinstance(r, list) or not r:
                raise DSLError(f"{where}: 每行必须是数组")
            rows.append([str(c) for c in r])
        # 各行长度必须一致。这条以前没有，于是"每行 3 个、某一行 4 个"会一路过校验、
        # 直到 manim 的 `Table.__init__` 抛 `ValueError: Not all rows in table have the
        # same length.` —— 那是非 DSLError，会穿透校验层变成用户的 500（HANDOFF §8.24 那类）。
        widths = {len(r) for r in rows}
        if len(widths) > 1:
            bad = {len(r): i for i, r in enumerate(rows)}
            raise DSLError(
                f"{where}: 每行的列数必须一样，收到 {sorted(widths)}"
                f"（第 {bad[min(widths)] + 1} 行有 {min(widths)} 列、"
                f"第 {bad[max(widths)] + 1} 行有 {max(widths)} 列）"
            )
        return rows
    if t == T_LIST:
        if not isinstance(v, list) or not v:
            raise DSLError(f"{where}: 不能为空数组")
        if len(v) > 10:
            raise DSLError(f"{where}: 最多 10 项")
        return v
    return v


def _norm_place(place, where, declared):
    """布局原语：单个对象或数组（按顺序依次应用）。"""
    items = place if isinstance(place, list) else [place]
    if not isinstance(items, list) or not items:
        raise DSLError(f"{where}: place 必须是对象或对象数组")
    if len(items) > 6:
        raise DSLError(f"{where}: place 最多 6 条")
    out = []
    for i, p in enumerate(items):
        w = f"{where}[{i}]"
        if not isinstance(p, dict) or not p:
            raise DSLError(f"{w}: 每条 place 必须是非空对象")

        # ---- ① 先认「已经展平过的 next_to」----
        # `{"next_to":"id", "direction":..., "buff":...}` 正是本函数下面那段**自己**
        # 展平产出的形态。规范化的输出必须能被自己重新读入，否则第二遍就会把它判成
        # "一条 place 里有多个布局键"而报错 —— 也就是 f(f(x)) != f(x)（不幂等）。
        # 2026-09-14 由 tests/test_normalize_idempotent.py 抓出，是 HANDOFF §8.6 那条：
        # replace-scene 曾因此报"校验失败"，而同一份数据前一个闸刚说"通过"。
        # ⚠️ 判据要求**必须带一个伴随键**，免得把模型写错的裸 `{"next_to":"ax"}` 也放过去 —
        # 那种情况仍走下面的分支报错，由重试机制引导它写全。
        if ("next_to" in p and isinstance(p["next_to"], str)
                and set(p) <= {"next_to", "direction", "buff", "aligned"}
                and ({"direction", "buff", "aligned"} & set(p))):
            if p["next_to"] not in declared:
                raise DSLError(f"{w}: 引用了未声明的元素 {p['next_to']!r}")
            d = {
                "next_to": p["next_to"],
                "direction": str(p.get("direction", "down")).lower(),
                "buff": float(p.get("buff", 0.4)),
            }
            al = str(p.get("aligned") or "").lower()
            if al:
                if al not in ALIGNED_EDGES:
                    raise DSLError(f"{w}: aligned 只能是 left/right/top/bottom")
                d["aligned"] = al
            out.append(d)
            continue

        # "buff" 是所有布局共用的修饰键，不算主键
        keys = [k for k in p if k != "buff"]
        if len(keys) != 1:
            raise DSLError(
                f"{w}: 每条 place 只能有一个布局键（可另加 buff），收到 {sorted(p)}"
            )
        key, val = keys[0], p[keys[0]]
        if key == "edge":
            d = str(val).lower()
            if d not in EDGES:
                raise DSLError(f"{w}: edge 只能是 up/down/left/right")
            out.append({"edge": d, "buff": float(p.get("buff", 0.6))})
        elif key == "corner":
            d = str(val).lower()
            if d not in ("ul", "ur", "dl", "dr"):
                raise DSLError(f"{w}: corner 只能是 UL/UR/DL/DR")
            out.append({"corner": d, "buff": float(p.get("buff", 0.6))})
        elif key == "next_to":
            if not isinstance(val, dict) or "of" not in val:
                raise DSLError(f"{w}: next_to 需要 {{{{of, direction, buff}}}}")
            if val["of"] not in declared:
                raise DSLError(f"{w}: 引用了未声明的元素 {val['of']!r}")
            d = {
                "next_to": val["of"],
                "direction": str(val.get("direction", "down")).lower(),
                "buff": float(val.get("buff", 0.4)),
            }
            # aligned 为空时**不写这个键**（而不是写 null）：写 null 会让规范化产物里
            # 带一个"值为 null 的可选键"，第二遍再读进来形态就对不上了 —— 同 §8.6 那类问题。
            al = str(val.get("aligned") or "").lower()
            if al:
                if al not in ALIGNED_EDGES:
                    raise DSLError(f"{w}: aligned 只能是 left/right/top/bottom")
                d["aligned"] = al
            out.append(d)
        elif key == "at_point":
            out.append({"at_point": _check_point(val, f"{w}.at_point", declared)})
        elif key == "cell":
            # 「把元素放在表格的第 r 行第 c 格上」—— 讲卷积/矩阵时把格子里的数"提出来"
            # 就靠它：先锚在格子上，再 move_to 出去排成一行，观众才看得出哪一项对应哪一格。
            # 位置由格子算（`_cells_center`），和 cell_box 一样不存在"自己估坐标"的错位。
            if not isinstance(val, dict) or "of" not in val:
                raise DSLError(f"{w}: cell 需要 {{of, row, col}}")
            if val["of"] not in declared:
                raise DSLError(f"{w}: 引用了未声明的元素 {val['of']!r}")
            r, c = val.get("row"), val.get("col")
            if not isinstance(r, int) or not isinstance(c, int):
                raise DSLError(f"{w}.cell: row/col 必须是整数（行/列号从 1 开始）")
            out.append({"cell": {"of": val["of"], "row": r, "col": c}})
        elif key == "shift":
            if not isinstance(val, list) or len(val) != 2:
                raise DSLError(f"{w}: shift 必须是 [dx, dy]")
            out.append({"shift": [_as_num_or_ref(val[0], f"{w}[0]"),
                                  _as_num_or_ref(val[1], f"{w}[1]")]})
        elif key == "scale":
            out.append({"scale": _as_num_or_ref(val, f"{w}.scale")})
        elif key == "rotate":
            out.append({"rotate": _as_num_or_ref(val, f"{w}.rotate")})
        elif key == "buff":
            raise DSLError(f"{w}: buff 是修饰键，必须和 edge/corner 写在一起")
        elif key == "center":
            out.append({"center": True})
        elif key == "fit_width":
            out.append({"fit_width": _as_num_or_ref(val, f"{w}.fit_width")})
        elif key == "z":
            out.append({"z": int(val)})
        else:
            raise DSLError(
                f"{w}: 未知布局键 {key!r}。"
                f"可用: edge/corner/next_to/at_point/shift/scale/rotate/center/fit_width/z"
            )
    return out


def _flatten_parallel(subs, declared, trackers, where):
    """
    把 parallel 的子动作摊平成一个**可并行**的列表（2026-09-15）。

    两条规则，都是为了让 `_Builder._parallel()` 能无歧义地把它合成**一条**
    `self.play(...)`，同时保证「实际耗时 == `_est_action_time` 的估算」这条等式成立：

    1. **嵌套 parallel 递归摊平**。`AnimationGroup(a, AnimationGroup(b, c))` 的实际
       结束时刻是 `max(a, max(b, c))`，与完全摊平的 `max(a, b, c)` **恒等**，
       所以摊平不改变语义；但摊平后估时函数不用再递归处理嵌套，少一层出错的可能。
       （如果哪天改成嵌 AnimationGroup，估时也得跟着改，而那种"两处必须同时改"的地方
         正是本项目最容易漏的一类。）
    2. **多 target 的动作拆成多个单 target 副本**。`show: {target:[a,b]}` 会展开成
       两条 `self.add(...)`，塞不进一条 `self.play`；拆开之后每个副本仍是原来的语义
       （并行 add）。渲染端因此只需处理"1 个 target"这一种情况。

    ⚠️ **刻意不做的一件事**：把"同一元素上的多个动作"合并成一条链式调用。
    理由不是做不到，而是**不该并行** —— 对同一个 mobject 的两个操作天然有先后
    （先放大再旋转 vs 先旋转再放大，结果不同），硬塞进同一条 play 里，谁先生效
    取决于 manim 内部执行顺序。渲染端遇到同元素重复会**整段降级为顺序播放**，
    那才是符合直觉的语义。这样渲染端也不必认识"链式动作"这种新形态。
    """
    flat = []
    for s in subs:
        if s.get("do") == "parallel":
            flat.extend(_flatten_parallel(s.get("actions") or [], declared, trackers, where))
            continue
        targets = s.get("target") or []
        if len(targets) > 1:
            for t in targets:
                cp = dict(s)
                cp["target"] = [t]
                flat.append(cp)
        else:
            flat.append(s)

    # 断言：渲染端的可并行判据必须能接受这里产出的每一个动作（1 个 target）。
    # 不做这一步的话，"校验层放行 / 渲染层降级"两边会悄悄漂移 —— 而漂移的症状是
    # "写的是并行、跑出来是串行"，正是这次要修的毛病本身。
    for s in flat:
        if len(s.get("target") or []) > 1:
            raise DSLError(
                f"{where}: parallel 的子动作 {s.get('do')!r} 展开后仍有多个 target，"
                "内部不一致（这是校验层的 bug）"
            )
    return flat


def _norm_action(ac, declared, trackers, where):
    if not isinstance(ac, dict):
        raise DSLError(f"{where}: 动作必须是对象")
    do = ac.get("do")
    if do not in ACTION_SPEC:
        raise DSLError(f"{where}: 未知动作 {do!r}。可用: {sorted(ACTION_SPEC)}")
    spec = ACTION_SPEC[do]
    out = {"do": do}

    # ⚠️ 多写的参数名**必须报错**（2026-09-17，与元素侧同一类静默失败）：
    # 原来 `_norm_action` 只按 do 分支读它认识的键，多写的键被**静静丢掉** ——
    # 模型写 `{"do":"create","target":"x","duration":2}`（想说时长、名字写错）时
    # 什么都不发生，动画还是默认时长。要报错就必须先有"什么算合法"的名单，
    # 于是有了 `_ACTION_KEYS`（改它等于改对模型的契约，见那张表上面的说明）。
    _allowed = set(_ACTION_UNIVERSAL) | set(_ACTION_KEYS.get(do, ()))
    _extra = sorted(k for k in ac
                    if k not in _allowed and not str(k).startswith("_"))
    if _extra:
        extra_keys = "、".join(_ACTION_KEYS.get(do, ())) or "（没有附加参数）"
        raise DSLError(
            f"{where}: {do} 没有这些参数：{_extra}。"
            f"{do} 接受的附加参数是：{extra_keys}"
            f"（另有通用键 run_time / lag_ratio / target）。"
            f"名字写错会静默失效，所以直接报出来。"
        )

    rt = ac.get("run_time", spec["run_time"])
    if rt is not None:
        rt = _as_num_or_ref(rt, f"{where}.run_time")
        if isinstance(rt, str):
            raise DSLError(f"{where}.run_time: 不能用追踪器")
        # show / remove / trace 是无动画动作，run_time 记 0
        if rt != 0 and not (0.05 <= rt <= 20):
            raise DSLError(f"{where}.run_time: {rt} 超出 [0.05, 20]")
        out["run_time"] = rt

    if spec["target"]:
        if "target" not in ac:
            raise DSLError(f"{where}: {do} 需要 target")
        tgt = ac["target"]
        # target 允许内联定义元素（随手造随手用）：
        # 内联的元素第一遍就被转成 out["inline"] + out["target"]=[id]，而**它不在
        # declared（那是 elements 数组的名单）里**。所以第二遍读回来必须认这个形态 ——
        # 否则就会报"引用了未声明的元素 d1"，而那份数据正是规范化自己生成的（不幂等）。
        # 2026-09-14 由 tests/ 抓出，属 HANDOFF §8.6 那一类"规范化产物喂回 raw 入口"。
        inline_here = ac.get("inline")
        inline_id = inline_here.get("id") if isinstance(inline_here, dict) else None
        # target 允许内联定义元素（随手造随手用）
        if isinstance(tgt, dict):
            tgt = _norm_element(tgt, declared, trackers, f"{where}.target")
            out["inline"] = tgt
            out["target"] = [tgt["id"]]
        elif isinstance(tgt, list):
            out["target"] = []
            for t in tgt:
                if not isinstance(t, str):
                    raise DSLError(f"{where}: target 数组里只能是 id 字符串")
                if t not in declared and t != inline_id:
                    raise DSLError(f"{where}: 引用了未声明的元素 {t!r}")
                out["target"].append(t)
        else:
            if not isinstance(tgt, str) or (tgt not in declared and tgt != inline_id):
                raise DSLError(f"{where}: 引用了未声明的元素 {tgt!r}")
            out["target"] = [tgt]
        # 已经规范化过的内联元素：原样带走（元素本身已在第一遍校验过）。
        # 没有这一步，inline 就会在下一次规范化时被"drop"掉，内联定义的元素凭空消失。
        if isinstance(inline_here, dict) and "inline" not in out:
            out["inline"] = inline_here

        out["target"] = list(out["target"])

    # 各动作的附加参数
    if do in ("transform", "replace"):
        if "into" not in ac:
            raise DSLError(f"{where}: {do} 需要 into")
        into = ac["into"]
        inline_here = ac.get("inline_into")
        inline_id = inline_here.get("id") if isinstance(inline_here, dict) else None
        if isinstance(into, dict):
            into = _norm_element(into, declared, trackers, f"{where}.into")
            out["inline_into"] = into
            out["into"] = into["id"]
        else:
            # `into` 可能是第一遍从内联元素转出来的 id（它不在 declared 里），同上
            if not isinstance(into, str) or (into not in declared and into != inline_id):
                raise DSLError(f"{where}: into 引用了未声明的元素 {into!r}")
            out["into"] = into
        if isinstance(inline_here, dict) and "inline_into" not in out:
            out["inline_into"] = inline_here

    if do == "replace":
        # effect 是**可选**参数，所以不写就不写进输出（写法与 ELEMENT_SPEC 的可选参数一致：
        # 补一个 "effect": "crossfade" 会让第二遍把"没写"读成"写了"，破坏幂等）。
        eff = ac.get("effect", REPLACE_EFFECT_DEFAULT)
        if not isinstance(eff, str) or eff.lower() not in REPLACE_EFFECTS:
            raise DSLError(
                f"{where}.effect: 未知过渡效果 {eff!r}。"
                f"可用: {' / '.join(REPLACE_EFFECTS)}"
                f"（不写就是 {REPLACE_EFFECT_DEFAULT}：旧的淡出、新的淡入）"
            )
        eff = eff.lower()
        if ac.get("effect") is not None:
            out["effect"] = eff

    if do == "wait":
        out["time"] = float(ac.get("time", out.get("run_time", 0.5)))
        if not (0.05 <= out["time"] <= 20):
            raise DSLError(f"{where}.time: {out['time']} 超出 [0.05, 20]")

    if do == "rotate_camera":
        # 相机角度用**角度制**（模型和人都按度数想），生成代码时再乘 DEGREES。
        # ⚠️ **至少要给一个参数**：一个都不给等于"原地不动"，那是一次静默无效的动作 ——
        # 写了、画面什么都没发生、也不报错，正是本项目最忌讳的形态。
        moved = False
        for key, lo, hi in (("theta", -360.0, 360.0), ("phi", 0.0, 90.0),
                            ("zoom", 0.1, 8.0)):
            if ac.get(key) is None:
                continue
            v = _as_num_or_ref(ac[key], f"{where}.{key}")
            if isinstance(v, str):
                raise DSLError(f"{where}.{key}: 相机角度不能用追踪器")
            if not (lo <= v <= hi):
                raise DSLError(f"{where}.{key}: {v} 超出 [{lo}, {hi}]")
            out[key] = v
            moved = True
        if not moved:
            raise DSLError(
                f"{where}: rotate_camera 至少要给 theta 或 phi（都不给等于没转）"
            )

    if do == "parallel":
        subs = ac.get("actions")
        if not isinstance(subs, list) or not subs:
            raise DSLError(f"{where}: parallel 需要非空的 actions 数组（子动作）")
        # 子动作要**递归**规范化，并且原样带进返回值。
        # 以前这里没有这段，actions 就被丢掉了；而 act() 与时长估算都读 ac["actions"]，
        # 于是 parallel 变成"写了却什么都没做"：不报错、不上屏、只吐一行注释 ——
        # 最典型的静默失败（2026-09-12 发现）。
        norm_subs = [
            _norm_action(s, declared, trackers, f"{where}.actions[{i}]")
            for i, s in enumerate(subs)
        ]
        # 真并行（2026-09-15）：把子动作**摊平**成一个可并行的列表。
        #
        # 为什么必须摊平而不是嵌套进一个 AnimationGroup：
        #   `_est_action_time()` 是给"软时长"补 wait 用的，它按 `max(子动作)` 估；
        #   而 manim 对 AnimationGroup **不缩放**子动画时长（composition.py 里
        #   `build_animations_with_timings` 用的是子动画**原始** run_time，没乘 rate_func）。
        #   只要渲染端真的是「一条 self.play 带多个动画」，实际总时长就同等于
        #   `max(子动作估时)` —— 估算与实际天然一致。嵌一层 AnimationGroup 会
        #   让这条等式多一圈可能出现偏差的转译，而且估时函数也得跟着复杂化。
        #   （顺带确认过：AnimationGroup 的 run_time 与 rate_func 只影响组内**相对**
        #     时间映射，不会缩放子动画时长 —— 见 init_run_time / interpolate。）
        out["actions"] = _flatten_parallel(norm_subs, declared, trackers, where)

    if do == "tracker_to":
        v = _as_num_or_ref(ac.get("to", 1.0), f"{where}.to")
        if isinstance(v, str):
            raise DSLError(f"{where}.to: 不能用追踪器")
        out["to"] = v
        rf = str(ac.get("rate_func", "linear")).lower()
        if rf not in ("linear", "smooth", "rush_into", "rush_from",
                      "there_and_back", "ease_in", "ease_out"):
            raise DSLError(f"{where}: 未知 rate_func {rf!r}")
        out["rate_func"] = rf
        tids = out["target"]
        for t in tids:
            if t not in trackers:
                raise DSLError(f"{where}: {t!r} 不是 tracker，tracker_to 只能驱动 tracker")

    if do == "shift":
        v = ac.get("vector", [0, 0.5])
        if not isinstance(v, list) or len(v) != 2:
            raise DSLError(f"{where}: shift 需要 vector=[dx, dy]")
        out["vector"] = [_as_num_or_ref(v[0], f"{where}.vector[0]"),
                         _as_num_or_ref(v[1], f"{where}.vector[1]")]

    if do == "move_to":
        # 以前这里是 ac["point"]：模型漏写 point（或写成 to / target_point）时
        # 直接 KeyError 冒到路由层，整个请求 500，用户看到"服务端异常"。
        # 漏参数是**模型输出**的问题，应该按 DSLError 报出来让重试机制回灌纠正。
        pt = ac.get("point", ac.get("to", ac.get("target_point")))
        if pt is None:
            raise DSLError(
                f"{where}: move_to 需要 point=[x, y] / {{ref,x,y}} / {{of,anchor}}"
                f"（收到键 {sorted(ac)}）"
            )
        out["point"] = _check_point(pt, f"{where}.point", declared)

    if do == "move_cells":
        # 滑动窗口：把 cell_box 挪到另一组格子。位置由表格算出来，所以模型
        # 不需要（也不允许）自己估坐标 —— 估坐标就是错位的来源。
        tbl = ac.get("of")
        if not isinstance(tbl, str) or tbl not in declared:
            raise DSLError(
                f"{where}: move_cells 需要 of=表格元素 id"
                f"（收到 {tbl!r}）。例：{{\"do\":\"move_cells\",\"target\":\"win\","
                "\"of\":\"img\",\"rows\":[1,2],\"cols\":[2,3]}"
            )
        out["of"] = tbl
        if ac.get("rows") is not None:
            out["rows"] = _check_cell_ids(ac["rows"], f"{where}.rows")
        if ac.get("cols") is not None:
            out["cols"] = _check_cell_ids(ac["cols"], f"{where}.cols")

    if do == "scale":
        out["factor"] = _as_num_or_ref(ac.get("factor", 1.2), f"{where}.factor")

    if do == "rotate":
        out["angle"] = _as_num_or_ref(ac.get("angle", 90), f"{where}.angle")

    if do == "set_color":
        out["color"] = _check_color(ac.get("color", "ACCENT"), f"{where}.color")

    if do == "set_opacity":
        o = _as_num_or_ref(ac.get("opacity", 0.3), f"{where}.opacity")
        if not (0.0 <= float(o) <= 1.0):
            raise DSLError(f"{where}.opacity: 必须在 [0, 1]")
        out["opacity"] = o

    if do == "set_stroke":
        out["color"] = _check_color(ac.get("color", "ACCENT"), f"{where}.color")
        out["width"] = _as_num_or_ref(ac.get("width", 4), f"{where}.width")

    if do == "stretch":
        out["factor"] = _as_num_or_ref(ac.get("factor", 1.2), f"{where}.factor")
        d = str(ac.get("dim", "x")).lower()
        if d not in ("x", "y"):
            raise DSLError(f"{where}: dim 只能是 x/y")
        out["dim"] = d

    if do in ("move_along",):
        if ac.get("path") not in declared:
            raise DSLError(f"{where}: path 引用了未声明的元素 {ac.get('path')!r}")
        out["path"] = ac["path"]

    if do == "trace":
        out["color"] = _check_color(ac.get("color", "YELLOW"), f"{where}.color")
        out["width"] = _as_num_or_ref(ac.get("width", 3), f"{where}.width")

    if do in ("indicate", "circumscribe", "flash"):
        out["color"] = _check_color(ac.get("color", "ACCENT"), f"{where}.color")

    if do == "grow":
        e = str(ac.get("from", "center")).lower()
        if e not in ("center", "up", "down", "left", "right"):
            raise DSLError(f"{where}: grow 的 from 只能是 center/up/down/left/right")
        out["from"] = e

    if do == "fade_in":
        s = ac.get("shift")
        if s is not None:
            d = str(s).lower()
            if d not in DIRECTIONS:
                raise DSLError(f"{where}: shift 方向 {d!r} 不合法")
            out["shift"] = d

    if "lag_ratio" in ac:
        lr = _as_num_or_ref(ac["lag_ratio"], f"{where}.lag_ratio")
        if not (0 <= float(lr) <= 1):
            raise DSLError(f"{where}.lag_ratio: 必须在 [0, 1]")
        out["lag_ratio"] = lr

    return out


def _scan_point_exprs(obj):
    """递归找出所有"点坐标"位置上写的表达式字符串，供标识符校验使用。"""
    out = []

    def _pt(p):
        if isinstance(p, dict) and "ref" in p:
            for k in ("x", "y"):
                v = p.get(k)
                if isinstance(v, str):
                    out.append(_strip_dollar(v))
        elif isinstance(p, list) and len(p) == 2 and all(
            isinstance(e, (int, float, str)) for e in p
        ):
            for e in p:
                if isinstance(e, str):
                    out.append(_strip_dollar(e))

    def walk(o, in_place=False):
        if isinstance(o, dict):
            if "at_point" in o:
                _pt(o["at_point"])
            elif "point" in o:
                _pt(o["point"])
            elif "ref" in o and ("x" in o or "y" in o):
                _pt(o)
            else:
                for v in o.values():
                    walk(v, in_place)
        elif isinstance(o, list):
            # 点的数组形态：[x, y] 是点，[[x,y],[x,y]] 是多边形顶点
            if o and all(isinstance(e, (int, float, str)) for e in o) and len(o) == 2:
                _pt(o)
            else:
                for v in o:
                    walk(v, in_place)

    for key in ("at", "start", "end", "a", "vertex", "c", "points", "place"):
        if key in obj:
            walk(obj[key])
    return out


# 表达式里允许出现的「自由变量」（x 与数学函数之外的变量名）。
#
# ⚠️ 必须**按元素类型**给，不能全局放开。反例：`plot` 的 expr 里写 y 属于写错，
# 现在会被明确拦下；一旦全局允许 y，生成的代码会走 `_safe_eval` → NameError →
# 兜底成 0 → 曲线变成一条 y=0 的直线：画面能出、内容全错，本项目最怕的一类。
#
# ⚠️ `parametric` 这一条是**修 bug**（2026-09-16 探针实测）：它的 fx/fy 参数名是 t，
# 而 ELEMENT_SPEC 给的默认值本身就是 "cos(t)"/"sin(t)" —— 不放开 t 的话，
# 凡是用 parametric 的分镜**必然被拒**（连自己的默认值都过不了校验），
# 模型只能改写成 `cos(x)` 这种错写法，或者编一个叫 t 的 tracker
# （那会让曲线的参数 t 被 tracker 的值覆盖，画出来仍然是错的）。
_FREE_VARS = {
    "parametric": ("t",),
    "space_curve": ("t",),      # 空间曲线：x/y/z 都是 t 的函数
    "surface": ("y",),          # 曲面：z = f(x, y)
}


def _scan_expr_names(where_root, norm_els, norm_acts, trackers, extra_exprs=None,
                     carried=None, prev_trackers=None):
    """
    表达式里出现的标识符：不是 x、不是该元素允许的自由变量（见 _FREE_VARS）、
    不是数学函数，就必须是已声明的 tracker。

    不查这一遍，写错名字只会让 `_safe_eval` 抛异常并兜底成 0 —— 画面能出、内容全错，
    这正是本项目从头到尾最怕的静默失败。

    返回**错误字符串列表**而不是抛第一个：调用方会把它们和别的错误一起回灌，
    让模型一轮改完（见 validate_scene 与 _format_errors）。

    Args:
        extra_exprs: `(where, expr[, 允许的自由变量])` 列表。给**没能通过规范化的
            元素**用 —— 那些元素进不了 norm_els，但它们表达式里的名字写错是独立的一处
            错误，顺手扫一遍能少烧一轮重试（这些表达式只做只读扫描，不参与规范化）。
        carried / prev_trackers: **只影响报错文案**。本镜 carry 了哪些 id、
            上一分镜里有哪些 tracker。命中时说明"这是承接过来的元素引用了上一镜的
            tracker"—— 这种声明是从上一镜原样抄来的、看着毫无问题，光看"名字未定义"
            根本想不到是 carry 的事（见 HANDOFF §8.46）。
    """
    found = list(extra_exprs or [])
    for el in norm_els:
        free = _FREE_VARS.get(el.get("kind"), ())
        for key in ("expr", "fx", "fy", "fz"):
            if isinstance(el.get(key), str):
                found.append((f"{el['id']}.{key}", el[key], free))
        found += [(f"{el['id']}", e, free) for e in _scan_point_exprs(el)]
    for i, ac in enumerate(norm_acts):
        found += [(f"timeline[{i}].point", e, ()) for e in _scan_point_exprs(ac)]

    errs = []
    _carried = carried or set()
    _prev_tr = prev_trackers or set()
    # carry 诊断的**去重**：(元素, tracker) 只报一次。
    #
    # 为什么必须去重：一个点的 x / y 是两条独立表达式，会各扫出一处"名字未定义"——
    # 旧的通用文案里两条内容不同（分别带上自己的表达式），还算有用；而 carry 这条
    # 是整段重写的说明，两条会**一字不差地重复**，白占回灌预算（_MAX_ERROR_CHARS=1800）。
    _hinted = set()
    for item in found:
        # 兼容老的两元组形态：调用方少写一个字段时不该整个炸掉
        where, expr = item[0], item[1]
        free = item[2] if len(item) > 2 else ()
        for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr):
            if (name != "x" and name not in free
                    and name not in SAFE_NAMES and name not in trackers):
                allowed = "、".join(("x",) + tuple(free))
                # 「承接过来的元素引用了上一镜的 tracker」要单独说清。它是**唯一**一种
                # "表达式看着完全正常却过不了校验"的形态：声明是从上一镜原样搬来的，
                # 名字也没写错，只是那个 tracker 不在本镜里 —— 光报"未定义"会把人带偏
                # （写 few-shot 示例时我们自己就照着规格踩了一次）。
                # ⚠️ 两条出路都要给，而且顺序有讲究：**推荐固定值重声明**，因为
                # `carry` 一个 tracker 在拆分渲染下会回到声明时的初始值（画面跳回去）。
                eid = str(where).split(".", 1)[0]
                if eid in _carried and name in _prev_tr:
                    if (eid, name) in _hinted:
                        continue
                    _hinted.add((eid, name))
                    stub = '{"id": "%s", "carry": true}' % name
                    errs.append(
                        f"{where_root}.{where}: {eid} 是**承接**过来的元素，它引用的是"
                        f"上一分镜里的 tracker {name!r}，而本镜既没承接 {name!r}、"
                        f"也没重新声明它。两条出路："
                        f"① **更稳**：**不要 carry 这个元素**，改用固定值在本镜重新"
                        f"声明一遍（把 {name!r} 换成上一镜结束时的那个数）—— "
                        f"位置对得上、也不会跳；"
                        f"② 把 {name!r} 也一起承接（在 elements 里加 {stub}）——"
                        f"语法也合法，但拆分渲染下承接来的 tracker 会回到**声明时的"
                        f"初始值**，画面会跳回去。"
                    )
                    continue
                errs.append(
                    f"{where_root}.{where}: 表达式 {expr!r} 里的 {name!r} 未定义"
                    f"（只能是 {allowed}、数学函数，或某个 tracker 的 id）"
                )
    return errs


# 一次回灌多少条错误：给得多，但不无限 —— 报错本身也要占上下文
_MAX_ERRORS = 12
_MAX_ERROR_CHARS = 1800


def _format_errors(errors):
    """
    把收集到的多处问题拼成一条错误信息。

    为什么要"一次全报"：原先的 `_norm_element` / `_norm_action` 是**遇到第一个错就抛**，
    而 llm 那边的重试上限只有 4 次 —— 一份 4~6 分镜的 JSON 只要散着两三个错，
    就会「报一个 → 模型改一个 → 又撞下一个」把次数耗光。模型本来能一次改完所有错，
    是我们的反馈带宽把它逼死的（HANDOFF 里 D7 门要求"三次内 ≥95%"）。
    """
    lines = [f"校验未通过，共 {len(errors)} 处问题，请**一次性全部改正**后重新输出完整 JSON："]
    used = shown = 0
    for i, e in enumerate(errors[:_MAX_ERRORS], 1):
        text = str(e)[:_MAX_ERROR_CHARS // 3]
        if used + len(text) > _MAX_ERROR_CHARS:
            break
        lines.append(f"  {i}) {text}")
        used += len(text)
        shown = i
    if shown < len(errors):
        lines.append(f"  ...（还有 {len(errors) - shown} 处未列出，先修上面这些）")
    return "\n".join(lines)


def _check_cell_refs(where_root, norm_els, norm_acts):
    """
    格相关引用的**类型**校验（declared 只保证 id 存在，不看 kind，所以必须在这里补一遍）。

    重点拦三件事：
      1. cell_box 的 of 必须真的是 table（指向 rect 会生成出取不到格子的代码）；
      2. 用 move_to / shift 去挪 cell_box —— 那是"自己估坐标"，实测必然和网格错位
         （2026-09-13：1.9×1.9 的框去套 2×2 格子，只盖住半格）。直接报错，
         让重试机制把"请用 move_cells"这句话喂回给模型；
      3. 被框着的表格再做 scale/shift：框是建表时算好的，表格一动框就不跟了。
    """
    byid = {el["id"]: el for el in norm_els}
    kinds = {k: v["kind"] for k, v in byid.items()}

    def _table_of(eid, where):
        el = byid.get(eid)
        if el is None or el["kind"] != "table":
            raise DSLError(
                f"{where}: of={eid!r} 必须指向一个 table 元素"
                f"（收到 {kinds.get(eid, '未声明')}）"
            )
        return el

    def _in_range(rows, cols, tbl, where):
        n_rows, n_cols = len(tbl["rows"]), max(len(r) for r in tbl["rows"])
        if rows[-1] > n_rows or cols[-1] > n_cols:
            raise DSLError(
                f"{where}: 格号超出表格范围（表格 {n_rows}×{n_cols}，"
                f"rows={rows}, cols={cols}）"
            )

    for el in norm_els:
        if el["kind"] != "cell_box":
            continue
        w = f"{where_root}.elements: cell_box {el['id']!r}"
        tbl = _table_of(el["of"], w)
        _in_range(el["rows"], el["cols"], tbl, w)

    for i, ac in enumerate(norm_acts):
        w = f"{where_root}.timeline[{i}]"
        if ac["do"] == "move_cells":
            tgt_id = ac["target"][0]
            tgt = byid.get(tgt_id)
            if tgt is None or tgt["kind"] != "cell_box":
                raise DSLError(
                    f"{w}: move_cells 只能驱动 cell_box 元素"
                    f"（{tgt_id!r} 是 {kinds.get(tgt_id, '未声明')}）"
                )
            tbl = _table_of(ac["of"], w)
            # 没给 rows/cols 就沿用这个框自己声明的那组（等于原地重播一次）
            rows = ac.get("rows") or tgt["rows"]
            cols = ac.get("cols") or tgt["cols"]
            # 这里原来有一条"框的大小不能变（len(rows)/len(cols) 必须一致）"的检查，
            # 2026-09-16 删掉 —— 它同时犯了两个错（详见 HANDOFF §8.33）：
            #   · 拦不住该拦的：[1,8]（宽 8 格）→ [5,8]（宽 4 格）长度都是 2，照样放行，
            #     而那时的代码只 move_to 不改大小 → 8 格宽的框被居中到 4 格中心，
            #     整张表被横穿、右边还探出画面；
            #   · 挡住了该做的：二分查找这类"候选范围逐轮减半"根本没法表达。
            # 现在 move_cells 直接变形到"框住目标格子"的新框，大小随格子走，歪不了，
            # 于是这条检查失去了存在理由。
            _in_range(rows, cols, tbl, w)
            # 补全后交给 codegen 直接用，避免在那里再判一次默认值
            ac["rows"], ac["cols"] = rows, cols
        elif ac["do"] in ("move_to", "shift", "scale", "stretch", "rotate"):
            for t in ac["target"]:
                if kinds.get(t) == "cell_box":
                    raise DSLError(
                        f"{w}: {ac['do']} 作用在 cell_box 上会和网格错位 —— "
                        '请改用 {"do":"move_cells","target":"%s","of":"<表格id>",'
                        '"rows":[...],"cols":[...]}' % t
                    )

    # 被 cell_box 框着的表格：不能再缩放/平移/旋转。
    # 框是**建表时**按格子算出来的一次性结果，表格一动，框就留在原地不动了 ——
    # 又是"能渲染、但内容错"。要调整表格大小请改 font_size（格子尺寸随内容走）。
    watched = {el["of"] for el in norm_els if el["kind"] == "cell_box"}
    if watched:
        for i, ac in enumerate(norm_acts):
            if ac["do"] not in ("scale", "stretch", "shift", "move_to", "rotate"):
                continue
            for t in ac["target"]:
                if t in watched:
                    raise DSLError(
                        f"{where_root}.timeline[{i}]: {t!r} 正被 cell_box 框着，"
                        f"不能再对它做 {ac['do']}（框会留在原地、和格子错位）；"
                        "想改表格大小请调它的 font_size"
                    )


# ==============================================================================
# 三维一致性（"写了 3D 的东西，就真的按 3D 生效"）
# ==============================================================================
def _iter_ref_points(obj):
    """递归找出所有 `{ref, ..., z}` 形态的点 —— 用来查"z 只能挂在三维坐标系上"。"""
    if isinstance(obj, dict):
        if "ref" in obj and obj.get("z") is not None:
            yield obj
        else:
            for v in obj.values():
                yield from _iter_ref_points(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_ref_points(v)


def _check_3d(where_root, norm_els, norm_acts, trackers=()):
    """
    三维相关的**类型 + 值域**校验。declared 只保证 id 存在、不看 kind，所以必须补这一遍。

    拦的是同一类失败：**写了三维写法，却被 2D 的东西默默吃掉 / 顶出画面。**
      · `Axes.coords_to_point` 的实现是 `zip(other_axes, coords[1:], strict=False)`
        （manim 0.21 `coordinate_systems.py:2183`）—— 2D 的 Axes 只有两条轴，
        第三个坐标会被**直接丢掉**：点画在平面上某个位置，z 根本没生效。
      · 曲面 / 空间曲线挂在 2D axes 上同理：z 被吃掉，立体东西塌成一条线，
        画面能出、内容全错 —— 正是本项目最忌讳的那一类。
      · `rotate_camera` 在没有 three_axes 的分镜上会调 `self.move_camera`，
        而那是 ThreeDScene 才有的方法：2D 的 Scene 上直接 AttributeError，
        整个分镜渲不出来（校验层本该把它拦在渲染之前）。
      · **值域顶出坐标系**：曲面/曲线的取值范围超出 three_axes 声明的区间时，
        超出部分会画到画面外（2026-09-16 实测：z 到 9.7、z_range 只到 7，
        一个"碗"直接顶满并且冲出屏幕上沿）。这类错误必须在渲染前报掉，
        而且要报出"该把 range 改成多少"。

    返回错误列表（不抛），用法与 `_check_cell_refs` 一致。
    """
    byid = {el["id"]: el for el in norm_els}
    kinds = {k: v["kind"] for k, v in byid.items()}
    errs = []

    owners = [(el, f"{where_root}.{el['id']}") for el in norm_els]
    owners += [(ac, f"{where_root}.timeline[{i}]")
               for i, ac in enumerate(norm_acts)]

    for el in norm_els:
        kind = el.get("kind")
        if kind in ("surface", "space_curve"):
            ax = byid.get(el.get("axes"))
            if ax is None or ax.get("kind") != "three_axes":
                errs.append(
                    f"{where_root}.{el['id']}.axes: {kind} 必须挂在 three_axes 上，"
                    f"当前是 {kinds.get(el.get('axes'), '未声明')!r} —— 2D 坐标系会把 "
                    f"z 丢掉，画出来看着像对、其实是错的"
                )
            else:
                errs += _d3_range_errors(where_root, el, ax, trackers)
    for owner, where in owners:
        for pt in _iter_ref_points(owner):
            if kinds.get(pt["ref"]) != "three_axes":
                errs.append(
                    f"{where}: 坐标 {pt!r} 里写了 z，但它引用的是 "
                    f"{kinds.get(pt['ref'], '未声明')!r} —— z 只有挂在 three_axes 上"
                    f"才生效（2D 坐标系会默默丢掉它）"
                )

    if not any(k == "three_axes" for k in kinds.values()):
        for i, ac in enumerate(norm_acts):
            if ac.get("do") == "rotate_camera":
                errs.append(
                    f"{where_root}.timeline[{i}]: rotate_camera 需要一个 three_axes "
                    f"元素才能转视角（这个分镜里一个都没有）"
                )
    return errs


# 值域采样的点数：曲面取 _D3_SAMPLES×_D3_SAMPLES 个点、空间曲线取 _D3_SAMPLES 个点。
# 9 已经足够判断"会不会顶出坐标系"，而且纯算术、每份分镜都跑一遍也不心疼。
_D3_SAMPLES = 9

# ------------------------------------------------------------------------------
# 构建期表达式求值（只服务值域检查）
# ------------------------------------------------------------------------------
# ⚠️ 语义必须与 RUNTIME_HELPER 里的 `_safe_eval` 保持一致（同一套函数白名单）：
# 不一致的话，校验层算出来的值域和渲染时真画出来的东西就会分家 —— 那正是本项目
# 最忌讳的"两处必须同时改"的漂移点。之所以不能直接复用那份实现：它在
# **注入到生成代码里的字符串**里（RUNTIME_HELPER），不是本模块的可调用对象。
#
# 兜底方向刻意选"算不了就当没问题"（返回 None → 跳过检查）：这里是**校验层**，
# 误报（把正确的分镜拒掉）比漏报更贵 —— 它会白烧一轮 LLM 重试。
_MATH_NS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "exp": math.exp, "log": math.log, "ln": math.log, "log2": math.log2,
    "log10": math.log10, "sqrt": math.sqrt, "abs": abs, "floor": math.floor,
    "ceil": math.ceil, "sign": lambda v: (v > 0) - (v < 0), "min": min, "max": max,
    "mod": math.fmod, "pow": pow, "round": round,
    "pi": math.pi, "e": math.e, "tau": 2 * math.pi, "inf": math.inf,
}


def _safe_eval_static(expr, x=0.0, **extra):
    """构建期求值：算不了就返回 None（调用方据此跳过检查，绝不误报）。"""
    ns = dict(_MATH_NS)
    ns["x"] = float(x)
    ns.update({k: float(v) for k, v in extra.items()})
    try:
        v = float(eval(expr, {"__builtins__": {}}, ns))     # noqa: S307
    except Exception:                                        # noqa: BLE001
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _d3_range_errors(where_root, el, ax, trackers):
    """
    三维元素的**值域**检查：曲面/曲线的取值不能顶出 three_axes 声明的区间。

    求值用的是与生成代码**同一个** `_safe_eval`（同一套函数白名单、同样的兜底），
    所以这里算出来的就是渲染时真会画到的东西 —— 两边不会漂移。

    ⚠️ 表达式里含 tracker 时**直接跳过**：那种值域在构建期未知，
    采样出来的只是"某一瞬间"的值，据此报错就是误报（误报比漏报更坏，它会白烧一轮重试）。
    """
    kind = el["kind"]
    exprs = ([el.get("expr")] if kind == "surface"
             else [el.get(f) for f in ("fx", "fy", "fz")])
    for e in exprs:
        if isinstance(e, str) and set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", e)) & set(trackers):
            return []

    def _rng2(v):
        return float(v[0]), float(v[1])

    def _axis(vals, rng, name, how):
        lo, hi = _rng2(rng)
        vmin, vmax = min(vals), max(vals)
        if vmin < lo - 1e-9 or vmax > hi + 1e-9:
            return [
                f"{where_root}.{el['id']}: {how}的 {name} 取值范围是 "
                f"[{vmin:.2f}, {vmax:.2f}]，超出了 three_axes 声明的 {name}_range "
                f"[{rng[0]}, {rng[1]}] —— 超出的部分会直接画到画面外。"
                f"改法：把 {name}_range 放宽成对 [{min(lo, vmin):.2f}, {max(hi, vmax):.2f}]，"
                f"或缩小取值区间"
            ]
        return []

    out = []
    if kind == "surface":
        u0, u1 = _rng2(el["u_range"])
        v0, v1 = _rng2(el["v_range"])
        xs, ys, zs = [], [], []
        for i in range(_D3_SAMPLES):
            u = u0 + (u1 - u0) * i / (_D3_SAMPLES - 1)
            for j in range(_D3_SAMPLES):
                v = v0 + (v1 - v0) * j / (_D3_SAMPLES - 1)
                # 与生成代码同一套语义：表达式里的 x 是 u、y 是 v
                z = _safe_eval_static(el["expr"], u, y=v)
                if z is None:
                    return []          # 有采样点算不出来 → 放弃检查（宁漏勿误）
                zs.append(z)
                xs.append(u)
                ys.append(v)
        what = "这个曲面"
    else:
        t0, t1 = _rng2(el["t_range"])
        xs, ys, zs = [], [], []
        for i in range(_D3_SAMPLES):
            t = t0 + (t1 - t0) * i / (_D3_SAMPLES - 1)
            x = _safe_eval_static(el["fx"], t)
            y = _safe_eval_static(el["fy"], t)
            z = _safe_eval_static(el["fz"], t)
            if x is None or y is None or z is None:
                return []
            xs.append(x)
            ys.append(y)
            zs.append(z)
        what = "这条空间曲线"
    out += _axis(xs, ax["x_range"], "x", what)
    out += _axis(ys, ax["y_range"], "y", what)
    out += _axis(zs, ax["z_range"], "z", what)
    return out


# ==============================================================================
# 版面出界判定（"会不会放不下"的唯一判据）
# ==============================================================================
def _layout_errors_for(el, where):
    """
    单个元素 → 出界问题列表（空 = 放得下）。

    为什么把这件事从"数条数"搬到"估尺寸"：原来那几条硬上限（表格 8 行 8 列、
    格号最多 6 个、单元格 80 字）与"占多大地方"完全无关 —— 8 列单字数字表宽 12.03
    几乎顶满预算，8 列长公式表能到 30+，两者一样地"8 列"，却是一样的判罚。
    换成估算之后，**只有真放不下才拒**，而且拒的时候能说清超了多少、怎么改。

    估算器是保守上界（见 metrics 顶部），所以这里不会放过"其实放不下"的情况；
    代价是极少数本来勉强能塞下的会被判回炉，那也比渲染时字被缩到看不清好。
    """
    kind = el["kind"]
    out = []

    if kind in _D3_KINDS:
        # 三维元素**不做出界估算**：metrics 里的系数全是按 2D 帧宽高量出来的，
        # 而 3D 物体在屏幕上的大小由相机角度决定（转一下视角就变），估出来的数没有意义。
        # 强行估只会把正确的分镜误判成"放不下" —— 误报比漏报更坏：它会让模型白改一轮。
        return out

    def _too_big(w, h, hints, what):
        if not metrics.fits(w, h):
            out.append(metrics.overflow_msg(where, what, w, h, hints))

    if kind == "text":
        fs = el["font_size"]
        w, h = metrics.est_block_size(el["content"], fs)
        n_lines = len(str(el["content"]).split("\\n"))
        hints = []
        if w > metrics.BUDGET_W:
            hints.append(f"把 font_size 从 {fs} 降到 {metrics.suggest_font_size(fs, w, h)}")
            if n_lines == 1:
                hints.append("用 `\\n` 拆成两行（每行短一半，宽度立刻减半）")
            else:
                hints.append(f"现在已有 {n_lines} 行，把每行再拆短一点")
        if h > metrics.BUDGET_H:
            hints.append(f"把 font_size 从 {fs} 降到 {metrics.suggest_font_size(fs, w, h)}"
                         f"（或整段拆成两个分镜）")
        _too_big(w, h, hints, "这段文字")
        return out

    if kind == "formula":
        fs = el["font_size"]
        w, h = metrics.est_formula_size(el["content"], fs)
        hints = []
        if w > metrics.BUDGET_W:
            hints.append(f"把 font_size 从 {fs} 降到 {metrics.suggest_font_size(fs, w, h)}")
            hints.append("拆成两行公式（`\\\\` 换行）或分成两个步骤")
        if h > metrics.BUDGET_H:
            hints.append("把公式拆成两个分镜，一屏只放一段推导")
        _too_big(w, h, hints, "这个公式")
        return out

    if kind == "table":
        fs = el["font_size"]
        info = metrics.est_table_size(el["rows"], fs, el.get("cell_w"), el.get("cell_h"),
                                      el.get("pad_x"), el.get("pad_y"),
                                      square=bool(el.get("square")))
        # ① 给了 cell_w/cell_h 就必须真的装得下：装不下的话格子会被内容撑大，
        #    "所有格子等宽"这个承诺就悄悄失效了（改了不生效 = 最忌讳的失效）。
        if el.get("cell_w") is not None and info["need_cell_w"] > el["cell_w"] + 1e-9:
            cw, r, c = info["widest"]
            out.append(
                f"{where}.cell_w: 定小了 —— 第 {r + 1} 行第 {c + 1} 列的内容估算宽 "
                f"{cw:.2f}，加留白 {info['pad_x']:.2f} 至少要 "
                f"{info['need_cell_w']:.2f} 才装得下，否则那一格会被内容撑大、"
                f"格子就不等宽了。改法：把 cell_w 提到 {info['need_cell_w']:.2f} 以上，"
                f"或缩短那一格的内容，或把 pad_x 调小"
            )
        if el.get("cell_h") is not None and info["need_cell_h"] > el["cell_h"] + 1e-9:
            out.append(
                f"{where}.cell_h: 定小了 —— 最高的内容加留白要 "
                f"{info['need_cell_h']:.2f}，改法：把 cell_h 提到它以上或调小 pad_y"
            )
        hints = []
        if info["w"] > metrics.BUDGET_W:
            hints.append(f"去掉 {info['n_cols'] - metrics.suggest_count(info['n_cols'], info['w'])} 列"
                         f"（现在 {info['n_cols']} 列）")
            if info["pad_x"] > 0.35:
                saved = (info["pad_x"] - 0.35) * info["n_cols"]
                hints.append(f"把 pad_x 从 {info['pad_x']:.2f} 降到 0.35"
                             f"（每格省 {info['pad_x'] - 0.35:.2f}，共省 {saved:.2f}）")
            hints.append(f"把 font_size 从 {fs} 降到 "
                         f"{metrics.suggest_font_size(fs, info['w'], info['h'])}")
        if info["h"] > metrics.BUDGET_H:
            hints.append(f"去掉 {info['n_rows'] - metrics.suggest_count(info['n_rows'], info['h'], metrics.BUDGET_H)} 行")
            hints.append(f"把 font_size 从 {fs} 降到 "
                         f"{metrics.suggest_font_size(fs, info['w'], info['h'])}")
        _too_big(info["w"], info["h"], hints, f"这张 {info['n_rows']}×{info['n_cols']} 的表")
        return out

    if kind == "legend":
        fs = el["font_size"]
        ws = [metrics.est_text_w(str(it[1]), fs) + 0.4 + 0.12 for it in el["items"]]
        hs = metrics.est_text_h("中", fs)
        w = max(ws) if ws else 0.0
        h = sum(hs for _ in el["items"]) + 0.16 * (len(el["items"]) - 1)
        hints = [f"把 font_size 从 {fs} 降到 {metrics.suggest_font_size(fs, w, h)}"]
        if len(el["items"]) > 1 and h > metrics.BUDGET_H:
            hints.append(f"图例项从 {len(el['items'])} 条减到 4 条以内")
        _too_big(w, h, hints, "这个图例")
        return out

    # 其余元素（axes/plot/dot/brace/cell_box/group/number…）要么尺寸由引擎决定、
    # 要么本来就很小，估不了也不值得估 —— 它们由运行时的 _fit 兜底（真缩会打 WARN）。
    return out


def _check_layout_fits(where_root, norm_els):
    """
    版面出界判定：**返回**错误列表（不抛），好跟别的闸的错误一起回灌。

    沿用与 `_check_cell_refs` 相同的策略：只在上游无错时跑（有上游错时估出来的
    尺寸没有意义，只会产噪音）。
    """
    out = []
    for i, el in enumerate(norm_els):
        try:
            out += _layout_errors_for(el, f"{where_root}.elements[{i}]")
        except DSLError as e:
            out.append(str(e))
    return out


# 动作对"在不在屏上"的影响。只列**确定**的：
#   · transform / replace 之类一律当作"不改变在屏状态"—— 宁可漏报也不误报，
#     误报会把正确的分镜拒掉，白烧一轮重试。
_ON_SCREEN_ADD = frozenset({"create", "write", "fade_in", "grow", "draw_border", "show"})
_ON_SCREEN_DROP = frozenset({"fade_out", "remove"})


def _carry_deps(el):
    """一个元素的构建期依赖（id 列表）。"""
    out = [el[k] for k in BUILD_DEPS if isinstance(el.get(k), str)]
    out += [it for it in (el.get("items") or []) if isinstance(it, str)]
    return out


def _walk_on_screen(acts, live):
    """timeline（规范化的）→ 结束时还在屏上的集合。就地改 live。"""
    for ac in acts or []:
        do = ac.get("do")
        if do == "clear_all":
            live.clear()
        elif do == "parallel":
            _walk_on_screen(ac.get("actions") or [], live)
        elif do in _ON_SCREEN_ADD:
            live.update(ac.get("target") or [])
        elif do in _ON_SCREEN_DROP:
            live.difference_update(ac.get("target") or [])
    return live


def on_screen_at_end(norm_scene):
    """
    规范化的分镜 → 它**演完之后**还在屏上的元素 id 集合（下一分镜的 carry 校验要用）。

    为什么必须模拟而不能只看"声明过"：声明了却从没被上屏动作动过的元素根本不在屏上
    （这正是动态元素那个老坑）。承接一个"不在屏上"的元素，下一分镜就是一片空白 ——
    而且不报错，属于最难查的一类静默失效。
    """
    live = {el["id"] for el in norm_scene["elements"] if el.get("carry")}
    return _walk_on_screen(norm_scene["timeline"], live)


def validate_scene(scene, idx, prev=None):
    """
    校验一个 v2 分镜（elements + timeline）。返回规范化 dict。
    所有引用完整性问题在这里拦掉 —— 引用了不存在的元素就是静默失败的温床。

    ⚠️ 这里**不再"遇到第一个错就抛"**：elements / timeline / 表达式三处能独立发现的问题
    会被收集起来一次报完。理由见 `_format_errors()` —— 重试次数有限，
    一轮只报一个错等于把模型的 4 次机会浪费成 4 个错上。

    Args:
        prev: 上一个分镜的上下文 `{"elements": {id: 规范元素}, "on_screen": set(ids)}`。
            `carry`（承接）要靠它判断"能不能承接"；不传 = 这是第一个分镜。
    """
    where_root = f"scene[{idx}]"
    if not isinstance(scene, dict):
        raise DSLError(f"{where_root}: 必须是对象")

    elements = scene.get("elements")
    if not isinstance(elements, list) or not elements:
        raise DSLError(f"{where_root}: 缺少 elements 数组或为空")
    if len(elements) > 40:
        raise DSLError(f"{where_root}: 元素过多（{len(elements)}），上限 40")

    # carry 元素先处理：它们在 t=0 就已经在屏上了，语义上"最先存在" ——
    # 后面的元素引用它们不该报"未声明"，模型也不必纠结书写顺序。
    # 没有 carry 时 order 原样不动（稳定排序），所以老分镜的产物逐字节不变。
    order = list(range(len(elements)))
    if any(isinstance(e, dict) and e.get("carry") for e in elements):
        order.sort(key=lambda i: 0 if elements[i].get("carry") else 1)

    declared, trackers = set(), set()
    errors = []
    norm_els = []
    extra_exprs = []          # 没能通过规范化的元素，仍然做一次只读的表达式扫描
    for i in order:
        el = elements[i]
        where = f"{where_root}.elements[{i}]"
        try:
            norm_els.append(_norm_element(el, declared, trackers, where, prev))
        except DSLError as e:
            errors.append(str(e))
            # 尽力把这个 id 登记进 declared：否则后面凡是引用它的元素都会级联报
            # "引用了未声明的元素"，把真正的问题淹掉（假错误比没有错误更坏）。
            eid = el.get("id") if isinstance(el, dict) else None
            if isinstance(eid, str) and _IDENT.match(eid):
                declared.add(eid)
                if el.get("kind") == "tracker":
                    trackers.add(eid)
            if isinstance(el, dict):
                # 只写相对路径（不带 where_root）：_scan_expr_names 会自己补前缀，
                # 写全了会拼成 "scene[1].scene[1].elements[1].expr" 这种重复前缀。
                free = _FREE_VARS.get(el.get("kind"), ())
                for key in ("expr", "fx", "fy", "fz"):
                    if isinstance(el.get(key), str):
                        extra_exprs.append((f"elements[{i}].{key}", el[key], free))

    # 承接的元素，它依赖的元素也必须一起承接。只承接 plot 不承接 axes 的话，
    # 边界清屏会把 axes 淡掉、只剩一个飘着的图 —— 下面这条把它变成硬错误。
    carried = {el["id"] for el in norm_els if el.get("carry")}
    if carried:
        missing = sorted({d for el in norm_els if el.get("carry")
                          for d in _carry_deps(el) if d not in carried})
        if missing:
            errors.append(
                f"{where_root}: carry 的元素依赖 {missing}，但它们没被一起承接 —— "
                f"分镜边界会把它们清掉，画面只剩一个飘着的东西。"
                + "请在 elements 里同样写上 "
                + " / ".join('{"id": "%s", "carry": true}' % m for m in missing)
            )

    timeline = scene.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        raise DSLError(f"{where_root}: 缺少 timeline 数组或为空")
    if len(timeline) > 80:
        raise DSLError(f"{where_root}: 时间线过长（{len(timeline)}），上限 80")

    norm_acts = []
    for i, ac in enumerate(timeline):
        try:
            norm_acts.append(_norm_action(ac, declared, trackers,
                                          f"{where_root}.timeline[{i}]"))
        except DSLError as e:
            errors.append(str(e))

    # 表达式标识符扫描是只读的、与上面互不影响，所以总是跟别的错误一起报。
    # 额外带上 carried + 上一镜的 tracker 名单，好把「承接来的元素引用了上一镜的
    # tracker」这条报清楚（见 _scan_expr_names 的 Args）。
    prev_trackers = {i for i, e in ((prev or {}).get("elements") or {}).items()
                     if e.get("kind") == "tracker"}
    errors += _scan_expr_names(where_root, norm_els, norm_acts, trackers, extra_exprs,
                               carried=carried, prev_trackers=prev_trackers)

    # 这两道都看"规范化后的整体"。上游已经有错时跳过 —— 那种情况下它们给出的
    # 只会是级联噪音，例如"of 指向的 table 本身没过校验"。
    if not errors:
        try:
            _check_cell_refs(where_root, norm_els, norm_acts)
        except DSLError as e:
            errors.append(str(e))
        else:
            # 三维一致性：3D 元素必须挂在 three_axes 上、z 不能被 2D 坐标系默默吃掉
            errors += _check_3d(where_root, norm_els, norm_acts, trackers)
            # 版面出界（表格/文本/公式/图例按内容估算）。放在 cell_refs 之后：
            # 它对元素自身参数不敏感，不必跟"引用错了"这类问题抢注意力。
            errors += _check_layout_fits(where_root, norm_els)

    if errors:
        raise DSLError(_format_errors(errors))

    duration = scene.get("duration")
    if duration is not None:
        duration = float(duration)
        if not (0.5 <= duration <= 120):
            raise DSLError(f"{where_root}: duration {duration} 超出 [0.5, 120]")

    return {
        "elements": norm_els,
        "timeline": norm_acts,
        "duration": duration,
    }


# ==============================================================================
# 代码构建
# ==============================================================================

def _num(v):
    if isinstance(v, str) and _TRACKER_REF.match(v):
        return f"{_V}{v[1:]}.get_value()"
    f = float(v)
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return repr(round(f, 6))


def _color(v):
    return v if v in COLORS else f'"{v}"'


def _esc(s):
    s = str(s)
    s = s.replace('"""', '"')
    return re.sub(r"[\x00-\x1f]", " ", s)





class _Builder:
    def __init__(self, env):
        self.env = env
        self.els = {}        # id -> element dict
        self.trackers = set()
        self.lines = []
        self.built = set()
        self.alias = {}      # id -> "现在哪个 id 的变量才代表我"（只有 replace 会改它）

    # ---------- 元素 id → 变量名 ----------

    def _var(self, eid):
        """
        元素 id → **当前代表它**的那个变量名。

        为什么需要这一层：`replace` 之后这个 id 所代表的物体已经换人了 ——
        `ReplacementTransform(a, b)` 的语义是"把 a 从场景里删掉、把 b 加进去"。
        之后若仍按 id 取回原来那个 `_e_a`，那个对象**已经不在场景里**，而 manim 的
        `Scene.add_mobjects_from_animations()` 恰好有一条：

            # Anything animated that's not already in the scene gets added to the scene
            if mob is not None and mob not in curr_mobjects:
                self.add(mob)

        于是每一次 `replace` 都会先把**上一版**的文字重新加回屏上、再叠一层新的。
        2026-09-16 实测（二分查找那题）：一个分镜里连做 6 次
        `replace info into iN`，底部最后叠着 4 行不同的提示文字糊成一团。
        （`transform` 不受影响：它的源对象一直留在场景里，原地变形。）
        """
        seen = set()
        while eid in self.alias and eid not in seen:
            seen.add(eid)
            eid = self.alias[eid]
        return f"{_V}{eid}"

    # ---------- 点坐标 → 代码 ----------

    def _coord(self, v):
        """点坐标分量：数字 / $tracker / 数学表达式。"""
        if isinstance(v, str) and _TRACKER_REF.match(v):
            return f"{_V}{v[1:]}.get_value()"
        if isinstance(v, str):
            # 表达式：只把其中出现的 tracker 名作为额外参数传进去
            names = sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", v)) & self.trackers)
            extra = "".join(f", {n}={_V}{n}.get_value()" for n in names)
            return f'_safe_eval(r"""{_esc(v)}""", 0.0{extra})'
        return _num(v)

    def _point_code(self, pt):
        """点 → 代码。_check_point 已把形态规整好。"""
        if isinstance(pt, dict):
            if "ref" in pt:
                return (f"{self._var(pt['ref'])}.c2p({self._coord(pt.get('x', 0))}, "
                        f"{self._coord(pt.get('y', 0))})")
            return f"{self._var(pt['of'])}.{ANCHORS[pt.get('anchor', 'center')]}"
        return f"np.array([{self._coord(pt[0])}, {self._coord(pt[1])}, 0])"

    # ---------- 元素构建 ----------

    def build(self, el):
        eid = el["id"]
        if eid in self.built:
            return
        kind = el["kind"]
        if kind == "tracker":
            self.trackers.add(eid)

        # ---- 承接元素（carry）：不重画、不加入场动画 ----
        # 两种编排方式必须分开处理，否则都会出错：
        #   reuse  —— 整个分镜集拼成**一个** Scene，所有分镜共用同一个 construct()
        #             作用域。上一分镜建的 `_e_<id>` 变量还活着、对象也还在屏上，
        #             所以这里**一行都不该生成**：再赋一次值只会让变量指向一个
        #             不在屏上的新对象，后续 move_to 之类的动作就会打在空气里。
        #   rebuild —— 拆分渲染（render_split），每个分镜是独立文件、独立 Scene，
        #             变量不存在。只能重建一次，但**不走入场动画**，直接 add ——
        #             观感上就是"接着上一镜继续演"。
        if el.get("carry"):
            if self.env.get("carry_mode", "rebuild") == "reuse":
                self.built.add(eid)
                return
            self._build_carry(el)
            self.built.add(eid)
            return

        self._build_deps(el)
        self._emit_object(el)
        self.built.add(eid)

    def _build_deps(self, el):
        """依赖先建：axes 必须早于 plot（`of` / `curve` / `path` / `items` 同理）。"""
        for dep in BUILD_DEPS:
            if el.get(dep) in self.els:
                self.build(self.els[el[dep]])
        for it in el.get("items", []) or []:
            if isinstance(it, str) and it in self.els:
                self.build(self.els[it])

    def _build_carry(self, el):
        """
        拆分渲染下的承接元素：重建一次并**立刻上屏**（不走入场动画）。

        依赖必须先建：carry 校验已经保证依赖也被一起承接了，所以它们在本文件里同样是
        carry；顺序上仍要先生成依赖的变量，否则 `win` 的构造会引用到还没赋值的
        `_e_img` —— NameError，整个分镜渲不出来。
        """
        self._build_deps(el)
        var = self._emit_object(el)
        self.lines.append(f"        self.add({var})   # carry：承接上一分镜，直接上屏")

    def _emit_object(self, el):
        """按"动态性 / 适配规则"生成构造代码，返回变量名。"""
        kind = el["kind"]
        eid = el["id"]
        expr = self._construct(el)
        var = var_name(eid)
        calls = self._place_calls(el.get("place"))

        # 含追踪器引用（或显式 live）→ 自动 always_redraw（机制 A）
        dynamic = ".get_value()" in expr or el.get("live")
        fit = kind in ("text", "formula", "legend", "table")

        if kind == "number" and dynamic:
            # DecimalNumber 不能走 always_redraw：它每帧要重建整个对象并重排文字
            # （内部走 Pango），实测是动态场景里最贵的一项。
            # 改成"建一次 + 只更新数值"，定位也就只需算一次。
            # 刷新走 _set_number 而不是 m.set_value：带中文单位的数字是个 VGroup，
            # 没有 set_value（见运行时 helper 里那段说明）。
            self.lines.append(f"        {var} = {expr}")
            self.lines.append(
                f"        {var}.add_updater(lambda m: _set_number(m, "
                f"{self._coord(el['value'])}))"
            )
            for c in calls:
                self.lines.append(f"        {var}{c}")
        elif dynamic:
            # 定位必须写进 lambda 内部：always_redraw 每帧重建对象，
            # 写在 lambda 外面的定位只会生效一次，元素随后会弹回原点。
            inner = f"_fit({expr}, {eid!r})" if fit else expr
            self.lines.append(
                f"        {var} = always_redraw(lambda: {inner}" + "".join(calls) + ")"
            )
        else:
            self.lines.append(f"        {var} = {expr}")
            for c in calls:
                self.lines.append(f"        {var}{c}")
            if fit:
                self.lines.append(f"        {var} = _fit({var}, {eid!r})")

        # 三维分镜里的"屏幕固定"元素（HUD 字幕）：只**登记**、不上屏 ——
        # 上屏时机仍由 timeline 的动作决定。所以这里调的是 camera 上的那个方法
        # （它只往白名单里加一个对象），而不是 ThreeDScene.add_fixed_in_frame_mobjects
        # （那个会顺手 `self.add(...)`，元素会在分镜一开始就冒出来，出场动画全废）。
        if self._screen_fixed(el):
            self.lines.append(
                f"        self.camera.add_fixed_in_frame_mobjects({var})")
            if dynamic:
                # always_redraw 每帧 `become` 出来的子对象是**新对象**，而相机那份
                # 白名单是按对象身份存的（three_d_camera.py:81 / :365）——
                # 不补登记，换出来的新子对象就会重新被旋转投影（字会突然歪掉）。
                self.lines.append(
                    f"        {var}.add_updater("
                    f"lambda m: self.camera.add_fixed_in_frame_mobjects(m))")

        # tracker 必须 add 进场景才会被动画更新（Manim 的硬性要求）
        if kind == "tracker":
            self.lines.append(f"        self.add({var})")
        return var

    # 三维分镜里"可以当字幕看"的元素类型。几何图形不在其中 ——
    # 它们本来就该待在三维空间里，跟着场景一起转。
    _SCREEN_KINDS = ("text", "formula", "table", "legend", "number")

    def _screen_fixed(self, el):
        """
        这个元素在**三维分镜**里要不要钉在屏幕上（当 HUD 字幕）。

        判据：① 本分镜有 three_axes；② 是文字类元素；③ 它用了**屏幕平面**的定位
        （edge / corner / next_to / center）。

        为什么必须有这条：三维场景里一切都会被相机投影。放在画面右上角的标题不钉住的话，
        会跟着视角一起被转斜、挪位，甚至转出画面（2026-09-16 实测：`place:[{"corner":"ur"}]`
        的公式在 phi=68 / theta=-48 下几乎看不见了）。

        为什么用 `place` 当判据、而不是"所有文字都钉住"：这样两类写法都自然 ——
        写在角落的是字幕（钉住、不随视角转）；直接给坐标、点在立体图形上的标签
        （不写 place，或只写 at_point）留在三维空间里跟着转。
        **一个开关两种语义，不必给模型引入新概念。**
        """
        if not self.env.get("d3"):
            return False
        if el.get("kind") not in self._SCREEN_KINDS:
            return False
        return any(isinstance(p, dict)
                   and ({"edge", "corner", "next_to", "center"} & set(p))
                   for p in (el.get("place") or []))

    def _place_calls(self, place):
        """布局 → 一串可链式追加的方法调用，如 ['.to_edge(UP, buff=0.8)']。"""
        out = []
        for p in place or []:
            if "edge" in p:
                out.append(f".to_edge({EDGES[p['edge']]}, buff={_num(p['buff'])})")
            elif "corner" in p:
                out.append(f".to_corner({DIRECTIONS[p['corner']]}, buff={_num(p['buff'])})")
            elif "next_to" in p:
                d = DIRECTIONS.get(p["direction"], "DOWN")
                s = f".next_to({self._var(p['next_to'])}, {d}, buff={_num(p['buff'])}"
                if p.get("aligned"):
                    # 走 ALIGNED_EDGES 翻译：top→UP、bottom→DOWN（直接 upper() 会
                    # 生成 BOTTOM 这种不存在的名字，见常量表那里的说明）
                    s += f", aligned_edge={ALIGNED_EDGES.get(p['aligned'], 'ORIGIN')}"
                out.append(s + ")")
            elif "at_point" in p:
                out.append(f".move_to({self._point_code(p['at_point'])})")
            elif "cell" in p:
                c = p["cell"]
                out.append(f".move_to(_cells_center({self._var(c['of'])}, "
                           f"[{int(c['row'])}], [{int(c['col'])}]))")
            elif "shift" in p:
                out.append(f".shift(np.array([{_num(p['shift'][0])}, {_num(p['shift'][1])}, 0]))")
            elif "scale" in p:
                out.append(f".scale({_num(p['scale'])})")
            elif "rotate" in p:
                rad = round(float(p["rotate"]) * 3.141592653589793 / 180.0, 6)
                out.append(f".rotate({rad})")
            elif "center" in p:
                out.append(".center()")
            elif "fit_width" in p:
                out.append(f".scale_to_fit_width({_num(p['fit_width'])})")
            elif "z" in p:
                out.append(f".set_z_index({p['z']})")
        return out

    def _expr_call(self, expr, xvar="x", extra_kw=""):
        """
        生成 _safe_eval 调用；表达式里出现的 tracker 名自动作为额外参数传入。

        Args:
            xvar: 表达式里**自由变量的名字**（也就是代码里那个函数参数名）。
            extra_kw: 额外的一个关键字参数，形如 `"y=v"`（曲面用：z = f(x,y) 里
                两个自由变量都要喂进去）。放最后是因为 `_safe_eval(expr, x, **extra)`
                的关键字参数与位置参数互不影响，顺序怎么写都对。

        ⚠️ xvar 不是 "x" 时必须**再显式传一个同名关键字**（2026-09-17 修的真 bug）：
        运行时是 `_safe_eval(expr, x, **extra)`，它只把第二个位置参数绑到名字 **x** 上。
        而 param 曲线/空间曲线的自由变量叫 **t** —— 表达式 `cos(t)` 在运行时
        直接 NameError，被 `_safe_eval` 兜底成 0.0，于是整条曲线**塌成一个点**，
        画面能出、内容全错，一声不响。曲面（z=f(x,y)）之所以没露馅，
        纯粹是因为它的自由变量恰好叫 x（走位置参数就对了）。
        """
        names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr))
        extra = [f"{t}={_V}{t}.get_value()" for t in sorted(names & self.trackers)]
        # 自由变量名不是 x（如 t）→ 补一个同名实参，否则表达式里的 t 是未定义的
        # （同名 tracker 已经占了 extra 的位置时跳过：那种情况本来就有歧义，
        #  但至少不能让生成代码报"重复关键字参数"而整条渲不出来）
        if xvar != "x" and xvar not in self.trackers:
            extra.append(f"{xvar}={xvar}")
        if extra_kw:
            extra.append(extra_kw)
        args = ", ".join([f'r"""{_esc(expr)}"""', xvar] + extra)
        return f"_safe_eval({args})"

    def _construct(self, el):
        k = el["kind"]
        f = getattr(self, f"_mk_{k}", None)
        if f is None:
            raise DSLError(f"元素类型 {k!r} 没有对应的构建器（spec 与实现不一致）")
        return f(el)

    # ---- 各元素构造 ----

    def _mk_text(self, el):
        return (f'rich(r"""{_esc(el["content"])}""", '
                f'font_size={el["font_size"]}, color={_color(el["color"])})')

    def _mk_formula(self, el):
        if el.get("tex_colors"):
            # 颜色交给 content 里的 \color{...}：**不能**给 MathTex 传 color，
            # 传了 manim 的 set_color 会把 LaTeX 指定的颜色整个盖掉（见 §8.49）。
            return (f'formula(r"""{_esc(el["content"])}""", '
                    f'font_size={el["font_size"]})')
        return (f'formula(r"""{_esc(el["content"])}""", '
                f'font_size={el["font_size"]}, color={_color(el["color"])})')

    def _axes_config(self, el, numbers=True):
        """
        坐标轴配置。这里藏着本项目最大的一笔性能优化：

        默认情况下 Manim 的每个刻度数字都是一个 MathTex —— 也就是**一次 LaTeX 编译**。
        实测一个 5 分镜的分镜要编译 24 次，占首次渲染 60s 里的 34s（57%）。
        刻度数字只是 0/1/2 这种阿拉伯数字，根本用不着 LaTeX 排版，
        改用 Text 渲染后这部分开销直接归零，而且没有 LaTeX 环境时也能显示刻度。
        """
        if not numbers or not el.get("numbers", True):
            return '{"color": GREY_B, "include_numbers": False}'

        style = str(el.get("numbers_style", "latex")).lower()
        if self.env.get("fast") or not self.env.get("use_latex", True):
            style = "text"
        if style == "text":
            # label_constructor 用 _text 而不是 Text：刻度数字同样是"Text 里的数字"，
            # 得跟着走 LATIN_FONT，否则坐标轴上就会出现两种西文字体。
            return '{"color": GREY_B, "include_numbers": True, "label_constructor": _text}'
        return '{"color": GREY_B, "include_numbers": True}'

    def _mk_axes(self, el):
        tips = "True" if el.get("tips", True) else "False"
        return (f'Axes(x_range={self._rng(el["x_range"])}, y_range={self._rng(el["y_range"])}, '
                f'x_length={_num(el["x_length"])}, y_length={_num(el["y_length"])}, '
                f'axis_config={self._axes_config(el)}, tips={tips})')

    def _mk_three_axes(self, el):
        """
        三维坐标系。刻度数字沿用 2D 那套开关（`numbers` / `numbers_style` 由
        `_axes_config` 统一处理，`--fast` 或没装 LaTeX 时会自动换成 Text 渲染）。

        ⚠️ 三条轴的长度**必须显式给**：ThreeDAxes 的默认长度是按 1080p 的帧高算的
        （x/y 轴默认 `frame_height + 2.5` ≈ 10.5），照搬会顶满甚至超出画面。
        """
        return (f'ThreeDAxes(x_range={self._rng(el["x_range"])}, '
                f'y_range={self._rng(el["y_range"])}, '
                f'z_range={self._rng(el["z_range"])}, '
                f'x_length={_num(el["x_length"])}, y_length={_num(el["y_length"])}, '
                f'z_length={_num(el["z_length"])}, '
                f'axis_config={self._axes_config(el)})')

    def _mk_number_plane(self, el):
        return (
            'NumberPlane(x_range=%s, y_range=%s, background_line_style='
            '{"stroke_opacity": %s, "stroke_color": GREY_B})'
            % (self._rng(el["x_range"]), self._rng(el["y_range"]), _num(el["opacity"]))
        )

    def _rng(self, r):
        return "[" + ", ".join(_num(x) for x in r) + "]"

    def _xr(self, el):
        """plot 的 x_range：不给就沿用所属 axes 的。"""
        if el.get("x_range"):
            return self._rng(el["x_range"])
        ax = self.els.get(el.get("axes"))
        if ax:
            return f'[{_num(ax["x_range"][0])}, {_num(ax["x_range"][1])}]'
        return "[-5, 5]"

    def _mk_plot(self, el):
        # 注意：只能用 set_stroke(opacity=...)，绝不能用 set_opacity()。
        # 曲线是开放路径，set_opacity() 会连 fill_opacity 一起拉满，
        # 而 VMobject 填充开放路径时会按「首尾相连」当封闭区域来填 ——
        # 于是函数图像会变成一个实心块（y=x**2 看着像个实心半圆），
        # 而且 opacity 默认 1.0，这条 bug 是必现的，不是参数写错才触发。
        return (f'{_V}{el["axes"]}.plot(lambda x: {self._expr_call(el["expr"])}, '
                f'x_range={self._xr(el)}, color={_color(el["color"])}, '
                f'stroke_width={_num(el["stroke_width"])})'
                f'.set_stroke(opacity={_num(el["opacity"])})'
                f'.set_fill(opacity=0)')

    def _mk_parametric(self, el):
        tr = self._rng(el["t_range"])
        return (f'{_V}{el["axes"]}.plot_parametric_curve('
                f'lambda t: (np.array([{self._expr_call(el["fx"], "t")}, '
                f'{self._expr_call(el["fy"], "t")}, 0])), t_range={tr}, '
                f'color={_color(el["color"])}, stroke_width={_num(el["stroke_width"])})')

    def _mk_surface(self, el):
        """
        曲面 z = f(x, y)：`_safe_eval(expr, u, y=v)` —— 表达式里的 x 就是 u、y 就是 v
        （`_FREE_VARS["surface"]` 放开的正是 y，见那里的说明）。

        u_range / v_range 只取前两个数：manim 的 Surface 收的是 (min, max) 二元区间，
        第三个"步长"在这里没有意义（采样密度由 Surface 自己的 resolution 决定）。
        """
        return (f'Surface(lambda u, v: {self._var(el["axes"])}.c2p('
                f'u, v, {self._expr_call(el["expr"], "u", "y=v")}), '
                f'u_range={self._rng(el["u_range"][:2])}, '
                f'v_range={self._rng(el["v_range"][:2])}, '
                f'color={_color(el["color"])}, '
                f'fill_opacity={_num(el["opacity"])}, stroke_width=0)')

    def _mk_space_curve(self, el):
        """空间曲线 (fx(t), fy(t), fz(t))：三维坐标系上的参数曲线。"""
        return (f'ParametricFunction(lambda t: {self._var(el["axes"])}.c2p('
                f'{self._expr_call(el["fx"], "t")}, '
                f'{self._expr_call(el["fy"], "t")}, '
                f'{self._expr_call(el["fz"], "t")}), '
                f't_range={self._rng(el["t_range"])}, '
                f'color={_color(el["color"])}, '
                f'stroke_width={_num(el["stroke_width"])})')

    def _mk_area(self, el):
        return (f'{_V}{el["axes"]}.get_area({_V}{el["curve"]}, '
                f'x_range={self._rng(el["x_range"])}, color={_color(el["color"])}, '
                f'opacity={_num(el["opacity"])}, stroke_width=0)')

    def _mk_riemann(self, el):
        a, b = el["x_range"][0], el["x_range"][1]
        n = max(1, int(el["n"]))
        dx = f"({_num(b)} - {_num(a)}) / {n}"
        return (f'{_V}{el["axes"]}.get_riemann_rectangles({_V}{el["curve"]}, '
                f'x_range=[{_num(a)}, {_num(b)}], dx={dx}, color={_color(el["color"])}, '
                f'fill_opacity={_num(el["opacity"])}, stroke_width=0.4, '
                f'input_sample_type="{el["sample"]}")')

    def _mk_dot(self, el):
        return (f'Dot(point={self._point_code(el["at"])}, color={_color(el["color"])}, '
                f'radius={_num(el["radius"])})')

    def _mk_line(self, el):
        cls = "DashedLine" if el.get("dashed") else "Line"
        return (f'{cls}({self._point_code(el["start"])}, {self._point_code(el["end"])}, '
                f'color={_color(el["color"])}, stroke_width={_num(el["stroke_width"])})')

    def _mk_arrow(self, el):
        return (f'Arrow({self._point_code(el["start"])}, {self._point_code(el["end"])}, '
                f'color={_color(el["color"])}, stroke_width={_num(el["stroke_width"])}, '
                f'buff={_num(el["buff"])})')

    def _mk_circle(self, el):
        s = (f'Circle(radius={_num(el["radius"])}, color={_color(el["color"])}, '
             f'stroke_width={_num(el["stroke_width"])})')
        s += f'.set_fill(color={_color(el["color"])}, opacity={_num(el["fill_opacity"])})'
        if el.get("at"):
            s = f'{s}.move_to({self._point_code(el["at"])})'
        return s

    def _mk_ellipse(self, el):
        return (f'Ellipse(width={_num(el["width"])}, height={_num(el["height"])}, '
                f'color={_color(el["color"])}, stroke_width={_num(el["stroke_width"])})'
                f'.set_fill(color={_color(el["color"])}, opacity={_num(el["fill_opacity"])})')

    def _mk_rect(self, el):
        return (f'RoundedRectangle(width={_num(el["width"])}, height={_num(el["height"])}, '
                f'corner_radius={_num(el["radius"])}, color={_color(el["color"])}, '
                f'stroke_width={_num(el["stroke_width"])})'
                f'.set_fill(color={_color(el["color"])}, opacity={_num(el["fill_opacity"])})')

    def _mk_polygon(self, el):
        pts = ", ".join(self._point_code(p) for p in el["points"])
        return (f'Polygon({pts}, color={_color(el["color"])}, '
                f'stroke_width={_num(el["stroke_width"])})'
                f'.set_fill(color={_color(el["color"])}, opacity={_num(el["fill_opacity"])})')

    def _mk_angle(self, el):
        v = self._point_code(el["vertex"])
        # 注意：Manim 的 Angle 用 radius，RightAngle 用 length —— 名字不同，别写错
        if el.get("right"):
            return (f'RightAngle(Line({v}, {self._point_code(el["a"])}), '
                    f'Line({v}, {self._point_code(el["c"])}), '
                    f'length={_num(el["radius"])}, color={_color(el["color"])})')
        return (f'Angle(Line({v}, {self._point_code(el["a"])}), '
                f'Line({v}, {self._point_code(el["c"])}), '
                f'radius={_num(el["radius"])}, color={_color(el["color"])})')

    def _mk_brace(self, el):
        d = DIRECTIONS.get(str(el["direction"]).lower(), "DOWN")
        if not el.get("label"):
            return f'Brace({_V}{el["of"]}, {d}, color={_color(el["color"])})'
        # 括号和文字要成为一个整体，先把括号落成临时变量再打包
        self.lines.append(
            f'        _brc = Brace({_V}{el["of"]}, {d}, color={_color(el["color"])})'
        )
        return (f'VGroup(_brc, rich(r"""{_esc(el["label"])}""", '
                f'font_size={el["font_size"]}, color={_color(el["color"])}'
                f').next_to(_brc, {d}, buff=0.12))')

    def _mk_table(self, el):
        rows = ", ".join(
            "[" + ", ".join(f'rich(r"""{_esc(c)}""", font_size={el["font_size"]})' for c in r) + "]"
            for r in el["rows"]
        )
        outer = "True" if el["outer_lines"] else "False"
        # 不给新参数时**生成的代码逐字节不变**（回归断言靠这条）：连 h_buff/v_buff
        # 都不显式传 —— 显式传"和默认值一样"的数也会改字节。
        keys = ("cell_w", "cell_h", "pad_x", "pad_y", "square")
        if all(not el.get(k) for k in keys):
            return f'MobjectTable([{rows}], include_outer_lines={outer})'
        # ⚠️ 留白必须在这里算好再显式传下去：`effective_pads()` 是"给了 cell_w 就用紧凑
        # 留白"这条规则的**唯一实现**，校验层（metrics.est_table_size）用的是同一个函数。
        # 若让运行时自己判默认值，两边就有了两份规则 —— 迟早漂移成"校验说放得下、
        # 渲染出来是另一个尺寸"。
        px, py = metrics.effective_pads(el.get("cell_w"), el.get("cell_h"),
                                        el.get("pad_x"), el.get("pad_y"))
        opts = [f"{k}={_num(el[k])}" for k in ("cell_w", "cell_h") if el.get(k) is not None]
        opts += [f"pad_x={_num(px)}", f"pad_y={_num(py)}"]
        if el.get("square"):
            opts.append("square=True")
        opts.append(f"include_outer_lines={outer}")
        return f'_table([{rows}], ' + ", ".join(opts) + ")"

    def _mk_cell_box(self, el):
        # 注意：build() 先把依赖（of 指向的 table）建好、定位、_fit 完，才轮到这一步，
        # 所以这里取到的格子就是表格**最终位置**上的格子。
        return (f'_cell_box({_V}{el["of"]}, {el["rows"]}, {el["cols"]}, '
                f'color={_color(el["color"])}, stroke_width={_num(el["stroke_width"])}, '
                f'buff={_num(el["buff"])})')

    def _mk_legend(self, el):
        items = ", ".join(
            f'[{_color(it[0])}, r"""{_esc(it[1])}"""]' for it in el["items"]
        )
        return f'_legend([{items}], font_size={el["font_size"]})'

    def _mk_highlight(self, el):
        return (f'SurroundingRectangle({_V}{el["of"]}, color={_color(el["color"])}, '
                f'buff={_num(el["buff"])}, corner_radius={_num(el["radius"])})')

    def _mk_tangent_line(self, el):
        # 切线：过 (x0, f(x0))，斜率 f'(x0) 用中心差分求。
        # 生成的代码始终在 always_redraw 内，支持 tracker 驱动。
        dx = 1e-5
        ax_v = f"{_V}{el['axes']}"
        xcode = self._coord(el["x"])
        names = sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", el["expr"])) & self.trackers)
        extra = ", ".join(f"{n}={_V}{n}.get_value()" for n in names)
        xp = f", {extra}" if extra else ""
        e = _esc(el["expr"])
        half = _num(el["length"])
        # f(x0) 的求值表达式
        fx0 = f'_safe_eval(r"""{e}""", {xcode}{xp})'
        # 中心差分求 f'(x0)
        slope = f'(_safe_eval(r"""{e}""", {xcode}+{dx}{xp}) - _safe_eval(r"""{e}""", {xcode}-{dx}{xp}))/(2*{dx})'
        return (
            f'Line('
            f'{ax_v}.c2p({xcode} - {half}/2, {fx0} - ({slope}) * {half}/2), '
            f'{ax_v}.c2p({xcode} + {half}/2, {fx0} + ({slope}) * {half}/2), '
            f'color={_color(el["color"])}, stroke_width={_num(el["stroke_width"])})'
        )

    def _mk_normal_line(self, el):
        # 法线：过 (x0, f(x0))，方向垂直于切线。
        # 切线方向 (1, k) → 法线方向 (-k, 1)，归一化后取 half 长度。
        # 用 np.hypot 避免 k≈0 时除零爆炸。
        dx = 1e-5
        ax_v = f"{_V}{el['axes']}"
        xcode = self._coord(el["x"])
        names = sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", el["expr"])) & self.trackers)
        extra = ", ".join(f"{n}={_V}{n}.get_value()" for n in names)
        xp = f", {extra}" if extra else ""
        e = _esc(el["expr"])
        half = _num(el["length"])
        fx0 = f'_safe_eval(r"""{e}""", {xcode}{xp})'
        slope = f'(_safe_eval(r"""{e}""", {xcode}+{dx}{xp}) - _safe_eval(r"""{e}""", {xcode}-{dx}{xp}))/(2*{dx})'
        # 法线方向 = (-k, 1)，归一化后 * half/2
        # x 方向分量 = -k / hypot(k, 1) * half/2
        # y 方向分量 =  1 / hypot(k, 1) * half/2
        return (
            f'Line('
            f'{ax_v}.c2p({xcode} - ({slope})/np.hypot({slope}, 1) * {half}/2, '
            f'{fx0} + 1/np.hypot({slope}, 1) * {half}/2), '
            f'{ax_v}.c2p({xcode} + ({slope})/np.hypot({slope}, 1) * {half}/2, '
            f'{fx0} - 1/np.hypot({slope}, 1) * {half}/2), '
            f'color={_color(el["color"])}, stroke_width={_num(el["stroke_width"])})'
        )

    def _mk_number(self, el):
        # 不直接生成 DecimalNumber：单位可能是中文，那种情况进 MathTex 必崩
        # （实测 `"unit": " 块"` → 整个分镜渲不出来）。分流交给运行时 _number。
        unit = f', unit=r"""{_esc(el["unit"])}"""' if el.get("unit") else ""
        return (f'_number({self._coord(el["value"])}, '
                f'num_decimal_places={el["decimals"]}{unit}, '
                f'font_size={el["font_size"]}, color={_color(el["color"])})')

    def _mk_tracker(self, el):
        return f'ValueTracker({_num(el["value"])})'

    def _mk_group(self, el):
        items = ", ".join(self._var(i) for i in el["items"] if isinstance(i, str))
        d = DIRECTIONS.get(str(el["direction"]).lower(), "DOWN")
        al = ALIGNED_EDGES.get(str(el.get("aligned", "left")).lower(), "LEFT")
        return (f'VGroup({items}).arrange({d}, aligned_edge={al}, '
                f'buff={_num(el["buff"])})')

    # ---------- 时间线 ----------

    # ---- parallel：真并行 ----
    #
    # 2026-09-15 之前这里是"把子动作顺序展开成多条 self.play(...)"—— 也就是
    # **`parallel` 这个承诺从来没兑现过**：模型写"圆画出来的同时文字写出来"，
    # 实际演的是"圆画完、再写文字"，总时长等于各子动作之和。契约型静默失效。
    #
    # 真并行的实现就一条：把子动作编译成**动画表达式**（而不是直接写行），
    # 合成**一条** `self.play(a, b, ...)`，交给 manim 按共同时间轴同时跑。
    #
    # 读源码确认过的两条事实（不是猜的）：
    #   · `Scene.get_run_time()`（manim/scene/scene.py）= `max(各动画 run_time)`
    #   · `AnimationGroup.build_animations_with_timings()`
    #     （manim/animation/composition.py）里子动画的 run_time 决定它自己的结束时刻
    # 所以"同时开始、各自按时长结束、整段 = max"正是 manim 的默认行为，
    # 不需要 AnimationGroup、也不该传外层 run_time（`_setup_animations()` 会把
    # `self.play` 的 kwargs 挨个 setattr 到**每个**子动画上，会把子动作时长全部抹平）。
    _PARALLEL_ANIM_ACTS = frozenset({
        "create", "write", "fade_in", "fade_out", "grow", "draw_border",
        "indicate", "circumscribe", "flash", "wiggle", "focus",
        "shift", "move_to", "move_cells",
        "scale", "rotate", "set_color", "set_opacity", "set_stroke", "stretch",
    })
    # show / remove / trace 不是 .animate 类动画，manim 不接受，只能降级
    _PARALLEL_INSTANT_ACTS = frozenset({"show", "remove", "trace"})

    _tmp_n = 0          # 「旋转 + 平移」合成刚体运动时的临时变量计数

    def _parallel(self, ac):
        subs = ac.get("actions") or []
        # 先按 target 归并（保持出现顺序）：同一元素上的子动作交给 _parallel_group 归约 ——
        # 其中「旋转 + 平移」会合成**一次刚体运动**（见 _rigid_rotate）。
        # target 多于一个的动作（如 show/remove/trace）会展开成多条语句，塞不进一条 self.play，
        # 由校验层的展平保证这里不会遇到，真遇到就整段降级（见下）。
        groups = []
        for sub in subs:
            ts = sub.get("target") or []
            if sub.get("do") in self._PARALLEL_INSTANT_ACTS or len(ts) != 1:
                groups.append((None, [sub]))
                continue
            for g in groups:
                if g[0] == ts[0]:
                    g[1].append(sub)
                    break
            else:
                groups.append((ts[0], [sub]))

        exprs, prelude = [], []
        for t, lst in groups:
            one = self._parallel_group(t, lst, prelude)
            if one is None:
                break
            exprs.append(one)
        else:
            if exprs:
                # 支点那几行必须在动画**之前**算（它们读的是元素当前的中心）
                self.lines.extend("        " + p for p in prelude)
                self.lines.append(f"        # >> 真并行：{len(exprs)} 条子动作合成一条 self.play")
                self.lines.append("        self.play(" + ", ".join(exprs) + ")")
                return

        # ---- 降级：顺序播放，但**如实**写出"这段是顺序的" ----
        # 能走到这里说明子动作里有 show/remove/trace、有多个 target，或同一个元素被
        # 三个以上子动作碰（"先放大再旋转"这类本来就有先后，顺序语义才是对的）。
        # 宁可顺序播（画面还是对的）也不能生成 manim 会报错的代码。
        self.lines.append("        # >> 降级为顺序播放：含无法并行的子动作")
        for i, sub in enumerate(subs):
            # show / remove 是幂等的即时操作，重复执行没有副作用，直接丢掉
            if i > 0 and sub.get("do") in ("show", "remove"):
                continue
            # trace 第二次会重复加一条 TracedPath —— 保留第一条（它本来就不吃并行）
            if i > 0 and sub.get("do") == "trace":
                continue
            self.lines.append(f"        # >> 顺序子动作 > {sub.get('do', '?')}")
            self.act(sub)

    def _parallel_group(self, target, subs, prelude):
        """把一个元素上的若干子动作归约成**一条** manim 动画表达式；做不到返回 None。"""
        if target is None or not subs:
            return None
        if len(subs) == 1:
            if subs[0].get("do") not in self._PARALLEL_ANIM_ACTS:
                return None
            # 用 _anim_timed：把子动作自己的 run_time 注入到**它的表达式内部**
            # （为什么不拼在外面 / 不写在外层，见 _anim_timed 的 docstring）
            return self._anim_timed(subs[0]["do"], self._var(target), subs[0])
        # 恰好两条、且是「旋转 + 平移」→ 合成一次刚体运动
        dos = sorted(s.get("do") for s in subs)
        if len(subs) == 2 and dos in (["move_to", "rotate"], ["rotate", "shift"]):
            rot = next(s for s in subs if s["do"] == "rotate")
            mv = next(s for s in subs if s["do"] != "rotate")
            return self._rigid_rotate(target, rot, mv, prelude)
        return None

    def _rigid_rotate(self, target, rot, mv, prelude):
        """
        「旋转 θ + 平移到目标」合成**一次** manim 的 `Rotate` —— 绕一个算出来的支点转。

        为什么必须合成一条：manim 里同一个 mobject 的两条动画各自取初态、会互相打架；
        而 `.animate` 链（`x.animate.rotate(θ).move_to(p)`）是**逐点线性插值**，
        转 180° 时图形会从中间塌过去 —— 用户 2026-09-17 反馈的「旋转时发生形变」正是这个。

        支点：绕 P 转 θ 会把 C0 送到 C1，当
            P = C0 + d/2 + (cot(θ/2)/2)·J(d)，  d = C1 − C0，J 是逆时针 90°
        时成立（θ = 180° 时 cot 项为 0，P 就是中点；θ → 0 退化成纯平移）。
        于是「转 θ」和「把中心挪到 C1」一次完成，而且全程是刚体 —— 不形变。
        """
        var = self._var(target)
        rad = round(float(rot["angle"]) * 3.141592653589793 / 180.0, 6)
        if rad == 0:                       # 没转，就是纯平移
            return self._anim_timed(mv["do"], var, mv)
        rt = max(x.get("run_time") or 0.0 for x in (rot, mv)) or None
        self._tmp_n += 1
        n = self._tmp_n
        prelude.append(f"_c0_{n} = {var}.get_center()")
        if mv["do"] == "move_to":
            prelude.append(f"_d_{n} = {self._point_code(mv['point'])} - _c0_{n}")
        else:                              # shift：位移就是向量
            prelude.append(f"_d_{n} = np.array([{_num(mv['vector'][0])}, "
                           f"{_num(mv['vector'][1])}, 0])")
        prelude.append(
            f"_piv_{n} = _c0_{n} + _d_{n} / 2 + "
            f"np.array([-_d_{n}[1], _d_{n}[0], 0]) * (0.5 / np.tan({rad} / 2))")
        rt_s = f", run_time={_num(rt)}" if rt else ""
        return f"Rotate({var}, {rad}, about_point=_piv_{n}{rt_s})"

    def act(self, ac):
        do = ac["do"]
        if do == "wait":
            self.lines.append(f"        self.wait({_num(ac['time'])})")
            return
        if do == "clear_all":
            rt = ac.get("run_time", 0.5)
            self.lines.append(
                "        if self.mobjects:\n"
                f"            self.play(FadeOut(Group(*self.mobjects)), run_time={_num(rt)})\n"
                "            self.clear()"
            )
            return
        if do == "parallel":
            self._parallel(ac)
            return

        if "inline" in ac:
            self.els[ac["inline"]["id"]] = ac["inline"]
            self.build(ac["inline"])
        if "inline_into" in ac:
            self.els[ac["inline_into"]["id"]] = ac["inline_into"]
            self.build(ac["inline_into"])

        rt = ac.get("run_time")
        rt_s = f", run_time={_num(rt)}" if rt else ""
        lr = ac.get("lag_ratio")
        lr_s = f", lag_ratio={_num(lr)}" if lr is not None else ""

        def v(i):
            # 必须过 _var：被 replace 过的 id 现在挂在另一个变量上（见 _var 的说明）
            return self._var(i)

        if do == "show":
            for t in ac["target"]:
                self.lines.append(f"        self.add({v(t)})")
            return
        if do == "remove":
            for t in ac["target"]:
                self.lines.append(f"        self.remove({v(t)})")
            return
        if do == "tracker_to":
            for t in ac["target"]:
                self.lines.append(
                    f"        self.play({v(t)}.animate.set_value({_num(ac['to'])}), "
                    f"run_time={_num(rt or 3.0)}, rate_func={ac['rate_func']})"
                )
            return
        if do == "trace":
            for t in ac["target"]:
                self.lines.append(
                    f"        self.add(TracedPath({v(t)}.get_center, "
                    f"stroke_color={_color(ac['color'])}, stroke_width={_num(ac['width'])}))"
                )
            return
        if do == "rotate_camera":
            # 相机运动：`ThreeDScene.move_camera` 自带动画，可直接给 run_time。
            # 角度制 → manim 要弧度，所以生成 `theta * DEGREES`。
            # （能在 2D 的 Scene 上调这个方法是校验层的责任：_check_3d 已拦住这种情况。）
            kw = []
            for k in ("theta", "phi"):
                if ac.get(k) is not None:
                    kw.append(f"{k}={_num(ac[k])} * DEGREES")
            if ac.get("zoom") is not None:
                kw.append(f"zoom={_num(ac['zoom'])}")
            self.lines.append(f"        self.move_camera({', '.join(kw)}{rt_s})")
            return

        if do == "transform":
            a, b = ac["target"][0], ac["into"]
            self.lines.append(f"        self.play(Transform({v(a)}, {v(b)}){rt_s})")
            return
        if do == "replace":
            a, b = ac["target"][0], ac["into"]
            eff = ac.get("effect", REPLACE_EFFECT_DEFAULT)
            va, vb = v(a), v(b)
            if eff == "morph":
                # 旧行为：形变，旧字飞向新字的位置再变成新字。字幕类内容别用它。
                play = f"ReplacementTransform({va}, {vb})"
            elif eff == "write":
                # 旧的先淡出、新的逐字写出。
                play = f"FadeOut({va}), Write({vb})"
            elif eff == "slide":
                # 旧的向上淡出、新的从下方顶上来（同一个 shift 方向才是"往上换一条"）。
                shift = f"UP * {_num(REPLACE_SLIDE_SHIFT)}"
                play = f"FadeOut({va}, shift={shift}), FadeIn({vb}, shift={shift})"
            else:                                   # crossfade（默认）
                play = (f"FadeOut({va}, rate_func=_xfade_out), "
                        f"FadeIn({vb}, rate_func=_xfade_in)")
            # ⚠️ 四种效果合在一条 `self.play(...)` 里是安全的，依据是 manim 的两条源码事实
            # （2026-09-16 核对，不是猜的）：
            #   · `Animation._setup_scene()` 只对 **introducer** 调 `scene.add` —— FadeIn/Write
            #     都是 introducer，新对象会在**淡入状态**下进场景（begin() → interpolate(0)
            #     把它设成 starting_mobject，也就是那个 opacity=0 的副本），不会先整整一帧不透明地闪一下；
            #   · `Scene.add_mobjects_from_animations()` 对 introducer 直接跳过，所以也不会
            #     在 play 开头被提前加上屏。
            #   · FadeOut 是 remover，结束时把旧对象 `scene.remove` 掉。
            # 也就是说四种效果的**对象身份结果完全一致**：旧的离场、新的在场 —— 所以下面
            # 那句换绑对四种都成立，不需要分支。
            self.lines.append(f"        self.play({play}{rt_s})")
            # 替换完成之后，id `a` 这个"槽位"里装的就是 b 那个物体了 —— 必须换绑，
            # 否则后续引用 `a` 的动作会打到一个已经被移出场景的旧对象上，
            # 而 manim 会"把被动画却不在场景里的对象重新加回场景"，把旧内容叠回来。
            self.alias[a] = self.alias.get(b, b)
            # ⚠️ 2026-09-17 补：换绑只管**本镜编译器**（`self.alias` 是 `_Builder` 的），
            # 而分镜边界的清屏/末尾淡出片段是**渲染器**拼的 —— 它按 id 直接拼变量名
            # （`_clear_except` / `_tail_fade` → `dsl.var_name(id)`），不知道 `a` 已经换人了。
            # 于是把**屏幕上真正的那个新对象**当成"上一镜的残留"淡掉：
            # 实测（卷积示例）镜 2 末尾 `replace tb_out into tb_out1` 之后，镜 3 一开头的
            # `_keep` 里留的还是早已离场的 `_e_tb_out`，装好 37 的输出表整块消失，
            # 直到镜 3 末尾自己再 replace 一次才又出现 —— 看起来就像"输出没 carry"。
            # 把原名也指到新对象上，两边就一致了（`_keep` 里的名字仍然是它，但指的是现在的它）。
            self.lines.append(f"        {_V}{a} = {vb}")
            return
        if do == "move_along":
            a = ac["target"][0]
            self.lines.append(f"        self.play(MoveAlongPath({v(a)}, {v(ac['path'])}){rt_s})")
            return

        # 通用动画：target 可以是多个 → 并行播放
        anims = []
        for t in ac["target"]:
            anims.append(self._anim(do, v(t), ac))
        if not anims:
            return
        body = ", ".join(anims)
        # 多目标且给了 lag_ratio → 合成 VGroup 依次播放
        if len(anims) > 1 and lr is not None:
            ids = ", ".join(v(t) for t in ac["target"])
            body = f"{self._anim_cls(do)}(VGroup({ids}){self._anim_extra(do, ac)})"
        self.lines.append(f"        self.play({body}{rt_s}{lr_s})")

    def _anim_cls(self, do, extra=None):
        return {
            "create": "Create", "write": "Write", "fade_in": "FadeIn",
            "draw_border": "DrawBorderThenFill", "fade_out": "FadeOut",
        }.get(do, "FadeIn")

    def _anim_extra(self, do, ac):
        if do == "fade_in" and ac.get("shift"):
            return f", shift={DIRECTIONS[ac['shift']]} * 0.3"
        return ""

    def _anim(self, do, var, ac):
        """构造动画表达式（**不带** run_time；时长由调用方决定怎么加）。"""
        if do == "create":
            return f"Create({var})"
        if do == "write":
            return f"Write({var})"
        if do == "fade_in":
            return f"FadeIn({var}{self._anim_extra(do, ac)})"
        if do == "fade_out":
            return f"FadeOut({var})"
        if do == "grow":
            if ac.get("from", "center") == "center":
                return f"GrowFromCenter({var})"
            return f"GrowFromEdge({var}, {DIRECTIONS[ac['from']]})"
        if do == "draw_border":
            return f"DrawBorderThenFill({var})"
        if do == "indicate":
            return f"Indicate({var}, color={_color(ac['color'])})"
        if do == "circumscribe":
            return f"Circumscribe({var}, color={_color(ac['color'])})"
        if do == "flash":
            return f"Flash({var}, color={_color(ac['color'])})"
        if do == "wiggle":
            return f"Wiggle({var})"
        if do == "focus":
            return f"FocusOn({var})"
        if do == "shift":
            return (f"{var}.animate.shift(np.array([{_num(ac['vector'][0])}, "
                    f"{_num(ac['vector'][1])}, 0]))")
        if do == "move_to":
            return f"{var}.animate.move_to({self._point_code(ac['point'])})"
        if do == "move_cells":
            # 位置**和大小**都由目标格子算出来：直接变形到"框住这组格子"的那个新框。
            #
            # 为什么不是 `.animate.move_to(_cells_center(...))`：move_to 只挪不改大小，
            # 框还是声明时的那个宽度。跨度一变就必然歪 —— 2026-09-16 实测：`win`
            # 声明 cols=[1,8]（整张表宽），后面挪到 cols=[8]（只剩最后一格），
            # 结果一个 8 格宽的蓝框被居中到第 8 格上，右边探出画面、左边横穿整张表。
            # 变形到 `_cell_box(...)` 就两种情况都对：同宽就是纯滑动（卷积核那种），
            # 改宽就是跟着格子重新贴合（二分查找候选范围逐轮减半那种）—— 不需要分支。
            box = self.els.get(ac["target"][0]) or {}
            return (
                f"Transform({var}, _cell_box({self._var(ac['of'])}, "
                f"{ac.get('rows') or box.get('rows')}, {ac.get('cols') or box.get('cols')}, "
                f"color={_color(box.get('color', 'RED'))}, "
                f"stroke_width={_num(box.get('stroke_width', 3.5))}, "
                f"buff={_num(box.get('buff', 0.04))}))"
            )
        if do == "scale":
            return f"{var}.animate.scale({_num(ac['factor'])})"
        if do == "rotate":
            # ⚠️ 2026-09-17 修：原来生成 `x.animate.rotate(θ)` —— `.animate` 是**逐点线性
            # 插值**，转 180° 时三角形的三个顶点各自走弦、中途会塌成一条线（用户反馈的
            # 「旋转的时候进行了形变」）。manim 的 `Rotate` 才是刚体旋转
            # （`about_point=None` 时绕自身中心转，实测源码确认）。
            rad = round(float(ac["angle"]) * 3.141592653589793 / 180.0, 6)
            return f"Rotate({var}, {rad})"
        if do == "set_color":
            return f"{var}.animate.set_color({_color(ac['color'])})"
        if do == "set_opacity":
            return f"{var}.animate.set_opacity({_num(ac['opacity'])})"
        if do == "set_stroke":
            return (f"{var}.animate.set_stroke(color={_color(ac['color'])}, "
                    f"width={_num(ac['width'])})")
        if do == "stretch":
            return f"{var}.animate.stretch({_num(ac['factor'])}, dim={ac['dim']})"
        raise DSLError(f"动作 {do!r} 没有对应实现（spec 与实现不一致）")

    def _anim_timed(self, do, var, ac):
        """
        `_anim()` + 把这个子动作自己的 run_time 加进去 —— **只有真并行需要它**。

        为什么不能像普通路径那样把 `run_time=` 拼在 `self.play(...)` 的末尾：
        `self.play(Create(x), run_time=2, Write(y))` 是 **Python 语法错误**
        （位置参数跟在关键字参数之后）。第一版就是这么写的，被
        `renderer._check_syntax()` 的 compile() 兜底拦下 —— 那道闸存在的意义正在于此。
        而 `run_time` 又**不能**写在外层：manim 的 `_setup_animations()` 会把 play 的
        kwargs 挨个 setattr 到**每个**子动画上，把各自的时长抹平。
        所以只能逐个注入到各自表达式的内部。

        两种形态注入方式**不一样**：
          · 类构造器 `Create(x)`  → 在最后一个右括号前插关键字参数：`Create(x, run_time=2)`
          · `.animate` 链        → 必须写成**调用形式**并在方法**之前**：
                                   `x.animate(run_time=2).scale(1.5)`

        ⚠️ `.animate` 那条实测踩过坑（2026-09-15）：想当然写成
        `x.animate.scale(1.5).set_run_time(2)` —— **它会被静默忽略，时长仍是默认 1.0s**。
        原因是 `set_run_time` 是 `Animation` 上的方法、**不在 Mobject 上**
        （实测 `hasattr(Mobject, "set_run_time") == False`），于是
        `_AnimationBuilder.__getattr__` 把它当成一个普通的目标方法转发到
        `mobject.target` 上，然后……什么都不发生。既不报错也没有效果 ——
        正是本项目最忌讳的静默失效，而症状会是"估时 0.4s、实际演 1.0s"的时长漂移。
        三种写法实测：`animate(run_time=.4).scale()` → 0.40s ✓ ｜
        `animate.scale().set_run_time(.4)` → 1.00s ✗ ｜ 不给 → 1.00s。
        """
        expr = self._anim(do, var, ac)
        rt = ac.get("run_time")
        if not rt:
            # 不给就用 manim 自己的默认（通常 1.0s）。真并行下"每个子动作各自时长"
            # 本来就得逐个写出来，这是拿回该能力的代价。
            return expr
        if ".animate." in expr:
            return expr.replace(".animate.", f".animate(run_time={_num(rt)}).", 1)
        assert expr.endswith(")"), expr
        return f"{expr[:-1]}, run_time={_num(rt)})"


def _est_action_time(ac):
    """
    估算一个动作真正占用的时长（供"软时长"补 wait 用）。

    ⚠️ parallel 必须与 `_Builder._parallel()` **保持一致**，这两处是一对：

    | 实现 | 实际耗时 | 这里必须 |
    |---|---|---|
    | 顺序展开成多条 self.play（2026-09-15 之前） | 各子动作之和 | `sum` |
    | 合成一条 self.play（现在，真并行） | 各子动作的 **max** | `max` |

    改一处不改另一处，分镜就会莫名提前结束（估多了）或莫名拖一截（估少了）。
    这也是 manim 自己的语义：`Scene.get_run_time()` 取的就是 max。
    """
    if ac["do"] == "wait":
        return float(ac["time"])
    if ac["do"] == "parallel":
        # 空 actions 在规范化阶段已被拒，这里 max(..., default=0) 只是防御
        return max((_est_action_time(s) for s in ac.get("actions") or []), default=0.0)
    rt = ac.get("run_time") or ACTION_SPEC[ac["do"]]["run_time"]
    return float(rt) if rt is not None else 0.0


def build_scene(scene: dict, env: dict) -> str:
    """把一个 v2 分镜（elements + timeline）编译成 construct() 体内的代码。"""
    # 本分镜是不是三维分镜（含 three_axes）。env 是**整份分镜共用**的那一份，
    # 而"这一镜有没有 3D"是逐镜的（拆分渲染时更是每个文件一镜），所以这里拷贝一份再标注，
    # 免得把上一镜的状态漏给下一镜；同时也保证纯 2D 分镜的生成源码逐字节不变。
    ax3 = next((el for el in scene["elements"]
                if el.get("kind") == "three_axes"), None)
    env = dict(env)
    env["d3"] = ax3 is not None

    b = _Builder(env)
    for el in scene["elements"]:
        b.els[el["id"]] = el

    # 三维分镜：**先把相机拨到声明好的初始视角**再开始画。
    # 不写这一步的话，manim 的 ThreeDScene 默认是 phi=0 的俯视 ——
    # 一个曲面从正上方看就是一张平面图，立体感全无（模型写的 phi/theta 也就看不出效果）。
    if ax3:
        b.lines.append(
            f"        self.set_camera_orientation("
            f"phi={_num(ax3.get('phi', 70.0))} * DEGREES, "
            f"theta={_num(ax3.get('theta', -45.0))} * DEGREES)"
        )

    for el in scene["elements"]:
        b.build(el)

    for ac in scene["timeline"]:
        b.lines.append(f"        # >> {ac['do']}")
        b.act(ac)

    # 软时长：timeline 跑完还没到 duration 就补 wait，保证节奏不赶
    if scene.get("duration"):
        est = sum(_est_action_time(ac) for ac in scene["timeline"])
        pad = round(float(scene["duration"]) - est, 2)
        if pad > 0.1:
            b.lines.append(f"        self.wait({pad})")

    return "\n".join(b.lines)


# ==============================================================================
# 给 LLM 看的说明（可直接塞进 system prompt）
# ==============================================================================

def _wrap_names(names, per_line=10):
    """
    把名称集合排成几行。

    为什么分行而不是拼成一整行：颜色有 70+ 个，挤成一行时模型很容易"读漏一半"，
    分行后它整段照抄的成功率高得多（同 §8.10：规格里的关键取值要让模型能一眼抄完）。
    """
    items = sorted(names)
    return ["、".join(items[i:i + per_line]) for i in range(0, len(items), per_line)]


def describe(include_place=True) -> str:
    """把元素库与动作表渲染成一份紧凑的规格说明 —— 喂给 LLM 用，永不手写第二份。"""
    out = ["# 分镜 DSL 规格（严格按此输出 JSON）", ""]
    out.append("## 场景结构")
    out.append("```")
    out.append('{"id":1, "elements":[...], "timeline":[...], "duration":6}')
    out.append("```")
    out.append("elements = 画面上有哪些东西；timeline = 它们按什么顺序出场/变化。")
    out.append("")
    # ⚠️ 2026-09-17 改：原文是"目标秒数（4~12）" —— §8.31 把那条硬约束改成"建议"时
    # 只改了 llm.py 的手写规范，规格里这句没跟着改。它比校验层（0.5~120）严得多，
    # 且与"连贯过程用一个长分镜一口气演完"直接冲突 —— 模型会把长过程硬切在 12 秒处。
    # 秒数按内容定，这里只留真实上限。
    out.append("`duration` 是这一镜的**目标秒数**（按内容定，上限 120 秒），它是「软」的：")
    out.append("- 算式 = timeline 里每个动作的耗时相加（parallel 段按其中**最长**的那个算）"
               "，省略 run_time 时就用下面动作表里给的默认值；")
    out.append("- 这个和**小于** duration → 自动补一段停顿，节奏不会赶；")
    out.append("- 这个和**大于** duration → 不报错，但视频会比声明的长，所以别把动作堆太满。")
    out.append("算这道加法时要带上默认 run_time —— 例如 `create`(1.2) + `wait`(0.6) "
               "对应 duration 1.8，不是 0.6。")
    out.append("")

    out.append("## 跨分镜延续：carry（承接）")
    out.append("**默认每个分镜都从空白画面开始**：分镜边界会把上一镜的东西全部淡出。"
               "所以同一个东西在下一镜里只能**重新画一遍** —— 而重画在观感上等于"
               "「从头再来」，讲连贯过程（数组一直在、指针一步步挪、迭代逐步收敛）时很别扭。")
    out.append("要让一个东西跨分镜留着，在**后续分镜**的 elements 里这样写：")
    out.append("```")
    out.append('{ "id": "arr", "carry": true }')
    out.append("```")
    out.append("意思：`arr` 在上一分镜结束时就在屏上，本分镜**不要重画它、也不要清掉它**。"
               "之后可以照常用动作动它（`move_cells` / `move_to` / `set_color` `indicate`…）。")
    out.append("三条硬性规则：")
    out.append("1. 只写 `id` 和 `carry`，**不能**再写 kind / 参数 —— 内容沿用上一分镜的声明。"
               "要改画面请在 timeline 里用动作，靠重写参数是改不动的。")
    out.append("2. 上一分镜结束时它必须**真的在屏上**：被 create / write / fade_in / grow / "
               "draw_border / show 上过屏，且之后没被 fade_out / remove / clear_all 清掉。"
               "（声明了却从没被动作点过的元素不算在屏上。)")
    out.append("3. 它依赖的东西也要一起 carry：`plot` 的 `axes`、`cell_box` 的 `of`、"
               "`group` 的 `items`，**以及它表达式里引用的 tracker**"
               "（点写成 `\"y\": \"s**2\"`，那 `s` 也得一起承接）——"
               "否则依赖会被分镜边界清掉，画面只剩一个飘着的东西。")
    # ⚠️ 2026-09-17 加：这条原来只列了三种**元素**依赖，漏了 tracker。照着规格写也会撞：
    # 承接一个引用 tracker 的元素、却没承接那个 tracker → 表达式校验报「xx 未定义」，
    # 报错完全看不出是 carry 的事（写 few-shot 示例时踩了一次）。
    # 但**不能只补一句"把 tracker 也 carry"**：拆分渲染下每个分镜是独立 Scene，
    # 承接来的 tracker 会回到**声明时的初始值**（不是上一镜结束时的值）——
    # 上一镜扫到 3、这一镜却是 1，点会当场跳回去。所以两条路都得写清楚。
    out.append("⚠️ 但**承接一个 tracker 要格外小心**：拆分渲染下每个分镜是独立的 Scene，"
               "承接来的 tracker 会回到**声明时的初始值**——上一镜扫到 3、这一镜却是 1，"
               "点会当场跳回去。这种情况**别 carry**，改用**固定值**在本镜重新声明一遍"
               "（写 `\"x\": 3`，取上一镜结束时的那个数），画面才接得上。")
    out.append("")

    out.append("## 元素类型（kind）")
    for k, spec in ELEMENT_SPEC.items():
        ps = ", ".join(
            f"{n}{'' if spec['params'][n][1] is None else '=' + repr(spec['params'][n][1])}"
            for n in spec["params"]
        )
        out.append(f"- **{k}**({ps})  ")
        out.append(f"  {spec['desc']}")
    out.append("")

    out.append("## 布局（元素的可选 place 字段，按顺序应用）")
    out.append("```")
    out.append('"place": [{"edge":"up","buff":0.8}, {"shift":[0,-0.5]}]')
    out.append("```")
    out.append("可用键：`edge`(up/down/left/right) `corner`(UL/UR/DL/DR) "
               "`next_to`({of,direction,buff,aligned}) `at_point` "
               "`cell`({of,row,col}：贴在表格的某一格上，把格子里的数「提出来」时用它) "
               "`shift`([dx,dy]) `scale` `rotate`(度) `center` `fit_width` `z`")
    # ⚠️ 实测（2026-09-12）：不写这句话，模型会写出 `"place":[{"center"}]` —— 看着像
    # JSON、其实少了个 `: true`，直接解析失败。`{ "center" }` 是 JS 简写语法，
    # 不是 JSON；只在键名列表里提过 `center`，模型就会照着简写。必须给真值示例。
    out.append("⚠️ 以上键都是**普通 JSON 键，必须带值**："
               "`{\"center\": true}`（不是 `{\"center\"}`）、`{\"scale\": 0.8}`、"
               "`{\"z\": 5}`。写成 `{\"center\"}` 不是合法 JSON，解析会直接失败。")
    out.append("点坐标三种写法：`[x,y]` / `{\"ref\":\"ax\",\"x\":2,\"y\":3}`（坐标系里的点）"
               " / `{\"of\":\"g1\",\"anchor\":\"top\"}`（另一元素的锚点）")
    out.append("")

    out.append("## 动作（do）")
    for k, spec in ACTION_SPEC.items():
        need = "需要 target" if spec["target"] else "无需 target"
        extra = f"，附加参数：{spec['args']}" if spec.get("args") else ""
        out.append(f"- **{k}**（{need}，默认 run_time={spec['run_time']}）：{spec['desc']}{extra}")
    out.append("")
    out.append("target 可以是元素 id、id 数组（并行播放），或直接内联一个元素对象。")
    out.append("⚠️ 附加参数的名字是**写死的**，按上面括号里写的来：move_to 用 `point`、"
               "shift 用 `vector`、tracker_to 用 `to`、move_cells 用 `of`+`rows`/`cols`、"
               "replace 用 `into`+`effect`。"
               "名字写错（或漏写）会直接被拒绝重做。")
    out.append("")
    out.append("## 同一位置换内容：用 replace（不要另 create 一个叠上去）")
    out.append("底部字幕/说明这类「位置不变、内容换掉」的地方，用 `replace`，"
               "它会把旧的**下屏**、新的**上屏**；另 create 一个新元素会与旧的叠在一起。"
               "不写 `effect` 就是 `crossfade`（旧的淡出、新的淡入），另有 "
               "`slide`（上滑交叉）/ `write`（逐字写出）/ `morph`（形变，适合图形变形）：")
    out.append("```")
    out.append('{"id":"cap","kind":"text","content":"先看它在 x=1 处的情形",'
               '"place":[{"edge":"down","buff":0.5}]}')
    out.append('{"do":"replace","target":"cap","into":{"id":"cap2","kind":"text",'
               '"content":"斜率算出来是 2，所以切线是 y = 2x - 1",'
               '"place":[{"edge":"down","buff":0.5}]},"effect":"slide","run_time":1.2}')
    out.append("```")
    # ⚠️ 2026-09-17 改：原示例的 `into` 没写 place，教出来的是"换一条字幕就跳回画面正中"
    # —— replace 用的是**新元素自己**的位置，不继承旧字幕的 place（见 _place_calls）。
    # 字幕类内容位置必须稳定，所以示例里把 place 写全，并把这条规则点破。
    out.append("⚠️ `into` 里的新元素**要写和原来一样的 `place`**，"
               "否则新文字会跑到画面正中：replace 用的是新元素自己的位置，不会继承旧的。")
    out.append(metrics.budget_line())
    out.append("")

    out.append("## 动态效果（重点）")
    out.append("1. 声明 `{\"id\":\"t\",\"kind\":\"tracker\",\"value\":0}`")
    out.append("2. 任何数值位置写 `\"$t\"` 即引用其当前值，例如 `\"x_range\":[0,\"$t\"]`；")
    out.append("   也可以混写成表达式，如 `\"x\":\"$t - 1.1\"`、`\"y\":\"t**2 - 2*t\"`")
    out.append("3. 用 `{\"do\":\"tracker_to\",\"target\":\"t\",\"to\":6.28,\"run_time\":5,\"rate_func\":\"linear\"}` 驱动")
    out.append("4. 函数表达式里可直接写 tracker 名，如 `\"expr\":\"sin(x + t)\"`")
    out.append("5. 需要跟着动但自己不引用 tracker 的元素（例如始终套在动点外的框），加 `\"live\": true`")
    out.append("6. 实时显示数值用 `number` 元素，`\"value\":\"2*t\"` 会随 tracker 刷新；"
               "`\"unit\"` 是显示在数字右边的单位后缀，写中文（如 `\" 块\"`）没问题")
    out.append("")
    # 刻意不写引擎那侧的词汇（always_redraw / add 之类）：红线 1 明确要求模型
    # "别把 Manim 的名字搬过来"，规格自己就不该先把它们摆出来。
    out.append("含 tracker 引用（或 live）的元素是**每帧重算**的对象：")
    # ⚠️ 2026-09-17 改：原来写"必须用 `show` 显式显示"，等于把 **show（瞬间出现）**当成了
    # 唯一出路。实测动态元素用 `fade_in` 上屏完全有效 —— 会真的淡入（半途帧是半透明的），
    # 而且淡入后 updater 照常工作（点继续跟着 tracker 走）。见 HANDOFF §8.47。
    # 而"只能瞬间出现"正是"画面里东西啪地冒出来"这种生硬过渡的来源。
    out.append("**它们不会自动上屏，必须显式上屏**：`show` 是瞬间出现、`fade_in` 是淡入 —— "
               "**要过渡自然就用 `fade_in`**（淡入之后它照旧每帧重算）。")
    # 2026-09-17 改：这里原先是「鼓励动态」的正面表态（"不该放弃动态""就该动起来"），
    # 与 llm.py 取舍常识里那条同步收紧为中性 —— 动态不是加分项，按画面需不需要来定。
    out.append("每帧重算只是**慢一点**，它既不比静态好、也不比静态差："
               "用不用只看画面需不需要。")
    out.append("")
    out.append("## 网格与滑动窗口（卷积 / 池化 / 棋盘格 / 矩阵）")
    out.append("讲这类内容：格子用 `table`（数字天然落在格子里，不要用 text 拼多行数字），")
    out.append("框选/滑动的窗口用 `cell_box` —— 它的位置和大小都由格子算出来，**绝不会和网格错位**：")
    out.append("```")
    out.append('{"id":"img","kind":"table","rows":[["1","0","1"],["0","1","0"],["1","1","0"]]}')
    out.append('{"id":"win","kind":"cell_box","of":"img","rows":[1,2],"cols":[1,2],"color":"RED"}')
    out.append('{"do":"move_cells","target":"win","of":"img","rows":[1,2],"cols":[2,3],"run_time":1}')
    out.append("```")
    out.append("行号/列号**从 1 开始**（左上角是第 1 行第 1 列）。")
    out.append("`rows`/`cols` 换了跨度也可以：框会跟着重新贴合新格子。所以"
               "「候选范围一轮轮变小」（二分查找那种）直接写目标格号即可，"
               "不需要重建元素。")
    out.append("格子尺寸默认由内容撑出来（一个长数字会把整列撑宽）。想统一/紧凑："
               "`table` 加 `\"cell_w\":1.2, \"cell_h\":0.9` 让所有格子一样大，"
               "`\"pad_x\":0.4` 调内容到格线的留白（默认 1.3 偏大）。")
    out.append("⚠️ 不要用 `rect` + `move_to` 自己估坐标去框格子：表格格子的尺寸是内容撑出来的，")
    out.append("估出来的框一定会歪（校验层也会直接拒绝 cell_box 上的 move_to/shift/scale）。")
    out.append("")
    out.append("## 三维（立体）")
    out.append("用 `three_axes` 建空间坐标系，把曲面 / 空间曲线 / 空间点挂上去，"
               "再用 `rotate_camera` 转视角 —— 立体感要靠视角动起来才看得出来。")
    out.append("```")
    out.append('{"id":"ax3","kind":"three_axes","x_range":[-3,3,1],"y_range":[-3,3,1],"z_range":[-3,3,1]}')
    out.append('{"id":"s","kind":"surface","axes":"ax3","expr":"x**2 + y**2","color":"PRIMARY"}')
    out.append('{"do":"rotate_camera","theta":-25,"phi":65,"run_time":3}')
    out.append("```")
    out.append("三条硬性规则：")
    out.append("1. `surface` / `space_curve` 的 `axes` **必须是 three_axes**；"
               "带 z 的点（`{ref,x,y,z}`）也一样 —— 挂在 2D 的 `axes` 上时 z 会被"
               "**默默丢掉**（画面看着像对、其实是错的），校验层会直接拒绝。")
    out.append("2. `rotate_camera` 的角度是**角度制**：`phi` 是俯仰角"
               "（0 = 平视、90 = 从正上方往下看），`theta` 是水平转角（负值反向）。"
               "至少要给一个，否则等于没转。")
    out.append("3. 三维分镜里的文字/公式分两种，按 `place` 区分："
               "写了 `place`（edge / corner / next_to / center）的是**屏幕字幕** —— "
               "固定在画面上不动、不随视角转（标题、结论、公式都用这个写法）；"
               "不写 `place`、或用 `at_point` 给坐标的则留在三维空间里，跟着场景一起转"
               "（给立体图形贴标签用这个写法）。")
    out.append("4. 立体图形本身（three_axes / surface / space_curve）不要写 `place`："
               "`to_edge` / `to_corner` 是「屏幕平面」里的概念，"
               "对一个会随视角转动的立体对象没有稳定含义。")
    out.append("")

    out.append("## 组合动画")
    out.append("- **parallel**：并行执行多个子动作，每个可有独立 run_time。")
    out.append('  ```{"do":"parallel","actions":[{"do":"create","target":"a","run_time":2},{"do":"write","target":"b","run_time":1}]}```')
    out.append("- **clear_all**：一键 FadeOut + 清除所有元素，等价于旧模板的分镜间清屏。")
    out.append("- **tangent_line / normal_line**：曲线在某点的切线/法线，x 可引用 tracker 实时跟踪。")
    out.append("")
    out.append("## 硬性规则")
    out.append("- formula/content 里的 LaTeX 用 `\\frac` 等命令；中文说明用 text 的 `$...$` 混排")
    out.append("- 元素必须先声明（在 elements 里）才能被引用；引用不存在的 id 会直接报错")
    # ⚠️ 颜色与函数是**封闭白名单**（校验层会硬拒白名单外的写法），所以必须**列全**。
    # 原先是 `BLUE/RED/.../PRIMARY` 这种带省略号的写法 —— 用省略号描述一个封闭集合，
    # 模型只能靠直觉补（写出 LIGHT_BLUE / DARK_RED 这类不存在的名字）然后白丢一次重试。
    # 同 §8.10 的教训：规格里"只列名字不给全"的地方，模型一定会猜错。
    out.append("- **颜色只能用下面这些**（本项目的主题色是 PRIMARY / SECONDARY / ACCENT），"
               "或 `#RRGGBB`：")
    out += ["  " + ln for ln in _wrap_names(COLORS)]
    out.append("- **表达式里只能用 x、数字、运算符和下面这些函数/常量**（以及已声明的 tracker 名）：")
    out += ["  " + ln for ln in _wrap_names(SAFE_NAMES)]
    return "\n".join(out)

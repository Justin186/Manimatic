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

import re

# 变量前缀：避免元素 id 撞上 Python 关键字或 manim 的全局名
_V = "_e_"

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


def formula(s, **kw):
    """
    公式渲染三级分流：
      1. 无 LaTeX 环境  → Text 兜底（保证不崩）
      2. 纯 LaTeX       → MathTex（快）
      3. 含中文         → Tex + ctex 模板（xelatex），公式里可直接嵌中文
    """
    if not USE_LATEX:
        return Text(s, font=CN_FONT, **kw)
    if not _has_cjk(s):
        return MathTex(s, **kw)
    try:
        return Tex(f"${s}$", tex_template=TexTemplateLibrary.ctex, **kw)
    except Exception:
        return Text(s, font=CN_FONT, **kw)


def _rich_line(s, font_size, **kw):
    """单行混排：用 $...$ 包 LaTeX，其余按中文渲染。"""
    parts = _re.split(r"\\$(.+?)\\$", s)
    if len(parts) == 1:
        return Text(s, font=CN_FONT, font_size=font_size, **kw)
    mobs = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 1:
            # LaTeX 字面比中文字面视觉高度小约 25%，不补偿会明显偏小
            mobs.append(formula(part, font_size=round(font_size * 1.3), **kw))
        else:
            mobs.append(Text(part, font=CN_FONT, font_size=font_size, **kw))
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


def _fit(m, max_w=12.4, max_h=6.6):
    """防溢出护栏：超宽/超高就等比缩小。不做这步，长文本会静默飘出画面。"""
    if m.width > max_w:
        m.scale_to_fit_width(max_w)
    if m.height > max_h:
        m.scale_to_fit_height(max_h)
    return m


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
        "desc": "表格（可放公式）",
        "params": {
            "rows": (T_TABLE, None, "二维数组，如 [[\"x\",\"0\",\"1\"],[\"y\",\"0\",\"1\"]]"),
            "font_size": (T_INT, 24, "字号"),
            "outer_lines": (T_BOOL, True, "是否画外框"),
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
    "number": {
        "desc": "动态数字标签（配合 tracker 实时显示斜率、面积等数值）",
        "params": {
            "value": (T_COORD, 0.0, "数值，可写 \"$t\" 或含 tracker 的表达式，如 \"2*t\""),
            "decimals": (T_INT, 2, "小数位数 0~6"),
            "unit": (T_STR, "", "单位后缀，如 \"\\,m^2\""),
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

ACTION_SPEC = {
    "create": {"desc": "Create：沿路径画出线条图形", "target": True, "run_time": 1.2},
    "write": {"desc": "Write：书写文字/公式", "target": True, "run_time": 1.0},
    "fade_in": {"desc": "FadeIn：淡入（可带 shift 方向）", "target": True, "run_time": 0.6},
    "grow": {"desc": "GrowFromCenter / GrowFromEdge：生长出现", "target": True, "run_time": 0.8},
    "draw_border": {"desc": "DrawBorderThenFill：先描边再填充", "target": True, "run_time": 1.2},
    "show": {"desc": "直接 add 上屏，无动画（动态元素用这个）", "target": True, "run_time": 0},
    "transform": {"desc": "Transform：把一个元素变形为另一个", "target": True, "run_time": 1.6},
    "replace": {"desc": "ReplacementTransform：替换式变形", "target": True, "run_time": 1.6},
    "indicate": {"desc": "Indicate：闪烁强调", "target": True, "run_time": 1.0},
    "circumscribe": {"desc": "Circumscribe：画圈圈强调", "target": True, "run_time": 1.0},
    "flash": {"desc": "Flash：闪光", "target": True, "run_time": 0.8},
    "wiggle": {"desc": "Wiggle：抖动", "target": True, "run_time": 1.0},
    "focus": {"desc": "FocusOn：聚焦光圈", "target": True, "run_time": 0.8},
    "fade_out": {"desc": "FadeOut：淡出", "target": True, "run_time": 0.5},
    "remove": {"desc": "直接移除，无动画", "target": True, "run_time": 0},
    "shift": {"desc": "平移", "target": True, "run_time": 0.8},
    "move_to": {"desc": "移动到指定点", "target": True, "run_time": 0.8},
    "scale": {"desc": "缩放", "target": True, "run_time": 0.7},
    "rotate": {"desc": "旋转（角度制）", "target": True, "run_time": 1.0},
    "set_color": {"desc": "改颜色", "target": True, "run_time": 0.5},
    "set_opacity": {"desc": "改不透明度", "target": True, "run_time": 0.5},
    "set_stroke": {"desc": "改描边（颜色/线宽）", "target": True, "run_time": 0.5},
    "stretch": {"desc": "沿某方向拉伸", "target": True, "run_time": 0.8},
    "move_along": {"desc": "沿某条曲线移动", "target": True, "run_time": 2.0},
    "trace": {"desc": "给动点加拖尾轨迹（TracedPath）", "target": True, "run_time": 0},
    "tracker_to": {"desc": "驱动追踪器变化（带它的元素会自动跟着动）", "target": True, "run_time": 3.0},
    "wait": {"desc": "停顿", "target": False, "run_time": 0.5},
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
    raise DSLError(
        f"{where}: 非法颜色 {v!r}。可用 Manim 常量（BLUE/RED/...）或 #RRGGBB"
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
    """点：坐标数组 / {ref,x,y} / {of,anchor}。"""
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
            return {"ref": rid,
                    "x": _as_coord(v["x"], f"{where}.x"),
                    "y": _as_coord(v["y"], f"{where}.y")}
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


# 每种元素的必填参数（不写就一定会生成出错误代码，必须在校验期拦掉）
REQUIRED = {
    "text": ["content"], "formula": ["content"],
    "plot": ["axes", "expr"], "parametric": ["axes", "fx", "fy"],
    "area": ["axes", "curve", "x_range"], "riemann": ["axes", "curve", "x_range"],
    "dot": ["at"], "line": ["start", "end"], "arrow": ["start", "end"],
    "polygon": ["points"], "angle": ["a", "vertex", "c"],
    "brace": ["of"], "table": ["rows"], "legend": ["items"],
    "highlight": ["of"], "group": ["items"],
}


def _norm_element(el, declared, trackers, where):
    """校验并规范化一个元素定义，返回新 dict。"""
    if not isinstance(el, dict):
        raise DSLError(f"{where}: 元素必须是对象")
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

    # live 是所有元素通用的开关：true 则强制用 always_redraw 包裹，
    # 用于"跟着别的动态元素一起动"（例如始终套在动点外面的强调框）。
    unknown = set(el.keys()) - set(spec) - {"id", "kind", "place", "live"}
    out["live"] = bool(el.get("live", False))
    for k, v in el.items():
        if k in ("id", "kind", "place", "live"):
            continue
        if k not in spec:
            continue
        t, default, _ = spec[k]
        out[k] = _norm_value(v, t, f"{where}.{k}", declared)

    for k in spec:
        if k not in out:
            out[k] = spec[k][1]
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

    if kind == "tracker":
        trackers.add(eid)
    if "place" in el:
        out["place"] = _norm_place(el["place"], f"{where}.place", declared)

    out["_dropped"] = sorted(unknown)
    declared.add(eid)
    return out


def _norm_value(v, t, where, declared):
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
        return str(v)[:400]
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
        if not isinstance(v, list) or not v:
            raise DSLError(f"{where}: rows 不能为空")
        if len(v) > 8:
            raise DSLError(f"{where}: 表格最多 8 行")
        rows = []
        for r in v:
            if not isinstance(r, list) or not r:
                raise DSLError(f"{where}: 每行必须是数组")
            if len(r) > 8:
                raise DSLError(f"{where}: 每行最多 8 列")
            rows.append([str(c)[:80] for c in r])
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
            out.append({
                "next_to": val["of"],
                "direction": str(val.get("direction", "down")).lower(),
                "buff": float(val.get("buff", 0.4)),
                "aligned": str(val.get("aligned", "")).lower() or None,
            })
        elif key == "at_point":
            out.append({"at_point": _check_point(val, f"{w}.at_point", declared)})
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


def _norm_action(ac, declared, trackers, where):
    if not isinstance(ac, dict):
        raise DSLError(f"{where}: 动作必须是对象")
    do = ac.get("do")
    if do not in ACTION_SPEC:
        raise DSLError(f"{where}: 未知动作 {do!r}。可用: {sorted(ACTION_SPEC)}")
    spec = ACTION_SPEC[do]
    out = {"do": do}

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
                if t not in declared:
                    raise DSLError(f"{where}: 引用了未声明的元素 {t!r}")
                out["target"].append(t)
        else:
            if not isinstance(tgt, str) or tgt not in declared:
                raise DSLError(f"{where}: 引用了未声明的元素 {tgt!r}")
            out["target"] = [tgt]

    # 各动作的附加参数
    if do in ("transform", "replace"):
        if "into" not in ac:
            raise DSLError(f"{where}: {do} 需要 into")
        into = ac["into"]
        if isinstance(into, dict):
            into = _norm_element(into, declared, trackers, f"{where}.into")
            out["inline_into"] = into
            out["into"] = into["id"]
        else:
            if into not in declared:
                raise DSLError(f"{where}: into 引用了未声明的元素 {into!r}")
            out["into"] = into

    if do == "wait":
        out["time"] = float(ac.get("time", out.get("run_time", 0.5)))
        if not (0.05 <= out["time"] <= 20):
            raise DSLError(f"{where}.time: {out['time']} 超出 [0.05, 20]")

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
        out["point"] = _check_point(ac["point"], f"{where}.point", declared)

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


def validate_scene(scene, idx):
    """
    校验一个 v2 分镜（elements + timeline）。返回规范化 dict。
    所有引用完整性问题在这里拦掉 —— 引用了不存在的元素就是静默失败的温床。
    """
    where_root = f"scene[{idx}]"
    if not isinstance(scene, dict):
        raise DSLError(f"{where_root}: 必须是对象")

    elements = scene.get("elements")
    if not isinstance(elements, list) or not elements:
        raise DSLError(f"{where_root}: 缺少 elements 数组或为空")
    if len(elements) > 40:
        raise DSLError(f"{where_root}: 元素过多（{len(elements)}），上限 40")

    declared, trackers = set(), set()
    norm_els = []
    for i, el in enumerate(elements):
        norm_els.append(_norm_element(el, declared, trackers, f"{where_root}.elements[{i}]"))

    timeline = scene.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        raise DSLError(f"{where_root}: 缺少 timeline 数组或为空")
    if len(timeline) > 80:
        raise DSLError(f"{where_root}: 时间线过长（{len(timeline)}），上限 80")

    norm_acts = []
    for i, ac in enumerate(timeline):
        norm_acts.append(_norm_action(ac, declared, trackers, f"{where_root}.timeline[{i}]"))

    # 表达式里出现的标识符：不是 x、不是数学函数，就必须是已声明的 tracker。
    # 不查这一遍，写错名字只会让 _safe_eval 抛异常并兜底成 0 —— 画面能出、内容全错，
    # 这正是本项目从头到尾最怕的静默失败。
    found = []
    for el in norm_els:
        for key in ("expr", "fx", "fy"):
            if isinstance(el.get(key), str):
                found.append((f"{el['id']}.{key}", el[key]))
        found += [(f"{el['id']}", e) for e in _scan_point_exprs(el)]
    for i, ac in enumerate(norm_acts):
        found += [(f"timeline[{i}].point", e) for e in _scan_point_exprs(ac)]

    for where, expr in found:
        for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr):
            if name != "x" and name not in SAFE_NAMES and name not in trackers:
                raise DSLError(
                    f"{where_root}.{where}: 表达式 {expr!r} 里的 {name!r} 未定义"
                    f"（只能是 x、数学函数，或某个 tracker 的 id）"
                )

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
                return (f"{_V}{pt['ref']}.c2p({self._coord(pt.get('x', 0))}, "
                        f"{self._coord(pt.get('y', 0))})")
            return f"{_V}{pt['of']}.{ANCHORS[pt.get('anchor', 'center')]}"
        return f"np.array([{self._coord(pt[0])}, {self._coord(pt[1])}, 0])"

    # ---------- 元素构建 ----------

    def build(self, el):
        eid = el["id"]
        if eid in self.built:
            return
        kind = el["kind"]
        if kind == "tracker":
            self.trackers.add(eid)

        # 依赖先建：axes 必须早于 plot
        for dep in ("axes", "curve", "of", "path"):
            if el.get(dep) in self.els:
                self.build(self.els[el[dep]])
        for it in el.get("items", []) or []:
            if isinstance(it, str) and it in self.els:
                self.build(self.els[it])

        expr = self._construct(el)
        var = f"{_V}{eid}"
        calls = self._place_calls(el.get("place"))

        # 含追踪器引用（或显式 live）→ 自动 always_redraw（机制 A）
        dynamic = ".get_value()" in expr or el.get("live")
        fit = kind in ("text", "formula", "legend", "table")

        if kind == "number" and dynamic:
            # DecimalNumber 不能走 always_redraw：它每帧要重建整个对象并重排文字
            # （内部走 Pango），实测是动态场景里最贵的一项。
            # 改成"建一次 + 只更新数值"，定位也就只需算一次。
            self.lines.append(f"        {var} = {expr}")
            self.lines.append(
                f"        {var}.add_updater(lambda m: m.set_value({self._coord(el['value'])}))"
            )
            for c in calls:
                self.lines.append(f"        {var}{c}")
        elif dynamic:
            # 定位必须写进 lambda 内部：always_redraw 每帧重建对象，
            # 写在 lambda 外面的定位只会生效一次，元素随后会弹回原点。
            inner = f"_fit({expr})" if fit else expr
            self.lines.append(
                f"        {var} = always_redraw(lambda: {inner}" + "".join(calls) + ")"
            )
        else:
            self.lines.append(f"        {var} = {expr}")
            for c in calls:
                self.lines.append(f"        {var}{c}")
            if fit:
                self.lines.append(f"        {var} = _fit({var})")

        # tracker 必须 add 进场景才会被动画更新（Manim 的硬性要求）
        if kind == "tracker":
            self.lines.append(f"        self.add({var})")

        self.built.add(eid)

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
                s = f".next_to({_V}{p['next_to']}, {d}, buff={_num(p['buff'])}"
                if p.get("aligned"):
                    s += f", aligned_edge={p['aligned'].upper()}"
                out.append(s + ")")
            elif "at_point" in p:
                out.append(f".move_to({self._point_code(p['at_point'])})")
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

    def _expr_call(self, expr, xvar="x"):
        """生成 _safe_eval 调用；表达式里出现的 tracker 名自动作为额外参数传入。"""
        names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr))
        extra = [f"{t}={_V}{t}.get_value()" for t in sorted(names & self.trackers)]
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
            return '{"color": GREY_B, "include_numbers": True, "label_constructor": Text}'
        return '{"color": GREY_B, "include_numbers": True}'

    def _mk_axes(self, el):
        tips = "True" if el.get("tips", True) else "False"
        return (f'Axes(x_range={self._rng(el["x_range"])}, y_range={self._rng(el["y_range"])}, '
                f'x_length={_num(el["x_length"])}, y_length={_num(el["y_length"])}, '
                f'axis_config={self._axes_config(el)}, tips={tips})')

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
        return (f'{_V}{el["axes"]}.plot(lambda x: {self._expr_call(el["expr"])}, '
                f'x_range={self._xr(el)}, color={_color(el["color"])}, '
                f'stroke_width={_num(el["stroke_width"])}).set_opacity({_num(el["opacity"])})')

    def _mk_parametric(self, el):
        tr = self._rng(el["t_range"])
        return (f'{_V}{el["axes"]}.plot_parametric_curve('
                f'lambda t: (np.array([{self._expr_call(el["fx"], "t")}, '
                f'{self._expr_call(el["fy"], "t")}, 0])), t_range={tr}, '
                f'color={_color(el["color"])}, stroke_width={_num(el["stroke_width"])})')

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
        return (f'MobjectTable([{rows}], '
                f'include_outer_lines={"True" if el["outer_lines"] else "False"})')

    def _mk_legend(self, el):
        items = ", ".join(
            f'[{_color(it[0])}, r"""{_esc(it[1])}"""]' for it in el["items"]
        )
        return f'_legend([{items}], font_size={el["font_size"]})'

    def _mk_highlight(self, el):
        return (f'SurroundingRectangle({_V}{el["of"]}, color={_color(el["color"])}, '
                f'buff={_num(el["buff"])}, corner_radius={_num(el["radius"])})')

    def _mk_number(self, el):
        unit = f', unit=r"""{_esc(el["unit"])}"""' if el.get("unit") else ""
        return (f'DecimalNumber({self._coord(el["value"])}, '
                f'num_decimal_places={el["decimals"]}{unit}, '
                f'font_size={el["font_size"]}, color={_color(el["color"])})')

    def _mk_tracker(self, el):
        return f'ValueTracker({_num(el["value"])})'

    def _mk_group(self, el):
        items = ", ".join(f"{_V}{i}" for i in el["items"] if isinstance(i, str))
        d = DIRECTIONS.get(str(el["direction"]).lower(), "DOWN")
        al = str(el.get("aligned", "left")).upper()
        return (f'VGroup({items}).arrange({d}, aligned_edge={al}, '
                f'buff={_num(el["buff"])})')

    # ---------- 时间线 ----------

    def act(self, ac):
        do = ac["do"]
        if do == "wait":
            self.lines.append(f"        self.wait({_num(ac['time'])})")
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
            return f"{_V}{i}"

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

        if do == "transform":
            a, b = ac["target"][0], ac["into"]
            self.lines.append(f"        self.play(Transform({v(a)}, {v(b)}){rt_s})")
            return
        if do == "replace":
            a, b = ac["target"][0], ac["into"]
            self.lines.append(f"        self.play(ReplacementTransform({v(a)}, {v(b)}){rt_s})")
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
        if do == "scale":
            return f"{var}.animate.scale({_num(ac['factor'])})"
        if do == "rotate":
            rad = round(float(ac["angle"]) * 3.141592653589793 / 180.0, 6)
            return f"{var}.animate.rotate({rad})"
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


def build_scene(scene: dict, env: dict) -> str:
    """把一个 v2 分镜（elements + timeline）编译成 construct() 体内的代码。"""
    b = _Builder(env)
    for el in scene["elements"]:
        b.els[el["id"]] = el

    for el in scene["elements"]:
        b.build(el)

    for ac in scene["timeline"]:
        b.lines.append(f"        # >> {ac['do']}")
        b.act(ac)

    # 软时长：timeline 跑完还没到 duration 就补 wait，保证节奏不赶
    if scene.get("duration"):
        est = 0.0
        for ac in scene["timeline"]:
            if ac["do"] == "wait":
                est += ac["time"]
            else:
                est += ac.get("run_time") or ACTION_SPEC[ac["do"]]["run_time"]
        pad = round(float(scene["duration"]) - est, 2)
        if pad > 0.1:
            b.lines.append(f"        self.wait({pad})")

    return "\n".join(b.lines)


# ==============================================================================
# 给 LLM 看的说明（可直接塞进 system prompt）
# ==============================================================================

def describe(include_place=True) -> str:
    """把元素库与动作表渲染成一份紧凑的规格说明 —— 喂给 LLM 用，永不手写第二份。"""
    out = ["# 分镜 DSL 规格（严格按此输出 JSON）", ""]
    out.append("## 场景结构")
    out.append("```")
    out.append('{"id":1, "elements":[...], "timeline":[...], "duration":6}')
    out.append("```")
    out.append("elements = 画面上有哪些东西；timeline = 它们按什么顺序出场/变化。")
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
               "`shift`([dx,dy]) `scale` `rotate`(度) `center` `fit_width` `z`")
    out.append("点坐标三种写法：`[x,y]` / `{\"ref\":\"ax\",\"x\":2,\"y\":3}`（坐标系里的点）"
               " / `{\"of\":\"g1\",\"anchor\":\"top\"}`（另一元素的锚点）")
    out.append("")

    out.append("## 动作（do）")
    for k, spec in ACTION_SPEC.items():
        need = "需要 target" if spec["target"] else "无需 target"
        out.append(f"- **{k}**（{need}，默认 run_time={spec['run_time']}）：{spec['desc']}")
    out.append("")
    out.append("target 可以是元素 id、id 数组（并行播放），或直接内联一个元素对象。")
    out.append("")

    out.append("## 动态效果（重点）")
    out.append("1. 声明 `{\"id\":\"t\",\"kind\":\"tracker\",\"value\":0}`")
    out.append("2. 任何数值位置写 `\"$t\"` 即引用其当前值，例如 `\"x_range\":[0,\"$t\"]`；")
    out.append("   也可以混写成表达式，如 `\"x\":\"$t - 1.1\"`、`\"y\":\"t**2 - 2*t\"`")
    out.append("3. 用 `{\"do\":\"tracker_to\",\"target\":\"t\",\"to\":6.28,\"run_time\":5,\"rate_func\":\"linear\"}` 驱动")
    out.append("4. 函数表达式里可直接写 tracker 名，如 `\"expr\":\"sin(x + t)\"`")
    out.append("5. 需要跟着动但自己不引用 tracker 的元素（例如始终套在动点外的框），加 `\"live\": true`")
    out.append("6. 实时显示数值用 `number` 元素，`\"value\":\"2*t\"` 会随 tracker 刷新")
    out.append("")
    out.append("含 tracker 引用（或 live）的元素会被渲染成 always_redraw 对象：")
    out.append("**它们不会自动上屏，要用 `{\"do\":\"show\",\"target\":\"...\"}` 显式 add。**")
    out.append("")
    out.append("## 硬性规则")
    out.append("- formula/content 里的 LaTeX 用 `\\frac` 等命令；中文说明用 text 的 `$...$` 混排")
    out.append("- 颜色只能用 Manim 常量（BLUE/RED/GREEN/YELLOW/TEAL/PURPLE/ORANGE/PINK/GOLD/"
               "WHITE/GREY/PRIMARY/ACCENT...）或 `#RRGGBB`")
    out.append("- 元素必须先声明（在 elements 里）才能被引用；引用不存在的 id 会直接报错")
    out.append("- 表达式只能用 x、数字、运算符和数学函数（sin/cos/exp/log/sqrt/abs/min/max...）")
    return "\n".join(out)

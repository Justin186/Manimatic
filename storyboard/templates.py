"""
Manim 代码模板库 —— 这是整个架构的关键层。

设计原则（对应方案中的三个坑）：

1. **模板是确定性代码，不经过大模型**
   → 语法错误为 0，ManimGL/CE 版本冲突为 0（避开了 145 个不兼容 API）。

2. **大模型只能填 params，不能改布局 / 坐标 / 动画顺序**
   → 消除「静默失败」：元素不会越界、不会重叠、动画顺序由数组下标决定。

3. **全部使用 Manim Community Edition API**
   → 用 Create 而不是 GL 的 ShowCreation，用 Axes.plot 而不是旧 API。

每个模板函数返回一段会被注入 Scene.construct() 的代码字符串。
"""


def esc_text(s):
    """
    清洗要嵌入生成代码 raw 三引号字符串的文本字段。

    - 反斜杠【保留】：\\frac 这类 LaTeX 命令靠它活着（本次修复的核心，
      之前 \\frac 嵌进普通字符串，\\f 被解析成 form feed，画面出现方块+rac）
    - 三连引号去掉：防止截断 raw 三引号字符串
    - 控制字符（\\x00-\\x1f）替换为空格：防止任何不可见字符进源码
    """
    s = str(s)
    s = s.replace('"""', '"')
    s = re.sub(r"[\x00-\x1f]", " ", s)
    return s

# 模板注册表：大模型只能从这个枚举里选，不能发明新模板
TEMPLATE_REGISTRY = {}

import re


def register(name):
    def deco(fn):
        TEMPLATE_REGISTRY[name] = fn
        return fn
    return deco


# --------------------------------------------------------------------------
# 工具：安全的数学表达式求值
# --------------------------------------------------------------------------

SAFE_EXPR_HELPER = '''
import numpy as np
import re as _re

_SAFE_NS = {
    "sin": np.sin, "cos": np.cos, "tan": np.tan,
    "exp": np.exp, "log": np.log, "sqrt": np.sqrt,
    "pi": np.pi, "e": np.e, "abs": abs,
}

def _eval_expr(expr, x):
    """在受限命名空间里求值数学表达式。"""
    return float(eval(expr, {"__builtins__": {}}, dict(_SAFE_NS, x=float(x))))


_CJK_RE = _re.compile(r"[\\u4e00-\\u9fff\\u3000-\\u303f\\uff00-\\uffef]")

def _has_cjk(s):
    """字符串里是否含中文/全角标点。"""
    return bool(_CJK_RE.search(str(s)))

'''


# --------------------------------------------------------------------------
# 模板 1：标题卡
# --------------------------------------------------------------------------

@register("title_card")
def title_card(params, env):
    text = esc_text(params.get("text", ""))
    subtitle = esc_text(params.get("subtitle", ""))
    duration = float(params.get("duration", 2.0))

    lines = []
    lines.append(f'        title = rich(r"""{text}""", font_size=44, color=PRIMARY)')
    lines.append(f'        title.to_edge(UP, buff=1.2)')
    lines.append(f'        self.play(Write(title), run_time=1.2)')
    if subtitle:
        lines.append(f'        sub = rich(r"""{subtitle}""", font_size=26, color=SECONDARY)')
        lines.append(f'        sub.next_to(title, DOWN, buff=0.5)')
        lines.append(f'        self.play(FadeIn(sub), run_time=0.8)')
    lines.append(f'        self.wait({max(duration - 2.0, 0.5)})')
    lines.append(f'        self.play(FadeOut(Group(*self.mobjects)), run_time=0.5)')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 模板 2：坐标系 + 函数图像
# --------------------------------------------------------------------------

@register("axes_plot")
def axes_plot(params, env):
    xr = params.get("x_range", [-5, 5, 1])
    yr = params.get("y_range", [-2, 10, 2])
    funcs = params.get("functions", [])
    caption = esc_text(params.get("caption", ""))
    duration = float(params.get("duration", 3.0))

    # 坐标轴刻度数字由 MathTex 渲染，没有 LaTeX 时必须关掉，否则整个渲染崩溃
    inc_numbers = "True" if env.get("use_latex", True) else "False"

    lines = []
    lines.append(f'        axes = Axes(')
    lines.append(f'            x_range={xr}, y_range={yr},')
    lines.append(f'            x_length=9, y_length=5.5,')
    lines.append(f'            axis_config={{"color": GREY_B, "include_numbers": {inc_numbers}}},')
    lines.append(f'        ).to_edge(DOWN, buff=0.8)')
    lines.append(f'        self.play(Create(axes), run_time=1.2)')

    for i, fn in enumerate(funcs):
        expr = str(fn.get("expr", "x"))
        color = str(fn.get("color", "BLUE")).upper()
        label = esc_text(fn.get("label", ""))
        vname = f"g{i}"
        lines.append(f'        {vname} = axes.plot(lambda x: _eval_expr("{expr}", x), color={color}, x_range=[{xr[0]}, {xr[1]}])')
        lines.append(f'        self.play(Create({vname}), run_time=1.5)')
        if label:
            lines.append(f'        lbl{i} = {env["formula"]}(r"""{label}""", font_size=28, color={color})')
            lines.append(f'        lbl{i}.to_corner(UR, buff=0.6).shift(DOWN * {i * 0.7})')
            lines.append(f'        self.play(FadeIn(lbl{i}), run_time=0.6)')

    if caption:
        lines.append(f'        cap = rich(r"""{caption}""", font_size=24, color=WHITE)')
        lines.append(f'        cap.to_edge(UP, buff=0.5)')
        lines.append(f'        self.play(Write(cap), run_time=0.8)')

    lines.append(f'        self.wait({max(duration - 3.0, 0.5)})')
    lines.append(f'        self.play(FadeOut(Group(*self.mobjects)), run_time=0.5)')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 模板 3：函数变换（核心演示模板）
# --------------------------------------------------------------------------

@register("function_transform")
def function_transform(params, env):
    xr = params.get("x_range", [-5, 5, 1])
    yr = params.get("y_range", [-2, 10, 2])
    from_expr = str(params.get("from_expr", "x"))
    to_expr = str(params.get("to_expr", "x"))
    from_label = esc_text(params.get("from_label", ""))
    to_label = esc_text(params.get("to_label", ""))
    caption = esc_text(params.get("caption", ""))
    duration = float(params.get("duration", 4.0))

    # 同上：没有 LaTeX 时关掉刻度数字
    inc_numbers = "True" if env.get("use_latex", True) else "False"

    lines = []
    lines.append(f'        axes = Axes(')
    lines.append(f'            x_range={xr}, y_range={yr},')
    lines.append(f'            x_length=9, y_length=5.5,')
    lines.append(f'            axis_config={{"color": GREY_B, "include_numbers": {inc_numbers}}},')
    lines.append(f'        ).to_edge(DOWN, buff=0.8)')
    lines.append(f'        self.play(Create(axes), run_time=1.0)')

    lines.append(f'        g0 = axes.plot(lambda x: _eval_expr("{from_expr}", x), color=BLUE, x_range=[{xr[0]}, {xr[1]}])')
    if from_label:
        lines.append(f'        l0 = {env["formula"]}(r"""{from_label}""", font_size=30, color=BLUE)')
        lines.append(f'        l0.to_corner(UR, buff=0.6)')
        lines.append(f'        self.play(Create(g0), Write(l0), run_time=1.5)')
    else:
        lines.append(f'        self.play(Create(g0), run_time=1.5)')
    lines.append(f'        self.wait(0.6)')

    lines.append(f'        g1 = axes.plot(lambda x: _eval_expr("{to_expr}", x), color=YELLOW, x_range=[{xr[0]}, {xr[1]}])')
    lines.append(f'        self.play(Transform(g0, g1), run_time=2.0)')
    if to_label:
        lines.append(f'        l1 = {env["formula"]}(r"""{to_label}""", font_size=30, color=YELLOW)')
        lines.append(f'        l1.to_corner(UR, buff=0.6)')
        lines.append(f'        self.play(Transform(l0, l1), run_time=1.0)')

    if caption:
        lines.append(f'        cap = rich(r"""{caption}""", font_size=24, color=WHITE)')
        lines.append(f'        cap.to_edge(UP, buff=0.5)')
        lines.append(f'        self.play(Write(cap), run_time=0.8)')

    lines.append(f'        self.wait({max(duration - 4.0, 0.5)})')
    lines.append(f'        self.play(FadeOut(Group(*self.mobjects)), run_time=0.5)')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 模板 4：总结卡
# --------------------------------------------------------------------------

@register("summary_card")
def summary_card(params, env):
    title = esc_text(params.get("title", "小结"))
    points = params.get("points", [])
    duration = float(params.get("duration", 3.0))

    lines = []
    lines.append(f'        head = rich(r"""{title}""", font_size=38, color=ACCENT)')
    lines.append(f'        head.to_edge(UP, buff=1.0)')
    lines.append(f'        self.play(Write(head), run_time=1.0)')

    lines.append(f'        items = VGroup()')
    for p in points:
        p = esc_text(p)
        lines.append(f'        items.add(rich(r"""{p}""", font_size=26, color=WHITE))')
    lines.append(f'        items.arrange(DOWN, aligned_edge=LEFT, buff=0.35)')
    lines.append(f'        items.next_to(head, DOWN, buff=0.6).to_edge(LEFT, buff=1.5)')
    lines.append(f'        for it in items:')
    lines.append(f'            self.play(FadeIn(it, shift=RIGHT * 0.3), run_time=0.6)')
    lines.append(f'        self.wait({max(duration - 2.0, 0.5)})')
    lines.append(f'        self.play(FadeOut(Group(*self.mobjects)), run_time=0.5)')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 模板 5b：旋转向量生成正弦波（观赏性最强的一个）
# --------------------------------------------------------------------------

def _hex_lerp(c1, c2, t):
    """两个 hex 颜色之间线性插值，返回 hex 字符串（纯 Python，不依赖 manim）。"""
    def rgb(c):
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    r1, g1, b1 = rgb(c1)
    r2, g2, b2 = rgb(c2)
    f = lambda a, b: int(round(a + (b - a) * max(0.0, min(1.0, t))))
    return "#%02X%02X%02X" % (f(r1, r2), f(g1, g2), f(b1, b2))


@register("vector_wave")
def vector_wave(params, env):
    """
    旋转向量 → 正弦波。

    左边一个匀速旋转的向量，右边同步"拉"出正弦曲线，
    中间用虚线连接圆周上的动点和曲线末端。

    为什么值得做：这是把「sin 是圆周运动的投影」讲清楚的唯一直观方式，
    静态图讲一百遍不如看它转一圈。观赏性也是全场最高的。
    """
    radius = float(params.get("radius", 1.3))
    duration = float(params.get("duration", 7.0))
    caption = esc_text(params.get("caption", ""))
    inc_numbers = "True" if env.get("use_latex", True) else "False"

    lines = []
    lines.append(f'        ax = Axes(')
    lines.append(f'            x_range=[0, 7.2, 1], y_range=[-1.6, 1.6, 1],')
    lines.append(f'            x_length=6.4, y_length=3.5,')
    lines.append(f'            axis_config={{"color": GREY_B, "include_numbers": {inc_numbers}}},')
    lines.append(f'        ).shift(RIGHT * 2.5)')
    lines.append(f'        circle = Circle(radius={radius}, color=BLUE, stroke_width=3).shift(LEFT * 4.0)')
    lines.append(f'        self.play(Create(circle), Create(ax), run_time=1.2)')

    lines.append(f'        tracker = ValueTracker(0.001)')
    lines.append(f'        def _pt(t):')
    lines.append(f'            return circle.get_center() + {radius} * np.array([np.cos(t), np.sin(t), 0])')
    # 三个 always_redraw 保证每帧跟随 tracker 重算
    lines.append(f'        dot = always_redraw(lambda: Dot(_pt(tracker.get_value()), color=YELLOW, radius=0.10))')
    lines.append(f'        rline = always_redraw(lambda: Line(circle.get_center(), _pt(tracker.get_value()), color=YELLOW, stroke_width=3))')
    lines.append(f'        hline = always_redraw(lambda: DashedLine(')
    lines.append(f'            _pt(tracker.get_value()),')
    lines.append(f'            ax.c2p(tracker.get_value(), np.sin(tracker.get_value())),')
    lines.append(f'            color=GREY, stroke_width=1.5))')
    lines.append(f'        curve = always_redraw(lambda: ax.plot(')
    lines.append(f'            lambda x: np.sin(x), x_range=[0.001, tracker.get_value()],')
    lines.append(f'            color=PRIMARY, stroke_width=3.5))')
    lines.append(f'        self.add(dot, rline, hline, curve)')

    if caption:
        lines.append(f'        cap = rich(r"""{caption}""", font_size=24, color=WHITE)')
        lines.append(f'        cap.to_edge(UP, buff=0.5)')
        lines.append(f'        self.play(Write(cap), run_time=0.8)')

    # rate_func=linear 很关键：匀速旋转才对应标准正弦波，用默认的平滑会失真
    lines.append(f'        self.play(tracker.animate.set_value(6.9), run_time={duration}, rate_func=linear)')
    lines.append(f'        self.wait(0.6)')
    lines.append(f'        self.play(FadeOut(Group(*self.mobjects)), run_time=0.5)')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 模板 5c：级数逐项逼近（傅里叶级数可视化）
# --------------------------------------------------------------------------

@register("series_approx")
def series_approx(params, env):
    """
    傅里叶级数逐项叠加逼近目标波形。

    每加一项，曲线就更接近方波一点点 —— 这是「量变引起质变」
    最直观的演示，也是这个 demo 里观赏性仅次于 vector_wave 的模板。
    """
    xr = params.get("x_range", [-4, 4, 1])
    yr = params.get("y_range", [-1.8, 1.8, 1])
    terms = [int(t) for t in params.get("terms", [1, 3, 5, 7, 9, 11])]
    series = str(params.get("series", "square")).lower()
    caption = esc_text(params.get("caption", ""))
    duration = float(params.get("duration", 8.0))
    inc_numbers = "True" if env.get("use_latex", True) else "False"

    if series == "sawtooth":
        target_fn = "2 * ((x / (2 * np.pi) + 0.5) % 1.0 - 0.5)"
        term = lambda k: f"2 / ({k} * np.pi) * (1 if {k} % 2 else -1) * np.sin({k} * x)"
    else:  # square
        target_fn = "np.sign(np.sin(x))"
        term = lambda k: f"4 / ({k} * np.pi) * np.sin({k} * x)"

    per = max(duration / max(len(terms), 1), 1.0)

    lines = []
    lines.append(f'        ax = Axes(')
    lines.append(f'            x_range={xr}, y_range={yr},')
    lines.append(f'            x_length=10, y_length=4.6,')
    lines.append(f'            axis_config={{"color": GREY_B, "include_numbers": {inc_numbers}}},')
    lines.append(f'        ).to_edge(DOWN, buff=1.0)')
    lines.append(f'        self.play(Create(ax), run_time=1.0)')

    # 目标波形：虚线 + 半透明，作为"要逼近的目标"
    lines.append(f'        target = ax.plot(lambda x: {target_fn}, x_range=[{xr[0]}, {xr[1]}],'
                 f' color=WHITE, stroke_width=2, stroke_opacity=0.40)')
    lines.append(f'        self.play(Create(target), run_time=1.0)')

    lines.append(f'        prev = None')
    for i, k in enumerate(terms):
        t = i / max(len(terms) - 1, 1)
        color = _hex_lerp("#58C4DD", "#FFFF00", t)   # 蓝 → 黄 渐变
        expr = " + ".join(term(kk) for kk in terms[:i + 1])
        lines.append(f'        # 第 {i + 1} 次逼近：叠加到第 {k} 次谐波')
        lines.append(f'        c{i} = ax.plot(lambda x: {expr}, x_range=[{xr[0]}, {xr[1]}],'
                     f' color="{color}", stroke_width={"3.2" if i == len(terms) - 1 else "2.2"})')
        if i > 0:
            # 上一条降级为半透明细线，避免画面糊成一团
            lines.append(f'        self.play(prev.animate.set_stroke(opacity=0.28, width=1.2), run_time=0.25)')
        lines.append(f'        self.play(Create(c{i}), run_time={min(per, 1.4)})')
        lines.append(f'        prev = c{i}')

    if caption:
        lines.append(f'        cap = rich(r"""{caption}""", font_size=24, color=WHITE)')
        lines.append(f'        cap.to_edge(UP, buff=0.5)')
        lines.append(f'        self.play(Write(cap), run_time=0.8)')

    lines.append(f'        self.wait(1.0)')
    lines.append(f'        self.play(FadeOut(Group(*self.mobjects)), run_time=0.5)')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 模板 5d：泰勒多项式逐步逼近（高数极限题的核心可视化）
# --------------------------------------------------------------------------

@register("taylor_approx")
def taylor_approx(params, env):
    """
    泰勒多项式逐项叠加，逼近目标函数。

    求极限时用泰勒展开，本质是"用多项式局部代替原函数"。
    这个模板把每一阶多项式画出来，直观展示逼近过程 ——
    比在黑板上写一堆展开式有说服力得多。
    """
    xr = params.get("x_range", [-1.2, 3.5, 1])
    yr = params.get("y_range", [-2.5, 2.5, 1])
    target = str(params.get("target_expr", "sin(x)"))
    terms = [str(t) for t in params.get("terms", ["x"])]
    caption = esc_text(params.get("caption", ""))
    duration = float(params.get("duration", 8.0))
    inc_numbers = "True" if env.get("use_latex", True) else "False"

    per = max(duration / max(len(terms), 1), 1.0)

    lines = []
    lines.append(f'        ax = Axes(')
    lines.append(f'            x_range={xr}, y_range={yr},')
    lines.append(f'            x_length=9.5, y_length=5.0,')
    lines.append(f'            axis_config={{"color": GREY_B, "include_numbers": {inc_numbers}}},')
    lines.append(f'        ).to_edge(DOWN, buff=0.9)')
    lines.append(f'        self.play(Create(ax), run_time=1.0)')

    # 目标函数：半透明白线，作为"要逼近的对象"
    lines.append(f'        tgt = ax.plot(lambda x: _eval_expr("{target}", x),'
                 f' x_range=[{xr[0]}, {xr[1]}], color=WHITE, stroke_width=2.5, stroke_opacity=0.55)')
    lines.append(f'        self.play(Create(tgt), run_time=1.1)')

    lines.append(f'        prev = None')
    lines.append(f'        err = None')
    acc = ""
    for i, t in enumerate(terms):
        acc = f"{acc} + ({t})" if acc else f"({t})"
        col = _hex_lerp("#58C4DD", "#FFFF00", i / max(len(terms) - 1, 1))
        lines.append(f'        # 第 {i + 1} 阶：累加到 {t}')
        lines.append(f'        p{i} = ax.plot(lambda x: _eval_expr("{acc}", x),'
                     f' x_range=[{xr[0]}, {xr[1]}], color="{col}",'
                     f' stroke_width={"3.4" if i == len(terms) - 1 else "2.2"})')
        if i > 0:
            lines.append(f'        self.play(prev.animate.set_stroke(opacity=0.30, width=1.2), run_time=0.25)')
        lines.append(f'        self.play(Create(p{i}), run_time={min(per, 1.5)})')
        lines.append(f'        prev = p{i}')
        # 误差区域：目标函数与当前多项式之间的红色填充，随阶数升高而缩小
        # ——"逼近"二字的视觉化。同样是 Manim 现成 API（get_area）。
        lines.append(f'        _err{i} = ax.get_area(tgt, x_range=[{xr[0]}, {xr[1]}],'
                     f' bounded_graph=p{i}, color=RED, opacity=0.22, stroke_width=0)')
        lines.append(f'        if err is None:')
        lines.append(f'            self.play(FadeIn(_err{i}), run_time=0.4)')
        lines.append(f'        else:')
        lines.append(f'            self.play(Transform(err, _err{i}), run_time=0.4)')
        lines.append(f'        err = _err{i}')

    if caption:
        lines.append(f'        cap = rich(r"""{caption}""", font_size=23, color=WHITE)')
        lines.append(f'        cap.to_edge(UP, buff=0.45)')
        lines.append(f'        self.play(Write(cap), run_time=0.8)')

    # 图例：右上角，"目标函数 + 各阶泰勒多项式"
    lines.append(f'        legend_items = VGroup()')
    lines.append(f'        _l0 = Line(LEFT * 0.18, RIGHT * 0.18, color=WHITE, stroke_width=2.5).set_opacity(0.55)')
    lines.append(f'        _l0_label = Text(r"""f(x) = {target}""", font_size=16, font=CN_FONT, color=WHITE).next_to(_l0, RIGHT, buff=0.12)')
    lines.append(f'        legend_items.add(VGroup(_l0, _l0_label))')
    for i, t in enumerate(terms):
        col = _hex_lerp("#58C4DD", "#FFFF00", i / max(len(terms) - 1, 1))
        _seg_lbl_str = "T_{" + str(i + 1) + "}=" + t
        lines.append(f'        _seg = Line(LEFT * 0.18, RIGHT * 0.18, color="{col}", stroke_width=2.2)')
        lines.append(f'        _seg_lbl = Text(r"""{_seg_lbl_str}""", font_size=16, font=CN_FONT, color=WHITE).next_to(_seg, RIGHT, buff=0.12)')
        lines.append(f'        legend_items.add(VGroup(_seg, _seg_lbl))')
    lines.append(f'        legend_items.arrange(DOWN, aligned_edge=LEFT, buff=0.14).to_corner(UR, buff=0.45).scale(0.95)')
    lines.append(f'        self.play(FadeIn(legend_items), run_time=0.5)')

    lines.append(f'        self.wait(0.8)')
    lines.append(f'        self.play(FadeOut(Group(*self.mobjects)), run_time=0.5)')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 模板 5e：曲边梯形面积（定积分的几何意义）
# --------------------------------------------------------------------------

@register("area_under_curve")
def area_under_curve(params, env):
    """
    定积分的几何意义：曲线下的面积。

    画被积函数 → 填充 [a, b] 区间下的面积 → 标出积分上下限。
    把"算一个数"变成"求一块面积"，是讲定积分最直观的方式。
    """
    xr = params.get("x_range", [-0.5, 2.2, 1])
    yr = params.get("y_range", [-0.4, 1.4, 1])
    expr = str(params.get("expr", "sin(x)"))
    a = float(params.get("a", 0.0))
    b = float(params.get("b", 1.0))
    caption = esc_text(params.get("caption", ""))
    duration = float(params.get("duration", 6.0))
    inc_numbers = "True" if env.get("use_latex", True) else "False"

    lines = []
    lines.append(f'        ax = Axes(')
    lines.append(f'            x_range={xr}, y_range={yr},')
    lines.append(f'            x_length=9.5, y_length=4.6,')
    lines.append(f'            axis_config={{"color": GREY_B, "include_numbers": {inc_numbers}}},')
    lines.append(f'        ).to_edge(DOWN, buff=1.0)')
    lines.append(f'        curve = ax.plot(lambda x: _eval_expr("{expr}", x),'
                 f' x_range=[{xr[0]}, {xr[1]}], color=PRIMARY, stroke_width=3.5)')
    lines.append(f'        self.play(Create(ax), run_time=0.9)')
    lines.append(f'        self.play(Create(curve), run_time={min(duration * 0.3, 1.8)})')

    # 上下限处的竖直虚线（端点高度在运行时算，避免模板里硬编码数值）
    lines.append(f'        _ya = _eval_expr("{expr}", {a})')
    lines.append(f'        _yb = _eval_expr("{expr}", {b})')
    lines.append(f'        self.play(Create(DashedLine(ax.c2p({a}, 0), ax.c2p({a}, _ya),'
                 f' color=GREY_B, stroke_width=2)),')
    lines.append(f'                      Create(DashedLine(ax.c2p({b}, 0), ax.c2p({b}, _yb),'
                 f' color=GREY_B, stroke_width=2)), run_time=0.6)')

    # 面积填充：GrowFromEdge 让它从下限"长"到上限，比直接 FadeIn 有过程感
    lines.append(f'        area = ax.get_area(curve, x_range=[{a}, {b}],'
                 f' color=ACCENT, opacity=0.42)')
    lines.append(f'        self.play(GrowFromEdge(area, LEFT), run_time={min(duration * 0.45, 2.0)})')

    # 黎曼矩形逼近：n=8 → n=24 → n=60，矩形逐渐贴合曲线。
    # 这是"积分 = 无限分割求和"最直观的动态解释，比静态填充有说服力得多。
    # get_riemann_rectangles 是 Manim 现成 API，确定性代码，零风险。
    lines.append(f'        riemanns = VGroup()')
    for n in (8, 24, 60):
        lines.append(f'        riemanns.add(ax.get_riemann_rectangles('
                     f' curve, x_range=[{a}, {b}], dx=({b} - {a}) / {n},'
                     f' color=PRIMARY, fill_opacity=0.55, stroke_width=0.5,'
                     f' input_sample_type="left"))')
    lines.append(f'        self.play(Transform(area, riemanns[0]), run_time=1.0)')
    lines.append(f'        self.play(Transform(area, riemanns[1]), run_time=1.0)')
    lines.append(f'        self.play(Transform(area, riemanns[2]), run_time=1.1)')
    lines.append(f'        self.play(area.animate.set_opacity(0.42), run_time=0.5)')

    if caption:
        lines.append(f'        cap = rich(r"""{caption}""", font_size=23, color=WHITE)')
        lines.append(f'        cap.to_edge(UP, buff=0.45)')
        lines.append(f'        self.play(Write(cap), run_time=0.8)')

    lines.append(f'        self.wait({max(duration * 0.25, 0.8)})')
    lines.append(f'        self.play(FadeOut(Group(*self.mobjects)), run_time=0.5)')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 模板 5：解题步骤卡（对应「步骤分析」环节的输出）
# --------------------------------------------------------------------------

@register("step_card")
def step_card(params, env):
    """
    解题步骤卡 —— 累积式板书（默认）。

    设计要点：
    1. 历史步骤**保留在屏幕上**（不像翻页那样清掉），因为解题推导需要上下文 ——
       学生要看到"这一步是从哪一步来的"。
    2. 历史步骤自动缩小 + 降低不透明度，把视觉焦点让给当前步。
       不这么做的话 5 步之后会挤爆画面，这也是"静默失败"的一种。
    3. 每次追加后重新锚定到左上角，保证整体不会越界。

    mode:
        "stack" 累积板书式（默认）
        "page"  翻页式（每步清掉上一步，信息密度低，一般不推荐）
    """
    steps = params.get("steps", [])
    duration = float(params.get("duration", 3.0))
    mode = str(params.get("mode", "stack")).lower()
    if mode not in ("stack", "page"):
        mode = "stack"
    per = max(duration / max(len(steps), 1), 1.2)

    lines = []
    if mode == "page":
        # 翻页模式：保留旧行为，每步清掉上一步
        lines.append("        prev_grp = None")
        for i, st in enumerate(steps):
            text = esc_text(st.get("text", ""))
            formula = esc_text(st.get("formula_latex", ""))
            lines.append(f'        grp = VGroup()')
            lines.append(f'        t = rich(r"""第 {i + 1} 步  {text}""", font_size=26, color=WHITE)')
            lines.append(f'        t.to_edge(UP, buff=1.0).to_edge(LEFT, buff=1.0)')
            lines.append(f'        grp.add(t)')
            if formula:
                lines.append(f'        f = {env["formula"]}(r"""{formula}""", font_size=36, color=ACCENT)')
                lines.append(f'        f.next_to(t, DOWN, buff=0.6).to_edge(LEFT, buff=1.5)')
                lines.append(f'        grp.add(f)')
            lines.append(f'        if prev_grp is not None:')
            lines.append(f'            self.play(FadeOut(prev_grp), run_time=0.35)')
            lines.append(f'            self.remove(prev_grp)')
            lines.append(f'        self.play(Write(grp), run_time=1.0)')
            lines.append(f'        self.wait({max(per - 1.0, 0.3)})')
            lines.append(f'        prev_grp = grp')
        return "\n".join(lines)

    # ---- 累积板书模式（默认）----
    #
    # 关键设计：**布局在生成代码时就算好，不靠运行时动画去调整**。
    #
    # 之前那版是"每加一步就把历史步骤动画缩小淡化再重排"——有两个问题：
    #   1. 动画拖慢节奏，观众在等位移而不是在读内容；
    #   2. 靠动画兜底本身就说明布局没算准，步骤一多仍可能溢出画面。
    # 现在改成：按槽位高度预计算每一行的 y 坐标，超出可用高度就整体等比缩小，
    # 位置写死 → 结构性保证不重叠，也不需要任何"调整"动画。
    FRAME_TOP = 3.25
    FRAME_BOTTOM = -3.25
    AVAIL = FRAME_TOP - FRAME_BOTTOM

    def slot_h(st):
        """每步占据的垂直槽位高度（Manim 单位），有公式的更高。"""
        # 经验值：26pt 中文 + 32pt 公式 + 0.30 间隙 ≈ 1.50，留 0.05 余量
        return 1.55 if st.get("formula_latex") else 0.85

    total = sum(slot_h(s) for s in steps) or 1.0
    scale = 1.0 if total <= AVAIL else AVAIL / total

    fs_t = max(17, round(26 * scale))
    fs_f = max(19, round(32 * scale))
    gap = round(0.26 * scale, 3)

    n = len(steps)
    # 节奏：把总时长的大部分留给 Write，每步之后只短暂停留，不拖沓
    write_t = round(min(1.0, 0.85 * duration / max(n, 1)), 2)
    write_t = max(write_t, 0.45)
    wait_t = round(max((duration - n * write_t) / max(n, 1), 0.25), 2)

    lines = ["        _items = VGroup()"]
    y_cursor = FRAME_TOP
    for i, st in enumerate(steps):
        text = esc_text(st.get("text", ""))
        formula = esc_text(st.get("formula_latex", ""))
        g = f"_grp{i}"

        h = slot_h(st) * scale
        cy = y_cursor - h / 2.0      # 该槽位的中心 y
        y_cursor -= h                # 光标下移，为下一步留位 → 天然不重叠

        lines.append(f'        _t{i} = rich(r"""第 {i + 1} 步  {text}""", font_size={fs_t}, color=WHITE)')
        lines.append(f'        {g} = VGroup(_t{i})')
        if formula:
            lines.append(f'        _f{i} = {env["formula"]}(r"""{formula}""", font_size={fs_f}, color=ACCENT)')
            lines.append(f'        _f{i}.next_to(_t{i}, DOWN, buff={gap}, aligned_edge=LEFT)')
            lines.append(f'        {g}.add(_f{i})')
        if scale < 0.999:
            lines.append(f'        {g}.scale({round(scale, 4)})')
        # 位置写死：水平锚左，垂直落在预先算好的槽位中心
        lines.append(f'        {g}.to_edge(LEFT, buff=0.9).shift(UP * {round(cy, 3)})')
        lines.append(f'        self.play(Write({g}), run_time={write_t})')
        lines.append(f'        _items.add({g})')
        lines.append(f'        self.wait({wait_t})')

    return "\n".join(lines)

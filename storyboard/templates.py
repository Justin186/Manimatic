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

# 模板注册表：大模型只能从这个枚举里选，不能发明新模板
TEMPLATE_REGISTRY = {}


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
    text = str(params.get("text", "")).replace('"', '\\"')
    subtitle = str(params.get("subtitle", "")).replace('"', '\\"')
    duration = float(params.get("duration", 2.0))

    lines = []
    lines.append(f'        title = Text("{text}", font_size=44, font=CN_FONT, color=PRIMARY)')
    lines.append(f'        title.to_edge(UP, buff=1.2)')
    lines.append(f'        self.play(Write(title), run_time=1.2)')
    if subtitle:
        lines.append(f'        sub = Text("{subtitle}", font_size=26, font=CN_FONT, color=SECONDARY)')
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
    caption = str(params.get("caption", "")).replace('"', '\\"')
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
        expr = str(fn.get("expr", "x")).replace('"', '\\"')
        color = str(fn.get("color", "BLUE")).upper()
        label = str(fn.get("label", "")).replace('"', '\\"')
        vname = f"g{i}"
        lines.append(f'        {vname} = axes.plot(lambda x: _eval_expr("{expr}", x), color={color}, x_range=[{xr[0]}, {xr[1]}])')
        lines.append(f'        self.play(Create({vname}), run_time=1.5)')
        if label:
            lines.append(f'        lbl{i} = {env["formula"]}(r"{label}", font_size=28, color={color})')
            lines.append(f'        lbl{i}.to_corner(UR, buff=0.6).shift(DOWN * {i * 0.7})')
            lines.append(f'        self.play(FadeIn(lbl{i}), run_time=0.6)')

    if caption:
        lines.append(f'        cap = Text("{caption}", font_size=24, font=CN_FONT, color=WHITE)')
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
    from_expr = str(params.get("from_expr", "x")).replace('"', '\\"')
    to_expr = str(params.get("to_expr", "x")).replace('"', '\\"')
    from_label = str(params.get("from_label", "")).replace('"', '\\"')
    to_label = str(params.get("to_label", "")).replace('"', '\\"')
    caption = str(params.get("caption", "")).replace('"', '\\"')
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
        lines.append(f'        l0 = {env["formula"]}(r"{from_label}", font_size=30, color=BLUE)')
        lines.append(f'        l0.to_corner(UR, buff=0.6)')
        lines.append(f'        self.play(Create(g0), Write(l0), run_time=1.5)')
    else:
        lines.append(f'        self.play(Create(g0), run_time=1.5)')
    lines.append(f'        self.wait(0.6)')

    lines.append(f'        g1 = axes.plot(lambda x: _eval_expr("{to_expr}", x), color=YELLOW, x_range=[{xr[0]}, {xr[1]}])')
    lines.append(f'        self.play(Transform(g0, g1), run_time=2.0)')
    if to_label:
        lines.append(f'        l1 = {env["formula"]}(r"{to_label}", font_size=30, color=YELLOW)')
        lines.append(f'        l1.to_corner(UR, buff=0.6)')
        lines.append(f'        self.play(Transform(l0, l1), run_time=1.0)')

    if caption:
        lines.append(f'        cap = Text("{caption}", font_size=24, font=CN_FONT, color=WHITE)')
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
    title = str(params.get("title", "小结")).replace('"', '\\"')
    points = params.get("points", [])
    duration = float(params.get("duration", 3.0))

    lines = []
    lines.append(f'        head = Text("{title}", font_size=38, font=CN_FONT, color=ACCENT)')
    lines.append(f'        head.to_edge(UP, buff=1.0)')
    lines.append(f'        self.play(Write(head), run_time=1.0)')

    lines.append(f'        items = VGroup()')
    for p in points:
        p = str(p).replace('"', '\\"')
        lines.append(f'        items.add(Text("{p}", font_size=26, font=CN_FONT, color=WHITE))')
    lines.append(f'        items.arrange(DOWN, aligned_edge=LEFT, buff=0.35)')
    lines.append(f'        items.next_to(head, DOWN, buff=0.6).to_edge(LEFT, buff=1.5)')
    lines.append(f'        for it in items:')
    lines.append(f'            self.play(FadeIn(it, shift=RIGHT * 0.3), run_time=0.6)')
    lines.append(f'        self.wait({max(duration - 2.0, 0.5)})')
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
            text = str(st.get("text", "")).replace('"', '\\"')
            formula = str(st.get("formula_latex", "")).replace('"', '\\"')
            lines.append(f'        grp = VGroup()')
            lines.append(f'        t = Text("第 {i + 1} 步  {text}", font_size=26, font=CN_FONT, color=WHITE)')
            lines.append(f'        t.to_edge(UP, buff=1.0).to_edge(LEFT, buff=1.0)')
            lines.append(f'        grp.add(t)')
            if formula:
                lines.append(f'        f = {env["formula"]}(r"{formula}", font_size=36, color=ACCENT)')
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
    lines.append("        _items = VGroup()")
    for i, st in enumerate(steps):
        text = str(st.get("text", "")).replace('"', '\\"')
        formula = str(st.get("formula_latex", "")).replace('"', '\\"')
        g = f"_grp{i}"

        # 1) 构造当前步（还没写进画面）
        lines.append(f'        _t{i} = Text("第 {i + 1} 步  {text}", font_size=26, font=CN_FONT, color=WHITE)')
        lines.append(f'        {g} = VGroup(_t{i})')
        if formula:
            lines.append(f'        _f{i} = {env["formula"]}(r"{formula}", font_size=32, color=ACCENT)')
            lines.append(f'        _f{i}.next_to(_t{i}, DOWN, buff=0.28, aligned_edge=LEFT)')
            lines.append(f'        {g}.add(_f{i})')

        # 2) 定位：第一步锚左上角，后续接在上一步下方
        if i == 0:
            lines.append(f'        {g}.to_edge(UP, buff=0.85).to_edge(LEFT, buff=0.9)')
        else:
            lines.append(f'        {g}.next_to(_items[-1], DOWN, buff=0.30, aligned_edge=LEFT)')

        # 3) 关键：把历史步骤降级（缩小 + 变暗），再整体重新锚定，给新步腾出空间
        if i > 0:
            lines.append(f'        self.play(*[_m.animate.scale(0.80).set_opacity(0.42) for _m in _items], run_time=0.40)')
            lines.append(f'        self.play(_items.animate.arrange(DOWN, aligned_edge=LEFT, buff=0.20)'
                         f'.to_edge(UP, buff=0.85).to_edge(LEFT, buff=0.9), run_time=0.35)')
            lines.append(f'        {g}.next_to(_items[-1], DOWN, buff=0.30, aligned_edge=LEFT)')

        # 4) 写出当前步
        lines.append(f'        self.play(Write({g}), run_time=1.0)')
        lines.append(f'        _items.add({g})')
        lines.append(f'        self.wait({max(per - 1.0, 0.3)})')

    return "\n".join(lines)

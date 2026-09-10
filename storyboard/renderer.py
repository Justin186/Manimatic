"""
渲染器：分镜 JSON → 可执行的 Manim 源码（确定性翻译）。

两条路径，共用同一个 header 与同一套运行时 helper：

    v1 模板路径  templates.py 里的固定模板  —— 兼容旧分镜
    v2 DSL 路径  dsl.py 的元素+时间线       —— 推荐，组合空间无限

无论哪条路径，**代码都由 Python 生成，大模型碰不到一个字符**。
这是 PROJECT_KNOWLEDGE 决策 1 的底线，本次重构没有动它 ——
变的只是"大模型能表达多少种组合"，不是"大模型能不能写代码"。
"""

import os
from . import templates
from . import dsl
from . import latex_env

HEADER = '''# -*- coding: utf-8 -*-
"""
自动生成的 Manim 场景 —— 请勿手工编辑。
来源分镜：{title}
由渲染器确定性生成：语法由代码保证，不经过大模型。
"""
from manim import *

# ---- 全局样式常量（所有场景共享，保证视觉一致）----
BG        = "#1C1C1C"
PRIMARY   = "#58C4DD"
SECONDARY = "#83C167"
ACCENT    = "#FFFF00"
CN_FONT   = "{cn_font}"

USE_LATEX = {use_latex}

# ---- 运行时 helper：安全求值、公式分流、防溢出、图例 ----
{helper}


class StoryboardScene(Scene):
    def construct(self):
        self.camera.background_color = BG

{body}

        self.wait(0.3)
'''


def _prepare_env(use_latex: bool, fast: bool = False) -> dict:
    """探测 LaTeX 并组装渲染环境。找不到就降级，绝不让渲染直接崩掉。"""
    if use_latex:
        info = latex_env.detect()
        if not info["available"]:
            print(f"      [WARN] LaTeX 不可用（{info['reason']}），公式降级为纯文本")
            use_latex = False
        elif info["injected"]:
            print(f"      [PATH+] 已注入 TeX 目录: {info['injected'][0]}")
    return {"formula": "formula", "use_latex": use_latex, "fast": fast}


def _scene_code(sc, env):
    """单个分镜 → (代码, 标签)。DSL 与旧模板两种写法都走这里。"""
    if sc.get("mode") == "dsl":
        code = dsl.build_scene(sc, env)
        label = (f"scene {sc['id']} | dsl  "
                 f"{len(sc['elements'])} 元素 / {len(sc['timeline'])} 动作")
    else:
        tpl_name = sc["template"]
        code = templates.TEMPLATE_REGISTRY[tpl_name](sc["params"], env)
        label = f"scene {sc['id']} | template: {tpl_name}"
    return code, label


# 分镜边界的统一清屏片段。
#
# 为什么放在这里而不是让每个模板自己清：
#   清屏是"分镜之间"的规则，不是"某个模板"的规则。
#   之前靠模板自觉（末尾 FadeOut），但 step_card 的累积板书
#   需要保留到本分镜结束，于是下一个分镜就直接叠了上去 ——
#   画面能渲染、代码不报错，内容却是错的（静默失败）。
_CLEAR = (
    "        # ---- 清屏：移除上一个分镜的残留 ----\n"
    "        if self.mobjects:\n"
    "            self.play(FadeOut(Group(*self.mobjects)), run_time=0.35)\n"
    "            self.clear()\n"
)


def render(storyboard: dict,
           use_latex: bool = True,
           cn_font: str = "STZhongsong",
           fast: bool = False) -> str:
    """
    把校验过的分镜 JSON 渲染成 Manim Python 源码（所有分镜拼成一个 Scene）。

    Args:
        storyboard: 已经过 schema.validate() 的分镜 dict
        use_latex: 是否用 LaTeX 渲染公式（没装 LaTeX 时传 False）
        cn_font: 中文字体

    Returns:
        Manim 场景源码字符串
    """
    env = _prepare_env(use_latex, fast)

    blocks = []
    for idx, sc in enumerate(storyboard["scenes"]):
        code, label = _scene_code(sc, env)
        head = "" if idx == 0 else _CLEAR
        blocks.append(head + f"        # ---- {label} ----\n" + code + "\n")

    src = HEADER.format(
        title=storyboard.get("title", "untitled"),
        use_latex=env["use_latex"],
        cn_font=cn_font,
        helper=dsl.RUNTIME_HELPER,
        body="\n".join(blocks),
    )

    _check_syntax(src)
    return src


def _check_syntax(src: str) -> None:
    """
    闸 4：生成结果必须先过一遍 Python 编译。

    模板是确定性代码，理论上不会出语法错误；但 DSL 路径是"自由组合"出来的，
    任何一条 f-string 拼接都可能拼出坏字符。与其让 manim 报一堆看不懂的错，
    不如在这里用一行 compile() 把"语法错误为 0"变成硬保证。
    """
    try:
        compile(src, "<storyboard_generated>", "exec")
    except SyntaxError as e:
        raise SyntaxError(
            f"生成的 Manim 代码有语法错误（这是渲染器的 bug，不是分镜的问题）：\n"
            f"  行 {e.lineno}: {e.msg}\n  {e.text}"
        ) from e


def render_split(storyboard: dict,
                 use_latex: bool = True,
                 cn_font: str = "STZhongsong",
                 fast: bool = False) -> list:
    """
    每个分镜生成一份**独立可渲染**的源码 —— 并行渲染用。

    为什么值得做：实测串行渲染 4 个分镜 48.1s，4 进程并行只要 11.4s（20 核）。
    本机瓶颈是 Python 侧的 mobject 计算而不是光栅化（像素量涨 4.3 倍只慢 12%），
    所以多核并行才是真正有效的加速手段，换 GPU 最多省 12%。

    附带好处：某个分镜失败时只重跑那一个，正合 PROJECT_KNOWLEDGE 决策 3
    「每个分镜可独立重试」。

    Returns:
        [(scene_id, src), ...]，顺序与输入分镜一致
    """
    env = _prepare_env(use_latex, fast)
    out = []
    for idx, sc in enumerate(storyboard["scenes"]):
        code, label = _scene_code(sc, env)
        # 拆分渲染后靠 ffmpeg 拼接，每个分镜末尾必须淡出，
        # 否则片段之间会硬切（单场景模式下这一步由下一个分镜的 _CLEAR 完成）
        code += (
            "\n        if self.mobjects:\n"
            "            self.play(FadeOut(Group(*self.mobjects)), run_time=0.35)\n"
        )
        src = HEADER.format(
            title=storyboard.get("title", "untitled"),
            use_latex=env["use_latex"],
            cn_font=cn_font,
            helper=dsl.RUNTIME_HELPER,
            body=f"        # ---- {label} ----\n" + code,
        )
        _check_syntax(src)
        out.append((sc.get("id", idx + 1), src))
    return out


def render_to_file(storyboard: dict, out_path: str, use_latex: bool = True,
                   cn_font: str = "STZhongsong", fast: bool = False) -> str:
    """渲染并写入 .py 文件，返回文件路径。"""
    src = render(storyboard, use_latex=use_latex, cn_font=cn_font, fast=fast)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(src)
    return out_path

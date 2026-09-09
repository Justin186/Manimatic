"""
模板渲染器：分镜 JSON → 可执行的 Manim 代码（确定性翻译）。

这是「方案 B」的核心：大模型不碰代码，只产出受约束的 JSON，
由这里的代码把它翻译成 Manim 场景。
因此语法错误为 0、版本冲突为 0、布局由模板保证。
"""

import os
from . import templates
from . import latex_env

HEADER = '''# -*- coding: utf-8 -*-
"""
自动生成的 Manim 场景 —— 请勿手工编辑。
来源分镜：{title}
模板渲染器生成，语法由模板保证。
"""
from manim import *

# ---- 全局样式常量（所有场景共享，保证视觉一致）----
BG        = "#1C1C1C"
PRIMARY   = "#58C4DD"
SECONDARY = "#83C167"
ACCENT    = "#FFFF00"
CN_FONT   = "{cn_font}"

USE_LATEX = {use_latex}

{template_helper}

def formula(s, **kw):
    """
    公式渲染：三级分流，避免渲染崩溃并最大化表现力。

    1. 纯 LaTeX（无中文）→ MathTex：快（dvi 路线），适合绝大多数公式
    2. 含中文           → Tex + ctex 模板：走 xelatex，公式里可以直接嵌中文
                          （如 \\text{{顶点}}(2,0) 不会再显示成原始命令）
    3. 无 LaTeX 环境     → Text 兜底：保证不崩，公式变纯文本

    ctex 模板来自 Manim 官方 TexTemplateLibrary（见官方文档 using_text.md）。
    首次编译会触发 MiKTeX 自动安装宏包，可能偏慢（数十秒），之后走缓存。
    """
    if not USE_LATEX:
        return Text(s, font=CN_FONT, **kw)
    if not _has_cjk(s):
        return MathTex(s, **kw)
    try:
        return Tex(f"${{s}}$", tex_template=TexTemplateLibrary.ctex, **kw)
    except Exception:
        # ctex 宏包缺失等编译失败时退回纯文本 —— 响亮失败不如降级可用
        return Text(s, font=CN_FONT, **kw)


def formula_parts(text_parts, **kw):
    """
    中英混排公式：把 ["由", "a^2+b^2", "得", "c=5"] 这样的片段
    拼成一个横向 VGroup，中文走 Text、公式走 MathTex。

    需要真·中英混排时用这个，比 formula() 效果好。
    """
    mobs = []
    for part in text_parts:
        mobs.append(Text(part, font=CN_FONT, **kw) if _has_cjk(part)
                    else (MathTex(part, **kw) if USE_LATEX
                          else Text(part, font=CN_FONT, **kw)))
    grp = VGroup(*mobs)
    grp.arrange(RIGHT, buff=0.15)
    return grp


import re as _re_rich

def rich(s, **kw):
    """
    行内混排：字符串里用 $...$ 包 LaTeX 公式，其余是中文。

    例：rich("由 $f'(x)=3x^2-3$ 得驻点 $x=\\\\pm 1$")
    → ["由 ", MathTex("f'(x)=3x^2-3"), " 得驻点 ", MathTex("x=\\\\pm 1")]

    为什么用 $ 分隔符：LLM 写 Markdown 时本来就习惯 $...$ 包公式，
    不需要额外教学；校验层也容易检查（$ 内禁中文）。
    """
    parts = _re_rich.split(r"\$(.+?)\$", s)
    if len(parts) == 1:
        return Text(s, font=CN_FONT, **kw)
    mobs = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 1:  # 奇数段 = 公式（复用 formula 的三级分流，公式段里也可含中文）
            mobs.append(formula(part, **kw))
        else:           # 偶数段 = 中文文字
            mobs.append(Text(part, font=CN_FONT, **kw))
    grp = VGroup(*mobs)
    grp.arrange(RIGHT, buff=0.12)
    # 防溢出：混排行整体超宽时等比缩小（画面安全宽度约 11.5 单位）。
    # 不做这步，长句会静默超出画面 —— 又一个只有看视频才能发现的坑。
    if grp.width > 11.5:
        grp.scale_to_fit_width(11.5)
    return grp


class StoryboardScene(Scene):
    def construct(self):
        self.camera.background_color = BG

{body}

        self.wait(0.3)
'''


def render(storyboard: dict,
           use_latex: bool = True,
           cn_font: str = "Source Han Serif CN") -> str:
    """
    把校验过的分镜 JSON 渲染成 Manim Python 源码。

    Args:
        storyboard: 已经过 schema.validate() 的分镜 dict
        use_latex: 是否用 LaTeX 渲染公式（没装 LaTeX 时传 False）
        cn_font: 中文字体（思源宋体 Source Han Serif CN / 楷体 KaiTi / 雅黑 Microsoft YaHei）

    Returns:
        Manim 场景源码字符串
    """
    # 自动探测 LaTeX：找不到就降级，绝不让渲染直接崩掉。
    # MiKTeX 常常装了但不写 PATH，latex_env 会扫描常见路径并注入。
    if use_latex:
        info = latex_env.detect()
        if not info["available"]:
            print(f"      [WARN] LaTeX 不可用（{info['reason']}），公式降级为纯文本")
            use_latex = False
        elif info["injected"]:
            print(f"      [PATH+] 已注入 TeX 目录: {info['injected'][0]}")

    env = {"formula": "formula", "use_latex": use_latex}

    blocks = []
    for idx, sc in enumerate(storyboard["scenes"]):
        tpl_name = sc["template"]
        fn = templates.TEMPLATE_REGISTRY[tpl_name]
        code = fn(sc["params"], env)

        # ---- 分镜边界：统一清屏 ----
        #
        # 为什么放在这里而不是让每个模板自己清：
        #   清屏是"分镜之间"的规则，不是"某个模板"的规则。
        #   之前靠模板自觉（末尾 FadeOut），但 step_card 的累积板书
        #   需要保留到本分镜结束，于是下一个分镜就直接叠了上去 ——
        #   画面能渲染、代码不报错，内容却是错的（静默失败）。
        #   放在渲染层统一处理，任何模板（包括以后新加的）都不可能漏掉。
        if idx == 0:
            head = ""
        else:
            head = (
                "        # ---- 清屏：移除上一个分镜的残留 ----\n"
                "        if self.mobjects:\n"
                "            self.play(FadeOut(Group(*self.mobjects)), run_time=0.35)\n"
                "            self.clear()\n"
            )

        blocks.append(
            head
            + f"        # ---- scene {sc['id']} | template: {tpl_name} ----\n"
            + code
            + "\n"
        )

    body = "\n".join(blocks)

    src = HEADER.format(
        title=storyboard.get("title", "untitled"),
        use_latex=use_latex,
        cn_font=cn_font,
        template_helper=templates.SAFE_EXPR_HELPER,
        body=body,
    )
    return src


def render_to_file(storyboard: dict, out_path: str, use_latex: bool = True,
                   cn_font: str = "SimSun") -> str:
    """渲染并写入 .py 文件，返回文件路径。"""
    src = render(storyboard, use_latex=use_latex, cn_font=cn_font)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(src)
    return out_path

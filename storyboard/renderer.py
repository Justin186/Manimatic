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
LATIN_FONT = "{latin_font}"     # 数字/字母专用（中文仍走 CN_FONT）

USE_LATEX = {use_latex}

# ---- 运行时 helper：安全求值、公式分流、防溢出、图例 ----
{helper}


class StoryboardScene(Scene):
    """
    分镜场景。带一个可选的"边渲边切"能力（SECTIONS=True 时启用）。

    为什么自己切而不是等 manim 的 --save_sections：后者要等整个 Scene 渲完，
    在 finish() 里才一次性把所有 section 合并出来，"渲好一段推一段"就没戏了。
    实际上 partial 片段是**渲染过程中就写好**的（每个 animation 一个 mp4），
    所以只要在分段点当场把它们合并，就能做到"这一分镜渲完立刻出一个 mp4"。

    实测：合并是 PyAV remux（不重编码），8 段合计约 0.5s，几乎不影响总耗时。
    """

    SECTIONS = {sections_flag}
    SECTION_TOTAL = {sections_total}
    # 每渲完一个分镜回调一次：(1-based 序号, mp4 绝对路径, 真实时长秒)
    ON_SECTION = None

    def section(self, index):
        """分镜 index（从 1 起）开始处调用：先把上一段导出去，再开新的一段。"""
        if not self.SECTIONS:
            return
        if index > 1:
            self._emit(index - 1)
        super().next_section("scene_%d" % index)

    def _emit(self, index):
        """把刚结束那一段的 partial 片段合并成 mp4，并回调通知。"""
        fw = self.renderer.file_writer
        # 正在编码的任务先收尾，否则最后几个 animation 的 mp4 还没落盘
        fw.join_all_encode_jobs()
        if not fw.sections:
            return
        files = fw.sections[-1].get_clean_partial_movie_files()
        if not files:
            return
        out_dir = fw.partial_movie_directory.parent.parent / "sections"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / ("%s_%04d_scene_%d%s" % (
            fw.output_name, index - 1, index, config.movie_file_extension))
        fw.combine_files(files, out)
        duration = 0.0
        try:
            from manim import get_video_metadata
            duration = float(get_video_metadata(out).get("duration", 0.0))
        except Exception:
            pass
        if callable(self.ON_SECTION):
            self.ON_SECTION(index, str(out), duration)

    def tear_down(self):
        # 最后一段没有"下一段"来触发导出，在这里收尾
        if self.SECTIONS and self.SECTION_TOTAL:
            self._emit(self.SECTION_TOTAL)

    def construct(self):
        self.camera.background_color = BG

{body}

        self.wait(0.3)
'''

# 分段完成事件的发射器（模块级，追加在 HEADER 之后）。
# 走字符串拼接而不是 HEADER 的 .format()，因为 .format 会把下面 json 的
# 花括号当占位符解析 —— 这类"模板里混进代码"的坑最好用拼接绕开。
SECTION_EMITTER = '''

# ---- 每个分镜渲完就往 stdout 打一行 @@ {json}，父进程按行读取 ----
import json as _sb_json          # noqa: E402
import sys as _sb_sys            # noqa: E402


def _sb_on_section(index, path, duration):
    _sb_sys.stdout.write("@@ " + _sb_json.dumps(
        {"section": index, "video": path, "duration": round(duration, 3)},
        ensure_ascii=False) + "\\n")
    _sb_sys.stdout.flush()


StoryboardScene.ON_SECTION = staticmethod(_sb_on_section)
'''


def _prepare_env(use_latex: bool, fast: bool = False,
                 carry_mode: str = "rebuild") -> dict:
    """探测 LaTeX 并组装渲染环境。找不到就降级，绝不让渲染直接崩掉。

    Args:
        carry_mode: 「承接元素」（`carry`）怎么处理，两条编排路径必须分开：
            `reuse`   —— 所有分镜拼成**一个** Scene（`render()`），共用同一个
                         construct() 作用域，上一分镜的元素变量还活着、对象还在屏上，
                         所以承接元素**一行代码都不生成**。
            `rebuild` —— 每个分镜是独立文件、独立 Scene（`render_split()`），
                         变量不存在，只能重建一次并立刻 add（不走入场动画）。
    """
    if use_latex:
        info = latex_env.detect()
        if not info["available"]:
            print(f"      [WARN] LaTeX 不可用（{info['reason']}），公式降级为纯文本")
            use_latex = False
        elif info["injected"]:
            print(f"      [PATH+] 已注入 TeX 目录: {info['injected'][0]}")
    return {"formula": "formula", "use_latex": use_latex, "fast": fast,
            "carry_mode": carry_mode}


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


def _carried(scene) -> list:
    """一个分镜里所有 `carry`（承接上一分镜）的元素 id。"""
    return [el["id"] for el in (scene or {}).get("elements", [])
            if isinstance(el, dict) and el.get("carry")]


def _clear_except(keep_ids) -> str:
    """
    分镜边界的清屏片段：把上一镜的残留淡出，但**承接过来的元素留着**。

    为什么不能一律 `clear()`：carry 的全部意义就是"接着上一镜继续演"。
    清掉它不只是少了 0.35 秒的淡出 —— 下一镜的代码里根本不会重建它
    （reuse 模式下承接元素一行代码都不生成），画面会直接空掉。

    为什么用 `m is not k` 的身份比较而不是 `m not in [...]`：manim 的 Mobject
    定义了 `__eq__`（按点集比较），用 `in` 会在两个内容相同的对象之间误判。
    """
    if not keep_ids:
        return _CLEAR          # 没有承接元素时逐字节沿用原片段，老分镜零影响
    keep = ", ".join(dsl.var_name(i) for i in sorted(keep_ids))
    return (
        "        # ---- 清屏：移除上一个分镜的残留（承接过来的元素留着）----\n"
        f"        _keep = [{keep}]\n"
        "        _doomed = [m for m in self.mobjects if all(m is not k for k in _keep)]\n"
        "        if _doomed:\n"
        "            self.play(FadeOut(Group(*_doomed)), run_time=0.35)\n"
        "            self.remove(*_doomed)\n"
    )


def _tail_fade(keep_ids) -> str:
    """
    拆分渲染时每个分镜**末尾**的淡出。`keep_ids` 是下一分镜要承接的元素 —— 它们不能淡出，
    否则下一段开头就会缺东西（虽然 rebuild 模式下会被重新 add，但那是一次肉眼可见的闪断）。
    """
    if not keep_ids:
        return ("\n        if self.mobjects:\n"
                "            self.play(FadeOut(Group(*self.mobjects)), run_time=0.35)\n")
    keep = ", ".join(dsl.var_name(i) for i in sorted(keep_ids))
    return (
        "\n        # ---- 末尾淡出：下一分镜要承接的元素留着 ----\n"
        f"        _keep = [{keep}]\n"
        "        _doomed = [m for m in self.mobjects if all(m is not k for k in _keep)]\n"
        "        if _doomed:\n"
        "            self.play(FadeOut(Group(*_doomed)), run_time=0.35)\n"
    )


def render(storyboard: dict,
           use_latex: bool = True,
           cn_font: str = "STZhongsong",
           latin_font: str = "Times New Roman",
           fast: bool = False,
           sections: bool = False) -> str:
    """
    把校验过的分镜 JSON 渲染成 Manim Python 源码（所有分镜拼成一个 Scene）。

    Args:
        storyboard: 已经过 schema.validate() 的分镜 dict
        use_latex: 是否用 LaTeX 渲染公式（没装 LaTeX 时传 False）
        cn_font: 中文字体
        latin_font: 数字/字母字体（默认新罗马体）。两者是**分开**的：
            CN_FONT 自带拉丁字形，Pango 不会为数字/字母去 fallback，
            所以必须显式指定，见 dsl.RUNTIME_HELPER 里的 _text()。
        sections: 开启"边渲边切"：在每个分镜开头插 `self.section(n)`，
            该分镜渲完就立刻把它的 partial 片段合并成一个 mp4 并打一行
            `@@ {json}` 到 stdout（父进程按行读 → 可立刻推给前端）。
            整条成片仍由 manim 正常输出，不需要 `--save_sections`。
            实测代价约 1s/条（合并是 PyAV remux，不重编码），换来首段 2.3s 可见。

    Returns:
        Manim 场景源码字符串
    """
    # 一个 Scene 演到底：承接元素靠"共用同一个 construct() 作用域"活下来
    env = _prepare_env(use_latex, fast, carry_mode="reuse")
    scenes = storyboard["scenes"]

    blocks = []
    for idx, sc in enumerate(scenes):
        code, label = _scene_code(sc, env)
        # 边界清屏：本分镜**承接过来**的元素要留着（没有 carry 时就是原来那段 _CLEAR）
        head = "" if idx == 0 else _clear_except(_carried(sc))
        # 分段点放在 _CLEAR 之前：清屏是"下一个分镜开始前的准备"，算进新分镜更自然。
        # 第一个分镜也插 —— manim 启动时会自带一个空的 autocreated section，
        # self.section(1) 内部的 next_section() 会把它删掉（finish_last_section 弹掉空段）。
        mark = f"        self.section({idx + 1})\n" if sections else ""
        blocks.append(mark + head + f"        # ---- {label} ----\n" + code + "\n")

    src = HEADER.format(
        title=storyboard.get("title", "untitled"),
        use_latex=env["use_latex"],
        cn_font=cn_font,
        latin_font=latin_font,
        helper=dsl.RUNTIME_HELPER,
        sections_flag=sections,
        sections_total=len(scenes),
        body="\n".join(blocks),
    )
    if sections:
        src += SECTION_EMITTER

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
                 latin_font: str = "Times New Roman",
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
    # 每个分镜都是独立文件、独立 Scene：承接元素只能重建 + 立刻 add
    env = _prepare_env(use_latex, fast, carry_mode="rebuild")
    scenes = storyboard["scenes"]
    out = []
    for idx, sc in enumerate(scenes):
        code, label = _scene_code(sc, env)
        # 拆分渲染后靠 ffmpeg 拼接，每个分镜末尾必须淡出，
        # 否则片段之间会硬切（单场景模式下这一步由下一个分镜的 _CLEAR 完成）。
        # ⚠️ 下一分镜要承接的元素**不能**淡出 —— 它会先淡掉、再被下一段重新 add，
        #    中间那一下闪断肉眼可见。
        nxt = scenes[idx + 1] if idx + 1 < len(scenes) else None
        code += _tail_fade(_carried(nxt))
        src = HEADER.format(
            title=storyboard.get("title", "untitled"),
            use_latex=env["use_latex"],
            cn_font=cn_font,
            latin_font=latin_font,
            helper=dsl.RUNTIME_HELPER,
            # 拆分渲染时每个文件只有一个分镜，不存在"分镜边界"，
            # 所以 SECTIONS 恒为 False（分段由 generate.py 用 ffmpeg 拼接完成）。
            #
            # ⚠️ 2026-09-12 补：这两个占位符此前**漏传**了，于是 HEADER.format() 直接
            #    抛 KeyError: 'sections_flag' —— 也就是 `generate.py --split` 整条路
            #    从写下那天起就是坏的（默认路径不走 render_split，所以一直没暴露）。
            sections_flag=False,
            sections_total=0,
            body=f"        # ---- {label} ----\n" + code,
        )
        _check_syntax(src)
        out.append((sc.get("id", idx + 1), src))
    return out


def render_to_file(storyboard: dict, out_path: str, use_latex: bool = True,
                   cn_font: str = "STZhongsong",
                   latin_font: str = "Times New Roman", fast: bool = False,
                   sections: bool = False) -> str:
    """渲染并写入 .py 文件，返回文件路径。"""
    src = render(storyboard, use_latex=use_latex, cn_font=cn_font,
                 latin_font=latin_font, fast=fast, sections=sections)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(src)
    return out_path

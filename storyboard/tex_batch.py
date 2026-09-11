# -*- coding: utf-8 -*-
"""
LaTeX 批处理预热：把「N 个公式各编译一次」变成「一次编译所有公式」。

================================================================================
为什么需要它
================================================================================
Manim 每创建一个 MathTex 就单独跑一遍 `latex` + `dvisvgm`。而一次 latex 调用的
约 400ms 里，绝大部分是**固定初始化开销**（加载 TeX 引擎和格式文件），跟你公式
是 `x^2` 还是一整行积分几乎无关。

实测（2026-09-10，本机 MiKTeX）：
    24 个公式逐个编译 ......... 18495 ms
    24 个公式塞进多页 tex .....   888 ms   ← 快 21 倍

================================================================================
怎么做
================================================================================
1. 从生成的源码里扫出所有公式
2. 用 Manim 自己的 API 算出每个公式的缓存文件名（tex_hash = sha256(tex内容)[:16]）
3. 把未缓存的公式拼进一个多页 tex（用 standalone 的 multi 选项让每个公式独立成页）
4. 一次 latex + 一次 dvisvgm，再按页序回填成 Manim 期望的缓存文件
5. 真正渲染时 Manim 发现缓存已存在，直接跳过编译

================================================================================
正确性（重要）
================================================================================
开发期已逐页比对验证：批处理第 N 页 与 单独编译第 N 个公式，两者的路径数据和
viewBox 完全一致，唯一差异是 dvisvgm 的字体内部编号（g1- vs g0-，因多页时字体
编号从 1 起）。该 id 仅在单个 SVG 文件内部引用，自洽，不影响渲染。

运行时仍保留一道完整性检查：**产出的 SVG 数量必须等于公式数量**，不等就整体作废
回退到逐个编译。任何异常也一律回退——预热只是加速，绝不能成为失败源。
"""

import os
import re
import shutil
import subprocess
import tempfile
import time

# 与 storyboard/renderer.py 的 formula() 保持一致：含中文走 ctex 模板
_CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")

# 从生成的代码里抓公式：
#   formula(r"""...""")          —— 独立公式
#   rich(r"""...$...$...""")     —— 行内混排里的 $...$ 段
#   x_range / y_range            —— 坐标轴刻度数字（也是 MathTex，数量不少）
_RE_FORMULA = re.compile(r'formula\(r"""(.*?)"""', re.S)
_RE_RICH = re.compile(r'rich\(r"""(.*?)"""', re.S)
_RE_INLINE = re.compile(r"\$(.+?)\$", re.S)
_RE_RANGE = re.compile(r"[xy]_range=\[([^\]]+)\]")

# Manim 给每个 tex_string 都套上这对 \special 标记（用于分部分上色），
# 而缓存 hash 是对「套好标记之后」的完整 tex 内容算的。
# 不照做的话算出的 hash 全都对不上 —— 这是本模块最容易踩的坑。
_SPECIAL_PRE = r"\special{dvisvgm:raw <g id='unique000'>}"
_SPECIAL_POST = r"\special{dvisvgm:raw </g>}"


def _fmt_num(v):
    """刻度数字的写法要和 Manim 的 DecimalNumber 一致：整数不带小数点。"""
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return ("%.4f" % v).rstrip("0").rstrip(".")


def _ticks_from_ranges(src):
    """
    从源码里的 x_range / y_range 推出坐标轴刻度数字。

    这些刻度每个都是一个 MathTex，是 LaTeX 编译的大户（一个坐标系动辄十几个）。
    它们由 Manim 内部创建，不在我们的源码里显式出现，只能这样反推。
    """
    out = []
    for m in _RE_RANGE.finditer(src):
        parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
        if len(parts) < 2:
            continue
        try:
            lo, hi = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        step = 1.0
        if len(parts) >= 3:
            try:
                step = float(parts[2])
            except ValueError:
                step = 1.0
        if step <= 0 or step > 100 or (hi - lo) > 400:
            continue
        n = int((hi - lo) / step)
        if n < 0 or n > 80:          # 刻度太多就放弃，避免生成一堆垃圾缓存
            continue
        for i in range(n + 1):
            out.append(_fmt_num(lo + i * step))
    return out


def collect_formulas(src):
    """从生成的 Manim 源码里提取所有会被 LaTeX 渲染的公式（去重保序）。"""
    out = []
    for m in _RE_FORMULA.finditer(src):
        out.append(m.group(1))
    for m in _RE_RICH.finditer(src):
        for i, seg in enumerate(_RE_INLINE.split(m.group(1))):
            if i % 2 == 1:          # 奇数段 = $...$ 里的公式
                out.append(seg)
    out += _ticks_from_ranges(src)
    seen, uniq = set(), []
    for f in out:
        f = f.strip()
        if f and f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def _split_tex(output):
    """把完整 tex 内容拆成 (preamble, body, postamble)。拆不开返回 None。"""
    if "\\begin{document}" not in output or "\\end{document}" not in output:
        return None
    pre, rest = output.split("\\begin{document}", 1)
    if "\\end{document}" not in rest:
        return None
    body, post = rest.split("\\end{document}", 1)
    return pre, body, post


def _make_multipage(pre, bodies):
    """
    把 preamble 改造成支持多页的形式，并拼上所有 body。

    standalone 的 preview 模式会把 \\newpage 吞掉（实测 DVI 只有 1 页），
    必须改用它的 multi 选项：每个 msbp 环境单独成为一页。
    """
    if "standalone" not in pre:
        return None                      # 不是 standalone，无法用 multi 方案

    def add_multi(m):
        inner = m.group(1)
        opts = inner[1:-1] if inner else ""      # 去掉方括号
        opts = (opts + "," if opts else "") + "multi=msbp"
        return "\\documentclass[" + opts + "]{standalone}"

    pre2, n = re.subn(r"\\documentclass(\[[^\]]*\])?\{standalone\}", add_multi, pre)
    if n == 0:
        return None
    pre2 += "\n\\newenvironment{msbp}{}{}\n"
    return (pre2 + "\\begin{document}\n"
            + "\n".join("\\begin{msbp}\n%s\n\\end{msbp}" % b for b in bodies)
            + "\n\\end{document}\n")


def prewarm(src, media_dir, verbose=True):
    """
    批量预编译源码里的所有公式，填充 Manim 的 Tex 缓存。

    Args:
        src: 生成的 Manim 源码（render() / render_split() 的产物）
        media_dir: Manim 的 media 目录，缓存写在它下面的 Tex/ 里
        verbose: 是否打印统计

    Returns:
        dict: {"total": 公式总数, "cached": 已有缓存数, "built": 本次编译数,
               "seconds": 耗时}
        任何环节失败都返回 {"total": n, "cached": 0, "built": 0, "skipped": 原因}
    """
    formulas = collect_formulas(src)
    stats = {"total": len(formulas), "cached": 0, "built": 0, "seconds": 0.0}
    if not formulas:
        return stats

    t0 = time.time()
    try:
        # 延迟 import：Manim 导入约 2~3 秒，没公式时完全不必付这个代价
        # 注意 TexTemplateLibrary 在 utils.tex_templates（不是 utils.tex）
        from manim import config, TexTemplateLibrary
        from manim.utils.tex_file_writing import generate_tex_file
    except Exception as e:                                   # pragma: no cover
        stats["skipped"] = f"import manim 失败: {e}"
        return stats

    # Manim 每写一个 tex 文件都要打一行 INFO，几十个公式会把控制台刷爆，临时关掉
    import logging
    logging.disable(logging.INFO)
    try:
        config.media_dir = media_dir
        tex_dir = config.get_dir("tex_dir")
        tex_dir.mkdir(parents=True, exist_ok=True)

        default_tpl = config["tex_template"]
        ctex_tpl = TexTemplateLibrary.ctex

        # 按模板分组：普通公式走默认模板，含中文的走 ctex
        groups = {}
        for f in formulas:
            # 必须套 \special 标记，否则 hash 与 Manim 对不上（见 _SPECIAL_PRE 注释）
            if _CJK.search(f):
                # 对应 renderer 里的 Tex(f"${s}$", tex_template=ctex)
                groups.setdefault("ctex", []).append(
                    (_SPECIAL_PRE + f"${f}$" + _SPECIAL_POST, None, ctex_tpl))
            else:
                # 对应 MathTex(s)，默认环境 align*
                groups.setdefault("math", []).append(
                    (_SPECIAL_PRE + f + _SPECIAL_POST, "align*", default_tpl))

        latex = shutil.which("latex") or shutil.which("pdflatex")
        dvisvgm = shutil.which("dvisvgm")
        if not (latex and dvisvgm):
            stats["skipped"] = "未找到 latex / dvisvgm"
            return stats

        for gname, items in groups.items():
            # 1) 用 Manim 自己的 API 算出每个公式的缓存路径，分成"已有/待编译"
            todo = []
            for expr, env, tpl in items:
                try:
                    tex_file = generate_tex_file(expr, env, tpl)
                except Exception:
                    continue
                svg = tex_file.with_suffix(".svg")
                if svg.exists():
                    stats["cached"] += 1
                    continue
                try:
                    content = tex_file.read_text(encoding="utf-8")
                except Exception:
                    continue
                todo.append((tex_file, content))

            if not todo:
                continue

            # 2) 拼多页 tex
            parts = [_split_tex(c) for _, c in todo]
            if any(p is None for p in parts):
                stats["skipped"] = f"{gname}: 无法解析 tex 模板结构"
                continue
            pre = parts[0][0]
            multi = _make_multipage(pre, [p[1] for p in parts])
            if multi is None:
                stats["skipped"] = f"{gname}: 模板不是 standalone，跳过批处理"
                continue

            # 3) 一次 latex + 一次 dvisvgm
            work = tempfile.mkdtemp(prefix="msb_texbatch_")
            try:
                with open(os.path.join(work, "batch.tex"), "w", encoding="utf-8") as f:
                    f.write(multi)
                # 必须用 cwd + 相对路径：dvisvgm 遇到 Windows 反斜杠的绝对路径会
                # 直接以 -4 退出（连报错都没有），Manim 内部也是用 as_posix() 绕开
                subprocess.run(
                    [latex, "-interaction=batchmode", "-output-format=dvi",
                     "-halt-on-error", "-output-directory=.", "batch.tex"],
                    cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=300,
                )
                dvi = os.path.join(work, "batch.dvi")
                if not os.path.exists(dvi):
                    stats["skipped"] = f"{gname}: latex 编译失败"
                    continue
                subprocess.run(
                    [dvisvgm, "--page=1-", "--no-fonts", "--verbosity=0",
                     "--output=out-%p.svg", "batch.dvi"],
                    cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=300,
                )

                # 4) 完整性检查：页数必须等于公式数，否则整体作废（宁可慢也不能错）
                svgs = sorted(
                    f for f in os.listdir(work)
                    if f.startswith("out") and f.endswith(".svg")
                )
                if len(svgs) != len(todo):
                    stats["skipped"] = (
                        f"{gname}: 产出 {len(svgs)} 页 != 公式 {len(todo)} 个，放弃预热"
                    )
                    continue

                # 5) 按页序回填缓存
                for (tex_file, _), svg_name in zip(todo, svgs):
                    shutil.copyfile(
                        os.path.join(work, svg_name),
                        str(tex_file.with_suffix(".svg")),
                    )
                    stats["built"] += 1
            finally:
                shutil.rmtree(work, ignore_errors=True)

    except Exception as e:                                   # pragma: no cover
        stats["skipped"] = f"异常: {e}"
    finally:
        logging.disable(logging.NOTSET)

    stats["seconds"] = round(time.time() - t0, 2)
    if verbose and stats["built"]:
        print(f"      [TeX 批处理] {stats['built']} 个公式一次编译完成"
              f"（{stats['seconds']}s，另有 {stats['cached']} 个已缓存）")
    elif verbose and stats.get("skipped"):
        print(f"      [TeX 批处理] 跳过：{stats['skipped']}")
    return stats

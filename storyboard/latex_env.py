#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
LaTeX 环境探测与 PATH 自动注入。

为什么需要这个模块：
    Manim 用 shutil.which("latex") 找 LaTeX。MiKTeX 安装时默认**不写 PATH**
    （它靠自己的 PATH 注入机制，在子进程里不生效），结果就是：
    LaTeX 明明装好了，Manim 却报 FileNotFoundError。

    这个模块扫描常见安装路径，把 bin 目录注入 os.environ["PATH"]，
    子进程（manim render）会继承，从而能找到 latex / dvisvgm。

另一个真实坑：
    MiKTeX 默认遇到缺失宏包会**弹窗询问**，Manim 是非交互子进程，
    弹窗没人点 → 进程挂死，表现为"渲染卡住不动"。
    必须把 MiKTeX 设为自动安装模式（见 README 的排错章节）。
"""

import os
import shutil
import subprocess
import sys

# MiKTeX / TeX Live 在 Windows 上的常见安装位置
CANDIDATE_DIRS = [
    r"D:\MiKTeX\miktex\bin\x64",
    r"C:\MiKTeX\miktex\bin\x64",
    r"C:\Program Files\MiKTeX\miktex\bin\x64",
    r"C:\Users\{user}\AppData\Local\Programs\MiKTeX\miktex\bin\x64",
    r"C:\texlive\2025\bin\windows",
    r"C:\texlive\2024\bin\windows",
]

# Manim 真正需要的两个二进制
REQUIRED = ["latex", "dvisvgm"]

_injected = False


def _candidates():
    user = os.environ.get("USERNAME", "")
    for d in CANDIDATE_DIRS:
        yield d.replace("{user}", user)


def inject_path(verbose=False):
    """
    把找到的 TeX bin 目录注入 PATH。幂等，可重复调用。

    Returns:
        注入的目录列表（可能为空）
    """
    global _injected
    injected = []

    for d in _candidates():
        if not os.path.isdir(d):
            continue
        # 必须同时有 latex 和 dvisvgm 才认为这是个可用的 TeX 发行版
        if not all(
            os.path.exists(os.path.join(d, f"{b}.exe")) or os.path.exists(os.path.join(d, b))
            for b in REQUIRED
        ):
            continue

        cur = os.environ.get("PATH", "")
        if d.lower() not in cur.lower():
            os.environ["PATH"] = d + os.pathsep + cur
            injected.append(d)
            if verbose:
                print(f"      [PATH+] 注入 {d}")
        elif verbose:
            print(f"      [PATH=] 已在 PATH: {d}")

    _injected = True
    return injected


def detect(verbose=False):
    """
    探测 LaTeX 环境。

    Returns:
        dict: {
            "available": bool,   # latex + dvisvgm 是否都可用
            "latex": str|None,
            "dvisvgm": str|None,
            "injected": list,    # 本次注入的 PATH 目录
            "reason": str,       # 不可用时说明原因
        }
    """
    if not _injected:
        injected = inject_path(verbose=verbose)
    else:
        injected = []

    latex = shutil.which("latex") or shutil.which("pdflatex")
    dvisvgm = shutil.which("dvisvgm")

    if latex and dvisvgm:
        return {
            "available": True,
            "latex": latex,
            "dvisvgm": dvisvgm,
            "injected": injected,
            "reason": "",
        }

    missing = [b for b, p in (("latex", latex), ("dvisvgm", dvisvgm)) if not p]
    return {
        "available": False,
        "latex": latex,
        "dvisvgm": dvisvgm,
        "injected": injected,
        "reason": "未找到: " + ", ".join(missing),
    }


def smoke_test_formula(timeout=120):
    """
    真正跑一次 MathTex 渲染，而不仅仅是 which 找得到。

    这一步不能省：which 找到 latex 只说明二进制存在，
    但宏包缺失、MiKTeX 弹窗挂死、dvisvgm 版本不匹配
    都只会在真正渲染时暴露。

    Returns:
        (ok: bool, message: str, seconds: float)
    """
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="msb_tex_")
    scene = os.path.join(tmpdir, "tex_smoke.py")
    with open(scene, "w", encoding="utf-8") as f:
        f.write(
            "from manim import *\n"
            "class TexSmoke(Scene):\n"
            "    def construct(self):\n"
            "        t = MathTex(r'\\\\frac{a}{b} + x^2', font_size=48)\n"
            "        self.add(t)\n"
            "        self.wait(0.5)\n"
        )

    import time
    t0 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, "-m", "manim", "render", "-ql",
             "--disable_caching", "--media_dir", tmpdir, scene, "TexSmoke"],
            capture_output=True, text=True, timeout=timeout, cwd=tmpdir,
        )
        elapsed = time.time() - t0
    except subprocess.TimeoutExpired:
        return False, (
            f"超时（>{timeout}s）。极可能是 MiKTeX 弹窗等待确认——"
            f"请把 MiKTeX 设为自动安装缺失宏包，见 README 排错章节。"
        ), timeout

    if r.returncode == 0:
        return True, f"MathTex 渲染成功（{elapsed:.1f}s）", elapsed

    tail = (r.stderr or r.stdout or "")[-1200:]
    return False, f"MathTex 渲染失败（{elapsed:.1f}s）:\n{tail}", elapsed


if __name__ == "__main__":
    print("LaTeX 环境探测")
    print("=" * 50)
    info = detect(verbose=True)
    print(f"\nlatex   : {info['latex'] or '(未找到)'}")
    print(f"dvisvgm : {info['dvisvgm'] or '(未找到)'}")
    print(f"可用    : {info['available']}")
    if info["injected"]:
        print(f"本次注入: {len(info['injected'])} 个目录到 PATH")
    if not info["available"]:
        print(f"原因    : {info['reason']}")
        sys.exit(1)

    print("\n--- MathTex 实际渲染测试 ---")
    ok, msg, sec = smoke_test_formula()
    print(("  [OK] " if ok else "  [FAIL] ") + msg)
    sys.exit(0 if ok else 1)

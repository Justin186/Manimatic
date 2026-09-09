#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
环境自检 —— 装完依赖先跑这个，能在 30 秒内定位所有环境问题。

用法：
    python check_env.py
"""

import os
import shutil
import subprocess
import sys
import time

OK = "[OK]  "
BAD = "[FAIL]"
WARN = "[WARN]"


def check(label, fn):
    try:
        result = fn()
        print(f"{OK} {label}: {result}")
        return True
    except Exception as e:
        print(f"{BAD} {label}: {type(e).__name__}: {e}")
        return False


def main():
    print("=" * 60)
    print("MathStoryboard 环境自检")
    print("=" * 60)

    # 必须最先做：把 MiKTeX/TeX Live 的 bin 目录注入 PATH，
    # 否则后面的 which("latex") 会误报"未安装"。
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from storyboard import latex_env

    print(f"\nPython: {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 10):
        print(f"{BAD} Python 版本过低，Manim CE 需要 3.10+（推荐 3.11）")
    elif sys.version_info >= (3, 14):
        print(f"{WARN} Python 3.14+ 可能有依赖兼容问题，建议用 3.11")

    results = {}

    print("\n--- 依赖检查 ---")
    results["manim"] = check("manim", lambda: __import__("manim").__version__)
    results["numpy"] = check("numpy", lambda: __import__("numpy").__version__)

    print("\n--- 系统工具 ---")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        print(f"{OK} ffmpeg: {ffmpeg}")
        results["ffmpeg"] = True
    else:
        print(f"{BAD} ffmpeg 未找到 —— 必须安装")
        results["ffmpeg"] = False

    print("\n--- LaTeX（公式渲染的关键依赖）---")
    tex = latex_env.detect(verbose=True)
    if tex["available"]:
        print(f"{OK} latex   : {tex['latex']}")
        print(f"{OK} dvisvgm : {tex['dvisvgm']}")
        if tex["injected"]:
            print(f"      注意：TeX 不在系统 PATH 里，已临时注入（仅本次进程有效）")
            print(f"      永久生效请把它加进系统 PATH: {tex['injected'][0]}")
        results["latex"] = True
    else:
        print(f"{BAD} {tex['reason']}")
        print("      公式会退化为纯文本。安装 MiKTeX 后重跑本脚本。")
        results["latex"] = False

    print("\n--- 中文字体 ---")
    # 用 Pango 的字体列表检查（官方文档推荐方式），比扫 Fonts 目录准：
    # Pango 能看到的不只是文件，还包括按字体家族名注册的所有变体。
    # 若要求的字体不在列表里，Manim 会静默 fallback 到默认字体 —— 又一个静默失败点。
    try:
        import manimpango
        fonts = manimpango.list_fonts()
        wanted = {"SimSun": "宋体", "KaiTi": "楷体", "Microsoft YaHei": "微软雅黑",
                  "SimHei": "黑体", "NSimSun": "新宋体"}
        found_any = False
        for fam, zh in wanted.items():
            mark = OK if fam in fonts else WARN
            if fam in fonts:
                found_any = True
            print(f"{mark} {fam}（{zh}）: {'可用' if fam in fonts else '未注册到 Pango'}")
        if not found_any:
            print(f"{BAD} 没有任何常用中文字体！中文将显示为方块。")
    except ImportError:
        print(f"{WARN} manimpango 不可用，跳过字体检查")

    # ctex 模板可用性：决定「公式里嵌中文」是否走真 LaTeX 渲染
    print("\n--- ctex（公式内中文）---")
    try:
        from manim import TexTemplateLibrary
        t = TexTemplateLibrary.ctex
        print(f"{OK} TexTemplateLibrary.ctex 可用（xelatex 路线，公式内可嵌中文）")
        print("      首次编译会触发 MiKTeX 自动安装宏包，偏慢；之后走缓存。")
    except Exception as e:
        print(f"{WARN} ctex 模板异常：{e}")

    # 最小渲染测试
    print("\n--- 最小渲染测试 ---")
    if not results.get("manim"):
        print("跳过（manim 未安装）")
        return 1

    test_scene = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "output", "_smoke_test.py")
    os.makedirs(os.path.dirname(test_scene), exist_ok=True)
    with open(test_scene, "w", encoding="utf-8") as f:
        f.write(
            "from manim import *\n"
            "class SmokeTest(Scene):\n"
            "    def construct(self):\n"
            "        c = Circle(radius=1, color=BLUE)\n"
            "        self.play(Create(c), run_time=1)\n"
            "        self.wait(0.5)\n"
        )

    t0 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, "-m", "manim", "render", "-ql",
             "--disable_caching", test_scene, "SmokeTest"],
            capture_output=True, text=True, timeout=180,
        )
        elapsed = time.time() - t0
        if r.returncode == 0:
            print(f"{OK} 渲染成功，耗时 {elapsed:.1f} 秒（-ql）")
            print(f"     这是你后续估算出片时间的基准")
        else:
            print(f"{BAD} 渲染失败（{elapsed:.1f} 秒）")
            tail = (r.stderr or r.stdout or "")[-1500:]
            print("-" * 50)
            print(tail)
    except subprocess.TimeoutExpired:
        print(f"{BAD} 渲染超时（>180 秒）")
    except FileNotFoundError:
        print(f"{BAD} 无法调用 manim")

    # MathTex 单独测：which 找到 latex ≠ 公式能渲染出来。
    # 宏包缺失、MiKTeX 弹窗挂死都只在这一步暴露。
    if results.get("latex"):
        print("\n--- MathTex 公式渲染测试（关键）---")
        print("     正在渲染 \\frac{a}{b} + x^2，首次会生成宏包缓存，可能较慢...")
        ok, msg, _ = latex_env.smoke_test_formula(timeout=180)
        print(f"{OK if ok else BAD} {msg}")
        if not ok:
            print("      公式是讲解视频的核心，这一步不过就必须修，不能带病开发。")

    print("\n" + "=" * 60)
    missing = [k for k, v in results.items() if not v]
    if missing:
        print(f"缺失项: {', '.join(missing)}")
    else:
        print("全部就绪")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())

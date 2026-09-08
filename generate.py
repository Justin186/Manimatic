#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
主入口：分镜 JSON → 校验 → 生成 Manim 代码 → 渲染 MP4

用法：
    python generate.py examples/quadratic_transform.json
    python generate.py examples/quadratic_transform.json -q m      # 中等质量
    python generate.py examples/quadratic_transform.json --dry-run # 只生成代码不渲染
    python generate.py examples/quadratic_transform.json --no-latex
"""

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storyboard import schema, renderer, templates, latex_env  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "output")


def main():
    ap = argparse.ArgumentParser(description="分镜 JSON → 讲解动画")
    ap.add_argument("storyboard", help="分镜 JSON 文件路径")
    ap.add_argument("-q", "--quality", default="l",
                    choices=["l", "m", "h"], help="渲染质量 l/m/h（默认 l）")
    ap.add_argument("--dry-run", action="store_true", help="只生成代码，不渲染")
    ap.add_argument("--no-latex", action="store_true", help="强制不用 LaTeX")
    ap.add_argument("--keep-code", action="store_true", help="保留生成的 .py 文件")
    args = ap.parse_args()

    # ---------- 1. 读取 ----------
    path = args.storyboard
    if not os.path.exists(path):
        path = os.path.join(HERE, "examples", os.path.basename(args.storyboard))
    if not os.path.exists(path):
        print(f"[FAIL] 找不到分镜文件: {args.storyboard}")
        return 1

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    print(f"[1/4] 读取分镜: {os.path.basename(path)}")

    # ---------- 2. 校验 ----------
    t0 = time.time()
    try:
        sb = schema.validate(raw, templates.TEMPLATE_REGISTRY)
    except schema.SchemaError as e:
        print(f"[FAIL] 分镜校验失败: {e}")
        print("       （这一步拦下的错误，就是原本会变成静默失败的错误）")
        return 2
    print(f"[2/4] 校验通过，{len(sb['scenes'])} 个分镜，耗时 {time.time() - t0:.3f}s")

    dropped = [(s["id"], s["_dropped_keys"]) for s in sb["scenes"] if s["_dropped_keys"]]
    if dropped:
        print(f"      已忽略的未知参数: {dropped}")

    # ---------- 3. 生成代码 ----------
    # LaTeX 探测交给 latex_env：它会扫描 MiKTeX/TeX Live 常见路径并注入 PATH，
    # 避免「装了却找不到」这类最耗时的环境坑。
    if args.no_latex:
        use_latex = False
    else:
        info = latex_env.detect()
        use_latex = info["available"]
        if use_latex:
            if info["injected"]:
                print(f"      [PATH+] 已注入 TeX 目录: {info['injected'][0]}")
            print(f"      LaTeX: {os.path.basename(info['latex'])} "
                  f"+ {os.path.basename(info['dvisvgm'])}")
        else:
            print(f"      [WARN] {info['reason']}，公式将退化为纯文本")

    name = os.path.splitext(os.path.basename(path))[0]
    py_path = os.path.join(OUTPUT, f"{name}_scene.py")
    renderer.render_to_file(sb, py_path, use_latex=use_latex)
    n_lines = sum(1 for _ in open(py_path, encoding="utf-8"))
    print(f"[3/4] 生成 Manim 代码: {py_path}  ({n_lines} 行, LaTeX={use_latex})")

    if args.dry_run:
        print("\n[dry-run] 生成的代码：\n" + "-" * 60)
        print(open(py_path, encoding="utf-8").read())
        return 0

    # ---------- 4. 渲染 ----------
    os.makedirs(OUTPUT, exist_ok=True)
    print(f"[4/4] 渲染中（-q{args.quality}）...")
    t1 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, "-m", "manim", "render",
             f"-q{args.quality}", "--disable_caching",
             "--media_dir", OUTPUT, py_path, "StoryboardScene"],
            capture_output=True, text=True, timeout=900, cwd=OUTPUT,
        )
        elapsed = time.time() - t1
    except subprocess.TimeoutExpired:
        print("[FAIL] 渲染超时（>900 秒）")
        return 3

    if r.returncode != 0:
        print(f"[FAIL] 渲染失败（{elapsed:.1f}s）")
        print("-" * 60)
        print((r.stderr or r.stdout or "")[-2500:])
        return 3

    print(f"[OK] 渲染成功，耗时 {elapsed:.1f} 秒")

    # 找最终视频：必须按 manim 的规则精确构造路径，不能遍历。
    # 遍历会拿到别的场景/别的清晰度的旧文件（字母序在前就中招），
    # 表现为"渲染成功了但视频是上一次的" —— 又一种静默失败。
    quality_dir = {"l": "480p15", "m": "720p30", "h": "1080p60",
                   "p": "1440p60", "k": "2160p60"}[args.quality]
    mp4 = os.path.join(OUTPUT, "videos", f"{name}_scene",
                       quality_dir, "StoryboardScene.mp4")
    if not os.path.exists(mp4):
        print(f"[WARN] 未在预期路径找到产物: {mp4}")
        mp4 = None
    if mp4:
        size = os.path.getsize(mp4) / 1024 / 1024
        print(f"\n输出: {mp4}  ({size:.2f} MB)")

    total = time.time() - t0
    print(f"总耗时: {total:.1f}s（含校验+生成+渲染）")
    print(f"分镜数: {len(sb['scenes'])}  平均每分镜: {elapsed / max(len(sb['scenes']), 1):.1f}s")

    if not args.keep_code:
        pass  # 保留 .py 便于调试，默认不删

    return 0


if __name__ == "__main__":
    sys.exit(main())

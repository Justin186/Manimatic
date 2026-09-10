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
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storyboard import schema, renderer, templates, latex_env  # noqa: E402
from storyboard import dsl  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "output")


def quality_tag(args):
    """manim 的产物目录名：{高度}p{帧率}。"""
    if args.resolution:
        h = args.resolution.split(",")[1].strip()
        return f"{h}p{args.fps}" if args.fps else f"{h}p30"
    return {"l": "480p15", "m": "720p30", "h": "1080p60",
            "p": "1440p60", "k": "2160p60"}[args.quality]


def quality_args(args):
    """传给 manim 的分辨率/帧率参数。"""
    out = [f"-q{args.quality}"] if not args.resolution else ["--resolution", args.resolution]
    if args.fps:
        out += ["--fps", str(args.fps)]
    return out


def render_parallel(sb, name, args, use_latex, quality_dir):
    """
    每个分镜一个 manim 进程并行渲染，最后用 ffmpeg 拼接。

    为什么不用 GPU：实测像素量涨 4.3 倍（480p15→720p30）耗时只涨 12%，
    说明光栅化只占总耗时约 12%，换 GPU 天花板极低；而瓶颈在 Python 侧的
    mobject 计算，多核并行才是对症的 —— 4 分镜 48.1s → 11.4s（20 核）。

    Returns:
        (ok: bool, elapsed: float)
    """
    parts = renderer.render_split(sb, use_latex=use_latex,
                                  cn_font=args.font, fast=args.fast)
    parts_dir = os.path.join(OUTPUT, "_parts", name)
    os.makedirs(parts_dir, exist_ok=True)

    jobs = args.jobs or min(len(parts), os.cpu_count() or 4, 8)
    jobs = max(1, min(jobs, len(parts)))

    all_metas, todo, cached = [], [], 0
    for i, (sid, src) in enumerate(parts):
        stem = f"{name}_s{i}"
        py = os.path.join(parts_dir, f"{stem}.py")
        media = os.path.join(parts_dir, f"m{i}")
        seg = os.path.join(media, "videos", stem, quality_dir, "StoryboardScene.mp4")
        item = (i, sid, py, media, stem)
        all_metas.append(item)
        # 增量：源码没变且片段已存在就跳过。
        # 这样"只重跑失败的那个分镜"不用把整条链重来一遍。
        same_src = os.path.exists(py) and open(py, encoding="utf-8").read() == src
        if same_src and os.path.exists(seg):
            cached += 1
            continue
        with open(py, "w", encoding="utf-8") as f:
            f.write(src)
        todo.append(item)

    print(f"[4/4] 并行渲染 {len(parts)} 个分镜（-q{args.quality}，并发 {jobs}"
          + (f"，{cached} 个命中缓存跳过" if cached else "") + ")...")

    t1 = time.time()
    failures = []
    for k in range(0, len(todo), jobs):
        batch = todo[k:k + jobs]
        procs = []
        for (i, sid, py, media, stem) in batch:
            procs.append((sid, subprocess.Popen(
                [sys.executable, "-m", "manim", "render"] + quality_args(args) +
                ["--disable_caching", "--media_dir", media, py, "StoryboardScene"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", cwd=OUTPUT)))
        for sid, p in procs:
            # 中文 Windows 下必须显式 encoding/errors，否则 GBK 解码会崩掉读取线程
            out, err = p.communicate(timeout=900)
            if p.returncode != 0:
                failures.append((sid, (err or out or "")[-1200:]))
    elapsed = time.time() - t1

    if failures:
        print(f"[FAIL] {len(failures)}/{len(parts)} 个分镜渲染失败（{elapsed:.1f}s）")
        print("       （只重跑失败的分镜即可 —— 这就是分镜独立渲染的好处）")
        for sid, msg in failures:
            print(f"\n---- 分镜 {sid} ----\n{msg}")
        return False, elapsed

    print(f"[OK] {len(todo)} 个分镜渲染完成，耗时 {elapsed:.1f} 秒"
          + (f"（另有 {cached} 个命中缓存）" if cached else ""))

    segs = []
    for (i, sid, py, media, stem) in all_metas:
        m = os.path.join(media, "videos", stem, quality_dir, "StoryboardScene.mp4")
        if not os.path.exists(m):
            print(f"[FAIL] 分镜 {sid} 未产出: {m}")
            return False, elapsed
        segs.append(m)

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("[FAIL] 未找到 ffmpeg，无法拼接分段（分段本身已生成，可手动拼接）")
        return False, elapsed

    final_dir = os.path.join(OUTPUT, "videos", f"{name}_scene", quality_dir)
    os.makedirs(final_dir, exist_ok=True)
    final = os.path.join(final_dir, "StoryboardScene.mp4")
    lst = os.path.join(parts_dir, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for m in segs:
            f.write("file '" + m.replace("\\", "/") + "'\n")

    r = subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", lst,
                        "-c", "copy", final],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.exists(final):
        # 各段编码参数不完全一致时 -c copy 会失败，退回重新编码
        r = subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", lst,
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", final],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 or not os.path.exists(final):
            print("[FAIL] ffmpeg 拼接失败")
            print((r.stderr or "")[-1200:])
            return False, elapsed
    return True, elapsed


def main():
    ap = argparse.ArgumentParser(description="分镜 JSON → 讲解动画")
    ap.add_argument("storyboard", nargs="?", help="分镜 JSON 文件路径")
    ap.add_argument("-q", "--quality", default="l",
                    choices=["l", "m", "h"], help="渲染质量 l/m/h（默认 l）")
    ap.add_argument("--dry-run", action="store_true", help="只生成代码，不渲染")
    ap.add_argument("--no-latex", action="store_true", help="强制不用 LaTeX")
    ap.add_argument("--keep-code", action="store_true", help="保留生成的 .py 文件")
    ap.add_argument("--spec", action="store_true",
                    help="打印分镜 DSL 规格（给大模型用的 prompt 片段）后退出")
    ap.add_argument("--parallel", action="store_true",
                    help="每个分镜开一个进程并行渲染再拼接（多核加速，实测 4 分镜 48s→11s）")
    ap.add_argument("--jobs", type=int, default=0,
                    help="并行渲染的并发数（默认 min(分镜数, CPU 核数, 8)）")
    ap.add_argument("--fast", action="store_true",
                    help="快速模式：坐标轴刻度数字改用 Text 渲染，省掉大量 LaTeX 编译"
                         "（实测首次渲染 60s→39s，出片时再关掉）")
    ap.add_argument("--resolution", default="",
                    help="自定义分辨率，如 1280,720（默认用 -q 的预设）")
    ap.add_argument("--fps", type=int, default=0,
                    help="自定义帧率。实测帧率比分辨率更影响耗时："
                         "720p15 与 480p15 几乎一样快，720p30 才明显变慢")
    ap.add_argument("--font", default="STZhongsong",
                    help="中文字体（STZhongsong=华文中宋 / KaiTi=楷体 / Microsoft YaHei=雅黑）")
    args = ap.parse_args()

    if args.spec:
        print(dsl.describe())
        return 0
    if not args.storyboard:
        ap.error("需要提供分镜 JSON 路径（或用 --spec 查看 DSL 规格）")

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

    dropped = [(s["id"], s.get("_dropped_keys") or []) for s in sb["scenes"]
               if s.get("_dropped_keys")]
    if dropped:
        print(f"      已忽略的未知参数: {dropped}")

    n_dsl = sum(1 for s in sb["scenes"] if s.get("mode") == "dsl")
    if n_dsl:
        print(f"      其中 {n_dsl} 个分镜使用 DSL（元素+时间线）写法")

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
    renderer.render_to_file(sb, py_path, use_latex=use_latex,
                           cn_font=args.font, fast=args.fast)
    n_lines = sum(1 for _ in open(py_path, encoding="utf-8"))
    print(f"[3/4] 生成 Manim 代码: {py_path}  ({n_lines} 行, LaTeX={use_latex})")

    if args.dry_run:
        print("\n[dry-run] 生成的代码：\n" + "-" * 60)
        print(open(py_path, encoding="utf-8").read())
        return 0

    # ---------- 4. 渲染 ----------
    os.makedirs(OUTPUT, exist_ok=True)
    quality_dir = quality_tag(args)

    # 并行模式：每个分镜单独渲染再拼接。首次不划算（每进程都要重复付启动+LaTeX 开销），
    # 但重跑时靠增量缓存能做到秒级。
    if args.parallel and len(sb["scenes"]) > 1:
        ok, elapsed = render_parallel(sb, name, args, use_latex, quality_dir)
        if not ok:
            return 3
        mp4 = os.path.join(OUTPUT, "videos", f"{name}_scene",
                           quality_dir, "StoryboardScene.mp4")
        if os.path.exists(mp4):
            print(f"\n输出: {mp4}  ({os.path.getsize(mp4) / 1024 / 1024:.2f} MB)")
        print(f"总耗时: {time.time() - t0:.1f}s（含校验+生成+渲染）")
        print(f"分镜数: {len(sb['scenes'])}  平均每分镜: "
              f"{elapsed / max(len(sb['scenes']), 1):.1f}s")
        return 0

    print(f"[4/4] 渲染中（-q{args.quality}）...")
    t1 = time.time()
    try:
        # encoding / errors 必须显式指定：
        #   中文 Windows 的 locale 是 GBK，而 manim 输出的是 UTF-8。
        #   不指定的话 subprocess 会用 GBK 解码 → UnicodeDecodeError，
        #   读取线程直接崩掉，主流程卡死在 communicate() 上（表面上像"渲染卡住"）。
        #   errors="replace" 是兜底：即使有解不出的字节也只替换，不让线程死。
        r = subprocess.run(
            [sys.executable, "-m", "manim", "render"]
            + quality_args(args)
            + ["--disable_caching",
               "--media_dir", OUTPUT, py_path, "StoryboardScene"],
            capture_output=True, text=True, timeout=900, cwd=OUTPUT,
            encoding="utf-8", errors="replace",
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

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
from storyboard import dsl, tex_batch, llm  # noqa: E402

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
    分镜级渲染：每个分镜单独产出 mp4，最后用 ffmpeg 拼成整条。

    ⚠️ 实现于 2026-09-12 重写，原因见 README「分镜级渲染」一节：
    旧实现是"每个分镜起一个 manim 进程"，看起来像是能吃掉 20 核，实测却最慢 ——
    瓶颈是**内存带宽**这个被所有进程共享的资源：单进程能吃 35 GB/s，8 个进程并行
    时每个只剩 7.2 GB/s（整机上限约 58 GB/s 就封顶）。多开进程不会多出算力，
    只会互相拖慢，外加每个进程都要重付一次 manim 初始化。
    实测（free_product_rule，8 分镜，480p15）：
        单进程整条渲染 12.2s ｜ 8 分镜 × 8 进程 18.0s ｜ 单进程内连渲 11.3s

    因此改为：把待渲分镜分成 jobs 组，每组交给一个 storyboard.render_worker：
      - **组内共用同一个渲染目录** —— manim 只初始化一次目录结构与 tex 缓存
        （每个分镜一个全新目录要多付 ~0.4s/分镜，8 个分镜就是 3.9s）
      - **整组公式合并成一次 LaTeX 预热** —— 原来是每个分镜各编译一遍
        （8 次 × 0.7s ≈ 5.4s，合并后约 1s）
      - 渲染在同一个进程里依次进行（场景初始化与模块缓存只付一次）
      - 每个分镜渲完立刻把产物搬出共用目录，否则会被下一个分镜覆盖

    jobs 默认 1（最快也最稳）；实测加到 2~4 还有小收益（此时带宽尚未跑满），
    到 8 就转为负收益。产物固定落在 `_parts/<name>/segments/`，与 jobs 无关，
    保证增量缓存的判断路径稳定。

    Returns:
        (ok: bool, elapsed: float)
    """
    parts = renderer.render_split(sb, use_latex=use_latex,
                                  cn_font=args.font, fast=args.fast)
    parts_dir = os.path.join(OUTPUT, "_parts", name)
    # 产物固定放在这里，与 --jobs 无关 —— 增量缓存的判断路径必须稳定
    seg_root = os.path.join(parts_dir, "segments")
    os.makedirs(parts_dir, exist_ok=True)

    all_metas, todo, cached = [], [], 0
    for i, (sid, src) in enumerate(parts):
        stem = f"{name}_s{i}"
        py = os.path.join(parts_dir, f"{stem}.py")
        seg = os.path.join(seg_root, stem, quality_dir, "StoryboardScene.mp4")
        item = (i, sid, py, stem, seg)
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

    jobs = max(1, min(args.jobs or 1, len(todo))) if todo else 1
    # 每组一个渲染目录（组内共用）。共用的两个好处：
    #   ① 整组公式能合并成一次 LaTeX 编译（原来是每个分镜各编译一遍）
    #   ② manim 只需初始化一次目录结构与 tex 缓存（每个分镜一个全新目录要多付 ~0.4s）
    # 组与组之间必须分开，否则多个 worker 会互相覆盖产物。
    render_dirs = [os.path.join(parts_dir, f"media{k}") for k in range(jobs)]

    print(f"[4/4] 分镜级渲染 {len(parts)} 个分镜（-q{args.quality}，{jobs} 个渲染进程"
          + (f"，{cached} 个命中缓存跳过" if cached else "") + ")...")

    # LaTeX 预热：把该组所有分镜的源码合并后**一次性**编译
    if use_latex and not args.no_prewarm:
        for k in range(jobs):
            group = todo[k::jobs]
            if not group:
                continue
            merged = []
            for (i, sid, py, stem, seg) in group:
                with open(py, encoding="utf-8") as f:
                    merged.append(f.read())
            tex_batch.prewarm("\n".join(merged), render_dirs[k])

    sid_of = {i: sid for (i, sid, py, stem, seg) in all_metas}
    worker = os.path.join(HERE, "storyboard", "render_worker.py")
    t1 = time.time()
    failures = []
    procs = []

    # 分组：第 k 组拿 todo[k::jobs]，轮转分配以便把较重的分镜摊开
    for k in range(jobs):
        group = todo[k::jobs]
        if not group:
            continue
        tasks = [[py, render_dirs[k], i, seg]
                 for (i, sid, py, stem, seg) in group]
        procs.append(subprocess.Popen(
            [sys.executable, worker,
             "--tasks", json.dumps(tasks),
             "--quality-dir", quality_dir,
             "--quality", args.quality,
             "--resolution", args.resolution or "",
             "--fps", str(args.fps or 0)],
            # 中文 Windows 下必须显式 encoding/errors，否则 GBK 解码会崩掉读取线程
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", cwd=OUTPUT))

    for p in procs:
        out, err = p.communicate(timeout=1800)
        # worker 每渲完一个分镜就打一行 "@@ {json}" —— 这里逐行解析，
        # 将来接 SSE 时把这一层换成"边读边推"即可，协议不用动。
        for line in (out or "").splitlines():
            if not line.startswith("@@ "):
                continue
            try:
                event = json.loads(line[3:])
            except ValueError:
                continue
            if event.get("i") is not None and not event.get("ok"):
                failures.append((sid_of.get(event["i"], event["i"]),
                                 event.get("error", "")))
        if p.returncode != 0 and not failures:
            failures.append((-1, (err or out or "")[-1200:]))
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
    for (i, sid, py, stem, seg) in all_metas:
        if not os.path.exists(seg):
            print(f"[FAIL] 分镜 {sid} 未产出: {seg}")
            return False, elapsed
        segs.append(seg)

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
    ap.add_argument("--split", action="store_true",
                    help="拆分渲染：每个分镜独立渲染成 mp4，再用 ffmpeg 拼成整条。"
                         "慢于默认路径（拆场景要重复付渲染器初始化），仅用于调试单个分镜")
    ap.add_argument("--jobs", type=int, default=0,
                    help="配合 --split 使用：渲染进程数（默认 1，所有分镜在一个进程内连渲）。"
                         "分成 --jobs 组、每组一个进程串行渲染组内分镜；"
                         "加到 2~4 还有小收益，到 8 转为负收益（内存带宽封顶）")
    ap.add_argument("--fast", action="store_true",
                    help="快速模式：坐标轴刻度数字改用 Text 渲染，省掉大量 LaTeX 编译"
                         "（实测首次渲染 60s→39s，出片时再关掉）")
    ap.add_argument("--no-prewarm", action="store_true",
                    help="关闭 LaTeX 批处理预热（默认开启，首次渲染可省一大半 LaTeX 时间）")
    ap.add_argument("--resolution", default="",
                    help="自定义分辨率，如 1280,720（默认用 -q 的预设）")
    ap.add_argument("--fps", type=int, default=0,
                    help="自定义帧率。实测帧率比分辨率更影响耗时："
                         "720p15 与 480p15 几乎一样快，720p30 才明显变慢")
    ap.add_argument("--font", default="STZhongsong",
                    help="中文字体（STZhongsong=华文中宋 / KaiTi=楷体 / Microsoft YaHei=雅黑）")

    # ---- LLM 接入：题目 → 分镜 JSON（不给就是原来的"手写 JSON"路径）----
    ap.add_argument("--problem", default="",
                    help="直接给题目文本，由大模型生成分镜（不用手写 JSON）")
    ap.add_argument("--problem-file", default="", help="题目文本文件（UTF-8）")
    ap.add_argument("--provider", default="",
                    help=f"LLM 厂商预设: {sorted(llm.PROVIDERS)}")
    ap.add_argument("--model", default="", help="模型名（不给则用厂商预设）")
    ap.add_argument("--base-url", default="",
                    help="自定义 OpenAI 兼容 base_url（必须带版本路径，如 https://xxx/v1）")
    ap.add_argument("--api-key", default="",
                    help="API 密钥（也可用环境变量 MSB_API_KEY 等，或 llm.local.json）")
    ap.add_argument("--attempts", type=int, default=4,
                    help="LLM 最多调用几轮（含校验失败后的带错重试），默认 4")
    ap.add_argument("--temperature", type=float, default=None,
                    help="采样温度，默认 0.2（分镜要稳，别太随机）")
    ap.add_argument("--max-tokens", type=int, default=0,
                    help="单次回复 token 上限，0 = 用默认 8192")
    ap.add_argument("--no-json-mode", action="store_true",
                    help="不要求接口强制 JSON 输出（部分厂商不支持 response_format）")
    ap.add_argument("--rules", default="",
                    help="追加给分镜师的额外要求，如「多用 tracker 做动态演示」")
    ap.add_argument("--name", default="",
                    help="输出文件名前缀（LLM 模式默认按时间戳命名）")
    args = ap.parse_args()

    if args.spec:
        print(dsl.describe())
        return 0

    # 题目来源：--problem / --problem-file。给了就走 LLM 生成分镜。
    problem = args.problem
    if args.problem_file:
        if not os.path.exists(args.problem_file):
            print(f"[FAIL] 找不到题目文件: {args.problem_file}")
            return 1
        with open(args.problem_file, "r", encoding="utf-8") as f:
            problem = f.read()
    problem = (problem or "").strip()

    if not problem and not args.storyboard:
        ap.error('需要提供分镜 JSON 路径，或用 --problem "题目" 让大模型生成'
                 "（--spec 查看 DSL 规格）")

    # ---------- 1. 输入：题目 → LLM 生成 / 直接读手写分镜 ----------
    name = ""
    if problem:
        # 1a. LLM 路径。生成 + 校验 + 失败带错重试全在 llm.py 里闭环，
        #     这里拿到的已经是"过了校验"的分镜。
        try:
            cfg = llm.resolve_config(
                provider=args.provider, model=args.model, base_url=args.base_url,
                api_key=args.api_key, temperature=args.temperature,
                max_tokens=args.max_tokens or None)
        except llm.LLMError as e:
            print(f"[FAIL] LLM 配置有误:\n{e}")
            return 4

        preview = problem[:60] + ("..." if len(problem) > 60 else "")
        print(f"[1/4] 题目 → 分镜（{cfg['provider']} / {cfg['model']}）: {preview}")
        t_llm = time.time()
        try:
            res = llm.generate_storyboard(
                problem, cfg, templates.TEMPLATE_REGISTRY,
                max_attempts=args.attempts, json_mode=not args.no_json_mode,
                extra_rules=args.rules)
        except llm.LLMError as e:
            print(f"[FAIL] 分镜生成失败:\n{e}")
            return 4

        n_bad = sum(1 for h in res["history"] if not h["ok"])
        print(f"      生成完成: {time.time() - t_llm:.1f}s，{res['attempts']} 轮调用"
              + (f"（其中 {n_bad} 轮没通过校验、带错重试后修正）" if n_bad else ""))

        # 把模型原始输出落盘 ——「分镜可编辑」是本项目的产品价值：
        # 留一份人能看懂、能手改、能重跑的 JSON，改完当普通分镜跑即可。
        name = args.name or time.strftime("llm_%m%d_%H%M%S")
        os.makedirs(OUTPUT, exist_ok=True)
        path = os.path.join(OUTPUT, f"{name}_storyboard.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(res["raw"], f, ensure_ascii=False, indent=2)
        print(f"      分镜已保存: {path}")
        print(f'      （要改画面就手改这份 JSON，再 python generate.py "{path}" 重跑）')
        raw = res["raw"]
    else:
        # 1b. 手写 JSON 路径
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
    # 两条输入路径都在这里汇合，好处是"校验 → 报告被丢弃的参数 → 渲染"只有一份代码。
    # LLM 路径其实在 llm.generate_storyboard() 内部已经校验过一遍（不过就不放行），
    # 这里对同一份原始 JSON 再走一次是幂等的，不重复花钱，只是让下游逻辑统一。
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

    name = name or os.path.splitext(os.path.basename(path))[0]
    py_path = os.path.join(OUTPUT, f"{name}_scene.py")

    # --split：走"拆分渲染 + ffmpeg 拼接"老路（每个分镜独立渲染）。
    # 默认不走它 —— 见 README「分镜级渲染」：拆场景会重复付渲染器初始化，
    # 比"一个 Scene 演到底 + manim 原生分段"慢 20%+。
    if args.split:
        renderer.render_to_file(sb, py_path, use_latex=use_latex,
                                cn_font=args.font, fast=args.fast)
    else:
        renderer.render_to_file(sb, py_path, use_latex=use_latex,
                                cn_font=args.font, fast=args.fast,
                                sections=len(sb["scenes"]) > 1)

    n_lines = sum(1 for _ in open(py_path, encoding="utf-8"))
    print(f"[3/4] 生成 Manim 代码: {py_path}  ({n_lines} 行, LaTeX={use_latex})")

    if args.dry_run:
        print("\n[dry-run] 生成的代码：\n" + "-" * 60)
        print(open(py_path, encoding="utf-8").read())
        return 0

    # ---------- 3.5 预热 LaTeX 缓存 ----------
    # 把源码里所有公式一次性编译掉，省下「每个公式各跑一遍 latex」的固定开销。
    # 实测 24 个公式：逐个编译 18.5s → 批处理 0.9s。
    if use_latex and not args.no_prewarm:
        with open(py_path, encoding="utf-8") as f:
            tex_batch.prewarm(f.read(), OUTPUT)

    # ---------- 4. 渲染 ----------
    os.makedirs(OUTPUT, exist_ok=True)
    quality_dir = quality_tag(args)

    if args.split and len(sb["scenes"]) > 1:
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

    save_sections = not args.split and len(sb["scenes"]) > 1
    if save_sections:
        print(f"[4/4] 渲染中（-q{args.quality}，每个分镜渲完即出片）...")
    else:
        print(f"[4/4] 渲染中（-q{args.quality}）...")
    t1 = time.time()
    # ⚠️ 这里不能用 subprocess.run(capture_output=True) —— 它要等进程结束才返回，
    #    拿不到"渲好一段"的实时时机。必须 Popen + 逐行读 stdout。
    # encoding / errors 也要显式指定：中文 Windows 的 locale 是 GBK，
    #    而 manim 输出是 UTF-8，不指定会 UnicodeDecodeError 崩掉读取线程，
    #    主流程卡死在 read 上（表面上像"渲染卡住"）。
    proc = subprocess.Popen(
        [sys.executable, "-m", "manim", "render"]
        + quality_args(args)
        + ["--disable_caching",
           "--media_dir", OUTPUT, py_path, "StoryboardScene"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", cwd=OUTPUT, bufsize=1)

    section_events = []
    section_t0 = t1
    for line in proc.stdout:
        stripped = line.rstrip("\n")
        if stripped.startswith("@@ "):
            try:
                evt = json.loads(stripped[3:])
            except ValueError:
                continue
            if evt.get("section"):
                section_events.append(evt)
                mark = time.time()
                print(f"      [分镜 {evt['section']}] 已完成 "
                      f"{evt.get('duration', 0)}s  →  {os.path.basename(evt.get('video', ''))}"
                      f"  （累计 {mark - section_t0:.1f}s）")
            continue
        # manim 自己的日志：只保留失败相关的，避免刷屏
        if "error" in stripped.lower() or "Traceback" in stripped:
            print("      " + stripped)

    try:
        proc.wait(timeout=900)
    except subprocess.TimeoutExpired:
        proc.kill()
        print("[FAIL] 渲染超时（>900 秒）")
        return 3
    elapsed = time.time() - t1
    r = proc  # 下面统一用 returncode / stdout

    if r.returncode != 0:
        print(f"[FAIL] 渲染失败（{elapsed:.1f}s）")
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

    # 分镜片段：渲染过程中每渲完一段就被场景自己合并导出（见 renderer.HEADER 的
    # `section()`），这里只是把结果汇总展示，顺带把索引写到 sections/index.json，
    # 供后端 / 前端直接消费（真实时长比大纲估算值准）。
    if save_sections and section_events:
        sections_dir = os.path.join(OUTPUT, "videos", f"{name}_scene",
                                    quality_dir, "sections")
        index = os.path.join(sections_dir, "index.json")
        index_data = [
            {"index": int(e["section"]) - 1,
             "video": os.path.basename(e["video"]),
             "duration": e.get("duration", 0.0)}
            for e in section_events
        ]
        with open(index, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
        print(f"分镜片段: {len(index_data)} 段（渲染中实时交付）→ {sections_dir}")

    total = time.time() - t0
    print(f"总耗时: {total:.1f}s（含校验+生成+渲染）")
    print(f"分镜数: {len(sb['scenes'])}  平均每分镜: {elapsed / max(len(sb['scenes']), 1):.1f}s")

    if not args.keep_code:
        pass  # 保留 .py 便于调试，默认不删

    return 0


if __name__ == "__main__":
    sys.exit(main())

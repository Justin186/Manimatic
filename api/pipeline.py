# -*- coding: utf-8 -*-
"""
渲染管线 → SSE 事件流（本服务层的核心工作量）。

================================================================================
后端要做的转换
================================================================================
子进程 stdout 里每渲完一段会打一行：

    @@ {"section": 1, "video": ".../sections/StoryboardScene_0000_scene_1.mp4", "duration": 5.0}

这里把它转成：

    event: tool_result
    data: {"index":0,"url":"http://localhost:8000/media/.../s1.mp4","durationSec":5.0}

就这一件事，但有两个坑必须按原文照抄（generate.py 里已经踩过）：

  ⚠️ 必须 `Popen` **逐行读** stdout，不能用 `subprocess.run(capture_output=True)` ——
     后者要等进程结束才返回，拿不到"渲好一段"的实时时机，"渲好一段播一段"就没了。
  ⚠️ 必须显式 `encoding="utf-8", errors="replace"` —— 中文 Windows 的 locale 是 GBK，
     而 manim 输出是 UTF-8，不指定会 UnicodeDecodeError 崩掉读取线程、主流程卡死在 read 上，
     表面上看起来就像"渲染卡住了"。

================================================================================
两种模式（都是串行 1→N 交付，前端不用区分）
================================================================================
  full        默认路径：一个 Scene 演到底 + 边渲边切。最快（13.7s）、首段 2.3s 可见。
              → 首次生成走这条（/api/render/confirm）
  incremental --split 路径 + `_parts` 增量缓存：源码没变的分镜直接命中旧片段。
              比 full 慢 2~3s，但"只改了一个分镜"时只需要重渲那一个。
              → 重试 / 整分镜替换走这条（/api/render/retry、/api/storyboard/replace-scene）

⚠️ 不要为了"并行"去拆更多进程：实测瓶颈是内存带宽（单进程 35 GB/s，8 进程并行时
   每个只剩 7.2 GB/s，整机约 58 GB/s 封顶），20 个核帮不上忙。详见 HANDOFF §5.2。
"""

import asyncio
import collections
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback

from storyboard import dsl, latex_env, renderer, schema, templates, tex_batch

from . import config, events, store

HERE = config.ROOT
OUTPUT = config.OUTPUT_DIR

_SENTINEL = object()


def _log(msg):
    print(f"[api] {msg}", flush=True)


class Canceller:
    """
    取消令牌 + "正在跑的子进程"登记处。

    为什么要登记进程、而不是只 set 一个标志：客户端断开时上层只知道"该停了"，
    但 `for line in proc.stdout` 正阻塞在 readline 上，而且**一个分镜可能要渲十几秒**——
    等下一次读到行才看到标志，等于白发十几秒 CPU，还占着并发额度不让排队的人进来。
    把进程登记进来，cancel() 就能立刻 kill 它，readline 随即返回 EOF。
    """

    def __init__(self):
        self.event = threading.Event()
        self._procs = set()
        self._lock = threading.Lock()

    def is_set(self):
        return self.event.is_set()

    def register(self, proc):
        """登记一个子进程；若此前已被取消，就地 kill 并返回 False。"""
        with self._lock:
            if self.event.is_set():
                try:
                    proc.kill()
                except Exception:                               # noqa: BLE001
                    pass
                return False
            self._procs.add(proc)
            return True

    def unregister(self, proc):
        with self._lock:
            self._procs.discard(proc)

    def cancel(self):
        self.event.set()
        with self._lock:
            procs = list(self._procs)
        for p in procs:
            try:
                p.kill()
            except Exception:                                   # noqa: BLE001
                pass


def new_cancel():
    return Canceller()


# ==============================================================================
# 小工具
# ==============================================================================

def quality_dir_name():
    """
    manim 的产物目录名。实现已移到 config（见那里的注释：store 读产物也要用，
    而 store 不能反向 import pipeline）。这里只留一个转发，免得改一堆调用点。
    """
    return config.quality_dir_name()


def _quality_args():
    out = [f"-q{config.QUALITY}"] if not config.RESOLUTION else ["--resolution", config.RESOLUTION]
    if config.FPS:
        out += ["--fps", str(config.FPS)]
    return out


def _use_latex():
    """LaTeX 不可用就降级为纯文本 —— 公式退化，但整条链不能崩掉。"""
    if not config.USE_LATEX:
        return False
    info = latex_env.detect()
    if not info["available"]:
        _log(f"LaTeX 不可用（{info['reason']}），公式降级为纯文本")
        return False
    return True


def _probe_duration(mp4):
    """
    量真实时长（秒）。

    为什么不用大纲里的 durationSec：那是设计值，和 manim 按真实帧数算出来的差不少。
    前端整片时间轴、进度条、分镜定位全用这个值，用错就会漂。
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", mp4],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return round(float((r.stdout or "").strip()), 3)
    except Exception:
        return 0.0


def _section_rel(name, qdir, index):
    """唯一实现在 store（读产物那一侧也要用，而 store 不能反向 import 本模块）。"""
    return store.section_rel(name, qdir, index)


def _final_rel(name, qdir):
    """同上：写产物与读产物共用 store 里那一份命名。"""
    return store.final_rel(name, qdir)


def _tail_error(tail):
    """从 manim 的日志尾巴里挑出最有用的几行（报错信息通常在最末 40 行内）。"""
    lines = [ln.rstrip() for ln in tail if ln.strip()]
    if not lines:
        return ""
    keep = [ln for ln in lines if ("Error" in ln or "error" in ln or "Traceback" in ln
                                   or "Exception" in ln)]
    picked = (keep or lines)[-6:]
    return "\n".join(picked)[:800]


def write_index(name, qdir, n, durations):
    """
    写 sections/index.json —— 前端时间轴的数据源。

    结构： [{"index":0,"video":"StoryboardScene_0000_scene_1.mp4","duration":5.0}, ...]
    """
    sec = store.sections_dir(name, qdir)
    data = []
    for i in range(n):
        fname = store.section_filename(i)
        dur = durations.get(i)
        if dur is None:
            dur = _probe_duration(os.path.join(sec, fname))
            durations[i] = dur
        data.append({"index": i, "video": fname, "duration": dur})
    with open(os.path.join(sec, "index.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def _concat(name, qdir, n):
    """把 N 段拼成整条成片（ffmpeg concat；失败时退回重编码）。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _log("未找到 ffmpeg，无法拼接分段")
        return False
    pdir = store.parts_dir(name)
    os.makedirs(pdir, exist_ok=True)
    lst = os.path.join(pdir, "concat.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for i in range(n):
            p = os.path.join(store.sections_dir(name, qdir),
                             store.section_filename(i)).replace("\\", "/")
            f.write(f"file '{p}'\n")
    final = os.path.join(store.video_dir(name, qdir), "StoryboardScene.mp4")
    os.makedirs(os.path.dirname(final), exist_ok=True)
    base = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", lst]
    r = subprocess.run(base + ["-c", "copy", final], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.exists(final):
        # 各段编码参数不完全一致时 -c copy 会失败，退回重新编码（慢但一定能成）
        r = subprocess.run(base + ["-c:v", "libx264", "-pix_fmt", "yuv420p", final],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    return r.returncode == 0 and os.path.exists(final)


def _validate(raw):
    """唯一的闸口：校验失败在这里抛，绝不带到渲染阶段。"""
    return schema.validate(raw, templates.TEMPLATE_REGISTRY)


# ==============================================================================
# 模式一：full —— 一个 Scene 演到底 + 边渲边切（默认、最快）
# ==============================================================================

def iter_full(name, raw, cancel=None):
    """
    产出 (event, data) 元组序列。调用方负责编码成 SSE。

    事件顺序（与 Mock 完全一致）：
        tool_progress(step=0, stage=prewarm)         ← 仅当真的会预热 LaTeX
        tool_progress(step=0, stage=rendering)
        tool_result(0) → tool_progress(1) → tool_result(1) → ...
        tool_progress(n, stage=concat)
        tool_done
    """
    # 兜底也必须是 Canceller（路由都传它）：它才带 register/unregister，
    # 传个裸 Event 进来的话下面 cancel.register(proc) 会 AttributeError。
    cancel = cancel or new_cancel()

    try:
        sb = _validate(raw)
    except (schema.SchemaError, dsl.DSLError) as e:
        yield events.error(f"分镜校验失败：{e}", scope="task")
        return

    scenes = sb["scenes"]
    n = len(scenes)
    if n == 0:
        yield events.error("这份分镜里没有任何分镜（intent=none 的纯文字回答不该走到渲染）",
                           scope="task")
        return

    qdir = quality_dir_name()
    use_latex = _use_latex()
    py = os.path.join(OUTPUT, f"{name}_scene.py")

    # 只有一个分镜时不开分段：分段只是多付一次合并开销，而"渲好一段播一段"没有意义。
    try:
        src = renderer.render(sb, use_latex=use_latex, cn_font=config.CN_FONT,
                              latin_font=config.LATIN_FONT,
                              fast=config.FAST, sections=n > 1)
    except SyntaxError as e:
        # 生成的代码语法错 = 渲染器的 bug，不是分镜的问题（闸 4）
        yield events.error(f"生成的 Manim 代码有语法错误（渲染器 bug）：{e}", scope="task")
        return

    os.makedirs(OUTPUT, exist_ok=True)
    with open(py, "w", encoding="utf-8") as f:
        f.write(src)

    if use_latex and config.PREWARM:
        # 预热是**一次性**的（把整条里所有公式合并成一次 latex 编译），
        # 所以只报一次 prewarm，不要逐分镜循环 —— 那会让人以为每个分镜都在重复预热。
        yield events.tool_progress(0, n, "prewarm")
        tex_batch.prewarm(src, OUTPUT, verbose=False)

    yield events.tool_progress(0, n, "rendering")

    proc = subprocess.Popen(
        [sys.executable, "-m", "manim", "render"] + _quality_args() +
        ["--disable_caching", "--media_dir", OUTPUT, py, "StoryboardScene"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", cwd=OUTPUT, bufsize=1)

    tail = collections.deque(maxlen=40)
    durations = {}
    delivered = 0
    rc = None
    if not cancel.register(proc):
        _log(f"{name}: 请求已取消，未开始渲染")
        return
    try:
        for line in proc.stdout:
            if cancel.is_set():
                proc.kill()
                _log(f"{name}: 客户端断开，已终止渲染")
                return
            s = line.rstrip("\n")
            if s.startswith("@@ "):
                try:
                    evt = json.loads(s[3:])
                except ValueError:
                    continue
                sec = int(evt.get("section") or 0)
                if sec <= 0:
                    continue
                idx = sec - 1
                dur = float(evt.get("duration") or 0.0)
                durations[idx] = round(dur, 3)
                # durationSec 必须来自这里（manim 按真实帧数算的），不能用大纲估算值
                yield events.tool_result(idx, store.media_url(_section_rel(name, qdir, idx)), dur)
                delivered = sec
                if sec < n:
                    yield events.tool_progress(sec, n, "rendering")
                continue
            tail.append(s)
        try:
            rc = proc.wait(timeout=config.RENDER_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            yield events.error(f"渲染超时（>{config.RENDER_TIMEOUT}s）", scope="task")
            return
    finally:
        cancel.unregister(proc)
        if proc.poll() is None:
            proc.kill()

    if rc != 0:
        # 串行模型下"一个 Scene 演到底"，任一环节抛异常就是整条失败 ——
        # 不存在"只重跑第 3 段"。但失败可以定位：已经交付到第几段，就是哪一段出的问题。
        idx = max(0, min(delivered, n - 1))
        yield events.error(_tail_error(tail) or f"manim 退出码 {rc}",
                           scope="step", index=idx, retryable=True)
        return

    final = os.path.join(store.video_dir(name, qdir), "StoryboardScene.mp4")
    if not os.path.exists(final):
        yield events.error(f"渲染结束但没找到成片：{final}", scope="task")
        return

    if n == 1:
        # 单分镜时没开 sections，于是没有任何 @@ 事件：这里补一次交付，
        # 并把片段搬进 sections/ —— 对外产物形状保持一致，前端不用为 N=1 走特例。
        sec = store.sections_dir(name, qdir)
        os.makedirs(sec, exist_ok=True)
        dst = os.path.join(sec, store.section_filename(0))
        shutil.copyfile(final, dst)
        dur = durations.get(0) or _probe_duration(dst)
        durations[0] = dur
        yield events.tool_result(0, store.media_url(_section_rel(name, qdir, 0)), dur)

    write_index(name, qdir, n, durations)
    yield events.tool_progress(n, n, "concat")
    yield events.tool_done(store.media_url(_final_rel(name, qdir)), n)


# ==============================================================================
# 模式二：incremental —— 复用 task 目录，只重渲源码变了的那些分镜
# ==============================================================================

def iter_incremental(name, raw, emit_indices, cancel=None):
    """
    增量渲染。`_parts/<name>/segments/` 里源码未变的分镜会直接命中缓存（跳过渲染）。

    Args:
        emit_indices: 需要**向前端交付**的分镜序号集合。
            retry 场景传 range(from, n)（从失败那段起全部重新交付）
            replace-scene 传 [index]（只交付改过的那一段）
            不在集合里的分段仍会被拼进成片，只是不推事件 —— 前端那边它们本来就是 done。
    """
    cancel = cancel or new_cancel()
    emit_set = set(int(i) for i in emit_indices)

    try:
        sb = _validate(raw)
    except (schema.SchemaError, dsl.DSLError) as e:
        yield events.error(f"分镜校验失败：{e}", scope="task")
        return

    scenes = sb["scenes"]
    n = len(scenes)
    if n == 0:
        yield events.error("这份分镜里没有任何分镜", scope="task")
        return

    qdir = quality_dir_name()
    use_latex = _use_latex()

    try:
        parts = renderer.render_split(sb, use_latex=use_latex, cn_font=config.CN_FONT,
                                      latin_font=config.LATIN_FONT,
                                      fast=config.FAST)
    except SyntaxError as e:
        yield events.error(f"生成的 Manim 代码有语法错误（渲染器 bug）：{e}", scope="task")
        return

    pdir = store.parts_dir(name)
    seg_root = os.path.join(pdir, "segments")
    sec_dir = store.sections_dir(name, qdir)
    os.makedirs(seg_root, exist_ok=True)
    os.makedirs(sec_dir, exist_ok=True)

    def seg_path(i):
        return os.path.join(seg_root, f"{name}_s{i}", qdir, "StoryboardScene.mp4")

    todo, cached = [], []
    for i, (sid, src) in enumerate(parts):
        scene_py = os.path.join(pdir, f"{name}_s{i}.py")
        seg = seg_path(i)
        # 增量判据（与 generate.py::render_parallel 完全一致）：
        # 源码没变 **且** 片段还在 → 跳过。修正时复用同一个 name 就是为了这个。
        same_src = (os.path.exists(scene_py)
                    and open(scene_py, encoding="utf-8").read() == src)
        if same_src and os.path.exists(seg):
            cached.append((i, seg))
            continue
        with open(scene_py, "w", encoding="utf-8") as f:
            f.write(src)
        todo.append((i, scene_py, seg))

    _log(f"{name}: 增量渲染 —— 待渲 {len(todo)} 段，缓存命中 {len(cached)} 段")
    if not todo:
        _log(f"{name}: 全部分镜命中缓存，只需重新拼接")

    render_dir = os.path.join(pdir, "media0")
    if use_latex and config.PREWARM and todo:
        merged = []
        for _, scene_py, _ in todo:
            with open(scene_py, encoding="utf-8") as f:
                merged.append(f.read())
        # 预热必须写进 worker 真正使用的 media 目录，否则缓存对不上、白预热
        tex_batch.prewarm("\n".join(merged), render_dir, verbose=False)

    start = min(emit_set) if emit_set else 0
    yield events.tool_progress(start, n, "rendering")

    # 命中缓存、但**需要交付**的段落先发出去。
    # 典型场景：重试时那一段的源码其实没变（上次失败是环境问题，比如 LaTeX 不在 PATH），
    # 于是它命中缓存 —— 但前端那一段还标着错误态，不补一条 tool_result 就永远修不回来
    # （done 事件会看到 hasError 而把整条渲染判成失败）。
    for i, seg in sorted(cached):
        if i not in emit_set:
            continue
        dst = os.path.join(sec_dir, store.section_filename(i))
        if not os.path.exists(dst):
            shutil.copyfile(seg, dst)
        dur = _probe_duration(dst)
        yield events.tool_result(i, store.media_url(_section_rel(name, qdir, i)), dur)
        if i + 1 in emit_set:
            yield events.tool_progress(i + 1, n, "rendering")

    if todo:
        tasks = [[scene_py, render_dir, i, seg] for (i, scene_py, seg) in todo]
        worker = os.path.join(HERE, "storyboard", "render_worker.py")
        proc = subprocess.Popen(
            [sys.executable, worker, "--tasks", json.dumps(tasks),
             "--quality-dir", qdir, "--quality", config.QUALITY,
             "--resolution", config.RESOLUTION or "", "--fps", str(config.FPS or 0)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", cwd=OUTPUT, bufsize=1)
        tail = collections.deque(maxlen=40)
        failed = None
        rc = None
        if not cancel.register(proc):
            _log(f"{name}: 请求已取消，未开始渲染")
            return
        try:
            for line in proc.stdout:
                if cancel.is_set():
                    proc.kill()
                    _log(f"{name}: 客户端断开，已终止渲染")
                    return
                s = line.rstrip("\n")
                if not s.startswith("@@ "):
                    tail.append(s)
                    continue
                try:
                    evt = json.loads(s[3:])
                except ValueError:
                    continue
                if evt.get("worker_done"):
                    continue
                i = evt.get("i")
                if i is None:
                    continue
                if not evt.get("ok"):
                    failed = (int(i), evt.get("error") or "未知错误")
                    yield events.error(f"分镜 {int(i) + 1} 渲染失败：{failed[1]}",
                                       scope="step", index=int(i), retryable=True)
                    break
                # worker 单进程串行，事件天然按 index 递增 —— 渲好一段就能立刻交付，
                # 不用等整条渲完（这就是"复用缓存"不该牺牲"逐段可见"的原因）。
                src_seg = seg_path(int(i))
                if os.path.exists(src_seg):
                    shutil.copyfile(src_seg, os.path.join(sec_dir, store.section_filename(int(i))))
                if int(i) in emit_set:
                    dst = os.path.join(sec_dir, store.section_filename(int(i)))
                    dur = _probe_duration(dst)
                    yield events.tool_result(int(i),
                                             store.media_url(_section_rel(name, qdir, int(i))), dur)
                    if int(i) + 1 in emit_set:
                        yield events.tool_progress(int(i) + 1, n, "rendering")
            try:
                rc = proc.wait(timeout=config.RENDER_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                yield events.error(f"渲染超时（>{config.RENDER_TIMEOUT}s）", scope="task")
                return
        finally:
            cancel.unregister(proc)
            if proc.poll() is None:
                proc.kill()
        if failed:
            return
        if rc != 0:
            yield events.error(_tail_error(tail) or f"渲染进程退出码 {rc}",
                               scope="step", index=max(0, n - 1), retryable=True)
            return

    # 命中缓存的段落也要搬进对外目录（拼接要用它们）
    for i, seg in cached:
        dst = os.path.join(sec_dir, store.section_filename(i))
        if not os.path.exists(dst):
            shutil.copyfile(seg, dst)

    missing = [i for i in range(n)
               if not os.path.exists(os.path.join(sec_dir, store.section_filename(i)))]
    if missing:
        yield events.error(f"缺少分段文件（分镜 {[i + 1 for i in missing]}），无法拼接成片",
                           scope="task")
        return

    if not _concat(name, qdir, n):
        yield events.error("ffmpeg 拼接失败（分段本身已生成，可手动拼接）", scope="task")
        return

    write_index(name, qdir, n, {})
    yield events.tool_progress(n, n, "concat")
    yield events.tool_done(store.media_url(_final_rel(name, qdir)), n)


# ==============================================================================
# 把阻塞式事件生产者搬到线程里
# ==============================================================================

def pump(gen, push):
    """把 (event, data) 生成器接到 push 上（省得每个路由都写一遍 for 循环）。"""
    for ev in gen:
        push(*ev)


async def stream(run, cancel):
    """
    阻塞式事件生产者 → 异步生成器。

    Args:
        run(push): 在工作线程里执行的函数。`push(event_name, data)` 线程安全，
            回调里也能直接调用（LLM 的 on_delta 就是这么把文字推出来的）。
        cancel: Canceller。客户端断开时 cancel() 会被调用，
            它登记的子进程随之被 kill（不然渲染白跑十几秒还占着并发额度）。

    为什么要把这些事情搬到线程里：渲染与 LLM 调用都是**阻塞**的（Popen 逐行读、
    tex_batch 起 latex、urllib 等响应），直接在事件循环里跑会卡死整个服务
    （所有请求一起等）。搬到线程 + asyncio.Queue，就既保持了同步的写法，
    又不阻塞事件循环。

    客户端中途断开时 Starlette 会关掉这个生成器 → finally 里 cancel()
    → 生产者线程的子进程立刻被杀掉，循环随之退出。
    """
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def push(event, data):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, (event, data))
        except RuntimeError:
            pass                      # 事件循环已关（连接断了），丢掉即可

    def body():
        try:
            run(push)
        except Exception as e:        # 兜底：任何异常都要变成一条 error，不能让流无声无息断掉
            traceback.print_exc()
            push(*events.error(f"服务端异常：{type(e).__name__}: {e}", scope="task"))
        finally:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)
            except RuntimeError:
                pass

    threading.Thread(target=body, daemon=True, name="msb-producer").start()
    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            yield item
    finally:
        cancel.cancel()

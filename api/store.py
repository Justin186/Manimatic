# -*- coding: utf-8 -*-
"""
task 目录 / 产物路径管理。

================================================================================
"复用同一个 name" = 白捡的增量渲染
================================================================================
`renderer.render_split()` 那套缓存判据是「源码没变 且 片段已存在」：

    same_src = os.path.exists(py) and open(py).read() == src
    if same_src and os.path.exists(seg): 跳过

只要**修正时复用同一个 name**，`output/_parts/<name>/` 里的旧片段还在，
没改动的分镜就自动跳过渲染。于是"用户说把第三步讲慢一点 → 只重渲第三步"
这个能力不需要额外实现，只要保证 name 稳定（见 task_name()）。

================================================================================
目录布局
================================================================================
    output/_tasks/threads/<thread>.json     ← 该会话"当前这一版"分镜（confirm 靠它找分镜）
    output/_tasks/<name>/storyboard.json    ← 某次渲染实际用的分镜快照（retry 靠它）
    output/_tasks/<name>/meta.json          ← 创建/更新时间、渲染版本号
    output/_parts/<name>/segments/...       ← 增量缓存（render_worker 的产物）
    output/videos/<name>_scene/<quality>/…  ← 对外的成片与分段（/media 挂的就是它）
"""

import json
import os
import re
import shutil
import time

from . import config

# thread_id / message_id 由前端生成（如 `demo`、`ma_1a2b3c`），只做保守清洗：
# 允许字母数字与 - _，其余换成 _。这样拼出来的路径不可能跑出 output/ 之外。
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def safe_id(s, default="anon", limit=48):
    s = _SAFE.sub("_", str(s or ""))[:limit]
    return s or default


def task_name(thread_id, message_id):
    """
    task 目录名。**必须稳定**（同一个 thread+message 永远是同一个名字），
    否则 _parts 缓存每次都落空，增量渲染直接失效。
    """
    return f"{safe_id(thread_id, 't')}__{safe_id(message_id, 'm')}"


# ---------------- 路径 ----------------

def tasks_root():
    return os.path.join(config.OUTPUT_DIR, "_tasks")


def task_dir(name):
    return os.path.join(tasks_root(), name)


def thread_file(thread_id):
    return os.path.join(tasks_root(), "threads", safe_id(thread_id, "t") + ".json")


def parts_dir(name):
    """增量缓存目录（与 generate.py --split 用的完全同一处，所以要共用命名）。"""
    return os.path.join(config.OUTPUT_DIR, "_parts", name)


def video_dir(name, quality_dir):
    """整条成片所在目录。也是 /media 静态服务的根之下的相对路径起点。"""
    return os.path.join(config.OUTPUT_DIR, "videos", f"{name}_scene", quality_dir)


def sections_dir(name, quality_dir):
    return os.path.join(video_dir(name, quality_dir), "sections")


def section_filename(index):
    """与 manim 自己的 section 命名保持一致（StoryboardScene_0000_scene_1.mp4）。"""
    return f"StoryboardScene_{int(index):04d}_scene_{int(index) + 1}.mp4"


# ---------------- URL ----------------

def media_url(rel_path):
    """
    产物 → 对前端可用的**绝对** URL。

    两个细节都不能省：
      1. 绝对地址：前端把 url 直接塞 <video src>，相对路径会打到 3000 端口（Next.js）
         而不是后端 8000，表现为"渲染成功了但视频 404"。
      2. `?v=<mtime>`：重渲后路径完全相同（这正是增量渲染想要的效果），
         不加版本号浏览器会继续放缓存里的旧视频 —— 用户会以为"改了没生效"。
    """
    rel = str(rel_path).replace("\\", "/").lstrip("/")
    path = os.path.join(config.OUTPUT_DIR, "videos", rel)
    try:
        stamp = int(os.path.getmtime(path))
    except OSError:
        stamp = int(time.time())
    return f"{config.PUBLIC_BASE_URL}/media/{rel}?v={stamp}"


# ---------------- 读写（原子写，避免读到半个文件）----------------

def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---------------- 会话级：当前这一版分镜 ----------------

def save_thread_storyboard(thread_id, raw_storyboard, plan=None, task=None):
    """
    记下"这个会话当前这一版分镜"。

    为什么必须有这一层：前端在 confirm 时**只发 thread_id + message_id**，不重复发分镜本体
    （见 web/src/lib/api.ts 的 ConfirmRequest.storyboard 是可选的，而 Workbench 没传）。
    所以后端得自己记住上一步 /api/chat 生成的分镜，否则 confirm 无从渲起。
    """
    _write_json(thread_file(thread_id), {
        "storyboard": raw_storyboard,
        "plan": plan or [],
        "task": task or "",
        "updated_at": time.time(),
    })


def load_thread_storyboard(thread_id):
    data = _read_json(thread_file(thread_id)) or {}
    return data.get("storyboard"), data.get("plan") or []


# ---------------- 会话级：对话历史（多轮上下文的原料）----------------
#
# 为什么不塞进 thread_file：那个文件回答的是"前端点 confirm 时该渲哪一份分镜"，
# 和"模型该看到哪些历史"是两件事。混在一起的结果是 —— 清分镜会把对话一起清掉，
# 或者反过来"重生成"把历史写坏。主流做法就是各存各的。
#
# 存的是**原始 user/assistant 文本**（不是"用户问题 + brief"那种压缩版）：
# 追问里经常出现"刚才那个第二步"，压缩掉上下文后再喂回模型就没法指代了。
# 长度控制交给"喂给模型时"的裁剪（见 llm.trim_history），存的时候不丢信息。

def thread_messages_file(thread_id):
    return os.path.join(tasks_root(), "messages", safe_id(thread_id, "t") + ".json")


def load_thread_messages(thread_id):
    data = _read_json(thread_messages_file(thread_id)) or {}
    msgs = data.get("messages")
    return msgs if isinstance(msgs, list) else []


def append_thread_message(thread_id, role, content, meta=None):
    """
    追加一轮对话，返回完整列表。

    ⚠️ 只记**user 和 assistant 的自然语言**，不记分镜 JSON。
    分镜是机器产物、动辄几千 token，塞进历史会：① 把上下文预算吃光；
    ② 让前缀缓存每次都不命中（主流厂商按 token 计费，缓存命中便宜一大截）。
    模型要分镜时会重新生成，不需要从历史里读。
    """
    text = str(content or "").strip()
    if not text:
        return load_thread_messages(thread_id)
    path = thread_messages_file(thread_id)
    data = _read_json(path) or {}
    msgs = data.get("messages")
    msgs = msgs if isinstance(msgs, list) else []
    entry = {"role": role, "content": text, "at": time.time()}
    if meta:
        entry.update(meta)
    msgs.append(entry)
    _write_json(path, {"messages": msgs, "updated_at": time.time()})
    return msgs


# ---------------- 任务级：某次渲染的分镜快照 ----------------

def save_task_storyboard(name, raw_storyboard, meta=None):
    d = task_dir(name)
    os.makedirs(d, exist_ok=True)
    _write_json(os.path.join(d, "storyboard.json"), raw_storyboard)
    old = _read_json(os.path.join(d, "meta.json")) or {}
    m = {
        "name": name,
        "created_at": old.get("created_at") or time.time(),
        "updated_at": time.time(),
        # 渲染版本号：每次重渲 +1。前端不用它，但排查"这份 mp4 是哪一版"时很省事。
        "render_version": int(old.get("render_version") or 0) + 1,
    }
    m.update(meta or {})
    _write_json(os.path.join(d, "meta.json"), m)
    return m


def load_task_storyboard(name):
    return _read_json(os.path.join(task_dir(name), "storyboard.json"))


def load_task_meta(name):
    return _read_json(os.path.join(task_dir(name), "meta.json")) or {}


# ---------------- 清理 ----------------

def cleanup_stale(days=None):
    """
    删掉超过 N 天没动过的 task 目录与它的产物/缓存。

    ⚠️ 只删**我们自己建过的东西**（以 output/_tasks/ 下的目录名为准），
       绝不去扫 output/videos 反推 —— output/ 里还有 examples 的历史产物和
       benchmark 目录（_bench_* / _prof / _spd），无差别清理会误伤。
    """
    days = config.TASK_TTL_DAYS if days is None else days
    if not days or days <= 0:
        return {"removed": [], "skipped": "cleanup disabled"}
    cutoff = time.time() - days * 86400
    root = tasks_root()
    removed = []
    if not os.path.isdir(root):
        return {"removed": []}
    for entry in os.listdir(root):
        full = os.path.join(root, entry)
        if entry == "threads" or not os.path.isdir(full):
            continue
        try:
            if os.path.getmtime(full) >= cutoff:
                continue
        except OSError:
            continue
        targets = [full, parts_dir(entry),
                   os.path.join(config.OUTPUT_DIR, "videos", f"{entry}_scene")]
        for t in targets:
            if os.path.isdir(t):
                shutil.rmtree(t, ignore_errors=True)
        removed.append(entry)
    # 会话文件单独按时间戳清（分镜 + 对话历史两个目录同一套规矩）
    for sub in ("threads", "messages"):
        tdir = os.path.join(root, sub)
        if not os.path.isdir(tdir):
            continue
        for f in os.listdir(tdir):
            fp = os.path.join(tdir, f)
            try:
                if os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
                    removed.append(f"{sub}/{f}")
            except OSError:
                pass
    return {"removed": removed, "days": days}

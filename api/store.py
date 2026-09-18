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
    """
    id → 可安全拼进路径的名字。**所有"由 id 拼路径"的地方都走这一个函数**
    （见 HANDOFF 的账号体系一节：正因为只有这一个入口，数据隔离才能只改这里）。

    ★ 加上了**账号命名空间**前缀（`u<id>_`）：登录用户的会话、分镜、产物
      全部落在自己的分区里，A 拿不到 B 的。未登录时前缀为空，行为与以前一致。

    ⚠️ 必须幂等：`list_threads()` 会把**已经带前缀**的文件名再喂回本函数。
       幂等性由 `auth.scope.scoped_safe_id` 保证（先剥前缀再清洗再拼回）。
    """
    from .auth import scope as _identity
    return _identity.scoped_safe_id(_raw_safe_id, s, default, limit)


def _raw_safe_id(s, default="anon", limit=48):
    """真正做清洗的那一步。**只应由 safe_id 调用**（否则会绕过命名空间）。"""
    s = _SAFE.sub("_", str(s or ""))[:limit]
    return s or default


def _in_my_namespace(stem):
    """
    这个"已经是文件名形态"的名字属不属于当前账号。

    用**归属者 id 比对**，而不是字符串前缀比对：`stem` 里可能挂着别人的前缀
    （`u7_t_abc`），直接比 prefix 会把"u1_" 和 "u17_" 这类判错。
    无归属者（老数据）只有在**没有登录身份**时才可见 —— 也就是升级前的
    单租户行为原样保留；一旦登录，它就只属于"还没收编"的那批数据。
    """
    from .auth import scope as _identity
    owner = _identity.uid_from_namespaced(stem)
    me = _identity.user_id()
    return owner == me


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
      1. `{PUBLIC_BASE_URL}` 前缀：前端只认 `pathname` 是 `/media/` 的绝对地址，会把它换成
         同源前缀交给 Next 转发（web/src/lib/api.ts::mediaUrl）。写相对路径反而认不出来，
         所以这里**必须**带上前缀 —— 但它是个"写法"，不代表访问者能直接连到这个地址。
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


def clear_thread_storyboard(thread_id):
    """
    删掉"这个会话当前这一版分镜"。

    为什么要单独有它：`storyboard.save_thread_storyboard` 每次都以
    **同一个文件名**写入（路径由 thread_id 决定），而文件名是隔离的
    （`u1_t.json`）、内容不是 —— 里面存的 `storyboard` 是会话内**最后一条**
    尚未渲染的代码，可能属于另一个账号。所以在跨分区返回之前必须清一次。
    """
    try:
        os.remove(thread_file(thread_id))
        return True
    except OSError:
        return False


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


def _merge_duplicate_ids(msgs):
    """
    同一个 message_id 出现多次时，**只留最后一条，并保住它第一次出现的位置**。

    什么情况下会重复：前端"重新生成"复用同一个 message id（新答案要顶掉旧的），
    以及同一发请求被送到后端两次（网络重发 / 刷新后重进）。这两种都不该在记录里
    留下两条同 id 的条目。

    重复的后果不只是前端那句 React "two children with the same key"：
      - 喂给模型的历史里，同一句提问出现两次 —— 下一轮指代就含糊了；
      - 侧栏副标题的"N 条对话"、标题取首句都会算错。

    ⚠️ **没有 id 的条目一律不动**：判不了身份，合并等于凭空吞掉一轮对话。
       顺序也必须保：按 id 去重（dict 保序）会把"后写的那条"挪到末尾，
       于是助手回答跑到提问前面去了 —— 这里只**就地替换**，位置一动不动。
    """
    out, pos = [], {}
    for m in msgs:
        if not isinstance(m, dict):
            out.append(m)
            continue
        mid = str(m.get("message_id") or "").strip()
        if not mid:
            out.append(m)
            continue
        i = pos.get(mid)
        if i is None:
            pos[mid] = len(out)
            out.append(m)
        else:
            out[i] = m               # 内容用后写的那条，位置还是原来那个
    return out


def load_thread_messages(thread_id):
    """
    这个会话的对话记录（喂模型、推标题、恢复界面都用它）。

    ⚠️ 出去之前**必须过一遍去重**：这里是所有读路径的唯一收口
       （chat 的历史、list_threads、load_thread_detail、分享页），
       所以已经写坏的旧记录也能在这一层被修好 —— 不必等用户删掉那条会话。
    """
    data = _read_json(thread_messages_file(thread_id)) or {}
    msgs = data.get("messages")
    return _merge_duplicate_ids(msgs) if isinstance(msgs, list) else []


def append_thread_message(thread_id, role, content, meta=None):
    """
    追加一轮对话，返回完整列表。

    ⚠️ `content` 只记**自然语言**（前端聊天框显示的就是它），分镜 JSON 记在
    `meta.storyboard`：
      · 塞进 content 会把界面撑坏 —— 几千字 JSON 会直接显示在聊天气泡里；
      · 但模型**确实需要看到自己上一版写了什么**（多轮"改这一版"全靠它），
        所以由 `llm._history_with_storyboards()` 在**发出去之前**从 meta 还原成
        "brief + 分镜 JSON" 的 assistant 消息。
      · 顺带：JSON 不进 content，消息文件也保持可读（`_tasks/messages/*.json` 是排查用的）。

    2026-09-17 改：这里原先写的是"不记分镜 JSON，因为 ① 吃预算 ② 前缀缓存不命中"。
    两条理由后来都不成立 —— ① JSON 中位 **1463 token**（49 份实测），默认 10000 的预算下
    能留住约 6 轮带画面的对话；② 历史是**只追加**的，前缀缓存照常命中
    （每一轮逐字稳定的前缀都在，变的只是末尾新增的那一轮）。
    而代价是模型手里没有原料：实测"用户只让改第 2 镜，它顺手把第 3、4 镜也重写了"。
    """
    text = str(content or "").strip()
    if not text:
        return load_thread_messages(thread_id)
    path = thread_messages_file(thread_id)
    data = _read_json(path) or {}
    msgs = data.get("messages")
    msgs = _merge_duplicate_ids(msgs) if isinstance(msgs, list) else []
    entry = {"role": role, "content": text, "at": time.time()}
    if meta:
        entry.update(meta)
    # 同一个 message_id 再写一次 = **原地覆盖这一轮**，不是又追加一轮。
    # 前端"重新生成"就是复用同一个 id（新答案顶掉旧的）；而同一个请求被送到两次时，
    # 也只有覆盖才能让结果和只发一次一样 —— 追加会留下两条同 id 的记录，
    # 前端按 id 渲染直接撞 key，模型那边也会看到同一句话出现两次。
    mid = str(entry.get("message_id") or "").strip()
    for i, m in enumerate(msgs):
        if mid and isinstance(m, dict) and str(m.get("message_id") or "").strip() == mid:
            msgs[i] = entry
            break
    else:
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
        # 这次渲染用的质量目录（480p15 / 720p30 …）。**必须落盘**：
        # 读产物那侧（rebuild_render_state）如果按"当前配置"去猜目录，
        # 中途改过 MSB_QUALITY 就会去错误的目录找文件 —— 表现是视频集体消失
        # 而且不报任何错，正是本项目最忌讳的静默失效。
        "quality_dir": config.quality_dir_name(),
    }
    m.update(meta or {})
    _write_json(os.path.join(d, "meta.json"), m)
    return m


def load_task_storyboard(name):
    return _read_json(os.path.join(task_dir(name), "storyboard.json"))


def load_task_meta(name):
    return _read_json(os.path.join(task_dir(name), "meta.json")) or {}


# ---------------- 产物路径（相对 /media 的根）----------------
#
# ⚠️ 必须与 pipeline 写产物时用的是**同一套命名**：那边写、这边读，差一个字符
#    恢复出来的就是一串 404 的视频地址，而且不会报任何错。
#    所以实现放在这里，pipeline 的 _section_rel/_final_rel 改为转发 ——
#    pipeline 可以 import store，反过来不行（会成环）。

def section_rel(name, qdir, index):
    """某一段 mp4 相对 output/videos 的路径（media_url 吃的就是这个）。"""
    return f"{name}_scene/{qdir}/sections/{section_filename(index)}"


def final_rel(name, qdir):
    """整条成片相对 output/videos 的路径。"""
    return f"{name}_scene/{qdir}/StoryboardScene.mp4"


# ---------------- 恢复：会话列表 / 会话详情 ----------------
#
# 这一层**不新增任何写入**，只是把 _tasks/ 下已经躺着的东西读出来拼回去。
# 渲染热路径一行不动，也就不会多出一个新的静默失效面。

def _now_ms(ts):
    """
    秒 → 毫秒。

    后端 time.time() 是秒，前端的 Date.now() 和"3 分钟前"那类相对时间全按毫秒算。
    漏转一次，界面上就会显示"1970 年前"。
    """
    return int(round(float(ts) * 1000))


def _thread_title(msgs, meta=None):
    """
    会话标题。**用户改过的名字优先**（存在 sessions/<t>.json 里），
    没有才退回首条用户消息的前 20 字。

    退回首句这条规则是前端新建会话时用的（Workbench.send 拿第一句当标题），
    但那是内存态、刷新即失 —— 所以后端按同样的规则推一遍，至少两边看着一致。
    """
    t = str((meta or {}).get("title") or "").strip()
    if t:
        return t
    for m in msgs:
        if str(m.get("role")) != "user":
            continue
        t = str(m.get("content") or "").strip()
        if t:
            return t[:20]
    return "新的讲解"


def _thread_subtitle(msgs, storyboard, plan):
    """副标题。拿不到分镜数就退化成消息条数，都没有就让前端去显示相对时间。"""
    scenes = storyboard.get("scenes") if isinstance(storyboard, dict) else None
    n = len(scenes) if isinstance(scenes, list) and scenes else len(plan or [])
    if n:
        return f"{n} 个分镜"
    return f"{len(msgs)} 条对话" if msgs else ""


def list_threads():
    """
    会话摘要列表。**置顶的排前面**，其余按最近更新倒序。

    ⚠️ "算不算一条会话"**只以 messages/ 为准** —— 侧栏叫"历史会话"，语义是对话。

    一开始这里取的是 messages/ 与 threads/ 的**并集**，想法是"只存在其中一个很正常"。
    但 threads/ 里还会躺着 **CLI 直接生成分镜**留下的名字（`generate.py` 跑的调试任务，
    从来就没有对话），列进去的结果是点开一片空白 —— 用户当场撞上了这个。
    并集只在"messages 缺、threads 有"时是错的，而反过来（刚问一句还没出分镜）
    并集也是多余的：那种情况 messages 本来就有。
    所以只用 messages/ 定成员，threads/ 退居"补充分镜数"的角色。

    返回的 id 取自**文件名**（它已经是 safe_id 清洗过的形态）。这是安全的：
    safe_id 幂等，前端把这个 id 原样发回来时清洗结果不变，仍指向同一个文件。

    ⚠️ messages/ 是**所有账号共用**的一个目录，所以这里必须按"本命名空间下
       消息文件真的存在"过滤一遍。不过滤的话，每个登录用户都会看到别人的会话
       以"只有 id、没有内容"的幽灵条目形式出现在侧栏里 —— 而这正是最容易
       被当成"数据没隔离"的一幕。
    """
    root = tasks_root()
    d = os.path.join(root, "messages")
    try:
        names = os.listdir(d)
    except OSError:
        return []                      # 还没有任何对话（目录都还没建）
    ids = {f[:-5] for f in names if f.endswith(".json")}
    ids = {tid for tid in ids if _in_my_namespace(tid)}

    out = []
    for tid in ids:
        msgs = load_thread_messages(tid)
        raw, plan = load_thread_storyboard(tid)
        meta = load_session_meta(tid)
        stamps = []
        for p in (thread_messages_file(tid), thread_file(tid)):
            try:
                stamps.append(os.path.getmtime(p))
            except OSError:
                pass
        updated = max(stamps) if stamps else time.time()
        out.append({
            "id": tid,
            "title": _thread_title(msgs, meta),
            "subtitle": _thread_subtitle(msgs, raw, plan),
            "updatedAt": _now_ms(updated),
            "pinned": bool(meta.get("pinned")),
            "shared": bool(str(meta.get("share") or "").strip()),
        })
    # 排序在服务端定：置顶的永远在最前，其余按最近更新。
    # 让前端自己排的话，两处规则一旦不一致，界面顺序就和接口顺序对不上。
    out.sort(key=lambda t: (not t["pinned"], -t["updatedAt"]))
    return out


def rebuild_render_state(thread_id, message_id, plan=None):
    """
    从磁盘把一条消息的渲染状态拼回来。没渲染过就返回 None（前端表现为普通对话）。

    为什么是"读盘推导"而不是"渲染时另写一份状态"：后者要动渲染热路径，
    而热路径上多一次写入就多一个静默失效的可能（本项目最忌讳的东西）。
    产物本身已经把事实记全了：
        sections/index.json   每段的**真实时长**（manim 按帧数算的，不是大纲估算值）
        sections/*.mp4        哪几段真的交付了
        StoryboardScene.mp4   成片在不在
    拼一份"当前状态"是纯函数；读不出来的部分就老实缺着。

    ⚠️ 一次 listdir 建集合，**绝不逐段 stat**，也**不跑 ffprobe** ——
       ffprobe 是进程级开销，一个会话就能把接口拖到秒级。
       时长只认 index.json，它缺失时退化为大纲里的设计时长。

    ⚠️ `stop` 的语义要和渲染的真实形态对齐：后端是「一个 Scene 演到底」，
       某一段崩了之后**后面的分镜根本没执行过**。所以第一个缺口的段是 error，
       其后一律 queued —— 不是"全都失败了"。
    """
    if not thread_id or not message_id:
        return None
    name = task_name(thread_id, message_id)
    if not os.path.isdir(task_dir(name)):
        return None                    # 这个 task 从没建过 = 这条消息没渲染过

    snap = load_task_storyboard(name)
    scenes = snap.get("scenes") if isinstance(snap, dict) else None
    n = len(scenes) if isinstance(scenes, list) and scenes else len(plan or [])
    if not n:
        return None

    # 质量目录**优先读渲染当时落盘的那个**：中途改过 MSB_QUALITY 的话，
    # 按当前配置去猜会跑到别的目录找文件 —— 视频集体消失，而且不报任何错。
    meta = load_task_meta(name)
    qdir = str(meta.get("quality_dir") or "").strip() or config.quality_dir_name()
    sec = sections_dir(name, qdir)

    durations = {}
    for row in (_read_json(os.path.join(sec, "index.json")) or []):
        if not isinstance(row, dict):
            continue
        try:
            durations[int(row.get("index"))] = float(row.get("duration") or 0.0)
        except (TypeError, ValueError):
            continue

    present = set()
    try:
        present = {f for f in os.listdir(sec) if f.endswith(".mp4")}
    except OSError:
        present = set()

    missing = [i for i in range(n) if section_filename(i) not in present]
    stop = missing[0] if missing else n
    have_final = os.path.exists(os.path.join(video_dir(name, qdir), "StoryboardScene.mp4"))

    out = []
    for i in range(n):
        row = plan[i] if (isinstance(plan, list) and i < len(plan)
                          and isinstance(plan[i], dict)) else {}
        dur = durations.get(i)
        if dur is None:
            dur = row.get("durationSec")
        status = "done" if i < stop else ("error" if i == stop else "queued")
        item = {
            "index": i,
            "title": str(row.get("title") or f"分镜 {i + 1}"),
            "status": status,
            "durationSec": round(float(dur or 0.0), 3),
        }
        if status == "done":
            item["url"] = media_url(section_rel(name, qdir, i))
        out.append(item)

    state = {
        "status": "done" if (have_final and not missing) else "error",
        "step": stop,                  # 与 SSE 的 tool_progress 同义：已交付段数
        "total": n,
        "scenes": out,
    }
    if have_final:
        state["finalUrl"] = media_url(final_rel(name, qdir))
    return state


def _task_index(thread_id):
    """
    这个会话下所有"渲染过产物"的记录：[(message_id, created_at)]，按时间升序。

    message_id 取自 `_tasks/<thread>__<message>/meta.json` —— render.py 在 confirm /
    retry 时写进去的（那时它手上正好有前端给的 message_id）。
    这正是给"改造之前的旧消息"找回 id 的来源。
    """
    out = []
    root = tasks_root()
    prefix = safe_id(thread_id, "t") + "__"
    if not os.path.isdir(root):
        return out
    try:
        entries = os.listdir(root)
    except OSError:
        return out
    for d in entries:
        full = os.path.join(root, d)
        if not d.startswith(prefix) or not os.path.isdir(full):
            continue
        meta = load_task_meta(d)
        mid = str(meta.get("message_id") or "").strip() or d[len(prefix):]
        try:
            at = float(meta.get("created_at") or 0.0)
        except (TypeError, ValueError):
            at = 0.0
        if not at:
            try:
                at = os.path.getmtime(full)
            except OSError:
                at = 0.0
        out.append((mid, at))
    out.sort(key=lambda x: x[1])
    return out


def thread_belongs_to_me(thread_id):
    """
    这条会话是不是当前账号的。

    ⚠️ 关键的一条：请求里的 `thread_id` **完全由客户端给**。没有这一道，
       A 只要把 B 的 thread_id 填进 URL 就能读到、改名甚至删掉 B 的会话
       （路径是拼出来的，拼出来就能访问）。所以凡是"按 id 取一条会话"的
       写操作都必须先问这一句。

    `thread_id` 可能带前缀（列表页给的就是带前缀的），所以按**归属者**判断，
    不按字符串前缀判断。老数据（无前缀）落在"未登录才可见"那一档。
    """
    return _in_my_namespace(str(thread_id or ""))


def load_thread_detail(thread_id):
    """
    一个会话的完整可恢复状态（消息数组 + 每条助手消息的渲染状态）。

    消息 id 是这里最要紧的一件事 —— 它是前端 confirm 时定位产物的唯一钥匙：
      - **新记录**：/api/chat 已经把前端生成的 id 落盘了，直接用。
      - **旧记录**（这次改造之前写的）：messages 文件里没有 id，但 **task 目录名里有**
        （`<thread>__<message>`，confirm 时写进去的）。所以按区间认领一个：
        渲染必然发生在这轮回答之后、用户下一次发言之前，task 的 created_at 落进这个
        区间的就是它。**这是有据可依的区间匹配，不是时间就近猜** —— 猜错会把 A 问的
        视频挂到 B 问底下，那比"没有视频"更糟。
      - **认领不到**（比如产物已被清理）：补一个由位置推导的稳定 id（ma_hist_3），
        只为让前端有稳定的 key 可渲染；那些消息恢复后只有文字、没有视频 ——
        这是诚实的降级，比"假装有个能点开的视频"好。

    另一处诚实的降级：逐轮大纲是这次才落盘的。旧记录取不到自己那一轮的大纲，
    于是**只给最后一条助手消息**挂上"会话当前这一版"（来自
    _tasks/threads/<t>.json）—— 这样"接着点确认去渲染"这条路还走得通，
    又不会把同一份大纲复制到每条历史消息上。
    """
    msgs = load_thread_messages(thread_id)
    _, cur_plan = load_thread_storyboard(thread_id)

    # 消息的原始时间戳（秒）。给旧记录划"哪些 task 属于它"的区间要用。
    stamps = []
    for m in msgs:
        try:
            stamps.append(float(m.get("at") or 0.0) if isinstance(m, dict) else 0.0)
        except (TypeError, ValueError):
            stamps.append(0.0)

    pool = _task_index(thread_id)
    claimed = set()

    last_asst = -1
    for i, m in enumerate(msgs):
        if isinstance(m, dict) and str(m.get("role")) == "assistant":
            last_asst = i

    out = []
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            continue
        role = "assistant" if str(m.get("role")) == "assistant" else "user"
        mid = str(m.get("message_id") or "").strip()

        # 旧记录：从 task 目录里按区间认领（见上面那段说明）。
        if not mid and role == "assistant" and stamps[i]:
            upper = float("inf")
            if i + 1 < len(stamps) and stamps[i + 1] > stamps[i]:
                upper = stamps[i + 1]
            for k, (tmid, tat) in enumerate(pool):
                if k not in claimed and stamps[i] <= tat < upper:
                    mid = tmid
                    claimed.add(k)
                    break
        # 认领不到就补一个稳定 id：至少让前端有 key 可渲染
        if not mid:
            mid = f"{'ma' if role == 'assistant' else 'mu'}_hist_{i}"

        item = {
            "id": mid,
            "role": role,
            "text": str(m.get("content") or ""),
            "createdAt": _now_ms(m.get("at") or time.time()),
        }
        if role != "assistant":
            out.append(item)
            continue

        if isinstance(m.get("plan"), list) and m["plan"]:
            item["plan"] = m["plan"]
        elif i == last_asst and isinstance(cur_plan, list) and cur_plan:
            item["plan"] = cur_plan

        intent = str(m.get("intent") or "").strip()
        if intent not in ("propose", "none"):
            intent = "propose" if item.get("plan") else "none"
        item["intent"] = intent

        # 视频标题：这一轮分镜 JSON 里模型给的 `title`。
        # 恢复时也带回去，否则刷新后前端会退回"第一个分镜名"（那是分镜标题，
        # 不是这条视频的名字）。拿不到就**不给这个字段** —— 前端自己有兜底。
        raw = m.get("storyboard")
        if isinstance(raw, dict):
            t = str(raw.get("title") or "").strip()
            if t:
                item["videoTitle"] = t

        # 只传这条消息**自己那一轮**的大纲：拿 cur_plan 兜底会把"会话当前这一版"
        # 的分镜标题套到别的产物上（段数由快照决定，标题却会串轮）。
        render = rebuild_render_state(thread_id, mid, item.get("plan"))
        if render:
            item["render"] = render
            # 有产物 = 用户当时确实点过"确认"（渲染只能从 confirm 发起）
            item["planState"] = "confirmed"
        elif intent == "none":
            item["planState"] = "none"
        else:
            # 有大纲但没渲染过 → 待确认。
            # （"放弃大纲"是前端的本地状态、不上报，恢复后一律回到 pending ——
            #   这是唯一不骗人的取值。）
            item["planState"] = "pending"
        out.append(item)
    return out


def build_share_payload(thread_id):
    """
    分享页要的东西：标题 + 成片 + 分段清单。

    取"这个会话里**最后一条出过片**的助手消息" —— 分享的是成片本身，不是整段对话
    （对话里可能有用户的问题原文和追问，分享页只放视频与标题）。

    没有成片时也返回一个合法结构（`final=None`），让分享页能显示"这条还没有视频"，
    而不是把 404 的处理推给前端。
    """
    meta = load_session_meta(thread_id)
    msgs = load_thread_messages(thread_id)
    title = _thread_title(msgs, meta)

    # ⚠️ 读到一半（而不是在入口处）就把身份切成归属者，是因为上面那三行
    #    已经各按**当前身份**读了各自的文件、结果自洽；而这里要遍历产物目录，
    #    必须站在归属者的分区里才找得到（分享页本身没有登录身份）。
    #    切身份用 with —— 漏了还原的话，同一个线程服务下一个请求时就用错人了。
    from .auth import scope as _identity
    owner = _identity.uid_from_namespaced(thread_id)
    if owner is not None and _identity.user_id() != owner:
        with _identity.as_user(owner):
            return _share_payload(thread_id, msgs, meta, title)
    return _share_payload(thread_id, msgs, meta, title)


def _share_payload(thread_id, msgs, meta, title):
    """真正拼分享数据的那一半（身份已经切对了）。"""
    picked = None
    for m in load_thread_detail(thread_id):
        r = m.get("render")
        if m.get("role") == "assistant" and isinstance(r, dict) and r.get("finalUrl"):
            picked = r
    if not picked:
        return {"thread_id": thread_id, "title": title, "subtitle": "",
                "segments": [], "final": None}

    segments = [
        {"index": int(s.get("index") or 0),
         "title": str(s.get("title") or f"分镜 {int(s.get('index') or 0) + 1}"),
         "url": s.get("url") or "",
         "durationSec": float(s.get("durationSec") or 0.0)}
        for s in (picked.get("scenes") or []) if isinstance(s, dict)
    ]
    return {
        "thread_id": thread_id,
        "title": title,
        "subtitle": f"{picked.get('total') or len(segments)} 个分镜",
        "segments": segments,
        "final": {"url": picked["finalUrl"],
                  "durationSec": round(sum(s["durationSec"] for s in segments), 3)},
    }


# ---------------- 会话元数据：用户改出来的东西 ----------------
#
# 标题（重命名）、置顶、分享 token 这三样，都**不是渲染产物**，也**不能**混进
# messages / threads 那两个文件 —— 它们各有各的写入方（chat 追加消息、render 覆盖
# 分镜），而 `_write_json` 是**覆盖写**，往里塞字段会被下一次写入整体带掉。
# 所以单独一个文件，一条会话一个。

def thread_exists(thread_id):
    """
    这个会话有没有真实数据（对话或分镜）。

    用来拦住"给不存在的会话写元数据" —— 否则会留下一个只有 sessions/<t>.json 的
    "幽灵会话"：列表里不显示它（列表以 messages/ 为准），但它永远赖在磁盘上。
    """
    return (os.path.isfile(thread_messages_file(thread_id))
            or os.path.isfile(thread_file(thread_id)))


def sessions_dir():
    return os.path.join(tasks_root(), "sessions")


def session_file(thread_id):
    return os.path.join(sessions_dir(), safe_id(thread_id, "t") + ".json")


def load_session_meta(thread_id):
    """读用户改出来的会话属性。没改过就是空 dict（不是错误）。"""
    data = _read_json(session_file(thread_id)) or {}
    return data if isinstance(data, dict) else {}


def save_session_meta(thread_id, patch):
    """
    合并写，**不是**覆盖写。

    ⚠️ 必须是合并：置顶和分享是两个独立动作，覆盖写会让"先分享、后置顶"
    把分享链接悄悄抹掉 —— 又一种静默失效。
    `patch` 里值为 None 表示"删掉这个键"（比如取消分享）。
    """
    cur = load_session_meta(thread_id)
    for k, v in (patch or {}).items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    cur["updated_at"] = time.time()
    _write_json(session_file(thread_id), cur)
    return cur


def find_share_slug(slug):
    """
    按分享 token 找回会话 id（分享页用，公开访问）。

    遍历 sessions/ 逐个比对，而不是另建一张 slug → thread 的反查表：
    多一张表就多一个"删会话时忘了删表"的漏，而这里量级很小（内网、少量用户），
    根本不需要索引。
    """
    # ⚠️ 必须**跨命名空间**找：分享页是公开的（白名单放行），请求里根本没有
    #    登录身份，而它要读的偏偏是**别人的**会话。所以这里自己扫 sessions/
    #    目录、直接读文件，返回**带前缀的 stem** —— 归属者信息就藏在那个前缀里，
    #    回调方（`build_share_payload`）据此临时切成那个人的身份去读。
    #
    #    ⚠️ 不能用 `load_session_meta` 逐个试：它会按**当前身份**拼路径，
    #       在匿名上下文里算出来的路径是错的（永远找不到），表现是
    #       "分享页能打开、视频全 404"，而错误信息什么都说明不了。
    want = str(slug or "").strip()
    if not want:
        return ""
    d = sessions_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return ""
    for f in names:
        if not f.endswith(".json"):
            continue
        stem = f[:-5]
        # 老数据（无前缀）在公共区：顺手按无身份读一次，保持升级前的行为
        meta = _read_json(os.path.join(d, f)) or {}
        if isinstance(meta, dict) and str(meta.get("share") or "").strip() == want:
            return stem
    return ""


def delete_thread(thread_id):
    """
    删掉一条会话：对话历史、分镜快照、用户元数据，以及它名下**全部渲染产物**。

    ⚠️ 只认"以这个 thread_id 命名"的东西，且 task 目录用 `safe_id(thread) + "__"`
       **整段前缀**识别，绝不做模糊匹配 —— 否则 `t_4rf2aue` 会误伤 `t_4rf2aue2`
       的产物。那个 `__` 分隔符正是为此存在的（见 task_name）。
    """
    sid = safe_id(thread_id, "t")
    prefix = sid + "__"
    root = tasks_root()
    removed = []

    # 1) 会话级三个文件
    for p in (thread_messages_file(thread_id), thread_file(thread_id),
              session_file(thread_id)):
        if os.path.isfile(p):
            try:
                os.remove(p)
                removed.append(os.path.basename(p))
            except OSError:
                pass

    # 2) 该会话名下的每个 task：产物 + 增量缓存 + 成片 + 生成的 Manim 源码
    try:
        entries = os.listdir(root)
    except OSError:
        entries = []
    for name in entries:
        if not name.startswith(prefix) or not os.path.isdir(os.path.join(root, name)):
            continue
        targets = [
            task_dir(name),                                  # 分镜快照 + meta
            parts_dir(name),                                 # 增量缓存（_parts）
            os.path.join(config.OUTPUT_DIR, f"{name}_scene.py"),
            os.path.join(config.OUTPUT_DIR, "videos", f"{name}_scene"),
        ]
        for p in targets:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
                removed.append(os.path.basename(p))
            elif os.path.isfile(p):
                try:
                    os.remove(p)
                    removed.append(os.path.basename(p))
                except OSError:
                    pass
    return removed


# ---------------- 清理 ----------------

def cleanup_stale(days=None):
    """
    删掉超过 N 天没动过的**渲染产物**（task 快照、增量缓存、成片、生成的 Manim 源码）。

    ⚠️ **绝不碰会话数据**：`messages/`（对话）、`threads/`（当前这一版分镜）、
       `sessions/`（标题 / 置顶 / 分享）一律留着。
       这条分界是后来才划清的 —— 以前它们跟着产物一起按 TTL 删，于是"清理磁盘"
       会顺带把会话历史清掉，而用户只会看到"过一阵我的对话就没了"，
       根本不会联想到是自己配的那个天数干的。
       现在的规矩是：**文字永久保留，只有视频这类大文件可以按需清**。
       被清掉的消息恢复出来只是没有视频，大纲和文字都还在，重新点确认还能再渲。

    ⚠️ 只删**我们自己建过的东西**（以 output/_tasks/ 下的目录名为准），
       绝不去扫 output/videos 反推 —— output/ 里还有 examples 的历史产物和
       benchmark 目录（_bench_* / _prof / _spd），无差别清理会误伤。
    """
    days = config.TASK_TTL_DAYS if days is None else days
    if not days or days <= 0:
        return {"removed": [], "skipped": "cleanup disabled（会话与产物都保留）"}

    cutoff = time.time() - days * 86400
    root = tasks_root()
    if not os.path.isdir(root):
        return {"removed": [], "days": days}

    # 这三个是会话数据目录，永远不参与清理
    keep_dirs = {"threads", "messages", "sessions"}

    removed = []
    for entry in os.listdir(root):
        full = os.path.join(root, entry)
        if entry in keep_dirs or not os.path.isdir(full):
            continue
        try:
            if os.path.getmtime(full) >= cutoff:
                continue
        except OSError:
            continue
        targets = [
            full,                                                          # 分镜快照 + meta
            parts_dir(entry),                                              # 增量缓存
            os.path.join(config.OUTPUT_DIR, f"{entry}_scene.py"),          # 生成的 Manim 源码
            os.path.join(config.OUTPUT_DIR, "videos", f"{entry}_scene"),   # 成片与分段
        ]
        for t in targets:
            if os.path.isdir(t):
                shutil.rmtree(t, ignore_errors=True)
            elif os.path.isfile(t):
                try:
                    os.remove(t)
                except OSError:
                    pass
        removed.append(entry)
    return {"removed": removed, "days": days}

# -*- coding: utf-8 -*-
"""
"当前是谁" —— 身份的隐式上下文，以及**数据命名空间**。

================================================================================
为什么身份走 ContextVar，而不是当参数一层层传
================================================================================
`store.list_threads()` 无参、`store.task_name(thread_id, message_id)` 两参，
把"当前用户"加进每一个 store 函数的签名，等于把鉴权这件事**传染**给整个
数据访问层；而它们的调用点有几十处，漏一处就是"某条路径没隔离"。
身份是**这一次请求**的属性，ContextVar 正好对应。

================================================================================
数据隔离：只改「由 id 拼路径」的唯一入口
================================================================================
原来的存储布局是**单租户**的：

    output/_tasks/messages/<thread>.json      ← 对话
    output/_tasks/threads/<thread>.json       ← 当前这一版分镜
    output/_tasks/sessions/<thread>.json      ← 标题 / 置顶 / 分享
    output/_tasks/<thread>__<message>/        ← 某次渲染的分镜快照
    output/_parts/<thread>__<message>/        ← 增量缓存
    output/videos/<thread>__<message>_scene/  ← 成片与分段（/media 服务的就是它）

而 `api/store.py` 里所有"由 id 推导路径"的地方**全部**经过同一个函数
`safe_id()`。所以隔离只需在这里加一层命名空间前缀，上面六处**同时**被隔离，
并且互相之间仍然自洽：

    task_name 产出          u1_t__u1_m
    delete_thread 的前缀    safe_id(t) + "__"  →  u1_t__   ✅ 能匹配上
    _task_index 的前缀      同上                      ✅ 能匹配上
    thread_file             u1_t.json                 ✅

⚠️ **为什么不能"每人一个 output 子目录"**（看起来最干净）：
   `api/main.py` 把 `/media` 静态挂在了 `output/videos` 上。产物一旦挪到
   `output/_u/1/videos/…`，这条挂载就再也服务不到它们 —— 表现是"渲染成功但
   视频全 404"，而且**不报任何错**。所以隔离只能靠**目录名**，不能靠父目录。

================================================================================
`safe_id` 必须**幂等**，否则列表页会自毁
================================================================================
`list_threads()` 是"从 messages/ 目录读文件名，再把名字喂回读取函数"。
喂回去的那个名字**已经带前缀**了。所以带前缀的 id 再洗一次必须不变：

    safe_id("u1_abc")  →  "u1_abc"      （不能变成 "u1_u1_abc"）

实现见 `_scoped_safe_id`：**先剥前缀 → 再清洗 → 再拼回**，顺序不能换。
不能写成"清洗后看有没有前缀"—— `safe_id` 会按 limit 截断，长 id 截断后
前缀判断会失真，于是"写出"和"读回"算出两个不同的名字。
"""

import contextvars
import os
import re

_NS_USER = contextvars.ContextVar("msb_auth_user", default=None)

# `u<数字>_`，用来从目录名反推归属者（分享页跨命名空间查找时要用）
_NS_RE = re.compile(r"^u(\d+)_")


def set_current(user):
    """登记当前用户（闸门里调）。返回 token，供 finally 里 reset。"""
    return _NS_USER.set(user if isinstance(user, dict) else None)


def reset(token):
    try:
        _NS_USER.reset(token)
    except (ValueError, LookupError):
        pass


def current():
    """当前用户 dict，未登录返回 None。"""
    u = _NS_USER.get()
    return u if isinstance(u, dict) else None


def user_id():
    u = current()
    try:
        return int(u.get("id")) if u else None
    except (TypeError, ValueError):
        return None


def namespace():
    """
    数据命名空间前缀：`u<id>`（未登录 / 未启用鉴权时为 ""）。

    用「u + 数字 id」而不是邮箱：邮箱里的 `@` `.` 会被 safe_id 折叠成 `_`，
    而折叠是**多对一**的（不同邮箱可能折成同一个）—— id 是唯一的。
    """
    uid = user_id()
    return f"u{uid}" if uid else ""


def uid_from_namespaced(stem):
    """从 `u3_t_abc` 这样的名字里反推出 3；没有前缀返回 None。"""
    m = _NS_RE.match(str(stem or ""))
    try:
        return int(m.group(1)) if m else None
    except ValueError:
        return None


def scoped_safe_id(orig, s, default="anon", limit=48):
    """
    `store.safe_id` 的命名空间版（幂等，见文件头）。

    ⚠️ 顺序不能换：**先剥前缀 → 再清洗 → 再拼回**。
    """
    ns = namespace()
    if not ns:
        return orig(s, default, limit)
    pre = ns + "_"
    raw = str(s or "")
    if raw.startswith(pre):
        raw = raw[len(pre):]          # 剥掉已有前缀，避免 u1_u1_…
    return pre + orig(raw, default, limit)


class as_user:
    """
    临时切成"某个用户"的身份（分享页读别人的数据时用）。

    `with` 保证一定会切回来 —— 漏了的话，同一个线程复用时会用错身份。
    """

    def __init__(self, uid):
        self._uid = uid
        self._token = None

    def __enter__(self):
        # 只需要 id 这一个字段：namespace() 只看它。
        # 不去查库拿完整用户 —— 那会多一次 IO，而这里不需要任何别的信息。
        self._token = set_current({"id": self._uid})
        return self

    def __exit__(self, *exc):
        reset(self._token)
        return False


def bind_context(func):
    """
    把一个函数包成"**在当前 context 里**执行"。

    ⚠️ 为什么需要它：`pipeline.stream()` 是**另起一个线程**跑 `run()` 的，
    而 **ContextVar 不会自动进入新线程** —— 那里拿到的身份是 None，于是
    "提问 → 落盘"这条人人都会走的路径会把数据写进**公共区**（登录后看不到
    自己的会话，而错误信息什么都说明不了）。

    所以 `pipeline.stream` 起线程时用它包一层，并且**必须在调用 stream 的那一刻
    复制 context**（那时还在请求任务里、身份还在）—— 等进了线程再复制就晚了。
    """
    ctx = contextvars.copy_context()

    def wrapper(*args, **kwargs):
        return ctx.run(func, *args, **kwargs)

    return wrapper


# ==============================================================================
# 老数据收编（把改造前"没有前缀"的会话搬进某个账号）
# ==============================================================================

def _move(src, dst):
    if not os.path.exists(src) or os.path.exists(dst):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.rename(src, dst)          # 同一磁盘上的移动是原子且瞬间的
        return True
    except OSError:
        return False


def _namespaced_task_name(n, ns):
    """给 `<thread>__<message>` 两侧各加一次前缀，与 task_name 的算法一致。"""
    if "__" not in n or _NS_RE.match(n):
        return None
    t, _, m = n.partition("__")
    return f"{ns}{t}__{ns}{m}"


def adopt_legacy(cfg, uid, dry=False):
    """
    把"账号体系之前就存在"的数据（没有命名空间前缀的那些）搬到 `uid` 名下。

    ⚠️ 为什么必须有一条这样的通路：隔离上线后，老用户登录会发现自己
       **一条会话都没有** —— 数据没丢，只是躺在公共区里。这个观感就是
       "我的东西被删了"，比真的报个错还糟。

    ⚠️ 它是**搬文件**（不可逆、一次性）。这里按项目所有者的决定做成
       "首次建出超级管理员后自动跑一次"，但保留 `MSB_AUTH_ADOPT_LEGACY=0` 关掉：
       自动执行意味着"数据悄悄换了归属"，这种事有开关才说得过去。

    ⚠️ **幂等**：目标已存在就跳过，带前缀的名字不重复收编。所以重复调用安全。

    Returns:
        dict: {"ok", "moved": [...], "count"}
    """
    from .. import store

    ns = f"u{int(uid)}_"
    out_root = store.OUTPUT_DIR if hasattr(store, "OUTPUT_DIR") else None
    if out_root is None:
        from .. import config as api_config
        out_root = api_config.OUTPUT_DIR

    moved = []
    plan = []                            # dry-run 时收集"会搬哪些"

    def take(src, dst):
        plan.append(os.path.basename(src))
        if dry:
            return
        if _move(src, dst):
            moved.append(os.path.basename(src))

    # a) 会话级三件套：threads / messages / sessions 各一个 json
    for sub in ("threads", "messages", "sessions"):
        d = os.path.join(store.tasks_root(), sub)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for f in names:
            if not f.endswith(".json") or _NS_RE.match(f):
                continue
            take(os.path.join(d, f), os.path.join(d, ns + f))

    # b) 任务目录 / 增量缓存 / 成片：目录名本身就是 `<thread>__<message>`
    for base, suffix in (
        (store.tasks_root(), ""),
        (os.path.join(out_root, "_parts"), ""),
        (os.path.join(out_root, "videos"), "_scene"),
    ):
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for n in entries:
            if n in ("threads", "messages", "sessions"):
                continue
            full = os.path.join(base, n)
            if not os.path.isdir(full):
                continue
            stem = n[:-len(suffix)] if suffix and n.endswith(suffix) else n
            new = _namespaced_task_name(stem, ns)
            if not new:
                continue
            take(full, os.path.join(base, new + suffix))

    # c) 生成的 manim 源码：output/<name>_scene.py
    try:
        entries = os.listdir(out_root)
    except OSError:
        entries = []
    for n in entries:
        if not n.endswith("_scene.py") or _NS_RE.match(n):
            continue
        new = _namespaced_task_name(n[:-len("_scene.py")], ns)
        if new:
            take(os.path.join(out_root, n), os.path.join(out_root, new + "_scene.py"))

    if not dry and moved:
        print(f"[auth] 已把 {len(moved)} 项老数据收编到 u{uid} 名下", flush=True)
    return {"ok": True, "moved": moved, "count": len(plan) if dry else len(moved)}

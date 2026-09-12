# -*- coding: utf-8 -*-
"""
并发闸 + 每日配额。

为什么必须有：单条 8 分镜任务峰值会吃满 min(8, CPU 核数) 个核，而且瓶颈是**内存带宽**
（本机实测：单进程能吃 35 GB/s，8 进程并行时每个只剩 7.2 GB/s，整机约 58 GB/s 封顶）。
也就是说多开任务不会变快，只会让每个都变慢 —— 不设闸，两个用户同时点"生成"就互相拖垮。

MVP 用 asyncio.Semaphore 就够（见 docs/前端接入-后端改造清单.md §6），
不引入 Celery/RQ：渲染 13 秒，犯不上为它养一套任务队列。

⚠️ 每日配额目前是**进程内内存计数**。多进程/多实例部署时会各算各的，
   那时换 Redis 计数器（接口保持不变即可）。
"""

import asyncio
import time
from contextlib import asynccontextmanager

from . import config

_render_sem = asyncio.Semaphore(config.MAX_CONCURRENT_RENDERS)

# 当日已渲染条数。用日期字符串做 key，跨天自动归零，不需要定时任务。
_daily = {"date": time.strftime("%Y-%m-%d"), "count": 0}

# 当前正在渲染的任务名，/api/health 会露出来，方便排查"为什么没反应"。
_active = set()
_waiting = 0

# ==============================================================================
# 同一个 task 名必须串行 —— 这不是优化，是正确性
# ==============================================================================
# `_parts/<name>/` 是**同一个任务的多段渲染共用**的工作目录（scene 源码、
# media0/ 下 manim 的 partial movie files、segments/ 里的成段视频）。两个请求
# 同时用同一个 name 渲染，会互相删建对方的中间文件。
#
# 实测过一次真实的踩踏：两个请求并发跑同一个 name，其中一个在 manim 内部抛
#     FileNotFoundError: ...\output\_parts\<name>\media0\...
# 整个分镜渲染失败，而失败信息看上去像"渲染器坏了"，跟真正的原因（重复触发）
# 毫无关系 —— 这类错误最难查，所以从机制上堵掉。
#
# 触发路径在真实使用里很常见：用户点「重试」时上一次渲染还在跑、或者手抖双击。
#
# ⚠️ 加锁顺序必须是 **name 锁 → 全局并发闸**，顺序不能反：
#    若先占全局位再等 name 锁，一个重复请求会白占一个并发位（本机只有 2 个），
#    把别的任务饿死。
_name_locks = {}

# 锁对象本身很小，但任务名会越积越多（每个 task 一个），所以要定期收掉不再用的。
_LOCK_PRUNE_THRESHOLD = 512


def _name_lock(name):
    lock = _name_locks.get(name)
    if lock is None:
        lock = _name_locks[name] = asyncio.Lock()
    return lock


def _prune_locks():
    """
    丢掉"没人持有、也不在渲染中"的锁。

    安全性：asyncio.Lock 在**未被持有**时不可能有等待者 —— acquire() 在锁空闲时
    是不 await 直接拿到锁的，所以"观察到未持有"就意味着没有等待者。
    （不能顺手清掉 locked() 为真的锁：那正是别人正在用的那把。）
    """
    if len(_name_locks) <= _LOCK_PRUNE_THRESHOLD:
        return
    for k in [k for k, lk in _name_locks.items() if not lk.locked() and k not in _active]:
        _name_locks.pop(k, None)


class QuotaExceeded(Exception):
    """当日渲染配额用尽。"""


def _today():
    return time.strftime("%Y-%m-%d")


def daily_used():
    if _daily["date"] != _today():
        _daily["date"] = _today()
        _daily["count"] = 0
    return _daily["count"]


def quota_remaining():
    if config.DAILY_RENDER_QUOTA <= 0:
        return None                      # None = 不限
    return max(0, config.DAILY_RENDER_QUOTA - daily_used())


def _consume_quota():
    if config.DAILY_RENDER_QUOTA > 0:
        if daily_used() >= config.DAILY_RENDER_QUOTA:
            raise QuotaExceeded(
                f"今日渲染额度已用完（{config.DAILY_RENDER_QUOTA} 条/天），明天再来"
            )
    _daily["count"] += 1


@asynccontextmanager
async def render_slot(task_name):
    """
    占一个渲染位：先查配额（快速失败）→ 等**同名任务**的前一次渲染结束
    → 排队等全局并发闸。

    配额在**排队之前**扣，是为了让"超额"立刻返回错误，而不是让用户排到队头才被告知
    今天没额度了 —— 那段时间他什么都看不到。

    同名任务要排队而不是直接拒绝：这正好符合前端契约（点重试后那一段变回
    "排队中"），用户不需要理解"上一次还在渲"。
    """
    global _waiting
    _consume_quota()

    lock = _name_lock(task_name)
    _waiting += 1
    got_lock = False
    try:
        # 顺序固定：name 锁 → 全局闸（理由见上面 _name_locks 的注释）
        await lock.acquire()
        got_lock = True
        try:
            await _render_sem.acquire()
        except BaseException:
            lock.release()
            raise
    finally:
        _waiting -= 1

    _active.add(task_name)
    try:
        yield
    finally:
        _active.discard(task_name)
        _render_sem.release()
        if got_lock:
            lock.release()
        _prune_locks()


def stats():
    return {
        "max_concurrent": config.MAX_CONCURRENT_RENDERS,
        "running": sorted(_active),
        "queued": _waiting,
        "quota_per_day": config.DAILY_RENDER_QUOTA or None,
        "quota_used_today": daily_used(),
        "quota_remaining": quota_remaining(),
    }

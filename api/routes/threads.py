# -*- coding: utf-8 -*-
"""
会话读取 / 修改接口 —— 刷新后恢复工作台，以及侧栏那套菜单。

================================================================================
写接口只在这几个动作上，为什么
================================================================================
会话的**内容**（对话、分镜、渲染产物）不是这里写的：那三个写入方各有各的位置
（`/api/chat`、`/api/render/*`、渲染管线），而且它们写的是**事实**。

这里写的全是**用户改出来的属性**：标题、置顶、分享开关。它们不属于任何渲染产物，
也不能混进 messages / threads —— 那两个文件是覆盖写，塞进去会被下一次写入带掉。
所以单独落在 `_tasks/sessions/<thread>.json`（见 store 的"会话元数据"一节）。

================================================================================
时间戳一律是**毫秒**
================================================================================
后端内部到处是 time.time()（秒），前端全是 Date.now()（毫秒）。
转换只在 `store._now_ms()` 一处做，这里透传 —— 漏一处界面上就是"1970 年前"。
"""

import secrets

from fastapi import APIRouter
from pydantic import BaseModel

from .. import config, store

router = APIRouter()


@router.get("/threads")
async def list_threads():
    """
    会话摘要列表（置顶优先，其余按最近更新倒序）。

    顺手把数据保留期限（ttl_days）带出去：超过这个天数没动过的会话，会在服务启动
    时被清理掉。界面得能提前告诉用户，否则"数据凭空消失"只会被当成 bug。
    """
    return {
        "threads": store.list_threads(),
        "ttl_days": config.TASK_TTL_DAYS,
    }


@router.get("/threads/{thread_id}")
async def thread_detail(thread_id: str):
    """
    一个会话的完整消息（含每条助手消息的渲染状态与视频地址）。

    ⚠️ 会话不存在时返回**空消息列表**，而不是 404：
       "新建会话 → 立刻跳进去"这条正常路径上，这个 id 本来就还没落到磁盘，
       报 404 会让新建会话一进页面就弹错。对使用者来说，
       "这个会话还没有消息"和"还没有这个会话"本来就是一回事。
    """
    return {
        "thread_id": thread_id,
        "messages": store.load_thread_detail(thread_id),
    }


class PatchThreadRequest(BaseModel):
    """只传要改的字段；没传的（None）一律不动。"""

    title: str | None = None
    pinned: bool | None = None


@router.patch("/threads/{thread_id}")
async def patch_thread(thread_id: str, req: PatchThreadRequest):
    """
    改标题 / 置顶。

    ⚠️ 标题在这里截断，而不是指望前端自觉：存一个几万字的标题进去，侧栏每次渲染
       都要吃它，而界面上根本看不出是"标题太长"，只会觉得"列表卡了"。
    ⚠️ 会话不存在就直接拒绝，否则会凭空造出一个只有元数据的"幽灵会话" ——
       它不会出现在列表里（列表以 messages/ 为准），却永远留在磁盘上。
    """
    patch = {}
    if req.title is not None:
        t = req.title.strip()
        if not t:
            return {"ok": False, "error": "标题不能为空"}
        patch["title"] = t[:60]
    if req.pinned is not None:
        patch["pinned"] = bool(req.pinned)
    if not patch:
        return {"ok": False, "error": "没有要改的字段"}
    if not store.thread_exists(thread_id):
        return {"ok": False, "error": "没有这个会话"}

    meta = store.save_session_meta(thread_id, patch)
    return {"ok": True, "title": str(meta.get("title") or ""),
            "pinned": bool(meta.get("pinned"))}


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """
    删掉一条会话，**连同它的全部渲染产物**（成片、分段、增量缓存一起清）。

    ⚠️ 不可撤销，而且动的是磁盘上的文件：
       - 前端必须先让用户确认。这一层拦不住任何东西，它只能保证"删的确实是这个 id"；
       - `store.delete_thread` 用**整段前缀**（`<id>__`）识别 task 目录，绝不模糊匹配
         —— 否则 `t_4rf2aue` 会误伤 `t_4rf2aue2` 的产物。
    """
    if not store.thread_exists(thread_id) and not store.load_session_meta(thread_id):
        return {"ok": False, "error": "没有这个会话"}
    removed = store.delete_thread(thread_id)
    return {"ok": True, "removed": len(removed)}


class ShareRequest(BaseModel):
    on: bool = True


@router.post("/threads/{thread_id}/share")
async def share_thread(thread_id: str, req: ShareRequest):
    """
    打开 / 关闭分享。打开时生成一个随机 token 并返回。

    ⚠️ 只返回 slug，**不返回完整 URL**：后端知道自己的地址（MSB_PUBLIC_BASE_URL 是
       media 的前缀），但不知道前端跑在哪个端口。拼出一个打不开的链接，是那种
       很难被当成 bug 看出来的错。前端有 location.origin，它来拼最准。
    """
    if not store.thread_exists(thread_id):
        return {"ok": False, "error": "没有这个会话"}

    if not req.on:
        store.save_session_meta(thread_id, {"share": None})
        return {"ok": True, "slug": ""}

    slug = str(store.load_session_meta(thread_id).get("share") or "").strip()
    if not slug:
        # 分享链接是公开的，token 得不可猜。11 个字符的 urlsafe token 够了 ——
        # 它防的是"随手试出别人的分享"，不是加密。
        slug = secrets.token_urlsafe(8)
        store.save_session_meta(thread_id, {"share": slug})
    return {"ok": True, "slug": slug}


@router.get("/share/{slug}")
async def get_share(slug: str):
    """
    公开读取一份分享（分享页用）。**不需要、也不能带鉴权** —— 拿到链接的人都能看。

    ⚠️ 只回标题、成片和分段清单，**不回对话文字**。
       会话里可能有用户的问题、追问、甚至贴进去的题目原文；分享的语义是
       "这段视频给你看"，不是"把我的对话记录也给你看"。
    """
    tid = store.find_share_slug(slug)
    if not tid:
        return {"ok": False, "error": "分享不存在，或已被取消"}
    return {"ok": True, **store.build_share_payload(tid)}

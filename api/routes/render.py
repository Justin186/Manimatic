# -*- coding: utf-8 -*-
"""
POST /api/render/confirm —— 大纲确认后的真实渲染。SSE。
POST /api/render/retry   —— 从失败的分镜起重跑。SSE。

两条路都产出同一套事件：tool_call → tool_progress* → tool_result* → tool_done → done，
只是渲染模式不同（见 pipeline.py 顶部的说明）：

    confirm  → full         一个 Scene 演到底 + 边渲边切（最快，首段 2.3s 可见）
    retry    → incremental  复用 _parts 缓存，只重渲源码变了的那些分镜

⚠️ 分镜本体从哪儿来：前端 confirm 时**只发 thread_id + message_id**（不重复发分镜），
   所以这里必须能自己找回来：先看请求里带没带，再看该 task 的快照，
   最后回落到"这个会话当前这一版分镜"（/api/chat 存下来的那份）。
"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import events, jobs, pipeline, store
from ._common import SSE_HEADERS, SSE_MEDIA_TYPE, error_stream, sse_response

router = APIRouter()


class ConfirmRequest(BaseModel):
    thread_id: str = "demo"
    message_id: str = "m"
    plan: list = []
    storyboard: dict | None = None
    # 仅 Mock 模式用来演示单分镜失败，真后端忽略（前端的注释也是这么写的）
    simulateFailure: bool = False


class RetryRequest(BaseModel):
    thread_id: str = "demo"
    message_id: str = "m"
    scene_index: int = 0
    plan: list | None = None


def _has_scenes_key(d):
    """判断"这是一份分镜本体"，而不是"空 dict / None"。"""
    return isinstance(d, dict) and "scenes" in d


def _find_storyboard(thread_id, message_id, inline=None):
    """
    按优先级找回分镜：请求体 → 该 task 的快照 → 这个会话当前这一版。

    ⚠️ 请求体里带了 storyboard 就**以它为准**（哪怕 scenes 是空数组）。
       这一条曾写成 `inline.get("scenes")` 判真，于是"显式传了一份空分镜"会被
       当成"没传"，静默回落到会话里上一次的分镜 —— 用户会看到一段跟这次请求
       完全无关的视频。空分镜是合法输入（intent=none 的纯文字回答），
       该报"没有分镜可渲"，不该拿旧的顶包。
    """
    if _has_scenes_key(inline):
        return inline
    name = store.task_name(thread_id, message_id)
    snap = store.load_task_storyboard(name)
    if _has_scenes_key(snap):
        return snap
    raw, _plan = store.load_thread_storyboard(thread_id)
    if _has_scenes_key(raw):
        return raw
    return None


@router.post("/render/confirm")
async def confirm(req: ConfirmRequest):
    name = store.task_name(req.thread_id, req.message_id)
    raw = _find_storyboard(req.thread_id, req.message_id, req.storyboard)
    if raw is None:
        return sse_response(error_stream(
            "找不到这个会话的分镜：请先调 /api/chat 生成一次，"
            "或在请求体里直接带上 storyboard", scope="task"))

    n = len(raw.get("scenes") or [])
    if n == 0:
        # intent=none 的纯文字回答走到这里：这不是"错误"，而是"这题不需要视频"。
        # 给一句能看懂的话，别丢一个"scenes 为空"的校验报错给用户。
        return sse_response(error_stream(
            "这次回答没有分镜（纯文字讲解），不需要渲染视频", scope="task"))

    async def agen():
        # tool_call 在拿并发位**之前**发：用户点了"生成"，界面上应该立刻出现工具卡片，
        # 而不是排队时一片空白（排队本身可能要等十几秒）。
        yield events.sse(*events.tool_call(n))
        try:
            slot = jobs.render_slot(name)
            async with slot:
                store.save_task_storyboard(
                    name, raw, meta={"thread_id": req.thread_id,
                                     "message_id": req.message_id, "mode": "full"})
                cancel = pipeline.new_cancel()

                def run(push):
                    pipeline.pump(pipeline.iter_full(name, raw, cancel), push)

                async for chunk in events.encode_stream(pipeline.stream(run, cancel)):
                    yield chunk
        except jobs.QuotaExceeded as e:
            # 配额是"业务上的拒绝"，用 error 事件表达，前端能给出比 HTTP 500 好得多的提示
            yield events.sse(*events.error(str(e), scope="task"))
        yield events.sse(*events.done("m_" + req.message_id))

    return StreamingResponse(agen(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)


@router.post("/render/retry")
async def retry(req: RetryRequest):
    """
    从失败的那一段起重跑。

    ⚠️ 语义（docs/分镜实时交付-契约与前端改造.md §4.1）：后端是「一个 Scene 演到底」，
    某段抛异常 → 渲染进程整体退出，**后面还没渲的分镜根本没执行过**。
    所以必须重跑 `scene_index` **及其后所有段落** —— 只补那一段会让后续分镜
    永远停在"排队中"，而成片又是拼好的，状态自相矛盾。
    对前端而言交互不变（点重试 → 那一段变回 queued → 重新交付）。
    """
    name = store.task_name(req.thread_id, req.message_id)
    raw = _find_storyboard(req.thread_id, req.message_id)
    if raw is None:
        return sse_response(error_stream(
            "找不到这个会话的分镜，无法重试（先完成一次生成）", scope="task"))

    scenes = raw.get("scenes") or []
    n = max(1, len(scenes))
    start = max(0, min(int(req.scene_index), n - 1))

    async def agen():
        try:
            async with jobs.render_slot(name):
                store.save_task_storyboard(
                    name, raw, meta={"thread_id": req.thread_id,
                                     "message_id": req.message_id, "mode": "incremental"})
                cancel = pipeline.new_cancel()

                def run(push):
                    pipeline.pump(
                        pipeline.iter_incremental(name, raw, range(start, n), cancel), push)

                async for chunk in events.encode_stream(pipeline.stream(run, cancel)):
                    yield chunk
        except jobs.QuotaExceeded as e:
            yield events.sse(*events.error(str(e), scope="task"))
        yield events.sse(*events.done("m_" + req.message_id))

    return StreamingResponse(agen(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)

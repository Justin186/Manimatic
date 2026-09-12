# -*- coding: utf-8 -*-
"""路由之间共用的东西：SSE 响应头、错误流、请求体 -> 渲染参数。"""

import json

from fastapi.responses import StreamingResponse

from .. import events

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # 告诉 nginx / 各种反向代理"别缓冲这个响应"，否则事件会被攒到流结束才一起吐出来
    "X-Accel-Buffering": "no",
}

SSE_MEDIA_TYPE = "text/event-stream; charset=utf-8"


def sse_response(agen):
    """包成 SSE 响应。agen 产出的是已经编码好的字符串。"""
    return StreamingResponse(agen, media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)


async def error_stream(message, scope="task", index=None, retryable=False):
    """
    单条错误的"流"：HTTP 一律 200，错误以 error 事件下发。

    为什么不用 4xx/5xx：前端的 api.ts 在 `!res.ok` 时是**直接 throw**，
    走的是 catch 分支（只给一句 message）；而事件分发那条路能区分
    scope=step（把某个分镜标红并自动重试）与 scope=task（整条失败）。
    想表达的错误如果被 HTTP 状态码吞掉，就丢掉了这层语义。
    """
    yield events.sse(*events.error(message, scope=scope, index=index, retryable=retryable))
    yield events.sse(*events.done("m_error"))


def describe_body(body):
    """排查日志用：别把 storyboard 整份打进日志（太长）。"""
    if isinstance(body, dict):
        return json.dumps({k: v for k, v in body.items() if k != "storyboard"},
                          ensure_ascii=False)[:400]
    return str(body)[:400]

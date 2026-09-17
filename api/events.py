# -*- coding: utf-8 -*-
"""
SSE 事件构造 —— 与 web/src/lib/types.ts 的 `AssistantEvent` 一一对应。

================================================================================
为什么要手写而不是用 sse-starlette
================================================================================
不是"不想依赖"，是**分隔符**：
前端的 readSSE 用 `buffer.indexOf("\n\n")` 切事件，而 sse-starlette 默认输出
CRLF (`\r\n\r\n`)。CRLF 里没有连续的两个 `\n`，结果是前端一个事件都切不出来 ——
连接是通的、服务端日志一切正常、UI 就是不动。这类"契约对了但分隔符不对"的 bug
排查起来极其费时，不如自己拼这 3 行字符串，把它变成看得见的东西。

Mock（web/src/app/api/_stream.ts）用的是同一套格式，所以切真后端时前端不用改一行。
"""

import json


def sse(event, data):
    """把 (事件名, 数据) 编码成一条 SSE 消息。必须以空行结尾。"""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def text_delta(text):
    return "text_delta", {"text": text}


def thinking_delta(text):
    """
    模型的思考过程增量（不是给用户看的内容）。

    ⚠️ 这是**新增事件类型**，旧前端不认它 —— 会走到"未知事件"分支被忽略，
    所以不会崩，但也看不到。前端要显示"正在思考…"就得显式处理它。
    后端默认**不发**（`MSB_LLM_SHOW_THINKING=1` 才发）：推理内容里可能含
    模型对用户的判断或本该隐藏的中间结论，是否展示是产品决定，不该是默认行为。
    """
    return "thinking_delta", {"text": text}


def plan(outline, intent):
    return "plan", {"plan": list(outline or []), "intent": intent}


def tool_call(segment_count, storyboard=None):
    """args.storyboard 可选：前端类型允许它，Mock 没带。分镜本体走 store 更省流量。"""
    args = {"segmentCount": int(segment_count)}
    if storyboard is not None:
        args["storyboard"] = storyboard
    return "tool_call", {"name": "render_storyboard", "args": args}


def tool_progress(step, total, stage):
    """
    ⚠️ `step` 的语义是「**已交付**段数」，不是"正在渲第几段"，且每个 step 只发一次。

    前端的用法（Workbench 的事件分发）：
        stage=rendering 时把 scenes[step] 从 queued 翻成 rendering
        step/total 也是进度条本身的语义
    所以正确的发法是在"开始渲第 index 段"时发 step=index（含义：前 index 段已交付）。
    """
    return "tool_progress", {"step": int(step), "total": int(total), "stage": stage}


def tool_result(index, url, duration_sec):
    """
    ⚠️ `durationSec` 必须是 manim 按**真实帧数**算出来的时长（sections 事件的 duration），
    不能用大纲的估算值 —— 前端整片时间轴、进度条、分镜定位全部用它，用错就会漂。
    """
    return "tool_result", {"index": int(index), "url": url,
                           "durationSec": round(float(duration_sec), 3)}


def tool_done(url, segment_count):
    return "tool_done", {"url": url, "segmentCount": int(segment_count)}


def error(message, scope="task", index=None, retryable=False):
    """
    scope="step"：定位到某个分镜的失败，前端会把该分镜标红并自动重试一次。
    scope="task"：整条任务失败（LLM 挂、环境缺 LaTeX…），前端只给一条错误提示。
    """
    data = {"scope": scope, "message": str(message), "retryable": bool(retryable)}
    if index is not None:
        data["index"] = int(index)
    return "error", data


def done(message_id):
    return "done", {"messageId": message_id}


# 心跳：一条 SSE **注释行**（以 `:` 开头），只为"让连接上一直有字节在动"。
#
# 为什么是注释行而不是一个新的自定义事件：
#   1. 前端的 readSSE 本来就跳过 `:` 开头的行（sse.ts::parseChunk 第一句），
#      于是心跳对前端**完全透明** —— 不用改协议、不用加事件类型、旧前端也不会被它干扰；
#   2. 它是纯粹的链路保活（防浏览器/网关把空闲连接掐掉），不是业务事件。
#      冒充业务事件会污染事件契约，将来"事件列表"里就多一个没人处理的类型。
#
# 为什么需要它：见 config.SSE_HEARTBEAT_SEC —— 模型思考期间流上长时间没有任何数据，
# 前端会看到一句"网络错误"，而任务其实还在跑。
PING = "__ping__"


async def encode_stream(agen):
    """把 (事件名, 数据) 的异步生成器编码成 SSE 文本流。"""
    async for event, data in agen:
        if event == PING:
            # 注释行也必须以空行结尾，否则会跟下一条事件粘成一段被前端整段丢弃
            yield ": ping\n\n"
        else:
            yield sse(event, data)

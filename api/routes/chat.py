# -*- coding: utf-8 -*-
"""
POST /api/chat —— 题目 → （流式文字）→ 大纲。SSE。

================================================================================
关键在**字段顺序**（详见 docs/前端接入-后端改造清单.md §3.2）
================================================================================
system prompt 要求模型按 brief → intent → outline → scenes 输出，于是流式时
brief 的字符最先到（1~2 秒），前端立刻有字可显示 —— 不需要为了"秒级见字"
把一次 LLM 调用拆成两次。这条链路上真正干活的是 llm.BriefTap：
它从流式到达的 JSON 文本里增量抠出 brief 的值（而不是等 JSON 收完再解析）。

顺序：text_delta* → plan → done。intent=none 时 plan 为空数组，这条消息就是
一次普通对话（前端 Q15：纯概念问答不该硬造动画）。
"""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from storyboard import llm, templates

from .. import config, events, pipeline, store
from ._common import error_stream, sse_response

router = APIRouter()


class ChatRequest(BaseModel):
    thread_id: str = "demo"
    message: str = ""
    # 前端会把这三样一起发过来（web/src/lib/api.ts 的 ChatRequest），
    # 它们只影响 prompt 措辞，不参与校验，所以这里用宽松的 dict 收。
    profile: dict | None = None
    overrides: dict | None = None


_ROLE_LABEL = {"student": "学生", "parent": "家长", "teacher": "教师"}


def _extra_rules(req: ChatRequest):
    """
    把前端的画像/风格/时长要求翻成给模型的一句提示。

    值得做：这几个字段前端一直在发（设置页里改），但 Mock 后端完全忽略了它们。
    真后端忽略掉的话，"我是家长"这个设置就只是装饰。
    """
    bits = []
    overrides = req.overrides or {}
    style = str(overrides.get("style") or "").strip()
    if style == "concise":
        bits.append("讲解要精简：分镜数量控制在 3~4 个，brief 不超过两句。")
    elif style == "fun":
        bits.append("语气轻松一些，可以用生活化的类比，但结论必须严谨。")
    elif style == "detailed":
        bits.append("讲解要细致：关键步骤都要停一下给读者看，不要跳步。")
    target = overrides.get("targetDurationSec")
    if isinstance(target, (int, float)) and target > 0:
        bits.append(f"整条时长尽量控制在 {int(target)} 秒左右。")

    profile = req.profile or {}
    role = _ROLE_LABEL.get(str(profile.get("role") or ""), "")
    grade = str(profile.get("grade") or "").strip()
    if role or grade:
        bits.append(f"听众是{grade}{role}，用词按这个身份拿捏。")
    return "\n".join(bits)


@router.post("/chat")
async def chat(req: ChatRequest):
    topic = (req.message or "").strip()
    if not topic:
        return sse_response(error_stream("题目是空的，先在输入框里写一道题", scope="task"))

    mid = "m_" + uuid.uuid4().hex[:12]
    cancel = pipeline.new_cancel()
    state = {"streamed": 0, "thinking": 0}

    def run(push):
        try:
            cfg = llm.resolve_config(provider=config.LLM_PROVIDER or None,
                                     model=config.LLM_MODEL or None,
                                     base_url=config.LLM_BASE_URL or None,
                                     temperature=0.2,
                                     profile=config.LLM_PROFILE or None)
        except llm.LLMError as e:
            # 配置类错误（没 key / 未知厂商）要在**发起请求前**报出来，
            # 否则会等成一次看不懂的超时。
            push(*events.error(f"LLM 配置有误：{e}", scope="task"))
            push(*events.done(mid))
            return

        # 多轮上下文：把这个会话之前的对话喂给模型。
        # 不带的话，「生成对应视频」这种追问会被模型当成"你没给题目"——
        # 实测它真的会回一句「这次只收到了一句指令，没有附带任何题干」。
        past = store.load_thread_messages(req.thread_id)
        # 把本轮用户消息**先记下来**再调用：模型要看到"最后一条是本次请求"。
        # 顺序反过来的话，历史里永远缺当前这句。
        store.append_thread_message(req.thread_id, "user", topic)

        def on_delta(text):
            state["streamed"] += len(text)
            push(*events.text_delta(text))

        # 思考流：开了深度思考的模型，正文要等思考走完才开始出字
        # （实测中转站是 49s / /api/chat 端到端首字 93s）。这几十秒里唯一在动的
        # 就是思考流，所以把它变成 thinking_delta 发出去，前端才有"正在思考…"可显示。
        #
        # ⚠️ 计两个状态而不是一个：前端靠"有没有思考/正文"来判断该收尾还是该继续等，
        # 混成一个计数会把"思考完了在写正文"误判成"什么都没发生"。
        on_thinking = None
        if config.LLM_SHOW_THINKING:
            def on_thinking(text):
                state["thinking"] += len(text)
                push(*events.thinking_delta(text))

        try:
            res = llm.stream_storyboard(
                topic, cfg, templates.TEMPLATE_REGISTRY,
                max_attempts=config.LLM_MAX_ATTEMPTS,
                # json_mode 不传：交给 llm.resolve_config() 决定（llm.local.json >
                # MSB_LLM_JSON_MODE），否则会覆盖用户在配置里写的 false
                extra_rules=_extra_rules(req),
                on_delta=on_delta,
                on_thinking=on_thinking,
                history=past,
            )
        except llm.LLMError as e:
            push(*events.error(f"生成失败：{e}", scope="task"))
            push(*events.done(mid))
            return

        sb = res["storyboard"]
        raw = res["raw"]

        # brief 一个字都没流出来（模型没按顺序输出，或流式被降级成非流式）→
        # 补发整段。不补的话用户会看到"有回答、可屏幕上没字"，比慢更难受。
        if not state["streamed"] and sb.get("brief"):
            push(*events.text_delta(sb["brief"]))

        # 助手这轮的**自然语言结论**进历史（brief），**不是**分镜 JSON：
        # 分镜几千 token，塞进去会把上下文预算吃光，也会让前缀缓存次次落空。
        # brief 里已经写清了"这一轮讲了什么"，足够支撑下一轮的指代消解。
        store.append_thread_message(req.thread_id, "assistant", sb.get("brief") or "")

        # 落盘：confirm 只会发 thread_id + message_id，分镜本体必须由后端记住。
        store.save_thread_storyboard(req.thread_id, raw, plan=sb.get("outline") or [])
        # 把"思考了多少字 / 正文多少字"打出来：开了深度思考的模型，
        # 这两个数的比例直接决定首字延迟，排查"怎么这么慢"时一眼就能看到
        # 是模型在思考还是链路卡住了。
        print(f"[api] chat topic={topic[:40]!r} -> {len(sb['scenes'])} 分镜 "
              f"intent={sb.get('intent')} attempts={res['attempts']} "
              f"streamed={res.get('streamed')} "
              f"思考={state['thinking']}字 正文={state['streamed']}字", flush=True)

        push(*events.plan(sb.get("outline") or [], sb.get("intent") or "propose"))
        push(*events.done(mid))

    return sse_response(events.encode_stream(pipeline.stream(run, cancel)))

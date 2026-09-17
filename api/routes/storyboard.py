# -*- coding: utf-8 -*-
"""
POST /api/storyboard/replace-scene —— 整分镜替换（对应前端 Q16）。

================================================================================
为什么不做"文本 patch"
================================================================================
分镜内部的 `duration` 与 `timeline` 里各动作的 `run_time` 是**耦合**的：
只把 duration 从 6 改成 10，而不动 timeline，就会得到"动画 6 秒演完、后面 4 秒干等"，
或者反过来"时间不够被硬切" —— 而且这两种**都不会报错**，正是本项目最想避免的静默失败。

所以这一层让模型**重新输出一个完整的分镜对象**，再过一遍同一套 dsl/schema 校验，
最后只重渲这一个分镜（其余分镜命中 _parts 缓存，几秒就能出片）。

⚠️ 请求里的 `durationSec` 是"用户期望的新时长"（模型按它重排 timeline），
   而返回的 `tool_result.durationSec` 必须是**重渲后的真实帧时长** ——
   两者通常不等（run_time 只能是帧的整数倍）。时间轴必须用后者，否则定位会漂。
"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from storyboard import llm, templates

from .. import config, events, jobs, pipeline, store
from ._common import SSE_HEADERS, SSE_MEDIA_TYPE, error_stream, sse_response

router = APIRouter()


class ReplaceSceneRequest(BaseModel):
    thread_id: str = "demo"
    message_id: str = "m"
    scene_index: int = 0
    instruction: str = ""
    durationSec: float | None = None


@router.post("/storyboard/replace-scene")
async def replace_scene(req: ReplaceSceneRequest):
    instruction = (req.instruction or "").strip()
    if not instruction:
        return sse_response(error_stream("请说明要怎么改这个分镜", scope="task"))

    raw, _plan = store.load_thread_storyboard(req.thread_id)
    if not isinstance(raw, dict) or "scenes" not in raw:
        return sse_response(error_stream(
            "找不到这个会话的分镜，无法修改（先完成一次生成）", scope="task"))

    scenes = raw.get("scenes") or []
    if not scenes:
        return sse_response(error_stream(
            "这次回答没有分镜（纯文字讲解），没有可以修改的画面", scope="task"))
    idx = int(req.scene_index)
    if not (0 <= idx < len(scenes)):
        return sse_response(error_stream(
            f"分镜序号越界：{idx}（这份分镜只有 {len(scenes)} 段）", scope="task"))

    n = len(scenes)
    name = store.task_name(req.thread_id, req.message_id)

    async def agen():
        # 先让模型重画这一个分镜（十几秒，期间前端显示该分镜为排队态）
        try:
            # 同 chat 路由：temperature 由 resolve_config() 说了算（原先这里写死 0.3，
            # 会让配置文件/设置页里的温度静默失效），见 api/config.py 末尾的说明。
            cfg = llm.resolve_config(provider=config.LLM_PROVIDER or None,
                                     model=config.LLM_MODEL or None,
                                     base_url=config.LLM_BASE_URL or None,
                                     profile=config.LLM_PROFILE or None)
        except llm.LLMError as e:
            yield events.sse(*events.error(f"LLM 配置有误：{e}", scope="task"))
            yield events.sse(*events.done("m_" + req.message_id))
            return

        try:
            new_scene = llm.regenerate_scene(
                instruction=instruction,
                scene=scenes[idx],
                registry=templates.TEMPLATE_REGISTRY,
                cfg=cfg,
                context={"title": raw.get("title", ""), "index": idx, "total": n},
                duration_sec=req.durationSec,
                max_attempts=config.LLM_SCENE_ATTEMPTS,
                # json_mode 同样交给 llm.resolve_config()（见 config.py 里的注释）
            )
        except llm.LLMError as e:
            # 改不动就让用户看到原因（这一层失败**不会**影响已经渲好的成片）
            yield events.sse(*events.error(f"这个分镜没能改好：{e}", scope="task"))
            yield events.sse(*events.done("m_" + req.message_id))
            return

        # new_scene 是模型原样输出（raw），不能是 schema 规范化过的产物：
        # 整份分镜只能有一种形态，规范化只发生在 pipeline._validate() 一处。
        # 混装会让 _validate() 对同一个分镜规范化第二遍，而它并不幂等
        # （plot.x_range 的默认 None 会被当成非法范围）。详见 llm.regenerate_scene()。
        updated = dict(raw)
        updated["scenes"] = list(scenes)
        updated["scenes"][idx] = new_scene
        store.save_thread_storyboard(req.thread_id, updated, plan=raw.get("outline") or [])

        try:
            async with jobs.render_slot(name):
                store.save_task_storyboard(
                    name, updated, meta={"thread_id": req.thread_id,
                                         "message_id": req.message_id,
                                         "mode": "incremental",
                                         "replaced_index": idx})
                cancel = pipeline.new_cancel()

                def run(push):
                    # 只交付改过的那一段：其余分镜前端本来就是 done，
                    # 重发一遍虽然幂等，但会让播放器白白重新加载一次画面。
                    pipeline.pump(
                        pipeline.iter_incremental(name, updated, [idx], cancel), push)

                async for chunk in events.encode_stream(pipeline.stream(run, cancel)):
                    yield chunk
        except jobs.QuotaExceeded as e:
            yield events.sse(*events.error(str(e), scope="task"))
        yield events.sse(*events.done("m_" + req.message_id))

    return StreamingResponse(agen(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)

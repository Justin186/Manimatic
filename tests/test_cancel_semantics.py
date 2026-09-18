# -*- coding: utf-8 -*-
"""
取消语义（2026-09-18 变更）：**断开 ≠ 停止**。

为什么值得用测试钉住：
  "刷新页面不该让这一轮白跑"全靠 `pipeline.stream()` 里那几行实现，而它原先
  恰恰是相反的行为（finally 里 `cancel()`）。以后谁顺手改回去，症状是
  "用户按 F5 之后这一轮永远没有结果"，而且**不报错、不打日志** ——
  正是本项目最忌讳的那类静默失效。所以两个方向都固化成测试：

    ① 消费者走了（≈ 客户端断开） → 任务照跑完，cancel() 不许被调；
    ② 显式 `cancel_thread()`     → 任务真的停（LLMCancelled 冒出来）。

另外顺带钉住 `kind` 过滤：新对话只会掐掉同一条会话上**上一条对话生成**
（两条流同时写 messages 文件会丢消息），但前端"停止"按钮是不分类型全掐。
"""

import asyncio
import threading
import time

from api import pipeline
from storyboard import llm


# ---------------------------------------------------------------- ① 断开不断任务
def test_client_gone_does_not_cancel_task():
    """消费者中途走人：任务继续跑完，且取消令牌**不许**被触发。"""
    finished = threading.Event()

    def run(push):
        push("tool_progress", {"step": 0})
        # 比消费端活得久：模拟"任务还在跑，人已经走了"
        time.sleep(0.4)
        push("done", {})
        finished.set()

    async def main():
        cancel = pipeline.new_cancel()
        agen = pipeline.stream(run, cancel, thread_id="t_go_1", kind="chat")
        first = await agen.__anext__()
        assert first[0] == "tool_progress"
        assert pipeline.is_running("t_go_1")

        await agen.aclose()                     # ≈ Starlette 关掉这个生成器

        assert not cancel.is_set(), "断开被当成了取消 —— 刷新页面又会白跑一轮"

        async def wait_done():
            while not finished.is_set():
                await asyncio.sleep(0.02)

        await asyncio.wait_for(wait_done(), 5)
        assert not cancel.is_set()

        # 任务结束后必须注销，否则 is_running 会永远为真（前端一直显示"后台还在生成"）
        async def wait_dereg():
            while pipeline.is_running("t_go_1"):
                await asyncio.sleep(0.02)

        await asyncio.wait_for(wait_dereg(), 5)

    asyncio.run(main())


# ---------------------------------------------------------------- ② 显式取消
def test_explicit_cancel_stops_task():
    """`cancel_thread()` 之后，任务应当看到取消（真实链路里是 LLMCancelled）。"""
    cancelled = threading.Event()

    def run(push):
        push("tool_progress", {"step": 0})
        for _ in range(200):
            try:
                llm.check_cancelled()           # 真实代码里每读到一个增量就检查一次
            except llm.LLMCancelled:
                cancelled.set()
                raise                            # 与 llm.py 一样：让它冒到 stream()
            time.sleep(0.02)
        push("done", {})

    async def main():
        cancel = pipeline.new_cancel()
        agen = pipeline.stream(run, cancel, thread_id="t_stop_1", kind="chat")
        await agen.__anext__()
        assert pipeline.is_running("t_stop_1")

        assert pipeline.cancel_thread("t_stop_1") == 1

        # 生产者被中断后，流应当自己收尾（SENTINEL），不会挂住
        rest = [item async for item in agen]
        assert cancelled.wait(3), "显式取消没让任务停下来"
        assert not [c for c in rest if c[0] == "error"], "取消不该在前端弹红色报错"
        assert not pipeline.is_running("t_stop_1")

    asyncio.run(main())


# ---------------------------------------------------------------- kind 过滤
def test_cancel_thread_kind_filter_and_idempotence():
    chat, render = pipeline.new_cancel(), pipeline.new_cancel()
    pipeline.register_active("t_kind_1", chat, kind="chat")
    pipeline.register_active("t_kind_1", render, kind="render")

    # 同一条会话上有两种任务时，只掐同类（chat 路由开新对话时用这个）
    assert pipeline.cancel_thread("t_kind_1", kind="chat") == 1
    assert chat.is_set() and not render.is_set()

    # 不传 kind = 全掐 —— 前端"停止"按钮的语义
    assert pipeline.cancel_thread("t_kind_1") == 1
    assert render.is_set()

    pipeline.unregister_active("t_kind_1", chat)
    pipeline.unregister_active("t_kind_1", render)
    assert not pipeline.is_running("t_kind_1")

    # 幂等：没在跑也不报错（接口层靠这个保证"按下去总有反应"）
    assert pipeline.cancel_thread("t_kind_1") == 0
    # 空 thread_id 是"旧调用方没带"，不该炸也不该误伤
    assert pipeline.cancel_thread("") == 0
    assert pipeline.is_running("") is False

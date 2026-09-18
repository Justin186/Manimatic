# -*- coding: utf-8 -*-
"""
POST /api/cancel —— 显式停止这条会话正在跑的任务。

==============================================================================
为什么需要它（而不是继续靠"客户端断开"来取消）
==============================================================================
原先"停止"是这样实现的：前端 abort 掉 fetch → 连接断开 → Starlette 关掉 SSE
生成器 → `pipeline.stream()` 的 finally 里 `cancel()` → 模型调用/渲染子进程被掐。

问题是**断开的原因不止一种**：

    用户点"停止"           → 想停
    用户按 F5 / 关标签页    → 只是不看了，多半还想看结果
    网络抖动 / 代理掐连接   → 同上

三种在服务端长得一模一样，而按"想停"处理的那版有一个很坑的后果：
**刷新页面 = 这一轮白跑**。模型调用被掐，而 assistant 消息只在生成成功后才落盘，
于是磁盘上从来没存在过 —— 用户回来只剩自己那句提问，连"思考过程"的痕迹都没有。

所以现在把两件事分开（见 `api/pipeline.py` 顶部那段说明）：
  · 断开  = 没人看了：事件丢掉，**任务继续跑完并落盘**，刷新即可看到结果；
  · 本接口 = 用户明确要求停下：就地掐断模型调用 / kill 渲染子进程。

⚠️ 前端因此必须**先调本接口、再断本地读流**（见 `web/src/lib/streams.ts` 与
   Workbench 里停止按钮那一段）。漏掉这一步，"停止"会静默失效 ——
   按钮看起来按了、转圈停了，但服务端还在烧 token。这正是本项目最忌讳的
   那种"看起来生效了"的失效，所以两个动作放在同一处、并写了注释互相指认。
"""

from fastapi import APIRouter
from pydantic import BaseModel

from .. import pipeline, store

router = APIRouter()


class CancelRequest(BaseModel):
    # 前端手上唯一齐全的键：那条会话的 id（渲染与对话生成共用同一颗停止按钮）
    thread_id: str = ""


@router.post("/cancel")
def cancel(req: CancelRequest):
    """
    取消这条会话上正在跑的任务（对话生成 + 渲染，全掐）。

    ⚠️ 用 `def` 而不是 `async def`：`cancel()` 会 kill 子进程、close 掉 HTTP 响应，
       是阻塞调用；FastAPI 会把同步处理函数丢进线程池，不占事件循环。

    ★ 归属校验：这是**能影响别人磁盘/CPU** 的接口（知道 id 就能把别人的任务掐掉），
      与改名/删除同一档，必须校验归属。不属于自己的按"没有这个会话"处理。

    没在跑也返回 ok（cancelled=0）：停止是幂等动作，用 4xx 表达"其实没有在跑"
    只会让人以为按钮坏了 —— 与 DELETE 的取舍一致。
    """
    tid = (req.thread_id or "").strip()
    if not tid:
        return {"ok": False, "error": "缺少 thread_id"}
    if not store.thread_belongs_to_me(tid):
        return {"ok": False, "error": "没有这个会话"}
    n = pipeline.cancel_thread(tid)
    if n:
        print(f"[api] 收到停止请求：thread={tid}，已取消 {n} 个任务", flush=True)
    return {"ok": True, "cancelled": n}

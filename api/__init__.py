"""Manimatic 后端服务层：把 CLI 渲染管线包成 HTTP + SSE。

分层（每层只干一件事，边界清楚才守得住）：

    routes/*.py     HTTP 语义：读请求、判参数、选渲染模式
    pipeline.py     把子进程 stdout 的 `@@ {json}` 流转成事件
    store.py        task 目录 / 产物路径（复用同一 name = 白捡的增量渲染）
    jobs.py         并发闸 + 每日配额（别等崩了才救）
    events.py       SSE 事件构造（与前端 AssistantEvent 一一对应）

刻意**不**做的事：不重写校验层。schema.py + dsl.py 那五道闸是本项目最值钱的资产，
HTTP 只是它的外壳（见 docs/前端接入-后端改造清单.md §1）。

为什么在这里插 sys.path：`storyboard/` 与本包同级，uvicorn 由 `api.main:app` 起服务时
能不能 import 到它，取决于 cwd —— 靠 cwd 是脆的（换个目录启动就 ImportError），
显式插一行更省心。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

__version__ = "0.1.0"

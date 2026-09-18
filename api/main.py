# -*- coding: utf-8 -*-
"""
Manimatic 后端服务入口。

启动：
    cd MathStoryboard
    python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

前端切到真后端（web/.env.local，组件一行不用改）：
    NEXT_PUBLIC_USE_MOCK=false
    NEXT_PUBLIC_API_BASE_URL=/msb     # 同源前缀，由 Next 转发到本服务（web/next.config.ts）

**本服务不必对外监听**：局域网用的人访问的是 Next 的 3000 端口，API 与视频都由
Next 在同一台机器上转发过来。要改这一点，就改 web/next.config.ts 里的 BACKEND。

启动时自检一次环境（LaTeX / ffmpeg / LLM 密钥），把结论打进日志 ——
这四样里少任何一样都会在渲染时才炸，而那时用户已经在等视频了。
"""

import contextlib
import importlib.metadata
import json
import os
import shutil
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from storyboard import latex_env

from . import __version__, auth, config, jobs, store
from .routes import chat as chat_routes
from .routes import render as render_routes
from .routes import settings as settings_routes
from .routes import storyboard as storyboard_routes
from .routes import threads as threads_routes


@contextlib.asynccontextmanager
async def lifespan(_app):
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(os.path.join(config.OUTPUT_DIR, "videos"), exist_ok=True)
    os.makedirs(store.tasks_root(), exist_ok=True)
    # 账号：建表 + 确保超级管理员存在。**必须在横幅之前** —— 初始密码要靠横幅喊出来。
    auth.startup()
    _banner()
    try:
        result = store.cleanup_stale()
        if result.get("removed"):
            print(f"[api] 启动清理：移除 {len(result['removed'])} 个过期 task", flush=True)
    except Exception as e:                                    # noqa: BLE001
        # 清理失败绝不能挡住服务启动
        print(f"[api] 启动清理失败（已忽略）：{e}", flush=True)
    yield


app = FastAPI(
    title="Manimatic API",
    version=__version__,
    description="一道题 → 带推导过程的讲解动画：SSE 流式生成 + 逐分镜实时交付。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_routes.router, prefix="/api", tags=["chat"])
app.include_router(render_routes.router, prefix="/api", tags=["render"])
app.include_router(storyboard_routes.router, prefix="/api", tags=["storyboard"])
app.include_router(settings_routes.router, prefix="/api", tags=["settings"])
app.include_router(threads_routes.router, prefix="/api", tags=["threads"])

# 账号体系：闸门（/api/** 需登录）+ `/api/auth/*` 五个端点。
# ⚠️ 必须在这五个 include_router **之后**装：它要把闸门追加到中间件栈的
#    最内层（CORS 之内），而栈在首次请求时才装配（见 auth/guard.py 文件头）。
auth.install(app)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/docs")


@app.get("/api/health")
async def health():
    """
    环境自检 + 生效配置 + 并发情况。

    特意把"生效的配置值"也吐出来：这几个开关全走环境变量，
    排查"到底读没读到我设的那个值"靠猜最费时间。
    """
    tex = latex_env.detect()
    try:
        manim_version = importlib.metadata.version("manim")
    except Exception:
        manim_version = ""
    return {
        "status": "ok",
        "version": __version__,
        "python": sys.version.split()[0],
        "manim": manim_version,
        "latex": {"available": tex["available"], "reason": tex.get("reason", "")},
        "ffmpeg": shutil.which("ffmpeg") or "",
        "ffprobe": shutil.which("ffprobe") or "",
        "config": config.as_dict(),
        "jobs": jobs.stats(),
    }


@app.post("/api/admin/cleanup")
async def cleanup(days: int | None = None):
    """
    清理过期的 task 目录 / 增量缓存 / 产物。

    ⚠️ 只删"我们自己建过的 task"（以 output/_tasks/ 下的目录名为准），
       不去扫 output/ 反推 —— 那里还有 examples 的历史产物和 benchmark 目录。
    """
    return store.cleanup_stale(days)


def _banner():
    lines = [
        "",
        "=" * 68,
        f" Manimatic API v{__version__}  →  http://localhost:{os.environ.get('PORT', '8000')}/docs",
        "-" * 68,
    ]
    tex = latex_env.detect()
    lines.append(f" LaTeX   : {'可用' if tex['available'] else '不可用 → ' + str(tex.get('reason'))}")
    lines.append(f" ffmpeg  : {shutil.which('ffmpeg') or '未找到（分段无法拼接）'}")
    try:
        from storyboard import llm
        cfg = llm.resolve_config()
        key = "已配置" if cfg.get("api_key") else "未配置（/api/chat 会返回明确的错误事件）"
        # 多档案下"到底用了哪一档"最容易搞错，所以档案名和模型一起打
        lines.append(f" LLM     : [{cfg.get('profile') or 'default'}] "
                     f"{cfg['model']} @ {cfg['base_url']}，密钥{key}")
        lines.append(f" LLM 选项: json_mode={cfg.get('json_mode')}  "
                     f"temperature={cfg.get('temperature')}  "
                     f"max_tokens={cfg.get('max_tokens')}")
        # extra_body 打出来是**必需的**，不是可选的调试信息：
        # 它现在承载"关思考"这类开关，而"模型为什么不吐正文/为什么在思考"
        # 正是最难靠猜判断的一件事（§8.69 那几个坑都在这一带）。
        if cfg.get("extra_body"):
            lines.append(" LLM 私有 : " + json.dumps(cfg["extra_body"], ensure_ascii=False))
        # 提示词体积：以前只能手工量。示例越攒越多时，这个数决定"规则会不会被淹没"
        # （窗口是 1M，撑不爆；要盯的是质量稀释，见 HANDOFF §8.59）。
        st = llm.prompt_stats()
        lines.append(f" 提示词  : {st['tokens']} token（{st['chars']} 字符，"
                     f"其中示例 {st['example_tokens']}，预算 {st['budget']}）")
        if cfg.get("headers"):
            # 有些中转站不带浏览器头会 403，把"额外带了哪些头"打出来省得怀疑配置没生效
            lines.append(f" LLM 头  : {', '.join(sorted(cfg['headers']))}")
        # 调用留档：出问题和"省了多少钱"都靠它，把路径打出来省得找。
        # 打印的是实际生效路径（受 MSB_LLM_LOG_DIR 影响），不是"默认值 + 一句说明"。
        from storyboard import llm_log
        lines.append(f" LLM 留档: {llm_log.calls_path()}")
        if llm.replay_enabled():
            # 回放会跳过真实调用 —— 必须在最显眼的地方喊出来，否则就是静默失效
            lines.append(" LLM 回放: **已开启**（MSB_LLM_REPLAY=1）—— "
                         "命中留档时不调模型，只念旧答案")
        if config.LLM_SHOW_THINKING:
            lines.append(" LLM 思考: **thinking_delta 已开启** —— "
                         "模型的推理过程会推给前端（默认关，推理内容不等于给用户看的话术）")
    except Exception as e:                                    # noqa: BLE001
        lines.append(f" LLM     : 配置有误 → {e}")
    lines.append(f" 产物目录 : {config.OUTPUT_DIR}")
    # 「数据会不会被清」是最容易被怀疑且最难查的一类问题，所以把结论直接打出来。
    # 默认是**永久保留**：会话记录与渲染产物都不自动清，只有用户手动删会话才会动磁盘。
    lines.append(
        f" 保留策略 : {config.TASK_TTL_DAYS} 天后清理渲染产物（会话记录永久保留）"
        if config.TASK_TTL_DAYS
        else " 保留策略 : 永久保留（未开启任何自动清理）"
    )
    lines.append(f" 媒体前缀 : {config.PUBLIC_BASE_URL}/media/")
    lines.append(f" 并发上限 : {config.MAX_CONCURRENT_RENDERS} 条同时渲染")
    lines.append("=" * 68)
    print("\n".join(lines), flush=True)


# 启动逻辑见文件上方的 lifespan（FastAPI 新版里 on_event 已废弃，
# 用 lifespan 既能做同样的事，又不会在启动日志里留一条 DeprecationWarning）。


# /media 挂的是 output/videos（而不是整个 output/）：
# 对外只需要暴露成片与分段，把 Tex/、_parts/ 这些中间产物也暴露出去没有意义，
# 还会把"哪些是内部结构"这件事变得含糊。
_videos = os.path.join(config.OUTPUT_DIR, "videos")
os.makedirs(_videos, exist_ok=True)
app.mount("/media", StaticFiles(directory=_videos, html=False), name="media")

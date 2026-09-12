# -*- coding: utf-8 -*-
"""服务配置：全部可用环境变量覆盖，默认值面向"本机先跑通"。

为什么不用 pydantic-settings / .env 那一套：配置项就这么十来个，而每多一个依赖
就多一类"装了却版本不对"的环境坑（本项目已经吃过 MiKTeX 找不到、GBK 解码崩的亏）。
环境变量 + 露一个 /api/health 把生效值打出来，排查成本比框架低。
"""

import json
import os

# 本仓库根目录（MathStoryboard/）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_str(name, default=""):
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else default


def _env_int(name, default):
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name, default):
    v = _env_str(name, "").lower()
    if not v:
        return default
    return v not in ("0", "false", "no", "off")


# ---- 产物 ----
# output/ 是渲染管线的默认根目录，_parts/ 的增量缓存也在它下面。
OUTPUT_DIR = os.path.abspath(_env_str("MSB_OUTPUT_DIR", os.path.join(ROOT, "output")))

# 返回给前端的视频 URL 前缀。必须是**绝对地址**：
# 前端拿到 url 直接塞进 <video src>，相对路径会打到 Next.js 那个端口（3000）而不是后端（8000）。
PUBLIC_BASE_URL = _env_str("MSB_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# 前端是另一个端口，SSE 的 fetch 是跨域请求 —— 默认放开，生产按需收紧。
CORS_ORIGINS = [s.strip() for s in _env_str("MSB_CORS_ORIGINS", "*").split(",") if s.strip()]

# ---- 渲染参数（与 generate.py 的 CLI 默认值保持一致）----
QUALITY = _env_str("MSB_QUALITY", "l")
CN_FONT = _env_str("MSB_FONT", "STZhongsong")
USE_LATEX = _env_bool("MSB_USE_LATEX", True)
PREWARM = _env_bool("MSB_PREWARM", True)
FAST = _env_bool("MSB_FAST", False)
RESOLUTION = _env_str("MSB_RESOLUTION", "")
FPS = _env_int("MSB_FPS", 0)

# 单条 8 分镜任务峰值会吃满 min(8, CPU核数) 个核，多用户同时来会直接打爆机器。
# MVP 用信号量足够，不必上 Celery（见 docs/前端接入-后端改造清单.md §6）。
MAX_CONCURRENT_RENDERS = max(1, _env_int("MSB_MAX_CONCURRENT_RENDERS", 2))

# 每日渲染配额，0 = 不限。真上生产建议换 Redis 计数（多进程/多实例下内存计数会各算各的）。
DAILY_RENDER_QUOTA = max(0, _env_int("MSB_DAILY_RENDER_QUOTA", 0))

# task 目录 / _parts 缓存的保留天数，0 = 关闭清理。
# 清理只删"我们自己建过的 task 目录"，绝不去动 output/ 下的历史产物。
TASK_TTL_DAYS = max(0, _env_int("MSB_TASK_TTL_DAYS", 14))

# 单条渲染的超时（秒）。前端/网关的超时要调得比它更大（≥120s）。
RENDER_TIMEOUT = max(30, _env_int("MSB_RENDER_TIMEOUT", 900))

# LLM 最多调用几轮（含校验失败回灌重试）
LLM_MAX_ATTEMPTS = max(1, _env_int("MSB_LLM_ATTEMPTS", 4))
LLM_SCENE_ATTEMPTS = max(1, _env_int("MSB_LLM_SCENE_ATTEMPTS", 3))

# 注：json_mode（是否强制 response_format=json_object）**不在这里定义**。
# 它的唯一归属是 storyboard/llm.py::resolve_config()，优先级：
# llm.local.json → 环境变量 MSB_LLM_JSON_MODE → 默认 true。
# 之前这里也存了一份并显式传给 LLM 调用，结果是"配置文件里的
# "json_mode": false 被这里传的 True 覆盖掉"——一个典型的静默失效。
# 现在路由一律不传该参数，由 resolve_config() 说了算。

# 额外的 LLM 请求头（JSON 串）。有些中转站前面挂着 Cloudflare 一类防护，
# 不带浏览器 UA / Referer 会直接 403（实测 `error code: 1010`）。
# 更常见的做法是写进 llm.local.json 的 "headers"，这个环境变量是给部署用的。
LLM_HEADERS = _env_str("MSB_LLM_HEADERS", "")

# 是否把模型的**思考过程**也通过 SSE 发出去（thinking_delta 事件）。默认**关**。
#
# 为什么默认关：推理内容不是给用户看的话术，里面可能有模型对用户的判断、
# 或者本该藏起来的中间结论。要不要展示是产品决定，不该是默认行为。
# 打开的价值：开了深度思考的模型正文要等思考走完才出字（实测首字 93s），
# 这几十秒里唯一在动的就是思考流 —— 不发给前端，用户就只能干等。
LLM_SHOW_THINKING = _env_bool("MSB_LLM_SHOW_THINKING", False)

# 显式指定模型 / base_url（不给就走 llm.local.json → 厂商预设）
LLM_MODEL = _env_str("MSB_MODEL", "")
LLM_BASE_URL = _env_str("MSB_BASE_URL", "")
LLM_PROVIDER = _env_str("MSB_PROVIDER", "")

# 多模型档案：用 llm.local.json 里 profiles 下的哪一个。
# 空 = 用文件里 active 指定的那个（所以默认不用管它）。
# 部署时"这台机器用哪档"用环境变量即可，不必去改配置文件。
LLM_PROFILE = _env_str("MSB_LLM_PROFILE", "")

# LLM 调用留档目录（output/_llm/calls.jsonl）。默认写在 OUTPUT_DIR 下；
# 想放到磁盘别处（比如不想跟产物一起被清）就设 MSB_LLM_LOG_DIR。
# ⚠️ 这里只影响"给人看的展示路径"，实际写入仍由 storyboard/llm_log.py 决定
# （它必须能在没有 api/config.py 的场景下独立工作，CLI 也用它）。
LLM_LOG_DIR = _env_str("MSB_LLM_LOG_DIR", "")


def as_dict():
    """给 /api/health 用：把生效配置露出来，省得猜"到底读没读到环境变量"。"""
    return {
        "output_dir": OUTPUT_DIR,
        "public_base_url": PUBLIC_BASE_URL,
        "quality": QUALITY,
        "cn_font": CN_FONT,
        "use_latex": USE_LATEX,
        "prewarm": PREWARM,
        "fast": FAST,
        "max_concurrent_renders": MAX_CONCURRENT_RENDERS,
        "daily_render_quota": DAILY_RENDER_QUOTA,
        "task_ttl_days": TASK_TTL_DAYS,
        "render_timeout": RENDER_TIMEOUT,
        "llm_max_attempts": LLM_MAX_ATTEMPTS,
        "llm_replay": _env_bool("MSB_LLM_REPLAY", False),
        "llm_show_thinking": LLM_SHOW_THINKING,
        "llm_profile": LLM_PROFILE,
        "llm_log_dir": LLM_LOG_DIR,
        "llm_extra_headers": sorted(json.loads(LLM_HEADERS).keys())
        if LLM_HEADERS and LLM_HEADERS.startswith("{") else [],
        "cors_origins": CORS_ORIGINS,
    }

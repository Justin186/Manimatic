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

# 返回给前端的视频 URL 前缀。
#
# ⚠️ 前端会把 {前缀}/media/... 之类**绝对地址**改写成同源地址再交给 Next 转发
#    （web/src/lib/api.ts::mediaUrl，rewrites 在 web/next.config.ts）。
#    所以这里写 localhost 是安全的：它只是"地址的写法"，不会被访问者的浏览器直接拿去请求 ——
#    否则局域网里那个 localhost 会指访问者自己的机器，视频必然 404。
#    如果将来换 CDN，把这里改成 CDN 域名即可，前端只认 pathname 是 /media/ 的地址、其余原样放行。
PUBLIC_BASE_URL = _env_str("MSB_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

# 前端是另一个端口，SSE 的 fetch 是跨域请求 —— 默认放开，生产按需收紧。
CORS_ORIGINS = [s.strip() for s in _env_str("MSB_CORS_ORIGINS", "*").split(",") if s.strip()]

# ---- 渲染参数（与 generate.py 的 CLI 默认值保持一致）----
QUALITY = _env_str("MSB_QUALITY", "l")
CN_FONT = _env_str("MSB_FONT", "STZhongsong")
# 数字/字母单独一种字体。**不能靠中文字体自动 fallback**：华文中宋自带拉丁字形，
# Pango 只对"当前字体缺字形"的字符去找后备字体，于是数字字母会一直是宋体的西文。
LATIN_FONT = _env_str("MSB_LATIN_FONT", "Times New Roman")
USE_LATEX = _env_bool("MSB_USE_LATEX", True)
PREWARM = _env_bool("MSB_PREWARM", True)
FAST = _env_bool("MSB_FAST", False)
RESOLUTION = _env_str("MSB_RESOLUTION", "")
FPS = _env_int("MSB_FPS", 0)

# manim 的产物目录名（{高度}p{帧率}）。**实现只有这一处**：
# pipeline 写产物要用它，store 读产物（恢复渲染状态）也要用它，而 store 不能反向
# import pipeline（pipeline 已经 import store，会成环）。各写一份的话迟早漂移 ——
# 一旦不一致，恢复出来的视频地址会整片 404，且不会报任何错。
QUALITY_DIRS = {"l": "480p15", "m": "720p30", "h": "1080p60",
                "p": "1440p60", "k": "2160p60"}


def quality_dir_name():
    """manim 的产物目录名：{高度}p{帧率}。与 generate.py::quality_tag 保持一致。"""
    if RESOLUTION:
        h = RESOLUTION.split(",")[1].strip()
        return f"{h}p{FPS}" if FPS else f"{h}p30"
    return QUALITY_DIRS.get(QUALITY, "480p15")

# 单条 8 分镜任务峰值会吃满 min(8, CPU核数) 个核，多用户同时来会直接打爆机器。
# MVP 用信号量足够，不必上 Celery（见 docs/前端接入-后端改造清单.md §6）。
MAX_CONCURRENT_RENDERS = max(1, _env_int("MSB_MAX_CONCURRENT_RENDERS", 2))

# 每日渲染配额，0 = 不限。真上生产建议换 Redis 计数（多进程/多实例下内存计数会各算各的）。
DAILY_RENDER_QUOTA = max(0, _env_int("MSB_DAILY_RENDER_QUOTA", 0))

# 渲染产物（task 快照 / 增量缓存 / 成片）的保留天数。**默认 0 = 不自动清理。**
#
# ⚠️ 这里只管**产物**：会话数据（对话记录、分镜快照、标题/置顶/分享）**永不自动清理**。
#    以前这两样混在一起按 TTL 删，后果是"清理磁盘"会顺带把会话历史也清掉 ——
#    用户只会看到"过一阵我的对话就没了"，而且完全不知道是自己配的那个天数干的。
#    现在分清了：文字永久保留，只有视频这类大文件可以按需清
#    （被清掉的消息恢复出来只是没有视频，大纲和文字都还在，重新点确认还能再渲）。
TASK_TTL_DAYS = max(0, _env_int("MSB_TASK_TTL_DAYS", 0))

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
        "latin_font": LATIN_FONT,
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

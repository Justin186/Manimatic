# -*- coding: utf-8 -*-
"""
LLM 接入层：把「一道数学题」变成一份**已经通过校验的**分镜 JSON。

================================================================================
这一层守的是什么
================================================================================
决策 1 的底线：大模型只输出受 Schema 约束的 JSON，**一个字符的 Manim 代码都不写**。
所以本模块做三件事，一件都不多：

    build_system_prompt()  把 dsl.describe() 的规格 + 红线规则拼成 system prompt
    chat()              调一次 OpenAI 兼容的 /chat/completions
    generate_storyboard()  调 LLM → 解析 JSON → 过 schema/dsl 校验 → 不过就带着
                        错误信息回灌重试，直到通过或次数用尽

================================================================================
为什么要"带错误回灌"的重试，而不是"失败了让用户重跑"
================================================================================
PROJECT_KNOWLEDGE 的 D7 门是「一次成功率 ≥85%、三次内 ≥95%」。
没有任何模型能一次就满足 20 种元素 × 26 种动作的引用完整性约束 ——
"引用了没声明的 id"、"颜色拼成 BLUEISH"、"duration 写成 13" 这类错误必然发生。

关键是：**这些错误校验层已经能精确定位**（schema/dsl 的报错都带路径和原因），
把它们原样回灌给模型，模型几乎总能一次改对。这比让用户手动调 JSON 高一个数量级。

所以重试不是"再试一次碰运气"，是**把校验层的确定性注入到模型的下一轮**。

================================================================================
为什么不引入 openai SDK
================================================================================
requirements.txt 只有 manim + numpy。为了少一个依赖（以及少一类"装了 SDK 却版本不兼容"
的坑），这里直接用标准库 urllib 发一个 POST。OpenAI 兼容接口就是 JSON in / JSON out，
没有流式、没有 function call 的需求，SDK 带来的主要是便利而非能力。
"""

import contextvars
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from . import dsl
from . import llm_log
from . import schema


class LLMError(Exception):
    """LLM 调用/解析/重试耗尽。渲染前抛出，绝不带到渲染阶段。"""
    pass


class LLMCancelled(Exception):
    """
    用户主动停止（客户端断开），模型调用被就地中断。

    ⚠️ **刻意不做 LLMError 的子类**，这一点是整个机制的关键：
       `chat_stream` / `stream_storyboard` / `generate_storyboard` 里全都是
       `except LLMError`，而那些分支的语义是"这次调用失败，换个方式再来一遍"
       —— 尤其是 `stream_storyboard` 那条"流式不可用 → 退回非流式重试"的降级，
       会把取消当故障处理，**立刻再发一次完整的 LLM 请求**。
       用户按的是"停止"，结果换来一轮更长时间的等待和账单，比不修还糟。

    不继承 LLMError，它就会穿过所有那些 `except LLMError`，一路冒到
    `api/pipeline.py::stream()` 的线程体里被单独接住 —— 那里知道"这是取消，不是故障"。
    """


# ==============================================================================
# 取消令牌：让"用户点了停止"能真正掐断模型调用
# ==============================================================================
# 背景（这是一个真实修过的 bug）：停止按钮原先**停不掉模型**。
#
# `api/pipeline.py::Canceller` 只登记**子进程**（渲染用），而 LLM 调用是
# `for raw in resp:` 阻塞读一条 HTTP 流 —— 没有任何东西会去中断它。所以点了停止：
# 浏览器那条连接确实断了、`stream()` 的 finally 也确实调了 `cancel()`，
# 但没有任何进程可杀、没有连接可关 → 工作线程继续阻塞在 read 上，
# **模型继续吐 token、继续计费**；`push()` 因为事件循环已关而静默丢弃，
# 最后还会把整份结果落盘。用户以为停了，账单和 CPU 都不同意。
#
# 为什么走 ContextVar 而不是"给每个函数加参数"：
#   `chat` / `chat_stream` 的调用方有五六处（普通问答、分镜生成、重画分镜…），
#   加参数就得逐个改签名，漏一处就静默退化回"停不掉"。
#   取消是**这一次请求**的上下文属性，不是业务参数，ContextVar 正好对应。
#
# ⚠️ ContextVar 不会自动进入新线程 —— 所以 `pipeline.stream()` 必须在
#    **起线程之后、跑 run() 之前**在**那个线程里**设置它。见 api/pipeline.py。
_CANCEL_TOKEN = contextvars.ContextVar("msb_llm_cancel", default=None)


def set_cancel_token(token):
    """
    在当前上下文里登记取消令牌。返回 token，供调用方 reset（用完必须还原）。

    令牌的契约（鸭子类型，避免 llm.py 反向依赖 api/）：
        token.is_set()          -> bool，是否已被取消
        token.watch(obj)        -> 登记一个"可强制中断"的资源（如 HTTP 响应），
        token.unwatch(obj)      -> 注销
        取消时令牌负责对每个 watch 过的对象调用 obj.close()
    """
    return _CANCEL_TOKEN.set(token)


def reset_cancel_token(token):
    try:
        _CANCEL_TOKEN.reset(token)
    except (ValueError, LookupError):
        pass


def current_cancel_token():
    return _CANCEL_TOKEN.get()


def check_cancelled():
    """
    若已取消则抛 LLMCancelled，否则什么都不做。

    调用点有两种：
      · 循环里（每收到一个增量调一次）—— 保证"停止"最多延迟一个增量生效；
      · 建连前 —— 已经取消的请求不该再白发一次网络请求。
    """
    token = _CANCEL_TOKEN.get()
    if token is not None and token.is_set():
        raise LLMCancelled("用户已停止生成")


# ==============================================================================
# 厂商预设
# ==============================================================================
#
# 全部走 OpenAI 兼容协议，所以切换厂商 = 换 base_url + model，代码一行不用改。
# base_url 必须**带版本路径**（/v1、/v4 这类），程序只会在末尾拼 /chat/completions。
# 各家型号名在变，这里的默认值只求"能跑通"，正式使用以官方文档为准。
PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "qwen": {   # 阿里云百炼 / DashScope 的 OpenAI 兼容模式
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "glm": {    # 智谱
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "key_env": "ZHIPUAI_API_KEY",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-32k",
        "key_env": "MOONSHOT_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "key_env": "OPENAI_API_KEY",
    },
}

DEFAULT_PROVIDER = "deepseek"

# 多模型档案：llm.local.json 可以写成
#     {"active":"relay", "profiles":{"relay":{...}, "direct":{...}}}
# 改 `active` 就换模型，不用删改配置。文件里**没有** profiles 时按"整个文件就是
# 一份配置"的旧形态读，老配置一个字都不用改。
DEFAULT_PROFILE = "default"

# 环境变量名（按顺序取第一个非空的）：
#   MSB_* 是本项目专用；LLM_* / OPENAI_* 是通用习惯，方便已有的环境直接复用。
_ENV_BASE_URL = ("MSB_BASE_URL", "LLM_BASE_URL", "OPENAI_BASE_URL")
_ENV_MODEL = ("MSB_MODEL", "LLM_MODEL", "OPENAI_MODEL")
_ENV_PROVIDER = ("MSB_PROVIDER", "LLM_PROVIDER")


def _first_env(names, default=""):
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
    return default


def _key_from_env(provider):
    """按厂商各自的环境变量名找密钥，再退回通用名。"""
    names = []
    preset = PROVIDERS.get(provider)
    if preset:
        names.append(preset["key_env"])
    names += ["MSB_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY", "API_KEY"]
    return _first_env(names)


def config_path():
    """可选的本地配置文件 —— 不想设环境变量的人把密钥写这里。"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "llm.local.json",
    )


def _as_bool(v, default=False):
    """把 '1' / 'true' / '0' / 'false' / 布尔 之类统一成 bool；认不出来就用 default。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "n"):
            return False
    return default


def effective_json_mode(cfg, json_mode=None):
    """
    决定这次请求要不要带 `response_format={"type":"json_object"}`。

    为什么要有这么个函数：`json_mode` 既可能来自 llm.local.json / 环境变量，
    也可能被调用方显式传进来（CLI 的 --no-json-mode）。谁说了算必须有唯一答案，
    否则就是"配置文件里明明写 false 却不生效"的静默失败。

    规则：**显式参数（非 None）> cfg 里的值**。调用方想交给配置决定就传 None。
    """
    if json_mode is not None:
        return bool(json_mode)
    return _as_bool(cfg.get("json_mode"), True)


def replay_enabled():
    """
    是否开启「回放」：命中留档就不再发真实请求。

    默认**关**，因为回放会掩盖"其实没有调模型"这件事 —— 本项目最忌讳的静默失效。
    要省钱/反复调 prompt 时显式打开：`set MSB_LLM_REPLAY=1`。
    """
    return _as_bool(_first_env(("MSB_LLM_REPLAY",)), False)


def _merge_extra_body(payload, cfg):
    """
    把 `cfg["extra_body"]` 原样并进请求体（**不覆盖**已存在的标准字段）。

    为什么要留这个出口：各厂商"关思考 / 调思考"的参数名完全不统一，而且都不是
    OpenAI 标准 —— DeepSeek 官方是 `{"thinking": {"type": "disabled"}}`，
    别家还有 `enable_thinking` / `reasoning_effort` 等等。在代码里硬编码任何一家
    都会误导下一家适配（而且没法改）。所以做成透传槽位，由使用方按文档填进档案。

    ⚠️ **不覆盖标准字段**（`payload.setdefault`）：
       用户手滑在 extra_body 里写个 `model` 是不该生效的 —— 那会让"我明明切了档
       却还是老模型"这种静默失效重新出现。标准字段的唯一归属是 resolve_config()。
    """
    extra = cfg.get("extra_body") or {}
    if not isinstance(extra, dict):
        return
    for k, v in extra.items():
        payload.setdefault(str(k), v)


def _merge_cfg(cfg, src, from_file, only_missing=False):
    """
    把一份配置字典合并进 cfg（llm.local.json 的两种形态共用这一份逻辑）。

    Args:
        from_file: 就地累加"来源是文件"的键名。这在多档案下依然成立：
            档案在文件里，所以它提供的值就该压过环境变量。
        only_missing: True 时**只补"档案没给过"的键**（给顶层的"公共默认值"用）。

    ⚠️ only_missing 的判据必须是 `k not in from_file`，**不能**写成
    "cfg[k] 是不是空值"：`temperature`/`max_tokens`/`timeout` 在 cfg 里本来就带着
    非空默认值（0.2 / 8192 / 180），按"空值"判断的话，用户写在顶层的公共默认值
    会被**永远忽略**而且不报错 —— 又是一种静默失效。
    """
    if not isinstance(src, dict):
        return
    for k, v in src.items():
        if k.startswith("_"):
            # 以 _ 开头的是给人看的注释（如 "_max_tokens_why"），不是配置
            continue
        if k == "headers":
            # headers 是 dict，不能走下面那个标量过滤（否则会被整个丢掉，
            # 表现为"配置明明写了却还是 403"）
            if isinstance(v, dict):
                if only_missing and k in from_file:
                    continue
                cfg["headers"] = {str(a): str(b) for a, b in v.items() if b}
                from_file.add(k)
            continue
        if k == "extra_body":
            # extra_body 也是 dict（原样进请求体的服务商私有参数）。
            # ⚠️ 与 headers 同理：必须在这里显式处理，不能指望下面那行
            #    `if k in cfg` 兜住 —— 那个判断只放行**已有键**，
            #    而 extra_body 的每个子键（thinking / reasoning_effort…）都不在 cfg 里。
            #    不写这一段的话，配置里写了等于没写（§8.2 那个静默失效的同一形态）。
            if isinstance(v, dict):
                if only_missing and k in from_file:
                    continue
                cfg["extra_body"] = dict(v)
                from_file.add(k)
            continue
        if k in cfg and v is not None and v != "":
            if only_missing and k in from_file:
                continue
            # bool 用显式 `is not None` 判断：False 是**合法配置**（json_mode: false），
            # 写成真值判断会把它当"没给"——这正是当初 json_mode 静默失效的形态之一
            cfg[k] = v
            from_file.add(k)


def resolve_config(provider=None, model=None, base_url=None, api_key=None,
                   temperature=None, max_tokens=None, timeout=None, headers=None,
                   json_mode=None, profile=None):
    """
    三级覆盖，优先级从低到高：厂商预设 → 本地 llm.local.json → 环境变量 → 显式参数。

    Args:
        profile: 多模型档案名（`llm.local.json` 的 `profiles` 里的键）。
            不传 = 用文件里 `active` 指定的那个。
            这是**按优先级参与**的：比文件里的 active 高，但比环境变量/显式参数低，
            所以"这一次想换个模型试试"只需 `--profile direct`，不用改文件。
        headers: 额外的 HTTP 请求头。**不是所有 OpenAI 兼容接口都只看 Authorization**：
            有些中转站前面挂着 Cloudflare 一类的防护，不带浏览器 UA/Referer 会直接
            403（实测 `error code: 1010`）。这类站点把默认头写进 llm.local.json 的
            "headers" 即可，代码不用为每家写分支。
        json_mode: 是否强制 JSON 输出（`response_format`）。**默认 None = 交给配置决定**，
            不要在这里写死 True/False —— 有些模型（如 deepseek-reasoner）不支持
            response_format，调用方写死就会覆盖掉用户在 llm.local.json 里的设置。

    Returns:
        dict: 已经填好的配置
    """
    cfg = {"provider": DEFAULT_PROVIDER, "base_url": "", "model": "",
           "api_key": "", "temperature": 0.2, "max_tokens": 8192, "timeout": 180,
           "headers": {}, "json_mode": True, "profile": "",
           # 服务商私有参数，**原样**并进请求体（不进 payload 的标准字段）。
           # 用途：不同厂商的"关思考/调思考"开关名字完全不一样 ——
           #   DeepSeek 官方是 {"thinking": {"type": "disabled"}}
           #   有的站是 {"enable_thinking": false} / {"reasoning_effort": "..."}
           # 这些既不是 OpenAI 标准、也不该在代码里硬编码（会误导下一家适配），
           # 所以做成一个透传槽位，由使用者按自己那家服务商的文档填。
           # ⚠️ 它是**部署/档案级**参数，不是每次调用都要变的东西 ——
           #    所以只从配置文件读，不做成 resolve_config() 的显式参数。
           "extra_body": {}}
    from_file = set()          # 记录 llm.local.json 里出现过的键，用来实现"文件 > 环境变量"
    from_file = set()          # 记录 llm.local.json 里出现过的键，用来实现"文件 > 环境变量"

    # 0) 本地配置文件（存在才读；密钥不进 git）
    #
    # 文件支持两种形态，**旧形态原样兼容**（老配置一个字都不用改）：
    #   A. 扁平单模型    {"provider":"...","base_url":"...","model":"...", ...}
    #   B. 多模型档案    {"active":"relay","profiles":{"relay":{...},"direct":{...}}}
    # B 形态解决的是"想同时留着中转站和官方直连、随时切换"这个需求：一份文件里
    # 存多个模型档案，改 `active` 就换（CLI 的 --profile 也能只覆盖这一次）。
    #
    # ⚠️ 合并顺序（踩过两次才定下来的）：**先决定用哪个档案，然后只合并那一个档案**。
    # 早期版本是先合并 active 那一档、再合并指定档案，结果是"换档案只换掉档案里写了的
    # 字段，没写的字段留着上一档的值" —— 切到 direct 却继承中转站的 max_tokens=32768，
    # 直连模型会因为超过上限直接 400。**档案是一整套互相配套的参数，必须整档生效。**
    fp = config_path()
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("顶层不是对象")

            profiles = {str(k): v for k, v in (data.get("profiles") or {}).items()
                        if isinstance(v, dict)}
            if profiles:
                # 档案名的优先级：显式参数 > 环境变量 > 文件里的 active
                want = (profile or _first_env(("MSB_LLM_PROFILE",))
                        or str(data.get("active") or DEFAULT_PROFILE)).strip()
                if want not in profiles:
                    # 写错档案名必须**报错**，绝不能悄悄退到某个默认档案 ——
                    # 那会让用户以为切到了 A、实际一直在用 B，正是本项目最忌讳的静默失效。
                    raise LLMError(
                        f"配置档案 {want!r} 不存在（{os.path.basename(fp)}）。\n"
                        f"  可用档案: {sorted(profiles)}\n"
                        f"  改文件里的 \"active\"，或用 --profile / MSB_LLM_PROFILE 指定"
                    )
                cfg["profile"] = want
                _merge_cfg(cfg, profiles[want], from_file)
                # 档案之外的顶层标量算"公共默认值"，只补档案没给的键
                _merge_cfg(cfg, {k: v for k, v in data.items()
                                 if k not in ("profiles", "active")},
                           from_file, only_missing=True)
            else:
                # 旧形态：整个文件就是一份配置（老配置继续能用）
                cfg["profile"] = DEFAULT_PROFILE
                _merge_cfg(cfg, data, from_file)
            if cfg.get("provider"):
                cfg["provider"] = str(cfg["provider"]).lower()
        except LLMError:
            # 上面那个"档案名写错"是**要报给用户的配置错误**，不能被下面兜住
            raise
        except Exception as e:   # 配置文件写坏了不应该让整条链断掉
            print(f"      [WARN] 读取 {os.path.basename(fp)} 失败，已忽略：{e}")

    # 0.5) 环境变量里的 headers（JSON 串）—— 部署时不想落盘密钥/头的用这个
    env_headers = _first_env(("MSB_LLM_HEADERS",))
    if env_headers:
        try:
            parsed = json.loads(env_headers)
            if isinstance(parsed, dict):
                cfg["headers"].update({str(a): str(b) for a, b in parsed.items() if b})
        except ValueError:
            print("      [WARN] MSB_LLM_HEADERS 不是合法 JSON，已忽略")

    # 0.6) json_mode 的环境变量兜底（仅当 llm.local.json 没写时才生效）
    if "json_mode" not in from_file:
        env_jm = _first_env(("MSB_LLM_JSON_MODE",))
        if env_jm:
            cfg["json_mode"] = _as_bool(env_jm, True)

    # 1) 环境变量
    # 注：档案名 MSB_LLM_PROFILE 已经在上面选档案时用掉了（它必须在那一步参与，
    # 否则就会出现"档案已经合并完、才发现环境变量要换档"的顺序倒错）。
    cfg["provider"] = _first_env(_ENV_PROVIDER, cfg["provider"]).lower()
    cfg["base_url"] = _first_env(_ENV_BASE_URL, cfg["base_url"])
    cfg["model"] = _first_env(_ENV_MODEL, cfg["model"])
    if not cfg["api_key"]:
        cfg["api_key"] = _key_from_env(cfg["provider"])

    # 2) 显式参数（命令行）优先级最高
    if provider:
        cfg["provider"] = provider.lower()
    if base_url:
        cfg["base_url"] = base_url
    if model:
        cfg["model"] = model
    if api_key:
        cfg["api_key"] = api_key
    if headers:
        cfg["headers"].update(headers)
    for k, v in (("temperature", temperature), ("max_tokens", max_tokens),
                 ("timeout", timeout)):
        if v is not None:
            cfg[k] = v
    if json_mode is not None:
        cfg["json_mode"] = bool(json_mode)
    cfg["json_mode"] = _as_bool(cfg["json_mode"], True)
    # 记下"这一档是谁"，启动横幅/日志里要能一眼看出在用哪个模型
    cfg.setdefault("profile", DEFAULT_PROFILE)

    # 3) 预设兜底：base_url / model 没给就从预设取
    preset = PROVIDERS.get(cfg["provider"])
    if preset:
        cfg["base_url"] = cfg["base_url"] or preset["base_url"]
        cfg["model"] = cfg["model"] or preset["model"]
    if not cfg["base_url"]:
        raise LLMError(
            f"未知厂商 {cfg['provider']!r}，且没有给 base_url。\n"
            f"  可用预设: {sorted(PROVIDERS)}\n"
            f"  自定义请加 --base-url（必须带版本路径，如 https://xxx/v1）"
        )
    return cfg


def endpoint(base_url):
    """
    base_url → 完整的 chat/completions 地址。

    ⚠️ 只走 `{base}/chat/completions` 这一种形态。有几种写法这里**故意不支持**：
      - base_url 以 `/v1/chat/completions` 结尾（有些工具是这么存的）：那就正好
        走上面那条 endswith 分支；
      - 直接给域名不带版本路径（如 `https://xxx`）→ 会拼成 `https://xxx/chat/completions`，
        通常 404。**报错信息里会带上完整 URL**，照提示补 `/v1` 即可。
        不在这里做"自动补 /v1"是因为猜错的代价是静默打到别的接口，不如让用户看一眼 URL。
    """
    u = base_url.strip().rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    return u + "/chat/completions"


def _auth_headers(cfg):
    """
    认证头。

    备注一个已知的服务端差异：**Anthropic 原生协议用 `x-api-key` + `anthropic-version`**，
    而本项目只讲 OpenAI 兼容协议（`Authorization: Bearer`）。
    遇到只支持 Anthropic 协议的站点（如某些中转的 Claude 模型），
    `base_url` 换成它的 OpenAI 兼容入口才行 —— 这两条路在这里不做适配，
    因为适配它们等于在 llm.py 里养两套协议，收益不抵复杂度。
    """
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
    }


# ==============================================================================
# HTTP（标准库，无第三方依赖）
# ==============================================================================

def _http_post_json(url, headers, payload, timeout):
    """
    返回 `(解析后的响应 dict, 原始响应体文本)`。

    为什么要连原始文本一起返回：留档（llm_log）要存"模型到底回了什么"，
    只有解析后的 `choices[0].message.content` 会丢掉 `usage`、reasoning 等
    排查时最需要的东西。多返回一个字符串，比让调用方再发一次请求便宜得多。
    """
    # 建连前先看一眼：已经取消的请求不该再白发一次（非流式这条路同样要能停 ——
    # stream_storyboard 的**重试轮**走的正是它，不加这一手会出现
    # "首轮停掉了、重试轮又发了一次完整请求"）。
    check_cancelled()

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    token = _CANCEL_TOKEN.get()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # 与 _http_post_json_stream 同理：resp.read() 会一直阻塞到整个响应体读完，
            # 深度思考几分钟时这一段同样是"停不掉"的黑洞。登记后 cancel() 一 close，
            # read 立刻抛 OSError。
            if token is not None:
                try:
                    token.watch(resp)
                except Exception:                           # noqa: BLE001
                    token = None
            try:
                body = resp.read().decode("utf-8", "replace")
            except Exception:                               # noqa: BLE001
                # 先分清"用户取消"与"真断流"，两者的处理完全相反。
                #
                # ⚠️ 这里**不能只 catch OSError**：取消时我们 close() 响应，
                #    而读线程可能正停在 http.client 内部的 `fp.peek()` 上 ——
                #    close() 把 fp 置成 None，抛出来的是
                #    `AttributeError: 'NoneType' object has no attribute 'peek'`。
                #    与 _http_post_json_stream 里踩到的是同一个坑。
                check_cancelled()
                raise
            finally:
                if token is not None:
                    try:
                        token.unwatch(resp)
                    except Exception:                       # noqa: BLE001
                        pass
    except urllib.error.HTTPError as e:
        # 错误体里通常有厂商的中文说明，截一段出来 —— 比只报状态码有用得多
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        hint = {
            400: "常见三种：model 名不对 / max_tokens 超了模型上限 / "
                 "开了 json_mode 但 prompt 里没有 json 字样"
                 "（DeepSeek 会报 `Prompt must contain the word 'json'`）",
            401: "密钥不对或没带上",
            403: "密钥没有该模型的权限；若错误体里是 Cloudflare 的 `error code: 1010`，"
                 "那是它挡了非浏览器请求 —— 在 llm.local.json 里补 \"headers\""
                 "（User-Agent / Referer / Origin）",
            404: "base_url 写错了（要带版本路径，如 .../v1）",
            429: "触发限流或余额不足，稍后重试",
        }.get(e.code, "")
        raise LLMError(
            f"LLM 接口返回 HTTP {e.code}" + (f"（{hint}）" if hint else "") +
            f"\n{url}\n{detail}"
        )
    except urllib.error.URLError as e:
        raise LLMError(f"连不上 LLM 接口：{e.reason}\n{url}")
    except TimeoutError:
        raise LLMError(f"LLM 接口超时（>{timeout}s）\n{url}")

    try:
        return json.loads(body), body
    except json.JSONDecodeError:
        raise LLMError(f"接口返回的不是 JSON（前 500 字符）：\n{body[:500]}")


def chat(messages, cfg, json_mode=False, purpose="chat", seen=None):
    """
    调一次 chat/completions，返回模型的文本回复。

    每次调用都会**留档**到 output/_llm/calls.jsonl（含完整 prompt 与回复原文）；
    开了 MSB_LLM_REPLAY=1 时，命中同一份留档就直接回放、不发请求。

    Args:
        purpose: 留档里的用途标签（chat / storyboard / scene）。
            **刻意放在最底层**：三条业务路径都从这里发请求，写在这一处
            就不可能漏记；反过来若写在三条上层路径里，将来加第四条必然漏。

    ⚠️ json_mode 默认 **False**，且**这一层不读配置**，只认传进来的布尔值。
    "要不要强制 JSON"是**业务策略**，由上层（generate_storyboard /
    stream_storyboard / regenerate_scene）用 effective_json_mode() 定好再传下来。
    底层 HTTP 包装函数不该根据环境里的配置偷偷改 payload —— 那会让一个普通的
    chat() 调用突然带上 `response_format`，而 DeepSeek 之类厂商要求
    **prompt 里必须出现 json 字样**，于是报一个和调用方毫无关系的 400。
    """
    if not cfg.get("api_key"):
        raise LLMError(
            "没有 API 密钥。三种给法，任选其一：\n"
            "  1) 命令行:  --api-key sk-xxx\n"
            "  2) 环境变量: set MSB_API_KEY=sk-xxx   （或各厂商自己的 *_API_KEY）\n"
            f"  3) 配置文件: {config_path()}  内容 {{\"api_key\":\"sk-xxx\","
            f"\"provider\":\"deepseek\",\"model\":\"deepseek-chat\"}}"
        )

    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg.get("temperature", 0.2),
    }
    if cfg.get("max_tokens"):
        payload["max_tokens"] = cfg["max_tokens"]
    if json_mode:
        # 让模型直接吐 JSON。多数 OpenAI 兼容厂商都支持；不支持的话接口会报 400
        # （所以可以在 llm.local.json 里用 "json_mode": false 关掉），
        # 关掉后仍靠 prompt 约束 + parse_json_object() 兜底。
        #
        # ⚠️ 有的厂商（DeepSeek）还额外要求 **prompt 里必须出现 "json" 字样**，
        # 否则直接 400 `Prompt must contain the word 'json'`。我们的分镜 prompt
        # 天然包含它，所以正常路径没问题；但给一个普通问答打开这个开关就会炸 ——
        # 这也是为什么"要不要开"必须由上层业务决定，而不是底层自动继承配置。
        payload["response_format"] = {"type": "json_object"}

    # ★ 服务商私有参数（如 DeepSeek 的 {"thinking": {"type":"disabled"}}）原样并进去。
    #    ⚠️ 必须在 `cache_key()` **之前**：留档指纹要含它。否则"改了思考开关"
    #       会命中旧回复（回放模式下尤其误导：配置变了，拿到的还是老结果）。
    _merge_extra_body(payload, cfg)

    key = llm_log.cache_key(payload)

    # 回放：命中历史留档就不发请求。purpose 一并参与匹配，免得把"重画一个分镜"
    # 的回复喂给"生成整份分镜"那条路（两者的 prompt 完全不同，指纹本来就不同，
    # 多一层 purpose 只是让意图更明确）。
    if replay_enabled():
        hit = llm_log.lookup(key, purpose=purpose)
        if hit:
            print(f"      [REPLAY] 命中留档，跳过真实调用"
                  f"（{hit.get('ts')}，{hit['seconds']}s）", flush=True)
            llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                           key=key, ok=True, seconds=0.0, reply=hit["reply"],
                           usage=hit.get("usage"), messages=messages,
                           json_mode=json_mode, replayed=True)
            return hit["reply"]

    headers = {**(cfg.get("headers") or {}), **_auth_headers(cfg)}
    t0 = time.time()
    try:
        resp, _raw = _http_post_json(endpoint(cfg["base_url"]), headers, payload,
                                     cfg.get("timeout", 180))
    except LLMCancelled:
        # 非流式这条路也要留档：它是 stream_storyboard 的**重试轮**走的路径，
        # 用户点停止时很可能正卡在这一轮。不记的话留档里会出现
        # "上一轮 ok=true、然后什么都没有"的空白，看不出是用户停了还是进程崩了。
        llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                       key=key, ok=False, seconds=time.time() - t0,
                       error="用户已停止生成（cancelled）",
                       messages=messages, json_mode=json_mode)
        raise
    except LLMError as e:
        # 失败也要留档：调 prompt 时"这一版把模型逼到 400 了"同样是关键信息
        llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                       key=key, ok=False, seconds=time.time() - t0, error=str(e),
                       messages=messages, json_mode=json_mode)
        raise

    try:
        choice = resp["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"接口返回结构不认识：\n{json.dumps(resp, ensure_ascii=False)[:600]}")
    # `finish_reason` 是判断"回复是不是被 max_tokens 砍断"的**唯一权威信号**。
    # 抓它但不用它 = 白抓：截断会伪装成"JSON 解析失败"或"返回空内容"，
    # 排查方向直接被带到语法错误上去（HANDOFF §8.11 就踩过这个坑）。
    fr = str(choice.get("finish_reason") or "")
    if seen is not None:
        seen["finish_reason"] = fr
        seen["usage"] = resp.get("usage")
    if fr == "length":
        # 必须喊出来：截断会把错因伪装成"JSON 解析失败"/"返回空内容"，
        # 排查方向直接被带到语法错误上去（HANDOFF §8.11 的原始踩坑）。流式那条路同理。
        print("      [WARN] 回复被 max_tokens 截断（finish_reason=length）——"
              " 模型的思考先吃掉了大部分 token 预算，正文是残的", flush=True)
    if isinstance(content, list):
        # 少数厂商把 content 返回成分段数组
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not content or not str(content).strip():
        raise LLMError(
            "模型返回了空内容"
            + ("（**被 max_tokens 截断**：思考先吃光了 token 预算，正文一个字都没写。"
               "调大 max_tokens，或换思考更短的模型）" if fr == "length" else
               "（可能是 max_tokens 太小，或被内容策略拦了）")
        )

    text = str(content)
    llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                   key=key, ok=True, seconds=time.time() - t0, reply=text,
                   usage=resp.get("usage"), messages=messages, json_mode=json_mode,
                   finish_reason=fr, max_tokens=cfg.get("max_tokens") or 0)
    return text


# ==============================================================================
# 会话标题：一次**独立的极短调用**
# ==============================================================================
#
# 为什么不复用分镜 JSON 里那个 `title`（它也是模型起的、而且就在同一份输出里）：
#   · **时机**：它在 JSON 的第 4 个键上，要等整份分镜生成完（十几秒到一分钟）才拿得到；
#     这里是**与主生成并行**发的一条独立小请求，1~2 秒就回 —— 侧栏几乎立刻有了名字。
#   · **用途**：那个 title 是**视频标题**（要能说清这道題讲了什么，长一点没关系）；
#     会话标题是侧栏里的一个**标签**，必须短。共用一个必然有一边难看。
#
# ⚠️ 刻意**不带**主 system prompt（那本手册约 18k token）：为起个名字付 18k 的输入
#    代价和几秒延迟不划算，也会把它挤进留档、污染"提示词体积画像"。
#
# ⚠️ 调用方必须允许它失败：标题是锦上添花，失败就退回"用户输入前 N 字"，
#    绝不能因为它把整轮对话拖垮。所以这里不重试。

_TITLE_SYSTEM = (
    "你只做一件事：给用户的问题起一个**会话标题**。\n"
    "要求：\n"
    "  · 中文，8~12 字，像侧边栏里的一个标签 —— 一眼能认出这是哪一道题、哪一个话题；\n"
    "  · 只输出标题本身：**不要引号、不要句号、不要解释、不要 markdown、不要换行**；\n"
    "  · 抓**主题**，不要复述整道题（不要塞进冗长的表达式）；\n"
    "  · 数学题就写「题型 + 对象」，例如：导数求单调区间 / 勾股定理的面积证法。"
)

_TITLE_MAX_CHARS = 14


def suggest_thread_title(user_text, cfg):
    """
    给这一轮的用户问题起一个会话标题。

    Returns:
        str: 清洗好的标题（可能为空串，调用方要自己兜底）

    Raises:
        LLMError: 配置有误 / 接口报错 / 空内容。**调用方一律静默降级**，不要重试。
    """
    text = (user_text or "").strip()
    if not text:
        raise LLMError("用户消息是空的，起不出标题")

    reply = chat(
        [{"role": "system", "content": _TITLE_SYSTEM},
         # 截断：用户可能整段粘贴题目源码，起标题看前面这些就够了
         {"role": "user", "content": text[:500]}],
        cfg,
        purpose="title",
    )
    return _clean_title(reply)


def _clean_title(reply):
    """
    把模型回的话修成能直接当标题用的字符串。

    逐条**修**而不是整段拒收：模型偶尔加引号、先给标题再补一句解释，
    这些都不是错误，拒掉一次就白花一次调用。
    """
    t = str(reply or "").strip()
    t = t.strip("\"'「」『』《》*#` ").strip()
    lines = t.splitlines()
    t = lines[0].strip() if lines else ""       # 只取第一行
    t = re.sub(r"^(标题|会话标题|主题)\s*[:：]\s*", "", t)
    # 侧栏是单行 truncate。换行/多余空白在这里清掉，比交给 CSS 挤掉更可控
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > _TITLE_MAX_CHARS:
        t = t[:_TITLE_MAX_CHARS].rstrip("，,。.：:、；; ")
    return t


# ==============================================================================
# 流式（SSE）：给「秒级见字」用
# ==============================================================================
#
# 为什么值得单独做一条路：一次性调用要等模型把整个 JSON 写完（十几秒）才有内容可显示，
# 而 prompt 已经把 brief 放在第一个键 —— 于是"边收边把 brief 的字符吐出去"就能让
# 用户在 1~2 秒内看到回答，不用付"把一次 LLM 调用拆成两次"的代价。
#
# 这里只解析 OpenAI 兼容的 SSE 骨架（`data: {...}` 行 + `[DONE]` 收尾），
# 各厂商的差异都在这个骨架之内，所以仍然不需要 SDK。

# 模型把"思考/推理"放在哪个字段里，各家不一样，实测见过的都列上：
#   reasoning        —— 本项目中转站的 deepseek-v4.1-flash（思考走独立字段）
#   reasoning_content—— 官方 DeepSeek 推理模型的习惯写法
#   thinking         —— 少数网关这么叫
# ⚠️ 有的模型思考是**明文混在 content 里**的（见 parse_json_object 砍 `final answer:`），
# 那种这里管不着 —— 它压根不是独立字段。
_REASONING_KEYS = ("reasoning", "reasoning_content", "thinking")


def _iter_sse_stream(resp, seen=None):
    """
    从 OpenAI 兼容的流式响应体里逐块取出增量，**思考与正文分开**：

        yield ("reasoning", 文本片段)   ← 模型的思考过程
        yield ("content",   文本片段)   ← 真正要显示的正文

    为什么要分流而不是只 yield content：开了深度思考的模型，思考期间正文一个字符
    都不出（实测中转站 49s 后才轮到正文）。这 49 秒里唯一在动的东西就是 reasoning，
    把它丢掉的代价就是用户干等 —— 前端拿不到任何进度信号。见 HANDOFF §8.11。

    `seen`（可选 dict）会被就地填上：
        usage          末包里的 usage（DeepSeek 不带 stream_options 也会给）
        finish_reason  结束原因
        reasoning_chars / content_chars
                       两路各自收到多少**字符**。流被上游掐断时，这是唯一能回答
                       "它到底走到哪一步了"的数字（HANDOFF §8.35）。

    ⚠️ 实测（2026-09-12，DeepSeek）：撞到 `[DONE]` 之后**仍可能再来带 usage 的收尾包**，
    所以不能一见 [DONE] 就收工，必须把流读完，否则会莫名丢掉 token 统计。
    """
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if chunk == "[DONE]":
            continue
        try:
            obj = json.loads(chunk)
        except ValueError:
            continue
        if seen is not None and obj.get("usage"):
            seen["usage"] = obj["usage"]
        choices = obj.get("choices") or []
        if not choices:
            continue
        if seen is not None and choices[0].get("finish_reason"):
            seen["finish_reason"] = choices[0]["finish_reason"]
        delta = choices[0].get("delta") or choices[0].get("message") or {}
        for key in _REASONING_KEYS:
            v = delta.get(key)
            if v:
                if seen is not None:
                    seen["reasoning_chars"] = seen.get("reasoning_chars", 0) + len(str(v))
                yield "reasoning", str(v)
        piece = delta.get("content")
        if isinstance(piece, list):          # 少数厂商把 content 分片返回
            piece = "".join(p.get("text", "") for p in piece if isinstance(p, dict))
        if piece:
            if seen is not None:
                seen["content_chars"] = seen.get("content_chars", 0) + len(str(piece))
            yield "content", str(piece)


def _iter_sse_content(resp, seen=None):
    """只要正文增量的老接口（保留给不需要思考流的调用方）。"""
    for kind, piece in _iter_sse_stream(resp, seen):
        if kind == "content":
            yield piece


def _iter_replay_text(text, chunk=64):
    """
    把留档的回复**切成小块**吐出去，模拟流式的观感。

    为什么不直接 yield 整段：上层（stream_storyboard）用 BriefTap 从增量里抠 brief，
    一次性喂进去也能跑；但切成小块更接近真实流式的行为，调 UI 时才不会"回放看着是好的、
    真调用却露出括号边界问题"。
    """
    for i in range(0, len(text), chunk):
        yield text[i:i + chunk]


def _stream_break_message(err, timeout, seen):
    """
    流式读取中断（读超时 / 连接被切断）时的报错文案。

    为什么值得单独写一段（HANDOFF §8.35）：这条失败的原形是
    `TimeoutError: The read operation timed out` —— 它既不是 `LLMError`、也不会进留档，
    于是一路冒到 `/api/chat` 的兜底分支变成一句「服务端异常」：**一次挂了五分钟的调用
    什么都没留下**。而它和"输出被截断"其实是同一件事的两种收尾方式，
    只有把"收到多少思考 / 多少正文"摆出来才分得清。
    """
    r_chars = int((seen or {}).get("reasoning_chars") or 0)
    c_chars = int((seen or {}).get("content_chars") or 0)
    lines = [
        f"流式读取中断：{type(err).__name__}: {err}",
        f"  {int(timeout)} 秒没收到任何新数据（配置里的 timeout），"
        f"这一条流上共收到：思考 {r_chars} 字 / 正文 {c_chars} 字。",
    ]
    if r_chars and not c_chars:
        lines += [
            "  思考吐了一大堆、正文一个字都没有 —— 这不是我们的流式坏了，"
            "而是**思考把 token 预算烧穿**之后上游没有按约定收尾"
            "（正常命中上限应当是 finish_reason=length + [DONE]，见 HANDOFF §8.11 / §8.30），"
            "于是连接挂着不关，我们只能等到读超时。",
            "  对策：把该模型档案的 max_tokens 调大只能买回「更晚才失败」，"
            "根治得靠收紧思考（prompt 里已加这条硬要求）。",
        ]
    else:
        lines.append("  常见原因：中转站/网关断掉了这条连接（上游 5xx、被限流、NAT 超时），"
                     "或本机网络抖动 —— 直接重试即可。")
    lines.append("  ⚠️ 把 llm.local.json 里的 timeout 调大不会让它继续吐字，"
                 "只会让你等更久才看到失败。")
    return "\n".join(lines)


def _http_post_json_stream(url, headers, payload, timeout, seen=None):
    """
    流式 POST，逐块 yield `(kind, text)` —— kind 是 "reasoning" 或 "content"。
    错误处理与 _http_post_json 保持一致。

    `seen` 会被透传给 _iter_sse_stream，用来把 usage / finish_reason 带出来
    （流式没有"整个响应体"可回看，只能边读边记）。
    """
    # 建连前先看一眼：已经取消的请求不该再白发一次网络请求（也省一条计费可能）
    check_cancelled()

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Accept", "text/event-stream")
    # 调用方没给 seen 也要自建一个：断流时的报错文案靠它说清"走到哪一步了"，
    # 而"没传 seen"和"这次没收到东西"在报错里必须分得开（HANDOFF §8.35）。
    if seen is None:
        seen = {}
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        hint = {
            400: "多半是 model 名不对，或该厂商不支持流式（可关掉流式再试）",
            401: "密钥不对或没带上",
            403: "密钥没有该模型的权限",
            404: "base_url 写错了（要带版本路径，如 .../v1）",
            429: "触发限流或余额不足，稍后重试",
        }.get(e.code, "")
        raise LLMError(
            f"LLM 接口返回 HTTP {e.code}" + (f"（{hint}）" if hint else "") +
            f"\n{url}\n{detail}"
        )
    except urllib.error.URLError as e:
        raise LLMError(f"连不上 LLM 接口：{e.reason}\n{url}")
    except TimeoutError:
        raise LLMError(f"LLM 接口超时（>{timeout}s）\n{url}")
    # ★ 把响应对象交给取消令牌登记。
    #
    # 为什么必须这样：下面 `for raw in resp:` 是**阻塞**读 —— 没有这一手的话，
    # 点"停止"之后线程会一直卡在 readline 上，等到上游把这一轮吐完（或读超时）
    # 才醒来：模型照跑、token 照烧，用户以为停了其实没停。
    # 登记之后 cancel() 会 close() 这个响应，read 立刻抛 OSError → 循环退出。
    token = _CANCEL_TOKEN.get()
    if token is not None:
        try:
            token.watch(resp)
        except Exception:                                   # noqa: BLE001
            token = None            # 令牌不认这个契约也不该把正常调用弄挂
    with resp:
        try:
            # 循环里再兜一道：close() 与 read 之间有竞态（可能刚好读完一块），
            # 每轮检查一次能保证"停止"最多延迟一个增量生效。
            for kind, piece in _iter_sse_stream(resp, seen):
                check_cancelled()
                yield kind, piece
        except LLMCancelled:
            raise
        except Exception as e:                              # noqa: BLE001
            # ⚠️ 这里**不能只 catch (OSError, HTTPException)** —— 实测踩到了：
            #
            #   取消时我们 close() 这个响应，而读线程正阻塞在 `resp.fp.peek()` 上。
            #   close() 把 `resp.fp` 置成 None，读线程随即抛的是
            #       AttributeError: 'NoneType' object has no attribute 'peek'
            #   **不是 OSError**。只 catch OSError 的话它接不住，会一路冒到
            #   pipeline 的兜底分支变成「服务端异常：AttributeError」——
            #   既没留档，也没被识别成取消，"停止"等于还是失效（第一次改就栽在这）。
            #
            # 所以顺序是：**先问令牌"是不是被取消了"**，再决定怎么包装。
            # `check_cancelled()` 在已取消时抛 LLMCancelled（穿到 pipeline，不重试、不报错）。
            check_cancelled()
            # 没被取消 → 才按"真故障"处理。
            if isinstance(e, (OSError, http.client.HTTPException, AttributeError)):
                # ⚠️ 读到一半断流 / 读超时**必须在这里就地包成 LLMError**。
                # 不包的话它以原形冒上去：上层（chat_stream / stream_storyboard / 路由）
                # 全都是 `except LLMError`，一个都接不住，最后落在 pipeline 的兜底分支里
                # 变成「服务端异常：TimeoutError」——不留档、没有收到多少思考，
                # 也分不清它和"被截断"的区别（HANDOFF §8.35）。
                #
                # AttributeError 一起收进来：上面那个竞态的**非取消**版本
                # （上游刚好在别的路径上关掉了 fp）症状与断流完全一致，
                # 报成"连接中断"比报成"NoneType has no attribute peek"有用得多。
                raise LLMError(_stream_break_message(e, timeout, seen)) from e
            # 其它异常是**真 bug**，原样抛出去 —— 包装成"断流"会把方向带偏。
            raise
        finally:
            # 无论怎么退出（正常读完 / 断流 / 取消）都要注销，
            # 否则令牌会一直攥着一个已关闭的响应对象（长会话里越攒越多）。
            if token is not None:
                try:
                    token.unwatch(resp)
                except Exception:                           # noqa: BLE001
                    pass


def chat_stream(messages, cfg, json_mode=False, purpose="chat", on_reasoning=None,
                seen=None):
    """
    流式调一次 chat/completions，逐个 yield **正文**文本增量。json_mode 语义同 chat()。

    Args:
        on_reasoning: callable(str)。开了深度思考的模型会先把思考过程流出来，
            这里逐个片段回调；**不传就丢弃**（默认行为，见下面的"为什么默认不要"）。
            它和 on_delta 一样在调用方线程里被同步调用。

    为什么"思考流"要显式开：**推理内容可能包含模型对用户的判断、
    甚至是被要求隐藏的中间结论**，直接推到终端用户屏幕上是有风险的。
    所以底层只是**把它交出去**，要不要给用户看由业务层决定（`MSB_LLM_SHOW_THINKING`）。

    留档/回放语义与 chat() 完全一致：**整段回复原文**在流结束后记一条；
    回放时把留档的原文切块吐出来（观感与真流式一致）。
    """
    if not cfg.get("api_key"):
        raise LLMError(
            "没有 API 密钥。三种给法，任选其一：\n"
            "  1) 命令行:  --api-key sk-xxx\n"
            "  2) 环境变量: set MSB_API_KEY=sk-xxx   （或各厂商自己的 *_API_KEY）\n"
            f"  3) 配置文件: {config_path()}"
        )
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg.get("temperature", 0.2),
        "stream": True,
    }
    if cfg.get("max_tokens"):
        payload["max_tokens"] = cfg["max_tokens"]
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    # 服务商私有参数（同 chat()：必须在 cache_key 之前，指纹要含它）
    _merge_extra_body(payload, cfg)

    key = llm_log.cache_key(payload)

    if replay_enabled():
        hit = llm_log.lookup(key, purpose=purpose)
        if hit:
            print(f"      [REPLAY] 命中留档，跳过真实流式调用"
                  f"（{hit.get('ts')}，{hit['seconds']}s）", flush=True)
            llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                           key=key, ok=True, seconds=0.0, reply=hit["reply"],
                           usage=hit.get("usage"), messages=messages,
                           json_mode=json_mode, streamed=True, replayed=True)
            yield from _iter_replay_text(hit["reply"])
            return

    headers = {**(cfg.get("headers") or {}), **_auth_headers(cfg)}
    t0 = time.time()
    # 调用方可以传一个 dict 进来收 `finish_reason` / `usage`（同 chat() 的 seen）。
    # 缺省自建一个：老调用方一个字都不用改。
    if seen is None:
        seen = {}
    pieces = []
    try:
        for kind, piece in _http_post_json_stream(endpoint(cfg["base_url"]), headers,
                                                  payload, cfg.get("timeout", 180),
                                                  seen=seen):
            if kind == "reasoning":
                if on_reasoning:
                    on_reasoning(piece)
                continue
            pieces.append(piece)
            yield piece
    except LLMCancelled:
        # 用户停止：同样留一条档（ok=False），把已经收到的部分存下来。
        # 为什么值得记：排查"这一轮花了多少钱/思考到哪一步"时，
        # 被用户停掉的调用和"真断流"必须能区分开 —— 否则留档里只有一堆 ok=False，
        # 看不出哪些是网络问题、哪些是有人按了按钮。
        llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                       key=key, ok=False, seconds=time.time() - t0,
                       error="用户已停止生成（cancelled）",
                       reply="".join(pieces), messages=messages, json_mode=json_mode,
                       streamed=True, finish_reason=str(seen.get("finish_reason") or ""),
                       max_tokens=cfg.get("max_tokens") or 0)
        raise
    except LLMError as e:
        # 断流时把**已经收到的正文**一起落盘：否则"它到底写到哪一步了"只能靠猜，
        # 而半份 JSON 恰恰是判断"是语法写错还是被掐断"的关键（HANDOFF §8.35）。
        # 不会污染回放：lookup() / last_reply() 都只看 ok=True 的记录。
        llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                       key=key, ok=False, seconds=time.time() - t0, error=str(e),
                       reply="".join(pieces), messages=messages, json_mode=json_mode,
                       streamed=True, finish_reason=str(seen.get("finish_reason") or ""),
                       max_tokens=cfg.get("max_tokens") or 0)
        raise

    fr = str(seen.get("finish_reason") or "")
    if fr == "length":
        # 截断是**必须喊出来**的：否则它会伪装成"JSON 解析失败"，
        # 把排查方向从"预算不够"带偏到"语法写错"上（HANDOFF §8.11 的原始踩坑）。
        print(f"      [WARN] 回复被 max_tokens 截断（finish_reason=length）——"
              f" 模型的思考先吃掉了大部分 token 预算，正文是残的", flush=True)
    llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                   key=key, ok=True, seconds=time.time() - t0, reply="".join(pieces),
                   usage=seen.get("usage"), messages=messages, json_mode=json_mode,
                   streamed=True, finish_reason=fr,
                   max_tokens=cfg.get("max_tokens") or 0)


class BriefTap:
    """
    从**流式到达的 JSON 文本**里增量抠出 `brief` 的值，边到边吐字。

    为什么不用"等 JSON 收完再解析"：那样等于放弃了流式，用户还是要等十几秒。
    这里只做一件事：在文本里定位 `"brief"` `:` `"` 之后，把字符逐个解码吐出去，
    直到遇到未转义的收尾引号 —— 不试图解析整个 JSON（那是收完以后的事）。

    转义按 JSON 规则处理（\\n \\t \\" \\\\ \\uXXXX），因为 brief 里出现英文双引号
    或换行都很正常，直接把原始字节吐出去会把 `\\n` 显示成两个字符。
    """

    _ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
                '"': '"', "\\": "\\", "/": "/"}

    _KEY = '"brief"'

    def __init__(self):
        self._buf = ""
        self._state = "seek_key"     # seek_key → seek_colon → seek_quote → in_string → done
        self._esc = ""               # "" 无转义 / "\\" 收到反斜杠 / "u" 正在收 \uXXXX
        self._u = ""
        self.finished = False

    def feed(self, piece):
        """喂入一段新到的文本，返回**本次新增**的可显示文字（可能为空串）。"""
        if self.finished or not piece:
            return ""
        buf = self._buf + piece
        out = []
        i = 0
        while i < len(buf):
            ch = buf[i]
            if self._state == "seek_key":
                if ch == '"':
                    # 关键：key 本身可能被切在两次 feed 之间（"bri|ef"），
                    # 字符不够就先不动，等下一块 —— 否则会把开头的引号吃掉、再也匹配不上。
                    if len(buf) - i < len(self._KEY):
                        break
                    if buf.startswith(self._KEY, i):
                        self._state = "seek_colon"
                        i += len(self._KEY)
                        continue
                i += 1
                continue
            if self._state == "seek_colon":
                if ch == ":":
                    self._state = "seek_quote"
                elif ch not in " \t\r\n":
                    self._state = "seek_key"       # 前缀恰好命中，但不是这个 key
                i += 1
                continue
            if self._state == "seek_quote":
                if ch == '"':
                    self._state = "in_string"
                elif ch not in " \t\r\n":
                    self._state = "seek_key"       # brief 不是字符串，放弃
                i += 1
                continue
            # ---- in_string：逐字符解码 ----
            if self._esc == "u":
                self._u += ch
                if len(self._u) == 4:
                    try:
                        out.append(chr(int(self._u, 16)))
                    except ValueError:
                        pass
                    self._u = ""
                    self._esc = ""
                i += 1
                continue
            if self._esc:
                if ch == "u":
                    self._esc = "u"
                    self._u = ""
                else:
                    out.append(self._ESCAPES.get(ch, ch))
                    self._esc = ""
                i += 1
                continue
            if ch == "\\":
                self._esc = "\\"
                i += 1
                continue
            if ch == '"':
                self.finished = True
                self._state = "done"
                i += 1
                break
            out.append(ch)
            i += 1
        # 只保留还没消费的尾部（可能是不完整的 key / 转义序列），避免 buf 无限增长
        self._buf = buf[i:]
        return "".join(out)


# ==============================================================================
# 解析：从模型回复里抠出那个 JSON 对象
# ==============================================================================

# JS 风格的"简写键"修复：`{"center"}` / `{"center", "shift":[0,1]}` 这类写法。
# 键名都来自我们自己的 spec（见 _SHORTHAND_KEYS），所以"某个裸字符串后面紧跟
# `}` 或 `,`"这种模式**只会**出现在简写键上，不会误伤普通的字符串数组
# （如 `"target": ["a", "b"]` —— 它后面跟的是 `,` 且**不在**这个白名单里）。
_SHORTHAND_KEYS = ("center",)
_RE_SHORTHAND = re.compile(
    r'"(' + "|".join(_SHORTHAND_KEYS) + r')"\s*(?=[,}])')


def _fix_shorthand_keys(s):
    """
    把 `{"center"}` 补成 `{"center": true}`。

    为什么要救：布局键里只有 `center` 是"布尔简写"形态，模型的直觉是
    `{"center"}`（JS 里合法，JSON 里非法），实测确实这么输出了
    （2026-09-12，DeepSeek，见留档）。这不是模型犯傻——是我们给的规格里
    只有键名列表、没给带值的示例，属于我们自己的锅。
    规格已经补了示例，这里再加一道网：**留档的价值就在这里，先能看见，才谈得上修**。
    """
    return _RE_SHORTHAND.sub(r'"\1": true', s)


def parse_json_object(text):
    """
    容错解析。模型常见的"手滑"都要能救回来：
      1. 用 ```json 围栏包起来
      2. 前面写一句"好的，这是分镜："后面才给 JSON
      3. 前后有解释文字
      4. `{"center"}` 这种 JS 风格的简写键（少个 `: true`）
    """
    s = str(text).strip()

    # 0) 砍掉推理轨迹。有些模型（deepseek/gpt 系的思考版）上面挂着类似
    #    `Thinking Process: ... answer ... final answer:` 的思考文本，
    #    它是**明文**混在 content 里的，不是独立字段 —— 不砍掉的话，
    #    下面"扫描第一个完整 {...}"有可能扫到思考里举的例子，静默解析出错的 JSON。
    #    只在它后面还有内容时才砍（避免把正文里恰好出现的同一个词误伤）。
    low = s.lower()
    for marker in ("final answer:", "最终答案：", "最终答案:"):
        pos = low.rfind(marker)
        if pos > 0 and s[pos + len(marker):].strip():
            s = s[pos + len(marker):].strip()

    # 1) 去 Markdown 围栏
    m = re.search(r"```(?:json|JSON)?\s*(.+?)```", s, re.S)
    if m:
        s = m.group(1).strip()

    # 1.5) 修 JS 风格的简写键（`{"center"}` → `{"center": true}`）
    s = _fix_shorthand_keys(s)

    # 2) 直接解析
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 3) 退化成"扫第一个完整的 {...}"：按括号配对找，且要考虑字符串内的括号
    start = s.find("{")
    if start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(_fix_shorthand_keys(s[start:i + 1]))
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break

    # 报错里带路径：只说"不是合法 JSON"，拿到的是一千多字的原文，根本不知道从哪看起。
    # 有路径 + 出错行，"去调 prompt"才有了方向。
    #
    # ⚠️ 但**行号只在"真的解析不了"时才可信**。两种情况会把人带偏，必须分开说：
    #   a) 少了闭合引号/括号 —— json.loads 会报在**它最先卡住的那行**，而那行的
    #      开头一半是好的，看起来完全正常，会让人以为是那行的语法错；
    #   b) 整段其实能解析、只是顶层不是对象（如合法 JSON 数组）—— 这种情况
    #      下面的 json.loads 不会抛异常，`where` 就是空的，不能硬塞个行号进去。
    where = ""
    try:
        parsed = json.loads(s)
        hint = ("\n（注意：这份内容是**合法 JSON**，只是顶层不是对象，而是 "
                f"{type(parsed).__name__}。要的是一整个 {{...}}。）")
    except json.JSONDecodeError as e:
        hint = ""
        lines = s.splitlines()
        bad = e.lineno - 1
        where = f"\n出错位置：第 {e.lineno} 行第 {e.colno} 列（{e.msg}）"
        if 0 <= bad < len(lines):
            where += f"\n  {lines[bad].strip()[:200]}"
        # 未闭合是最容易误判的一类，单独点破：位置几乎总在结尾附近，真正的
        # 原因是**内容被截断了**（max_tokens 用尽 / 开了思考把预算吃光）。
        # 判定"截断"的可靠信号：报错位置**落在整段文本的最末尾**。
        # 只按行号判断会误报 —— 真把括号写错也可能发生在前几行、恰好离末行很近
        # （实测过：4 行文本在第 3 行少个逗号，就被错认成截断）。
        offset = sum(len(x) + 1 for x in lines[:e.lineno - 1]) + (e.colno - 1)
        at_text_end = offset >= len(s.rstrip()) - 2
        if "Unterminated" in e.msg or at_text_end:
            hint = ("\n⚠️ 报错位置在文末附近、且是「未闭合」类错误时，八成不是语法写错，"
                    "而是**回复被截断了**（max_tokens 用尽；开了思考的模型会先吃掉大量 "
                    "token 再输出正文，8192 往往不够）。先确认 completion_tokens 是否"
                    "顶到了 max_tokens —— 完整原文和 usage 都在调用留档里："
                    "`python generate.py --show-last-reply` 或 output/_llm/calls.jsonl。")
    raise LLMError(
        f"模型没有返回合法的 JSON 对象。{where}{hint}\n"
        f"原文前 600 字符：\n{s[:600]}"
    )


# ==============================================================================
# Prompt
# ==============================================================================

# few-shot 示例。目的是让模型"照着格式抄"，而不是靠文字描述去猜 envelope 长什么样。
#
# ⚠️ 键的顺序是**故意**的：brief 必须第一个出现。
# 前端接 SSE 时按流式字符增量解析（api/ 里那层），brief 在最前面意味着
# 1~2 秒就有字可显示；把 title 放前面也一样不行 —— 它太短，解析不出"已经能显示"的信号。
#
# ⚠️ 为什么是**三个**示例而不是一个（2026-09-16）：
# 原先只有下面这一个，它演示的恰好是"tracker + 切线"这一条路，而 `describe()` 里
# 花了大量篇幅讲的 table / cell_box / move_cells / parallel / intent=none 一个都没示范。
# 模型对"只描述过、没看过"的东西只能靠直觉猜 —— 这正是 §8.10 那次 `{"center"}`
# 踩过的坑（规格里只给键名 → 模型按 JS 直觉写出非法 JSON）。
# 示例是最强的那份规格，覆盖不到的部分等于没写。
#
# ⚠️ 2026-09-17 补：示例 1 原来**一个字的说明都没有**（只有轴、曲线、点、切线、数值），
# 而它是最容易被照抄的那一个 —— 等于在示范"图 + 数字 = 讲完"。后来补了底部字幕和一次
# `replace` 逐句换字幕；**再后来整条示例被重做成 5 镜的完整讲解**
# （examples/fewshot_derivative.json）：几何直觉两镜（取点算比值 → 让 Δx 趋近 0）
# ＋ 用定义推导一镜（每步带旁注）＋ 常用公式表一镜。用户反馈的"解释性文字偏少"，
# 根子就在这：规范里写了、示例里没写，等于没写。
#
# ⚠️ 示例 1 从**文件**读，不再内联成字面量（2026-09-17）：它里面有大量 LaTeX 反斜杠
# （\frac、\overline、\Delta、\lim），而下面三个示例都是**普通**三引号字符串 ——
# 一旦内联，`\frac` 会被解释成 `\f`(换页) + "rac"、`\times` 会变成制表符，
# 改完肉眼看不出错、要渲染才发现。走 json.loads → json.dumps，转义由标准库负责，写不错；
# 代价是那个文件成了**唯一事实来源**（改示例不用回来同步两份 —— 历史漂移都是这么来的）。
# 下划线开头的键（_note / _structure）是写给作者看的，进 prompt 前一律剥掉。


def _strip_author_notes(obj):
    """递归剥掉下划线开头的键 —— 那些是作者注（_note/_structure），不是给模型看的。"""
    if isinstance(obj, dict):
        return {k: _strip_author_notes(v) for k, v in obj.items()
                if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip_author_notes(v) for v in obj]
    return obj


def _dump_example_json(obj, indent=0, width=120):
    """分镜 JSON 的**统一排版**：小的元素/动作对象压成一行，大的才逐键换行。

    ⚠️ 不用 `json.dumps(indent=2)`：它连 `[-1, 5, 1]` 这种短数组都拆成 5 行，
    一个 3×3 表格元素就能占 **32 行**（内容其实只是 9 个数 + 位置），
    5 镜示例从 300 行涨到 **1149 行 / 2.5 万字符**（实测）。
    两个后果都不是小事：
      · 费 token —— 而且 `_ECHO_LIMIT` 是**按字符数**截的，排太松会自己把回灌顶穿
        （卷积示例 24749 字符 > 20000，未压缩时**必被截断**；压完 16473 才安全）；
      · **模型是靠示例的结构去模仿的** —— `rows: [["1","2","3"], ...]` 一个元素一行，
        才看得出"这是什么形状"；拆成一列数字反而看不清。所以**排版本身就是规格**。

    这套排版用在**两个**地方，必须一致（否则"进 prompt 的格式"和"回灌的格式"两套）：
      · `_example_from_file` —— few-shot 示例进 system prompt；
      · `_scene_user_prompt` —— 多轮改一镜时回灌"这个分镜当前的样子"。
    """
    pad = " " * indent
    if not isinstance(obj, (dict, list)):
        return json.dumps(obj, ensure_ascii=False)
    inline = json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))
    if indent + len(inline) <= width:
        return inline
    # 每个子项先各自排好（值大的会自己多行），再**贪心地把短项接在同一行**。
    # ⚠️ 为什么不能"每个键一行"：那样比人写的还散 —— 手写的示例文件 299 行，
    # 同一份数据"每键一行"排出来是 529 行（进 prompt 反而变长了）。
    # 贪心接行的密度正好复现手写（299 → 299 / 213 → 219 行）。
    if isinstance(obj, dict):
        parts = [json.dumps(k, ensure_ascii=False) + ": " + _dump_example_json(v, indent + 2, width)
                 for k, v in obj.items()]
    else:
        parts = [_dump_example_json(v, indent + 2, width) for v in obj]
    head = " " * (indent + 2)
    rows, cur = [], None
    for text in parts:
        if cur is None:
            cur = head + text
        elif "\n" not in text and len(cur) + 2 + len(text) <= width:
            cur += ", " + text          # 这一项还放得下，就跟在同一行后面
        else:
            rows.append(cur)
            cur = head + text
    if cur is not None:
        rows.append(cur)
    body = ",\n".join(rows)
    if isinstance(obj, dict):
        return "{\n" + body + "\n" + pad + "}"
    return "[\n" + body + "\n" + pad + "]"


def _example_from_file(name):
    """把 examples/<name> 读成 few-shot 示例文本（剥作者注、重新序列化）。"""
    path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         os.pardir, "examples", name))
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        # 读不到就少一个示例，但**不能让整轮对话起不来** —— 其余三个示例还在。
        print(f"[warn] few-shot 示例读不到（{path}）：{exc}", file=sys.stderr)
        return "{}"
    return _dump_example_json(_strip_author_notes(data))


def save_example(path, data, width=120):
    """
    把示例写回文件 —— **用同一套排版**（`_dump_example_json`）。

    为什么要有这个入口：示例文件是**唯一事实来源**，作者/脚本改完必须写回去。
    而 `json.dumps(indent=2)` 会把文件写散（实测 1149 行 vs 481 行、24749 vs 16473 字符），
    于是同一个东西在磁盘上和在提示词里**两套排版** —— 时间一长必然互相漂移，
    而且散的那份还会顶穿 `_ECHO_LIMIT`。写文件走这里，两边就永远是同一种形状。
    """
    text = _dump_example_json(data, width=width) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text


_EXAMPLE = _example_from_file("fewshot_derivative.json")

# 示例 2：几何题。**故意全程不用 `axes`** —— 这是对"所有题都上坐标系"这个模板的直接
# 反例，而几何恰恰是模板化最容易被看出来的题型（一道证明题里冒出坐标系就会很突兀）。
# 2026-09-17：内容换成**赵爽弦图证明勾股定理**（`examples/fewshot_pythagoras.json`，走
# `_example_from_file` 读，理由同示例 1）。前身是"割补法求三角形面积"，被用户否掉 ——
# 「太简单了，本质只是套一次公式，撑不起一个示例」。现在这一条既有**图形拼接**
# （四个全等三角形旋转 + 平移到四条边上，斜边围成 c² 大正方形、中间空出 (b−a)²），
# 又有**真正的代数推导**（c² = 2ab + (b−a)² = a² + b²），还示范了"字幕跟着运动走"、
# "推导每步带旁注"、"中间量用完退场"这三条规矩。
_EXAMPLE_GEOMETRY = _example_from_file("fewshot_pythagoras.json")

# 示例 3：表格类（table + cell_box + move_cells）+ **跨分镜承接（carry）**。
# 三个作用合一：
#   1. `cell_box` 的正面示例：窗口位置和大小**全由格子算**（滑动用 move_cells），
#      而不是 `rect` 自己估坐标 —— 那条规矩光靠嘴说压不住，得有一个能照抄的正面例子；
#   2. 表格尺寸的正面示例：**只写 pad_x 收紧，不写 cell_w/cell_h**。用户 2026-09-17
#      反馈的「表格太大、单元格太大」就是模型写了 `cell_w: 3.6`（3 列 → 整表 10.8，
#      占画面 76%）造成的；
#   3. 第二镜**不重画网格**——只写 `{"id":"tb","carry":true}`（"连贯过程别重画"）。
# 2026-09-17：内容换成 **`examples/fewshot_conv.json`**（走 `_example_from_file` 读，
# 理由同示例 1）。前身是 2 镜的卷积小示例（窗口算式还写错了项：写成 `1+0+1+0`，
# 而窗口是 [[1,0],[0,1]]），中间还试过一版「二分查找」被用户否掉（太小太简单）。
_EXAMPLE_GRID = _example_from_file("fewshot_conv.json")

# 示例 4：纯概念问答。这是最容易被忽略的一种合法产物 —— 模型看到其余示例全是动画，
# 会倾向于"给什么题都硬造分镜"。这里把 intent=none 的形态整体摆出来。
#
# ⚠️ 2026-09-17 **换题**（用户）：原来示范的是"什么是直角三角形"，用户当场质疑
# **"难道直角三角形不能画出来给用户看吗"**。这一问正中要害 —— 拿一个**明明该画**的
# 图形题去示范"不出动画"，等于在教模型"这种也别画"，而且和《第一步》里
# "几何题只画图形本身"那条**直接对冲**。举例本身就是最强的示范，选错比不写还坏。
# 判据是**"这道题不画也能讲明白"**，不是"题短"、更不是"这个词听起来很基础"：
# 能画、且画出来更清楚的（几何、函数、数轴、网格…）一律 intent=propose。
# 换成的这题是**术语辨析** —— 解释"当且仅当"讲的是逻辑上的双向推出关系，
# 画任何图都只是装饰。规则 2 里的举例也一并改掉了（原来举的正是"直角三角形"，同源）。
_EXAMPLE_NONE = '''{
  "brief": "「当且仅当」是把两个方向的推出关系一起说了：P 当且仅当 Q，意思是「P 成立能推出 Q」而且「Q 成立能推出 P」—— 两个方向都成立，缺一不可。只成立一个方向时只能说「P 能推出 Q」，不能叫「当且仅当」。所以遇到它，就等于遇到一个双向的等价关系，正着推、反着推都成立。",
  "intent": "none",
  "outline": [],
  "title": "「当且仅当」是什么意思",
  "problem_type": "concept",
  "scenes": []
}'''


def build_system_prompt(extra_rules=""):
    """system prompt = 红线 + 设计规范 + 元素/动作规格（从 dsl.py 自动生成）+ 示例。"""
    parts = [
        "你是一名数学讲解视频的分镜师。你的任务是把用户给的数学题**讲明白**，"
        "并把它设计成一份**分镜 JSON**。",
        "",
        # ⚠️ 2026-09-17 加：此前开篇只写「唯一任务＝产出分镜 JSON」，等于把"把 JSON 填对"
        # 摆在了"让人看懂"前面。模型于是把力气全花在结构上：数是怎么来的、这一步凭什么成立，
        # 全留给观众自己猜 —— 公式往画面上一摆就算讲完。分镜是**讲法的载体**，不是任务本身，
        # 所以这条必须放在整本手册的最前面。
        "⚠️ **让人看懂、听懂，比分镜本身重要得多。** 设计分镜之前，先把这道题**怎么讲**"
        "想清楚：视频里出现的每一个数**是哪来的**（题目给的、还是上一步算出来的）、"
        "每一个公式**为什么这么写**（这一步在做什么、凭什么成立），都要在画面里交代清楚。"
        "**把公式往那儿一摆就完事，不叫讲解。** 分镜只负责把这套讲法安排成画面，"
        "它替代不了讲法。",
        "",
        # 为什么要点破引擎：模型对 Manim 有很强的先验（知道 always_redraw 每帧重建、
        # 知道公式要 LaTeX 编译），用上这份先验能明显改善「画面引擎」的取舍质量 ——
        # 而对能力的边界感，是"别设计渲不出来的东西"的前提。
        # 但**必须同时收紧红线 1**（见下面第一条）：点破引擎的直接副作用，就是模型
        # 开始拿 Manim 的类名当 kind 填（Axes / Brace / SurroundingRectangle…）。
        "这份 JSON 会被确定性地编译成 **Manim**（Python 数学动画库）代码，渲染成视频。"
        "你可以用对 Manim 的了解来判断什么画面好做、什么代价高，但请记住："
        "**你输出的不是 Manim 代码**，而是一套小写下划线的 DSL —— 两者的名字是"
        "**故意错开**的，别把 Manim 的类名搬过来。",
        "",
        # ⚠️ 这一节的基调是「成本不是你的约束，别为了快牺牲画面」（2026-09-16 改）。
        # 原来两句都是成本警告（动态元素贵、公式贵），模型读到会本能地少做动态、少写公式，
        # 而这两样恰恰是画面质量的主要来源。旧说法在**当时**也不算错，但现在已不成立：
        # LaTeX 走了批处理预热（tex_batch：24 个公式逐个编译 18.5s → 一次 0.9s，默认开启），
        # 静态公式与刻度数字都便宜了；而 DSL 里公式内容本就是固定字符串，
        # 「每帧在变」的数值由 number 元素承担，那条路有专门的廉价实现（建一次 + 只更新值）。
        # 结论：**把省时间的念头从模型脑子里拿掉，让它按"这道题该怎么讲"来设计画面。**
        # 2026-09-17：动态那条从「鼓励」收紧为「中性」（见下面那一条的说明）——
        # "成本不是约束"这个基调保留，但不再替模型判断"该不该动"。
        "几条帮你取舍的常识：",
        "- **渲染慢一点没关系，画面质量优先。** 不要为了省时间去砍动作、把该动的画成静止的，"
        "或把公式降级成大白话 —— 出片多花几十秒不是问题，画面对不上题才是问题。",
        # ⚠️ 2026-09-17 改：这一条原来叫「鼓励动态」，写的是"特点、不是缺点""就该让关键的量
        # 真的动起来"，还专门叮嘱"不必回避"。实测效果不好 —— 中性地描述能力即可，
        # 让模型按"这道题该怎么讲"自己判断，不要替它定一个"动比不动好"的偏好。
        "- **动态看需要**：含 `$tracker` 引用（或 `live: true`）的元素每帧都会重算，"
        "比静态元素慢。用不用**只看画面需不需要**：让变化真的看得见有帮助就用，"
        "静止画面能讲清楚就不必加 —— 动态既不是加分项，也不是要回避的坑。",
        "- **公式随便用**：`formula`、`text` 里的 `$...$` 混排、坐标轴刻度都已经做了批量"
        "预处理，不用替编译时间操心，也不要为了省事把公式写成普通文字。",
        # ⚠️ 2026-09-16 改：原文是"元素库里没有 3D、shader、交互、外部图片/视频，
        # 也没有镜头运镜。这类画面设计不出来" —— 把两层意思混成了一句。
        # 事实核对（对着装着的 manim 0.21 看的）：**Manim 本身 3D 一应俱全**
        # （ThreeDAxes、Surface、Sphere、Cube/Prism、Cone、Cylinder、Line3D/Arrow3D、
        # Torus、Polyhedron 系列，以及 ThreeDScene 的 set_camera_orientation /
        # move_camera / 环境自转），外部图片也有 ImageMobject。
        # 真正没有的是**我们这套引擎**：元素表里一个 3D kind 都没有，生成的场景类也是
        # 普通 `Scene`（renderer.HEADER），所以模型**输出不出来**。
        # 对模型的约束一个字没变，但表述必须说准：**"做不到"和"我们还没做"是两件事** ——
        # 混在一句里，读的人（包括以后的我们自己）会误判能力边界，甚至据此否掉本该能做的题。
        "- kind 只能从下面规格里挑。**三维已经支持**：`three_axes`（空间坐标系）+ "
        "`surface`（曲面 z=f(x,y)）/ `space_curve`（空间曲线）+ `rotate_camera`（转视角）。"
        "立体几何、空间曲面、二重积分这类内容就该真的把它立起来讲，比画三视图直观得多。"
        "但**别默认上 3D**：平面几何、函数图像、代数变形这些题目摆一个空间坐标系只会绕远路。",
        "- 还没有的能力：三维之外的相机运镜、交互、外部图片/视频。"
        "这类东西**不要**自己造 kind 去凑，那一定生成失败。",
        "",
        "## 输出格式（最重要）",
        "只输出一个 JSON 对象。不要 Markdown 围栏、不要任何解释文字、不要注释。",
        # 思考预算这条是给**开了深度思考的模型**写的：思考与正文共用同一个 max_tokens，
        # 实测有整轮卡死在思考里、正文一个字都没出来、最后连接被上游挂到读超时的
        # （HANDOFF §8.35）。示例里那句"想清楚就动笔"原本只在**截断重试**时才说，
        # 那时已经白烧一次调用和几分钟了 —— 必须提前到第一次调用就说。
        "⚠️ 思考与正文共用同一个 token 预算，它有硬上限。**想清楚画面引擎就直接动笔**："
        "不要在思考里逐帧推演、不要罗列备选方案、不要写完再自查一遍 —— "
        "思考把预算烧穿的代价是正文一个字都写不出来，这一轮等于白做。",
        "**键的顺序必须严格照下面的来，尤其 brief 必须是第一个键，不要调整**"
        "（前端靠这个顺序做到「1~2 秒就有字可显示」）：",
        "{",
        '  "brief": "先用 2~3 句话直接回答这道题（讲人话、不要空话套话、不要双引号、300 字以内；'
        '追问轮见下面规则 4：先说清这一轮改了什么，不要复述上一轮）",',
        '  "intent": "propose",',
        '  "outline": [ {"id": 1, "title": "分镜标题", "durationSec": 9, "summary": "这一镜讲什么"} ],',
        '  "title": "整个讲解的标题",',
        '  "problem_type": "题型标签（英文小写下划线），见下面「第一步」，要在里面先选一个",',
        '  "scenes": [ 分镜, ... ]',
        "}",
        "每个分镜 = {\"id\": 整数, \"duration\": 秒数, \"elements\": [...], \"timeline\": [...]}",
        "",
        # ⚠️ 2026-09-18 加（用户指出）：brief 里的数学原先是模型随手写的 —— 实测留档里是
        #   `f'(x)=3x²−3=3(x−1)(x+1)`：unicode 上标 ² ³、unicode 减号 −，**一个 $ 都没有**。
        #   前端拿着它只能当纯文本显示；靠"猜哪些是公式"去补救又必然误伤正文。
        #   所以契约要在**产出这一端**定死：数学一律是 LaTeX + `$` 标记，前端才认得出来。
        "## 数学写法（只针对 brief —— 用户直接看到的那段回答）",
        "1. **所有数学一律写 LaTeX，并用 `$...$` 包起来。**（`$x^2$`，不是 `x²`）",
        "   · 上标下标写 `^2` `_{1}` —— **不要用** unicode 上标（² ³ ⁿ ₁）；",
        "   · 减号写 `-` —— **不要用** unicode 减号（−）；乘除写 `\\times` `\\div`；"
        "不等号写 `\\le` `\\ge` `\\ne`；",
        "   · 分式 `\\frac{a}{b}`、根号 `\\sqrt{x}`、希腊字母 `\\alpha`、无穷 `\\infty`；",
        "   · **中文放在 `$` 外面**：`$x^2$ 是非负的`（对），不要写 `$x^2$ 是非负的$`（错）。",
        "   照这句写：`求导得 $f'(x)=3x^2-3=3(x-1)(x+1)$，令它为零得到 $x=-1$ 和 $x=1$ 两个分界点。`",
        "2. ⚠️ **JSON 里的反斜杠必须写成两个**：`\\frac{a}{b}` 在 JSON 字符串里要写成 "
        "`\\\\frac{a}{b}`。只写一个反斜杠，它和后面的字母会被当成转义序列"
        "（`\\f` 是换页符），整段话会变成乱码、甚至让 JSON 解析失败。",
        "3. 需要单独成行、居中的式子用 `$$...$$`；其余一律 `$...$`。",
        "4. 只有**数学**才进 `$`：金额、百分比、步骤编号（`$5`、第 2 步）不要包。",
        "5. title 与 outline 的 title / summary 是**短标签**，用中文描述，不要写数学式子。",
        "",
        "## intent 与 outline 的规则",
        '1. 适合用动画讲的题（求极值、证明、几何、函数图像、求面积…）→ intent 取 "propose"，'
        "此时 outline 每个分镜一条，与 scenes 一一对应（durationSec 与那一镜的 duration 写同一个数）。",
        '2. 纯概念问答（"什么是当且仅当"、"导数是干嘛的"、"这个符号怎么读"）→ intent 取 "none"，'
        "此时 **outline 和 scenes 都给空数组 []**，只把答案写在 brief 里。"
        "不要为了凑动画硬造分镜。",
        # ⚠️ 2026-09-17 加（用户当场质疑"难道直角三角形不能画出来看吗"）：原来这条举例里
        # 写的就是"什么是直角三角形"—— 一个**明明该画**的图形题被当成"不配动画"的样本，
        # 和《第一步》"几何题只画图形本身"直接对冲。判据必须说清楚，否则模型会按
        # "题短 / 词基础"去判，把能画的也划到 none 里。
        '⚠️ 判据是**「这道题不画也能讲明白吗」**，不是「题目短」、更不是「这个词听起来基础」。'
        "能画、而且画出来更清楚的（几何图形、函数图像、数轴、网格、计数…）一律用 propose —— "
        "**先把图画出来，再说话**。只有那种「画什么图都只是装饰」的纯术语/纯逻辑关系"
        "（当且仅当、逆命题、充要条件）才用 none。",
        "3. brief 是「不看视频也能看懂」的那段话：给出结论和关键一步，"
        "不要写成「下面我用动画演示一下」这类空话。**它不能为空** —— 纯问答时它就是全部回答。",
        # ⚠️ 2026-09-17 加（用户反馈）：多轮里第二轮的 brief 和第一轮几乎重复，
        # 读起来像"没听懂要求"。根因是当时的规则只有"直接回答这道题"这一条 ——
        # 模型在「用视频讲一下、给无基础的人」这类**修改要求**的追问轮里，
        # 会把上一轮的答案原样再讲一遍（契约上没错，体验上完全错）。
        # 这一条把"追问轮该说什么"补上。
        "4. ⚠️ **如果对话历史里已经有你上一轮的回答（说明这次是追问，或要求修改讲法/画面），"
        "brief 要先用一句话说清「这一轮改了什么、接下来怎么讲」，不要再把上一轮的内容复述一遍。**"
        "例：「好的，我换成零基础的口径，并把「割线趋近切线」这个过程拆成 3 个分镜："
        "先算平均变化率，再让第二个点贴过去，最后给出导数的定义。」"
        "（用户问的是**新问题**、不是要求修改时，直接回答新问题。）",
        "5. title、outline 的 title / summary 都是给中文用户看的，写中文，别太长。",
        # 2026-09-17 加：多轮"改一版"的行为准则。
        # 模型能看到的历史里带着**它自己历次输出的分镜 JSON**（见 _history_with_storyboards），
        # 所以这里要说清"拿到那些 JSON 该怎么办" —— 否则它会当成参考资料，而不是"这就是当前这一版"。
        "6. **历史里你自己输出的分镜 JSON，就是当前这一版的画面**（多轮改一版全靠它）：",
        "   · 用户这次是在**改那一版**（「第 2 镜讲慢点」「换成蓝色」「别用坐标系」）→ "
        "**只改他点名的地方**。没被点名的分镜要**照着你上一版的 JSON 原样保留**："
        "`id` / `duration` / `elements` / `timeline` 逐项一致，"
        "**连坐标、颜色、文字内容都不要动**（全文就在你手里，直接抄）。"
        "顺手优化、顺手统一风格都算越界 —— 用户会觉得「我调好的东西又乱了」。"
        "改了哪几镜，brief 里就点明是哪几镜（配合规则 4）。",
        "   · **即使被校验打回重做，也只修被拒的那一处**，不要借机改动别的地方"
        "（反馈语里的「没有报错的部分保持你原来的设计」就是这个意思）。",
        # ⚠️ 2026-09-17：用户提过"同一个会话里经常问不同的题，前后两个视频可以完全无关"，
        # 所以**不能有"偏向保留"的默认**（原来写的是"拿不准时按改上一版处理"，已改）——
        # 把新题误当修改（新视频带着旧骨架）比把修改误当新题（重做一版、题目不变）更坏。
        "   · 用户这次提的是**另一道题** → 正常做一份新的。判据就是**用户这一句话本身**："
        "像一道完整的题目（自带题面）→ 当新题做，前后两版毫无关系也完全正常 —— "
        "同一段对话里问几道不同的题是常事，**不要为了「接得上」硬留上一版的元素**；"
        "像一句针对画面的改动要求（通常很短、并提到上一版的某个具体东西）→ 按改上一版处理，"
        "并在 brief 里说明这一轮改了什么。",
        "",
        "## 红线（违反会被校验层拒绝，你会一次收到**全部**错误，请一次改完）",
        "1. 你不写任何代码。元素类型（kind）和动作（do）**只认下面规格里列出的名字**"
        "（全小写、下划线分隔），不能发明新的；每个动作的附加参数名也是写死的"
        "（规格里标了 `args`）。"
        "⚠️ 规格里的这些名字和 Manim 的类名/方法名**故意错开**，就是为了让你没法凭 Manim 的"
        "记忆直接填 —— 凡是规格里没列出的名字，不管它在 Manim 里多常见，在这里一律非法。",
        "2. 元素必须先写在该分镜的 elements 里，才能被 timeline 或别的元素引用。"
        "引用不存在的 id 会报错。",
        "3. 颜色名与数学函数是**封闭白名单**（规格最后「硬性规则」里列全了），"
        "不要凭印象拼写（写 LIGHT_BLUE / arcsinx 这类不存在的名字会被直接拒绝）。",
        # ⚠️ 2026-09-17 改：原文是"必须用 show 显式显示" —— 把 show（瞬间出现）当成了唯一出路。
        # 实测动态元素用 fade_in 上屏完全有效（真的淡入，之后 updater 照常工作，见 §8.47），
        # 而"只能瞬间出现"正是"东西啪地冒出来"这种生硬过渡的来源。
        "4. 含 tracker 引用（\"$名字\"）的元素是动态元素，**不会自动上屏** —— 必须显式上屏："
        "`{\"do\":\"show\"}` 是**瞬间出现**，`{\"do\":\"fade_in\"}` 是**淡入**。"
        "⚠️ **别一律用 show**：画面里啪地冒出一个点、一条线，观感很硬；"
        "要过渡自然就用 `fade_in`（淡入之后它照样每帧重算，不影响动态性）。",
        "",
        "## 第一步：先判题型，再决定这道题的「画面引擎」",
        "**这一步决定成品有没有模板味，比后面所有细节都重要。**",
        "拿到题先归类（problem_type 就从这里选一个），再回答一个问题：这道题最关键的"
        "**变化 / 对比 / 累积 / 构造**过程，用什么画面能让人一眼看见？"
        "把它定为主体分镜的机制，其余元素都为它服务。",
        "",
        "各题型的**典型画面引擎**（是提示，不是让你从里面挑一个套上去）：",
        "- `function_analysis`（函数图像、单调性、极值、切线）→ **参数扫描**："
        "一个 tracker 驱动的动点走一遍，切线/函数值跟着变，旁边同步显示数值。",
        "- `geometry`（几何、证明、图形关系）→ **只画图形本身**"
        "（polygon / angle / brace / highlight），靠平移、旋转、割补、拼接让结论自己浮出来。",
        "- `area_integral`（面积、积分、累积量）→ **分割与累加**：把区域切成小块，"
        "逐块长出来，块数变多时逼近真值（area / riemann）。",
        "- `algebra`（方程、不等式、恒等变形）→ **两边同做一件事**：对两边同时加/乘/移项，"
        "把差异画出来，临界时刻定格。",
        "- `sequence_series`（数列、求和、归纳、级数）→ **累积构建**：一项一项长出来"
        "（`create` + `lag_ratio`），或对称配对消去。",
        "- `limit`（极限、逼近、收敛）→ 让参数连续扫过临界值，定格在"
        "「无限接近但取不到」的那一刻。",
        "- `probability`（概率、计数、组合）→ **穷举或树状展开**，或用频率逐步逼近理论值。",
        "- `concept`（纯概念问答）→ 不出动画（intent=none），或只做一个概念示意图。"
        "⚠️ 只有**画什么图都只是装饰**的纯术语/纯逻辑关系（当且仅当、逆命题、充要条件）"
        "才这样；能画的图形题属于 `geometry`，先把图画出来。",
        "",
        "### 三条**反模板**硬要求（违反就是白做，请重做）",
        "1. **不要默认上坐标系。** 只有函数图像、解析几何、参数扫描这几类题才该出现 "
        "`axes`。几何题画多边形，网格/矩阵题画表格，概率题画点阵或树 —— "
        "一道几何证明里出现 `axes`，基本就是跑题的信号。",
        "2. **至少有一个分镜要有「只有这道题才有」的东西**：一个具体数值、"
        "一个特定位置的动点、一段真的推导步骤。把所有名词换成「函数」「图形」「公式」"
        "之后依然成立的分镜，就是模板分镜，删掉重想。",
        "3. **不要每镜都是同一套元素。** 封面镜、结论镜可以纯文字；"
        "图形只出现在真的需要看见它的那一两镜里。"
        "每一镜都摆 text + formula + 同一个坐标系，是最典型的模板产物。"
        # ⚠️ 2026-09-17 加：这一条原样留着会被读成"少写字" —— 它说的是**图形元素**
        # 的复制粘贴，不含"这一步在做什么"的讲解文字。不点破的话，它会和下面
        # 「每一镜都要有讲解文字」这条直接对冲（用户反馈：解释性文字偏少）。
        "⚠️ 它针对的是**图形元素**的复制粘贴，**不是让你少写字**："
        "「这一步在做什么、这个数从哪来」的讲解文字每一镜都该有 —— 那是讲解，不是模板。",
        "",
        "### 每一镜都要有「讲解文字」（用户反馈：解释性文字偏少）",
        # 为什么单开一节、且写得这么具体：这属于"偏好"而不是"禁令" —— 校验层拦不住
        # "这一镜一句解释都没有"，只能靠 prompt 把下限说死（同 §8.31 的教训：
        # 只描述不示范的规范，模型会按最省力的方式理解成"可以不做"）。
        "- **画面上的字是你唯一能说话的地方。** 观众看的是视频，不是你的 brief："
        "**每一镜都要有文字**说清这一步在做什么、这个数 / 这个式子从哪来、凭什么可以这么算。"
        "只把图形或公式摆出来、一个字都不解释，等于让观众自己猜 —— 那不算讲完。",
        "- **下限**：每个分镜至少 1~2 处讲解文字（结论镜、纯演示镜也要有一句"
        "「这是在干什么」）。一镜从头到尾只有图形和一个标题，就是不合格。",
        "- **文字要紧挨着它解释的东西**：给某个量配标注就用 "
        "`{\"next_to\": {\"of\": \"p\", \"direction\": \"up\", \"buff\": 0.3}}` 贴在它旁边；"
        "结论、公式再单独放到一角或底部并放大（`font_size` ≥ 32）。"
        "**不要把该贴在图旁边的说明全塞到四个角**。",
        "- **连续推导用底部字幕一句一句换**（`replace`），比堆一个长公式好懂得多 ——"
        "写法见规格里的「同一位置换内容」和示例 1。",
        # ⚠️ 2026-09-17 加（用户要求）：字幕与运动是**重叠**关系，不是串行。
        # 示例 1 镜 3 实测出来的：把"点走到哪，切线斜率就是 2x"这句先 replace 上去、
        # **再**开扫 —— 那句话才是对正在发生的事的注解；等扫完了再打出来，观众已经看完了。
        # 这条原来是模型自己"碰巧做对、没人告诉它"，写进来才稳定。
        "- ⚠️ **一句讲解配一段运动，两者要重叠，不是「演完再说」**："
        "先把字幕 `replace` 成下一句，**再**开始这一段运动（`tracker_to` 这类）—— "
        "那句话是给正在发生的事做注解的，等动完了才出现就等于没讲；"
        "反过来，运动已经开扫、字幕还停在上一句，观众也不知道自己在看什么。"
        "（示例 1 镜 3 的顺序：`replace` 字幕 → `tracker_to` 扫描 → 扫完 `replace` 收口。）",
        # ⚠️ 2026-09-17 加（用户要求）：字幕停不住 = 观众读不完。
        # 实测踩过：一句话刚 `write` 完就 `replace`，那条字幕的"完整可见时间"是 0 ——
        # 写动画的最后一帧和淡出动画的第一帧挨在一起，观众根本来不及读。
        "- ⚠️ **每条字幕（除去淡入淡出）至少要完整停留 1 秒**：写完 / 换句之后要留出**读它的"
        "时间** —— 用 `wait`，或者让后面那段动作替它占够 1 秒，别上一句刚写完就换下一句；"
        "淡入淡出本身不算停留时间。一镜最后那条字幕同样要留够（否则会直接跟着分镜边界淡掉）。",
        # ⚠️ 2026-09-17 加（用户要求）：推导类画面最怕"式子摆出来、为什么这么变不说"。
        # 示例 1 镜 4 的四行推导每行右侧挂一句旁注（a1~a4，GREY、比公式小一号），
        # 那是这一行"在干什么"；旁注比公式便宜（0.8 秒），所以紧跟着公式出现。
        "- ⚠️ **推导类画面（按定义算导数/积分、解方程、恒等变形…）每一步都要有"
        "「这一步在干什么」**：一行公式配一句旁注，用 `next_to` 挂在那一行的右侧、"
        "字号比公式小一号、用 `GREY` 这类弱色 —— **别把一长串式子摆完就让观众自己跟**。"
        "推导过程还要**真的占分镜**，不能只在结尾一句话提一嘴「用定义也能算出来」。"
        "（写法见示例 1 镜 4：几行用 `next_to(down, aligned=left)` 左对齐串起来，每行右侧一句旁注。）",
        # ⚠️ 2026-09-17 加（用户要求）：几何直觉与代数推导是**两条路**，只走一条都不完整。
        # 实测反馈：前三镜全是看图（比值逼近 2、点走一遍看斜率），"用定义算"只在结尾被提了一嘴 ——
        # 观众学会了看图，考场上还是算不出来；反过来只摆代数变形，就退回"把公式摆出来"。
        "- ⚠️ **几何直觉与代数推导是两条路，别只走一条**：**几何直觉**（让量真的动起来、"
        "看它逼近谁）回答「这东西是什么、为什么长这样」；**代数推导**（代入、展开、约分、取极限）"
        "回答「具体怎么算」。只讲前者，观众会看图不会做题；只讲后者，就回到「把公式摆出来」的老路。"
        "两边都能让这道题更清楚时**就都讲**（示例 1：镜 2/3 是几何直觉，镜 4 是用定义推导），"
        "并让它们**互相印证**：推导出来的结果要和图上看到的、算出来的对得上。"
        "⚠️ 这里是说「题里本来就有两条路」的情况；纯几何题、纯代数题不必硬凑另一条。",
        "- ⚠️ 讲解文字也是元素、也要上屏时间：加完字幕记得把 `write` / `replace` 的"
        "耗时算进 duration（示例 1 的算法：镜 1 的 "
        "`1.2 + 1.5 + 1.2 + 0.8 + 1.2 + 1.0 + 0.5 = 7.4`；省略 `run_time` 按规格里的默认值算）。",
        "",
        "## 分镜设计规范",
        "- 分镜数量按内容定，2~6 个都行。**不要为了凑结构硬加一个「封面镜」**——"
        "标题本身没有信息量就直接从内容开始。",
        # ⚠️ 2026-09-17 改：原来是"每个分镜 duration 建议 4~12 秒"（§8.31 把硬约束改成
        # "建议"时留下的半成品）。它给了一个**封闭区间**，模型于是先把长过程按 12 秒切开，
        # 正好和下面"连贯过程别拆镜、用长分镜一口气演完"打架。秒数该跟着内容走，不该反过来。
        "- duration 按内容定：**先想这一步要演什么、这句话要说到哪，再让秒数跟着走**。"
        "不要先挑一个秒数（「就 8 秒吧」）再往里塞内容或砍内容 —— 校验层允许 0.5~120 秒、"
        "timeline 最多 80 个动作，宁可一镜长一点，也别为了让数字好看切开连贯过程。",
        "- ⚠️ **连贯的过程不要拆成多镜、每镜各画一遍。** 分镜边界会把画面全部淡出、"
        "每个分镜都从空白画起，所以同一个东西在下一镜里只能重画 —— "
        "观感上等于「从头再来」，这是最常见的质量事故（数组类内容尤其明显）。"
        "两种正确写法：① **用一个长分镜 + 一条长 timeline 一口气演完**"
        "（二分查找、排序、迭代收敛这类步骤多的内容就该这样）；"
        "② 确实要分镜时，把该一直留在屏上的东西用 `carry` 承接过去"
        "（写法见规格里的「跨分镜延续：carry」，示例 3 就是）。",
        # ⚠️ 2026-09-17 删：这里原有一条「每个分镜屏幕上元素别超过 8 个，多了会挤」
        #（用户要求去掉）。它本来就只是软建议、校验层从不检查元素条数；真正管拥挤的是
        # `metrics.py` 的尺寸估算（估算超预算才拒），所以删掉不会放进来"堆不下"的画面。
        # 别再顺手加回条数上限 —— 字幕也算元素，条数上限会顺带压掉解释性文字（§8.44）。
        "- duration 是**目标节奏**，不是硬约束：把 timeline 里所有动作的耗时加起来"
        "（parallel 段按最长的那个算，省略 run_time 就用规格里的默认值），"
        "这个和应该≈ duration，差 1 秒以内都正常。和偏小会自动补停顿；"
        "偏大不会报错，但视频会比声明的长。",
        "- 中文文字放 text 的 content；公式放 formula，或写在 text 的 $...$ 里做混排。",
        "- 讲「变化」「联动」（动点滑动、切线跟着转、参数在变）时，"
        "**必须**用 tracker + \"$名字\" 引用，不要写成死的数字——那才是这个 DSL 的价值。",
        "- 结论分镜要给出明确的数学结论（公式或数值），不要只说\"所以得证\"。",
        "- 讲网格类内容（卷积/池化/滑动窗口/棋盘格/矩阵）：格子用 table，"
        "框选与滑动用 cell_box + move_cells（框的位置由格子算出来）。"
        "**不要**用 rect + move_to 自己估坐标框格子 —— 格子尺寸是内容撑出来的，估出来一定歪。"
        "格子想统一大小就写 cell_w/cell_h，想紧凑把 pad_x 调小（默认 1.3 偏大）。",
        "- ⚠️ **同一个位置换文字（底部字幕、逐句解释）必须用 `replace`** ——"
        "它会把旧文字下屏、新文字上屏；再 create 一个新 text 会和旧文字叠在一起。"
        "默认 effect 是 crossfade（淡出淡入），换字幕就用默认值或 \"slide\"，"
        "别用 \"morph\"（那是给图形变形用的）。"
        "⚠️ `into` 里的新文字**要写和原来一样的 `place`** —— replace 用的是新元素"
        "自己的位置、不会继承旧的，不写新的就会跑到画面正中。",
        # ⚠️ 2026-09-17 加（用户："输出的表格还是会中途淡入淡出"）：`replace` 换的是**整个
        # 元素**，而表格/坐标系这类"容器 + 局部内容"的元素里，一次只变一点点 ——
        # 用 replace 会把边框、格子、已有内容全部淡一遍。四个数分四镜填，整张表就闪四次。
        # 正确做法是"容器声明一次、全程不动，局部内容做成独立元素单独上屏"。
        "- ⚠️ **表格 / 坐标系这类「容器 + 局部内容」的元素，不要用 `replace` 整块换** ——"
        "它会把边框和已有内容一起淡一遍（填一次闪一次）。正确做法是：**容器声明一次、"
        "全程留着不动**，把要变的那一小块做成**独立元素**单独上屏/更新。"
        "比如表格逐格填数：表格本身不动，每个数字是一个 text，用 "
        "`{\"cell\": {\"of\": \"表id\", \"row\": 1, \"col\": 1}}` 贴在那一格上，"
        "算出来一个就 `fade_in` 一个（位置由格子算，也不用自己估坐标）。"
        "⚠️ 判断标准：**这一镜真的变了的，是「整个元素」还是「元素里的一小块」** —— "
        "前者用 `replace`（字幕就是典型），后者拆出独立元素做局部更新。",
        # ⚠️ 2026-09-17 加（用户："右上角的 4×4=16 没消失"）：中间量的**退场**要和
        # 它的**上台**一样有意识。carry 的错误用法是"上一镜有的就全带过来"，
        # 于是屏幕上越堆越多、观众分不清哪个数还有用。
        "- ⚠️ **每个分镜只 carry 这一镜还需要的东西**：上一镜用过的中间量"
        "（算出来的某个数、临时标注、辅助线）如果这一镜不再用，就**让它退场**"
        "（本镜不 carry 它，边界清屏会自动淡掉）—— 不要「上一镜有什么就全带过来」。"
        "屏幕上只留当前这一步用得上的东西，观众才知道该看哪里。"
        "⚠️ 反过来，**这一镜结论要用到的东西一定要 carry**（别让它在切镜时消失）："
        "先问一句「这一镜结束时，哪些东西还得留在屏上」。",
        "- ⚠️ 元素太大放不下会被**直接拒绝**（不是帮你缩小）：报错会写明估算尺寸、可用范围"
        "和改法。别指望靠缩小字号硬塞，一条超宽的表/一行太长的文字请**减列、换行或拆分镜**。",
        "",
        "## 元素与动作规格（唯一权威来源，严格按此生成）",
        dsl.describe(),
        "",
        "## 参考示例",
        "四个示例都**必须看完**。它们只示范**怎么写**（envelope 结构、tracker 的引用方式、"
        "`show` / `fade_in` 的上屏时机、duration 怎么凑够、字幕怎么一句句换），"
        "**不是让你照抄它们的结构或镜头数** —— 示例 1 有坐标系、有 5 个镜头，"
        "是因为那道题（求导数）本身就要走「几何直觉 → 用定义推导 → 推广成公式表」这条路；"
        "换一道几何题还上坐标系、还硬摆 5 镜，就是跑题。",
        "",
        "示例 1（函数题，完整的 5 镜：几何直觉 + 用定义推导 + 常用公式表；"
        "字幕跟着运动切、推导每一步带旁注）：",
        _EXAMPLE,
        "",
        "示例 2（几何题／勾股定理证明：**全程没有 axes**；四个全等三角形用 rotate + move_to "
        "拼成弦图，再走一遍代数推导，字幕跟着运动切、每一步带旁注）：",
        _EXAMPLE_GEOMETRY,
        "",
        "示例 3（表格／卷积：3×3 输入 ⊛ 2×2 核 = 2×2 输出；窗口用 cell_box + move_cells "
        "滑动，位置全由格子算；**格子里的数会被贴在格子上、再提到下方逐项相乘**"
        "（`place.cell`）、算完拼成输出表。表格只写 pad_x 收紧、不写 cell_w）：",
        _EXAMPLE_GRID,
        "",
        "示例 4（纯概念问答：intent=none，只回文字不给动画。**注意它的题是「当且仅当」** ——"
        "换成几何题、函数题就该出动画了，参见上面规则 2 的判据）：",
        _EXAMPLE_NONE,
    ]
    if extra_rules:
        parts += ["", "## 补充要求（本次调用专属）", str(extra_rules).strip()]
    text = "\n".join(parts)
    _warn_if_prompt_too_big(text)
    return text


# 提示词预算（token，粗估）。
#
# ⚠️ 这里防的**不是**"上下文撑爆"（实际用的模型是 1M 窗口，见 HANDOFF §8.59），
# 而是**质量稀释**：示例越堆越多，规则会被淹没、注意力被分散。
# 经验值（2026-09-17 实测）：4 个示例时 system prompt 约 18.7k token，其中示例占
# 8.6k（46%）。建议把"完整示例"控制在 6~10 个、总量控制在 40k 以内；每个新示例
# 都该覆盖**还没覆盖过的题型或机制**，重复的删掉（宁少勿滥）。
_PROMPT_BUDGET_TOKENS = 40000
_prompt_budget_warned = False


def _warn_if_prompt_too_big(text):
    """超预算只在**第一次**喊：每次调用都喊会刷屏，而这件事一天只该看一次。"""
    global _prompt_budget_warned
    if _prompt_budget_warned:
        return
    n = _est_tokens(text)
    if n > _PROMPT_BUDGET_TOKENS:
        _prompt_budget_warned = True
        print(f"      [WARN] system prompt 已约 {n} token（预算 {_PROMPT_BUDGET_TOKENS}）——"
              f"示例可能开始摊薄规则了。跑 python generate.py --dump-prompt 看体积画像。",
              flush=True)


def prompt_stats(extra_rules=""):
    """
    提示词体积画像：总量、四个示例各占多少、是否超预算。

    给启动横幅（api/main.py）和 `--dump-prompt` 用。为什么要单列出来：
    "当前提示词多大"这件事以前只能手工量 —— 2026-09-17 就是手工量才发现
    示例占了 61% 字符的。看不见的东西没法管。
    """
    text = build_system_prompt(extra_rules)
    ex = {"示例 1 函数": _est_tokens(_EXAMPLE),
          "示例 2 几何": _est_tokens(_EXAMPLE_GEOMETRY),
          "示例 3 网格": _est_tokens(_EXAMPLE_GRID),
          "示例 4 概念": _est_tokens(_EXAMPLE_NONE)}
    total = _est_tokens(text)
    return {"chars": len(text), "tokens": total,
            "examples": ex, "example_tokens": sum(ex.values()),
            "budget": _PROMPT_BUDGET_TOKENS,
            "over_budget": total > _PROMPT_BUDGET_TOKENS}


# 多轮对话：预算与裁剪
# ------------------------------------------------------------------------------
# 主流路线（ChatGPT / Claude / DeepSeek 网页版）都是这三条，缺一条就会出问题：
#
#   1. **带上原始历史**（用户原话 + 助手原话），不做摘要、不做改写。
#      曾想过"只带用户问题 + brief"来省 token —— 但追问里全是"刚才那个第二步"
#      这种指代，压缩后就指不到东西了。
#   2. **按 token 预算裁剪，且从最旧的开始丢**，并且**丢到"用户"这条边界为止**。
#      从中间切断会出现"assistant 的回答没有对应的提问"，模型会开始胡乱猜测。
#   3. **保留最近 N 轮**作为下限，保证再怎么裁都能接上话。
#
# 预算给多大（2026-09-17：6000 → 20000 → 最终 10000）
# ------------------------------------------------------------------------------
# 6000 是当初拍的，没有实测依据；而 `/api/chat` 的《工作手册》已经是
# **22000 字符**（每轮固定发）—— 也就是说历史预算只有固定开销的 ~27%：
# 聊到十几轮就开始丢最早的内容，用户的感受就是"它记不住"。
# 中间试过 20000，实测输入从 14.5k 涨到 20k token **耗时不变**（3.5s vs 3.1s，
# 噪声内），所以"多发历史会变慢"这件事本身不成立 —— 贵的是 token 账单，不是等待。
#
# 2026-09-17：6000 → 10000 → **30000**。
# 10000 是按"省输入 token"定的（上面那次实测说明它只是贵、不是慢），而当天晚些
# 确认了实际用的模型是 **1M 上下文** —— 30000 只占窗口的 3%，却能把"带画面的对话"
# 从约 6 轮拉到约 18 轮。多轮改一版的关键就是"模型手里有没有上一版的原料"（§8.38），
# 所以这个钱值得花。想更长/更省：`set MSB_HISTORY_TOKENS=…`（见 history_budget()）。
#
# ⚠️ 这里数的是**输入历史**：思考（reasoning）不进历史、也不占这个预算 ——
#    它和正文共用的是输出侧的 max_tokens（见 HANDOFF §8.11 / §8.35）。
#
# token 只能估：中文约 1 字 ≈ 1 token、英文约 4 字符 ≈ 1 token。
# 精确计数要引第三方库，而这里只需要"别把上下文撑爆"，粗估足够 ——
# 估高一点（偏保守）比估低好。
_HISTORY_TOKEN_BUDGET = 30000
_HISTORY_MIN_TURNS = 4


def history_budget():
    """
    历史预算（token）。默认取 `_HISTORY_TOKEN_BUDGET`，可用 `MSB_HISTORY_TOKENS` 覆盖。

    为什么留这个口子：这个数直接决定"能聊多少轮"和"每轮多花多少输入 token"，
    属于**该按实测调、不该写死**的参数。做成环境变量，扫档位就不用改代码。
    写坏了（非数字 / 非正数）只打 WARN 并回退默认值 —— 配置错误不该让服务起不来。
    """
    v = _first_env(("MSB_HISTORY_TOKENS",))
    if v:
        try:
            n = int(float(v))
            if n > 0:
                return n
            raise ValueError("必须是正数")
        except ValueError as e:
            print(f"      [WARN] MSB_HISTORY_TOKENS={v!r} 无效（{e}），"
                  f"回退默认 {_HISTORY_TOKEN_BUDGET}")
    return _HISTORY_TOKEN_BUDGET


def _est_tokens(text):
    """粗估 token 数（中文按字、英文按 4 字符）。宁可高估，别把上下文撑爆。"""
    s = str(text or "")
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk + max(0, len(s) - cjk) // 4


def trim_history(history, budget=None, min_turns=_HISTORY_MIN_TURNS):
    """
    history 是 [{"role": "user"|"assistant", "content": str}, ...]（按时间正序）。
    返回裁剪后的列表：**从最旧的开始丢**，并保证切在"用户"这条边界上。

    Args:
        budget: 历史预算（token 估值）。**None = 用 `history_budget()`**
            （默认 30000，可被 `MSB_HISTORY_TOKENS` 覆盖）。显式传值只影响这一次调用。

    为什么必须有 min_turns：极端情况下（用户一次贴了篇论文）预算会被一条消息吃光，
    裁完剩个空列表，模型就彻底失忆了 —— 那还不如不管预算、至少留着最近几轮。
    """
    if budget is None:
        budget = history_budget()
    items = [h for h in (history or [])
             if isinstance(h, dict) and h.get("role") in ("user", "assistant")
             and str(h.get("content") or "").strip()]

    # 从后往前累计，直到超预算
    kept, used = [], 0
    for h in reversed(items):
        cost = _est_tokens(h["content"]) + 4          # +4 是每条消息的固定开销
        if kept and used + cost > budget:
            break
        kept.append(h)
        used += cost
    kept.reverse()

    # 保证至少有 min_turns 条（按"条"算，user+assistant 各算一条）
    if len(kept) < min_turns:
        kept = items[-min_turns:]

    # 切到"用户"边界：开头若是 assistant（它的提问被裁掉了），继续往后丢
    while len(kept) > 1 and kept[0]["role"] != "user":
        kept.pop(0)
    return kept


def _history_with_storyboards(history):
    """
    消息记录 → 真正发给模型的 messages：**assistant 的内容 = brief + 它那一轮输出的分镜 JSON**。

    为什么要这样（2026-09-17，用户要求"把 json 也当作模型的对话输出"）：
    多轮改一版时，模型得看得到自己上一版**具体写了什么** —— 坐标、颜色、文字内容全在 JSON 里。
    只看 brief 的话它没有可照抄的原料：实测症状是"用户只让改第 2 镜，它顺手把第 3、4 镜也
    重写了"。那不是越界，是**手里没有原料**。（先试过一版"压成每镜一行的摘要"，结构级够用、
    细节级不够 —— 摘要里没有参数，模型只能重写。）
    把 JSON 放进它**自己的历史消息**里，等于"它说过的话"：多轮天然连续，
    也不局限只带上一版（要哪一版都在上下文里，由 token 预算裁剪）。

    JSON 存在记录的 `meta.storyboard`、**不是 `content`**：
      · `content` 是给人看的（前端聊天框显示的就是它），塞 JSON 进去会污染界面；
      · "模型该看到的"和"界面该显示的"本来就是两件事。
    没有 storyboard 的记录（纯文字回答、2026-09-17 之前的旧记录）原样返回 ——
    老会话一个字都不受影响。

    ⚠️ 代价是量过的：一份 JSON 中位 **1463 token**（49 份实测，最大 2776），
    每轮从 ~360 涨到 ~1630 —— 默认预算 30000 下能留住约 **18 轮**带画面的对话
    （默认值 2026-09-17 从 10000 提到 30000，理由见上面那段注释）。
    """
    out = []
    for h in history or []:
        if not isinstance(h, dict):
            continue
        role, content = h.get("role"), h.get("content")
        raw = None
        if role == "assistant" and isinstance(h.get("meta"), dict):
            raw = h["meta"].get("storyboard")
        if isinstance(raw, dict) and raw.get("scenes"):
            content = (str(content or "").rstrip()
                       + "\n\n【这一轮产出的分镜 JSON】\n```json\n"
                       + json.dumps(raw, ensure_ascii=False) + "\n```")
        out.append({"role": role, "content": content})
    return out


def diff_scenes(prev_raw, new_raw):
    """
    逐镜对比两份分镜的"结构"，返回变化说明列表（空 = 结构完全一致）。

    用来回答一个很实际的问题：**用户只让改第 2 镜，模型有没有顺手改别的？**
    2026-09-17 实测（拿 examples/free_pythagorean.json 当上一版、让它"第 2 镜讲慢一点"）：
    目标镜把时长从 9 秒拉到 12 秒（对），但第 3、4 镜也被动了（第一次尝试把图例写错、
    被校验打回后连别的镜一起改了）。提示词已经加了"没被点名的分镜逐项一致"，
    但**提示词管不住的东西要能看见** —— 这个函数就是那条视线。

    ⚠️ 只做**只读对比**，不拦截、不改写：模型有权为了修校验错误调整别的分镜
    （比如引用完整性要求它改），硬拦会把正确的输出拒掉。调用方拿它打 WARN 即可。
    """
    def shape(raw):
        out = {}
        for i, sc in enumerate((raw or {}).get("scenes") or [], 1):
            if not isinstance(sc, dict):
                continue
            kinds = tuple(sorted({str(el.get("kind")) for el in (sc.get("elements") or [])
                                  if isinstance(el, dict) and el.get("kind")}))
            acts = tuple(str(ac.get("do")) for ac in (sc.get("timeline") or [])
                         if isinstance(ac, dict) and ac.get("do"))
            out[sc.get("id", i)] = (sc.get("duration"), kinds, acts)
        return out

    a, b = shape(prev_raw), shape(new_raw)
    msgs = []
    for sid in sorted(set(a) | set(b), key=lambda x: str(x)):
        if sid not in a or sid not in b:
            msgs.append(f"分镜 {sid}：{'新增' if sid not in a else '被删掉了'}")
            continue
        if a[sid] == b[sid]:
            continue
        parts = []
        if a[sid][0] != b[sid][0]:
            parts.append(f"时长 {a[sid][0]}→{b[sid][0]}")
        if a[sid][1] != b[sid][1]:
            parts.append(f"元素 {list(a[sid][1])}→{list(b[sid][1])}")
        if a[sid][2] != b[sid][2]:
            parts.append("动作序列有变")
        msgs.append(f"分镜 {sid}：" + "、".join(parts))
    return msgs


def build_user_prompt_multi(problem, history=None):
    """
    有历史时的 user prompt。

    为什么要显式写"可能是追问、要先判断指代"：模型看到一句孤零零的
    「生成对应视频」，会照字面理解成"你没给题目"，然后反问一句让人啼笑皆非的话。
    我们实测踩过这个坑（用户先问傅里叶变换，再说"生成对应视频"，模型回：
    「这次只收到了一句指令，没有附带任何题干……」）。历史给全了再加这一句，
    它才会去做指代消解。

    Args:
        history: 之前的对话记录。**带 `meta.storyboard` 的 assistant 记录会被还原成
            "brief + 分镜 JSON"**（见 `_history_with_storyboards`）——
            多轮"改这一版"靠的就是模型能看到自己上一版写的是什么。

    ⚠️ 历史是**只追加**的：前缀缓存按"前缀逐字相同"生效，所以每轮只能在末尾接一段。
        这也是"分镜 JSON 放进 assistant 消息"而不是"另起一段当前画面"的原因 ——
        后者每轮内容都在变，会把历史的缓存整个打掉。
    """
    parts = []
    if history:
        parts.append(
            "下面是这个会话里之前的对话，最后一条才是本次请求。\n"
            "⚠️ 本次请求可能是**对上一条的追问或指代**（例如「生成对应视频」「换个讲法」"
            "「第三步再讲慢点」）。这种情况请结合上文判断用户到底要讲什么，"
            "**不要因为这一句本身没给题目就反问用户**。\n"
            "另外：你之前的回答里若有「请把题目发来」这类话，说明那次是缺上下文，"
            "现在已经补上了，按上文重新理解即可。\n"
        )
    parts.append("请按「用户这次的输入」处理：它可能是一道**新题目**，"
                 "也可能是要**改你上一版的分镜**（判据见 system 规则 6）。\n"
                 "要求：图形与推导要真的对得上这道题，不要套模板话术。\n"
                 "只输出 JSON 对象本身。\n\n"
                 f"---- 用户这次的输入 ----\n{str(problem).strip()}")
    return "\n".join(parts)


_FEEDBACK_JSON = (
    "你上一次的输出不是合法 JSON（解析失败：{err}）。\n"
    "请重新输出**一个完整的 JSON 对象**，不要任何额外文字、不要 Markdown 围栏。"
)

_FEEDBACK_SCHEMA = (
    "你上一次的分镜 JSON 没有通过校验，**全部**错误如下：\n\n"
    "  {err}\n\n"
    "请把这些错误**一次全部改掉**，然后**重新输出完整的 JSON 对象**"
    "（不是补丁、不是片段，是完整的那一份；键顺序仍是 brief 在最前）。"
    "没有报错的部分保持你原来的设计。"
)

_FEEDBACK_TRUNCATED = (
    "你上一次的输出被**长度上限截断**了（finish_reason=length）：思考占掉了绝大部分 "
    "token 预算，正文还没写完就断了。\n"
    "请**大幅压缩思考**，直接按约定的结构把 JSON 写出来 —— 不要复述题目、"
    "不要罗列备选方案、不要写完再自我检查一遍。想清楚就动笔。\n"
    "仍然要输出**完整的 JSON 对象**，brief 在最前。"
)

_FEEDBACK_BRIEF = (
    "你上一次的输出里 `brief` 缺失或为空。\n"
    "brief 是直接展示给用户的文字回答（纯概念问答时它就是**全部**内容），"
    "**不能为空**，而且必须是 JSON 的**第一个键**。\n"
    "请按 brief → intent → outline → title → problem_type → scenes 的顺序"
    "重新输出**完整的 JSON 对象**，其余内容保持你原来的设计。"
)

# 校验错误的回灌上限。合并报错后一条消息里可能有多处问题，600 字符会把后面的砍掉
# （模型只改看得见的那一条，下一轮又撞上下一条 —— 白烧一次重试）。
# dsl._format_errors() 已把总长压到 1800 字符左右，这里留出余量。
_ERR_LIMIT = 2400

# 解析错误信息的回灌上限。比 _ERR_LIMIT 小是**故意的**：
# parse_json_object() 的报错里前半段是"出错行列 + 提示"（真正有用的部分），
# 后半段是"原文前 600 字符"（和上面回灌给它的那份重复）。留 800 字符刚好保住
# 前半段的提示（含"回复被截断了"那条），把重复的原文让位给 _echo_back。
_JSON_ERR_LIMIT = 800

# 回灌"你自己上一轮写了什么"的长度上限。
#
# ⚠️ 这个值不能小：一份 4~6 分镜的 JSON 轻松超过 3000 字符，而反馈语要求它
# 「重新输出完整的那一份」。原先截到 3000，模型看到的是一份**腰斩的 JSON**，
# 很自然地顺着断口续写，于是再炸一次 —— 反馈语和回灌内容是互相矛盾的。
# 2026-09-17：12000 → 20000。老值是按"单镜 1000+ 字符"的旧示例定的，
# 而示例 1 换成 5 镜后**一份就 1.7 万字符** —— 模型照着新示例那样写，回灌必被截断。
# 20000 = 5 镜一份 + 余量；代价只在重试/多轮时多花约 2k token（HANDOFF §8.55）。
#
# ⚠️ **判断口径是"单行压缩后的长度"**：回灌调的是 `json.dumps(raw)`（无 indent），
# 不是示例文件那种给人看的紧凑排版 —— 后者带换行、缩进、空格，字符数会多出好几成
# （实测同一份数据：压缩 11936 / 紧凑排版 14954）。**别拿磁盘上的文件大小来校这个值**，
# 会把自己吓一跳（以为超限了，其实回灌时只有三分之二）。
_ECHO_LIMIT = 20000


def _echo_back(text):
    """
    把模型上一轮输出回灌给它自己。

    超长时**明说"这是被截断的"**，而不是默默切一刀：默默截断会让模型以为自己
    写完了，然后"只改错的地方"—— 结果输出一个残缺的分镜。
    """
    s = str(text or "")
    if len(s) <= _ECHO_LIMIT:
        return s
    return (s[:_ECHO_LIMIT] +
            f"\n\n...（你上一轮的输出共 {len(s)} 字符，此处只显示前 {_ECHO_LIMIT} 字符。"
            f"请**完整重写**整份 JSON，不要顺着上面的断口续写。）")


def _brief_of(raw):
    """取出 brief；缺失/非字符串/空白一律返回 None。"""
    v = raw.get("brief") if isinstance(raw, dict) else None
    if not isinstance(v, str) or not v.strip():
        return None
    return v.strip()


def _warn_brief_order(raw, verbose=True):
    """
    检查 brief 是不是第一个键。

    只**告警不重试**：键序错了 JSON 本身仍然合法（BriefTap 会一直往后扫，
    照样能找到 brief），损失的只是"1~2 秒见字"这个体验。而重试轮走的是
    非流式，重来一次也换不回流式体验，纯属白花一次调用。
    但必须喊出来 —— 否则"用户要干等十几秒"会被当成正常现象，没人知道该修哪。
    """
    first = next(iter(raw), None) if isinstance(raw, dict) else None
    if first != "brief" and verbose:
        print(f"      [WARN] brief 不是第一个键（实际首个是 {first!r}）——"
              f" 流式期间不会吐字，用户要等整份 JSON 生成完才看到回答")


# ==============================================================================
# 主流程：生成 → 校验 → 失败回灌重试
# ==============================================================================

def generate_storyboard(problem, cfg, registry, max_attempts=4,
                        json_mode=None, extra_rules="", history=None,
                        verbose=True):
    """
    反复调用 LLM 直到产出一份**通过 schema/dsl 校验**的分镜。

    Args:
        problem: 题目文本
        cfg: resolve_config() 的结果
        registry: 旧模板注册表（templates.TEMPLATE_REGISTRY），schema.validate 需要
        max_attempts: 最多调用几次模型（1 次生成 + 若干次带错误重试）
        json_mode: 是否要求接口强制 JSON 输出。None = 按 cfg（llm.local.json /
            环境变量）来，调用方不要写死 —— 详见 effective_json_mode()
        history: 该会话之前的对话（`[{"role","content"}, ...]`，时间正序）。
            **这是多轮的基础**：不给的话模型看到的只有当前这一句，
            于是「生成对应视频」这种追问会被它当成"你没给题目"。
            长度由 trim_history() 按 token 预算裁剪，调用方不需要自己控。
            上一版分镜**跟着 history 走**：记录里带 `meta.storyboard` 的 assistant 消息
            会被还原成"brief + 分镜 JSON"（见 `_history_with_storyboards`），
            于是模型看得到自己上一版具体写了什么 —— 多轮"改这一版"靠的就是这个。
        extra_rules: 附加到 system prompt 的要求

    Returns:
        dict:
            storyboard  已规范化，可直接交给 renderer.render()
            raw         模型原始输出的分镜（未规范化）—— 落盘给用户手改用这个
            attempts    实际调用了几轮模型
            history     每轮的耗时/错误，便于排查是哪一类错误总是改不掉

    Raises:
        LLMError: 次数用尽仍未通过校验，或网络/接口错误
    """
    # 先把"带 meta.storyboard 的记录"还原成"brief + 分镜 JSON"的历史消息，
    # 再按 token 预算裁剪 —— 顺序不能反：裁剪要看的是**真正会发出去的长度**。
    past = trim_history(_history_with_storyboards(history))
    messages = [
        {"role": "system", "content": build_system_prompt(extra_rules)},
        *past,
        {"role": "user", "content": build_user_prompt_multi(problem, past)},
    ]
    attempts_log = []
    last_err = None
    # 策略在**这一层**定好，往下只传具体布尔值（理由见 chat() 的注释）
    jm = effective_json_mode(cfg, json_mode)

    for attempt in range(1, max_attempts + 1):
        t0 = time.time()
        meta = {}
        text = chat(messages, cfg, json_mode=jm, purpose="storyboard", seen=meta)
        dt = time.time() - t0

        # 截断**先于解析**判断：JSON 被砍断时解析层只能说"语法错 / 疑似截断"，
        # 而这里能给出确定结论 + 明确修法（压缩思考），也省掉一次白跑的解析。
        if meta.get("finish_reason") == "length":
            attempts_log.append({"attempt": attempt, "ok": False,
                                 "seconds": round(dt, 1),
                                 "error": "输出被 max_tokens 截断（finish_reason=length）"})
            if verbose:
                print(f"      [LLM {attempt}/{max_attempts}] {dt:.1f}s "
                      f"→ 输出被截断（思考吃光了预算），回灌重试")
            last_err = LLMError("输出被 max_tokens 截断（finish_reason=length）")
            messages.append({"role": "assistant", "content": _echo_back(text)})
            messages.append({"role": "user", "content": _FEEDBACK_TRUNCATED})
            continue

        # 先尝试解析
        try:
            raw = parse_json_object(text)
        except LLMError as e:
            hist = {"attempt": attempt, "ok": False, "seconds": round(dt, 1),
                    "error": f"JSON 解析失败: {e}"}
            attempts_log.append(hist)
            if verbose:
                print(f"      [LLM {attempt}/{max_attempts}] {dt:.1f}s "
                      f"→ JSON 解析失败，回灌错误重试")
            last_err = e
            messages.append({"role": "assistant", "content": _echo_back(text)})
            messages.append({"role": "user",
                             "content": _FEEDBACK_JSON.format(err=str(e)[:_JSON_ERR_LIMIT])})
            continue

        # brief 为空等于这次生成对用户毫无产出（纯问答时更是全部内容），必须重试。
        # 它虽然不在 schema 的硬校验里，但"静默地不给回答"比报错严重得多。
        if _brief_of(raw) is None:
            attempts_log.append({"attempt": attempt, "ok": False,
                                 "seconds": round(dt, 1), "error": "brief 缺失或为空"})
            if verbose:
                print(f"      [LLM {attempt}/{max_attempts}] {dt:.1f}s "
                      f"→ brief 为空，回灌重试")
            last_err = LLMError("brief 缺失或为空")
            messages.append({"role": "assistant",
                             "content": _echo_back(json.dumps(raw, ensure_ascii=False))})
            messages.append({"role": "user", "content": _FEEDBACK_BRIEF})
            continue
        _warn_brief_order(raw, verbose)

        # 再尝试校验（这一层是决策 1 的执行者：不确定的东西在这里被拦下）
        try:
            sb = schema.validate(raw, registry)
        except (schema.SchemaError, dsl.DSLError) as e:
            hist = {"attempt": attempt, "ok": False, "seconds": round(dt, 1),
                    "error": str(e)}
            attempts_log.append(hist)
            if verbose:
                print(f"      [LLM {attempt}/{max_attempts}] {dt:.1f}s "
                      f"→ 校验失败，回灌错误重试：{e}")
            last_err = e
            messages.append({"role": "assistant",
                             "content": _echo_back(json.dumps(raw, ensure_ascii=False))})
            messages.append({"role": "user",
                             "content": _FEEDBACK_SCHEMA.format(err=str(e)[:_ERR_LIMIT])})
            continue

        n_sc = len(sb.get("scenes", []))
        hist = {"attempt": attempt, "ok": True, "seconds": round(dt, 1),
                "scenes": n_sc, "raw": raw}
        attempts_log.append(hist)
        if verbose:
            print(f"      [LLM {attempt}/{max_attempts}] {dt:.1f}s "
                  f"→ 校验通过，{n_sc} 个分镜")
        return {"storyboard": sb, "raw": raw, "attempts": attempt,
                "history": attempts_log}

    raise LLMError(
        f"连续 {max_attempts} 次都没能产出通过校验的分镜。\n"
        f"最后一次的错误：{last_err}\n"
        f"（可以加 --attempts 提高次数，或换个更强的模型；"
        f"history 里保留了每一轮的原始输出）"
    )


def _user_content_with_images(problem, past, images):
    """
    本轮 user 的 content：无图时是**纯字符串**，有图时是"图在前、文在后"的数组。

    单独抽一个函数是为了让"有没有图"这个分支只出现一次 —— 写成像
    `build_user_prompt_multi(...) if not images else [...]` 那样内联的话，
    将来加第二条带图路径（比如 regenerate_scene 也要读图）必然漏改一处。

    ⚠️ 无图必须返回**字符串**：纯文本模型收到数组形态的 content 会直接 400，
       而且这保证了老路径的请求体逐字节不变（见 stream_storyboard 的说明）。
    """
    if not images:
        return build_user_prompt_multi(problem, past)
    from . import vision
    text = vision.user_text_with_image_note(problem, images)
    return vision.to_content(text, images)


def stream_storyboard(problem, cfg, registry, max_attempts=4, json_mode=None,
                      extra_rules="", on_delta=None, on_thinking=None,
                      history=None, verbose=True, images=None):
    """
    generate_storyboard() 的**流式**版本：语义、返回值、重试策略完全一致，
    唯一区别是第一轮走 SSE，把 `brief` 的字符边到边通过 on_delta 回调吐出去。

    为什么只有第一轮流式：重试轮是"带着错误重新生成"，上一轮的错字还挂在屏幕上，
    再吐一遍只会造成文字重复。用户要的是"尽快看到第一版答案"，不是"看三次"。
    （思考流同理：只有第一轮回调，见下面的实现注释。）

    Args:
        on_delta: callable(str)，收到**新增的** brief 文本片段时调用一次。
            它会在工作线程里被调用（阻塞式回调），调用方负责把它转成 SSE 事件。
        on_thinking: callable(str)，收到**新增的**思考片段时调用一次（同线程、阻塞）。
            开了深度思考的模型，正文要等思考结束后才开始出字（实测中转站要 49s），
            这段空白期唯一在动的东西就是思考流 —— 前端"正在思考…"就靠它。
            **不传即丢弃**：推理内容是否该给终端用户看，是业务层的决定。
        history: 该会话之前的对话（同 generate_storyboard 的 history）。
            **多轮追问（"生成对应视频"、'换个讲法'、'第 2 镜讲慢点'）靠的就是它** ——
            里面带着模型历次输出的分镜 JSON（见 `_history_with_storyboards`）。
        images: 已规范化的题目图片（`storyboard/vision.py::normalize_many` 的产物，
            每项含 `data_url`）。**空/None 时行为与以前完全一致** ——
            这是"图片输入"唯一的接入口，占位在 messages 的构造处。

            ⚠️ 为什么参数放在最后（而不是挨着 problem）：这样所有**老调用方**
               一个字都不用改，新调用方按关键字传即可 —— 加在中间会让位置参数
               悄悄错位，而错位是静默的（比如 history 被当成 images）。
        verbose: 与 generate_storyboard 一致，打印每轮耗时/错误

    Returns:
        同 generate_storyboard，额外多一个 "streamed": bool（首轮是否真的走了流式）
    """
    # 先把"带 meta.storyboard 的记录"还原成"brief + 分镜 JSON"的历史消息，
    # 再按 token 预算裁剪 —— 顺序不能反：裁剪要看的是**真正会发出去的长度**。
    past = trim_history(_history_with_storyboards(history))
    # ★ 带图时：文字里补一句"这是题目图、该怎么用"，content 换成数组形态（图在前）。
    #   不带图时 to_content() 返回**纯字符串**，messages 与以前逐字节相同 ——
    #   这也是让 LLM 留档指纹保持稳定的前提（无图请求的缓存键不该因为支持图片而变）。
    messages = [
        {"role": "system", "content": build_system_prompt(extra_rules)},
        *past,
        {"role": "user", "content": _user_content_with_images(problem, past, images)},
    ]
    attempts_log = []
    last_err = None
    use_stream = True
    streamed = False
    # 用户是否已经看到过回答文字。重试轮走的是非流式，所以"首轮一个字都没吐出去"
    # （brief 缺失、被网关重排、或流式降级）时必须在结束前补发一次，
    # 否则用户看到的是"思考完了，然后什么都没有"，且**不报任何错**。
    emitted = False
    # 策略在这一层定好，往下只传具体布尔值（理由见 chat() 的注释）
    jm = effective_json_mode(cfg, json_mode)

    for attempt in range(1, max_attempts + 1):
        t0 = time.time()
        # 收 finish_reason / usage 的出口（流式与非流式共用同一个 dict，
        # 因为下面两条分支都可能跑，而判断截断只看这一次调用的结果）
        meta = {}
        if use_stream:
            pieces = []
            tap = BriefTap()
            # ★ 思考流**每一轮都转发**（原先只有第一轮）。
            #
            # 为什么改（这是一个实测到的问题）：重试轮要跑十几秒到几分钟，
            # 而思考流是这期间**唯一在动的东西**。原先重试轮不转发它，界面就
            # 完全静止 —— 用户看到"思考停在 2 万多字"然后半天没反应，
            # 以为程序卡死了（真实反馈："思考到几万字然后就卡住了，过了半天
            # 大纲突然就有了"）。代价是同一个气泡里会接上第二轮思考，
            # 但那远好过一段几分钟的静默。
            thinking = on_thinking
            try:
                for piece in chat_stream(messages, cfg, json_mode=jm,
                                         purpose="storyboard",
                                         on_reasoning=thinking, seen=meta):
                    pieces.append(piece)
                    # ⚠️ 但**正文只吐第一轮**：重试轮是"带着错误重新生成"，
                    #    brief 会重新写一遍。上一轮的错字还挂在屏幕上，
                    #    再吐一遍就成了同一段话出现两次（用户要的是"尽快看到
                    #    第一版答案"，不是"看三次"）。
                    #    所以"要不要流式"与"要不要把正文推给用户"是两件事，
                    #    原先把它们绑在一起（重试就不流式），代价是连进度信号
                    #    一起丢了。
                    if on_delta and attempt == 1:
                        visible = tap.feed(piece)
                        if visible:
                            emitted = True
                            on_delta(visible)
                text = "".join(pieces)
                streamed = True
            except LLMError as e:
                # 部分厂商/网关不支持 `stream: true`（或提前断流）。
                # 一条路走不通就退回非流式重来这一次 —— 降级只损失"秒级见字"，
                # 不该让整个请求失败。
                #
                # ⚠️ 判据必须是"这一条流上**收没收到过任何增量**"，而不是只看正文 pieces：
                # 思考走单独的字段，模型完全可能吐了两万字思考、正文一个字没出就被上游
                # 掐掉（思考把预算烧穿的典型形态）。那种情况流式明明是好用的，
                # 退回非流式只会**再发一次请求**，让用户又干等几分钟（HANDOFF §8.35）。
                if pieces or meta.get("reasoning_chars"):
                    raise
                if verbose:
                    print(f"      [LLM] 流式不可用（{e}），退回非流式")
                # ⚠️ 这里是**唯一**还应该关掉流式流的地方：连一个字节都没收到，
                #    说明这个网关/模型根本不支持 `stream: true`，再试也是白等。
                #    （与之相对，下面那 4 条"回灌重试"路径**不再**关流式 ——
                #     那里流式明明是好用的，关掉只会让界面失去所有进度信号。）
                use_stream = False
                text = chat(messages, cfg, json_mode=jm, purpose="storyboard",
                            seen=meta)
        else:
            text = chat(messages, cfg, json_mode=jm, purpose="storyboard", seen=meta)
        dt = time.time() - t0

        # 截断**先于解析**判断（理由同 generate_storyboard）
        if meta.get("finish_reason") == "length":
            attempts_log.append({"attempt": attempt, "ok": False,
                                 "seconds": round(dt, 1),
                                 "error": "输出被 max_tokens 截断（finish_reason=length）"})
            if verbose:
                print(f"      [LLM {attempt}/{max_attempts}] {dt:.1f}s "
                      f"→ 输出被截断（思考吃光了预算），回灌重试")
            last_err = LLMError("输出被 max_tokens 截断（finish_reason=length）")
            # ⚠️ 这里**不再**降级成非流式（原先写的是 use_stream = False）。
            #    重试要跑十几秒到几分钟，非流式**没有任何进度事件** ——
            #    界面会完全静止，用户以为卡死了（真实反馈见上面 thinking 那段注释）。
            #    正文不会重复：上面已按 `attempt == 1` 把"推正文"与"要不要流式"解耦。
            messages.append({"role": "assistant", "content": _echo_back(text)})
            messages.append({"role": "user", "content": _FEEDBACK_TRUNCATED})
            continue

        # 先尝试解析
        try:
            raw = parse_json_object(text)
        except LLMError as e:
            attempts_log.append({"attempt": attempt, "ok": False, "seconds": round(dt, 1),
                                 "error": f"JSON 解析失败: {e}"})
            if verbose:
                print(f"      [LLM {attempt}/{max_attempts}] {dt:.1f}s "
                      f"→ JSON 解析失败，回灌错误重试")
            last_err = e
            # 保持流式：重试轮的耗时同样以分钟计，非流式会让界面完全静止（见上面注释）
            messages.append({"role": "assistant", "content": _echo_back(text)})
            messages.append({"role": "user",
                             "content": _FEEDBACK_JSON.format(err=str(e)[:_JSON_ERR_LIMIT])})
            continue

        # brief 为空等于这次生成对用户毫无产出（纯问答时更是全部内容），必须重试。
        if _brief_of(raw) is None:
            attempts_log.append({"attempt": attempt, "ok": False,
                                 "seconds": round(dt, 1), "error": "brief 缺失或为空"})
            if verbose:
                print(f"      [LLM {attempt}/{max_attempts}] {dt:.1f}s "
                      f"→ brief 为空，回灌重试")
            last_err = LLMError("brief 缺失或为空")
            # 保持流式（同上）
            messages.append({"role": "assistant",
                             "content": _echo_back(json.dumps(raw, ensure_ascii=False))})
            messages.append({"role": "user", "content": _FEEDBACK_BRIEF})
            continue
        _warn_brief_order(raw, verbose)

        # 再尝试校验（决策 1 的执行者）
        try:
            sb = schema.validate(raw, registry)
        except (schema.SchemaError, dsl.DSLError) as e:
            attempts_log.append({"attempt": attempt, "ok": False, "seconds": round(dt, 1),
                                 "error": str(e)})
            if verbose:
                print(f"      [LLM {attempt}/{max_attempts}] {dt:.1f}s "
                      f"→ 校验失败，回灌错误重试：{e}")
            last_err = e
            # 保持流式（同上）
            messages.append({"role": "assistant",
                             "content": _echo_back(json.dumps(raw, ensure_ascii=False))})
            messages.append({"role": "user",
                             "content": _FEEDBACK_SCHEMA.format(err=str(e)[:_ERR_LIMIT])})
            continue

        # 兜底补发：整条链路走完，用户一个字都还没看到（brief 不在流里 / 流式降级 /
        # 首轮就失败过）。这里把 brief 整段送出去 —— 时机上晚了，但比"什么都没有"好，
        # 而且这一步不做的话，这种失败**完全不可见**（不报错、日志也正常）。
        if on_delta and not emitted:
            brief = _brief_of(raw)
            if brief:
                emitted = True
                on_delta(brief)

        n_sc = len(sb.get("scenes", []))
        attempts_log.append({"attempt": attempt, "ok": True, "seconds": round(dt, 1),
                             "scenes": n_sc, "raw": raw})
        if verbose:
            print(f"      [LLM {attempt}/{max_attempts}] {dt:.1f}s "
                  f"→ 校验通过，{n_sc} 个分镜"
                  + ("（intent=none，纯文字回答）" if sb.get("intent") == "none" else ""))
        return {"storyboard": sb, "raw": raw, "attempts": attempt,
                "history": attempts_log, "streamed": streamed}

    raise LLMError(
        f"连续 {max_attempts} 次都没能产出通过校验的分镜。\n"
        f"最后一次的错误：{last_err}\n"
        f"（history 里保留了每一轮的原始输出）"
    )


# ==============================================================================
# 单分镜重生成（对应前端 Q16「整分镜替换」）
# ==============================================================================

_SCENE_SYSTEM_EXTRA = (
    "本次调用是**局部修改**：你只需要重新设计**一个分镜**，不要输出整份分镜 JSON，"
    "也不要输出其它分镜。"
)


def _scene_user_prompt(instruction, scene, context, duration_sec):
    ctx = json.dumps(context or {}, ensure_ascii=False)
    # ⚠️ 用统一的紧凑排版，不用 `indent=2`（它会把一个分镜写成上千行，白烧 token）——
    # 和 few-shot 示例保持同一种形状，模型看到的"当前样子"才和示例对得上。
    old = _dump_example_json(scene, width=100)
    target = "" if duration_sec is None else str(duration_sec)
    return (
        f"整份讲解的背景：{ctx}\n\n"
        f"用户对这个分镜的要求：{instruction}\n\n"
        f"这个分镜当前的样子：\n{old}\n\n"
        "请**重新输出这一个分镜的完整对象**（含 id / duration / elements / timeline，"
        "与原来同一种写法），要求：\n"
        "1. 只输出那一个对象本身，不要外层包装、不要 Markdown 围栏、不要解释。\n"
        + (f"2. duration 控制在 {target} 秒左右（±1 秒内可接受）；"
           "timeline 里各动作 run_time 之和要与之一致，"
           "不要出现「动画演完了还在干等」或「时间不够被硬切」。\n" if target else "")
        + "3. 元素 id 沿用原来的命名风格；只能用规格里列出的 kind / do，不要发明新的。\n"
        "4. 用户没提到的部分尽量保持原样，不要顺手改别的画面。"
    )


def regenerate_scene(instruction, scene, registry, cfg=None, context=None,
                     duration_sec=None, max_attempts=3, json_mode=None, verbose=True):
    """
    让模型重新输出**一个完整的分镜对象**，并过一遍同一套校验。

    为什么不做"文本 patch"：分镜内部的 duration 与 timeline 的 run_time 是耦合的
    （见 docs/前端接入-后端改造清单.md §5），只改一处会改出"动画演完干等"的半吊子结果，
    而这种错误**不会报错**——正是本项目最想避免的静默失败。让模型重画整个分镜，
    再用 dsl/schema 校验一次，才能保证改完仍然自洽。

    Args:
        instruction: 用户那句要求（如"第 3 步讲慢一点"）
        scene: 原分镜（规范化后的 dict）
        registry: 旧模板注册表
        cfg: resolve_config() 结果；不给则自行解析
        context: {"title":..., "total":..., "index":...}，给模型一点全局感
        duration_sec: 目标时长（用户期望），不给则沿用原分镜
        max_attempts: 校验失败回灌重试的上限

    Returns:
        **未规范化**的分镜 dict（模型原样输出，id 已对齐原分镜）。

        ⚠️ 这里刻意返回 raw 而不是 schema.validate() 的规范化产物：
        调用方会把它塞回原始分镜（threads/<thread>.json 里存的那一份），
        再由 pipeline._validate() 统一规范化一次。整份分镜必须自始至终保持
        "模型原样输出"这一种形态 —— 混进一个已经规范化过的分镜，
        _validate() 就会对它规范化第二遍，而规范化**不是幂等的**
        （见下面 return 处的详细说明）。

    Raises:
        LLMError: 网络/接口错误，或次数用尽仍未通过校验
    """
    if cfg is None:
        cfg = resolve_config()
    if duration_sec is None:
        duration_sec = scene.get("duration")

    messages = [
        {"role": "system", "content": build_system_prompt(_SCENE_SYSTEM_EXTRA)},
        {"role": "user",
         "content": _scene_user_prompt(instruction, scene, context, duration_sec)},
    ]
    last_err = None
    # 策略在这一层定好，往下只传具体布尔值（理由见 chat() 的注释）
    jm = effective_json_mode(cfg, json_mode)

    for attempt in range(1, max_attempts + 1):
        t0 = time.time()
        # 注意：chat() 的失败（网络/鉴权）是**整条链的失败**，必须直接抛出去，
        # 不能被下面的"回灌重试"吞掉 —— 那样只会把同一个接口错误再问三遍。
        meta = {}
        text = chat(messages, cfg, json_mode=jm, purpose="scene", seen=meta)
        dt = time.time() - t0
        try:
            if meta.get("finish_reason") == "length":
                raise LLMError("输出被 max_tokens 截断（finish_reason=length）")
            obj = parse_json_object(text)
            cand = obj
            # 模型常见三种包装：直接给分镜 / {"scene":{...}} / {"scenes":[{...}]}
            if isinstance(obj.get("scene"), dict):
                cand = obj["scene"]
            elif isinstance(obj.get("scenes"), list) and obj["scenes"]:
                cand = obj["scenes"][0]
            cand = dict(cand)
            cand["id"] = scene.get("id", 1)
            # 借道整份校验：这样 DSL 与旧模板两条路径都能复用同一套闸，
            # 不用为"单个分镜"再写一份校验（重复的校验层迟早会漂移）。
            wrapped = {"title": (context or {}).get("title", "scene"),
                       "intent": "propose", "scenes": [cand]}
            schema.validate(wrapped, registry)
            if verbose:
                print(f"      [LLM 分镜重生成 {attempt}/{max_attempts}] {dt:.1f}s → 通过校验")
            # 校验只用来"借闸"，返回值用**没规范化过的 cand**。
            #
            # 踩过的坑（2026-09-12，replace-scene 报"分镜校验失败"却看不到原因）：
            # 这里原本 return schema.validate(...) 的产物，于是路由把「规范化后的分镜」
            # 写回了「raw 分镜」的槽位。随后 pipeline._validate() 会再规范化一遍，
            # 而规范化不是幂等的 —— 最典型的是 plot.x_range：它的默认值就是 None，
            # 第一遍把缺省补成 "x_range": null，第二遍就按"非法范围"报错：
            #     scene[2].elements[1].x_range: 范围必须是 [min, max] 或 [min, max, step]
            # 症状因此极具误导性：服务端日志明明打了"通过校验"，
            # 前端收到的却是"分镜校验失败"（两个闸对同一份数据给出相反结论）。
            #
            # 结论：整份分镜只能有一种形态 —— raw。_validate() 是唯一的规范化入口。
            return cand
        except (LLMError, schema.SchemaError, dsl.DSLError) as e:
            last_err = e
            if verbose:
                print(f"      [LLM 分镜重生成 {attempt}/{max_attempts}] {dt:.1f}s "
                      f"→ 失败，回灌错误重试：{e}")
            messages.append({"role": "assistant", "content": _echo_back(text)})
            messages.append({"role": "user",
                             "content": _FEEDBACK_SCHEMA.format(err=str(e)[:_ERR_LIMIT])})

    raise LLMError(
        f"这个分镜连续 {max_attempts} 次都没能改对。\n最后一次的错误：{last_err}"
    )


# ==============================================================================
# 把提示词导出一份"给人看"的文档（generate.py --dump-prompt）
# ==============================================================================
#
# 为什么要有这一段：提示词散在三处 —— 本模块手写的规则（build_system_prompt）、
# dsl.describe() 自动生成的规格、四个 few-shot 示例。想知道"模型到底看到什么"只能读
# 代码；而读代码的人一多就会开始**转述**，转述一定会失真（漏一段、把"建议"说成"硬性
# 要求"）。这里把三处按**真实发送顺序**原样摆出来，一个字都不改写、一句都不手抄。

# 导出时没给题目就写这个占位符。**不能留空**：空着的话读的人分不清"这里本来就没有
# 内容"和"导出漏拿了题目"，而"分不清"正是这次要排除的东西。
_DUMP_NO_PROBLEM = "【占位符：导出时没给 --problem；真实调用时，题目原文就在这一行】"


def _md_headings(text):
    """抓出提示词里的 `## ` 小节标题，拼一份目录（不然几千字只能从上往下翻）。"""
    return [ln.strip()[3:].strip()
            for ln in text.splitlines() if ln.strip().startswith("## ")]


def dump_prompt_text(problem="", extra_rules="", history=None, cfg=None,
                     cfg_error=""):
    """
    把「模型实际会收到的东西」拼成一份可读文档（Markdown）返回。

    文档分两个区，读的时候别混：
      【一】第一轮真实发送的      system +（会话历史）+ user —— 发出去的就是这三段
      【二】附：出错打回重做时才追加的 —— 第一轮一个字都没有，但它们同样在影响输出

    内容全部取自本模块的常量与 dsl.describe()，**没有一句是手抄的**：将来改了提示词，
    导出的文档自动跟着变，不需要"记得同步更新文档"这种约定（同 describe() 的思路）。

    Args:
        problem: 题目原文（空则写一个明说的占位符）
        extra_rules: 本次的附加要求（与真实调用传的是同一个参数）
        history: 会话历史；给了就按真实顺序摆出来，没给就说明"真实调用时这里有什么"
        cfg: resolve_config() 的结果；只用来打印"这次用的是哪个模型"，拿不到也不影响导出
        cfg_error: 读配置失败时的原因（照实写进文档，而不是留个空栏）
    """
    sys_text = build_system_prompt(extra_rules)
    # 先把"带 meta.storyboard 的记录"还原成"brief + 分镜 JSON"的历史消息，
    # 再按 token 预算裁剪 —— 顺序不能反：裁剪要看的是**真正会发出去的长度**。
    past = trim_history(_history_with_storyboards(history))
    user_text = build_user_prompt_multi(problem or _DUMP_NO_PROBLEM, past)

    L = []
    add = L.append
    add("# MathStoryboard —— 模型实际收到的提示词")
    add("")
    add("> 本文档由 `python generate.py --dump-prompt` 从代码里**直接导出**，"
        "不是手抄的，也没有删改。")
    _st = prompt_stats(extra_rules)
    add("")
    add(f"> **体积**：system prompt 约 **{_st['tokens']} token**（{_st['chars']} 字符），"
        f"其中四个示例合计 {_st['example_tokens']} token"
        f"（占 {round(100 * _st['example_tokens'] / max(1, _st['tokens']))}%）；"
        f"预算 {_st['budget']}。"
        + ("⚠️ **已超预算** —— 该想想哪个示例可以被取代了。" if _st["over_budget"] else ""))
    add("")
    add("模型每次只收到两样东西：一本每轮都要念的《工作手册》（role=system），"
        "和这一单的要求（role=user）。下面按真实顺序摆出来。")
    add("")

    add("## 这次导出用的设置")
    add("")
    if cfg:
        add(f"- 厂商 / 模型：{cfg.get('provider')} / {cfg.get('model')}")
        add(f"- 模型档案：{cfg.get('profile') or DEFAULT_PROFILE}")
        add(f"- temperature（随机程度，越低越稳）：{cfg.get('temperature')}")
        add(f"- max_tokens（单次回复的长度上限）：{cfg.get('max_tokens')}")
        add(f"- 强制 JSON 输出：{effective_json_mode(cfg, None)}"
            "（true = 让接口保证回复是 JSON）")
    elif cfg_error:
        add("- ⚠️ 读配置失败（只影响这一栏，提示词内容照常导出）："
            + cfg_error.replace("\n", " "))
    else:
        add("- （本次只导出了提示词正文，没有带配置）")
    add("- 本次附加要求（extra_rules）："
        + (extra_rules.strip().replace("\n", " / ") if extra_rules.strip() else "（没有）"))
    add("")

    add("## 体量")
    add("")
    add(f"- 《工作手册》（system）：{len(sys_text)} 字符")
    add(f"- 这一单的要求（user）：{len(user_text)} 字符")
    add(f"- 单次调用的固定开销 ≈ {len(sys_text) + len(user_text)} 字符"
        "（会话历史、出错重试的消息再往上加）")
    add("")

    add("## 《工作手册》包含哪些小节（按出现顺序）")
    add("")
    for h in _md_headings(sys_text):
        add(f"- {h}")
    add("")

    add("---")
    add("")
    add("# 一、第一轮真实发送的内容")
    add("")
    add(f"## [1/3] role = system —— 《工作手册》（{len(sys_text)} 字符）")
    add("")
    add("每一轮都发，内容固定；只有末尾「## 补充要求（本次调用专属）」那一节会随"
        "前端设置变化（就是上面写的 extra_rules）。")
    add("")
    add("```text")
    add(sys_text)
    add("```")
    add("")

    add("## [2/3] 会话历史（夹在手册和这次要求之间）")
    add("")
    if past:
        add(f"本次导出带了 {len(past)} 条历史（按时间正序）：")
        add("")
        for i, h in enumerate(past, 1):
            add(f"- 第 {i} 条：role = {h.get('role')}，"
                f"{len(str(h.get('content')))} 字符")
    else:
        add("本次导出没有带历史。真实调用时（网页端多轮对话）这里会插入这个会话里之前"
            "的问答，位置在 system 之后、这次要求之前，按时间正序。")
        add("")
        add("为什么非带不可：用户第二句常常是「生成对应视频」「第三步再讲慢点」这类"
            "**指代**，不带历史的话，模型会以为「你根本没给题目」然后反问一句废话。")
    add("")

    add(f"## [3/3] role = user —— 这一单的要求（{len(user_text)} 字符）")
    add("")
    add("```text")
    add(user_text)
    add("```")
    add("")

    add("---")
    add("")
    add("# 二、附：只有出错打回重做时，模型才会再多看到的消息")
    add("")
    add("顺利通过校验时，下面这些**一个字都不会发**。列出来是因为它们同样在影响模型"
        "输出 —— 调提示词的时候必须连它们一起看。每次重试都会多出一对消息："
        "「你上一轮写的东西」+「错在哪」。")
    add("")

    add("## 共用的第 1 条：把模型上一轮的输出原样回灌给它自己")
    add("")
    add(f"（role = assistant）上限 {_ECHO_LIMIT} 字符。超过时末尾会自动补一句"
        f"「你的输出共 N 字符，此处只显示前 {_ECHO_LIMIT} 字符，请**完整重写**整份 JSON，"
        "不要顺着上面的断口续写」—— 默默切一刀会让模型以为自己写完了，"
        "然后只改错的地方，交出一份残缺的分镜。")
    add("")

    add("## 第 2 条（四种情况之一）")
    add("")
    add("### 情况 A：回复不是合法 JSON")
    add("")
    add("```text")
    add(_FEEDBACK_JSON.replace("{err}", "（真实调用时填解析出错的位置和提示，最多 "
                                     + str(_JSON_ERR_LIMIT) + " 字符）"))
    add("```")
    add("")
    add("### 情况 B：JSON 合法，但没通过校验")
    add("")
    add("```text")
    add(_FEEDBACK_SCHEMA.replace("{err}", "（真实调用时填全部错误，最多 "
                                       + str(_ERR_LIMIT) + " 字符）"))
    add("```")
    add("")
    add("### 情况 C：回复被长度上限截断（思考把预算吃光了）")
    add("")
    add("```text")
    add(_FEEDBACK_TRUNCATED)
    add("```")
    add("")
    add("### 情况 D：brief 缺失或为空（等于这一轮对用户毫无产出）")
    add("")
    add("```text")
    add(_FEEDBACK_BRIEF)
    add("```")
    add("")
    add("另外两条行为，看文档也要知道：**重试轮不再走流式**（用户已经看过一轮字，"
        "再吐一遍只是噪音）；最多重试到 `max_attempts` 次，仍不通过就整条失败。")
    add("")

    add("---")
    add("")
    add("# 三、附：网页端「重做这一个分镜」用的是另一套")
    add("")
    add("这时 system 还是同一本《工作手册》，只在末尾追一句：")
    add("")
    add("```text")
    add(_SCENE_SYSTEM_EXTRA)
    add("```")
    add("")
    add("user 换成下面这个模板。下面是用**占位内容**渲染出来的样子"
        "（调的是同一个函数，所以形状与真实调用一致）：")
    add("")
    add("```text")
    add(_scene_user_prompt(
        "（用户对这一个分镜的要求，如“第 3 步讲慢一点”）",
        {"id": 2, "duration": 6.0,
         "elements": ["（这个分镜现有的元素，原样 JSON）"],
         "timeline": ["（这个分镜现有的动作，原样 JSON）"]},
        {"title": "（整份讲解的标题）", "total": 3, "index": 2},
        6.0))
    add("```")
    add("")

    add("---")
    add("")
    add("以上就是全部 —— **没有别的隐藏提示词**。想核对真实发出去的原文（含每一轮"
        "重试），看调用留档：" + llm_log.calls_path())
    return "\n".join(L)

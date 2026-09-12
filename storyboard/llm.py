# -*- coding: utf-8 -*-
"""
LLM 接入层：把「一道数学题」变成一份**已经通过校验的**分镜 JSON。

================================================================================
这一层守的是什么
================================================================================
决策 1 的底线：大模型只输出受 Schema 约束的 JSON，**一个字符的 Manim 代码都不写**。
所以本模块做三件事，一件都不多：

    build_prompt()      把 dsl.describe() 的规格 + 红线规则拼成 system prompt
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

import json
import os
import re
import time
import urllib.error
import urllib.request

from . import dsl
from . import llm_log
from . import schema


class LLMError(Exception):
    """LLM 调用/解析/重试耗尽。渲染前抛出，绝不带到渲染阶段。"""
    pass


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
           "headers": {}, "json_mode": True, "profile": ""}
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
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
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


def chat(messages, cfg, json_mode=False, purpose="chat"):
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
    except LLMError as e:
        # 失败也要留档：调 prompt 时"这一版把模型逼到 400 了"同样是关键信息
        llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                       key=key, ok=False, seconds=time.time() - t0, error=str(e),
                       messages=messages, json_mode=json_mode)
        raise

    try:
        content = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"接口返回结构不认识：\n{json.dumps(resp, ensure_ascii=False)[:600]}")
    if isinstance(content, list):
        # 少数厂商把 content 返回成分段数组
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not content or not str(content).strip():
        raise LLMError("模型返回了空内容（可能是 max_tokens 太小，或被内容策略拦了）")

    text = str(content)
    llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                   key=key, ok=True, seconds=time.time() - t0, reply=text,
                   usage=resp.get("usage"), messages=messages, json_mode=json_mode)
    return text


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
                yield "reasoning", str(v)
        piece = delta.get("content")
        if isinstance(piece, list):          # 少数厂商把 content 分片返回
            piece = "".join(p.get("text", "") for p in piece if isinstance(p, dict))
        if piece:
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


def _http_post_json_stream(url, headers, payload, timeout, seen=None):
    """
    流式 POST，逐块 yield `(kind, text)` —— kind 是 "reasoning" 或 "content"。
    错误处理与 _http_post_json 保持一致。

    `seen` 会被透传给 _iter_sse_stream，用来把 usage / finish_reason 带出来
    （流式没有"整个响应体"可回看，只能边读边记）。
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Accept", "text/event-stream")
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
    with resp:
        yield from _iter_sse_stream(resp, seen)


def chat_stream(messages, cfg, json_mode=False, purpose="chat", on_reasoning=None):
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
    except LLMError as e:
        llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                       key=key, ok=False, seconds=time.time() - t0, error=str(e),
                       messages=messages, json_mode=json_mode, streamed=True)
        raise

    llm_log.record(purpose=purpose, model=cfg["model"], base_url=cfg["base_url"],
                   key=key, ok=True, seconds=time.time() - t0, reply="".join(pieces),
                   usage=seen.get("usage"), messages=messages, json_mode=json_mode,
                   streamed=True)


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

# few-shot 示例。用 DSL 写法最短的一个完整故事板 ——
# 目的是让模型"照着格式抄"，而不是靠文字描述去猜 envelope 长什么样。
#
# ⚠️ 键的顺序是**故意**的：brief 必须第一个出现。
# 前端接 SSE 时按流式字符增量解析（api/ 里那层），brief 在最前面意味着
# 1~2 秒就有字可显示；把 title 放前面也一样不行 —— 它太短，解析不出"已经能显示"的信号。
_EXAMPLE = '''{
  "brief": "导数描述的是函数「变化有多快」。对 y = x^2 来说，它在 x 处的导数就是曲线在那一点切线的斜率，代入求导公式得 y' = 2x，所以切线会随着点一起转动。",
  "intent": "propose",
  "outline": [
    { "id": 1, "title": "动点与切线", "durationSec": 9, "summary": "点沿抛物线滑动，切线实时跟随，右上角显示斜率" }
  ],
  "title": "导数就是切线斜率",
  "problem_type": "calculus",
  "scenes": [
    {
      "id": 1,
      "duration": 9.0,
      "elements": [
        { "id": "ax", "kind": "axes", "x_range": [-1.5, 3.5, 1], "y_range": [-1, 8, 1] },
        { "id": "t",  "kind": "tracker", "value": -1.0 },
        { "id": "g",  "kind": "plot", "axes": "ax", "expr": "x**2", "color": "PRIMARY" },
        { "id": "p",  "kind": "dot", "at": { "ref": "ax", "x": "$t", "y": "t**2" } },
        { "id": "k",  "kind": "number", "value": "2*t", "decimals": 2, "color": "ACCENT",
          "place": [{ "corner": "ur", "buff": 0.9 }] },
        { "id": "tan", "kind": "tangent_line", "axes": "ax", "expr": "x**2", "x": "$t",
          "length": 3.0, "color": "RED" }
      ],
      "timeline": [
        { "do": "create", "target": "ax" },
        { "do": "create", "target": "g", "run_time": 1.5 },
        { "do": "show", "target": ["t", "p", "tan", "k"] },
        { "do": "tracker_to", "target": "t", "to": 3.0, "run_time": 6.0, "rate_func": "smooth" },
        { "do": "wait", "time": 0.6 }
      ]
    }
  ]
}'''


def build_system_prompt(extra_rules=""):
    """system prompt = 红线 + 设计规范 + 元素/动作规格（从 dsl.py 自动生成）+ 示例。"""
    parts = [
        "你是一名数学讲解视频的分镜师。你的唯一任务：把用户给的数学题，"
        "设计成一份**分镜 JSON**。",
        "",
        "## 输出格式（最重要）",
        "只输出一个 JSON 对象。不要 Markdown 围栏、不要任何解释文字、不要注释。",
        "**键的顺序必须严格照下面的来，尤其 brief 必须是第一个键，不要调整**"
        "（前端靠这个顺序做到「1~2 秒就有字可显示」）：",
        "{",
        '  "brief": "先用 2~3 句话直接回答这道题（讲人话，不要客套话，不要双引号）",',
        '  "intent": "propose",',
        '  "outline": [ {"id": 1, "title": "分镜标题", "durationSec": 4.5, "summary": "这一镜讲什么"} ],',
        '  "title": "整个讲解的标题",',
        '  "problem_type": "题型",',
        '  "scenes": [ 分镜, ... ]',
        "}",
        "每个分镜 = {\"id\": 整数, \"duration\": 秒数, \"elements\": [...], \"timeline\": [...]}",
        "",
        "## intent 与 outline 的规则",
        '1. 适合用动画讲的题（求极值、证明、几何、函数图像、求面积…）→ intent 取 "propose"，'
        "此时 outline 每个分镜一条，与 scenes 一一对应（durationSec 与分镜的 duration 保持一致）。",
        '2. 纯概念问答（"什么是直角三角形"、"导数是干嘛的"、"这个公式怎么读"）→ intent 取 "none"，'
        "此时 **outline 和 scenes 都给空数组 []**，只把答案写在 brief 里。"
        "不要为了凑动画硬造分镜。",
        "3. brief 是「不看视频也能看懂」的那段话：给出结论和关键一步，"
        "不要写成「下面我用动画演示一下」这类空话。",
        "",
        "## 红线（违反会被校验层直接拒绝，然后你会收到错误信息重做）",
        "1. 你不写任何代码。只能用下面规格里列出的元素类型（kind）和动作（do），"
        "不能发明新的。",
        "2. 元素必须先写在该分镜的 elements 里，才能被 timeline 或别的元素引用。"
        "引用不存在的 id 会报错。",
        "3. 颜色只能用规格里给出的 Manim 常量名（BLUE/RED/GREEN/PRIMARY/ACCENT...）"
        "或 #RRGGBB。拼错颜色名会报错。",
        "4. 数学表达式只能用 x、数字、四则运算、** 幂，以及白名单函数"
        "（sin/cos/tan/exp/log/sqrt/abs/min/max/floor/pi/e...）。不能调用别的东西。",
        "5. 含 tracker 引用（\"$名字\"）的元素是动态元素，"
        "**不会自动上屏，必须用 {\"do\":\"show\",\"target\":\"...\"} 显式显示**。",
        "",
        "## 分镜设计规范",
        "- 通常 3~6 个分镜：封面（这题问什么）→ 图形/推导（主体）→ 结论。",
        "- 每个分镜 duration 4~12 秒；屏幕上元素别超过 8 个，多了会挤。",
        "- 中文文字放 text 的 content；公式放 formula，或写在 text 的 $...$ 里做混排。",
        "- 讲「变化」「联动」（动点滑动、切线跟着转、参数在变）时，"
        "**必须**用 tracker + \"$名字\" 引用，不要写成死的数字——那才是这个 DSL 的价值。",
        "- 结论分镜要给出明确的数学结论（公式或数值），不要只说\"所以得证\"。",
        "",
        "## 元素与动作规格（唯一权威来源，严格按此生成）",
        dsl.describe(),
        "",
        "## 参考示例（注意 envelope 结构、tracker 的用法、show 的上屏时机）",
        _EXAMPLE,
    ]
    if extra_rules:
        parts += ["", "## 补充要求（本次调用专属）", str(extra_rules).strip()]
    return "\n".join(parts)


def build_user_prompt(problem):
    return (
        "请把下面这道题设计成分镜 JSON。\n"
        "要求：图形与推导要真的对得上这道题，不要套模板话术。\n"
        "只输出 JSON 对象本身。\n\n"
        "---- 题目 ----\n" + str(problem).strip()
    )


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
# token 只能估：中文约 1 字 ≈ 1 token、英文约 4 字符 ≈ 1 token。
# 精确计数要引第三方库，而这里只需要"别把上下文撑爆"，粗估足够 ——
# 估高一点（偏保守）比估低好。
_HISTORY_TOKEN_BUDGET = 6000
_HISTORY_MIN_TURNS = 4


def _est_tokens(text):
    """粗估 token 数（中文按字、英文按 4 字符）。宁可高估，别把上下文撑爆。"""
    s = str(text or "")
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk + max(0, len(s) - cjk) // 4


def trim_history(history, budget=_HISTORY_TOKEN_BUDGET, min_turns=_HISTORY_MIN_TURNS):
    """
    history 是 [{"role": "user"|"assistant", "content": str}, ...]（按时间正序）。
    返回裁剪后的列表：**从最旧的开始丢**，并保证切在"用户"这条边界上。

    为什么必须有 min_turns：极端情况下（用户一次贴了篇论文）预算会被一条消息吃光，
    裁完剩个空列表，模型就彻底失忆了 —— 那还不如不管预算、至少留着最近几轮。
    """
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


def build_user_prompt_multi(problem, history=None):
    """
    有历史时的 user prompt。

    为什么要显式写"可能是追问、要先判断指代"：模型看到一句孤零零的
    「生成对应视频」，会照字面理解成"你没给题目"，然后反问一句让人啼笑皆非的话。
    我们实测踩过这个坑（用户先问傅里叶变换，再说"生成对应视频"，模型回：
    「这次只收到了一句指令，没有附带任何题干……」）。历史给全了再加这一句，
    它才会去做指代消解。
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
    parts.append("请把下面这道题设计成分镜 JSON。\n"
                 "要求：图形与推导要真的对得上这道题，不要套模板话术。\n"
                 "只输出 JSON 对象本身。\n\n"
                 f"---- 题目 ----\n{str(problem).strip()}")
    return "\n".join(parts)


_FEEDBACK_JSON = (
    "你上一次的输出不是合法 JSON（解析失败：{err}）。\n"
    "请重新输出**一个完整的 JSON 对象**，不要任何额外文字、不要 Markdown 围栏。"
)

_FEEDBACK_SCHEMA = (
    "你上一次的分镜 JSON 没有通过校验，错误如下：\n\n"
    "  {err}\n\n"
    "请针对这个错误修正后**重新输出完整的 JSON 对象**（不是补丁、不是片段，"
    "是完整的那一份）。其余部分保持你原来的设计，只改错的地方。"
)


# ==============================================================================
# 主流程：生成 → 校验 → 失败回灌重试
# ==============================================================================

def generate_storyboard(problem, cfg, registry, max_attempts=4,
                        json_mode=None, extra_rules="", history=None, verbose=True):
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
    past = trim_history(history)
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
        text = chat(messages, cfg, json_mode=jm, purpose="storyboard")
        dt = time.time() - t0

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
            messages.append({"role": "assistant", "content": str(text)[:3000]})
            messages.append({"role": "user",
                             "content": _FEEDBACK_JSON.format(err=str(e)[:400])})
            continue

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
                             "content": json.dumps(raw, ensure_ascii=False)[:3000]})
            messages.append({"role": "user",
                             "content": _FEEDBACK_SCHEMA.format(err=str(e)[:600])})
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


def stream_storyboard(problem, cfg, registry, max_attempts=4, json_mode=None,
                      extra_rules="", on_delta=None, on_thinking=None,
                      history=None, verbose=True):
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
            **多轮追问（"生成对应视频"、'换个讲法'）靠的就是它。**
        verbose: 与 generate_storyboard 一致，打印每轮耗时/错误

    Returns:
        同 generate_storyboard，额外多一个 "streamed": bool（首轮是否真的走了流式）
    """
    past = trim_history(history)
    messages = [
        {"role": "system", "content": build_system_prompt(extra_rules)},
        *past,
        {"role": "user", "content": build_user_prompt_multi(problem, past)},
    ]
    attempts_log = []
    last_err = None
    use_stream = True
    streamed = False
    # 策略在这一层定好，往下只传具体布尔值（理由见 chat() 的注释）
    jm = effective_json_mode(cfg, json_mode)

    for attempt in range(1, max_attempts + 1):
        t0 = time.time()
        if use_stream:
            pieces = []
            tap = BriefTap()
            # 思考流只在第一轮转发：重试轮是"带着错误重新生成"，
            # 用户已经看过一轮思考了，再刷一遍只是噪音。
            thinking = on_thinking if attempt == 1 else None
            try:
                for piece in chat_stream(messages, cfg, json_mode=jm,
                                         purpose="storyboard",
                                         on_reasoning=thinking):
                    pieces.append(piece)
                    if on_delta:
                        visible = tap.feed(piece)
                        if visible:
                            on_delta(visible)
                text = "".join(pieces)
                streamed = True
            except LLMError as e:
                # 部分厂商/网关不支持 `stream: true`（或提前断流）。
                # 一条路走不通就退回非流式重来这一次 —— 降级只损失"秒级见字"，
                # 不该让整个请求失败。
                if pieces:
                    raise
                if verbose:
                    print(f"      [LLM] 流式不可用（{e}），退回非流式")
                use_stream = False
                text = chat(messages, cfg, json_mode=jm, purpose="storyboard")
        else:
            text = chat(messages, cfg, json_mode=jm, purpose="storyboard")
        dt = time.time() - t0

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
            use_stream = False
            messages.append({"role": "assistant", "content": str(text)[:3000]})
            messages.append({"role": "user",
                             "content": _FEEDBACK_JSON.format(err=str(e)[:400])})
            continue

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
            use_stream = False
            messages.append({"role": "assistant",
                             "content": json.dumps(raw, ensure_ascii=False)[:3000]})
            messages.append({"role": "user",
                             "content": _FEEDBACK_SCHEMA.format(err=str(e)[:600])})
            continue

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
    old = json.dumps(scene, ensure_ascii=False, indent=2)
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
        text = chat(messages, cfg, json_mode=jm, purpose="scene")
        dt = time.time() - t0
        try:
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
            messages.append({"role": "assistant", "content": str(text)[:3000]})
            messages.append({"role": "user",
                             "content": _FEEDBACK_SCHEMA.format(err=str(e)[:600])})

    raise LLMError(
        f"这个分镜连续 {max_attempts} 次都没能改对。\n最后一次的错误：{last_err}"
    )

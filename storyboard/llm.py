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


def resolve_config(provider=None, model=None, base_url=None, api_key=None,
                   temperature=None, max_tokens=None, timeout=None):
    """
    三级覆盖，优先级从低到高：厂商预设 → 本地 llm.local.json → 环境变量 → 显式参数。

    Returns:
        dict: 已经填好的配置
    """
    cfg = {"provider": DEFAULT_PROVIDER, "base_url": "", "model": "",
           "api_key": "", "temperature": 0.2, "max_tokens": 8192, "timeout": 180}

    # 0) 本地配置文件（存在才读；密钥不进 git）
    fp = config_path()
    if os.path.exists(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    if k in cfg and v not in (None, ""):
                        cfg[k] = v
            if cfg.get("provider"):
                cfg["provider"] = str(cfg["provider"]).lower()
        except Exception as e:   # 配置文件写坏了不应该让整条链断掉
            print(f"      [WARN] 读取 {os.path.basename(fp)} 失败，已忽略：{e}")

    # 1) 环境变量
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
    for k, v in (("temperature", temperature), ("max_tokens", max_tokens),
                 ("timeout", timeout)):
        if v is not None:
            cfg[k] = v

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
    """base_url → 完整的 chat/completions 地址。"""
    u = base_url.strip().rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    return u + "/chat/completions"


# ==============================================================================
# HTTP（标准库，无第三方依赖）
# ==============================================================================

def _http_post_json(url, headers, payload, timeout):
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
            400: "多半是 model 名不对（或 max_tokens 超了模型上限）",
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

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise LLMError(f"接口返回的不是 JSON（前 500 字符）：\n{body[:500]}")


def chat(messages, cfg, json_mode=False):
    """调一次 chat/completions，返回模型的文本回复。"""
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
        # 让模型直接吐 JSON。多数 OpenAI 兼容厂商都支持；不支持的话接口会报 400，
        # 所以默认关掉，用 prompt 约束 + 解析兜底。
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
    }
    resp = _http_post_json(endpoint(cfg["base_url"]), headers, payload,
                           cfg.get("timeout", 180))

    try:
        content = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"接口返回结构不认识：\n{json.dumps(resp, ensure_ascii=False)[:600]}")
    if isinstance(content, list):
        # 少数厂商把 content 返回成分段数组
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not content or not str(content).strip():
        raise LLMError("模型返回了空内容（可能是 max_tokens 太小，或被内容策略拦了）")
    return str(content)


# ==============================================================================
# 解析：从模型回复里抠出那个 JSON 对象
# ==============================================================================

def parse_json_object(text):
    """
    容错解析。模型常见的三种"手滑"都要能救回来：
      1. 用 ```json 围栏包起来
      2. 前面写一句"好的，这是分镜："后面才给 JSON
      3. 前后有解释文字
    """
    s = str(text).strip()

    # 1) 去 Markdown 围栏
    m = re.search(r"```(?:json|JSON)?\s*(.+?)```", s, re.S)
    if m:
        s = m.group(1).strip()

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
                        obj = json.loads(s[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break

    raise LLMError(f"模型没有返回合法的 JSON 对象。原文前 600 字符：\n{s[:600]}")


# ==============================================================================
# Prompt
# ==============================================================================

# few-shot 示例。用 DSL 写法最短的一个完整故事板 ——
# 目的是让模型"照着格式抄"，而不是靠文字描述去猜 envelope 长什么样。
_EXAMPLE = '''{
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
        '结构：{"title": "标题", "problem_type": "题型", "scenes": [ 分镜, ... ]}',
        "每个分镜 = {\"id\": 整数, \"duration\": 秒数, \"elements\": [...], \"timeline\": [...]}",
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
                        json_mode=False, extra_rules="", verbose=True):
    """
    反复调用 LLM 直到产出一份**通过 schema/dsl 校验**的分镜。

    Args:
        problem: 题目文本
        cfg: resolve_config() 的结果
        registry: 旧模板注册表（templates.TEMPLATE_REGISTRY），schema.validate 需要
        max_attempts: 最多调用几次模型（1 次生成 + 若干次带错误重试）
        json_mode: 是否要求接口强制 JSON 输出
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
    messages = [
        {"role": "system", "content": build_system_prompt(extra_rules)},
        {"role": "user", "content": build_user_prompt(problem)},
    ]
    history = []
    last_err = None

    for attempt in range(1, max_attempts + 1):
        t0 = time.time()
        text = chat(messages, cfg, json_mode=json_mode)
        dt = time.time() - t0

        # 先尝试解析
        try:
            raw = parse_json_object(text)
        except LLMError as e:
            hist = {"attempt": attempt, "ok": False, "seconds": round(dt, 1),
                    "error": f"JSON 解析失败: {e}"}
            history.append(hist)
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
            history.append(hist)
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
        history.append(hist)
        if verbose:
            print(f"      [LLM {attempt}/{max_attempts}] {dt:.1f}s "
                  f"→ 校验通过，{n_sc} 个分镜")
        return {"storyboard": sb, "raw": raw, "attempts": attempt, "history": history}

    raise LLMError(
        f"连续 {max_attempts} 次都没能产出通过校验的分镜。\n"
        f"最后一次的错误：{last_err}\n"
        f"（可以加 --attempts 提高次数，或换个更强的模型；"
        f"history 里保留了每一轮的原始输出）"
    )

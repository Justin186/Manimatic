# -*- coding: utf-8 -*-
"""
LLM 调用留档 + 回放 —— 省 API 钱的那一层。

================================================================================
为什么要有这个文件
================================================================================
调一次模型十几秒、还要真金白银，而**调 prompt / 调渲染时会反复重跑同一批输入**。
把每次调用原样落盘，同一批输入就能直接回放，不用再烧一遍钱。

本项目原来的落盘只覆盖了"产物"：CLI 的 `output/<name>_storyboard.json`、
API 的 `output/_tasks/threads/<thread>.json`。它们有两个问题：
  1. 存的是**解析后的分镜**，不是模型回复的原文（丢了 reasoning、usage、原始文本）；
  2. 只存**最终通过校验的那一版** —— 校验失败回灌的中间轮全丢，
     而"哪一轮错在哪"恰恰是调 prompt 时最想知道的事。

================================================================================
落什么、落在哪
================================================================================
一行一条 JSON（JSONL），追加写，**不覆盖**：

    output/_llm/calls.jsonl

每条含：时间、用途、轮次、model / provider host、**完整 messages**、**完整回复原文**、
耗时、token usage（厂商给了才有）、错误信息、以及 prompt 指纹（回放靠它匹配）。

⚠️ 密钥绝不入档：只记 provider 和 base_url 的 host，不记 Authorization。
⚠️ 这个目录在 output/ 下，但**不在** `output/_tasks/` 里 —— store.cleanup_stale()
   只清 `_tasks/` 下的任务目录，所以留档不会被 TTL 清掉（见 store.py 的注释）。

================================================================================
回放（MSB_LLM_REPLAY=1）为什么要做成显式开关
================================================================================
回放会**跳过真实网络请求**，直接返回历史留档里的回复。调试能省掉几乎全部费用，
但它同时也意味着"看着在调模型、其实在念旧答案"——这正是本项目最忌讳的静默失效。
所以：默认**关**，且开启后必须在启动横幅里喊出来。
"""

import hashlib
import json
import os
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)              # 仓库根（MathStoryboard/）

# pipeline 是在工作线程里跑的，多个任务可能同时写 —— 加锁保证"一行一条"不被写花
_LOCK = threading.Lock()

# 参与算「请求指纹」的字段：只有这些会改变模型的输出。
# 刻意**不含** `stream`：流式与非流式同输入应当共用同一份留档。
_KEY_FIELDS = ("model", "messages", "temperature", "response_format", "max_tokens")


def calls_path():
    """
    留档文件路径：`MSB_LLM_LOG_DIR` 优先（部署时想把留档挪出 output/ 就设它），
    否则落在 `output/_llm/calls.jsonl`。

    这里直接读环境变量而不是 `api/config.py`：本模块要能被 CLI（generate.py）
    独立使用，不能反过来依赖服务层 —— 依赖方向反了，CLI 就得先起一套服务配置。
    """
    d = (os.environ.get("MSB_LLM_LOG_DIR") or "").strip()
    if not d:
        return os.path.join(_ROOT, "output", "_llm", "calls.jsonl")
    # 写成目录就用它当目录，写成 .jsonl 就当成文件本身 —— 两种直觉都接住
    if d.lower().endswith(".jsonl"):
        return d
    return os.path.join(d, "calls.jsonl")


def cache_key(payload):
    """请求指纹（32 位 hex）。同样的 messages + 同样的采样参数 → 同一个指纹。"""
    material = {k: payload.get(k) for k in _KEY_FIELDS if k in payload}
    blob = json.dumps(material, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _host(url):
    """base_url → host。留档里只留 host，不留完整 URL（免得把带密钥的查询串泄进去）。"""
    try:
        return str(url).split("://", 1)[1].split("/", 1)[0]
    except Exception:                       # noqa: BLE001
        return str(url)


def record(purpose="chat", model="", base_url="", key="", ok=True, seconds=0.0,
           reply="", usage=None, error="", attempt=0, json_mode=False,
           streamed=False, replayed=False, messages=None):
    """
    追加一条调用记录，返回这条记录（便于测试断言）。

    **写失败绝不能影响主流程**：留档是辅助手段，不是必经之路 ——
    磁盘满了、权限不对，都只打一行 WARN 就继续，绝不把一次成功的调用搞成失败。
    """
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": purpose,
        "attempt": int(attempt or 0),
        "ok": bool(ok),
        "replayed": bool(replayed),
        "streamed": bool(streamed),
        "seconds": round(float(seconds or 0.0), 2),
        "json_mode": bool(json_mode),
        "provider_host": _host(base_url),
        "model": model,
        "key": key,
        "messages": messages or [],
        "reply": reply or "",
        "usage": usage or None,
        "error": error or "",
    }
    try:
        path = calls_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:                  # noqa: BLE001
        print(f"      [WARN] LLM 留档写入失败（已忽略）：{e}", flush=True)
    return rec


def _iter_records():
    """逐条读留档；损坏的行直接跳过（留档坏一行不该让回放整体失效）。"""
    path = calls_path()
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def lookup(key, purpose=None):
    """
    按指纹找一条**成功且回复非空**的留档，没有就返回 None。

    取**最后一条**匹配：同一输入重跑过就用最新那份（用户往往是在手工删掉坏记录后重跑）。
    """
    if not key:
        return None
    hit = None
    try:
        for rec in _iter_records():
            if rec.get("key") != key or not rec.get("ok") or not rec.get("reply"):
                continue
            if purpose and rec.get("purpose") != purpose:
                continue
            hit = rec
    except Exception as e:                  # noqa: BLE001
        print(f"      [WARN] LLM 留档读取失败（已忽略）：{e}", flush=True)
        return None
    return hit


def last_reply(purpose=None):
    """最近一条成功留档的回复原文。手工取一份分镜 JSON 出来重跑时很好用。"""
    hit = None
    for rec in _iter_records():
        if rec.get("ok") and rec.get("reply"):
            if purpose and rec.get("purpose") != purpose:
                continue
            hit = rec
    return (hit or {}).get("reply", "")


_COUNTERS = ("calls", "replayed", "failed", "seconds",
             "prompt", "completion", "thinking", "cache_hit")


def summary():
    """
    按用途汇总。`python storyboard/llm.py --llm-stats` 可直接看。

    `thinking` 单独统计，因为它是**开了深度思考的模型最大的成本项**：
    实测一次分镜生成的 completion_tokens 里有一大半是思考
    （4741~12312 个 reasoning token）。混在"输出 token"里看不出来，
    单列出来才能判断"这个思考值不值"。
    """
    rows, total = {}, {k: (0.0 if k == "seconds" else 0) for k in _COUNTERS}
    for rec in _iter_records():
        p = rec.get("purpose", "?")
        r = rows.setdefault(p, {k: (0.0 if k == "seconds" else 0) for k in _COUNTERS})
        r["calls"] += 1
        total["calls"] += 1
        r["seconds"] += rec.get("seconds") or 0
        total["seconds"] += rec.get("seconds") or 0
        if rec.get("replayed"):
            r["replayed"] += 1
            total["replayed"] += 1
        if not rec.get("ok"):
            r["failed"] += 1
            total["failed"] += 1
        u = rec.get("usage") or {}
        details = u.get("completion_tokens_details") or {}
        pairs = (("prompt_tokens", "prompt"), ("completion_tokens", "completion"),
                 ("prompt_cache_hit_tokens", "cache_hit"))
        for k, dst in pairs:
            v = u.get(k)
            if isinstance(v, (int, float)):
                r[dst] += int(v)
                total[dst] += int(v)
        v = details.get("reasoning_tokens")
        if isinstance(v, (int, float)):
            r["thinking"] += int(v)
            total["thinking"] += int(v)
    return rows, total


def _main():
    path = calls_path()
    print(f"留档文件: {path}")
    if not os.path.exists(path):
        print("（还没有记录 —— 跑一次生成就有了）")
        return
    rows, total = summary()
    print(f"{'用途':<12}{'调用':>6}{'回放':>6}{'失败':>6}{'耗时(s)':>10}"
          f"{'输入tok':>10}{'输出tok':>10}{'其中思考tok':>13}{'缓存命中tok':>13}")
    for p in sorted(rows):
        r = rows[p]
        print(f"{p:<12}{r['calls']:>6}{r['replayed']:>6}{r['failed']:>6}"
              f"{r['seconds']:>10.1f}{r['prompt']:>10}{r['completion']:>10}"
              f"{r['thinking']:>13}{r['cache_hit']:>13}")
    print(f"{'合计':<12}{total['calls']:>6}{total['replayed']:>6}{total['failed']:>6}"
          f"{total['seconds']:>10.1f}{total['prompt']:>10}{total['completion']:>10}"
          f"{total['thinking']:>13}{total['cache_hit']:>13}")
    if total["replayed"]:
        print(f"\n其中 {total['replayed']} 次是回放（没有真的调用模型）。")
    if total["thinking"] and total["completion"]:
        share = total["thinking"] / total["completion"] * 100
        print(f"思考 token 占输出 token 的 {share:.0f}% —— 开了深度思考的模型，"
              f"这一项通常是大头（也因此 max_tokens 必须给足，"
              f"否则思考会把预算吃光、正文一个字都出不来）。")
    print("\n注：prompt_cache_hit_tokens 是厂商侧的上下文缓存命中量，"
          "DeepSeek 这类厂商按更低单价计费，它越大越省。")


if __name__ == "__main__":
    _main()

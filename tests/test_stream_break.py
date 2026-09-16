# -*- coding: utf-8 -*-
"""
流式读取被上游掐断（读超时 / 连接断）时的两条保证。

背景（HANDOFF §8.35）：中转站上跑深度思考时，"思考到一定长度就卡住"的真实形态是
`socket` 读超时 —— 而它**既不是 `LLMError`、也不进留档**，于是一路冒到 `/api/chat`
的兜底分支变成一句「服务端异常」，五分钟的等待什么都没留下，也分不清它和"被截断"。

这里把三件事钉死：
  1. `_iter_sse_stream` 要如实累计两路各收到多少字符（否则事后无从判断走到哪一步）；
  2. 断流必须就地包成 `LLMError`，且报错里带上这两个数字；
  3. 断流**不能**被当成"上游不支持流式"而去退回非流式 —— 那会再发一次请求、
     让用户又干等几分钟。
"""
import pytest

from storyboard import llm


# --------------------------------------------------------------- ① 增量计数
def _chunk(*deltas):
    """把若干 delta 组装成一条 OpenAI 兼容的 SSE 行。"""
    import json
    out = []
    for d in deltas:
        out.append(("data: " + json.dumps({"choices": [{"delta": d}]}) + "\n").encode("utf-8"))
    return out


def test_iterates_reasoning_and_content_are_counted():
    seen = {}
    raw = _chunk({"reasoning": "一二三"}, {"content": "abcde"}, {"reasoning_content": "四"})
    got = list(llm._iter_sse_stream(raw, seen))
    assert [k for k, _ in got] == ["reasoning", "content", "reasoning"]
    assert seen["reasoning_chars"] == 4      # "一二三" + "四"
    assert seen["content_chars"] == 5        # "abcde"


# --------------------------------------------------------------- ② 断流的报错
class _StallingStream:
    """先吐几行、再抛读超时 —— 复刻"连接挂着不关"的真实形态。"""

    def __init__(self, lines):
        self._lines = list(lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return self

    def __next__(self):
        if self._lines:
            return self._lines.pop(0)
        raise TimeoutError("The read operation timed out")


def test_read_timeout_becomes_llm_error_with_counts(monkeypatch):
    fake = _StallingStream(_chunk({"reasoning": "想想想"},
                                  {"reasoning": "再想想"},
                                  {"content": "{\"brief\":"}))
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: fake)
    seen = {}
    with pytest.raises(llm.LLMError) as ei:
        list(llm._http_post_json_stream("https://relay/v1/chat/completions",
                                        {"Authorization": "Bearer x"}, {}, 300, seen=seen))
    msg = str(ei.value)
    assert "流式读取中断" in msg
    assert "思考 6 字 / 正文 9 字" in msg        # 两个数字都要出现
    assert "300" in msg                          # 等了多久
    # 正文也收到过（哪怕只有 9 个字符）→ 按"连接/网关断了"给建议，不冤枉成截断
    assert "网关断掉了这条连接" in msg
    assert seen["reasoning_chars"] == 6 and seen["content_chars"] == 9


def test_timeout_with_only_reasoning_points_at_token_budget(monkeypatch):
    """只吐思考、正文一个字都没有 —— 这才是"思考烧穿预算"的形态，文案必须不一样。"""
    fake = _StallingStream(_chunk({"reasoning": "想" * 40}))
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: fake)
    with pytest.raises(llm.LLMError) as ei:
        list(llm._http_post_json_stream("https://relay/v1/chat/completions",
                                        {"Authorization": "Bearer x"}, {}, 300))
    msg = str(ei.value)
    assert "正文一个字都没有" in msg
    assert "预算" in msg and "finish_reason" in msg


# --------------------------------------------------------------- ③ 不退回非流式
CFG = {"provider": "custom", "base_url": "https://relay/v1", "model": "m",
       "api_key": "k", "temperature": 0, "max_tokens": 32768, "timeout": 300,
       "headers": {}, "json_mode": True, "profile": "t"}


def test_stall_does_not_fall_back_to_non_stream(monkeypatch):
    """
    断流时如果因为"正文是空的"就退回非流式，用户会**再等一次完整的几分钟**，
    而且大概率再断一次。判据必须是"这一条流上收没收到过任何增量"。
    """
    called = {}

    def fake_chat_stream(messages, cfg, json_mode=False, purpose="chat",
                         on_reasoning=None, seen=None):
        if on_reasoning:
            on_reasoning("想了很久" * 10)
        if seen is not None:
            seen["reasoning_chars"] = 40
        raise llm.LLMError("流式读取中断：TimeoutError: 300 秒没收到任何新数据")
        yield  # pragma: no cover  —— 让这个函数成为生成器

    def fake_chat(*a, **k):
        called["chat"] = True
        raise AssertionError("断流不该退回非流式重发一次请求")

    monkeypatch.setattr(llm, "chat_stream", fake_chat_stream)
    monkeypatch.setattr(llm, "chat", fake_chat)
    with pytest.raises(llm.LLMError):
        llm.stream_storyboard("一加一为什么等于二", CFG, None, verbose=False)
    assert not called, "断流后退回了非流式（会再白等几分钟）"

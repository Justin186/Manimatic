# -*- coding: utf-8 -*-
"""
会话标题也要能"看图"（2026-09-18）。

用户的反馈："图片好像也没送进会话标题 ai 的输入里"。确实没有，而且是两个口子：

  ① 只拍图不打字时，`/api/chat` 的标题分支要求 `topic` 非空 —— **压根不发起**标题调用；
  ② 就算发起了，`suggest_thread_title` 也只把文字塞进 user content，图片一个字节没带。

而手机拍照搜题恰恰就是"只拍图不打字"这种形态，于是侧栏永远停在兜底名「题目图片」上。
下面把两件事分别钉住：

  · 带图 → 多模态 content（**图在前、文在后**，与主生成同一形态）；
  · 无图 → 仍然是**纯字符串**（老路径逐字节不变：数组形态发给纯文本模型会直接 400）。
"""

import pytest

from storyboard import llm

IMG = {
    "data_url": "data:image/jpeg;base64,AAAA",
    "sha256": "deadbeef",
    "width": 800,
    "height": 600,
}


def _capture(monkeypatch):
    """把 llm.chat 换成"只记录入参"的假实现，这样不用真调模型就能断言请求长什么样。"""
    box = {}

    def fake_chat(messages, cfg, purpose=None):
        box["messages"] = messages
        box["purpose"] = purpose
        return "导数求单调区间"

    monkeypatch.setattr(llm, "chat", fake_chat)
    return box


def test_title_sees_the_image(monkeypatch):
    """只拍图不打字：标题请求必须是多模态的，且图片真的在里面。"""
    box = _capture(monkeypatch)
    t = llm.suggest_thread_title("", {"model": "m"}, images=[IMG])

    assert t == "导数求单调区间"           # 清洗后可直接当标题
    assert box["purpose"] == "title"
    content = box["messages"][1]["content"]
    assert isinstance(content, list), "只发文字的话，模型读不到题图"
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"] == IMG["data_url"]
    assert content[-1]["type"] == "text"
    assert "图片" in content[-1]["text"]    # 得告诉模型"这是题图、先读图"


def test_title_with_text_and_image_keeps_the_text(monkeypatch):
    """用户自己写的那句（如"只讲第二问"）不能因为带了图就丢。"""
    box = _capture(monkeypatch)
    llm.suggest_thread_title("只讲第二问", {"model": "m"}, images=[IMG])
    text = box["messages"][1]["content"][-1]["text"]
    assert "只讲第二问" in text


def test_title_without_image_stays_a_plain_string(monkeypatch):
    """无图路径必须原样：纯字符串 + 截断 500 字。"""
    box = _capture(monkeypatch)
    llm.suggest_thread_title("讲一下导数是什么", {"model": "m"})
    assert box["messages"][1]["content"] == "讲一下导数是什么"
    # 长文本截断（整段粘贴题目源码时不该为起标题付全量输入）
    llm.suggest_thread_title("题" * 900, {"model": "m"})
    assert len(box["messages"][1]["content"]) == 500


def test_title_without_text_or_image_raises():
    """两边都空 → 起不出标题。调用方本来就允许它失败（静默降级）。"""
    with pytest.raises(llm.LLMError):
        llm.suggest_thread_title("", {"model": "m"})
    with pytest.raises(llm.LLMError):
        llm.suggest_thread_title("   ", {"model": "m"}, images=[])

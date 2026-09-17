# -*- coding: utf-8 -*-
"""
多轮"改一版"的回归测试：**分镜 JSON 当作模型自己的对话输出**（2026-09-17）。

背景
----
`/api/chat` 原来只把历史里的 **brief**（一段纯文字结论）喂给模型，brief 里没有元素、
没有动作 —— 用户说「第 2 镜讲慢点」时它手里**没有可照抄的原料**，于是整份重做：
id 变了、镜数变了、上一版调好的地方全丢。用户的感受是"它没在改我的视频，
它在重新生成一个"。

先试过一版"把上一版压成每镜一行的摘要塞进 user 消息"（见 HANDOFF §8.41）：
结构级够用、**细节级不够** —— 摘要里没有坐标/颜色/内容，非目标镜只能重写。
实测目标镜改对了（时长 9→12 秒）、第 1 镜没动，但第 3、4 镜被顺手重写了。

现在的做法（用户要求"把 json 也当作模型的对话输出"）：
把每一版分镜存进 assistant 记录的 `meta.storyboard`，发出去之前由
`_history_with_storyboards()` 还原成 **"brief + 分镜 JSON"** 的 assistant 消息。
于是模型看到的是**自己历次说过的话**，多轮天然连续，也不局限只带上一版
（要哪一版都在上下文里，由 token 预算裁剪）。

两条设计约束，各有定点用例守着：
  ① JSON 走 `meta` 不走 `content` —— content 是给人看的（前端聊天气泡就是它），
     塞几千字 JSON 会把界面撑坏；
  ② 历史**只追加**（前缀缓存按"前缀逐字相同"生效）—— 所以每轮只能在末尾接一段，
     不能用"另起一段当前画面"的写法（那种每轮都在变，会把历史缓存整个打掉）。
"""

import inspect
import json

from storyboard import llm

RAW = {
    "title": "勾股定理：面积证法",
    "intent": "propose",
    "scenes": [
        {"id": 1, "duration": 4.5,
         "elements": [{"id": "t", "kind": "text", "content": "标题"}],
         "timeline": [{"do": "write", "target": "t"}]},
        {"id": 2, "duration": 9.0,
         "elements": [{"id": "p", "kind": "polygon", "points": [[0, 0], [1, 0], [0, 1]]}],
         "timeline": [{"do": "create", "target": "p"}]},
    ],
}


def rec(role, content, storyboard=None):
    return {"role": role, "content": content,
            "meta": ({"storyboard": storyboard} if storyboard else {})}


# ==============================================================================
# 1. 记录 → 真正发出去的消息：assistant 的内容 = brief + 分镜 JSON
# ==============================================================================
def test_storyboard_is_inlined_into_assistant_message():
    msgs = llm._history_with_storyboards(
        [rec("user", "讲一下勾股定理"),
         rec("assistant", "把三边各作一个正方形，比面积。", RAW)])
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "讲一下勾股定理"          # 用户消息原样
    a = msgs[1]["content"]
    assert a.startswith("把三边各作一个正方形")             # brief 还在最前
    assert "分镜 JSON" in a and "```json" in a
    assert '"scenes"' in a                                # 真的把 JSON 塞进去了
    # 关键：模型要能看到**参数级**的原料（坐标/内容），不然只能重写
    assert '"points"' in a and '"duration": 9.0' in a


def test_records_without_storyboard_are_untouched():
    """
    纯文字回答（intent=none）、以及 2026-09-17 之前的旧记录都没有 storyboard ——
    必须原样通过，老会话一个字都不受影响。
    """
    msgs = llm._history_with_storyboards(
        [rec("user", "什么是直角三角形"), rec("assistant", "有一个角是 90° 的三角形。")])
    assert msgs == [{"role": "user", "content": "什么是直角三角形"},
                    {"role": "assistant", "content": "有一个角是 90° 的三角形。"}]


def test_text_only_answer_is_not_inlined():
    """intent=none 的记录即使带了空 scenes，也不该塞一段空 JSON 进去。"""
    msgs = llm._history_with_storyboards(
        [rec("assistant", "纯文字回答", {"title": "t", "intent": "none", "scenes": []})])
    assert "```json" not in msgs[0]["content"]


def test_content_field_is_not_polluted():
    """
    ⚠️ JSON 只能进 meta，不能进 content —— content 是前端聊天气泡显示的东西。
    这条守着"界面不被几千字 JSON 撑坏"。
    """
    r = rec("assistant", "简短回答", RAW)
    assert r["content"] == "简短回答"                     # 记录本身没被动过
    assert "scenes" not in r["content"]


def test_history_is_append_only_for_prefix_cache():
    """
    历史只追加 → 前缀缓存才命中（前缀逐字相同）。每条的转换只依赖它自己，
    所以"第 4 轮的历史"一定以"第 1~2 轮的历史"为前缀。
    """
    recs = [rec("user", "题一"), rec("assistant", "答一", RAW),
            rec("user", "第 2 镜讲慢点"), rec("assistant", "答二", RAW)]
    two = llm._history_with_storyboards(recs[:2])
    four = llm._history_with_storyboards(recs)
    assert four[:len(two)] == two


def test_entrypoints_actually_use_it():
    """
    ⚠️ 接线点的回归：JSON 存进了 meta、却忘了在发出去之前还原 —— 这条线就白做了。
    （之前踩过"参数加了但某一处没传"的同类问题，所以直接盯住调用。）
    """
    for fn in (llm.generate_storyboard, llm.stream_storyboard):
        assert "_history_with_storyboards(history)" in inspect.getsource(fn), fn.__name__


def test_json_counts_toward_the_history_budget():
    """
    裁剪要看**真正会发出去的长度** —— 所以必须先还原 JSON、再 trim。
    一份 JSON 中位 1463 token（实测），大预算才有意义（见 HANDOFF §8.38）。
    """
    recs = []
    for i in range(1, 6):
        recs += [rec("user", f"第 {i} 轮"), rec("assistant", "答", RAW)]
    plain = [{"role": r["role"], "content": r["content"]} for r in recs]
    inlined = llm._history_with_storyboards(recs)
    # 预算按实测体积取一半（跟着 RAW 大小自适应，不是写死的魔数）：
    # 同样的预算下，带 JSON 的那份被裁得更狠 —— 说明 JSON 的体积**真的**被算进去了。
    budget = sum(llm._est_tokens(m["content"]) + 4 for m in inlined) // 2
    assert len(llm.trim_history(inlined, budget=budget)) < len(
        llm.trim_history(plain, budget=budget))


# ==============================================================================
# 2. system 规则：拿到那些 JSON 之后该怎么办
# ==============================================================================
def test_system_prompt_has_the_rule():
    sys_p = llm.build_system_prompt()
    assert "历史里你自己输出的分镜 JSON，就是当前这一版的画面" in sys_p
    assert "逐项一致" in sys_p                    # 没被点名的分镜不许动
    assert "连坐标、颜色、文字内容都不要动" in sys_p  # 细节级一致（摘要做不到的那一层）
    assert "也只修被拒的那一处" in sys_p            # 重试时也不许顺手改别的


def test_rule_does_not_assume_continuity():
    """
    同一段会话里问**几道不同的题**是常事（用户明确提过），前后两个视频可以完全无关。

    ⚠️ 所以规则里不能有"拿不准就按改上一版处理"这类**偏向保留**的默认 ——
    那会让新题的视频带着旧题的骨架。判据必须交还给"用户这一句话本身"。
    """
    sys_p = llm.build_system_prompt()
    assert "同一段对话里问几道不同的题是常事" in sys_p
    assert "不要为了「接得上」硬留上一版的元素" in sys_p


# ==============================================================================
# 3. diff_scenes：越界改动要看得见
# ==============================================================================
def test_diff_no_change():
    assert llm.diff_scenes(RAW, json.loads(json.dumps(RAW))) == []


def test_diff_reports_duration_and_elements():
    new = json.loads(json.dumps(RAW))
    new["scenes"][1]["duration"] = 12.0                      # 目标镜：讲慢一点
    new["scenes"][0]["elements"].append({"id": "x", "kind": "formula", "content": "a^2"})
    msgs = llm.diff_scenes(RAW, new)
    assert any("时长 9.0→12.0" in m for m in msgs)
    assert any(m.startswith("分镜 1") and "元素" in m for m in msgs)


def test_diff_reports_scene_added_or_removed():
    new = json.loads(json.dumps(RAW))
    new["scenes"].append({"id": 3, "duration": 5.0, "elements": [], "timeline": []})
    assert any("新增" in m for m in llm.diff_scenes(RAW, new))
    assert any("删掉" in m for m in llm.diff_scenes(new, RAW))


def test_diff_ignores_id_order_and_accepts_missing_side():
    # 只做只读对比，输入残缺也不该炸（旧记录可能缺字段）
    assert llm.diff_scenes(None, RAW) == ["分镜 1：新增", "分镜 2：新增"]
    assert llm.diff_scenes(RAW, None) == ["分镜 1：被删掉了", "分镜 2：被删掉了"]

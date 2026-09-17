# -*- coding: utf-8 -*-
"""
提示词体积闸（`prompt_stats` / `_warn_if_prompt_too_big`）与历史预算。

背景（HANDOFF §8.59）：实际用的模型是 1M 窗口 —— **撑不爆**，真正要盯的是
"示例越堆越多、把规则淹没"的质量稀释，以及"根本不知道自己多大"这件事本身
（2026-09-17 那 61% 是手工量出来的）。这里钉三件事：

  1. 体积画像算得出来，且四个示例都被算进去（数量级错了 = 示例没进统计）；
  2. 超预算时**喊且只喊一次** —— 每次调用都喊等于刷屏，等于没有信号；
  3. 默认历史预算足够大 —— 一份分镜 JSON 中位 1463 token，10000 只留得住约 6 轮，
     而 1M 窗口下这个限制没有意义（想省 token 的人可以自己设 MSB_HISTORY_TOKENS）。
"""
from __future__ import annotations

import io
import json
import os

from storyboard import llm


def test_prompt_stats_reports_examples():
    st = llm.prompt_stats()
    assert st["chars"] > 10000 and st["tokens"] > 3000
    assert len(st["examples"]) == 4
    # 两个"完整示例"各自都得上千 token
    assert st["examples"]["示例 1 函数"] > 1500, st["examples"]
    assert st["examples"]["示例 2 几何"] > 1000, st["examples"]
    assert st["example_tokens"] == sum(st["examples"].values())
    assert st["over_budget"] is False, "当前不该超预算；超了说明示例该淘汰了"


def test_over_budget_warns_exactly_once(capsys, monkeypatch):
    monkeypatch.setattr(llm, "_PROMPT_BUDGET_TOKENS", 100)
    monkeypatch.setattr(llm, "_prompt_budget_warned", False)
    llm.build_system_prompt()
    llm.build_system_prompt()
    out = capsys.readouterr().out
    assert out.count("system prompt 已约") == 1, out
    assert "预算 100" in out


def test_history_budget_roomy_for_big_window():
    assert llm.history_budget() >= 20000, (
        "1M 窗口下按「省 token」定的 10000 太小（§8.59）；"
        "要省 token 请用 MSB_HISTORY_TOKENS 显式压回去，不要把默认值调小"
    )


# ==============================================================================
# 示例文件的排版：磁盘上和在提示词里必须是**同一种形状**
# ==============================================================================
# 背景（HANDOFF §8.65）：`_build_conv.py` 当年用 `json.dumps(indent=2)` 写文件，
# 同一个东西在磁盘上是 1149 行 / 24749 字符、进 prompt 是 481 行 / 16473 字符 ——
# 两套形状，而且散的那份还把 `_ECHO_LIMIT`（20000）顶穿了。
# 现在写文件走 `llm.save_example()`、读进 prompt 走 `_dump_example_json()`，同一个函数。
def test_dump_is_compact_not_indent_2():
    """
    `_dump_example_json` 必须把小对象压一行 —— 不能退化成 `json.dumps(indent=2)`。

    退化是最容易发生的事（一行 `indent=2` 看着"更标准"），代价却是示例体积翻倍、
    且结构反而看不清。这里用**体积上界**钉住：一个 3×3 表格元素在紧凑排版下
    ≈ 7 行，`indent=2` 是 32 行（实测）。
    """
    el = {"id": "tb", "kind": "table", "rows": [["1", "2", "3"], ["4", "5", "6"]],
          "place": [{"at_point": [-4.3, 0.6]}]}
    compact = llm._dump_example_json(el)
    loose = json.dumps(el, ensure_ascii=False, indent=2)
    assert compact.count("\n") + 1 <= 10, compact
    assert (compact.count("\n") + 1) * 2 <= loose.count("\n") + 1, (
        "紧凑排版没有比 indent=2 小一半，说明压行逻辑失效了：\n" + compact)


def test_examples_on_disk_use_the_same_layout():
    """
    每个 few-shot 示例文件，**磁盘上的样子**必须和"进 prompt 的样子"同一个数量级。

    判据用行数（排版差异直接体现在行数上，字符数受内容影响不好定阈值）。
    为什么阈值是 1.4 倍而不是"完全相等"：示例文件是**手写**的（作者会按内容分组空行、
    一行放几个键，比机器排得更有意图），行数与 dump 有 ~5% 出入很正常（实测最大
    299 vs 314）。但 `indent=2` 的漫散是**翻倍以上**（一个 3×3 表格元素 32 行 vs 7 行），
    所以"没有超 1.4 倍"足以抓住真正的漂移：脚本又用 `indent=2` 写文件、或有人把文件排散。
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    names = ["fewshot_derivative.json", "fewshot_geometry.json",
             "fewshot_pythagoras.json", "fewshot_conv.json"]
    for name in names:
        with io.open(os.path.join(here, "examples", name), encoding="utf-8") as f:
            text = f.read()
        want = llm._dump_example_json(json.loads(text)).count("\n") + 1
        got = text.count("\n") + 1
        assert got <= want * 1.4, (
            f"{name} 磁盘 {got} 行 vs 进提示词 {want} 行 —— 磁盘那份排版太散，"
            f"迟早和提示词里的漂移（用 llm.save_example() 写文件，HANDOFF §8.65）")


def test_examples_fit_echo_limit_when_compressed():
    """
    示例进 prompt 的体积要留得住 —— 而回灌走的是**单行压缩**（`json.dumps(raw)`），
    不是文件那种带缩进的排版。这里按真正的口径量：压缩后必须显著低于 `_ECHO_LIMIT`。

    为什么值得钉：`_ECHO_LIMIT` 一旦被顶穿，多轮改一版时回灌给模型的上一轮 JSON
    会被**腰斩**，模型顺着断口续写 —— 这是 §8.55 那类"反馈语和回灌内容互相矛盾"。
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("fewshot_derivative.json", "fewshot_geometry.json",
                 "fewshot_pythagoras.json", "fewshot_conv.json"):
        with io.open(os.path.join(here, "examples", name), encoding="utf-8") as f:
            data = json.load(f)
        compact_len = len(json.dumps(data, ensure_ascii=False))
        assert compact_len <= llm._ECHO_LIMIT, (
            f"{name} 压缩后 {compact_len} 字符 > _ECHO_LIMIT {llm._ECHO_LIMIT}，"
            f"回灌会被截断（HANDOFF §8.55/§8.65）")


def test_none_example_is_a_genuinely_undrawable_topic():
    """
    `intent=none` 的示例题**必须是真的不该画的题**。

    背景（HANDOFF §8.66）：原来示范的是"什么是直角三角形"，用户当场质疑
    "难道直角三角形不能画出来看吗" —— 拿一个**明明该画**的图形题去示范"不出动画"，
    等于教模型"这种也别画"，还和《第一步》里"几何题只画图形本身"直接对冲。
    **举例本身就是最强的示范，选错比不写还坏。**
    这里钉住形状：intent=none + scenes 空 + problem_type 是 concept。
    """
    text = llm.build_system_prompt()
    assert '"intent": "none"' in text
    assert '"problem_type": "concept"' in text, "none 示例的题应该是概念类"
    # 那个"不画"的示例里不该出现任何图形/坐标系痕迹
    i = text.index("示例 4（")
    seg = text[i:i + 900]
    for bad in ("axes", "polygon", "直角三角形"):
        assert bad not in seg, f"none 示例里出现了 {bad} —— 这题该画出来，不该用 none"


def test_save_example_roundtrips(tmp_path):
    """`save_example()` 写出来的文件必须还能读回、且就是那套紧凑排版。"""
    data = {"a": [1, 2, 3], "b": {"id": "x", "kind": "text", "place": [{"at_point": [0, 0]}]}}
    path = str(tmp_path / "ex.json")
    llm.save_example(path, data)
    with io.open(path, encoding="utf-8") as f:
        text = f.read()
    assert json.loads(text) == data
    # 这个对象小到能整个压成一行 —— 那正是紧凑排版该干的事（`indent=2` 会排出十几行）
    assert text.strip().count("\n") == 0, text
    assert text.endswith("\n"), "文件末尾要有换行"

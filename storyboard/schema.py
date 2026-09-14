"""
分镜 JSON 的 Schema 定义与校验。

这一层是「把不确定性从代码层上移到数据层」的具体落地：
大模型输出的是受约束的数据，不是自由生成的代码。
校验通过后才交给渲染器，越界/非法值在这一步就被拦截。

支持两种分镜写法：
    v1 模板式  {"template": "axes_plot", "params": {...}}
               —— 保留兼容，组合空间有限，画面容易千篇一律
    v2 DSL 式  {"elements": [...], "timeline": [...]}
               —— 推荐。元素自由组合 + 动作自由编排，详见 dsl.py
"""

import traceback

from . import dsl

# 允许的颜色（防止大模型写出不存在的颜色常量）
ALLOWED_COLORS = {
    "BLUE", "YELLOW", "RED", "GREEN", "PURPLE", "ORANGE",
    "WHITE", "GREY", "GREY_B", "PINK", "TEAL", "GOLD",
}

# 各模板允许的参数键
TEMPLATE_PARAMS = {
    "title_card": {"text", "subtitle", "duration"},
    "axes_plot": {"x_range", "y_range", "functions", "caption", "duration"},
    "function_transform": {
        "x_range", "y_range", "from_expr", "to_expr",
        "from_label", "to_label", "caption", "duration",
    },
    "summary_card": {"title", "points", "duration"},
    "step_card": {"steps", "duration", "mode"},
    "vector_wave": {"radius", "duration", "caption"},
    "series_approx": {
        "x_range", "y_range", "terms", "series", "caption", "duration",
    },
    "taylor_approx": {
        "x_range", "y_range", "target_expr", "target_label",
        "terms", "caption", "duration",
    },
    "area_under_curve": {
        "x_range", "y_range", "expr", "a", "b", "caption", "duration",
    },
}


class SchemaError(Exception):
    pass


def _check_range(rng, name, scene_id):
    if not (isinstance(rng, list) and len(rng) >= 2):
        raise SchemaError(f"scene {scene_id}: {name} 必须是 [min, max] 或 [min, max, step]")
    lo, hi = float(rng[0]), float(rng[1])
    if hi <= lo:
        raise SchemaError(f"scene {scene_id}: {name} 的 max 必须大于 min（当前 {lo} -> {hi}）")
    if hi - lo > 200:
        raise SchemaError(f"scene {scene_id}: {name} 跨度过大（{hi - lo}），会导致图像不可读")
    return [lo, hi] + ([float(rng[2])] if len(rng) > 2 else [1.0])


def _check_expr(expr, scene_id, field):
    """校验数学表达式：只允许数字、x、运算符和安全函数名。"""
    import re
    allowed_names = {"sin", "cos", "tan", "exp", "log", "sqrt", "pi", "e", "abs"}
    if not isinstance(expr, str) or not expr.strip():
        raise SchemaError(f"scene {scene_id}: {field} 不能为空")
    # 去掉允许的函数名和变量名后，只允许数字与运算符
    probe = re.sub(r"\b(sin|cos|tan|exp|log|sqrt|pi|e|abs|x)\b", "", expr)
    probe = re.sub(r"[\d\s\+\-\*/\*\*\(\)\.\,]", "", probe)
    if probe:
        raise SchemaError(
            f"scene {scene_id}: {field} 含不允许的字符 {probe!r}（只允许 x / 数字 / 运算符 / 基础函数）"
        )
    return expr


def _check_label(s, scene_id, field):
    """
    校验 LaTeX 标签字段，返回规范化字符串。

    2026-09-09 更新：放开中文限制。
    之前禁中文是因为 MathTex 无 ctex，中文会把原始 LaTeX 命令画上屏幕（静默失败）。
    现在 renderer.formula() 对含中文的公式自动切换 Manim 官方 ctex 模板
    （TexTemplateLibrary.ctex，走 xelatex），中文可以直接嵌在公式里，
    所以这里不再需要拦截 —— 技术问题用技术解决，比一禁了之表现力强。

    注意：ctex 首次编译会触发 MiKTeX 自动装宏包，偏慢（数十秒），之后走缓存。
    """
    if not isinstance(s, str):
        return ""
    # LaTeX 公式本来就比 label 长，给 120 字符的余量
    return s[:120]


def _norm_brief(v):
    """
    `brief`：给用户看的文字回答（2026-09-12 新增，供前端流式显示）。

    刻意宽松 —— 它不参与渲染，缺了只是"少一句话"。500 字符上限是按
    "2~3 句话"给的余量，超了截断而不是报错（报错会让整条生成白跑）。
    """
    if v is None:
        return ""
    if not isinstance(v, str):
        v = str(v)
    return v.strip()[:500]


def _norm_intent(v):
    """
    `intent`：propose = 出大纲并等确认渲染；none = 纯概念问答，只回文字。

    只有明确写 "none" 才当 none，其余（含缺失、写错）一律按 propose ——
    默认值偏"多做一步"，因为漏掉动画比多做一次大纲更容易被用户发现。
    """
    return "none" if str(v).strip().lower() == "none" else "propose"


def _norm_outline(v):
    """
    `outline`：大纲（前端确认页展示的那一份）。每项 = {id,title,durationSec,summary}。

    这里和 scenes 的态度相反：**宽松**。大纲是"给人看的计划"，缺字段就用默认值补齐，
    不因为一个 summary 没写就把整份分镜判死 —— 而 scenes 少一个 id 就会静默画错，
    所以那边必须严格。宽松与严格的分界线就是"错了会不会静默失败"。
    """
    if not isinstance(v, list):
        return []
    out = []
    for i, item in enumerate(v):
        if not isinstance(item, dict):
            continue
        try:
            dur = float(item.get("durationSec", 6.0))
        except (TypeError, ValueError):
            dur = 6.0
        if not (0.5 <= dur <= 60.0):
            dur = 6.0
        try:
            sid = int(item.get("id", i + 1))
        except (TypeError, ValueError):
            sid = i + 1
        out.append({
            "id": sid,
            "title": str(item.get("title", "") or f"分镜 {i + 1}")[:60],
            "durationSec": dur,
            "summary": str(item.get("summary", ""))[:200],
        })
        if len(out) >= 12:
            break
    return out


def validate(storyboard: dict, registry: dict) -> dict:
    """
    校验并规范化分镜 JSON。返回规范化后的 dict。
    任何不合法之处直接抛 SchemaError，带上 scene id 便于定位。
    """
    if not isinstance(storyboard, dict):
        raise SchemaError("分镜必须是 JSON 对象")

    intent = _norm_intent(storyboard.get("intent"))
    scenes = storyboard.get("scenes")
    if not isinstance(scenes, list):
        raise SchemaError(
            '缺少 scenes 数组（纯概念问答也要显式给空数组 []，并把 intent 设为 "none"）'
        )

    # 纯概念问答：模型按 prompt 给 intent=none + 空 scenes，这是**合法结果**，不是错误。
    # 不加这个分支的话，llm.generate_storyboard() 会把一次正确的回答判成校验失败，
    # 然后带着"scenes 不能为空"的错误回灌去逼模型编动画 —— 完全跑偏。
    if not scenes:
        if intent == "none":
            return {
                "title": str(storyboard.get("title", ""))[:60],
                "problem_type": str(storyboard.get("problem_type", "generic"))[:40],
                "brief": _norm_brief(storyboard.get("brief")),
                "intent": "none",
                "outline": _norm_outline(storyboard.get("outline")),
                "scenes": [],
            }
        raise SchemaError(
            "scenes 为空：要出动画请至少给 1 个分镜；"
            '如果是纯概念问答，请把 intent 设为 "none"'
        )

    if len(scenes) > 12:
        raise SchemaError(f"分镜数量过多（{len(scenes)}），上限 12 个")

    out = {
        "title": str(storyboard.get("title", ""))[:60],
        "problem_type": str(storyboard.get("problem_type", "generic"))[:40],
        # ---- brief / intent / outline：对话层需要的三个字段（见 docs/前端接入-后端改造清单.md §3）----
        "brief": _norm_brief(storyboard.get("brief")),
        "intent": intent,
        "outline": _norm_outline(storyboard.get("outline")),
        "scenes": [],
    }

    seen_ids = set()
    for idx, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            raise SchemaError(f"scene[{idx}]: 必须是对象")

        sid = sc.get("id", idx + 1)
        if sid in seen_ids:
            raise SchemaError(f"scene {sid}: id 重复")
        seen_ids.add(sid)

        # ---- v2：元素 + 时间线（DSL 式，推荐）----
        # 判定条件很宽松：只要出现 elements 或 timeline 之一就走 DSL 分支，
        # 剩下的交给 dsl.validate_scene 报出精确错误（比如"有 elements 却没 timeline"）。
        if "elements" in sc or "timeline" in sc:
            try:
                dsl_scene = dsl.validate_scene(sc, sid)
            except dsl.DSLError:
                raise
            except Exception as e:
                # 兜底：校验器自己踩到 KeyError/TypeError（模型给了个没预料到的
                # 字段组合）时，不能让它冒到路由层变成"服务端异常 500"。
                # 转成 SchemaError → llm 的重试机制会把错误回灌给模型再生成一次。
                # 原始堆栈照打，免得真 bug 被这层吃掉。
                traceback.print_exc()
                raise SchemaError(
                    f"scene {sid}: 结构不合规（{type(e).__name__}: {e}），"
                    "请检查 elements/timeline 里各动作的必需参数是否写全"
                ) from e
            dsl_scene["id"] = sid
            dsl_scene["mode"] = "dsl"
            out["scenes"].append(dsl_scene)
            continue

        # ---- v1：模板 + 参数（兼容旧 examples）----
        tpl = sc.get("template")
        if tpl not in registry:
            raise SchemaError(
                f"scene {sid}: 未知模板 {tpl!r}。"
                f"可用模板: {sorted(registry.keys())}"
            )

        params = sc.get("params", {})
        if not isinstance(params, dict):
            raise SchemaError(f"scene {sid}: params 必须是对象")

        # 过滤掉该模板不认识的键（大模型经常多给字段）
        allowed = TEMPLATE_PARAMS.get(tpl, set())
        unknown = set(params.keys()) - allowed
        params = {k: v for k, v in params.items() if k in allowed}

        # 时长范围
        dur = float(params.get("duration", 2.0))
        if not (0.5 <= dur <= 12.0):
            raise SchemaError(f"scene {sid}: duration {dur} 超出范围 [0.5, 12]")
        params["duration"] = dur

        # 坐标范围
        for key in ("x_range", "y_range"):
            if key in params:
                params[key] = _check_range(params[key], key, sid)

        # 函数表达式
        for key in ("from_expr", "to_expr"):
            if key in params:
                params[key] = _check_expr(params[key], sid, key)

        # LaTeX 标签：禁止中文（含 \\text{顶点} 也不行）
        for key in ("from_label", "to_label"):
            if key in params:
                params[key] = _check_label(params[key], sid, key)

        # 函数列表
        if "functions" in params:
            fns = params["functions"]
            if not isinstance(fns, list) or not fns:
                raise SchemaError(f"scene {sid}: functions 不能为空")
            if len(fns) > 4:
                raise SchemaError(f"scene {sid}: 函数最多 4 个，当前 {len(fns)}")
            for f in fns:
                if not isinstance(f, dict) or "expr" not in f:
                    raise SchemaError(f"scene {sid}: 每个函数需要 expr 字段")
                f["expr"] = _check_expr(f["expr"], sid, "functions[].expr")
                color = str(f.get("color", "BLUE")).upper()
                if color not in ALLOWED_COLORS:
                    raise SchemaError(f"scene {sid}: 非法颜色 {color}")
                f["color"] = color
                f["label"] = _check_label(str(f.get("label", "")), sid, "functions[].label")

        # 步骤列表
        if "steps" in params:
            steps = params["steps"]
            if not isinstance(steps, list) or not steps:
                raise SchemaError(f"scene {sid}: steps 不能为空")
            if len(steps) > 6:
                raise SchemaError(f"scene {sid}: 步骤最多 6 步，当前 {len(steps)}")
            for st in steps:
                if not isinstance(st, dict) or "text" not in st:
                    raise SchemaError(f"scene {sid}: 每个步骤需要 text 字段")
                st["text"] = str(st["text"])[:150]
                # formula_latex 不允许中文（和 label 一样的原因）
                st["formula_latex"] = _check_label(
                    str(st.get("formula_latex", "")), sid, "steps[].formula_latex"
                )

        # 旋转向量：半径范围
        if "radius" in params:
            r = float(params["radius"])
            if not (0.4 <= r <= 2.2):
                raise SchemaError(f"scene {sid}: radius {r} 超出范围 [0.4, 2.2]")
            params["radius"] = r

        # terms 字段：按模板分支校验（傅里叶=整数，泰勒=字符串表达式）
        if "terms" in params:
            if tpl == "taylor_approx":
                ts = params["terms"]
                if not isinstance(ts, list) or not ts:
                    raise SchemaError(f"scene {sid}: taylor terms 必须是非空数组")
                if len(ts) > 8:
                    raise SchemaError(f"scene {sid}: taylor terms 最多 8 项")
                params["terms"] = [_check_expr(t, sid, f"terms[{i}]") for i, t in enumerate(ts)]
            else:
                # series_approx 等：整数谐波
                ts = params["terms"]
                if not isinstance(ts, list) or not ts:
                    raise SchemaError(f"scene {sid}: terms 必须是非空数组")
                if len(ts) > 8:
                    raise SchemaError(f"scene {sid}: terms 最多 8 项，当前 {len(ts)}（画面会糊）")
                out_ts = []
                for t in ts:
                    try:
                        iv = int(t)
                    except (TypeError, ValueError):
                        raise SchemaError(f"scene {sid}: terms 元素必须是整数，收到 {t!r}")
                    if iv < 1 or iv > 99:
                        raise SchemaError(f"scene {sid}: 谐波次数 {iv} 超出范围 [1, 99]")
                    out_ts.append(iv)
                params["terms"] = out_ts

        if "series" in params:
            s = str(params["series"]).lower()
            if s not in ("square", "sawtooth"):
                raise SchemaError(f"scene {sid}: series 只支持 square / sawtooth，收到 {s!r}")
            params["series"] = s

        # 泰勒/面积模板：目标函数和各项是合法表达式
        for key in ("target_expr", "expr"):
            if key in params:
                params[key] = _check_expr(params[key], sid, key)

        # 积分上下限校验
        for key in ("a", "b"):
            if key in params:
                v = float(params[key])
                params[key] = v
        if "a" in params and "b" in params:
            if params["b"] <= params["a"]:
                raise SchemaError(f"scene {sid}: b({params['b']}) 必须大于 a({params['a']})")

        if "mode" in params:
            m = str(params["mode"]).lower()
            if m not in ("stack", "page"):
                raise SchemaError(f"scene {sid}: step_card 的 mode 只支持 stack / page，收到 {m!r}")
            params["mode"] = m

        # 文本长度限制（防止溢出画面 —— 这是静默失败的主要来源之一）
        for key in ("text", "subtitle", "caption", "title"):
            if key in params:
                params[key] = str(params[key])[:150]

        if "points" in params:
            pts = params["points"]
            if not isinstance(pts, list) or not pts:
                raise SchemaError(f"scene {sid}: points 不能为空")
            if len(pts) > 5:
                raise SchemaError(f"scene {sid}: 要点最多 5 条，当前 {len(pts)}")
            params["points"] = [str(p)[:150] for p in pts]

        out["scenes"].append({
            "id": sid,
            "template": tpl,
            "params": params,
            "_dropped_keys": sorted(unknown),
        })

    return out

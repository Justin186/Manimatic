# -*- coding: utf-8 -*-
"""
POST /api/chat —— 题目 → （流式文字）→ 大纲。SSE。

================================================================================
关键在**字段顺序**（详见 docs/前端接入-后端改造清单.md §3.2）
================================================================================
system prompt 要求模型按 brief → intent → outline → scenes 输出，于是流式时
brief 的字符最先到（1~2 秒），前端立刻有字可显示 —— 不需要为了"秒级见字"
把一次 LLM 调用拆成两次。这条链路上真正干活的是 llm.BriefTap：
它从流式到达的 JSON 文本里增量抠出 brief 的值（而不是等 JSON 收完再解析）。

顺序：text_delta* → plan → done。intent=none 时 plan 为空数组，这条消息就是
一次普通对话（前端 Q15：纯概念问答不该硬造动画）。
"""

import threading
import time
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from storyboard import llm, templates, vision

from .. import config, events, pipeline, store
from ..auth import scope
from ._common import error_stream, sse_response

router = APIRouter()


class ChatRequest(BaseModel):
    thread_id: str = "demo"
    message: str = ""
    # 前端会把这几样一起发过来（web/src/lib/api.ts 的 ChatRequest），
    # 它们只影响 prompt 措辞，不参与校验，所以这里用宽松的 dict 收。
    profile: dict | None = None
    overrides: dict | None = None
    # 前端自己生成的消息 id（`uid("ma")` / `uid("mu")`，见 web/src/lib/utils.ts）。
    # **可选**：不传时后端自己生成一个，旧客户端的行为一个字都不用改。
    #
    # 为什么非存不可：前端 confirm 时发的 message_id 就是它自己那个 assistant id，
    # 而 store.task_name(thread_id, message_id) 是定位渲染产物的唯一钥匙。
    # 不落盘的话，刷新后谁也不知道该拿哪个 id 去取分镜 —— 后端存着全套产物也读不回来。
    message_id: str = ""
    user_message_id: str = ""
    # ★ 题目图片（base64 或 data URL）。不传/为空 = 与以前完全一致（纯文字）。
    #
    # ⚠️ 有意**不做成 multipart**：这条链路的响应是 SSE，而 multipart 在前端要另写
    #    一套（FormData + 不能带 Content-Type 头）；base64 直接放进已有的 JSON 请求体，
    #    前端改动最小、curl 调试也最直观。代价是体积大 37%，但图已在 vision 层压到 4MB 内。
    #
    # ⚠️ 这个字段**只进不出**：图片不落盘、不进历史（见下面落盘那段的说明）。
    images: list[dict] | None = None


_ROLE_LABEL = {"student": "学生", "parent": "家长", "teacher": "教师"}


def _extra_rules(req: ChatRequest):
    """
    把前端的画像/风格/时长要求翻成给模型的一句提示。

    值得做：这几个字段前端一直在发（设置页里改），但 Mock 后端完全忽略了它们。
    真后端忽略掉的话，"我是家长"这个设置就只是装饰。
    """
    bits = []
    overrides = req.overrides or {}
    style = str(overrides.get("style") or "").strip()
    if style == "concise":
        bits.append("讲解要精简：分镜数量控制在 3~4 个，brief 不超过两句。")
    elif style == "fun":
        bits.append("语气轻松一些，可以用生活化的类比，但结论必须严谨。")
    elif style == "detailed":
        bits.append("讲解要细致：关键步骤都要停一下给读者看，不要跳步。")
    target = overrides.get("targetDurationSec")
    if isinstance(target, (int, float)) and target > 0:
        bits.append(f"整条时长尽量控制在 {int(target)} 秒左右。")

    profile = req.profile or {}
    role = _ROLE_LABEL.get(str(profile.get("role") or ""), "")
    grade = str(profile.get("grade") or "").strip()
    if role or grade:
        bits.append(f"听众是{grade}{role}，用词按这个身份拿捏。")
    return "\n".join(bits)


@router.post("/chat")
async def chat(req: ChatRequest):
    topic = (req.message or "").strip()
    raw_images = [it for it in (req.images or []) if it]
    # 只传了图、没写字是**合法**的一次提问（拍照搜题最常见的形式）
    if not topic and not raw_images:
        return sse_response(error_stream("题目是空的，先在输入框里写一道题", scope="task"))

    # 前端带了 id 就用它的 —— 两边共用同一个 id 是整条恢复链路的前提：
    # confirm 发的 message_id 必须和这里落盘的那个一模一样，否则算不出 task_name。
    mid = (req.message_id or "").strip() or ("m_" + uuid.uuid4().hex[:12])
    # 同一条会话上**不许有两条对话生成并存**：两条流都会 append_thread_message，
    # 而那是"读-改-写" —— 并发落盘会丢消息（与 jobs.py 给渲染加 name 锁同一个理由）。
    # 前端本来也有这条不变量（web/src/lib/streams.ts），但那是靠"断连即取消"兜的；
    # 现在断连不再等于取消（刷新/网络抖动都走断开），所以必须在这里显式重申一次。
    pipeline.cancel_thread(req.thread_id, kind="chat")
    cancel = pipeline.new_cancel()
    # thinking_text 攒推理原文（落盘用），thinking_* 记时间只为算"思考了多久"
    # （前端折叠态显示"思考 N 秒"，刷新后不能拿 createdAt 去减 —— 那会算成几天）
    state = {"streamed": 0, "thinking": 0, "thinking_text": [],
             "thinking_first": None, "thinking_last": None, "thinking_end": None}

    def run(push):
        try:
            # ⚠️ 不传 temperature：它和 json_mode 一样，唯一归属是 resolve_config()
            # （llm.local.json 的档案 → 默认 0.2）。路由层写死 0.2 会让设置页/配置文件里
            # 改的温度**静默失效**，而启动横幅打印的又是配置文件里的值 —— 报的和跑的不是
            # 一个数，与 §8.2 那个 json_mode 的坑同形。
            base_cfg = llm.resolve_config(provider=config.LLM_PROVIDER or None,
                                          model=config.LLM_MODEL or None,
                                          base_url=config.LLM_BASE_URL or None,
                                          profile=config.LLM_PROFILE or None)
        except llm.LLMError as e:
            # 配置类错误（没 key / 未知厂商）要在**发起请求前**报出来，
            # 否则会等成一次看不懂的超时。
            push(*events.error(f"LLM 配置有误：{e}", scope="task"))
            push(*events.done(mid))
            return

        # ---- 图片（可选）：配置 → 能力闸口 → 规范化 ----
        # 三段都**先于**任何落盘：图不合法时这一轮根本不该在会话里留下痕迹。
        vcfg = vision.load_config()
        cfg, note = base_cfg, ""
        images = []
        if raw_images:
            try:
                # 读图可以单独用一个模型档（当前档是纯文本模型时的常见情形）
                cfg, note = vision.resolve_vision_config(
                    base_cfg, vcfg,
                    provider=config.LLM_PROVIDER or None,
                    base_url=config.LLM_BASE_URL or None)
                # 明确不支持就直接说清楚，而不是等接口回一个含糊的 400
                vision.ensure_can_read_images(cfg, vcfg)
                images, warns = vision.normalize_many(raw_images, vcfg)
                # 图片落盘（**文件**，不进 messages JSON）：刷新之后气泡里还能看到
                # 当时那张题图 —— 以前只留 sha256 摘要，用户回看时图已经没了。
                # 用**用户消息**的 id 命名：它本来就挂在用户气泡上，
                # 会话详情恢复时也按同一个 id 去找（见 store.load_message_images）。
                # ⚠️ 落盘失败**不该影响这一轮**：图只是输入，模型已经拿到它了。
                if images:
                    store.save_message_images(req.thread_id,
                                              req.user_message_id or mid, images)
            except vision.VisionConfigError as e:
                push(*events.error(f"LLM 配置有误：{e}", scope="task"))
                push(*events.done(mid))
                return
            except vision.VisionUnsupported as e:
                push(*events.error(str(e), scope="task"))
                push(*events.done(mid))
                return
            except vision.ImageRejected as e:
                # 校验类错误（不是 PNG、太大、张数超限）要**原样**报给用户：
                # 它们是可操作的，混进"生成失败"就浪费了。
                push(*events.error(f"{e}", scope="task"))
                push(*events.done(mid))
                return
            if vcfg.verbose:
                orig = sum(x.get("original_bytes") or 0 for x in images)
                now = sum(x["bytes"] for x in images)
                print(f"      [VI] 图片 {len(images)} 张："
                      f"{orig // 1024}KB → {now // 1024}KB（已降采样）"
                      + (f"；{note}" if note else ""), flush=True)

        # 多轮上下文：把这个会话之前的对话喂给模型。
        # 不带的话，「生成对应视频」这种追问会被模型当成"你没给题目"——
        # 实测它真的会回一句「这次只收到了一句指令，没有附带任何题干」。
        past = store.load_thread_messages(req.thread_id)
        # 上一版分镜：这里取它**只为了生成后对账**（llm.diff_scenes 打 WARN）。
        # ⚠️ 必须在下面 save_thread_storyboard 之前取，否则拿到的是这一轮的新版本。
        # 模型看到的"当前画面"不走这条路 —— 它跟着 past 里每条 assistant 记录的
        # `meta.storyboard` 走（见 llm._history_with_storyboards）：JSON 就是模型自己的输出，
        # 放回对话里最自然，也不用维护第二份"当前版本"的真相。
        prev_raw, _prev_plan = store.load_thread_storyboard(req.thread_id)
        # 把本轮用户消息**先记下来**再调用：模型要看到"最后一条是本次请求"。
        # 顺序反过来的话，历史里永远缺当前这句。
        #
        # meta 里带上前端给的那个 id：恢复会话时要把这条 user 气泡也放回原位。
        # 没有它就只能靠"role + 顺序"猜，连续两条 user 消息会错位。
        #
        # ⚠️ 只传了图没写字时，给历史一个占位符（图**不进历史**，见落盘那段）——
        #    否则这一轮在历史里是空的，下一轮追问"第二问再讲一遍"会失去指代对象。
        record_text = topic or f"（上传了 {len(images)} 张题目图片）"
        store.append_thread_message(
            req.thread_id, "user", record_text,
            meta={"message_id": req.user_message_id} if req.user_message_id else None)

        def on_delta(text):
            # 正文开始出字 = 思考阶段结束（前端也是按这个把"正在思考…"熄火的）。
            # 记下来只为一件事：思考时长要能落盘，刷新后"思考 N 秒"才不会变成
            # 一个从 createdAt 起算的荒唐大数。
            if state["thinking_first"] is not None and state["thinking_end"] is None:
                state["thinking_end"] = time.time()
            state["streamed"] += len(text)
            push(*events.text_delta(text))

        # 思考流：开了深度思考的模型，正文要等思考走完才开始出字
        # （实测中转站是 49s / /api/chat 端到端首字 93s）。这几十秒里唯一在动的
        # 就是思考流，所以把它变成 thinking_delta 发出去，前端才有"正在思考…"可显示。
        #
        # ⚠️ 计两个状态而不是一个：前端靠"有没有思考/正文"来判断该收尾还是该继续等，
        # 混成一个计数会把"思考完了在写正文"误判成"什么都没发生"。
        on_thinking = None
        if config.LLM_SHOW_THINKING:
            def on_thinking(text):
                now = time.time()
                if state["thinking_first"] is None:
                    state["thinking_first"] = now
                state["thinking_last"] = now
                state["thinking"] += len(text)
                # 原文也攒一份：这一轮结束时它会随 assistant 消息一起落盘，
                # 刷新后"思考过程"才回得来（见下面落盘那段）。
                state["thinking_text"].append(text)
                push(*events.thinking_delta(text))

        # ---- 会话标题：只在第一轮起，且与主生成**并行** ----
        #
        # 为什么单独发一次短请求，而不是复用分镜 JSON 里那个 `title`：
        #   · **快**：那份 title 在 JSON 第 4 个键上，要等整份分镜生成完（十几秒~一分钟）
        #     才拿得到；这条独立小请求 1~2 秒就回，侧栏几乎立刻有了名字。
        #   · **不共用**（用户要求）：那个 title 是**视频标题**（要能说清讲了什么，
        #     长一点没关系）；会话标题是侧栏里的标签，必须短。共用一个必然有一边难看。
        #
        # 三条刻意的取舍：
        #   1. **只有第一轮起** —— 会话标题由"最一开始的请求"决定。否则用户在这条会话里
        #      问第二道题时，第一条的名字会跳成新题，历史就对不上了。
        #   2. 用户已改过名（meta.title 非空）就**不动**：手动改名优先级最高。
        #   3. 失败/超时**静默放弃**：标题是锦上添花，退回"用户输入前 20 字"就够了，
        #      绝不能因为它把这一轮拖垮（所以不重试、不报错、不 push error）。
        #
        # ⚠️ 线程体内必须**自己登记取消令牌**：ContextVar 不会自动进入新线程
        #    （HANDOFF §8.70），漏了这一行，"停止"对这条请求就是完全无效且不报错。
        title_thread = None
        is_first_turn = not any(m.get("role") == "assistant" for m in past)
        # ⚠️ `topic or images`：**只拍图不打字**也要起标题。
        #    以前这里判的是 `topic`，于是拍照搜题（最自然的用法）那一轮压根没人调它 ——
        #    侧栏永远停在「题目图片」这个兜底名上，用户分不清哪条是哪条。
        #    起标题那一侧的入参也要跟着带上图片，见 _suggest_title。
        if ((topic or images) and is_first_turn
                and not (store.load_session_meta(req.thread_id) or {}).get("title")):
            def _suggest_title():
                token = llm.set_cancel_token(cancel)
                try:
                    # cfg 在带图时已经是"能读图的那一档"（见上面 resolve_vision_config）
                    t = llm.suggest_thread_title(topic, cfg, images=images)
                except Exception:                                  # noqa: BLE001
                    return                      # 起不出来就退回"用户输入前 20 字"
                finally:
                    llm.reset_cancel_token(token)
                if t:
                    store.save_session_meta(req.thread_id, {"title": t})
                    # 标题一到就推（不必等主生成），侧栏立刻改名
                    push(*events.thread_title(t))

            # ⚠️ 这一层 context 复制**不能省**：它只为了 `save_session_meta`
            #    那一行 —— 那个调用要按**当前账号**拼路径，而 ContextVar 不跨线程。
            #    漏了的话标题会被写进公共区的 `t_xxx.json`：界面上一切正常
            #    （SSE 已经推过去了），但刷新之后标题就没了，且永远差这一份。
            #    这就是 HANDOFF §8.70 那个坑的第二次登场。
            title_thread = threading.Thread(
                target=scope.bind_context(_suggest_title), daemon=True,
                name="msb-title")
            title_thread.start()

        try:
            res = llm.stream_storyboard(
                topic, cfg, templates.TEMPLATE_REGISTRY,
                max_attempts=config.LLM_MAX_ATTEMPTS,
                # json_mode 不传：交给 llm.resolve_config() 决定（llm.local.json >
                # MSB_LLM_JSON_MODE），否则会覆盖用户在配置里写的 false
                extra_rules=_extra_rules(req),
                on_delta=on_delta,
                on_thinking=on_thinking,
                history=past,
                # 空列表 = 老路径（content 仍是纯字符串，请求体逐字节不变）
                images=images,
            )
        except llm.LLMError as e:
            # 带图时，厂商对"拿纯文本模型发图"通常只回一句含糊的
            # `400 messages[0].content must be a string`，用户看不出问题在哪。
            # 这里翻译成人话（含下一步动作）再抛给前端。
            is_vision, human = vision.explain_api_error(str(e), cfg.get("model"))
            push(*events.error(f"生成失败：{human if is_vision else e}", scope="task"))
            push(*events.done(mid))
            return

        sb = res["storyboard"]
        raw = res["raw"]

        # brief 一个字都没流出来（模型没按顺序输出，或流式被降级成非流式）→
        # 补发整段。不补的话用户会看到"有回答、可屏幕上没字"，比慢更难受。
        if not state["streamed"] and sb.get("brief"):
            push(*events.text_delta(sb["brief"]))

        # 有没有"顺手改了别的地方"？只告警不拦截（见 llm.diff_scenes 的说明）：
        # 提示词里已经要求"没被点名的分镜逐项一致"，但提示词管不住的东西要能看见 ——
        # 否则"我调的又乱了"只能靠用户的眼睛发现。
        if prev_raw:
            changed = llm.diff_scenes(prev_raw, raw)
            if changed:
                print("      [WARN] 这一轮改动的分镜：" + "；".join(changed), flush=True)

        # 助手这轮的**自然语言结论**进历史（brief），**不是**分镜 JSON：
        # 分镜几千 token，塞进去会把上下文预算吃光，也会让前缀缓存次次落空。
        # brief 里已经写清了"这一轮讲了什么"，足够支撑下一轮的指代消解。
        # meta 里存 id + plan + intent，三样都是恢复链路要用的：
        #   id     —— 刷新后拿它对上 confirm 时的 task_name（取回分镜与视频）
        #   plan   —— _tasks/threads/<t>.json 只存"当前这一版"，恢复不了每一轮各自的大纲
        #   intent —— 决定恢复后这张卡片是"待确认的大纲"还是"纯文字回答"
        meta = {"message_id": mid,
                "plan": sb.get("outline") or [],
                "intent": sb.get("intent") or "propose",
                # 把这一版分镜本体存进 meta：下一轮它会被还原成"模型自己的输出"
                # 一起发出去（见 llm._history_with_storyboards）——多轮改一版全靠它。
                # ⚠️ 不进 content：那是给人看的（前端聊天框显示的就是它），
                #    塞几千字 JSON 会把界面撑坏。
                "storyboard": raw,
                # 图片**只留摘要**（尺寸/指纹），绝不存 base64：一张图几百 KB，
                # 存进会话会让 /api/threads/<id> 返回几 MB 的 JSON。
                # 摘要留着只为一件事：排查"当时到底传了几张、多大"。
                "images": vision.describe_images(images)}

        # 思考过程也落盘（2026-09-18）。不落的话，"刷新一下"就等于把这段推理
        # 永久删掉 —— 用户的反馈原话是"回来发现深度思考那里不见了"。
        # 现在它随会话一起回来：折叠态显示"思考 N 秒 · 共 M 字"，想看全文点开即可。
        #
        # ⚠️ 两条界，别越：
        #   1. **不进模型上下文**：`llm._history_with_storyboards()` 只读 `content`
        #      与 `meta.storyboard`，这里多一个键不会被喂回去。否则每轮都要为
        #      几万字的推理再付一遍 token。
        #   2. 只有真的收到过思考才写这个键 —— 没开 `MSB_LLM_SHOW_THINKING` 时
        #      它与以前逐字节一致，老会话与"无思考"的会话文件也不会变大。
        thinking_text = "".join(state["thinking_text"])
        if thinking_text:
            meta["thinking"] = thinking_text
            first = state["thinking_first"]
            last = state["thinking_end"] or state["thinking_last"]
            if first and last and last > first:
                # 存**秒数**而不是时间戳：前端只需要显示"N 秒"，
                # 算好再存就不用两边各推一遍（也就不会两边算出不同的数）。
                meta["thinking_sec"] = int(round(last - first))

        store.append_thread_message(req.thread_id, "assistant", sb.get("brief") or "",
                                    meta=meta)

        # 落盘：confirm 只会发 thread_id + message_id，分镜本体必须由后端记住。
        store.save_thread_storyboard(req.thread_id, raw, plan=sb.get("outline") or [])
        # 把"思考了多少字 / 正文多少字"打出来：开了深度思考的模型，
        # 这两个数的比例直接决定首字延迟，排查"怎么这么慢"时一眼就能看到
        # 是模型在思考还是链路卡住了。
        print(f"[api] chat topic={topic[:40]!r} imgs={len(images)} "
              f"-> {len(sb['scenes'])} 分镜 "
              f"intent={sb.get('intent')} attempts={res['attempts']} "
              f"streamed={res.get('streamed')} "
              # 带上"有没有上一版"：排查"改了没生效"时第一眼就看这里
              f"旧版={'有' if prev_raw else '无'} "
              f"思考={state['thinking']}字 正文={state['streamed']}字", flush=True)

        # 等标题那条收尾（通常早已跑完）。**有上限地等**：它万一卡住也不能拖住这一轮，
        # 而且必须排在 plan / done 之前 —— 否则事件顺序就乱了（标题晚于"完成"）。
        if title_thread is not None:
            title_thread.join(timeout=3)

        # title 是模型给的「整个讲解的标题」，前端拿它当**视频标题**
        # （不是会话标题，见上面 _suggest_title 那一段的说明）。
        push(*events.plan(sb.get("outline") or [], sb.get("intent") or "propose",
                          sb.get("title") or ""))
        push(*events.done(mid))

    return sse_response(events.encode_stream(
        pipeline.stream(run, cancel, thread_id=req.thread_id, kind="chat")))

# -*- coding: utf-8 -*-
"""
会话读取 / 修改接口 —— 刷新后恢复工作台，以及侧栏那套菜单。

================================================================================
写接口只在这几个动作上，为什么
================================================================================
会话的**内容**（对话、分镜、渲染产物）不是这里写的：那三个写入方各有各的位置
（`/api/chat`、`/api/render/*`、渲染管线），而且它们写的是**事实**。

这里写的全是**用户改出来的属性**：标题、置顶、分享开关。它们不属于任何渲染产物，
也不能混进 messages / threads —— 那两个文件是覆盖写，塞进去会被下一次写入带掉。
所以单独落在 `_tasks/sessions/<thread>.json`（见 store 的"会话元数据"一节）。

================================================================================
时间戳一律是**毫秒**
================================================================================
后端内部到处是 time.time()（秒），前端全是 Date.now()（毫秒）。
转换只在 `store._now_ms()` 一处做，这里透传 —— 漏一处界面上就是"1970 年前"。
"""

import secrets
import time

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config, pipeline, store

router = APIRouter()


@router.get("/threads")
async def list_threads():
    """
    会话摘要列表（置顶优先，其余按最近更新倒序）。

    顺手把数据保留期限（ttl_days）带出去：超过这个天数没动过的会话，会在服务启动
    时被清理掉。界面得能提前告诉用户，否则"数据凭空消失"只会被当成 bug。
    """
    return {
        "threads": store.list_threads(),
        "ttl_days": config.TASK_TTL_DAYS,
    }


@router.get("/threads/{thread_id}")
async def thread_detail(thread_id: str):
    """
    一个会话的完整消息（含每条助手消息的渲染状态与视频地址）。

    ⚠️ 会话不存在时返回**空消息列表**，而不是 404：
       "新建会话 → 立刻跳进去"这条正常路径上，这个 id 本来就还没落到磁盘，
       报 404 会让新建会话一进页面就弹错。对使用者来说，
       "这个会话还没有消息"和"还没有这个会话"本来就是一回事。

    ★ 归属校验（数据隔离）：id 是**客户端给的**，不加判断的话 A 把 B 的
      thread_id 填进 URL 就能读到别人的对话。不属于自己的返回空 ——
      与"不存在"同样处理，不给"这个 id 存在但不是你的"这种信息。

    ★ `running`：这条会话此刻有没有任务在跑（**进程内**状态，见 pipeline.is_running）。
     它存在的理由：客户端断开**不再等于取消**（刷新/网络抖动的任务会继续跑完并落盘），
     于是刷新后的界面只看到"一句提问"，用户会以为这一轮死了 —— 而这个字段让界面
     能说"还在后台生成"，并给出停止按钮。
    """
    if not store.thread_belongs_to_me(thread_id):
       return {"thread_id": thread_id, "messages": [], "running": False}
    return {
       "thread_id": thread_id,
       "messages": store.load_thread_detail(thread_id),
       "running": pipeline.is_running(thread_id),
    }



@router.get("/threads/{thread_id}/images/{message_id}/{name}")
async def thread_image(thread_id: str, message_id: str, name: str):
    """
    取一张题目图片（消息气泡里的缩略图）。

    ⚠️ 为什么不走 `/media` 那个静态目录：那里是**公开**的（成片与分段要能给分享页看），
       而题目照片是用户自己的东西 —— 可能是他家的书桌、孩子的作业本。
       所以它必须跟在**登录身份**后面，而不是"URL 难猜就安全"。

    ⚠️ `name` 必须严格校验（白名单正则，见 `store.message_image_path`）：
       它是从 URL 直接拼进文件路径的，不校验就是一条目录穿越口子
       （`../../messages/t_x.json` 能把整份会话记录读走）。

    ★ 归属校验：与其它读接口同一套 —— 不属于自己的按"没有"处理（404），
      不泄露"这个 id 存在但不是你的"。
    """
    if not store.thread_belongs_to_me(thread_id):
        return Response(status_code=404)
    p = store.message_image_path(thread_id, message_id, name)
    if not p:
        return Response(status_code=404)
    # 缓存 10 分钟：URL 里没有内容指纹（`<序号>.jpg`），而"重新生成"会复用同一个
    # 用户消息 id —— 万一那一轮又传了别的图，同一个地址的内容会变。
    # 所以不敢写 `immutable`，也不想每次打开会话都重下一遍 200KB，取个中间值。
    return FileResponse(p, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=600"})


class PatchThreadRequest(BaseModel):
    """只传要改的字段；没传的（None）一律不动。"""

    title: str | None = None
    pinned: bool | None = None


@router.patch("/threads/{thread_id}")
async def patch_thread(thread_id: str, req: PatchThreadRequest):
    """
    改标题 / 置顶。

    ⚠️ 标题在这里截断，而不是指望前端自觉：存一个几万字的标题进去，侧栏每次渲染
       都要吃它，而界面上根本看不出是"标题太长"，只会觉得"列表卡了"。
    ⚠️ 会话不存在就直接拒绝，否则会凭空造出一个只有元数据的"幽灵会话" ——
       它不会出现在列表里（列表以 messages/ 为准），却永远留在磁盘上。

    ★ 归属校验：这条是**写**接口，不校验就等于"只要知道别人的 thread_id
      就能改他的标题/置顶"。返回话术统一用"没有这个会话"，不泄露存在性。
    """
    if not store.thread_belongs_to_me(thread_id):
        return {"ok": False, "error": "没有这个会话"}

    patch = {}
    if req.title is not None:
        t = req.title.strip()
        if not t:
            return {"ok": False, "error": "标题不能为空"}
        patch["title"] = t[:60]
    if req.pinned is not None:
        patch["pinned"] = bool(req.pinned)
    if not patch:
        return {"ok": False, "error": "没有要改的字段"}
    if not store.thread_exists(thread_id):
        return {"ok": False, "error": "没有这个会话"}

    meta = store.save_session_meta(thread_id, patch)
    return {"ok": True, "title": str(meta.get("title") or ""),
            "pinned": bool(meta.get("pinned"))}


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """
    删掉一条会话，**连同它的全部渲染产物**（成片、分段、增量缓存一起清）。

    ⚠️ 不可撤销，而且动的是磁盘上的文件：
       - 前端必须先让用户确认。这一层拦不住任何东西，它只能保证"删的确实是这个 id"；
       - `store.delete_thread` 用**整段前缀**（`<id>__`）识别 task 目录，绝不模糊匹配
         —— 否则 `t_4rf2aue` 会误伤 `t_4rf2aue2` 的产物。

    ★ 归属校验：这是**破坏性**接口，不校验等于"知道 id 就能删别人的视频"。
    """
    if not store.thread_belongs_to_me(thread_id):
        return {"ok": False, "error": "没有这个会话"}
    if not store.thread_exists(thread_id) and not store.load_session_meta(thread_id):
        return {"ok": False, "error": "没有这个会话"}
    removed = store.delete_thread(thread_id)
    return {"ok": True, "removed": len(removed)}


class ShareRequest(BaseModel):
    on: bool = True


@router.post("/threads/{thread_id}/share")
async def share_thread(thread_id: str, req: ShareRequest):
    """
    打开 / 关闭分享。打开时生成一个随机 token 并返回。

    ⚠️ 只返回 slug，**不返回完整 URL**：后端知道自己的地址（MSB_PUBLIC_BASE_URL 是
       media 的前缀），但不知道前端跑在哪个端口。拼出一个打不开的链接，是那种
       很难被当成 bug 看出来的错。前端有 location.origin，它来拼最准。

    ★ 归属校验：分享是**把内容公开出去**的动作，不校验等于"能把别人的视频
      一键发到公网"。
    """
    if not store.thread_belongs_to_me(thread_id):
        return {"ok": False, "error": "没有这个会话"}
    if not store.thread_exists(thread_id):
        return {"ok": False, "error": "没有这个会话"}

    if not req.on:
        store.save_session_meta(thread_id, {"share": None})
        return {"ok": True, "slug": ""}

    slug = str(store.load_session_meta(thread_id).get("share") or "").strip()
    if not slug:
        # 分享链接是公开的，token 得不可猜。11 个字符的 urlsafe token 够了 ——
        # 它防的是"随手试出别人的分享"，不是加密。
        slug = secrets.token_urlsafe(8)
        store.save_session_meta(thread_id, {"share": slug})
    return {"ok": True, "slug": slug}


@router.get("/share/{slug}")
async def get_share(slug: str):
    """
    公开读取一份分享（分享页用）。**不需要、也不能带鉴权** —— 拿到链接的人都能看。

    ⚠️ 只回标题、成片和分段清单，**不回对话文字**。
       会话里可能有用户的问题、追问、甚至贴进去的题目原文；分享的语义是
       "这段视频给你看"，不是"把我的对话记录也给你看"。
    """
    tid = store.find_share_slug(slug)
    if not tid:
        return {"ok": False, "error": "分享不存在，或已被取消"}
    return {"ok": True, **store.build_share_payload(tid)}


# ==============================================================================
# 画廊
# ==============================================================================
#
# 与"分享"的关系：**画廊是分享的超集**，不另建一套取数逻辑。
#
#   分享 = 一个不可猜的 token + 一个公开读接口（只给你知道链接的人看）
#   画廊 = 一个"我主动发布"的标记 + 一个公开列表接口（给所有人看）
#
# 所以这里只是在同一份数据（`_tasks/sessions/<t>.json` + 渲染产物）上，
# 加了一个键和一个列表查询 —— 成片、分段、标题全部复用
# `store.build_share_payload`。复制一份"从 task 目录推导视频"的代码，
# 等于承认项目里有两个真相来源，而它们迟早在某个分支上分叉。
#
# ⚠️ 权限是**刻意不对称**的：
#    · 发布 / 撤下（POST）走默认闸门 + 归属校验 —— 必须证明"这条会话是我的"；
#    · 浏览（GET）是公开的（见 auth/guard.py 的 PUBLIC_PREFIXES）。
#    浏览是社区行为，而"发布是作者主动做的动作"这一前提，
#    让公开展示不构成隐私问题（对话文字始终不在返回里）。


class GalleryPublishRequest(BaseModel):
    on: bool = True


@router.post("/threads/{thread_id}/gallery")
async def publish_to_gallery(thread_id: str, req: GalleryPublishRequest):
    """
    发布到画廊 / 从画廊撤下。

    ⚠️ **只写一个时间戳**（发布时刻），不复制成片地址、不存标题快照。
       画廊展示的永远是"这条会话**当前**最新的一版成片" —— 与分享语义一致：
       作者重渲出更好的一版，画廊自动更新，不需要重新发布。
       代价是保留不了旧版本，而这一点与分享页的既有行为完全相同，
       不引入新的心智负担。

    ⚠️ 值存**时间戳**而不是 `true`：列表要按发布时间倒序。
       只存布尔的话排序就只能退回文件 mtime，那个时间会被"改个标题"
       这类无关动作刷新 —— 作品会莫名其妙跳到最前面。

    ⚠️ 发布前必须 `thread_exists`：sessions/ 是**覆盖写**的元数据文件，
       给一个不存在的会话写进去，会造出一个永远赖在磁盘上、
       却不出现在任何列表里的"幽灵发布"。

    ★ 归属校验：发布是"把内容摆到公开页面"的动作，不校验等于
      "知道别人的 thread_id 就能把他的视频挂上画廊"。
    """
    if not store.thread_belongs_to_me(thread_id):
        return {"ok": False, "error": "没有这个会话"}
    if not store.thread_exists(thread_id):
        return {"ok": False, "error": "没有这个会话"}

    if not req.on:
        store.save_session_meta(thread_id, {"gallery": None})
        return {"ok": True, "published": False}

    # ⚠️ 没有成片的会话不能发布：画廊卡片就是一张封面，
    #    发一个点开来是空白的条目，对浏览者比"看不到"更糟。
    payload = store.build_share_payload(thread_id)
    if not (payload.get("final") or {}).get("url"):
        return {"ok": False, "error": "这条会话还没有渲染出成片，先渲完再发布"}

    at = store.save_session_meta(thread_id, {"gallery": time.time()})
    return {"ok": True, "published": True, "publishedAt": store._now_ms(at.get("gallery"))}


def _authors():
    """
    `uid → 显示名` 映射，**每请求建一次**。

    ⚠️ 绝不逐条 `get_user_by_id`：画廊一屏可能有几十条，那就是几十次
       sqlite 查询（N+1），而且是完全可以用一次 `list_users()` 消掉的。
       拿不到账号库（比如压根没启用鉴权）时回空 dict —— 作者显示成空，
       UI 自己兜底，不该让整个画廊因此 500。
    """
    from ..auth import store as auth_store

    try:
        users = auth_store.list_users()
    except Exception:                                              # noqa: BLE001
        return {}
    out = {}
    for u in users:
        # 没填名字就退回邮箱 @ 前那段 —— 与前端 displayName() 同一套兜底规则，
        # 两边算出来必须一样，否则同一个作者在两处显示成两个名字。
        name = str(u.get("name") or "").strip() or str(u.get("email") or "").split("@")[0]
        out[int(u["id"])] = name
    return out


@router.get("/gallery")
async def list_gallery(scope: str = "all", limit: int = 200):
    """
    画廊列表（**公开**，见 auth/guard.py 的 PUBLIC_PREFIXES）。

    `scope=all` 看全部；`scope=mine` 只看自己发布的。
    ⚠️ `mine` 是**按登录身份过滤**，不是"前端自己筛" —— 前端拿不到别人的
       归属信息，而且一个把别人的作品标成"我的"的接口本身就是错的。

    只回封面所需的轻量字段（见 store.build_gallery_item）：
    网格里 50 张卡不该拖 50 份分镜清单。
    """
    from ..auth import scope as identity

    me = identity.user_id()
    want_mine = str(scope or "").strip().lower() == "mine"

    authors = _authors()
    items = []
    skipped = 0
    for stem, at in store.list_gallery(limit=limit):
        owner = identity.uid_from_namespaced(stem)
        if want_mine and owner != me:
            continue
        try:
            item = store.build_gallery_item(stem, at)
        except Exception as e:                                     # noqa: BLE001
            # 单条坏数据不该让整页 500。跳过并计数，让前端能说
            # "有 N 条作品读取失败" —— 而不是白屏，也不是假装它不存在。
            print(f"[gallery] 跳过 {stem}：{e}", flush=True)
            skipped += 1
            continue
        item["mine"] = bool(me is not None and owner == me)
        item["author"] = authors.get(owner, "") if owner is not None else ""
        items.append(item)

    return {"ok": True, "items": items, "skipped": skipped, "total": len(items)}


@router.get("/gallery/{thread_id}")
async def gallery_item(thread_id: str):
    """
    单条作品的**全量**数据（含分段），供卡片被点开时就地播放。

    ⚠️ 这是公开路径，任何人不带登录就能取。所以：
      · 只回标题 + 成片 + 分段，**不回对话文字**（与分享页同一条规矩）；
      · 不校验归属 —— 画廊本来就是公开的。但**必须是已发布的**：
        否则这就变成了"知道 thread_id 就能拿到任意会话的成片"，
        比分享还宽松（分享至少要求一个不可猜的 token）。

    ⚠️ `publishedAt` 与发布状态**必须从 `_payload` 拿**（它内部已经把身份切成
       归属者），不能再自己 `store.load_session_meta(thread_id)` —— 那样又是
       按当前身份拼路径：以 u3 打开 u5 的作品时读不到那份 sessions 文件，
       于是被判成"不在画廊里"，表现是**别人的作品点开就报错**。
    """
    payload = store.build_share_payload(thread_id)
    if not payload.get("publishedAt"):
        return {"ok": False, "error": "这条作品不在画廊里"}

    from ..auth import scope as identity

    owner = identity.uid_from_namespaced(thread_id)
    return {
        "ok": True,
        "item": {
            "threadId": payload.get("thread_id") or thread_id,
            "title": payload.get("title") or "一段讲解动画",
            "subtitle": payload.get("subtitle") or "",
            "segments": payload.get("segments") or [],
            "final": payload.get("final"),
            "publishedAt": store._now_ms(payload.get("publishedAt")),
            "mine": bool(identity.user_id() is not None and owner == identity.user_id()),
            "author": _authors().get(owner, "") if owner is not None else "",
        },
    }

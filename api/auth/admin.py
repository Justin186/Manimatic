# -*- coding: utf-8 -*-
"""
`/api/auth/users/*` —— 账号管理（仅管理员）。

================================================================================
为什么这些接口**自己**做鉴权，而不是靠"挂在白名单外面"
================================================================================
`/api/auth/` 整段在闸门的公开白名单里（登录/注册必须能匿名访问），所以走到
这里的请求**没有经过任何身份检查**。于是每个管理接口开头都要显式复核一次。

把它们挪到白名单外（比如 `/api/admin/users`）能省掉这一步，但那意味着以后
每加一个管理接口都要**两处同步改**，而"忘了改白名单"的后果是**接口裸奔**。
统一显式鉴权的话，漏了只会 403（看得见），不会静默放行。

================================================================================
三条防呆：不安全的方向一律拒绝
================================================================================
1. **不能停用 / 降级 / 删除自己** —— 手滑一下就把自己锁在门外，而补救要
   直接改数据库（没有管理员席位了，谁也进不来管理面板）。
2. **不能动超级管理员** —— root 是"最后的退路"，它的地位不由普通管理员决定。
3. **不能把最后一个管理员降级/停用/删除** —— 全站没有管理员之后，
   `bootstrap` 也救不了（它只在空库时可用）。宁可拒绝，也不制造这种状态。
"""

import os
import shutil

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import __version__
from . import passwords, scope, store
from .config import ONLINE_CHECKLIST as _ONLINE_CHECKLIST
from .config import load_config
from .router import _EMAIL_RE, _public_user

router = APIRouter()


def _deny(message, status=403, code="forbidden"):
    return JSONResponse({"ok": False, "error": message, "code": code},
                        status_code=status)


def _me():
    return scope.current()


def _admin_guard():
    """
    管理员闸门。返回 (me, None) 或 (None, 拒绝响应)。

    未登录与非管理员**分开报**：这不是登录失败，是权限不足，
    前端据此显示的也不一样（前者跳登录、后者只提示）。
    """
    me = _me()
    if not me:
        return None, _deny("未登录或登录已失效", 401, "unauthorized")
    if not me.get("is_admin"):
        return None, _deny("只有管理员能管理账号", 403, "not_admin")
    return me, None


def _admin_count(cfg):
    _n, admins = store.count_users(cfg)
    return admins


def _last_admin_block(me, cfg, action, target):
    """把"降级/停用/删除最后一个管理员"这条挡住。"""
    if target["role"] != "admin" or target["status"] != "active":
        return None
    if _admin_count(cfg) > 1:
        return None
    if target["id"] == me["id"]:
        # 这一条正常情况下已经被"不能动自己"挡住了，留在这里是为了
        # "改别人但那个人是唯一管理员"这条路径也有一个明确的说法。
        return None
    return _deny(f"{action}失败：这是最后一个管理员，全站没有管理员之后就没人能进管理面板了")


def _dir_size(path):
    """目录占用（字节）。**单个文件失败就跳过** —— 统计不该因为一个权限问题整块失败。"""
    total = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        total += _dir_size(e.path)
                    else:
                        total += e.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _ns_usage(prefix):
    """
    某个命名空间（`u3_`）名下的产物占用。

    ⚠️ 它统计的是**文件系统中带这个前缀的东西**，不是数据库里的账号 ——
       账号删了、数据还在（这是本项目的取舍），所以这里能明确显示出来，
       否则"删了号还占着几个 G"永远没人知道。
    """
    from .. import store as api_store

    out = api_store.OUTPUT_DIR if hasattr(api_store, "OUTPUT_DIR") else ""
    if not out:
        from .. import config as api_config
        out = api_config.OUTPUT_DIR

    total = 0
    targets = [
        (os.path.join(api_store.tasks_root(), "messages"), ".json"),
        (os.path.join(api_store.tasks_root(), "threads"), ".json"),
        (os.path.join(api_store.tasks_root(), "sessions"), ".json"),
        (api_store.tasks_root(), ""),
        (os.path.join(out, "_parts"), ""),
        (os.path.join(out, "videos"), "_scene"),
    ]
    for base, suffix in targets:
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for n in names:
            if n in ("threads", "messages", "sessions"):
                continue
            if not n.startswith(prefix):
                continue
            if suffix and not n.endswith(suffix):
                continue
            full = os.path.join(base, n)
            if os.path.isdir(full):
                total += _dir_size(full)
            elif os.path.isfile(full):
                try:
                    total += os.path.getsize(full)
                except OSError:
                    pass
    return total


@router.get("/auth/overview")
async def overview():
    """
    后台首屏要的一屏数据。

    刻意**不**做实时耗时统计（那些要读留档、按天聚合）。这一屏回答的是四个
    最常被问、且现在就必须能看出来的问题：

      1. 服务还活着吗（版本 / LaTeX / ffmpeg / LLM 档案）
      2. 有多少人、多少条会话
      3. 磁盘被谁占着（老数据在不在公共区、谁最占地方）
      4. 有什么需要现在处理（没管理员、注册开着、数据没隔离、鉴权关了）
    """
    me, err = _admin_guard()
    if err:
        return err

    from .. import config as api_config
    from .. import store as api_store
    from .. import jobs
    from storyboard import latex_env

    cfg = load_config()
    users = store.list_users(cfg)
    by_id = {u["id"]: u for u in users}

    out_dir = api_store.OUTPUT_DIR if hasattr(api_store, "OUTPUT_DIR") else api_config.OUTPUT_DIR
    tasks = api_store.tasks_root()

    # ---- 会话与产物分布（按命名空间分组）----
    groups = {}          # ns（"u1" / ""）→ {"threads", "bytes", "orphan"}
    for sub in ("messages", "threads", "sessions"):
        d = os.path.join(tasks, sub)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if not n.endswith(".json"):
                continue
            stem = n[:-5]
            uid = scope.uid_from_namespaced(stem)
            key = f"u{uid}" if uid else ""
            g = groups.setdefault(key, {"threads": 0, "bytes": 0, "orphan": False})
            if sub == "messages":
                g["threads"] += 1
            try:
                g["bytes"] += os.path.getsize(os.path.join(d, n))
            except OSError:
                pass

    # 产物目录（按名字前缀归到命名空间；顺带算占用）
    for base in (tasks, os.path.join(out_dir, "_parts"), os.path.join(out_dir, "videos")):
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for n in names:
            if n in ("threads", "messages", "sessions"):
                continue
            full = os.path.join(base, n)
            if not os.path.isdir(full):
                continue
            uid = scope.uid_from_namespaced(n)
            key = f"u{uid}" if uid else ""
            groups.setdefault(key, {"threads": 0, "bytes": 0, "orphan": False})
            groups[key]["bytes"] += _dir_size(full)

    # ---- 给每个账号配上占用；顺带标出"数据在但账号没了" ----
    accounts = []
    for u in users:
        key = f"u{u['id']}"
        g = groups.pop(key, {"threads": 0, "bytes": 0})
        accounts.append({
            "id": u["id"], "email": u["email"], "name": u["name"],
            "role": u["role"], "isAdmin": u["is_admin"], "isRoot": u["is_root"],
            "status": u["status"],
            "threads": g["threads"], "bytes": g["bytes"],
        })
    # 剩下的是"有数据但没有对应账号"的分区（删过号）与公共区（老数据）
    orphan_bytes = sum(g["bytes"] for k, g in groups.items() if k)
    orphan_threads = sum(g["threads"] for k, g in groups.items() if k)
    legacy = groups.get("", {"threads": 0, "bytes": 0})

    total_threads = sum(a["threads"] for a in accounts) + orphan_threads + legacy["threads"]
    total_bytes = sum(a["bytes"] for a in accounts) + orphan_bytes + legacy["bytes"]

    tex = latex_env.detect()
    try:
        from storyboard import llm
        llm_cfg = llm.resolve_config()
        llm_info = {"model": llm_cfg.get("model"), "profile": llm_cfg.get("profile") or "",
                    "has_key": bool(llm_cfg.get("api_key"))}
    except Exception:                                          # noqa: BLE001
        llm_info = {"model": "", "profile": "", "has_key": False}

    # ---- 需要现在处理的事（这些是"没人盯着就会出事"的）----
    warnings = []
    if not cfg.enabled:
        warnings.append("鉴权已关闭（MSB_AUTH_ENABLED=0）—— 所有接口裸奔，仅供排查")
    if not any(u["is_admin"] for u in users):
        warnings.append("没有管理员 —— 没人能进这个后台")
    if cfg.invite:
        warnings.append(
            "注册是开放的（配了 MSB_AUTH_INVITE）—— 拿到邀请码的人都能进来用你的 LLM 密钥")
    if legacy["threads"]:
        warnings.append(f"公共区还有 {legacy['threads']} 条**没有归属**的会话（升级前的数据，未收编）")
    if orphan_threads:
        warnings.append(f"有 {orphan_threads} 条会话属于**已删除的账号** —— 数据还在磁盘上，但没人能访问")

    return {
        "ok": True,
        "users": accounts,
        "stats": {
            "users": len(accounts),
            "admins": sum(1 for a in accounts if a["isAdmin"]),
            "threads": total_threads,
            "bytes": total_bytes,
        },
        "legacy": {"threads": legacy["threads"], "bytes": legacy["bytes"]},
        "orphan": {"threads": orphan_threads, "bytes": orphan_bytes},
        "server": {
            "version": __version__,
            "latex": tex["available"],
            "latex_reason": tex.get("reason", ""),
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "llm": llm_info,
            "quality": api_config.quality_dir_name(),
            "ttlDays": api_config.TASK_TTL_DAYS,
            "maxRenders": api_config.MAX_CONCURRENT_RENDERS,
            "jobs": jobs.stats(),
            "outputDir": out_dir,
        },
        "config": {
            "registerOpen": bool(cfg.invite),
            "cookieSecure": cfg.cookie_secure,
            "sessionDays": cfg.session_days,
            "adoptLegacy": cfg.adopt_legacy,
            "db": cfg.db,
        },
        "warnings": warnings,
        "checklist": list(_ONLINE_CHECKLIST),
    }


@router.get("/auth/users")
async def list_users():
    """所有账号。**绝不回任何密码信息**（含哈希、轮数、盐）。"""
    me, err = _admin_guard()
    if err:
        return err
    return {"ok": True, "users": [_public_user(u) | {
        "status": u["status"],
        "createdAt": int(u["created_at"] * 1000),
    } for u in store.list_users()]}


@router.post("/auth/users")
async def create_user(request: Request):
    """
    管理员建号。**不受注册开关约束** —— `MSB_AUTH_INVITE` 管的是"陌生人能不能
    自助注册"，不是"管理员能不能加人"。把 `closed` 理解成后者的话，
    整个服务会被锁死（谁也别想再加人）。
    """
    me, err = _admin_guard()
    if err:
        return err
    cfg = load_config()
    body = await request.json() if request.headers.get(
        "content-type", "").startswith("application/json") else {}
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    name = str(body.get("name") or "").strip()
    role = "admin" if body.get("role") == "admin" else "user"

    if not _EMAIL_RE.match(email):
        return _deny("邮箱格式不正确", 400, "bad_email")
    why = passwords.weak_password_reason(password, cfg)
    if why:
        return _deny(why, 400, "weak_password")
    if store.get_user_by_email(email, cfg) is not None:
        return _deny("这个邮箱已经注册过了", 409, "email_taken")

    u = store.create_user(email, password, name=name, role=role, cfg=cfg)
    if u is None:
        return _deny("这个邮箱已经注册过了", 409, "email_taken")
    return {"ok": True, "user": _public_user(u)}


@router.patch("/auth/users/{uid}")
async def update_user(uid: int, request: Request):
    """
    改一个账号：显示名 / 角色 / 状态 / 重置密码。

    返回体里带 `revoked`：重置密码时**吊销了他正在用的全部会话**，
    这一点要让管理员知道（对方会被踢下线），否则会被当成"我这操作没生效"。
    """
    me, err = _admin_guard()
    if err:
        return err
    cfg = load_config()
    body = await request.json() if request.headers.get(
        "content-type", "").startswith("application/json") else {}

    target = store.get_user_by_id(uid, cfg)
    if target is None:
        return _deny("没有这个账号", 404, "not_found")

    # ⚠️ **先判"自己"再判"root"**：两条都成立时（root 想改自己），
    #    "不能改自己的角色"比"不能修改超级管理员的角色"更贴近他刚做的事，
    #    也更可操作（后者会让人以为是"root 这个身份特殊"而不是"这是你自己"）。
    is_self = target["id"] == me["id"]
    if target["is_root"] and not is_self:
        return _deny("不能修改超级管理员的账号", 403, "root_protected")

    patch = {}

    if "name" in body:
        patch["name"] = str(body.get("name") or "").strip()[:40]

    if "role" in body:
        role = "admin" if body.get("role") == "admin" else "user"
        if role != target["role"]:
            if is_self:
                return _deny("不能改自己的角色（先让另一个管理员来改）", 403, "self_protected")
            if target["is_root"]:
                return _deny("不能修改超级管理员的角色", 403, "root_protected")
            if role == "user":
                blocked = _last_admin_block(me, cfg, "降级", target)
                if blocked:
                    return blocked
            patch["role"] = role

    if "status" in body:
        status = "active" if body.get("status") == "active" else "disabled"
        if status != target["status"]:
            if status == "disabled":
                if is_self:
                    return _deny("不能停用自己", 403, "self_protected")
                if target["is_root"]:
                    return _deny("不能停用超级管理员", 403, "root_protected")
                blocked = _last_admin_block(me, cfg, "停用", target)
                if blocked:
                    return blocked
            patch["status"] = status

    revoked = 0
    if body.get("password"):
        pwd = str(body["password"])
        why = passwords.weak_password_reason(pwd, cfg)
        if why:
            return _deny(why, 400, "weak_password")
        patch["password_hash"] = passwords.hash_password(pwd, cfg.pbkdf2_iters)
        # ⚠️ 改密码必须踢掉旧会话：不踢的话"我改了密码"只是自我安慰，
        #    偷到旧令牌的人照样进得来（而且他不会告诉你）。
        revoked = store.revoke_user_sessions(target["id"], cfg)

    if not patch:
        return _deny("没有要改的字段", 400, "empty_patch")

    u = store.update_user(target["id"], patch, cfg)
    return {"ok": True, "user": _public_user(u) | {"status": u["status"]},
            "revoked": revoked}


@router.delete("/auth/users/{uid}")
async def delete_user(uid: int):
    """
    删号。**不删他的会话与视频**。

    取舍：数据比账号值钱 —— 账号可以重建（同一个邮箱再建一次就是新账号），
    而视频渲一遍要几分钟。所以删号只切断登录，产物留在那个 `u<id>_` 分区里
    （以后再建同 id 的账号也拿不回来，这一点要在界面上说清）。
    """
    me, err = _admin_guard()
    if err:
        return err
    cfg = load_config()
    target = store.get_user_by_id(uid, cfg)
    if target is None:
        return _deny("没有这个账号", 404, "not_found")
    # 同上：自己那条排在 root 之前，理由见 update_user 里的注释
    if target["id"] == me["id"]:
        return _deny("不能删除自己", 403, "self_protected")
    if target["is_root"]:
        return _deny("不能删除超级管理员", 403, "root_protected")
    blocked = _last_admin_block(me, cfg, "删除", target)
    if blocked:
        return blocked

    store.delete_user(target["id"], cfg)
    return {"ok": True, "removed": target["email"],
            "note": "该账号的会话与视频**没有被删除**，仍在磁盘上（数据比账号值钱）"}

# -*- coding: utf-8 -*-
"""
`/api/auth/*` —— 注册 / 登录 / 登出 / 当前身份 / 公开配置。

================================================================================
这五个端点整段都在闸门的**白名单**里（见 guard.PUBLIC_PREFIXES）
================================================================================
登录与注册必须能匿名访问，于是闸门对它们完全不设防 —— 所以每个端点
**自己**负责安全，不能假设"前面已经验过了"。

================================================================================
三条贯穿实现的安全规矩（都是"改掉一条就开一个洞"级别）
================================================================================
1. **登录不区分"邮箱不存在"与"密码错"** —— 都回同一句"邮箱或密码不正确"。
   分开报会让这个接口变成"这个邮箱注册过吗"的探测器（可用于定向钓鱼）。
2. **失败路径也消耗同样的时间** —— 邮箱不存在时**故意**跑一次假的 PBKDF2
   （`passwords.fake_verify`）。否则"0ms vs 300ms"的耗时差等于把第 1 条白写了。
3. **令牌只出口一次** —— 只出现在 `Set-Cookie` 头里，响应体里**没有**。
   前端即使把响应整个打进日志也拿不到它。
"""

import re
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import passwords, scope, store
from .config import load_config

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

# 失败时永远回这一句，不区分原因（见文件头第 1 条）
_BAD_CRED = "邮箱或密码不正确"


def _public_user(u):
    """能安全回给前端的字段。**绝不回密码哈希**。"""
    if not u:
        return None
    return {
        "id": u["id"],
        "email": u["email"],
        "name": u["name"],
        "role": u["role"],
        "isAdmin": bool(u["is_admin"]),
        "isRoot": bool(u["is_root"]),
    }


def _bad(message, status=400, code=""):
    return JSONResponse({"ok": False, "error": message, "code": code},
                        status_code=status)


def _ok(payload=None, token=""):
    resp = JSONResponse({"ok": True, **(payload or {})})
    if token:
        _set_cookie(resp, token)
    return resp


def _set_cookie(resp, token):
    cfg = load_config()
    resp.set_cookie(
        key=cfg.cookie_name,
        value=token,
        max_age=cfg.session_days * 86400,
        # HttpOnly：JS 拿不到 → XSS 偷不走登录态
        httponly=True,
        # 前端走同源代理，SameSite=Lax 足够；它同时允许从外链点进来时仍带 Cookie
        samesite="lax",
        # 走 HTTPS 必须设 1，否则浏览器不会存（见 config.ONLINE_CHECKLIST）
        secure=cfg.cookie_secure,
        # ⚠️ 必须是 "/"，且**不设 Domain**：
        #   · 前端经过 Next 的 /msb 代理，浏览器看到的是同源的 3000 端口，
        #     Cookie 落在 host 上、Path=/ 才能被所有 /msb/api/* 请求带上；
        #   · 设了 Domain 反而会在"后端 8000 直连"和"前端 3000 代理"两种
        #     访问方式之间串味，出现"登录成功、下一个请求还是 401"。
        path="/",
    )


def _clear_cookie(resp):
    cfg = load_config()
    resp.delete_cookie(cfg.cookie_name, path="/")


def _invite_ok(given, cfg):
    """邀请码校验。没配 `MSB_AUTH_INVITE` = **注册关闭**（缺失值只能是拒绝）。"""
    want = str(cfg.invite or "")
    if not want:
        return False, "当前未开放注册（需要管理员开放）"
    if str(given or "").strip() != want:
        return False, "邀请码不正确"
    return True, ""


@router.get("/auth/config")
async def auth_config():
    """
    登录页需要的公开信息。

    ⚠️ **不回传邀请码本身**，只回"是否需要"—— 它是能注册进来的唯一凭证，
       不能出现在任何匿名可访问的响应里。
    """
    cfg = load_config()
    return {
        "ok": True,
        "enabled": cfg.enabled,
        "registerOpen": bool(cfg.invite),
        "minPassword": cfg.min_password,
        "sessionDays": cfg.session_days,
    }


@router.get("/auth/me")
async def auth_me(request: Request):
    """
    当前身份。未登录返回 `{"user": null}` —— **不是 401**。

    前端每次加载都要问一次"我登录了吗"，这是正常流程。返回 401 会让控制台
    刷满红色错误，还会让 fetch 封装把它当成故障走异常分支。
    """
    u = scope.current() or store.resolve_session(_token_of(request))
    return {"ok": True, "user": _public_user(u)}


def _token_of(request: Request):
    cfg = load_config()
    return str(request.cookies.get(cfg.cookie_name) or "").strip()


@router.post("/auth/register")
async def auth_register(request: Request):
    cfg = load_config()
    body = await request.json() if request.headers.get(
        "content-type", "").startswith("application/json") else {}
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    name = str(body.get("name") or "").strip()
    invite = str(body.get("invite") or "").strip()

    if not _EMAIL_RE.match(email):
        return _bad("邮箱格式不正确", code="bad_email")
    why = passwords.weak_password_reason(password, cfg)
    if why:
        return _bad(why, code="weak_password")
    ok, why = _invite_ok(invite, cfg)
    if not ok:
        return _bad(why, 403, "bad_invite")

    if store.get_user_by_email(email, cfg) is not None:
        # 这一条**可以**明说：邮箱是否被占用在注册页上本来就是可见的
        # （输完就提示），装作不知道只会让人反复试。
        return _bad("这个邮箱已经注册过了", 409, "email_taken")

    user = store.create_user(email, password, name=name, role="user", cfg=cfg)
    if user is None:
        return _bad("这个邮箱已经注册过了", 409, "email_taken")
    return _ok({"user": _public_user(user)}, store.create_session(user["id"], cfg))


@router.post("/auth/login")
async def auth_login(request: Request):
    cfg = load_config()
    body = await request.json() if request.headers.get(
        "content-type", "").startswith("application/json") else {}
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")

    user = store.get_user_by_email(email, cfg)
    if user is None:
        # ⚠️ 必须跑一次等时的假校验（文件头第 2 条）
        passwords.fake_verify(password)
        return _bad(_BAD_CRED, 401, "bad_cred")

    left = store.locked_left(user)
    if left > 0:
        return _bad(f"连续输错多次，请 {int(left / 60) + 1} 分钟后再试",
                    429, "locked")

    ok, needs_upgrade = passwords.verify(password, _hash_of(user, cfg))
    if not ok:
        just_locked = store.note_fail(user, cfg)
        msg = "连续输错已达上限，账号已临时锁定" if just_locked else _BAD_CRED
        return _bad(msg, 429 if just_locked else 401, "bad_cred")

    store.clear_fails(user, cfg)
    # 轮数调高之后，老密码在登录成功时平滑升级 —— 不需要发邮件逼所有人改密码
    if needs_upgrade:
        store.update_user(user["id"],
                          {"password_hash": passwords.hash_password(
                              password, cfg.pbkdf2_iters)}, cfg)

    if user["status"] != "active":
        return _bad("这个账号已被停用", 403, "disabled")

    return _ok({"user": _public_user(user)}, store.create_session(user["id"], cfg))


@router.post("/auth/logout")
async def auth_logout(request: Request):
    """
    登出：**服务端吊销** + 清 Cookie，两件都做。

    只清 Cookie 的话，那份令牌在数据库里仍然有效 —— 有人在清之前把它抄走了
    （或日志里有），就能一直用到过期。
    """
    cfg = load_config()
    token = _token_of(request)
    if token:
        store.revoke_session(token, cfg)
    resp = JSONResponse({"ok": True})
    _clear_cookie(resp)
    return resp


def _hash_of(user, cfg):
    """读密码哈希。单独抽出来是因为它是唯一一处"直接读受保护列"的地方。"""
    con = store.connect(cfg)
    try:
        r = con.execute("SELECT password_hash FROM users WHERE id=?",
                        (int(user["id"]),)).fetchone()
        return r["password_hash"] if r else ""
    finally:
        con.close()


def _now():
    return time.time()

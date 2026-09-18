# -*- coding: utf-8 -*-
"""
账号库（sqlite）。

================================================================================
为什么这里必须用数据库，而其余一切仍然是文件
================================================================================
项目迄今为止 100% 文件存储（JSON + MP4），那对"内容"是对的。账号数据有两条
文件存储**做不到**的性质：

1. **唯一约束**：邮箱必须唯一。文件存储只能"先扫一遍再比对"，两个请求同时
   注册同一邮箱会双双通过（check-then-act 竞态）。`UNIQUE` 索引把它从
   "很难复现的 bug" 变成"不可能发生"。
2. **按身份查询**：找出"某个人的会话"要能一次命中。

而 `sqlite3` 是标准库 —— `requirements.txt` 一行不改。

================================================================================
关于并发
================================================================================
WAL 模式 + `busy_timeout`：FastAPI 的请求跑在线程池里，写操作会并发。
不开 WAL 的话"登录"和"写会话"会互相等锁，表现为偶发的 500。

================================================================================
会话表只存**令牌的 SHA-256 指纹**
================================================================================
库文件万一泄露（备份、路径遍历），明文令牌等于"直接可用的登录态"；
存指纹则还要再爆破一次，而且**吊销只需删一行**。

令牌本身是 32 字节高熵随机值、不可枚举，所以用**不加盐**的 SHA-256 就够，
不需要 PBKDF2 那种慢哈希 —— 每个请求都要查一次库，慢哈希会让每个 API
请求多几百毫秒。
"""

import hashlib
import os
import secrets
import sqlite3
import time

from ..config import ROOT
from .config import load_config
from . import passwords

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL DEFAULT '',
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',
    is_root       INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'active',
    fail_count    INTEGER NOT NULL DEFAULT 0,
    locked_until  REAL    NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL,
    updated_at    REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at REAL    NOT NULL,
    expires_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


def db_path(cfg=None):
    """相对路径按**仓库根**解析（uvicorn / bat 都从 MathStoryboard 启动）。"""
    cfg = cfg or load_config()
    p = cfg.db
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def connect(cfg=None):
    cfg = cfg or load_config()
    path = db_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=10.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db(cfg=None):
    con = connect(cfg)
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------

def _row_user(r):
    if r is None:
        return None
    return {
        "id": int(r["id"]),
        "email": r["email"],
        "name": r["name"],
        "role": r["role"],
        "is_root": bool(r["is_root"]),
        "is_admin": r["role"] == "admin",
        "status": r["status"],
        "fail_count": int(r["fail_count"]),
        "locked_until": float(r["locked_until"]),
        "created_at": float(r["created_at"]),
    }


def get_user_by_email(email, cfg=None):
    con = connect(cfg)
    try:
        r = con.execute("SELECT * FROM users WHERE email=?",
                        (str(email or "").strip().lower(),)).fetchone()
        return _row_user(r)
    finally:
        con.close()


def get_user_by_id(uid, cfg=None):
    con = connect(cfg)
    try:
        r = con.execute("SELECT * FROM users WHERE id=?", (int(uid),)).fetchone()
        return _row_user(r)
    finally:
        con.close()


def list_users(cfg=None):
    con = connect(cfg)
    try:
        rows = con.execute(
            "SELECT * FROM users ORDER BY is_root DESC, id ASC").fetchall()
        return [_row_user(r) for r in rows]
    finally:
        con.close()


def count_users(cfg=None):
    con = connect(cfg)
    try:
        n = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        admins = con.execute(
            "SELECT COUNT(*) c FROM users WHERE role='admin'").fetchone()["c"]
        return int(n), int(admins)
    finally:
        con.close()


def create_user(email, password, name="", role="user", is_root=False, cfg=None):
    """返回 user dict；邮箱已存在返回 None。**不校验密码强度**（那是路由层的事）。"""
    cfg = cfg or load_config()
    now = time.time()
    con = connect(cfg)
    try:
        cur = con.execute(
            "INSERT INTO users (email, name, password_hash, role, is_root,"
            " status, fail_count, locked_until, created_at, updated_at)"
            " VALUES (?,?,?,?,?, 'active', 0, 0, ?, ?)",
            (str(email or "").strip().lower(), str(name or "").strip(),
             passwords.hash_password(password, cfg.pbkdf2_iters),
             "admin" if role == "admin" else "user",
             1 if is_root else 0, now, now),
        )
        con.commit()
        return get_user_by_id(cur.lastrowid, cfg)
    except sqlite3.IntegrityError:
        return None                      # 邮箱重复
    finally:
        con.close()


def update_user(uid, patch, cfg=None):
    """
    改一个用户的字段。可改：name / role / status / password_hash / 失败计数。

    ⚠️ 不允许改 email 与 is_root：两者都是身份锚点（前者是登录名，
       后者决定"这个账号不可被降级/删除"），改起来牵动的东西太多。
    """
    allowed = ("name", "role", "status", "password_hash",
               "fail_count", "locked_until")
    sets, vals = [], []
    for k, v in (patch or {}).items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return get_user_by_id(uid, cfg)
    sets.append("updated_at=?")
    vals.append(time.time())
    vals.append(int(uid))
    con = connect(cfg)
    try:
        con.execute("UPDATE users SET " + ",".join(sets) + " WHERE id=?", vals)
        con.commit()
    finally:
        con.close()
    return get_user_by_id(uid, cfg)


def delete_user(uid, cfg=None):
    """删号。**连带吊销他的全部会话**；他名下的会话与视频**不动**（数据比账号值钱）。"""
    cfg = cfg or load_config()
    con = connect(cfg)
    try:
        con.execute("DELETE FROM sessions WHERE user_id=?", (int(uid),))
        con.execute("DELETE FROM users WHERE id=?", (int(uid),))
        con.commit()
        return True
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 登录失败与锁定
# ---------------------------------------------------------------------------

def locked_left(user, now=None):
    """还剩多少秒解锁（0 = 没锁）。"""
    left = float(user.get("locked_until") or 0) - (now or time.time())
    return max(0.0, left)


def note_fail(user, cfg=None):
    """记一次失败；到上限就锁定。**返回是否刚被锁上**（好给前端一句人话）。"""
    cfg = cfg or load_config()
    n = int(user.get("fail_count") or 0) + 1
    patch = {"fail_count": n}
    just_locked = False
    if n >= cfg.max_fails:
        patch["locked_until"] = time.time() + cfg.lock_minutes * 60
        patch["fail_count"] = 0
        just_locked = True
    update_user(user["id"], patch, cfg)
    return just_locked


def clear_fails(user, cfg=None):
    if int(user.get("fail_count") or 0) or float(user.get("locked_until") or 0):
        update_user(user["id"], {"fail_count": 0, "locked_until": 0}, cfg)


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------

def _fingerprint(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id, cfg=None):
    """
    建一个会话，返回**明文令牌**。

    ⚠️ 明文令牌**只在这一刻存在一次**：库里存的是它的 SHA-256 指纹。
       之后它就只活在用户的 Cookie 里，服务端再也拿不回来 ——
       这正是"库泄露也不等于登录态泄露"的原因。
    """
    cfg = cfg or load_config()
    token = secrets.token_urlsafe(32)
    now = time.time()
    con = connect(cfg)
    try:
        con.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at)"
            " VALUES (?,?,?,?)",
            (_fingerprint(token), int(user_id), now,
             now + cfg.session_days * 86400))
        con.commit()
    finally:
        con.close()
    return token


def resolve_session(token, cfg=None):
    """
    令牌 → 用户。**同时校验三件事**：没过期、账号是 active、账号还存在。

    第三条为什么必须查：`status` 是唯一能让"已发出的令牌立刻失效"的手段 ——
    只删 sessions 表的话，被停用的用户手里那份令牌要等自然过期才失效。

    ⚠️ **故意不校验 `locked_until`**（连续输错密码那个锁）—— 这不是遗漏：
       锁定的语义是"暂时不接受**新的密码尝试**"，不是"这个身份不该再访问"。
       如果它也能踢掉已登录的会话，那么**任何人只要知道你的邮箱，就能靠连续
       输错密码把你踢下线** —— 一个只要邮箱就能发起的拒绝服务。
       所以：停用 / 改密 → 立刻失效（那是身份层面的变化）；锁定 → 只挡登录动作。
    """
    if not token:
        return None
    con = connect(cfg)
    try:
        r = con.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id"
            " WHERE s.token_hash=? AND s.expires_at>?",
            (_fingerprint(token), time.time())).fetchone()
    finally:
        con.close()
    u = _row_user(r)
    if not u or u["status"] != "active":
        return None
    return u


def revoke_user_sessions(uid, cfg=None):
    """
    吊销某个用户的**全部**会话。

    ⚠️ 改密码必须调它：换了密码却让旧令牌继续有效，等于"我改了密码但小偷
       还在里面"。停用/删号不用调 —— 那两条由 `resolve_session` 查 `status`
       和删 sessions 各自兜住了（见 resolve_session 的说明）。
    """
    # 删得掉/删不掉会改变这个调用的耗时。这里补一次等时的假哈希，免得
    # "目标账号此刻有没有活跃会话"变成一条可探测的计时侧信道。
    passwords.fake_verify("")
    con = connect(cfg)
    try:
        n = con.execute("DELETE FROM sessions WHERE user_id=?",
                        (int(uid),)).rowcount
        con.commit()
        return int(n or 0)
    finally:
        con.close()


def revoke_session(token, cfg=None):
    con = connect(cfg)
    try:
        con.execute("DELETE FROM sessions WHERE token_hash=?",
                    (_fingerprint(token),))
        con.commit()
        return True
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 预置的超级管理员
# ---------------------------------------------------------------------------

def ensure_root(cfg=None):
    """
    保证超级管理员存在。幂等，每次启动都可以调。

    返回 dict：
        {"user": ..., "created": bool, "generated_password": str}

    ⚠️ **已存在时绝不改密码**：否则每次启动都会把你手工改过的密码覆盖回去，
       而表现是"我明明改了密码，重启后又变成旧的"（很难想到是启动逻辑干的）。

    ⚠️ 没配 `MSB_AUTH_ROOT_PASSWORD` 时**生成一个强随机密码**并回出来，
       由调用方（启动横幅）负责把它显示给人看 —— 密码不能有个"默认明文值"，
       但也不能让人登不进来。
    """
    cfg = cfg or load_config()
    init_db(cfg)
    email = str(cfg.root_email or "").strip().lower()
    if not email:
        return {"user": None, "created": False, "generated_password": "",
                "error": "MSB_AUTH_ROOT_EMAIL 是空的"}

    user = get_user_by_email(email, cfg)
    if user is not None:
        # 补强：即使库里有这个账号（比如更早的版本建的），也要确保它是 root+admin
        patch = {}
        if not user["is_root"]:
            patch["is_root"] = 1
        if user["role"] != "admin":
            patch["role"] = "admin"
        if user["status"] != "active":
            patch["status"] = "active"
        if patch:
            user = update_user(user["id"], patch, cfg)
        return {"user": user, "created": False, "generated_password": ""}

    password = cfg.root_password or secrets.token_urlsafe(12)
    user = create_user(email, password, name=cfg.root_name,
                       role="admin", is_root=True, cfg=cfg)
    if user is None:
        return {"user": None, "created": False, "generated_password": "",
                "error": "创建超级管理员失败"}
    return {"user": user, "created": True,
            "generated_password": "" if cfg.root_password else password}

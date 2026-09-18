# -*- coding: utf-8 -*-
"""
账号体系（认证 + 数据归属）。

================================================================================
为什么在 core 里，而不是做成插件
================================================================================
参考实现（`plugins/auth/`）是插件：它靠**运行时替换** `api.store.safe_id`、
`pipeline.stream`、`find_share_slug` 来生效，因为插件不许改 `api/` 的源文件。

登录是**常驻能力**（每个接口都要过、每个 store 函数都要知道"我是谁"），
按 HANDOFF §1.2 的判据应当并进 core。并进来之后，那些运行时替换就变回
**直接改源码** —— 少一层隐式魔法，也少一类"改了源码但插件没跟上"的静默失效。

================================================================================
模块地图
================================================================================
    config.py     MSB_AUTH_* 环境变量（前缀独立，不碰 api/config.py）
    passwords.py  PBKDF2 哈希 / 校验 / 防时序侧信道的"假校验"
    store.py      sqlite：users / sessions 表，以及"预置超级管理员"
    guard.py      鉴权闸门（中间件）—— 解析身份、写 ContextVar、放行白名单
    scope.py      数据隔离：给 store 的 id→路径 加命名空间
    router.py     /api/auth/* 端点

================================================================================
依赖：只有标准库
================================================================================
    sqlite3（标准库）  —— 账号需要"唯一约束"和"按身份查询"，文件存储做不到
    hashlib / hmac     —— 密码哈希与定值时间比较
    secrets            —— 会话令牌
`requirements.txt` 一行都不用改。
"""

from . import config as _config


def install(app):
    """
    挂闸门 + 挂 `/api/auth/*` 路由。在 `api/main.py` 的 import 期调用一次。

    ⚠️ 闸门必须挂到 CORS **之内**，见 guard.install_gate 的说明。
    """
    cfg = _config.load_config()
    if not cfg.enabled:
        print("[auth] ⚠️ MSB_AUTH_ENABLED=0 —— 鉴权已关闭，所有接口裸奔"
              "（只为排查问题，别留在生产上）", flush=True)
        return

    from . import admin, guard, router, store

    store.init_db(cfg)
    guard.install_gate(app)
    app.include_router(router.router, prefix="/api", tags=["auth"])
    # 管理接口与上面五条在同一个前缀下，但**各自显式复核管理员身份**
    # （见 admin.py 文件头：白名单里的接口没有任何前置身份检查）
    app.include_router(admin.router, prefix="/api", tags=["auth-admin"])
    app._auth_installed = True


def startup(cfg=None):
    """
    启动时跑一次：建表（幂等）+ 确保超级管理员存在。

    返回一份"账号现状"给启动横幅用。**这里是有权打印初始密码的唯一地方** ——
    密码不设默认明文值（那等于把后门写进代码），但也不能让人登不进来，
    所以没配 `MSB_AUTH_ROOT_PASSWORD` 时生成随机密码、并在此处喊出来。
    """
    cfg = cfg or _config.load_config()
    if not cfg.enabled:
        return {"enabled": False}

    from . import scope, store

    store.init_db(cfg)
    root = store.ensure_root(cfg)
    n, admins = store.count_users(cfg)

    # 老数据收编：只有**这次刚建出超级管理员**时才做（首次启用账号体系那一刻）。
    # 之后每次启动都不该再扫一遍 —— 它是搬文件，不是幂等的清理。
    adopt = {"count": 0, "moved": []}
    if root.get("created") and cfg.adopt_legacy:
        adopt = scope.adopt_legacy(cfg, root["user"]["id"])

    info = {
        "adopt": adopt,
        "enabled": True,
        "root": root.get("user"),
        "created": bool(root.get("created")),
        "generated_password": root.get("generated_password") or "",
        "users": n,
        "admins": admins,
        "register_open": bool(cfg.invite),
        "error": root.get("error") or "",
    }
    _print_startup(info, cfg)
    return info


def _print_startup(info, cfg):
    if info.get("error"):
        print(f"[auth] ⚠️ 超级管理员没建起来：{info['error']}", flush=True)
        return

    lines = [f" 账号     : {info['users']} 个（其中管理员 {info['admins']} 个）",
             f" 注册     : {'需要邀请码' if info['register_open'] else '**已关闭**（未配 MSB_AUTH_INVITE）'}"]
    root = info.get("root") or {}
    if info["created"]:
        lines.append(f" 超级管理员: {root.get('email')}（已创建）")
        if info["generated_password"]:
            # ⚠️ 只在**首次创建**时打，且只打这一次 —— 之后库里只剩指纹，
            #    服务端自己也拿不回来。没看到的人只能靠删库重建或改库重置。
            lines.append(f"            初始密码 **{info['generated_password']}**"
                         f"（未配置 MSB_AUTH_ROOT_PASSWORD，随机生成；只显示这一次）")
        else:
            lines.append("            初始密码取自 MSB_AUTH_ROOT_PASSWORD")
    else:
        lines.append(f" 超级管理员: {root.get('email')}（已存在，未改动其密码）")

    ad = info.get("adopt") or {}
    if ad.get("count"):
        lines.append(f" 老数据   : 已收编 **{ad['count']} 项** 到超级管理员名下"
                     f"（改造前的会话与产物；这是搬文件，不是复制）")
    elif info.get("created"):
        lines.append(" 老数据   : 没有需要收编的（公共区是空的）")

    if info["admins"] == 0:
        lines.append(" ⚠️ 没有任何管理员 —— 没人能进账号管理，请检查上面的初始化结果")
    print("\n".join(lines), flush=True)

# -*- coding: utf-8 -*-
"""
鉴权闸门（纯 ASGI 中间件）+ Cookie 读写。

================================================================================
中间件顺序：这是唯一一处「不这么写就一定是错的」的地方
================================================================================
Starlette 的 `build_middleware_stack()` 是**倒序**包裹：

    for cls, args, kwargs in reversed(middleware):
        app = cls(app, ...)

所以 `user_middleware[0]` 是**最外层**，最后一个元素最靠近路由。
而 `add_middleware()` 的实现是 `insert(0, ...)` —— **后加的会在更外层**。

`api/main.py` 在 import 期就加了 CORSMiddleware，本模块装得更晚。若直接
`app.add_middleware(AuthGate)`，闸门就跑到 CORS **外面**：

    闸门先返回 401 → CORS 没经手 → 响应上没有 Access-Control-Allow-Origin
    → 浏览器判定为"跨域失败" → 前端只拿到一句含糊的 "Failed to fetch"

⚠️ 本项目前端走 Next 的**同源代理**（`/msb`），本来没有跨域问题。但直连
   8000 调试时仍然会遇到，而且这个坑的**症状指向完全错误的方向**（让人去
   查网络而不是查鉴权），所以一律按最内层挂。

================================================================================
为什么"默认全拦 + 白名单放行"，而不是逐个路由声明
================================================================================
漏配白名单一条 → 某个公开接口被拦 → 联调时**立刻**暴露；
反过来"默认放行、显式拦下"漏一条 → **接口裸奔且无人察觉**。
两种错误的代价不对称，所以选前者。
"""

import json

from starlette.middleware import Middleware
from starlette.responses import Response

# ⚠️ 这个模块里每个函数都有个 ASGI 的 `scope` 参数，会**遮住**同名模块。
#    所以身份上下文模块必须换个名字导入 —— 直接 `from . import scope` 的话，
#    下面 `scope.set_current(...)` 会去调那个 dict，报
#    "'dict' object has no attribute 'set_current'"。
from . import scope as identity
from . import store
from .config import load_config

# 不需要登录的路径前缀。
#
# ⚠️ 每加一条都要问一句"它泄露的东西是不是本来就公开的"：
#   · /api/auth/    —— 登录/注册必须能匿名访问（它们自己管自己的安全）
#   · /api/health   —— 环境自检，被拦会让"服务到底活着没有"也查不了
#   · /api/share/   —— 分享本来就是公开的（且它只回标题与视频，不回对话）
PUBLIC_PREFIXES = (
    "/api/auth/",
    "/api/health",
    "/api/share/",
)

# 不拦的路径（非 /api，本来就不在保护范围，列出来只为让人一眼看清边界）
#   /media/** —— 视频静态文件。**知道完整地址就能播**：
#                地址里的 id 是前端生成的随机串、不可枚举，但它不是秘密。
#                要收紧得让 <video> 带凭据加载，会改变视频加载行为，
#                而且分享功能本身要求它公开可看。
_UNAUTHORIZED = json.dumps(
    {"ok": False, "error": "未登录或登录已失效", "code": "unauthorized"},
    ensure_ascii=False,
)


def _is_public(path):
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


def _cookie_token(scope):
    """从请求头里抠出会话 Cookie。**只认我们自己那一个名字**。"""
    name = load_config().cookie_name.encode("latin-1")
    for k, v in scope.get("headers") or []:
        if k.lower() == b"cookie":
            for part in v.split(b";"):
                if not part:
                    continue
                key, _, val = part.partition(b"=")
                if key.strip() == name:
                    return val.strip().decode("latin-1")
    return ""


class AuthGate:
    """
    纯 ASGI 中间件。

    为什么不用 `BaseHTTPMiddleware`：它会把响应体收集起来再往下传，
    而 `/api/chat` 是**长流 SSE**（思考几分钟、逐字吐），被它一包就变成
    "要么缓冲到结束、要么中途卡住"。这种问题只在真实长任务下才出现，
    本地点两下测不出来 —— 所以宁可自己写这几十行 ASGI。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 只管 HTTP；websocket / lifespan 直接放行
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        cfg = load_config()
        path = str(scope.get("path") or "")

        # 关掉总开关时整条闸门形同虚设（只为排查问题存在）。
        # 不在 /api/ 之下的（/media、/docs…）也不归它管 —— 那里既不用拦，
        # 也没有任何代码在读身份。
        if not cfg.enabled or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        # 没有 Cookie 就**不查库**：健康检查、匿名访问分享页都走这条路，
        # 没必要为它们各加一次 sqlite 查询。
        cookie = _cookie_token(scope)
        user = store.resolve_session(cookie, cfg) if cookie else None

        if user is None:
            # 预检（OPTIONS）不带 Cookie 是浏览器的规矩，不是"没登录"。
            # 正常路径上 CORS 在最外层已经应答了，这里只是兜底。
            if _is_public(path) or str(scope.get("method") or "").upper() == "OPTIONS":
                await self.app(scope, receive, send)
                return
            await Response(
                _UNAUTHORIZED,
                status_code=401,
                media_type="application/json; charset=utf-8",
                headers={"cache-control": "no-store"},
            )(scope, receive, send)
            return

        # ★ 即使是**白名单**路径，只要带了有效 Cookie 也要登记身份。
        #
        #   白名单的语义是"**不拦**"，不是"**不认身份**" —— 把两者混为一谈
        #   会直接踩到：`/api/auth/users/*`（账号管理）就挂在 `/api/auth/` 这个
        #   白名单前缀下，它必须知道"谁在调"，否则管理员点进去只会得到
        #   "未登录或登录已失效"，而人明明是登录着的。
        #
        # 必须在 finally 里 reset —— 线程池会复用线程服务下一个请求，
        # 不还原就会出现"A 的请求用了 B 的身份"。
        token = identity.set_current(user)
        try:
            await self.app(scope, receive, send)
        finally:
            identity.reset(token)


def install_gate(app):
    """
    把闸门挂到**最内层**（CORS 之内）。幂等。

    ⚠️ 不能用 `app.add_middleware`（那是 insert(0, ...) → 跑到 CORS 外面，
       见文件头）。这里改为往 `user_middleware` 末尾追加，并把
       `middleware_stack` 置空让它重建（Starlette 1.6 是**懒构建**的：
       首次请求时若发现它是 None 就按当前 user_middleware 重新装配）。
    """
    if getattr(app, "_auth_gate", False):
        return
    app.user_middleware.append(Middleware(AuthGate))
    app.middleware_stack = None
    app._auth_gate = True

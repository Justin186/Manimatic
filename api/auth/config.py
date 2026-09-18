# -*- coding: utf-8 -*-
"""
账号体系的配置 —— 全部走 `MSB_AUTH_*` 环境变量，**不碰 api/config.py**。

与 `storyboard/vision.py::load_config` 同一套路：前缀独立、每次调用重读
（测试里改 env 立刻生效，不用重启进程）。

默认值按"**内网/本机演示**"定：注册需要邀请码、Cookie 不带 Secure。
要放到公网上，必须改的那几条写在 `ONLINE_CHECKLIST` 里。
"""

import os

# 放到公网前必须逐条过一遍的东西。
# 写在这里而不是散进文档：改配置的人第一眼看到的就是这个文件。
ONLINE_CHECKLIST = (
    "MSB_AUTH_ROOT_PASSWORD 设成强密码（或删掉它，让服务生成并看启动横幅）",
    "MSB_AUTH_COOKIE_SECURE=1（走 HTTPS；否则浏览器不会存这个 Cookie）",
    "MSB_AUTH_INVITE 换成你自己的强码（泄露 = 任何人都能注册进来烧你的 LLM 密钥）",
    "MSB_AUTH_DB 挂到持久卷并纳入备份（账号库丢了所有人都登不进来）",
)


def _env_str(name, default=""):
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else default


def _env_int(name, default):
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name, default=False):
    v = _env_str(name, "").lower()
    if not v:
        return default
    return v not in ("0", "false", "no", "off")


class AuthConfig:
    def __init__(self):
        # ---- 总开关 ----
        # 关掉 = 所有接口裸奔。**只为排查问题存在**，正常不该出现。
        self.enabled = _env_bool("MSB_AUTH_ENABLED", True)

        # ---- 预置的超级管理员 ----
        # ⚠️ 与"空库时网页 bootstrap 建管理员"那条路不同：预置账号不存在
        #    "谁先注册谁当管理员"的抢注窗口，也不依赖人记得去点那一下。
        self.root_email = _env_str("MSB_AUTH_ROOT_EMAIL", "admin@example.com")
        self.root_password = _env_str("MSB_AUTH_ROOT_PASSWORD", "")
        self.root_name = _env_str("MSB_AUTH_ROOT_NAME", "超级管理员")

        # ---- 注册 ----
        # 空串 = **注册关闭**。安全开关的缺失值只能是拒绝，不能是放行。
        self.invite = _env_str("MSB_AUTH_INVITE", "")

        # ---- 会话 ----
        self.session_days = max(1, _env_int("MSB_AUTH_SESSION_DAYS", 14))
        self.cookie_name = _env_str("MSB_AUTH_COOKIE", "msb_session")
        self.cookie_secure = _env_bool("MSB_AUTH_COOKIE_SECURE", False)

        # ---- 密码 ----
        # 600k 轮是 OWASP 2023 对 PBKDF2-HMAC-SHA256 的建议下限。
        # 它直接决定"登录一次要算多久"（本机实测 ~0.3s），测试里可调低。
        self.pbkdf2_iters = max(1000, _env_int("MSB_AUTH_PBKDF2_ITERS", 600000))
        self.min_password = max(6, _env_int("MSB_AUTH_MIN_PASSWORD", 8))

        # ---- 防暴破 ----
        self.max_fails = max(3, _env_int("MSB_AUTH_MAX_FAILS", 8))
        self.lock_minutes = max(1, _env_int("MSB_AUTH_LOCK_MINUTES", 15))

        # ---- 存储 ----
        self.db = _env_str("MSB_AUTH_DB", "output/_auth/auth.db")

        # ---- 老数据收编 ----
        # 首次建出 root 后，把"没有命名空间前缀"的老数据搬进 root 名下（幂等）。
        # 默认开：这是本机/内网场景，root 就是你自己，而"登录后一条会话都没有"
        # 这个观感比报错还糟。它是一次性、不可逆的**搬文件**，所以留了开关。
        self.adopt_legacy = _env_bool("MSB_AUTH_ADOPT_LEGACY", True)

    def as_dict(self):
        """给启动横幅与 /api/auth/config 用。**绝不回传任何密钥**。
        邀请码只回"有没有配"，不回值。"""
        return {
            "enabled": self.enabled,
            "register_open": bool(self.invite),
            "root_email": self.root_email,
            "session_days": self.session_days,
            "min_password": self.min_password,
            "cookie_secure": self.cookie_secure,
            "adopt_legacy": self.adopt_legacy,
            "db": self.db,
        }


def load_config():
    return AuthConfig()

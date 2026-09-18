# -*- coding: utf-8 -*-
"""
密码哈希与校验 —— 只用标准库。

================================================================================
为什么是 PBKDF2 而不是 bcrypt / argon2
================================================================================
后两者要装 C 扩展（Windows 上尤其容易翻车），而本项目的取舍一直是
"少一个依赖少一类环境坑"。`hashlib.pbkdf2_hmac` 是标准库、且是 OWASP
推荐列表里的算法，配 600k 轮足够。

================================================================================
三条不能"顺手优化"掉的规矩
================================================================================
1. **校验用 `hmac.compare_digest`，不能用 `==`**：`==` 是短路的，
   攻击者能靠响应时间差逐字节把正确哈希试出来。
2. **哈希格式自描述**：`pbkdf2_sha256$<轮数>$<盐>$<哈希>` 把参数写进值里，
   将来调高轮数时老密码**不需要重置**（按存着的轮数验，登录成功再平滑升级）。
3. **"用户不存在"也必须跑一次哈希**（`fake_verify`）：见 `verify` 的注释。
"""

import hashlib
import hmac
import os
import re
import time

from .config import load_config

_FORMAT = "pbkdf2_sha256"
_SALT_BYTES = 16
_RE = re.compile(r"^pbkdf2_sha256\$(\d+)\$([0-9a-f]+)\$([0-9a-f]+)$")


def hash_password(password, iters=None):
    """返回自描述格式的哈希串。**不存明文，也不存可逆形式**。"""
    cfg = load_config()
    rounds = int(iters or cfg.pbkdf2_iters)
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"{_FORMAT}${rounds}${salt.hex()}${dk.hex()}"


def verify(password, stored):
    """
    校验密码。

    ⚠️ 失败时**也要花掉同样的时间**，这是本函数最容易被人"优化"坏的地方：
    如果"用户不存在"直接 return False（0ms）而"密码错"要跑 PBKDF2（300ms），
    这个耗时差就等于把"登录失败不区分邮箱不存在/密码错"那条规矩白写了 ——
    攻击者拿它当"这个邮箱注册过吗"的探测器。

    所以调用方（router.login）在查不到用户时要调 `fake_verify()`，
    而不是跳过校验。
    """
    m = _RE.match(str(stored or ""))
    if not m:
        # 格式不对（老数据/手工改过）：也跑一次，避免又一条计时侧信道
        fake_verify(password)
        return False, False
    rounds, salt_hex, want = int(m.group(1)), m.group(2), m.group(3)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), rounds)
    got = dk.hex()
    ok = hmac.compare_digest(got, want)
    # 需要升级：存的轮数低于当前配置（只报给调用方，由它决定要不要重写）
    return ok, ok and rounds < load_config().pbkdf2_iters


def fake_verify(password):
    """按当前轮数跑一次无意义的 PBKDF2，只为把时间花掉。"""
    hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"), os.urandom(_SALT_BYTES),
        load_config().pbkdf2_iters)


def weak_password_reason(password, cfg=None):
    """返回拒绝原因（空串 = 通过）。判据刻意只说**最低**要求，不搞复杂度打分。"""
    cfg = cfg or load_config()
    p = str(password or "")
    if len(p) < cfg.min_password:
        return f"密码至少 {cfg.min_password} 位"
    if p.strip() == "":
        return "密码不能全是空格"
    # 只挡最糟的那一类，不要在这里拦"必须含大小写数字"——
    # 那种规则实测只会让人把 Password1 写在便利贴上。
    if p.lower() in ("12345678", "password", "11111111", "abcdefgh"):
        return "这个密码太常见了，换一个"
    return ""


def timed(func):
    """量一下哈希要多久 —— 只在启动自检里用（决定"登录一次卡不卡"）。"""
    t0 = time.time()
    func()
    return time.time() - t0

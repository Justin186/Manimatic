# -*- coding: utf-8 -*-
"""
LLM 配置的读写接口 —— 给设置页用。

================================================================================
为什么要有这一层（而不是让前端直接改 llm.local.json）
================================================================================
密钥落在**后端**（MathStoryboard/llm.local.json），前端只发一个模型档案名过来。
这不是洁癖：`web/.env.local` 里的 `NEXT_PUBLIC_*` 会被打进浏览器那份 JS，
把密钥放那儿等于公开。所以设置页能改的只有"用哪一档 / 增删哪一档"，
密钥始终由后端保管、**永不回传**。

================================================================================
三条设计约束
================================================================================
1. **密钥只进不出**。`GET` 只回 `has_key`（布尔），不回原文、也不回后四位 ——
   服务默认绑 127.0.0.1，但"本机"不等于"只有自己"，中间还隔着一个浏览器。
2. **改完必须能被下一次调用看到**。配置是每次调用 `resolve_config()` 现读文件的，
   所以不需要重启服务；但这一层写完要**回读校验**一次（见下面 save 的实现）。
3. **坏名字不许静默退回**。active 指向不存在的档案时 `resolve_config()` 会直接报错，
   这里不兜底 —— 兜底就等于"用户以为切了 A、实际在用 B"。
"""

import json
import os

from fastapi import APIRouter
from pydantic import BaseModel

from storyboard import llm

router = APIRouter()

# 这些键是"配置项"，其余（profiles/active/_注释）都是结构
_PROFILE_KEYS = ("provider", "base_url", "model", "api_key", "max_tokens",
                 "temperature", "timeout", "json_mode")


def _read_file():
    """读原始配置文件。读不出来就当成"还没有配置"（不抛，交给上层决定）。"""
    path = llm.config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:                                          # noqa: BLE001
        return {}


def _public(profile_name, prof, active):
    """
    一个档案对外长什么样。

    ⚠️ 这里**不能**出现 api_key 本身。用 has_key 表达"配好了没"就够了：
    设置页要展示的是"这一档能不能用"，不是"密钥是什么"。
    """
    return {
        "name": profile_name,
        "active": profile_name == active,
        "provider": prof.get("provider", ""),
        "base_url": prof.get("base_url", ""),
        "model": prof.get("model", ""),
        "has_key": bool(prof.get("api_key")),
        "max_tokens": prof.get("max_tokens"),
        "temperature": prof.get("temperature"),
        "json_mode": bool(prof.get("json_mode", True)),
        "headers": sorted((prof.get("headers") or {}).keys()),
        "note": str(prof.get("_desc") or "").strip(),
    }


def _snapshot():
    """当前配置的全貌（给设置页渲染用），不含任何密钥。"""
    raw = _read_file()
    profiles = {k: v for k, v in (raw.get("profiles") or {}).items()
                if isinstance(v, dict)}
    if not profiles:
        # 旧形态：整个文件就是一份配置。也把它当"一档"呈现，
        # 免得设置页对着一个能用的配置显示"没有配置"。
        if raw:
            clean = {k: v for k, v in raw.items() if not k.startswith("_")}
            return {"profiles": [_public("default", clean, "default")],
                    "active": "default", "legacy": True, "path": llm.config_path()}
        return {"profiles": [], "active": "", "legacy": False,
                "path": llm.config_path()}

    active = str(raw.get("active") or llm.DEFAULT_PROFILE)
    return {
        "profiles": [_public(n, profiles[n], active) for n in sorted(profiles)],
        "active": active,
        "legacy": False,
        "path": llm.config_path(),
    }


@router.get("/llm/profiles")
async def list_profiles():
    """列出所有模型档案 + 当前生效的那个。不含密钥。"""
    snap = _snapshot()
    # 顺带把"真正生效的值"也算一遍：profile/active 不一致、或某档缺少 key
    # 这类问题，只有 resolve_config() 说了算 —— 让设置页看到的是**生效结果**，
    # 而不是"文件里写的什么"（两者不一致时最容易骗人）。
    try:
        cfg = llm.resolve_config()
        snap["effective"] = {
            "profile": cfg.get("profile"),
            "model": cfg["model"],
            "provider": cfg.get("provider"),
            "max_tokens": cfg.get("max_tokens"),
            "has_key": bool(cfg.get("api_key")),
            "error": "",
        }
    except llm.LLMError as e:
        snap["effective"] = {"error": str(e)}
    return snap


class SwitchRequest(BaseModel):
    profile: str


@router.post("/llm/active")
async def set_active(req: SwitchRequest):
    """
    切换当前生效的档案。

    写文件 + **回读校验**：写完立刻用 resolve_config(profile=...) 试一次。
    光看写入成功没有意义 —— 名字写错、JSON 写坏，都要在这时候就报出来，
    而不是等用户下一次提问时才发现"怎么还是老模型"。
    """
    path = llm.config_path()
    raw = _read_file()
    profiles = raw.get("profiles") or {}
    if req.profile not in profiles:
        return {"ok": False, "error": f"没有这个档案：{req.profile}",
                "available": sorted(profiles)}

    try:
        cfg = llm.resolve_config(profile=req.profile)
    except llm.LLMError as e:
        return {"ok": False, "error": f"这一档配置有问题，没有切换：{e}"}

    raw["active"] = req.profile
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as e:
        return {"ok": False, "error": f"写入失败：{e}"}

    # 回读：确认落盘的内容真的能解析出这个档案
    check = _read_file()
    if check.get("active") != req.profile:
        return {"ok": False, "error": "写入后回读不一致，配置可能已损坏"}

    return {"ok": True, "active": req.profile, "model": cfg["model"],
            "profile": cfg.get("profile")}


class SaveProfileRequest(BaseModel):
    """新增或修改一档。`profile` 为空 = 新增（名字取下面的 name）。"""
    name: str
    profile: str = ""              # 非空 = 修改已存在的这一档（改名由 name 决定）
    provider: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""              # 留空 = 不改（避免每次保存都要重打密钥）
    max_tokens: int | None = None
    temperature: float | None = None
    json_mode: bool = True
    headers: dict | None = None
    note: str = ""


@router.post("/llm/profile")
async def save_profile(req: SaveProfileRequest):
    """
    新增 / 修改一个模型档案（设置页的"加一个模型"）。

    ⚠️ api_key 留空 = **保持原值**，不是"清空成空的"。这是刻意的：
    前端拿不到旧密钥原文（我们从不回传），如果留空就当清空，
    用户每改一次 model 就得重打一遍密钥。要清空请显式传一个清空标记
    （目前不提供 —— 没人需要把一个能用的档改成没密钥）。
    """
    name = (req.name or "").strip()
    if not name:
        return {"ok": False, "error": "档案名不能为空"}
    if any(c in name for c in (" ", "\t", "/", "\\")):
        return {"ok": False, "error": "档案名里不能有空格或斜杠"}

    raw = _read_file()
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict):
        # 旧形态：先把它收编成 relay/direct 那种形态，否则新增会覆盖掉老配置
        return {"ok": False,
                "error": "当前是旧版扁平配置（没有 profiles），"
                         "请先在 llm.local.json 里改成 profiles/active 形态再加档案"}

    target = (req.profile or name).strip()
    existing = profiles.get(target)
    if req.profile and not isinstance(existing, dict):
        return {"ok": False, "error": f"要修改的档案不存在：{target}"}

    prof = dict(existing or {})
    if req.provider:
        prof["provider"] = req.provider
    if req.base_url:
        prof["base_url"] = req.base_url.rstrip("/")
    if req.model:
        prof["model"] = req.model
    if req.api_key:
        prof["api_key"] = req.api_key          # 留空保持原值
    if req.max_tokens:
        prof["max_tokens"] = int(req.max_tokens)
    if req.temperature is not None:
        prof["temperature"] = float(req.temperature)
    prof["json_mode"] = bool(req.json_mode)
    if req.headers is not None:
        prof["headers"] = {str(k): str(v) for k, v in req.headers.items() if v}
    if req.note:
        prof["_desc"] = req.note

    for k in ("api_key", "base_url", "model"):
        if not prof.get(k):
            return {"ok": False, "error": f"缺少必填项：{k}"}

    if req.profile and req.profile != name:
        del profiles[target]                   # 改名
    profiles[name] = prof
    raw["profiles"] = profiles
    raw.setdefault("active", name)

    # 先试一遍能不能解析（拿掉 key 会解析失败，所以临时带上）
    path = llm.config_path()
    backup = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            backup = f.read()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
            f.write("\n")
        llm.resolve_config(profile=name)       # 回读校验
    except (llm.LLMError, OSError) as e:
        if backup is not None:                 # 校验不过就还原，绝不留下半坏的配置
            with open(path, "w", encoding="utf-8") as f:
                f.write(backup)
        return {"ok": False, "error": f"保存后校验失败，已还原：{e}"}

    return {"ok": True, "name": name}

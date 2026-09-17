# -*- coding: utf-8 -*-
"""
图片输入（读题目照片）—— 图片解码/校验/降采样 + 能力闸口 + 多模态 messages。

================================================================================
为什么这件事能成立：底层本来就是透传的
================================================================================
本模块 `chat()` / `chat_stream()` 把 messages **原样**塞进 OpenAI 兼容的请求体：

    payload = {"model": ..., "messages": messages, ...}

也就是说底层从没假设过 `content` 一定是字符串。所以只要构造出标准的 content
数组形态（text + image_url 两段），多模态请求天然就能发出去。

缺的只是"上层没有地方能表达带图"：`build_user_prompt_multi()` 一律返回 str。
所以 `llm.stream_storyboard()` 现在多了一个 `images` 参数，本轮 user content
由本模块的 `to_content()` 构造 —— 与普通路径共用同一套重试/校验/留档，
**不再复刻一份**（早期以插件形式存在时复刻过，结果和主路径漂移了三处：
temperature 被写死、漏了 meta.storyboard、漏了 diff_scenes 对账。教训见
HANDOFF §8.68）。

================================================================================
三个必须知道的设计取舍
================================================================================
1. **图片先降采样**（`normalize`）。直接透传手机照片有两个必然后果：
   请求体十几 MB 被网关 413、视觉模型按图块计费导致费用数倍。
   降采样是"既省钱又不掉准确率"，不是妥协。

2. **模型不支持图片时明确报错，不静默降级**（`ensure_can_read_images`）。
   悄悄丢掉图片继续跑，用户会以为图被读到了 —— 那是最糟的一种"看起来成功了"。

3. **图片不落盘、不进历史**。会话文件里只存文字和图片摘要（尺寸/指纹）。
   一张 base64 图几百 KB，存进会话会让恢复接口返回几 MB 的 JSON。
   代价是刷新后看不到"当时发的那张图" —— 这是诚实的降级。

================================================================================
为什么用 Pillow（而不是自己解析 PNG/JPEG 头）
================================================================================
`pillow` 是 **manim 自己的硬依赖**（`pip show manim` 的 Requires 里就有），
而 manim 是本项目的核心依赖 —— 所以这里 `import PIL` **没有引入任何新依赖**。
自己写解码器只会引入"某些 PNG 变体解不开"这类问题，没有任何好处。
"""

import base64
import binascii
import hashlib
import io
import os
import re

from PIL import Image, ImageOps, UnidentifiedImageError

# 关掉 Pillow 的"解压炸弹"提示转异常行为，改成我们自己在下面显式判像素数。
# 默认行为是在超过 MAX_IMAGE_PIXELS 时只发 Warning（不阻断），
# 而我们要的是一个**明确的、可读的**拒绝理由，所以自己控。
Image.MAX_IMAGE_PIXELS = None

# 像素总数硬上限（约 8000×8000）。超过就拒绝：
# 一张 1 亿像素的图解码本身就要几百 MB 内存，属于"传一张图把服务打挂"的攻击面。
_MAX_PIXELS = 64_000_000

# 声明式魔数校验：**不信任调用方给的 mime**，只看文件头真实字节。
# 前端声明 image/png 而实际传别的东西（或传坏文件）时，靠这个拦下来。
_MAGIC = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[-\w.+/]+)?;base64,(?P<b64>.*)$", re.S)


# ==============================================================================
# 配置（环境变量前缀 MSB_VI_）
# ==============================================================================

def _env_str(name, default=""):
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else default


def _env_int(name, default):
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_bool(name, default):
    v = _env_str(name, "").lower()
    if not v:
        return default
    return v not in ("0", "false", "no", "off")


# 「当前模型能不能读图」这一判断有三种态度，**必须由使用者显式选**，
# 因为这个能力无法在本地真正探知（能力在厂商侧）。详见 ensure_can_read_images()。
ALLOW_AUTO = "auto"        # 保守黑名单：明确是纯文本模型就直接拒绝，其余放行并让接口裁决
ALLOW_ALWAYS = "always"    # 一律放行（已知自己的模型支持视觉、或黑名单误伤时用）
ALLOW_NEVER = "never"      # 一律拒绝（不想让这个能力被误用；或部署环境不许传图）

VALID_ALLOW = (ALLOW_AUTO, ALLOW_ALWAYS, ALLOW_NEVER)


class VisionConfig:
    """
    图片输入的运行参数。**每次调用现读环境变量**（同项目 resolve_config() 的作风）。

    ⚠️ 这些默认值是**按"一张手机拍的题目照片"来定的**，不是按"任意大图"定的：
       现代手机一张照片 3~8MB、长边 3000~4000px，直接塞进请求体的后果是
       ① 请求体几十 MB，网关多半直接 413；② 视觉模型按图块计费，图越大越贵。
    """

    def __init__(self):
        # auto / always / never。默认 auto：既能挡住"拿纯文本模型读图"这种必然失败，
        # 又不会因为黑名单不全而误伤真正支持的模型。
        self.allow = _env_str("MSB_VI_ALLOW", ALLOW_AUTO).lower()
        if self.allow not in VALID_ALLOW:
            # 写了不认识的值**不静默退回**：那会让人以为设上了。退回默认并留痕。
            print(f"      [VI] MSB_VI_ALLOW={self.allow!r} 不认识，"
                  f"已按 {ALLOW_AUTO} 处理（可选：{', '.join(VALID_ALLOW)}）", flush=True)
            self.allow = ALLOW_AUTO

        # 留空 = 用项目当前生效的那一档（api_config.LLM_PROFILE）。
        # 为什么单独留这两个开关：**读图和出分镜可以是两个模型**。
        # 常见情形是当前档配的是便宜/快的纯文本模型（出 JSON 够用），
        # 而读图需要另一个支持视觉的档。不提供开关，用户只能去改
        # llm.local.json 的 active、跑完再改回来 —— 中途忘了改回去会导致
        # 后面所有生成都换了模型（静默的行为变化）。
        self.profile = _env_str("MSB_VI_PROFILE", "")
        self.model = _env_str("MSB_VI_MODEL", "")

        # 单次请求最多几张图。默认 4：题目照片一张就够，给 4 张是留"多页题目 /
        # 题干+图形分拍"的余地。再多就是误用（费用成倍涨，收益很低）。
        self.max_images = max(1, _env_int("MSB_VI_MAX_IMAGES", 4))
        # 降采样后的长边上限（px）。1568 是视觉模型常见的最优输入边长量级 ——
        # 再大通常只是按图块多花钱，识别准确率几乎不再提升。
        self.max_dim = max(256, _env_int("MSB_VI_MAX_DIM", 1568))
        # 单张图编码后的字节上限。超了就降质量/降尺寸，确保请求体不会失控。
        self.max_bytes = max(64 * 1024, _env_int("MSB_VI_MAX_BYTES", 4 * 1024 * 1024))
        # 重编码时的 JPEG 质量（仅当必须牺牲体积时使用）
        self.jpeg_quality = min(95, max(50, _env_int("MSB_VI_JPEG_QUALITY", 85)))
        # 是否允许请求体里直接给 http(s) 图片地址（而不是 base64）。
        # ⚠️ **默认关**：让服务端按用户给的地址去发请求，等于把"服务端能访问到的内网"
        #    暴露成一个可探测面（SSRF）。前端只发 base64，这条是给脚本调用留的口子，
        #    要用的人显式打开。
        self.allow_url = _env_bool("MSB_VI_ALLOW_URL", False)

        # ---- 调试 ----
        self.verbose = _env_bool("MSB_VI_VERBOSE", True)

    def as_dict(self):
        return {
            "allow": self.allow,
            "profile": self.profile,
            "model": self.model,
            "max_images": self.max_images,
            "max_dim": self.max_dim,
            "max_bytes": self.max_bytes,
            "jpeg_quality": self.jpeg_quality,
            "allow_url": self.allow_url,
            "verbose": self.verbose,
        }


def load_config():
    """每次调用现读环境变量 —— 与项目 resolve_config() 的作风一致，改配置不用重启。"""
    return VisionConfig()


# ==============================================================================
# 异常
# ==============================================================================

class ImageRejected(Exception):
    """图片不合法（格式不认识 / 太大 / 张数超限 / 结构损坏）。消息是**给用户看的**。"""


class VisionConfigError(Exception):
    """读图相关的配置错误（档名不存在等）。消息是给用户看的。"""


class VisionUnsupported(Exception):
    """当前模型不支持图片输入。消息是给用户看的，必须含"下一步怎么办"。"""


# ==============================================================================
# 1) 解码 / 校验 / 降采样
# ==============================================================================

def _sniff(head):
    """按真实文件头判断格式。认不出来返回 None。"""
    for magic, name in _MAGIC:
        if head.startswith(magic):
            return name
    # WEBP 是 RIFF 容器：前 4 字节 RIFF + 第 8~12 字节 WEBP
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def _decode_b64(payload, label):
    """
    解 base64。容忍 URL-safe 变体和缺失的 padding —— 前端用 btoa 还是 Buffer
    还是别的方式编码，我们不预设，全部收下。
    """
    s = str(payload or "").strip()
    if not s:
        raise ImageRejected(f"{label}：图片内容是空的")
    # 去掉换行（有些客户端会按 76 字符折行）
    s = re.sub(r"\s+", "", s)
    s = s.replace("-", "+").replace("_", "/")
    pad = len(s) % 4
    if pad:
        s += "=" * (4 - pad)
    try:
        return base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError) as e:
        raise ImageRejected(f"{label}：图片 base64 解不开（{e}）")


def _load_image(raw, label):
    """
    字节 → PIL Image。同时做**魔数校验 + 像素数上限 + EXIF 方向纠正**。

    EXIF 那一步不能省：手机竖拍的照片，像素在文件里其实是横的，靠 EXIF 的
    Orientation 标记告诉查看器"要转 90°"。不解 EXIF 直接送模型，模型看到的
    是一张**躺着的题目照片** —— 文字识别质量会明显下降，而且不报任何错。
    """
    if not raw:
        raise ImageRejected(f"{label}：图片是空的")
    kind = _sniff(raw[:16])
    if kind is None:
        raise ImageRejected(
            f"{label}：这不是支持的图片格式（只认 PNG / JPEG / WEBP / GIF / BMP）。"
            f"iPhone 的 HEIC 请先转成 JPEG 再上传"
        )
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ImageRejected(f"{label}：图片文件损坏或无法解码（{e}）")

    w, h = img.size
    if w * h > _MAX_PIXELS:
        raise ImageRejected(
            f"{label}：图片像素太多（{w}×{h}）。请先缩小到 "
            f"{int(_MAX_PIXELS ** 0.5)}×{int(_MAX_PIXELS ** 0.5)} 以内再上传"
        )
    if w < 8 or h < 8:
        raise ImageRejected(f"{label}：图片太小了（{w}×{h}），看不清题目")

    # 按 EXIF 把图转正（见上面的注释）
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:                                       # noqa: BLE001
        pass            # EXIF 坏掉不该让整张图传不进去
    return img


def _flatten(img):
    """
    带透明通道 / 调色板 / 灰度 → RGB（JPEG 可编码的形态）。

    ⚠️ alpha 必须**合成到白底**再转 RGB。直接 `convert("RGB")` 的话，
       透明像素的 RGB 分量通常是 (0,0,0)，于是透明底截图会变成**黑底**，
       黑字直接消失。这是"图传上去了、模型说看不到文字"最隐蔽的一种成因。
    """
    if img.mode in ("RGB", "L"):
        return img.convert("RGB")
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        # 用 alpha 当遮罩贴上白底
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def _to_bytes(img, fmt, quality):
    buf = io.BytesIO()
    if fmt == "jpeg":
        img.save(buf, format="JPEG", quality=quality, optimize=True)
    else:
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _fit_within_bytes(img, cfg, label):
    """
    把图压到 cfg.max_bytes 以内。策略：**先降质量，再降尺寸**。

    为什么是这个顺序：降质量只影响 JPEG 的压缩率，**图片的像素尺寸不变** ——
    而题目照片里最关键的信息是文字的可辨识度，那主要由尺寸决定。
    所以先牺牲压缩质量（几乎不损失可读性），实在不行才动尺寸。

    循环有硬上限（最多 8 轮），保证一定终止 —— 在"压不下去就无限重试"这类
    循环上卡死整个服务，是最不该发生的事故。
    """
    data = _to_bytes(img, "jpeg", cfg.jpeg_quality)
    if len(data) <= cfg.max_bytes:
        return data

    # 第一段：降质量
    q = cfg.jpeg_quality
    for _ in range(4):
        q = max(55, q - 12)
        data = _to_bytes(img, "jpeg", q)
        if len(data) <= cfg.max_bytes:
            return data

    # 第二段：缩尺寸（等比，每轮 0.8）
    cur = img
    for _ in range(4):
        nw = max(64, int(cur.width * 0.8))
        nh = max(64, int(cur.height * 0.8))
        cur = cur.resize((nw, nh), Image.LANCZOS)
        data = _to_bytes(cur, "jpeg", q)
        if len(data) <= cfg.max_bytes:
            return data

    raise ImageRejected(
        f"{label}：图片压缩后仍超过 "
        f"{cfg.max_bytes // 1024 // 1024}MB 上限，请手动缩小后再上传"
    )


def normalize(raw_b64, cfg, label="图片", fmt_hint=""):
    """
    一张上传的图片 → 规范化后的 data URL。

    Args:
        raw_b64: 纯 base64，或 `data:image/...;base64,...` 形式的 data URL
        fmt_hint: 调用方声明的 mime（只用于日志，**不参与判断**）

    Returns:
        dict: {
            "data_url": "data:image/jpeg;base64,..."  可直接塞进 image_url.url
            "bytes": 编码后字节数,
            "width"/"height": 降采样后的尺寸,
            "sha256": 内容指纹（去重 / 日志用，**绝不记 base64 本身**）,
            "original_bytes": 原始字节数,
            "source": "upload",
        }

    Raises:
        ImageRejected: 格式/体积/尺寸不合法，消息可直接展示给用户
    """
    m = _DATA_URL_RE.match(str(raw_b64 or "").strip())
    payload = m.group("b64") if m else raw_b64
    raw = _decode_b64(payload, label)
    original_bytes = len(raw)

    img = _load_image(raw, label)

    # 降采样到长边上限（只缩不放：小图不放大，放大没有信息增量、只会更贵）
    longest = max(img.size)
    if longest > cfg.max_dim:
        ratio = cfg.max_dim / float(longest)
        img = img.resize((max(1, int(img.width * ratio)),
                          max(1, int(img.height * ratio))), Image.LANCZOS)

    img = _flatten(img)

    # 统一输出 JPEG：体积比 PNG 小一个量级，且视觉接口普遍支持。
    # （题目照片/截图都没有必须保留无损的需求，选 JPEG 是纯收益。）
    data = _fit_within_bytes(img, cfg, label)

    sha = hashlib.sha256(data).hexdigest()
    b64 = base64.b64encode(data).decode("ascii")
    return {
        "data_url": f"data:image/jpeg;base64,{b64}",
        "bytes": len(data),
        "width": img.width,
        "height": img.height,
        "sha256": sha,
        "original_bytes": original_bytes,
        "source": "upload",
    }


def normalize_url(url, cfg, label="图片"):
    """
    远程图片地址 → 规范化结果。**默认关闭**（见 cfg.allow_url）。

    为什么默认关：让服务端按用户给的地址去发请求，等于把"服务端能访问到的内网"
    暴露成一个可探测面（SSRF）。想用的人显式打开。
    """
    import urllib.error
    import urllib.request

    u = str(url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        raise ImageRejected(f"{label}：只接受 http/https 的图片地址")
    req = urllib.request.Request(u, method="GET")
    # 有些图床会对非浏览器 UA 直接 403（本项目已经踩过一次 Cloudflare 1010）
    req.add_header("User-Agent", "Mozilla/5.0 (compatible; Manimatic/1.0)")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            # 只读前 max_bytes*2，避免对端用超大文件把内存吃光
            raw = resp.read(cfg.max_bytes * 2)
    except Exception as e:                                  # noqa: BLE001
        raise ImageRejected(f"{label}：下载失败（{e}）")

    b64 = base64.b64encode(raw).decode("ascii")
    out = normalize(b64, cfg, label=label)
    out["source"] = "url"
    out["origin"] = u
    return out


def normalize_many(items, cfg, allow_url=None):
    """
    一组图片输入 → 规范化列表。**超过上限直接报错，不静默截断。**

    为什么不做"只取前 N 张"：用户传了 6 张、系统悄悄只用 4 张，结果是他以为
    第 5、6 页的题干被读到了 —— 而这属于"静默失效"，正是本项目最忌讳的。
    宁可明确报错让他知道。

    Returns:
        (list[dict], list[str]) —— (成功规范化后的图片, 人类可读的警告)
    """
    if allow_url is None:
        allow_url = cfg.allow_url
    items = [it for it in (items or []) if it]
    if len(items) > cfg.max_images:
        raise ImageRejected(
            f"一次最多 {cfg.max_images} 张图片，收到 {len(items)} 张。"
            f"请精简后重试（题目照片通常 1 张就够）"
        )

    out, warnings = [], []
    for i, it in enumerate(items):
        label = f"第 {i + 1} 张图"
        if isinstance(it, str):
            it = {"url": it} if it.lower().startswith(("http://", "https://")) else {"data": it}
        if not isinstance(it, dict):
            raise ImageRejected(f"{label}：格式不对（应为 {{data}} 或 {{url}}）")

        url = str(it.get("url") or "").strip()
        data = it.get("data") or it.get("base64") or it.get("data_url")
        if url:
            if not allow_url:
                # 不静默跳过：明确告诉调用方"地址方式被关了"，否则他会以为图没传上去
                raise ImageRejected(
                    f"{label}：本服务已关闭「按 URL 取图」（MSB_VI_ALLOW_URL=0），"
                    f"请改为 base64 上传"
                )
            out.append(normalize_url(url, cfg, label=label))
        elif data:
            out.append(normalize(data, cfg, label=label,
                                 fmt_hint=str(it.get("mime") or "")))
        else:
            raise ImageRejected(f"{label}：没有内容（需要 data 或 url 字段）")

    if out:
        total = sum(x["bytes"] for x in out)
        warnings.append(f"{len(out)} 张图，压缩后共 {total // 1024}KB")
    return out, warnings


# ==============================================================================
# 2) 能力闸口：当前模型能不能读图
# ==============================================================================
# 为什么这件事必须显式做：模型是否支持视觉，**能力在厂商侧，本地无法真正探知**。
# 我们只能拿模型名去猜（黑名单），其余交给接口裁决。而"交给接口裁决"这一步的
# **错误可读性**很差：纯文本模型收到带图请求，厂商通常只回一句含糊的
# `400 messages[0].content must be a string`，用户完全看不出是模型不支持图片。
# 按项目一贯的取舍（绝不静默失败 + 错误必须指向下一步），这里在发请求前先拦一道。
#
# ⚠️ 黑名单**必然不全**（新模型一直在出、命名也不统一），所以：
#    默认策略 auto 是"命中 → 拒绝；**没命中 → 放行，让接口裁决**"
#    （而不是"没命中就拒绝"—— 那会让新出的视觉模型全被误伤）。
# ==============================================================================
# ⚠️⚠️ 2026-09-17：这个黑名单**误判过一次，代价是"图片功能被自己人关掉"**
# ==============================================================================
# 实情：`deepseek-chat` 一度被列在这里，用户上传图片时被闸口拒绝，提示
# "当前模型不支持图片输入"。但**实测它完全能读图**（0.8s，准确读出图里的
# 方程 `x^2 - 5x + 6 = 0`；`deepseek-flash` 同样能读）。
# 也就是说：不是模型不支持，是**我们凭一个过时的印象拦住了自己能用的功能**。
#
# 教训（这条比黑名单本身重要）：
#   **"终端用户拿不到这个能力"的可能性，比"传图过去吃一个可读的 400"严重得多。**
#   黑名单只该放"确定不支持**且**厂商错误信息极难读"的型号；拿不准的一律不放。
#   判据应当是**实测**，不是"我觉得这个型号应该没有"。想验证某个型号能不能读图：
#   绕过 ensure_can_read_images() 直接发一个多模态请求即可（见 HANDOFF §8.70）。
#
# 因此这里清掉了两个已被实测**证伪**的条目（deepseek-chat / deepseek-flash 系），
# 只保留明确无视觉能力的那些。
_TEXT_ONLY_MARKERS = (
    # ---- DeepSeek：**故意一条都不放** ----
    # ⚠️ 这里原本写着 "deepseek-chat", "deepseek-reasoner", "deepseek-r1" 等，
    #    依据是"DeepSeek 的对话/推理模型没有视觉，VL 是单独系列"。
    #    2026-09-17 实测**证伪**：deepseek-chat 与 deepseek-flash 都能准确读图
    #    （0.8s / 1.2s 读出 `x^2 - 5x + 6 = 0`）。
    #    保留那些条目 = 用户传图被自己人挡下，还编了个"模型不支持"的理由。
    #
    #    注意 deepseek-v4-pro 实测**确实**不能读图（模型自己回"无法识别图片内容"），
    #    但它**不在**这个名单里 —— 这正是期望行为：放行、让它自己说话，
    #    好过我们凭猜测把它拦下。
    #
    # ---- 以下均为「**未实测**」的保守条目 ----
    # 保留的唯一理由是"省一次注定失败的往返"，**不代表确定不支持**。
    # 任何一条被实测证伪（模型其实能读）就删掉它；拿不准时**宁可删**。
    #
    # ⚠️ 动手改这一块时注意：别把"同一批"里的条目一起删掉。
    #    本次修复只该删 deepseek 系（实测证伪），而 llama-3/llama2 是**另一件事** ——
    #    它们没被实测过，删掉会放行一批真·纯文本模型（实测过：删掉 "llama-3"
    #    之后 llama-3-70b 就被放行了，那是个回归）。
    "llama-2", "llama2", "llama-3", "llama3",
    "mixtral", "mistral-7b",
    "yi-", "baichuan", "internlm",
    "gemma-2", "gemma2", "phi-3", "phi3",
    "chatglm",
)


# ==============================================================================
# 实测白名单：**最高优先级**，压过下面的所有猜测
# ==============================================================================
# 为什么需要它：黑名单是"按名字猜"，猜错一次就等于把功能关掉（见上面的教训）。
# 而"实测过能读图"是**事实**，不该在下一次有人整理黑名单时被重新猜到黑名单里去。
#
# 用法：手工验证某型号能读图后，把型号子串register在这里 + 注明验证日期与证据。
# （验证方法：绕过 ensure_can_read_images 直接发一个多模态请求，见 HANDOFF §8.70）
_VERIFIED_VISION = (
    ("deepseek-chat", "2026-09-17 实测 0.8s 准确读出图中方程"),
    ("deepseek-flash", "2026-09-17 实测 1.2s 准确读出图中方程"),
)


def _verified(model):
    """实测过能读图的型号 → (True, 证据)；否则 (False, "")。"""
    m = str(model or "").lower()
    for sub, why in _VERIFIED_VISION:
        if sub in m:
            return True, why
    return False, ""


def _looks_text_only(model):
    """
    这个模型**大概**读不了图吗？纯启发式判断，**只用于省一次注定失败的往返**。

    ============================================================================
    闸口该在什么时候拦：判据是"接口自己的报错**可不可读**"，不是"模型行不行"
    ============================================================================
    这条判据是本项目踩坑之后才想清楚的（见上面那段教训）：

      · 老式纯文本模型（llama / mistral 那一代）收到带图请求，接口回的是
        `400 messages[0].content must be a string` 之类**看不懂**的话 ——
        这时候拦下有价值（我们能把话说清楚，还给出下一步）。

      · 而实测 `deepseek-v4-pro` 失败时，接口**自己就说得很清楚**：
        模型直接回"无法识别图片内容"。这种情况闸口没有增益 ——
        拦下反而多一层误判风险（万一它哪天支持了呢，用户就被我们关在门外了）。

    所以：**误拒的代价（用户彻底失去功能 + 拿到误导提示）远大于误放
    （多花一次往返，拿一句能读的报错）**。宁放勿拦。
    """
    m = str(model or "").lower()
    if not m:
        return False
    # 0) **实测能读图的优先放行** —— 唯一基于事实的一层，压过所有猜测。
    if _verified(m)[0]:
        return False
    # 1) 名字里"明确是视觉"的特征也直接放行 —— 必须靠前，
    #    否则 "qwen-vl-plus" 会因为包含 "qwen-" 被黑名单误伤。
    if re.search(r"(-vl|-vision|-visual|-v\b|-omni|gpt-4o|gpt-4\.1|claude-3|claude-4|"
                 r"gemini|glm-4v|qwen-vl|internvl|llava|pixtral|step-1v|"
                 r"ernie-4\.0-turbo-vl|doubao-vision|hunyuan-vision)", m):
        return False
    # 2) 最后才是黑名单（**必然不全，所以没命中就放行**）
    return any(k in m for k in _TEXT_ONLY_MARKERS)


def resolve_vision_config(base_cfg, vcfg, provider=None, base_url=None):
    """
    在项目已解析的 cfg 基础上，叠加"读图专用档"。

    优先级：显式档（vcfg.profile/model）> 项目当前 cfg。
    **只覆盖 profile 相关的那几个字段**，其余（temperature/json_mode/headers/
    timeout…）保持项目原样 —— 那些是"调用策略"，不该被读图这个动作改掉。

    为什么不直接复用 resolve_config(profile=...) 重新解析一遍：那会丢掉调用方
    已经定好的 model/provider 覆盖，也会多读一遍文件。这里要的只是"换个档案"，
    语义比"重新解析"窄，就该用窄的实现。
    """
    if not (vcfg.profile or vcfg.model):
        return base_cfg, ""

    from . import llm

    kw = {}
    if vcfg.profile:
        kw["profile"] = vcfg.profile
    if vcfg.model:
        kw["model"] = vcfg.model
    if base_url:
        kw["base_url"] = base_url
    if provider:
        kw["provider"] = provider

    try:
        cfg = llm.resolve_config(**kw)
    except llm.LLMError as e:
        # 档名写错必须报出来（resolve_config 本身也是这个态度），
        # 不能悄悄用回原来那一档 —— 那会让用户以为图是用视觉模型读的。
        raise VisionConfigError(f"读图专用模型档不可用：{e}")
    return cfg, f"读图档 {vcfg.profile or vcfg.model}"


def ensure_can_read_images(cfg, vcfg):
    """
    发请求前的闸口。通过返回 None，不通过抛 VisionUnsupported。

    三种策略（MSB_VI_ALLOW）：
        never  一律拒绝 —— 部署环境不许传图时用
        always 一律放行 —— 明知模型支持、但名字不在白名单里时用
        auto   黑名单命中 → 拒绝；否则放行，交给接口裁决（默认）
    """
    if vcfg.allow == ALLOW_NEVER:
        raise VisionUnsupported(
            "本服务已关闭图片输入（MSB_VI_ALLOW=never）。"
            "去掉这个环境变量即可启用"
        )
    if vcfg.allow == ALLOW_ALWAYS:
        return

    model = str(cfg.get("model") or "")
    if _looks_text_only(model):
        raise VisionUnsupported(
            f"当前模型 `{model}` 不支持图片输入，无法读题。\n"
            f"两个办法（任选其一）：\n"
            f"  1) 在设置页把模型换成支持视觉的（名字里通常带 vl / vision，"
            f"或 gpt-4o / claude-3 这一代）；\n"
            f"  2) 不想换默认模型的话，用环境变量给读图单独指定一个档：\n"
            f"     MSB_VI_PROFILE=<llm.local.json 里那个支持视觉的档案名>\n"
            f"  若你确定这个模型其实支持视觉（黑名单误判），"
            f"设 MSB_VI_ALLOW=always 即可跳过这层判断"
        )


# 把"接口层报的不支持图片"翻译成人话。
# 黑名单不可能全，所以一定会有"没被拦住、但接口拒绝了"的情况。
# 厂商的原话通常非常含糊，用户看不出问题在哪。这里按关键词识别并补充说明。
_UNSUPPORTED_HINTS = (
    "content must be a string", "must be a string",
    "image_url", "image url", "invalid_content", "unsupported content",
    "does not support image", "not support vision", "multimodal",
    "content type", "invalid message", "vision",
)


def explain_api_error(err_text, model):
    """
    接口报错 → 如果是"不支持图片"，给出人话版本；否则原样返回。

    返回 (是否是不支持图片, 给用户看的消息)。
    """
    s = str(err_text or "")
    low = s.lower()
    if any(h in low for h in _UNSUPPORTED_HINTS):
        return True, (
            f"接口拒绝了这次带图请求，看起来是模型 `{model}` 不支持图片输入。\n"
            f"请换一个支持视觉的模型，或用 MSB_VI_PROFILE 给读图单独指定一个档。\n"
            f"--- 接口原始信息 ---\n{s[:800]}"
        )
    return False, s


# ==============================================================================
# 3) 多模态 messages 构造
# ==============================================================================
# 标准形态（OpenAI 兼容，各家基本一致）：
#     {"role": "user", "content": [
#         {"type": "text",      "text": "...题目要求..."},
#         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
#     ]}
#
# ⚠️ **图片放在文字之前**：实测（以及各家文档的建议）是把图放前面 ——
#    模型先"看"到图，再读要求，对"按图里的第 2 问作答"这类指令的遵从度更好；
#    反过来时有模型会先按文字建立预期、再拿图去套，容易漏读图里的细节。

def to_content(text, images):
    """
    拼一个 user content。

    images 为空时**返回纯字符串**而不是"只有一段 text 的数组"：
        · 纯文本模型收到数组形态的 content 会直接 400（这是兼容性最好的一条路）
        · 无图时保持和项目其它调用完全同形的请求体，也让 LLM 留档的指纹更稳定
    """
    text = str(text or "")
    if not images:
        return text
    parts = [{"type": "image_url", "image_url": {"url": img["data_url"]}}
             for img in images]
    parts.append({"type": "text", "text": text})
    return parts


def user_text_with_image_note(text, images):
    """
    给模型的文字部分。图片已经作为独立的 content 段传过去了，这里只负责
    **说明"这是一张图"以及该怎么用它** —— 不说明的话，模型有时会把图片
    当成装饰、仍然只按文字回答（它看不到"有图"这件事的显式提示）。

    用户自己写的补充要求（如"只讲第二问"）原样保留，放在最后 —— 那是最强的指令，
    放末尾更符合"指令越靠后越管用"的经验。
    """
    text = str(text or "").strip()
    if not images:
        return text

    n = len(images)
    head = (
        f"我上传了 {n} 张图片，内容是**数学题**（可能是拍照的试卷、"
        f"课本页面或屏幕截图）。请先仔细读图，把图里的题目抄准"
        f"（函数、方程、几何图形、数字都要认对），再按上面的规范设计分镜 JSON。\n"
        f"⚠️ 如果图片里的题目不完整或看不清，**不要猜**："
        f"把能看清的部分做完，并在 brief 里说明哪里看不清。\n"
    )
    if text:
        return f"{head}\n另外，我对这道题还有这些要求：\n{text}"
    return head


def describe_images(images):
    """
    图片的可读摘要，用于日志与落盘（**绝不包含 base64**）。

    为什么要单独一个函数：一张 base64 图动辄几百 KB，任何一处把它打进日志或
    写进会话文件，都会让日志文件爆炸、让会话恢复接口返回一个几 MB 的 JSON。
    所以"记录图片"这件事统一走这里，只记尺寸和指纹。
    """
    if not images:
        return []
    return [
        {
            "sha256": img.get("sha256", "")[:16],
            "w": img.get("width"),
            "h": img.get("height"),
            "kb": round((img.get("bytes") or 0) / 1024, 1),
            "source": img.get("source", "upload"),
        }
        for img in images
    ]

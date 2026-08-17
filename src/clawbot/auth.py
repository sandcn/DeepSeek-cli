"""扫码登录与凭证管理 — 微信 ClawBot iLink 登录流程。

流程：
1. GET /ilink/bot/get_bot_qrcode 获取二维码（qrcode + qrcode_img_content）
2. 二维码保存为本地图片并打印提示
3. GET /ilink/bot/get_qrcode_status 轮询扫码状态，status=confirmed 时拿到 bot_token
4. 凭证缓存到 ~/.chat_config/clawbot_cred.json（token/base_url/saved_at）

iLink 连接有效期 24 小时，到期前需重新扫码（自动重连由 runner 负责）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import Callable, Optional

from ..config.defaults import CONFIG_DIR
from .client import IlinkClient

_logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────
CRED_FILE = CONFIG_DIR / "clawbot_cred.json"
QR_FILE = CONFIG_DIR / "clawbot_qrcode.png"
SESSION_DURATION = 24 * 3600      # iLink 会话有效期（秒）
QR_SCAN_TIMEOUT = 600             # 等待扫码超时（秒）


# ── 凭证读写 ──────────────────────────────────────────

def load_cred() -> Optional[dict]:
    """读取缓存的登录凭证。

    Returns:
        {"token": str, "base_url": str, "saved_at": float} 或 None
    """
    if not CRED_FILE.exists():
        return None
    try:
        data = json.loads(CRED_FILE.read_text(encoding="utf-8"))
        token = (data.get("token") or "").strip()
        if not token:
            return None
        return {
            "token": token,
            "base_url": (data.get("base_url") or "").strip(),
            "saved_at": float(data.get("saved_at") or 0),
        }
    except (json.JSONDecodeError, OSError, ValueError):
        _logger.warning("读取 ClawBot 凭证失败，忽略", exc_info=True)
        return None


def save_cred(token: str, base_url: str = "") -> None:
    """保存登录凭证到本地缓存。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CRED_FILE.write_text(
        json.dumps({
            "token": token,
            "base_url": base_url,
            "saved_at": time.time(),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_cred() -> None:
    """删除缓存的登录凭证。"""
    if CRED_FILE.exists():
        CRED_FILE.unlink()


def cred_remaining(cred: dict) -> float:
    """返回凭证剩余有效秒数（可能为负）。"""
    return SESSION_DURATION - (time.time() - float(cred.get("saved_at") or 0))


# ── 二维码展示 ────────────────────────────────────────

def _save_qrcode(data: dict) -> str:
    """把二维码内容保存为本地文件，返回展示说明文本。

    处理 qrcode_img_content 的多种形态：
    - data:image/png;base64,... → 解码保存 png
    - http(s)://... → 直接返回链接（可发给微信文件传输助手后手机打开）
    - 纯 base64 / svg → 尝试解码保存，失败则原样返回
    """
    content = data.get("qrcode_img_content") or ""
    if not content:
        return f"二维码标识: {data.get('qrcode', '')}"
    if content.startswith("data:image/"):
        header, _, b64 = content.partition(",")
        ext_match = re.search(r"data:image/(\w+)", header)
        ext = ext_match.group(1) if ext_match else "png"
        path = CONFIG_DIR / f"clawbot_qrcode.{ext}"
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(b64))
        return f"二维码已保存: {path}\n（手机微信扫码，或用浏览器打开该图片再扫码）"
    if content.startswith("http"):
        return f"二维码链接: {content}"
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        QR_FILE.write_bytes(base64.b64decode(content))
        return f"二维码已保存: {QR_FILE}"
    except Exception:
        return f"二维码内容: {content[:200]}"


# ── 二维码终端渲染（登录与重连共用） ─────────────────

def _print_qr_box(lines: list, print_fn: Callable = print) -> None:
    """打印带边框的终端二维码（整个盒子一次输出，保持宽高比）。

    ★ 渲染修复（2026-08-18）：TUI 模式下 ``print_fn`` 映射为
    ``ChatUI.write_line``，逐行调用会为每一行生成独立聊天块（每块尾部
    自动追加空行分隔）→ 二维码行间出现空行、纵向拉伸变形。改为整个
    盒子（上边框+内容+下边框）用 ``\n`` 连接后**一次**输出：单块单尾
    空行，二维码比例保持。边框宽度自适应内容最大行宽（修复前固定宽度，
    宽二维码时边框包不住内容）。

    Args:
        lines: 二维码文本行列表
        print_fn: 输出函数（print / ChatUI.write_line）
    """
    if not lines:
        print_fn("")
        return
    content_w = max(len(line) for line in lines)
    title = "请用手机微信扫描下方二维码"
    inner = max(content_w, len(title) + 4)
    gap = inner - len(title) - 2
    left = gap // 2
    right = gap - left
    top = "╔" + "═" * left + " " + title + " " + "═" * right + "╗"
    bottom = "╚" + "═" * inner + "╝"
    text = "\n".join([top] + list(lines) + [bottom])
    print_fn("\n" + text + "\n")


def _save_qrcode_png(content: str) -> str:
    """用 qrcode 库生成标准黑白 PNG 并保存，返回文件路径（失败返回空串）。"""
    try:
        from .qrimage import qrcode_png_bytes
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path = CONFIG_DIR / "clawbot_qrcode.png"
        path.write_bytes(qrcode_png_bytes(content))
        return str(path)
    except Exception:
        _logger.warning("保存二维码 PNG 失败", exc_info=True)
        return ""


def display_qrcode(data: dict, print_fn: Callable = print,
                   width: Optional[int] = None) -> None:
    """把登录二维码渲染到终端并提供多种扫码方式。

    微信官方 get_bot_qrcode 返回的 qrcode_img_content 实际是 liteapp
    登录页 URL（如 https://liteapp.weixin.qq.com/q/xxx?qrcode=...&bot_type=3），
    **该 URL 就是二维码内容**——用它生成二维码扫码即可登录；而 qrcode
    字段只是内部 hash，不能作为二维码内容。

    提供三种扫码方式：
    1. 终端直接渲染二维码（手机微信扫一扫 → 扫屏幕）
    2. 保存标准 PNG 图片文件（手机微信扫一扫 → 相册选图）
    3. 打印登录 URL（发到手机微信【文件传输助手】点击打开）

    Args:
        data: get_bot_qrcode 的响应 dict
        print_fn: 终端输出函数
        width: 终端字符宽度（None 不限制）。过窄时降采样二维码模块，
            避免自动换行错位（TUI 模式下传入真实终端宽度）。
    """
    qrcode = (data.get("qrcode") or "").strip()
    img_content = (data.get("qrcode_img_content") or "").strip()

    # ── 形态1：官方位图/SVG（data:image/... 或 <svg>）→ 按官方图片渲染 ──
    from .qrimage import render_img_content
    try:
        lines, hint = render_img_content(img_content, max_width=width)
        if lines:
            _print_qr_box(lines, print_fn)
            saved = _save_qrcode(data)
            if saved:
                print_fn(saved)
            return
        if hint.startswith("http"):
            url = hint
            from .render import render_qrcode_ascii
            _print_qr_box(render_qrcode_ascii(url, max_width=width), print_fn)
            png_path = _save_qrcode_png(url)
            if png_path:
                print_fn(f"📱 扫码方式2：打开图片文件，用手机微信【扫一扫】→ 相册选图：{png_path}")
            print_fn(f"🔗 扫码方式3：把链接发到手机微信【文件传输助手】，点击打开即可连接：{url}")
            return
    except Exception as e:
        _logger.warning("官方二维码渲染失败: %s", e)

    # ── 形态3：回退 qrcode 字段（内容可能不被识别，仅兜底） ──
    if qrcode:
        from .render import render_qrcode_ascii
        _print_qr_box(render_qrcode_ascii(qrcode, max_width=width), print_fn)
        png_path = _save_qrcode_png(qrcode)
        if png_path:
            print_fn(f"📱 打开图片文件，用手机微信【扫一扫】→ 相册选图：{png_path}")


# ── 登录主流程 ────────────────────────────────────────

async def login(client: IlinkClient, force: bool = False,
                print_fn: Callable = print,
                width: Optional[int] = None) -> tuple:
    """登录流程，返回 (token, base_url)。

    已有有效缓存凭证且 force=False 时直接复用，否则走扫码流程。

    Args:
        client: iLink 客户端
        force: 强制重新扫码（忽略缓存）
        print_fn: 终端输出函数（测试可替换）
        width: 终端字符宽度（None 不限制）；TUI 模式传真实宽度避免二维码换行

    Returns:
        (token, base_url)
    """
    cred = load_cred()
    if cred and not force and cred_remaining(cred) > 0:
        remain_h = cred_remaining(cred) / 3600
        print_fn(f"已复用本地登录凭证（剩余约 {remain_h:.1f} 小时）")
        return cred["token"], cred.get("base_url", "")

    print_fn("正在获取微信 ClawBot 登录二维码...")
    data = await client.get_bot_qrcode()
    qrcode = (data.get("qrcode") or "").strip()
    if not qrcode:
        from .client import safe_dump
        raise RuntimeError(f"获取二维码失败: {safe_dump(data)}")

    # ── 终端直接渲染二维码（手机扫码，优先官方图片） ──
    display_qrcode(data, print_fn=print_fn, width=width)

    hint = _save_qrcode(data)
    if hint:
        print_fn(hint)
    print_fn("请用手机微信扫码并在手机上确认授权（10 分钟内有效）...")

    deadline = time.time() + QR_SCAN_TIMEOUT
    while time.time() < deadline:
        status = await client.get_qrcode_status(qrcode)
        st = status.get("status", "")
        if st == "confirmed":
            token = (status.get("bot_token") or "").strip()
            base_url = (status.get("baseurl") or "").strip()
            if not token:
                raise RuntimeError("扫码成功但未返回 bot_token")
            save_cred(token, base_url)
            print_fn("✅ 登录成功！")
            return token, base_url
        if st in ("expired", "canceled", "failed"):
            raise RuntimeError(f"二维码已失效（{st}），请重新运行")
        await asyncio.sleep(1)

    raise TimeoutError(f"等待扫码超时（{QR_SCAN_TIMEOUT // 60} 分钟），请重新运行")

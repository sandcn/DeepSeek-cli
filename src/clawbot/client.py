"""iLink Bot API 客户端 — 对接微信 ClawBot 官方接口。

协议参考：https://github.com/Tencent/openclaw-weixin（官方 openclaw-weixin 插件）
接入域名默认 https://ilinkai.weixin.qq.com，扫码登录后服务端可能返回专属 baseurl。

接口列表（均为微信官方 iLink CGI）：
- GET  ilink/bot/get_bot_qrcode?bot_type=3    获取登录二维码
- GET  ilink/bot/get_qrcode_status?qrcode=..  轮询扫码状态（拿 bot_token）
- POST ilink/bot/getupdates                   长轮询获取新消息
- POST ilink/bot/sendmessage                  发送消息（文本/图片/视频/文件）
- POST ilink/bot/getconfig                    获取账号配置（typing ticket）
- POST ilink/bot/sendtyping                   发送/取消输入状态指示

通用请求头：
- AuthorizationType: ilink_bot_token（固定值）
- Authorization: Bearer <token>（登录后获取，扫码前不需要）
- X-WECHAT-UIN: 随机 uint32 的 base64 编码
"""

from __future__ import annotations

import base64
import json
import logging
import random
from typing import Optional

import httpx

_logger = logging.getLogger(__name__)

# 微信官方 iLink CGI 服务域名
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
# 插件通道版本号（社区协议通用值）
CHANNEL_VERSION = "1.0.2"


def _make_uin() -> str:
    """生成 X-WECHAT-UIN：随机 uint32 的 base64 编码。"""
    uin = str(random.randint(0, 0xFFFFFFFF))
    return base64.b64encode(uin.encode()).decode()


class IlinkClient:
    """微信 ClawBot iLink API 客户端（异步，httpx）。"""

    def __init__(self, token: str = "", base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 90.0):
        self._token = token
        self._base_url = base_url.rstrip("/") or DEFAULT_BASE_URL
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    # ── 访问器 ──────────────────────────────────────────

    @property
    def token(self) -> str:
        return self._token

    @property
    def base_url(self) -> str:
        return self._base_url

    def set_auth(self, token: str, base_url: str = "") -> None:
        """登录后设置 Bearer token 与专属 base_url。"""
        self._token = token
        if base_url:
            self._base_url = base_url.rstrip("/") or DEFAULT_BASE_URL

    def _headers(self, with_token: bool = True) -> dict:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _make_uin(),
        }
        if with_token and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        return self._client

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端。"""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    # ── 登录接口（无需 token） ─────────────────────────

    async def get_bot_qrcode(self, bot_type: int = 3) -> dict:
        """获取登录二维码。

        Returns:
            {"qrcode": str, "qrcode_img_content": str}
        """
        client = await self._ensure_client()
        resp = await client.get(
            f"{self._base_url}/ilink/bot/get_bot_qrcode",
            params={"bot_type": bot_type},
            headers=self._headers(with_token=False),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_qrcode_status(self, qrcode: str) -> dict:
        """轮询扫码状态。

        Returns:
            {"status": "confirmed"|"waiting"|..., "bot_token": str, "baseurl": str}
        """
        client = await self._ensure_client()
        resp = await client.get(
            f"{self._base_url}/ilink/bot/get_qrcode_status",
            params={"qrcode": qrcode},
            headers=self._headers(with_token=False),
        )
        resp.raise_for_status()
        return resp.json()

    # ── 消息接口（需 token） ───────────────────────────

    async def get_updates(self, buf: str) -> dict:
        """长轮询获取新消息。

        Args:
            buf: 上次响应返回的同步游标，首次传空字符串

        Returns:
            完整响应: {"ret", "msgs", "get_updates_buf", "longpolling_timeout_ms"}
        """
        client = await self._ensure_client()
        resp = await client.post(
            f"{self._base_url}/ilink/bot/getupdates",
            json={
                "get_updates_buf": buf,
                "base_info": {"channel_version": CHANNEL_VERSION},
            },
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def send_message(self, to_user_id: str, context_token: str,
                           text: str) -> dict:
        """发送文本消息给用户。

        Args:
            to_user_id: 目标用户 ID（消息中的 from_user_id）
            context_token: 会话上下文令牌（消息中的 context_token）
            text: 文本内容
        """
        client = await self._ensure_client()
        client_id = f"openclaw-weixin-{random.randint(0, 0xFFFFFFFF):08x}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        resp = await client.post(
            f"{self._base_url}/ilink/bot/sendmessage",
            json=body,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_config(self, ilink_user_id: str, context_token: str) -> dict:
        """获取账号配置（typing ticket 等）。

        Returns:
            {"ret": 0, "typing_ticket": str}
        """
        client = await self._ensure_client()
        resp = await client.post(
            f"{self._base_url}/ilink/bot/getconfig",
            json={
                "ilink_user_id": ilink_user_id,
                "context_token": context_token,
                "base_info": {"channel_version": CHANNEL_VERSION},
            },
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def send_typing(self, ilink_user_id: str, typing_ticket: str,
                          status: int) -> dict:
        """发送/取消输入状态指示。

        Args:
            ilink_user_id: 目标用户 ID
            typing_ticket: 从 get_config 获取的 ticket
            status: 1 = 正在输入，2 = 取消输入
        """
        client = await self._ensure_client()
        resp = await client.post(
            f"{self._base_url}/ilink/bot/sendtyping",
            json={
                "ilink_user_id": ilink_user_id,
                "typing_ticket": typing_ticket,
                "status": status,
                "base_info": {"channel_version": CHANNEL_VERSION},
            },
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()


# ── 消息解析辅助 ──────────────────────────────────────

def extract_text(msg: dict) -> str:
    """从 WeixinMessage 提取文本内容（type=1 的 text_item）。"""
    items = msg.get("item_list") or []
    for item in items:
        if item.get("type") == 1:
            text_item = item.get("text_item") or {}
            return text_item.get("text", "") or ""
    return ""


def is_user_message(msg: dict) -> bool:
    """判断是否为用户发来的消息（message_type=1 USER）。"""
    return msg.get("message_type") == 1


def safe_dump(data: dict, limit: int = 300) -> str:
    """安全地将响应 JSON 转字符串用于日志/提示（截断防泄漏）。"""
    try:
        text = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(data)
    return text[:limit]

"""AI 会话标题生成器 — 后台异步为会话生成 AI 摘要标题。

由 ``_session_lifecycle._finalize_round`` 在每轮对话完成后后台触发：
1. 构建标题生成 prompt（仅取对话开头，控制 token 成本）
2. 通过 ``AsyncModelPort`` 非流式调用模型生成标题
3. 成功后通过 ``chat_msgs.rename_session`` 写入会话文件（失败静默，
   保持截断标题作为 fallback）

设计原则：
- 纯核心层逻辑，模型调用经端口注入（不直接 import api 层）
- 所有函数幂等 / 失败静默，绝不阻塞主对话流程
- 标题规范化（normalize_title）对模型输出去引号/前缀/换行
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

#: AI 标题最大长度（字符）
_TITLE_MAX_CHARS = 30
#: 标题生成 prompt 的输入总字符上限（控制 token 成本）
_TITLE_INPUT_MAX_CHARS = 1500
#: 单条消息最大长度（字符）
_MSG_MAX_CHARS = 300

_TITLE_SYSTEM_PROMPT = (
    "你是会话标题助手。根据对话内容为会话生成一个简洁的中文标题。\n"
    "要求：\n"
    "1. 20 字以内，准确概括对话主题\n"
    "2. 直接输出标题文本本身，不要引号、冒号、编号、解释或换行\n"
    "3. 使用简洁的名词短语或动宾短语\n"
    "4. 不要包含'标题：'等前缀"
)

_TITLE_USER_TEMPLATE = "请为以下对话生成标题：\n\n{conversation}"


def build_title_messages(
    messages: list[dict],
    max_chars: int = _TITLE_INPUT_MAX_CHARS,
) -> list[dict]:
    """从会话消息构建标题生成 prompt（纯函数）。

    只取对话开头部分（跳过 system / tool 消息），控制输入 token。
    工具调用结果不参与标题摘要（噪音大）。

    Args:
        messages: 会话消息列表
        max_chars: 输入总字符上限

    Returns:
        [(system, user)] 消息列表；无可提取内容返回 []
    """
    lines: list[str] = []
    total = 0
    for msg in messages:
        role = msg.get("role", "?")
        if role == "system":
            continue
        if role == "tool" or msg.get("tool_calls"):
            continue
        content = msg.get("content") or ""
        text = " ".join(content.split())
        if not text:
            continue
        if len(text) > _MSG_MAX_CHARS:
            text = text[:_MSG_MAX_CHARS] + "…"
        lines.append(f"{role}: {text}")
        total += len(text)
        if total >= max_chars:
            break
    if not lines:
        return []
    return [
        {"role": "system", "content": _TITLE_SYSTEM_PROMPT},
        {"role": "user", "content": _TITLE_USER_TEMPLATE.format(conversation="\n".join(lines))},
    ]


def normalize_title(text: str) -> str:
    """清理模型输出的标题文本。

    依次处理：空白 → 包围引号/书名号 → "标题：" 前缀 → 多行取首行 →
    末尾标点 → 长度截断。

    Args:
        text: 模型原始输出

    Returns:
        规范化标题；空/无效时返回空字符串
    """
    if not text:
        return ""
    title = text.strip()
    if not title:
        return ""
    # 去掉常见包围符号
    title = title.strip('"\'“”‘’《》【】「」')
    # 去掉 "标题："/"Title:" 等前缀
    for prefix in ("标题：", "标题:", "标题 ", "Title:", "title:", "标题为："):
        if title.startswith(prefix):
            title = title[len(prefix):].strip(" \n\t")
            break
    # 取第一行（模型可能输出多行/解释）
    title = title.splitlines()[0].strip() if title else ""
    # 去掉末尾标点
    title = title.rstrip("。.!！?？;；,，：:")
    title = title.strip()
    # 限制长度
    if len(title) > _TITLE_MAX_CHARS:
        title = title[:_TITLE_MAX_CHARS] + "…"
    return title


async def generate_title_async(model_port, messages: list[dict], model: str) -> str | None:
    """异步生成会话标题。

    Args:
        model_port: AsyncModelPort 实例（经端口注入，避免直接依赖 api 层）
        messages: 会话消息列表
        model: 模型名称

    Returns:
        规范化标题；生成失败 / 无效时返回 None
    """
    msgs = build_title_messages(messages)
    if not msgs:
        return None
    try:
        result = await model_port.call_sync(msgs, model=model, label="会话标题生成")
        content = result.content or ""
        # 中断占位 / 空结果 → 无有效标题
        if not content.strip() or content.strip() in ("(已中断)", "已中断"):
            return None
        title = normalize_title(content)
        return title if title else None
    except Exception as exc:
        _logger.debug("AI 标题生成失败（保持既有标题）: %s", exc)
        return None


async def maybe_update_title_async(
    model_port,
    messages: list[dict],
    model: str,
    session_id: str,
) -> str | None:
    """后台生成标题并写入会话文件（幂等、失败静默）。

    Args:
        model_port: AsyncModelPort 实例
        messages: 会话消息列表
        model: 模型名称
        session_id: 会话 ID

    Returns:
        成功写入的标题；失败返回 None
    """
    if not session_id:
        return None
    title = await generate_title_async(model_port, messages, model)
    if not title:
        return None
    try:
        # rename_session 为同步文件 IO → 在线程中执行
        import asyncio
        await asyncio.to_thread(_rename_session_sync, session_id, title)
        return title
    except Exception as exc:
        _logger.debug("写入 AI 标题失败（保持既有标题）: %s", exc)
        return None


def _rename_session_sync(session_id: str, title: str) -> None:
    """同步更新会话文件标题（延迟导入避免模块级跨层依赖）。"""
    from ..chat_msgs import rename_session
    rename_session(session_id, title)

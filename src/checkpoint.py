"""任务断点保存与恢复（用于长任务中断后 /resume）"""

from __future__ import annotations

import json
import logging
import time

from .paths import CHAT_DIR

_logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────
CHECKPOINT_FILE = CHAT_DIR / "_checkpoint.json"


def save_checkpoint(messages: list[dict], model: str,
                    task_description: str = "") -> None:
    """保存当前任务状态为断点

    Args:
        messages: 完整消息列表（含 system 消息）
        model: 模型名称
        task_description: 任务描述，为空时从最后一条 user 消息自动提取
    """
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 自动提取任务描述
    if not task_description:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "") or ""
                task_description = content[:200]
                if len(content) > 200:
                    task_description += "…"
                break

    data = {
        "saved_at": time.time(),
        "model": model,
        "task_description": task_description,
        "message_count": len(messages),
        "messages": messages,
    }

    try:
        CHECKPOINT_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        _logger.info("断点已保存: %s 条消息 | %s", len(messages), task_description[:60])
    except OSError as e:
        _logger.error("断点保存失败: %s", e)


def load_checkpoint() -> dict | None:
    """加载最新断点数据

    Returns:
        断点数据字典（含 messages/model/task_description 等），
        不存在或损坏时返回 None
    """
    try:
        data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, OSError) as e:
        _logger.warning("断点文件损坏: %s", e)
        return None


def clear_checkpoint() -> None:
    """清除断点（任务成功完成时调用）"""
    try:
        CHECKPOINT_FILE.unlink(missing_ok=True)
        _logger.info("断点已清除")
    except OSError as e:
        _logger.warning("断点清除失败: %s", e)


def has_checkpoint() -> bool:
    """检查是否存在有效断点"""
    return CHECKPOINT_FILE.exists()


def get_checkpoint_info() -> dict | None:
    """获取断点摘要信息（用于显示）

    Returns:
        {
            "message_count": int,
            "task_description": str,
            "elapsed_minutes": float,
            "model": str,
        }
        不存在时返回 None
    """
    data = load_checkpoint()
    if not data:
        return None
    saved_at = data.get("saved_at", 0)
    elapsed = time.time() - saved_at
    return {
        "message_count": data.get("message_count", 0),
        "task_description": data.get("task_description", ""),
        "elapsed_minutes": elapsed / 60,
        "model": data.get("model", "?"),
    }

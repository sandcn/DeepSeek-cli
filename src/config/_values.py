"""配置值规范化小工具（零依赖，供 schema / model_profiles 共用）。

放在独立模块而非 ``model_profiles`` 中，避免 ``config.schema`` 反向导入
``config.model_profiles`` 形成 ``loader → schema → model_profiles → loader``
的循环（架构分层测试会检测完整 AST 导入环）。
"""

from __future__ import annotations


def normalize_multimodal(value) -> str:
    """多模态读图开关 → ``"true"`` / ``"false"``（缺失/非法 → false）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value or "").strip().lower()
    return "true" if text in ("true", "1", "yes", "on") else "false"


__all__ = ["normalize_multimodal"]

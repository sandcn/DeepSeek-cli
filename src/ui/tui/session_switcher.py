"""会话切换器 — 浏览和加载已保存的会话。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

from ...chat_msgs import list_sessions, load_session
from ._selector_base import BaseBottomBarSelector
from ._animator import AnimatorContext, BreathPalette
from ._terminal import is_narrow
from ._text_utils import build_scan_highlight_ansi

_logger = logging.getLogger(__name__)


class SessionSwitcher(BaseBottomBarSelector[dict, Optional[dict[str, object]]]):
    """会话切换器 — 列出已保存会话，选择后加载。

    继承 BaseBottomBarSelector，复用 TTLCache + run_bottom_bar_selection 通用流程。

    用法：
        switcher = SessionSwitcher()
        data = switcher.show()

    向后兼容保留：
        refresh_cache() → 委托 self.refresh()
    """

    def _fetch_items(self) -> list[dict]:
        """扫描文件系统获取已保存会话列表（TTLCache 60s 缓存）。"""
        return list_sessions()

    def _format_display(self, items: list[dict]) -> list[str]:
        """格式化会话字典列表为显示标签 — 美化展示。

        使用 256 色体系增强视觉层次，标题使用呼吸色。
        """
        from ..colors import RESET
        from ..colors import CYAN_256, DARK_GRAY_256, GREEN_256, BRIGHT_CYAN_256, DIM_256
        frame = AnimatorContext.get_default().breath_frame
        labels: list[str] = []
        now = time.time()
        for idx, s in enumerate(items):
            title = s.get("title", "")
            # ★ 标题使用呼吸色
            if title and frame > 0:
                title_color_num = BreathPalette.get_color("role_user", frame)
                title_color = f"\033[38;5;{title_color_num}m"
                title_info = f"{title_color}\u300c{title}\u300d{RESET}"
            elif title:
                title_info = f"{BRIGHT_CYAN_256}\u300c{title}\u300d{RESET}"
            else:
                title_info = f"{DIM_256}(\u65e0\u6807\u9898){RESET}"  # (无标题)
            sid = s.get("id", "")
            sid_short = sid[:min(8, len(sid))] if sid else "?"
            model = s.get("model", "?")
            count = s.get("message_count", 0)

            # ★ 美化：添加时间戳 — 从 saved_at ISO 字符串解析
            saved_at_str = s.get("saved_at", "")
            time_info = ""
            if saved_at_str and saved_at_str != "?":
                try:
                    created_ts = datetime.fromisoformat(saved_at_str).timestamp()
                    age = max(0, now - created_ts)
                    if age < 60:
                        time_info = f"{DIM_256}\u521a\u521a{RESET}"               # 刚刚
                    elif age < 3600:
                        time_info = f"{DIM_256}{int(age // 60)}\u5206\u949f\u524d{RESET}"  # N分钟前
                    elif age < 86400:
                        time_info = f"{DIM_256}{int(age // 3600)}\u5c0f\u65f6\u524d{RESET}"  # N小时前
                    else:
                        time_info = f"{DIM_256}{time.strftime('%m-%d', time.localtime(created_ts))}{RESET}"  # 月-日
                except (ValueError, TypeError, OSError):
                    pass

            # ★ 美化：增加视觉层次，图标对齐
            # 模型名使用呼吸色（非窄屏）
            if frame > 0:
                model_color_num = BreathPalette.get_color("model", frame)
                model_color = f"\033[38;5;{model_color_num}m"
                model_part = f"{model_color}\u25c9 {model}{RESET}"
            else:
                model_part = f"{CYAN_256}\u25c9 {model}{RESET}"
            label = f"{DARK_GRAY_256}{sid_short}{RESET} {time_info}  {title_info}  {model_part}  {GREEN_256}\u25c6 {count}m{RESET}"
            # ★ 扫描高亮：宽屏时对选中行添加周期性扫描高亮背景
            if not is_narrow() and frame > 0:
                label = build_scan_highlight_ansi(idx, frame, len(items), label, scan_period=20)
            labels.append(label)
        return labels

    def _on_selected(self, item: dict) -> dict[str, object] | None:
        """用户确认选择后调用 load_session 加载会话。"""
        sid = item.get("id", "")
        if not sid:
            _logger.warning("SessionSwitcher._on_selected: 会话缺少 id 字段")
            return None
        return load_session(sid)

    def _get_title(self) -> str:
        return "Sessions"

    # ── 向后兼容（保留旧方法名） ────────────────────────

    def refresh_cache(self) -> None:
        """强制刷新会话缓存（委托 refresh）。"""
        self.refresh()


__all__ = ["SessionSwitcher"]

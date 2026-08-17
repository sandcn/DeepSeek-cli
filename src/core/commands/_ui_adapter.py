"""CommandUiAdapter — 命令系统的 UI 适配器（依赖倒置）

封装命令函数中需要的 UI 交互操作（底部栏选择、主题切换、diff 渲染、
消息显示、消息编辑等），所有 ui/ 包的导入被限制在此适配器内部，
通过延迟导入（函数体内 import）确保 core/ 层不直接依赖 ui/ 基础设施。

2026-07-29 TUI 重构适配：
  - run_bottom_bar_selection → 使用 _bottom_bar.py 内置方法
  - 主题函数 → 去除了 theme.py 依赖，返回默认值
  - diff → 移到 _diff_renderer.py
  - display_messages → 委托活跃 ChatUIConsumer（路径 A），无 ChatUI 时回退
    pipeline/message_display（P2-4 docstring 修正）
  - edit_current_messages → 委托到 pipeline/message_editor
"""

from __future__ import annotations

import logging
import time
from typing import Any

_logger = logging.getLogger(__name__)


class CommandUiAdapter:
    """命令 UI 适配器 — 封装命令函数需要的所有 UI 操作"""

    def run_bottom_bar_selection(
        self,
        items: list[str],
        display_items: list[str],
        initial_idx: int = 0,
        title: str = "选择",
        bottom_bar: Any = None,
    ) -> dict:
        """运行交互式选择（标准 React Ink UserSelectPopup 协议优先）。

        ★ 标准 React Ink 化（消灭例外，2026-08-05）：优先使用
        ``model.user_select`` + ``UserSelectPopup`` 标准组件协议（设置弹窗
        状态 → 组件 use_input 交互 → 轮询 done）——与 message_editor /
        user_select 工具同协议。无 ChatUI 活跃（测试桩/单次模式）时回退
        旧补全弹窗路径（``bottom_bar.show_completions`` + 轮询，兼容保留）。

        返回: {"action": "confirmed"|"cancel"|"error", "index": int | None}
        """
        # ── 标准 React Ink 协议（ChatUI 活跃时优先） ──
        chat_ui = self._get_active_chat_ui()
        model = None
        session = None
        if chat_ui is not None:
            try:
                model = chat_ui.get_model()
                session = chat_ui  # 有 request_bottom_redraw
            except Exception:
                model = None
        if model is None and bottom_bar is not None:
            # 兜底：从 bottom_bar（InkBridge）提取（防御 mock 类型）
            cand = getattr(bottom_bar, "_model", None)
            if cand is not None and type(cand).__name__ != "MagicMock" and hasattr(cand, "user_select"):
                model = cand
                session = getattr(bottom_bar, "_session", None) or session
        if model is not None and session is not None and hasattr(model, "user_select"):
            from ...tui.app.model import UserSelectState
            display = display_items if display_items else items
            prev_seq = getattr(model.user_select, "seq", 0)
            model.user_select = UserSelectState(
                visible=True,
                seq=prev_seq + 1,
                title=title or "选择",
                options=list(display),
                selected=max(0, min(int(initial_idx), len(display) - 1)),
                deadline=time.monotonic() + 60,
            )
            # ★ 模态底部视图（2026-08-17 通用机制）：与 user_select 工具同协议
            #   ——激活底部视图（底部区只渲染弹窗，状态栏/输入区不显示）。
            if hasattr(model, "bottom_view"):
                model.bottom_view = "user_select"
            try:
                session.request_bottom_redraw()
            except Exception:
                pass
            # ★ P1/P2（review 修复）：轮询 + 解析 + 清理整段 try/finally——
            #   异常路径也保证 user_select + bottom_view 恢复（不残留弹窗/
            #   底部视图，输入区不消失）；selected 归一化仿 message_editor
            #   （selected 可能为 None/非数字，int() 抛 TypeError 会跳过清理
            #   泄漏 bottom_view → App 持续只渲染弹窗，输入区消失）。
            try:
                deadline = model.user_select.deadline
                while not model.user_select.done:
                    if deadline > 0 and time.monotonic() >= deadline:
                        model.user_select.done = True
                        model.user_select.action = "timeout"
                        break
                    time.sleep(0.05)
                st = model.user_select
                action = st.action or "timeout"
                try:
                    selected = int(getattr(st, "selected", -1))
                except (TypeError, ValueError):
                    selected = -1
                if action != "confirmed":
                    return {"action": "cancel", "index": None}
                return {"action": "confirmed", "index": selected if selected >= 0 else initial_idx}
            finally:
                # 清理弹窗状态 + 关闭底部视图 → App 恢复状态栏 + 输入区。
                model.user_select = UserSelectState()
                if hasattr(model, "bottom_view"):
                    model.bottom_view = ""
                try:
                    session.request_bottom_redraw()
                except Exception:
                    pass

        # ── 旧补全弹窗路径（无 ChatUI 兼容） ──
        if bottom_bar is None:
            return {"action": "error", "index": None}

        display = display_items if display_items else items
        try:
            bottom_bar.show_completions(display, initial_idx,
                                        texts=items,
                                        start_pos=0,
                                        orig_prefix="",
                                        types=None,
                                        match_prefix="")
        except Exception as e:
            _logger.debug("run_bottom_bar_selection: show_completions 失败: %s", e)
            return {"action": "error", "index": None}

        deadline = time.monotonic() + 60

        while time.monotonic() < deadline:
            try:
                input_inst = getattr(bottom_bar, '_input', None)
                if input_inst is not None:
                    text = input_inst.get_queued_input()
                    if text is not None:
                        sel_idx = bottom_bar.get_selected_completion_index()
                        bottom_bar.hide_completions()
                        return {"action": "confirmed", "index": sel_idx}
            except Exception:
                pass

            try:
                bottom_bar.force_redraw()
            except Exception:
                pass

            time.sleep(0.05)

        bottom_bar.hide_completions()
        return {"action": "cancel", "index": None}

    @staticmethod
    def _get_active_chat_ui():
        """获取活跃 ChatUIConsumer（惰性导入，无活跃时 None）。"""
        try:
            from ...tui.consumer import get_active_chat_ui as _fn
            return _fn()
        except Exception:
            return None

    def get_theme_names_with_desc(self) -> list[tuple[str, str]]:
        """获取所有主题名称和描述（Claude TUI parity 步骤 3.5/4.3）。

        主题集单一真源在 ``tui.core._theme.ThemeRegistry``（dark/light/
        high-contrast；app._theme 为 re-export 存根，2026-08-05 公共工具
        归位 core 层）；描述为中文文案。
        """
        try:
            from ...tui.core._theme import ThemeRegistry
            desc = {"dark": "暗色", "light": "亮色", "high-contrast": "高对比"}
            return [(n, desc.get(n, n)) for n in ThemeRegistry.names()]
        except Exception:
            _logger.debug("get_theme_names_with_desc 读取 ThemeRegistry 异常", exc_info=True)
            return [("dark", "暗色"), ("light", "亮色"), ("high-contrast", "高对比")]

    def get_active_theme(self) -> str:
        """获取当前主题名称（读 config THEME；异常/缺省回退 dark）。"""
        try:
            from ...config.proxy import config
            value = config.get("theme", "dark")
            if isinstance(value, str) and value:
                return value
        except Exception:
            _logger.debug("get_active_theme 读 config 异常", exc_info=True)
        return "dark"

    def set_theme(self, name: str) -> None:
        """设置活动主题：校验名 ∈ 主题集 → 写 config → 失效调色板缓存。

        未知名忽略（不抛异常）；config 写入失败仅记日志（不阻断返回）。
        调色板缓存失效使组件下次渲染按新主题取色（Step 4.3 全量生效）。
        """
        names = [n for n, _d in self.get_theme_names_with_desc()]
        if name not in names:
            _logger.debug("set_theme(%s): 未知主题，忽略", name)
            return
        try:
            from ...config.loader import update_config
            update_config("theme", name)
        except Exception:
            try:
                from ...config.proxy import config
                config.set("theme", name)
            except Exception:
                _logger.debug("set_theme(%s): config 持久化失败", name, exc_info=True)
        try:
            from ...tui.core._theme import _invalidate_palette_cache
            _invalidate_palette_cache()
        except Exception:
            _logger.debug("set_theme(%s): 调色板缓存失效异常", name, exc_info=True)

    def render_diff_to_ansi(self, path: str, old_content: str, new_content: str) -> str:
        """将文件差异渲染为带 ANSI 颜色的纯文本字符串。"""
        from ...tui._diff_renderer import render_diff_to_ansi as _fn
        return _fn(path, old_content, new_content)

    def display_messages(
        self,
        data: list[dict],
        agent: Any = None,
        idx_map: list[int] | None = None,
        speed: int = 0,
    ) -> None:
        """恢复会话后展示所有消息内容。

        输出路径统一（方向C 步骤4）：ChatUI 活跃时委托 ChatUIConsumer.display_messages
        （路径 A：DisplayMsgsCmd 管线，经 render_lock 保护），否则回退
        pipeline/message_display 直写（非 ChatUI 上下文兜底，如单次模式）。

        P3-6 标注：``agent`` / ``idx_map`` **仅兜底路径有效**——路径 A 委托下
        ChatUIConsumer.display_messages 仅接受 messages/speed，不传递二者；
        委托异常时降级兜底直写并 warning 日志（与 deitmsg_plugin 防御风格对齐）。
        """
        from ...tui.consumer import get_active_chat_ui
        chat_ui = get_active_chat_ui()
        if chat_ui is not None:
            try:
                chat_ui.display_messages(data, speed=speed)
                return
            except Exception as exc:
                _logger.warning(
                    "CommandUiAdapter display_messages 路径A委托异常: %s", exc,
                    exc_info=True,
                )
                # 降级兜底直写，保证消息不丢失（仅此分支传递 agent/idx_map）
        from ...tui.pipeline.message_display import display_messages as _fn
        _fn(data, agent=agent, idx_map=idx_map, speed=speed)

    def edit_current_messages(
        self, agent: Any, state: dict,
        bottom_bar: Any = None, input_: Any = None,
    ) -> bool:
        """编辑当前消息列表。

        委托到 pipeline/message_editor.py（已恢复）。
        """
        from ...tui.pipeline.message_editor import edit_current_messages as _fn
        return _fn(agent, state, bottom_bar=bottom_bar, input_=input_)


__all__ = ["CommandUiAdapter"]

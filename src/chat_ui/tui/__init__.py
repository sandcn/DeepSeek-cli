"""TUI（终端用户界面）模块 — 分层模块化架构 + 端口抽象

提供终端交互界面，包括消息编辑、选择器、状态栏、命令面板、会话切换等功能。

架构层级：
  Layer 0 — 基础工具:      _terminal.py, _state.py,
                             _ttl_cache.py, _time_format.py,
                             _text_utils.py
                             (_ttl_cache.py / _time_format.py 仅供同包内部使用，不对外导出)
  Layer 1 — 显示层:        _message_display.py
  Layer 2 — 选择器基础设施: _selector_base.py
  Layer 3 — 功能层:        message_editor.py, status_bar.py,
                             command_palette.py, session_switcher.py

架构原则：
  1. 单一状态源：TUIStateTree 是唯一状态容器
  2. 依赖注入：所有子组件通过构造函数注入
  3. 直接调用：组件间通过方法直接调用
  4. 零模块级可变状态：所有可变状态归入 TUIStateTree 或实例级
  5. 抽象优先：共享模式提取为基类 (BaseBottomBarSelector)，文本工具统一提取

重构历史（2026-05-24 v3）：
  25 模块 → 16 模块（合并 9 个碎片化模块）

重构历史（2026-05-24 v4 — 用更好的实现重构 TUI）：
  消除 PickerManager 双重定义、TTLCache 提取、StreamCoordinator 解耦、ports 精简

重构历史（2026-05-24 v5 — 消除模块级可变状态+窄屏合并+废弃清理）：
  _TermWidthCache 类、窄屏合并到 _terminal.py、废弃清理

重构历史（2026-05-24 v6 — 交互端口简化+建造者消除+空对象合并）：
  消除 TUIDefaultInteraction：TUIApplication 直接实现 ITUIInteraction，删除 130 行纯委托代码
  简化 TUIApplicationBuilder：内联建造逻辑，保留 build() 向后兼容
  合并 _NullStatusBar 到 TUIInteractionNull：消除重复 Null Object
  修复 _PickerInjector.get_scroll_window() 自定义工厂时仍使用默认 scroll_window 的 bug

重构历史（2026-05-25 v7 — 消除残余架构债务）：
  移除 src/ui/tui/narrow.py 纯重导出层（__init__.py 直接从 _terminal.py 导入）
  移除 ports.py 中 3 个未使用 Protocol（IBottomGeometry / IStatusBar / IBottomPanel）
  清理 _state.py __all__ 中下划线前缀私有常量
  清理 __init__.py 中 dead submodule 导入和端口导出

重构历史（2026-05-25 v8 — app.py 拆分 + ITUIInteraction 迁入 ports）：
  ITUIInteraction 从 app.py 迁入 ports.py 作为 Protocol（原为普通 class）
  TUIInteractionNull / _NullStatusBar 迁入 _interaction.py
  TUIContext + thread-local 全局状态迁入 _context.py
  移除 TUIApplicationBuilder（已废弃的建造者模式）
  app.py 减少约 120 行，专注 TUIApplication 单一职责

重构历史（2026-05-25 v10 — TUI 代码简化）：
  移除 _stream_coordinator.py / _picker_manager.py / _interaction.py 三个文件
  StreamCoordinator + PickerManager 内联到 TUIApplication
  TUIInteractionNull 迁入 ports.py
  TickParams 工厂方法移除，改用直接构造
  _PanelLayoutCache 简化，移除双检锁缓存
  移除 TUIStateTree.snapshot()/reset() 死代码

重构历史（2026-05-26 — 移除分屏）：
  移除 split_screen.py / bottom_panel.py / _renderer.py 三个文件
  移除 ISplitScreen / IRenderer 端口
  简化 TUIApplication 及 ITUIInteraction

重构历史（2026-05-26 v12 — 死代码清理）：
  移除 app.py / _context.py / ports.py 中 ITUIInteraction / TUIInteractionNull
  （TUIApplication + ports + _context 约 470 行死代码从未接入 app_loop.py）

重构历史（2026-05-31 v13 — 抽象与类优化）：
  新增 _text_utils.py — 统一 truncate() 文本截断工具（消除 _message_display._truncate
    与 message_editor._truncate_text 重复定义）
  新增 _selector_base.py — BaseBottomBarSelector[T,R] 抽象基类（消除 CommandPalette /
    SessionSwitcher 中 TTLCache + run_bottom_bar_selection 重复模式）
  CommandPalette → 继承 BaseBottomBarSelector[str, str|None]（减少 ~15 行样板代码）
  SessionSwitcher → 继承 BaseBottomBarSelector[dict, dict|None]（减少 ~20 行样板代码）
  架构层级更新：L0 新增 _text_utils.py/_ttl_cache.py/_time_format.py，
    L3.5 新增选择器基础设施层
重构历史（2026-05-31 v14 — 移除 prompt-toolkit 死代码）：
  移除 src/ui/picker.py / src/ui/tui/completer.py / src/ui/tui/key_bindings.py / src/ui/tui/input_handler.py
  清理 __init__.py 对应导入导出和 __all__ 条目
  内联 scroll_window 到 _message_display.py，清理测试导入路径
  移除 tests/test_completer.py，pyproject.toml 移除 prompt-toolkit 依赖
"""

# ── 模块导入（公开模块） ─────────────────────────────────
from . import message_editor
from . import status_bar
from . import command_palette
from . import session_switcher
from . import _text_utils
from . import _selector_base

# ── 便捷导出 ──────────────────────────────────────────────
from ._terminal import (
    is_narrow, get_terminal_width,
    narrow_truncate, narrow_indent, narrow_sep_width,
)
from .message_editor import MessageEditor
from ._message_display import display_messages
from .status_bar import StatusBar
from .command_palette import CommandPalette
from .session_switcher import SessionSwitcher

from ._state import (
    TUIStateTree, UISessionState, InputState,
)

from ._terminal import ILockedTerminal
from ._selector_base import BaseBottomBarSelector
from ._text_utils import truncate

__all__ = [
    # 子模块（公开）
    "message_editor", "status_bar", "command_palette",
    "session_switcher",
    # ── narrow ──
    "is_narrow", "get_terminal_width",
    "narrow_truncate", "narrow_indent", "narrow_sep_width",
    # ── message_editor ──
    "MessageEditor", "display_messages",
    # ── status_bar ──
    "StatusBar",
    # ── command_palette ──
    "CommandPalette",
    # ── session_switcher ──
    "SessionSwitcher",
    # ── 状态 ──
    "TUIStateTree", "UISessionState", "InputState",
    # ── ports ──
    "ILockedTerminal",
    # ── selector_base ──
    "BaseBottomBarSelector",
    # ── text_utils ──
    "truncate",
]

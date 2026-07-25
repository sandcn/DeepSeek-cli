"""循环依赖修复的 import 路径验证测试。

验证所有关键 import 路径在执行时不会触发循环导入（RuntimeError: circular import）。
每个测试函数验证一条完整的 import 路径，确保模块可在顶层安全导入。
"""


def test_terminal_terminal_import() -> None:
    """验证 terminal.terminal 顶层导入无循环依赖。"""
    from src.tui.terminal.terminal import LockedTerminal
    assert LockedTerminal is not None


def test_widgets_bottom_bar_import() -> None:
    """验证 widgets._BottomBar 顶层导入无循环依赖。

    关键路径：widgets.__init__ 在加载 _BottomBar 时不应触发
    terminal.terminal → widgets.lock → widgets.__init__ 循环。
    """
    from src.tui.widgets import _BottomBar
    assert _BottomBar is not None


def test_parallel_display_import() -> None:
    """验证 ParallelDisplay 顶层导入无循环依赖。

    验证 4 处 # noqa: PLC0415 修复为顶层导入后无循环依赖。
    """
    from src.tui.parallel_display import ParallelDisplay
    assert ParallelDisplay is not None


def test_component_registry_import() -> None:
    """验证 ComponentRegistry 顶层导入无循环依赖。

    验证 # noqa: PLC0415 修复（from ..engine.const import RenderCommand）
    改为从零依赖 _cmd_ids 导入后无循环依赖。
    """
    from src.tui.core.component_registry import ComponentRegistry
    assert ComponentRegistry is not None


def test_locks_module_import() -> None:
    """验证 _locks 零依赖模块导入正常。"""
    from src.tui._locks import render_lock, io_lock, diff_active, OUTPUT_LOCK_TIMEOUT
    assert render_lock is not None
    assert io_lock is not None
    assert diff_active is not None
    assert OUTPUT_LOCK_TIMEOUT == 1.0


def test_cmd_ids_module_import() -> None:
    """验证 _cmd_ids 零依赖模块导入正常。"""
    from src.tui.engine._cmd_ids import (
        REASONING, CONTENT, NOTIFICATION, WRITE_LINE, ERROR,
        SUBAGENT_FRAME, SPLASH, MAIN_PHASE,
    )
    assert REASONING == 0
    assert CONTENT == 1
    assert NOTIFICATION == 11
    assert WRITE_LINE == 12
    assert ERROR == 16
    assert SUBAGENT_FRAME == 18
    assert SPLASH == 19
    assert MAIN_PHASE == 20


def test_terminal_terminal_import_via_widgets() -> None:
    """验证通过 widgets.terminal 间接导入无循环依赖。

    确保 widgets.__init__ 中延迟导入的 _BottomBar 等模块
    在后续导入 terminal.terminal 时不会触发循环。
    """
    from src.tui.widgets import _BottomBar
    assert _BottomBar is not None
    # 确保 terminal.terminal 在 widgets 已加载后仍可正常导入
    from src.tui.terminal.terminal import get_terminal_width, is_narrow
    assert get_terminal_width is not None
    assert is_narrow is not None


def test_parallel_display_get_active_chat_ui() -> None:
    """验证 ParallelDisplay 中 get_active_chat_ui 导入路径正确。

    确保 4 处从 src.tui.state.consumer_registry 的顶层导入有效。
    """
    from src.tui.state.consumer_registry import get_active_chat_ui
    # 函数对象本身应可调用
    assert callable(get_active_chat_ui)


def test_widgets_lock_re_exports() -> None:
    """验证 widgets.lock 的 re-export 与原导入路径兼容。"""
    from src.tui.widgets.lock import (
        render_lock, io_lock, diff_active,
        OUTPUT_LOCK_TIMEOUT, _try_acquire_output_lock,
    )
    assert render_lock is not None
    assert io_lock is not None
    assert diff_active is not None
    assert OUTPUT_LOCK_TIMEOUT == 1.0
    assert callable(_try_acquire_output_lock)


def test_all_key_paths_together() -> None:
    """验证所有关键 import 路径在同一进程中互不干扰。"""
    from src.tui.terminal.terminal import (
        LockedTerminal, get_terminal_width, is_narrow,
    )
    from src.tui.widgets import (
        _BottomBar, StatusBar, CommandPalette, SessionSwitcher,
    )
    from src.tui.parallel_display import ParallelDisplay
    from src.tui.core.component_registry import ComponentRegistry
    from src.tui._locks import render_lock, io_lock
    from src.tui.engine._cmd_ids import (
        REASONING, CONTENT, NOTIFICATION, SUBAGENT_FRAME,
    )

    assert LockedTerminal is not None
    assert _BottomBar is not None
    assert ParallelDisplay is not None
    assert ComponentRegistry is not None
    assert render_lock is not None
    assert io_lock is not None
    assert REASONING == 0
    assert CONTENT == 1
    assert NOTIFICATION == 11
    assert SUBAGENT_FRAME == 18

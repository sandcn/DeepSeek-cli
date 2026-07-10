"""应用层 — 统一编排应用生命周期

提供 Application 类、AppMode 协议和 SessionManager，
将 app.py 中的启动/运行/关闭逻辑抽象为可测试的组件。

架构位置：
    chat.py → Application → {InteractiveMode, SingleMode, WebUIMode} → ChatSession

设计原则：
    - 依赖倒置：所有外部依赖通过 AppContext 注入
    - 模式策略：不同的运行模式实现 AppMode 协议
    - 生命周期明确：bootstrap → run → shutdown
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from .core.session import ChatSession
from .core.ports import PersistencePort, CheckpointPort, ConfigPort
from .core.adapters.output import DefaultOutputAdapter, get_default_output_port
from .core.telemetry.trace_context import TraceContext, get_current_trace_id
from .paths import CHAT_MSGS_DIR
from .chat_msgs import load_session, get_recover_cmd, list_sessions

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 应用上下文
# ═══════════════════════════════════════════════════════════════

@dataclass
class AppContext:
    """应用上下文 — 聚合应用运行所需的所有依赖

    通过依赖注入方式提供所有外部依赖，便于测试和替换。
    """
    output_port: DefaultOutputAdapter = field(default_factory=get_default_output_port)
    persistence_port: Optional[PersistencePort] = None
    checkpoint_port: Optional[CheckpointPort] = None
    config_port: Optional[ConfigPort] = None
    trace_context: Optional[TraceContext] = None
    loaded_data: Optional[dict] = None
    session: Optional[ChatSession] = None


# ═══════════════════════════════════════════════════════════════
# 会话管理器
# ═══════════════════════════════════════════════════════════════

class SessionManager:
    """会话管理器 — 负责会话的创建、恢复、保存和列举

    封装 ChatSession 的初始化、数据加载和持久化逻辑，
    隐藏会话管理的复杂度。
    """

    def __init__(self, ctx: AppContext):
        self._ctx = ctx

    def create_session(self, loaded_data: Optional[dict] = None) -> ChatSession:
        """创建并初始化 ChatSession

        Args:
            loaded_data: 从 --load 恢复的会话数据

        Returns:
            初始化好的 ChatSession 实例
        """
        session = ChatSession(
            persistence_port=self._ctx.persistence_port,
            checkpoint_port=self._ctx.checkpoint_port,
            config_port=self._ctx.config_port,
        )
        session.initialize()
        self._ctx.session = session
        return session

    def restore_session(self, session_id: str) -> Optional[dict]:
        """加载一个保存的会话

        Args:
            session_id: 会话 ID

        Returns:
            会话数据字典，不存在时返回 None
        """
        return load_session(session_id)

    def list_sessions(self) -> list[dict]:
        """列出所有保存的会话"""
        return list_sessions()

    def save_and_show_recover(self, session: ChatSession, output: DefaultOutputAdapter) -> Optional[str]:
        """保存会话并显示恢复命令

        Args:
            session: ChatSession 实例
            output: 输出端口

        Returns:
            保存的会话 ID，无可保存内容时返回 None
        """
        non_system_msgs = [m for m in session.messages if m.get("role") != "system"]
        if not non_system_msgs:
            return None

        sid = session.save()
        if sid:
            from .ui.colors import GREEN, CYAN, DIM, RESET
            filepath = f"{CHAT_MSGS_DIR}/{sid}.json"
            recover_cmd = get_recover_cmd(sid)
            output.write(f"\n{GREEN}  ✓ 对话已保存到 {filepath}{RESET}", level="raw")
            output.write(f"\n{CYAN}  恢复命令: {recover_cmd}{RESET}", level="raw")
        return sid


# ═══════════════════════════════════════════════════════════════
# AppMode 协议
# ═══════════════════════════════════════════════════════════════

class AppMode(ABC):
    """应用运行模式 — 策略模式

    子类实现 run() 方法定义具体的运行方式。
    内置 bootstrap()/shutdown() 生命周期管理。
    """

    def __init__(self, ctx: AppContext):
        self._ctx = ctx

    @property
    def ctx(self) -> AppContext:
        return self._ctx

    @abstractmethod
    async def run(self) -> None:
        """执行此模式的主逻辑"""
        ...

    async def bootstrap(self) -> None:
        """启动前的初始化（可被子类重写）"""
        if self._ctx.trace_context is None:
            self._ctx.trace_context = TraceContext()
        _logger.info("应用启动 | trace_id=%s", get_current_trace_id())

    async def shutdown(self) -> None:
        """关闭时的清理（可被子类重写）"""
        _logger.info("应用关闭")
        session = self._ctx.session
        if session:
            save_mgr = SessionManager(self._ctx)
            save_mgr.save_and_show_recover(session, self._ctx.output_port)


# ═══════════════════════════════════════════════════════════════
# Application — 统一编排
# ═══════════════════════════════════════════════════════════════

class Application:
    """应用主编排器 — 统一管理启动、运行和关闭

    使用方式:
        app = Application()
        app.set_mode(mode_instance)
        await app.run()

    生命周期流程:
        mode.bootstrap() → mode.run() → mode.shutdown()
    """

    def __init__(self, ctx: Optional[AppContext] = None):
        self._ctx = ctx or AppContext()
        self._mode: Optional[AppMode] = None
        self._session_mgr: Optional[SessionManager] = None

    @property
    def ctx(self) -> AppContext:
        return self._ctx

    @property
    def mode(self) -> Optional[AppMode]:
        return self._mode

    def set_mode(self, mode: AppMode) -> None:
        """设置运行模式"""
        self._mode = mode

    def get_session_manager(self) -> SessionManager:
        """获取会话管理器（惰性初始化）"""
        if self._session_mgr is None:
            self._session_mgr = SessionManager(self._ctx)
        return self._session_mgr

    async def run(self) -> None:
        """执行应用主循环

        流程: bootstrap → mode.run() → shutdown
        """
        if self._mode is None:
            raise RuntimeError("未设置运行模式，请先调用 set_mode()")

        try:
            await self._mode.bootstrap()
            await self._mode.run()
        except asyncio.CancelledError:
            _logger.info("应用被取消")
        except KeyboardInterrupt:
            _logger.info("用户中断")
        except Exception as e:
            _logger.critical("应用崩溃", exc_info=True)
            self._ctx.output_port.write(f"\n  ❌ 致命错误: {e}", level="error")
        finally:
            await self._mode.shutdown()
            self._ctx.output_port.write("  Goodbye!", level="raw")


# ═══════════════════════════════════════════════════════════════
# InteractiveMode — 交互式对话
# ═══════════════════════════════════════════════════════════════

class InteractiveMode(AppMode):
    """交互式对话模式 — 封装 app_loop.run_interactive_mode_async"""

    async def run(self) -> None:
        from .app_loop import run_interactive_mode_async
        await run_interactive_mode_async(self._ctx.loaded_data)

    async def shutdown(self) -> None:
        """交互模式下退出时不自动保存（app_loop 已处理保存逻辑）"""
        _logger.info("交互模式关闭")


# ═══════════════════════════════════════════════════════════════
# SingleMode — 单次对话
# ═══════════════════════════════════════════════════════════════

class SingleMode(AppMode):
    """单次对话模式 — 封装 app_loop.run_single_mode_async"""

    def __init__(self, ctx: AppContext, prompt_text: str):
        super().__init__(ctx)
        self._prompt_text = prompt_text

    async def run(self) -> None:
        from .app_loop import run_single_mode_async
        await run_single_mode_async(self._prompt_text)

    async def shutdown(self) -> None:
        """单次模式下退出时不自动保存（app_loop 已处理保存逻辑）"""
        _logger.info("单次模式关闭")

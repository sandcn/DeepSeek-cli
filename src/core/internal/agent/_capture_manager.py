"""工具 stdout 实时捕获管理器

将 Agent 中散落的 stdout 捕获逻辑（_SharedCapture / _capture_state /
_ensure_capture_state / _start_tool_output_capture 等）封装为独立模块，
消除 Agent、SubAgent、ParallelExecutor 之间重复的泄漏检测和恢复代码。

核心抽象:
- SharedCapture（io.StringIO）— 共享 stdout 捕获，多 label 分发
- CaptureManager — 管理捕获生命周期，线程安全
- 顶层工具函数 — 泄漏检测/恢复（与 _capture_utils.py 互补）
"""

from __future__ import annotations

import io
import logging
import sys
import threading

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# SharedCapture — 多 label 共享 stdout 捕获
# ═══════════════════════════════════════════════════════════════

class SharedCapture(io.StringIO):
    """共享 stdout 捕获，将写入内容分发到各工具 label 的事件总线。

    多个并行工具共享同一个实例，各自的 output 通过 tool_labels 列表
    分发到对应气泡，同时写入 real_stdout 确保终端直接可见。
    """

    def __init__(self, tool_labels: list, real_stdout, bus, event_port=None):
        super().__init__()
        self._tool_labels = tool_labels
        self._real_stdout = real_stdout
        self._bus = bus  # 保留兼容，不直接用于事件发布
        self._event_port = event_port if event_port is not None else bus

    def write(self, s: str) -> int:
        if s and s.strip():
            from ....tui.events.event_types import ToolOutputChunkEvent
            for lbl in list(self._tool_labels):
                try:
                    self._event_port.publish_event(ToolOutputChunkEvent(
                        label=lbl, text=s, source="agent",
                    ))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _logger.warning("发布工具输出事件异常", exc_info=True)
        # ChatUI 通过 EventBus 消费 ToolOutputChunkEvent 统一终端输出，
        # 不再需要 _real_stdout 直写（避免与 ChatUI 重复打印）。
        return len(s) if s else 0

    def flush(self) -> None:
        if self._real_stdout:
            try:
                self._real_stdout.flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.warning("刷新 real_stdout 异常", exc_info=True)

    @property
    def active_labels(self) -> list:
        """当前活跃的工具 label 列表（外部可读引用）"""
        return self._tool_labels


# 必须延迟导入 asyncio（模块级 import 在 Python 3.13+ 某些上下文中会失败）
import asyncio  # noqa: E402 — 延迟导入，位于 SharedCapture 之后


# ═══════════════════════════════════════════════════════════════
# _LegacyBusAdapter — 将旧式 DisplayEventBus 包装为 EventPort 接口
# ═══════════════════════════════════════════════════════════════

class _LegacyBusAdapter:
    """将旧式 DisplayEventBus 适配为具有 publish_event 接口的简单包装器。

    当 CaptureManager 只收到 event_bus 参数（旧 API）时，
    将其包装为此适配器，使 SharedCapture 可统一通过
    publish_event(event) 发布类型化事件。
    """

    __slots__ = ('_bus',)

    def __init__(self, bus):
        self._bus = bus

    def publish_event(self, event, source: str = "core") -> None:
        """发布类型化事件，委托给底层 DisplayEventBus.publish()"""
        self._bus.publish(event)


# ═══════════════════════════════════════════════════════════════
# CaptureManager — 捕获周期管理器
# ═══════════════════════════════════════════════════════════════

class CaptureManager:
    """工具 stdout 捕获管理器。

    封装 _SharedCapture 的完整生命周期：初始化、label 注册/注销、
    sys.stdout 劫持与恢复、泄漏检测与自我修复。

    线程安全：_init_lock 保护 _state 的首次创建（检查-设置竞态）。
    Agent 和 SubAgent 等宿主通过 self.capture = CaptureManager()
    使用，不再各自维护 _capture_state 属性和 7 个分散方法。
    """

    def __init__(self, event_bus=None, event_port=None):
        self._state: dict | None = None
        self._init_lock = threading.Lock()

        if event_port is not None:
            self._event_port = event_port
        elif event_bus is not None:
            # 将旧式 DisplayEventBus 包装为具有 publish_event 接口
            self._event_port = _LegacyBusAdapter(event_bus)
        else:
            from ...adapters.events import DisplayEventBusAdapter
            self._event_port = DisplayEventBusAdapter.get_default()

    # ── 属性 ──────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """是否有活跃的捕获会话（sys.stdout 被劫持中）"""
        return self._state is not None and self._state.get('capture') is not None

    @property
    def active_labels(self) -> list[str]:
        """当前活跃的工具 label 列表（空列表表示无捕获）"""
        if self._state is None:
            return []
        return list(self._state.get('active_labels', []))

    # ── 状态管理 ──────────────────────────────────────────

    def _ensure_state(self) -> dict | None:
        """初始化/检查捕获状态，返回 _state 字典。

        线程安全：使用 _init_lock 保护 _state 的首次创建，
        防止多个协程同时进入时检查-设置模式的竞态条件。

        自动修复：检测到孤立 SharedCapture 劫持 sys.stdout 时，
        自动恢复并重建状态。
        """
        # 自修复：检测 sys.stdout 泄漏
        if is_shared_capture_inst(sys.stdout):
            if self._state is None or sys.stdout is not self._state.get('capture'):
                _safe_restore("CaptureManager._ensure_state 检测到孤立 SharedCapture")
                self._state = None

        if self._state is not None:
            # 强化：即使 _state 已存在，也检查 real_stdout 是否被污染
            if is_shared_capture_inst(self._state['real_stdout']):
                self._state['real_stdout'] = sys.__stdout__
            elif isinstance(self._state['real_stdout'], io.StringIO):
                self._state['real_stdout'] = sys.__stdout__
            return self._state

        # 首次初始化（阻塞式获取锁，消除 TOCTOU 竞态）
        with self._init_lock:
            if self._state is not None:
                return self._state
            self._state = self._build_state()
        return self._state

    def _build_state(self) -> dict:
        """构造初始状态字典。"""
        if is_shared_capture_inst(sys.stdout):
            real_stdout = sys.__stdout__
        elif isinstance(sys.stdout, io.StringIO):
            real_stdout = sys.__stdout__
        else:
            real_stdout = sys.stdout
        return {
            'active_labels': [],
            'real_stdout': real_stdout,
            'capture': None,
        }

    # ── 核心 API ─────────────────────────────────────────

    def start_capture(self, tool_label: str) -> None:
        """为一个工具 label 启动 stdout 捕获。

        重定向 sys.stdout → SharedCapture，将工具 print 输出：
        1. 通过 SharedCapture.write() 推送到 real_stdout（终端打印）
        2. 同时发布为 ToolOutputChunkEvent → EventBus → WebUI

        多个工具可共享同一个 SharedCapture 实例（并发捕获）。
        """
        try:
            state = self._ensure_state()
            if state is None:
                return
            state['active_labels'].append(tool_label)
            if state['capture'] is None:
                state['capture'] = SharedCapture(
                    tool_labels=state['active_labels'],
                    real_stdout=state['real_stdout'],
                    bus=self._event_port,
                )
                sys.stdout = state['capture']
        except Exception:
            _logger.warning("启动工具输出捕获异常", exc_info=True)
            state = self._state
            if state and tool_label in state.get('active_labels', []):
                try:
                    state['active_labels'].remove(tool_label)
                except ValueError:
                    pass
            if state and not state.get('active_labels'):
                self.cleanup()

    def stop_capture(self, tool_label: str) -> None:
        """停止一个工具 label 的捕获。

        最后一个 label 移除时自动恢复 sys.stdout 并清理资源。
        """
        state = self._state
        if state is None:
            return
        if tool_label:
            try:
                state['active_labels'].remove(tool_label)
            except ValueError:
                pass
        if not state['active_labels']:
            self.cleanup()

    def cleanup(self) -> None:
        """恢复 sys.stdout 并清理所有捕获资源。

        使用 None 哨兵而非 del 属性语义——CaptureManager 管理自己的 _state，
        不存在 Agent 中 hasattr 对 None 值返回 True 的边界问题。
        """
        state = self._state
        if state is None:
            # 兜底：即使 _state 不存在，也检查 sys.stdout 是否被劫持
            if is_shared_capture_inst(sys.stdout):
                _safe_restore(
                    "CaptureManager.cleanup 检测到孤立 SharedCapture 劫持 sys.stdout"
                    "（_state 已丢失）"
                )
            return

        real_stdout = state.get('real_stdout')
        capture = state.get('capture')
        self._state = None  # 清空状态（等价于 del）

        # 清理 SharedCapture 实例：先 flush 再 close
        if capture is not None:
            try:
                capture.flush()
            except Exception:
                _logger.warning("清理捕获 flush 异常", exc_info=True)
            try:
                capture.close()
            except Exception:
                _logger.warning("清理捕获 close 异常", exc_info=True)

        if real_stdout is not None:
            sys.stdout = real_stdout

        # 防捕获级联泄漏：恢复后的 sys.stdout 如果仍是 SharedCapture
        if is_shared_capture_inst(sys.stdout) and _detect_leak():
            _safe_restore(
                "CaptureManager.cleanup 恢复后的 sys.stdout 是孤立 SharedCapture"
                "（捕获级联）"
            )

        # 防 StringIO 泄漏：恢复后的 sys.stdout 如果是普通 StringIO
        if isinstance(sys.stdout, io.StringIO) and not is_shared_capture_inst(sys.stdout):
            _safe_restore(
                "CaptureManager.cleanup 恢复后的 sys.stdout 是普通 StringIO"
                "（临时缓冲区残留）"
            )


# ═══════════════════════════════════════════════════════════════
# 顶层泄漏检测与恢复工具函数
# ═══════════════════════════════════════════════════════════════

def is_shared_capture_inst(obj) -> bool:
    """判断对象是否为 SharedCapture 实例。"""
    return isinstance(obj, io.StringIO) and type(obj).__name__ in ('SharedCapture', '_SharedCapture')


def _detect_leak() -> bool:
    """检测 sys.stdout 是否被孤立 SharedCapture 劫持（泄漏）。

    扫描全堆中所有宿主对象的 _capture_mgr / _capture_state，
    确认当前 sys.stdout 指向的 SharedCapture 是否仍有活跃 label 监听。
    """
    import gc
    if not is_shared_capture_inst(sys.stdout):
        return False

    for obj in gc.get_objects():
        name = type(obj).__name__
        # 新版：CaptureManager 宿主
        capture_mgr = getattr(obj, '_capture_mgr', None)
        if capture_mgr is not None and capture_mgr.is_active:
            if capture_mgr._state and capture_mgr._state.get('capture') is sys.stdout:
                if capture_mgr._state.get('active_labels'):
                    return False
        # 旧版兼容：Agent/SubAgent 直接持有 _capture_state
        if name in ('Agent', 'SubAgent'):
            cs = getattr(obj, '_capture_state', None)
            if (cs is not None and
                cs.get('capture') is sys.stdout and
                cs.get('active_labels')):
                return False
    return True


def _safe_restore(reason: str = "") -> None:
    """安全恢复 sys.stdout 到 sys.__stdout__。

    同时处理 SharedCapture 泄漏和普通 StringIO 残留两种场景。
    """
    if not isinstance(sys.stdout, io.StringIO):
        return
    try:
        sys.stdout.flush()
    except Exception:
        _logger.debug("sys.stdout.flush 失败（非关键）")
    _logger.warning(
        "sys.stdout 泄漏检测: %s，已自动恢复到 sys.__stdout__",
        reason,
    )
    sys.stdout = sys.__stdout__

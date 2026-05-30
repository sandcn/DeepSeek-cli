"""流式输出生命周期回调（供 TUI 状态栏实时刷新用）。

嵌套深度计数：工具调用可能触发嵌套 stream_call_async，
仅在最外层调用时触发 start/end，避免重复。
"""

import threading


# 嵌套深度计数 ─────────────────────────────────────────
_stream_depth: int = 0
_stream_depth_lock = threading.Lock()

# 生命周期回调（由 app_loop 注册）
_stream_start_cb = None
_stream_end_cb = None
_stream_progress_cb = None
# 回调写入锁 — 保护 set_stream_lifecycle_callbacks 与 _notify_* 读取不竞态
_stream_cb_lock = threading.Lock()


def set_stream_lifecycle_callbacks(start_cb=None, end_cb=None, progress_cb=None):
    """注册流式输出生命周期回调。

    Args:
        start_cb: 流式开始时调用（仅在嵌套深度 0→1 时触发）
        end_cb:   流式结束时调用（仅在嵌套深度 1→0 时触发）
        progress_cb: 流式进行中周期性调用（≈0.5s 间隔）
    """
    global _stream_start_cb, _stream_end_cb, _stream_progress_cb
    with _stream_cb_lock:
        _stream_start_cb = start_cb
        _stream_end_cb = end_cb
        _stream_progress_cb = progress_cb


def _notify_stream_started():
    """通知流式开始（嵌套计数，仅最外层触发）。"""
    global _stream_depth
    with _stream_depth_lock:
        _stream_depth += 1
        if _stream_depth == 1 and _stream_start_cb is not None:
            try:
                _stream_start_cb()
            except Exception:
                import logging
                logging.getLogger(__name__).debug("stream_start_cb 异常", exc_info=True)


def _notify_stream_ended():
    """通知流式结束（嵌套计数，仅最外层触发）。"""
    global _stream_depth
    with _stream_depth_lock:
        if _stream_depth > 0:
            _stream_depth -= 1
        if _stream_depth == 0 and _stream_end_cb is not None:
            try:
                _stream_end_cb()
            except Exception:
                import logging
                logging.getLogger(__name__).debug("stream_end_cb 异常", exc_info=True)


def _notify_stream_progress():
    """通知流式进度（由 pipeline process 循环和 SpeedHandler 周期性调用）。"""
    with _stream_cb_lock:
        cb = _stream_progress_cb
    if cb is not None:
        try:
            cb()
        except Exception:
            import logging
            logging.getLogger(__name__).debug("stream_progress_cb 异常", exc_info=True)

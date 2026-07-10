"""应用初始化包 — 从 app_init.py 拆分

子模块分工：
- _args.py — 命令行参数解析（VERSION / _parse_args / _apply_theme）
- _signal.py — 信号处理管理器（SignalManager）
- _session_cmd.py — 会话管理命令（_handle_session_command）
- main.py — 应用异步入口（main）
"""

from .main import main

__all__ = ["main"]

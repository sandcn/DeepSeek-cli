import asyncio
import sys
from src import main


def _run() -> int:
    """运行异步主入口并统一处理退出路径。

    Python 3.9 的 asyncio.run() 在清理阶段（shutdown_default_executor）可能
    因二次信号（Ctrl+C / SIGTERM / SIGHUP）取消任务而抛出 CancelledError
    （参见 src/app_init/_signal.py 的 mark_exiting 修复），此处兜底吞掉，
    避免输出裸 traceback；KeyboardInterrupt 映射为标准退出码 130。
    """
    try:
        asyncio.run(main())
    except asyncio.CancelledError:
        return 0
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    # ── 删除 stdio 缓冲区 ──────────────────────────────────
    # Android Termux 中 sys.stdout.isatty() 返回 False，
    # Python 会使用块缓冲（~8KB），导致 print() 输出可能滞留
    # 在缓冲区中不刷出。组合 write_through=True（绕过
    # TextIOWrapper 内部缓冲）和 line_buffering=True（换行时
    # 触发 BufferedWriter 刷出），实现近似无缓冲效果。
    try:
        sys.stdout.reconfigure(write_through=True, line_buffering=True)
    except (ValueError, AttributeError):
        pass  # 不可行时保持默认
    try:
        sys.stderr.reconfigure(write_through=True, line_buffering=True)
    except (ValueError, AttributeError):
        pass

    sys.exit(_run())

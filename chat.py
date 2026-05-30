import asyncio
import sys
from src import main

if __name__ == "__main__":
    # ── 强制 stdout 行缓冲 ─────────────────────────────────
    # Android Termux 中 sys.stdout.isatty() 返回 False，
    # Python 会使用块缓冲（~8KB），导致 print() 输出可能滞留
    # 在缓冲区中不刷出。设 line_buffering=True 确保换行即刷新。
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (ValueError, AttributeError):
        pass  # 不可行时保持默认
    try:
        sys.stderr.reconfigure(line_buffering=True)
    except (ValueError, AttributeError):
        pass

    asyncio.run(main())

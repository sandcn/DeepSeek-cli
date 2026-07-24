import asyncio
import sys
from src import main

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

    asyncio.run(main())

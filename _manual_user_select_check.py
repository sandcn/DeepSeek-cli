"""临时手动验证脚本（用完即删）：真实 Windows 控制台启动 TUI 并调用 user_select。

验证目标：Windows 控制台（termios 不可用）下 user_select 弹窗**显示出来**
并可交互（修复前 HAS_TERMIOS=False 直接回退 non_interactive，弹窗不显示）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.tui.consumer import ChatUIConsumer
from src.tools.user_select import UserSelectFunc


def main() -> int:
    ui = ChatUIConsumer()
    ui.start()
    print("TUI started; opening user_select ...", flush=True)

    async def _run() -> str:
        await asyncio.sleep(1.0)
        return await UserSelectFunc(
            title="Windows 弹窗验证",
            options=["选项 A", "选项 B", "选项 C"],
            option_descriptions=[
                "第一个选项的说明（右栏展示）",
                "第二个选项的说明（右栏展示）",
                "第三个选项的说明（右栏展示）",
            ],
            multi_select=False,
            default_options=["选项 B"],
            timeout=90,
        ).execute()

    result = ""
    try:
        result = asyncio.run(_run())
    finally:
        print("\nRESULT:", result, flush=True)
        try:
            ui.stop()
        except Exception as exc:  # pragma: no cover - 验证脚本
            print("stop error:", exc, flush=True)
        print("DONE - window closes in 8s", flush=True)
        time.sleep(8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

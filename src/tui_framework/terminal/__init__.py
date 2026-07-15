"""Terminal 模块 — 终端能力抽象。

子模块列表：

| 子模块 | 说明 |
|--------|------|
| adapter.py | TerminalAdapter — Blessed 终端适配器，包含 query_terminal_size() |
| blessed.py | BlessedTerminal 单例 — 全局 Blessed Terminal 实例 |
| capabilities.py | 终端能力检测 — 颜色支持、Unicode 支持等 |
| narrow.py | 窄屏检测 — is_narrow() 及窄屏降级函数 |
| output_target.py | IOutputTarget Protocol + InlineOutputTarget（非全屏纯文本输出） + TerminalTarget/BufferTarget/NullTarget |
| terminal.py | LockedTerminal — 线程安全终端写入 + 全屏渲染帧管理 |

InlineOutputTarget 特性：
  - 非全屏模式：去除 DECSTBM/SCOSC/DECRC 光标控制，输出为纯文本流
  - supports_inline=True，适合嵌入到非全屏 CLI 场景
  - 与 TerminalTarget（全屏帧覆盖模式）互补
"""

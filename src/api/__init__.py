"""API 模块：HTTP 客户端、流式处理、模型调用、Token 估算、中断控制（异步）。

子模块：
- client_async.py —— httpx 异步客户端封装、连接恢复、SSE 流解析
- model_async.py  —— call_model_async / call_model_sync_async 公开接口 + 重试逻辑
- stream/         —— 流式输出处理、可中断异步迭代器
- tokens.py      —— Token 启发式估算
- interrupt_async.py —— 全局中断信号（threading.Event，跨事件循环安全）
- stats.py       —— 会话级 token 统计
- renderer/         —— 流式输出 Markdown 渲染（策略模式）
- json_repair.py    —— JSON 格式自动修复
- stream_parse.py   —— 流式工具调用解析
- escape_monitor/ —— Esc 键中断监听（包，原 escape_monitor.py 拆分）
"""

from .tokens import estimate_tokens  # noqa: F401

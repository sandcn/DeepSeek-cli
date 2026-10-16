"""API 模块：HTTP 客户端、流式处理、模型调用、Token 估算、中断控制（异步）。

子模块：
- client_async.py —— httpx 异步客户端封装、连接恢复、SSE 流解析
- errors.py      —— API 错误分类体系：语义化异常、统一重试判定、
                    指数退避（含 Retry-After）、友好错误消息
- model_async.py  —— call_model_async / call_model_sync_async 公开接口 + 重试逻辑
- stream/         —— 流式输出处理、可中断异步迭代器
- tokens.py      —— Token 启发式估算
- interrupt_async.py —— 全局中断信号（asyncio.Event）
- stats.py       —— 会话级 token 统计
- json_repair.py    —— JSON 格式自动修复
- stream_parse.py   —— 流式工具调用解析
- escape_monitor/ —— Esc 键中断监听（包，原 escape_monitor.py 拆分）

本包 ``__init__`` **不**做任何数值/重导出：``estimate_tokens`` 等符号经
``src.api.tokens`` 等真实实现路径导入（避免为无调用方的兼容面引入导入开销）。
"""

__all__: list[str] = []

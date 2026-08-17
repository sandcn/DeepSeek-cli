"""
subagent — 并行子 Agent 调度工具

模型通过此工具同时派发多个独立子任务，并行执行后汇总结果。
当同一轮有多个 subagent 调用时，共享一个 ParallelExecutor 实现真正的并行。
"""

import asyncio
from .base import Func, tool_metadata


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="general",
    priority=50,
    tool_category="general",
    description="并行子Agent调度",
)
class SubagentFunc(Func):
    name = "subagent"

    def __init__(self, description: str, prompt: str, target_agent_type: str = "execute"):
        super().__init__()
        self.description = description
        self.prompt = prompt
        self.target_agent_type = target_agent_type  # 目标子Agent类型，与 Func.agent_type 独立

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        desc = arguments.get("description", "?")
        return f"agent: {desc}"[:max_len]

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": (
                    "派发子 Agent 执行任务（独立上下文+文件沙盒），同轮多次调用自动并行。"
                    "type：execute（读写+bash，默认）/ map（只读分析）/ review（代码审查 P0-P3）/ plan（生成计划，仅写 .chat/plan/）。"
                    "同一文件的所有修改必须在单次调用内完成。"
                    "返回：子 Agent 执行结果。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "简短的任务摘要，显示在 UI 中作为子任务的标题标签。建议不超过50字，提炼任务核心，如「解析 user.py 模块」「测试 auth.py 登录流程」。",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "子Agent的完整任务指令。必须包含：目标、具体文件路径、输出格式要求、约束条件等全部信息。长度不限，但建议结构清晰、分点列出，避免模糊表述，确保子Agent可独立执行无需额外上下文。",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["map", "review", "plan", "execute"],
                            "description": "子Agent类型。execute（默认）：排除 subagent、user_select 和 web_search，保留所有读写工具+bash，无路径限制，用于执行计划文件步骤并返回修改文件列表。map：只读分析型，仅保留 read_file/search/find/ls 等读取工具，专用于项目代码分析和地图生成。review：代码审查型，只读工具集（含 read_file/search/find/ls/web_search），专用于文件列表的 Code Review（P0-P3 分级输出）。plan：计划型，只读分析工具 + write_file/update_file/mkdir（仅限写入 .chat/plan/ 目录），根据指令生成计划。",
                        },
                    },
                    "required": ["description", "prompt"],
                },
            },
        }

    @classmethod
    def from_args(cls, args: dict):
        return cls(
            description=args.get("description", ""),
            prompt=args.get("prompt", ""),
            target_agent_type=args.get("type", "execute"),
        )

    async def execute(self) -> str:
        if not self.description or not self.prompt:
            return "错误：未提供子 Agent 任务描述或指令"
        if not self.agent:
            return "错误：未关联父 Agent"

        # 检查是否有共享的 ParallelExecutor（同一轮多个 subagent）
        shared = getattr(self.agent, '_shared_executor', None)
        if shared is not None and shared.is_batch_mode:
            # 传递 tool_label，用于前端将 subagent 路由到对应的 dispatch 工具容器
            tool_label = getattr(self, 'tool_label', '')

            # 模型选择：对指定类型的 subagent 检查是否应使用低优先级模型
            model = getattr(self.agent, 'model', None)
            low_model_types = {"map", "execute"}
            if self.target_agent_type in low_model_types:
                try:
                    config_port = self.agent.get_config_port()
                    low_model = config_port.get_low_model()
                    if low_model:  # 非空字符串表示已设置
                        model = low_model
                except Exception:
                    pass  # 安全降级：继续使用父模型

            idx = shared.add_agent(self.description, self.prompt, agent_type=self.target_agent_type, model=model, tool_label=tool_label)
            try:
                await shared.register_and_wait()
            except asyncio.CancelledError:
                # 被级联取消时（如同层工具异常触发 FIRST_EXCEPTION），
                # 若自己是最后一个已注册 agent，则唤醒其他等待者防止死锁
                async with shared._agents_lock:
                    if shared._registered_count >= shared._expected_count:
                        shared._all_done.set()
                raise
            r = shared.get_result(idx)
            return self._format_single(r)

        # 独立模式（无 shared_executor）→ 无法执行
        # 正常流程下由 _tool_callbacks.py 创建 shared executor，
        # 此处为异常回退路径（外部非正常调用）
        return ("错误：subagent 未处于有效的并行执行上下文中。"
                "请通过 tools 系统正常调用 subagent。")

    @staticmethod
    def _format_single(r: dict) -> str:
        """格式化单个 agent 结果。"""
        header = f"## {r['description']}"
        if r.get("error"):
            return f"{header}\n错误: {r['error']}"
        result = r.get('result', '') or ''
        return f"{header}\n{result}"

    async def display(self) -> str:
        return await self.execute()

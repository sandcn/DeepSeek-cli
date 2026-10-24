"""
SubAgentSpawner — SubAgent 创建与面板装配

从 ParallelExecutor 提取，封装 SubAgent 实例创建与显示注册：

1. spawn() — 从 spec 创建 SubAgent 实例并注册到 display

注：subagent 启动提词与结束结果的 markdown 消息区块已删除（用户需求），
本模块不再发布 subagent 提词 / 结果事件。
"""

from typing import Dict, Any

from ...subagent import SubAgent

# ── 结果字典键常量 ─────────────────────────────────
_DESCRIPTION_KEY = "description"


class SubAgentSpawner:
    """创建 SubAgent 实例并装配 display"""

    def __init__(self, parent_agent, agent_factory):
        self.parent = parent_agent
        self._agent_factory = agent_factory

    # -- 公开 API --

    def spawn(self, spec: Dict[str, Any], index: int, display) -> SubAgent:
        """从 spec 创建一个 SubAgent 并注册到 display。

        Args:
            spec: agent 配置 dict（description/prompt/model）
            index: agent 序号（用于生成 label "agent-N"）
            display: ParallelDisplay 实例

        Returns:
            已配置的 SubAgent 实例
        """
        return self._spawn_subagent(spec, index, display)

    # -- 内部方法 --

    def _spawn_subagent(self, spec: Dict[str, Any], index: int, display) -> SubAgent:
        """创建单个 SubAgent 实例：构造标签、调用工厂、绑定 display。

        ★ 2026-08-18（后台 subagent）：spec 可携带唯一 label（如 task_id
        ``sa-xxx``）——后台 subagent 并发执行时每个 label 唯一，避免默认
        ``agent-N``（index 恒 0 → 全部 ``agent-1``）互相覆盖 TUI 槽位 /
        ``_subagent_records`` 导出去重键 / 轨迹存档（P1，review）。
        spec 无 label（直接调用 spawner 场景）→ 保持 ``agent-{index+1}``。
        """
        label = spec.get("label") or f"agent-{index + 1}"
        desc = spec.get(_DESCRIPTION_KEY, f"子任务 {index + 1}")
        agent_type = spec.get("agent_type", "execute")
        sa = self._agent_factory(
            label=label,
            description=desc,
            prompt=spec["prompt"],
            parent_agent=self.parent,
            model=spec.get("model"),
            agent_type=agent_type,
            # ★ 2026-08-17（用户需求：agent 内容合并到 subagent）：
            #   所属 subagent 的 tool_call_id（spec["tool_label"]，add_agent
            #   传入）——随 _record_to_parent 写入会话存档，load 恢复后主轨迹
            #   仍可合并；独立模式（run）spec 无 tool_label → 空串（独立记录）。
            dispatch_label=spec.get("tool_label", ""),
        )
        sa.display = display
        # ★ 2026-08-19（用户需求：轨迹 Trace 也能用回车显示后台 subagent）：
        #   add_agent 透传 dispatch_label（spec["tool_label"]，subagent 调用
        #   tool_call_id）——修复前只写入 SubAgent 实例（sa.dispatch_label）
        #   而面板槽位 dispatch_label 恒为空：后台 subagent（独立模式 run）
        #   槽位无法与主轨迹中派发它的 subagent 工具记录匹配合并 → 生成独立
        #   subagent 记录，用户选中派发工具记录回车无反应（无 subagent_label
        #   无法进入 subagent 轨迹）。透传后主轨迹合并到工具记录
        #   （subagent_label 设置）→ Enter 进入后台 subagent 轨迹。
        display.add_agent(
            label, desc, status="running", agent_type=agent_type,
            dispatch_label=spec.get("tool_label", ""),
        )
        return sa

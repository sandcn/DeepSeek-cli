"""test_cmd_priority — 命令优先级独立模块（ink/_cmd_priority.py）架构固化。

架构决策（2026-08-05 重构，方向B：InkSession 职责拆分）：命令优先级常量
与映射函数（_get_cmd_priority/_get_cmd_id/_cmd_name）从 ``ink/session.py``
迁至独立模块 ``ink/_cmd_priority.py``——优先级策略独立可测，session 聚焦
队列 + 生命周期 + 渲染循环。本测试固化：
  - _cmd_priority 独立可导入，不依赖 session
  - session re-export 保持旧导入路径兼容（单一真源）
  - 优先级映射行为与迁移前一致
"""

from __future__ import annotations

from pathlib import Path


def _ink_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent / "src" / "tui" / "ink"


class TestCmdPriorityModuleIndependent:
    """_cmd_priority.py 模块边界。"""

    def test_module_file_exists(self) -> None:
        assert (_ink_dir() / "_cmd_priority.py").is_file()

    def test_direct_import_works(self) -> None:
        from src.tui.ink import _cmd_priority
        assert callable(_cmd_priority._get_cmd_priority)
        assert callable(_cmd_priority._get_cmd_id)
        assert callable(_cmd_priority._cmd_name)

    def test_no_session_dependency(self) -> None:
        """_cmd_priority 不依赖 session（防循环/职责倒置）。"""
        source = (_ink_dir() / "_cmd_priority.py").read_text(encoding="utf-8")
        assert "session" not in source.replace("InkSession", "").lower().replace(
            "session.py", ""
        ) or "from .session" not in source
        assert "from .session" not in source

    def test_layer0_const(self) -> None:
        """_cmd_priority 仅依赖 _const（Layer 0 零内部依赖）。"""
        source = (_ink_dir() / "_cmd_priority.py").read_text(encoding="utf-8")
        assert "from src.tui._const import" in source


class TestSessionReexport:
    """session.py 模块级 re-export 保持旧导入路径兼容。"""

    def test_reexport_identity(self) -> None:
        """session._get_cmd_priority 与 _cmd_priority 为同一函数（单一真源）。"""
        from src.tui.ink import session
        from src.tui.ink import _cmd_priority
        assert session._get_cmd_priority is _cmd_priority._get_cmd_priority
        assert session._get_cmd_id is _cmd_priority._get_cmd_id
        assert session._cmd_name is _cmd_priority._cmd_name
        assert session._CRITICAL_CMDS is _cmd_priority._CRITICAL_CMDS
        assert session._STREAM_CMDS is _cmd_priority._STREAM_CMDS

    def test_public_all_kept(self) -> None:
        """__all__ 导出面保持（优先级符号仍从 session 可导入）。"""
        from src.tui.ink import session
        for name in ("_get_cmd_priority", "_get_cmd_id", "_CRITICAL_CMDS",
                     "_STREAM_CMDS", "_CONTENT_COMMANDS"):
            assert name in session.__all__, f"__all__ 缺失 {name}"

    def test_session_has_no_local_impl(self) -> None:
        """session 不再本地定义优先级映射函数。"""
        import ast
        source = (_ink_dir() / "session.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        local_defs = [
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        ]
        for fn in ("_get_cmd_priority", "_get_cmd_id", "_cmd_name"):
            assert fn not in local_defs, (
                f"session 不应本地定义 {fn}（应 re-export）"
            )


class TestCmdPriorityBehaviour:
    """优先级映射行为（与迁移前 test_session.TestCommandPriority 对齐）。"""

    def test_priority_mapping(self) -> None:
        from src.tui.ink import _cmd_priority as cp
        from src.tui._const import (
            ContentCmd, ReasoningCmd, PhaseDoneCmd, ToolSummaryCmd,
            ToolOutputCmd, UserMsgCmd, ParseInfoCmd, NotificationCmd,
            WriteLineCmd, DisplayMsgsCmd, ErrorCmd, SubagentFrameCmd,
            SplashCmd, ClearMsgsCmd, ToolCountIncCmd,
        )
        # CRITICAL / STREAM → 0
        assert cp._get_cmd_priority(ReasoningCmd(text="x")) == 0
        assert cp._get_cmd_priority(ContentCmd(text="x")) == 0
        assert cp._get_cmd_priority(ToolOutputCmd(text="x")) == 0
        assert cp._get_cmd_priority(PhaseDoneCmd(phase="reasoning")) == 0
        assert cp._get_cmd_priority(ToolSummaryCmd()) == 0
        assert cp._get_cmd_priority(ToolCountIncCmd()) == 0
        assert cp._get_cmd_priority(SplashCmd()) == 0
        # HIGH → 1
        assert cp._get_cmd_priority(ErrorCmd(message="e")) == 1
        assert cp._get_cmd_priority(SubagentFrameCmd()) == 1
        # NORMAL → 2
        assert cp._get_cmd_priority(UserMsgCmd(text="u")) == 2
        assert cp._get_cmd_priority(ParseInfoCmd()) == 2
        assert cp._get_cmd_priority(NotificationCmd(text="n")) == 2
        # LOW → 3
        assert cp._get_cmd_priority(WriteLineCmd(text="w")) == 3
        assert cp._get_cmd_priority(DisplayMsgsCmd()) == 3
        assert cp._get_cmd_priority(ClearMsgsCmd()) == 3

    def test_critical_stream_disjoint(self) -> None:
        """CRITICAL 与 STREAM 命令集不相交。"""
        from src.tui.ink import _cmd_priority as cp
        from src.tui._const import RenderCommand
        for cid in cp._CRITICAL_CMDS:
            assert cid not in cp._STREAM_CMDS

    def test_stream_contains_tool_output(self) -> None:
        """TOOL_OUTPUT 属 STREAM（与 Open/Close 同序，prio0）。"""
        from src.tui.ink import _cmd_priority as cp
        from src.tui._const import RenderCommand
        assert RenderCommand.TOOL_OUTPUT in cp._STREAM_CMDS
        assert RenderCommand.TOOL_OUTPUT not in cp._CRITICAL_CMDS

    def test_critical_key_commands(self) -> None:
        """关键命令归属 CRITICAL（blocking 语义）。"""
        from src.tui.ink import _cmd_priority as cp
        from src.tui._const import RenderCommand
        for cid in (RenderCommand.PHASE_DONE, RenderCommand.TOOL_SUMMARY,
                    RenderCommand.TOOL_OPEN, RenderCommand.TOOL_CLOSE,
                    RenderCommand.SPLASH, RenderCommand.MAIN_PHASE):
            assert cid in cp._CRITICAL_CMDS
        assert RenderCommand.CONTENT not in cp._CRITICAL_CMDS
        assert RenderCommand.SUBAGENT_FRAME not in cp._CRITICAL_CMDS

    def test_get_cmd_id(self) -> None:
        from src.tui.ink import _cmd_priority as cp
        from src.tui._const import ContentCmd, RenderCommand
        assert cp._get_cmd_id(ContentCmd(text="x")) == RenderCommand.CONTENT

    def test_cmd_name(self) -> None:
        from src.tui.ink import _cmd_priority as cp
        from src.tui._const import RenderCommand
        assert cp._cmd_name(RenderCommand.CONTENT) == "CONTENT"
        assert cp._cmd_name(99999) == "99999"  # 未知 cid 回退字符串

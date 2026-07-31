"""IncrementalRenderer.close() flush parser 尾部 token 机制验证测试（步骤 3.4）。

覆盖本计划整体修复所依赖的机制前提：
- 无尾部换行的文本（尾部 token 滞留 parser 缓冲）在 close() 时被 flush 渲染输出
- 真实渲染路径（Parser → TokenPipeline → RenderEngine → OutputAdapter captured_output）
"""

from __future__ import annotations

from src.renderer import IncrementalRenderer


class TestRendererTailFlush:
    """close() flush parser 缓冲渲染尾部无换行 token。"""

    def test_close_flushes_tail_token_without_newline(self):
        """无尾部换行的尾部 token 在 close() 时被渲染输出。"""
        captured: list[str] = []
        renderer = IncrementalRenderer(show_indicator=False, captured_output=captured)
        # 模拟尾部 token 滞留 parser 缓冲：无尾部换行的完整句子
        renderer.write("hello **world")
        renderer.close()
        assert "world" in "".join(captured)

    def test_close_flushes_inline_markdown_tail(self):
        """尾部 inline markdown 片段（未闭合）在 close() 时同样被渲染输出。"""
        captured: list[str] = []
        renderer = IncrementalRenderer(show_indicator=False, captured_output=captured)
        renderer.write("tail **token")
        renderer.close()
        joined = "".join(captured)
        assert "token" in joined
        assert "tail" in joined

    def test_write_after_close_ignored(self):
        """close() 后 write() 早退（_closed 保护），不产生额外输出。"""
        captured: list[str] = []
        renderer = IncrementalRenderer(show_indicator=False, captured_output=captured)
        renderer.write("before")
        renderer.close()
        before_len = len("".join(captured))
        renderer.write("after")
        assert len("".join(captured)) == before_len

"""src/renderer/factory — create_stream_renderers 单元测试。

覆盖：
  - 默认输出返回两个独立渲染器（推理 + 内容）
  - 传入 output_file 时渲染输出写入该目标
  - 渲染器可正常执行增量渲染（write/close 端到端冒烟）
"""

from __future__ import annotations

import io

from src.renderer.factory import create_stream_renderers


def test_create_stream_renderers_default():
    reason, content = create_stream_renderers()
    assert reason is not None
    assert content is not None
    assert reason is not content


def test_create_stream_renderers_renders_to_output_file():
    """output_file 透传：渲染内容写入传入的目标。"""
    buf = io.StringIO()
    reason, content = create_stream_renderers(output_file=buf)
    content.write("**加粗文本**")
    content.close()
    text = buf.getvalue()
    assert "加粗文本" in text


def test_create_stream_renderers_both_renderers_work():
    buf = io.StringIO()
    reason, content = create_stream_renderers(output_file=buf)
    reason.write("推理内容")
    reason.close()
    content.write("正文内容")
    content.close()
    text = buf.getvalue()
    assert "推理内容" in text
    assert "正文内容" in text

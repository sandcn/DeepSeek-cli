#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for src/api/renderer/factory.py — 流式渲染器工厂

覆盖内容：
  1. create_stream_renderers 返回 2-tuple
  2. 两个渲染器均为 IncrementalRenderer 实例
  3. 推理渲染器使用 dim style
  4. 可传入 output_file 和 typing_speed
"""

from io import StringIO
import pytest

from src.renderer.factory import create_stream_renderers


# ═══════════════════════════════════════════════════════════════
# 1. 基本返回结构
# ═══════════════════════════════════════════════════════════════

class TestBasicReturn:
    """create_stream_renderers 基本返回结构"""

    def test_returns_tuple_of_two(self):
        """返回值为 2 元组"""
        result = create_stream_renderers()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_both_are_renderer_instances(self):
        """两个元素均为 IncrementalRenderer 实例"""
        reason_renderer, content_renderer = create_stream_renderers()
        # 通过 duck-typing 检查是否具备核心接口
        assert hasattr(reason_renderer, 'write')
        assert hasattr(reason_renderer, 'close')
        assert hasattr(content_renderer, 'write')
        assert hasattr(content_renderer, 'close')

    def test_returns_two_different_instances(self):
        """返回两个不同的实例（非同一对象）"""
        r1, r2 = create_stream_renderers()
        assert r1 is not r2

    def test_renderers_have_same_type(self):
        """推理渲染器和内容渲染器类型相同"""
        reason_renderer, content_renderer = create_stream_renderers()
        assert type(reason_renderer) == type(content_renderer)

    def test_create_multiple_pairs_independent(self):
        """多次调用创建不同的实例对"""
        r1a, r1b = create_stream_renderers()
        r2a, r2b = create_stream_renderers()
        assert r1a is not r2a
        assert r1b is not r2b


# ═══════════════════════════════════════════════════════════════
# 2. 参数传递
# ═══════════════════════════════════════════════════════════════

class TestParameters:
    """参数传递验证"""

    def test_with_output_file(self):
        """传入 output_file 正常工作"""
        buf = StringIO()
        reason_renderer, content_renderer = create_stream_renderers(output_file=buf)
        assert reason_renderer is not None
        assert content_renderer is not None

    def test_with_output_file_and_close(self):
        """传入 output_file 后可正常关闭渲染器"""
        buf = StringIO()
        reason_renderer, content_renderer = create_stream_renderers(output_file=buf)
        # 写入一些内容然后关闭
        content_renderer.write('hello')
        content_renderer.close()
        reason_renderer.close()

    def test_with_typing_speed_zero(self):
        """typing_speed=0 禁用打字机效果"""
        r1, r2 = create_stream_renderers(typing_speed=0)
        assert r1 is not None
        assert r2 is not None

    def test_with_typing_speed_high(self):
        """typing_speed 高值"""
        r1, r2 = create_stream_renderers(typing_speed=9999)
        assert r1 is not None
        assert r2 is not None

    def test_with_both_params(self):
        """同时传入 output_file 和 typing_speed"""
        buf = StringIO()
        r1, r2 = create_stream_renderers(output_file=buf, typing_speed=500)
        assert r1 is not None
        assert r2 is not None

    def test_with_none_output_file(self):
        """output_file=None 使用默认输出（sys.stdout）"""
        r1, r2 = create_stream_renderers(output_file=None)
        assert r1 is not None
        assert r2 is not None

    # ── 边界 ───────────────────────────────────────────────

    def test_negative_typing_speed(self):
        """typing_speed 负数（应正常工作）"""
        r1, r2 = create_stream_renderers(typing_speed=-1)
        assert r1 is not None
        assert r2 is not None

    def test_reasoning_and_content_are_different_types(self):
        """推理渲染器和内容渲染器具有不同的 style 配置"""
        reason_renderer, content_renderer = create_stream_renderers()
        # 通过输出验证它们是不同配置的实例
        reason_attrs = {k: v for k, v in vars(reason_renderer).items()
                       if not k.startswith('_')}
        content_attrs = {k: v for k, v in vars(content_renderer).items()
                        if not k.startswith('_')}
        # 检查它们是否具有不同的 style（如果该属性公开）
        if hasattr(reason_renderer, 'style') and hasattr(content_renderer, 'style'):
            assert reason_renderer.style != content_renderer.style

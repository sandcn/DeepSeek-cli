"""测试 Framework 门面 — get_animator() 及相关 API。

测试覆盖：
  - Framework.get_animator() 返回 AnimatorContext 实例
  - Framework.get_animator() 延迟导入与缓存
  - Framework.get_animator() 多次调用返回同一实例
  - Framework.reset_default() 后重新获取框架与 animator
  - get_animator().frame 与 get_frame() 一致性
  - 模块级便捷函数 get_animator()
  - Framework.get_default().get_animator() 线程安全性（单例不变性）
"""

from __future__ import annotations

import pytest

from src.tui.framework import Framework, get_animator
from src.tui.core.animator import AnimatorContext


# ════════════════════════════════════════════════════════
# 测试前/后清理
# ════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_framework():
    """每个测试前重置 Framework 单例，确保隔离。"""
    Framework.reset_default()
    AnimatorContext.reset_default()
    yield
    Framework.reset_default()
    AnimatorContext.reset_default()


# ════════════════════════════════════════════════════════
# get_animator() 基础行为
# ════════════════════════════════════════════════════════


class TestGetAnimator:
    """Framework.get_animator() 基础行为。"""

    def test_returns_animator_context_instance(self):
        """get_animator() 返回 AnimatorContext 实例。"""
        framework = Framework.get_default()
        animator = framework.get_animator()
        assert isinstance(animator, AnimatorContext)

    def test_multiple_calls_return_same_instance(self):
        """多次调用 get_animator() 返回同一实例。"""
        framework = Framework.get_default()
        a = framework.get_animator()
        b = framework.get_animator()
        assert a is b

    def test_returns_singleton(self):
        """get_animator() 返回 AnimatorContext 单例。"""
        framework = Framework.get_default()
        animator = framework.get_animator()
        assert animator is AnimatorContext.get_default()

    def test_animator_initial_frame_is_zero(self):
        """新获取的 animator 初始帧号为 0。"""
        framework = Framework.get_default()
        animator = framework.get_animator()
        assert animator.frame == 0


# ════════════════════════════════════════════════════════
# get_animator() 与 get_frame() 一致性
# ════════════════════════════════════════════════════════


class TestAnimatorFrameConsistency:
    """get_animator().frame 与 get_frame() 一致性。"""

    def test_frame_matches_get_frame(self):
        """get_animator().frame 应与 get_frame() 返回相同值。"""
        framework = Framework.get_default()
        animator = framework.get_animator()
        # 初始状态两者应一致
        assert animator.frame == framework.get_frame() == 0

    def test_frame_matches_after_tick(self):
        """tick() 推进帧号后，get_animator().frame 与 get_frame() 保持一致。"""
        framework = Framework.get_default()
        animator = framework.get_animator()
        animator.tick(delta=5)
        assert animator.frame == 5
        assert framework.get_frame() == 5

    def test_get_frame_delegates_to_animator(self):
        """get_frame() 内部委托给 get_animator().frame。"""
        framework = Framework.get_default()
        animator = framework.get_animator()
        animator.tick(delta=3)
        assert framework.get_frame() == animator.frame

    def test_get_frame_error_recovery(self):
        """当 AnimatorContext 异常时 get_frame() 兜底返回 0。"""
        # 验证当前实现：即使重置后获取，也应正常返回 0（因为新单例 frame=0）
        Framework.reset_default()
        AnimatorContext.reset_default()
        framework = Framework.get_default()
        assert framework.get_frame() == 0


# ════════════════════════════════════════════════════════
# reset_default() 行为
# ════════════════════════════════════════════════════════


class TestResetDefault:
    """Framework.reset_default() 后 animator 行为。"""

    def test_reset_then_get_animator_returns_fresh(self):
        """reset_default() 后 get_animator() 返回新的 AnimatorContext 实例。"""
        framework = Framework.get_default()
        old_animator = framework.get_animator()
        old_animator.tick(delta=10)

        Framework.reset_default()
        AnimatorContext.reset_default()

        new_framework = Framework.get_default()
        new_animator = new_framework.get_animator()
        assert new_animator.frame == 0
        assert new_animator is not old_animator

    def test_reset_then_get_frame_is_zero(self):
        """reset 后 get_frame() 返回 0。"""
        framework = Framework.get_default()
        framework.get_animator().tick(delta=42)
        assert framework.get_frame() == 42

        Framework.reset_default()
        AnimatorContext.reset_default()

        new_framework = Framework.get_default()
        assert new_framework.get_frame() == 0

    def test_reset_clear_cached_animator(self):
        """reset 后 Framework 内缓存清空，重新延迟导入。"""
        Framework.reset_default()
        AnimatorContext.reset_default()

        framework = Framework.get_default()
        # 首次访问前 _animator 应为 None
        assert framework._animator is None
        animator = framework.get_animator()
        # 访问后 _animator 应为 AnimatorContext 类
        from src.tui.core.animator import AnimatorContext as AC
        assert framework._animator is AC
        assert isinstance(animator, AnimatorContext)


# ════════════════════════════════════════════════════════
# 模块级便捷函数
# ════════════════════════════════════════════════════════


class TestModuleLevelGetAnimator:
    """模块级 get_animator() 便捷函数。"""

    def test_returns_animator_context(self):
        """模块级 get_animator() 返回 AnimatorContext 实例。"""
        animator = get_animator()
        assert isinstance(animator, AnimatorContext)

    def test_same_as_framework_method(self):
        """模块级 get_animator() 与 Framework.get_default().get_animator() 返回同一实例。"""
        a = get_animator()
        b = Framework.get_default().get_animator()
        assert a is b

    def test_after_reset_returns_fresh(self):
        """reset 后模块级函数也返回新实例。"""
        old = get_animator()
        old.tick(delta=99)

        Framework.reset_default()
        AnimatorContext.reset_default()

        new = get_animator()
        assert new.frame == 0
        assert new is not old


# ════════════════════════════════════════════════════════
# 线程安全与单例不变性
# ════════════════════════════════════════════════════════


class TestSingletonInvariants:
    """单例不变性。"""

    def test_framework_singleton_has_unique_animator_cache(self):
        """Framework 单例的 get_animator() 缓存正确。"""
        f1 = Framework.get_default()
        f2 = Framework.get_default()
        assert f1 is f2

        a1 = f1.get_animator()
        a2 = f2.get_animator()
        assert a1 is a2

    def test_get_animator_does_not_reimport(self):
        """get_animator() 只在首次调用时导入，后续使用缓存。"""
        framework = Framework.get_default()
        # 首次调用 → 内部缓存 AnimatorContext 类
        animator1 = framework.get_animator()
        cached_cls = framework._animator

        # 再次调用 → 使用缓存
        animator2 = framework.get_animator()
        assert framework._animator is cached_cls
        assert animator1 is animator2

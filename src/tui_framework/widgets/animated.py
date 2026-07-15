"""AnimatedWidget — 声明式动效控件的基类。

在 Widget 基础上增加声明式动效能力：
  - 通过 ``@effect`` 类装饰器声明效果元数据
  - ``did_mount()`` 时自动从 ``_declared_effects`` 初始化效果实例
  - ``trigger_effect(name)`` 激活指定效果
  - ``_apply_effects(content)`` 对渲染输出施加所有激活中的效果

与 ``AnimatorContext`` 集成：效果帧号自动从全局动画时钟同步。

用法::

    from tui_framework.animation.declarative import effect
    from tui_framework.widgets.animated import AnimatedWidget

    @effect("appear", type="fade_in", duration=6)
    class MyWidget(AnimatedWidget):
        def _render_content(self) -> str:
            return "Hello World"

        def render(self) -> str:
            content = self._render_content()
            return self._apply_effects(content)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tui_framework.animation.declarative import EffectInstance
from tui_framework.widgets.base import Widget

if TYPE_CHECKING:
    from tui_framework.core.animator import AnimatorContext


__all__ = ["AnimatedWidget"]


class AnimatedWidget(Widget):
    """声明式动效控件基类。

    ## 生命周期扩展

    在 Widget 生命周期基础上新增：
      1. ``did_mount()`` → 自动初始化 ``_declared_effects`` 中的效果实例
      2. 每次 ``render()`` 前从 ``AnimatorContext`` 同步全局帧号

    ## 使用方法

    子类应：
      1. 用 ``@effect`` 装饰器声明效果
      2. 在适当位置调用 ``self.trigger_effect("name")`` 激活效果
      3. 在 ``render()`` 结尾调用 ``self._apply_effects(content)`` 施加效果

    ## 效果实例存储

    - ``_effect_instances``: 所有效果实例的列表（EffectInstance）
    - ``_effects``: 继承自 Widget 的效果列表（向后兼容）
    """

    def __init__(self) -> None:
        super().__init__()
        self._effect_instances: list[EffectInstance] = []
        self._animator: AnimatorContext | None = None
        self._animator_bound: bool = False
        self._last_applied_frame: int = -1

    # ── 生命周期 ────────────────────────────────────────

    def did_mount(self) -> None:
        """挂载后初始化声明式效果实例。"""
        super().did_mount()
        self._init_effects()

    # ── 效果初始化 ──────────────────────────────────────

    def _init_effects(self) -> None:
        """从 ``_declared_effects`` 初始化效果实例。

        遍历类上通过 ``@effect`` 装饰器注册的元数据，
        为每条元数据创建 ``EffectInstance`` 并追加到内部列表。
        """
        from tui_framework.animation.declarative import EffectBuilder

        declared: list[dict[str, Any]] = getattr(
            self.__class__, "_declared_effects", []
        )
        for meta in declared:
            inst = EffectBuilder.build(meta)
            self._effect_instances.append(inst)
            self._effects.append(inst)  # 同步到 Widget 的 _effects 列表

    # ── 效果控制 ────────────────────────────────────────

    def trigger_effect(self, name: str) -> bool:
        """激活指定名称的效果。

        Args:
            name: 效果名称（与 ``@effect`` 声明的 name 一致）。

        Returns:
            True 表示找到并激活了效果，False 表示未找到。
        """
        for inst in self._effect_instances:
            if inst.name == name:
                inst.trigger()
                return True
        return False

    def reset_effect(self, name: str) -> bool:
        """重置指定效果为未激活状态。

        Args:
            name: 效果名称。

        Returns:
            True 表示找到并重置，False 表示未找到。
        """
        for inst in self._effect_instances:
            if inst.name == name:
                inst.reset()
                return True
        return False

    def has_active_effects(self) -> bool:
        """检查是否有任何效果处于激活状态。"""
        return any(inst.active for inst in self._effect_instances)

    def get_effect(self, name: str) -> EffectInstance | None:
        """按名称查找效果实例。

        Args:
            name: 效果名称。

        Returns:
            EffectInstance 或 None。
        """
        for inst in self._effect_instances:
            if inst.name == name:
                return inst
        return None

    # ── 帧同步 ──────────────────────────────────────────

    def _ensure_animator(self) -> AnimatorContext:
        """获取全局动画上下文（惰性初始化）。"""
        if self._animator is None:
            from tui_framework.core.animator import AnimatorContext
            self._animator = AnimatorContext.get_default()
        return self._animator

    @property
    def current_frame(self) -> int:
        """当前全局动画帧号。"""
        return self._ensure_animator().frame

    def update_animation(self, frame: int) -> None:
        """手动更新动画帧号（通常不需要，帧号自动从 AnimatorContext 同步）。

        Args:
            frame: 新的帧号。
        """
        self._ensure_animator().frame = frame

    # ── 效果应用 ────────────────────────────────────────

    def _apply_effects(self, content: str) -> str:
        """对所有激活中的效果逐帧推进并应用到内容。

        每个激活的效果调用 ``tick()`` 推进帧号，
        然后调用 ``apply(content)`` 施加效果到当前内容。
        多个效果按声明顺序叠加（后声明的效果包裹先声明的）。

        帧去重：若当前帧号与上次应用帧号相同，跳过 tick()，
        避免同一帧内重复 render 导致效果帧推进过快。

        Args:
            content: 待施加效果的原始渲染内容。

        Returns:
            叠加所有激活效果后的 ANSI 文本。
        """
        if not content:
            return content

        current = self.current_frame
        if current == self._last_applied_frame:
            return content
        self._last_applied_frame = current

        result = content
        for inst in self._effect_instances:
            if inst.active:
                inst.tick()
                result = inst.apply(result)
        return result

    # ── 渲染钩子 ────────────────────────────────────────

    def render(self) -> str:
        """渲染组件内容（覆写 Widget.render）。

        子类通常应继续覆写此方法并调用 ``_apply_effects()``，
        或覆写 ``_render_content()`` 由基类处理效果。

        Returns:
            渲染后的文本内容（含动效 ANSI 序列）。
        """
        if not self._visible:
            return ""
        return self._render_content()

    def _render_content(self) -> str:
        """渲染原始内容（不含动效）。

        子类可覆写此方法提供原始渲染输出，
        然后由 ``render()`` 自动通过 ``_apply_effects()`` 施加效果。
        如果子类覆写 ``render()`` 并自行调用 ``_apply_effects()``，
        则无需覆写此方法。

        Returns:
            不含动效的原始渲染文本。
        """
        return ""

"""动效计算混入类 — 统一组件的动效计算逻辑。（已废弃）

.. deprecated::
    自 v0.5.0 起废弃。请直接调用底层函数而非通过此类转发：

    - :func:`~src.tui.core._wave.fade_factor`
    - :func:`~src.tui.core._wave.sine_color`
    - :func:`~src.tui.core._wave.fade_color`
    - :func:`~src.tui.core.effects.sparkle_color`
    - :func:`~src.tui.core.text_utils.apply_fade_in`
    - :func:`~src.tui.core.text_utils.build_left_border_ansi`

    迁移示例（各组件已自动迁移，仅对第三方引用有影响）:
        ``self.fade_factor(frame)`` → ``fade_factor(frame)``
        ``self.breath_color(...)`` → ``sine_color(...)``
        ``self.sparkle_ansi(frame, 45, 6)`` → 内联 ANSI 构造
        ``self.left_border_ansi(...)`` → ``build_left_border_ansi(...)``
"""

class EffectMixin:
    """动效计算混入类（已废弃）。

    .. deprecated::
        自 v0.5.0 起废弃。请直接调用底层函数而非通过此类转发。
        此类保留为空壳以避免破坏现有 import 依赖，将在未来版本移除。
    """


__all__ = [
    "EffectMixin",
]

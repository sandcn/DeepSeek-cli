"""effect 失败语义回归（2026-10-24）。

背景：``_commit_live`` / ``_run_destroy`` 修复前在 effect 抛异常时只记
warning、不推进 ``last_deps``，而渲染循环是 60Hz——同一失败 effect 会被
逐帧重试（每秒 60 条 warning + 重复副作用）。修复后：

  - create 失败：``last_deps`` 记为本次 deps（deps 变化前不重试）+
    ``fail_count`` / ``next_retry_at``（deps=None 的 effect 退避）；
  - destroy 失败：``hook.destroy`` 先置空再调用（不重复销毁）。
"""

from __future__ import annotations

from src.tui.ink import Text, h
from src.tui.ink.hooks import use_effect
from src.tui.ink.reconciler import Reconciler


def test_failed_effect_is_not_retried_every_frame() -> None:
    calls: list[int] = []

    def Comp(props):
        def create() -> None:
            calls.append(1)
            raise RuntimeError("boom")

        use_effect(create, [])
        return h(Text, None, "x")

    reconciler = Reconciler(None)
    root = reconciler.create_root()
    reconciler.render(root, h(Comp, None), 20, 5)
    # 连续多帧（deps 未变）：失败 effect 不再逐帧重试。
    for _ in range(5):
        reconciler.render(root, h(Comp, None), 20, 5)
    assert len(calls) == 1


def test_failed_effect_retries_when_deps_change() -> None:
    calls: list[int] = []

    def Comp(props):
        def create() -> None:
            calls.append(props["n"])
            raise RuntimeError("boom")

        use_effect(create, [props["n"]])
        return h(Text, None, "x")

    reconciler = Reconciler(None)
    root = reconciler.create_root()
    reconciler.render(root, h(Comp, {"n": 1}), 20, 5)
    reconciler.render(root, h(Comp, {"n": 1}), 20, 5)
    assert calls == [1]
    # deps 变化 → 允许重试（新 deps 的首次尝试）。
    reconciler.render(root, h(Comp, {"n": 2}), 20, 5)
    assert calls == [1, 2]


def test_failed_destroy_is_not_repeated() -> None:
    creates: list[int] = []
    destroys: list[int] = []

    def Comp(props):
        def create():
            creates.append(props["n"])

            def destroy() -> None:
                destroys.append(props["n"])
                raise RuntimeError("destroy boom")

            return destroy

        use_effect(create, [props["n"]])
        return h(Text, None, "x")

    reconciler = Reconciler(None)
    root = reconciler.create_root()
    reconciler.render(root, h(Comp, {"n": 1}), 20, 5)
    # deps 变化：先销毁旧 effect（抛异常），create 仍应执行一次。
    reconciler.render(root, h(Comp, {"n": 2}), 20, 5)
    for _ in range(5):
        reconciler.render(root, h(Comp, {"n": 2}), 20, 5)
    assert destroys == [1]
    assert creates == [1, 2]

"""跨线程 setState 不丢更新（2026-10-23 state queue 加锁回归测试）。"""

from __future__ import annotations

import threading

from src.tui.ink import Text, h
from src.tui.ink.hooks import use_state
from src.tui.ink.reconciler import Reconciler


def test_cross_thread_set_state_updates_are_not_lost() -> None:
    holder: dict = {}

    def Comp(_props):
        value, set_value = use_state(0)
        holder["set"] = set_value
        holder["value"] = value
        return h(Text, None, str(value))

    reconciler = Reconciler(None)
    root = reconciler.create_root()
    reconciler.render(root, h(Comp, None), 20, 10)
    assert holder["value"] == 0

    setter = holder["set"]
    threads = [
        threading.Thread(target=lambda: setter(lambda value: value + 1))
        for _ in range(100)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    reconciler.render(root, h(Comp, None), 20, 10)
    assert holder["value"] == 100

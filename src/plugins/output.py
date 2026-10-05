"""输出端口插件 — 提供 ``ctx.output``。

「一切皆插件」：默认输出端口（``DefaultOutputAdapter``）由内核服务独占，
``DefaultOutputAdapter.get_default()`` / ``get_default_output_port()`` 内核
优先返回该实例；内核缺失（单元测试、独立调用）时回退进程级单例。

输出端口经 ``get_output_publisher`` 工厂把文本投递给渲染层（无锁/持锁
两种写入），因此替换本服务持有的端口即改变整条输出链路。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class OutputService(Service):
    """输出服务 — 占据 ``ctx.output``。"""

    provide = "output"
    name = "output"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..core.adapters.output import DefaultOutputAdapter

        self._port = DefaultOutputAdapter()

    @property
    def port(self):
        return self._port

    def write(self, text: str, level: str = "info", source: str = "core") -> None:
        self._port.write(text, level=level, source=source)

    def write_with_lock(self, text: str, level: str = "info", source: str = "core") -> None:
        self._port.write_with_lock(text, level=level, source=source)


@plugin("output", provide=["output"])
def apply(ctx):
    return OutputService(ctx)


__all__ = ["OutputService", "apply"]

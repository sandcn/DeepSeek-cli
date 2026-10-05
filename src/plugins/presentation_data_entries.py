"""表现层数据条目插件 — 清单中每张数据表一个独立插件条目。

「一切皆插件」：渲染/TUI 的纯数据表（Emoji 短代码 / 上下标 / 圈号 / HTML 块
标签配色 / 列表符号 / 轨迹 KIND/STATUS / 运行模式文本样式）不再硬编码在各
模块字典里，而是由清单中的独立条目声明::

    - id: presentation_data_emoji
      plugin: src.plugins.presentation_data_entries:apply_presentation_data
      config:
        id: emoji                           # 内置表 id（可被 patch/overlay 定位）
        # data: {":smile:": "😊", ...}       # 可选：整表替换
        # name: emoji                        # 可选：覆盖表名

插件挂载时把该 id 的内置数据表注册进注册表（``spec=None`` 用默认表）；卸载时
撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置表随之缺席
（``presentation_data`` 聚合插件经 ``managed_presentation_data`` 抑制默认装配）。
"""

from __future__ import annotations

from ..kernel import plugin


@plugin("presentation_data_table")
def apply_presentation_data(ctx):
    from ..presentation_data import DataTable, default_data_table, register_builtin_data

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("presentation_data 条目缺少 config.id")
    data = ctx.config.get("data")
    spec = None
    if data is not None:
        base = default_data_table(spec_id)
        spec = DataTable(
            id=spec_id,
            name=str(ctx.config.get("name", base.name)),
            data=data,
        )
    undo = register_builtin_data(spec_id, spec)
    ctx.effect(lambda: undo)


__all__ = ["apply_presentation_data"]

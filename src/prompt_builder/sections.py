"""系统提示词片段注册表 — 每个片段一个独立插件条目（一切皆插件）。

``build_*_system_prompt`` 此前把系统提示词的**片段顺序与装配逻辑**硬编码在
``builder._build_prompt`` 里（静态提词 → 项目摘要 → Agent 摘要 → 环境信息 +
版本控制 → 技能章节 → MCP 章节），无法被 Profile/Bundle 声明，也无法被
Patch/Overlay 按 id 单独禁用、覆盖或替换。本模块把片段**声明**收敛为注册表，
每一段都由清单中的**独立插件条目**（``prompt_section``，经
``src.plugins.prompt_section_entries``）显式注册——因此可被 Profile/Bundle
声明，也可被 Patch/Overlay 按 id 单独禁用、覆盖（替换构造函数点分引用）或
替换（调整 ``order`` 改变装配顺序）。

片段构造函数以**点分引用**声明（``module:attr``），惰性解析——注册表为叶子
模块（仅标准库），不引入业务依赖，也不产生 import 环。

``attach_to`` 非空的片段不在装配结果里新开一条 system 消息，而是把文本追加到
目标片段的输出（同一 system 消息内）——版本控制信息即 ``attach_to="env_info"``，
保持与既有「环境信息 + 版本控制」单段输出完全一致。

**清单接管**：``prompt`` 聚合插件收到组合根注入的 ``managed_prompt_sections``
（清单已接管的 id，含被禁用的）时经 ``set_managed_builtin_sections`` 声明这些
id 由清单条目负责；无清单（单元测试、独立调用）时无接管，全部内置片段默认
生效。
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

_lock = threading.RLock()
_ABSENT = object()

#: 片段构造函数所在模块（点分引用前缀）
_BUILDER_MODULE = "src.prompt_builder.builder"


def _ref(func_name: str) -> str:
    return f"{_BUILDER_MODULE}:{func_name}"


@dataclass(frozen=True)
class PromptSection:
    """一个系统提示词片段。"""

    id: str
    label: str
    order: int = 0
    builder: str = ""
    attach_to: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "order": self.order,
            "builder": self.builder,
            "attach_to": self.attach_to,
            "description": self.description,
        }


def _section(spec_id: str, label: str, order: int, builder: str,
             attach_to: str = "", description: str = "") -> PromptSection:
    return PromptSection(
        id=spec_id, label=label, order=order, builder=builder,
        attach_to=attach_to, description=description,
    )


#: 内置片段声明（id → 规格）——每项由清单中的独立插件条目注册。
_BUILTIN_SECTIONS: Dict[str, PromptSection] = {
    "export": _section("export", "静态提词", 10, _ref("_section_export"),
                       description="prompts_export_*.md 静态规则（缺失时用兜底提词）"),
    "global_md": _section("global_md", "项目摘要（global.md）", 20, _ref("_section_global_md"),
                          description="工作目录 global.md 的摘要"),
    "agent_md": _section("agent_md", "Agent 摘要（<agent>.md）", 30, _ref("_section_agent_md"),
                         description="工作目录 <agent>.md 的摘要"),
    "env_info": _section("env_info", "执行环境信息", 40, _ref("_section_env_info"),
                         description="操作系统 / 主机名 / 日期 / 工作目录 / 命令行"),
    "vcs_info": _section("vcs_info", "版本控制信息", 50, _ref("_section_vcs_info"),
                         attach_to="env_info",
                         description="Git 仓库 / 分支 / 状态（并入环境信息同一段落）"),
    "skills": _section("skills", "技能章节", 60, _ref("_section_skills"),
                       description="可用技能目录与自动加载技能正文"),
    "mcp": _section("mcp", "MCP 外部工具章节", 70, _ref("_section_mcp"),
                    description="已配置 MCP server 的工具清单"),
}

_registered_builtin: Dict[str, PromptSection] = {}
_managed_builtin: set = set()
_disabled_builtin: set = set()
_extension: Dict[str, PromptSection] = {}


def builtin_section_ids() -> List[str]:
    """全部内置片段 id（含被接管/禁用的，按声明顺序）。"""
    return list(_BUILTIN_SECTIONS)


def _normalize_ids(ids) -> List[str]:
    if isinstance(ids, str):
        ids = [ids]
    selected: List[str] = []
    for item in ids or ():
        if item not in _BUILTIN_SECTIONS:
            raise KeyError(f"未知内置提词片段: {item!r}（可用: {list(_BUILTIN_SECTIONS)}）")
        selected.append(item)
    return selected


def default_section(spec_id: str) -> PromptSection:
    try:
        return _BUILTIN_SECTIONS[spec_id]
    except KeyError:
        raise KeyError(
            f"未知内置提词片段: {spec_id!r}（可用: {list(_BUILTIN_SECTIONS)}）"
        ) from None


def active_sections() -> Dict[str, PromptSection]:
    """当前生效的片段（内置装配 + 扩展），按声明顺序。"""
    with _lock:
        result: Dict[str, PromptSection] = {}
        for spec_id, default in _BUILTIN_SECTIONS.items():
            if spec_id in _disabled_builtin:
                continue
            override = _registered_builtin.get(spec_id)
            if override is not None:
                result[spec_id] = override
                continue
            if spec_id in _managed_builtin:
                continue
            result[spec_id] = default
        result.update(_extension)
        return result


def ordered_sections() -> List[PromptSection]:
    """当前生效片段按 ``order`` 排序（装配顺序）。"""
    sections = active_sections()
    return sorted(sections.values(), key=lambda sec: (sec.order, sec.id))


def resolve_builder(section: PromptSection) -> Callable:
    """解析片段的构造函数点分引用（``module:attr`` / ``module.attr``）。"""
    ref = section.builder
    if ":" in ref:
        module_name, _, attr = ref.partition(":")
    else:
        module_name, _, attr = ref.rpartition(".")
    if not module_name or not attr:
        raise ValueError(f"提词片段构造引用非法: {ref!r}")
    module = importlib.import_module(module_name)
    obj: Any = module
    for part in attr.split("."):
        obj = getattr(obj, part)
    if not callable(obj):
        raise TypeError(f"提词片段构造不可调用: {ref!r}")
    return obj


def register_builtin_section(spec_id: str, section: Optional[PromptSection] = None) -> Callable[[], None]:
    """注册/覆盖一个内置片段（``section=None`` 用默认声明）；返回幂等撤销。"""
    if spec_id not in _BUILTIN_SECTIONS:
        raise KeyError(f"未知内置提词片段: {spec_id!r}（可用: {list(_BUILTIN_SECTIONS)}）")
    effective = section if section is not None else _BUILTIN_SECTIONS[spec_id]
    with _lock:
        previous = _registered_builtin.get(spec_id, _ABSENT)
        _registered_builtin[spec_id] = effective

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _registered_builtin.pop(spec_id, None)
            else:
                _registered_builtin[spec_id] = previous

    return _undo


def unregister_builtin_section(spec_id: str) -> bool:
    with _lock:
        return _registered_builtin.pop(spec_id, None) is not None


def set_managed_builtin_sections(ids) -> Callable[[], None]:
    """声明这些内置片段 id 由清单条目负责（默认装配被抑制）；返回撤销。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _managed_builtin]
        _managed_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _managed_builtin.discard(item)

    return _undo


def managed_section_ids() -> List[str]:
    with _lock:
        return sorted(_managed_builtin)


def disable_builtin_sections(ids) -> Callable[[], None]:
    """禁用一个或多个内置片段（返回幂等撤销）。"""
    selected = _normalize_ids(ids)
    with _lock:
        added = [item for item in selected if item not in _disabled_builtin]
        _disabled_builtin.update(added)

    def _undo() -> None:
        with _lock:
            for item in added:
                _disabled_builtin.discard(item)

    return _undo


def disabled_section_ids() -> List[str]:
    with _lock:
        return sorted(_disabled_builtin)


def register_section(section: PromptSection) -> Callable[[], None]:
    """注册一个扩展片段（返回幂等撤销）。"""
    if not isinstance(section, PromptSection):
        raise TypeError(f"提词片段规格非法: {section!r}")
    if not section.id:
        raise ValueError("提词片段 id 必须是非空字符串")
    with _lock:
        previous = _extension.get(section.id, _ABSENT)
        _extension[section.id] = section

    def _undo() -> None:
        with _lock:
            if previous is _ABSENT:
                _extension.pop(section.id, None)
            else:
                _extension[section.id] = previous

    return _undo


def unregister_section(spec_id: str) -> bool:
    with _lock:
        return _extension.pop(spec_id, None) is not None


def extension_sections() -> Dict[str, PromptSection]:
    with _lock:
        return dict(_extension)


def clear() -> None:
    """清空扩展片段与清单注册（测试用；不影响内置默认与禁用状态）。"""
    with _lock:
        _extension.clear()
        _registered_builtin.clear()


def reset() -> None:
    """重置全部状态到「无清单、无禁用、全部默认」（测试隔离用）。"""
    with _lock:
        _extension.clear()
        _registered_builtin.clear()
        _managed_builtin.clear()
        _disabled_builtin.clear()


__all__ = [
    "PromptSection",
    "builtin_section_ids",
    "default_section",
    "active_sections",
    "ordered_sections",
    "resolve_builder",
    "register_builtin_section",
    "unregister_builtin_section",
    "set_managed_builtin_sections",
    "managed_section_ids",
    "disable_builtin_sections",
    "disabled_section_ids",
    "register_section",
    "unregister_section",
    "extension_sections",
    "clear",
    "reset",
]

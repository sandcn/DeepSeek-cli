"""操作宏（``bash_opt`` 的 ``op=record`` / ``op=replay`` 实现层）。

「录制与回放」把一段操作序列（点击 → 输入 → 回车 → 截图 等，与
``op=sequence`` 的 ``actions`` 同构）保存成**命名宏**（JSON 文件），之后可用
``op=replay`` 一条调用重复执行——重复性的 GUI 任务（登录、导出、翻页巡检）
不必每次重新拼动作，也便于把一套稳定流程固化下来。

职责划分：

  - 本模块：宏数据模型（:class:`Macro`）、步骤结构校验、JSON 持久化与命名
    安全校验（读写仅限宏目录或调用方显式给出的安全路径）；
  - 工具层（``bash_opt``）：把宏步骤交给既有的序列执行器按序注入，复用
    ``op=sequence`` 的全部能力（element / via / wait_for / diff / shot 等）。

扩展方式：新增宏元数据字段时在 :class:`Macro` 增加字段并保持向后兼容读取
（缺省值填充）；新增存储后端只需替换 :func:`save_macro` / :func:`load_macro`
的写入读取实现，工具层无需改动。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..file_ops import validate_path_security
from .sequence import MAX_SEQUENCE_STEPS, SequenceError, parse_sequence

logger = logging.getLogger(__name__)

#: 宏文件格式版本（读取时校验，向后兼容）
MACRO_VERSION = 1

#: 默认宏目录（相对当前工作目录）
DEFAULT_MACRO_DIR = "bash_opt_macros"

#: 宏文件后缀
MACRO_SUFFIX = ".json"

#: 单个宏允许的最大步数
MAX_MACRO_STEPS = MAX_SEQUENCE_STEPS

#: 宏名长度上限
MAX_NAME_LENGTH = 64

#: 宏名非法字符（路径分隔符与 Windows 保留字符）
_INVALID_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class MacroError(ValueError):
    """宏操作失败（名称非法 / 文件不存在 / 内容损坏 / 步骤非法）。"""


@dataclass(frozen=True)
class Macro:
    """一个命名操作宏。

    Attributes:
        name: 宏名（用于默认文件名，需通过 :func:`validate_macro_name`）。
        steps: 步骤列表（每项为与 ``op=sequence`` 相同的 dict）。
        window: 默认窗口选择器（空 = 主窗口）。
        created: 创建时间（``YYYY-MM-DD HH:MM:SS``；导入旧文件缺失时为空）。
        updated: 最近更新时间。
        version: 格式版本。
    """

    name: str
    steps: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    window: str = ""
    created: str = ""
    updated: str = ""
    version: int = MACRO_VERSION

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "name": self.name,
            "window": self.window,
            "created": self.created,
            "updated": self.updated,
            "steps": [dict(step) for step in self.steps],
        }

    def summary(self) -> dict:
        """列表展示用的精简信息。"""
        return {
            "name": self.name,
            "steps": self.step_count,
            "window": self.window or "main",
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Macro":
        """从字典还原（校验版本与结构）。

        Raises:
            MacroError: 结构非法或版本不支持。
        """
        if not isinstance(data, Mapping):
            raise MacroError(f"宏内容必须是对象，当前: {type(data).__name__}")
        version = data.get("version", MACRO_VERSION)
        try:
            version = int(version)
        except (TypeError, ValueError):
            raise MacroError(f"宏版本非法: {version!r}") from None
        if version > MACRO_VERSION:
            raise MacroError(
                f"宏版本 {version} 高于当前支持的 {MACRO_VERSION}，"
                f"请升级程序后再回放"
            )
        steps = data.get("steps")
        if not isinstance(steps, (list, tuple)):
            raise MacroError("宏缺少 steps 数组")
        validate_steps(steps)
        name = str(data.get("name") or "").strip() or "macro"
        return cls(
            name=name,
            steps=tuple(dict(step) for step in steps),
            window=str(data.get("window") or ""),
            created=str(data.get("created") or ""),
            updated=str(data.get("updated") or ""),
            version=version,
        )


def validate_macro_name(name: str) -> str:
    """校验并归一化宏名（去空白；非法字符报错）。

    Raises:
        MacroError: 名称为空、过长、含路径分隔符 / 保留字符，或为 ``.`` / ``..``。
    """
    text = str(name or "").strip()
    if not text:
        raise MacroError("宏名不能为空")
    if len(text) > MAX_NAME_LENGTH:
        raise MacroError(f"宏名过长（最多 {MAX_NAME_LENGTH} 字符）: {text[:20]}…")
    if text in (".", ".."):
        raise MacroError(f"宏名非法: {text!r}")
    if _INVALID_NAME_RE.search(text):
        raise MacroError(
            f"宏名含非法字符（不能包含 \\ / : * ? \" < > |）: {text!r}"
        )
    return text


def validate_steps(steps: Sequence) -> None:
    """校验步骤结构（与 ``op=sequence`` 同规则，另加宏步数上限）。

    Raises:
        MacroError: 步数为空、超限或结构非法。
    """
    if not steps:
        raise MacroError("宏至少需要一个步骤")
    if len(steps) > MAX_MACRO_STEPS:
        raise MacroError(
            f"宏步骤过多（{len(steps)} > {MAX_MACRO_STEPS}）：请拆分为多个宏"
        )
    try:
        parse_sequence(list(steps))
    except SequenceError as exc:
        raise MacroError(f"宏步骤结构非法: {exc}") from exc


def macro_path(name: str, directory: str | None = None) -> str:
    """返回宏名对应的文件路径（相对目录 + ``<name>.json``）。"""
    safe = validate_macro_name(name)
    base = directory or DEFAULT_MACRO_DIR
    return os.path.join(base, safe + MACRO_SUFFIX)


def resolve_macro_path(path: str) -> str:
    """把显式路径规范化为绝对路径并做安全检查。

    Raises:
        MacroError: 路径为空或未通过安全校验。
    """
    expanded = os.path.expanduser(str(path or "").strip())
    if not expanded:
        raise MacroError("宏文件路径不能为空")
    absolute = os.path.abspath(expanded)
    try:
        validate_path_security(absolute)
    except Exception as exc:  # noqa: BLE001 - 安全校验失败统一转为 MacroError
        raise MacroError(f"宏文件路径非法: {exc}") from exc
    return absolute


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def save_macro(macro: Macro, *, path: str | None = None,
               directory: str | None = None, append: bool = False) -> str:
    """把宏写入 JSON 文件，返回写入路径。

    Args:
        macro: 待保存的宏。
        path: 显式文件路径（优先于 ``directory``）。
        directory: 宏目录（缺省 :data:`DEFAULT_MACRO_DIR`）。
        append: 是否把新步骤追加到已有宏末尾（同名宏存在时）。

    Raises:
        MacroError: 名称 / 步骤非法，或（append 时）已有内容损坏。
    """
    validate_steps(list(macro.steps))
    target = (resolve_macro_path(path) if path
              else os.path.abspath(macro_path(macro.name, directory)))
    now = _timestamp()
    created = macro.created or now
    steps = [dict(step) for step in macro.steps]
    if append and os.path.isfile(target):
        existing = load_macro(path=target)
        steps = [dict(step) for step in existing.steps] + steps
        validate_steps(steps)
        created = existing.created or created
    merged = Macro(name=macro.name, steps=tuple(steps),
                   window=macro.window, created=created, updated=now,
                   version=MACRO_VERSION)
    directory_path = os.path.dirname(target)
    if directory_path:
        os.makedirs(directory_path, exist_ok=True)
    payload = json.dumps(merged.to_dict(), ensure_ascii=False, indent=2)
    temp = target + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(payload)
    os.replace(temp, target)
    return target


def load_macro(*, name: str | None = None, path: str | None = None,
               directory: str | None = None) -> Macro:
    """读取宏（``path`` 优先，否则按 ``name`` 在 ``directory`` 中查找）。

    Raises:
        MacroError: 参数缺失、文件不存在或内容损坏。
    """
    if path:
        target = resolve_macro_path(path)
    elif name:
        target = os.path.abspath(macro_path(name, directory))
    else:
        raise MacroError("读取宏需要 name 或 path 参数")
    if not os.path.isfile(target):
        raise MacroError(f"宏文件不存在: {target}")
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise MacroError(f"读取宏文件失败: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MacroError(f"宏文件不是合法 JSON: {exc}") from exc
    macro = Macro.from_dict(data)
    if not macro.name or macro.name == "macro":
        macro = Macro(name=name or os.path.splitext(os.path.basename(target))[0],
                      steps=macro.steps, window=macro.window,
                      created=macro.created, updated=macro.updated,
                      version=macro.version)
    return macro


def list_macros(directory: str | None = None) -> list[dict]:
    """列出目录下的全部宏（按名称排序；目录不存在返回空表）。"""
    base = directory or DEFAULT_MACRO_DIR
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return []
    result: list[dict] = []
    for entry in entries:
        if not entry.endswith(MACRO_SUFFIX):
            continue
        path = os.path.join(base, entry)
        if not os.path.isfile(path):
            continue
        try:
            macro = load_macro(path=path)
        except MacroError as exc:
            logger.debug("跳过损坏的宏 %s: %s", path, exc)
            continue
        summary = macro.summary()
        summary["path"] = path
        result.append(summary)
    return result


def delete_macro(name: str, directory: str | None = None) -> bool:
    """删除命名宏（不存在返回 False）。"""
    target = os.path.abspath(macro_path(name, directory))
    try:
        os.remove(target)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise MacroError(f"删除宏失败: {exc}") from exc


__all__ = [
    "DEFAULT_MACRO_DIR",
    "MACRO_SUFFIX",
    "MACRO_VERSION",
    "MAX_MACRO_STEPS",
    "MAX_NAME_LENGTH",
    "Macro",
    "MacroError",
    "delete_macro",
    "list_macros",
    "load_macro",
    "macro_path",
    "resolve_macro_path",
    "save_macro",
    "validate_macro_name",
    "validate_steps",
]

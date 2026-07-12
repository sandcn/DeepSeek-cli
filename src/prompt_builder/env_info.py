from __future__ import annotations

import datetime
import logging
import os
import platform
import re


_SUMMARY_CORE_MAX_ITEMS = 4
_SUMMARY_CORE_MAX_LEN = 50
_SUMMARY_TECH_MAX_ITEMS = 3
_SUMMARY_TRUNCATE_LENGTH = 300

_logger = logging.getLogger(__name__)


def _resolve_cwd(cwd: str | None = None) -> str:
    if cwd is None:
        return os.getcwd()
    return cwd


def build_environment_info(
    cwd: str | None = None,
    include_hostname: bool = False,
    include_cwd: bool = False,
) -> str:
    cwd_resolved = _resolve_cwd(cwd)
    today = datetime.date.today()

    lines = [
        f"# 当前执行环境",
        f"- 操作系统: {platform.system()} {platform.release()} ({platform.machine()})",
    ]
    if include_hostname:
        lines.append(f"- 主机名: {platform.node()}")
    lines.append(f"- 日期: {today.year}年{today.month:02d}月{today.day:02d}日")
    lines.append(f"- Python: {platform.python_version()}")
    if include_cwd:
        lines.append(f"- 工作目录: {cwd_resolved}")
    lines.append("")

    return "\n".join(lines) + "\n"


def generate_concise_summary(full_summary: str) -> str:
    lines = full_summary.split('\n')
    concise_parts = []

    for line in lines:
        if line.startswith('# '):
            continue
        stripped = line.strip()
        if stripped and not stripped.startswith('-') and not stripped.startswith('#'):
            concise_parts.append(stripped)
            break

    core_features = _extract_section_items(lines, ['核心功能', '核心功能与特点'],
                                           max_items=_SUMMARY_CORE_MAX_ITEMS, max_len=_SUMMARY_CORE_MAX_LEN)
    if core_features:
        concise_parts.append("核心功能：" + "；".join(core_features))

    tech_items = _extract_section_items(lines, ['技术栈', '编程语言'], max_items=_SUMMARY_TECH_MAX_ITEMS)
    if tech_items:
        concise_parts.append("技术栈：" + "，".join(tech_items))

    if len(concise_parts) < 2:
        return full_summary[:_SUMMARY_TRUNCATE_LENGTH] + "..."

    result = "项目概述：" + "；".join(concise_parts)
    return result[:600] if len(result) > 600 else result


def _extract_section_items(lines: list, section_names: list, max_items: int = 4, max_len: int = 0) -> list:
    items = []
    in_section = False
    for line in lines:
        if any(re.match(r'^#+\s+' + re.escape(name) + r'(\s|$)', line) for name in section_names):
            in_section = True
            continue
        if in_section:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('-') or stripped.startswith('*'):
                item = stripped.lstrip('-* ').replace('**', '')
                if max_len and len(item) > max_len:
                    continue
                items.append(item)
                if len(items) >= max_items:
                    break
            elif not stripped.startswith('-') and not stripped.startswith('*'):
                break
    return items


def build_work_md(name="global.md",summary_mode: str = "concise", cwd: str | None = None) -> str:
    path = os.path.join(cwd, name)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            mdstr = f.read().strip()
    except FileNotFoundError:
        return ""
    except (IOError, OSError, PermissionError) as e:
        _logger.warning("读取 %s 失败: %s",name, e)
        return ""
    if not mdstr:
        return ""
    if summary_mode == "concise":
        return (
            f"{generate_concise_summary(mdstr)}\n"
        )
    return (
        f"{mdstr}\n"
    )

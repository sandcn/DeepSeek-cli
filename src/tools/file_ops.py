"""文件操作工具模块 — 从 tools/utils.py 拆分而来

包含：路径安全校验、文件大小检查、原子写入、异步文件辅助函数等。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import logging

_logger = logging.getLogger(__name__)

from ._constants import (
    DANGEROUS_DEVICE_FILES,
    SYSTEM_CRITICAL_PATHS,
    DOS_DEVICE_NAMES,
    WIN_DEVICE_PREFIXES,
)


def validate_path_security(path):
    """验证路径安全性，不安全时抛出 ValueError"""
    if path is None:
        raise ValueError("缺少必需参数: path")
    if any(path.startswith(p) for p in WIN_DEVICE_PREFIXES):
        raise ValueError(f"不允许写入原始设备路径: {path}")
    normalized = os.path.normpath(path)
    resolved = os.path.realpath(normalized)
    check_path = resolved

    # ★ 先检测具体危险路径（设备文件、系统关键路径等），给出精确错误信息
    if check_path in DANGEROUS_DEVICE_FILES:
        raise ValueError(f"不允许写入特殊设备文件: {check_path}")

    if any(check_path == c or check_path.startswith(c + "/") for c in SYSTEM_CRITICAL_PATHS):
        raise ValueError(f"不允许写入系统关键文件: {check_path}")

    basename = os.path.basename(check_path)
    stem = basename.split(".")[0].upper() if "." in basename else basename.upper()
    if stem in DOS_DEVICE_NAMES:
        raise ValueError(f"不允许写入 DOS 设备名: {check_path}")
    if ":" in basename and os.name == "nt":
        # Windows 上冒号是 NTFS ADS 流语法（"file:stream"）——整体拒绝；
        # POSIX 允许冒号文件名，仅拒绝 $ 流/多冒号形态（下行）
        raise ValueError(f"路径包含 NTFS 流或非法冒号: {check_path}")
    if ":" in basename and (":$" in basename or basename.count(":") > 1):
        raise ValueError(f"路径包含 NTFS 流或非法冒号: {check_path}")


def get_plan_allowed_dir() -> str:
    """返回 plan agent 允许写入的目录（``.chat/plan``，realpath 解析符号链接）。

    plan 白名单三处使用点（file_base._validate_path_and_size、mkdir.execute、
    base.Func.can_use）统一经本函数 + :func:`is_path_within_dir` 校验，
    避免 realpath/abspath 混用导致的符号链接绕过（安全一致性）。
    """
    return os.path.realpath(os.path.join(os.getcwd(), '.chat', 'plan'))


def is_path_within_dir(path: str, allowed_dir: str) -> bool:
    """判断 path（realpath 解析符号链接）是否位于 allowed_dir 目录下。

    - realpath 解析符号链接：防止 ``.chat/plan/link``（指向目录外）绕过白名单
    - commonpath 判断子路径关系：防 ``../`` 穿越
    - 不同驱动器（Windows）等无法比较时返回 False（拒绝）

    Args:
        path: 待校验路径（可相对/绝对/含符号链接）。
        allowed_dir: 允许的目录（应为本函数族生成的 realpath 绝对路径）。

    Returns:
        True — path 在 allowed_dir 下；False — 不在或无法比较。
    """
    real = os.path.realpath(path)
    try:
        return os.path.commonpath([allowed_dir, real]) == allowed_dir
    except ValueError:
        return False




def check_file_size(path, max_mb=100):
    """检查文件大小，超限时抛出 ValueError"""
    try:
        size_mb = os.stat(path).st_size / (1024 * 1024)
        if size_mb > max_mb:
            raise ValueError(f"文件大小({size_mb:.1f}MB)超过最大限制({max_mb}MB)")
    except (OSError, FileNotFoundError):
        # 文件不可读/不存在：跳过大小检查（调用方后续打开文件会报错），
        # 记录 debug 便于排查
        _logger.debug("check_file_size 无法读取文件 %s", path, exc_info=True)


async def async_check_file_size(path: str, max_mb: int = 100) -> None:
    """异步检查文件大小，超限时抛出 ValueError。"""
    try:
        size_bytes = (await asyncio.to_thread(os.stat, path)).st_size
        size_mb = size_bytes / (1024 * 1024)
        if size_mb > max_mb:
            raise ValueError(f"文件大小({size_mb:.1f}MB)超过最大限制({max_mb}MB)")
    except (OSError, FileNotFoundError):
        _logger.debug("async_check_file_size 无法读取文件 %s", path, exc_info=True)


def _copy_file_permissions(src, dst):
    """跨平台复制文件权限。

    ★ 只复制权限位（st_mode & 0o777），不复制 setuid/setgid/sticky
      特殊位——意外传播特权位是安全风险。
    """
    import stat as _stat
    if os.name == 'nt':
        src_ro = not os.access(src, os.W_OK)
        if src_ro:
            os.chmod(dst, _stat.S_IREAD)
    else:
        os.chmod(dst, os.stat(src).st_mode & 0o777)


def atomic_write_file(path, content, encoding='utf-8', errors='replace'):
    """原子写入文件，返回 (lines_count, size_bytes)

    使用 tempfile + os.replace 实现原子写入。
    不依赖 flock 文件锁：同一进程内并发写同一文件已由 ToolDAG 的路径依赖
    分层（写依赖写串行）保证，无需额外互斥；os.replace 本身保证替换原子性。
    """
    fd = None
    temp_path = None
    try:
        dir_path = os.path.dirname(path)
        if not dir_path:
            # 无目录的相对路径（如 "notes.md"）：临时文件必须建在目标同目录
            # （"."），否则 tempfile 建到系统临时目录（可能跨文件系统），
            # os.replace 跨设备（EXDEV）必然失败
            dir_path = "."
        # 在写入前重新校验路径（防 TOCTOU）
        validate_path_security(path)
        fd, temp_path = tempfile.mkstemp(
            prefix=f".~{os.path.basename(path)}.",
            suffix='.tmp',
            dir=dir_path
        )
        with os.fdopen(fd, 'w', encoding=encoding, errors=errors) as f:
            f.write(content)
            fd = None

        if os.path.exists(path):
            try:
                _copy_file_permissions(path, temp_path)
            except OSError:
                pass

        os.replace(temp_path, path)
        temp_path = None

        lines_count = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
        size_bytes = len(content.encode(encoding))
        return lines_count, size_bytes

    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_path is not None and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                _logger.debug("临时文件清理失败: %s", temp_path)
        raise


def get_last_user_message_preview(messages, max_chars=100):
    """从消息列表中提取最后一条用户消息的前 max_chars 个字"""
    for msg in reversed(messages):
        if msg.get("role") == "user" and msg.get("content"):
            # content 可能为 list（多模态 content blocks）——提取文本
            content = msg["content"]
            if isinstance(content, list):
                try:
                    from ..api.multimodal import content_to_text
                    content = content_to_text(content)
                except Exception:
                    content = ""
            text = content.strip() if isinstance(content, str) else ""
            if len(text) <= max_chars:
                return text
            return text[:max_chars] + "..."
    return "聊天已完成"


# ── 异步文件操作辅助函数 ──

async def async_atomic_write(path, content, encoding='utf-8', errors='replace'):
    """异步原子写入文件，委托同步版通过 asyncio.to_thread 执行。"""
    return await asyncio.to_thread(
        atomic_write_file, path, content, encoding, errors,
    )


async def async_read_file_content(path, encoding='utf-8', errors='replace') -> str | None:
    """异步读取文件全部内容"""
    try:
        return await asyncio.to_thread(_sync_read_file, path, encoding, errors)
    except Exception:
        return None


def _sync_read_file(path: str, encoding: str = 'utf-8', errors: str = 'replace') -> str | None:
    """同步读取文件（在 asyncio.to_thread 中执行）"""
    try:
        with open(path, 'r', encoding=encoding, errors=errors) as f:
            return f.read()
    except Exception:
        return None


async def async_file_exists(path: str) -> bool:
    """异步检查文件是否存在"""
    return await asyncio.to_thread(os.path.exists, path)


async def async_file_stat(path: str):
    """异步获取文件状态信息"""
    return await asyncio.to_thread(os.stat, path)


async def async_remove_file(path: str):
    """异步删除文件"""
    return await asyncio.to_thread(os.remove, path)


async def async_makedirs(path: str, exist_ok: bool = True):
    """异步创建目录"""
    return await asyncio.to_thread(os.makedirs, path, exist_ok=exist_ok)


async def async_is_link(path: str) -> bool:
    """异步检查是否为符号链接"""
    return await asyncio.to_thread(os.path.islink, path)


async def async_realpath(path: str) -> str:
    """异步获取真实路径"""
    return await asyncio.to_thread(os.path.realpath, path)


async def async_collect_files(path: str) -> list[str]:
    """异步递归收集目录下的所有文件路径"""
    return await asyncio.to_thread(_sync_collect_files, path)


def _sync_collect_files(path: str) -> list[str]:
    """同步收集文件列表"""
    files = []
    if os.path.isfile(path) or os.path.islink(path):
        files.append(path)
    elif os.path.isdir(path):
        for root, _dirs, filenames in os.walk(path, followlinks=False):
            for filename in filenames:
                files.append(os.path.join(root, filename))
    return files


async def async_record_directory_files(
    source_path: str,
    dest_path: str,
    source_files: list[str],
    tool_name: str,
    dst_existing: dict[str, str | None] | None = None,
    source_deleted: bool = True,
    source_contents: dict[str, str | None] | None = None,
) -> None:
    """记录目录移动/复制操作中所有文件的沙盒变更

    Args:
        source_path: 源目录路径
        dest_path: 目标目录路径
        source_files: 源目录下的所有文件路径列表
        tool_name: 工具名称（如 "mv", "cp"）
        dst_existing: 目标目录中已存在的文件内容映射（可选）
                      格式: {文件路径: 内容或None}
        source_deleted: 源文件是否标记为已删除（True=move删除, False=cp保留）
        source_contents: 预读取的源文件内容映射（可选）。
                         格式: {文件路径: 内容或None}
                         用于 mv 等工具在 move 之后源文件已不可读的场景。
                         不传时回退到 async_read_file_content(fp)。
    """
    # 以 source_path 自身为根计算相对路径，确保 dst_fp 与 cp._build_dest_path 一致
    src_root = os.path.normpath(source_path)

    for fp in source_files:
        rel_path = os.path.relpath(fp, src_root)
        dst_fp = os.path.normpath(os.path.join(dest_path, rel_path))
        if source_contents is not None and fp in source_contents:
            content = source_contents[fp]
        else:
            try:
                content = await async_read_file_content(fp)
            except Exception:
                content = None
        await async_record_sandbox(fp, content, None if source_deleted else content, tool_name)
        if dst_existing is not None:
            await async_record_sandbox(dst_fp, dst_existing.get(dst_fp), content, tool_name)
        else:
            await async_record_sandbox(dst_fp, None, content, tool_name)


# ── 公共沙盒记录函数 ──────────────────────────────────────

_LOGGER_SANDBOX = None


def _get_sandbox_logger():
    global _LOGGER_SANDBOX
    if _LOGGER_SANDBOX is None:
        import logging as _logging
        _LOGGER_SANDBOX = _logging.getLogger("tools.file_ops.sandbox")
    return _LOGGER_SANDBOX


async def async_record_sandbox(
    file_path: str,
    content_before: str | None,
    content_after: str | None,
    tool_name: str,
    record_type: str = "file",
) -> None:
    """记录文件变更到沙盒，捕获并记录异常。"""
    from ..core.sandbox_manager import async_record_file_change_from_context
    try:
        await async_record_file_change_from_context(
            file_path, content_before, content_after, tool_name,
            record_type,
        )
    except Exception as e:
        _get_sandbox_logger().warning("沙盒记录失败 %s: %s", file_path, e)


async def async_makedirs_and_record(path: str, tool_name: str) -> None:
    """创建目录（含父目录）并记录到沙盒，使 /undo 可正确清理。

    与 async_makedirs 的区别：本函数会将所有隐式创建（原本不存在）的
    父目录一并记录到沙盒的 file_history 中，确保 restore_to_message()
    回滚时可以删除这些空目录，避免残留。

    Args:
        path: 要创建的目录路径（类似 mkdir -p）
        tool_name: 触发创建的工具名（如 "write_file"、"cp"）
    """
    import os as _os

    # 收集不存在的父目录（从叶到根，即从 path 向上）
    missing: list[str] = []
    check = _os.path.abspath(path)
    while True:
        if _os.path.exists(check):
            break
        missing.append(check)
        parent = _os.path.dirname(check)
        if parent == check:  # 到达文件系统根目录
            break
        check = parent

    if not missing:
        return  # 全部已存在，无需创建

    # 创建目录
    await async_makedirs(path, exist_ok=True)

    # 从叶到根记录每个新创建的目录到沙盒
    # content_before=None 表示目录原本不存在，content_after="" 表示目录已创建
    for d in missing:
        await async_record_sandbox(d, None, "", tool_name, record_type="directory")

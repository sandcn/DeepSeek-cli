"""文件操作工具模块 — 从 tools/utils.py 拆分而来

包含：路径安全校验、文件大小检查、原子写入、异步文件辅助函数等。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
try:
    import fcntl
except ImportError:
    fcntl = None
import logging

_logger = logging.getLogger(__name__)

# 原子写入锁文件统一存放在系统临时目录，避免污染源码树
_LOCK_DIR = os.path.join(tempfile.gettempdir(), 'chat_atomic_locks')

from ._constants import (
    DANGEROUS_DEVICE_FILES,
    SYSTEM_CRITICAL_PATHS,
    DOS_DEVICE_NAMES,
    WIN_DEVICE_PREFIXES,
)


def validate_path_security(path):
    """验证路径安全性，不安全时抛出 ValueError"""
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
    if ":" in basename and (":$" in basename or basename.count(":") > 1):
        raise ValueError(f"路径包含 NTFS 流或非法冒号: {check_path}")




def check_file_size(path, max_mb=100):
    """检查文件大小，超限时抛出 ValueError"""
    try:
        size_mb = os.stat(path).st_size / (1024 * 1024)
        if size_mb > max_mb:
            raise ValueError(f"文件大小({size_mb:.1f}MB)超过最大限制({max_mb}MB)")
    except (OSError, FileNotFoundError):
        pass


async def async_check_file_size(path: str, max_mb: int = 100) -> None:
    """异步检查文件大小，超限时抛出 ValueError。"""
    try:
        size_bytes = (await asyncio.to_thread(os.stat, path)).st_size
        size_mb = size_bytes / (1024 * 1024)
        if size_mb > max_mb:
            raise ValueError(f"文件大小({size_mb:.1f}MB)超过最大限制({max_mb}MB)")
    except (OSError, FileNotFoundError):
        pass


def _copy_file_permissions(src, dst):
    """跨平台复制文件权限。"""
    import stat as _stat
    if os.name == 'nt':
        src_ro = not os.access(src, os.W_OK)
        if src_ro:
            os.chmod(dst, _stat.S_IREAD)
    else:
        os.chmod(dst, os.stat(src).st_mode)


def atomic_write_file(path, content, encoding='utf-8', errors='replace'):
    """原子写入文件，返回 (lines_count, size_bytes)

    使用 fcntl.flock 互斥锁 + tempfile + os.replace 实现原子写入。
    锁文件统一存放在系统临时目录（_LOCK_DIR），不污染源码树。
    """
    fd = None
    temp_path = None
    lock_f = None
    lock_file_path = None
    try:
        if fcntl is not None:
            os.makedirs(_LOCK_DIR, exist_ok=True)
            # 用绝对路径的 SHA-256 哈希生成唯一锁文件名，避免路径冲突
            abs_path = os.path.abspath(path)
            path_hash = hashlib.sha256(abs_path.encode()).hexdigest()
            lock_file_path = os.path.join(_LOCK_DIR, f".lock_{path_hash}")
            lock_f = open(lock_file_path, 'w')
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)

        dir_path = os.path.dirname(path)
        if not dir_path:
            dir_path = tempfile.gettempdir()
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
    finally:
        if lock_f is not None:
            try:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
            except Exception:
                _logger.warning("解锁文件锁失败: %s", lock_file_path)
            try:
                lock_f.close()
            except Exception:
                _logger.warning("关闭锁文件失败: %s", lock_file_path)
        # 锁文件存于系统临时目录，保留不删无副作用（系统重启自动清理）


def get_last_user_message_preview(messages, max_chars=100):
    """从消息列表中提取最后一条用户消息的前 max_chars 个字"""
    for msg in reversed(messages):
        if msg.get("role") == "user" and msg.get("content"):
            text = msg["content"].strip()
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

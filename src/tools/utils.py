"""工具函数集合 — re-export 来自拆分后各子模块的所有公开接口"""

import asyncio
import os
import subprocess

# re-export 编码检测
from .encoding import (
    detect_encoding,
    async_detect_encoding,
)

# re-export 文件操作
from .file_ops import (
    validate_path_security,
    check_file_size,
    atomic_write_file,
    get_last_user_message_preview,
    async_atomic_write,
    async_read_file_content,
    async_file_exists,
    async_remove_file,
    async_makedirs,
    async_is_link,
    async_collect_files,
)

# re-export 安全常量（从共享常量模块导入）
from ._constants import (
    DANGEROUS_DEVICE_FILES,
    SYSTEM_CRITICAL_PATHS,
    DOS_DEVICE_NAMES,
    WIN_DEVICE_PREFIXES,
)

# ── Termux 通知 ─────────────────────────────────────


def termux_notify(message, title="聊天完成", vibrate=True, duration=10000, notification=True, sound=True, toast=False, gravity="middle", short=False):
    """在Termux环境下发送震动和系统通知"""
    def _check_termux():
        if os.environ.get('TERMUX_VERSION'):
            return True
        try:
            return subprocess.run(['command', '-v', 'termux-api'], capture_output=True, timeout=2).returncode == 0
        except Exception:
            return False

    def _run(cmd):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            return r.returncode == 0, r.stderr or r.stdout or ""
        except subprocess.TimeoutExpired:
            return False, "命令执行超时"
        except FileNotFoundError:
            return False, "命令不存在"
        except Exception as e:
            return False, str(e)

    if not _check_termux():
        return "非Termux环境，跳过通知"

    results = []
    if vibrate:
        ok, out = _run(["termux-vibrate", "-d", str(duration)])
        results.append(f"震动成功 ({duration}ms)" if ok else f"震动失败: {out}")
    if notification:
        cmd = ["termux-notification", "--title", title, "--content", message]
        if sound:
            cmd.append("--sound")
        ok, out = _run(cmd)
        results.append("系统通知发送成功" if ok else f"系统通知失败: {out}")
    if toast:
        cmd = ["termux-toast", "-g", gravity]
        if short:
            cmd.append("-s")
        cmd.append(message)
        ok, out = _run(cmd)
        results.append(f"弹窗显示成功 (位置: {gravity})" if ok else f"弹窗失败: {out}")
    return "\n".join(results) if results else "通知已发送"


async def async_termux_notify(
    message: str,
    title: str = "聊天完成",
    vibrate: bool = True,
    duration: int = 5000,
    notification: bool = True,
    sound: bool = True,
    toast: bool = False,
    gravity: str = "middle",
    short: bool = False,
) -> str:
    """异步版本：在Termux环境下发送震动和系统通知"""

    async def _check_termux() -> bool:
        if os.environ.get('TERMUX_VERSION'):
            return True
        try:
            proc = await asyncio.create_subprocess_exec(
                'command', '-v', 'termux-api',
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            rc = await proc.wait()
            return rc == 0
        except Exception:
            return False

    async def _run(*cmd: str) -> tuple[bool, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return True, ""
            return False, stderr.decode('utf-8', errors='replace') or stdout.decode('utf-8', errors='replace')
        except FileNotFoundError:
            return False, "命令不存在"
        except Exception as e:
            return False, str(e)

    if not await _check_termux():
        return "非Termux环境，跳过通知"

    results = []
    if vibrate:
        ok, out = await _run("termux-vibrate", "-d", str(duration))
        results.append(f"震动成功 ({duration}ms)" if ok else f"震动失败: {out}")
    if notification:
        cmd = ["termux-notification", "--title", title, "--content", message]
        if sound:
            cmd.append("--sound")
        ok, out = await _run(*cmd)
        results.append("系统通知发送成功" if ok else f"系统通知失败: {out}")
    if toast:
        cmd = ["termux-toast", "-g", gravity]
        if short:
            cmd.append("-s")
        cmd.append(message)
        ok, out = await _run(*cmd)
        results.append(f"弹窗显示成功 (位置: {gravity})" if ok else f"弹窗失败: {out}")
    return "\n".join(results) if results else "通知已发送"


# ── 沙盒记录 re-export（实际实现已移至 file_ops.py） ──────
from .file_ops import async_record_sandbox, async_makedirs_and_record  # noqa: F401

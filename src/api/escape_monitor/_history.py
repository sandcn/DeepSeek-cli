"""历史文件读写 + 模块级常量。

子模块：从原 escape_monitor.py 提取的历史文件锁、读写、压缩函数，
以及模块级常量和全局活跃实例。
"""

from __future__ import annotations

import os
import threading
import logging

from ...config.defaults import INPUT_HISTORY_FILE

_logger = logging.getLogger(__name__)

# 监控配置
MONITOR_JOIN_TIMEOUT = 1.0    # 线程 join 超时时间（秒）；stop() 中等待 monitor 线程退出的超时
MONITOR_START_JOIN_TIMEOUT = 2.0  # start() 中等待旧线程退出的 join 超时（秒）
UNIX_SELECT_TIMEOUT = 0.1     # Unix select 超时时间（秒）
WINDOWS_POLL_INTERVAL = 0.05  # Windows 轮询间隔（秒）
_POLL_INTERVAL = 0.1          # ESC序列检测等待超时（秒）— 100ms 确保 ANSI 序列（如 ↑↓箭头）有充足时间到达

# 故障熔断阈值
_EOF_THRESHOLD = 20           # stdin EOF 连续检测阈值：select 可读但 read 返回空的次数
_SELECT_ERROR_THRESHOLD = 30  # select 连续错误阈值：select 持续抛异常的次数

# ── 输入历史多进程写入配置 ────────────────────────────────
_HISTORY_MAX_ENTRIES = 1000       # 内存历史最大条目数
_HISTORY_COMPACT_RATIO = 1.5      # 压缩触发比例：行数 > 去重后*1.5 时触发

# 全局活跃实例（供其他模块暂停/恢复）
_active_monitor = None
_active_monitor_lock = threading.RLock()

# ── 跨进程文件锁辅助函数（输入历史多进程写入） ──────────────

def _lock_history_file(fd: int, shared: bool = False) -> bool:
    """对历史文件加跨进程锁（基于 fcntl.flock）。

    Args:
        fd: 文件描述符（open() 返回的 fileno()）。
        shared: True=共享锁（LOCK_SH，读取用），False=独占锁（LOCK_EX，写入用）。

    Returns:
        True=获取锁成功，False=获取失败（非阻塞跳过，不阻塞 UI）。
    """
    try:
        import fcntl
        op = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        fcntl.flock(fd, op | fcntl.LOCK_NB)
        return True
    except ImportError:
        # Windows: fcntl 不可用，跳过锁（单进程无需锁）
        return True
    except (BlockingIOError, OSError) as exc:
        _logger.warning("历史文件锁获取失败(%s): %s", "共享" if shared else "独占", exc)
        return False

def _unlock_history_file(fd: int) -> None:
    """释放历史文件锁。Windows 降级跳过。"""
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    except ImportError:
        pass  # Windows: fcntl 不可用
    except (ValueError, OSError):
        pass  # fd 已关闭等正常降级

def _read_history_file() -> tuple[str, bool]:
    """加共享锁读取历史文件，保证跨进程读取一致性。

    Returns:
        (content: str, locked: bool)
        content — 文件全部内容（文件不存在返回空字符串）。
        locked  — 是否成功获取文件锁。
    """
    try:
        with open(INPUT_HISTORY_FILE, "r", encoding="utf-8", errors="replace") as f:
            locked = _lock_history_file(f.fileno(), shared=True)
            try:
                content = f.read()
            finally:
                if locked:
                    _unlock_history_file(f.fileno())
        return content, locked
    except (OSError, FileNotFoundError):
        return "", False

def _append_to_history_file(text: str) -> bool:
    """加独占锁追加写入一行到历史文件。

    仅追加当前条目，不覆写整个文件。多进程安全。
    Args:
        text: 已转义（\\n→\\\\n）的单行字符串。
    Returns:
        True=写入成功，False=写入失败（不阻塞 UI）。
    """
    try:
        INPUT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(INPUT_HISTORY_FILE, "a", encoding="utf-8") as f:
            locked = _lock_history_file(f.fileno(), shared=False)
            if not locked:
                return False
            try:
                f.write(text + "\n")
                os.fsync(f.fileno())  # 强制落盘，Android Termux ext4 安全
            finally:
                _unlock_history_file(f.fileno())
        return True
    except OSError as exc:
        _logger.warning("历史文件追加写入失败: %s", exc)
        return False

def _compact_history_file() -> bool:
    """加独占锁压缩历史文件：读取→去重→重写。

    使用两趟 O(n) 算法：
      第一趟：记录每个条目在文件中的最后出现索引。
      第二趟：只保留最后出现的条目，保持原始先后顺序。
    仅在文件行数 > 去重后条数 * _HISTORY_COMPACT_RATIO 时触发重写。

    使用以新换旧策略确保 crash 安全：
      先写入临时文件，再 os.rename() 原子替换原文件。

    Returns:
        True=完成压缩，False=无需压缩/锁失败。
    """
    try:
        with open(INPUT_HISTORY_FILE, "r", encoding="utf-8", errors="replace") as f:
            locked = _lock_history_file(f.fileno(), shared=False)
            if not locked:
                return False
            try:
                raw = f.read()
            finally:
                _unlock_history_file(f.fileno())
    except (OSError, FileNotFoundError):
        return False

    if not raw:
        return False

    lines = raw.splitlines()
    if not lines:
        return False

    # 第一趟 O(n)：记录每个条目在文件中的最后出现索引（含转义后的 unescape）
    latest: dict[str, int] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        latest[stripped] = i

    # 第二趟 O(n)：只保留最后出现的条目，保持原始顺序
    kept: set[str] = set()
    unique: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # 仅在该行是此条目的最后出现时才保留
        if i == latest.get(stripped) and stripped not in kept:
            unique.append(stripped)
            kept.add(stripped)

    if len(lines) <= len(unique) * _HISTORY_COMPACT_RATIO:
        return False  # 无需压缩

    # 使用临时文件原子替换（crash 安全：原文件在 rename 前始终完整）
    try:
        tmp_path = INPUT_HISTORY_FILE.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as tmp:
            tmp.write("\n".join(unique) + "\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.rename(tmp_path, INPUT_HISTORY_FILE)
        _logger.debug("历史文件压缩完成: %d 行 → %d 条", len(lines), len(unique))
        return True
    except OSError as exc:
        _logger.warning("历史文件压缩失败: %s", exc)
        # 清理临时文件
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
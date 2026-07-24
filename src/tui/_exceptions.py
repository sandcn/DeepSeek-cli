"""TUI 异常分类层级 — 可恢复 vs 不可恢复异常统一管理。

异常分类原则：
  - ``RecoverableError``：可安全恢复的异常（渲染降级、终端 I/O 临时失败等）
  - ``FatalError``：不可恢复的异常（配置损坏、资源耗尽等），须终止操作

上下文管理器 ``safe_execute`` / ``safe_execute_silent`` 提供统一异常处理模式：
  - ``safe_execute``：日志记录 + 重新抛出不可恢复异常
  - ``safe_execute_silent``：仅日志记录，所有异常均吞并（适用于非关键路径）

用法::

    from src.tui._exceptions import safe_execute, RecoverableError, FatalError

    with safe_execute("Vertical.child.render"):
        child.render(tmp)

    # 或直接抛异常
    raise FatalError("配置加载失败")
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

_logger = logging.getLogger(__name__)


__all__: list[str] = [
    "TuiError",
    "RecoverableError",
    "FatalError",
    "safe_execute",
    "safe_execute_silent",
]


class TuiError(Exception):
    """TUI 模块异常基类。

    所有 TUI 自定义异常均应继承此类，便于调用方按异常树统一捕获。
    """


class RecoverableError(TuiError):
    """可恢复异常 — 操作失败但可安全降级。

    适用场景：
      - 子控件渲染失败（布局降级为空白）
      - 终端 I/O 操作临时失败（select/read/write 等）
      - EventBus 发布失败（非关键路径可跳过）
      - 终端尺寸查询失败（回退默认值）

    处理策略：记录日志 + 继续执行（降级/跳过）。
    """


class FatalError(TuiError):
    """不可恢复异常 — 操作失败后无法安全继续。

    适用场景：
      - 配置损坏或加载失败
      - 资源耗尽（内存不足等）
      - 核心依赖缺失（必须的第三方库未安装）

    处理策略：记录日志 + 重新抛出（终止当前操作）。
    """


@contextmanager
def safe_execute(
    label: str,
    logger: logging.Logger | None = None,
    exc_info: bool = True,
) -> Generator[None, None, None]:
    """安全执行上下文 — 可恢复异常吞并，不可恢复异常重抛。

    在 ``yield`` 点捕获所有 ``Exception``，按异常类型分派：
      - ``FatalError``/``RecoverableError``：按类本身语义处理（可恢复吞并，不可恢复重抛）
      - 其他 ``Exception``：视为可恢复异常，记录日志后吞并

    Args:
        label: 操作标签，用于日志上下文（如 "Vertical.child.render"）。
        logger: 日志记录器，默认使用模块级 _logger。
        exc_info: 是否在日志中包含异常堆栈，默认 True。

    Yields:
        无，仅提供上下文保护。

    Raises:
        FatalError: 不可恢复异常会重新抛出。
    """
    log = logger or _logger
    try:
        yield
    except FatalError:
        log.error("[FatalError] %s: 不可恢复异常，终止操作", label, exc_info=exc_info)
        raise
    except RecoverableError:
        log.debug("[RecoverableError] %s: 可恢复异常，安全降级", label, exc_info=exc_info)
    except Exception:
        log.debug("[Exception] %s: 视为可恢复异常，安全降级", label, exc_info=exc_info)


@contextmanager
def safe_execute_silent(
    label: str,
    logger: logging.Logger | None = None,
    exc_info: bool = True,
) -> Generator[None, None, None]:
    """安全执行上下文（静默模式）— 所有异常均吞并，仅记录日志。

    与 ``safe_execute`` 的区别：
      - 不区分可恢复/不可恢复，所有 ``Exception`` 均吞并
      - 适用于非关键路径（如清理操作、通知类操作）

    Args:
        label: 操作标签，用于日志上下文。
        logger: 日志记录器，默认使用模块级 _logger。
        exc_info: 是否在日志中包含异常堆栈，默认 True。

    Yields:
        无，仅提供上下文保护。
    """
    log = logger or _logger
    try:
        yield
    except Exception:
        log.debug("[safe_execute_silent] %s: 异常已吞并", label, exc_info=exc_info)

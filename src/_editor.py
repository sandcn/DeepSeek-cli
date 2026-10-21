"""编辑器集成 — 外部编辑器（默认 vim）编辑文本。

★ 2026-10-21（用户需求：user_select 自定义输入支持 Ctrl+G 用 vim 输入）：
实现从 ``src.app_loop._editor`` 下沉到 L0——TUI 表现层（``src.tui``）与
主循环（``src.app_loop``）都需要调用外部编辑器，下沉后避免表现层反向依赖
app_loop，同时保持原导入路径 ``src.app_loop._editor`` 兼容（该模块现为
re-export shim）。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

_logger = logging.getLogger(__name__)


def edit_in_vim_sync(initial_text: str) -> str | None:
    """同步调用外部编辑器（``EDITOR``，默认 vim）编辑文本。

    使用临时文件承接内容；编辑器正常退出后读回文件内容。编辑器未找到/
    启动失败返回 None（调用方保持原文本）。

    Args:
        initial_text: 预填到编辑器中的初始文本。

    Returns:
        编辑后的文本；编辑器不可用或异常时 None。
    """
    tmpfile: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(initial_text)
            tmpfile = f.name
        editor = os.environ.get("EDITOR", "vim")
        editor_path = shutil.which(editor)
        if not editor_path:
            _logger.warning("编辑器未找到: %s（设置 EDITOR 环境变量或安装 vim）", editor)
            return None
        ret = subprocess.call([editor_path, tmpfile])
        if ret != 0:
            _logger.warning("编辑器退出码: %d", ret)
        with open(tmpfile, "r", encoding="utf-8") as f:
            result = f.read()
        return result
    except FileNotFoundError:
        _logger.warning("编辑器未安装（设置 EDITOR 环境变量或安装 vim）")
        return None
    except OSError as e:
        _logger.error("编辑器启动失败: %s", e)
        return None
    finally:
        if tmpfile is not None:
            try:
                os.unlink(tmpfile)
            except OSError:
                pass


__all__ = ["edit_in_vim_sync"]

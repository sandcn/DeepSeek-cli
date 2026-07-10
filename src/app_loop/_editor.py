"""编辑器集成 — vim 编辑功能"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

_logger = logging.getLogger(__name__)


def edit_in_vim_sync(initial_text: str) -> str | None:
    """同步版 vim 编辑 — 在 monitor 线程中直接调用 subprocess.call。"""
    tmpfile: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
            f.write(initial_text)
            tmpfile = f.name
        editor = os.environ.get('EDITOR', 'vim')
        editor_path = shutil.which(editor)
        if not editor_path:
            _logger.warning("vim 编辑器未找到: %s", editor)
            return None
        ret = subprocess.call([editor_path, tmpfile])
        if ret != 0:
            _logger.warning("vim 退出码: %d", ret)
        with open(tmpfile, 'r', encoding='utf-8') as f:
            result = f.read()
        return result
    except FileNotFoundError:
        _logger.warning("vim 未安装，请先安装 vim")
        return None
    except OSError as e:
        _logger.error("vim 编辑失败: %s", e)
        return None
    finally:
        if tmpfile is not None:
            try:
                os.unlink(tmpfile)
            except OSError:
                pass

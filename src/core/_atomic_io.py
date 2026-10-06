#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原子文件 I/O 工具（沙盒恢复 / 文件记录应用共用）。

- 写入：tempfile + os.replace，保证替换原子性；临时文件创建在目标同目录
  （避免跨文件系统 EXDEV）；仅复制权限位（0o777），不传播
  setuid/setgid/sticky 特殊位。
- 删除：统一文件 / 目录 / 符号链接的移除语义（符号链接删链接本身）。

供 ``core.internal.shared._sandbox_history``（沙盒恢复）与
``core.file_change_record``（记录应用/回滚）复用，避免两处重复实现。
"""

from __future__ import annotations

import os
import shutil
import tempfile


def atomic_write_text(
    path: str,
    content: str,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> None:
    """原子写入文本文件（父目录不存在时自动创建）。"""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".~{os.path.basename(path)}.", suffix=".tmp", dir=parent,
    )
    try:
        try:
            fobj = os.fdopen(fd, "w", encoding=encoding, errors=errors)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        with fobj:
            fobj.write(content)
        if os.path.exists(path):
            try:
                os.chmod(tmp_path, os.stat(path).st_mode & 0o777)
            except OSError:
                pass
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def remove_path(path: str) -> None:
    """移除路径（文件 / 目录 / 符号链接）；不存在时静默返回。

    符号链接按链接本身删除（不递归其指向的目标）。
    """
    if os.path.islink(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


__all__ = ["atomic_write_text", "remove_path"]

"""文件系统端口 — 能力接缝（Definition）。

对应 DeepSeek Harness 的 ``ctx.fs``：文件系统是一项可替换能力，由

- **Service Definition**（本协议）声明接口；
- **Service Provider**（默认本地实现 / 远程沙箱实现）实现它；
- **Consumer**（``read_file`` / ``write_file`` / ``rm`` 等工具）使用它。

把 Provider 指向远程沙箱，也就把读写、遍历、删除一并搬了过去，无需
Provider 专用 fork。
"""

from __future__ import annotations

from typing import Any, Dict, List, Protocol, runtime_checkable


@runtime_checkable
class FsPort(Protocol):
    """文件系统接缝协议。"""

    def exists(self, path: str) -> bool:
        """路径是否存在。"""
        ...

    def is_file(self, path: str) -> bool:
        """是否为普通文件。"""
        ...

    def is_dir(self, path: str) -> bool:
        """是否为目录。"""
        ...

    def read_text(self, path: str, *, encoding: str = "utf-8", errors: str = "replace") -> str:
        """读取文本内容。"""
        ...

    def read_bytes(self, path: str) -> bytes:
        """读取二进制内容。"""
        ...

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> None:
        """写入文本内容（原子写 + 权限继承由 Provider 决定）。"""
        ...

    def write_bytes(self, path: str, data: bytes) -> None:
        """写入二进制内容。"""
        ...

    def remove(self, path: str, *, recursive: bool = False) -> None:
        """删除文件或目录。"""
        ...

    def move(self, src: str, dst: str) -> None:
        """移动 / 重命名。"""
        ...

    def mkdir(self, path: str, *, parents: bool = True) -> None:
        """创建目录。"""
        ...

    def list_dir(self, path: str) -> List[str]:
        """列出目录条目名（不含 . 与 ..）。"""
        ...

    def walk_files(self, path: str) -> List[str]:
        """递归列出目录下全部文件路径。"""
        ...

    def stat(self, path: str) -> Dict[str, Any]:
        """返回 ``{size, mtime, is_dir, is_file}``。"""
        ...

    def realpath(self, path: str) -> str:
        """返回规范化绝对路径。"""
        ...

    def size_mb(self, path: str) -> float:
        """文件大小（MB）。"""
        ...


__all__ = ["FsPort"]

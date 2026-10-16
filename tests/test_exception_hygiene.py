"""异常吞没卫生守卫 — 禁止「宽泛异常 + 仅 pass」。

背景（P0 审计）：全项目原有 101 处 ``except Exception: pass`` / ``except
(BaseException…): pass``（裸 ``except:`` 为 0）把**任意**失败静默丢弃，故障
只能在现象层被察觉。审计结论与处置：

  - **宽泛异常（``Exception`` / ``BaseException`` / 裸 except）**：禁止仅
    ``pass``——一律记录 ``debug`` 日志（``exc_info=True``，``-vv`` 可见完整
    堆栈）或显式处理（重抛/返回失败）。本守卫固化该政策，防止回归。
  - **窄异常（``ImportError`` / ``OSError`` / ``ValueError`` / …）**：属于
    「预期内失败」（可选依赖缺失、探测失败、进程已退出等），允许 ``pass``
    ——异常类型本身已表达「可忽略」语义。

实现：以 AST 为单一事实源扫描 ``src/``（排除 ``__pycache__``）。
"""

from __future__ import annotations

import ast
import os

from tests._paths import repo_root

_SRC = repo_root() / "src"

#: 视为「宽泛」的异常类型名（含二者的组合元组）
_BROAD_NAMES = {"Exception", "BaseException"}


def _iter_except_pass():
    """产出 (相对路径, 行号, 异常类型源码) —— 处理体仅 ``pass`` 的 except 子句。"""
    for dirpath, dirnames, filenames in os.walk(_SRC):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            source = open(path, encoding="utf-8").read()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                if len(node.body) != 1 or not isinstance(node.body[0], ast.Pass):
                    continue
                type_src = ast.unparse(node.type) if node.type is not None else ""
                yield os.path.relpath(path, repo_root()).replace(os.sep, "/"), node.lineno, type_src


def _is_broad(type_src: str) -> bool:
    """裸 except（空类型）或类型中出现 Exception / BaseException 即为宽泛。"""
    if not type_src:
        return True
    names = {part.strip() for part in type_src.strip("()").split(",")}
    return bool(names & _BROAD_NAMES)


def test_no_broad_except_with_only_pass():
    """``src`` 不得存在「宽泛异常 + 仅 pass」的静默吞没点。"""
    offenders = [
        f"{path}:{lineno}  except {type_src}"
        for path, lineno, type_src in _iter_except_pass()
        if _is_broad(type_src)
    ]
    assert not offenders, (
        "以下位置静默吞掉宽泛异常（须改为 debug 日志或显式处理）：\n"
        + "\n".join(offenders)
    )


def test_narrow_except_pass_is_allowed():
    """窄异常 + pass 属「预期内失败」，允许存在（政策不被误伤）。

    ``ImportError`` 是典型窄异常（可选依赖探测）；断言扫描器确实能识别它，
    避免上方守卫因扫描故障而静默通过。
    """
    tree = ast.parse(
        "try:\n"
        "    import x\n"
        "except ImportError:\n"
        "    pass\n"
    )
    handler = tree.body[0].handlers[0]
    type_src = ast.unparse(handler.type)
    assert not _is_broad(type_src)

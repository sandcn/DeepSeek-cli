"""glob 工具路径模式（``**`` / ``/``）与大括号展开回归测试。

覆盖：
- ``**/*`` 匹配搜索根下全部文件与目录（不含根自身）；
- ``**/*.py`` 与 ``src/**/*.py`` 的跨层级匹配、``src/*.py`` 的单层匹配；
- 名称模式（``*.py``）保持「任意层级按名匹配」的既有语义；
- ``**`` 结尾（``src/**``）、尾随 ``/``、``./`` 前缀等规范化；
- ``type`` 过滤、空格分隔多模式的 OR 语义；
- 大括号展开（``**/*.{c,h,cpp,hpp,cc,py,mk}``、嵌套、空项、未闭合、上限回退）；
- 排除目录（node_modules / .git）不返回、不深入；
- ``execute()`` 端到端输出格式与计数。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.tools.glob import GlobFunc, _expand_braces, _split_patterns

_ALL_PY = {"a.py", "src/c.py", "src/deep/d.py", "pkg/__init__.py"}


def _make_tree(root: Path) -> None:
    """构造测试目录树（含应被排除的 node_modules / .git）。"""
    (root / "src" / "deep").mkdir(parents=True)
    (root / "pkg").mkdir()
    (root / "node_modules" / "lib").mkdir(parents=True)
    (root / ".git").mkdir()

    (root / "a.py").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")
    (root / "src" / "c.py").write_text("c", encoding="utf-8")
    (root / "src" / "deep" / "d.py").write_text("d", encoding="utf-8")
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "node_modules" / "lib" / "skip.py").write_text("", encoding="utf-8")
    (root / ".git" / "config").write_text("", encoding="utf-8")


def _found(pattern: str, root: Path, **kwargs) -> set[str]:
    """返回匹配结果的相对 POSIX 路径集合。"""
    resolved = root.resolve()
    tool = GlobFunc(pattern, path=str(resolved), **kwargs)
    return {
        path.relative_to(resolved).as_posix()
        for path in tool._sync_find_files(resolved)
    }


def test_globstar_matches_all_entries(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    found = _found("**/*", tmp_path)

    assert {"a.py", "b.txt", "src", "src/c.py", "src/deep", "src/deep/d.py",
            "pkg", "pkg/__init__.py"} <= found
    # 排除目录自身与其内容都不返回
    assert "node_modules" not in found
    assert "node_modules/lib/skip.py" not in found
    assert ".git" not in found
    assert ".git/config" not in found
    # 搜索根自身不参与路径模式匹配
    assert "." not in found


def test_globstar_extension_matches_any_depth(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    assert _found("**/*.py", tmp_path) == _ALL_PY


def test_path_pattern_scoped_to_subdir(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    assert _found("src/**/*.py", tmp_path) == {"src/c.py", "src/deep/d.py"}
    assert _found("src/*.py", tmp_path) == {"src/c.py"}
    assert _found("src/**", tmp_path) == {"src/c.py", "src/deep", "src/deep/d.py"}


def test_basename_pattern_keeps_recursive_semantics(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    # 不含 / 的模式按条目名匹配，默认遍历全部层级（既有语义）
    assert _found("*.py", tmp_path) == _ALL_PY


def test_type_filter(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    assert _found("**/*", tmp_path, type="dir") == {"src", "src/deep", "pkg"}
    assert _found("**/*", tmp_path, type="file") == {
        "a.py", "b.txt", "src/c.py", "src/deep/d.py", "pkg/__init__.py",
    }


def test_pattern_normalization_and_multiple_patterns(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    # ./ 前缀与尾随 / 规范化
    assert _found("./**/*.py", tmp_path) == _ALL_PY
    assert _found("src/", tmp_path) == {"src"}
    # 空格分隔多模式：OR 语义（名称模式 + 路径模式混用）
    assert _found("**/*.txt src/deep/*.py", tmp_path) == {"b.txt", "src/deep/d.py"}


def test_segment_wildcard_does_not_cross_slash(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    # a/*/b 语义：* 只匹配单层目录
    assert _found("src/*/d.py", tmp_path) == {"src/deep/d.py"}
    assert _found("src/*/*.py", tmp_path) == {"src/deep/d.py"}
    # 不应把 src/deep/d.py 当作 src/*.py 的命中
    assert _found("*/*.py", tmp_path) == {"src/c.py", "pkg/__init__.py"}


def test_execute_output(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    output = asyncio.run(GlobFunc("**/*.py", path=str(tmp_path)).execute())

    assert "找到 4 个文件" in output
    assert "src/deep/d.py" in output
    assert "node_modules" not in output


def test_empty_pattern_finds_nothing(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    assert _found("   ", tmp_path) == set()


# ── 大括号展开 ─────────────────────────────────────────


def test_expand_braces_helper() -> None:
    assert _expand_braces("**/*.{c,h}") == ["**/*.c", "**/*.h"]
    assert _expand_braces("a{b,c}d{e,f}") == ["abde", "abdf", "acde", "acdf"]
    assert _expand_braces("{a,}") == ["a", ""]
    assert _expand_braces("{a}") == ["{a}"]          # 无顶层逗号：按字面量
    assert _expand_braces("literal") == ["literal"]
    assert _expand_braces("*.{py") == ["*.{py"]      # 未闭合：按字面量
    assert _expand_braces("a{b,{c,d}}") == ["ab", "ac", "ad"]


def test_split_patterns_respects_braces() -> None:
    assert _split_patterns("*.{c, h} x.py") == ["*.{c, h}", "x.py"]
    # 花括号不平衡时退回普通空格拆分（既有语义）
    assert _split_patterns("*.{c h.py") == ["*.{c", "h.py"]


def test_brace_expansion_extension_group(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    for name in ("src/a.c", "src/b.h", "src/c.cpp", "src/d.hpp", "src/e.cc", "src/f.mk"):
        (tmp_path / name).write_text("", encoding="utf-8")

    found = _found("**/*.{c,h,cpp,hpp,cc,py,mk}", tmp_path)

    assert found == _ALL_PY | {
        "src/a.c", "src/b.h", "src/c.cpp", "src/d.hpp", "src/e.cc", "src/f.mk",
    }


def test_brace_expansion_basename_pattern(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    # 名称模式同样支持展开（任意层级按名匹配）
    assert _found("*.{py,txt}", tmp_path) == _ALL_PY | {"b.txt"}
    # 备选两侧空白被忽略
    assert _found("*.{py, txt}", tmp_path) == _ALL_PY | {"b.txt"}


def test_brace_expansion_nested_and_mixed(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    for name in ("src/x.c", "src/x.h", "src/x.hpp", "src/x.md"):
        (tmp_path / name).write_text("", encoding="utf-8")

    assert _found("src/*.{c,{h,hpp}}", tmp_path) == {
        "src/x.c", "src/x.h", "src/x.hpp",
    }
    # 大括号模式与普通模式空格混用仍为 OR 语义
    assert _found("**/*.{md,rst} src/deep/*.py", tmp_path) == {
        "src/x.md", "src/deep/d.py",
    }


def test_brace_expansion_empty_alternative(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    assert _found("**/*.py{,}", tmp_path) == _ALL_PY


def test_brace_expansion_over_limit_falls_back_to_literal(tmp_path: Path) -> None:
    _make_tree(tmp_path)

    # 12^4 = 20736 个备选，超过展开上限 → 整条按字面量处理，不匹配任何文件
    oversized = "*." + "{a,b,c,d,e,f,g,h,i,j,k,l}" * 4
    assert _found(oversized, tmp_path) == set()


def test_brace_expansion_execute_output(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    (tmp_path / "src" / "main.c").write_text("", encoding="utf-8")
    (tmp_path / "src" / "main.h").write_text("", encoding="utf-8")

    output = asyncio.run(
        GlobFunc("**/*.{c,h,py}", path=str(tmp_path)).execute()
    )

    assert "找到 6 个文件" in output
    assert "src/main.c" in output
    assert "src/main.h" in output

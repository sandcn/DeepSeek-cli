"""操作宏（_window_input/macro.py）单元测试。

覆盖：宏名校验、步骤校验、保存 / 读取 / 列表 / 删除、追加模式、版本校验、
损坏文件与非法参数的错误提示。
"""

from __future__ import annotations

import json

import pytest

from src.tools._window_input.macro import (
    MACRO_VERSION,
    Macro,
    MacroError,
    delete_macro,
    list_macros,
    load_macro,
    macro_path,
    save_macro,
    validate_macro_name,
    validate_steps,
)


def _steps(*kinds) -> tuple:
    return tuple({"op": kind} if kind != "wait" else {"op": "wait", "seconds": 0}
                 for kind in kinds)


# ── 名称 / 步骤校验 ─────────────────────────────────────

def test_validate_macro_name_accepts_reasonable_names():
    assert validate_macro_name(" login ") == "login"
    assert validate_macro_name("导出-1") == "导出-1"


@pytest.mark.parametrize("bad", ["", "   ", "a/b", "a\\b", "a:b", "..", ".", "x" * 100])
def test_validate_macro_name_rejects_invalid(bad):
    with pytest.raises(MacroError):
        validate_macro_name(bad)


def test_validate_steps_requires_non_empty_and_valid_shape():
    validate_steps([{"op": "wait", "seconds": 0}])
    with pytest.raises(MacroError):
        validate_steps([])
    with pytest.raises(MacroError):
        validate_steps([{"foo": "bar"}])


# ── 保存 / 读取 ─────────────────────────────────────────

def test_save_and_load_roundtrip(tmp_path):
    macro = Macro(name="login", steps=_steps("click", "wait"), window="main")
    saved = save_macro(macro, directory=str(tmp_path))
    assert saved.endswith("login.json")
    loaded = load_macro(name="login", directory=str(tmp_path))
    assert loaded.name == "login"
    assert loaded.step_count == 2
    assert loaded.window == "main"
    assert loaded.created and loaded.updated


def test_save_by_explicit_path(tmp_path):
    target = tmp_path / "sub" / "my.json"
    save_macro(Macro(name="my", steps=_steps("click")), path=str(target))
    assert target.is_file()
    loaded = load_macro(path=str(target))
    assert loaded.step_count == 1


def test_append_mode_extends_existing(tmp_path):
    save_macro(Macro(name="m", steps=_steps("click")), directory=str(tmp_path))
    save_macro(Macro(name="m", steps=_steps("wait")), directory=str(tmp_path),
               append=True)
    loaded = load_macro(name="m", directory=str(tmp_path))
    assert loaded.step_count == 2
    assert [step["op"] for step in loaded.steps] == ["click", "wait"]


def test_overwrite_replaces_steps(tmp_path):
    save_macro(Macro(name="m", steps=_steps("click", "click")), directory=str(tmp_path))
    save_macro(Macro(name="m", steps=_steps("wait")), directory=str(tmp_path))
    assert load_macro(name="m", directory=str(tmp_path)).step_count == 1


def test_load_missing_raises(tmp_path):
    with pytest.raises(MacroError):
        load_macro(name="nope", directory=str(tmp_path))
    with pytest.raises(MacroError):
        load_macro()


def test_load_corrupt_file_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(MacroError):
        load_macro(path=str(path))


def test_from_dict_rejects_future_version():
    with pytest.raises(MacroError):
        Macro.from_dict({"version": MACRO_VERSION + 1, "steps": [{"op": "wait",
                                                                 "seconds": 0}]})


def test_load_rejects_invalid_steps(tmp_path):
    path = tmp_path / "weird.json"
    path.write_text(json.dumps({"version": 1, "name": "x", "steps": []}),
                    encoding="utf-8")
    with pytest.raises(MacroError):
        load_macro(path=str(path))


# ── 列表 / 删除 ─────────────────────────────────────────

def test_list_macros_and_delete(tmp_path):
    save_macro(Macro(name="b", steps=_steps("click")), directory=str(tmp_path))
    save_macro(Macro(name="a", steps=_steps("click")), directory=str(tmp_path))
    names = [item["name"] for item in list_macros(str(tmp_path))]
    assert names == ["a", "b"]
    assert delete_macro("a", str(tmp_path)) is True
    assert delete_macro("a", str(tmp_path)) is False
    assert [item["name"] for item in list_macros(str(tmp_path))] == ["b"]


def test_list_macros_missing_dir_returns_empty(tmp_path):
    assert list_macros(str(tmp_path / "none")) == []


def test_list_macros_skips_corrupt(tmp_path):
    (tmp_path / "ok.json").write_text(
        json.dumps({"version": 1, "name": "ok",
                    "steps": [{"op": "wait", "seconds": 0}]}), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    names = [item["name"] for item in list_macros(str(tmp_path))]
    assert names == ["ok"]


def test_macro_path_uses_suffix(tmp_path):
    assert macro_path("x", str(tmp_path)).endswith("x.json")

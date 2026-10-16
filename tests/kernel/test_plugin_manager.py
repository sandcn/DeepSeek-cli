"""插件管理器（plugin list/add/remove）测试。"""

from __future__ import annotations

import pytest

from src.plugins import manager


@pytest.fixture
def isolated_plugins_dir(tmp_path, monkeypatch):
    directory = tmp_path / "plugins"
    monkeypatch.setattr(manager, "_PRIMARY_DIR", str(directory))
    monkeypatch.setattr(
        manager,
        "EXTERNAL_PLUGIN_DIRS",
        (str(directory),),
    )
    return directory


def test_add_and_list_and_remove_plugin(isolated_plugins_dir, tmp_path):
    source = tmp_path / "my_plugin.py"
    source.write_text(
        "from src.kernel import plugin\n"
        "@plugin('my_plugin')\n"
        "def apply(ctx):\n"
        "    pass\n",
        encoding="utf-8",
    )
    name = manager.add_plugin(str(source))
    assert name == "my_plugin.py"
    assert (isolated_plugins_dir / "my_plugin.py").is_file()

    installed = manager._load_registry(str(isolated_plugins_dir))
    assert "my_plugin.py" in installed

    files = manager.discover_plugin_files(str(isolated_plugins_dir))
    assert "my_plugin.py" in files

    assert manager.remove_plugin("my_plugin.py") is True
    assert not (isolated_plugins_dir / "my_plugin.py").exists()
    assert manager.remove_plugin("my_plugin.py") is False


def test_add_plugin_directory(isolated_plugins_dir, tmp_path):
    src_dir = tmp_path / "bundle_plugin"
    src_dir.mkdir()
    (src_dir / "a.py").write_text("x = 1\n", encoding="utf-8")
    name = manager.add_plugin(str(src_dir))
    assert name == "bundle_plugin"
    assert (isolated_plugins_dir / "bundle_plugin" / "a.py").is_file()


def test_add_missing_source_raises(isolated_plugins_dir, tmp_path):
    with pytest.raises(FileNotFoundError):
        manager.add_plugin(str(tmp_path / "nope.py"))


def test_list_plugins_summary_shape(isolated_plugins_dir):
    summary = manager.list_plugins("cli")
    assert summary["profile"] == "cli"
    assert "core" in summary["bundles"]
    assert any(entry["id"] == "core::tools" for entry in summary["plugins"])
    text = manager.format_plugins(summary)
    assert "profile: cli" in text
    assert "core::tools" in text

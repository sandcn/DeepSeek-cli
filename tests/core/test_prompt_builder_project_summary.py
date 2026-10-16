"""src/prompt_builder/project_summary — 项目摘要 prompt 构建单元测试。

覆盖：
  - _redact_sensitive：API key / 长 base64 token 脱敏
  - _scan_project_files：目录/扩展名/隐藏文件排除、优先级排序、深度限制
  - _read_file_contents：token 配额、截断比例、不可读文件跳过
  - _build_summary_prompt / generate_summary_prompt：端到端 prompt 组装
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.prompt_builder.project_summary as ps


# ── _redact_sensitive ─────────────────────────────────────

def test_redact_sk_api_key():
    text = "api_key=sk-abcdefghijklmnopqrstuvwxyz1234567890 end"
    out = ps._redact_sensitive(text)
    assert "[API_KEY_REDACTED]" in out
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in out


def test_redact_short_sk_not_touched():
    text = "sk-abc123"  # 不足 20 位字母数字
    assert ps._redact_sensitive(text) == text


def test_redact_long_base64_token():
    token = "aGVsbG93b3JsZHRoaXNpc2F2ZXJ5bG9uZ2Jhc2U2NHN0cmluZ3ZhbHVlMTIzNDU2Nzg5MA=="
    out = ps._redact_sensitive(f"Bearer {token}")
    assert "[TOKEN_REDACTED]" in out
    assert token not in out


def test_redact_short_plain_text_untouched():
    text = "普通文本 hello world 123"
    assert ps._redact_sensitive(text) == text


def test_redact_multiple_patterns():
    text = "sk-" + "a" * 30 + " and " + "b" * 45
    out = ps._redact_sensitive(text)
    assert "[API_KEY_REDACTED]" in out
    assert "[TOKEN_REDACTED]" in out


# ── _scan_project_files ───────────────────────────────────

@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    """构造含各类文件的临时项目；放宽深度限制便于在深层 tmp 下测试。"""
    monkeypatch.setattr(ps, "_MAX_WALK_DEPTH", 50)
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / "readme.md").write_text("# hi", encoding="utf-8")
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / "secret.pyc").write_bytes(b"x")
    (tmp_path / ".hidden").write_text("secret", encoding="utf-8")
    (tmp_path / "init.md").write_text("init", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "app.ts").write_text("x", encoding="utf-8")
    return tmp_path


def test_scan_excludes_and_sorts(project: Path):
    files, total = ps._scan_project_files(str(project))
    names = [Path(p).name for p, _ in files]
    assert "main.py" in names
    assert "readme.md" in names
    assert "data.json" in names
    # 排除项：扩展名/隐藏/init.md/排除目录
    assert "image.png" not in names
    assert "secret.pyc" not in names
    assert ".hidden" not in names
    assert "init.md" not in names
    assert "dep.js" not in names
    assert "app.ts" in names
    # 优先级排序：py/ts 等代码在 md/json 前
    idx = {name: i for i, name in enumerate(names)}
    assert idx["main.py"] < idx["readme.md"]
    assert idx["app.ts"] < idx["readme.md"]
    assert total > 0


def test_scan_missing_dir_empty():
    files, total = ps._scan_project_files("/nonexistent/xyz/abc")
    assert files == []
    assert total == 0


def test_scan_depth_limit(tmp_path: Path, monkeypatch):
    """超过深度上限的目录被裁剪。"""
    monkeypatch.setattr(ps, "_MAX_WALK_DEPTH", 0)
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("x", encoding="utf-8")
    files, _ = ps._scan_project_files(str(tmp_path))
    assert files == []  # depth 0 连根目录都跳过


# ── _read_file_contents ───────────────────────────────────

def test_read_contents_until_token_limit(tmp_path: Path, monkeypatch):
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text("x" * 100, encoding="utf-8")
    f2.write_text("y" * 100, encoding="utf-8")
    files = [(str(f1), 100), (str(f2), 100)]
    # 每个文件估算 token ~30（ascii 0.3/char），max_tokens=40 → 只读一个 + 截断第二个
    monkeypatch.setattr(ps, "estimate_tokens", lambda s: max(1, int(len(s) * 0.3)))
    contents, tokens = ps._read_file_contents(files, max_tokens=40)
    assert len(contents) == 2
    assert 0 < tokens <= 40


def test_read_contents_truncates_oversized_file(tmp_path: Path, monkeypatch):
    f = tmp_path / "big.py"
    f.write_text("a" * 200, encoding="utf-8")
    files = [(str(f), 200)]
    monkeypatch.setattr(ps, "estimate_tokens", lambda s: max(1, int(len(s) * 0.3)))
    contents, tokens = ps._read_file_contents(files, max_tokens=10)
    assert len(contents) == 1
    assert tokens <= 10
    assert len(contents[0]["content"]) < 200  # 被截断


def test_read_contents_skips_unreadable(tmp_path: Path, monkeypatch):
    missing = tmp_path / "ghost.py"
    files = [(str(missing), 1)]
    contents, tokens = ps._read_file_contents(files)
    assert contents == []
    assert tokens == 0


def test_read_contents_redacts(tmp_path: Path, monkeypatch):
    f = tmp_path / "key.py"
    f.write_text("token=sk-" + "a" * 25, encoding="utf-8")
    files = [(str(f), 1)]
    contents, _ = ps._read_file_contents(files, max_tokens=100)
    assert contents[0]["content"] == "token=[API_KEY_REDACTED]"


def test_read_contents_stops_when_quota_full(tmp_path: Path, monkeypatch):
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text("字" * 1000, encoding="utf-8")  # CJK 高 token 密度且非 base64 可脱敏
    f2.write_text("y", encoding="utf-8")
    monkeypatch.setattr(ps, "estimate_tokens", lambda s: max(1, int(len(s) * 2.5)))
    files = [(str(f1), 1000), (str(f2), 1)]
    contents, _ = ps._read_file_contents(files, max_tokens=5)
    assert len(contents) == 1  # 配额耗尽后第二个文件不再读取


# ── _build_summary_prompt / generate_summary_prompt ───────

def test_build_summary_prompt_contains_sections():
    files_info = [("a.py", 10), ("b.md", 5)]
    contents = [{"path": "a.py", "size": 10, "content": "code"}]
    system, user = ps._build_summary_prompt(contents, files_info, total_size=15)
    assert "2 个文件" in system
    assert "15 字节" in system
    assert "## 文件: a.py" in user
    assert "code" in user


def test_generate_summary_prompt_end_to_end(project: Path):
    prompt = ps.generate_summary_prompt(str(project))
    assert prompt
    assert "项目目标与描述" in prompt
    assert "## 文件:" in prompt
    assert "[API_KEY_REDACTED]" not in prompt  # 已脱敏


def test_generate_summary_prompt_empty_project(tmp_path: Path):
    prompt = ps.generate_summary_prompt(str(tmp_path))
    assert prompt == ""


def test_generate_summary_prompt_default_cwd(monkeypatch, project: Path):
    monkeypatch.chdir(project)
    prompt = ps.generate_summary_prompt()
    assert prompt

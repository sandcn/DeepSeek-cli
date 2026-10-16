"""图片引用识别支持拖放路径形态（引号 / 转义 / file URI / Windows 路径）。

用户需求 2026-10-07：输入框拖入文件后路径以规范化形态（含空格者双引号
包裹）出现在消息文本中；``core.multimodal.extract_image_refs`` 必须能识别
这类路径，否则拖入的图片不会被作为多模态图片输入。
"""

from __future__ import annotations

import base64

from src.core.multimodal import build_user_content_blocks, extract_image_refs

_MODEL = "deepseek-flash"


def _png(tmp_path, name: str = "pic.png"):
    f = tmp_path / name
    f.write_bytes(b"\x89PNG\r\n\x1a\n")
    return f


def test_double_quoted_image_path(tmp_path):
    f = _png(tmp_path, "a b.png")
    refs = extract_image_refs('看下 "%s"' % f)
    assert [r["kind"] for r in refs] == ["local"]
    assert refs[0]["ref"] == str(f)


def test_single_quoted_image_path(tmp_path):
    f = _png(tmp_path, "a b.png")
    refs = extract_image_refs("看下 '%s'" % f)
    assert refs and refs[0]["ref"] == str(f)


def test_backslash_escaped_image_path(tmp_path):
    f = _png(tmp_path, "a b.png")
    escaped = str(f).replace(" ", "\\ ")
    refs = extract_image_refs("看下 %s" % escaped)
    assert refs and refs[0]["ref"] == str(f)


def test_file_uri_image_path(tmp_path):
    f = _png(tmp_path, "a b.png")
    uri = "file://" + str(f).replace(" ", "%20")
    refs = extract_image_refs("看下 %s" % uri)
    assert refs and refs[0]["ref"] == str(f)


def test_trailing_punct_still_stripped(tmp_path):
    f = _png(tmp_path, "a b.png")
    refs = extract_image_refs("看下 '%s'，谢谢" % f)
    assert refs and refs[0]["ref"] == str(f)


def test_tilde_image_path_expanded(tmp_path, monkeypatch):
    f = _png(tmp_path, "pic.png")
    monkeypatch.setenv("HOME", str(tmp_path))
    refs = extract_image_refs("看下 ~/pic.png")
    assert any(r["ref"] == str(f) for r in refs)


def test_quoted_path_replaced_by_placeholder(tmp_path):
    f = _png(tmp_path, "a b.png")
    out = build_user_content_blocks('看下 "%s" 谢谢' % f, _MODEL)
    assert isinstance(out, list)
    assert out[0]["type"] == "text"
    assert "[图片: %s]" % f in out[0]["text"]
    assert '"' not in out[0]["text"]
    url = out[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == f.read_bytes()


def test_multiple_quoted_paths(tmp_path):
    f1 = _png(tmp_path, "a b.png")
    f2 = _png(tmp_path, "c d.png")
    out = build_user_content_blocks("%s\n%s" % ('"%s"' % f1, '"%s"' % f2), _MODEL)
    assert isinstance(out, list)
    assert out[0]["text"].count("[图片:") == 2
    assert len(out) == 3

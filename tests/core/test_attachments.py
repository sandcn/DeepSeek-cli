"""图片附件存储（DSH durable attachment）测试。"""

from __future__ import annotations

import base64

import pytest

from src.core.attachments import (
    AttachmentError,
    attachment_image_block,
    is_attachment_image_block,
    load_block_image,
    load_image,
    resolve_attachment_blocks,
    save_image,
)


def test_save_load_roundtrip():
    data = b"\x89PNG\r\n\x1a\nhello"
    ref = save_image(
        data, "image/png", width=10, height=20, name="a.png",
        original_dimensions={"width": 40, "height": 80},
    )
    assert ref["attachmentId"].startswith("sha256:")
    assert ref["mediaType"] == "image/png"
    assert ref["bytes"] == len(data)
    assert ref["name"] == "a.png"
    assert ref["originalDimensions"] == {"width": 40, "height": 80}

    loaded, meta = load_image(ref["attachmentId"])
    assert loaded == data
    assert meta["mediaType"] == "image/png"
    assert meta["width"] == 10 and meta["height"] == 20


def test_content_addressing_dedup():
    data = b"\x89PNG\r\n\x1a\nsame-bytes"
    a = save_image(data, "image/png", width=1, height=1, name="a.png")
    b = save_image(data, "image/png", width=1, height=1, name="b.png")
    assert a["attachmentId"] == b["attachmentId"]
    # 内容寻址：同一字节只存一份，元数据保留首次写入的展示名
    assert a["name"] == "a.png"


def test_missing_attachment_error():
    with pytest.raises(AttachmentError) as exc:
        load_image("sha256:" + "0" * 64)
    assert exc.value.code == "ATTACHMENT_NOT_FOUND"


def test_invalid_attachment_id():
    with pytest.raises(AttachmentError) as exc:
        load_image("../../etc/passwd")
    assert exc.value.code == "ATTACHMENT_INVALID_ID"


def test_expected_bytes_mismatch():
    ref = save_image(b"abc", "image/png", width=1, height=1)
    with pytest.raises(AttachmentError) as exc:
        load_image(ref["attachmentId"], expected_bytes=999)
    assert exc.value.code == "ATTACHMENT_CORRUPT"


def test_block_helpers():
    ref = save_image(b"abc", "image/jpeg", width=2, height=3)
    block = attachment_image_block(ref)
    assert is_attachment_image_block(block)
    assert not is_attachment_image_block({"type": "image"})
    media, data, _meta = load_block_image(block)
    assert media == "image/jpeg"
    assert data == b"abc"


def test_resolve_attachment_blocks_to_data_uri():
    ref = save_image(b"abc", "image/png", width=1, height=1)
    messages = [{"role": "tool", "content": [
        {"type": "text", "text": "x"},
        attachment_image_block(ref),
    ]}]
    stats = resolve_attachment_blocks(messages)
    assert stats == {"resolved": 1, "missing": 0}
    block = messages[0]["content"][1]
    assert block["type"] == "image_url"
    prefix, b64 = block["image_url"]["url"].split(",", 1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(b64) == b"abc"


def test_resolve_ignores_non_attachment_blocks():
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "text", "text": "hi"},
    ]}]
    assert resolve_attachment_blocks(messages) == {"resolved": 0, "missing": 0}
    assert messages[0]["content"][0]["type"] == "image_url"

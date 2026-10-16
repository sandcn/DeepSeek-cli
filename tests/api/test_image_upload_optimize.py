"""上传前图片优化（折叠 / 压缩 / 编码缓存）测试。

背景：read_image 按原始尺寸返回 base64 PNG，并随对话累积——每轮 API 请求
都会重传整个历史（含全部图片 base64），体积随图片数量线性膨胀，导致多图
后请求极卡。src/api/image_upload.py 在**发送副本**上做折叠 + 压缩 + 缓存，
本文件覆盖其行为与边界（不改 read_image 的「原始尺寸返回」契约）。
"""

from __future__ import annotations

import base64
import copy
import io

import pytest

pytest.importorskip("PIL")

from PIL import Image

from src.api.image_upload import (
    _compress_image_bytes,
    _decode_data_uri,
    clear_upload_cache,
    optimize_messages_for_upload,
)
from src.api.multimodal import build_image_content_blocks


# ── 构造辅助 ──────────────────────────────────────────────

def _png_bytes(w: int = 8, h: int = 6, noise: bool = False) -> bytes:
    img = Image.new("RGB", (w, h))
    if noise:
        import random
        rnd = random.Random(7)
        img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                     for _ in range(w * h)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _rgba_png_bytes(w: int, h: int) -> bytes:
    import random
    rnd = random.Random(11)
    img = Image.new("RGBA", (w, h))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256),
                  rnd.randrange(256)) for _ in range(w * h)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _tool_msg(call_id: str, data: bytes) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": build_image_content_blocks(f"图片 {call_id}", data, "image/png"),
    }


def _messages_with_images(n: int, data_factory) -> list:
    msgs: list = [{"role": "system", "content": "sys"}]
    for i in range(n):
        msgs.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": "read_image", "arguments": "{}"}}],
        })
        msgs.append(_tool_msg(f"c{i}", data_factory(i)))
        msgs.append({"role": "assistant", "content": "ok"})
    return msgs


def _image_blocks(messages: list) -> list:
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "image_url":
                    out.append(b)
    return out


def _placeholders(messages: list) -> int:
    count = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text" \
                        and "已省略" in b.get("text", ""):
                    count += 1
    return count


# ── 折叠 ─────────────────────────────────────────────────

def test_fold_keeps_recent_images():
    """仅保留最近 N 张图片，更早的替换为文本占位。"""
    clear_upload_cache()
    msgs = _messages_with_images(5, lambda i: _png_bytes(8, 6))
    stats = optimize_messages_for_upload(
        msgs, keep_recent=2, max_dimension=0, quality=80)
    assert stats["folded"] == 3
    assert len(_image_blocks(msgs)) == 2
    assert _placeholders(msgs) == 3


def test_fold_zero_means_unlimited():
    """keep_recent=0 表示不折叠（全部保留）。"""
    clear_upload_cache()
    msgs = _messages_with_images(4, lambda i: _png_bytes(8, 6))
    stats = optimize_messages_for_upload(
        msgs, keep_recent=0, max_dimension=0, quality=80)
    assert stats["folded"] == 0
    assert len(_image_blocks(msgs)) == 4


def test_fold_noop_when_within_recent():
    """图片数不超过保留数时不折叠。"""
    clear_upload_cache()
    msgs = _messages_with_images(2, lambda i: _png_bytes(8, 6))
    stats = optimize_messages_for_upload(
        msgs, keep_recent=4, max_dimension=0, quality=80)
    assert stats["folded"] == 0
    assert len(_image_blocks(msgs)) == 2


def test_fold_preserves_text_and_roles():
    """折叠只替换图片块，文本块与消息结构（role/tool_call_id）保持不变。"""
    clear_upload_cache()
    msgs = _messages_with_images(3, lambda i: _png_bytes(8, 6))
    optimize_messages_for_upload(msgs, keep_recent=1, max_dimension=0, quality=80)
    first_text = msgs[2]["content"][0]
    assert first_text["type"] == "text"
    assert first_text["text"] == "图片 c0"
    assert msgs[2]["role"] == "tool"
    assert msgs[2]["tool_call_id"] == "c0"


# ── 压缩 ─────────────────────────────────────────────────

def test_compress_large_image_to_jpeg_within_dimension():
    """大图 → 降采样 + JPEG 重编码，长边不超过上限且体积减小。"""
    clear_upload_cache()
    raw = _png_bytes(640, 480, noise=True)
    assert len(raw) > 64 * 1024
    msgs = [{"role": "tool", "tool_call_id": "c0",
             "content": build_image_content_blocks("big", raw, "image/png")}]
    stats = optimize_messages_for_upload(
        msgs, keep_recent=0, max_dimension=200, quality=70)
    assert stats["compressed"] == 1
    assert stats["bytes_after"] < stats["bytes_before"]
    block = _image_blocks(msgs)[0]
    url = block["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    decoded = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert max(decoded.size) <= 200
    assert decoded.format == "JPEG"


def test_small_image_unchanged():
    """小图（低于压缩阈值）不做处理，data URI 原样保留。"""
    clear_upload_cache()
    raw = _png_bytes(8, 6)
    msgs = [{"role": "tool", "tool_call_id": "c0",
             "content": build_image_content_blocks("small", raw, "image/png")}]
    before = _image_blocks(msgs)[0]["image_url"]["url"]
    stats = optimize_messages_for_upload(
        msgs, keep_recent=0, max_dimension=64, quality=70)
    assert stats["compressed"] == 0
    assert stats["skipped"] == 1
    assert _image_blocks(msgs)[0]["image_url"]["url"] == before


def test_compress_rgba_flattens_to_rgb():
    """带透明通道的大图 → 白底合成后编码为 JPEG（无 alpha）。"""
    clear_upload_cache()
    raw = _rgba_png_bytes(640, 480)
    msgs = [{"role": "tool", "tool_call_id": "c0",
             "content": build_image_content_blocks("rgba", raw, "image/png")}]
    stats = optimize_messages_for_upload(
        msgs, keep_recent=0, max_dimension=128, quality=75)
    assert stats["compressed"] == 1
    url = _image_blocks(msgs)[0]["image_url"]["url"]
    decoded = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert decoded.mode == "RGB"


def test_animated_image_not_compressed():
    """多帧动图跳过压缩（避免丢失动画帧）。"""
    frames = [Image.new("RGB", (64, 64), c) for c in ((255, 0, 0), (0, 255, 0))]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:])
    assert _compress_image_bytes(buf.getvalue(), 32, 70) is None


# ── 开关 / 非 data URI / 缓存 ────────────────────────────

def test_disabled_leaves_messages_untouched():
    """关闭优化时不折叠、不压缩。"""
    clear_upload_cache()
    msgs = _messages_with_images(5, lambda i: _png_bytes(640, 480, noise=True))
    snapshot = copy.deepcopy(msgs)
    stats = optimize_messages_for_upload(
        msgs, enabled=False, keep_recent=1, max_dimension=64, quality=70)
    assert stats == {"folded": 0, "compressed": 0, "skipped": 0,
                     "bytes_before": 0, "bytes_after": 0}
    assert msgs == snapshot


def test_http_image_url_skipped():
    """非 data URI（http 图片 URL）原样保留。"""
    clear_upload_cache()
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
    ]}]
    stats = optimize_messages_for_upload(
        msgs, keep_recent=0, max_dimension=64, quality=70)
    assert stats["compressed"] == 0
    assert stats["skipped"] == 0
    assert msgs[0]["content"][1]["image_url"]["url"] == "https://x/y.png"


def test_cache_reuse_same_bytes():
    """同一图片字节重复优化：结果一致（命中缓存不重复编码）。"""
    clear_upload_cache()
    raw = _png_bytes(640, 480, noise=True)
    msgs1 = [{"role": "tool", "tool_call_id": "c0",
              "content": build_image_content_blocks("a", raw, "image/png")}]
    optimize_messages_for_upload(msgs1, keep_recent=0, max_dimension=200, quality=70)
    url1 = _image_blocks(msgs1)[0]["image_url"]["url"]

    msgs2 = [{"role": "tool", "tool_call_id": "c1",
              "content": build_image_content_blocks("b", raw, "image/png")}]
    optimize_messages_for_upload(msgs2, keep_recent=0, max_dimension=200, quality=70)
    url2 = _image_blocks(msgs2)[0]["image_url"]["url"]
    assert url1 == url2


# ── data URI 解析边界 ────────────────────────────────────

def test_decode_data_uri_rejects_non_image_and_bad_base64():
    assert _decode_data_uri("https://x/y.png") is None
    assert _decode_data_uri("data:text/plain;base64,QQ==") is None
    assert _decode_data_uri("data:image/png;base64,!!!不是base64!!!") is None
    mime, raw = _decode_data_uri("data:image/png;base64,QUJD")
    assert mime == "image/png" and raw == b"ABC"


# ── 调用方语义：发送副本不被原始消息影响 ──────────────────

def test_original_messages_untouched_when_copy_optimized():
    """model_async 的调用模式：先 deepcopy 再优化 → 原始历史不被改动。"""
    clear_upload_cache()
    original = _messages_with_images(5, lambda i: _png_bytes(640, 480, noise=True))
    snapshot = copy.deepcopy(original)
    messages_copy = copy.deepcopy(original)
    optimize_messages_for_upload(
        messages_copy, keep_recent=2, max_dimension=200, quality=70)
    assert original == snapshot


# ── 统计字段 ─────────────────────────────────────────────

def test_stats_keys_and_fold_only():
    """仅折叠时统计正确（无压缩字节统计）。"""
    clear_upload_cache()
    msgs = _messages_with_images(3, lambda i: _png_bytes(8, 6))
    stats = optimize_messages_for_upload(
        msgs, keep_recent=1, max_dimension=0, quality=80)
    assert stats["folded"] == 2
    assert stats["compressed"] == 0
    assert stats["bytes_before"] == 0 and stats["bytes_after"] == 0


def test_empty_messages_returns_zero_stats():
    assert optimize_messages_for_upload([]) == {
        "folded": 0, "compressed": 0, "skipped": 0,
        "bytes_before": 0, "bytes_after": 0,
    }


# ── 配置项注册 ───────────────────────────────────────────

def test_config_keys_registered():
    """新增的四个上传优化配置项已注册（元数据 + 默认值 + 延迟访问）。"""
    from src.config.defaults import CONFIG_KEYS, DEFAULTS
    assert CONFIG_KEYS["IMAGE_UPLOAD_OPTIMIZE"]["rc_path"] == ("image_upload_optimize",)
    assert CONFIG_KEYS["IMAGE_UPLOAD_OPTIMIZE"]["type"] is bool
    for name, path in (
        ("IMAGE_UPLOAD_KEEP_RECENT", ("image_upload_keep_recent",)),
        ("IMAGE_UPLOAD_MAX_DIMENSION", ("image_upload_max_dimension",)),
        ("IMAGE_UPLOAD_QUALITY", ("image_upload_quality",)),
    ):
        assert CONFIG_KEYS[name]["rc_path"] == path
        assert CONFIG_KEYS[name]["type"] is int
    assert DEFAULTS["image_upload_optimize"] is True
    assert DEFAULTS["image_upload_keep_recent"] == 4
    from src import config as _config
    assert isinstance(_config.IMAGE_UPLOAD_OPTIMIZE, bool)
    assert isinstance(_config.IMAGE_UPLOAD_MAX_DIMENSION, int)


# ── 端到端接线：call_model_async 发送前确实瘦身 ──────────

class _FakeAdapter:
    """最小适配器：仅提供入口所需的两个方法。"""

    def prepare_messages(self, messages, model):
        return messages

    def is_reasoner_model(self, model):
        return False


async def test_call_model_async_optimizes_before_send(monkeypatch):
    """call_model_async 发送前执行图片瘦身，且不改动原始历史。"""
    import src.api.model_async as ma

    clear_upload_cache()
    monkeypatch.setattr(
        "src.api.image_upload._cfg_bool", lambda name, default: True)
    monkeypatch.setattr(
        "src.api.image_upload._cfg_int",
        lambda name, default: 2 if name == "IMAGE_UPLOAD_KEEP_RECENT" else default,
    )

    captured: dict = {}

    async def _fake_retry(func, **kwargs):
        captured["messages"] = kwargs["api_args"][0]
        return "", "", {"input": 0, "output": 0}, []

    monkeypatch.setattr(ma, "retry_on_parse_failure_async", _fake_retry)
    monkeypatch.setattr(ma, "get_adapter", lambda model: _FakeAdapter())

    original = _messages_with_images(5, lambda i: _png_bytes(8, 6))
    await ma.call_model_async(original, model="deepseek-flash")

    sent = captured["messages"]
    assert len(_image_blocks(sent)) == 2      # 发送副本已折叠到最近 2 张
    assert len(_image_blocks(original)) == 5  # 原始历史不受影响

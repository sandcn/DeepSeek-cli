"""deepseek-v4-flash-vision-exp 接入测试（2026-08-22）。

覆盖：
- 默认模型列表 / token 价格包含 deepseek-v4-flash-vision-exp
- V4 模型判定（is_deepseek_v4_model）与多模态判定（is_multimodal_model）
- 用户消息图片输入（build_user_content_blocks：本地路径 base64 / URL /
  非多模态模型回退纯文本）
- 图片引用提取（extract_image_refs：Markdown / 裸 URL / 本地路径）
- 消息 content 兼容辅助（content_to_text：str / list[dict]）
"""

from __future__ import annotations

import base64
import io

import pytest

from src.config.defaults import PROVIDERS
from src.api.adapters._utils import is_deepseek_v4_model
from src.api.multimodal import (
    is_multimodal_model, clear_multimodal_cache,
    build_user_content_blocks, extract_image_refs, content_to_text,
)

_VISION_MODEL = "deepseek-v4-flash-vision-exp"


def _make_image_bytes(w: int = 2, h: int = 2, fmt: str = "PNG") -> bytes:
    """生成简单测试图像字节。"""
    PILImage = pytest.importorskip("PIL.Image")
    img = PILImage.new("RGBA", (w, h), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ── 1. 模型列表 / 价格 ────────────────────────────────

def test_deepseek_provider_models_include_vision():
    """deepseek provider 默认模型列表包含 vision 模型。"""
    assert _VISION_MODEL in PROVIDERS["deepseek"]["models"]


def test_deepseek_provider_token_prices_include_vision():
    """vision 模型价格与 V4-Flash 一致（input 0.55 / output 2.19）。"""
    prices = PROVIDERS["deepseek"]["token_prices"][_VISION_MODEL]
    assert prices["input"] == 0.55
    assert prices["output"] == 2.19
    assert prices["input_cache_hit"] == 0.07
    assert prices == PROVIDERS["deepseek"]["token_prices"]["deepseek-v4-flash"]


# ── 2. 模型判定 ───────────────────────────────────────

def test_is_v4_model_vision():
    """deepseek-v4-flash-vision-exp 属于 V4 系列（注入 thinking 参数）。"""
    assert is_deepseek_v4_model(_VISION_MODEL) is True


def test_is_multimodal_model_vision():
    """deepseek-v4-flash-vision-exp 判定为多模态；deepseek-v4-flash 不是。"""
    clear_multimodal_cache()
    assert is_multimodal_model(_VISION_MODEL) is True
    assert is_multimodal_model("deepseek-v4-flash") is False
    assert is_multimodal_model("deepseek-v4-pro") is False
    clear_multimodal_cache()


# ── 3. 图片引用提取 ───────────────────────────────────

def test_extract_image_refs_markdown_local(tmp_path):
    """Markdown 图片语法提取本地路径。"""
    p = tmp_path / "a.png"
    p.write_bytes(_make_image_bytes())
    refs = extract_image_refs(f"看图 ![示例]({p}) 谢谢")
    assert len(refs) == 1
    assert refs[0]["kind"] == "local"
    assert refs[0]["alt"] == "示例"
    assert refs[0]["ref"] == str(p)


def test_extract_image_refs_markdown_url():
    """Markdown 图片语法提取 http(s) URL。"""
    refs = extract_image_refs("![图](https://example.com/x.png?size=1)")
    assert len(refs) == 1
    assert refs[0]["kind"] == "url"
    assert refs[0]["ref"] == "https://example.com/x.png?size=1"


def test_extract_image_refs_raw_url():
    """裸 http(s) 图片 URL 提取。"""
    refs = extract_image_refs("请看 https://example.com/photo.jpg 这张图")
    assert len(refs) == 1
    assert refs[0]["kind"] == "url"
    assert refs[0]["ref"] == "https://example.com/photo.jpg"


def test_extract_image_refs_local_path(tmp_path):
    """裸本地图片路径提取（文件必须存在）。"""
    p = tmp_path / "b.webp"
    p.write_bytes(_make_image_bytes(fmt="WEBP"))
    refs = extract_image_refs(f"分析 {p}")
    assert len(refs) == 1
    assert refs[0]["kind"] == "local"
    assert refs[0]["ref"] == str(p)


def test_extract_image_refs_no_false_positive(tmp_path):
    """不存在的路径 / 非图片扩展名 / 普通 URL 不误判。"""
    assert extract_image_refs("文件不存在 /no/such.png") == []
    assert extract_image_refs("普通文本 a.txt 而已") == []
    assert extract_image_refs("访问 https://example.com/page 试试") == []


# ── 4. 用户消息图片输入 ───────────────────────────────

def test_build_user_blocks_non_multimodal_returns_text():
    """非多模态模型：原样返回纯文本。"""
    out = build_user_content_blocks("看图 ![a](x.png)", "deepseek-v4-flash")
    assert out == "看图 ![a](x.png)"


def test_build_user_blocks_no_image_ref_returns_text():
    """多模态模型但无图片引用：原样返回纯文本。"""
    out = build_user_content_blocks("普通提问", _VISION_MODEL)
    assert out == "普通提问"


def test_build_user_blocks_local_image(tmp_path):
    """多模态模型 + 本地图片路径 → text + image_url(base64) blocks。"""
    p = tmp_path / "c.png"
    p.write_bytes(_make_image_bytes())
    text = f"分析这张图 {p} 并总结"
    out = build_user_content_blocks(text, _VISION_MODEL)
    assert isinstance(out, list)
    assert out[0]["type"] == "text"
    assert "分析这张图" in out[0]["text"]
    assert "[图片:" in out[0]["text"]
    url = out[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    # 解码验证与源文件一致
    b64 = url.split(",", 1)[1]
    assert base64.b64decode(b64) == p.read_bytes()


def test_build_user_blocks_url():
    """多模态模型 + 图片 URL → image_url 原样保留。"""
    out = build_user_content_blocks(
        "看这个 https://example.com/photo.jpg 怎么样", _VISION_MODEL,
    )
    assert isinstance(out, list)
    assert out[1]["type"] == "image_url"
    assert out[1]["image_url"]["url"] == "https://example.com/photo.jpg"


def test_build_user_blocks_missing_local_image_keeps_text(tmp_path):
    """本地图片不存在（无法读取）→ 跳过转换，返回原文本。"""
    text = f"看下 {tmp_path / 'no.png'} 文件"
    out = build_user_content_blocks(text, _VISION_MODEL)
    assert out == text


# ── 5. content_to_text 兼容辅助 ───────────────────────

def test_content_to_text_str():
    assert content_to_text("hello") == "hello"
    assert content_to_text(None) == ""
    assert content_to_text("") == ""


def test_content_to_text_blocks():
    """list[dict] content blocks → 提取文本部分（图片仅占位标记，不含 data URI）。"""
    content = [
        {"type": "text", "text": "第一段"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
        {"type": "text", "text": "第二段"},
    ]
    text = content_to_text(content)
    assert "第一段" in text
    assert "第二段" in text
    assert "[图片]" in text
    assert "base64" not in text
    assert "data:image" not in text


def test_content_to_text_non_text_blocks():
    """未知 block 类型：尝试 text 字段，无则跳过不崩溃。"""
    content = [
        {"type": "file", "file_id": "file-api-xxx"},
        {"type": "text", "text": "正文"},
    ]
    assert content_to_text(content) == "正文"


# ── 6. Agent 用户消息集成 ─────────────────────────────

def test_agent_add_user_message_vision_blocks(tmp_path, monkeypatch):
    """BaseAgent.add_user_message 在 vision 模型下将图片路径转为 content blocks。"""
    from src.core.base_agent import BaseAgent
    p = tmp_path / "d.png"
    p.write_bytes(_make_image_bytes())
    agent = BaseAgent()
    agent.model = _VISION_MODEL
    agent.add_user_message(f"看下 {p}")
    msg = agent.messages[-1]
    assert msg["role"] == "user"
    assert isinstance(msg["content"], list)
    assert msg["content"][0]["type"] == "text"
    assert msg["content"][1]["type"] == "image_url"


def test_agent_add_user_message_non_vision_plain(monkeypatch):
    """非多模态模型：add_user_message 保持纯文本 content。"""
    from src.core.base_agent import BaseAgent
    agent = BaseAgent()
    agent.model = "deepseek-v4-flash"
    agent.add_user_message("看下 /no/such.png 文件")
    msg = agent.messages[-1]
    assert msg["role"] == "user"
    assert msg["content"] == "看下 /no/such.png 文件"

"""read_image 工具测试（2026-08-19）。

覆盖：
- RGBA 十六进制输出（非多模态模型 / format=rgba_hex）
- 多模态输出（format=multimodal / 多模态模型 auto 检测 → result_blocks）
- 分块读取（start_x/start_y/end_x/end_y 像素区域裁剪）
- 图像操作（grayscale / rotate / flip / scale）
- 错误路径（文件不存在 / 非图像 / 危险路径拒绝）
- ToolResult 机制（_run_tool_func 包装 + _append_tool_result 展开）
- Anthropic tool_result image block 转换
- is_multimodal_model 检测（模式匹配 + 配置扩展）
"""

from __future__ import annotations

import asyncio
import base64
import io
import os

import pytest

pytest.importorskip("PIL")  # read_image 依赖 Pillow，未安装时跳过全部测试

from src.tools.read_image import ReadImageFunc
from src.tools.base import ToolResult, to_tool_text
from src.core.base_agent import BaseAgent
from src.core.tool_executor_async import ToolScheduler
from src.api.multimodal import (
    is_multimodal_model, build_image_content_blocks, clear_multimodal_cache,
)
from src.api.adapters.anthropic import (
    AnthropicAdapter, _convert_tool_content_blocks,
)


def _make_image_bytes(w: int = 4, h: int = 3, fmt: str = "PNG") -> bytes:
    """生成简单 RGBA 测试图像（每像素颜色与坐标相关）。"""
    from PIL import Image
    img = Image.new("RGBA", (w, h))
    for y in range(h):
        for x in range(w):
            img.putpixel((x, y), (x * 60 % 256, y * 80 % 256, 30, 255))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _write_test_image(path, w: int = 4, h: int = 3) -> str:
    with open(path, "wb") as f:
        f.write(_make_image_bytes(w, h))
    return str(path)


# ── 1. RGBA 十六进制输出 ─────────────────────────────

async def test_rgba_output_shape(tmp_path, monkeypatch):
    """非多模态模型（auto）→ RGBA 十六进制字符，按图像宽度逐行。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    out = await ReadImageFunc(path=p, format="auto").execute()
    lines = out.splitlines()
    assert any(l.startswith("图片:") for l in lines)
    assert any("RGBA 十六进制" in l for l in lines)
    # 像素行：每行 2 个 #RRGGBBAA
    pixel_rows = [l for l in lines if l.startswith("#")]
    assert len(pixel_rows) == 2
    for row in pixel_rows:
        px = row.split()
        assert len(px) == 2
        for p in px:
            assert len(p) == 9 and p[0] == "#"
            int(p[1:], 16)


async def test_rgba_output_explicit_format(tmp_path, monkeypatch):
    """format=rgba_hex 强制 RGBA 输出（即使模型支持多模态）。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: True)
    p = _write_test_image(tmp_path / "t.png", 1, 1)
    f = ReadImageFunc(path=p, format="rgba_hex")
    out = await f.execute()
    assert "RGBA 十六进制" in out
    assert f.result_blocks is None


async def test_rgba_max_dimension_capped(tmp_path, monkeypatch):
    """RGBA 模式超出 64 上限时自动缩小到 64。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "big.png", 100, 100)
    out = await ReadImageFunc(path=p, max_dimension=512).execute()
    # 100x100 缩小到 64x64 → 64 行像素
    pixel_rows = [l for l in out.splitlines() if l.startswith("#")]
    assert len(pixel_rows) == 64


async def test_rgba_output_volume_hint(tmp_path, monkeypatch):
    """RGBA 输出携带体积估算提示（引导模型控制上下文占用）。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "t.png", 4, 3)
    out = await ReadImageFunc(path=p).execute()
    assert "RGBA 输出约" in out


async def test_pixel_area_limit(tmp_path, monkeypatch):
    """超大像素面积（>5000 万）解码前拒绝，避免内存耗尽。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    # 8000x7000 = 5600 万像素 > 工具上限 5000 万，但低于 Pillow
    # decompression bomb 默认阈值（约 8947 万）→ 不触发 bomb 警告
    p = tmp_path / "huge.png"
    from PIL import Image
    Image.new("RGB", (8000, 7000)).save(str(p), format="PNG")
    out = await ReadImageFunc(path=str(p)).execute()
    assert out.startswith("(读取失败: 图像过大")


async def test_result_blocks_reset_on_failure(tmp_path, monkeypatch):
    """同一实例：多模态成功后失败路径重置 result_blocks（防残留误包装）。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: True)
    p = _write_test_image(tmp_path / "t.png")
    f = ReadImageFunc(path=p, format="multimodal")
    out = await f.execute()
    assert f.result_blocks is not None
    # 同一实例改为读不存在的文件 → 失败路径必须清除 result_blocks
    f.path = str(tmp_path / "nope.png")
    out2 = await f.execute()
    assert out2.startswith("(文件不存在:")
    assert f.result_blocks is None


# ── 2. 多模态输出 ────────────────────────────────────

async def test_multimodal_output_sets_result_blocks(tmp_path, monkeypatch):
    """多模态模型（auto）→ 设置 result_blocks（text + image_url data URI）。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: True)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    f = ReadImageFunc(path=p, format="auto")
    out = await f.execute()
    assert "多模态(base64 PNG)" in out
    blocks = f.result_blocks
    assert isinstance(blocks, list) and len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "image_url"
    url = blocks[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    # base64 可解码为 PNG 签名
    b64 = url.split(",", 1)[1]
    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


async def test_multimodal_explicit_format(tmp_path, monkeypatch):
    """format=multimodal 强制多模态输出（即使模型不支持）。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "t.png")
    f = ReadImageFunc(path=p, format="multimodal")
    out = await f.execute()
    assert f.result_blocks is not None
    assert f.result_blocks[1]["type"] == "image_url"


# ── 3. 分块读取（区域裁剪） ──────────────────────────

async def test_region_crop(tmp_path, monkeypatch):
    """start_x/start_y/end_x/end_y 指定区域裁剪（类似 read_file 行号范围）。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "t.png", 4, 3)
    f = ReadImageFunc(path=p, start_x=1, start_y=0, end_x=2, end_y=1)
    out = await f.execute()
    assert "区域: (1,0)-(2,1)" in out
    # 2x2 区域 → 2 行像素，每行 2 个
    pixel_rows = [l for l in out.splitlines() if l.startswith("#")]
    assert len(pixel_rows) == 2
    assert len(pixel_rows[0].split()) == 2


async def test_region_reversed_swaps(tmp_path, monkeypatch):
    """start > end 时自动交换（与 read_file 语义一致）。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "t.png", 4, 3)
    f = ReadImageFunc(path=p, start_x=3, end_x=0, start_y=2, end_y=0)
    out = await f.execute()
    pixel_rows = [l for l in out.splitlines() if l.startswith("#")]
    assert len(pixel_rows) == 3  # 全图 4x3
    assert len(pixel_rows[0].split()) == 4


# ── 4. 图像操作 ──────────────────────────────────────

async def test_operation_grayscale(tmp_path, monkeypatch):
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "t.png", 1, 1)  # (0,0) = (0,0,30,255)
    f = ReadImageFunc(path=p, operation="grayscale")
    out = await f.execute()
    assert "操作: grayscale" in out
    # 灰度 (0,0,30) → L = 0.299*0 + 0.587*0 + 0.114*30 ≈ 3
    pixel_rows = [l for l in out.splitlines() if l.startswith("#")]
    px = pixel_rows[0].split()[0]
    r, g, b, a = (int(px[i:i + 2], 16) for i in (1, 3, 5, 7))
    assert r == g == b
    assert a == 255


async def test_operation_rotate90(tmp_path, monkeypatch):
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "t.png", 4, 2)
    f = ReadImageFunc(path=p, operation="rotate90")
    out = await f.execute()
    assert "操作: rotate90" in out
    # 4x2 顺时针旋转 → 2x4
    pixel_rows = [l for l in out.splitlines() if l.startswith("#")]
    assert len(pixel_rows) == 4
    assert len(pixel_rows[0].split()) == 2


async def test_operation_flip_h(tmp_path, monkeypatch):
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "t.png", 2, 1)
    f = ReadImageFunc(path=p, operation="flip_h")
    out = await f.execute()
    assert "操作: flip_h" in out
    pixel_rows = [l for l in out.splitlines() if l.startswith("#")]
    px0 = pixel_rows[0].split()[0]
    px1 = pixel_rows[0].split()[1]
    # 原图 (0,0)=(0,0,30,255), (1,0)=(60,0,30,255)；翻转后左右交换
    r0 = int(px0[1:3], 16)
    r1 = int(px1[1:3], 16)
    assert r0 == 60 and r1 == 0


async def test_operation_scale(tmp_path, monkeypatch):
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "t.png", 4, 2)
    f = ReadImageFunc(path=p, operation="scale", scale_width=8, scale_height=4)
    out = await f.execute()
    assert "操作: scale" in out
    pixel_rows = [l for l in out.splitlines() if l.startswith("#")]
    assert len(pixel_rows) == 4
    assert len(pixel_rows[0].split()) == 8


async def test_operation_scale_proportional(tmp_path, monkeypatch):
    """scale 只给一个维度时按比例计算另一维。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "t.png", 4, 2)
    f = ReadImageFunc(path=p, operation="scale", scale_width=8)
    out = await f.execute()
    pixel_rows = [l for l in out.splitlines() if l.startswith("#")]
    assert len(pixel_rows) == 4  # 2 * (8/4) = 4


async def test_operation_invalid_falls_back(tmp_path, monkeypatch):
    """非法 operation 回退 none。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: False)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    f = ReadImageFunc.from_args({"path": p, "operation": "blur_xyz"})
    assert f.operation == "none"
    out = await f.execute()
    assert "操作: none" in out


# ── 5. 错误路径 ──────────────────────────────────────

async def test_file_not_exists(tmp_path):
    out = await ReadImageFunc(path=str(tmp_path / "nope.png")).execute()
    assert out.startswith("(文件不存在:")


async def test_not_an_image(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("hello", encoding="utf-8")
    out = await ReadImageFunc(path=str(p)).execute()
    assert out.startswith("(读取失败:")


async def test_dangerous_path_rejected():
    with pytest.raises(ValueError):
        ReadImageFunc(path="/dev/null")


async def test_missing_path_raises():
    with pytest.raises(ValueError):
        ReadImageFunc.from_args({})


# ── 6. ToolResult 机制 ───────────────────────────────

async def test_run_tool_func_wraps_tool_result():
    """_run_tool_func：工具设置 result_blocks → 包装为 ToolResult。"""
    scheduler = ToolScheduler()

    class FakeFunc:
        result_blocks = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]

        async def execute(self):
            return "文本摘要"

    out, ok = await scheduler._run_tool_func(FakeFunc(), {"name": "read_image"}, None)
    assert ok is True
    assert isinstance(out, ToolResult)
    assert out.text == "文本摘要"
    assert out.to_content() == [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]


async def test_run_tool_func_run_method_tuple():
    """run_method 返回 tuple 时同样包装。"""

    async def run_method(func, tc):
        return ("文本", True)

    scheduler = ToolScheduler()

    class FakeFunc:
        result_blocks = [{"type": "image_url", "image_url": {"url": "u"}}]

        async def execute(self):
            return "x"

    out, ok = await scheduler._run_tool_func(FakeFunc(), {}, run_method)
    assert isinstance(out, ToolResult)
    assert out.text == "文本"
    assert ok is True


async def test_run_tool_func_no_blocks_unchanged():
    """未设置 result_blocks → 原样返回 str。"""
    scheduler = ToolScheduler()

    class FakeFunc:
        result_blocks = None

        async def execute(self):
            return "普通文本"

    out, ok = await scheduler._run_tool_func(FakeFunc(), {}, None)
    assert out == "普通文本" and ok is True


def test_append_tool_result_expands_tool_result():
    """_append_tool_result：ToolResult 展开为 content blocks。"""
    agent = BaseAgent()
    tr = ToolResult(text="t", blocks=[{"type": "image_url", "image_url": {"url": "u"}}])
    agent._append_tool_result("call-1", tr)
    assert agent.messages[-1]["role"] == "tool"
    assert agent.messages[-1]["tool_call_id"] == "call-1"
    assert agent.messages[-1]["content"] == [
        {"type": "image_url", "image_url": {"url": "u"}}]


def test_append_tool_result_str_unchanged():
    """_append_tool_result：str content 原样保留（原有行为）。"""
    agent = BaseAgent()
    agent._append_tool_result("call-1", "普通结果")
    assert agent.messages[-1]["content"] == "普通结果"


def test_append_tool_result_blocks_list_direct():
    """_append_tool_result：list content 直接作为 content blocks。"""
    agent = BaseAgent()
    blocks = [{"type": "image_url", "image_url": {"url": "u"}}]
    agent._append_tool_result("call-1", blocks)
    assert agent.messages[-1]["content"] == blocks


def test_to_tool_text():
    """to_tool_text：ToolResult → text，str 原样。"""
    assert to_tool_text(ToolResult(text="a", blocks=[])) == "a"
    assert to_tool_text("b") == "b"
    assert to_tool_text(None) == ""


# ── 7. Anthropic 转换 ────────────────────────────────

def test_convert_tool_content_blocks_image_url():
    """OpenAI image_url data URI → Anthropic image block。"""
    blocks = [
        {"type": "text", "text": "图片信息"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    converted = _convert_tool_content_blocks(blocks)
    assert converted[0] == {"type": "text", "text": "图片信息"}
    assert converted[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"},
    }


def test_convert_tool_content_blocks_bad_url_skipped():
    """非法 image_url（非 data URI）跳过，不中断转换。"""
    blocks = [
        {"type": "image_url", "image_url": {"url": "http://example.com/a.png"}},
        {"type": "text", "text": "ok"},
    ]
    converted = _convert_tool_content_blocks(blocks)
    assert converted == [{"type": "text", "text": "ok"}]


def test_anthropic_adapter_messages_tool_blocks():
    """AnthropicAdapter._convert_messages：tool 消息 content list → tool_result blocks。"""
    adapter = AnthropicAdapter()
    blocks = [
        {"type": "text", "text": "图片信息"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
    ]
    system, msgs = adapter._convert_messages([
        {"role": "user", "content": "看图"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_image", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": blocks},
    ])
    last = msgs[-1]
    assert last["role"] == "user"
    tr = last["content"][0]
    assert tr["type"] == "tool_result"
    assert tr["tool_use_id"] == "c1"
    assert tr["content"][0] == {"type": "text", "text": "图片信息"}
    assert tr["content"][1]["type"] == "image"
    assert tr["content"][1]["source"]["data"] == "QUJD"


def test_anthropic_adapter_tool_str_unchanged():
    """AnthropicAdapter：tool 消息 str content 原样保留（原有行为）。"""
    adapter = AnthropicAdapter()
    _, msgs = adapter._convert_messages([
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "文件内容"},
    ])
    assert msgs[-1]["content"][0]["content"] == "文件内容"


# ── 8. 多模态检测 ────────────────────────────────────

def test_multimodal_model_patterns():
    clear_multimodal_cache()
    assert is_multimodal_model("claude-sonnet-4-6")
    assert is_multimodal_model("claude-opus-4-6")
    assert is_multimodal_model("gpt-4o")
    assert is_multimodal_model("glm-5.2")
    assert is_multimodal_model("qwen2.5-vl-7b")
    assert is_multimodal_model("llava-v1.6")
    assert is_multimodal_model("gemini-2.0-flash")
    assert not is_multimodal_model("deepseek-v4-flash")
    assert not is_multimodal_model("gpt-3.5-turbo")
    clear_multimodal_cache()


def test_multimodal_short_model_boundary():
    """o1/o3/o4 短模式需边界匹配，不误命中普通模型名。"""
    clear_multimodal_cache()
    assert is_multimodal_model("o3-mini")
    assert is_multimodal_model("gpt-o1")
    assert not is_multimodal_model("foo1")
    assert not is_multimodal_model("deepseek-o1x")  # 无边界不命中
    clear_multimodal_cache()


def test_multimodal_config_extension(monkeypatch):
    """RC 配置 multimodal_models 扩展判定。"""
    clear_multimodal_cache()
    monkeypatch.setattr(
        "src.api.multimodal._configured_multimodal_models",
        lambda: ("my-vision-model",),
    )
    assert is_multimodal_model("my-vision-model-v2")
    clear_multimodal_cache()


def test_multimodal_empty_or_none():
    clear_multimodal_cache()
    assert not is_multimodal_model("")
    assert not is_multimodal_model(None)
    clear_multimodal_cache()


# ── 9. content blocks 构造 ───────────────────────────

def test_build_image_content_blocks():
    blocks = build_image_content_blocks("说明", b"\x89PNG\r\n\x1a\n", "image/png")
    assert blocks[0] == {"type": "text", "text": "说明"}
    assert blocks[1]["type"] == "image_url"
    assert blocks[1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
    )


# ── 10. schema 描述同步 ──────────────────────────────

def test_tool_schema_mentions_features():
    """schema 描述向大模型声明分块/操作/双格式返回。"""
    desc = ReadImageFunc.to_tool_schema()["function"]["description"]
    assert "分块" in desc or "start_x" in desc
    assert "RGBA" in desc
    assert "多模态" in desc
    assert "grayscale" in desc
    props = ReadImageFunc.to_tool_schema()["function"]["parameters"]["properties"]
    assert "path" in props and "operation" in props and "format" in props
    assert props["format"]["enum"] == ["auto", "multimodal", "rgba_hex"]


# ── 11. from_args / display_params 边界 ───────────────

def test_from_args_paths_list():
    """from_args 兼容旧的 paths 数组格式（取首元素）。"""
    f = ReadImageFunc.from_args({"paths": ["/tmp/a.png", "/tmp/b.png"]})
    assert f.path == "/tmp/a.png"


def test_from_args_defaults():
    """from_args 缺省参数取默认值；非法 operation/format 回退。"""
    f = ReadImageFunc.from_args({"path": "/tmp/a.png"})
    assert f.operation == "none"
    assert f.format == "auto"
    assert f.max_dimension == 512


def test_display_params():
    """display_params 展示路径 + 区域 + 操作。"""
    assert ReadImageFunc.display_params({"path": "/tmp/a.png"}) == "'/tmp/a.png'"
    d = ReadImageFunc.display_params({
        "path": "/tmp/a.png", "operation": "grayscale",
        "start_x": 1, "start_y": 0, "end_x": 2, "end_y": 1,
    })
    assert "grayscale" in d and "(1,0)-(2,1)" in d
    # paths 列表形态
    assert ReadImageFunc.display_params({"paths": ["/tmp/a.png"]}) == "'/tmp/a.png'"


# ── 12. Anthropic 适配器边界（parse/stream/tools） ────

def test_anthropic_parse_response_tool_use():
    """parse_response：content blocks 中 text + tool_use 解析。"""
    adapter = AnthropicAdapter()
    parsed = adapter.parse_response({
        "content": [
            {"type": "text", "text": "思考完成"},
            {"type": "tool_use", "id": "tu1", "name": "read_file",
             "input": {"path": "x.py"}},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5,
                  "cache_read_input_tokens": 2},
    })
    assert parsed["content"] == "思考完成"
    assert parsed["tool_calls"] == [
        {"id": "tu1", "name": "read_file", "arguments": {"path": "x.py"}},
    ]
    # input = input_tokens(10) + cache_read(2) = 12
    assert parsed["usage"]["input"] == 12
    assert parsed["usage"]["input_cache_hit"] == 2


def test_anthropic_parse_stream_chunk_blocks():
    """parse_stream_chunk：content_block_start/stop + message_delta 累积。"""
    adapter = AnthropicAdapter()
    state = {}
    # message_start 清空状态
    r = adapter.parse_stream_chunk({"type": "message_start"}, state)
    assert r["content"] == ""
    # tool_use 开始（无完整 input）
    r = adapter.parse_stream_chunk({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "tu1", "name": "read_file"},
    }, state)
    assert r["tool_calls"] == []
    # 参数增量
    r = adapter.parse_stream_chunk({
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
    }, state)
    r = adapter.parse_stream_chunk({
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": '"x.py"}'},
    }, state)
    # 结束 → 累积完整参数输出 tool_call
    r = adapter.parse_stream_chunk({
        "type": "content_block_stop", "index": 0,
    }, state)
    assert r["tool_calls"] == [
        {"id": "tu1", "name": "read_file", "arguments": {"path": "x.py"}},
    ]
    # usage
    r = adapter.parse_stream_chunk({
        "type": "message_delta",
        "usage": {"input_tokens": 9, "output_tokens": 3,
                  "cache_read_input_tokens": 1},
    }, state)
    assert r["usage"]["input_cache_hit"] == 1


def test_anthropic_parse_stream_empty_input():
    """parse_stream_chunk：start 携带空 input {}（空参数工具）不被丢弃。"""
    adapter = AnthropicAdapter()
    state = {}
    r = adapter.parse_stream_chunk({
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "tu-empty",
                          "name": "some_tool", "input": {}},
    }, state)
    # 空参数工具调用必须输出（否则消息序列缺 tool 消息 → 下一轮 API 400）
    assert r["tool_calls"] == [
        {"id": "tu-empty", "name": "some_tool", "arguments": {}},
    ]


def test_anthropic_convert_messages_merges_consecutive_tools():
    """连续 tool 消息合并为单个 user 消息（多 tool_result blocks），
    避免连续 user 消息触发 Anthropic "roles must alternate" 400。"""
    adapter = AnthropicAdapter()
    _, msgs = adapter._convert_messages([
        {"role": "user", "content": "执行两个工具"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}},
            {"id": "c2", "type": "function",
             "function": {"name": "search", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "文件内容1"},
        {"role": "tool", "tool_call_id": "c2", "content": "搜索结果2"},
    ])
    # 连续 tool 消息合并为 1 个 user 消息（2 个 tool_result blocks）
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 2  # 初始 user + 合并的 tool_result user
    last = user_msgs[-1]
    assert last["content"][0]["type"] == "tool_result"
    assert last["content"][0]["tool_use_id"] == "c1"
    assert last["content"][1]["type"] == "tool_result"
    assert last["content"][1]["tool_use_id"] == "c2"


def test_anthropic_convert_tools():
    """_convert_tools：OpenAI 工具格式 → Anthropic 格式。"""
    adapter = AnthropicAdapter()
    tools = [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }]
    converted = adapter._convert_tools(tools)
    assert converted == [{
        "name": "read_file", "description": "读取文件",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }]


def test_anthropic_build_request_kwargs_stream():
    """build_request_kwargs：system 提取 + messages 转换 + stream 标志。"""
    adapter = AnthropicAdapter()
    kwargs = adapter.build_request_kwargs(
        messages=[
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "你好"},
        ],
        model="claude-sonnet-4-6",
        stream=True,
    )
    assert kwargs["system"] == "规则"
    assert kwargs["stream"] is True
    assert kwargs["messages"][0]["content"] == [{"type": "text", "text": "你好"}]


def test_anthropic_user_multimodal_blocks():
    """user 消息 content list（image_url data URI）→ Anthropic image block。"""
    adapter = AnthropicAdapter()
    _, msgs = adapter._convert_messages([
        {"role": "user", "content": [
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
        ]},
    ])
    blocks = msgs[0]["content"]
    assert blocks[0] == {"type": "text", "text": "看这张图"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["data"] == "QUJD"


# ── 13. 工具调度边界（ToolResult / plan 白名单 / background） ──

def test_run_tool_func_tuple_short_defense():
    """run_method 返回长度不足 tuple 时防御（不 IndexError）。"""

    async def run_method(func, tc):
        return ("文本",)

    scheduler = ToolScheduler()
    out, ok = asyncio.run(scheduler._run_tool_func(
        type("F", (), {"result_blocks": None, "execute": lambda: "x"})(),
        {}, run_method,
    ))
    assert out == "文本" and ok is True


def test_can_use_plan_whitelist(tmp_path, monkeypatch):
    """can_use：plan agent 仅允许 .chat/plan/ 目录（realpath 防符号链接绕过）。"""
    from src.tools.base import Func
    monkeypatch.chdir(tmp_path)
    plan_dir = tmp_path / ".chat" / "plan"
    plan_dir.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    inside = plan_dir / "plan.md"
    ok, err = Func.can_use("write_file", agent_type="plan", path=str(inside))
    assert ok is True and err is None
    ok, err = Func.can_use("write_file", agent_type="plan", path=str(outside))
    assert ok is False and "只能在" in err
    # 非 plan agent 不受白名单限制
    ok, _ = Func.can_use("write_file", agent_type="execute", path=str(outside))
    assert ok is True


def test_is_path_within_dir(tmp_path):
    """is_path_within_dir：子路径 True / 外部 False / 符号链接解析。"""
    from src.tools.file_ops import is_path_within_dir
    base = str(tmp_path)
    assert is_path_within_dir(str(tmp_path / "a" / "b.txt"), base) is True
    assert is_path_within_dir(str(tmp_path.parent / "out.txt"), base) is False


def test_parse_background_flag_string():
    """parse_background_flag：字符串布尔正确解析（"false" → 前台）。"""
    from src.tools.subagent import parse_background_flag
    assert parse_background_flag({"background": "false"}) is False
    assert parse_background_flag({"background": "true"}) is True
    assert parse_background_flag('{"background": "false"}') is False
    assert parse_background_flag({"background": False}) is False
    assert parse_background_flag({}) is True  # 缺省后台
    assert parse_background_flag("{broken") is True  # 解析失败回退后台
    assert parse_background_flag(None) is True


def test_subagent_from_args_background_string():
    """SubagentFunc.from_args：字符串 "false" → 前台执行。"""
    from src.tools import Subagent
    f = Subagent.from_args({
        "description": "d", "prompt": "p", "background": "false",
    })
    assert f.background is False
    f = Subagent.from_args({"description": "d", "prompt": "p"})
    assert f.background is True


# ── 14. 安全与文件边界（review 修复回归） ────────────

def test_sanitize_args_str_json_masked():
    """_sanitize_args_for_log：str 形态 JSON 参数敏感字段脱敏（防 audit.log 泄露）。"""
    from src.core.internal.agent._tool_callbacks import ToolCallbackChain
    sanitized = ToolCallbackChain._sanitize_args_for_log(
        '{"password": "hunter2", "api_key": "sk-secret", "path": "x.py"}')
    assert "hunter2" not in sanitized
    assert "sk-secret" not in sanitized
    assert "***" in sanitized
    assert "x.py" in sanitized  # 非敏感字段保留


def test_sanitize_args_str_non_json_masked():
    """_sanitize_args_for_log：无法 JSON 解析的 str 按键名模式掩码。"""
    from src.core.internal.agent._tool_callbacks import ToolCallbackChain
    sanitized = ToolCallbackChain._sanitize_args_for_log('{"token": "abc123" 截断文本')
    assert "abc123" not in sanitized


def test_sanitize_args_dict_masked():
    """_sanitize_args_for_log：dict 参数敏感字段脱敏（递归）。"""
    from src.core.internal.agent._tool_callbacks import ToolCallbackChain
    sanitized = ToolCallbackChain._sanitize_args_for_log(
        {"api_key": "sk-1", "nested": {"password": "pw1"}, "path": "x"})
    assert "sk-1" not in sanitized and "pw1" not in sanitized
    assert "x" in sanitized


def test_atomic_write_no_dir_relative_path(tmp_path, monkeypatch):
    """atomic_write_file：无目录相对路径（如 README.md）跨设备 rename 不失败。"""
    from src.tools.file_ops import atomic_write_file
    monkeypatch.chdir(tmp_path)
    lines, size = atomic_write_file("notes.md", "hello\nworld\n")
    assert lines == 2 and size > 0
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "hello\nworld\n"


def test_file_ops_copy_permissions_no_special_bits(tmp_path):
    """_copy_file_permissions：只复制权限位，不复制 setuid/setgid/sticky。"""
    import stat
    from src.tools.file_ops import _copy_file_permissions
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("x", encoding="utf-8")
    dst.write_text("y", encoding="utf-8")
    # 源文件带 setuid 位
    os.chmod(str(src), 0o4755)
    _copy_file_permissions(str(src), str(dst))
    mode = stat.S_IMODE(os.stat(str(dst)).st_mode)
    assert mode == 0o755  # 特殊位被剥离
    assert stat.S_IMODE(os.stat(str(src)).st_mode) == 0o4755

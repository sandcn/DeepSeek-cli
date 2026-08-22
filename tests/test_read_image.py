"""read_image 工具测试（对齐 DSH read_image 加工契约，2026-08）。

覆盖：
- 扩展名格式门禁（仅 PNG/JPEG/WebP/GIF，拒绝 BMP/文本等）
- magic-byte / 声明类型一致校验（扩展名声明与实际解码格式不符）
- 严格图像能力门禁（非多模态模型 → DSH 式拒绝，不再降级 rgba/palette）
- 8-bit sRGB 规范化（16 位 PNG 拒绝）
- 多模态输出（result_blocks = image_url data URI content blocks）
- 分块读取（start_x/start_y/end_x/end_y 像素区域裁剪）
- 图像操作（grayscale / rotate / flip / scale）
- 防爆上下文（max_tokens 预算 + 字节硬上限）
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

from src.tools.read_image import ReadImageFunc, IMAGE_EXTENSIONS
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


def _write_test_image(path, w: int = 4, h: int = 3, fmt: str = "PNG") -> str:
    with open(path, "wb") as f:
        f.write(_make_image_bytes(w, h, fmt))
    return str(path)


def _multimodal(monkeypatch, value: bool = True):
    """强制 read_image 的图像能力判定结果为 value。"""
    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", lambda m: value)


# ── 0. 扩展名格式门禁（仅 PNG/JPEG/WebP/GIF） ──────────

async def test_format_gate_rejects_txt(tmp_path):
    """非图片扩展名（.txt）→ 扩展名门禁拒绝。"""
    p = tmp_path / "t.txt"
    p.write_text("hello", encoding="utf-8")
    out = await ReadImageFunc(path=str(p)).execute()
    assert out.startswith('(cannot read "')
    assert "not a recognized image extension" in out


async def test_format_gate_accepts_bmp_converts_png(tmp_path, monkeypatch):
    """BMP（非 vision 原生格式）→ 允许读取并自动转换为 PNG（模型支持格式）。"""
    _multimodal(monkeypatch, True)
    from PIL import Image
    p = tmp_path / "t.bmp"
    Image.new("RGB", (2, 2)).save(str(p), format="BMP")
    f = ReadImageFunc(path=str(p))
    out = await f.execute()
    assert "图片:" in out
    assert "格式: PNG" in out
    blocks = f.result_blocks
    assert blocks is not None and len(blocks) == 2
    url = blocks[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    raw = base64.b64decode(url.split(",", 1)[1])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


# ── 1. 严格图像能力门禁 ───────────────────────────────

async def test_capability_gate_refuses_non_multimodal(tmp_path, monkeypatch):
    """非多模态模型 → DSH 式拒绝（不降级为 rgba/palette）。"""
    _multimodal(monkeypatch, False)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    out = await ReadImageFunc(path=str(p)).execute()
    assert out.startswith('(cannot read "')
    assert "does not declare image input" in out
    assert "switch to an image-capable model to read images" in out


async def test_capability_gate_passes_multimodal(tmp_path, monkeypatch):
    """多模态模型 → 返回多模态图片（success 路径）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    f = ReadImageFunc(path=str(p))
    out = await f.execute()
    assert "图片:" in out
    assert "尺寸:" in out
    # 多模态 success 路径 → 设置 result_blocks（text + image_url data URI）
    assert f.result_blocks is not None


async def test_capability_gate_uses_routed_agent_model(tmp_path, monkeypatch):
    """对齐 DSH：门禁取「调用代理当前路由模型」（agent.model），而非全局 MODEL。

    用户把模型切到 deepseek-v4-flash-vision-exp 后应放行；旧逻辑读全局 MODEL
    （默认 deepseek-v4-flash）会把视觉模型误判为「不能输入图片」而拒绝。
    """
    seen = []

    def spy(model):
        seen.append(model)
        return is_multimodal_model(model)

    monkeypatch.setattr("src.tools.read_image.is_multimodal_model", spy)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    f = ReadImageFunc(path=str(p))

    class FakeAgent:
        model = "deepseek-v4-flash-vision-exp"

    f.set_agent(FakeAgent())  # 模拟 registry.dispatch 注入的调用代理
    out = await f.execute()
    # 门禁确实询问了调用代理的当前路由模型（而非全局默认）
    assert seen and seen[0] == "deepseek-v4-flash-vision-exp"
    assert not out.startswith("(cannot read ")
    assert f.result_blocks is not None


# ── 2. magic-byte / 声明类型一致校验 ──────────────────

async def test_type_mismatch_png_bytes_jpg_ext(tmp_path, monkeypatch):
    """.jpg 扩展名但内容是 PNG → DSH 式「扩展名与实际格式不符」。"""
    _multimodal(monkeypatch, True)
    p = tmp_path / "t.jpg"
    with open(str(p), "wb") as f:
        f.write(_make_image_bytes(2, 2, "PNG"))  # 实际 PNG 字节
    out = await ReadImageFunc(path=str(p)).execute()
    assert out.startswith('(cannot read "')
    assert "extension declares image/jpeg" in out
    assert "use a different image format" in out
    assert "rename the file" in out


# ── 3. 8-bit sRGB 规范化 ─────────────────────────────

async def test_16bit_png_refused(tmp_path, monkeypatch):
    """16 位 PNG → 无法归一化为 8-bit sRGB，返回 DSH 式错误。"""
    _multimodal(monkeypatch, True)
    from PIL import Image
    p = tmp_path / "t16.png"
    Image.new("I;16", (2, 2)).save(str(p), format="PNG")
    out = await ReadImageFunc(path=str(p)).execute()
    assert out.startswith('(cannot read "')
    assert "could not be converted to the normalized 8-bit sRGB form" in out


# ── 4. 多模态输出 ────────────────────────────────────

async def test_multimodal_output_sets_result_blocks(tmp_path, monkeypatch):
    """多模态模型 → 设置 result_blocks（text + image_url data URI）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    f = ReadImageFunc(path=p)
    out = await f.execute()
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


async def test_result_blocks_reset_on_failure(tmp_path, monkeypatch):
    """同一实例：多模态成功后失败路径重置 result_blocks（防残留误包装）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png")
    f = ReadImageFunc(path=p)
    out = await f.execute()
    assert f.result_blocks is not None
    # 同一实例改为读不存在的文件 → 失败路径必须清除 result_blocks
    f.path = str(tmp_path / "nope.png")
    out2 = await f.execute()
    assert out2.startswith("(文件不存在:")
    assert f.result_blocks is None


# ── 5. 分块读取（区域裁剪） ──────────────────────────

async def test_region_crop(tmp_path, monkeypatch):
    """start_x/start_y/end_x/end_y 指定区域裁剪（类似 read_file 行号范围）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 3)
    f = ReadImageFunc(path=p, start_x=1, start_y=0, end_x=2, end_y=1)
    out = await f.execute()
    assert "区域: (1,0)-(2,1)" in out
    # 裁剪后输出尺寸 2x2（元信息中的原始尺寸仍为 4x3）
    assert "尺寸: 2x2" in out
    assert "原始尺寸: 4x3" in out


async def test_region_reversed_swaps(tmp_path, monkeypatch):
    """start > end 时自动交换（与 read_file 语义一致）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 3)
    f = ReadImageFunc(path=p, start_x=3, end_x=0, start_y=2, end_y=0)
    out = await f.execute()
    assert "尺寸: 4x3" in out  # 全图 4x3（交换后无缩小区）


# ── 6. 图像操作 ──────────────────────────────────────

async def test_operation_grayscale(tmp_path, monkeypatch):
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 1, 1)
    f = ReadImageFunc(path=p, operation="grayscale")
    out = await f.execute()
    assert "操作: grayscale" in out
    assert f.result_blocks is not None


async def test_operation_rotate90(tmp_path, monkeypatch):
    """4x2 顺时针旋转 → 2x4。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 2)
    f = ReadImageFunc(path=p, operation="rotate90")
    out = await f.execute()
    assert "操作: rotate90" in out
    assert "尺寸: 2x4" in out


async def test_operation_flip_h(tmp_path, monkeypatch):
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 2, 1)
    f = ReadImageFunc(path=p, operation="flip_h")
    out = await f.execute()
    assert "操作: flip_h" in out


async def test_operation_scale(tmp_path, monkeypatch):
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 2)
    f = ReadImageFunc(path=p, operation="scale", scale_width=8, scale_height=4)
    out = await f.execute()
    assert "操作: scale" in out
    assert "尺寸: 8x4" in out


async def test_operation_scale_proportional(tmp_path, monkeypatch):
    """scale 只给一个维度时按比例计算另一维。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 4, 2)
    f = ReadImageFunc(path=p, operation="scale", scale_width=8)
    out = await f.execute()
    assert "操作: scale" in out
    assert "尺寸: 8x4" in out


async def test_operation_invalid_falls_back(tmp_path, monkeypatch):
    """非法 operation 回退 none。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    f = ReadImageFunc.from_args({"path": p, "operation": "blur_xyz"})
    assert f.operation == "none"
    out = await f.execute()
    assert "操作: none" in out


# ── 7. 错误路径 ──────────────────────────────────────

async def test_file_not_exists(tmp_path):
    out = await ReadImageFunc(path=str(tmp_path / "nope.png")).execute()
    assert out.startswith("(文件不存在:")


async def test_dangerous_path_rejected():
    # 用跨平台 DOS 设备名（basename 大写匹配 DOS_DEVICE_NAMES），
    # 避免 /dev/null 在 Windows 下被 realpath 归一化后不再命中，
    # 也避免 NUL 被 realpath 重写为 \\.\NUL 导致 basename 为空。
    with pytest.raises(ValueError):
        ReadImageFunc(path="CON")


async def test_missing_path_raises():
    with pytest.raises(ValueError):
        ReadImageFunc.from_args({})


# ── 8. 防爆上下文（预算 + 字节上限） ──────────────────

async def test_output_meta_no_tailnote(tmp_path, monkeypatch):
    """输出精简：核心元信息保留，不再携带「模式/预计占用/如需细节」技术尾注。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "t.png", 2, 2)
    out = await ReadImageFunc(path=p).execute()
    assert "图片:" in out
    assert "尺寸:" in out
    assert "格式: PNG" in out
    assert "预计占用" not in out
    assert "模式: 多模态" not in out
    assert "如需细节" not in out


async def test_max_tokens_zero_disables_budget(tmp_path, monkeypatch):
    """max_tokens=0 禁用预算约束（仍保留字节硬上限/长边上限）。"""
    _multimodal(monkeypatch, True)
    p = _write_test_image(tmp_path / "big.png", 100, 100)
    out = await ReadImageFunc(path=p, max_dimension=512, max_tokens=0).execute()
    # 100x100 无预算约束也不触发字节上限 → 尺寸保持不变
    assert "尺寸: 100x100" in out


async def test_multimodal_budget_shrinks(tmp_path, monkeypatch):
    """多模态：base64 输出超出预算时以真实 PNG 编码长度迭代降采样。"""
    from src.api.tokens import estimate_tokens
    import random
    from PIL import Image as PILImage
    _multimodal(monkeypatch, True)
    # 噪声图（PNG 压缩率低 → 编码体积大 → 必超预算触发缩小）
    p = tmp_path / "noise.png"
    noise = PILImage.new("RGB", (256, 256))
    rnd = random.Random(42)
    noise.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                   for _ in range(256 * 256)])
    noise.save(str(p), format="PNG")
    f = ReadImageFunc(path=str(p), max_dimension=256, max_tokens=2000)
    out = await f.execute()
    assert "尺寸:" in out
    import re
    dim = re.search(r"^尺寸: (\d+)x(\d+)", out, re.M)
    assert dim is not None
    ow, oh = int(dim.group(1)), int(dim.group(2))
    assert max(ow, oh) < 256  # 预算适配应把 256x256 噪声图缩小
    blocks = f.result_blocks
    assert blocks is not None and blocks[1]["type"] == "image_url"
    url = blocks[1]["image_url"]["url"]
    assert estimate_tokens(url) <= 2000  # data URI 本身 token 不超预算


# ── 9. ToolResult 机制 ───────────────────────────────

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


# ── 10. Anthropic 转换 ───────────────────────────────

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


# ── 11. 多模态检测 ───────────────────────────────────

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
    # DeepSeek V4 多模态实验模型（deepseek-v4-flash-vision-exp）
    assert is_multimodal_model("deepseek-v4-flash-vision-exp")
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


# ── 12. content blocks 构造 ──────────────────────────

def test_build_image_content_blocks():
    blocks = build_image_content_blocks("说明", b"\x89PNG\r\n\x1a\n", "image/png")
    assert blocks[0] == {"type": "text", "text": "说明"}
    assert blocks[1]["type"] == "image_url"
    assert blocks[1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
    )


# ── 13. schema 描述同步 ──────────────────────────────

def test_tool_schema_mentions_features():
    """schema 描述向大模型声明分块/操作/全量格式/能力门禁。"""
    desc = ReadImageFunc.to_tool_schema()["function"]["description"]
    assert "分块" in desc or "start_x" in desc
    assert "多模态" in desc
    assert "grayscale" in desc
    assert "PNG/JPEG/WebP/GIF" in desc
    assert "BMP" in desc  # 全量图片格式已声明
    props = ReadImageFunc.to_tool_schema()["function"]["parameters"]["properties"]
    assert "path" in props and "operation" in props and "max_tokens" in props
    # 已移除 rgba_hex/palette 降级格式
    assert "format" not in props and "palette_colors" not in props


def test_image_extensions_full():
    """扩展名映射覆盖全量常用图像格式（PNG/JPEG/WebP/GIF/BMP/TIFF/ICO/PNM…）。"""
    for ext, mime in {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp",
    }.items():
        assert IMAGE_EXTENSIONS[ext] == mime
    # 非 vision 原生格式也在全量支持范围内（自动转 PNG）
    assert IMAGE_EXTENSIONS[".bmp"] == "image/bmp"
    assert IMAGE_EXTENSIONS[".tiff"] == "image/tiff"
    assert IMAGE_EXTENSIONS[".ico"] == "image/vnd.microsoft.icon"
    assert IMAGE_EXTENSIONS[".ppm"] == "image/x-portable-pixmap"
    assert IMAGE_EXTENSIONS[".tga"] == "image/x-targa"
    # 无法识别为非图片的扩展名不在映射中
    assert ".txt" not in IMAGE_EXTENSIONS
    assert ".pdf" not in IMAGE_EXTENSIONS
    assert ".mpg" not in IMAGE_EXTENSIONS


# ── 14. from_args / display_params 边界 ───────────────

def test_from_args_paths_list():
    """from_args 兼容旧的 paths 数组格式（取首元素）。"""
    f = ReadImageFunc.from_args({"paths": ["/tmp/a.png", "/tmp/b.png"]})
    assert f.path == "/tmp/a.png"


def test_from_args_defaults():
    """from_args 缺省参数取默认值；非法 operation 回退。"""
    f = ReadImageFunc.from_args({"path": "/tmp/a.png"})
    assert f.operation == "none"
    assert f.max_dimension == 256
    assert f.max_tokens == 8000
    # rgba/palette 相关已移除
    assert not hasattr(f, "format")
    assert not hasattr(f, "palette_colors")


def test_max_tokens_negative_normalized():
    """max_tokens 负值归一化为 0（不限制）。"""
    f = ReadImageFunc(path="/tmp/a.png", max_tokens=-5)
    assert f.max_tokens == 0
    f2 = ReadImageFunc(path="/tmp/a.png", max_tokens=None)
    assert f2.max_tokens == 8000


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


def test_display_params_partial_coords():
    """display_params 部分坐标缺失时不渲染 (None,None)。"""
    d = ReadImageFunc.display_params({"path": "/tmp/a.png", "start_x": 1})
    assert "None" not in d
    assert "(1,)-(" in d

"""多模态图片（视觉）token 估算与上下文统计口径测试。

图片在文本提取（``content_to_text``）里只占 ``[图片]`` 占位，视觉 token 须
单独估算并计入上下文占用百分比与压缩判断。本测试覆盖：

  - 分块公式 ``ceil(w/patch)*ceil(h/patch)``（含 patch 配置覆盖）；
  - 上传瘦身对齐：长边 ``image_upload_max_dimension`` 降采样、
    ``image_upload_keep_recent`` 折叠（仅计最近 N 张）；
  - 无法读尺寸（http URL / 解码失败）→ ``multimodal_image_token_default``；
  - ``compute_message_stats`` 与 ``ContextManager.refresh_usage`` 计入图片；
  - 压缩判断 token 口径含图片。
"""

from __future__ import annotations

import base64
import io

import pytest

from src.core import image_tokens
from src.core.image_tokens import (
    estimate_image_block_tokens,
    estimate_image_tokens_by_size,
    estimate_messages_image_tokens,
    is_image_block,
    iter_image_blocks,
)


# ── 公共工具 ──────────────────────────────────────────────

def _png_data_uri(width: int, height: int, color=(0, 0, 0)) -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _img_block(width: int, height: int) -> dict:
    return {"type": "image_url",
            "image_url": {"url": _png_data_uri(width, height)}}


@pytest.fixture(autouse=True)
def _isolate_context_globals():
    from src.core.context_manager import (
        set_context_usage_percent, set_streaming_extra_tokens,
        set_active_context_manager,
    )
    set_context_usage_percent(None)
    set_streaming_extra_tokens(0)
    set_active_context_manager(None)
    yield
    set_context_usage_percent(None)
    set_streaming_extra_tokens(0)
    set_active_context_manager(None)


# ═══════════════════════════════════════════════════════════════
# 1. 分块公式
# ═══════════════════════════════════════════════════════════════

class TestBySizeFormula:

    @pytest.mark.parametrize("w,h,expected", [
        (28, 28, 1),
        (29, 28, 2),
        (56, 56, 4),
        (8, 8, 1),
        (1, 1, 1),
        (280, 140, 10 * 5),
    ])
    def test_patch_28(self, w, h, expected):
        assert estimate_image_tokens_by_size(w, h, patch=28) == expected

    def test_patch_override(self):
        assert estimate_image_tokens_by_size(28, 28, patch=14) == 4
        assert estimate_image_tokens_by_size(14, 14, patch=14) == 1

    def test_invalid_uses_default(self):
        assert estimate_image_tokens_by_size(0, 10, patch=28, default=777) == 777
        assert estimate_image_tokens_by_size(10, 10, patch=0, default=777) == 777


# ═══════════════════════════════════════════════════════════════
# 2. 块估算（尺寸 / 长边约束 / 回退）
# ═══════════════════════════════════════════════════════════════

class TestBlockEstimate:

    def test_small_image_1_patch(self):
        assert estimate_image_block_tokens(_img_block(8, 8)) == 1

    def test_medium_image_patch_grid(self):
        assert estimate_image_block_tokens(_img_block(280, 140)) == 10 * 5

    def test_long_side_downscale(self):
        """长边超 max_dimension → 等比降采样后再分块。"""
        block = _img_block(3000, 1000)
        # 缩放至 1568x523（round(1000*1568/3000)=523）
        expected = ((1568 + 27) // 28) * ((523 + 27) // 28)
        assert estimate_image_block_tokens(block, max_dimension=1568) == expected

    def test_no_downscale_when_disabled(self):
        block = _img_block(3000, 1000)
        expected = ((3000 + 27) // 28) * ((1000 + 27) // 28)
        assert estimate_image_block_tokens(block, max_dimension=0) == expected

    def test_http_url_falls_back_to_default(self):
        block = {"type": "image_url", "image_url": {"url": "https://x/y.png"}}
        assert estimate_image_block_tokens(block, default=800) == 800

    def test_broken_base64_falls_back_to_default(self):
        block = {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64,!!!not-base64!!!"}}
        assert estimate_image_block_tokens(block, default=800) == 800

    def test_anthropic_image_block(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (56, 56)).save(buf, format="PNG")
        block = {"type": "image", "source": {
            "type": "base64", "media_type": "image/png",
            "data": base64.b64encode(buf.getvalue()).decode("ascii"),
        }}
        assert estimate_image_block_tokens(block) == 4

    def test_non_image_block(self):
        assert estimate_image_block_tokens({"type": "text", "text": "hi"}, default=7) == 7


# ═══════════════════════════════════════════════════════════════
# 3. 块识别 / 遍历
# ═══════════════════════════════════════════════════════════════

class TestBlockDetection:

    def test_is_image_block(self):
        assert is_image_block(_img_block(8, 8)) is True
        assert is_image_block({"type": "text", "text": "x"}) is False
        assert is_image_block(None) is False
        assert is_image_block({"type": "image_url", "image_url": {}}) is False

    def test_iter_image_blocks(self):
        content = [
            {"type": "text", "text": "看这个"},
            _img_block(8, 8),
            {"type": "image", "source": {"type": "url", "url": "https://x"}},
            None,
        ]
        blocks = list(iter_image_blocks(content))
        assert len(blocks) == 2


# ═══════════════════════════════════════════════════════════════
# 4. messages 级估算（折叠 + 配置）
# ═══════════════════════════════════════════════════════════════

class TestMessagesEstimate:

    def _msgs(self, n_images: int, w=56, h=56):
        msgs = [{"role": "system", "content": "s"}]
        for _ in range(n_images):
            msgs.append({"role": "user", "content": [
                {"type": "text", "text": "看图"}, _img_block(w, h)]})
        return msgs

    def test_no_images_zero(self):
        assert estimate_messages_image_tokens([{"role": "user", "content": "hi"}]) == 0
        assert estimate_messages_image_tokens([]) == 0

    def test_single_image(self):
        assert estimate_messages_image_tokens(self._msgs(1)) == 4

    def test_fold_keeps_recent(self):
        """超过 keep_recent 张时只计最近 N 张（对齐上传折叠规则）。"""
        msgs = self._msgs(6)
        assert estimate_messages_image_tokens(msgs, keep_recent=4) == 4 * 4
        assert estimate_messages_image_tokens(msgs, keep_recent=2) == 4 * 2

    def test_fold_disabled_counts_all(self):
        msgs = self._msgs(6)
        assert estimate_messages_image_tokens(msgs, keep_recent=0) == 4 * 6

    def test_config_patch_override(self, monkeypatch):
        import src.config as config
        monkeypatch.setattr(config, "MULTIMODAL_IMAGE_TOKEN_PATCH", 14)
        # 56x56 / patch14 = 4*4
        assert estimate_messages_image_tokens(self._msgs(1)) == 16

    def test_config_default_override(self, monkeypatch):
        import src.config as config
        monkeypatch.setattr(config, "MULTIMODAL_IMAGE_TOKEN_DEFAULT", 123)
        msgs = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://x/y.png"}}]}]
        assert estimate_messages_image_tokens(msgs) == 123

    def test_optimize_off_no_fold_no_downscale(self, monkeypatch):
        import src.config as config
        monkeypatch.setattr(config, "IMAGE_UPLOAD_OPTIMIZE", False)
        msgs = self._msgs(6, w=3000, h=1000)
        expected = ((3000 + 27) // 28) * ((1000 + 27) // 28) * 6
        assert estimate_messages_image_tokens(msgs) == expected


# ═══════════════════════════════════════════════════════════════
# 5. 接入上下文统计（compute_message_stats / refresh_usage / 压缩）
# ═══════════════════════════════════════════════════════════════

class TestStatsIntegration:

    def test_compute_message_stats_includes_image(self):
        from src.core import context_selector as selector
        from src.core.tokens import estimate_tokens
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "看图"}, _img_block(280, 140)]}]
        chars, tokens = selector.compute_message_stats(msgs)
        text_tokens = estimate_tokens("看图 [图片]")
        assert tokens == text_tokens + 10 * 5
        # 字符口径只计文本（不含图片）
        assert chars == len("看图 [图片]")

    def test_refresh_usage_includes_image(self):
        from src.core.adapters.config import MockConfigAdapter
        from src.core.context_manager import (
            ContextManager, get_context_usage_percent,
        )
        from src.core.tokens import estimate_tokens

        msgs = [{"role": "system", "content": "s"},
                {"role": "user", "content": [
                    {"type": "text", "text": "hi"}, _img_block(280, 140)]}]
        cfg = MockConfigAdapter({"model_context_tokens": 10000})
        ContextManager(msgs, "m", config_port=cfg)
        img_tokens = estimate_messages_image_tokens(msgs)
        expected = round(
            (estimate_tokens("s") + estimate_tokens("hi [图片]") + img_tokens)
            / 10000 * 100, 1,
        )
        assert img_tokens > 0
        assert get_context_usage_percent() == expected

    def test_exceeds_limit_counts_image(self):
        from src.core import context_selector as selector
        msgs = [{"role": "user", "content": [{"type": "text", "text": "x"},
                                             _img_block(280, 140)]}]
        chars, tokens = selector.compute_message_stats(msgs)
        assert selector.exceeds_limit_values(chars, tokens, max_context_tokens=10) is True
        # 只按字符（图片不计入字符）时不超限
        assert selector.exceeds_limit_values(chars, 0, max_context_tokens=10) is False

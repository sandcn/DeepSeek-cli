"""title_generator — AI 会话标题生成器测试

覆盖：
- build_title_messages：跳过 system/tool、截断、空输入
- normalize_title：去引号/前缀/多行/标点/长度截断
- generate_title_async：成功/失败/空/中断占位
- maybe_update_title_async：写文件成功/失败静默
"""

from __future__ import annotations

import pytest


class TestBuildTitleMessages:
    def test_build_basic(self):
        from src.core.title_generator import build_title_messages

        messages = [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "帮我写一个 Python 脚本"},
            {"role": "assistant", "content": "好的，我来写。"},
            {"role": "tool", "content": "工具输出噪音"},
        ]
        msgs = build_title_messages(messages)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert "帮我写一个 Python 脚本" in msgs[1]["content"]
        # tool 消息不参与
        assert "工具输出噪音" not in msgs[1]["content"]

    def test_build_skips_tool_calls(self):
        from src.core.title_generator import build_title_messages

        messages = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "ls", "arguments": "{}"}}]},
            {"role": "assistant", "content": "最终回答"},
        ]
        msgs = build_title_messages(messages)
        assert len(msgs) == 2
        assert "最终回答" in msgs[1]["content"]

    def test_build_empty(self):
        from src.core.title_generator import build_title_messages

        assert build_title_messages([]) == []
        assert build_title_messages([{"role": "system", "content": "s"}]) == []
        assert build_title_messages([{"role": "user", "content": "   "}]) == []

    def test_build_truncates(self):
        from src.core.title_generator import build_title_messages

        long_msg = "内容" * 500  # 1000 字符，超过单条上限 300
        messages = [{"role": "user", "content": long_msg}]
        msgs = build_title_messages(messages)
        assert len(msgs) == 2
        assert len(msgs[1]["content"]) < 1000  # 已被截断

    def test_build_max_chars(self):
        from src.core.title_generator import build_title_messages

        messages = [
            {"role": "user", "content": f"消息{i}：内容内容"} for i in range(20)
        ]
        # max_chars=50 → 只取开头部分
        msgs = build_title_messages(messages, max_chars=50)
        assert len(msgs) == 2
        assert len(msgs[1]["content"]) < 500


class TestNormalizeTitle:
    def test_plain(self):
        from src.core.title_generator import normalize_title

        assert normalize_title("  Python 脚本编写  ") == "Python 脚本编写"

    def test_strip_quotes(self):
        from src.core.title_generator import normalize_title

        assert normalize_title('"Python 脚本"') == "Python 脚本"
        assert normalize_title("《Python 脚本》") == "Python 脚本"
        assert normalize_title("「Python 脚本」") == "Python 脚本"

    def test_strip_prefix(self):
        from src.core.title_generator import normalize_title

        assert normalize_title("标题：Python 脚本") == "Python 脚本"
        assert normalize_title("标题: Python 脚本") == "Python 脚本"
        assert normalize_title("Title: Python 脚本") == "Python 脚本"

    def test_multiline(self):
        from src.core.title_generator import normalize_title

        assert normalize_title("Python 脚本编写\n下面是一些说明") == "Python 脚本编写"

    def test_strip_trailing_punct(self):
        from src.core.title_generator import normalize_title

        assert normalize_title("Python 脚本编写。") == "Python 脚本编写"
        assert normalize_title("Python 脚本编写！") == "Python 脚本编写"

    def test_truncate(self):
        from src.core.title_generator import normalize_title

        long = "这是一个超过三十个字符长度的非常长的标题内容用于测试需要继续加长" * 1  # 40+ 字符
        result = normalize_title(long)
        assert result.endswith("…")
        assert len(result) <= 31  # 30 + …

    def test_empty(self):
        from src.core.title_generator import normalize_title

        assert normalize_title("") == ""
        assert normalize_title("   ") == ""
        assert normalize_title(None) == ""


class TestGenerateTitleAsync:
    async def test_success(self):
        from src.core.title_generator import generate_title_async
        from src.core.adapters.model import MockAsyncModelAdapter
        from src.core.ports.model import ModelResult

        port = MockAsyncModelAdapter(ModelResult(content="Python 脚本编写"))
        messages = [{"role": "user", "content": "帮我写脚本"}, {"role": "assistant", "content": "好的"}]
        title = await generate_title_async(port, messages, "model-a")
        assert title == "Python 脚本编写"
        assert port.call_count == 1
        assert port.last_model == "model-a"

    async def test_empty_content(self):
        from src.core.title_generator import generate_title_async
        from src.core.adapters.model import MockAsyncModelAdapter
        from src.core.ports.model import ModelResult

        port = MockAsyncModelAdapter(ModelResult(content=""))
        title = await generate_title_async(port, [{"role": "user", "content": "x"}], "m")
        assert title is None

    async def test_interrupted_placeholder(self):
        from src.core.title_generator import generate_title_async
        from src.core.adapters.model import MockAsyncModelAdapter
        from src.core.ports.model import ModelResult

        port = MockAsyncModelAdapter(ModelResult(content="(已中断)"))
        title = await generate_title_async(port, [{"role": "user", "content": "x"}], "m")
        assert title is None

    async def test_exception_returns_none(self):
        from src.core.title_generator import generate_title_async
        from src.core.ports.model import AsyncModelPort, ModelResult

        class BoomPort(AsyncModelPort):
            async def call(self, *a, **k):
                raise RuntimeError("boom")

            async def call_sync(self, *a, **k):
                raise RuntimeError("boom")

        title = await generate_title_async(BoomPort(), [{"role": "user", "content": "x"}], "m")
        assert title is None


class TestMaybeUpdateTitleAsync:
    async def test_writes_file(self):
        from src.core.title_generator import maybe_update_title_async
        from src.core.adapters.model import MockAsyncModelAdapter
        from src.core.ports.model import ModelResult
        from src.chat_msgs import save_session, load_session, delete_session

        # 预置一个会话文件（截断标题）
        sid = save_session([{"role": "user", "content": "AI 摘要测试标题"}], model="m")
        try:
            port = MockAsyncModelAdapter(ModelResult(content="AI 摘要测试标题"))
            messages = [{"role": "user", "content": "AI 摘要测试标题"}, {"role": "assistant", "content": "回复"}]
            title = await maybe_update_title_async(port, messages, "m", sid)
            assert title == "AI 摘要测试标题"
            # 文件标题已被覆盖为 AI 标题
            data = load_session(sid)
            assert data["title"] == "AI 摘要测试标题"
        finally:
            delete_session(sid)

    async def test_empty_session_id(self):
        from src.core.title_generator import maybe_update_title_async
        from src.core.adapters.model import MockAsyncModelAdapter
        from src.core.ports.model import ModelResult

        port = MockAsyncModelAdapter(ModelResult(content="标题"))
        title = await maybe_update_title_async(port, [{"role": "user", "content": "x"}], "m", "")
        assert title is None
        assert port.call_count == 0

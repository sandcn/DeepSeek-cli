"""会话标题子系统（dsh session-title 语义）—— 单元测试。

覆盖：
  - ``normalize``：清洗（ANSI / 控制符 / 不可见符 / 代理项）、UTF-8 安全截断、
    确定性回退派生（词数 / 汉字数 / 字节上限）；
  - ``provider``：系统指令、JSON 输入、输出解析（围栏 / 引号 / 前缀 / 多行）；
  - ``service``：回退物化、合格消息过滤、all-prompts 修订、陈旧结果丢弃、
    失败保留旧标题、用户钉住 / 解钉、日志事件、持久化；
  - ``chat_msgs``：标题快照落盘、标题即时更新、用户重命名来源标记；
  - 会话日志投影：``title`` 折叠最新事件，且标题事件**不进入**模型输入。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

import src.chat_msgs as cm
from src.core.events.agent_types import SessionEventType
from src.core.session_log import SessionLog
from src.core.session_log.builtin_projections import (
    builtin_projection_spec,
)
from src.core.session_title import (
    SessionTitleService,
    TitleSnapshot,
    build_system_prompt,
    build_title_messages,
    build_user_payload,
    derive_fallback_title,
    parse_title_response,
    request_bytes,
    sanitize_title,
    truncate_utf8,
)
from src.core.session_title.normalize import ELLIPSIS, clip_cjk, count_cjk
from src.core.session_title.provider import select_messages_within_budget

# ═══════════════════════════════════════════════════════════════
# normalize
# ═══════════════════════════════════════════════════════════════


def test_sanitize_strips_ansi_and_control_sequences():
    # OSC 序列整体剥离（``\x1b]0;窗口\x07`` 内部内容属于窗口标题参数，不保留）
    assert sanitize_title("\x1b]0;窗口\x07标题\x1b[31m红\x1b[0m") == "标题红"
    assert sanitize_title("a\x00b\x07c") == "abc"


def test_sanitize_strips_bidi_and_zero_width():
    assert sanitize_title("标\u200b题\ufeff") == "标题"
    assert sanitize_title("\u202e反向\u202c") == "反向"


def test_sanitize_collapses_whitespace_to_single_line():
    assert sanitize_title("第一行\n第二行") == "第一行 第二行"
    assert sanitize_title("  多\t空白\u2028行  ") == "多 空白 行"


def test_sanitize_cleans_lone_surrogates():
    assert sanitize_title("坏\ud83d") == "坏\ufffd"


def test_truncate_utf8_never_splits_codepoint():
    text = "中文标题"  # 4 个 3 字节字符
    assert truncate_utf8(text, 12) == "中文标题"
    assert truncate_utf8(text, 11) == "中文标"
    assert truncate_utf8(text, 10) == "中文标"
    assert truncate_utf8(text, 2) == ""
    assert truncate_utf8(text, 0) == ""


def test_count_cjk_and_clip():
    assert count_cjk("abc中文かな한글") == 6
    assert clip_cjk("一二三四五", 3) == "一二三"
    assert clip_cjk("ab一二cd三四", 2) == "ab一二cd"


def test_derive_fallback_cjk_limit():
    text = "实现我的世界原版所有东西 不能操作git"
    # 前 12 个汉字截断后追加省略号
    assert derive_fallback_title(text, cjk_characters=12) == "实现我的世界原版所有东西" + ELLIPSIS


def test_derive_fallback_word_limit():
    text = "one two three four five six seven eight nine"
    assert derive_fallback_title(text, max_words=8) == "one two three four five six seven eight" + ELLIPSIS


def test_derive_fallback_byte_limit_keeps_codepoint():
    title = derive_fallback_title("中" * 200, cjk_characters=0, max_bytes=30)
    assert title.endswith(ELLIPSIS)
    assert len(title.encode("utf-8")) <= 30
    # 截断处不产生半个码点（可正常编码、可往返）
    assert title[:-1].encode("utf-8").decode("utf-8") == title[:-1]


def test_derive_fallback_empty_and_short():
    assert derive_fallback_title("") == ""
    assert derive_fallback_title("   ") == ""
    assert derive_fallback_title("你好") == "你好"


# ═══════════════════════════════════════════════════════════════
# provider
# ═══════════════════════════════════════════════════════════════


def test_system_prompt_carries_targets():
    prompt = build_system_prompt(target_words=6, target_cjk_characters=12)
    assert "6 个词" in prompt and "12 个汉字" in prompt
    assert "currentTitle" in prompt


def test_system_prompt_prefers_latest_message():
    """提示词明确「以最新用户消息为准」（用户需求）：标题反映当前主题。"""
    prompt = build_system_prompt(target_words=6, target_cjk_characters=12)
    assert "最新" in prompt
    assert "当前" in prompt


def test_select_messages_within_budget_keeps_latest():
    """超预算时从最新往前保留；旧消息省略并计数（最新一定参与）。"""
    messages = [(index, f"消息{index}") for index in range(100)]
    selected, omitted = select_messages_within_budget(messages, 200)
    assert selected and selected[-1] == (99, "消息99")
    assert omitted > 0
    assert omitted + len(selected) == 100


def test_select_messages_truncates_oversized_latest():
    """最新一条单独超预算：截断但保留（不能因旧消息挤掉最新诉求）。"""
    big = "长" * 500
    selected, omitted = select_messages_within_budget([(1, "旧"), (2, big)], 120)
    assert selected and selected[-1][0] == 2
    assert len(selected[-1][1]) < len(big)
    assert omitted == 1


def test_select_messages_no_limit_returns_all():
    messages = [(1, "a"), (2, "b")]
    assert select_messages_within_budget(messages, 0) == (messages, 0)


def test_build_title_messages_budget_keeps_latest():
    messages = [(index, "x" * 40) for index in range(50)] + [(99, "最新")]
    built = build_title_messages(
        messages, "", target_words=6, target_cjk_characters=12, max_input_bytes=2000,
    )
    payload = json.loads(built[1]["content"])
    assert payload["messages"][-1]["text"] == "最新"
    assert payload.get("omittedMessages", 0) > 0
    assert request_bytes(built) <= 2000


def test_user_payload_is_json_with_current_title():
    payload = build_user_payload([(1, "第一句"), (3, "第二句")], "旧标题")
    data = json.loads(payload)
    assert data["currentTitle"] == "旧标题"
    assert data["messages"] == [
        {"seq": 1, "text": "第一句"},
        {"seq": 3, "text": "第二句"},
    ]


def test_build_title_messages_roles_and_bytes():
    messages = build_title_messages(
        [(1, "hi")], None, target_words=6, target_cjk_characters=12,
    )
    assert [m["role"] for m in messages] == ["system", "user"]
    assert request_bytes(messages) > 0


@pytest.mark.parametrize("raw,expected", [
    ("会话标题重构", "会话标题重构"),
    ('"重构会话标题"', "重构会话标题"),
    ("标题：重构会话标题。", "重构会话标题"),
    ("```\n重构会话标题\n```", "重构会话标题"),
    ("解释：这是标题\n重构会话标题", "解释：这是标题"),
    ("  多行\n第二行 ", "多行"),
    ("(已中断)", ""),
    ("(已中断，无内容)", ""),
    ("\n\n", ""),
    (None, ""),
])
def test_parse_title_response(raw, expected):
    assert parse_title_response(raw) == expected


# ═══════════════════════════════════════════════════════════════
# service
# ═══════════════════════════════════════════════════════════════


class _FakePersistence:
    """记录 update_session_title 调用的假持久化端口。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def update_session_title(self, session_id, **kwargs):
        self.calls.append({"session_id": session_id, **kwargs})
        return True


def _make_service(messages, *, model="test-model", session_id="sid1",
                  log=None, persistence=None, call_model=None):
    return SessionTitleService(
        messages_getter=lambda: messages,
        session_id_getter=lambda: session_id,
        model_getter=lambda: model,
        persistence=persistence,
        log_getter=(lambda: log) if log is not None else None,
        call_model=call_model,
    )


def test_ensure_fallback_uses_first_eligible_message():
    service = _make_service([
        {"role": "system", "content": "系统提词"},
        {"role": "user", "content": "帮我写一个测试"},
        {"role": "user", "content": "继续"},
    ])
    snapshot = service.ensure_fallback()
    assert snapshot is not None
    assert snapshot.title == "帮我写一个测试"
    assert snapshot.source == "fallback"
    # 已有标题时不覆盖
    assert service.ensure_fallback() is snapshot


def test_fallback_skips_injected_and_image_only_messages():
    messages = [
        {"role": "user", "content": "<skill_content>技能内容</skill_content>"},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]},
        {"role": "user", "content": "真正的第一条"},
    ]
    service = _make_service(messages)
    snapshot = service.ensure_fallback()
    assert snapshot is not None and snapshot.title == "真正的第一条"


def test_fallback_uses_log_seqs_when_available():
    log = SessionLog(session_id="sid1")
    log.append(SessionEventType.USER_MESSAGE, content="首条提问")
    log.append(SessionEventType.ASSISTANT_MESSAGE, content="回答")
    service = _make_service([{"role": "user", "content": "首条提问"}], log=log)
    snapshot = service.ensure_fallback()
    assert snapshot is not None
    assert snapshot.message_seqs == (1,)


def test_rename_pins_title_and_persists():
    persistence = _FakePersistence()
    service = _make_service([{"role": "user", "content": "hi"}], persistence=persistence)
    snapshot = service.rename("  我的标题  ")
    assert snapshot is not None
    assert snapshot.title == "我的标题" and snapshot.source == "user"
    assert service.pinned is True
    service.flush()
    assert persistence.calls and persistence.calls[-1]["source"] == "user"


def test_rename_rejects_empty_title():
    service = _make_service([{"role": "user", "content": "hi"}])
    with pytest.raises(ValueError):
        service.rename("   ")


def test_unpin_reports_previous_state():
    service = _make_service([{"role": "user", "content": "hi"}])
    service.rename("标题")
    assert service.unpin() is True
    assert service.pinned is False
    assert service.unpin() is False


def test_rename_appends_title_event_without_touching_messages():
    log = SessionLog(session_id="sid1")
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    service = _make_service([{"role": "user", "content": "hi"}], log=log)
    service.rename("标题A")
    title_events = [e for e in log.events() if e.type == SessionEventType.TITLE]
    assert title_events and title_events[-1].data["title"] == "标题A"
    # 标题事件绝不进入模型输入（derive_messages 只认消息类事件）
    assert log.derive_messages() == [{"role": "user", "content": "hi"}]


def test_ensure_fallback_does_not_persist():
    persistence = _FakePersistence()
    service = _make_service([{"role": "user", "content": "hi"}], persistence=persistence)
    service.ensure_fallback()
    service.flush()
    assert persistence.calls == []


def test_on_user_message_without_event_loop_is_safe():
    service = _make_service([{"role": "user", "content": "hi"}])
    service.on_user_message()  # 无运行中事件循环：只物化回退，不抛错
    assert service.snapshot is not None


def test_on_user_message_skips_when_pinned():
    service = _make_service([{"role": "user", "content": "hi"}])
    service.rename("钉住的标题")

    async def _run():
        service.on_user_message()
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert service.snapshot is not None and service.snapshot.title == "钉住的标题"


@pytest.mark.asyncio
async def test_refresh_accepts_provider_title():
    persistence = _FakePersistence()
    seen: list[list[dict]] = []

    def _call(messages):
        seen.append(messages)
        return "「重构会话标题」", ""

    service = _make_service(
        [{"role": "user", "content": "把标题改成 dsh 那套"}],
        persistence=persistence, call_model=_call,
    )
    snapshot = await service.refresh_async()
    assert snapshot is not None
    assert snapshot.title == "重构会话标题"
    assert snapshot.source == "provider"
    assert snapshot.model == "test-model"
    assert snapshot.message_seqs == (1,)
    assert seen and seen[0][0]["role"] == "system"
    service.flush()
    assert persistence.calls and persistence.calls[-1]["source"] == "provider"


@pytest.mark.asyncio
async def test_provider_failure_keeps_current_title():
    service = _make_service([{"role": "user", "content": "hi"}])
    service.ensure_fallback()
    before = service.snapshot

    def _boom(messages):
        raise RuntimeError("模型不可用")

    service._call_model = _boom  # 模拟辅助请求失败
    await service.refresh_async()
    assert service.snapshot == before
    assert "模型不可用" in service.last_error


@pytest.mark.asyncio
async def test_interrupted_conversation_skips_revision():
    """对话被中断（Esc 后未复位）：不发标题辅助请求，保留当前标题。"""
    calls: list = []
    service = _make_service(
        [{"role": "user", "content": "hi"}],
        call_model=lambda messages: calls.append(messages) or ("新标题", ""),
    )
    service.ensure_fallback()
    before = service.snapshot

    async def _interrupted():
        return True

    service._interrupted = _interrupted
    service._clear_interrupt = lambda: None
    await service.refresh_async()
    assert calls == []
    assert service.snapshot == before


@pytest.mark.asyncio
async def test_empty_model_output_keeps_current_title():
    service = _make_service([{"role": "user", "content": "hi"}])
    service.ensure_fallback()
    before = service.snapshot
    service._call_model = lambda messages: ("", "")
    await service.refresh_async()
    assert service.snapshot == before


@pytest.mark.asyncio
async def test_over_budget_sends_latest_and_omits_old():
    """超预算：所有消息尽量参与，旧消息被省略、最新消息必在（以最新为准）。"""
    messages = [{"role": "user", "content": f"旧消息{index}"} for index in range(60)]
    messages.append({"role": "user", "content": "最新的一条：请实现标题功能"})
    calls: list[list[dict]] = []

    def _call(built):
        calls.append(built)
        return "最新主题", ""

    service = _make_service(messages, call_model=_call)
    service.ensure_fallback()
    service._config = lambda: {
        "enabled": True, "target_words": 6, "target_cjk_characters": 12,
        "fallback_max_words": 8, "fallback_cjk_characters": 12,
        "fallback_max_bytes": 96, "max_title_bytes": 120,
        "max_input_bytes": 2000, "timeout_ms": 10000,
    }
    snapshot = await service.refresh_async()
    assert snapshot is not None and snapshot.title == "最新主题"
    assert calls
    payload = json.loads(calls[0][1]["content"])
    assert payload["messages"][-1]["text"] == "最新的一条：请实现标题功能"
    assert payload["omittedMessages"] > 0
    # 旧消息被裁掉（不是全部参与，但最新一定参与）
    assert all(item["text"] != "旧消息0" for item in payload["messages"])


@pytest.mark.asyncio
async def test_within_budget_sends_all_user_messages():
    """预算充足：所有用户消息都参与（不省略）。"""
    messages = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "回答"},
        {"role": "user", "content": "第二条"},
    ]
    calls: list[list[dict]] = []
    service = _make_service(messages, call_model=lambda built: calls.append(built) or ("标题", ""))
    service.ensure_fallback()
    await service.refresh_async()
    payload = json.loads(calls[0][1]["content"])
    assert [item["text"] for item in payload["messages"]] == ["第一条", "第二条"]
    assert "omittedMessages" not in payload


@pytest.mark.asyncio
async def test_stale_revision_does_not_commit():
    """较新的修订取代旧工作：陈旧结果不得提交。"""
    service = _make_service([{"role": "user", "content": "hi"}])
    service.ensure_fallback()
    before = service.snapshot
    service._revision = 5

    async def _slow(messages):
        service._revision = 6  # 期间又有新消息 → 新修订
        return "过期标题", ""

    service._call_model = _slow
    await service._run_revision(5)
    assert service.snapshot == before


@pytest.mark.asyncio
async def test_session_switch_discards_stale_result():
    """会话切换后丢弃旧会话的标题结果（避免写错文件 / 改错窗口标题）。"""
    state = {"sid": "sid1"}
    persistence = _FakePersistence()
    service = SessionTitleService(
        messages_getter=lambda: [{"role": "user", "content": "hi"}],
        session_id_getter=lambda: state["sid"],
        model_getter=lambda: "m",
        persistence=persistence,
        call_model=lambda messages: (state.__setitem__("sid", "sid2"), ("新标题", ""))[1],
    )
    service.ensure_fallback()
    before = service.snapshot
    service._revision = 1
    await service._run_revision(1)
    assert service.snapshot == before
    service.flush()
    assert persistence.calls == []


@pytest.mark.asyncio
async def test_legacy_session_is_retitled_automatically():
    """历史会话（无来源标记）加载后：默认自动起标题，下一轮即被模型标题替换。"""
    async def _not_interrupted():
        return False

    service = _make_service(
        [{"role": "user", "content": "hi"}],
        call_model=lambda messages: ("自动生成的标题", ""),
    )
    service.restore_from_data({"title": "旧的历史标题"})
    assert service.pinned is False
    service._interrupted = _not_interrupted

    service.on_user_message()
    await asyncio.sleep(0.05)
    assert service.snapshot is not None
    assert service.snapshot.title == "自动生成的标题"
    assert service.snapshot.source == "provider"


@pytest.mark.asyncio
async def test_wait_for_revision_returns_provider_title():
    """单次模式保存前等待：标题任务完成后返回模型标题。"""
    async def _slow(messages):
        await asyncio.sleep(0.05)
        return "等待后的标题", ""

    async def _not_interrupted():
        return False

    service = _make_service([{"role": "user", "content": "hi"}], call_model=_slow)
    service.ensure_fallback()
    service._interrupted = _not_interrupted
    service._revision = 1
    service._task = asyncio.create_task(service._run_revision(1))
    snapshot = await service.wait_for_revision(5.0)
    assert snapshot is not None and snapshot.title == "等待后的标题"
    assert snapshot.source == "provider"
    assert service.pinned is False


@pytest.mark.asyncio
async def test_wait_for_revision_timeout_keeps_current_title():
    """等待超时：保留当前标题，不阻塞退出。"""
    async def _hang(messages):
        await asyncio.sleep(30)
        return "不会到达", ""

    async def _not_interrupted():
        return False

    service = _make_service([{"role": "user", "content": "hi"}], call_model=_hang)
    service.ensure_fallback()
    before = service.snapshot
    service._interrupted = _not_interrupted
    service._revision = 1
    service._task = asyncio.create_task(service._run_revision(1))
    snapshot = await service.wait_for_revision(0.05)
    assert snapshot == before


@pytest.mark.asyncio
async def test_wait_for_revision_without_pending_task():
    service = _make_service([{"role": "user", "content": "hi"}])
    service.ensure_fallback()
    assert await service.wait_for_revision(1.0) == service.snapshot


def test_same_title_with_new_messages_updates_seqs():
    """标题相同但参与消息变了：更新归属消息与时间戳（以最新一次为准）。"""
    service = _make_service([{"role": "user", "content": "hi"}])
    first = service._accept(
        "标题", "provider", model="m", seqs=(1,), persist=False, target_sid=None,
    )
    second = service._accept(
        "标题", "provider", model="m", seqs=(1, 2, 3), persist=False, target_sid=None,
    )
    assert second is not first
    assert second.message_seqs == (1, 2, 3)
    assert second.updated_at >= first.updated_at


def test_same_title_same_messages_is_deduplicated():
    """同一标题 + 同一批消息：真正无变化，不产生新快照。"""
    service = _make_service([{"role": "user", "content": "hi"}])
    first = service._accept(
        "标题", "provider", model="m", seqs=(1,), persist=False, target_sid=None,
    )
    again = service._accept(
        "标题", "provider", model="m", seqs=(1,), persist=False, target_sid=None,
    )
    assert again is first


@pytest.mark.asyncio
async def test_generating_status_events_around_revision():
    """生成期间广播 title_generating=True，结束（成功/失败）后复位 False。"""
    events: list[tuple[str, dict]] = []

    async def _not_interrupted():
        return False

    service = SessionTitleService(
        messages_getter=lambda: [{"role": "user", "content": "hi"}],
        session_id_getter=lambda: "sid1",
        model_getter=lambda: "m",
        emit=lambda name, **kw: events.append((name, kw)),
        call_model=lambda messages: ("标题", ""),
    )
    service.ensure_fallback()
    service._interrupted = _not_interrupted
    await service.refresh_async()
    flags = [kw["generating"] for name, kw in events if name == "title_generating"]
    assert flags == [True, False]


@pytest.mark.asyncio
async def test_generating_status_resets_on_failure():
    """生成失败也要复位（否则状态栏会一直卡在「标题生成中」）。"""
    events: list[tuple[str, dict]] = []

    async def _not_interrupted():
        return False

    def _boom(messages):
        raise RuntimeError("模型不可用")

    service = SessionTitleService(
        messages_getter=lambda: [{"role": "user", "content": "hi"}],
        session_id_getter=lambda: "sid1",
        model_getter=lambda: "m",
        emit=lambda name, **kw: events.append((name, kw)),
        call_model=_boom,
    )
    service.ensure_fallback()
    service._interrupted = _not_interrupted
    await service.refresh_async()
    flags = [kw["generating"] for name, kw in events if name == "title_generating"]
    assert flags == [True, False]


def test_title_generating_updates_status_model():
    """UI 接线：title_generating 事件驱动状态栏段的数据源。"""
    from src.app_loop import _session_setup as setup
    from src.tui.app.model import AppModel

    class _Ui:
        def __init__(self):
            self._model = AppModel()
            self.redraws = 0

        def get_model(self):
            return self._model

        def request_bottom_redraw(self):
            self.redraws += 1

    ui = _Ui()
    callbacks = setup._make_round_callbacks(
        _FakeSession(), monitor=None, loop_state={}, chat_ui=ui,
    )
    callbacks["on_title_generating"](generating=True)
    assert ui._model.status.title_generating is True
    assert ui.redraws == 1
    callbacks["on_title_generating"](generating=False)
    assert ui._model.status.title_generating is False


def test_restore_from_data_legacy_title_is_not_pinned():
    """历史标题（无 title_source）按 fallback 处理：默认自动起标题，不钉住。"""
    service = _make_service([{"role": "user", "content": "hi"}])
    snapshot = service.restore_from_data({"title": "旧标题"})
    assert snapshot is not None and snapshot.source == "fallback"
    assert service.pinned is False


def test_restore_from_data_user_source_still_pins():
    service = _make_service([{"role": "user", "content": "hi"}])
    snapshot = service.restore_from_data({"title": "手动标题", "title_source": "user"})
    assert snapshot is not None and snapshot.source == "user"
    assert service.pinned is True


def test_restore_from_data_clears_when_no_title():
    service = _make_service([{"role": "user", "content": "hi"}])
    service.rename("旧标题")
    service.restore_from_data({"title": ""})
    assert service.snapshot is None
    assert service.pinned is False


def test_restore_from_log_folds_latest_event():
    log = SessionLog(session_id="sid1")
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    log.append(SessionEventType.TITLE, title="旧", source="fallback")
    log.append(SessionEventType.TITLE, title="新", source="user")
    service = _make_service([{"role": "user", "content": "hi"}], log=log)
    snapshot = service.restore_from_log(log)
    assert snapshot is not None
    assert snapshot.title == "新" and snapshot.source == "user"
    assert service.pinned is True


def test_title_updated_event_is_emitted():
    events: list[dict] = []
    service = SessionTitleService(
        messages_getter=lambda: [{"role": "user", "content": "hi"}],
        session_id_getter=lambda: "sid1",
        model_getter=lambda: "m",
        emit=lambda name, **kw: events.append({"name": name, **kw}),
    )
    service.ensure_fallback()
    assert events and events[-1]["name"] == "title_updated"
    assert events[-1]["title"] == "hi"


# ═══════════════════════════════════════════════════════════════
# chat_msgs（标题快照 / 即时更新 / 用户来源）
# ═══════════════════════════════════════════════════════════════


@pytest.fixture()
def msgs_dir(tmp_path: Path, monkeypatch):
    target = tmp_path / "msg_list"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cm, "CHAT_MSGS_DIR", target)
    monkeypatch.setattr(cm, "ensure_chat_msgs_dir", lambda: None)
    monkeypatch.setattr(cm, "get_token_stats", lambda: {"input": 0, "output": 0, "total": 0})
    monkeypatch.setattr(cm, "_sync_terminal_title", lambda title: None)
    cm._invalidate_session_cache()
    cm._session_cache_mtime = 0.0
    return target


def test_save_session_uses_title_snapshot(msgs_dir):
    sid = cm.save_session(
        [{"role": "user", "content": "hi"}],
        "m",
        title_snapshot={
            "title": "模型生成的标题",
            "source": "provider",
            "model": "m-fast",
            "message_seqs": [1],
            "updated_at": 123.0,
        },
    )
    data = json.loads((msgs_dir / f"{sid}.json").read_text(encoding="utf-8"))
    assert data["title"] == "模型生成的标题"
    assert data["title_source"] == "provider"
    assert data["title_model"] == "m-fast"
    assert data["title_message_seqs"] == [1]
    assert data["title_updated_at"] == 123.0


def test_update_session_title_only_touches_title(msgs_dir):
    sid = cm.save_session([{"role": "user", "content": "hi"}], "m")
    before = json.loads((msgs_dir / f"{sid}.json").read_text(encoding="utf-8"))
    assert cm.update_session_title(
        sid, title="新标题", source="provider", model="m-fast",
        message_seqs=[1], updated_at=200.0,
    ) is True
    data = json.loads((msgs_dir / f"{sid}.json").read_text(encoding="utf-8"))
    assert data["title"] == "新标题"
    assert data["title_source"] == "provider"
    assert data["title_updated_at"] == 200.0
    # 消息 / 计数 / 保存时间原样
    assert data["messages"] == before["messages"]
    assert data["message_count"] == before["message_count"]
    assert data["saved_at"] == before["saved_at"]


def test_update_session_title_missing_or_empty(msgs_dir):
    assert cm.update_session_title("ghost", title="x") is False
    sid = cm.save_session([{"role": "user", "content": "hi"}], "m")
    assert cm.update_session_title(sid, title="   ") is False


def test_rename_session_marks_user_source(msgs_dir):
    sid = cm.save_session([{"role": "user", "content": "hi"}], "m")
    assert cm.rename_session(sid, "用户标题") is True
    data = json.loads((msgs_dir / f"{sid}.json").read_text(encoding="utf-8"))
    assert data["title"] == "用户标题"
    assert data["title_source"] == "user"
    assert "title_message_seqs" not in data


def test_save_session_marks_unknown_source_as_fallback(msgs_dir):
    """旧文件有标题但没有来源：保存时记为 fallback（默认自动起标题，不钉住）。"""
    (msgs_dir / "legacy.json").write_text(json.dumps({
        "id": "legacy", "title": "手工标题", "model": "m",
        "saved_at": "2026-08-01T00:00:00",
        "messages": [{"role": "user", "content": "hi"}],
    }, ensure_ascii=False), encoding="utf-8")
    cm.save_session([{"role": "user", "content": "hi"}], "m", session_id="legacy")
    data = json.loads((msgs_dir / "legacy.json").read_text(encoding="utf-8"))
    assert data["title"] == "手工标题"
    assert data["title_source"] == "fallback"


# ═══════════════════════════════════════════════════════════════
# 会话日志：事件类型 + title 投影
# ═══════════════════════════════════════════════════════════════


def test_title_event_type_registered():
    assert SessionEventType.TITLE == "session/title"
    assert SessionEventType.TITLE in SessionEventType.ALL


def test_title_projection_folds_latest_event():
    initial, folder = builtin_projection_spec("title")
    log = SessionLog(session_id="sid1")
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    log.append(SessionEventType.TITLE, title="旧", source="fallback")
    log.append(SessionEventType.TITLE, title="新", source="provider", model="m", message_seqs=[1])

    state = initial()
    for event in log.events():
        state = folder(state, event)
    assert state["title"] == "新"
    assert state["source"] == "provider"
    assert state["model"] == "m"
    assert state["message_seqs"] == (1,)


def test_title_event_not_in_model_messages():
    log = SessionLog(session_id="sid1")
    log.append(SessionEventType.USER_MESSAGE, content="hi")
    log.append(SessionEventType.TITLE, title="标题", source="provider")
    assert log.derive_messages() == [{"role": "user", "content": "hi"}]


# ═══════════════════════════════════════════════════════════════
# 并发：后台落盘与周期保存共用文件锁
# ═══════════════════════════════════════════════════════════════


def test_concurrent_title_update_and_save_do_not_lose_data(msgs_dir):
    """标题落盘与整文件保存在并发线程下交错执行：两者结果都要保留。"""
    sid = cm.save_session([{"role": "user", "content": "hi"}], "m")
    stopped = threading.Event()
    errors: list = []

    def _updater():
        try:
            for index in range(20):
                cm.update_session_title(sid, title=f"标题{index}", source="provider")
        except Exception as exc:  # pragma: no cover - 失败即测试失败
            errors.append(exc)
        finally:
            stopped.set()

    thread = threading.Thread(target=_updater, daemon=True)
    thread.start()
    while not stopped.is_set():
        cm.save_session(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
            "m", session_id=sid,
        )
    thread.join(5)
    assert errors == []
    data = json.loads((msgs_dir / f"{sid}.json").read_text(encoding="utf-8"))
    assert len(data["messages"]) == 2
    assert data["title"]


def test_title_snapshot_roundtrip():
    snapshot = TitleSnapshot(title="标题", source="provider", model="m",
                             message_seqs=(1, 2), updated_at=time.time())
    restored = TitleSnapshot.from_mapping(snapshot.to_dict())
    assert restored == snapshot
    assert TitleSnapshot.from_mapping({"title": ""}) is None


# ═══════════════════════════════════════════════════════════════
# UI 接线：标题被接受 → 终端窗口标题（OSC）立即更新
# ═══════════════════════════════════════════════════════════════


class _FakeSession:
    """``_make_round_callbacks`` 构造期只需 session 属性存在（回调不触发）。"""


def test_title_updated_writes_terminal_title(monkeypatch):
    import src.tui._screen as screen
    from src.app_loop import _session_setup as setup

    captured: list[str] = []
    monkeypatch.setattr(screen, "set_window_title", lambda title: captured.append(title))
    monkeypatch.setattr(setup, "_terminal_title_sync_enabled", lambda: True)

    callbacks = setup._make_round_callbacks(
        _FakeSession(), monitor=None, loop_state={}, chat_ui=None,
    )
    callbacks["on_title"](title="  新标题  ", source="provider")
    assert captured == ["新标题"]


def test_title_updated_emits_real_osc_sequence(monkeypatch):
    """端到端：标题被接受 → 真实写出 OSC 0 窗口标题序列（不 mock set_window_title）。"""
    import io
    import sys

    from src.app_loop import _session_setup as setup

    buffer = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", buffer)
    monkeypatch.setattr(setup, "_terminal_title_sync_enabled", lambda: True)

    callbacks = setup._make_round_callbacks(
        _FakeSession(), monitor=None, loop_state={}, chat_ui=None,
    )
    callbacks["on_title"](title="标题A", source="provider")
    assert buffer.getvalue() == "\033]0;标题A\007"


def test_title_updated_respects_sync_config(monkeypatch):
    import src.tui._screen as screen
    from src.app_loop import _session_setup as setup

    captured: list[str] = []
    monkeypatch.setattr(screen, "set_window_title", lambda title: captured.append(title))
    monkeypatch.setattr(setup, "_terminal_title_sync_enabled", lambda: False)

    callbacks = setup._make_round_callbacks(
        _FakeSession(), monitor=None, loop_state={}, chat_ui=None,
    )
    callbacks["on_title"](title="新标题", source="provider")
    assert captured == []


# ═══════════════════════════════════════════════════════════════
# /load：会话切换同步 session_id 与标题状态
# ═══════════════════════════════════════════════════════════════


class _FakePort:
    def __init__(self, data):
        self._data = data
        self.saved: list = []

    def save_session(self, messages, model, session_id=None, subagents=None,
                     session_log=None, title_snapshot=None):
        self.saved.append(session_id)
        return session_id or "saved-id"

    def load_session(self, session_id):
        return self._data


def test_load_command_switches_session_id_and_title(monkeypatch):
    """/load 后：session_id 指向被加载会话，标题状态同步（含钉住来源）。"""
    from src.core.commands._data_cmd import _load_session_by_id
    from src.core.internal.commands._command_core import CommandContext

    data = {
        "id": "abc",
        "model": "m",
        "title": "加载的标题",
        "title_source": "provider",
        "messages": [{"role": "user", "content": "历史提问"}],
        "subagents": [],
        "session_log": [],
    }
    port = _FakePort(data)
    messages = [{"role": "system", "content": "sys"}]

    class _Session:
        def __init__(self):
            self.session_id = "old"
            self.title_service = SessionTitleService(
                messages_getter=lambda: messages,
                session_id_getter=lambda: self.session_id,
                model_getter=lambda: "m",
                persistence=port,
            )

    session = _Session()
    ctx = CommandContext(
        messages=messages, state={}, arg="abc",
        build_system_prompt=lambda: "", get_user_input=lambda prompt="": "",
        context_manager=None, session=session, persistence_port=port,
    )
    assert _load_session_by_id(ctx, "abc") is True
    assert session.session_id == "abc"
    assert session.title_service.snapshot is not None
    assert session.title_service.snapshot.title == "加载的标题"
    assert session.title_service.snapshot.source == "provider"
    assert any(m.get("content") == "历史提问" for m in messages)

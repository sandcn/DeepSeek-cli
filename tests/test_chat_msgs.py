"""chat_msgs 会话模块测试 — 终端窗口标题同步

覆盖：save_session 起完标题后同步终端窗口标题（OSC 序列）、
空标题跳过、超长标题截断同步。
"""

from __future__ import annotations


class TestTerminalTitleSync:
    """save_session 生成标题后同步终端窗口标题。"""

    def _capture(self, monkeypatch) -> list[str]:
        """patch set_window_title，返回捕获标题列表。"""
        captured: list[str] = []
        import src.tui._screen as screen

        def fake_set_window_title(title: str) -> None:
            captured.append(title)

        monkeypatch.setattr(screen, "set_window_title", fake_set_window_title)
        return captured

    def test_save_session_syncs_terminal_title(self, monkeypatch):
        """save_session 保存含 user 消息的会话 → 终端标题设为提取的标题。"""
        captured = self._capture(monkeypatch)
        from src.chat_msgs import save_session, delete_session

        sid = save_session([{"role": "user", "content": "我的测试任务"}], model="m")
        try:
            assert captured == ["我的测试任务"]
        finally:
            delete_session(sid)

    def test_save_session_syncs_first_user_message(self, monkeypatch):
        """标题取自第一条 user 消息（跳过 system）。"""
        captured = self._capture(monkeypatch)
        from src.chat_msgs import save_session, delete_session

        sid = save_session(
            [
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "第一条用户消息"},
                {"role": "assistant", "content": "回复"},
                {"role": "user", "content": "第二条用户消息"},
            ],
            model="m",
        )
        try:
            assert captured == ["第一条用户消息"]
        finally:
            delete_session(sid)

    def test_save_session_skip_when_no_user_message(self, monkeypatch):
        """无 user 消息（仅 system）→ 标题为空 → 不调用 set_window_title。"""
        captured = self._capture(monkeypatch)
        from src.chat_msgs import save_session, delete_session

        sid = save_session([{"role": "system", "content": "s"}], model="m")
        try:
            assert captured == []
        finally:
            delete_session(sid)

    def test_save_session_syncs_truncated_title(self, monkeypatch):
        """超长标题截断为 40 字符 + … 后同步。"""
        captured = self._capture(monkeypatch)
        from src.chat_msgs import save_session, delete_session

        long_msg = "这是一个非常长的用户消息用于测试标题截断" * 3  # 54 字符
        sid = save_session([{"role": "user", "content": long_msg}], model="m")
        try:
            assert len(captured) == 1
            assert captured[0] == long_msg[:40] + "…"
        finally:
            delete_session(sid)

    def test_sync_terminal_title_exception_is_silent(self, monkeypatch):
        """set_window_title 抛异常时不阻断会话保存（非关键路径）。"""
        import src.tui._screen as screen

        def boom(title: str) -> None:
            raise RuntimeError("no tty")

        monkeypatch.setattr(screen, "set_window_title", boom)
        from src.chat_msgs import save_session, delete_session, load_session

        sid = save_session([{"role": "user", "content": "异常场景"}], model="m")
        try:
            # 会话仍成功保存
            assert load_session(sid) is not None
        finally:
            delete_session(sid)


class TestSaveSessionPreserveTitle:
    """save_session 在已有标题（AI 生成/用户重命名）时保留，不覆盖为截断标题。"""

    def test_preserve_existing_title(self):
        from src.chat_msgs import save_session, load_session, delete_session, rename_session

        sid = save_session([{"role": "user", "content": "首条用户消息很长用于截断标题的测试内容"}], model="m")
        try:
            # 模拟 AI 标题生成（rename_session 覆盖）
            assert rename_session(sid, "AI 摘要标题")
            # 再次保存 → 保留 AI 标题，不被截断标题覆盖
            save_session([{"role": "user", "content": "首条用户消息很长用于截断标题的测试内容"}],
                         model="m", session_id=sid)
            data = load_session(sid)
            assert data["title"] == "AI 摘要标题"
        finally:
            delete_session(sid)

    def test_first_save_uses_truncated_title(self, monkeypatch):
        """首次保存（无已有文件）→ 使用截断标题。"""
        captured = []
        import src.tui._screen as screen

        def fake_set_window_title(title: str) -> None:
            captured.append(title)

        monkeypatch.setattr(screen, "set_window_title", fake_set_window_title)
        from src.chat_msgs import save_session, load_session, delete_session

        content = "首条用户消息很长用于截断标题的测试内容再来一些额外的字符补充继续加长到超过四十字符才行"  # >40 字符
        sid = save_session([{"role": "user", "content": content}], model="m")
        try:
            data = load_session(sid)
            assert data["title"] == content[:40] + "…"
            assert captured == [content[:40] + "…"]
        finally:
            delete_session(sid)

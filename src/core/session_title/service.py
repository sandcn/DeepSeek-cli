"""会话标题服务 —— 日志持久化标题 + 异步 all-prompts 生成 + 用户钉住。

对应 DeepSeek Harness ``ctx.sessionTitle``（``dsh-session-title``）的裁剪版
（本项目按用户需求只实现 ``all-prompts`` 节奏）：

- **三路来源，最新者胜**：确定性回退（首条合格用户消息开头若干词）、
  all-prompts 提供方（辅助 LLM 请求）、显式用户重命名（``source=user``，钉住）；
- **持久化**：每次被接受的修订都追加一条 ``session/title`` 会话事件（仅写入
  日志，绝不进入模型输入——``derive_messages`` 只认消息类与结构变更事件），
  并同步写入会话文件顶层字段（``title`` / ``title_source`` / ``title_model`` /
  ``title_message_seqs``），因此回放、恢复、分页后标题都在；
- **绝不阻塞主对话**：生成在独立 asyncio 任务里跑，晚到的结果只能追加自己
  那条事件；自动失败只告警并保留当前标题，显式 ``refresh`` 才是重试；
- **最新修订胜出**：新提示词、用户重命名、显式刷新都会中止旧工作；即使提供方
  忽略取消，陈旧结果也无法提交（修订号 + 会话 id 双重校验）；
- **不污染统计**：辅助请求以 ``STREAM_LABEL_TITLE`` + ``silent`` 发起，不渲染
  到终端，也不计入主 Agent 的上下文使用率。

标题文本处理（清洗 / 字节截断 / 回退派生）在 ``normalize``，提示词与输出解析
在 ``provider``；本模块只负责调度、接受校验、持久化与事件广播。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from . import provider as _provider
from .normalize import (
    derive_fallback_title,
    sanitize_title,
    truncate_utf8,
)
from ..loop_prompt import LOOP_PROMPT_MARKER

_logger = logging.getLogger(__name__)

#: 标题来源：确定性回退（首条合格用户消息）。
SOURCE_FALLBACK = "fallback"
#: 标题来源：all-prompts 提供方（辅助 LLM 请求）。
SOURCE_PROVIDER = "provider"
#: 标题来源：用户显式重命名（钉住，停止自动生成）。
SOURCE_USER = "user"

#: 视为「非人类输入」的 user 消息前缀（本项目内部注入，不应参与取名）。
_NON_HUMAN_PREFIXES: tuple[str, ...] = (
    "<skill_content>",
    "<compacted-summary>",
    "[对话摘要]",
    "[检查点恢复]",
    "【继续】",
    LOOP_PROMPT_MARKER,
)

#: 标题事件类型（会话日志，仅写入不进入模型输入）。
_TITLE_EVENT = "session/title"


@dataclass(frozen=True)
class TitleSnapshot:
    """一次被接受的标题修订（对应 dsh 的 ``SessionTitleSnapshot``）。"""

    title: str
    source: str = SOURCE_FALLBACK
    model: str = ""
    message_seqs: tuple[int, ...] = ()
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "source": self.source,
            "model": self.model,
            "message_seqs": list(self.message_seqs),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, data: Any) -> TitleSnapshot | None:
        """从会话文件 / 事件 data 构造快照（缺字段按默认值，标题空则 None）。"""
        if not isinstance(data, dict):
            return None
        title = sanitize_title(data.get("title") or "")
        if not title:
            return None
        raw_seqs = data.get("message_seqs") or ()
        seqs: list[int] = []
        if isinstance(raw_seqs, (list, tuple)):
            for item in raw_seqs:
                try:
                    seqs.append(int(item))
                except (TypeError, ValueError):
                    continue
        try:
            updated_at = float(data.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            updated_at = 0.0
        source = str(data.get("source") or SOURCE_FALLBACK)
        if source not in (SOURCE_FALLBACK, SOURCE_PROVIDER, SOURCE_USER):
            source = SOURCE_FALLBACK
        return cls(
            title=title,
            source=source,
            model=str(data.get("model") or ""),
            message_seqs=tuple(seqs),
            updated_at=updated_at,
        )


class SessionTitleService:
    """会话标题服务 —— 每个 ``ChatSession`` 一个实例。

    依赖全部以可调用对象注入（消息列表 / 会话日志 / 会话 id / 模型 / 持久化
    端口 / 事件发射），因此本模块与 ``ChatSession`` 之间没有循环导入，也便于
    单测直接构造。
    """

    def __init__(
        self,
        *,
        messages_getter: Callable[[], Sequence[dict]],
        session_id_getter: Callable[[], str | None],
        model_getter: Callable[[], str],
        persistence: Any = None,
        log_getter: Callable[[], Any] | None = None,
        emit: Callable[..., None] | None = None,
        config_getter: Callable[[], dict] | None = None,
        call_model: Callable[..., Any] | None = None,
    ) -> None:
        self._get_messages = messages_getter
        self._get_session_id = session_id_getter
        self._get_model = model_getter
        self._persistence = persistence
        self._get_log = log_getter
        self._emit = emit
        self._config_getter = config_getter
        self._call_model = call_model

        self._lock = threading.RLock()
        self._snapshot: TitleSnapshot | None = None
        #: 用户重命名后钉住：后续用户消息不再安排自动修订（refresh 是显式解钉）。
        self._pinned = False
        self._revision = 0
        self._task: asyncio.Task | None = None
        self._save_threads: list[threading.Thread] = []
        #: 最近一次失败原因（``/title show`` 展示；成功时清空）。
        self._last_error = ""

    # ── 配置 ─────────────────────────────────────────────

    def _config(self) -> dict:
        """读取标题配置（``session_title`` 节；缺字段回退内置默认）。"""
        if self._config_getter is not None:
            try:
                data = self._config_getter()
                if isinstance(data, dict):
                    return dict(data)
            except Exception:
                _logger.debug("读取标题配置失败，回退默认值", exc_info=True)
        from ...config.defaults import DEFAULTS

        base = dict(DEFAULTS.get("session_title") or {})
        try:
            from ...config import get_rc

            rc = get_rc()
        except Exception:
            rc = None
        raw = rc.get("session_title") if isinstance(rc, dict) else None
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key in base:
                    base[key] = value
        return base

    @staticmethod
    def _as_int(cfg: dict, key: str, fallback: int) -> int:
        try:
            return int(cfg.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    # ── 状态读取 ─────────────────────────────────────────

    @property
    def snapshot(self) -> TitleSnapshot | None:
        with self._lock:
            return self._snapshot

    @property
    def pinned(self) -> bool:
        with self._lock:
            return self._pinned

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def status(self) -> dict:
        """服务状态（``/title show`` 使用）。"""
        with self._lock:
            snapshot = self._snapshot
            return {
                "title": snapshot.title if snapshot else "",
                "source": snapshot.source if snapshot else "",
                "model": snapshot.model if snapshot else "",
                "message_seqs": list(snapshot.message_seqs) if snapshot else [],
                "updated_at": snapshot.updated_at if snapshot else 0.0,
                "pinned": self._pinned,
                "revision": self._revision,
                "generating": bool(self._task is not None and not self._task.done()),
                "last_error": self._last_error,
            }

    # ── 合格用户消息 ─────────────────────────────────────

    @staticmethod
    def _message_text(content: Any) -> str:
        """消息 content → 纯文本（多模态 content blocks 只取文本部分）。"""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        try:
            from ..multimodal import content_to_text

            return content_to_text(content)
        except Exception:
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                return " ".join(parts)
            return str(content)

    @classmethod
    def _is_human_text(cls, text: str) -> bool:
        """是否为「可取名」的人类文本（排除纯图片与内部注入消息）。"""
        cleaned = sanitize_title(text)
        if not cleaned:
            return False
        if cleaned.startswith(_NON_HUMAN_PREFIXES):
            return False
        # 纯图片消息：content 只有 image block 时文本部分为 ``[图片]`` 占位，
        # 不构成可取名的提示词（dsh 同样等待后续文本输入）。
        return bool(cleaned.replace("[图片]", "").strip())

    def _eligible_messages(self) -> list[tuple[int, str]]:
        """截至当前的合格用户消息 ``[(seq, text), ...]``（按日志顺序）。

        有会话日志时用 ``user/message`` 事件的 ``seq``（真实持久序号）；无日志
        视图（自定义 Agent / 旧会话）时回退消息列表**下标 + 1**——两种来源都
        只用于归因与展示，不参与模型输入序号语义。
        """
        log = self._get_log() if self._get_log is not None else None
        out: list[tuple[int, str]] = []
        if log is not None:
            try:
                for event in list(log):
                    if event.type != "user/message":
                        continue
                    text = self._message_text((event.data or {}).get("content"))
                    if self._is_human_text(text):
                        out.append((int(event.seq), sanitize_title(text)))
            except Exception:
                _logger.debug("读取会话日志中的用户消息失败，回退消息列表", exc_info=True)
                out = []
            if out:
                return out
        try:
            messages = list(self._get_messages() or ())
        except Exception:
            return out
        for index, message in enumerate(messages):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            text = self._message_text(message.get("content"))
            if self._is_human_text(text):
                out.append((index + 1, sanitize_title(text)))
        return out

    # ── 回退标题 ─────────────────────────────────────────

    def ensure_fallback(self) -> TitleSnapshot | None:
        """物化确定性回退标题（已有标题时原样返回，不覆盖）。

        Returns:
            当前快照；没有合格用户消息时返回 ``None``。
        """
        with self._lock:
            if self._snapshot is not None:
                return self._snapshot
        eligible = self._eligible_messages()
        if not eligible:
            return None
        cfg = self._config()
        title = derive_fallback_title(
            eligible[0][1],
            max_words=self._as_int(cfg, "fallback_max_words", 8),
            cjk_characters=self._as_int(cfg, "fallback_cjk_characters", 12),
            max_bytes=self._as_int(cfg, "fallback_max_bytes", 96),
        )
        if not title:
            return None
        return self._accept(
            title,
            SOURCE_FALLBACK,
            model="",
            seqs=(eligible[0][0],),
            persist=False,
            target_sid=self._get_session_id(),
        )

    # ── 调度（消费一条用户消息后调用） ───────────────────

    def on_user_message(self) -> None:
        """**消费一条用户消息后**、主模型请求发出前：物化回退 + 安排一次修订。

        时机（用户需求 2026-10-20）：用户消息进入会话日志后立刻触发，标题模型
        与主回答**并发**请求——标题不必等整轮回答完才开始生成。除此之外只有
        手动 ``/title refresh`` / ``/title auto`` 会调用标题模型（重试、
        周期保存、加载、退出都不触发）。

        用户钉住（``source=user``）后不再自动安排；未启用 / 无事件循环时静默
        跳过（同步 CLI、无头环境不因此报错）。
        """
        cfg = self._config()
        if not cfg.get("enabled", True):
            return
        self.ensure_fallback()
        with self._lock:
            if self._pinned:
                return
        self._schedule_revision()

    def _schedule_revision(self) -> None:
        """安排一次自动修订（较新的修订取代旧工作）。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _logger.debug("无运行中事件循环，跳过自动标题生成")
            return
        with self._lock:
            self._revision += 1
            revision = self._revision
            previous = self._task
            if previous is not None and not previous.done():
                previous.cancel()
            task = loop.create_task(
                self._run_revision(revision), name="session-title",
            )
            self._task = task
        task.add_done_callback(self._consume_task)

    @staticmethod
    def _consume_task(task: asyncio.Task) -> None:
        """消费任务异常（取消与失败都不应冒泡成「Task exception never retrieved」）。"""
        if task.cancelled():
            return
        with contextlib.suppress(asyncio.CancelledError):
            task.exception()

    def _cancel_revision(self) -> None:
        """中止进行中的自动修订（用户重命名 / 退出时调用）。"""
        with self._lock:
            self._revision += 1
            task = self._task
            self._task = None
        if task is not None and not task.done():
            task.cancel()

    # ── 生成 ─────────────────────────────────────────────

    async def _call_title_model(self, messages: list[dict]) -> tuple[str, str]:
        """调用标题模型，返回 ``(content, reasoning)``。

        经 ``api.model_async.call_model_async``（流式管线）以内部标签 + silent
        发起：不渲染、不计入主 Agent 上下文使用率；重试与空响应均有界（辅助
        请求必须快速失败，不能像主对话那样无限重发）。
        """
        if self._call_model is not None:
            result = self._call_model(messages)
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, tuple):
                content = result[0] if result else ""
                reasoning = result[1] if len(result) > 1 else ""
                return str(content or ""), str(reasoning or "")
            return str(result or ""), ""
        from ...api.model_async import call_model_async
        from ..constants import STREAM_LABEL_TITLE

        reasoning, content, _usage, _tool_calls = await call_model_async(
            messages,
            model=self._get_model(),
            tools=None,
            display=None,
            label=STREAM_LABEL_TITLE,
            silent=True,
            override_max_retries=1,
            empty_response_retries=1,
        )
        return str(content or ""), str(reasoning or "")

    @staticmethod
    async def _interrupted() -> bool:
        """当前是否处于对话中断状态（中断期间不发标题辅助请求）。

        中断标志在用户按 Esc 后保持到下一轮输入复位；期间发起的辅助请求会被
        管线直接以 ``(已中断，无内容)`` 占位符返回（解析层已拒绝该占位符，
        这里再加一道前置检查，省掉一次无意义的请求）。
        """
        try:
            from ..interrupt_state import is_interrupted_async

            return bool(await is_interrupted_async())
        except Exception:
            return False

    @staticmethod
    def _clear_interrupt() -> None:
        """清理中断标志（仅显式 ``refresh`` 使用：用户主动要求重新生成）。

        主对话被 Esc 中断后中断标志会保持到下一轮输入；显式刷新标题属于用户
        主动操作，此时主对话已停止，清理标志不会影响进行中的生成。
        """
        try:
            from ..interrupt_state import reset_interrupt_async

            reset_interrupt_async()
        except Exception:
            _logger.debug("清理中断标志失败（忽略）", exc_info=True)

    async def _run_revision(self, revision: int) -> None:
        """执行一次修订（失败保留当前标题，不抛出到主对话）。"""
        cfg = self._config()
        if await self._interrupted():
            _logger.debug("对话处于中断状态，跳过本次标题修订")
            return
        session_id = self._get_session_id()
        eligible = self._eligible_messages()
        if not eligible:
            return
        with self._lock:
            current = self._snapshot
        # ★ 2026-10-20（用户需求「所有用户消息参与、以最新为准」）：消息
        #   全量传给标题模型；仅当超出 ``max_input_bytes`` 时按**最新优先**
        #   裁剪（旧消息省略并计入 omittedMessages，最新一条必定保留），
        #   而不是整次放弃（此前超限直接失败 → 长会话永远只剩旧标题）。
        max_input_bytes = self._as_int(cfg, "max_input_bytes", 32768)
        messages = _provider.build_title_messages(
            eligible,
            current.title if current is not None else "",
            target_words=self._as_int(cfg, "target_words", 6),
            target_cjk_characters=self._as_int(cfg, "target_cjk_characters", 12),
            max_input_bytes=max_input_bytes,
        )
        timeout_ms = self._as_int(cfg, "timeout_ms", 10000)
        timeout = max(1.0, timeout_ms / 1000.0) if timeout_ms > 0 else None
        # ★ 2026-10-20（用户需求：取标题运行中要有状态显示）：生成期间广播
        #   ``title_generating=True``（状态栏显示「✎ 标题生成中」），结束 /
        #   失败 / 超时 / 取消都在 finally 复位——辅助任务此前对用户完全不可见。
        self._notify_generating(True)
        try:
            try:
                if timeout is not None:
                    content, reasoning = await asyncio.wait_for(
                        self._call_title_model(messages), timeout=timeout,
                    )
                else:
                    content, reasoning = await self._call_title_model(messages)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                self._record_error(f"标题生成超时（{int(timeout or 0)} 秒），保留当前标题")
                return
            except Exception as exc:  # noqa: BLE001 - 辅助请求失败不阻断对话
                _logger.warning("标题生成失败，保留当前标题: %s", exc, exc_info=True)
                self._record_error(f"标题生成失败：{exc}")
                return

            title = _provider.parse_title_response(content) or _provider.parse_title_response(reasoning)
            if not title:
                self._record_error("标题模型未返回可用标题，保留当前标题")
                return
            if revision != self._current_revision():
                return
            if session_id != self._get_session_id():
                # 会话已切换：丢弃这次结果（标题属于旧会话），避免写错文件 / 改错窗口标题。
                _logger.debug("会话已切换，丢弃陈旧的标题结果: %r", title)
                return
            self._accept(
                title,
                SOURCE_PROVIDER,
                model=self._get_model(),
                seqs=tuple(seq for seq, _text in eligible),
                persist=True,
                target_sid=session_id,
            )
        finally:
            self._notify_generating(False)

    def _current_revision(self) -> int:
        with self._lock:
            return self._revision

    # ── 接受 / 持久化 ────────────────────────────────────

    def _accept(
        self,
        title: str,
        source: str,
        *,
        model: str,
        seqs: Sequence[int],
        persist: bool,
        target_sid: str | None,
    ) -> TitleSnapshot | None:
        """接受一次标题修订（规范化 + 字节上限 + 去重 + 落盘 + 广播）。"""
        cfg = self._config()
        max_title_bytes = self._as_int(cfg, "max_title_bytes", 120)
        clean = truncate_utf8(sanitize_title(title), max_title_bytes).strip()
        if not clean:
            return None
        seqs_tuple = tuple(int(seq) for seq in seqs)
        with self._lock:
            previous = self._snapshot
            if (
                previous is not None
                and previous.title == clean
                and previous.source == source
                and previous.message_seqs == seqs_tuple
            ):
                # 同一来源、同一标题、同一批消息：真正无变化，不追加事件 / 不写盘。
                self._pinned = self._pinned or source == SOURCE_USER
                return previous
            # ★ 2026-10-20（用户需求「所有用户消息参与、以最新为准」）：标题相同但
            #   参与消息变了（又有新的用户消息），仍视为一次新修订——更新归属
            #   消息与时间戳并落盘，使「本次标题基于哪些消息」始终反映最新一次。
            snapshot = TitleSnapshot(
                title=clean,
                source=source,
                model=str(model or ""),
                message_seqs=seqs_tuple,
                updated_at=time.time(),
            )
            self._snapshot = snapshot
            if source == SOURCE_USER:
                self._pinned = True
            self._last_error = ""

        self._append_title_event(snapshot)
        if persist:
            self._persist(snapshot, target_sid)
        self._notify(snapshot)
        return snapshot

    def _append_title_event(self, snapshot: TitleSnapshot) -> None:
        """追加 ``session/title`` 会话事件（仅写入日志，不进模型输入）。"""
        log = self._get_log() if self._get_log is not None else None
        if log is None:
            return
        try:
            log.append(
                _TITLE_EVENT,
                title=snapshot.title,
                source=snapshot.source,
                model=snapshot.model,
                message_seqs=list(snapshot.message_seqs),
                updated_at=snapshot.updated_at,
            )
        except Exception:
            _logger.debug("追加标题事件失败（不阻断标题接受）", exc_info=True)

    def _notify(self, snapshot: TitleSnapshot) -> None:
        """广播标题变更（UI 层据此更新终端窗口标题 / 刷新界面）。"""
        if self._emit is None:
            return
        try:
            self._emit(
                "title_updated",
                title=snapshot.title,
                source=snapshot.source,
                model=snapshot.model,
            )
        except Exception:
            _logger.debug("广播标题变更失败", exc_info=True)

    def _notify_generating(self, generating: bool) -> None:
        """广播「正在生成标题」状态（状态栏运行中提示；无 UI 时无副作用）。

        用户需求 2026-10-20：取标题是**异步辅助任务**（不阻塞对话、没有流式
        输出），此前运行中对用户完全不可见——生成期间据此在状态栏显示
        「✎ 标题生成中」，结束（成功 / 失败 / 超时 / 取消）后自动消失。
        """
        if self._emit is None:
            return
        try:
            self._emit("title_generating", generating=bool(generating))
        except Exception:
            _logger.debug("广播标题生成状态失败", exc_info=True)

    def _persist(self, snapshot: TitleSnapshot, target_sid: str | None) -> None:
        """把标题写入会话文件（后台线程；端口不支持时仅内存生效）。"""
        sid = target_sid or self._get_session_id()
        if not sid or self._persistence is None:
            return
        updater = getattr(self._persistence, "update_session_title", None)
        if not callable(updater):
            _logger.debug("持久化端口不支持 update_session_title，标题仅内存生效")
            return

        def _work() -> None:
            try:
                updater(
                    sid,
                    title=snapshot.title,
                    source=snapshot.source,
                    model=snapshot.model,
                    message_seqs=list(snapshot.message_seqs),
                    updated_at=snapshot.updated_at,
                )
            except Exception:
                _logger.warning("写入会话标题失败: session=%s", sid, exc_info=True)

        thread = threading.Thread(
            target=_work, name="session-title-save", daemon=True,
        )
        with self._lock:
            self._save_threads = [t for t in self._save_threads if t.is_alive()]
            self._save_threads.append(thread)
        thread.start()

    # ── 显式操作（命令 / 恢复） ──────────────────────────

    def rename(self, title: str) -> TitleSnapshot | None:
        """用户重命名：接受并**钉住**标题（停止自动生成，中止进行中修订）。"""
        cfg = self._config()
        clean = truncate_utf8(
            sanitize_title(title), self._as_int(cfg, "max_title_bytes", 120),
        ).strip()
        if not clean:
            raise ValueError("标题不能为空")
        with self._lock:
            self._pinned = True
        self._cancel_revision()
        snapshot = self._accept(
            clean,
            SOURCE_USER,
            model="",
            seqs=(),
            persist=True,
            target_sid=self._get_session_id(),
        )
        return snapshot

    def unpin(self) -> bool:
        """解除钉住（恢复自动生成）。返回此前是否处于钉住状态。"""
        with self._lock:
            was_pinned = self._pinned
            self._pinned = False
        return was_pinned

    async def refresh_async(self) -> TitleSnapshot | None:
        """显式重新生成（有意的解钉手段；失败保留当前标题）。"""
        self.unpin()
        self._clear_interrupt()
        with self._lock:
            self._revision += 1
            revision = self._revision
        await self._run_revision(revision)
        return self.snapshot

    def refresh_sync(self) -> TitleSnapshot | None:
        """同步版 ``refresh_async``（供命令线程调用）。"""
        from ...api._sync_bridge import run_coroutine_blocking

        return run_coroutine_blocking(self.refresh_async())

    async def wait_for_revision(self, timeout: float = 10.0) -> TitleSnapshot | None:
        """等待进行中的自动修订结束（单次问答保存前用，确保标题落盘）。

        标题生成是**异步辅助任务**：单次模式（``chat.py -p``）回答完立即保存
        并退出，此前只能落到回退标题（实测进程退出时标题任务被取消）。调用方
        在保存前等待一小段时间，即可让模型标题进入会话文件；超时则保留当前
        标题、不阻塞退出。

        Args:
            timeout: 最长等待秒数（``0`` 立即返回）。

        Returns:
            等待结束后的当前快照（可能仍是回退标题）。
        """
        with self._lock:
            task = self._task
        if task is None or task.done():
            return self.snapshot
        try:
            await asyncio.wait_for(task, timeout=max(0.0, float(timeout)))
        except asyncio.TimeoutError:
            _logger.debug("等待标题修订超时（%.1fs），保留当前标题", timeout)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.debug("等待标题修订失败（保留当前标题）", exc_info=True)
        return self.snapshot

    def restore_from_data(self, data: Any) -> TitleSnapshot | None:
        """从会话文件恢复标题状态（加载会话时调用）。

        ★ 2026-10-20（用户需求「默认自动起标题」）：历史标题（旧会话文件没有
        ``title_source``）按 **fallback** 处理，**不再视为用户钉住**——加载后
        下一条用户消息即可触发自动重新生成（可能覆盖历史标题，用户已确认）。
        只有显式 ``/title set`` 产生的 ``source=user`` 才钉住。
        """
        if not isinstance(data, dict):
            return None
        snapshot = TitleSnapshot.from_mapping({
            "title": data.get("title"),
            "source": data.get("title_source"),
            "model": data.get("title_model"),
            "message_seqs": data.get("title_message_seqs"),
            "updated_at": data.get("title_updated_at"),
        })
        if snapshot is None:
            # 加载的会话没有标题（旧文件 / 空会话）：清空内存状态，避免把上一个
            # 会话的标题当作当前标题广播或写盘。
            with self._lock:
                self._snapshot = None
                self._pinned = False
                self._last_error = ""
            return None
        with self._lock:
            self._snapshot = snapshot
            self._pinned = snapshot.source == SOURCE_USER
            self._last_error = ""
        return snapshot

    def notify_current(self) -> None:
        """把当前标题重新广播给 UI（加载会话 / 切换会话后同步窗口标题用）。"""
        snapshot = self.snapshot
        if snapshot is not None:
            self._notify(snapshot)

    def restore_from_log(self, log: Any) -> TitleSnapshot | None:
        """从会话日志折叠最新标题事件（日志是标题的持久事实源）。"""
        if log is None:
            return None
        snapshot = None
        try:
            for event in list(log):
                if event.type != _TITLE_EVENT:
                    continue
                data = dict(event.data or {})
                data.setdefault("updated_at", float(event.timestamp or 0.0))
                candidate = TitleSnapshot.from_mapping(data)
                if candidate is not None:
                    snapshot = candidate
        except Exception:
            _logger.debug("折叠会话标题事件失败", exc_info=True)
            return None
        if snapshot is None:
            return None
        with self._lock:
            self._snapshot = snapshot
            self._pinned = self._pinned or snapshot.source == SOURCE_USER
        return snapshot

    # ── 生命周期 ─────────────────────────────────────────

    def cancel(self) -> None:
        """取消进行中的修订并等待落盘线程（退出路径调用）。"""
        self._cancel_revision()
        self.flush(timeout=2.0)

    def flush(self, timeout: float = 5.0) -> None:
        """等待后台落盘线程结束（测试 / 退出路径）。"""
        with self._lock:
            threads = list(self._save_threads)
        for thread in threads:
            thread.join(timeout)

    def _record_error(self, message: str) -> None:
        """记录最近一次生成失败原因（自动失败仅告警 + 保留当前标题）。"""
        with self._lock:
            self._last_error = str(message)
        _logger.warning("会话标题自动生成未更新：%s", message)


__all__ = [
    "SOURCE_FALLBACK",
    "SOURCE_PROVIDER",
    "SOURCE_USER",
    "SessionTitleService",
    "TitleSnapshot",
]

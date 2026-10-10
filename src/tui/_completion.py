"""补全交互模块 — Tab 补全弹出选择与自动补全。

_CmplHandler 在 EscapeMonitor/Render 线程中：
  1. 计算候选项（CompletionEngine）
  2. 设置补全状态（bottom_bar.show_completions / hide_completions / cycle_completion）
  3. 请求 render 线程重绘（request_redraw）
  4. 查询只读状态（is_completion_visible / get_selected_completion）

与旧 ``consumer/completion.py`` 逻辑完全一致，仅调整导入路径。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable
import os
import re

if TYPE_CHECKING:
    from src.tui._completion_engine import CompletionEngine
    from src.tui._ink_bridge import InkBridge


def _get_match_prefix(items: list, last_word: str) -> str:
    """计算补全弹窗的匹配前缀。

    路径补全场景（file/dir 类型）：使用 basename 作为匹配前缀，
    因为弹窗中只显示文件名（不显示路径前缀）。
    非路径场景：直接使用最后一个词。
    """
    if items and items[0].item_type in ("file", "dir"):
        # P2：路径分隔符兼容——原硬编码 ``'/' in last_word`` 对 Windows 路径
        # （``C:\foo\bar``）无效；统一按 ``os.sep``/``os.altsep`` 判断
        # （POSIX os.sep='/'；Windows 为 '\\'/'/' 双分隔符）。
        if os.sep in last_word or (os.altsep and os.altsep in last_word):
            return os.path.basename(last_word)
        return last_word
    return last_word


def _last_word_of(text: str) -> str:
    """取最后一个非空词（尾随空格时回退前一个词）。

    ``re.split(r"\\s+", text)`` 保留尾随空串（``"cd "`` → ``['cd', '']``）——
    取最后一个非空词使 ``orig_prefix`` 与引擎 ``start_pos`` 的「替换最后一个
    词」语义对齐（否则 ``/config set `` + Tab 会应用出重复前缀）。
    """
    words = re.split(r"\s+", text)
    last_word = words[-1] if words else ""
    if last_word == "" and words:
        for w in reversed(words):
            if w:
                return w
    return last_word


#: ``_show_completions_for`` 结果：候选就绪 / 数据加载中 / 无候选。
_SHOW_ITEMS = "items"
_SHOW_LOADING = "loading"
_SHOW_NONE = "none"


def _show_completions_for(bb, engine, text: str) -> str:
    """计算候选项并显示补全弹窗（on_auto / _first_tab / 数据就绪刷新共用）。

    三态返回：候选就绪显示弹窗；数据源仍在后台加载（异步模式）显示
    「加载中…」占位弹窗（就绪后由 ``_CmplHandler._on_data_ready`` 动态刷新）；
    无候选且无待加载数据返回 ``_SHOW_NONE``（调用方关闭弹窗）。

    Args:
        bb: InkBridge 实例（调用 show_completions）。
        engine: CompletionEngine 实例（调用 complete / pending）。
        text: 当前输入缓冲区文本。

    Returns:
        ``_SHOW_ITEMS`` / ``_SHOW_LOADING`` / ``_SHOW_NONE``。
    """
    items = engine.complete(text)
    last_word = _last_word_of(text)

    if not items:
        if getattr(engine, "pending", False):
            bb.show_completions(
                [], 0, texts=[], start_pos=0, orig_prefix=last_word,
                types=[], match_prefix=last_word, loading=True,
            )
            return _SHOW_LOADING
        return _SHOW_NONE

    match_prefix = _get_match_prefix(items, last_word)

    bb.show_completions(
        [item.display for item in items], 0,
        texts=[item.text for item in items],
        start_pos=items[0].start_pos,
        orig_prefix=last_word,
        types=[item.item_type for item in items],
        match_prefix=match_prefix,
        # Claude TUI parity 步骤 3.7：斜杠命令描述（缺省空列表兼容旧调用）
        descriptions=[getattr(item, "desc", "") for item in items],
    )
    return _SHOW_ITEMS


class _CmplHandler:
    """Tab 补全交互处理器。

    由 EscapeMonitor 线程回调驱动，管理补全弹窗的
    首次激活、循环选择、关闭和上下键导航。

    与 CompletionEngine（纯计算型）分工：
      - CompletionEngine：计算补全候选项（命令/路径/参数）
      - _CmplHandler：管理补全 UI 交互流程（弹窗/循环/应用）
    """

    def __init__(
        self, bottom_bar: "InkBridge", engine: "CompletionEngine",
        request_redraw: Callable[[], None],
        text_provider: Callable[[], str] | None = None,
    ):
        self._bb = bottom_bar
        self._engine = engine
        self._request_redraw = request_redraw
        self._last_auto_text: str | None = None
        #: 当前输入文本提供者（数据就绪后按**最新**输入重算；未注入时回退
        #: 最近一次补全请求文本，保证异步刷新不显示过期候选）。
        self._text_provider = text_provider
        self._last_request_text = ""
        # 异步补全：数据源后台加载就绪 → 重新计算并刷新弹窗（动态更新界面）。
        add_listener = getattr(engine, "add_listener", None)
        if callable(add_listener):
            try:
                add_listener(self._on_data_ready)
            except Exception:
                pass

    def set_text_provider(self, provider: Callable[[], str] | None) -> None:
        """注入当前输入文本提供者（装配后由 ``setup_completion`` 注入）。"""
        self._text_provider = provider

    def _current_text(self) -> str:
        """当前输入文本（provider 优先，异常/未注入回退最近一次请求文本）。"""
        provider = self._text_provider
        if callable(provider):
            try:
                text = provider()
                if text is not None:
                    return str(text)
            except Exception:
                pass
        return self._last_request_text

    def _on_data_ready(self, key: str = "") -> None:
        """数据源就绪回调（后台线程）→ 按最新输入重算并刷新补全弹窗。

        绕过自动补全防抖（``_last_auto_text`` 置 None）：同一文本的补全在
        数据未就绪时已被防抖跳过，就绪后必须重算才能显示候选。
        从未触发过补全（``_last_auto_text is None``）且弹窗不可见时直接返回
        ——启动预热完成不触发无谓重绘。
        """
        if self._last_auto_text is None and not self._bb.is_completion_visible:
            return
        self._last_auto_text = None
        self.on_auto(self._current_text())

    def on_tab(self, text: str) -> str | None:
        """Tab 补全入口。

        数据加载中（「加载中…」占位弹窗可见）→ 不应用、不插入制表符，
        等就绪后由 ``_on_data_ready`` 刷新；
        补全弹窗已可见 → 确认当前选中项并应用；
        弹窗不可见 → 计算候选项，显示弹窗，返回首个匹配。
        """
        self._last_request_text = text
        if self._bb.is_completion_loading:
            return text
        if self._bb.is_completion_visible:
            return self._cycle_tab(text)
        return self._first_tab(text)

    def on_dismiss(self) -> None:
        """关闭补全弹窗（ESC/非 Tab 按键触发）。"""
        self._bb.hide_completions()
        self._request_redraw()
        self._last_auto_text = None  # 重置防抖，允许下次相同文本重新触发

    def on_navigate(self, delta: int, text: str) -> str | None:
        """上下键导航补全弹窗（delta: -1=上, +1=下）。

        text 参数由 EscapeMonitor 传入当前输入缓冲区文本，
        确保与 on_tab 使用同一来源的 text，消除 _last_text 过期风险。

        弹窗不可见时返回 None，EscapeMonitor 回退为正常上下键行为。
        弹窗可见时更新选中状态 + 请求 render 线程重绘。
        """
        if not self._bb.is_completion_visible:
            return None
        self._bb.cycle_completion(delta)
        self._request_redraw()
        return text  # 仅导航高亮，不应用补全文字

    def on_auto(self, text: str) -> None:
        """自动补全入口 — 用户输入可打印字符时自动触发。

        规则：
          - 文本为空 → 隐藏弹窗
          - 不以 / 开头且长度 < 2 → 隐藏弹窗（避免过早弹出）
          - 有候选项 → 显示/更新弹窗，选中索引重置为 0
          - 无候选项 → 隐藏弹窗
        """
        # 防抖：文本未变化时跳过重复计算（None 为哨兵值，首次调用不跳过）
        if self._last_auto_text is not None and text == self._last_auto_text:
            return
        self._last_request_text = text

        if not text:
            self._bb.hide_completions()
            self._request_redraw()
            self._last_auto_text = text
            return

        # 最小触发长度：命令（/开头）1字符即可
        if not text.startswith('/'):
            # 自动补全普通文本仅对**疑似路径**触发——打字时对普通单词逐键
            # glob 当前目录（engine.complete → _complete_path）是渲染线程
            # 性能热点（大目录下输入卡顿）。Tab 明确补全（_first_tab）不走
            # 本分支，路径补全功能完整保留。方向3 性能优化。
            # ★ P2（review）：统一用 re.split(r"\s+")（与引擎口径一致）——
            #   split(" ") 对含 \t 输入不切分（cd\t/src 中 last_word 取整段
            #   含制表符串，路径判定失效）。
            words = re.split(r"\s+", text)
            last_word = words[-1] if words else ""
            # ★ 修复（review 方向）：路径分隔符兼容——与 _get_match_prefix
            #   一致按 os.sep/os.altsep 判断（修复前硬编码 "/"：Windows 上
            #   ``C:\proj\sr`` / ``foo\bar`` 永不触发自动路径补全）。
            sep_hit = os.sep in last_word or (os.altsep and os.altsep in last_word)
            is_pathish = (
                sep_hit
                or last_word.startswith("~")
                or last_word.startswith(".")
            )
            if len(text) < 2 or not is_pathish:
                self._bb.hide_completions()
                self._request_redraw()
                self._last_auto_text = text
                return

        if _show_completions_for(self._bb, self._engine, text) == _SHOW_NONE:
            self._bb.hide_completions()
            self._request_redraw()
            self._last_auto_text = text
            return

        # ★ P3（review）：删除成功的冗余 ``_request_redraw()``——
        #   ``_show_completions_for`` 内的 ``bb.show_completions`` 已请求重绘
        #   （见 InkBridge.show_completions 末尾），原第二次调用幂等冗余。
        self._last_auto_text = text

    # ── 内部方法 ──────────────────────────────────────

    def _cycle_tab(self, text: str) -> str | None:
        """已可见弹窗 → **确认当前选中项**，应用补全到输入缓冲区。

        Tab 键走此路径：直接获取当前高亮的补全项并应用（确认语义）。
        与 on_navigate（箭头键）不同——后者只移动高亮不应用补全（导航语义）。
        弹窗重新出现后 Tab 可继续确认新的选中项（配合 auto-completion 自动刷新）。
        """
        repl_text, start_pos, orig_prefix = self._bb.get_selected_completion()
        if not repl_text:
            return None
        return _apply_completion(text, repl_text, start_pos, orig_prefix)

    def _first_tab(self, text: str) -> str | None:
        """首次 Tab → 计算候选项，设置状态 + 请求重绘。

        数据加载中（``_SHOW_LOADING``）→ 弹窗显示占位并返回 text（不插入
        制表符，等就绪后自动刷新）；无候选 → 关闭弹窗返回 None（插入制表符）。
        """
        status = _show_completions_for(self._bb, self._engine, text)
        if status == _SHOW_NONE:
            self._bb.hide_completions()
            self._request_redraw()
            return None
        return text


def _apply_completion(
    text: str, repl_text: str, start_pos: int, orig_prefix: str,
) -> str:
    """将补全结果应用到输入文本（模块级纯函数）。

    三阶段定位 orig_prefix 的替换位置：
      1. 词边界匹配（(^|\\s) 前缀 + (?=\\s|$) 后缀）从尾部向前找——最后一个
         完整词前缀命中（替换词前缀，保留词前空格）
      2. start_pos 裁剪回退 — 基于偏移量裁剪尾部后拼接
      3. 返回 repl_text — 兜底全替换
    """
    if orig_prefix:
        # 方向2（中间词误匹配修复）：rfind 会匹配中间词（如 "foo bar baz"
        # 中 orig_prefix="ba" 命中 "bar" 中部）→ 丢弃后缀。改用词边界匹配
        # 从尾部向前找——(^|\s) 前缀 + (?=\s|$) 后缀边界，仅替换完整词前缀
        # （``text[:m.end(1)]`` 保留词前空格，与旧 rfind 语义一致）；未命中
        # 边界匹配回退既有 start_pos 逻辑。
        # 说明（BUG-57 评估回退）：补全语义为「光标在词尾」——``text[:m.end(1)]
        # + repl_text`` 丢弃词尾空格/后续内容是有意设计（既有测试
        # ``test_apply_completion_last_word_boundary_regression`` 锁定「无尾随
        # 空格」）；光标中间场景由 start_pos 分支处理（无生产调用方）。
        boundary_matches = list(
            re.finditer(rf"(^|\s){re.escape(orig_prefix)}(?=\s|$)", text)
        )
        if boundary_matches:
            m = boundary_matches[-1]
            return text[:m.end(1)] + repl_text

    if start_pos < 0:
        trim_len = -start_pos
        if trim_len >= len(text):
            return repl_text
        return text[:len(text) - trim_len] + repl_text

    # start_pos > 0：保留供非 CompletionEngine 来源的调用（本引擎 complete 产出
    #   start_pos 恒 <=0——死分支为兼容潜在外部/测试调用方保留，勿删除；若未来
    #   确认无外部调用可评估移除）。★ P3（review 2026-08-22）补注释明确。
    if start_pos > 0 and start_pos < len(text):
        return text[:start_pos] + repl_text
    return repl_text

"""config_view — ConfigView 配置中心视图组件（模态全屏视图，2026-08-20）。

/config 命令（ConfigCommand 插件）打开：App 在 ``model.fullscreen ==
"config"`` 时经全屏视图注册表**整屏只渲染本组件**（消息区/顶部标题栏/
状态栏/输入区全部不显示——「打开时其他 TUI 不显示，只显示配置界面」），
配置列表占满整个终端；Esc/Ctrl+H 关闭后恢复完整聊天界面。

★ 2026-08-20（用户需求：配置的每一项都有编辑不同的编辑界面，含子 JSON）：
按配置项类型提供三种编辑界面——
  - **选择界面**（``edit_mode == "select"``）：有候选选项集合的配置项
    （枚举 provider/theme/reasoning_effort、bool true/false、MODEL 模型
    列表）Enter 后主区切换为**候选选项列表**（↑↓/jk 导航、g/G 首末、
    PgUp/PgDn 翻页、Enter 确认写回、Esc 取消）；
  - **子 JSON 结构化编辑界面**（``edit_mode == "json"``）：list/dict 有
    子结构的配置项（MODELS/MULTIMODAL_MODELS/TOKEN_PRICES/skills 等）
    Enter 后主区显示**子 JSON 条目列表**（list ``[i] 元素`` / dict
    ``key = 值``），支持增删改（Enter 编辑选中条目 · a 追加 · d 删除 ·
    Esc 保存写回返回浏览模式）；条目编辑/追加走**子输入行**
    （``edit_mode == "json_input"``——字符累积 · 退格 · Enter 确认 ·
    Esc 取消）；
  - **输入界面**（``edit_mode == "input"``）：字符串/数值配置项底部显示
    ``编辑 key = value▏`` 输入行（字符累积 · 退格删除 · Enter 确认（类型
    校验 + 持久化 + 刷新显示值）· Esc 取消）。

交互（use_input 路由 + 模态全屏声明，config 视图打开期间激活）：
  - 浏览模式：↑↓/jk 选择 · g/G 首末 · PgUp/PgDn 翻页 · Home/End 首末 ·
    Enter 编辑选中项 · Esc/Ctrl+H 关闭；
  - 选择界面：↑↓/jk 导航候选 · g/G 首末 · PgUp/PgDn 翻页 · Enter 确认 ·
    Esc 取消；
  - 子 JSON 界面：↑↓/jk 导航条目 · Enter 编辑 · a 追加 · d 删除 ·
    Esc 保存返回；
  - 输入/子输入界面：可打印字符累积到 ``edit_value`` · 退格删除 ·
    Enter 确认 · Esc 取消。

数据协议（跨线程安全，与 user_select/editmsg 同构）：
  - 命令线程（``_cmd_config``）：构建 entries
    （``view_model.build_config_entries``）→ 设置 ``model.config_view``
    （visible=True, seq+1, entries）→ ``model.fullscreen="config"`` →
    request_bottom_redraw → 轮询 ``state.done``（带 deadline 超时）→
    finally 清理（config_view 重置 + fullscreen 置空 + request_bottom_redraw）；
  - 组件：浏览导航写 ``state.selected``；选择/输入/子 JSON 确认经类型
    校验后**直接调用 ``update_config`` 持久化**（config loader 有锁 +
    原子写，线程安全），成功后更新 ``entries[i].value/value_text`` 并写
    ``state.message``；Esc 关闭经 ``state.try_set_final("cancel")`` 原子
    终态写入（first-write-wins——命令线程超时已置位则放弃覆盖）。

依赖约束：仅依赖 app 同层（model/_state_types）与 ink 框架 + config
view_model（纯逻辑）；无 tools 层反向依赖。
"""

from __future__ import annotations

import copy
import json

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple, truncate_width as _truncate_width
from src.tui.ink import TEXT, Column, Row, StyledRun, h
from src.tui.ink.hooks import use_fullscreen, use_input, usePaste
from src.tui.ink.helpers import truncate_runs
from src.tui.ink.widgets.listview import ListView
from src.config.view_model import format_config_value, parse_config_value

__all__ = ["ConfigView"]

# ── 样式（静态色——配置界面为浏览/编辑界面，不呼吸，diff 零输出） ──
_S_TITLE = Style(fg=45, bold=True)        # 视图标题（亮青加粗）
_S_HINT = Style(fg=242)                    # 提示/弱化（暗灰）
_S_SEP_ROW = Style(fg=238)                 # 分隔线（深灰）
_S_KEY = Style(fg=75)                      # 配置键名（浅紫蓝）
_S_VAL = Style(fg=252)                     # 字符串值（亮白）
_S_NUM = Style(fg=214)                     # 数值（黄）
_S_BOOL_TRUE = Style(fg=40)                # true（绿）
_S_BOOL_FALSE = Style(fg=196)              # false（红）
_S_COMPLEX = Style(fg=135)                 # 复合值（list/dict，紫）
_S_SENSITIVE = Style(fg=110)               # 敏感值（浅蓝，脱敏）
_S_DESC = Style(fg=110)                    # 说明列（浅蓝）
_S_SEL_BG = Style(bg=237)                  # 选中行背景（静态 237）
_S_SEL_MARK = Style(fg=45, bold=True)      # 选中 ▶ 标记（亮青加粗）
_S_OK = Style(fg=40, bold=True)            # 成功消息（绿）
_S_ERR = Style(fg=196, bold=True)          # 错误消息（红）
_S_EDIT = Style(fg=45, bold=True)          # 编辑输入行（亮青加粗）

#: 编辑输入长度上限（渲染行按宽度截断，无上限累积只浪费内存）
_EDIT_VALUE_MAX = 400


def _viewport_rows() -> int:
    """配置视图可见行数（终端高度自适应；无高度上下文回退 16）。

    ★ 模态全屏视图：ConfigView 整屏渲染——可用高度 = 终端高 - 头部标题栏
    1 行 - 底部编辑/消息行 1 行 ≈ ``h - 2``（列表视口）。
    """
    try:
        from src.tui._screen import TerminalWidthCache
        h = TerminalWidthCache.get_default().get_height()
        # 预算 = header 1 行 + 底部提示/错误最多 2 行（json 模式）→ 预留 3 行，
        # 避免窄终端 list_h 溢出。
        return max(8, int(h) - 3)
    except Exception:
        return 16


def _value_style(entry) -> Style | None:
    """配置值显示样式（按类型/敏感度分色）。"""
    typ = entry.get("type")
    if typ is bool:
        return _S_BOOL_TRUE if str(entry.get("value_text", "")) == "true" else _S_BOOL_FALSE
    if typ in (int, float):
        return _S_NUM
    if typ in (list, dict):
        return _S_COMPLEX
    if entry.get("sensitive"):
        return _S_SENSITIVE
    return _S_VAL


def _json_value_style(value) -> Style | None:
    """子 JSON 条目值样式（标量/复合分色）。"""
    if isinstance(value, bool):
        return _S_BOOL_TRUE if value else _S_BOOL_FALSE
    if isinstance(value, (int, float)):
        return _S_NUM
    if isinstance(value, (list, dict)):
        return _S_COMPLEX
    return _S_VAL


def _editable_text(entry) -> str:
    """进入输入界面的预填文本（敏感项留空重输；list/dict 预填 JSON）。"""
    if entry.get("sensitive"):
        return ""
    value = entry.get("value")
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    if value is None:
        return ""
    return str(value)


def _entry_by_key(entries: list, key: str) -> dict | None:
    """按写回键查找配置项（entries 列表元素为可变 dict）。"""
    for e in entries:
        if e.get("key") == key:
            return e
    return None


def _parse_json_element(text: str):
    """子 JSON 元素/值解析：合法 JSON 字面量（标量/list/dict）→ 解析值；
    否则原样字符串（如普通文本）。空输入返回空字符串。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return text


def _parse_key_value(text: str):
    """dict 追加解析：``key=value`` 或 ``key: value`` → (key, value)；
    无有效分隔返回 None（组件提示格式错误）。"""
    for sep in ("=", ":"):
        if sep in text:
            k, _, v = text.partition(sep)
            k = k.strip()
            if k:
                return k, _parse_json_element(v)
    return None


def _start_edit(cv, entry) -> None:
    """进入编辑：按 entry.edit_kind 选择编辑界面。

    select=候选选择界面（枚举/布尔/模型）；json=子 JSON 结构化编辑界面
    （list/dict 深拷贝数据 + 条目列表）；input=文本/数值输入界面。
    """
    cv.editing = True
    cv.edit_key = entry["key"]
    cv.edit_error = ""
    cv.message = ""
    # edit_kind 缺失时按 options 回退（select）；两者皆无 → input
    edit_kind = entry.get("edit_kind") or ("select" if entry.get("options") else "input")
    if edit_kind == "select":
        options = entry.get("options") or []
        cv.edit_mode = "select"
        cv.edit_options = [str(o[0]) for o in options]
        cv.edit_options_desc = [str(o[1]) for o in options]
        cur = entry.get("value")
        if isinstance(cur, bool):
            cur = "true" if cur else "false"
        elif cur is None:
            cur = ""
        else:
            cur = str(cur)
        idx = 0
        for i, o in enumerate(cv.edit_options):
            if o == cur:
                idx = i
                break
        cv.edit_selected = idx
        cv.edit_value = ""
    elif edit_kind == "json":
        # 子 JSON 结构化编辑：深拷贝当前数据（退出时一次性写回）；
        # edit_json_path 为空 = 顶层根容器（递归嵌套层由路径段表示）
        data = copy.deepcopy(entry.get("value"))
        if not isinstance(data, (list, dict)):
            data = [] if entry["type"] is list else {}
        cv.edit_mode = "json"
        cv.edit_json_data = data
        cv.edit_json_path = []
        cv.edit_json_keys = list(data.keys()) if isinstance(data, dict) else []
        cv.edit_json_selected = 0
        cv.edit_json_action = "edit"
        cv.edit_value = ""
    else:
        cv.edit_mode = "input"
        cv.edit_options = []
        cv.edit_options_desc = []
        cv.edit_selected = 0
        cv.edit_value = _editable_text(entry)


def _cancel_edit(cv) -> None:
    """取消当前编辑（退出编辑界面，不保存）。"""
    cv.editing = False
    cv.edit_mode = "input"
    cv.edit_error = ""
    cv.edit_options = []
    cv.edit_options_desc = []
    cv.edit_json_data = None
    cv.edit_json_path = []
    cv.edit_json_keys = []
    cv.edit_json_selected = 0


def ConfigView(props) -> object:
    """配置中心视图组件（模态全屏视图；App 按 FULLSCREEN_VIEWS 整屏渲染）。

    Props:
        model: AppModel 实例（读 ``model.config_view`` / ``model.fullscreen``）。
        width: 终端宽度（布局与截断预算）。
    """
    model = props["model"]
    width = props.get("width", 0) or 0
    cv = getattr(model, "config_view", None)
    visible = bool(cv is not None and cv.visible and not cv.done)
    entries = list(getattr(cv, "entries", None) or []) if cv is not None else []
    total = len(entries)
    editing = bool(getattr(cv, "editing", False)) if cv is not None else False
    edit_mode = getattr(cv, "edit_mode", "input") if cv is not None else "input"
    pick_mode = editing and edit_mode == "select"
    json_mode = editing and edit_mode == "json"
    json_input_mode = editing and edit_mode == "json_input"

    def _persist(entry, value) -> bool:
        """类型校验 + update_config 持久化 + 刷新显示值；失败写 edit_error。"""
        parsed, err = parse_config_value(entry.get("type", str), str(value))
        if err:
            cv.edit_error = err
            return False
        # 敏感项（api_key）空输入确认不得清空已有密钥（数据丢失防御）
        if entry.get("sensitive") and (parsed is None or parsed == ""):
            cv.edit_error = "敏感项不能为空，请输入新值（Esc 取消）"
            return False
        try:
            from src.config.loader import update_config
            update_config(entry["key"], parsed)
        except Exception as exc:
            cv.edit_error = f"写入失败: {exc}"
            return False
        entry["value"] = parsed
        entry["value_text"] = format_config_value(
            parsed, entry.get("type", str),
            sensitive=bool(entry.get("sensitive")),
        )
        return True

    def _commit_select() -> None:
        """选择界面确认：写回当前高亮候选值。"""
        if cv is None:
            return
        entry = _entry_by_key(entries, cv.edit_key)
        if entry is None:
            _cancel_edit(cv)
            return
        opts = list(cv.edit_options or [])
        idx = cv.edit_selected
        if not (0 <= idx < len(opts)):
            _cancel_edit(cv)
            return
        if _persist(entry, opts[idx]):
            cv.editing = False
            cv.edit_mode = "input"
            cv.edit_error = ""
            cv.message = f"已更新 {entry['path']} = {entry['value_text']}"

    def _commit_input() -> None:
        """输入界面确认：文本缓冲类型校验 → 持久化 → 刷新显示值。"""
        if cv is None:
            return
        entry = _entry_by_key(entries, cv.edit_key)
        if entry is None:
            _cancel_edit(cv)
            return
        if _persist(entry, cv.edit_value):
            cv.editing = False
            cv.edit_mode = "input"
            cv.edit_error = ""
            cv.message = f"已更新 {entry['path']} = {entry['value_text']}"

    # ── 子 JSON 编辑界面辅助（支持递归嵌套层） ────────────

    def _json_container():
        """按递归路径导航到当前容器（list/dict）；导航失败返回 None。

        ``edit_json_path`` 为空 = 顶层根容器（``edit_json_data``）；
        非空 = 逐段下钻（dict 用键名段、list 用索引段）。
        """
        data = cv.edit_json_data
        for seg in list(getattr(cv, "edit_json_path", None) or []):
            if isinstance(data, dict):
                data = data.get(seg)
            elif isinstance(data, list):
                try:
                    data = data[int(seg)]
                except (ValueError, IndexError, TypeError):
                    return None
            else:
                return None
        return data if isinstance(data, (list, dict)) else None

    def _json_entry_value(container, idx: int):
        """当前容器选中条目的值（dict 键值 / list 元素）；越界返回 None。"""
        if isinstance(container, dict):
            keys = list(container.keys())
            return container.get(keys[idx]) if 0 <= idx < len(keys) else None
        if isinstance(container, list):
            return container[idx] if 0 <= idx < len(container) else None
        return None

    def _json_entry_seg(container, idx: int):
        """当前容器选中条目的路径段（dict→键名；list→索引字符串）。"""
        if isinstance(container, dict):
            keys = list(container.keys())
            return keys[idx] if 0 <= idx < len(keys) else None
        return str(idx)

    def _json_path_text() -> str:
        """当前容器完整路径显示（breadcrumb；空=顶层）。"""
        segs = list(getattr(cv, "edit_json_path", None) or [])
        return ".".join(str(s) for s in segs) if segs else ""

    # ★ P2（review 2026-08-22）：``_json_item_text`` 死代码已删除——渲染走
    #   ``_render_json_item``（见下属列表 renderItem），此方法无任何调用方。
    def _json_edit_selected() -> None:
        """json 界面 Enter：嵌套（list/dict 值）→ 递归进入下一层；
        标量 → 子输入编辑。"""
        container = _json_container()
        if container is None:
            cv.edit_error = "目标容器不可用"
            return
        if not container:
            cv.edit_error = "容器为空，按 a 追加"
            return
        idx = cv.edit_json_selected
        cur = _json_entry_value(container, idx)
        if cur is None:
            cv.edit_error = "无选中条目"
            return
        if isinstance(cur, (list, dict)):
            # 递归进入下一层（path 追加段）
            seg = _json_entry_seg(container, idx)
            if seg is None:
                cv.edit_error = "无选中条目"
                return
            cv.edit_json_path = list(getattr(cv, "edit_json_path", None) or []) + [seg]
            cv.edit_json_keys = list(cur.keys()) if isinstance(cur, dict) else []
            cv.edit_json_selected = 0
            cv.edit_json_action = "edit"
            cv.edit_value = ""
            cv.edit_error = ""
            cv.message = ""
            return
        # 标量 → 子输入编辑
        cv.edit_mode = "json_input"
        cv.edit_json_action = "edit"
        cv.edit_value = _json_text(cur)
        cv.edit_error = ""

    def _json_append_start() -> None:
        """json 界面 a：进入子输入（追加新条目到当前容器）。"""
        cv.edit_mode = "json_input"
        cv.edit_json_action = "append"
        cv.edit_value = ""
        cv.edit_error = ""

    def _json_delete_selected() -> None:
        """json 界面 d：删除当前容器选中条目。"""
        container = _json_container()
        if container is None:
            cv.edit_error = "目标容器不可用"
            return
        idx = cv.edit_json_selected
        if isinstance(container, list):
            if 0 <= idx < len(container):
                container.pop(idx)
                cv.edit_json_selected = max(0, min(cv.edit_json_selected, len(container) - 1))
                cv.edit_error = ""
                cv.message = f"已删除 [{idx}]（Esc 保存）"
        elif isinstance(container, dict):
            keys = list(container.keys())
            if 0 <= idx < len(keys):
                k = keys[idx]
                del container[k]
                cv.edit_json_keys = list(container.keys())
                cv.edit_json_selected = max(0, min(cv.edit_json_selected, len(cv.edit_json_keys) - 1))
                cv.edit_error = ""
                cv.message = f"已删除 {k}（Esc 保存）"

    def _json_commit_input() -> None:
        """json 子输入确认：按 edit_json_action 更新/追加当前容器后返回 json 界面。"""
        container = _json_container()
        if container is None:
            cv.edit_error = "目标容器不可用"
            return
        text = cv.edit_value
        if cv.edit_json_action == "edit":
            idx = cv.edit_json_selected
            if isinstance(container, list):
                if not (0 <= idx < len(container)):
                    cv.edit_error = "选中条目已不存在"
                    return
                container[idx] = _parse_json_element(text)
            elif isinstance(container, dict):
                keys = list(container.keys())
                if not (0 <= idx < len(keys)):
                    cv.edit_error = "选中条目已不存在"
                    return
                container[keys[idx]] = _parse_json_element(text)
            else:
                cv.edit_error = "目标容器不可用"
                return
            cv.edit_mode = "json"
            cv.edit_error = ""
        else:  # append
            if isinstance(container, list):
                container.append(_parse_json_element(text))
                cv.edit_json_selected = len(container) - 1
            elif isinstance(container, dict):
                kv = _parse_key_value(text)
                if kv is None:
                    cv.edit_error = "格式: key=value 或 key: value"
                    return
                k, v = kv
                container[k] = v
                cv.edit_json_keys = list(container.keys())
                cv.edit_json_selected = max(0, len(cv.edit_json_keys) - 1)
            else:
                cv.edit_error = "目标容器不可用"
                return
            cv.edit_mode = "json"
            cv.edit_error = ""
        cv.message = "子 JSON 已修改（Esc 保存写回）"

    def _commit_json() -> None:
        """json 界面顶层退出（Esc）：一次性写回 edit_json_data 到配置。"""
        entry = _entry_by_key(entries, cv.edit_key)
        if entry is None:
            _cancel_edit(cv)
            return
        try:
            from src.config.loader import update_config
            update_config(entry["key"], cv.edit_json_data)
        except Exception as exc:
            cv.edit_error = f"写入失败: {exc}"
            return
        entry["value"] = cv.edit_json_data
        entry["value_text"] = format_config_value(
            cv.edit_json_data, entry.get("type", dict),
        )
        cv.editing = False
        cv.edit_mode = "input"
        cv.edit_error = ""
        cv.message = f"已更新 {entry['path']} = {entry['value_text']}"

    def _handle(event) -> bool:
        if not visible or cv is None:
            return False
        # ── 编辑模式 ──
        if cv.editing:
            if cv.edit_mode == "select":
                # 选择界面：Enter 确认 / Esc 取消；导航键放行候选 ListView
                if event.kind == "enter":
                    _commit_select()
                    return True
                if event.kind == "escape":
                    _cancel_edit(cv)
                    return True
                return False
            if cv.edit_mode == "json":
                # 子 JSON 界面：Enter 编辑（嵌套递归进入）/ a 追加 / d 删除 /
                # Esc 逐级返回（顶层才保存写回）；导航键放行条目 ListView
                if event.kind == "enter":
                    _json_edit_selected()
                    return True
                if event.kind == "escape":
                    if getattr(cv, "edit_json_path", None):
                        # 递归返回上层容器（修改保留，不写回）
                        cv.edit_json_path = list(cv.edit_json_path)[:-1]
                        parent = _json_container()
                        cv.edit_json_keys = (
                            list(parent.keys()) if isinstance(parent, dict) else []
                        )
                        cv.edit_json_selected = 0
                        cv.edit_error = ""
                        return True
                    _commit_json()
                    return True
                if event.kind == "char" and getattr(event, "char", "") in ("a", "A"):
                    _json_append_start()
                    return True
                if event.kind == "char" and getattr(event, "char", "") in ("d", "D"):
                    _json_delete_selected()
                    return True
                if event.kind == "delete":
                    _json_delete_selected()
                    return True
                return False
            if cv.edit_mode == "json_input":
                # 子输入界面：字符累积 / 退格 / Enter 确认 / Esc 取消
                if event.kind == "escape":
                    cv.edit_mode = "json"
                    cv.edit_error = ""
                    return True
                if event.kind == "char":
                    ch = getattr(event, "char", "") or ""
                    if ch and "\n" not in ch and "\r" not in ch:
                        if len(cv.edit_value) < _EDIT_VALUE_MAX:
                            cv.edit_value += ch
                    return True
                if event.kind == "backspace":
                    if cv.edit_value:
                        cv.edit_value = cv.edit_value[:-1]
                    return True
                if event.kind == "enter":
                    _json_commit_input()
                    return True
                return True
            # 输入界面：字符累积 / 退格 / Enter 确认 / Esc 取消
            if event.kind == "escape":
                _cancel_edit(cv)
                return True
            if event.kind == "char":
                ch = getattr(event, "char", "") or ""
                if ch and "\n" not in ch and "\r" not in ch:
                    if len(cv.edit_value) < _EDIT_VALUE_MAX:
                        cv.edit_value += ch
                return True
            if event.kind == "backspace":
                if cv.edit_value:
                    cv.edit_value = cv.edit_value[:-1]
                return True
            if event.kind == "enter":
                _commit_input()
                return True
            # 未识别按键吞掉（模态——不落入输入缓冲）
            return True
        # ── 浏览模式：Esc / Ctrl+H 关闭视图 ──
        if event.kind == "escape":
            cv.try_set_final("cancel")
            return True
        if event.kind == "ctrl_key" and getattr(event, "char", "") == "\x08":
            cv.try_set_final("cancel")
            return True
        # ── Enter 编辑选中项（ListView 不传 onSelect → enter 放行到本处） ──
        if event.kind == "enter" and total > 0:
            try:
                idx = max(0, min(int(cv.selected), total - 1))
            except (TypeError, ValueError):
                idx = 0
            _start_edit(cv, entries[idx])
            return True
        # 其余按键放行（ListView 导航）
        return False

    use_input(_handle, visible)

    def _handle_paste(text: str) -> bool:
        """编辑输入界面粘贴追加（input / json_input；单行化）。"""
        if not visible or cv is None:
            return False
        if not cv.editing or cv.edit_mode not in ("input", "json_input"):
            return False
        paste = (text or "").replace("\r", "").replace("\n", "")
        if not paste:
            return True
        remaining = _EDIT_VALUE_MAX - len(cv.edit_value)
        if remaining > 0:
            cv.edit_value += paste[:remaining]
        return True

    usePaste(_handle_paste, {"isActive": bool(visible and cv and cv.editing)})
    # ★ 模态全屏视图声明（2026-08-17 通用机制）：visible 期间未消费按键被
    #   input router 吞掉（不落入输入缓冲）——字符/Enter 不误编辑/误提交；
    #   关闭后（visible=False）hook 不激活零影响，输入区恢复正常输入。
    # ★ P3（review 2026-08-20）：组件置 done → 命令线程 50ms 轮询清理之间
    #   存在≤1 帧窗口——use_fullscreen(False) 释放输入接管但 model.fullscreen
    #   仍为 "config"，App 继续渲染本组件（visible=False 返回空 TEXT），此间
    #   按键进入 input router 落入输入缓冲。与 user_select/editmsg 同构的
    #   已知窗口（渲染循环固有），命令线程轮询间隙极短，风险可接受。
    use_fullscreen(visible)

    if not visible:
        return h(TEXT, {"children": ""})

    # ── 选中钳制 ──
    try:
        selected = max(0, min(int(cv.selected), total - 1)) if total else 0
    except (TypeError, ValueError):
        selected = 0
    if selected != cv.selected:
        cv.selected = selected
    # 选择界面高亮钳制
    pick_total = len(cv.edit_options or [])
    try:
        pick_sel = max(0, min(int(cv.edit_selected), pick_total - 1)) if pick_total else 0
    except (TypeError, ValueError):
        pick_sel = 0
    if pick_sel != cv.edit_selected:
        cv.edit_selected = pick_sel
    # json 界面条目高亮钳制（基于递归路径导航到的当前容器）
    json_container = _json_container()
    json_is_dict = isinstance(json_container, dict)
    json_keys = list(cv.edit_json_keys or []) if json_is_dict else []
    json_item_count = (
        len(json_keys) if json_is_dict else len(json_container or [])
    )
    json_path_text = _json_path_text()
    try:
        json_sel = max(0, min(int(cv.edit_json_selected), json_item_count - 1)) if json_item_count else 0
    except (TypeError, ValueError):
        json_sel = 0
    if json_sel != cv.edit_json_selected:
        cv.edit_json_selected = json_sel

    # ── 栏宽分配（键列 / 值列 / 说明列） ──
    if width > 0 and total:
        key_w = min(30, max((wcswidth_simple(str(e["path"])) for e in entries), default=8) + 2)
        val_w = min(44, max((wcswidth_simple(str(e["value_text"])) for e in entries), default=10) + 2)
        desc_w = max(8, width - key_w - val_w - 6)
    else:
        key_w, val_w, desc_w = 22, 36, 12
    vh = _viewport_rows()

    # ── 配置列表行渲染（浏览模式） ──
    def _render_row(entry, i, is_sel):
        prefix = "\u25b6 " if is_sel else "  "
        runs = [StyledRun(prefix, _S_SEL_MARK if is_sel else None)]
        key = _truncate_width(str(entry["path"]), key_w)
        runs.append(StyledRun(key, _S_KEY))
        pad1 = max(0, key_w - wcswidth_simple(key) + 1)
        if pad1:
            runs.append(StyledRun(" " * pad1, None))
        val_txt = _truncate_width(str(entry["value_text"]), val_w)
        runs.append(StyledRun(val_txt, _value_style(entry)))
        pad2 = max(0, val_w - wcswidth_simple(val_txt) + 1)
        if pad2:
            runs.append(StyledRun(" " * pad2, None))
        desc = _truncate_width(str(entry.get("desc") or ""), desc_w)
        runs.append(StyledRun(desc, _S_DESC))
        if width > 0:
            runs = truncate_runs(runs, width)
        if is_sel:
            runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"cv-{i}"})

    def _on_navigate(idx: int) -> None:
        cv.selected = int(idx)

    # ── 候选选项行渲染（选择界面） ──
    pick_descs = list(cv.edit_options_desc or [])

    def _render_pick(opt, i, is_sel):
        prefix = "\u25b6 " if is_sel else "  "
        runs = [StyledRun(prefix, _S_SEL_MARK if is_sel else None)]
        val_txt = _truncate_width(str(opt), max(20, val_w + 8))
        runs.append(StyledRun(val_txt, _S_VAL))
        desc = pick_descs[i] if i < len(pick_descs) else ""
        if desc and width > 0:
            used = sum(getattr(r, "width", 1) for r in runs) + 2
            # ★ P3（review 2026-08-22）：desc_budget 下限 8 在极窄终端
            #   （width < used）溢出——改为 1（行本体已超宽时不强行 8 列）。
            desc_budget = max(1, width - used)
            desc_txt = _truncate_width(str(desc), desc_budget)
            runs.append(StyledRun(" " * 2 + desc_txt, _S_DESC))
        if width > 0:
            runs = truncate_runs(runs, width)
        if is_sel:
            runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"cv-pick-{i}"})

    def _on_pick_navigate(idx: int) -> None:
        cv.edit_selected = int(idx)

    # ── 子 JSON 条目行渲染（json 界面；当前递归容器） ──
    def _render_json_item(idx, i, is_sel):
        prefix = "\u25b6 " if is_sel else "  "
        if json_is_dict:
            k = json_keys[i] if i < len(json_keys) else ""
            v = json_container.get(k) if isinstance(json_container, dict) else None
            key_txt = _truncate_width(str(k), max(18, key_w))
            val_txt = _truncate_width(_json_text(v), max(16, val_w))
            runs = [
                StyledRun(prefix, _S_SEL_MARK if is_sel else None),
                StyledRun(key_txt, _S_KEY),
                StyledRun(" = ", _S_HINT),
                StyledRun(val_txt, _json_value_style(v)),
            ]
        else:
            v = json_container[i] if isinstance(json_container, list) and i < len(json_container) else None
            idx_txt = _truncate_width(f"[{i}]", max(6, key_w))
            val_txt = _truncate_width(_json_text(v), max(16, val_w))
            runs = [
                StyledRun(prefix, _S_SEL_MARK if is_sel else None),
                StyledRun(idx_txt, _S_KEY),
                StyledRun("  ", None),
                StyledRun(val_txt, _json_value_style(v)),
            ]
        if width > 0:
            runs = truncate_runs(runs, width)
        if is_sel:
            runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"cv-json-{i}"})

    def _json_items():
        """json 界面 ListView items（与当前容器条目索引一一对应）。"""
        if json_is_dict:
            return list(json_keys)
        return list(range(len(json_container or [])))

    def _on_json_navigate(idx: int) -> None:
        cv.edit_json_selected = int(idx)

    # ── 头部（标题 + 统计 + 提示；行尾 ─ 分隔线填充至满宽） ──
    if pick_mode:
        entry = _entry_by_key(entries, cv.edit_key)
        pick_path = entry["path"] if entry else cv.edit_key
        header_hint = f"选择 {pick_path}（\u2191\u2193/jk 选择 \u00b7 Enter 确认 \u00b7 Esc 取消）"
    elif json_mode:
        entry = _entry_by_key(entries, cv.edit_key)
        json_path = entry["path"] if entry else cv.edit_key
        if json_path_text:
            json_path = f"{json_path}.{json_path_text}"
        header_hint = f"子 JSON \u00b7 {json_path}（\u2191\u2193/jk 选择 \u00b7 Enter 编辑 \u00b7 a 追加 \u00b7 d 删除 \u00b7 Esc 返回/保存）"
    elif json_input_mode:
        header_hint = "子输入"
    elif editing:
        header_hint = "编辑"
    else:
        header_hint = "\u2191\u2193/jk 选择 \u00b7 Enter 编辑 \u00b7 Esc 关闭"
    header_runs = [
        StyledRun("\u258d\u2699 配置中心", _S_TITLE),
        StyledRun(f" \u00b7 {total} 项", _S_HINT),
        StyledRun(f"  {header_hint}", _S_HINT),
    ]
    if width > 0:
        header_runs = truncate_runs(header_runs, width)
        used = sum(getattr(r, "width", 1) for r in header_runs)
        pad = width - used
        if pad > 0:
            header_runs.append(StyledRun("\u2500" * pad, _S_SEP_ROW))

    # ── 主列表区 ──
    # ★ P2（review 2026-08-20）：修复前 ``vh - (1 if editing else 1)`` 恒等于
    #   ``vh - 1``（冗余条件表达式）——编辑模式与浏览模式底部行同为 1 行。
    list_h = max(4, vh - 1)
    if pick_mode:
        ledger = h(ListView, {
            "items": list(cv.edit_options or []),
            "height": list_h,
            "width": width if width > 0 else None,
            "cursor": pick_sel if pick_total else 0,
            "renderItem": _render_pick,
            "onNavigate": _on_pick_navigate,
            "focus": visible and pick_mode,
        })
    elif json_mode:
        ledger = h(ListView, {
            "items": _json_items(),
            "height": list_h,
            "width": width if width > 0 else None,
            "cursor": json_sel if json_item_count else 0,
            "renderItem": _render_json_item,
            "onNavigate": _on_json_navigate,
            "focus": visible and json_mode,
        })
    else:
        ledger = h(ListView, {
            "items": entries,
            "height": list_h,
            "width": width if width > 0 else None,
            "cursor": selected if total else 0,
            "renderItem": _render_row,
            "onNavigate": _on_navigate,
            "focus": visible and not editing,
        })

    # ── 底部行 ──
    bottom_rows: list = []
    if pick_mode:
        bottom_rows.append(h(TEXT, {
            "children": "  \u2191\u2193/jk 选择 \u00b7 g/G 首末 \u00b7 PgUp/PgDn 翻页 \u00b7 Enter 确认 \u00b7 Esc 取消",
            "style": _S_HINT,
            "textWrap": "truncate-end", "height": 1, "key": "cv-pick-hint",
        }))
    elif json_mode:
        hint = "  \u2191\u2193/jk 选择 \u00b7 Enter 编辑（嵌套进入）\u00b7 a 追加 \u00b7 d 删除 \u00b7 Esc 返回/保存"
        if cv.message:
            hint = f"  \u2713 {cv.message}"
        bottom_rows.append(h(TEXT, {
            "children": hint, "style": _S_OK if cv.message else _S_HINT,
            "textWrap": "truncate-end", "height": 1, "key": "cv-json-hint",
        }))
        if cv.edit_error:
            err_disp = f"  \u2716 {cv.edit_error}"
            if width > 0:
                err_disp = _truncate_width(err_disp, width)
            bottom_rows.append(h(TEXT, {
                "children": err_disp, "style": _S_ERR,
                "textWrap": "truncate-end", "height": 1, "key": "cv-json-err",
            }))
    elif json_input_mode:
        entry = _entry_by_key(entries, cv.edit_key)
        path = entry["path"] if entry else cv.edit_key
        if json_path_text:
            path = f"{path}.{json_path_text}"
        if json_is_dict and cv.edit_json_action == "append":
            prompt = f"  \u258d \u270e {path} 追加 key=value: {cv.edit_value}\u258f"
        elif isinstance(json_container, list) and cv.edit_json_action == "append":
            prompt = f"  \u258d \u270e {path} 追加元素: {cv.edit_value}\u258f"
        elif json_is_dict:
            k = json_keys[cv.edit_json_selected] if cv.edit_json_selected < len(json_keys) else ""
            prompt = f"  \u258d \u270e {path}.{k} = {cv.edit_value}\u258f"
        else:
            prompt = f"  \u258d \u270e {path}[{cv.edit_json_selected}] = {cv.edit_value}\u258f"
        if width > 0:
            prompt = _truncate_width(prompt, width)
        bottom_rows.append(h(TEXT, {
            "children": prompt, "style": _S_EDIT,
            "textWrap": "truncate-end", "height": 1, "key": "cv-json-edit",
        }))
        if cv.edit_error:
            err_disp = f"  \u2716 {cv.edit_error}"
            if width > 0:
                err_disp = _truncate_width(err_disp, width)
            bottom_rows.append(h(TEXT, {
                "children": err_disp, "style": _S_ERR,
                "textWrap": "truncate-end", "height": 1, "key": "cv-json-edit-err",
            }))
        else:
            bottom_rows.append(h(TEXT, {
                "children": "  Enter 保存 \u00b7 Esc 取消",
                "style": _S_HINT, "height": 1, "key": "cv-json-edit-hint",
            }))
    elif editing:
        entry = _entry_by_key(entries, cv.edit_key)
        path = entry["path"] if entry else cv.edit_key
        edit_disp = f"  \u258d \u270e 编辑 {path} = {cv.edit_value}\u258f"
        if width > 0:
            edit_disp = _truncate_width(edit_disp, width)
        bottom_rows.append(h(TEXT, {
            "children": edit_disp, "style": _S_EDIT,
            "textWrap": "truncate-end", "height": 1, "key": "cv-edit",
        }))
        if cv.edit_error:
            err_disp = f"  \u2716 {cv.edit_error}"
            if width > 0:
                err_disp = _truncate_width(err_disp, width)
            bottom_rows.append(h(TEXT, {
                "children": err_disp, "style": _S_ERR,
                "textWrap": "truncate-end", "height": 1, "key": "cv-err",
            }))
        else:
            bottom_rows.append(h(TEXT, {
                "children": "  Enter 保存 \u00b7 Esc 取消",
                "style": _S_HINT, "height": 1, "key": "cv-edit-hint",
            }))
    elif cv.message:
        msg_disp = f"  \u2713 {cv.message}"
        if width > 0:
            msg_disp = _truncate_width(msg_disp, width)
        bottom_rows.append(h(TEXT, {
            "children": msg_disp, "style": _S_OK,
            "textWrap": "truncate-end", "height": 1, "key": "cv-msg",
        }))
    else:
        bottom_rows.append(h(TEXT, {
            "children": "  \u2191\u2193/jk 选择 \u00b7 g/G 首末 \u00b7 PgUp/PgDn 翻页 \u00b7 Enter 编辑 \u00b7 Esc 关闭",
            "style": _S_HINT,
            "textWrap": "truncate-end", "height": 1, "key": "cv-hint",
        }))

    return h(Column, None, [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "cv-header"}),
        h(Row, None, [ledger]),
        h(Column, None, bottom_rows),
    ])


def _json_text(value) -> str:
    """子 JSON 值 → 显示文本（标量字符串化 / list/dict JSON 摘要）。"""
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)

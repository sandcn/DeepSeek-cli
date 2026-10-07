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
from src.tui.ink.hooks import use_input, usePaste, use_memo
from src.tui.ink.helpers import truncate_runs
from src.tui.ink.widgets.listview import ListView
from src.config.view_model import format_config_value, parse_config_value

from ._keymap_pane import keymap_panel_rows
from ._modal_view import empty_modal_frame, is_modal_close_key, use_modal_scope

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
# 增强（2026-10-07）：搜索高亮 / 状态行 / 帮助面板 / 来源提示
_S_SEARCH_BG = Style(bg=236)               # 搜索匹配行背景
_S_SEARCH_CUR_BG = Style(bg=25)            # 当前匹配行背景（亮蓝）
_S_STATUS = Style(fg=221)                  # 底部状态行
_S_SOURCE = Style(fg=108)                  # 配置来源路径（头部）
_S_HELP_KEY = Style(fg=214)                # 帮助面板键位（黄）
_S_HELP_GROUP = Style(fg=110, bold=True)   # 帮助面板分组标题
_S_HELP_DESC = Style(fg=252)               # 帮助面板说明
# 增强（2026-10-07 第三批）：分组头 / diff 预览 / 导入输入 / 撤销面板
_S_GROUP_HEAD = Style(fg=110, bold=True)   # 配置分组头（浅蓝加粗）
_S_GROUP_FOLD = Style(fg=108, bold=True)   # 折叠分组头
_S_DIFF = Style(fg=214, bold=True)         # diff 新值（黄加粗）
_S_IMPORT = Style(fg=45, bold=True)        # 导入路径输入行（亮青加粗）
_S_UNDO = Style(fg=221)                    # 撤销面板项（浅黄）

#: 编辑输入长度上限（渲染行按宽度截断，无上限累积只浪费内存）
_EDIT_VALUE_MAX = 400
#: 搜索输入长度上限
_SEARCH_QUERY_MAX = 200
#: 撤销栈深度上限
_UNDO_MAX = 20


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
    """取消当前编辑（退出编辑界面，不保存）。

    ★ P3（review）：委托 ``ConfigViewState.reset_edit_state()`` 集中复位
    （修复前为散点复位，``edit_json_action`` 残留 ``"append"``——下次进入
    JSON 编辑时沿用旧动作误判追加模式；``message`` 陈旧提示残留）。
    """
    cv.reset_edit_state()


# ═══════════════════════════════════════════════════════════
# 编辑提交 / 子 JSON 辅助（模块级；P1-1 巨型组件拆分）
# ═══════════════════════════════════════════════════════════
# 说明：以下逻辑原为 ``ConfigView`` 内部闭包（组件函数 ~680 行）。提取为
# 模块级函数后按显式参数传递上下文（``cv`` / ``entries``）——主组件只负责
# 「数据准备 + 渲染组装 + 事件接线」，职责分离、可独立测试与复用。


def _push_undo(cv, entry, old_value) -> None:
    """记录一次编辑前的值（``u`` 撤销数据源；有界栈）。

    写入 ``cv.undo_stack``（``[(key, 旧值, 旧显示文本, path)]``，最多
    ``_UNDO_MAX`` 条——超限丢弃最旧）。
    """
    try:
        stack = list(getattr(cv, "undo_stack", None) or [])
    except Exception:
        stack = []
    stack.append((
        entry.get("key", ""),
        copy.deepcopy(old_value),
        format_config_value(
            old_value, entry.get("type", str),
            sensitive=bool(entry.get("sensitive")),
        ),
        entry.get("path", entry.get("key", "")),
    ))
    cv.undo_stack = stack[-_UNDO_MAX:]


def _persist_value(cv, entry, value) -> bool:
    """类型校验 + update_config 持久化 + 刷新显示值；失败写 edit_error。"""
    parsed, err = parse_config_value(entry.get("type", str), str(value))
    if err:
        cv.edit_error = err
        return False
    # 敏感项（api_key）空输入确认不得清空已有密钥（数据丢失防御）
    if entry.get("sensitive") and (parsed is None or parsed == ""):
        cv.edit_error = "敏感项不能为空，请输入新值（Esc 取消）"
        return False
    old_value = copy.deepcopy(entry.get("value"))
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
    # ★ 2026-10-07（config 增强：撤销）：记录编辑前的值（``u`` 撤回）。
    _push_undo(cv, entry, old_value)
    return True


def _commit_select_edit(cv, entries) -> None:
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
    if _persist_value(cv, entry, opts[idx]):
        cv.editing = False
        cv.edit_mode = "input"
        cv.edit_error = ""
        cv.message = f"已更新 {entry['path']} = {entry['value_text']}"


def _commit_input_edit(cv, entries) -> None:
    """输入界面确认：文本缓冲类型校验 → 持久化 → 刷新显示值。"""
    if cv is None:
        return
    entry = _entry_by_key(entries, cv.edit_key)
    if entry is None:
        _cancel_edit(cv)
        return
    if _persist_value(cv, entry, cv.edit_value):
        cv.editing = False
        cv.edit_mode = "input"
        cv.edit_error = ""
        cv.message = f"已更新 {entry['path']} = {entry['value_text']}"


# ── 子 JSON 编辑界面辅助（支持递归嵌套层） ────────────


def _json_container(cv):
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


def _json_path_text(cv) -> str:
    """当前容器完整路径显示（breadcrumb；空=顶层）。"""
    segs = list(getattr(cv, "edit_json_path", None) or [])
    return ".".join(str(s) for s in segs) if segs else ""


def _json_edit_selected(cv) -> None:
    """json 界面 Enter：嵌套（list/dict 值）→ 递归进入下一层；
    标量 → 子输入编辑。"""
    container = _json_container(cv)
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


def _json_append_start(cv) -> None:
    """json 界面 a：进入子输入（追加新条目到当前容器）。"""
    cv.edit_mode = "json_input"
    cv.edit_json_action = "append"
    cv.edit_value = ""
    cv.edit_error = ""


def _json_delete_selected(cv) -> None:
    """json 界面 d：删除当前容器选中条目。"""
    container = _json_container(cv)
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


def _json_commit_input(cv) -> None:
    """json 子输入确认：按 edit_json_action 更新/追加当前容器后返回 json 界面。"""
    container = _json_container(cv)
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


def _json_escape_up(cv) -> None:
    """json 界面 Esc 的「递归返回上层容器」分支（修改保留，不写回）。"""
    cv.edit_json_path = list(cv.edit_json_path)[:-1]
    parent = _json_container(cv)
    cv.edit_json_keys = (
        list(parent.keys()) if isinstance(parent, dict) else []
    )
    cv.edit_json_selected = 0
    cv.edit_error = ""


def _commit_json_edit(cv, entries) -> None:
    """json 界面顶层退出（Esc）：一次性写回 edit_json_data 到配置。"""
    entry = _entry_by_key(entries, cv.edit_key)
    if entry is None:
        _cancel_edit(cv)
        return
    old_value = copy.deepcopy(entry.get("value"))
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
    # ★ 2026-10-07（config 增强：撤销）：记录编辑前的值（``u`` 撤回）。
    _push_undo(cv, entry, old_value)
    cv.editing = False
    cv.edit_mode = "input"
    cv.edit_error = ""
    cv.message = f"已更新 {entry['path']} = {entry['value_text']}"


# ── 增强辅助（2026-10-07：搜索 / 帮助 / 恢复默认 / 撤销 / 复制） ──────
# 说明：以下逻辑与 ``_handle_config_event`` / ConfigView 组件共享（模块级
# 纯辅助——便于单测与复用）。


def _config_entry_search_text(entry) -> str:
    """配置项搜索文本（路径 / 键名 / 说明 / 显示值 / 默认值）。"""
    if not isinstance(entry, dict):
        return ""
    parts = [
        str(entry.get("path", "") or ""),
        str(entry.get("key", "") or ""),
        str(entry.get("desc", "") or ""),
        str(entry.get("value_text", "") or ""),
        str(entry.get("default_text", "") or ""),
    ]
    return "\n".join(parts)


def _config_search_matches(entries: list, pattern: str) -> list:
    """搜索匹配的配置项索引列表（子串匹配，忽略大小写；空模式 → 空列表）。"""
    if not pattern:
        return []
    low = str(pattern).lower()
    out: list = []
    for i, entry in enumerate(entries or []):
        if low in _config_entry_search_text(entry).lower():
            out.append(i)
    return out


def _help_rows(right_w: int) -> list:
    """帮助面板内容行（配置中心键位速查）。"""
    from src.presentation_data import config_keymap

    return keymap_panel_rows(
        config_keymap(), right_w,
        key_style=_S_HELP_KEY, group_style=_S_HELP_GROUP,
        desc_style=_S_HELP_DESC, sep_style=_S_SEP_ROW,
        empty_text="(\u5feb\u6377\u952e\u901f\u67e5\u8868\u672a\u6ce8\u518c)",
    )


def _cycle_config_filter(cv) -> None:
    """``f``：切换过滤模式（列表只显示搜索匹配配置项）。"""
    new_value = not bool(getattr(cv, "search_filter", False))
    pattern = getattr(cv, "search_pattern", "") or ""
    matches = list(getattr(cv, "search_matches", None) or [])
    if new_value and not (pattern and matches):
        cv.search_filter = False
        cv.message = "过滤需先搜索且有匹配（/ 搜索）"
        return
    cv.search_filter = new_value
    cv.message = (
        f"过滤开启：仅显示 {len(matches)} 项匹配"
        if new_value else "过滤关闭：显示全部配置项"
    )


def _undo_last(cv, entries) -> None:
    """``u``：撤销最近一次编辑（写回旧值）。"""
    stack = list(getattr(cv, "undo_stack", None) or [])
    if not stack:
        cv.edit_error = ""
        cv.message = "无可撤销的编辑"
        return
    key, old_value, old_text, path = stack.pop()
    cv.undo_stack = stack
    entry = _entry_by_key(entries, key)
    if entry is None:
        cv.message = "无可撤销的编辑"
        return
    try:
        from src.config.loader import update_config
        update_config(key, old_value)
    except Exception as exc:
        cv.edit_error = f"撤销写入失败: {exc}"
        return
    entry["value"] = old_value
    entry["value_text"] = old_text
    cv.edit_error = ""
    cv.message = f"已撤销 {path} = {old_text}"


def _reset_entry_default(cv, entries, idx: int) -> None:
    """``r``：把选中配置项恢复为默认值（持久化 + 可撤销）。"""
    if not (0 <= int(idx) < len(entries)):
        cv.message = "无可恢复的配置项"
        return
    entry = entries[int(idx)]
    from src.config.defaults import CONFIG_KEYS, DEFAULTS

    key = entry.get("key", "")
    if key in CONFIG_KEYS:
        default = CONFIG_KEYS[key]["default"]
        typ = CONFIG_KEYS[key]["type"]
    else:
        default = DEFAULTS.get(key)
        typ = entry.get("type", str)
    old_value = copy.deepcopy(entry.get("value"))
    try:
        from src.config.loader import update_config
        update_config(key, default)
    except Exception as exc:
        cv.edit_error = f"写入失败: {exc}"
        return
    _push_undo(cv, entry, old_value)
    entry["value"] = default
    entry["value_text"] = format_config_value(
        default, typ, sensitive=bool(entry.get("sensitive")),
    )
    cv.edit_error = ""
    cv.message = f"已恢复默认 {entry.get('path', key)} = {entry['value_text']}"


def _copy_entry(cv, entry) -> None:
    """``y``：复制选中配置项 ``path = value`` 到剪贴板（OSC52）。"""
    if entry is None:
        cv.message = "无可复制的配置项"
        return
    text = f"{entry.get('path', entry.get('key', ''))} = {entry.get('value_text', '')}"
    from src.tui._screen import set_clipboard
    if set_clipboard(text):
        cv.message = f"已复制 {text}（{len(text)} 字符）"
    else:
        cv.edit_error = ""
        cv.message = "复制失败：无可用终端输出"


# ═══════════════════════════════════════════════════════════
# 增强辅助（2026-10-07 第三批：分组折叠 / diff 预览 / 导出导入 / 撤销面板）
# ═══════════════════════════════════════════════════════════

#: 无前缀分组的显示名（顶层配置项）
_TOP_GROUP = "\uff08\u9876\u5c42\uff09"  # （顶层）


class _ConfigGroupRow:
    """配置分组头行（不可选——ListView isSelectable 排除）。

    ``group`` 分组名、``count`` 组内条目数、``collapsed`` 是否折叠。
    """

    __slots__ = ("group", "count", "collapsed")

    def __init__(self, group: str, count: int = 0, collapsed: bool = False) -> None:
        self.group = str(group)
        self.count = int(count)
        self.collapsed = bool(collapsed)


def _group_of(entry) -> str:
    """配置项所属分组（``path`` 首段；无 ``.`` → 顶层）。"""
    if not isinstance(entry, dict):
        return _TOP_GROUP
    path = str(entry.get("path") or entry.get("key") or "")
    head = path.split(".", 1)[0]
    return head if head else _TOP_GROUP


def _config_rows(view_entries: list, collapsed_groups) -> tuple:
    """过滤视图条目 → (display_items, row_to_entry, entry_to_row)。

    ``display_items`` 为分组头（``_ConfigGroupRow``）+ 未折叠分组内的条目
    （分组标题不可选）；``row_to_entry`` 行下标 → 视图条目索引（分组头 -1）；
    ``entry_to_row`` 视图条目索引 → 行下标（折叠分组内条目不在表中）。
    """
    collapsed = set(collapsed_groups or ())
    groups: list = []
    by_group: dict = {}
    for i, e in enumerate(view_entries or []):
        g = _group_of(e)
        if g not in by_group:
            groups.append(g)
            by_group[g] = []
        by_group[g].append(i)
    display_items: list = []
    row_to_entry: list = []
    entry_to_row: dict = {}
    for g in groups:
        members = by_group[g]
        display_items.append(_ConfigGroupRow(g, len(members), g in collapsed))
        row_to_entry.append(-1)
        if g in collapsed:
            continue
        for i in members:
            display_items.append(view_entries[i])
            row_to_entry.append(i)
            entry_to_row[i] = len(display_items) - 1
    return display_items, row_to_entry, entry_to_row


def _is_config_selectable(item) -> bool:
    """配置列表可选性：分组头（``_ConfigGroupRow``）不可选（导航跳过）。"""
    return not isinstance(item, _ConfigGroupRow)


def _group_header_runs(row: "_ConfigGroupRow", width: int) -> list:
    """分组头行 runs（``▼ 分组 (N)`` / ``▶ 分组 (N, 已折叠)``）。"""
    if row.collapsed:
        runs = [
            StyledRun("\u25b6 ", _S_GROUP_FOLD),
            StyledRun(str(row.group), _S_GROUP_FOLD),
            StyledRun(f" ({row.count}, \u5df2\u6298\u53e0)", _S_HINT),
        ]
    else:
        runs = [
            StyledRun("\u25bc ", _S_GROUP_HEAD),
            StyledRun(str(row.group), _S_GROUP_HEAD),
            StyledRun(f" ({row.count})", _S_HINT),
        ]
    if width > 0:
        runs = truncate_runs(runs, width)
    return runs


def _toggle_group_collapse(cv, view_entries: list, sel_idx: int,
                           mode: str = "toggle") -> None:
    """``za``/``zc``/``zo``：折叠 / 展开选中项所在分组。"""
    if not (0 <= sel_idx < len(view_entries or [])):
        cv.message = "无可折叠的分组"
        return
    g = _group_of(view_entries[sel_idx])
    collapsed = set(getattr(cv, "collapsed_groups", None) or ())
    if mode == "close":
        new_state = True
    elif mode == "open":
        new_state = False
    else:
        new_state = g not in collapsed
    if not new_state:
        collapsed.discard(g)
        cv.collapsed_groups = collapsed
        cv.message = f"已展开分组 {g}"
        return
    collapsed.add(g)
    cv.collapsed_groups = collapsed
    # 选中条目随折叠隐藏 → 移到最近的其他分组条目
    new_sel = None
    for i in range(sel_idx - 1, -1, -1):
        if _group_of(view_entries[i]) != g:
            new_sel = i
            break
    if new_sel is None:
        for i in range(sel_idx + 1, len(view_entries)):
            if _group_of(view_entries[i]) != g:
                new_sel = i
                break
    if new_sel is not None:
        cv.selected = new_sel
        cv.cursor = 0
        cv.scroll = 0
    cv.message = f"已折叠分组 {g}（zo 展开）"


def _collapse_all_groups(cv, view_entries: list, fold: bool) -> None:
    """``zC``/``zO``：折叠 / 展开全部分组。"""
    if not fold:
        cv.collapsed_groups = set()
        cv.message = "已展开全部分组"
        return
    groups = {_group_of(e) for e in (view_entries or [])}
    if not groups:
        cv.message = "无可折叠的分组"
        return
    cv.collapsed_groups = groups
    cv.message = f"已折叠全部分组（{len(groups)} 个，zO 展开）"


def _jump_group(cv, view_entries: list, sel_idx: int, delta: int) -> None:
    """``[``/``]``：跳到上 / 下一个分组首条。"""
    n = len(view_entries or [])
    if n == 0:
        cv.message = "无分组可跳转"
        return
    cur_g = _group_of(view_entries[sel_idx]) if 0 <= sel_idx < n else ""
    target_g = None
    if delta > 0:
        for i in range(sel_idx + 1, n):
            if _group_of(view_entries[i]) != cur_g:
                target_g = _group_of(view_entries[i])
                break
    else:
        for i in range(sel_idx - 1, -1, -1):
            if _group_of(view_entries[i]) != cur_g:
                target_g = _group_of(view_entries[i])
                break
    if target_g is None:
        cv.message = "无下一个分组" if delta > 0 else "无上一个分组"
        return
    for i in range(n):
        if _group_of(view_entries[i]) == target_g:
            cv.selected = i
            cv.cursor = 0
            cv.scroll = 0
            cv.message = f"\u2192 分组 {target_g}"
            return


def _diff_runs(old_text, new_text) -> list:
    """编辑 diff 预览 runs（``旧: O → 新: N``；不同值高亮）。"""
    old_s = str(old_text if old_text is not None else "")
    new_s = str(new_text if new_text is not None else "")
    same = old_s == new_s
    return [
        StyledRun("  \u65e7: ", _S_HINT),
        StyledRun(old_s or "(\u7a7a)", _S_HINT),
        StyledRun("  \u2192  ", _S_HINT),
        StyledRun("\u65b0: ", _S_HINT),
        StyledRun(new_s or "(\u7a7a)", _S_HINT if same else _S_DIFF),
    ]


def _undo_entries(cv) -> list:
    """撤销栈 → 显示行（最新在前；``[(path, old_text, old_value, key)]``）。"""
    stack = list(getattr(cv, "undo_stack", None) or [])
    out: list = []
    for key, old_value, old_text, path in reversed(stack):
        out.append({
            "key": key, "path": path, "old_text": old_text, "old_value": old_value,
        })
    return out


def _undo_to(cv, entries, display_idx: int) -> None:
    """撤销历史面板 Enter：回退到该历史点（撤销该条及其后所有）。"""
    stack = list(getattr(cv, "undo_stack", None) or [])
    if not stack:
        cv.message = "无可撤销的编辑"
        return
    try:
        idx = int(display_idx)
    except (TypeError, ValueError):
        idx = 0
    target = len(stack) - 1 - idx  # 显示倒序 → 栈内下标
    if not (0 <= target < len(stack)):
        cv.message = "无可撤销的编辑"
        return
    from src.config.loader import update_config
    done = 0
    for key, old_value, old_text, _path in reversed(stack[target:]):
        try:
            update_config(key, old_value)
        except Exception as exc:
            cv.edit_error = f"撤销失败: {exc}"
            return
        entry = _entry_by_key(entries, key)
        if entry is not None:
            entry["value"] = old_value
            entry["value_text"] = old_text
        done += 1
    cv.undo_stack = stack[:target]
    cv.editing = False
    cv.edit_mode = "input"
    cv.edit_error = ""
    cv.message = f"已回退 {done} 步"


def _do_export(cv, entries) -> None:
    """``e``：导出当前配置为 JSON 文件。"""
    from .config_export import write_export
    items = [e for e in (entries or []) if isinstance(e, dict)]
    if not items:
        cv.message = "无可导出的配置项"
        return
    try:
        path = write_export(items)
    except Exception as exc:
        cv.edit_error = f"导出失败: {exc}"
        return
    cv.edit_error = ""
    cv.message = f"已导出 {len(items)} 项配置 → {path}"


def _start_import(cv) -> None:
    """``i``：进入导入路径输入模式。"""
    cv.editing = True
    cv.edit_mode = "import"
    cv.edit_key = ""
    cv.edit_value = ""
    cv.edit_error = ""
    cv.message = ""


def _commit_import(cv, entries) -> None:
    """导入输入确认：读取 JSON → 解析 → 逐项写回。"""
    path = (cv.edit_value or "").strip()
    if not path:
        cv.edit_error = "请输入 JSON 文件路径"
        return
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except Exception as exc:
        cv.edit_error = f"读取失败: {exc}"
        return
    from .config_export import apply_import, parse_import
    mapping, err = parse_import(text)
    if err:
        cv.edit_error = err
        return
    if not mapping:
        cv.edit_error = "配置为空"
        return
    applied, errors = apply_import(mapping)
    cv.editing = False
    cv.edit_mode = "input"
    # 刷新条目显示值（重建 entries）
    if applied:
        try:
            from src.config.view_model import build_config_entries
            cv.entries = build_config_entries()
        except Exception:
            pass
    cv.message = f"已导入 {applied} 项" + (
        f"（{len(errors)} 项失败）" if errors else ""
    )
    cv.edit_error = "; ".join(errors[:3]) if errors else ""


def _config_status_text(cv, filter_active: bool, match_count: int) -> str:
    """底部状态行文本（搜索匹配计数 + 操作反馈；空串不渲染）。

    Args:
        cv: ConfigViewState。
        filter_active: 是否处于过滤模式（追加 ``[过滤]`` 标注）。
        match_count: 当前匹配总数。
    """
    if cv is None:
        return ""
    parts: list = []
    pattern = getattr(cv, "search_pattern", "") or ""
    if pattern:
        n = int(match_count or 0)
        idx = getattr(cv, "search_idx", -1)
        cur = (idx + 1) if 0 <= idx < n else 0
        seg = f"/{pattern}  {cur}/{n}"
        if filter_active:
            seg += " [\u8fc7\u6ee4]"
        parts.append(seg)
    err = getattr(cv, "edit_error", "") or ""
    msg = getattr(cv, "message", "") or ""
    if err:
        parts.append(f"\u2716 {err}")
    elif msg:
        parts.append(f"\u2713 {msg}")
    return "  \u00b7  ".join(parts)


def _sel_index(cv, total: int) -> int:
    """当前选中索引（钳制到 ``[0, total-1]``；total<=0 → -1）。"""
    if total <= 0:
        return -1
    try:
        return max(0, min(int(getattr(cv, "selected", 0) or 0), total - 1))
    except (TypeError, ValueError):
        return 0


def _exec_config_search(cv, entries) -> None:
    """回车执行配置项搜索（匹配基于全量列表；定位到首项）。"""
    q = getattr(cv, "search_query", "") or ""
    cv.search_mode = False
    cv.search_pattern = q
    matches = _config_search_matches(entries, q)
    cv.search_matches = matches
    if matches:
        cv.search_idx = 0
        cv.selected = 0
        cv.message = f"{len(matches)} \u9879\u5339\u914d"
    else:
        cv.search_idx = -1
        cv.message = f"\u65e0\u5339\u914d\uff1a{q}" if q else ""


def _jump_config_match(cv, delta: int, view_map: dict | None = None) -> None:
    """``n``/``N``/``p``：在匹配项间环绕跳转（过滤模式下换算视图索引）。"""
    matches = list(getattr(cv, "search_matches", None) or [])
    if not matches:
        return
    idx = getattr(cv, "search_idx", -1)
    n = len(matches)
    if 0 <= idx < n:
        new_idx = (idx + delta) % n
    else:
        new_idx = 0 if delta > 0 else n - 1
    cv.search_idx = new_idx
    target = matches[new_idx]
    cv.selected = view_map.get(target, target) if view_map else target


# ── 事件处理（模块级；拆分自组件内闭包） ──────────────


def _handle_config_event(
    cv, entries, event, *, visible: bool, total: int,
    all_entries: list | None = None, view_map: dict | None = None,
    pane_vh: int = 0,
) -> bool:
    """ConfigView 输入事件处理（浏览 / 选择 / 输入 / 子 JSON / 子输入）。

    ★ 2026-10-07（config 增强）：新增参数——
      - ``all_entries``：未过滤的全量配置项（搜索/撤销等基于全量；None →
        同 ``entries``）；
      - ``view_map``：全量索引 → 过滤视图索引映射（过滤模式下匹配定位用）；
      - ``pane_vh``：主区可见行数（帮助面板翻页步长）。
    新增键位：``/`` 搜索、``n``/``N``/``p`` 匹配导航、``f`` 过滤、``?`` 帮助、
    ``r`` 恢复默认、``u`` 撤销、``y`` 复制。
    """
    if not visible or cv is None:
        return False
    search_source = all_entries if all_entries is not None else entries
    # ── 搜索输入模式（字符累积 / 退格 / 回车执行 / Esc 取消） ──
    if getattr(cv, "search_mode", False):
        if event.kind == "escape":
            cv.search_mode = False
            cv.search_query = ""
            return True
        if event.kind == "char":
            ch = getattr(event, "char", "") or ""
            if ch and "\n" not in ch and "\r" not in ch:
                q = getattr(cv, "search_query", "") or ""
                if len(q) < _SEARCH_QUERY_MAX:
                    cv.search_query = q + ch
            return True
        if event.kind == "backspace":
            q = getattr(cv, "search_query", "") or ""
            if q:
                cv.search_query = q[:-1]
            return True
        if event.kind == "enter":
            _exec_config_search(cv, search_source)
            return True
        return True
    # ── 帮助面板（``?`` 开关；覆盖主区，可滚动） ──
    if getattr(cv, "help_open", False):
        ch_help = getattr(event, "char", "") or ""
        if event.kind == "escape" or (
            event.kind == "char" and ch_help in ("?", "q")
        ):
            cv.help_open = False
            cv.help_scroll = 0
            return True
        cur_scroll = int(getattr(cv, "help_scroll", 0) or 0)
        step = max(1, int(pane_vh))
        if event.kind == "arrow_down" or (
            event.kind == "char" and ch_help in ("j", "J")
        ):
            cv.help_scroll = cur_scroll + 1
            return True
        if event.kind == "arrow_up" or (
            event.kind == "char" and ch_help in ("k", "K")
        ):
            cv.help_scroll = max(0, cur_scroll - 1)
            return True
        if event.kind == "page_down":
            cv.help_scroll = cur_scroll + step
            return True
        if event.kind == "page_up":
            cv.help_scroll = max(0, cur_scroll - step)
            return True
        if event.kind == "home" or (event.kind == "char" and ch_help == "g"):
            cv.help_scroll = 0
            return True
        if event.kind == "end" or (event.kind == "char" and ch_help == "G"):
            cv.help_scroll = 10 ** 9
            return True
        return True
    # ── 编辑模式 ──
    if cv.editing:
        if cv.edit_mode == "select":
            # 选择界面：Enter 确认 / Esc 取消；导航键放行候选 ListView
            if event.kind == "enter":
                _commit_select_edit(cv, entries)
                return True
            if event.kind == "escape":
                _cancel_edit(cv)
                return True
            return False
        if cv.edit_mode == "json":
            # 子 JSON 界面：Enter 编辑（嵌套递归进入）/ a 追加 / d 删除 /
            # Esc 逐级返回（顶层才保存写回）；导航键放行条目 ListView
            if event.kind == "enter":
                _json_edit_selected(cv)
                return True
            if event.kind == "escape":
                if getattr(cv, "edit_json_path", None):
                    _json_escape_up(cv)
                    return True
                _commit_json_edit(cv, entries)
                return True
            if event.kind == "char" and getattr(event, "char", "") in ("a", "A"):
                _json_append_start(cv)
                return True
            if event.kind == "char" and getattr(event, "char", "") in ("d", "D"):
                _json_delete_selected(cv)
                return True
            if event.kind == "delete":
                _json_delete_selected(cv)
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
                _json_commit_input(cv)
                return True
            return True
        # ★ 2026-10-07 第三批（导入路径输入）：字符累积 / 退格 / Enter 导入 / Esc 取消
        if cv.edit_mode == "import":
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
                _commit_import(cv, entries)
                return True
            return True
        # ★ 2026-10-07 第三批（撤销历史面板）：Enter 回退到该历史点、Esc 关闭；
        #   导航键放行列表控件（onNavigate 写 undo_cursor）。
        if cv.edit_mode == "undo":
            if event.kind == "escape":
                _cancel_edit(cv)
                return True
            if event.kind == "enter":
                _undo_to(cv, entries, getattr(cv, "undo_cursor", 0) or 0)
                return True
            return False
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
            _commit_input_edit(cv, entries)
            return True
        # 未识别按键吞掉（模态——不落入输入缓冲）
        return True
    # ── 增强键（2026-10-07：搜索 / 帮助 / 过滤 / 恢复默认 / 撤销 / 复制） ──
    if event.kind == "char":
        ch = getattr(event, "char", "") or ""
        # ★ 2026-10-07 第三批（分组折叠前缀）：z + a/c/o/C/O。
        prefix = getattr(cv, "pending_prefix", "") or ""
        if prefix == "z":
            cv.pending_prefix = ""
            if ch == "a":
                _toggle_group_collapse(
                    cv, entries, _sel_index(cv, total), "toggle",
                )
                return True
            if ch == "c":
                _toggle_group_collapse(
                    cv, entries, _sel_index(cv, total), "close",
                )
                return True
            if ch == "o":
                _toggle_group_collapse(
                    cv, entries, _sel_index(cv, total), "open",
                )
                return True
            if ch == "C":
                _collapse_all_groups(cv, entries, True)
                return True
            if ch == "O":
                _collapse_all_groups(cv, entries, False)
                return True
        elif ch == "z":
            cv.pending_prefix = "z"
            cv.message = "z\u2026\uff08a \u5207\u6362 / c \u6298\u53e0 / o \u5c55\u5f00 / C \u5168\u6298\u53e0 / O \u5168\u5c55\u5f00\uff09"
            return True
        # ★ 2026-10-07 第三批：分组跳转 / 导出 / 导入 / 撤销历史面板。
        if ch in ("[", "]"):
            _jump_group(
                cv, entries, _sel_index(cv, total), 1 if ch == "]" else -1,
            )
            return True
        if ch == "e":
            _do_export(cv, all_entries if all_entries is not None else entries)
            return True
        if ch == "i":
            _start_import(cv)
            return True
        if ch == "U":
            if not (getattr(cv, "undo_stack", None) or []):
                cv.message = "无可撤销的编辑"
                return True
            cv.editing = True
            cv.edit_mode = "undo"
            cv.undo_cursor = 0
            cv.message = ""
            cv.edit_error = ""
            return True
        if ch == "?":
            cv.help_open = True
            cv.help_scroll = 0
            cv.message = ""
            cv.edit_error = ""
            return True
        if ch == "/":
            cv.search_mode = True
            cv.search_query = getattr(cv, "search_pattern", "") or ""
            return True
        if ch in ("n", "N", "p") and (getattr(cv, "search_pattern", "") or ""):
            _jump_config_match(cv, 1 if ch == "n" else -1, view_map)
            return True
        if ch == "f":
            _cycle_config_filter(cv)
            return True
        if ch == "r":
            _reset_entry_default(cv, entries, _sel_index(cv, total))
            return True
        if ch == "u":
            _undo_last(cv, search_source)
            return True
        if ch == "y":
            sel_i = _sel_index(cv, total)
            _copy_entry(cv, entries[sel_i] if sel_i >= 0 else None)
            return True
    # ── 浏览模式：Esc / Ctrl+H 关闭视图（模态统一关闭键） ──
    if is_modal_close_key(event):
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


def _handle_config_paste(cv, visible: bool, text: str) -> bool:
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


# ── 行渲染器工厂（模块级；拆分自组件内闭包） ──────────


def _make_config_row_renderer(width: int, key_w: int, val_w: int, desc_w: int,
                              matched_ids: set | None = None,
                              cur_id: int | None = None):
    """配置列表行渲染器（ListView renderItem）。

    ★ 2026-10-07（config 搜索高亮）：``matched_ids`` 为搜索匹配项
    ``id(entry)`` 集合、``cur_id`` 为当前匹配项 id——匹配行背景
    ``_S_SEARCH_BG``、当前匹配行 ``_S_SEARCH_CUR_BG``（vim hlsearch 风格；
    优先级高于选中背景）。None = 无搜索（零成本快路径）。
    """

    def _render_row(entry, i, is_sel):
        # ★ 2026-10-07 第三批（分组头）：不可选分组分隔行（渲染分组名+计数）。
        if isinstance(entry, _ConfigGroupRow):
            return h(TEXT, {
                "styled": _group_header_runs(entry, width),
                "height": 1, "key": f"cv-grp-{i}",
            })
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
        eid = id(entry)
        if cur_id is not None and eid == cur_id:
            bg = _S_SEARCH_CUR_BG
        elif matched_ids is not None and eid in matched_ids:
            bg = _S_SEARCH_BG
        elif is_sel:
            bg = _S_SEL_BG
        else:
            bg = None
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"cv-{i}"})

    return _render_row


def _make_pick_row_renderer(width: int, val_w: int, pick_descs: list):
    """候选选项行渲染器（选择界面 ListView renderItem）。"""

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

    return _render_pick


def _make_json_row_renderer(
    width: int, key_w: int, val_w: int,
    json_is_dict: bool, json_keys: list, json_container,
):
    """子 JSON 条目行渲染器（json 界面 ListView renderItem）。"""

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

    return _render_json_item


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
    help_open = bool(getattr(cv, "help_open", False)) if cv is not None else False
    search_mode = bool(getattr(cv, "search_mode", False)) if cv is not None else False
    pattern = (getattr(cv, "search_pattern", "") or "") if cv is not None else ""
    filter_on = bool(getattr(cv, "search_filter", False)) if cv is not None else False
    matches = list(getattr(cv, "search_matches", None) or []) if cv is not None else []
    rc_file = (getattr(cv, "rc_file", "") or "") if cv is not None else ""

    # ── 过滤视图（``f``：列表只显示匹配配置项；编辑作用于同一 dict 对象） ──
    filter_active = bool(filter_on and pattern and matches)
    if filter_active:
        view_entries = [entries[i] for i in matches if 0 <= i < len(entries)]
        view_map = {orig: pos for pos, orig in enumerate(matches)}
    else:
        view_entries = entries
        view_map = None
    total = len(view_entries)

    editing = bool(getattr(cv, "editing", False)) if cv is not None else False
    edit_mode = getattr(cv, "edit_mode", "input") if cv is not None else "input"
    pick_mode = editing and edit_mode == "select"
    json_mode = editing and edit_mode == "json"
    json_input_mode = editing and edit_mode == "json_input"
    # ★ 2026-10-07 第三批：导入路径输入 / 撤销历史面板
    import_mode = editing and edit_mode == "import"
    undo_mode = editing and edit_mode == "undo"

    # 帮助面板内容行（主区覆盖渲染；use_memo 按栏宽缓存）
    help_rows = use_memo(
        lambda: _help_rows(width if width > 0 else 80),
        (1 if help_open else 0, width),
    )

    # ★ P1-1（巨型组件拆分）：事件处理（浏览/选择/输入/子 JSON/子输入）
    #   收敛到模块级 ``_handle_config_event`` / ``_handle_config_paste``——
    #   组件只接线（hooks 无条件注册，与拆分前一致）。
    # ★ 2026-10-07（config 增强）：搜索/撤销基于全量（all_entries），列表与
    #   编辑基于过滤视图（entries=view_entries + view_map 换算）。
    use_input(
        lambda ev: _handle_config_event(
            cv, view_entries, ev, visible=visible, total=total,
            all_entries=entries, view_map=view_map,
            pane_vh=max(1, _viewport_rows() - 1),
        ),
        visible,
    )
    usePaste(
        lambda text: _handle_config_paste(cv, visible, text),
        {"isActive": bool(visible and cv and cv.editing)},
    )
    # ★ 模态全屏视图声明（2026-08-17 通用机制）：visible 期间未消费按键被
    #   input router 吞掉（不落入输入缓冲）——字符/Enter 不误编辑/误提交；
    #   关闭后（visible=False）hook 不激活零影响，输入区恢复正常输入。
    # ★ P3（review 2026-08-20）：组件置 done → 命令线程 50ms 轮询清理之间
    #   存在≤1 帧窗口——use_fullscreen(False) 释放输入接管但 model.fullscreen
    #   仍为 "config"，App 继续渲染本组件（visible=False 返回空 TEXT），此间
    #   按键进入 input router 落入输入缓冲。与 user_select/editmsg 同构的
    #   已知窗口（渲染循环固有），命令线程轮询间隙极短，风险可接受。
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

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
    json_container = _json_container(cv)
    json_is_dict = isinstance(json_container, dict)
    # dict 容器的键列表（提示行显示当前选中键名）
    json_keys = list(cv.edit_json_keys or []) if json_is_dict else []
    # ★ P3（review）：条目数直接取容器长度——修复前 dict 分支取
    #   ``len(cv.edit_json_keys)``（派生显示态），与真实容器不同步时选中
    #   钳制错误（越界/无法到达末项）。
    json_item_count = len(json_container) if json_container is not None else 0
    json_path_text = _json_path_text(cv)
    try:
        json_sel = max(0, min(int(cv.edit_json_selected), json_item_count - 1)) if json_item_count else 0
    except (TypeError, ValueError):
        json_sel = 0
    if json_sel != cv.edit_json_selected:
        cv.edit_json_selected = json_sel

    # ── 栏宽分配（键列 / 值列 / 说明列） ──
    if width > 0 and total:
        key_w = min(30, max((wcswidth_simple(str(e["path"])) for e in view_entries), default=8) + 2)
        val_w = min(44, max((wcswidth_simple(str(e["value_text"])) for e in view_entries), default=10) + 2)
        desc_w = max(8, width - key_w - val_w - 6)
    else:
        key_w, val_w, desc_w = 22, 36, 12
    # 底部行预算（状态行 / 搜索输入行各占一行；编辑模式下由编辑行承担提示）
    status_text = (
        "" if editing else _config_status_text(cv, filter_active, len(matches))
    )
    extra_rows = (1 if search_mode else 0) + (1 if status_text else 0)
    # ★ 2026-10-07 第三批（编辑 diff 预览）：编辑态底部占 3 行（编辑行 + diff
    #   + 错误/提示）——从视口额外扣除 2 行（list_h 已扣 1 行）。
    if editing:
        extra_rows += 2
    vh = max(4, _viewport_rows() - extra_rows)

    # ── 行渲染器（模块级工厂；P1-1 拆分自组件内闭包） ──
    # ★ 2026-10-07（config 搜索高亮）：匹配项 id 集合 + 当前匹配项 id。
    _matched_ids = None
    _cur_entry_id = None
    if pattern and matches:
        _matched_ids = {id(entries[i]) for i in matches if 0 <= i < len(entries)}
        _idx_m = getattr(cv, "search_idx", -1)
        if 0 <= _idx_m < len(matches):
            _mi = matches[_idx_m]
            if 0 <= _mi < len(entries):
                _cur_entry_id = id(entries[_mi])
    render_row = _make_config_row_renderer(
        width, key_w, val_w, desc_w, _matched_ids, _cur_entry_id,
    )
    render_pick = _make_pick_row_renderer(
        width, val_w, list(cv.edit_options_desc or []),
    )
    render_json_item = _make_json_row_renderer(
        width, key_w, val_w, json_is_dict, json_keys, json_container,
    )

    # ★ 2026-10-07 第三批（分组折叠）：过滤视图条目 → 分组头 + 条目行，
    #   并维护「行 ↔ 视图条目索引」双向映射（导航/光标定位）。
    collapsed_groups = set(getattr(cv, "collapsed_groups", None) or ())
    display_items, row_to_entry, entry_to_row = _config_rows(
        view_entries, collapsed_groups,
    )
    sel_row = entry_to_row.get(selected, 0) if display_items else 0
    # 撤销历史面板数据（``U``）
    undo_items = _undo_entries(cv) if undo_mode else []

    def _on_navigate(row_idx: int) -> None:
        """列表导航（行下标）→ 视图条目索引（分组头 -1 忽略）。"""
        try:
            row_idx = int(row_idx)
        except (TypeError, ValueError):
            return
        idx = row_to_entry[row_idx] if 0 <= row_idx < len(row_to_entry) else -1
        if idx < 0:
            return
        cv.selected = idx
        # ★ 2026-10-07（config 增强）：切换选中清除陈旧操作反馈。
        cv.message = ""

    def _on_undo_navigate(idx: int) -> None:
        cv.undo_cursor = int(idx)

    def _render_undo_item(item, i, is_sel):
        """撤销历史项渲染（``path = 旧值``）。"""
        prefix = "\u25b6 " if is_sel else "  "
        label = str(item.get("path") or item.get("key") or "")
        old = str(item.get("old_text") or "")
        runs = [
            StyledRun(prefix, _S_SEL_MARK if is_sel else None),
            StyledRun(label, _S_KEY),
            StyledRun(" = ", _S_HINT),
            StyledRun(old, _S_UNDO),
        ]
        if width > 0:
            runs = truncate_runs(runs, width)
        if is_sel:
            runs = [
                StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG))
                for r in runs
            ]
        return h(TEXT, {
            "styled": runs, "height": 1, "key": f"cv-undo-{i}",
        })

    def _on_pick_navigate(idx: int) -> None:
        cv.edit_selected = int(idx)

    def _json_items():
        """json 界面 ListView items（与当前容器条目索引一一对应）。"""
        if json_is_dict:
            return list(json_keys)
        return list(range(len(json_container or [])))

    def _on_json_navigate(idx: int) -> None:
        cv.edit_json_selected = int(idx)

    # ── 头部（标题 + 统计 + 来源 + 提示；行尾 ─ 分隔线填充至满宽） ──
    if search_mode:
        header_hint = "  \u8f93\u5165\u641c\u7d22\u8bcd \u00b7 Enter \u6267\u884c \u00b7 Esc \u53d6\u6d88"
    elif help_open:
        header_hint = "  \u5e2e\u52a9\u9762\u677f \u00b7 ? / q / Esc \u5173\u95ed"
    elif pick_mode:
        entry = _entry_by_key(view_entries, cv.edit_key)
        pick_path = entry["path"] if entry else cv.edit_key
        header_hint = f"选择 {pick_path}（\u2191\u2193/jk 选择 \u00b7 Enter 确认 \u00b7 Esc 取消）"
    elif json_mode:
        entry = _entry_by_key(view_entries, cv.edit_key)
        json_path = entry["path"] if entry else cv.edit_key
        if json_path_text:
            json_path = f"{json_path}.{json_path_text}"
        header_hint = f"子 JSON \u00b7 {json_path}（\u2191\u2193/jk 选择 \u00b7 Enter 编辑 \u00b7 a 追加 \u00b7 d 删除 \u00b7 Esc 返回/保存）"
    elif json_input_mode:
        header_hint = "子输入"
    elif editing:
        header_hint = "编辑"
    else:
        header_hint = "\u2191\u2193/jk \u9009\u62e9 \u00b7 Enter \u7f16\u8f91 \u00b7 e/i \u5bfc\u51fa/\u5bfc\u5165 \u00b7 U \u64a4\u9500\u5386\u53f2 \u00b7 z \u5206\u7ec4 \u00b7 ? \u5e2e\u52a9"
    count_seg = f" \u00b7 {total} \u9879"
    if filter_active:
        count_seg += f"/{len(entries)}"
    if pattern:
        count_seg += f" \u00b7 \u5339\u914d {len(matches)}"
    header_runs = [
        StyledRun("\u258d\u2699 \u914d\u7f6e\u4e2d\u5fc3", _S_TITLE),
        StyledRun(count_seg, _S_HINT),
    ]
    if rc_file:
        header_runs.append(StyledRun(f" \u00b7 \u6765\u6e90 {rc_file}", _S_SOURCE))
    header_runs.append(StyledRun(f"  {header_hint}", _S_HINT))
    if width > 0:
        header_runs = truncate_runs(header_runs, width)
        used = sum(getattr(r, "width", 1) for r in header_runs)
        pad = width - used
        if pad > 0:
            header_runs.append(StyledRun("\u2500" * pad, _S_SEP_ROW))

    # ── 主列表区 ──
    # ★ P2（review 2026-08-20）：修复前 ``vh - (1 if editing else 1)`` 恒等于
    #   ``vh - 1``（冗余条件表达式）——编辑模式与浏览模式底部行同为 1 行。
    #   ★ P2（review）：下限由 4 改为 1——修复前 ``max(4, vh - 1)`` 在矮终端
    #   时总行数（header 1 + list ≥4 + 底部提示 ≤2）可能超终端高（溢出/裁剪）；
    #   与 editmsg_select/user_select 等模态弹窗的下限处置一致。
    list_h = max(1, vh - 1)
    if pick_mode:
        ledger = h(ListView, {
            "items": list(cv.edit_options or []),
            "height": list_h,
            "width": width if width > 0 else None,
            "cursor": pick_sel if pick_total else 0,
            "renderItem": render_pick,
            "onNavigate": _on_pick_navigate,
            "focus": visible and pick_mode,
        })
    elif json_mode:
        ledger = h(ListView, {
            "items": _json_items(),
            "height": list_h,
            "width": width if width > 0 else None,
            "cursor": json_sel if json_item_count else 0,
            "renderItem": render_json_item,
            "onNavigate": _on_json_navigate,
            "focus": visible and json_mode,
        })
    elif undo_mode:
        try:
            undo_sel = int(getattr(cv, "undo_cursor", 0) or 0)
        except (TypeError, ValueError):
            undo_sel = 0
        if undo_items:
            undo_sel = max(0, min(undo_sel, len(undo_items) - 1))
        else:
            undo_sel = 0
        ledger = h(ListView, {
            "items": undo_items,
            "height": list_h,
            "width": width if width > 0 else None,
            "cursor": undo_sel,
            "renderItem": _render_undo_item,
            "onNavigate": _on_undo_navigate,
            "focus": visible and undo_mode,
        })
    else:
        ledger = h(ListView, {
            "items": display_items,
            "height": list_h,
            "width": width if width > 0 else None,
            "cursor": sel_row if display_items else 0,
            "renderItem": render_row,
            "onNavigate": _on_navigate,
            "isSelectable": _is_config_selectable,
            "focus": visible and not editing and not help_open,
        })

    # ── 帮助面板（``?`` 开关；覆盖主区，可滚动） ──
    if help_open:
        scroll_h = max(0, int(getattr(cv, "help_scroll", 0) or 0))
        total_help = len(help_rows)
        panel_vh = max(1, list_h)
        max_scroll = max(0, total_help - panel_vh)
        scroll_h = min(scroll_h, max_scroll)
        if cv is not None and scroll_h != getattr(cv, "help_scroll", 0):
            cv.help_scroll = scroll_h
        help_children: list = []
        for i, runs in enumerate(help_rows[scroll_h:scroll_h + panel_vh]):
            help_children.append(h(TEXT, {
                "styled": runs, "height": 1, "key": f"cv-help-{i}",
            }))
        ledger = h(Column, None, help_children)

    # ── 底部行 ──
    bottom_rows: list = []
    if undo_mode:
        # ★ 2026-10-07 第三批（撤销历史面板）：列表即历史（主区），底部仅提示。
        bottom_rows.append(h(TEXT, {
            "children": (
                "  \u2191\u2193/jk \u9009\u62e9\u5386\u53f2 \u00b7 "
                "Enter \u56de\u9000\u5230\u8be5\u70b9 \u00b7 Esc \u5173\u95ed"
            ),
            "style": _S_HINT, "textWrap": "truncate-end", "height": 1,
            "key": "cv-undo-hint",
        }))
        if cv.message:
            msg_disp = f"  \u2713 {cv.message}"
            if width > 0:
                msg_disp = _truncate_width(msg_disp, width)
            bottom_rows.append(h(TEXT, {
                "children": msg_disp, "style": _S_OK,
                "textWrap": "truncate-end", "height": 1, "key": "cv-undo-msg",
            }))
    elif import_mode:
        # ★ 2026-10-07 第三批（从 JSON 导入配置）：路径输入行 + 错误/提示。
        disp = f"  \u258d \u21e5 \u5bfc\u5165\u6587\u4ef6: {cv.edit_value}\u258f"
        if width > 0:
            disp = _truncate_width(disp, width)
        bottom_rows.append(h(TEXT, {
            "children": disp, "style": _S_IMPORT,
            "textWrap": "truncate-end", "height": 1, "key": "cv-import",
        }))
        if cv.edit_error:
            err_disp = f"  \u2716 {cv.edit_error}"
            if width > 0:
                err_disp = _truncate_width(err_disp, width)
            bottom_rows.append(h(TEXT, {
                "children": err_disp, "style": _S_ERR,
                "textWrap": "truncate-end", "height": 1, "key": "cv-import-err",
            }))
        else:
            bottom_rows.append(h(TEXT, {
                "children": "  Enter \u5bfc\u5165 \u00b7 Esc \u53d6\u6d88",
                "style": _S_HINT, "height": 1, "key": "cv-import-hint",
            }))
    elif pick_mode:
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
        elif json_container is None:
            # ★ P3（review）：容器缺失（路径失效）——修复前落入最后 else 分支
            #   输出 ``path[sel] = value`` 形式的误导性提示。
            prompt = f"  \u258d \u270e {path}（容器不存在）: {cv.edit_value}\u258f"
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
        # ★ 2026-10-07 第三批（编辑 diff 预览）：旧值 → 新值对照
        old_val = ""
        if cv.edit_json_action == "edit":
            cur_v = _json_entry_value(json_container, cv.edit_json_selected)
            old_val = _json_text(cur_v) if cur_v is not None else ""
        diff_runs = _diff_runs(old_val, cv.edit_value)
        bottom_rows.append(h(TEXT, {
            "styled": truncate_runs(diff_runs, width) if width > 0 else diff_runs,
            "height": 1, "key": "cv-json-diff",
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
        # ★ 2026-10-07 第三批（编辑 diff 预览）：旧值 → 新值对照
        old_text = entry.get("value_text") if entry else ""
        diff_runs = _diff_runs(old_text, cv.edit_value)
        bottom_rows.append(h(TEXT, {
            "styled": truncate_runs(diff_runs, width) if width > 0 else diff_runs,
            "height": 1, "key": "cv-diff",
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
    elif status_text:
        # ★ 2026-10-07（config 增强）：搜索计数 / 操作反馈状态行。
        disp = "  " + status_text
        if width > 0:
            disp = _truncate_width(disp, width)
        bottom_rows.append(h(TEXT, {
            "children": disp, "style": _S_STATUS,
            "textWrap": "truncate-end", "height": 1, "key": "cv-status",
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
            "children": "  \u2191\u2193/jk 选择 \u00b7 Enter 编辑 \u00b7 r 默认 \u00b7 u 撤销 \u00b7 / 搜索 \u00b7 ? 帮助 \u00b7 Esc 关闭",
            "style": _S_HINT,
            "textWrap": "truncate-end", "height": 1, "key": "cv-hint",
        }))
    # ── 底部搜索输入行（``/`` 输入模式） ──
    if search_mode:
        q = getattr(cv, "search_query", "") or ""
        if width > 0:
            q = q[: max(0, width - 2)]
        bottom_rows.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_EDIT,
            "textWrap": "truncate-end", "height": 1, "key": "cv-search",
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

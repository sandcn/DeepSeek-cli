"""model_view — ModelView 模型选择器视图组件（模态全屏视图）。

``/models`` 命令（ModelsPlugin）或 ``/model`` 无参数打开：App 在
``model.fullscreen == "model"`` 时经全屏视图注册表**整屏只渲染本组件**
（消息区/顶部标题栏/状态栏/输入区全部不显示），模型列表占满整个终端；
Esc/Ctrl+H 关闭后恢复完整聊天界面。

能力（用户需求：增加模型选择器——选择 / 增加 / 编辑模型 url·key·name 等）：

  - **选择**：浏览模式 ↑↓/jk 选择模型，Enter 应用（切换当前模型）——
    列表 = **模型档案**（``model_profiles``，**模型列表的唯一来源**；RC
    顶层 ``models`` 字段已移除，内置 provider 模型只作元数据不进入列表，
    见 ``config.model_profiles.build_model_entries``）。
  - **增加**：``a`` 打开表单（名称 / 模型名 / 接口地址 / API 密钥 /
    Provider 五字段），``s`` 保存为新的模型档案。
  - **编辑**：``e`` 编辑选中档案（同上表单，``s`` 保存）。
  - **复制**：``c`` 以选中档案为模板新建（预填副本字段，改名后 ``s``
    保存）——基于已有模型快速派生变体。
  - **删除**：``d`` → 确认后删除选中档案。
  - **刷新**：``r`` 重新加载档案列表（外部改动 /config 后同步）。

美化与体验（2026-10-09 用户需求「美化，优化用户体验和操作 /models」）：

  - 列表行**同时显示模型名与接口主机**（修复此前「有 base_url 时模型名
    被地址覆盖」的缺陷），列对齐 + 状态徽标（``◆key`` / ``●当前``）；
  - 列表上方新增**选中详情行**：完整展示选中档案的模型名 · 提供商 ·
    接口地址 · 脱敏密钥，随选中实时更新；
  - 字段输入升级为**光标编辑**（←/→ 移动 · Home/End 首末 · Delete 删除 ·
    Ctrl+A/Ctrl+E 首末 · Ctrl+U 清空 · 退格，支持中间插入）；
  - 空态引导卡片化（图标 + 三步流程）。

交互（use_input 路由 + 模态全屏声明）：
  - 浏览模式：↑↓/jk 选择 · g/G 首末 · PgUp/PgDn 翻页 · Enter 应用 ·
    a 新增 · c 复制 · e 编辑 · d 删除 · r 刷新 · / 搜索 · n/N/p 匹配 ·
    f 过滤 · ? 帮助 · y 复制信息 · Esc/Ctrl+H 关闭；
  - 表单模式：↑↓/jk 选择字段 · Enter 编辑字段 · s 保存 · Esc 取消；
  - 字段输入：可打印字符（光标处插入）· ←/→/Home/End 移动光标 ·
    Delete/退格删除 · Ctrl+A/Ctrl+E 首末 · Ctrl+U 清空 · Enter 确认 ·
    Esc 取消。

数据协议（跨线程安全，与 config/plugin 同构）：
  - 命令线程（``_cmd_models``）：构建 entries → 设置 ``model.model_view``
    （visible=True, seq+1, entries）→ ``model.fullscreen="model"`` →
    request_bottom_redraw → 轮询 ``done``（带 deadline），期间检测
    ``applied_seq`` 变化即应用选中条目 → finally 清理；
  - 组件：浏览导航写 ``selected``；Enter 应用写 ``applied_seq``+``applied``
    （命令线程负责写 RC + 同步 session.model / 状态栏）；表单保存/删除经
    ``config.model_profiles.save_profiles`` 直接持久化（loader 有锁 + 原子
    写，线程安全）；Esc 关闭经 ``state.try_set_final("cancel")`` 原子终态写入。

依赖约束：仅依赖 app 同层（_modal_view/_keymap_pane）与 ink 框架 + config
model_profiles（纯逻辑）；无 tools 层反向依赖。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple, truncate_width as _truncate_width
from src.tui.ink import TEXT, Column, Row, StyledRun, h
from src.tui.ink.hooks import use_input, usePaste, use_memo
from src.tui.ink.helpers import truncate_runs, truncate_runs_ellipsis
from src.tui.ink.widgets.listview import ListView
from src.config.model_profiles import (
    FIELD_KEYS,
    PROFILE_FIELDS,
    build_model_entries,
    default_base_url,
    empty_profile,
    field_by_key,
    load_profiles,
    mask_key,
    normalize_profile,
    provider_choices,
    save_profiles,
    set_active_profile,
    validate_profile,
)

from ._keymap_pane import keymap_panel_rows
from ._modal_view import empty_modal_frame, is_modal_close_key, use_modal_scope

__all__ = ["ModelView"]

# ── 样式（静态色——浏览/编辑界面，不呼吸，diff 零输出） ──
_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_NAME = Style(fg=75)
_S_MODEL = Style(fg=252)
_S_URL = Style(fg=110)
_S_KEY = Style(fg=214)
_S_PROFILE = Style(fg=135)
_S_CURRENT = Style(fg=40, bold=True)
_S_SEL_BG = Style(bg=237)
_S_SEL_MARK = Style(fg=45, bold=True)
_S_OK = Style(fg=40, bold=True)
_S_ERR = Style(fg=196, bold=True)
_S_EDIT = Style(fg=45, bold=True)
_S_STATUS = Style(fg=221)
_S_SEARCH_BG = Style(bg=236)
_S_SEARCH_CUR_BG = Style(bg=25)
_S_HELP_KEY = Style(fg=214)
_S_HELP_GROUP = Style(fg=110, bold=True)
_S_HELP_DESC = Style(fg=252)
_S_FORM_LABEL = Style(fg=75)
_S_FORM_VALUE = Style(fg=252)
# ── 美化增强（选中详情行 / 空态 / 光标） ──
_S_DETAIL_LABEL = Style(fg=110)           # 详情行字段标签（浅蓝）
_S_DETAIL_VALUE = Style(fg=252)           # 详情行字段值（亮白）
_S_DETAIL_SEP = Style(fg=238)             # 详情行分隔符（深灰）
_S_EMPTY_TITLE = Style(fg=45, bold=True)  # 空态标题（亮青加粗）
_S_REQUIRED = Style(fg=214)               # 必填星标（黄）
_S_CURSOR = Style(fg=16, bg=45, bold=True)  # 字段输入光标（反色块）

#: 字段输入长度上限（渲染行按宽度截断）。
_EDIT_VALUE_MAX = 400
#: 搜索输入长度上限。
_SEARCH_QUERY_MAX = 200


def _viewport_rows() -> int:
    """主区可见行数（终端高度自适应；无高度上下文回退 16）。"""
    try:
        from src.tui._screen import TerminalWidthCache
        h_ = TerminalWidthCache.get_default().get_height()
        return max(6, int(h_) - 3)
    except Exception:
        return 16


def _disp_width(text: str) -> int:
    try:
        return wcswidth_simple(text)
    except Exception:
        return len(text)


def _url_host(url: str) -> str:
    """接口地址 → 主机简写（``scheme://host[:port]``，去掉路径）。

    列表行空间有限，展示主机名即可区分不同服务；完整地址在选中详情行
    展示。解析失败回退原串（不抛异常）。
    """
    s = str(url or "").strip()
    if not s:
        return ""
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(s if "://" in s else "//" + s)
        host = parts.netloc or parts.path.split("/")[0]
        return host or s
    except Exception:
        return s


def _profile_entry_values(entry) -> dict:
    """从模型条目提取档案字段值（仅 ``FIELD_KEYS``，缺失补空串）。"""
    out = {k: "" for k in FIELD_KEYS}
    if isinstance(entry, dict):
        for key in FIELD_KEYS:
            value = entry.get(key)
            if value is None and key == "base_url":
                value = entry.get("effective_base_url")
            out[key] = "" if value is None else str(value)
    return out


# ═══════════════════════════════════════════════════════════
# 纯辅助（搜索 / 复制 / 表单 / 帮助）
# ═══════════════════════════════════════════════════════════


def _entry_search_text(entry) -> str:
    """模型条目搜索文本（名称 / 模型名 / provider / 地址 / 类型）。"""
    if not isinstance(entry, dict):
        return ""
    return "\n".join([
        str(entry.get("name", "") or ""),
        str(entry.get("model", "") or ""),
        str(entry.get("provider", "") or ""),
        str(entry.get("effective_base_url", entry.get("base_url", "")) or ""),
        str(entry.get("hint", "") or ""),
    ])


def _search_matches(entries: list, pattern: str) -> list:
    """搜索匹配的条目索引（子串匹配，忽略大小写；空模式 → 空列表）。"""
    if not pattern:
        return []
    low = str(pattern).lower()
    return [i for i, e in enumerate(entries or []) if low in _entry_search_text(e).lower()]


def _help_rows(right_w: int) -> list:
    """帮助面板内容行（模型选择器键位速查）。"""
    from src.presentation_data import model_keymap

    return keymap_panel_rows(
        model_keymap(), right_w,
        key_style=_S_HELP_KEY, group_style=_S_HELP_GROUP,
        desc_style=_S_HELP_DESC, sep_style=_S_SEP,
        empty_text="(\u5feb\u6377\u952e\u901f\u67e5\u8868\u672a\u6ce8\u518c)",
    )


def _copy_entry(mv, entry) -> None:
    """``y``：复制选中模型信息到剪贴板（OSC52）。"""
    if entry is None:
        mv.message = "无可复制的模型"
        return
    parts = [f"{entry.get('name', '')}"]
    parts.append(f"model={entry.get('model', '')}")
    parts.append(f"provider={entry.get('provider', '')}")
    url = entry.get("effective_base_url") or entry.get("base_url") or ""
    if url:
        parts.append(f"base_url={url}")
    if entry.get("api_key"):
        parts.append(f"api_key={mask_key(entry.get('api_key'))}")
    text = " ".join(parts)
    from src.tui._screen import set_clipboard
    if set_clipboard(text):
        mv.message = f"已复制 {text}（{len(text)} 字符）"
    else:
        mv.message = "复制失败：无可用终端输出"


def _current_provider() -> str:
    """当前生效的提供商（读 RC；异常回退空串）。"""
    try:
        from src.config.loader import get_rc
        return str(get_rc().get("provider", "") or "")
    except Exception:
        return ""


def _start_form(mv, entry=None) -> None:
    """进入表单（entry=None → 新增；否则编辑该档案）。

    新增时预填**当前提供商**与其默认接口地址（用户只需填模型名与 API 密钥）。
    """
    mv.editing = True
    mv.edit_mode = "form"
    mv.form_selected = 0
    mv.form_edit_value = ""
    mv.form_select_options = []
    mv.form_select_desc = []
    mv.form_select_index = 0
    mv.edit_error = ""
    mv.message = ""
    if entry is None:
        mv.form_is_new = True
        mv.form_index = None
        provider = _current_provider() or "deepseek"
        values = empty_profile()
        values["provider"] = provider
        values["base_url"] = default_base_url(provider)
        mv.form_values = values
    else:
        mv.form_is_new = False
        mv.form_index = entry.get("index")
        for key in FIELD_KEYS:
            mv.form_values[key] = "" if entry.get(key) is None else str(entry.get(key))
    # 补全缺失字段
    values = dict(mv.form_values or {})
    for key in FIELD_KEYS:
        values.setdefault(key, "")
    mv.form_values = values


def _duplicate_selected(mv, entry) -> None:
    """``c``：以选中档案为模板新建（预填副本字段，改名后 ``s`` 保存）。

    复制而非直接新增——基于已有模型快速派生变体（如同一提供商下切换
    模型名）；副本显示名追加「 副本」避免与源档案重名校验冲突。
    """
    if not isinstance(entry, dict) or entry.get("kind") != "profile":
        mv.reset_edit_state()
        mv.message = "仅模型档案可复制"
        return
    values = _profile_entry_values(entry)
    base_name = (values.get("name") or values.get("model") or "").strip()
    values["name"] = f"{base_name} 副本" if base_name else "副本"
    _start_form(mv, None)
    mv.form_values = values
    mv.message = f"已复制档案「{base_name}」，编辑后按 s 保存"


def _start_select(mv, key: str) -> None:
    """进入某字段的选择界面（当前仅「提供商」字段）。"""
    choices = provider_choices()
    mv.edit_mode = "select"
    mv.form_select_options = [c[0] for c in choices]
    mv.form_select_desc = [c[1] for c in choices]
    cur = str((mv.form_values or {}).get(key, "") or "")
    idx = 0
    for i, opt in enumerate(mv.form_select_options):
        if opt == cur:
            idx = i
            break
    mv.form_select_index = idx
    mv.edit_error = ""


def _commit_select(mv) -> None:
    """选择界面确认：写回字段值；「提供商」选择后自动填接口地址。"""
    keys = list(FIELD_KEYS)
    idx = int(getattr(mv, "form_selected", 0) or 0)
    key = keys[idx] if 0 <= idx < len(keys) else ""
    opts = list(mv.form_select_options or [])
    si = max(0, min(int(getattr(mv, "form_select_index", 0) or 0), len(opts) - 1)) if opts else -1
    if not key or si < 0:
        mv.edit_mode = "form"
        return
    value = opts[si]
    values = dict(mv.form_values or {})
    values[key] = value
    if key == "provider":
        # 接口地址按提供商自动给定（用户无需输入；仍可在表单里修改）
        values["base_url"] = default_base_url(value)
    mv.form_values = values
    mv.edit_mode = "form"
    mv.edit_error = ""
    mv.message = f"提供商: {value}" if key == "provider" else ""


def _field_value_display(mv, key: str) -> str:
    """表单字段显示值（敏感字段脱敏）。"""
    field = field_by_key(key)
    value = str((mv.form_values or {}).get(key, "") or "")
    if field and field.get("sensitive"):
        return mask_key(value)
    return value


def _commit_field(mv) -> None:
    """字段输入确认：写回 form_values 并返回表单模式。"""
    keys = list(FIELD_KEYS)
    idx = int(getattr(mv, "form_selected", 0) or 0)
    if 0 <= idx < len(keys):
        values = dict(mv.form_values or {})
        values[keys[idx]] = mv.form_edit_value
        mv.form_values = values
    mv.edit_mode = "form"
    mv.form_edit_value = ""
    mv.form_edit_cursor = 0
    mv.edit_error = ""


def _commit_form(mv) -> None:
    """表单保存：校验 → 写回档案列表 → 重建条目 → 退出表单。"""
    profile = normalize_profile(mv.form_values or {})
    # 名称留空 → 用模型名（用户只需填提供商/模型名/密钥）
    if not (profile.get("name") or "").strip():
        profile["name"] = (profile.get("model") or "").strip()
    profiles = load_profiles()
    index = None if mv.form_is_new else mv.form_index
    err = validate_profile(profile, profiles, index=index)
    if err:
        mv.edit_error = err
        return
    is_new = mv.form_is_new or index is None or not (0 <= int(index) < len(profiles))
    if is_new:
        profiles.append(profile)
        msg = f"已新增模型档案 {profile['name']}"
    else:
        profiles[int(index)] = profile
        msg = f"已保存模型档案 {profile['name']}"
    try:
        save_profiles(profiles)
    except Exception as exc:
        mv.edit_error = f"保存失败: {exc}"
        return
    if is_new:
        # 新增档案即设为当前生效——LLM 访问参数唯一来源 = 模型档案，
        # 首次配置（启动引导）保存后立即可用
        set_active_profile(len(profiles) - 1)
    _refresh_entries(mv)
    # 保存后定位到该档案（新增/编辑/复制均回到刚保存的条目）
    mv.selected = _locate_entry(mv.entries, profile.get("name") or profile.get("model") or "")
    mv.reset_edit_state()
    mv.message = msg


def _delete_selected(mv, entry) -> None:
    """删除选中模型档案。"""
    if not isinstance(entry, dict) or entry.get("kind") != "profile":
        mv.reset_edit_state()
        mv.message = "仅模型档案可删除"
        return
    index = entry.get("index")
    profiles = load_profiles()
    if index is None or not (0 <= int(index) < len(profiles)):
        mv.reset_edit_state()
        mv.message = "该档案已不存在"
        return
    name = profiles[int(index)].get("name") or ""
    del profiles[int(index)]
    try:
        save_profiles(profiles)
    except Exception as exc:
        mv.edit_error = f"删除失败: {exc}"
        return
    _refresh_entries(mv)
    # 删除后选中被删位置的下一项（钳制到列表范围），保持浏览连续性
    total_new = len(getattr(mv, "entries", None) or [])
    mv.selected = max(0, min(int(index), total_new - 1)) if total_new else 0
    mv.reset_edit_state()
    mv.message = f"已删除模型档案 {name}"


def _locate_entry(entries, name: str) -> int:
    """按显示名定位条目索引（找不到回退 0——用于保存后定位）。"""
    target = str(name or "")
    if target:
        for i, e in enumerate(entries or []):
            if isinstance(e, dict) and str(e.get("name") or "") == target:
                return i
    return 0


def _refresh_entries(mv) -> None:
    """重建条目列表（档案增删改后刷新，保持视图与持久化一致）。"""
    try:
        mv.entries = build_model_entries()
    except Exception:
        pass
    # 搜索匹配同步刷新（过滤模式下保持过滤视图有效）
    pattern = getattr(mv, "search_pattern", "") or ""
    if pattern:
        matches = _search_matches(list(mv.entries or []), pattern)
        mv.search_matches = matches
        if matches:
            mv.search_idx = max(0, min(int(getattr(mv, "search_idx", 0) or 0), len(matches) - 1))
        else:
            mv.search_idx = -1


def _cycle_filter(mv) -> None:
    """``f``：切换过滤模式（列表只显示搜索匹配）。"""
    new_value = not bool(getattr(mv, "search_filter", False))
    pattern = getattr(mv, "search_pattern", "") or ""
    matches = list(getattr(mv, "search_matches", None) or [])
    if new_value and not (pattern and matches):
        mv.search_filter = False
        mv.message = "过滤需先搜索且有匹配（/ 搜索）"
        return
    mv.search_filter = new_value
    mv.message = (
        f"过滤开启：仅显示 {len(matches)} 项匹配"
        if new_value else "过滤关闭：显示全部模型"
    )


def _exec_search(mv, entries) -> None:
    """回车执行搜索（匹配基于全量；定位到首项）。"""
    q = getattr(mv, "search_query", "") or ""
    mv.search_mode = False
    mv.search_pattern = q
    matches = _search_matches(entries, q)
    mv.search_matches = matches
    if matches:
        mv.search_idx = 0
        mv.selected = 0
        mv.message = f"{len(matches)} 项匹配"
    else:
        mv.search_idx = -1
        mv.message = f"无匹配：{q}" if q else ""


def _jump_match(mv, delta: int, view_map: dict | None = None) -> None:
    """``n``/``N``/``p``：在匹配项间环绕跳转（过滤模式下换算视图索引）。"""
    matches = list(getattr(mv, "search_matches", None) or [])
    if not matches:
        return
    idx = getattr(mv, "search_idx", -1)
    n = len(matches)
    new_idx = (idx + delta) % n if 0 <= idx < n else (0 if delta > 0 else n - 1)
    mv.search_idx = new_idx
    target = matches[new_idx]
    mv.selected = view_map.get(target, target) if view_map else target


def _sel_index(mv, total: int) -> int:
    """当前选中索引（钳制到 ``[0, total-1]``；total<=0 → -1）。"""
    if total <= 0:
        return -1
    try:
        return max(0, min(int(getattr(mv, "selected", 0) or 0), total - 1))
    except (TypeError, ValueError):
        return 0


def _status_text(mv, filter_active: bool) -> str:
    """底部状态行文本（搜索计数 + 操作反馈）。"""
    if mv is None:
        return ""
    parts: list = []
    pattern = getattr(mv, "search_pattern", "") or ""
    if pattern:
        n = len(getattr(mv, "search_matches", None) or [])
        idx = getattr(mv, "search_idx", -1)
        cur = (idx + 1) if 0 <= idx < n else 0
        seg = f"/{pattern}  {cur}/{n}"
        if filter_active:
            seg += " [\u8fc7\u6ee4]"
        parts.append(seg)
    err = getattr(mv, "edit_error", "") or ""
    msg = getattr(mv, "message", "") or ""
    if err:
        parts.append(f"\u2716 {err}")
    elif msg:
        parts.append(f"\u2713 {msg}")
    return "  \u00b7  ".join(parts)


def _detail_runs(entry, width: int) -> list:
    """选中模型详情行（完整信息，随选中实时更新）。

    展示：模型名 · 提供商（显示说明） · 完整接口地址 · 脱敏密钥。
    条目为空 / 非法时返回空列表（调用方不渲染该行）。
    """
    if not isinstance(entry, dict):
        return []
    sep = "  \u00b7  "
    runs: list = [
        StyledRun("  \u21b3 ", _S_DETAIL_SEP),
        StyledRun(str(entry.get("model") or ""), _S_DETAIL_VALUE),
    ]
    provider = str(entry.get("provider") or "")
    if provider:
        hint = str(entry.get("hint") or provider)
        label = provider if hint == provider else f"{provider}\u00b7{hint}"
        runs.append(StyledRun(sep, _S_DETAIL_SEP))
        runs.append(StyledRun(label, _S_DETAIL_LABEL))
    url = str(entry.get("effective_base_url") or entry.get("base_url") or "")
    if url:
        runs.append(StyledRun(sep, _S_DETAIL_SEP))
        runs.append(StyledRun(url, _S_DETAIL_VALUE))
    key = entry.get("api_key")
    if key:
        runs.append(StyledRun(sep, _S_DETAIL_SEP))
        runs.append(StyledRun(f"key={mask_key(str(key))}", _S_KEY))
    if width > 0:
        runs = truncate_runs(runs, width)
    return runs


# ═══════════════════════════════════════════════════════════
# 事件处理（模块级；拆分自组件内闭包，便于单测）
# ═══════════════════════════════════════════════════════════


def _handle_model_event(
    mv, entries, event, *, visible: bool, total: int,
    all_entries: list | None = None, view_map: dict | None = None,
    pane_vh: int = 0,
) -> bool:
    """ModelView 输入事件处理（浏览 / 表单 / 字段输入 / 搜索 / 帮助）。"""
    if not visible or mv is None:
        return False
    search_source = all_entries if all_entries is not None else entries
    ch = getattr(event, "char", "") or ""
    kind = event.kind

    # ── 搜索输入模式（独占按键） ──
    if getattr(mv, "search_mode", False):
        if kind == "escape":
            mv.search_mode = False
            mv.search_query = ""
            return True
        if kind == "char":
            if ch and "\n" not in ch and "\r" not in ch:
                q = getattr(mv, "search_query", "") or ""
                if len(q) < _SEARCH_QUERY_MAX:
                    mv.search_query = q + ch
            return True
        if kind == "backspace":
            q = getattr(mv, "search_query", "") or ""
            if q:
                mv.search_query = q[:-1]
            return True
        if kind == "enter":
            _exec_search(mv, search_source)
            return True
        return True

    # ── 帮助面板（``?`` 开关；覆盖主区，可滚动） ──
    if getattr(mv, "help_open", False):
        if kind == "escape" or (kind == "char" and ch in ("?", "q")):
            mv.help_open = False
            mv.help_scroll = 0
            return True
        cur = int(getattr(mv, "help_scroll", 0) or 0)
        step = max(1, int(pane_vh))
        if kind == "arrow_down" or (kind == "char" and ch in ("j", "J")):
            mv.help_scroll = cur + 1
            return True
        if kind == "arrow_up" or (kind == "char" and ch in ("k", "K")):
            mv.help_scroll = max(0, cur - 1)
            return True
        if kind == "page_down":
            mv.help_scroll = cur + step
            return True
        if kind == "page_up":
            mv.help_scroll = max(0, cur - step)
            return True
        if kind == "home" or (kind == "char" and ch == "g"):
            mv.help_scroll = 0
            return True
        if kind == "end" or (kind == "char" and ch == "G"):
            mv.help_scroll = 10 ** 9
            return True
        return True

    # ── 表单模式 ──
    if getattr(mv, "editing", False):
        mode = getattr(mv, "edit_mode", "form")
        if mode == "field":
            # 字段输入（光标编辑）：字符在光标处插入 · ←/→ 移动 · Home/End
            # 首末 · Delete 删除 · 退格 · Ctrl+A/Ctrl+E 首末 · Ctrl+U 清空 ·
            # Enter 确认 · Esc 取消
            if kind == "escape":
                mv.edit_mode = "form"
                mv.form_edit_value = ""
                mv.form_edit_cursor = 0
                mv.edit_error = ""
                return True
            value = mv.form_edit_value
            try:
                cur = int(getattr(mv, "form_edit_cursor", 0) or 0)
            except (TypeError, ValueError):
                cur = 0
            cur = max(0, min(cur, len(value)))
            if kind == "ctrl_key":
                if ch == "\x15":  # Ctrl+U
                    mv.form_edit_value = ""
                    mv.form_edit_cursor = 0
                elif ch == "\x01":  # Ctrl+A
                    mv.form_edit_cursor = 0
                elif ch == "\x05":  # Ctrl+E
                    mv.form_edit_cursor = len(value)
                return True
            if kind == "char":
                if ch and "\n" not in ch and "\r" not in ch and len(value) < _EDIT_VALUE_MAX:
                    mv.form_edit_value = value[:cur] + ch + value[cur:]
                    mv.form_edit_cursor = cur + 1
                return True
            if kind == "backspace":
                if cur > 0:
                    mv.form_edit_value = value[:cur - 1] + value[cur:]
                    mv.form_edit_cursor = cur - 1
                return True
            if kind == "delete":
                if cur < len(value):
                    mv.form_edit_value = value[:cur] + value[cur + 1:]
                return True
            if kind == "arrow_left":
                mv.form_edit_cursor = max(0, cur - 1)
                return True
            if kind == "arrow_right":
                mv.form_edit_cursor = min(len(value), cur + 1)
                return True
            if kind == "home":
                mv.form_edit_cursor = 0
                return True
            if kind == "end":
                mv.form_edit_cursor = len(value)
                return True
            if kind == "enter":
                _commit_field(mv)
                return True
            return True
        if mode == "select":
            # 提供商选择界面：Enter 确认 / Esc 取消；导航键放行 ListView
            if kind == "enter":
                _commit_select(mv)
                return True
            if kind == "escape":
                mv.edit_mode = "form"
                mv.edit_error = ""
                return True
            return False
        if mode == "confirm":
            # 删除确认：Enter 确认 / Esc 取消
            if kind == "enter":
                entry = entries[_sel_index(mv, total)] if total > 0 else None
                _delete_selected(mv, entry)
                return True
            if kind == "escape":
                mv.reset_edit_state()
                return True
            return True
        # 表单字段列表：Enter 编辑字段 / s 保存 / Esc 取消；导航放行 ListView
        if kind == "enter":
            keys = list(FIELD_KEYS)
            idx = max(0, min(int(getattr(mv, "form_selected", 0) or 0), len(keys) - 1))
            field = field_by_key(keys[idx]) or {}
            if field.get("kind") == "select":
                _start_select(mv, keys[idx])
            else:
                mv.edit_mode = "field"
                mv.form_edit_value = str((mv.form_values or {}).get(keys[idx], "") or "")
                mv.form_edit_cursor = len(mv.form_edit_value)
                mv.edit_error = ""
            return True
        if kind == "char" and ch == "?":
            # 表单内查快捷键（帮助面板覆盖表单；关闭后回到表单）
            mv.help_open = True
            mv.help_scroll = 0
            return True
        if kind == "char" and ch in ("s", "S"):
            _commit_form(mv)
            return True
        if kind == "escape":
            mv.reset_edit_state()
            return True
        return False

    # ── 浏览模式增强键 ──
    if kind == "char":
        if ch == "?":
            mv.help_open = True
            mv.help_scroll = 0
            mv.message = ""
            mv.edit_error = ""
            return True
        if ch == "/":
            mv.search_mode = True
            mv.search_query = getattr(mv, "search_pattern", "") or ""
            return True
        if ch in ("n", "N", "p") and (getattr(mv, "search_pattern", "") or ""):
            _jump_match(mv, 1 if ch == "n" else -1, view_map)
            return True
        if ch == "f":
            _cycle_filter(mv)
            return True
        if ch == "y":
            sel = _sel_index(mv, total)
            _copy_entry(mv, entries[sel] if sel >= 0 else None)
            return True
        if ch == "a":
            _start_form(mv, None)
            return True
        if ch == "c":
            sel = _sel_index(mv, total)
            entry = entries[sel] if sel >= 0 else None
            if not isinstance(entry, dict) or entry.get("kind") != "profile":
                mv.message = "仅模型档案可复制（按 a 新增）"
                return True
            _duplicate_selected(mv, entry)
            return True
        if ch == "r":
            _refresh_entries(mv)
            total_new = len(getattr(mv, "entries", None) or [])
            try:
                cur_sel = int(getattr(mv, "selected", 0) or 0)
            except (TypeError, ValueError):
                cur_sel = 0
            mv.selected = max(0, min(cur_sel, total_new - 1)) if total_new else 0
            mv.message = "已刷新模型列表"
            return True
        if ch == "e":
            sel = _sel_index(mv, total)
            entry = entries[sel] if sel >= 0 else None
            if not isinstance(entry, dict) or entry.get("kind") != "profile":
                mv.message = "仅模型档案可编辑（按 a 新增）"
                return True
            _start_form(mv, entry)
            return True
        if ch == "d":
            sel = _sel_index(mv, total)
            entry = entries[sel] if sel >= 0 else None
            if not isinstance(entry, dict) or entry.get("kind") != "profile":
                mv.message = "仅模型档案可删除"
                return True
            mv.editing = True
            mv.edit_mode = "confirm"
            mv.message = f"确认删除档案「{entry.get('name', '')}」？(Enter 确认 / Esc 取消)"
            return True
    # ── Esc/Ctrl+H 关闭视图 ──
    if is_modal_close_key(event):
        mv.try_set_final("cancel")
        return True
    # ── Enter 应用选中模型（写 applied，命令线程落地并同步会话） ──
    if kind == "enter" and total > 0:
        sel = _sel_index(mv, total)
        if sel >= 0:
            entry = entries[sel]
            mv.applied = entry
            mv.applied_seq = int(getattr(mv, "applied_seq", 0) or 0) + 1
            name = entry.get("name") or entry.get("model") or ""
            mv.message = f"正在切换到 {name}…"
        return True
    # 其余按键放行（ListView 导航）
    return False


def _handle_model_paste(mv, visible: bool, text: str) -> bool:
    """字段输入 / 搜索输入粘贴追加（单行化）。"""
    if not visible or mv is None:
        return False
    if not getattr(mv, "editing", False) and not getattr(mv, "search_mode", False):
        return False
    paste = (text or "").replace("\r", "").replace("\n", "")
    if not paste:
        return True
    if getattr(mv, "editing", False) and getattr(mv, "edit_mode", "") == "field":
        value = mv.form_edit_value
        remaining = _EDIT_VALUE_MAX - len(value)
        if remaining > 0:
            try:
                cur = int(getattr(mv, "form_edit_cursor", 0) or 0)
            except (TypeError, ValueError):
                cur = len(value)
            cur = max(0, min(cur, len(value)))
            insert = paste[:remaining]
            mv.form_edit_value = value[:cur] + insert + value[cur:]
            mv.form_edit_cursor = cur + len(insert)
        return True
    if getattr(mv, "search_mode", False):
        remaining = _SEARCH_QUERY_MAX - len(getattr(mv, "search_query", "") or "")
        if remaining > 0:
            mv.search_query = (mv.search_query or "") + paste[:remaining]
        return True
    return False


# ═══════════════════════════════════════════════════════════
# 行渲染器工厂
# ═══════════════════════════════════════════════════════════


def _make_entry_renderer(width: int, name_w: int, prov_w: int, model_w: int,
                         matched_ids: set | None = None,
                         cur_id: int | None = None):
    """模型列表行渲染器（ListView renderItem）。

    行结构：``▶ 名称  [provider]  模型名  接口主机  ◆key  ●当前``——模型名
    与接口地址同时展示（修复此前有 base_url 时模型名被地址覆盖的缺陷）；
    ``名称 == 模型名``（显示名留空回退模型名）时模型列留空去重。
    """

    def _render_entry(entry, i, is_sel):
        if not isinstance(entry, dict):
            return h(TEXT, {"children": "", "height": 1, "key": f"mv-{i}"})
        prefix = "\u25b6 " if is_sel else "  "
        runs = [StyledRun(prefix, _S_SEL_MARK if is_sel else None)]
        # ── 名称列 ──
        name = str(entry.get("name", "") or "")
        name_txt = _truncate_width(name, name_w)
        runs.append(StyledRun(name_txt, _S_NAME))
        pad = max(0, name_w - _disp_width(name_txt) + 1)
        if pad:
            runs.append(StyledRun(" " * pad, None))
        # ── 提供商列 ──
        prov = str(entry.get("provider", "") or "")
        prov_txt = _truncate_width(prov, prov_w)
        runs.append(StyledRun(f"[{prov_txt}]", _S_PROFILE))
        pad2 = max(0, prov_w - _disp_width(prov_txt) + 1)
        if pad2:
            runs.append(StyledRun(" " * pad2, None))
        # ── 模型名列（与名称重复时留空去重） ──
        target = str(entry.get("model", "") or "")
        model_txt = "" if target == name else _truncate_width(target, model_w)
        runs.append(StyledRun(model_txt, _S_MODEL))
        pad3 = max(0, model_w - _disp_width(model_txt) + 1)
        if pad3:
            runs.append(StyledRun(" " * pad3, None))
        # ── 接口主机列（次信息，灰色） ──
        url = str(entry.get("effective_base_url", entry.get("base_url", "")) or "")
        host = _url_host(url)
        if host:
            host_w = max(8, width - name_w - prov_w - model_w - 16) if width > 0 else 24
            runs.append(StyledRun(_truncate_width(host, host_w), _S_URL))
        # ── 徽标 ──
        if entry.get("api_key"):
            runs.append(StyledRun("  \u25c6key", _S_KEY))
        if entry.get("current"):
            runs.append(StyledRun("  \u25cf\u5f53\u524d", _S_CURRENT))
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
        return h(TEXT, {"styled": runs, "height": 1, "key": f"mv-{i}"})

    return _render_entry


def _make_field_renderer(width: int, label_w: int, mv):
    """表单字段行渲染器（ListView renderItem）。"""

    def _render_field(field, i, is_sel):
        prefix = "\u25b6 " if is_sel else "  "
        key = field["key"]
        label = str(field.get("label", key))
        runs = [StyledRun(prefix, _S_SEL_MARK if is_sel else None)]
        runs.append(StyledRun(_truncate_width(label, label_w), _S_FORM_LABEL))
        pad = max(0, label_w - _disp_width(label) + 1)
        if pad:
            runs.append(StyledRun(" " * pad, None))
        runs.append(StyledRun(": ", _S_HINT))
        value = _field_value_display(mv, key)
        if not value:
            value = "(\u7a7a)"
        runs.append(StyledRun(_truncate_width(value, max(8, width - label_w - 8)),
                              _S_KEY if field.get("sensitive") else _S_FORM_VALUE))
        if field.get("required"):
            runs.append(StyledRun(" *", _S_REQUIRED))
        if width > 0:
            runs = truncate_runs(runs, width)
        if is_sel:
            runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"mv-f-{i}"})

    return _render_field


def _make_select_renderer(width: int, val_w: int, descs: list):
    """选择界面（提供商候选）行渲染器（ListView renderItem）。"""

    def _render_select(opt, i, is_sel):
        prefix = "\u25b6 " if is_sel else "  "
        runs = [StyledRun(prefix, _S_SEL_MARK if is_sel else None)]
        val = _truncate_width(str(opt), max(12, val_w))
        runs.append(StyledRun(val, _S_MODEL))
        pad = max(0, val_w - _disp_width(val) + 1)
        if pad:
            runs.append(StyledRun(" " * pad, None))
        desc = str(descs[i]) if i < len(descs) else ""
        if desc:
            budget = max(6, width - val_w - 4) if width > 0 else 40
            runs.append(StyledRun(_truncate_width(desc, budget), _S_URL))
        if width > 0:
            runs = truncate_runs(runs, width)
        if is_sel:
            runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"mv-sel-{i}"})

    return _render_select


# ═══════════════════════════════════════════════════════════
# 组件
# ═══════════════════════════════════════════════════════════


def ModelView(props) -> object:
    """模型选择器视图组件（模态全屏视图；App 按 FULLSCREEN_VIEWS 整屏渲染）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    mv = getattr(model, "model_view", None)
    visible = bool(mv is not None and mv.visible and not mv.done)
    entries = list(getattr(mv, "entries", None) or []) if mv is not None else []
    help_open = bool(getattr(mv, "help_open", False)) if mv is not None else False
    search_mode = bool(getattr(mv, "search_mode", False)) if mv is not None else False
    pattern = (getattr(mv, "search_pattern", "") or "") if mv is not None else ""
    filter_on = bool(getattr(mv, "search_filter", False)) if mv is not None else False
    matches = list(getattr(mv, "search_matches", None) or []) if mv is not None else []
    editing = bool(getattr(mv, "editing", False)) if mv is not None else False
    edit_mode = getattr(mv, "edit_mode", "form") if mv is not None else "form"

    # ── 过滤视图（``f``：列表只显示匹配条目） ──
    filter_active = bool(filter_on and pattern and matches)
    if filter_active:
        view_entries = [entries[i] for i in matches if 0 <= i < len(entries)]
        view_map = {orig: pos for pos, orig in enumerate(matches)}
    else:
        view_entries = entries
        view_map = None
    total = len(view_entries)

    help_rows = use_memo(
        lambda: _help_rows(width if width > 0 else 80),
        (1 if help_open else 0, width),
    )

    use_input(
        lambda ev: _handle_model_event(
            mv, view_entries, ev, visible=visible, total=total,
            all_entries=entries, view_map=view_map,
            pane_vh=max(1, _viewport_rows() - 1),
        ),
        visible,
    )
    usePaste(
        lambda text: _handle_model_paste(mv, visible, text),
        {"isActive": bool(visible and mv and (mv.editing or mv.search_mode))},
    )
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    # ── 选中钳制 ──
    try:
        selected = max(0, min(int(mv.selected), total - 1)) if total else 0
    except (TypeError, ValueError):
        selected = 0
    if selected != mv.selected:
        mv.selected = selected

    # ── 栏宽分配 ──
    if width > 0 and total:
        name_w = min(24, max((_disp_width(str(e.get("name", ""))) for e in view_entries), default=10) + 2)
        prov_w = min(12, max((_disp_width(str(e.get("provider", ""))) for e in view_entries), default=6) + 2)
        model_w = min(26, max((_disp_width(str(e.get("model", ""))) for e in view_entries), default=10) + 2)
    else:
        name_w, prov_w, model_w = 20, 8, 18
    label_w = max((_disp_width(str(f.get("label", ""))) for f in PROFILE_FIELDS), default=8)

    # ── 状态行 / 详情行 / 视口 ──
    status_text = "" if (editing or search_mode) else _status_text(mv, filter_active)
    show_detail = bool(total > 0 and not editing and not help_open and not search_mode)
    extra_rows = (1 if search_mode else 0) + (1 if status_text else 0)
    if editing:
        extra_rows += 2 if edit_mode == "field" else 1
    if show_detail:
        extra_rows += 1
    vh = max(4, _viewport_rows() - extra_rows)
    list_h = max(1, vh - 1)

    # ── 行渲染器 ──
    _matched_ids = None
    _cur_entry_id = None
    if pattern and matches:
        _matched_ids = {id(entries[i]) for i in matches if 0 <= i < len(entries)}
        _idx_m = getattr(mv, "search_idx", -1)
        if 0 <= _idx_m < len(matches):
            _mi = matches[_idx_m]
            if 0 <= _mi < len(entries):
                _cur_entry_id = id(entries[_mi])
    render_entry = _make_entry_renderer(width, name_w, prov_w, model_w, _matched_ids, _cur_entry_id)
    render_field = _make_field_renderer(width, label_w, mv)

    def _on_navigate(idx: int) -> None:
        try:
            mv.selected = max(0, min(int(idx), total - 1)) if total else 0
        except (TypeError, ValueError):
            pass
        mv.message = ""

    def _on_field_navigate(idx: int) -> None:
        mv.form_selected = int(idx)

    def _on_select_navigate(idx: int) -> None:
        mv.form_select_index = int(idx)

    # ── 主区（列表 / 表单 / 帮助） ──
    if help_open:
        scroll_h = max(0, int(getattr(mv, "help_scroll", 0) or 0))
        panel_vh = max(1, vh)
        max_scroll = max(0, len(help_rows) - panel_vh)
        scroll_h = min(scroll_h, max_scroll)
        if scroll_h != getattr(mv, "help_scroll", 0):
            mv.help_scroll = scroll_h
        children = [
            h(TEXT, {"styled": runs, "height": 1, "key": f"mv-help-{i}"})
            for i, runs in enumerate(help_rows[scroll_h:scroll_h + panel_vh])
        ]
        ledger = h(Column, None, children)
    elif editing and edit_mode == "select":
        sel_opts = list(getattr(mv, "form_select_options", None) or [])
        sel_descs = list(getattr(mv, "form_select_desc", None) or [])
        sel_idx = max(0, min(int(getattr(mv, "form_select_index", 0) or 0), len(sel_opts) - 1)) if sel_opts else 0
        render_select = _make_select_renderer(width, 14, sel_descs)
        ledger = h(ListView, {
            "items": sel_opts,
            "height": list_h,
            "width": width if width > 0 else None,
            "cursor": sel_idx,
            "renderItem": render_select,
            "onNavigate": _on_select_navigate,
            "focus": visible and editing and edit_mode == "select",
        })
    elif editing and edit_mode in ("form", "field"):
        form_sel = max(0, min(int(getattr(mv, "form_selected", 0) or 0), len(PROFILE_FIELDS) - 1))
        ledger = h(ListView, {
            "items": list(PROFILE_FIELDS),
            "height": list_h,
            "width": width if width > 0 else None,
            "cursor": form_sel,
            "renderItem": render_field,
            "onNavigate": _on_field_navigate,
            "focus": visible and editing and edit_mode == "form",
        })
    elif editing and edit_mode == "confirm":
        ledger = h(TEXT, {
            "children": f"  {mv.message}",
            "style": _S_ERR, "textWrap": "truncate-end", "height": 1,
            "key": "mv-confirm",
        })
    elif total == 0:
        ledger = h(Column, None, [
            h(TEXT, {
                "styled": [
                    StyledRun("  \u25c7 \u6682\u65e0\u6a21\u578b\u6863\u6848", _S_EMPTY_TITLE),
                    StyledRun("   \uff08\u672a\u914d\u7f6e model_profiles\uff09", _S_HINT),
                ],
                "height": 1, "key": "mv-empty",
            }),
            h(TEXT, {
                "children": "    \u6309 a \u65b0\u589e\uff1a\u2460 \u9009\u63d0\u4f9b\u5546 \u2192 \u2461 \u586b\u6a21\u578b\u540d \u2192 \u2462 \u586b API \u5bc6\u94a5\uff08\u63a5\u53e3\u5730\u5740\u81ea\u52a8\u586b\u597d\uff09",
                "style": _S_HINT, "textWrap": "truncate-end", "height": 1,
                "key": "mv-empty-hint",
            }),
        ])
    else:
        ledger = h(ListView, {
            "items": view_entries,
            "height": list_h,
            "width": width if width > 0 else None,
            "cursor": selected if total else 0,
            "renderItem": render_entry,
            "onNavigate": _on_navigate,
            "focus": visible and not editing and not help_open,
        })

    # ── 头部 ──
    if search_mode:
        header_hint = "  输入搜索词 \u00b7 Enter \u6267\u884c \u00b7 Esc \u53d6\u6d88"
    elif help_open:
        header_hint = "  帮助面板 \u00b7 ? / q / Esc \u5173\u95ed"
    elif editing and edit_mode == "field":
        header_hint = "  字段输入 \u00b7 \u2190\u2192 \u5149\u6807 \u00b7 Home/End \u9996\u672b \u00b7 Ctrl+U \u6e05\u7a7a \u00b7 Enter \u786e\u8ba4 \u00b7 Esc \u53d6\u6d88"
    elif editing and edit_mode == "select":
        header_hint = "  选择提供商（接口地址将自动填好）\u00b7 \u2191\u2193/jk \u9009\u62e9 \u00b7 Enter \u786e\u8ba4 \u00b7 Esc \u53d6\u6d88"
    elif editing and edit_mode == "confirm":
        header_hint = "  删除确认"
    elif editing:
        title = "新增模型档案" if getattr(mv, "form_is_new", False) else "编辑模型档案"
        header_hint = f"  {title} \u00b7 \u2191\u2193/jk \u9009\u62e9\u5b57\u6bb5 \u00b7 Enter \u7f16\u8f91 \u00b7 s \u4fdd\u5b58 \u00b7 Esc \u53d6\u6d88"
    else:
        header_hint = "  \u2191\u2193/jk \u9009\u62e9 \u00b7 Enter \u5e94\u7528 \u00b7 a \u65b0\u589e \u00b7 e \u7f16\u8f91 \u00b7 d \u5220\u9664 \u00b7 / \u641c\u7d22 \u00b7 ? \u5e2e\u52a9"
    count_seg = f" \u00b7 {len(entries)} \u6a21\u578b"
    if filter_active:
        count_seg += f"/{len(entries)}"
    if pattern:
        count_seg += f" \u00b7 \u5339\u914d {len(matches)}"
    header_runs = [
        StyledRun("\u258d\u25c8 \u6a21\u578b\u9009\u62e9\u5668", _S_TITLE),
        StyledRun(count_seg, _S_HINT),
        StyledRun(header_hint, _S_HINT),
    ]
    if width > 0:
        header_runs = truncate_runs_ellipsis(header_runs, width)
        used = sum(getattr(r, "width", 1) for r in header_runs)
        pad = width - used
        if pad > 0:
            header_runs.append(StyledRun("\u2500" * pad, _S_SEP))

    # ── 底部行 ──
    bottom_rows: list = []
    if help_open:
        bottom_rows.append(h(TEXT, {
            "children": "  \u2191\u2193/jk \u6eda\u52a8 \u00b7 ? / q / Esc \u5173\u95ed\u5e2e\u52a9",
            "style": _S_HINT, "height": 1, "key": "mv-help-hint",
        }))
    elif editing and edit_mode == "field":
        keys = list(FIELD_KEYS)
        idx = max(0, min(int(getattr(mv, "form_selected", 0) or 0), len(keys) - 1))
        field = field_by_key(keys[idx]) or {}
        raw = str(mv.form_edit_value or "")
        try:
            cur = int(getattr(mv, "form_edit_cursor", len(raw)) or 0)
        except (TypeError, ValueError):
            cur = len(raw)
        cur = max(0, min(cur, len(raw)))
        text = "*" * len(raw) if field.get("sensitive") else raw
        label = field.get("label", keys[idx])
        before, after = text[:cur], text[cur:]
        cur_ch = after[0] if after else " "
        field_runs = [
            StyledRun(f"  \u258d \u270e {label}: ", _S_EDIT),
            StyledRun(before, _S_FORM_VALUE),
            StyledRun(cur_ch, _S_CURSOR),
            StyledRun(after[1:], _S_FORM_VALUE),
        ]
        if width > 0:
            field_runs = truncate_runs(field_runs, width)
        bottom_rows.append(h(TEXT, {
            "styled": field_runs,
            "height": 1, "key": "mv-field",
        }))
        hint = field.get("hint", "") or "Enter 确认 \u00b7 Esc 取消"
        bottom_rows.append(h(TEXT, {
            "children": f"  {hint}", "style": _S_HINT,
            "textWrap": "truncate-end", "height": 1, "key": "mv-field-hint",
        }))
    elif editing and edit_mode == "select":
        bottom_rows.append(h(TEXT, {
            "children": "  \u2191\u2193/jk 选择提供商 \u00b7 Enter 确认 \u00b7 Esc 取消",
            "style": _S_HINT, "textWrap": "truncate-end", "height": 1,
            "key": "mv-select-hint",
        }))
    elif editing and edit_mode == "confirm":
        if mv.edit_error:
            bottom_rows.append(h(TEXT, {
                "children": f"  \u2716 {mv.edit_error}", "style": _S_ERR,
                "textWrap": "truncate-end", "height": 1, "key": "mv-confirm-err",
            }))
        else:
            bottom_rows.append(h(TEXT, {
                "children": "  Enter 确认删除 \u00b7 Esc 取消",
                "style": _S_HINT, "height": 1, "key": "mv-confirm-hint",
            }))
    elif editing:
        if mv.edit_error:
            bottom_rows.append(h(TEXT, {
                "children": f"  \u2716 {mv.edit_error}", "style": _S_ERR,
                "textWrap": "truncate-end", "height": 1, "key": "mv-form-err",
            }))
        else:
            keys = list(FIELD_KEYS)
            fidx = max(0, min(int(getattr(mv, "form_selected", 0) or 0), len(keys) - 1))
            hint = str((field_by_key(keys[fidx]) or {}).get("hint", "") or "")
            text = f"  {hint}  \u00b7  s 保存  \u00b7  Esc 取消" if hint else "  Enter 编辑字段 \u00b7 s 保存 \u00b7 Esc 取消"
            bottom_rows.append(h(TEXT, {
                "children": text,
                "style": _S_HINT, "height": 1, "key": "mv-form-hint",
            }))
    elif status_text:
        disp = "  " + status_text
        if width > 0:
            disp = _truncate_width(disp, width)
        bottom_rows.append(h(TEXT, {
            "children": disp, "style": _S_STATUS,
            "textWrap": "truncate-end", "height": 1, "key": "mv-status",
        }))
    else:
        bottom_rows.append(h(TEXT, {
            "children": "  \u2191\u2193/jk \u9009\u62e9 \u00b7 Enter \u5e94\u7528 \u00b7 a \u65b0\u589e \u00b7 c \u590d\u5236 \u00b7 e \u7f16\u8f91 \u00b7 d \u5220\u9664 \u00b7 r \u5237\u65b0 \u00b7 y \u590d\u5236\u4fe1\u606f \u00b7 ? \u5e2e\u52a9 \u00b7 Esc \u5173\u95ed",
            "style": _S_HINT,
            "textWrap": "truncate-end", "height": 1, "key": "mv-hint",
        }))
    if search_mode:
        q = getattr(mv, "search_query", "") or ""
        if width > 0:
            q = q[: max(0, width - 2)]
        bottom_rows.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_EDIT,
            "textWrap": "truncate-end", "height": 1, "key": "mv-search",
        }))

    body = [h(TEXT, {"styled": header_runs, "height": 1, "key": "mv-header"})]
    if show_detail:
        sel_entry = view_entries[selected] if 0 <= selected < total else None
        detail_runs = _detail_runs(sel_entry, width)
        if detail_runs:
            body.append(h(TEXT, {"styled": detail_runs, "height": 1, "key": "mv-detail"}))
    body.append(h(Row, None, [ledger]))
    body.append(h(Column, None, bottom_rows))
    return h(Column, None, body)

"""「/ 命令输入补全」完善回归测试（2026-10-20）。

覆盖用户需求「完善 / 命令的输入补全」的四个方向：

  1. **命令名匹配增强**——忽略大小写 + 子序列模糊匹配（``/modl`` → ``/model``），
     精确匹配优先；
  2. **命令候选排序/展示**——仅输入 ``/`` 时保持注册表顺序（``/help`` 优先 +
     字母序）而非名称长度序；描述列展示「分组 · 描述 · 用法」；
  3. **扩展子命令补全**——``/title`` / ``/skill`` / ``/sandbox`` / ``/context`` /
     ``/loop`` 子命令与参数、``/export`` 路径；
  4. **``/config`` 值补全**——``/config set <键> <值>`` 的枚举 / bool 候选。

同时锁定既有行为（``/model`` 参数补全、``/config show`` 无候选、``/export ``
空前缀不列目录）不回归。
"""

from __future__ import annotations

from src.tui._completion import _apply_completion, _last_word_of

COMMANDS = [
    "/help", "/changes", "/clear", "/config", "/cost", "/edit", "/export",
    "/load", "/model", "/models", "/sandbox", "/skill", "/theme", "/title",
]


def _engine(commands=None):
    from src.tui._completion_engine import CompletionEngine

    return CompletionEngine(commands_source=lambda: list(commands or COMMANDS))


# ═══════════════════════════════════════════════════════════
# 1. 命令名匹配增强
# ═══════════════════════════════════════════════════════════

def test_slash_only_keeps_registry_order():
    """仅输入 ``/``：按注册表顺序（/help 优先 + 字母序），不按名称长度排序。

    修复前 ``_ranked`` 把 ``/`` 视为前缀匹配、按名称长度升序——弹窗顺序为
    ``/pin /cost /edit ...``，与 ``/help`` 列表不一致。
    """
    eng = _engine()
    texts = [i.text for i in eng.complete("/")]
    assert texts == COMMANDS
    # 明确不是长度排序（/cost、/edit 均比 /changes 短，但必须排在后面）
    assert texts.index("/changes") < texts.index("/cost")


def test_command_prefix_keeps_alpha_and_length_order():
    """前缀匹配：短名优先、同级字母序（/model 在 /models 前）。"""
    eng = _engine()
    assert [i.text for i in eng.complete("/mode")] == ["/model", "/models"]


def test_command_match_ignores_case():
    """命令名匹配忽略大小写（``/CON`` → ``/config``）。"""
    eng = _engine()
    items = eng.complete("/CON")
    assert [i.text for i in items] == ["/config"]
    # 输入完整大写命令 → 进入参数补全（不再列命令）
    eng.register_source("models", lambda: ["m-a"])
    assert [i.text for i in eng.complete("/MODEL")] == ["/model m-a"]


def test_command_fuzzy_subsequence():
    """子序列模糊匹配：``/hlp`` → ``/help``、``/modl`` → ``/model``（排第一）。"""
    eng = _engine()
    assert [i.text for i in eng.complete("/hlp")] == ["/help"]
    texts = [i.text for i in eng.complete("/modl")]
    assert texts[0] == "/model"
    assert "/models" in texts  # 同为子序列命中，排在其后（名称更长）


def test_command_exact_match_ranked_first():
    """精确匹配排在前缀/子串/子序列命中之前。"""
    from src.tui._completion_engine import _rank_fuzzy

    assert _rank_fuzzy(["/mo", "/model", "/modelx"], "/model")[0] == "/model"


def test_command_fuzzy_requires_two_chars():
    """单字符查询不做子序列扩展（与子串等价，避免噪音）。"""
    from src.tui._completion_engine import _rank_fuzzy

    # 单字符：只有前缀/子串命中，不含子序列
    ranked = _rank_fuzzy(["/alpha", "/beta"], "l")
    assert ranked == ["/alpha"]


# ═══════════════════════════════════════════════════════════
# 2. 命令候选描述（分组 / 描述 / 用法）
# ═══════════════════════════════════════════════════════════

def test_command_desc_has_group_desc_usage():
    """命令描述列：「分组 · 描述 · 用法 /cmd <usage>」。"""
    eng = _engine(["/title"])
    eng.register_source("command_meta", lambda: {
        "/title": {
            "desc": "会话标题（查看来源 / 重新生成 / 重命名钉住）",
            "usage": "[show|refresh|set <文本>|auto]",
            "group": "session",
            "group_label": "会话",
        },
    })
    items = eng.complete("/tit")
    assert items[0].desc == (
        "会话 · 会话标题（查看来源 / 重新生成 / 重命名钉住）"
        " · 用法 /title [show|refresh|set <文本>|auto]"
    )


def test_command_desc_falls_back_to_registry_help(monkeypatch):
    """元数据缺失时回退注册表 help 文本（描述不丢失）。"""
    from src.tui import _completion_engine as ce

    eng = ce.CompletionEngine(commands_source=lambda: ["/help"])
    eng.register_source("command_meta", lambda: {})
    monkeypatch.setattr(ce, "_get_command_help", lambda: (lambda name: "显示帮助"))
    items = eng.complete("/he")
    assert items[0].desc == "显示帮助"


def test_fetch_command_meta_reads_plugin_registry(monkeypatch):
    """真实元数据源：命令插件注册表 → 分组标签 / 用法 / 描述。

    以假注册表注入（不向全局命令注册表注册命令——避免跨测试污染：
    全局表非空会抑制 ``get_plugin_registry`` 的兜底自动注册，影响其它
    用例的顺序无关性）。
    """
    from types import SimpleNamespace

    import src.core.commands.base as base
    from src.tui._completion_engine import CompletionEngine

    plugins = [
        SimpleNamespace(meta=SimpleNamespace(
            name="title", group="session",
            description="会话标题（查看来源 / 重新生成 / 重命名钉住）",
            usage="[show|refresh|set <文本>|auto]",
        )),
        SimpleNamespace(meta=SimpleNamespace(
            name="skill", group="ui", description="技能管理",
            usage="[list|install <仓库>|update|remove|info|refresh]",
        )),
    ]
    monkeypatch.setattr(
        base, "get_plugin_registry",
        lambda: SimpleNamespace(list=lambda: plugins),
    )
    meta = CompletionEngine._fetch_command_meta()
    title = meta.get("/title") or {}
    assert title.get("group") == "session"
    assert title.get("group_label") == "会话"
    assert "show" in (title.get("usage") or "")
    skill = meta.get("/skill") or {}
    assert "install" in (skill.get("usage") or "")


# ═══════════════════════════════════════════════════════════
# 3. 扩展子命令补全
# ═══════════════════════════════════════════════════════════

def test_title_subcommands_and_apply():
    """/title 子命令补全 + 词边界应用（保留命令前缀）。"""
    eng = _engine(["/title"])
    assert [i.text for i in eng.complete("/title")] == [
        "/title show", "/title refresh", "/title set", "/title auto",
    ]
    assert [i.text for i in eng.complete("/title re")] == ["refresh"]
    # 子命令已完整 → 后续为自由文本，无候选
    assert eng.complete("/title set") == []

    repl = eng.complete("/title re")[0]
    applied = _apply_completion("/title re", repl.text, repl.start_pos, "re")
    assert applied == "/title refresh"


def test_sandbox_subcommands():
    """/sandbox 子命令补全（history / records）。"""
    eng = _engine(["/sandbox"])
    assert [i.text for i in eng.complete("/sandbox")] == [
        "/sandbox history", "/sandbox records",
    ]
    assert [i.text for i in eng.complete("/sandbox rec")] == ["records"]


def test_skill_subcommands():
    """/skill 子命令补全（list / install / update / remove / info / refresh / help）。"""
    eng = _engine(["/skill"])
    texts = [i.text for i in eng.complete("/skill")]
    assert "/skill list" in texts and "/skill install" in texts
    assert "/skill update" in texts and "/skill remove" in texts
    assert "/skill info" in texts and "/skill refresh" in texts
    assert [i.text for i in eng.complete("/skill ins")] == ["install"]


def test_skill_list_installed():
    """/skill list → 二级参数 installed。"""
    eng = _engine(["/skill"])
    items = eng.complete("/skill list ")
    assert [i.text for i in items] == ["list installed"]
    applied = _apply_completion("/skill list ", items[0].text, items[0].start_pos, "list")
    assert applied == "/skill list installed"
    assert [i.text for i in eng.complete("/skill list in")] == ["installed"]
    # 已完整输入 → 无候选（应用后不再重复弹窗）
    assert eng.complete("/skill list installed") == []


def test_skill_info_completes_skill_names():
    """/skill info|remove 补全技能名（技能注册表数据源）。"""
    eng = _engine(["/skill"])
    eng.register_source("skills", lambda: [
        ("build-helper", "构建辅助"), ("reviewer", "代码审查"),
    ])
    items = eng.complete("/skill info ")
    assert [i.text for i in items] == ["info build-helper", "info reviewer"]
    assert [i.desc for i in items] == ["构建辅助", "代码审查"]
    applied = _apply_completion(
        "/skill info ", items[0].text, items[0].start_pos, "info",
    )
    assert applied == "/skill info build-helper"

    # 已输入技能名前缀 → 替换最后词
    assert [i.text for i in eng.complete("/skill info bu")] == ["build-helper"]
    # 技能名已完整 → 无候选（应用后不再重复弹窗）
    assert eng.complete("/skill info build-helper") == []
    # install / update 为 <owner/repo> 自由文本，无候选
    assert eng.complete("/skill install ") == []


def test_context_token_choices_and_current(monkeypatch):
    """/context 容量补全 + 当前值标注。"""
    from src.tui import _completion_engine as ce

    eng = _engine(["/context"])
    monkeypatch.setattr(ce, "_current_config_value", lambda key: "256k")
    items = eng.complete("/context")
    texts = [i.text for i in items]
    assert "/context 128k" in texts and "/context 256k" in texts
    assert "/context 512k" in texts and "/context 1m" in texts
    by_text = {i.text: i.desc for i in items}
    assert by_text["/context 256k"] == "当前"


def test_context_current_value_appended_when_unknown(monkeypatch):
    """当前值不在常用表中时按 k 归一化补入候选（desc=当前）。"""
    from src.tui import _completion_engine as ce

    eng = _engine(["/context"])
    monkeypatch.setattr(ce, "_current_config_value", lambda key: "300000")
    by_text = {i.text: i.desc for i in eng.complete("/context")}
    assert by_text["/context 300k"] == "当前"


def test_context_reads_lowercase_rc_key(monkeypatch):
    """当前值按 RC 键名（小写 model_context_tokens）读取。

    大写 ``MODEL_CONTEXT_TOKENS`` 是 CONFIG_KEYS 写回键，rc 字典里不存在
    （``config.get`` 返回 None）——修复前用错键导致「当前」永不标注。
    """
    from src.tui import _completion_engine as ce

    seen: list[str] = []

    def _fake_get(key: str) -> str:
        seen.append(key)
        return "1000000"

    monkeypatch.setattr(ce, "_current_config_value", _fake_get)
    eng = _engine(["/context"])
    by_text = {i.text: i.desc for i in eng.complete("/context")}
    assert seen == ["model_context_tokens"]
    assert by_text["/context 1m"] == "当前"


def test_context_current_tokens_use_km_label(monkeypatch):
    """配置整数 token 数（1000000）归一化为 ``1m`` 并标注当前。"""
    from src.tui._completion_engine import _format_context_tokens
    from src.tui import _completion_engine as ce

    assert _format_context_tokens("1000000") == "1m"
    assert _format_context_tokens("256000") == "256k"
    assert _format_context_tokens("262144") == "262144"
    assert _format_context_tokens("") == ""

    eng = _engine(["/context"])
    monkeypatch.setattr(ce, "_current_config_value", lambda key: "1000000")
    by_text = {i.text: i.desc for i in eng.complete("/context")}
    assert by_text["/context 1m"] == "当前"


def test_loop_count_choices():
    """/loop 次数候选 + 提词自由文本。"""
    eng = _engine(["/loop"])
    texts = [i.text for i in eng.complete("/loop")]
    assert "/loop 1" in texts and "/loop 3" in texts and "/loop 10" in texts
    assert eng.complete("/loop 3") == []       # 次数已完整 → 提词自由文本
    assert eng.complete("/loop 3 请") == []


def test_export_path_completion(tmp_path, monkeypatch):
    """/export <路径> 复用路径补全；空前缀不列目录。"""
    import os

    monkeypatch.chdir(tmp_path)
    (tmp_path / "report.md").write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()

    eng = _engine(["/export"])
    assert eng.complete("/export ") == []
    items = eng.complete("/export rep")
    assert [i.text for i in items] == ["report.md"]
    assert items[0].item_type == "file"
    applied = _apply_completion("/export rep", items[0].text, items[0].start_pos, "rep")
    assert applied == "/export report.md"
    # 目录候选带尾随分隔符
    dirs = eng.complete("/export sr")
    assert [i.text for i in dirs] == ["src" + os.sep]


# ═══════════════════════════════════════════════════════════
# 4. /config 值补全
# ═══════════════════════════════════════════════════════════

def _config_engine():
    eng = _engine(["/config"])
    eng.register_source("config_keys", lambda: [
        "theme", "enable_notifications", "temperature",
    ])
    eng.register_source("config_values", lambda: {
        "theme": {
            "options": [("dark", "暗色主题"), ("light", "亮色主题")],
            "current": "dark",
        },
        "enable_notifications": {
            "options": [("true", "开启"), ("false", "关闭")],
            "current": "true",
        },
    })
    return eng


def test_config_set_bool_values():
    """/config set <bool 键> → true/false 候选 + 当前值标注。"""
    eng = _config_engine()
    items = eng.complete("/config set enable_notifications ")
    by_text = {i.text: i.desc for i in items}
    assert set(by_text) == {
        "enable_notifications true", "enable_notifications false",
    }
    assert by_text["enable_notifications true"] == "当前"
    applied = _apply_completion(
        "/config set enable_notifications ",
        "enable_notifications false", -len("enable_notifications"),
        "enable_notifications",
    )
    assert applied == "/config set enable_notifications false"


def test_config_set_enum_value_prefix():
    """/config set <枚举键> <前缀> → 过滤候选并替换最后词。"""
    eng = _config_engine()
    assert [i.text for i in eng.complete("/config set theme l")] == ["light"]
    applied = _apply_completion("/config set theme l", "light", -1, "l")
    assert applied == "/config set theme light"
    # 值已完整 → 无候选（应用后不再重复弹窗）
    assert eng.complete("/config set theme light") == []


def test_config_set_key_prefix_still_completes_keys():
    """键未完整输入时仍按前缀补全键名（不被值补全劫持）。"""
    eng = _config_engine()
    texts = [i.text for i in eng.complete("/config set temp")]
    assert "temperature" in texts


def test_config_set_free_text_key_has_no_value_candidates():
    """无候选值的键（temperature 数值输入）→ 值补全为空。"""
    eng = _config_engine()
    assert eng.complete("/config set temperature ") == []
    assert eng.complete("/config set temperature 0.7") == []


def test_config_show_still_has_no_params():
    """既有行为：/config show 无候选。"""
    eng = _config_engine()
    assert eng.complete("/config show") == []


def test_fetch_config_values_bool_and_enum():
    """真实配置数据源：bool 键生成 true/false、THEME 枚举来自表现层数据表。"""
    from src.tui._completion_engine import CompletionEngine

    values = CompletionEngine._fetch_config_values()
    notify = values.get("enable_notifications") or {}
    assert [v for v, _d in notify.get("options") or []] == ["true", "false"]
    theme = values.get("theme") or {}
    theme_values = [v for v, _d in theme.get("options") or []]
    assert "dark" in theme_values and "light" in theme_values


# ═══════════════════════════════════════════════════════════
# 5. 既有行为不回归
# ═══════════════════════════════════════════════════════════

def test_model_param_completion_unchanged():
    """/model 参数补全保持既有语义（完整替换串 + 当前值标注）。"""
    eng = _engine(["/model"])
    eng.register_source("models", lambda: ["m-a", "m-b"])
    items = eng.complete("/model")
    assert [i.text for i in items] == ["/model m-a", "/model m-b"]
    assert all(i.item_type == "param" for i in items)


def test_last_word_of_trailing_space_matches_apply_semantics():
    """尾随空格时 orig_prefix 取前一个非空词（与引擎替换语义对齐）。"""
    assert _last_word_of("/title ") == "/title"
    assert _last_word_of("/skill info ") == "info"

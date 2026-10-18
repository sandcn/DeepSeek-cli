"""空模式提词：Git 确认、安全红线、元文件作用域、测试惯例。

对应本轮提示词修订（用户要求 1/2/4/7）：
- Git 提交/推送为单选，超时/无响应一律按「同意」处理（用户仍可取消）；
- 补回用户指令不可覆盖的安全红线；
- 元文件限制只作用于项目根目录，不误伤 ``.chat/``；
- 测试规则按语言/项目惯例，并允许记录未执行原因（不假称已运行）。
"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, prompt_bullets

_META_FILES = (
    "global.md",
    "main.md",
    "plan.md",
    "think.md",
    "map.md",
    "review.md",
    "execute.md",
)


def _bullet(prefix: str) -> str:
    """返回以 ``prefix`` 开头的提词条目行（缺失即失败）。"""
    for line in prompt_bullets():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"提示词缺少条目: {prefix}")


def test_git_rule_defaults_to_approving():
    """Git 条目：单选、默认「同意」，超时/无响应按同意推进，用户仍可取消。"""
    rule = _bullet("- **Git**")
    assert_contains(
        rule,
        "multi_select=False",
        "default_options",
        "置为「同意」",
        "用户可以取消",
        "超时",
        "无响应",
        "按「同意」处理",
        "提交/推送",
    )
    assert "不得包含同意项" not in rule


def test_security_redlines_cover_sensitive_data_and_scope():
    """安全条：用户指令不可覆盖的红线必须完整。"""
    rule = _bullet("- **安全红线")
    assert_contains(
        rule,
        "一票否决",
        "密钥/密码/token/PII",
        "远程命令执行",
        "越权",
        "网络扫描/渗透",
        "环境变量",
    )


def test_meta_file_rule_is_scoped_to_project_root():
    """元文件条：只限制根目录运行时元文件，``.chat/`` 不受影响。"""
    rule = _bullet("- **元文件与变更日志**")
    assert_contains(rule, "项目根目录", ".chat/", "不得主动读取", "系统已自动注入")
    assert_contains(rule, *(_META_FILES))
    assert_contains(rule, "`.chat/plan/*.md` 可正常读写")


def test_test_rule_uses_project_conventions_and_allows_documented_skip():
    """测试条：按项目惯例落测试，无入口时须记录原因。"""
    rule = _bullet("- **测试（强制）**")
    assert_contains(rule, "语言惯例", "无运行入口", "写明未执行项与原因", "不得假称已运行")
    assert "必须在 `./tests` 目录" not in rule

"""空模式提词：Git 确认、安全红线、元文件作用域、测试惯例。

对应本轮提示词修订（用户要求 1/2/4/7）：
- Git 提交/推送为单选，超时/无响应一律按「取消/不提交」处理（用户仍可取消）；
- 补回用户指令不可覆盖的安全红线；
- 元文件限制只作用于项目根目录，不误伤 ``.chat/``；
- 测试规则按语言/项目惯例，并允许记录未执行原因（不假称已运行）。
"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt

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
    """返回以 ``prefix`` 开头的条目及其全部缩进子项（缺失即失败）。"""
    lines = read_prompt().splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)
    if start is None:
        raise AssertionError(f"提示词缺少条目: {prefix}")
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.startswith("  - "):
            break
        block.append(line)
    return "\n".join(block)


def test_git_rule_defaults_to_declining():
    """Git 条目：单选、默认「取消/不提交」，超时/无响应按取消处理，用户仍可取消。"""
    rule = _bullet("- **Git**")
    assert_contains(
        rule,
        "multi_select=False",
        "default_options",
        "置为「取消/不提交」",
        "用户取消任一环节时",
        "超时",
        "无响应",
        "按「取消/不提交」处理",
        "直接提交或推送",
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
    assert_contains(
        rule,
        "`.chat/plan/*.md` 不受本条元文件限制",
        "写入仍须满足第零条的明确授权",
    )


def test_test_rule_uses_project_conventions_and_allows_documented_skip():
    """测试条：按项目惯例落测试，无入口时须记录原因。"""
    rule = _bullet("- **测试（强制）**")
    assert_contains(rule, "语言惯例", "无运行入口", "写明未执行项与原因", "不得假称已运行")
    assert "必须在 `./tests` 目录" not in rule

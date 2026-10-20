"""空模式提词：优先级、豁免、例外与工具边界的一致性守卫。

固化 2026-10 的一致性修复：优先级阶梯唯一、豁免与适用条件分离、
完成标准统一引用例外举证、授权边界唯一、回滚可执行、工具事实与并发边界正确。
"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, prompt_bullets, read_prompt


def test_priority_ladder_is_unique_and_ordered():
    text = read_prompt()
    assert_contains(
        text,
        "唯一优先级阶梯",
        "安全红线 → 第零条",
        "实现完整性红线 → 工具与验证四条硬红线",
        "第 2 层级（授权闸门）",
        "第 1 层级是安全红线",
    )
    assert "第 1 层级（授权闸门）" not in text
    assert "与安全红线同级" not in text


def test_high_priority_is_scope_effect_not_ladder_level():
    text = read_prompt()
    assert_contains(
        text,
        "「最高优先级」是效力表述，不是层级归属",
        "冲突裁决只按第 1 条的阶梯",
        "裁决层级次于安全红线",
    )


def test_exemption_separated_from_applicability():
    text = read_prompt()
    assert_contains(
        text,
        "豁免与适用条件必须区分",
        "不是豁免，也不属于违规",
        "唯一裁量豁免",
        "三查阶段对本次授权范围内缺陷的修复-复核迭代",
        "未获授权只能如实报告",
    )
    assert "④用户明确授权范围内的改动" not in text
    assert "具名豁免" not in text


def test_trigger_conditions_and_completion_exception_are_unified():
    text = read_prompt()
    assert_contains(
        text,
        "本轮动过代码、配置、脚本、测试、样式或资源文件",
        "只改配置、样式或资源而未动代码时，仍属于已产生实操类文件改动",
        "已按「例外必须举证到可核查」与「测试」条款举证的环境限制",
        "不视为未验证项",
    )


def test_authorization_boundary_is_single_valued():
    text = read_prompt()
    assert_contains(
        text,
        "判定标准「不修就无法完成用户明确要求",
        "若无法判断某处属于「签名机械适配」还是「行为变更/缺陷修复」",
        "按「有歧义必须问」处理",
        "发现与本任务直接相关、且不修就无法完成用户明确要求的缺陷",
    )


def test_rollback_is_executable():
    text = read_prompt()
    assert_contains(
        text,
        "先用 `read` 完整读取目标文件并在上下文中留存原文",
        "文件过大无法完整留存时，先向用户说明并取得同意后再改动",
        "未跟踪文件无法从 git 取得原文时",
        "新建的目录连同其中文件用 `rm` 递归删除",
        "确实无法恢复原文时",
    )


def test_tool_facts_and_shared_gui_state():
    text = read_prompt()
    assert_contains(
        text,
        "截图用 `op=screenshot`",
        "`op=windows` 仅用于列出窗口，不能截图",
        "`bash_opt` 的后台运行、交互、截图、GUI 输入属于内部工具操作，不是 shell 命令",
        "共享同一 GUI 前台焦点/屏幕/剪贴板/输入注入目标的任务",
    )


def test_git_status_is_consistently_classified():
    text = read_prompt()
    assert_contains(
        text,
        "`git diff`/`git log`/`git show`",
        "会写 `.git/index.lock` 的 `git status` 归入下条串行情形",
        "`git status` 会短暂写 `.git/index.lock`",
        "按共享状态串行",
    )


def test_decision_defaults_are_safe_and_destructive_is_single_choice():
    text = read_prompt()
    assert_contains(
        text,
        "方向性选择默认多选",
        "推荐项 / 中性项 / 暂不执行",
        "不得把互斥方案同时设为默认",
        "不得按「全选」回退",
        "破坏性、不可逆、远程写操作、扩大范围等需要取舍的问题改为单选",
    )


def test_git_defaults_to_decline():
    lines = read_prompt().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("- **Git**"))
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.startswith("  - "):
            break
        block.append(line)
    assert_contains("\n".join(block), "置为「取消/不提交」", "按「取消/不提交」处理")


def test_first_read_completes_before_action():
    text = read_prompt()
    assert_contains(
        text,
        "首次读取文件时完整读取",
        "必须在动手前读完与任务相关的全部内容",
        "若工具或运行环境限制单次读取长度，以其限制为准",
    )


def test_cross_module_rule_is_inside_three_check_block_and_requires_authorization():
    lines = read_prompt().splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("- **修改后 bash_opt 实操三查红线")),
        None,
    )
    assert start is not None, "缺少「修改后 bash_opt 实操三查红线」条目"
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.startswith("  - "):
            break
        block.append(line)
    joined = "\n".join(block)
    assert_contains(joined, "跨模块缺陷必须按第零条处理", "申请扩大授权", "获得授权后必须立即当场修复")
    assert "与安全红线同级" not in joined


def test_no_dangling_old_wording():
    text = read_prompt()
    for stale in ("除「违规回滚」外无豁免", "与安全红线同级", "第 1 层级（授权闸门）", "具名豁免"):
        assert stale not in text, f"残留旧口径: {stale}"

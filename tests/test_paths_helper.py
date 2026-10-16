"""测试路径助手（``tests/_paths``）守卫。

背景：大量用例用 ``Path(__file__).resolve().parent.parent`` / ``parents[1]``
定位仓库根，隐含「测试文件都在 tests/ 第一层」的假设——一旦按被测模块移入
子目录，这些引用会静默指向错误位置。``tests/_paths`` 改为基于标记文件向上
查找，用例无论放在哪一层都成立。

本测试固化：

1. ``repo_root()`` 从任意深度起点都能定位到含 ``pyproject.toml`` 的目录；
2. 从仓库根、``tests/``、``tests/`` 子目录、任意临时目录出发结果一致；
3. ``prompts_dir`` / ``src_dir`` / ``tests_dir`` 指向真实存在的目录。

注：以 ``from tests._paths import tests_dir`` 形式导入会把 ``tests_dir`` 当作
``test*`` 收集成用例（pytest 匹配 ``test*``），故统一经模块属性访问。
"""

from __future__ import annotations

from pathlib import Path

from tests import _paths


def test_repo_root_from_any_depth(tmp_path):
    expected = _paths.repo_root()
    # 起点为文件 / 目录 / 深达 3 层的临时子目录，结果都应一致
    assert _paths.repo_root(Path(__file__)) == expected
    assert _paths.repo_root(Path(__file__).parent) == expected
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert _paths.repo_root(deep) == expected


def test_repo_root_marker_is_pyproject():
    root = _paths.repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "src").is_dir()


def test_derived_dirs_exist():
    assert _paths.prompts_dir().is_dir()
    assert _paths.src_dir().is_dir()
    assert _paths.tests_dir().is_dir()
    assert _paths.prompts_dir().parent == _paths.repo_root()


def test_prompts_dir_contains_expected_files():
    for name in (
        "prompts_export_main.md",
        "prompts_export_main_empty.md",
        "prompts_export_main_simple.md",
        "prompts_export_map.md",
        "prompts_export_plan.md",
        "prompts_export_execute.md",
        "prompts_export_review.md",
    ):
        assert (_paths.prompts_dir() / name).is_file(), f"缺少系统提示词 {name}"


def test_repo_root_is_stable_across_calls():
    assert _paths.repo_root() == _paths.repo_root()


def test_tests_dir_contains_this_file():
    assert Path(__file__).resolve().parent == _paths.tests_dir().resolve()


def test_no_test_module_relies_on_its_own_depth():
    """新用例不得再写 ``__file__`` 深度假设（改用 tests._paths）。

    豁免：既有历史用例（本测试只作为新增代码的约定守卫，列出已知豁免清单，
    存量迁移见 README「能力现状与路线」的待办项）。
    """
    import re

    offenders: list[str] = []
    pattern = re.compile(r"__file__\)\.resolve\(\)\.parents\[1\]|__file__\)\.resolve\(\)\.parent\.parent")
    # 本守卫自身与路径助手在文档/正则里必然包含这些字面量，跳过
    self_names = {Path(_paths.__file__).name, Path(__file__).name}
    for path in sorted(_paths.tests_dir().rglob("*.py")):
        if path.name in self_names:
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(path.name)
    # 已有存量清单（迁移中）：出现新条目即回归
    known = {
        "test_architecture_layering.py",
        "test_empty_mode_default.py",
        "test_fix_config_and_annotations.py",
        "test_fix_task_refs_and_guards.py",
        "test_fix_timing_and_file_ops.py",
        "test_prompt_main_dev_and_other_gui_apps.py",
        "test_prompt_main_done_criteria.py",
        "test_prompt_main_empty_find_dirs_when_no_project_info.py",
        "test_prompt_main_empty_merged_rules.py",
        "test_prompt_main_empty_no_plan_rule.py",
        "test_prompt_main_empty_plan_before_action.py",
        "test_prompt_main_empty_read_full_file.py",
        "test_prompt_main_empty_structure_and_meta_rules.py",
        "test_prompt_main_fix_then_bash_opt_flow.py",
        "test_prompt_main_git_commit_push_confirm.py",
        "test_prompt_main_no_bash_file_modify.py",
        "test_prompt_main_no_simplify_due_to_complexity.py",
        "test_prompt_main_no_skip_due_to_context_size.py",
        "test_prompt_main_only_write_update_tools.py",
        "test_prompt_main_read_image_verify.py",
        "test_prompt_main_relative_path_only.py",
        "test_prompt_main_reuse_existing_code.py",
        "test_prompt_main_screenshot_via_bash_opt.py",
        "test_prompt_main_simple_fix_other_module_bugs.py",
        "test_prompt_main_simple_test_incomplete.py",
        "test_prompt_main_simple_test_via_bash_opt.py",
        "test_prompt_main_simplified_chinese_output.py",
        "test_prompt_main_small_benefit_still_do.py",
        "test_prompt_review_full_bug_list_rule.py",
        "test_prompt_review_low_level_rule.py",
        "test_review_bash_rule.py",
        "test_session_list_fastpath.py",
        "test_simple_mode.py",
        "test_tui_perf_improvements.py",
        "test_type_baseline.py",
    }
    new_offenders = sorted(set(offenders) - known)
    assert not new_offenders, (
        "以下测试新增了对自身所在层级的路径假设，请改用 tests._paths："
        f"{new_offenders}"
    )

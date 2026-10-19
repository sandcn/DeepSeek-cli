"""bash 工具提词：后台运行强制用 bash_opt 清理（2026-10-20）。

用户要求：bash 工具的系统提词（``to_tool_schema`` 的 description，模型可见）
必须明确声明——**后台运行强制用 bash_opt 清理**。

覆盖：
  1. function description 声明该强制约束，并给出两种清理方式
     （``bash_opt wait`` 取走结果 / ``bash_opt kill`` 终止进程树）；
  2. 明确禁止项：放任后台任务遗留运行、另起命令（其他 bash 调用 / 系统
     kill / 任务管理器）清理后台任务；
  3. ``background`` 参数 description 同样带上该强制约束（模型描述参数时
     也读得到，避免只看到「立即返回 task_id」就放任任务不管）。
"""

from __future__ import annotations

from src.tools.bash import BashFunc


def _function_schema() -> dict:
    return BashFunc.to_tool_schema()["function"]


def test_function_description_requires_bash_opt_cleanup():
    """function description：后台运行强制用 bash_opt 清理（wait / kill）。"""
    desc = _function_schema()["description"]
    assert "后台运行强制用 bash_opt 清理" in desc
    assert "bash_opt wait" in desc
    assert "bash_opt kill" in desc
    assert "task_id" in desc


def test_function_description_forbids_leaking_background_tasks():
    """function description：禁止放任遗留，禁止绕开 bash_opt 清理。"""
    desc = _function_schema()["description"]
    assert "禁止放任后台任务遗留运行" in desc
    assert "禁止另起命令" in desc
    assert "系统 kill" in desc


def test_background_param_description_requires_bash_opt_cleanup():
    """background 参数描述：同样声明强制由 bash_opt 清理。"""
    bg = _function_schema()["parameters"]["properties"]["background"]["description"]
    assert "bash_opt" in bg and "清理" in bg
    assert "bash_opt wait" in bg and "bash_opt kill" in bg
    assert "不得放任后台任务遗留运行" in bg

"""验证 prompts_export_review.md 新增的 UI 布局与交互专项维度。

本次文档修改要求：
- 增加 UI 维度：位置、大小、开启、关闭、层级、穿透、拖动、更新、显示等；
- 专项审查维度从 10 项扩展为 11 项（新增「11 UI 布局与交互专项」）；
- 专项 Bug 查找维度从 56 项扩展为 66 项（新增 J 组：UI 布局与交互 57-66）；
- 组数从 9 组扩展为 10 组。
本测试对文档关键规则文本做存在性与一致性断言。
"""

from pathlib import Path

PROMPTS_REVIEW = Path(__file__).resolve().parents[2] / "prompts" / "prompts_export_review.md"

# 用户点名的九类 UI 问题维度
UI_DIMENSIONS = ["位置", "大小", "开启", "关闭", "层级", "穿透", "拖动", "更新", "显示"]

# 专项审查维度中必须存在的 UI 专项条目
REQUIRED_REVIEW_DIMENSION = "11 UI 布局与交互专项"

# 专项 Bug 查找维度中必须存在的 J 组条目（编号+标题+关键词）
REQUIRED_J_GROUP = [
    "### J 组：UI 布局与交互（57-66）",
    "**57 UI 位置与坐标**",
    "**58 UI 大小与尺寸**",
    "**59 UI 开启**",
    "**60 UI 关闭**",
    "**61 UI 层级与遮挡**",
    "**62 UI 事件穿透**",
    "**63 UI 拖动**",
    "**64 UI 更新与刷新**",
    "**65 UI 显示与隐藏**",
    "**66 UI 交互反馈**",
]

# 编号/组数一致性的关键文本
REQUIRED_COUNTS = [
    "「专项审查维度（11 项）」",
    "「专项 Bug 查找维度（66 项）」",
    "### 专项审查维度（11 项）",
    "## 专项 Bug 查找维度（66 项）",
    "按 10 组执行",
]

# 不得残留的过时表述（旧编号）
OUTDATED_TEXTS = [
    "专项审查维度（10 项）",
    "专项 Bug 查找维度（56 项）",
    "按 9 组执行",
]


def _doc_text() -> str:
    assert PROMPTS_REVIEW.is_file(), f"文档不存在: {PROMPTS_REVIEW}"
    return PROMPTS_REVIEW.read_text(encoding="utf-8")


def test_ui_dimensions_present_in_review_dimension():
    """专项审查维度「11 UI 布局与交互专项」必须覆盖全部九类 UI 问题维度。"""
    text = _doc_text()
    assert REQUIRED_REVIEW_DIMENSION in text, f"缺少专项审查维度: {REQUIRED_REVIEW_DIMENSION}"
    segment = text.split(REQUIRED_REVIEW_DIMENSION)[1]
    segment = segment.split("## 专项 Bug 查找维度")[0]
    missing = [d for d in UI_DIMENSIONS if d not in segment]
    assert not missing, f"专项审查维度缺少以下 UI 维度: {missing}"


def test_j_group_present():
    """专项 Bug 查找维度必须新增 J 组（57-66）全部条目。"""
    text = _doc_text()
    missing = [rule for rule in REQUIRED_J_GROUP if rule not in text]
    assert not missing, f"文档缺少以下 J 组条目: {missing}"


def test_ui_dimensions_present_in_j_group():
    """J 组（57-66）各条目必须覆盖对应的 UI 问题维度关键词。"""
    text = _doc_text()
    j_segment = text.split("### J 组：UI 布局与交互（57-66）")[1]
    j_segment = j_segment.split("## 分步推理框架")[0]
    missing = [d for d in UI_DIMENSIONS if d not in j_segment]
    assert not missing, f"J 组缺少以下 UI 维度: {missing}"


def test_count_consistency():
    """专项审查维度 11 项、专项 Bug 查找维度 66 项、10 组，编号引用必须一致。"""
    text = _doc_text()
    missing = [rule for rule in REQUIRED_COUNTS if rule not in text]
    assert not missing, f"文档缺少以下编号一致性文本: {missing}"


def test_no_outdated_counts():
    """不得再残留旧编号表述（10 项 / 56 项 / 9 组）。"""
    text = _doc_text()
    outdated = [old for old in OUTDATED_TEXTS if old in text]
    assert not outdated, f"文档仍残留过时编号表述: {outdated}"

"""验证 prompts_export_review.md 的 UI 布局与交互专项维度。

本次文档修改要求：
- 增加 UI 维度：位置、大小、开启、关闭、层级、穿透、拖动、更新、显示等；
- 专项审查维度扩展为 12 项（「12 UI 布局与交互专项」）；
- 专项 Bug 查找维度扩展为 74 项（K 组：UI 布局与交互 65-74）；
- 组数扩展为 11 组（H 游戏 / I 图形学 / J 游戏服务器 / K UI）。
本测试对文档关键规则文本做存在性与一致性断言。
"""

from pathlib import Path

PROMPTS_REVIEW = Path(__file__).resolve().parents[2] / "prompts" / "prompts_export_review.md"

# 用户点名的九类 UI 问题维度
UI_DIMENSIONS = ["位置", "大小", "开启", "关闭", "层级", "穿透", "拖动", "更新", "显示"]

# 专项审查维度中必须存在的 UI 专项条目
REQUIRED_REVIEW_DIMENSION = "12 UI 布局与交互专项"

# 专项 Bug 查找维度中必须存在的 K 组条目（编号+标题+关键词）
REQUIRED_K_GROUP = [
    "### K 组：UI 布局与交互（65-74）",
    "**65 UI 位置与坐标**",
    "**66 UI 大小与尺寸**",
    "**67 UI 开启**",
    "**68 UI 关闭**",
    "**69 UI 层级与遮挡**",
    "**70 UI 事件穿透**",
    "**71 UI 拖动**",
    "**72 UI 更新与刷新**",
    "**73 UI 显示与隐藏**",
    "**74 UI 交互反馈**",
]

# 编号/组数一致性的关键文本
REQUIRED_COUNTS = [
    "「专项审查维度（12 项）」",
    "「专项 Bug 查找维度（74 项）」",
    "### 专项审查维度（12 项）",
    "## 专项 Bug 查找维度（74 项）",
    "按 11 组执行",
]

# 不得残留的过时表述（旧编号）
OUTDATED_TEXTS = [
    "专项审查维度（11 项）",
    "专项 Bug 查找维度（66 项）",
    "按 10 组执行",
    "### J 组：UI 布局与交互（57-66）",
    "**57 UI 位置与坐标**",
]


def _doc_text() -> str:
    assert PROMPTS_REVIEW.is_file(), f"文档不存在: {PROMPTS_REVIEW}"
    return PROMPTS_REVIEW.read_text(encoding="utf-8")


def test_ui_dimensions_present_in_review_dimension():
    """专项审查维度「12 UI 布局与交互专项」必须覆盖全部九类 UI 问题维度。"""
    text = _doc_text()
    assert REQUIRED_REVIEW_DIMENSION in text, f"缺少专项审查维度: {REQUIRED_REVIEW_DIMENSION}"
    segment = text.split(REQUIRED_REVIEW_DIMENSION)[1]
    segment = segment.split("## 专项 Bug 查找维度")[0]
    missing = [d for d in UI_DIMENSIONS if d not in segment]
    assert not missing, f"专项审查维度缺少以下 UI 维度: {missing}"


def test_k_group_present():
    """专项 Bug 查找维度必须新增 K 组（65-74）全部条目。"""
    text = _doc_text()
    missing = [rule for rule in REQUIRED_K_GROUP if rule not in text]
    assert not missing, f"文档缺少以下 K 组条目: {missing}"


def test_ui_dimensions_present_in_k_group():
    """K 组（65-74）各条目必须覆盖对应的 UI 问题维度关键词。"""
    text = _doc_text()
    k_segment = text.split("### K 组：UI 布局与交互（65-74）")[1]
    k_segment = k_segment.split("## 分步推理框架")[0]
    missing = [d for d in UI_DIMENSIONS if d not in k_segment]
    assert not missing, f"K 组缺少以下 UI 维度: {missing}"


def test_count_consistency():
    """专项审查维度 12 项、专项 Bug 查找维度 74 项、11 组，编号引用必须一致。"""
    text = _doc_text()
    missing = [rule for rule in REQUIRED_COUNTS if rule not in text]
    assert not missing, f"文档缺少以下编号一致性文本: {missing}"


def test_no_outdated_counts():
    """不得再残留旧编号表述（11 项 / 66 项 / 10 组 / 旧 J 组）。"""
    text = _doc_text()
    outdated = [old for old in OUTDATED_TEXTS if old in text]
    assert not outdated, f"文档仍残留过时编号表述: {outdated}"

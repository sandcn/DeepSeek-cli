"""验证 prompts_export_review.md 新增的图形学专项维度。

本次文档修改要求：
- 增加图形学相关维度；
- 专项审查维度扩展为 12 项（新增「10 图形学专项」，与「9 游戏专项」分离）；
- 专项 Bug 查找维度扩展为 74 项（新增 I 组：图形学 46-56，原 H 组拆为
  H 组游戏 41-45 + I 组图形学 46-56，J 组游戏服务器 57-64、K 组 UI 65-74）；
- 组数从 10 组扩展为 11 组。
本测试对文档关键规则文本做存在性与一致性断言。
"""

import re
from pathlib import Path

PROMPTS_REVIEW = Path(__file__).resolve().parents[2] / "prompts" / "prompts_export_review.md"

# 专项审查维度中必须存在的图形学专项条目
REQUIRED_REVIEW_DIMENSION = "10 图形学专项"

# 专项审查维度（图形学专项）必须覆盖的图形学子领域关键词
GRAPHICS_KEYWORDS = [
    "坐标系与变换数学",
    "渲染管线与 GPU 状态",
    "GPU 资源生命周期",
    "光照与着色",
    "阴影",
    "后处理与色彩",
    "材质与纹理",
    "几何与网格",
    "相机与视锥",
    "动画蒙皮",
    "粒子与特效",
    "渲染性能",
]

# 专项 Bug 查找维度中必须存在的 I 组条目（编号+标题）
REQUIRED_I_GROUP = [
    "### I 组：图形学（46-56）",
    "**46 渲染管线与 GPU 状态**",
    "**47 GPU 资源管理**",
    "**48 坐标系与数学运算**",
    "**49 光照与着色模型**",
    "**50 阴影**",
    "**51 后处理与色彩**",
    "**52 材质与纹理**",
    "**53 几何与网格**",
    "**54 相机与视锥**",
    "**55 动画与蒙皮**",
    "**56 粒子与特效**",
]

# I 组各条目必须覆盖的关键概念（条目关键词 → 覆盖词）
I_GROUP_KEYWORDS = {
    "46 渲染管线与 GPU 状态": ["渲染状态", "shader", "uniform", "透明物体排序", "渲染目标"],
    "47 GPU 资源管理": ["纹理", "缓冲", "帧缓冲", "显存", "图形上下文丢失"],
    "48 坐标系与数学运算": ["矩阵", "四元数", "坐标系", "行列主序", "浮点精度", "蒙皮矩阵"],
    "49 光照与着色模型": ["Blinn-Phong", "PBR", "法线", "切线空间", "IBL", "能量不守恒"],
    "50 阴影": ["阴影痤疮", "级联阴影", "阴影贴图", "PCF", "PCSS"],
    "51 后处理与色彩": ["HDR", "ToneMapping", "sRGB", "Bloom", "SSAO", "LUT"],
    "52 材质与纹理": ["UV", "mipmap", "各向异性", "纹理压缩", "金属度", "粗糙度"],
    "53 几何与网格": ["顶点", "索引", "包围盒", "LOD", "程序化生成", "细分曲面"],
    "54 相机与视锥": ["投影矩阵", "视锥体裁剪", "Z-fighting", "lookAt", "FOV"],
    "55 动画与蒙皮": ["骨骼", "蒙皮矩阵", "权重", "动画状态机", "deltaTime"],
    "56 粒子与特效": ["粒子生命周期", "资源释放", "GPU 粒子", "缓冲区", "渲染顺序"],
}

# 分组标题与编号范围
REQUIRED_GROUPS = [
    "### H 组：游戏（41-45）",
    "### I 组：图形学（46-56）",
    "### J 组：游戏服务器（57-64）",
    "### K 组：UI 布局与交互（65-74）",
]

# 不得残留的过时表述（旧合并分组）
OUTDATED_TEXTS = [
    "游戏与图形学",
    "### H 组：游戏与图形学（41-48）",
    "专项 Bug 查找维度（66 项）",
    "按 10 组执行",
]


def _doc_text() -> str:
    assert PROMPTS_REVIEW.is_file(), f"文档不存在: {PROMPTS_REVIEW}"
    return PROMPTS_REVIEW.read_text(encoding="utf-8")


def _bug_dimension_text() -> str:
    text = _doc_text()
    return text.split("## 专项 Bug 查找维度")[1]


def test_review_dimension_present():
    """专项审查维度必须包含「10 图形学专项」。"""
    text = _doc_text()
    assert REQUIRED_REVIEW_DIMENSION in text, f"缺少专项审查维度: {REQUIRED_REVIEW_DIMENSION}"


def test_review_dimension_keywords():
    """专项审查维度「10 图形学专项」必须覆盖全部图形学子领域关键词。"""
    text = _doc_text()
    segment = text.split(REQUIRED_REVIEW_DIMENSION)[1]
    segment = segment.split("## 专项 Bug 查找维度")[0]
    missing = [k for k in GRAPHICS_KEYWORDS if k not in segment]
    assert not missing, f"图形学专项缺少以下子领域: {missing}"


def test_i_group_present():
    """专项 Bug 查找维度必须新增 I 组（46-56）全部条目。"""
    text = _doc_text()
    missing = [rule for rule in REQUIRED_I_GROUP if rule not in text]
    assert not missing, f"文档缺少以下 I 组条目: {missing}"


def test_i_group_keywords():
    """I 组（46-56）各条目必须覆盖对应的图形学关键概念。"""
    text = _bug_dimension_text()
    i_segment = text.split("### I 组：图形学（46-56）")[1]
    i_segment = i_segment.split("### J 组：游戏服务器")[0]
    titles = list(I_GROUP_KEYWORDS)
    for idx, title in enumerate(titles):
        start = i_segment.index(title)
        end = i_segment.index(titles[idx + 1]) if idx + 1 < len(titles) else len(i_segment)
        body = i_segment[start:end]
        missing = [k for k in I_GROUP_KEYWORDS[title] if k not in body]
        assert not missing, f"I 组条目 {title} 缺少关键概念: {missing}"


def test_groups_present():
    """H/I/J/K 四组标题与编号范围必须存在。"""
    text = _doc_text()
    missing = [g for g in REQUIRED_GROUPS if g not in text]
    assert not missing, f"文档缺少以下分组标题: {missing}"


def test_bug_dimension_numbering_continuous():
    """专项 Bug 查找维度条目编号必须为 1-74 连续无缺失无重复。"""
    text = _bug_dimension_text()
    nums = [int(m) for m in re.findall(r"- \*\*(\d+) ", text)]
    assert nums == list(range(1, 75)), (
        f"编号不连续: 共 {len(nums)} 条, "
        f"缺失 {[i for i in range(1, 75) if i not in nums]}, "
        f"重复 {sorted({n for n in nums if nums.count(n) > 1})}"
    )


def test_review_dimension_numbering_continuous():
    """专项审查维度条目编号必须为 1-12 连续。"""
    text = _doc_text()
    segment = text.split("### 专项审查维度（12 项）")[1]
    segment = segment.split("## 专项 Bug 查找维度")[0]
    nums = [int(m) for m in re.findall(r"- \*\*(\d+) ", segment)]
    assert nums == list(range(1, 13)), f"专项审查维度编号异常: {nums}"


def test_no_outdated_group_texts():
    """不得再残留合并分组等过时表述。"""
    text = _doc_text()
    outdated = [old for old in OUTDATED_TEXTS if old in text]
    assert not outdated, f"文档仍残留过时表述: {outdated}"


def test_graphics_migrated_from_old_h_group():
    """原 H 组图形学条目（42 坐标系 / 43 渲染状态 / 44 GPU 资源）已迁移至 I 组新编号。"""
    text = _doc_text()
    assert "**48 坐标系与数学运算**" in text
    assert "**46 渲染管线与 GPU 状态**" in text
    assert "**47 GPU 资源管理**" in text
    # 旧编号不得以条目形式残留
    assert "**43 渲染状态与管线**" not in text
    assert "**44 GPU 资源管理**" not in text

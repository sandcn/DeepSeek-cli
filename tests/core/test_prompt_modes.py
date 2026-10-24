"""主 Agent 运行模式与静态提词契约测试。

守护「空 / 简单 / 标准」三模式的真实语义：三者是**三种提词范式**，不是
规则量递增的三档——`empty` 为单体完整规则集（规则完整、默认禁止子代理）、`simple` 为
命令式精简规则集（最小）、`standard` 为子代理编排范式。

同时锁定两处已修复的规则文本矛盾（防回归）：
  - 「零落盘时不得声明完成」与「纯问答/审查轮以结论完整为标准」互相否定；
  - 条款自述「裁决层级次于安全红线」与唯一阶梯（含第零条/实现完整性红线）不一致。

并锁定空模式第二轮审计的修复项：空选择以 `action` 字段裁决、计划项跨轮口径、
回滚与基线可执行、提问口子覆盖版本控制写操作、元文件注入事实、无图形证据形态、
输出按改动规模分级。

并锁定空模式后续审计的修复项：提问口子收敛为唯一清单、`action=empty` 属调用错误、
补测试作为受控第二次落盘、基线可选、git / 远程命令的 bash 通道、提词例外范围、
元文件注入事实细化，以及行号型交叉引用改为章节名。

并锁定空模式章节命名口径：标题不带数字编号（不用「2.1」式命名），
交叉引用一律按章节名称闭环，避免编号随改稿漂移。

并锁定空模式第三轮审计的修复项：跨层阶梯、`push` 显式确认、安全侧选项与
`custom` 语义、新建文件写前检查、破坏性修复项拆分、测试框架完成口径、
悬空章节引用与 `.bak` 快照提交边界。

并锁定空模式第七章「正例 / 反例对照」与正文判据的一致性（示例不得顶掉条款）。

并锁定空模式「输出语言」条款：思考与回答都强制中文，同时保留代码 / 命令 /
报错原文与安全红线脱敏要求的例外。
"""

from __future__ import annotations

import re

import pytest

from src.prompt_builder import builder
from src.prompt_builder.modes import DEFAULT_MODE, mode_order, resolve_mode_export

#: 模式 → (提词文件名, 该范式的身份标记)
MODE_PROMPTS = (
    ("empty", "prompts_export_main_empty", "第零条禁令"),
    ("simple", "prompts_export_main_simple", "强势禁止"),
    ("standard", "prompts_export_main", "Agent 总览"),
)


def test_default_mode_is_empty() -> None:
    assert DEFAULT_MODE == "empty"
    assert builder.get_mode() == "empty"


def test_mode_cycle_order_is_empty_simple_standard() -> None:
    assert mode_order() == ["empty", "simple", "standard"]


@pytest.mark.parametrize(("mode", "export", "_marker"), MODE_PROMPTS)
def test_mode_export_mapping(mode: str, export: str, _marker: str) -> None:
    assert resolve_mode_export(mode) == export


@pytest.mark.parametrize(("mode", "export", "marker"), MODE_PROMPTS)
def test_each_mode_loads_its_monolithic_prompt(mode: str, export: str, marker: str) -> None:
    content = builder._load_prompt(export)
    assert content.strip(), f"{mode} 模式提词为空"
    assert marker in content, f"{mode} 模式提词缺少身份标记：{marker}"


def test_three_prompts_are_distinct_paradigms() -> None:
    contents = [builder._load_prompt(export) for _mode, export, _m in MODE_PROMPTS]
    assert len(set(contents)) == len(contents)


def test_simple_prompt_forbids_deferring_work_to_next_round() -> None:
    """简单模式锁定「本轮执行不完也不许跳过」条款（防退化成允许延后）。"""
    content = builder._load_prompt("prompts_export_main_simple")

    assert "强制禁止因本轮执行不完就不执行" in content
    # 必须要求本轮持续执行到完成，不得把未完成项推给下一轮
    assert "必须在本轮持续执行到全部需求实际完成并验证通过" in content
    assert "剩余部分留到下一轮" in content
    # 仅用户叫停或按规则 user_select 澄清时才能暂停，答复后必须继续执行
    assert "仅用户明确叫停时可暂停" in content
    assert "用户答复后必须继续执行" in content
    # 既有的「耗时/难度不作为不做的理由」禁令不得被新条款顶掉
    assert "禁止以任务耗时长、复杂度高为由选择不做" in content
    assert "禁止以任务宏大、难度大为由选择不做" in content


def test_simple_prompt_requires_reading_code_before_conclusions() -> None:
    """简单模式锁定「必须先读代码再下结论 / 分析 / 计划 / 修复方案」条款。"""
    content = builder._load_prompt("prompts_export_main_simple")

    # 条款存在，且覆盖结论、分析、计划、修复方案、审查意见全部输出形态。
    assert "强制读完代码才能下结论" in content
    assert "禁止未读代码就做出结论、分析、计划、修复方案或审查意见" in content
    # 必须先 read 全文 + 追全调用链与引用关系，之后才允许输出判断。
    assert "必须先 `read` 读完相关源码及配置、测试、调用方" in content
    assert "并用 `grep`/`glob` 追全调用链与全部引用关系" in content
    assert "之后才允许输出任何判断、分析、计划与修复方案" in content
    # 禁止凭文件名、注释、印象、猜测或模糊措辞下判断。
    assert "禁止凭文件名、注释、印象、猜测" in content
    assert "未读代码即给结论/分析/计划/修复方案一律视为违规" in content
    # 既有的「先读够再动手」条款不得被新条款顶掉。
    assert "先读够或者查够代码在动手" in content


def test_build_system_prompt_uses_current_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    original = builder.get_mode()
    try:
        builder.set_mode("empty")
        parts = builder.build_system_prompt()
        assert parts
        assert "第零条禁令" in parts[0]
    finally:
        builder.set_mode(original)


def test_removed_contradictions_do_not_come_back() -> None:
    content = builder._load_prompt("prompts_export_main_empty")
    # 「零落盘时不得声明完成」与「纯问答/审查不适用、不得据此判定未完成」互斥
    assert "零落盘时不得声明完成" not in content
    # 条款自述层级不得与唯一阶梯冲突（须写「裁决层级见……」）
    assert "裁决层级次于安全红线" not in content


def test_prompt_contradictions_fixed_in_empty_mode() -> None:
    """锁定空模式提词本轮修复的授权、元文件与配置测试口径。"""
    content = builder._load_prompt("prompts_export_main_empty")

    # 空选择语义以 action 字段为准；不得再出现「空选择按默认全选」的自相矛盾写法。
    assert "主动拒绝或取消" not in content
    assert "`selected` 为空时按默认全选" not in content
    assert "空选择按默认全选" not in content
    assert "以 `action` 字段为准" in content
    assert "`selected` 为空视为未授权" in content
    assert "法定拟制授权" in content

    # 互斥选项不得同时全选。
    assert "不因方案互斥而少勾" not in content

    # 提词 / 文档改动以受影响测试或静态核查替代图形实操；不得再引用不存在的「配置项」。
    assert "**仅改提词 / 文档等非代码文本**" in content
    assert "以受影响的契约测试或静态核查替代" in content
    assert "不要求图形界面实操" in content
    assert "按配置处理" not in content

    # 元文件注入事实必须与实现一致。
    assert "7 个运行时元文件" not in content
    assert "`_empty` 空环境" not in content
    assert "不会自动进入主 Agent 上下文" in content

    # 超时授权只保留安全红线约束，不得声称只剩凭据一项。
    assert "唯一保留的约束是不外泄凭据值" not in content
    assert "当场多选" not in content
    assert "当场申请授权" not in content


def test_empty_prompt_has_positive_authorization_path() -> None:
    """正向授权判据：满足条件即执行，禁止把催促当成「没有新信息」。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "默认动作是执行" in content
    assert "视为已获明确授权" in content
    assert "这是授权确认" in content
    assert "原地重复提问" in content
    # 催促只对「修改请求」构成授权；必要提问是默认执行的显式例外。
    assert "同一修改请求" in content
    assert "〈提问规则〉列明的必要提问" in content
    # 提问必须收敛：只在互斥方向 / 破坏性 / 对象无法定位时问，且最多一轮
    assert "最多一轮" in content
    assert "需求澄清的纯文本答复同样有效" in content


def test_empty_prompt_distinguishes_unanswered_from_rejection() -> None:
    """未回答（answered=false）不得被判成拒绝或未授权。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "未回答 ≠ 拒绝" in content
    assert "answered=false" in content
    assert "`action=cancel`" in content
    # 既有约束不得被新条款顶掉
    assert "`selected` 为空视为未授权" in content


def test_empty_prompt_allows_graded_delivery() -> None:
    """完成标准分级：如实报告未完成项不判失败，谎报才判失败。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "如实报告 ≠ 失败" in content
    assert "分级交付" in content
    assert "只有以下情况判失败" in content


def test_empty_prompt_invariants_after_conflict_fix() -> None:
    """锁定本轮修复的硬冲突与悬空引用（防回退）。

    对应审计结论：条款互斥（计划延后 / 空选择 / 回滚 / 提问口子）、
    判据不可观测、与实现不符（配置项、元文件注入、截图证据）、成本失控。
    """
    content = builder._load_prompt("prompts_export_main_empty")

    # 计划延后口径统一：允许本轮分批，禁止跨轮延后。
    assert "不得跨轮延后" in content
    assert "本轮内分批不视为延后" in content

    # 回滚与基线可执行：写前快照、误改恢复、改动前基线。
    assert "写前快照" in content
    assert "git restore --source=HEAD --staged --worktree -- <path>" in content
    assert "基线例外" in content

    # 提问口子覆盖版本控制写操作；扩权「申请与执行」不得两缺。
    assert "④ 版本控制写操作" in content
    assert "不得两缺" in content

    # 不可观测判据与悬空引用必须消失。
    assert "模型自我说服" not in content
    assert "按配置处理" not in content
    assert "7 个运行时元文件" not in content

    # 无图形环境与输出成本有明确口径；路径例外与〈运行时元文件〉对齐。
    assert "无图形环境" in content
    assert "按改动规模分级" in content
    assert "~/.chat/plan/*.md" in content

    # 判据覆盖非中文指令、「本轮」有定义、测试范围收敛。
    assert "措辞清单是示例不是穷举" in content
    assert "「本轮」= 用户当前这条消息" in content
    assert "受影响测试集合" in content

    # git 三问不得因「最多一轮」而失效。
    assert "不占用新一轮提问" in content


def test_simple_prompt_has_positive_authorization_and_graded_delivery() -> None:
    """简单模式同步具备正向授权、未回答语义与分级交付。"""
    content = builder._load_prompt("prompts_export_main_simple")

    assert "默认执行（红线" in content
    assert "未回答 ≠ 拒绝" in content
    assert "如实报告未完成项不判失败" in content
    assert "仅在必要时提问" in content


def test_empty_prompt_question_gate_has_single_authority_list() -> None:
    """所有要求用户拍板 / 确认的条款都必须回引〈提问规则〉的唯一清单。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "允许先提问的情形（唯一清单" in content
    assert "⑤ 授权范围外的缺陷修复扩权" in content
    assert "属〈提问规则〉⑤" in content
    assert "属〈提问规则〉②" in content
    assert "属〈提问规则〉④" in content
    assert "〈提问规则〉①" in content
    assert "若修复项互斥则按〈提问规则〉① 改单选" in content
    assert "①：按〈决策与默认项〉的默认项（分析中给出的推荐方案）执行" in content
    assert "④：改用一句纯文本说明 + 按〈版本控制（Git）〉的默认项执行" in content
    assert "②：按〈安全红线〉必须显式确认，不适用默认项回退" in content
    assert "③：不得猜测，只做可执行部分并如实报告受阻" in content
    assert "普通本地 `commit` 按 ④ / 〈版本控制（Git）〉" in content
    assert "`commit --amend` 等破坏性 / 不可逆操作与 `push` 按本条 / 〈安全红线〉" in content
    assert "〈提问规则〉列明的情形除外" in content


def test_empty_prompt_batches_undetermined_requirement_points() -> None:
    """需求中有未确定的决策点时，必须一次性并发多个 user_select 问齐。"""
    content = builder._load_prompt("prompts_export_main_empty")

    # ⑥ 是提问清单的显式扩项，但不把可推定的小细节也变成提问。
    assert "⑥ 需求本身存在未确定的决策点" in content
    assert "无法从用户原话、上下文、代码现状与既有约定推定" in content
    assert "能自行推定的一律自行决定" in content
    assert "属 ⑥ 的决策点必须先问" in content
    assert "不得以「取最合理方案」绕过" in content

    # 必须一次问齐、并发弹出，禁止串行与拆轮。
    assert "需求类提问必须一次问齐、并发弹出" in content
    assert "同一轮并发发起多个 `user_select`" in content
    assert "禁止串行逐个问" in content
    assert "禁止等一个答复再问下一个" in content
    assert "禁止把同一批决策点拆到多轮" in content
    assert "不得只挑一部分问、其余自行定死" in content
    assert "例 6｜一轮问完（需求未确定点）" in content

    # 与正向判据不互斥：澄清需求不等于重复确认。
    assert "不视为重复确认" in content
    # 提问轮用尽后按推荐默认项推进，不得停摆。
    assert "⑥：按各问题的 `default_options`（推荐方案）执行" in content


def test_empty_prompt_execution_path_conflicts_removed() -> None:
    """验证时序、基线、git / 远程命令与 action=empty 的口径不得互斥。"""
    content = builder._load_prompt("prompts_export_main_empty")

    # 补测试是受控的第二次落盘；是否新增测试按〈测试〉判断。
    assert "受控的第二次落盘" in content
    assert "按〈测试〉判断是否需要新增" in content

    # 基线是可选建议，未跑基线只影响回归结论强度，不直接判未完成。
    assert "未做基线对比" in content
    assert "并列入未完成清单" not in content

    # 远程命令 / 破坏性操作是安全红线：超时默认项不得代替显式确认；
    # 只有指向执行侧（且不含安全侧）的明确选择才算确认。
    assert "`selected` **全部**指向**执行侧选项**" in content
    assert "自由输入时 `custom` 明确表达执行意图" in content
    assert "语义不明，一律视为未确认" in content
    assert "一律视为未确认，不得执行" in content
    assert "破坏性 / 不可逆项必须拆成独立的单选问题" in content
    assert "默认项取安全侧" in content
    assert "`push`（含普通 push）与强制推送 / 其他不可逆远程操作按〈安全红线〉必须显式确认" in content
    # git 写操作与远程命令必须有可执行的 bash 通道。
    assert "`commit` / `push` 经〈版本控制（Git）〉提问流程并按对应条款确认后" in content
    assert "远程命令经〈安全红线〉确认后" in content

    # 提词 / 文档改动的三查豁免按文本类型判定，不绑定具体仓库路径。
    assert "**仅改提词 / 文档等非代码文本**" in content
    assert "以受影响的契约测试或静态核查替代" in content
    assert "prompts_export_main" not in content
    assert "prompts_loop_prompt" not in content

    # action=empty 是调用错误，不得被当成默认授权。
    assert "`action=empty`（`options` 为空，工具未真正提问）" in content
    assert "不视为授权" in content
    # 扩权 / git / 三查的拒绝分支也必须覆盖 action=empty。
    assert "`action=cancel`、`action=empty`" in content
    assert "`action=empty` 先按〈提问规则〉修正参数重发" in content
    assert "仍失败则保留工作区改动并如实告知" in content


def test_empty_prompt_references_and_meta_files_are_stable() -> None:
    """行号型引用会随编辑漂移；元文件注入事实须与实现一致。"""
    content = builder._load_prompt("prompts_export_main_empty")

    # 交叉引用必须用可定位的章节名，不能再出现行号型引用。
    assert "三-54" not in content
    assert "三-59" not in content
    assert "本节「分级交付」" in content
    assert "〈工作方式 · 全量调用链与计划（强制 · 涉及代码的一切任务）〉" in content

    # 章节以名称命名，不使用「2.1」「5.4」这类数字编号；交叉引用按名称闭环。
    assert not re.search(r"^#{1,3} [0-9]+(\.[0-9]+)*[、. ]", content, re.MULTILINE)
    assert "## 授权闸门 · 正向判据" in content
    assert "## 协作与安全 · 安全红线" in content

    # 提词改动默认按非代码文本处理；但一旦文本里涉及代码引用，仍须先给全量调用链。
    assert "不要求代码调用链" in content
    assert "在普通文件（文档 / 说明 / 注释 / 配置 / 提词 / SQL / README 等）里补一段代码引用" in content
    assert "全量未完成任务列表" in content

    # global.md / main.md 按工作目录注入；think.md 无对应子代理。
    assert "工作目录（通常是项目根）" in content
    assert "存在且非空时" in content
    assert "`think.md` 无对应子代理，不注入" in content
    assert "`skills.auto_load` 配置的技能正文（如有）" in content


def test_empty_prompt_priority_and_failure_semantics() -> None:
    """授权补充项、硬红线例外与失败分类不得互相拆台。"""
    content = builder._load_prompt("prompts_export_main_empty")

    # 〈正向判据〉第 4 项的「按你的判断做」只有在既有任务 / 方案时才构成授权。
    assert "第 2–4 项是对既有方案" in content
    assert "仅在已有明确任务或上一轮方案时构成授权" in content

    # 〈实操三查与例外〉是「实操三查」红线的组成部分，避免低级条款削弱硬红线。
    assert "〈实操三查与例外〉是本款的组成部分" in content
    assert "不构成对红线的降低" in content

    # 触碰安全红线必须判失败，失败类别与「四类」口径一致。
    assert "触碰安全红线" in content
    assert "「失败」只指上一条的四类" in content

    # 元文件读取需说明用途，不得与「未经明确指定不得主动读取」互斥。
    assert "为完成当前任务确有必要时，可读取（读取前说明用途）" in content

    # 项目外授权、回滚 edit、安全红线层级、提交信息与基线口径。
    assert "〈运行时元文件〉允许的 `~/.chat/plan/*.md`——〈文件与工具〉路径限制的例外" in content
    assert "覆盖已有文件（`write` / `cp` / `mv` 覆盖目标）、局部改写（`edit`）或删除已有文件（`rm`）" in content
    assert "新建文件" in content
    assert "层级见「唯一阶梯」" in content
    assert "用户已给出则以其为准" in content
    assert "基线调用不得夹带对源码 / 配置 / 测试的改写" in content
    assert "回归结论已按〈验证时序〉举证" in content
    assert "不得宣称未引入回归" in content
    assert "本轮实际未改动源码 / 配置 / 测试 / 提词 / 文档 / 资源（工具缓存不计" in content
    assert "全部源码 / 配置 / 脚本 / 提词 / 文档 / 资源" in content
    assert "`action=error:*`，且 `default_options` 非空时按默认项执行" in content
    assert "本节的「实操类文件改动」" in content
    assert "项目内 `.bak`" in content
    assert "`default_options` 非空时按〈决策与默认项〉默认项推进" in content
    assert "为空时按调用错误处理，不视为授权" in content


def test_empty_prompt_third_audit_conflicts_removed() -> None:
    """第三轮审计：跨层阶梯、push 门槛、安全侧、新建文件与提交边界。"""
    content = builder._load_prompt("prompts_export_main_empty")

    # 跨层冲突不得与唯一阶梯互相否定。
    assert "同一层级内特别规定优先" in content
    assert "高层条款只是「允许」、低层有更严的禁止时，以更严者为准" in content
    assert "高层允许不等于解除低层禁令" in content

    # 新建文件不能被「写前必须 read / 快照」卡死。
    assert "新建文件**（目标尚不存在）不受此限" in content
    assert "确认目标不存在" in content

    # 破坏性修复项不得混入默认全选；快照不得进入提交范围。
    assert "非破坏性项默认全选" in content
    assert "不并入全选清单" in content
    assert "不含 `.bak` 快照" in content
    assert "快照不进入提交范围" in content

    # 完成标准与〈测试〉的无测试框架口径一致；无基线不得宣称未引入回归。
    assert "项目无测试框架时按〈测试〉以最接近方式验证并说明" in content

    # auto_load 技能不得被重复调用。
    assert "直接遵循，禁止重复调用" in content

    # 新请求与既有确认的授权判据分开，避免「只看动词 + 对象」自相矛盾。
    assert "**新请求**的授权判定只看「动词 + 对象」" in content
    assert "对既有方案 / 请求的确认按〈正向判据〉第 2–4 项" in content

    # Submit 页会用默认值 + confirmed 回传未回答项，必须靠安全侧默认项兜底。
    assert "并发多问题的 Submit 页会把**未回答**项" in content
    assert "无法与「用户显式确认默认项」区分" in content
    assert "每个 label 都必须出现在 `options` 中" in content

    # 纯文本只能否决〈安全红线〉类操作，不能替代执行侧选择；拒绝后重问仍走 user_select。
    assert "纯文本只能用于否决，不能替代执行侧选择" in content
    assert "`user_select` 内的 `custom` 自由输入按〈安全红线〉的语义判定" in content
    assert "重新用 `user_select` 的执行侧选项确认（纯文本不替代；该确认不占用需求澄清轮次" in content

    # git 的多问必须在同一轮并发，不得与「最多一轮」互斥。
    assert "拆成同一轮的独立问题（并发 `user_select`，不增加轮次）" in content
    # 用户明确说「不用确认，直接提交」时可按开篇口径覆盖〈版本控制（Git）〉；push 仍受〈安全红线〉约束。
    assert "按开篇口径覆盖本条的提问并记录" in content
    assert "`push` 与破坏性操作仍按〈安全红线〉显式确认" in content


def test_empty_prompt_requires_analysis_before_modification() -> None:
    """改代码前必须先分析完所有相关代码和文件，不得凭局部理解动手。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "要分析完所有相关代码和文件才能修改代码" in content
    # 该要求挂在「先读后改」上，不得与既有禁令互斥。
    assert "先读后改" in content
    assert "禁止凭猜测修改" in content


def test_empty_prompt_has_positive_and_negative_examples() -> None:
    """第七章的正例 / 反例必须与正文判据一致，且覆盖高风险节点。"""
    content = builder._load_prompt("prompts_export_main_empty")

    # 章节存在且声明「只解释判据、不新增规则」，避免示例反过来顶掉正文。
    assert "# 行为示例：正例 / 反例对照" in content
    assert "示例只解释判据、不新增规则" in content
    assert "✓ 正例" in content
    assert "✗ 反例" in content

    # 覆盖授权判定、提问确认、工具红线、交付与冲突裁决五个面向。
    assert "例 1｜改文件请求" in content
    assert "例 2｜纯提问" in content
    assert "例 3｜催促" in content
    assert "例 4｜无对象" in content
    assert "例 5｜能自查的先自查" in content
    assert "例 7｜未回答" in content
    assert "例 8｜破坏性 / 远程确认" in content
    assert "例 10｜验证时序" in content
    assert "例 12｜回归举证" in content
    assert "例 13｜两个清单" in content
    assert "例 14｜如实报告" in content
    assert "例 15｜条款冲突" in content
    assert "例 16｜用户明确覆盖" in content

    # 反例必须给出具体的错误动作与依据，而不是空泛措辞。
    assert "回「请确认是否要我修改该文件」" in content
    assert "`sed -i 's/old/new/' src/a.py`" in content
    assert "纯文本只能否决，不能替代执行侧选择" in content
    assert "把超时当成用户拒绝" in content
    assert "编造 pytest 输出" in content
    assert "把「简化一下」当成覆盖授权" in content

    # 示例中的正例不得与正文冲突：授权自检、默认项、三查证据形态。
    assert "授权自检：改 + config/app.toml → 已授权" in content
    assert "按〈决策与默认项〉默认项继续执行" in content
    assert "无图形环境按〈实操三查与例外〉⑤ 用命令输出替代截图" in content


def test_empty_prompt_forces_chinese_output() -> None:
    """输出语言条款：思考与回答都强制中文，且不与其他条款互斥。"""
    content = builder._load_prompt("prompts_export_main_empty")

    # 条款存在且覆盖「思考 + 回答」两侧，而不是只约束最终回答。
    assert "# 输出语言（强制）" in content
    assert "思考与回答都强制中文" in content
    assert "思考（内部推理过程）" in content
    assert "与回答，都用中文展示" in content

    # 代码 / 命令 / 报错原文可原样引用，避免与「强制中文」自相矛盾。
    assert "原样保留项" in content
    assert "命令输出与报错原文可原样引用" in content
    assert "解释、结论与交付说明必须用中文" in content

    # 与安全红线的脱敏要求冲突时以安全红线为准。
    assert "本条款属〈唯一阶梯〉最底层的「协作、流程与风格约定」" in content
    assert "与〈安全红线〉冲突时以安全红线为准" in content


def test_empty_prompt_forces_full_call_chain_from_program_entry() -> None:
    """涉及代码的一切任务都必须先一次性输出从程序入口起的全量调用链（含普通文件里的代码引用）。"""
    content = builder._load_prompt("prompts_export_main_empty")

    # 强制口径，覆盖涉及代码的一切任务，不只「动手改代码」。
    assert "全量调用链与计划（强制 · 涉及代码的一切任务）" in content
    assert "**只要任务中涉及代码**" in content
    assert "先一次性输出从程序入口起的全量调用链，再去做别的任何事情" in content
    assert "分析、修改、新增、补引用" in content
    # 承载文件是普通文件也触发：补引用、贴函数名 / 文件:行号、描述调用关系都算。
    assert "判定口径（有代码即触发）" in content
    assert "在普通文件（文档 / 说明 / 注释 / 配置 / 提词 / SQL / README 等）里补一段代码引用" in content
    assert "全部涉及点都要纳入同一张全量调用链" in content

    # 必须从程序入口起向上追溯，不得只给目标函数局部链。
    assert "先一次性输出从程序入口起的全量调用链" in content
    assert "必须从程序入口起" in content
    assert "向上追溯至程序入口" in content
    assert "「入口 → 关键中转 → 目标 → 叶子」" in content
    assert "不得仅以目标函数为唯一锚点" in content
    assert "不得只给目标函数局部链" in content

    # 一次性全量：禁止挤牙膏、禁止拖到交付才补；分层不得降级为摘要。
    assert "必须一次性全量" in content
    assert "禁止分批挤牙膏式补全" in content
    assert "禁止拖到交付阶段才补" in content
    assert "分层只是排版，不是摘要" in content

    # 交付与纯问答轮同样受约束；唯一例外是完全不含代码的纯文本。
    assert "**调用链（强制）**" in content
    assert "不得留到用户追问再补" in content
    assert "此项不受「本轮未改动」豁免" in content
    assert "唯一例外是不含代码的纯文本" in content
    assert "例 17｜涉及代码的任务强制全量调用链（先链后事）" in content


def test_simple_prompt_allows_push_by_default() -> None:
    """简单模式：`git push` 默认放行，不得被「没用户指定禁止操作 git」拦下。"""
    content = builder._load_prompt("prompts_export_main_simple")

    # 条款存在，且明确「默认直接执行 / 无需确认」。
    assert "`git push` 默认直接执行" in content
    assert "是默认允许的操作，任务需要推送时直接执行" in content
    assert "无需 `user_select` 确认" in content
    # 禁止把 push 当成需要先问一句的远程写。
    assert "禁止以「没用户指定操作 git」「属于远程写」为由拒绝、跳过或反过来问用户" in content

    # git 禁令必须给 push 开例外，不得与默认放行条款互斥。
    assert "没用户指定强制禁止操作 git svn（`git push` 除外" in content
    # 提问口子不得把普通 push 归入「远程写」。
    assert "普通 `git push` 按「`git push` 默认直接执行」条款直接放行，不属②" in content

    # 默认远端 / 分支有确定口径；失败反馈与不可逆操作的边界明确。
    assert "无上游时用 `origin` + 当前分支" in content
    assert "非快进被拒" in content
    assert "强制推送（`--force` / `--force-with-lease` / `+refs`）" in content
    assert "删除远程分支等不可逆远程操作不属默认放行范围" in content


def test_simple_prompt_asks_user_after_code_analysis() -> None:
    """简单模式：需求决策点必须先分析当前项目代码，再交用户拍板并多给 user_select。"""
    content = builder._load_prompt("prompts_export_main_simple")

    # 需求决策点纳入提问情形（第四个口子），不得与「仅在必要时提问」互斥。
    assert "需求本身存在需要用户拍板的决策点" in content
    assert "这四种情形才用 `user_select` 提问" in content

    # 先分析当前项目代码：目录全貌、源码/配置/测试、调用链与引用关系。
    assert "需求决策先分析当前项目代码再让用户决定" in content
    assert "先 `glob` 掌握目录全貌" in content
    assert "`read` 读完相关源码/配置/测试" in content
    assert "`grep` 追全调用链与引用关系" in content
    # 决策材料必须带现状依据、选项后果与推荐方案，不能空口让用户选。
    assert "代码现状依据 + 各选项后果差异 + 推荐方案" in content
    # 可推定项自行决定，不得反手全抛给用户。
    assert "能从用户原话、上下文、代码现状与既有约定推定的一律自行决定" in content
    assert "可推定项不得抛给用户" in content

    # 多给 user_select：并发问齐、默认项非空、未回答按默认推进。
    assert "多给 `user_select`" in content
    assert "禁止只用纯文本问一句" in content
    assert "每个独立决策点一个问题、同一轮全部并发发出" in content
    assert "`default_options` 必须非空且推荐方案入选" in content
    assert "多选默认全选、单选默认推荐方案" in content
    assert "按默认项推进并写明假设" in content
    assert "禁止串行逐个问、禁止把同一批决策点拆到多轮" in content
    # 拒绝语义仍以 action / selected 为准，不得被「多给 user_select」放宽。
    assert "只有 `action=cancel` 或 `confirmed` 且 `selected` 为空才是拒绝" in content


# ── 第四轮审计：轮次定义、范围外授权、cancel 回显与多选空语义 ──


def test_empty_prompt_defines_question_round_and_exemptions() -> None:
    """轮次必须有唯一计数口径，安全红线确认不得被「最多一轮」锁死。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "**轮次定义（唯一口径）**" in content
    assert "一轮 = 同一回复内**一次性并发发出的一批**" in content
    assert "②/④ 的确认类提问与 ⑤ 的扩权确认**不占用**需求澄清的轮次" in content
    # push / 破坏性确认在用户明确要求执行时可单独开一轮，不被轮次封顶锁死。
    assert "安全红线类确认（`push` / 强制推送 / 不可逆远程 / 破坏性操作）不受此限" in content
    assert "允许单独发起一轮确认（不计入需求澄清轮次）" in content
    # 收尾立即执行，不再承诺无法兑现的「纯文本否决」。
    assert "用户可用纯文本否决" not in content
    assert "用户下一条消息可要求撤销 / 回滚" in content


def test_empty_prompt_scope_gap_cannot_bypass_authorization() -> None:
    """范围外缺陷不得凭默认项改动（安全红线：禁止越权修改未授权文件）。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "**仅限已授权文件内**的非破坏性修复" in content
    assert "**范围外缺陷不得凭需求澄清的默认项改动**" in content
    assert "只能列入未完成清单" in content
    assert "或单独发起 ⑤ 确认" in content


def test_empty_prompt_cancel_selected_is_echo_only() -> None:
    """cancel 的 selected 是 default_options 回显，不得当作选择。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "此时 `selected` 只是 `default_options` 回显" in content
    assert "**一律忽略**，不得当作选择" in content


def test_empty_prompt_multiselect_empty_is_explicit_refusal() -> None:
    """多选提交空 = 明确「一个都不选」，不得回退默认项。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "**单选**：Submit 页以空默认值回填或用户未选" in content
    assert "**多选**提交空视为用户明确「一个都不选」" in content
    assert "不得回退 `default_options`" in content


def test_empty_prompt_tool_failure_without_action() -> None:
    """构造期参数非法返回纯文本错误（无 action），也按调用错误处理。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "工具直接返回 `工具执行失败:` 文本" in content
    assert "**无 `action` 字段**" in content
    assert "不得当作未回答、拒绝或授权" in content


def test_empty_prompt_graded_delivery_does_not_split_writes() -> None:
    """分级交付只是计划/报告层面，落盘仍一次性。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "该分批**只指计划与报告层面**" in content
    assert "仍按〈四条硬红线〉「验证时序」一次性完成" in content


def test_empty_prompt_file_changes_take_lightweight_call_chain() -> None:
    """提词/文档类改动不因文本里的工具名/路径被强制输出全量调用链。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "**提词 / 文档类改动的轻量路径**" in content
    assert "不单独构成「涉及代码」" in content
    assert "定义—引用关系 + 受影响测试" in content


def test_empty_prompt_delegation_gate_has_tool_layer() -> None:
    """空模式禁止子代理必须有 schema 层兜底，不能只靠提词。"""
    content = builder._load_prompt("prompts_export_main_empty")

    assert "从 schema 移除 `subagent` / `subagent_opt`" in content
    # 提词只描述行为，不绑定实现文件 / 函数名。
    assert "filter_schemas_for_empty_mode" not in content
    assert "src/tools/tool_policy.py" not in content


#: 提词交叉引用 → 对应章节标题（后缀匹配；防悬空引用）。
_REFERENCE_TITLES = {
    "〈正向判据〉": "授权闸门 · 正向判据",
    "〈不算授权〉": "授权闸门 · 不算授权",
    "〈提问规则〉": "授权闸门 · 提问规则",
    "〈授权范围〉": "授权闸门 · 授权范围",
    "〈自检与违规回滚〉": "授权闸门 · 自检与违规回滚",
    "〈决策与默认项〉": "协作与安全 · 决策与默认项",
    "〈安全红线〉": "协作与安全 · 安全红线",
    "〈运行时元文件〉": "协作与安全 · 运行时元文件",
    "〈版本控制（Git）〉": "协作与安全 · 版本控制（Git）",
    "〈四条硬红线〉": "工具与验证 · 四条硬红线",
    "〈验证时序〉": "工具与验证 · 验证时序",
    "〈文件与工具〉": "工具与验证 · 文件与工具",
    "〈实操三查与例外〉": "工具与验证 · 实操三查与例外",
    "〈测试〉": "工具与验证 · 测试",
    "〈完成标准〉": "工具与验证 · 完成标准",
}


def test_empty_prompt_cross_references_are_not_dangling() -> None:
    """每个被引用的章节名都必须有对应标题（规则一致性检查）。"""
    content = builder._load_prompt("prompts_export_main_empty")
    titles = [
        line.lstrip("# ").strip()
        for line in content.splitlines()
        if line.startswith("#")
    ]

    for ref, title in _REFERENCE_TITLES.items():
        assert ref in content, f"提词未使用该引用：{ref}"
        assert any(title in heading for heading in titles), f"悬空引用：{ref} → {title}"


def test_empty_prompt_section_titles_are_unique() -> None:
    """章节标题不得重复（重复会让交叉引用指向不明）。"""
    content = builder._load_prompt("prompts_export_main_empty")
    titles = [
        line.strip() for line in content.splitlines()
        if re.match(r"#{1,3} ", line)
    ]
    assert len(titles) == len(set(titles))

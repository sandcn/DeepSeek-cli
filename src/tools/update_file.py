import re

from .file_base import FileToolBase, FileToolError
from .base import tool_metadata


class StringNotFoundError(FileToolError):
    pass

class AmbiguousMatchError(FileToolError):
    pass

class RegexCompileError(FileToolError):
    pass


# ── 正则标志映射 ──────────────────────────────────────────
# regex_flags 字符串 → re 模块 flag。支持 i/m/s/a/x 五个常用标志，
# 可任意组合（如 "im" = IGNORECASE | MULTILINE），大小写均可。
_REGEX_FLAG_MAP = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "a": re.ASCII,
    "x": re.VERBOSE,
}

def _parse_regex_flags(flags: str) -> int:
    """解析 regex_flags 字符串为 re 模块标志位。未知字符抛 RegexCompileError。"""
    if not flags:
        return 0
    result = 0
    for ch in flags:
        key = ch.lower()
        flag = _REGEX_FLAG_MAP.get(key)
        if flag is None:
            raise RegexCompileError(
                f"不支持的 regex_flags 字符: {ch!r}。"
                f"支持: i(忽略大小写) m(多行) s(点匹配换行) a(ASCII) x(详细模式)，可组合如 \"im\"。"
            )
        result |= flag
    return result


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="io",
    priority=10,
    tool_category="write",
    description="更新文件内容",
)
class UpdateFileFunc(FileToolBase):
    name = "update_file"

    @classmethod
    def to_tool_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "update_file",
                "description": (
                    "精确替换文件中的文本片段（old_string→new_string）。"
                    "修改前必须先 read_file 获取最新内容，old_string 从中精确复制（含缩进/换行），禁止凭记忆构造。"
                    "默认精确字符串匹配，old_string 必须唯一；多次出现需加上下文或用 replace_all=true。"
                    "use_regex=true 时按正则匹配，new_string 支持 \\1 反向引用。"
                    "old_string 为空 = 追加到文件末尾；new_string 为空 = 删除 old_string。"
                    "删除代码/函数/类前必须先 search 确认无悬空引用。"
                    "返回：更新结果（成功含行数/字节数）；失败以 ( 开头。匹配失败须重读文件后重试。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "要修改的文件路径（相对或绝对路径）。文件不存在且 old_string 非空时报错「文件不存在或为空」，请改用 write_file 创建或设 old_string 为空实现追加。"
                        },
                        "old_string": {
                            "type": "string",
                            "description": "要被替换的原始文本（use_regex=true 时作为正则模式）。必须从最新 read_file 输出中精确复制（含完整缩进和换行符），禁止凭记忆构造。空字符串表示追加模式（添加到文件末尾）。replace_all=false（默认）时 old_string 在文件中必须恰好出现1次；replace_all=true 时可出现多次，全部替换。"
                        },
                        "new_string": {
                            "type": "string",
                            "description": "替换后的新文本（use_regex=true 时支持反向引用 \\1、\\g<name>）。空字符串表示删除 old_string。每次调用只做一处修改。连续修改同一文件时，后续 old_string 需基于修改后的最新文件内容。"
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "是否替换所有匹配项。false（默认）= 仅替换第1次出现的 old_string，匹配多次时报错；true = 替换文件中所有出现的 old_string。追加模式（old_string=\"\"）忽略此参数。"
                        },
                        "use_regex": {
                            "type": "boolean",
                            "description": "是否将 old_string 视为正则表达式（默认 false 走精确字符串匹配）。true 时 old_string 中的 \\d \\w \\s \\b ^ $ . * + ? ( ) [ ] | 等作为正则元字符处理；new_string 支持 \\1、\\g<name> 反向引用。"
                        },
                        "regex_flags": {
                            "type": "string",
                            "description": "正则标志（仅 use_regex=true 时生效）。字符组合：i=忽略大小写，m=多行（^$ 匹配每行行首尾），s=点匹配换行，a=ASCII，x=详细模式。例如 'im' 表示忽略大小写+多行。"
                        }
                    },
                    "required": ["path", "old_string", "new_string"]
                }
            }
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        path = arguments.get("path", "")
        parts = [f"'{cls._sanitize_display(path)}'"]
        if arguments.get("use_regex"):
            parts.append("[正则]")
        if arguments.get("replace_all"):
            parts.append("[全局替换]")
        return " ".join(parts)

    def __init__(self, path: str, old_string: str, new_string: str, replace_all: bool = False, use_regex: bool = False, regex_flags: str = ""):
        super().__init__(path, content_for_size_check=new_string)
        self.old_string = old_string
        self.new_string = new_string
        self.replace_all = replace_all
        self.use_regex = use_regex
        self.regex_flags = regex_flags

    async def _get_new_content(self) -> str:
        original = await self._read_original()

        # 追加模式
        if not self.old_string:
            if not original:
                new_content = self.new_string
            elif original.endswith('\n'):
                new_content = original + self.new_string
            else:
                new_content = original + '\n' + self.new_string
            # 对追加模式最终内容进行大小校验：original + new_string 可能远超 100MB 限制
            self._check_content_size(new_content)
            return new_content

        if not original:
            raise StringNotFoundError(
                "文件不存在或为空，无法匹配old_string。如需创建新文件请用write_file，如需追加请将old_string设为空。"
            )

        # ── 正则替换模式 ──────────────────────────────────
        if self.use_regex:
            try:
                pattern = re.compile(self.old_string, _parse_regex_flags(self.regex_flags))
            except re.error as e:
                raise RegexCompileError(
                    f"正则表达式编译失败: {e}\n"
                    f"请检查 old_string 的转义（如 \\d、\\.、\\( 等），或修正语法错误。"
                )

            # finditer 惰性迭代：取前两个匹配即可判定歧义，避免全量扫描
            matches = pattern.finditer(original)
            first = next(matches, None)
            if first is None:
                preview = self.old_string[:60].replace('\n', '\\n')
                if len(self.old_string) > 60:
                    preview += '...'
                raise StringNotFoundError(
                    f"正则未匹配到任何内容: /{preview}/\n"
                    f"请先用read_file读取文件确认内容，或调整正则模式。"
                )

            second = next(matches, None)
            if second is not None and not self.replace_all:
                # 统计总匹配次数仅发生在错误路径（罕见），代价可接受
                count = sum(1 for _ in pattern.finditer(original))
                raise AmbiguousMatchError(
                    f"正则 old_string 在文件中匹配了{count}处，无法确定替换哪一处。"
                    f"请让正则更精确，或设置 replace_all=True 进行全局替换。"
                )

            if self.replace_all:
                new_content = pattern.sub(self.new_string, original)
            else:
                new_content = pattern.sub(self.new_string, original, count=1)
            # 对替换后最终内容进行大小校验：正则替换后内容可能膨胀超过限制
            self._check_content_size(new_content)
            return new_content

        # ── 精确字符串替换模式 ─────────────────────────────
        # 优化：用 str.find 定位替代 count+replace 两次全量扫描。
        # 非 replace_all 唯一匹配场景仅需 1-2 次 find + 切片拼接，
        # 避免 count() 和 replace() 各一次 O(n) 全量扫描。
        idx = original.find(self.old_string)
        if idx == -1:
            preview = self.old_string[:60].replace('\n', '\\n')
            if len(self.old_string) > 60:
                preview += '...'
            raise StringNotFoundError(
                f"未找到匹配内容: \"{preview}\"\n"
                f"请先用read_file读取文件，确认要替换的内容与文件中完全一致（包括缩进和空白）。"
            )

        # 检查是否存在第二个匹配（用于非 replace_all 歧义检测）
        second_idx = original.find(self.old_string, idx + len(self.old_string))
        if second_idx != -1 and not self.replace_all:
            # 计算总出现次数用于错误消息（仅错误路径，稀有）
            count = original.count(self.old_string)
            raise AmbiguousMatchError(
                f"old_string在文件中出现了{count}次，无法确定替换哪一处。"
                f"请在old_string中包含更多上下文行使其唯一，"
                f"或设置 replace_all=True 进行全局替换。"
            )

        if self.replace_all:
            new_content = original.replace(self.old_string, self.new_string)
        else:
            # 唯一匹配：切片拼接，避免 replace 的全量扫描
            new_content = original[:idx] + self.new_string + original[idx + len(self.old_string):]
        # 对替换后最终内容进行大小校验：replace_all 多次替换或单次替换后内容可能膨胀超过限制
        self._check_content_size(new_content)
        return new_content

    def _success_verb(self) -> str:
        return "更新成功"

    def _mode_desc(self) -> str:
        if not self.old_string:
            return "追加内容"
        mode = "正则" if self.use_regex else "字符串"
        return f"{mode}全局替换" if self.replace_all else f"{mode}替换"

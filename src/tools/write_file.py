from .file_base import FileToolBase
from .base import tool_metadata


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="io",
    priority=10,
    tool_category="write",
    description="写入文件",
)
class WriteFileFunc(FileToolBase):
    name = "write_file"

    @classmethod
    def to_tool_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": (
                    "创建新文件或整体覆盖写入文件。适用：1.创建新文件；2.重写超过一半内容的文件。"
                    "少量修改用 update_file（更安全）。覆盖写入，UTF-8 编码，原子写入，自动创建父目录；"
                    "写入前建议先 read_file 确认现有内容。"
                    "返回：写入结果（成功含行数/字节数）；失败以 ( 开头。超过 100MB 拒绝；路径穿越被拒绝；沙盒可撤回。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "目标文件路径，相对或绝对路径。父目录不存在时自动创建。"
                        },
                        "content": {
                            "type": "string",
                            "description": "要写入的完整文件内容（UTF-8）。超过 100MB 会被拒绝。"
                        }
                    },
                    "required": ["path", "content"]
                }
            }
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        path = arguments.get("path", "")
        return f"'{path}'"

    def __init__(self, path: str, content: str):
        super().__init__(path, content_for_size_check=content)
        self.content = content

    async def _get_new_content(self) -> str:
        return self.content

    def _success_verb(self) -> str:
        return "写入成功"

    def _mode_desc(self) -> str:
        return "覆盖写入整个文件"

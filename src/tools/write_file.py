from .file_base import FileToolBase
from .base import tool_metadata


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="io",
    priority=10,
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
                "description": "接收 path（目标文件路径）和 content（写入内容）两个参数，将 content 写入 path 指定的文件（覆盖写入）。\n\n覆盖写入整个文件。用途：1.创建新文件 2.重写超过50%内容的文件。少量修改请用update_file（更安全、更高效）。已有文件会被完全覆盖，写入前建议先read_file确认当前内容。UTF-8编码，原子写入（先写临时文件再rename），自动创建父目录。\n\n【边界信息】\n- 最大文件限制：内容超过100MB会被拒绝\n- 路径安全校验：拒绝路径穿越；符号链接指向越界路径时拒绝写入\n- 父目录不存在时自动创建（mkdir -p）\n- 目录无写权限时返回PermissionError\n- 写入前通过沙盒记录文件原内容，支持后续撤回",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件路径，支持相对路径或绝对路径。父目录不存在时自动创建（mkdir -p）。已有文件会被完全覆盖，写入前建议先 read_file 确认当前内容。"
                        },
                        "content": {
                            "type": "string",
                            "description": "要写入的完整文件内容。UTF-8 编码。内容超过 100MB 会被拒绝。采用原子写入：先写入临时文件再 rename 到目标路径，避免写入过程中断导致文件损坏。"
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

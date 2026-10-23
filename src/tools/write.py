from .file_base import FileToolBase


class WriteFunc(FileToolBase):
    name = "write"

    @classmethod
    def to_tool_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "write",
                "description": (
                    "创建新文件或整体覆盖写入，父目录不存在时自动创建；"
                    "内容超过 100MB 拒绝写入。成功返回写入行数与字节数。"
                    "例：write({\"file_path\": \"docs/guide.md\", "
                    "\"content\": \"# 标题\\n正文\"})"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "目标文件路径（支持 ~，如 docs/guide.md）"
                        },
                        "content": {
                            "type": "string",
                            "description": "完整文件内容（整体覆盖）"
                        }
                    },
                    "required": ["file_path", "content"]
                }
            }
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        path = arguments.get("file_path", "")
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

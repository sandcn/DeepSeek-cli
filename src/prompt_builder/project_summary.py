import logging
import os
import re
from typing import List, Tuple, Dict, Any
from ..api.tokens import estimate_tokens

_logger = logging.getLogger(__name__)

_MAX_FILE_CHARS = 1000
# _read_file_contents 的默认 token 上限；调用方可传入自定义值覆盖
_DEFAULT_MAX_TOKENS = 8000
_MAX_WALK_DEPTH = 5

# 敏感模式：匹配后替换为占位符，防止 API key/密钥泄漏到 LLM prompt
_SENSITIVE_PATTERNS: list[tuple[str, str]] = [
    # OpenAI / 常见 API key: sk- 开头 + 字母数字
    (r'\bsk-[A-Za-z0-9]{20,}\b', '[API_KEY_REDACTED]'),
    # 通用 Bearer token / JWT（长 base64 序列）
    # ★ P0 修复: 移除尾 \b，Base64 末尾 = 不匹配 \w，\b 边界失效。
    #   改用否定 lookahead (?![A-Za-z0-9+/=]) 确保后跟非 base64 字符。
    (r'(?<![A-Za-z0-9+/=])[A-Za-z0-9+/=]{40,}(?![A-Za-z0-9+/=])', '[TOKEN_REDACTED]'),
]


def _redact_sensitive(content: str) -> str:
    """对内容应用敏感模式替换，返回脱敏后的字符串。"""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        content = re.sub(pattern, replacement, content)
    return content


def _scan_project_files(cwd: str = ".") -> Tuple[List[Tuple[str, int]], int]:
    """扫描项目文件，返回 (文件信息列表, 总大小)。

    Args:
        cwd: 项目根目录路径，默认为当前目录

    Returns:
        (files_info, total_size) 元组
        files_info: [(文件路径, 文件大小), ...]
        total_size: 所有文件总字节数
    """
    exclude_dirs = {'.git', '__pycache__', '.idea', '.vscode', 'node_modules', 'venv', '.venv', 'env',
                    'dist', 'build', 'target', '.mypy_cache', '.pytest_cache'}
    exclude_ext = {'.pyc', '.pyo', '.so', '.dll', '.exe', '.jpg', '.png', '.gif', '.pdf'}

    files_info: List[Tuple[str, int]] = []
    total_size = 0
    for root, dirs, files in os.walk(cwd, followlinks=False):
        depth = root.count(os.sep)
        if depth >= _MAX_WALK_DEPTH:
            dirs.clear()
            continue
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        for file in files:
            filepath = os.path.join(root, file)
            _, ext = os.path.splitext(file)
            if ext.lower() in exclude_ext:
                continue
            if file.startswith('.'):
                continue
            if file == 'init.md':
                continue
            try:
                size = os.path.getsize(filepath)
            except (IOError, OSError):
                _logger.debug("无法读取文件 %s，跳过", filepath)
                continue
            total_size += size
            files_info.append((filepath, size))

    def file_priority(path: str) -> int:
        ext = os.path.splitext(path)[1].lower()
        if ext in {'.py', '.js', '.ts', '.java', '.cpp', '.c', '.h', '.go', '.rs'}:
            return 0
        elif ext in {'.md', '.txt', '.rst', '.yml', '.yaml', '.json', '.toml', '.ini'}:
            return 1
        elif ext in {'.html', '.css', '.xml'}:
            return 2
        return 3

    files_info.sort(key=lambda x: (file_priority(x[0]), x[0]))
    return files_info, total_size


def _read_file_contents(
    files_info: List[Tuple[str, int]],
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    max_file_chars: int = _MAX_FILE_CHARS,
) -> Tuple[List[Dict[str, Any]], int]:
    """读取文件内容，直到达到 token 上限。

    Args:
        files_info: 文件信息列表 [(路径, 大小), ...]
        max_tokens: 最大 token 数上限
        max_file_chars: 每个文件最多读取字符数

    Returns:
        (file_contents, accumulated_tokens) 元组
        file_contents: [{"path": str, "size": int, "content": str}, ...]
    """
    accumulated_tokens = 0
    file_contents: List[Dict[str, Any]] = []

    for filepath, size in files_info:
        if accumulated_tokens >= max_tokens:
            break
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(max_file_chars)
            content = _redact_sensitive(content)
            tokens = estimate_tokens(content)
            if accumulated_tokens + tokens > max_tokens:
                if accumulated_tokens >= max_tokens:
                    break
                # 截断读取：用剩余配额按比例截取内容，避免超限文件被永久跳过
                ratio = (max_tokens - accumulated_tokens) / tokens
                content = content[:max(1, int(len(content) * ratio))]
                tokens = estimate_tokens(content)
            accumulated_tokens += tokens
            file_contents.append({"path": filepath, "size": size, "content": content})
        except (IOError, OSError):
            continue

    return file_contents, accumulated_tokens


def _build_summary_prompt(
    file_contents: List[Dict[str, Any]],
    files_info: List[Tuple[str, int]],
    total_size: int,
) -> Tuple[str, str]:
    system_prompt = f"""你是资深项目架构师，擅长从源码中提取项目架构、技术栈、编码规范。

根据以下文件内容生成项目摘要，返回给调用方。

# 输出要求
用中文 Markdown 格式，按优先级从高到低包含以下部分（无内容的部分标注"待补充"）：

1. **项目目标与描述** — 核心功能和用途（2-3句，说明项目解决什么问题）
2. **技术栈** — 语言、框架、关键依赖（标注版本要求）
3. **项目架构** — 目录结构、模块职责、关键文件依赖关系
4. **运行方式** — 安装依赖、配置、启动、测试的具体命令
5. **编码约定** — 命名风格、文件组织习惯、错误处理模式、日志规范
6. **注意事项** — 环境要求、已知限制、安全注意点、常见问题

# 质量要求
- 总长度 500-800 字，保持精简
- 避免空泛描述，每个结论必须能从文件内容中追溯
- 不要推测未明确的信息，标注"待补充"
- 文件路径使用相对路径
- 代码示例使用代码块标注语言

# 文件列表（共 {len(files_info)} 个文件，总大小 {total_size} 字节）
"""

    user_prompt = ""
    for item in file_contents:
        user_prompt += f"\n## 文件: {item['path']} (大小: {item['size']} 字节)\n```\n{item['content']}\n```\n"

    return system_prompt, user_prompt


def generate_summary_prompt(project_path: str = ".") -> str:
    """扫描项目文件并构建用于生成项目摘要的 prompt 文本。

    仅构建 prompt，不调用模型。调用方负责将返回的 prompt 传入模型 API。

    Args:
        project_path: 项目路径，默认为当前目录

    Returns:
        prompt 字符串（包含 system 和 user 两段提示文本），
        如果未读取到任何文件内容，返回空字符串。
    """
    files_info, total_size = _scan_project_files(project_path)

    file_contents, accumulated_tokens = _read_file_contents(files_info)

    if not file_contents:
        return ""

    system_prompt, user_prompt = _build_summary_prompt(file_contents, files_info, total_size)

    return f"{system_prompt}\n\n{user_prompt}"

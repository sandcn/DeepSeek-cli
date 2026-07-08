"""代码 fence 检测与常见编程语言白名单。

供 contexts.py / recursive_parser.py 等模块共用。
"""

from __future__ import annotations


# 常见编程语言白名单（流式代码块语言标识检测用）
_COMMON_LANGUAGES: frozenset[str] = frozenset({
    "apache", "arduino", "asm", "assembly", "bash", "c",
    "c++", "cfg", "clojure", "cmake", "cmakefile", "conf",
    "console", "cpp", "csharp", "css", "csv", "cxx",
    "dart", "diff", "docker", "dockerfile", "dotenv", "editorconfig",
    "elixir", "env", "erlang", "f77", "f90", "f95",
    "fortran", "fsharp", "gitignore", "go", "golang", "gql",
    "gradle", "graphql", "h", "haskell", "hcl", "hpp",
    "hs", "html", "http", "ini", "java", "javascript",
    "js", "json", "jsx", "julia", "kt", "kotlin", "latex",
    "less", "lisp", "log", "lua", "make", "makefile",
    "markdown", "matlab", "md", "mermaid", "mysql", "nasm", "nginx",
    "patch", "perl", "php", "pl", "plain", "postgresql",
    "powershell", "proto", "protobuf", "ps1", "py", "python", "r",
    "racket", "rb", "redis", "rest", "rlang", "rs",
    "ruby", "rust", "scala", "scheme", "scss", "sh",
    "shell", "shellscript", "sql", "sqlite", "stata", "svelte", "swift",
    "terminal", "terraform", "tex", "text", "tf", "toml",
    "ts", "tsx", "txt", "typescript", "vb", "vbnet",
    "vue", "wasm",
    "xml", "yaml", "yml", "zsh",
})


def _get_fence_info(stripped: str) -> tuple[str, int, str]:
    """从行中提取 fence 信息。

    返回 (fence_char, fence_len, lang)，如果不是 fence 行则返回 ('', 0, '')。
    """
    if not stripped:
        return ('', 0, '')
    first = stripped[0]
    if first not in ('`', '~'):
        return ('', 0, '')
    count = 0
    for ch in stripped:
        if ch == first:
            count += 1
        else:
            break
    if count < 3:
        return ('', 0, '')
    remaining = stripped[count:].strip()
    lang = ''
    if remaining:
        i = 0
        while i < len(remaining) and (remaining[i].isalnum() or remaining[i] in '+.#_-'):
            i += 1
        if i > 0:
            lang = remaining[:i].lower()
    return (first, count, lang)

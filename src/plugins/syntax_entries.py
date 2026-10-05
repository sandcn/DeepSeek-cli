"""语法高亮语言条目插件 — 清单中每个内置语言一个独立插件条目。

「一切皆插件」：``CodeBlock`` 的内置高亮语言（python/javascript/typescript/go/
rust/java/c/cpp/ruby/shell/sql/yaml/json/css/html）不再硬编码在
``src.tui.ink.widgets._syntax`` 的字典里，而是由清单中的独立条目声明::

    - id: syntax_language_python
      plugin: src.plugins.syntax_entries:apply_syntax_language
      config:
        id: python                          # 内置语言 id（可被 patch/overlay 定位）
        # keywords: [def, return, ...]      # 可选：覆盖关键字表
        # aliases: [py, python3]            # 可选：覆盖别名
        # line_comment: "#"                 # 可选：覆盖行注释前缀

插件挂载时把该 id 的内置语言注册进语言注册表（``spec=None`` 用默认规格）；
卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置语言
随之缺席（``syntax`` 聚合插件经 ``managed_syntax_languages`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Optional

from ..kernel import plugin

#: syntax_language 条目可由 config 覆盖的字段
_SPEC_FIELDS = ("keywords", "aliases", "line_comment")


def _spec_from_config(lang_id: str, config: dict):
    from ..tui.ink.widgets._syntax_registry import SyntaxLanguage, default_language

    if not any(field in config for field in _SPEC_FIELDS):
        return None
    base = default_language(lang_id)
    return SyntaxLanguage(
        id=lang_id,
        keywords=tuple(config.get("keywords", base.keywords)),
        aliases=tuple(config.get("aliases", base.aliases)),
        line_comment=config.get("line_comment", base.line_comment),
    )


@plugin("syntax_language")
def apply_syntax_language(ctx):
    from ..tui.ink.widgets._syntax_registry import register_builtin_language

    lang_id = ctx.config.get("id")
    if not lang_id:
        raise ValueError("syntax_language 条目缺少 config.id")
    undo = register_builtin_language(lang_id, _spec_from_config(lang_id, ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_syntax_language"]

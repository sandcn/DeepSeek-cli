"""builder — ASTNode → VNode 转换器。

将语义 AST 树转换为扁平的渲染 VNode 树。
VNode 专注于渲染属性和增量更新追踪。
"""

from __future__ import annotations

from ..ast.types import ASTNode, NodeType
from .types import VNode, VNodeType


class VNodeBuilder:
    """ASTNode → VNode 转换器。

    将语义 AST 树转换为扁平的渲染 VNode 树。
    VNode 专注于渲染属性和增量更新追踪。

    用法：
      builder = VNodeBuilder()
      vnode = builder.build(ast_root)          # 构建完整 VNode 树
      vnodes = builder.build_all(ast_children)  # 构建多个根级 VNode
    """

    def __init__(self):
        self._key_counters: dict[str, int] = {}

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    def build(self, node: ASTNode) -> VNode:
        """将 ASTNode 转换为 VNode（递归转换所有子节点）。

        对于展平类型（CODE_BLOCK），返回一个虚拟 ROOT 节点
        包含展平后的子节点序列（适用于根级节点转换）。

        Args:
            node: 要转换的 ASTNode

        Returns:
            转换后的 VNode（含递归完成的子节点）
        """
        # 展平类型处理（可在根级出现的类型，展开为子节点序列）
        if node.type == NodeType.CODE_BLOCK:
            children = self._build_code_block(node)
            return self._make_flat(children)

        if node.type == NodeType.LIST:
            children = self._build_children(node.children, context={})
            return self._make_flat(children)

        if node.type == NodeType.ORDERED_LIST:
            children = self._build_children(node.children, context={'ordered': True})
            return self._make_flat(children)

        # SECTION 不应出现在根级（已被 ASTBuilder/Optimizer 处理）
        if node.type == NodeType.SECTION:
            raise ValueError(
                f"不支持的节点类型: SECTION。"
                f" 请使用 build_all(section.children) 代替。"
            )

        vnode = self._to_vnode(node, context={})
        # DETAILS 和 ADMONITION 已将 children 内容拼入 content，不递归子 VNode
        if node.type in (NodeType.DETAILS, NodeType.ADMONITION):
            vnode.children = []
        else:
            vnode.children = self._build_children(node.children, context={})
        return vnode

    def build_all(self, nodes: list[ASTNode]) -> list[VNode]:
        """转换多个根级 ASTNode 为 VNode 列表。

        所有节点统一由 _next_key 分配 key，保证每次构建产生相同 key，
        使 VNodeDiffer 能正确匹配前后两次渲染的同一节点。

        展平类型（CODE_BLOCK/LIST/ORDERED_LIST）自动展开为多个 VNode，
        不再经过 build() 的 flat wrapper 再解包，直接处理。

        Args:
            nodes: ASTNode 列表

        Returns:
            VNode 列表（展平后）
        """
        result: list[VNode] = []
        # 清除计数器，确保每次 build_all 从零开始
        self._key_counters.clear()

        for node in nodes:
            if node.type == NodeType.CODE_BLOCK:
                # CODE_BLOCK 直接拆为围栏+行序列，不经过 build() 的 flat wrapper
                vnodes = self._build_code_block(node)
                result.extend(vnodes)
            elif node.type in (NodeType.LIST, NodeType.ORDERED_LIST):
                # LIST / ORDERED_LIST 展平为子节点序列
                ctx = {'ordered': True} if node.type == NodeType.ORDERED_LIST else {}
                vnodes = self._build_children(node.children, context=ctx)
                result.extend(vnodes)
            elif node.type == NodeType.SECTION:
                # SECTION 展平为子节点序列
                result.extend(self._build_children(node.children, {}))
            else:
                # 普通节点：通过 build() 转换（保持向后兼容）
                vnode = self.build(node)
                if (vnode.type is VNodeType.ROOT
                        and vnode.key.startswith('flat:')
                        and vnode.children):
                    # 展平包装回退路径（兼容 build() 可能返回 flat wrapper 的其他场景）
                    result.extend(vnode.children)
                else:
                    result.append(vnode)

        return result

    # ──────────────────────────────────────────────
    # Key 生成
    # ──────────────────────────────────────────────

    def _next_key(self, prefix: str) -> str:
        """生成稳定递增的 key，如 'p:0', 'p:1', 'cl:0:0'。

        Args:
            prefix: key 前缀（如 'p', 'h', 'cl'）

        Returns:
            格式化的 key 字符串
        """
        counter = self._key_counters.get(prefix, 0)
        self._key_counters[prefix] = counter + 1
        return f"{prefix}:{counter}"

    # ──────────────────────────────────────────────
    # 节点转换（单节点）
    # ──────────────────────────────────────────────

    def _to_vnode(self, node: ASTNode, context: dict) -> VNode:
        """单个 ASTNode → VNode 转换（不含 children 处理）。

        Args:
            node: 源 ASTNode
            context: 继承上下文（如 bullet=False 等父级传递属性）

        Returns:
            转换后的 VNode（children 为空列表）

        Raises:
            ValueError: 不支持的节点类型
        """
        vtype, prefix = self._resolve_type(node)
        vn = VNode(type=vtype, key=self._next_key(prefix), content=node.content)

        # 填充节点特定属性
        self._apply_node_props(vn, node)

        # 应用继承上下文属性（父级透传，不覆盖已有属性）
        for k, v in context.items():
            if k not in vn.props:
                vn.props[k] = v

        return vn

    @staticmethod
    def _resolve_type(node: ASTNode) -> tuple[VNodeType, str]:
        """根据 ASTNode 类型解析对应的 VNodeType 和 key 前缀。

        Returns:
            (VNodeType, key_prefix) 元组

        Raises:
            ValueError: 不支持/展平类型（SECTION/LIST/ORDERED_LIST）
        """
        # ── 展平类型（不生成 VNode，必须在 _build_children 中处理） ──
        flatten_types = {
            NodeType.SECTION,
            NodeType.LIST,
            NodeType.ORDERED_LIST,
            NodeType.CODE_BLOCK,  # 拆为多节点序列
        }
        if node.type in flatten_types:
            raise ValueError(
                f"不支持的节点类型: {node.type.name}。"
                f" 该类型为展平类型（{', '.join(t.name for t in flatten_types)}），"
                f"不能单独构建，须通过父节点的 _build_children 处理。"
            )

        mapping = {
            NodeType.DOCUMENT: (VNodeType.ROOT, 'root'),
            NodeType.PARAGRAPH: (VNodeType.PARAGRAPH, 'p'),
            NodeType.HEADING: (VNodeType.HEADING, 'h'),
            NodeType.HR: (VNodeType.HR, 'hr'),
            NodeType.EMPTY_LINE: (VNodeType.EMPTY, 'e'),
            NodeType.BLOCKQUOTE: (VNodeType.BLOCKQUOTE, 'bq'),
            NodeType.LIST_ITEM: (VNodeType.LIST_ITEM, 'li'),
            NodeType.DEFINITION_ITEM: (VNodeType.DEFINITION_ITEM, 'di'),
            NodeType.MATH_BLOCK: (VNodeType.MATH, 'math'),
            NodeType.MERMAID_BLOCK: (VNodeType.MERMAID, 'mm'),
            NodeType.TABLE: (VNodeType.TABLE, 'tbl'),
            NodeType.DETAILS: (VNodeType.DETAILS, 'det'),
            NodeType.ADMONITION: (VNodeType.ADMONITION, 'adm'),
            NodeType.HTML_BLOCK: (VNodeType.HTML_BLOCK, 'html'),
            NodeType.HTML_LINE: (VNodeType.HTML_LINE, 'hl'),
        }
        if node.type not in mapping:
            raise ValueError(f"不支持的节点类型: {node.type.name}。")
        return mapping[node.type]

    @staticmethod
    def _apply_node_props(vn: VNode, node: ASTNode) -> None:
        """根据 ASTNode 类型填充 VNode 的 props 和 content。"""
        if node.type == NodeType.HEADING:
            vn.props['level'] = node.meta.get('level', 1)

        elif node.type == NodeType.BLOCKQUOTE:
            vn.props['depth'] = node.meta.get('depth', 1)

        elif node.type == NodeType.LIST_ITEM:
            vn.props['depth'] = node.meta.get('depth', 0)
            # ordered 优先从 meta 取（bullet=False→ordered），否则由上下文注入
            if 'bullet' in node.meta:
                vn.props['ordered'] = not node.meta['bullet']
            if 'number' in node.meta:
                vn.props['number'] = node.meta['number']
            # 任务列表（checkbox）状态
            if node.meta.get('todo'):
                vn.props['todo'] = True
                vn.props['checked'] = node.meta.get('checked', False)

        elif node.type == NodeType.DEFINITION_ITEM:
            vn.props['term'] = node.meta.get('term', '')

        elif node.type == NodeType.TABLE:
            vn.props['rows'] = node.meta.get('rows', [])
            vn.props['alignments'] = node.meta.get('alignments', [])

        elif node.type == NodeType.DETAILS:
            vn.props['summary'] = node.meta.get('summary', '')
            # content 为子节点内容行拼接
            vn.content = _join_children_content(node)

        elif node.type == NodeType.ADMONITION:
            vn.props['type'] = node.meta.get('type', 'note')
            # content 为子节点内容行拼接
            vn.content = _join_children_content(node)

        elif node.type == NodeType.HTML_BLOCK:
            vn.props['tag'] = node.meta.get('tag', 'div')

        # CODE_BLOCK 特殊处理在 _build_code_block 中

    # ──────────────────────────────────────────────
    # 子节点处理（展平逻辑）
    # ──────────────────────────────────────────────

    def _build_children(
        self,
        children: list[ASTNode],
        context: dict,
    ) -> list[VNode]:
        """处理 AST 子节点列表，展平 SECTION/LIST/ORDERED_LIST。

        Args:
            children: ASTNode 子节点列表
            context: 继承上下文（如 ORDERED_LIST → bullet=False）

        Returns:
            展平后的 VNode 列表
        """
        vnodes: list[VNode] = []
        for child in children:
            if child.type == NodeType.SECTION:
                # SECTION 展平：不生成 VNode，直接递归子节点
                vnodes.extend(self._build_children(child.children, context))

            elif child.type == NodeType.LIST:
                # LIST 展平：不生成 VNode，直接递归子节点
                vnodes.extend(self._build_children(child.children, context))

            elif child.type == NodeType.ORDERED_LIST:
                # ORDERED_LIST 展平：不生成 VNode，注入 ordered=True
                ctx = dict(context, ordered=True)
                vnodes.extend(self._build_children(child.children, ctx))

            elif child.type == NodeType.CODE_BLOCK:
                # CODE_BLOCK 拆为围栏+行序列
                vnodes.extend(self._build_code_block(child))

            else:
                vn = self._to_vnode(child, context)
                vn.children = self._build_children(child.children, context={})
                vnodes.append(vn)

        return vnodes

    # ──────────────────────────────────────────────
    # 展平包装
    # ──────────────────────────────────────────────

    @staticmethod
    def _make_flat(children: list[VNode]) -> VNode:
        """将子节点列表包装为 ROOT 节点（用于 build_all 展开）。"""
        return VNode(
            type=VNodeType.ROOT,
            key='flat:0',
            children=children,
        )

    # ──────────────────────────────────────────────
    # 代码块拆分
    # ──────────────────────────────────────────────

    def _build_code_block(self, node: ASTNode) -> list[VNode]:
        """将 CODE_BLOCK 拆为 CODE_FENCE + CODE_LINE×N + CODE_FENCE。

        Args:
            node: CODE_BLOCK 类型的 ASTNode

        Returns:
            围栏+行序列 VNode 列表
        """
        lang = node.meta.get('lang', '')
        content = node.content
        # ★ 只去除一个尾换行，保留代码块中有意保留的尾空行
        # rstrip('\n') 会移除所有尾换行，导致 "line1\n\n" → ['line1'] 丢失尾空行
        if content:
            if content.endswith('\n'):
                content = content[:-1]
            lines = content.split('\n')
        else:
            lines = []

        # ── 打开围栏 ──
        fence_content = f"```{lang}" if lang else "```"
        open_fence = VNode(
            type=VNodeType.CODE_FENCE,
            key=self._next_key('cf'),
            content=fence_content,
            props={'lang': lang, 'title': node.meta.get('title', '')},
        )

        # ── 代码行 ──
        code_lines: list[VNode] = []
        for i, line in enumerate(lines):
            code_lines.append(VNode(
                type=VNodeType.CODE_LINE,
                key=self._next_key('cl'),
                content=line,
                props={'line_number': i + 1, 'lang': lang},
            ))

        # ── 关闭围栏 ──
        close_fence = VNode(
            type=VNodeType.CODE_FENCE,
            key=self._next_key('cf'),
            content="```",
            props={'lang': lang, 'indented': False},
        )

        return [open_fence] + code_lines + [close_fence]


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def _join_children_content(node: ASTNode) -> str:
    """将 ASTNode 子节点的 content 用换行符拼接。

    用于 DETAILS 和 ADMONITION 等需要行拼接的节点类型。
    """
    if node.children:
        return '\n'.join(c.content for c in node.children if c.content)
    return node.content

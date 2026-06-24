"""Virtual DOM layer — VNode, diff/patch, hooks, focus, layout."""
from .builder import build_vnode_tree  # noqa: F401
from .focus import FocusManager, use_focus  # noqa: F401
from .hooks import (  # noqa: F401
    use_state,
    use_effect,
    use_ref,
    use_memo,
    use_callback,
    use_context,
    use_reducer,
    create_context,
    get_hooks_runtime,
    _HooksRuntime,
)
from .layout import FlexLayout  # noqa: F401
from .types import HookState, EffectState, LayoutBox  # noqa: F401
from .vnode import VNode, Patch, diff, apply_patches  # noqa: F401

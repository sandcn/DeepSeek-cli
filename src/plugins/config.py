"""配置插件 — 提供 ``ctx.config``。

包装 ``src/config`` 子系统：读取/持久化运行时配置、base_url、模型列表。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class ConfigService(Service):
    """配置服务 — 占据 ``ctx.config``。"""

    provide = "config"
    name = "config"

    def rc(self) -> dict:
        from ..config.loader import get_rc

        return get_rc()

    def get(self, key: str, default=None):
        rc = self.rc()
        if key in rc:
            return rc[key]
        try:
            from ..config import CONFIG_KEYS

            if key in CONFIG_KEYS:
                path = CONFIG_KEYS[key]["rc_path"]
                value = rc
                for part in path:
                    if not isinstance(value, dict) or part not in value:
                        return CONFIG_KEYS[key]["default"]
                    value = value[part]
                return value
        except Exception:
            pass
        return default

    def update(self, key: str, value) -> None:
        from ..config.loader import update_config

        update_config(key, value)

    def base_url(self, provider=None) -> str:
        from ..config.loader import get_base_url

        return get_base_url(provider)

    def model(self) -> str:
        from ..config import MODEL

        return MODEL

    def low_model(self) -> str:
        from ..config import LOW_MODEL

        return LOW_MODEL

    def models(self) -> list[str]:
        from ..config import MODELS

        return list(MODELS or [])

    def token_prices(self) -> dict:
        from ..config import TOKEN_PRICES

        return TOKEN_PRICES

    def defaults(self) -> dict:
        from ..config.defaults import DEFAULTS

        return DEFAULTS

    def providers(self) -> dict:
        from ..config.defaults import PROVIDERS

        return PROVIDERS

    @property
    def port(self):
        """供 Agent 注入的 ConfigPort 适配器。"""
        from ..core.adapters.config import DefaultConfigAdapter

        return DefaultConfigAdapter()


@plugin("config", provide=["config"])
def apply(ctx):
    return ConfigService(ctx)

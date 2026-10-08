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
        """接口地址：当前生效档案地址；``provider`` 给定时用其内置默认地址。"""
        if provider:
            from ..config.loader import get_base_url

            return get_base_url(provider)
        from ..config import BASE_URL

        return BASE_URL

    def model(self) -> str:
        """当前模型名（唯一来源：模型档案 ``model_profiles``）。"""
        from ..config.model_profiles import current_model

        return current_model()

    def models(self) -> list[str]:
        """配置的模型列表（模型档案 ``model_profiles`` 的模型名）。"""
        from ..config.model_profiles import configured_models

        return configured_models()

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

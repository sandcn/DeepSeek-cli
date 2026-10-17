"""内置提供商模型列表（/models 候选）与各提供商官方最新模型同步测试。

2026-10-17 用户需求：「/models 增加对应提供商最新的模型列表」——``/models``
新增/编辑模型档案时，「模型名」字段候选取自 ``defaults.PROVIDERS[provider]``
的 ``models``（``config.model_profiles.provider_models`` /
``provider_model_choices``）。本文件固化该列表与各提供商**官方最新模型**的
一致性：

  1. 结构：每个 provider 具备 base_url / default_model / models / token_prices，
     且 default_model 必在 models 内（custom 无内置模型，不参与）；
  2. 最新模型：deepseek / anthropic / glm / mimo 各自最新一代模型在列；
  3. 计费口径：美元计价 provider（deepseek / anthropic）价格键与模型一一对应；
     人民币计价 provider（glm / mimo）不内置单价，避免单位混淆；
  4. 联动：``provider_model_choices`` 的「默认」标记独占、``infer_provider``
     能按最新模型名推断 provider、``is_multimodal_model`` 覆盖最新多模态模型。
"""

from __future__ import annotations

import pytest

from src.config import model_profiles as mp
from src.config.defaults import PROVIDERS
from src.core.multimodal import clear_multimodal_cache, is_multimodal_model

#: 以美元 / 百万 tokens 计价的 provider（token_prices 内建单价）
_USD_PROVIDERS = ("deepseek", "anthropic")
#: 以人民币计价的 provider（不并入美元价格表）
_RMB_PROVIDERS = ("glm", "mimo")
#: 参与结构校验的 provider（custom 无内置模型）
_STRUCTURED_PROVIDERS = ("deepseek", "anthropic", "glm", "mimo")

#: 各提供商当前（2026-10）官方最新一代模型
_LATEST_MODELS = {
    "deepseek": ("deepseek-flash", "deepseek-v4-pro"),
    "anthropic": (
        "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1",
    ),
    "glm": ("glm-5.3", "glm-5.3-flash", "glm-5.3-flashx"),
    "mimo": ("mimo-v2.6-pro", "mimo-v2.6-flash", "mimo-v2.6-pro-ultraspeed"),
}

#: 各提供商默认模型（官方推荐 / 旗舰）
_DEFAULT_MODELS = {
    "deepseek": "deepseek-v4-pro",
    "anthropic": "claude-opus-5-5",
    "glm": "glm-5.3",
    "mimo": "mimo-v2.6-pro",
}


# ── 1. 结构完整性 ─────────────────────────────────────

@pytest.mark.parametrize("provider", _STRUCTURED_PROVIDERS)
def test_provider_metadata_shape(provider):
    """每个 provider 元数据结构完整，且默认模型在候选列表内。"""
    cfg = PROVIDERS[provider]
    assert cfg["base_url"], f"{provider} 缺少 base_url"
    assert isinstance(cfg["models"], list) and cfg["models"], f"{provider} 内置模型为空"
    assert isinstance(cfg["token_prices"], dict)
    default = cfg["default_model"]
    assert default, f"{provider} 缺少 default_model"
    assert default in cfg["models"], f"{provider} 默认模型 {default} 不在候选列表内"


@pytest.mark.parametrize("provider", _STRUCTURED_PROVIDERS)
def test_provider_models_unique_and_stripped(provider):
    """候选列表去重、无空白项（``provider_models`` 与元数据同源）。"""
    models = mp.provider_models(provider)
    assert models == list(PROVIDERS[provider]["models"])
    assert len(models) == len(set(models))
    assert all(m == m.strip() and m for m in models)


# ── 2. 最新模型在列 ───────────────────────────────────

@pytest.mark.parametrize("provider", sorted(_LATEST_MODELS))
def test_latest_models_listed(provider):
    """各提供商官方最新一代模型均进入候选列表。"""
    models = mp.provider_models(provider)
    for expected in _LATEST_MODELS[provider]:
        assert expected in models, f"{provider} 候选缺少最新模型 {expected}"


@pytest.mark.parametrize("provider", sorted(_DEFAULT_MODELS))
def test_default_model_matches_official(provider):
    """默认模型 = 官方推荐 / 旗舰模型。"""
    assert mp.default_model_for(provider) == _DEFAULT_MODELS[provider]


def test_custom_provider_has_no_builtin_models():
    """custom 无内置模型（纯手动输入，零回归）。"""
    assert mp.provider_models("custom") == []
    assert mp.default_model_for("custom") == ""
    assert mp.provider_model_choices("custom") == []


# ── 3. 计费口径 ───────────────────────────────────────

@pytest.mark.parametrize("provider", _USD_PROVIDERS)
def test_usd_prices_cover_all_models(provider):
    """美元计价 provider：价格键与候选模型一一对应。"""
    prices = PROVIDERS[provider]["token_prices"]
    assert set(prices) == set(mp.provider_models(provider))


@pytest.mark.parametrize("provider", _RMB_PROVIDERS)
def test_rmb_providers_have_no_builtin_prices(provider):
    """人民币计价 provider 不内置单价（避免与美元口径混淆）。"""
    assert PROVIDERS[provider]["token_prices"] == {}
    descs = dict(mp.provider_model_choices(provider))
    assert descs[mp.default_model_for(provider)] == "默认"
    assert all("$" not in desc for desc in descs.values())


@pytest.mark.parametrize("provider", _USD_PROVIDERS)
def test_price_values_sane(provider):
    """单价数值合理：输出 ≥ 输入，缓存命中价 ≤ 输入价。"""
    for model, price in PROVIDERS[provider]["token_prices"].items():
        assert price["input"] > 0 and price["output"] > 0, f"{model} 单价非正"
        assert price["output"] >= price["input"], f"{model} 输出单价低于输入单价"
        cache_hit = price.get("input_cache_hit")
        if cache_hit is not None:
            assert 0 < cache_hit <= price["input"], f"{model} 缓存命中价异常"


def test_deepseek_latest_prices():
    """DeepSeek 最新官方价（美元 / 百万 tokens，高峰价）。"""
    prices = PROVIDERS["deepseek"]["token_prices"]
    assert prices["deepseek-flash"] == {"input": 0.3, "output": 1.2, "input_cache_hit": 0.006}
    assert prices["deepseek-v4-pro"] == {"input": 1.32, "output": 3.96, "input_cache_hit": 0.044}


def test_anthropic_latest_prices():
    """Anthropic 5.5 代官方价（美元 / 百万 tokens，标准档）。"""
    prices = PROVIDERS["anthropic"]["token_prices"]
    assert prices["claude-opus-5-5"] == {"input": 4.0, "output": 20.0, "input_cache_hit": 0.20}
    assert prices["claude-sonnet-5-5"] == {"input": 2.0, "output": 10.0, "input_cache_hit": 0.10}
    assert prices["claude-haiku-5-5"] == {"input": 0.10, "output": 0.50, "input_cache_hit": 0.01}
    assert prices["claude-fable-5-1"] == {"input": 10.0, "output": 50.0, "input_cache_hit": 0.25}


# ── 4. 候选 / 推断 / 多模态联动 ───────────────────────

@pytest.mark.parametrize("provider", sorted(_DEFAULT_MODELS))
def test_default_marker_exclusive(provider):
    """「默认」标记只出现在默认模型行，其余行回退通用描述。"""
    choices = dict(mp.provider_model_choices(provider))
    default = mp.default_model_for(provider)
    for model, desc in choices.items():
        if model == default:
            assert "默认" in desc
        else:
            assert "默认" not in desc


@pytest.mark.parametrize(
    "model,provider",
    [
        ("deepseek-flash", "deepseek"),
        ("claude-opus-5-5", "anthropic"),
        ("claude-haiku-5-5", "anthropic"),
        ("glm-5.3-flash", "glm"),
        ("mimo-v2.6-pro", "mimo"),
    ],
)
def test_infer_provider_from_latest_models(model, provider):
    """按最新模型名可推断 provider（/model 序号、档案 provider 推断依赖）。"""
    assert mp.infer_provider(model) == provider


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-opus-5-5", True),
        ("claude-sonnet-5-5", True),
        ("glm-5.3-flash", True),
        ("glm-5.3-flashx", True),
        ("mimo-v2.6-pro", True),
        ("mimo-v2.6-flash", True),
        ("deepseek-flash", True),
        ("deepseek-v4-pro", False),
    ],
)
def test_multimodal_detection_for_latest_models(model, expected):
    """最新多模态模型判定：Claude 5.5 / GLM-5.3-Flash / MiMo-V2.6 判为多模态。"""
    clear_multimodal_cache()
    try:
        assert is_multimodal_model(model) is expected
    finally:
        clear_multimodal_cache()

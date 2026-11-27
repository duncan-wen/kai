"""TTSConfig：依 engine 補預設值、/tts 切換保留哪些欄位。"""

import pytest

from core.agent_factory import AgentDefinition
from core.tts import TTSConfig, switch_engine


def test_kokoro_api_without_port_or_voice_uses_its_own_defaults() -> None:
    cfg = TTSConfig.from_dict({"engine": "kokoro-api"})
    assert cfg is not None
    assert (cfg.port, cfg.voice, cfg.lang) == (8885, "af_bella", "en-us")


def test_tts_true_means_edge_tts_with_aria() -> None:
    raw = AgentDefinition(name="a", model="m", system_prompt="s", tts=True).tts
    cfg = TTSConfig.from_dict(raw)
    assert cfg is not None
    assert (cfg.engine, cfg.voice, cfg.player) == (
        "edge-tts",
        "en-US-AriaNeural",
        "auto",
    )


def test_explicit_values_win_and_are_coerced() -> None:
    cfg = TTSConfig.from_dict(
        {"engine": "melotts", "voice": "ZH", "port": "9000", "speed": "1.5"}
    )
    assert cfg is not None
    assert (cfg.voice, cfg.port, cfg.speed) == ("ZH", 9000, 1.5)


@pytest.mark.parametrize("raw", [None, {}])
def test_missing_config_is_disabled(raw) -> None:
    assert TTSConfig.from_dict(raw) is None


def test_unknown_engine_is_disabled() -> None:
    assert TTSConfig.from_dict({"engine": "kokoro_api"}) is None


def test_switch_keeps_preferences_and_drops_engine_fields() -> None:
    raw = {
        "engine": "kokoro-api",
        "voice": "af_bella",
        "port": 8885,
        "host": "gpu-box",
        "speed": 1.3,
        "player": "ffplay",
    }
    new = switch_engine(raw, "melotts")
    assert new == {"engine": "melotts", "speed": 1.3, "player": "ffplay"}
    cfg = TTSConfig.from_dict(new)
    assert cfg is not None
    assert (cfg.port, cfg.host, cfg.voice) == (8886, "localhost", "EN-US")


def test_switch_from_nothing() -> None:
    assert switch_engine(None, "kokoro") == {"engine": "kokoro"}


def test_set_tts_schema_enum_matches_engines() -> None:
    from core.tts import ENGINES
    from mixins.app_tool_schemas import SET_TTS_TOOL

    enum = SET_TTS_TOOL["function"]["parameters"]["properties"]["engine"]["enum"]
    assert set(enum) == {*ENGINES, "off"}

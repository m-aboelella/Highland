from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError

from highland.settings import HighlandSettings, ModelBackend


def test_settings_import_has_no_network_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("settings import attempted network access")

    monkeypatch.setattr("socket.create_connection", unexpected_network)
    import highland.settings

    importlib.reload(highland.settings)


def test_settings_read_explicit_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("HIGHLAND_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("HIGHLAND_MODEL_BACKEND", "cohere")
    monkeypatch.setenv("COHERE_API_KEY", "secret-value")
    monkeypatch.setenv("HIGHLAND_CONNECTOR_COMMANDS", '{"archive":["python","connector.py"]}')

    settings = HighlandSettings()

    assert settings.workspace_dir == tmp_path
    assert settings.model_backend is ModelBackend.COHERE
    assert settings.cohere_api_key is not None
    assert settings.cohere_api_key.get_secret_value() == "secret-value"
    assert settings.connector_commands == {"archive": ("python", "connector.py")}
    assert "secret-value" not in repr(settings)


def test_invalid_limits_fail_during_configuration() -> None:
    with pytest.raises(ValidationError):
        HighlandSettings(max_model_calls_per_run=0)

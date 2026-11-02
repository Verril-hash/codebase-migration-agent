"""Tests for the LLM client: request shape, token counting, failures and settings.

No test calls the API: the SDK's ``messages.create`` is replaced by a fake.
"""

from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from migration_agent.llm.client import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    FALLBACK_BETA,
    MAX_RETRIES,
    LLMClient,
    LLMError,
    LLMSettings,
    read_env_file,
    usage_tokens,
)

DIFF = "--- a/app/db.py\n+++ b/app/db.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
ENV_NAMES = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_EFFORT", "LLM_MAX_TOKENS")


def reply(text=DIFF, stop="end_turn", tokens=(120, 30), iterations=None, details=None):
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(
            input_tokens=tokens[0], output_tokens=tokens[1], iterations=iterations
        ),
        stop_reason=stop,
        stop_details=details,
        model=DEFAULT_MODEL,
    )


class FakeCreate:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.params: list[dict] = []

    def __call__(self, **params):
        self.params.append(params)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No setting leaks in from the developer's shell or .env file."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def client_with(*responses, **settings) -> tuple[LLMClient, FakeCreate]:
    fake = FakeCreate(*responses)
    return LLMClient(LLMSettings(**settings), create=fake), fake


# ----------------------------------------------------------------------------- requests
def test_complete_returns_only_the_text_and_counts_tokens() -> None:
    client, _ = client_with(reply(), reply(tokens=(10, 5)))
    assert client.complete("You repair code.", "Fix app/db.py") == (DIFF, 120, 30)
    client.complete("You repair code.", "Fix it again")
    assert (client.calls, client.tokens_in, client.tokens_out) == (2, 130, 35)


def test_the_request_uses_effort_and_fallbacks_and_no_sampling_parameters() -> None:
    client, fake = client_with(reply())
    client.complete("You repair code.", "Fix app/db.py")
    params = fake.params[0]
    assert params["model"] == DEFAULT_MODEL
    assert params["max_tokens"] == DEFAULT_MAX_TOKENS
    assert params["system"] == "You repair code."
    assert params["messages"] == [{"role": "user", "content": "Fix app/db.py"}]
    assert params["output_config"] == {"effort": "high"}
    assert params["betas"] == [FALLBACK_BETA] and params["fallbacks"] == "default"
    for removed in ("temperature", "top_p", "top_k", "thinking"):
        assert removed not in params


def test_a_model_without_server_fallback_gets_none() -> None:
    client, fake = client_with(reply(), model="claude-haiku-5-5")
    client.complete("system", "prompt")
    assert "fallbacks" not in fake.params[0] and "betas" not in fake.params[0]


def test_no_effort_and_no_system_are_left_out() -> None:
    client, fake = client_with(reply(), effort=None)
    client.complete("  ", "prompt")
    assert "output_config" not in fake.params[0] and "system" not in fake.params[0]


def test_an_empty_prompt_is_refused_before_any_call() -> None:
    client, fake = client_with(reply())
    with pytest.raises(ValueError, match="empty"):
        client.complete("system", "   ")
    assert fake.params == []


# ----------------------------------------------------------------------------- outcomes
def test_a_fallback_counts_every_attempt() -> None:
    attempts = [
        SimpleNamespace(type="message", input_tokens=100, output_tokens=5),
        SimpleNamespace(type="fallback_message", input_tokens=100, output_tokens=40),
    ]
    assert usage_tokens(
        SimpleNamespace(input_tokens=100, output_tokens=40, iterations=attempts)
    ) == (
        200,
        45,
    )
    client, _ = client_with(reply(tokens=(100, 40), iterations=attempts))
    assert client.complete("system", "prompt")[1:] == (200, 45)


def test_a_refusal_raises_and_still_counts_its_tokens() -> None:
    details = SimpleNamespace(category="cyber", explanation="declined")
    client, _ = client_with(reply(text="", stop="refusal", tokens=(80, 0), details=details))
    with pytest.raises(LLMError, match=r"declined the request \(cyber\)") as caught:
        client.complete("system", "prompt")
    assert (caught.value.tokens_in, caught.value.tokens_out) == (80, 0)
    assert client.tokens_in == 80


def test_a_reply_cut_off_at_max_tokens_raises() -> None:
    client, _ = client_with(reply(stop="max_tokens"), max_tokens=500)
    with pytest.raises(LLMError, match="cut off at 500 tokens"):
        client.complete("system", "prompt")


def test_a_reply_without_text_raises() -> None:
    client, _ = client_with(reply(text=""))
    with pytest.raises(LLMError, match="no text"):
        client.complete("system", "prompt")


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls, status: int, message: str):
    return cls(message, response=httpx2.Response(status, request=REQUEST), body=None)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), "LLM_API_KEY"),
        (status_error(anthropic.NotFoundError, 404, "model not found"), "unknown model"),
        (status_error(anthropic.RateLimitError, 429, "slow down"), f"after {MAX_RETRIES}"),
        (status_error(anthropic.BadRequestError, 400, "bad field"), "rejected the request"),
        (status_error(anthropic.InternalServerError, 500, "oops"), "status 500"),
        (anthropic.APIConnectionError(request=REQUEST), "cannot reach"),
    ],
)
def test_sdk_errors_become_clear_llm_errors(error: Exception, expected: str) -> None:
    client, _ = client_with(error, api_key="sk-ant-secret-123")
    with pytest.raises(LLMError, match=expected) as caught:
        client.complete("system", "prompt")
    assert "sk-ant-secret-123" not in str(caught.value)


def test_the_sdk_client_gets_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[dict] = []

    class FakeAnthropic:
        def __init__(self, **options) -> None:
            built.append(options)
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=FakeCreate(reply())))

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    LLMClient(LLMSettings(api_key="sk-ant-key", base_url="http://proxy:8080")).complete("s", "p")
    LLMClient(LLMSettings()).complete("s", "p")
    assert built[0] == {
        "max_retries": MAX_RETRIES,
        "timeout": 600.0,
        "api_key": "sk-ant-key",
        "base_url": "http://proxy:8080",
    }
    assert "api_key" not in built[1] and "base_url" not in built[1]


# ----------------------------------------------------------------------------- settings
def test_defaults_without_any_configuration() -> None:
    settings = LLMSettings.from_env()
    assert settings == LLMSettings()
    assert (settings.model, settings.effort, settings.api_key) == (DEFAULT_MODEL, "high", None)


def test_settings_come_from_dotenv_and_the_environment_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "# local settings\n"
        'LLM_API_KEY="sk-ant-from-file"\n'
        "export LLM_MODEL=claude-sonnet-5-5\n"
        "LLM_EFFORT=Medium\n"
        "LLM_MAX_TOKENS=8000\n"
        "not a setting\n"
        "LLM_BASE_URL=\n",
        encoding="utf-8",
    )
    settings = LLMSettings.from_env()
    assert settings.api_key == "sk-ant-from-file"
    assert (settings.model, settings.effort, settings.max_tokens) == (
        "claude-sonnet-5-5",
        "medium",
        8000,
    )
    assert settings.base_url is None
    monkeypatch.setenv("LLM_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("LLM_EFFORT", "none")
    settings = LLMSettings.from_env()
    assert (settings.model, settings.effort) == ("claude-haiku-5-5", None)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("LLM_EFFORT", "extreme", "LLM_EFFORT"),
        ("LLM_MAX_TOKENS", "lots", "LLM_MAX_TOKENS"),
        ("LLM_MAX_TOKENS", "0", "LLM_MAX_TOKENS"),
    ],
)
def test_invalid_settings_name_the_variable(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        LLMSettings.from_env()


def test_the_api_key_is_kept_out_of_repr() -> None:
    assert "sk-ant-secret" not in repr(LLMSettings(api_key="sk-ant-secret"))


def test_a_missing_env_file_is_empty(tmp_path: Path) -> None:
    assert read_env_file(tmp_path / "nothing.env") == {}

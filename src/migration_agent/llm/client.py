"""One client for every model call, counting tokens for the overhead metric.

Owner: Prajwal. Reads the API key from the environment (.env), never from code.

Calls go to Claude through the official ``anthropic`` SDK. Each setting is read
from the process environment first, then from ``.env`` in the working directory
(gitignored; ``.env.example`` lists the names):

* ``LLM_API_KEY``: the API key. When empty, the SDK's own sources apply
  (``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile).
* ``LLM_MODEL``: default ``claude-opus-5-5``.
* ``LLM_BASE_URL``: only for a proxy or gateway; empty means Anthropic's API.
* ``LLM_EFFORT``: ``low``, ``medium``, ``high``, ``xhigh`` or ``max`` (default
  ``high``), or ``none`` to send no effort for a model that does not take one.
* ``LLM_MAX_TOKENS``: longest reply, default 16000.

Reproducibility: the SDD fixes the temperature at zero, but current Claude models
reject every sampling parameter, so none is sent; a run is pinned by its model,
effort and prompts instead. On models that support it, a request the model
declines for policy reasons is retried server-side on a fallback model
(``fallbacks: "default"``); a refusal that remains raises :class:`LLMError`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
DEFAULT_MAX_TOKENS = 16_000
EFFORTS = ("low", "medium", "high", "xhigh", "max")
#: Models that accept ``fallbacks: "default"`` on the Claude API.
FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: The SDK retries connection errors, 408, 409, 429 and 5xx this many times.
MAX_RETRIES = 4
#: Seconds one request may take.
TIMEOUT = 600.0


class LLMError(RuntimeError):
    """A model call failed, was declined or was cut off.

    ``tokens_in`` and ``tokens_out`` hold what the call used before it failed, so
    the overhead metric still counts it.
    """

    def __init__(self, message: str, tokens_in: int = 0, tokens_out: int = 0) -> None:
        super().__init__(message)
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


def read_env_file(path: str | Path = ".env") -> dict[str, str]:
    """``KEY=VALUE`` pairs of a .env file; a missing file is empty.

    Blank lines, comments and lines without ``=`` are skipped; ``export`` and one
    pair of surrounding quotes are removed.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, IsADirectoryError):
        return {}
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


@dataclass(frozen=True)
class LLMSettings:
    """Which model to call and how; the key is kept out of ``repr`` and logs."""

    model: str = DEFAULT_MODEL
    api_key: str | None = field(default=None, repr=False)
    base_url: str | None = None
    effort: str | None = DEFAULT_EFFORT
    max_tokens: int = DEFAULT_MAX_TOKENS

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> LLMSettings:
        """Settings from the environment, then from ``env_file``; raise ValueError if invalid."""
        from_file = read_env_file(env_file)

        def get(name: str) -> str:
            value = os.environ.get(name, "").strip()
            return value or from_file.get(name, "").strip()

        effort = get("LLM_EFFORT").lower() or DEFAULT_EFFORT
        if effort != "none" and effort not in EFFORTS:
            raise ValueError(
                f"LLM_EFFORT must be one of {', '.join(EFFORTS)} or none, got {effort!r}"
            )
        raw_tokens = get("LLM_MAX_TOKENS") or str(DEFAULT_MAX_TOKENS)
        try:
            max_tokens = int(raw_tokens)
        except ValueError:
            raise ValueError(f"LLM_MAX_TOKENS must be a whole number, got {raw_tokens!r}") from None
        if max_tokens <= 0:
            raise ValueError(f"LLM_MAX_TOKENS must be positive, got {raw_tokens!r}")
        return cls(
            model=get("LLM_MODEL") or DEFAULT_MODEL,
            api_key=get("LLM_API_KEY") or None,
            base_url=get("LLM_BASE_URL") or None,
            effort=None if effort == "none" else effort,
            max_tokens=max_tokens,
        )


def usage_tokens(usage: Any) -> tuple[int, int]:
    """Input and output tokens of one call.

    When a fallback model served the request, ``usage.iterations`` lists every
    attempt and the top-level counts cover only the last, so the attempts are summed.
    """
    if usage is None:
        return 0, 0
    iterations = getattr(usage, "iterations", None)
    if iterations:
        return (
            sum(getattr(entry, "input_tokens", 0) or 0 for entry in iterations),
            sum(getattr(entry, "output_tokens", 0) or 0 for entry in iterations),
        )
    return getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0


class LLMClient:
    """Calls the model and keeps running totals of calls and tokens."""

    def __init__(
        self, settings: LLMSettings | None = None, *, create: Callable[..., Any] | None = None
    ) -> None:
        """``create`` replaces the SDK's ``messages.create`` (tests use a fake)."""
        self.settings = settings or LLMSettings.from_env()
        self._create = create
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def request(self, system: str, prompt: str) -> dict[str, Any]:
        """The parameters of the Messages API request for one completion."""
        if not prompt.strip():
            raise ValueError("the prompt is empty")
        settings = self.settings
        params: dict[str, Any] = {
            "model": settings.model,
            "max_tokens": settings.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system.strip():
            params["system"] = system
        if settings.effort:
            params["output_config"] = {"effort": settings.effort}
        if settings.model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def complete(self, system: str, prompt: str) -> tuple[str, int, int]:
        """Return (text, input tokens, output tokens).

        No sampling parameters are sent (see the module docstring). Raise
        :class:`LLMError` when the call fails, is declined, is cut off at
        ``max_tokens`` or returns no text.
        """
        response = self._send(self.request(system, prompt))
        tokens_in, tokens_out = usage_tokens(getattr(response, "usage", None))
        self.calls += 1
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) or "no category given"
            raise LLMError(f"the model declined the request ({category})", tokens_in, tokens_out)
        if stop == "max_tokens":
            raise LLMError(
                f"the reply was cut off at {self.settings.max_tokens} tokens; raise LLM_MAX_TOKENS",
                tokens_in,
                tokens_out,
            )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            raise LLMError(
                f"the model returned no text (stop reason {stop})", tokens_in, tokens_out
            )
        return text, tokens_in, tokens_out

    def _sdk_create(self) -> Callable[..., Any]:
        import anthropic

        options: dict[str, Any] = {"max_retries": MAX_RETRIES, "timeout": TIMEOUT}
        if self.settings.api_key:
            options["api_key"] = self.settings.api_key
        if self.settings.base_url:
            options["base_url"] = self.settings.base_url
        try:
            client = anthropic.Anthropic(**options)
        except anthropic.AnthropicError as exc:
            raise LLMError(
                f"cannot create the API client; set LLM_API_KEY in .env ({exc})"
            ) from None
        return client.beta.messages.create

    def _send(self, params: dict[str, Any]) -> Any:
        # Imported here so that modules which never call a model work without the SDK.
        import anthropic

        if self._create is None:
            self._create = self._sdk_create()
        model = params["model"]
        try:
            return self._create(**params)
        except anthropic.AuthenticationError:
            raise LLMError("the API key was rejected; check LLM_API_KEY in .env") from None
        except anthropic.PermissionDeniedError as exc:
            raise LLMError(f"the API key may not use {model}: {exc.message}") from None
        except anthropic.NotFoundError as exc:
            raise LLMError(f"unknown model {model!r} or endpoint: {exc.message}") from None
        except anthropic.RateLimitError:
            raise LLMError(f"rate limited after {MAX_RETRIES} retries; try again later") from None
        except anthropic.BadRequestError as exc:
            raise LLMError(f"the API rejected the request: {exc.message}") from None
        except anthropic.APIStatusError as exc:
            raise LLMError(
                f"the API failed with status {exc.status_code} after {MAX_RETRIES} retries: "
                f"{exc.message}"
            ) from None
        except anthropic.APIConnectionError as exc:
            where = self.settings.base_url or "the Anthropic API"
            raise LLMError(f"cannot reach {where}: {exc}") from None
        except anthropic.AnthropicError as exc:
            raise LLMError(f"the model call failed: {exc}") from None

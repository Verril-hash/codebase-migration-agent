"""One client for every model call, counting tokens for the overhead metric.

Owner: Prajwal. Reads the API key from the environment (.env), never from code.
"""

from __future__ import annotations


class LLMClient:
    def complete(self, system: str, prompt: str) -> tuple[str, int, int]:
        """Return (text, input tokens, output tokens). Temperature is 0."""
        raise NotImplementedError("LLM client (Prajwal): complete")

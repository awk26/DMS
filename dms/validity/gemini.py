"""Google Gemini client (``generateContent`` REST API, via ``requests``).

Server side only: GEMINI_API_KEY is read from the environment, sent in the ``x-goog-api-key`` header
(never in a URL, so it cannot end up in proxy logs) and never returned by any endpoint. Document
content and extracted values are never logged.

Errors are classified so the job queue can decide what to do:
* ``TransientAIError``  timeout, network failure, HTTP 429 / 5xx, configuration (401/403/404): retried
  with exponential backoff up to the job's attempt limit;
* ``PermanentAIError``  the request itself is unusable (HTTP 400, e.g. a corrupted file; blocked
  content): not retried.
Tests replace the client through ``app.extensions["validity_ai_client"]`` and never call Google.
"""
import base64
import logging

import requests
from flask import current_app

from .rules import PROMPT, RESPONSE_SCHEMA

log = logging.getLogger(__name__)


class AIError(Exception):
    retry_after = None


class TransientAIError(AIError):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class PermanentAIError(AIError):
    pass


def ai_client():
    """The configured client (a test double when one is installed), or None when not configured."""
    app = current_app
    injected = app.extensions.get("validity_ai_client")
    if injected is not None:
        return injected
    if not app.config.get("GEMINI_API_KEY"):
        return None
    return GeminiClient(app.config)


class GeminiClient:
    def __init__(self, config):
        self.api_key = config.get("GEMINI_API_KEY")
        self.model = config.get("GEMINI_MODEL") or "gemini-3.8-flash"
        self.base = (config.get("GEMINI_API_BASE") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        self.timeout = config.get("GEMINI_TIMEOUT_SECONDS") or 90

    @property
    def model_name(self):
        return self.model

    def analyse(self, *, data=None, mime_type=None, text=None):
        """Send one document (``data`` bytes of ``mime_type``, or extracted ``text``) and return the
        model's JSON text. Raises TransientAIError / PermanentAIError."""
        parts = []
        if data is not None:
            parts.append({"inline_data": {"mime_type": mime_type, "data": base64.b64encode(data).decode("ascii")}})
        if text is not None:
            parts.append({"text": "Document text (extracted from the file):\n\n" + text})
        parts.append({"text": PROMPT})
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                 "responseSchema": RESPONSE_SCHEMA},
        }
        url = f"{self.base}/models/{self.model}:generateContent"
        try:
            resp = requests.post(url, json=body, timeout=self.timeout,
                                 headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"})
        except requests.Timeout:
            raise TransientAIError("The AI service did not answer in time.") from None
        except requests.RequestException as exc:
            raise TransientAIError(f"Could not reach the AI service ({type(exc).__name__}).") from None
        return self._result(resp)

    @staticmethod
    def _error_message(resp):
        """Google's own explanation of an error (e.g. a retired model), shortened. Never contains the key."""
        try:
            message = (resp.json().get("error") or {}).get("message") or ""
        except (ValueError, AttributeError):
            return ""
        return " ".join(str(message).split())[:300]

    @staticmethod
    def _result(resp):
        status = resp.status_code
        if status == 429:
            retry = resp.headers.get("Retry-After")
            raise TransientAIError("The AI service rate limit was reached.",
                                   retry_after=int(retry) if retry and retry.isdigit() else None)
        if status >= 500:
            raise TransientAIError(f"The AI service failed (HTTP {status}).")
        if status in (401, 403, 404):
            # Wrong key, API not enabled or unknown/retired model: fixable by an administrator, so retried.
            reason = GeminiClient._error_message(resp)
            log.error("Gemini rejected the request with HTTP %s: %s (check GEMINI_API_KEY and GEMINI_MODEL)",
                      status, reason or "no details")
            raise TransientAIError(f"The AI service refused the request (HTTP {status})"
                                   + (f": {reason}" if reason else "; check the configuration."))
        if status >= 400:
            reason = GeminiClient._error_message(resp)
            raise PermanentAIError(f"The AI service could not process this file (HTTP {status})"
                                   + (f": {reason}" if reason else "."))
        try:
            data = resp.json()
        except ValueError:
            raise TransientAIError("The AI service returned an unreadable answer.") from None
        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise PermanentAIError(f"The AI service declined this document ({feedback['blockReason']}).")
        candidates = data.get("candidates") or []
        if not candidates:
            raise TransientAIError("The AI service returned no answer.")
        first = candidates[0]
        reason = first.get("finishReason")
        if reason in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
            raise PermanentAIError(f"The AI service declined this document ({reason}).")
        text = "".join(p.get("text", "") for p in (first.get("content") or {}).get("parts", []) if isinstance(p, dict))
        if not text.strip():
            raise TransientAIError("The AI service returned an empty answer.")
        return text

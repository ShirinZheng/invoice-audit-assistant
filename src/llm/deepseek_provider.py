"""Optional DeepSeek LLM provider.

The provider is only constructed in DeepSeek mode. Offline audits do not import
credentials, make network requests, or depend on this class.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from src.config import (
    API_KEY_ERROR,
    LLM_TRACE_DIR,
    ConfigurationError,
    get_settings,
    redact,
)


class LLMResponseError(RuntimeError):
    """Raised when the LLM response cannot be parsed/validated after repair."""


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if match:
        return match.group(1)
    return text


def _extract_json_object(text: str) -> str:
    """Best-effort extraction of the first JSON object in the text."""
    text = _strip_code_fences(text)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


class DeepSeekProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0,
        max_tokens: int = 4096,
    ) -> None:
        if not api_key:
            raise ConfigurationError(API_KEY_ERROR)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    @classmethod
    def from_settings(cls) -> DeepSeekProvider:
        s = get_settings()
        s.require_api_key()
        return cls(
            api_key=s.deepseek_api_key,
            base_url=s.deepseek_base_url,
            model=s.deepseek_model,
            temperature=s.deepseek_temperature,
            max_tokens=s.deepseek_max_tokens,
        )

    # ------------------------------------------------------------------
    # Tracing (API key is never logged; protected env values are redacted)
    # ------------------------------------------------------------------
    def _trace(self, kind: str, payload: dict[str, Any]) -> None:
        settings = get_settings()
        if not settings.trace_enabled:
            return

        if settings.trace_include_content:
            safe_payload = payload
        else:
            safe_payload = _summarize_trace_payload(payload)

        LLM_TRACE_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": kind,
            "model": self.model,
            **safe_payload,
        }
        path = LLM_TRACE_DIR / f"{time.strftime('%Y%m%d')}-traces.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(redact(json.dumps(record, ensure_ascii=False)) + "\n")

    # ------------------------------------------------------------------
    # Raw chat call
    # ------------------------------------------------------------------
    def _chat(
        self,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool = False,
        tools: list[dict] | None = None,
    ) -> Any:
        request_id = uuid.uuid4().hex[:12]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
        self._trace(
            "request",
            {"request_id": request_id, "messages": messages, "tools": tools or []},
        )
        try:
            response = self._client.chat.completions.create(**kwargs)
        except OpenAIError as exc:
            raise LLMResponseError(f"DeepSeek request failed: {exc.__class__.__name__}") from exc
        if not response.choices:
            raise LLMResponseError("DeepSeek returned no response choices.")
        message = response.choices[0].message
        self._trace(
            "response",
            {
                "request_id": request_id,
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                    for tc in (message.tool_calls or [])
                ],
            },
        )
        return message

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        message = self._chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )
        return message.content or ""

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: type,
    ) -> dict:
        """Generate strict JSON and validate it with the given Pydantic schema.

        On parse/validation failure, retry ONCE with a repair prompt.
        If it still fails, raise LLMResponseError (callers mark the case failed).
        """
        if not (isinstance(response_schema, type) and issubclass(response_schema, BaseModel)):
            raise TypeError("response_schema must be a Pydantic BaseModel subclass")

        schema_json = json.dumps(response_schema.model_json_schema(), ensure_ascii=False)
        system = (
            f"{system_prompt}\n\n"
            "Output rules:\n"
            "- Respond with a single valid JSON object only.\n"
            "- No Markdown code fences.\n"
            "- No explanation outside the JSON.\n"
            f"- The JSON must conform to this JSON Schema:\n{schema_json}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        raw = self._chat(messages, json_mode=True).content or ""
        try:
            return self._parse_and_validate(raw, response_schema)
        except (json.JSONDecodeError, ValidationError) as first_error:
            self._trace(
                "validation_error",
                {"raw": raw, "error": str(first_error), "attempt": 1},
            )
            repair_messages = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "Your previous response failed JSON schema validation with "
                        f"this error:\n{first_error}\n\n"
                        "Return a corrected response as a single valid JSON object "
                        "that conforms to the schema. No code fences, no extra text."
                    ),
                },
            ]
            repaired = self._chat(repair_messages, json_mode=True).content or ""
            try:
                return self._parse_and_validate(repaired, response_schema)
            except (json.JSONDecodeError, ValidationError) as second_error:
                self._trace(
                    "validation_error",
                    {"raw": repaired, "error": str(second_error), "attempt": 2},
                )
                raise LLMResponseError(
                    f"LLM response failed schema validation after repair: {second_error}"
                ) from second_error

    @staticmethod
    def _parse_and_validate(raw: str, response_schema: type[BaseModel]) -> dict:
        parsed = json.loads(_extract_json_object(raw))
        validated = response_schema.model_validate(parsed)
        return validated.model_dump()

    def generate_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict],
    ) -> dict:
        """Single tool-planning turn. Returns the assistant message as a dict:

        {"content": str | None, "tool_calls": [{"id", "name", "arguments": dict}]}
        """
        message = self._chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=tools,
        )
        tool_calls = []
        for tc in message.tool_calls or []:
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_raw": tc.function.arguments}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": arguments})
        return {"content": message.content, "tool_calls": tool_calls}


def _summarize_trace_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep trace diagnostics without persisting invoice or prompt contents."""

    safe: dict[str, Any] = {}
    if "request_id" in payload:
        safe["request_id"] = payload["request_id"]

    if "messages" in payload:
        safe["messages"] = []
        for message in payload["messages"]:
            content = str(message.get("content") or "")
            safe["messages"].append(
                {
                    "role": message.get("role"),
                    "characters": len(content),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
            )

    if "tools" in payload:
        safe["tools"] = [
            tool.get("function", {}).get("name", "unknown") for tool in payload.get("tools", [])
        ]

    if "content" in payload:
        content = str(payload.get("content") or "")
        safe["content"] = {
            "characters": len(content),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    if "tool_calls" in payload:
        safe["tool_calls"] = [
            {
                "id": call.get("id"),
                "name": call.get("name"),
                "arguments_characters": len(str(call.get("arguments") or "")),
            }
            for call in payload.get("tool_calls", [])
        ]

    if "error" in payload:
        safe["error"] = str(payload["error"])
    if "attempt" in payload:
        safe["attempt"] = payload["attempt"]
    if "raw" in payload:
        raw = str(payload["raw"])
        safe["raw"] = {
            "characters": len(raw),
            "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        }
    return safe

from __future__ import annotations

"""Bounded model boundary for investigation explanations.

The model may summarize supplied evidence and identify missing information. It
never chooses transaction actions, changes probability, or receives raw tools.
"""

import json
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field


class ModelAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypotheses: list[str] = Field(min_length=1, max_length=8)
    missing_evidence: list[str] = Field(default_factory=list, max_length=12)
    explanation: str = Field(min_length=1, max_length=2000)
    tokens: int = Field(default=0, ge=0)


class ReasoningModel(Protocol):
    def assess(self, context: dict[str, Any]) -> ModelAssessment: ...


class DeterministicModel:
    def assess(self, context: dict[str, Any]) -> ModelAssessment:
        return ModelAssessment(
            hypotheses=[str(context.get("pattern", "none"))],
            missing_evidence=list(context.get("missing_evidence", [])),
            explanation="Assessment is grounded in the deterministic evidence context and policy engine.",
        )


class OpenRouterChatModel:
    """OpenRouter structured-output adapter using its OpenAI-compatible API."""

    def __init__(self, api_key: str, model: str = "openrouter/free", base_url: str = "https://openrouter.ai/api/v1", *, http_client: Any | None = None):
        if not api_key.strip():
            raise ValueError("OPENROUTER_API_KEY is required for the hosted model")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.http = http_client or httpx.Client(timeout=45, follow_redirects=True)

    def assess(self, context: dict[str, Any]) -> ModelAssessment:
        schema = ModelAssessment.model_json_schema()
        # The token count belongs to the API measurement, not model output.
        schema["properties"].pop("tokens", None)
        schema["required"] = [name for name in schema.get("required", []) if name != "tokens"]
        schema["additionalProperties"] = False
        response = self.http.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/tigergraph/hhgoa-fraud-agent",
                "X-Title": "HHGOA Fraud Investigation Pilot",
            },
            json={
                "model": self.model,
                "temperature": 0.1,
                "max_tokens": 700,
                "messages": [
                    {"role": "system", "content": "You are an evidence-grounded fraud investigation analyst. Use only the supplied context. Do not invent merchant identity, location, authorization status, customer testimony, or graph relationships. Return concise hypotheses, missing evidence, and an explanation. Your output is advisory; deterministic policy code controls actions."},
                    {"role": "user", "content": json.dumps(context, sort_keys=True, ensure_ascii=True)},
                ],
                "response_format": {"type": "json_schema", "json_schema": {"name": "fraud_assessment", "strict": True, "schema": schema}},
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage") or {}
            result = ModelAssessment.model_validate_json(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("OpenRouter returned an invalid structured assessment") from exc
        return result.model_copy(update={"tokens": int(usage.get("total_tokens") or 0)})

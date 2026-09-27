"""Typed decision contracts and a loopback-only System One HTTP adapter.

This module deliberately does not select or download model weights. It connects
MemoPad to an already-running local, TypeSafe-compatible decision server.
"""

from __future__ import annotations

import ipaddress
import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

DecisionType = Literal["choice", "noul", "score"]
DecisionValue = str | float
ProbabilityValues = dict[str, float] | list[float] | None


class DecisionProviderError(RuntimeError):
    """Base error for local decision provider failures."""


class DecisionProviderTransportError(DecisionProviderError):
    """The local provider could not be reached or returned an HTTP error."""


class DecisionProviderResponseError(DecisionProviderError):
    """The provider response did not match the submitted decision contract."""


class DecisionQuestion(BaseModel):
    """One typed decision question with validated runtime-defined criteria."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    type: DecisionType
    instructions: str | None = Field(default=None, max_length=4000)
    criteria: dict[str, str | None] | list[str] | None = None

    @model_validator(mode="after")
    def validate_criteria(self) -> DecisionQuestion:
        if self.type == "choice":
            if not isinstance(self.criteria, dict) or not 2 <= len(self.criteria) <= 255:
                raise ValueError("choice criteria must contain between 2 and 255 options")
            if any(not key.strip() for key in self.criteria):
                raise ValueError("choice option IDs must not be empty")
        elif self.type == "noul":
            if self.criteria is not None:
                if not isinstance(self.criteria, dict):
                    raise ValueError("noul criteria must be an optional true/false mapping")
                if not set(self.criteria).issubset({"true", "false"}):
                    raise ValueError("noul criteria keys must be 'true' or 'false'")
        else:
            if (
                not isinstance(self.criteria, list)
                or not 2 <= len(self.criteria) <= 10
                or any(not level.strip() for level in self.criteria)
            ):
                raise ValueError("score criteria must contain between 2 and 10 non-empty levels")
        return self


class DecisionBatch(BaseModel):
    """A bounded set of typed questions over one shared state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str | dict[str, Any] | list[Any]
    questions: list[DecisionQuestion] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_batch(self) -> DecisionBatch:
        if isinstance(self.state, str) and not self.state.strip():
            raise ValueError("state must not be empty")
        if isinstance(self.state, (dict, list)) and not self.state:
            raise ValueError("state must not be empty")
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question IDs must be unique within a decision batch")
        return self


@dataclass(frozen=True)
class TypedDecisionAnswer:
    """A provider answer normalized into a provider-neutral representation."""

    question_id: str
    type: DecisionType
    value: DecisionValue
    confidence: float | None = None
    probabilities: ProbabilityValues = None


@dataclass(frozen=True)
class DecisionResponse:
    """Validated answers and non-sensitive provider metadata."""

    model: str
    answers: dict[str, TypedDecisionAnswer]
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None


class DecisionProvider(Protocol):
    """Vendor-neutral contract for batched decisions over a shared state."""

    async def decide(self, batch: DecisionBatch) -> DecisionResponse: ...


class LocalSystemOneProvider:
    """Call a locally hosted TypeSafe-compatible ``/v1/systemone`` endpoint.

    Only loopback URLs are accepted and system proxy settings are disabled for
    the owned HTTP client, preventing note content from being routed remotely.
    The caller supplies the model name; MemoPad never downloads model weights.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 30.0,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = _validate_loopback_base_url(base_url)
        if not model.strip():
            raise ValueError("model must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> LocalSystemOneProvider:
        if self._client is None:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
            self._client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                trust_env=False,
                headers=headers,
                follow_redirects=False,
            )
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def decide(self, batch: DecisionBatch) -> DecisionResponse:
        if self._client is None:
            raise RuntimeError("LocalSystemOneProvider must be used as an async context manager")

        payload = {
            "model": self.model,
            "state": batch.state,
            "questions": {
                question.id: question.model_dump(exclude={"id"}, exclude_none=True)
                for question in batch.questions
            },
        }
        try:
            response = await self._client.post(
                f"{self.base_url}/v1/systemone",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else None,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DecisionProviderTransportError(
                f"Local decision provider returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise DecisionProviderTransportError(
                f"Could not reach local decision provider: {exc}"
            ) from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise DecisionProviderResponseError(
                "Local decision provider returned invalid JSON"
            ) from exc
        return _parse_response(body, batch, response.headers.get("x-typesafe-request-id"))


def _validate_loopback_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    if (
        parts.scheme not in {"http", "https"}
        or parts.hostname is None
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("base_url must be an HTTP(S) loopback origin without credentials or path")
    hostname = parts.hostname.lower()
    is_loopback = hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise ValueError("base_url must resolve directly to localhost or a loopback IP address")
    return f"{parts.scheme}://{parts.netloc}"


def _parse_response(body: Any, batch: DecisionBatch, request_id: str | None) -> DecisionResponse:
    if not isinstance(body, Mapping):
        raise DecisionProviderResponseError("Provider response must be a JSON object")
    model = body.get("model")
    raw_answers = body.get("answers")
    if not isinstance(model, str) or not model:
        raise DecisionProviderResponseError("Provider response is missing its model name")
    if not isinstance(raw_answers, Mapping):
        raise DecisionProviderResponseError("Provider response is missing its answers object")

    expected = {question.id: question for question in batch.questions}
    if set(raw_answers) != set(expected):
        raise DecisionProviderResponseError(
            "Provider answer IDs must exactly match the submitted question IDs"
        )

    answers: dict[str, TypedDecisionAnswer] = {}
    for question_id, question in expected.items():
        raw_answer = raw_answers[question_id]
        if not isinstance(raw_answer, Mapping) or raw_answer.get("type") != question.type:
            raise DecisionProviderResponseError(
                f"Answer {question_id!r} does not match its requested type"
            )
        probabilities = _validate_probabilities(raw_answer.get("probabilities"), question)
        raw_confidence = raw_answer.get("confidence")
        confidence = (
            _validate_unit_interval(raw_confidence, f"{question_id}.confidence")
            if raw_confidence is not None
            else None
        )

        if question.type == "choice":
            value = raw_answer.get("choice")
            if not isinstance(question.criteria, dict):
                raise DecisionProviderResponseError(
                    f"Question {question_id!r} has invalid choice criteria"
                )
            if not isinstance(value, str) or value not in question.criteria:
                raise DecisionProviderResponseError(
                    f"Answer {question_id!r} selected an unknown choice"
                )
        elif question.type == "noul":
            value = _validate_unit_interval(raw_answer.get("noul"), f"{question_id}.noul")
        else:
            value = raw_answer.get("score")
            if not isinstance(question.criteria, list):
                raise DecisionProviderResponseError(
                    f"Question {question_id!r} has invalid score criteria"
                )
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= len(question.criteria) - 1
            ):
                raise DecisionProviderResponseError(
                    f"Answer {question_id!r} returned an invalid score"
                )
            value = float(value)

        answers[question_id] = TypedDecisionAnswer(
            question_id=question_id,
            type=question.type,
            value=value,
            confidence=confidence,
            probabilities=probabilities,
        )

    usage = body.get("usage")
    if usage is not None and not isinstance(usage, Mapping):
        raise DecisionProviderResponseError("Provider usage must be a JSON object")
    input_tokens = _parse_token_count(usage, "input_tokens")
    output_tokens = _parse_token_count(usage, "output_tokens")
    return DecisionResponse(
        model=model,
        answers=answers,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        request_id=request_id,
    )


def _validate_probabilities(value: Any, question: DecisionQuestion) -> ProbabilityValues:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise DecisionProviderResponseError(
                f"Answer {question.id!r} has non-string probability keys"
            )
        if question.type == "choice":
            if not isinstance(question.criteria, dict):
                raise DecisionProviderResponseError(
                    f"Question {question.id!r} has invalid choice criteria"
                )
            valid_keys = set(question.criteria)
            if not set(value).issubset(valid_keys):
                raise DecisionProviderResponseError(
                    f"Answer {question.id!r} has probabilities for unknown choices"
                )
        parsed: dict[str, float] = {}
        for key, probability in value.items():
            parsed[key] = _validate_unit_interval(probability, f"{question.id}.probabilities")
        return parsed
    if isinstance(value, list):
        if (
            question.type != "score"
            or not isinstance(question.criteria, list)
            or len(value) != len(question.criteria)
        ):
            raise DecisionProviderResponseError(
                f"Answer {question.id!r} has an invalid probability list"
            )
        return [
            _validate_unit_interval(probability, f"{question.id}.probabilities")
            for probability in value
        ]
    raise DecisionProviderResponseError(f"Answer {question.id!r} has invalid probabilities")


def _validate_unit_interval(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise DecisionProviderResponseError(f"{name} must be a finite number between 0 and 1")
    return float(value)


def _parse_token_count(usage: Any, key: str) -> int | None:
    if usage is None or key not in usage:
        return None
    value = usage[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DecisionProviderResponseError(f"usage.{key} must be a non-negative integer")
    return value

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from memopad.services.local_decision_provider import (
    DecisionBatch,
    DecisionProviderResponseError,
    DecisionProviderTransportError,
    DecisionQuestion,
    LocalSystemOneProvider,
)


def _batch() -> DecisionBatch:
    return DecisionBatch(
        state="A customer requests a refund.",
        questions=[
            DecisionQuestion(
                id="intent",
                type="choice",
                instructions="What does the customer want?",
                criteria={"refund": "Wants money back", "other": "Something else"},
            ),
            DecisionQuestion(id="urgent", type="noul"),
            DecisionQuestion(
                id="severity",
                type="score",
                criteria=["low", "medium", "high"],
            ),
        ],
    )


def _response() -> dict:
    return {
        "model": "laya:en",
        "answers": {
            "intent": {
                "type": "choice",
                "choice": "refund",
                "confidence": 0.9,
                "probabilities": {"refund": 0.9, "other": 0.1},
            },
            "urgent": {"type": "noul", "noul": 0.2},
            "severity": {
                "type": "score",
                "score": 1.25,
                "probabilities": [0.1, 0.5, 0.4],
            },
        },
        "usage": {"input_tokens": 42, "output_tokens": 0},
    }


@pytest.mark.asyncio
async def test_local_provider_posts_one_typed_batch_and_normalizes_response():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=_response(),
            headers={"x-typesafe-request-id": "request-123"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with client:
        provider = LocalSystemOneProvider(
            "http://127.0.0.1:11435",
            "laya",
            api_key="local-key",
            client=client,
        )
        result = await provider.decide(_batch())

    assert len(seen) == 1
    assert str(seen[0].url) == "http://127.0.0.1:11435/v1/systemone"
    assert seen[0].headers["authorization"] == "Bearer local-key"
    payload = json.loads(seen[0].content)
    assert payload["model"] == "laya"
    assert set(payload["questions"]) == {"intent", "urgent", "severity"}
    assert "id" not in payload["questions"]["intent"]
    assert result.model == "laya:en"
    assert result.answers["intent"].value == "refund"
    assert result.answers["urgent"].value == 0.2
    assert result.answers["severity"].value == 1.25
    assert result.answers["severity"].probabilities == [0.1, 0.5, 0.4]
    assert result.input_tokens == 42
    assert result.output_tokens == 0
    assert result.request_id == "request-123"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.com",
        "http://192.168.1.10:11435",
        "http://localhost:11435/path",
        "http://user:password@localhost:11435",
        "http://localhost:11435?proxy=1",
    ],
)
def test_local_provider_rejects_non_loopback_or_ambiguous_url(base_url: str):
    with pytest.raises(ValueError, match="base_url"):
        LocalSystemOneProvider(base_url, "laya")


def test_local_provider_accepts_loopback_ipv6():
    provider = LocalSystemOneProvider("http://[::1]:11435/", "laya")
    assert provider.base_url == "http://[::1]:11435"


def test_decision_batch_rejects_duplicate_question_ids():
    question = DecisionQuestion(id="same", type="noul")
    with pytest.raises(ValidationError, match="unique"):
        DecisionBatch(state="text", questions=[question, question])


@pytest.mark.parametrize(
    "question",
    [
        {"id": "choice", "type": "choice", "criteria": {"only_one": None}},
        {"id": "score", "type": "score", "criteria": ["only_one"]},
        {"id": "invalid", "type": "noul", "criteria": {"maybe": "unclear"}},
    ],
)
def test_decision_question_rejects_invalid_criteria(question: dict):
    with pytest.raises(ValidationError):
        DecisionQuestion.model_validate(question)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda body: body["answers"].pop("urgent"), "IDs"),
        (lambda body: body["answers"]["intent"].update(choice="not-an-option"), "unknown choice"),
        (lambda body: body["answers"]["urgent"].update(noul=1.2), "between 0 and 1"),
        (lambda body: body["answers"]["severity"].update(score=3), "invalid score"),
        (
            lambda body: body["answers"]["intent"]["probabilities"].update(extra=0.1),
            "unknown choices",
        ),
    ],
)
async def test_local_provider_rejects_invalid_or_incomplete_answers(mutate, message: str):
    body = _response()
    mutate(body)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    )
    async with client:
        provider = LocalSystemOneProvider("http://localhost:11435", "laya", client=client)
        with pytest.raises(DecisionProviderResponseError, match=message):
            await provider.decide(_batch())


@pytest.mark.asyncio
async def test_local_provider_surfaces_http_failure_without_accepting_result():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(422, json={"error": "invalid local request"})
        )
    )
    async with client:
        provider = LocalSystemOneProvider("http://localhost:11435", "laya", client=client)
        with pytest.raises(DecisionProviderTransportError, match="HTTP 422"):
            await provider.decide(_batch())


@pytest.mark.asyncio
async def test_local_provider_requires_managed_or_injected_http_client():
    provider = LocalSystemOneProvider("http://localhost:11435", "laya")
    with pytest.raises(RuntimeError, match="async context manager"):
        await provider.decide(_batch())

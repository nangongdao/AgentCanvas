"""Workflow AI Copilot: the draft loop plus its HTTP contract.

The copilot proposes a document and the same validator the editor and compiler
use disposes of it, so these tests assert both halves: the service round-trips
through :func:`validate_dsl`, and the endpoint never persists anything.
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.engine.nodes import NODE_REGISTRY
from app.engine.validation import validate_dsl
from app.main import create_app
from app.providers import ChatMessage, StreamChunk, Usage
from app.providers.base import BaseChatProvider
from app.providers.mock_provider import _MOCK_COPILOT_DSL, MockChatProvider
from app.schemas.dsl import WorkflowDSL
from app.services.workflow_copilot import (
    WorkflowCopilotError,
    draft_workflow,
    node_catalog_block,
)

ADMIN_TOKEN = "copilot-admin-token-with-more-than-16-characters"


class ScriptedProvider(BaseChatProvider):
    """Replays canned replies and records every prompt it was handed."""

    name = "scripted"

    def __init__(self, replies: list[str]) -> None:
        super().__init__(model="scripted-model")
        self.replies = list(replies)
        self.calls: list[list[ChatMessage]] = []

    async def stream_chat(self, messages, *, tools=(), **params):
        self.calls.append(list(messages))
        reply = self.replies.pop(0) if self.replies else ""
        yield StreamChunk(type="text", text=reply)
        yield StreamChunk(type="usage", usage=Usage(prompt_tokens=7, completion_tokens=11))
        yield StreamChunk(type="done")


def _draft_reply(payload: dict | None = None) -> str:
    return json.dumps(payload if payload is not None else _MOCK_COPILOT_DSL)


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        rate_limit_chat_requests=100,
    )


def _admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


# --- prompt construction -------------------------------------------------


def test_prompt_catalog_covers_every_registered_node_type() -> None:
    catalog = node_catalog_block()
    for node_type in NODE_REGISTRY:
        assert f"- {node_type}:" in catalog, node_type


def test_prompt_catalog_omits_field_descriptions_to_stay_bounded() -> None:
    # The catalog is derived from the live JSON schemas, so it must carry field
    # names and types but not the long prose descriptions.
    agent_line = next(
        line for line in node_catalog_block().splitlines() if line.startswith("- agent:")
    )
    assert "model_config_id: string" in agent_line
    assert "description" not in agent_line


async def test_base_dsl_is_handed_to_the_model_as_context() -> None:
    provider = ScriptedProvider([_draft_reply()])
    await draft_workflow(provider, prompt="add a condition", base_dsl=_MOCK_COPILOT_DSL)
    user_message = provider.calls[0][1].content
    assert "add a condition" in user_message
    assert "Current workflow to modify" in user_message


async def test_empty_prompt_is_rejected() -> None:
    with pytest.raises(ValueError):
        await draft_workflow(ScriptedProvider([]), prompt="   ")


# --- the drafting loop ---------------------------------------------------


async def test_demo_provider_drafts_a_workflow_that_passes_the_validator() -> None:
    draft = await draft_workflow(MockChatProvider(), prompt="做一个问答工作流")
    assert draft.valid is True
    assert draft.errors == []
    assert draft.attempts == 1
    assert draft.provider == "mock"
    assert draft.completion_tokens > 0
    # Re-run the draft through the real gate rather than trusting `valid`.
    validate_dsl(WorkflowDSL.model_validate(draft.dsl))
    assert [node["id"] for node in draft.dsl["nodes"]] == ["start", "agent", "end"]


async def test_prose_before_json_is_repaired_in_one_more_attempt() -> None:
    provider = ScriptedProvider(["I cannot help with that.", f"```json\n{_draft_reply()}\n```"])
    draft = await draft_workflow(provider, prompt="x")
    assert draft.valid is True
    assert draft.attempts == 2
    assert len(provider.calls) == 2
    assert "Fix exactly these problems" in provider.calls[1][-1].content


async def test_schema_valid_but_graph_invalid_draft_is_returned_with_errors() -> None:
    broken = copy.deepcopy(_MOCK_COPILOT_DSL)
    broken["nodes"] = [node for node in broken["nodes"] if node["type"] != "end"]
    broken["edges"] = [edge for edge in broken["edges"] if edge["target"] != "end"]
    draft = await draft_workflow(ScriptedProvider([_draft_reply(broken)]), prompt="x", max_attempts=1)
    assert draft.valid is False
    assert draft.errors == ["at least one end node required"]
    # The rejected document is still handed back so the editor can repair it.
    assert draft.dsl["nodes"]


async def test_replies_that_never_parse_raise() -> None:
    provider = ScriptedProvider(["nope", "still nope"])
    with pytest.raises(WorkflowCopilotError):
        await draft_workflow(provider, prompt="x", max_attempts=2)
    assert len(provider.calls) == 2


async def test_unknown_model_reference_warns_instead_of_failing() -> None:
    provider = ScriptedProvider([_draft_reply()])
    draft = await draft_workflow(provider, prompt="x", available_model_ids={"other-model"})
    assert draft.valid is True
    assert draft.warnings == ["agent 'agent' references unknown chat model(s): default"]


async def test_known_model_reference_produces_no_warning() -> None:
    provider = ScriptedProvider([_draft_reply()])
    draft = await draft_workflow(provider, prompt="x", available_model_ids={"default"})
    assert draft.warnings == []


# --- HTTP contract -------------------------------------------------------


def test_endpoint_drafts_with_the_demo_provider(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        before = client.get("/api/workflows", headers=_admin()).json()["items"]
        response = client.post(
            "/api/workflows/copilot/draft",
            json={"prompt": "做一个翻译工作流"},
            headers=_admin(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["provider"] == "mock"
        assert body["attempts"] == 1
        assert body["errors"] == []
        assert body["usage"]["completion_tokens"] > 0
        validate_dsl(WorkflowDSL.model_validate(body["dsl"]))
        # The copilot proposes; it must never write.
        after = client.get("/api/workflows", headers=_admin()).json()["items"]
        assert len(after) == len(before)
        assert body["name"] not in {workflow["name"] for workflow in after}


def test_endpoint_requires_authentication(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.post("/api/workflows/copilot/draft", json={"prompt": "x"})
        assert response.status_code == 401


def test_endpoint_validates_the_prompt_and_the_model(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        empty = client.post(
            "/api/workflows/copilot/draft", json={"prompt": ""}, headers=_admin()
        )
        assert empty.status_code == 422
        unknown_model = client.post(
            "/api/workflows/copilot/draft",
            json={"prompt": "x", "model_config_id": "does-not-exist"},
            headers=_admin(),
        )
        assert unknown_model.status_code == 404
        unknown_project = client.post(
            "/api/workflows/copilot/draft",
            json={"prompt": "x", "project_id": "does-not-exist"},
            headers=_admin(),
        )
        assert unknown_project.status_code == 404

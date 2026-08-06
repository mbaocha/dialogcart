from __future__ import annotations

import pytest

from core.rendering.llm_renderer import HandlerRenderResult, LlmRenderRequest
from core.tests.harness.recording_render_client import RecordingRenderClient


def _request(text: str) -> LlmRenderRequest:
    return LlmRenderRequest(
        render_instruction="Use the supplied business facts.",
        facts={"structured_context": {"services": []}},
        user_request=text,
    )


def test_replay_lookup_uses_established_text_normalization() -> None:
    client = RecordingRenderClient({"  What Do You Recommend?  ": "Premium"})

    assert client.render(_request("what do you recommend?")).text == "Premium"
    assert client.last_request is not None
    assert client.last_request.user_request == "what do you recommend?"


def test_missing_replay_entry_has_clear_error() -> None:
    client = RecordingRenderClient({"known request": "Known response"})

    with pytest.raises(AssertionError, match="No recorded handler-render response"):
        client.render(_request("missing request"))


def test_recording_preserves_complete_result_without_shared_mutation() -> None:
    response = {"text": "Premium", "metadata": {"source": "handler"}}
    client = RecordingRenderClient()
    client.record("recommend", response)

    replayed = client.render(_request("recommend"))
    replayed.metadata["source"] = "changed"

    assert client.render(_request("recommend")) == HandlerRenderResult(
        text="Premium", metadata={"source": "handler"}
    )

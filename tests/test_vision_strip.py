"""An image must reach the model — or the model must say so honestly.

_strip_unsupported exists to remove cache_control, an Anthropic extension
that takes the whole request down at other providers. But it used to gather
only text blocks into the flattened result, so a picture vanished silently
for every non-Claude model, including the ones that see perfectly well. The
model then reasoned confidently about something it had never been shown.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from agent import provider  # noqa: E402


def _with_image():
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": "What is in this screenshot?"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
        ],
    }]


def _images_in(message) -> int:
    content = message["content"]
    if not isinstance(content, list):
        return 0
    return sum(1 for b in content if "image" in str(b.get("type", "")))


@pytest.mark.parametrize("model", [
    "claude-opus-4.8",
    "gpt-5.4",
    "gemini-2.5-flash-lite",
    "grok-4.1-fast-reasoning",
])
def test_models_that_see_receive_the_image(model):
    prepared = provider._strip_unsupported(_with_image(), model)
    assert _images_in(prepared[0]) == 1, f"{model} can see, yet got no image"


def test_a_blind_model_receives_an_honest_note():
    """The fallback cannot see — but staying silent about it is not allowed."""
    prepared = provider._strip_unsupported(_with_image(), "z-ai/glm-5.2")

    text = prepared[0]["content"]
    text = text if isinstance(text, str) else " ".join(
        b.get("text", "") for b in text)

    assert _images_in(prepared[0]) == 0, "the model would not understand the image"
    assert provider._IMAGE_LOST in text, \
        "without the note the model starts reasoning about what it never saw"
    assert "What is in this screenshot?" in text, "the question must survive"


def test_the_question_beside_the_image_survives():
    prepared = provider._strip_unsupported(_with_image(), "gpt-5.4")
    texts = [b.get("text", "") for b in prepared[0]["content"] if b.get("type") == "text"]
    assert any("screenshot" in t for t in texts)


def test_cache_control_is_stripped_for_others():
    """The Anthropic extension takes the request down at other providers."""
    messages = [{"role": "system", "content": [
        {"type": "text", "text": "instruction", "cache_control": {"type": "ephemeral"}}]}]

    prepared = provider._strip_unsupported(messages, "gpt-5.4")

    assert "cache_control" not in str(prepared)
    assert "instruction" in str(prepared), "the instruction itself must remain"


def test_anthropic_receives_the_request_untouched():
    messages = _with_image()
    assert provider._strip_unsupported(messages, "claude-opus-4.8") is messages


def test_an_empty_model_name_does_not_break_preparation():
    """model may arrive empty — that was handled before and must stay handled."""
    assert provider._strip_unsupported(_with_image(), "") is not None


def test_plain_text_still_collapses():
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "first"}, {"type": "text", "text": "second"}]}]

    prepared = provider._strip_unsupported(messages, "z-ai/glm-5.2")

    assert prepared[0]["content"] == "first\nsecond"


def test_an_empty_system_block_is_not_sent():
    messages = [{"role": "system", "content": [{"type": "text", "text": ""}]}]
    assert provider._strip_unsupported(messages, "gpt-5.4") == []

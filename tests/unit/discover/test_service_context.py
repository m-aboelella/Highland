from __future__ import annotations

from highland.discover.conversations import ConversationMessage
from highland.discover.service import _completed_prior_messages
from highland.models.contracts import MessageRole


def message(role: str, content: str, run_id: str) -> ConversationMessage:
    return ConversationMessage(role=role, content=content, run_id=run_id)


def test_completed_prior_messages_excludes_failed_and_empty_turns() -> None:
    messages = [
        message("user", "Earlier question", "run-complete"),
        message("assistant", "Earlier answer", "run-complete"),
        message("user", "Question that exhausted its budget", "run-failed"),
        message("assistant", "", "run-failed"),
        message("user", "Current question", "run-current"),
    ]

    context = _completed_prior_messages(messages, current_run_id="run-current")

    assert [(item.role, item.content) for item in context] == [
        (MessageRole.USER, "Earlier question"),
        (MessageRole.ASSISTANT, "Earlier answer"),
    ]

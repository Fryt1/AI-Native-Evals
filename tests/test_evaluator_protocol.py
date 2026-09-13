"""The evaluator's wire protocol is its own, not the subject's.

A run failed for a reason that looked like the subject's fault and was not: the
Agent wrote the expected file and reported it correctly, then evaluation failed
with `wire_api = "chat" is no longer supported`, because the protocol negotiated
for a DSH subject was handed to a Codex evaluator. Codex rejects `chat` at
startup, so the check errored and the run was graded `fail`.

These tests pin the rule that was broken: the value configured into the
evaluator's container comes from the evaluator's own profile.
"""

from __future__ import annotations

from ai_native_evals.runs.agent_sandbox import _evaluator_wire_api


def test_the_evaluator_uses_its_own_declared_protocol() -> None:
    """The regression: a Codex evaluator must not be given the subject's `chat`."""
    evaluator = {"id": "codex", "adapter": "codex", "protocol": "responses"}

    assert _evaluator_wire_api(None, evaluator) == "responses"


def test_the_subjects_protocol_never_reaches_the_evaluator() -> None:
    """`chat` is a legal subject protocol and an illegal evaluator one.

    A DSH subject negotiates `chat`; a DSH evaluator declares `chat` too. The
    distinction is which profile the value came from, and this asserts the
    evaluator's -- not the run's -- is the one used.
    """
    codex_evaluator = {"id": "codex", "protocol": "responses"}
    dsh_evaluator = {"id": "dsh-release", "protocol": "chat"}

    assert _evaluator_wire_api(None, codex_evaluator) == "responses"
    assert _evaluator_wire_api(None, dsh_evaluator) == "chat"


def test_an_explicit_protocol_still_wins() -> None:
    """A caller naming a protocol is talking about this evaluator."""
    evaluator = {"id": "codex", "protocol": "responses"}

    assert _evaluator_wire_api("chat", evaluator) == "chat"


def test_a_profile_without_a_protocol_falls_back_to_responses() -> None:
    """The default must be one every shipped evaluator accepts.

    `chat` is the tempting default because most subjects use it, and it is the
    wrong one: the evaluator is Codex.
    """
    assert _evaluator_wire_api(None, {"id": "codex"}) == "responses"
    assert _evaluator_wire_api(None, {}) == "responses"


def test_an_empty_explicit_value_is_not_treated_as_a_choice() -> None:
    """`""` arrives from callers that pass a default, and means "not specified"."""
    evaluator = {"id": "codex", "protocol": "responses"}

    assert _evaluator_wire_api("", evaluator) == "responses"

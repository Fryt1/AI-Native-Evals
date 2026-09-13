"""pi is a single-command Agent, and adding it must not need shared code.

The point of these tests is the shape of that: a profile, a Dockerfile and a
trace parser, with nothing edited in the build tool, the runner or the console.
pi was added to check exactly that, and the pieces it needed are pinned here so
the next Agent can be added the same way.
"""

from __future__ import annotations

from pathlib import Path

from ai_native_evals.adapters.events import normalize_event_lines
from ai_native_evals.agents.profile import load_agent_profile_file
from ai_native_evals.providers import agent_wire_apis

REPO = Path(__file__).resolve().parents[1]
PROFILE = REPO / "profiles" / "agents" / "pi.yaml"


def test_pi_is_declared_by_a_profile_alone() -> None:
    profile = load_agent_profile_file(PROFILE)

    assert profile.profile_id == "pi"
    assert profile.image == "ai-native-pi-agent:0.85.1"
    assert profile.build == {
        "dockerfile": "docker/pi-agent/Dockerfile",
        "version_arg": "PI_VERSION",
    }


def test_the_build_tool_needs_no_knowledge_of_pi() -> None:
    """A per-Agent parameter or Dockerfile path would mean it was edited.

    Matched as whole words and paths rather than as a substring: `pi` occurs
    inside `PyPIIndex`, `$PID` and `copying`, and a test that trips on those
    proves nothing.
    """
    import re

    text = (REPO / "tools" / "build-sandbox-images.ps1").read_text(encoding="utf-8")

    assert not re.search(r"\bpi\b", text), "the build tool names pi as a word"
    assert "pi-agent" not in text, "the build tool names pi's Dockerfile"


def test_pi_declares_the_protocol_it_actually_speaks() -> None:
    """`chat`, matching the `openai-completions` route its entrypoint writes.

    An adapter nobody registered used to fall back to Codex's protocol list,
    which would claim pi speaks `responses`. That is a guess, and a run built on
    it fails at the model call.
    """
    assert agent_wire_apis("pi", "generic") == ["chat"]
    assert load_agent_profile_file(PROFILE).protocol == "chat"


def test_pi_events_normalize_into_the_shared_vocabulary() -> None:
    """Real lines from a run, not invented ones."""
    lines = [
        '{"type":"session","version":3,"id":"abc"}',
        '{"type":"agent_start"}',
        '{"type":"turn_start"}',
        '{"type":"message_start","message":{"role":"user",'
        '"content":[{"type":"text","text":"make a file"}]}}',
        '{"type":"message_end","message":{"role":"user",'
        '"content":[{"type":"text","text":"make a file"}]}}',
        '{"type":"message_start","message":{"role":"assistant","content":[]}}',
        '{"type":"message_update","assistantMessageEvent":'
        '{"type":"text_delta","delta":"ok"}}',
        '{"type":"message_end","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"done"}],"stopReason":"stop"}}',
        '{"type":"turn_end","message":{"role":"assistant","content":[]}}',
        '{"type":"agent_end","messages":[]}',
        '{"type":"agent_settled"}',
    ]

    events = normalize_event_lines(lines, adapter="pi", agent_id="pi")
    kinds = [event["type"] for event in events]

    assert kinds == [
        "run_started",  # session
        "run_started",  # agent_start
        "turn_started",
        "prompt",  # the user's message_start
        "prompt",  # the user's message_end
        "agent_message",  # the assistant's message_start
        "provider_event",  # message_update: a delta, not a second message
        "agent_message",  # the assistant's message_end, carrying the text
        "turn_completed",
        "run_completed",  # agent_end
        "run_completed",  # agent_settled
    ]
    assert events[7]["payload"]["content"] == "done"
    assert events[7]["payload"]["stop_reason"] == "stop"


def test_a_failed_model_call_is_reported_as_an_error() -> None:
    """The failure that cost the most time to diagnose: pi answers in 20ms."""
    lines = [
        '{"type":"message_end","message":{"role":"assistant","content":[],'
        '"stopReason":"error","errorMessage":"Request timed out."}}'
    ]

    events = normalize_event_lines(lines, adapter="pi", agent_id="pi")

    assert events[0]["type"] == "error"
    assert events[0]["payload"]["error"] == "Request timed out."


def test_an_unknown_event_still_produces_an_event() -> None:
    """A pi release that adds an event type must not empty the Process phase."""
    events = normalize_event_lines(
        ['{"type":"something_new","detail":1}'], adapter="pi", agent_id="pi"
    )

    assert events[0]["type"] == "provider_event"
    assert events[0]["payload"]["raw"]["detail"] == 1

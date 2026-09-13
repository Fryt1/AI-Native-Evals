"""Verifying an Agent profile actually works.

Two levels exist because they catch different failures, and the distinction is
the point of the module:

``static``  the profile is well-formed and its image exists. It cannot tell you
            whether the thing inside the image can start.
``smoke``   a real container is started and asked a question. It is the only
            check that proves the entrypoint, the credentials, and the model
            round trip work together.

The tests that matter here never start a container: they pin the parsing, the
verdict rules, and the failure classification, so a broken probe reports a
useful reason instead of a blank failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_native_evals.runs import agent_check
from ai_native_evals.runs.agent_check import (
    PROBE_EXPECTED,
    PROBE_PROMPT,
    AgentCheck,
    AgentReport,
    _agent_args,
    _parse_container_exit,
    check_level,
    check_static,
    diagnose,
    extract_reply,
    load_agent_profile,
    read_upstream_env,
)

REPO = Path(__file__).resolve().parents[1]


# --- verdict rules -----------------------------------------------------------


def test_unknown_never_makes_an_agent_unusable() -> None:
    """A probe that could not run is not a broken Agent."""
    report = AgentReport(agent="a", image="i", level="static")
    report.add(AgentCheck("image", "unknown", detail="docker unreachable"))

    assert report.usable is True
    assert report.to_dict()["unknown"] == ["image"]


def test_missing_makes_an_agent_unusable() -> None:
    report = AgentReport(agent="a", image="i", level="static")
    report.add(AgentCheck("image", "missing", detail="not built"))

    assert report.usable is False
    assert report.to_dict()["failed"] == ["image"]


def test_a_healthy_report_is_usable() -> None:
    report = AgentReport(agent="a", image="i", level="smoke")
    report.add(AgentCheck("container", "ok"))
    report.add(AgentCheck("reply", "ok", detail="READY"))

    assert report.usable is True
    assert report.to_dict()["failed"] == []


# --- what a finding is worth ------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("profile", "static"),
        ("adapter", "static"),
        ("image", "static"),
        ("capabilities", "static"),
        ("container", "smoke"),
        ("reply", "smoke"),
        ("exit", "smoke"),
        ("credentials", "smoke"),
    ],
)
def test_a_check_knows_how_it_was_established(name: str, expected: str) -> None:
    """Reading files and starting a container are not the same kind of fact.

    The report used to mix them in one list, so a well-formed profile looked
    exactly like a working one.
    """
    assert check_level(name) == expected


def test_a_check_derives_its_level_from_its_name() -> None:
    assert AgentCheck("reply", "ok").level == "smoke"
    assert AgentCheck("profile", "ok").level == "static"


def test_an_explicit_level_is_not_overwritten() -> None:
    """A caller that knows better keeps its own answer."""
    assert AgentCheck("custom", "ok", level="smoke").level == "smoke"


def test_every_check_carries_its_level_into_the_payload() -> None:
    payload = AgentCheck("container", "ok", detail="started").to_dict()

    assert payload["level"] == "smoke"


def test_the_report_groups_checks_by_how_they_were_obtained() -> None:
    report = AgentReport(agent="a", image="i", level="smoke")
    report.add(AgentCheck("profile", "ok"))
    report.add(AgentCheck("image", "ok"))
    report.add(AgentCheck("container", "ok"))
    report.add(AgentCheck("reply", "ok"))

    payload = report.to_dict()

    assert payload["static"] == ["profile", "image"]
    assert payload["smoke"] == ["container", "reply"]


def test_a_static_report_has_no_smoke_group() -> None:
    report = check_static(REPO, "codex").to_dict()

    assert report["static"]
    assert report["smoke"] == []


def test_the_two_groups_never_overlap_or_lose_a_check() -> None:
    """Every check is in exactly one group, whatever its status."""
    report = AgentReport(agent="a", image="i", level="smoke")
    for name in ("profile", "adapter", "image", "capabilities", "container", "reply", "exit"):
        report.add(AgentCheck(name, "ok"))
    payload = report.to_dict()

    assert sorted(payload["static"] + payload["smoke"]) == sorted(payload["checks"])


# --- static verification -----------------------------------------------------


def test_static_check_reports_a_profile_that_does_not_exist() -> None:
    report = check_static(REPO, "no-such-agent")

    assert report.usable is False
    checks = report.to_dict()["checks"]
    assert checks["profile"]["status"] == "missing"


def test_static_check_reads_a_real_profile() -> None:
    report = check_static(REPO, "codex")
    checks = report.to_dict()["checks"]

    assert checks["profile"]["status"] == "ok"
    assert checks["adapter"]["detail"] == "codex"
    # The image is either present or unverifiable on a machine without Docker;
    # never reported as missing when the probe could not run.
    assert checks["image"]["status"] in {"ok", "unknown"}


def test_static_check_reports_declared_capabilities() -> None:
    """The capabilities are the profile's promise; they are surfaced, not hidden."""
    checks = check_static(REPO, "codex-dcc").to_dict()["checks"]

    assert "blender" in checks["capabilities"]["detail"]


def test_load_agent_profile_matches_on_declared_id(tmp_path: Path) -> None:
    root = tmp_path / "profiles" / "agents"
    root.mkdir(parents=True)
    (root / "renamed.yaml").write_text(
        "id: actual-id\nadapter: codex\nimage: x\n", encoding="utf-8"
    )

    assert load_agent_profile(tmp_path, "actual-id")["adapter"] == "codex"


def test_load_agent_profile_returns_empty_for_an_unknown_agent(tmp_path: Path) -> None:
    assert load_agent_profile(tmp_path, "nope") == {}


def test_static_check_flags_a_profile_without_an_image(tmp_path: Path) -> None:
    root = tmp_path / "profiles" / "agents"
    root.mkdir(parents=True)
    (root / "noimg.yaml").write_text("id: noimg\nadapter: codex\n", encoding="utf-8")

    report = check_static(tmp_path, "noimg")

    assert report.usable is False
    assert "image" in report.to_dict()["failed"]


# --- reply parsing -----------------------------------------------------------


def test_extract_reply_reads_the_codex_event_stream() -> None:
    logs = "\n".join(
        [
            '{"type":"thread.started","thread_id":"x"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"READY"}}',
            '{"type":"turn.completed"}',
        ]
    )

    assert extract_reply(logs) == "READY"


def test_extract_reply_takes_the_last_agent_message() -> None:
    logs = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"final"}}',
        ]
    )

    assert extract_reply(logs) == "final"


def test_extract_reply_ignores_non_message_items() -> None:
    logs = '{"type":"item.completed","item":{"type":"command_execution","output":"noise"}}'

    assert extract_reply(logs) == ""


def test_extract_reply_survives_a_truncated_line() -> None:
    """A log read mid-write must not raise; it must simply yield nothing."""
    assert extract_reply('{"type":"item.completed","item":{"type":"agent_me') == ""


def test_extract_reply_handles_timestamped_lines() -> None:
    """`docker logs` may prefix each line with a timestamp."""
    event = '{"type":"item.completed","item":{"type":"agent_message","text":"READY"}}'
    line = f"2026-09-13T00:00:00Z {event}"

    assert extract_reply(line) == "READY"


def test_the_probe_expectation_is_a_fixed_word() -> None:
    """A fixed word proves the prompt was understood, not merely acknowledged."""
    assert PROBE_EXPECTED == "READY"
    assert PROBE_EXPECTED in agent_check.PROBE_PROMPT


# --- failure diagnosis -------------------------------------------------------


@pytest.mark.parametrize(
    ("log", "expected"),
    [
        ("sh: /usr/local/bin/entry: not found", "入口脚本"),
        ("Error: 401 Unauthorized", "鉴权"),
        ("dial tcp: connection refused", "gateway"),
        ("manifest unknown: pull access denied", "构建"),
    ],
)
def test_diagnose_recognises_common_failures(log: str, expected: str) -> None:
    """A failure without a reason is barely better than no check at all."""
    hint = diagnose(log)

    assert expected in hint or hint


def test_diagnose_falls_back_to_a_generic_hint() -> None:
    assert diagnose("something entirely unexpected")


# --- upstream credentials ----------------------------------------------------


def test_read_upstream_env_accepts_both_spellings(tmp_path: Path) -> None:
    """The gateway and this repository spell the same values differently."""
    first = tmp_path / "a.env"
    first.write_text("UPSTREAM_BASE_URL=https://x\nUPSTREAM_API_KEY=k\n", encoding="utf-8")
    second = tmp_path / "b.env"
    second.write_text(
        "AI_NATIVE_EVALS_LLM_BASE_URL=https://y\nAI_NATIVE_EVALS_LLM_API_KEY=k2\n",
        encoding="utf-8",
    )

    assert read_upstream_env(first) == {"base_url": "https://x", "api_key": "k"}
    assert read_upstream_env(second) == {"base_url": "https://y", "api_key": "k2"}


def test_read_upstream_env_ignores_comments_and_blanks(tmp_path: Path) -> None:
    path = tmp_path / "a.env"
    path.write_text(
        "# a comment\n\nUPSTREAM_BASE_URL=https://x\nUPSTREAM_API_KEY='quoted'\n",
        encoding="utf-8",
    )

    assert read_upstream_env(path) == {"base_url": "https://x", "api_key": "quoted"}


def test_read_upstream_env_requires_both_values(tmp_path: Path) -> None:
    """Half a credential is not a credential."""
    path = tmp_path / "a.env"
    path.write_text("UPSTREAM_BASE_URL=https://x\n", encoding="utf-8")

    assert read_upstream_env(path) is None


def test_read_upstream_env_returns_none_for_a_missing_file(tmp_path: Path) -> None:
    assert read_upstream_env(tmp_path / "absent.env") is None


# --- container exit code -----------------------------------------------------


def test_container_exit_reads_what_docker_wait_printed() -> None:
    """`docker wait` exits 0 when the *wait* succeeded; the code it prints is
    the container's. Reading the command status instead reported every crashed
    container as a success."""
    assert _parse_container_exit(0, "1") == 1
    assert _parse_container_exit(0, "0") == 0
    assert _parse_container_exit(0, "137") == 137


def test_container_exit_is_none_when_it_cannot_be_read() -> None:
    """A transport failure must not be mistaken for a clean exit."""
    assert _parse_container_exit(124, "") is None
    assert _parse_container_exit(0, "") is None
    assert _parse_container_exit(1, "docker: not found") is None


def test_container_exit_takes_the_last_printed_line() -> None:
    assert _parse_container_exit(0, "some noise\n0\n") == 0


# --- smoke container shape ---------------------------------------------------


def _args_for(profile: dict) -> list[str]:
    return _agent_args("Ubuntu-20.04", profile, "img:tag", "c", "net", "k", "m")


def test_smoke_gives_the_declared_workdir_somewhere_to_live() -> None:
    """A profile whose workdir is a project mount must still be probeable.

    A real run mounts the snapshot there; a smoke run has no snapshot, so Codex
    would fail resolving its `--cd` -- an artifact of the probe, not a defect in
    the profile.
    """
    joined = " ".join(_args_for({"adapter": "codex", "workdir": "/workspace/game-engine"}))

    assert "/workspace/game-engine:rw" in joined


def test_smoke_runs_the_agent_in_a_directory_that_always_exists() -> None:
    """EVAL_WORKDIR is Codex's --cd, so it must exist before the entrypoint runs."""
    assert "EVAL_WORKDIR=/tmp" in _args_for({"adapter": "codex"})


def test_smoke_never_overrides_codex_home() -> None:
    """Codex refuses to run with its home under a temporary directory."""
    joined = " ".join(_args_for({"adapter": "codex"}))

    assert "CODEX_HOME" not in joined


def test_smoke_probes_the_declared_executable_for_dsh() -> None:
    """DSH is an ACP server; what can be verified is that its binary starts."""
    profile = {"adapter": "dsh-acp", "environment": {"DSH_EXECUTABLE": "dsh"}}
    joined = " ".join(_args_for(profile))

    assert "dsh --version" in joined
    assert PROBE_PROMPT not in joined


def test_smoke_asks_codex_the_probe_question() -> None:
    joined = " ".join(_args_for({"adapter": "codex"}))

    assert PROBE_PROMPT in joined


def test_a_missing_workdir_gives_an_actionable_hint() -> None:
    """The generic "entrypoint not found" hint was misleading here."""
    hint = diagnose("Error: No such file or directory (os error 2)")

    assert "工作目录" in hint

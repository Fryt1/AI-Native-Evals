"""Unit tests for the hello-world Scorer."""

from __future__ import annotations

import asyncio
from pathlib import Path

from inspect_ai.model import ModelName
from inspect_ai.solver import TaskState

from ai_native_evals.scorers.hello_world import hello_world_scorer


def _make_state(run_dir: Path) -> TaskState:
    return TaskState(
        model=ModelName("mock/disabled"),
        sample_id="test-hello",
        epoch=0,
        input="verify",
        messages=[],
        metadata={"run_dir": str(run_dir)},
        store={"run_dir": str(run_dir)},
    )


def _score(run_dir: Path, relative_path: str, expected: str):
    return asyncio.run(
        hello_world_scorer(relative_path=relative_path, expected_text=expected)(
            _make_state(run_dir), None
        )
    )


def test_pass_when_content_matches(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "hello.txt").write_text("ai-native-codex-ok", encoding="utf-8")
    result = _score(tmp_path, "evidence/hello.txt", "ai-native-codex-ok")
    assert result.value is True
    assert result.answer == "pass"


def test_fail_on_content_mismatch(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "hello.txt").write_text("something-else", encoding="utf-8")
    result = _score(tmp_path, "evidence/hello.txt", "ai-native-codex-ok")
    assert result.value is False
    assert result.answer == "content_mismatch"


def test_fail_when_file_missing(tmp_path: Path) -> None:
    result = _score(tmp_path, "evidence/hello.txt", "ai-native-codex-ok")
    assert result.value is False
    assert result.answer == "missing"


def test_fail_when_run_dir_not_published() -> None:
    state = TaskState(
        model=ModelName("mock/disabled"),
        sample_id="test-hello",
        epoch=0,
        input="verify",
        messages=[],
    )
    result = asyncio.run(hello_world_scorer()(state, None))
    assert result.value is False
    assert result.answer == "scoring_failed"

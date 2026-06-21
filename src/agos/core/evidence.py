"""Evidence store that keeps executor activity and governed-repo anchors distinct.

agent_diff/  = executor-reported diff from the isolated agent workspace.
repo_anchor/ = governed repo HEAD + porcelain status at capture time, not a claim
               that the agent edited the governed working tree.
"""
from __future__ import annotations

import json
from pathlib import Path


class EvidenceStore:
    """Filesystem-backed evidence writer for one active task."""

    def __init__(self, evidence_dir: Path) -> None:
        self.dir = evidence_dir

    def _ensure(self, subdir: str) -> Path:
        path = self.dir / subdir
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_run(self, run_id: str, meta: dict) -> Path:
        """Write executor-run metadata to runs/<run_id>.json."""

        path = self._ensure("runs") / f"{run_id}.json"
        path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return path

    def append_message(self, run_id: str, event: dict) -> Path:
        """Append one JSON event line to messages/<run_id>.jsonl."""

        path = self._ensure("messages") / f"{run_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
        return path

    def write_gate_log(
        self,
        gate_id: str,
        ts: str,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> Path:
        """Write one gate evaluation log file."""

        path = self._ensure("gates") / f"{gate_id}-{ts}.log"
        path.write_text(
            f"exit_code={exit_code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n",
            encoding="utf-8",
        )
        return path

    def write_agent_diff(self, run_id: str, diff: str) -> Path:
        """Write executor-side diff evidence."""

        path = self._ensure("agent_diff") / f"{run_id}.diff"
        path.write_text(diff, encoding="utf-8")
        return path

    def write_repo_anchor(self, ts: str, head: str, status_porcelain: str) -> Path:
        """Write governed-repo HEAD/status evidence with no edit claim."""

        path = self._ensure("repo_anchor") / f"{ts}.json"
        path.write_text(
            json.dumps(
                {
                    "head": head,
                    "status_porcelain": status_porcelain,
                    "claim": None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

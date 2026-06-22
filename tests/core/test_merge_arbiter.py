from __future__ import annotations

from agos.core.arbiters import CandidateMergeArbiter, MergeCandidateSnapshot


def test_merge_arbiter_selects_non_overlapping_bundle():
    decision = CandidateMergeArbiter().decide_bundle(
        [
            _candidate("candidate-a", ("src/a.py",)),
            _candidate("candidate-b", ("src/b.py",)),
        ],
        dirty_paths=(),
    )

    assert decision.strategy == "non_overlapping_bundle"
    assert decision.candidate_ids == ("candidate-a", "candidate-b")


def test_merge_arbiter_selects_single_candidate():
    decision = CandidateMergeArbiter().decide_bundle(
        [_candidate("candidate-a", ("src/a.py",))],
        dirty_paths=(),
    )

    assert decision.strategy == "single_candidate"
    assert decision.candidate_ids == ("candidate-a",)


def test_merge_arbiter_requires_manual_merge_for_overlapping_candidates():
    decision = CandidateMergeArbiter().decide_bundle(
        [
            _candidate("candidate-a", ("src/a.py",)),
            _candidate("candidate-b", ("src/a.py",)),
        ],
        dirty_paths=(),
    )

    assert decision.strategy == "manual_merge_required"
    assert decision.conflict_candidate_ids == ("candidate-a", "candidate-b")


def test_merge_arbiter_requires_manual_merge_for_dirty_overlap():
    decision = CandidateMergeArbiter().decide_bundle(
        [_candidate("candidate-a", ("src/a.py",))],
        dirty_paths=("src/a.py",),
    )

    assert decision.strategy == "manual_merge_required"
    assert decision.conflict_candidate_ids == ("candidate-a",)


def _candidate(
    candidate_id: str,
    paths: tuple[str, ...],
    *,
    score: int = 1,
) -> MergeCandidateSnapshot:
    return MergeCandidateSnapshot(
        candidate_id=candidate_id,
        patch_ref=f"evidence/candidate_patches/{candidate_id}.patch",
        patch_sha256=f"sha-{candidate_id}",
        touched_paths=paths,
        tests_passed=True,
        review_open_blocking_count=0,
        accepted=True,
        score=score,
    )

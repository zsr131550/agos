# AGOS Stabilization Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace reconstructed provenance claims with an honest hybrid policy, add offline Ed25519 signed anchors, and ensure pull-request verification executes from protected base code and configuration.

**Architecture:** Persist optional provenance metadata on candidates so old JSON remains readable as `legacy_unattested`. Keep deterministic candidate validation separate from cryptographic provenance evaluation: reconstructed PR candidates can pass `optional` or `disabled` policy without review/decision claims, while `required` accepts only ledger-bound candidates covered by an allowed signed anchor (and verifies external attestations when declared). Both PR jobs install AGOS and load policy/gates from an explicit protected-base checkout.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, PyYAML, pytest, GitHub Actions YAML, `cryptography>=42` Ed25519

## Global Constraints

- Preserve all current CLI spellings and continue accepting existing `.agos` files.
- Existing candidates without provenance metadata classify as `legacy_unattested`; do not rewrite archives in place.
- Existing file and git-ref anchors remain valid integrity anchors but never satisfy required provenance.
- `merge_gate.provenance_policy` defaults to `optional`; CLI override is `--provenance-policy`.
- Public keys resolve relative to the trusted config file; private keys must never be loaded from `.agos`.
- Verification and all required PR CI paths work without model credentials or network calls at runtime.
- Reconstructed PR evidence must never contain a synthetic review, decision, or apply event.
- Every production behavior change follows a witnessed red-green test cycle.
- Keep total coverage at or above 90 percent.

---

### Task 1: Backward-Compatible Provenance and Policy Models

**Files:**
- Modify: `src/agos/core/config.py`
- Modify: `src/agos/core/execution.py`
- Modify: `src/agos/core/execution_service.py`
- Create: `src/agos/core/provenance.py`
- Modify: `tests/core/test_config.py`
- Modify: `tests/core/test_execution.py`
- Modify: `tests/core/test_execution_service.py`
- Create: `tests/core/test_provenance.py`

**Interfaces:**
- Produces: `ProvenancePolicy`, `CandidateProvenanceSource`, `CandidateProvenance`, `TrustedSignerConfig`, `MergeGateConfig`, and `candidate_provenance_source(candidate)`.
- Persists: `CandidatePatch.provenance: CandidateProvenance | None = None`.
- Guarantees: candidates exported by `ExecutionService.submit_candidate()` use `worker_export` and bind the exact `candidate_patch_created` ledger hash.

- [ ] **Step 1: Add failing compatibility and config tests**

Add tests that load a pre-Phase-2 candidate JSON without `provenance` and assert:

```python
candidate = CandidatePatch.model_validate(legacy_payload)
assert candidate.provenance is None
assert candidate_provenance_source(candidate) == "legacy_unattested"
```

Add config tests asserting omitted policy resolves to `optional`, an explicit `required` value loads, duplicate `(issuer, key_id)` signer pairs fail, and signer paths remain relative strings until the trusted config path is known.

- [ ] **Step 2: Run the model tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/core/test_config.py \
  tests/core/test_execution.py \
  tests/core/test_provenance.py -q
```

Expected: FAIL because the provenance/config types do not exist.

- [ ] **Step 3: Add exact persistence models**

Use these public values and legacy-safe defaults:

```python
ProvenancePolicy = Literal["required", "optional", "disabled"]
CandidateProvenanceSource = Literal[
    "worker_export",
    "external_attested",
    "ci_reconstructed",
    "legacy_unattested",
]

class CandidateProvenance(BaseModel):
    source: CandidateProvenanceSource
    ledger_head_hash: str | None = None
    attestation_ref: str | None = None

class TrustedSignerConfig(BaseModel):
    issuer: str
    key_id: str
    public_key_path: str

class MergeGateConfig(BaseModel):
    provenance_policy: ProvenancePolicy = "optional"
    trusted_signers: list[TrustedSignerConfig] = Field(default_factory=list)
```

Add `merge_gate: MergeGateConfig` to `AGOSConfig`. Validate non-empty signer fields and unique issuer/key pairs. Keep `CandidatePatch.provenance` optional rather than defaulting a serialized object.

- [ ] **Step 4: Bind worker exports to their creation event**

Change `ExecutionService.submit_candidate()` to append `candidate_patch_created` before its final candidate write and persist:

```python
provenance=CandidateProvenance(
    source="worker_export",
    ledger_head_hash=str(created["hash"]),
)
```

The event payload and patch storage format remain unchanged.

- [ ] **Step 5: Verify GREEN and commit**

Run:

```bash
.venv/bin/python -m pytest \
  tests/core/test_config.py tests/core/test_execution.py \
  tests/core/test_execution_service.py tests/core/test_provenance.py -q
.venv/bin/python -m ruff check src/agos/core tests/core
```

Commit:

```bash
git add src/agos/core tests/core
git commit -m "feat: classify candidate provenance"
```

---

### Task 2: Offline Ed25519 Anchors and External Attestations

**Files:**
- Modify: `pyproject.toml`
- Create: `src/agos/core/signing.py`
- Modify: `src/agos/core/trust_anchor.py`
- Modify: `src/agos/core/provenance.py`
- Modify: `src/agos/cli/cmd_anchor.py`
- Modify: `tests/core/test_trust_anchor.py`
- Modify: `tests/core/test_provenance.py`
- Modify: `tests/cli/test_anchor.py`

**Interfaces:**
- Produces: `SignedTrustAnchorEnvelope`, `SignedFileTrustAnchorStore`, `CandidateAttestationPayload`, `SignedCandidateAttestation`, `publish_current_signed_anchor()`, `verify_current_signed_anchor()`, and `verify_candidate_attestation()`.
- Signing bytes: canonical UTF-8 JSON of `{algorithm, issuer, key_id, payload}`; signature encoding is strict base64.
- Algorithms: only the literal `Ed25519` is accepted.

- [ ] **Step 1: Add the signing dependency contract**

Add:

```toml
[project.optional-dependencies]
signing = ["cryptography>=42"]
dev = [
    "build>=1.2",
    "cryptography>=42",
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.8",
]
```

Install the updated local dev environment before running signing tests.

- [ ] **Step 2: Write failing signed-anchor tests**

Generate ephemeral Ed25519 PEM keys in `tmp_path`. Test a valid signed round trip, tampered payload, tampered signature, unknown issuer/key pair, wrong key, unsupported algorithm, stale ledger head, and missing `cryptography` error text. Assert unsigned `FileTrustAnchorStore` verification still passes integrity checks but reports `signed is False`.

- [ ] **Step 3: Run signing tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest \
  tests/core/test_trust_anchor.py tests/core/test_provenance.py \
  tests/cli/test_anchor.py -q
```

Expected: FAIL because signed envelope/store APIs are absent.

- [ ] **Step 4: Implement lazy Ed25519 helpers and envelopes**

`signing.py` must import `cryptography` inside callable helpers and raise:

```text
Ed25519 support requires AGOS with the 'signing' extra
```

The signed envelope includes:

```python
class SignedTrustAnchorEnvelope(BaseModel):
    schema_version: int = 1
    algorithm: Literal["Ed25519"] = "Ed25519"
    issuer: str
    key_id: str
    payload: TrustAnchorPayload
    signature: str
```

Require `envelope.issuer == envelope.payload.issuer`. Resolve `TrustedSignerConfig.public_key_path` against `trusted_config_path.parent`, reject absolute/private-key paths in governed config, and verify entirely from local files.

- [ ] **Step 5: Add signed anchor CLI without breaking old commands**

Extend the existing backend literal with `signed-file`. `anchor publish` requires `--private-key` and `--key-id` only for that backend. `anchor verify` requires `--trusted-config` for signed verification. Existing `file` and `git-ref` invocations and output remain valid.

- [ ] **Step 6: Implement external candidate attestation verification**

Bind the signature to `candidate_id`, `patch_sha256`, `base_commit`, `source_agent`, issuer, and key ID. `external_attested` candidates require `attestation_ref`; path resolution stays within `.agos/tasks/current`, and verification checks every bound field before accepting the signature.

- [ ] **Step 7: Verify GREEN and commit**

Run:

```bash
.venv/bin/python -m pytest \
  tests/core/test_trust_anchor.py tests/core/test_provenance.py \
  tests/cli/test_anchor.py -q
.venv/bin/python -m ruff check src/agos/core/signing.py \
  src/agos/core/trust_anchor.py src/agos/core/provenance.py \
  src/agos/cli/cmd_anchor.py tests
```

Commit:

```bash
git add pyproject.toml src/agos/core/signing.py src/agos/core/trust_anchor.py \
  src/agos/core/provenance.py src/agos/cli/cmd_anchor.py \
  tests/core/test_trust_anchor.py tests/core/test_provenance.py tests/cli/test_anchor.py
git commit -m "feat: verify offline signed anchors"
```

---

### Task 3: Policy-Aware Merge Gate

**Files:**
- Create: `src/agos/core/merge_gate_provenance.py`
- Modify: `src/agos/core/merge_gate.py`
- Modify: `src/agos/cli/cmd_merge_gate.py`
- Modify: `tests/core/test_merge_gate.py`
- Create: `tests/core/test_merge_gate_provenance.py`
- Modify: `tests/cli/test_merge_gate.py`

**Interfaces:**
- Extends: `verify_merge_gate(..., trusted_config_path=None, provenance_policy=None, signed_anchor_store=None)`.
- Extends: `MergeGateResult.provenance_state: Literal["proven", "unprovenanced", "disabled"]`.
- Produces: one `provenance` check and a policy evaluation that cannot silently downgrade contradictory signed evidence.

- [ ] **Step 1: Write the policy matrix as failing tests**

Cover these exact outcomes:

| Policy | Candidate evidence | Anchor | Expected |
|---|---|---|---|
| `required` | current `worker_export`, accepted/applied | allowed valid signed anchor | pass / `proven` |
| `required` | `ci_reconstructed` | any | block |
| `required` | `legacy_unattested` | unsigned | block |
| `optional` | current `worker_export`, accepted/applied | allowed valid signed anchor | pass / `proven` |
| `optional` | strict governed evidence | unsigned or absent | pass / `unprovenanced` |
| `optional` | tested `ci_reconstructed` | absent | pass / `unprovenanced` |
| `disabled` | tested exact-diff candidate | absent | pass / `disabled` |

Also test stale candidate ledger hash, forged source metadata, invalid external attestation, invalid supplied signed anchor, and missing trusted config. Contradictory signed material blocks under every policy where it is supplied.

- [ ] **Step 2: Run the new matrix to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/core/test_merge_gate_provenance.py -q
```

Expected: FAIL because policy-aware verification is absent.

- [ ] **Step 3: Load policy only from an explicit trusted source**

When `trusted_config_path` is provided, load `AGOSConfig` from exactly that file and never fall back to subject `.agos/agos.yaml`. Resolve workflow gates, provenance policy, and trusted signer keys from it. Without the argument, retain current local behavior and use `paths.agos_yaml`.

- [ ] **Step 4: Separate deterministic evidence from provenance claims**

For `ci_reconstructed`, require exactly one candidate with status `tested`, complete passed `patch_applies` plus locked gates, exact `base..head` patch bytes, and no `review_refs`, `decision_ref`, `candidate_decision_recorded`, or `candidate_applied`. Do not call the accepted/applied decision path for it.

For governed candidates, retain patch/test/review/decision/apply verification. Validate `worker_export.ledger_head_hash` equals that candidate's unique `candidate_patch_created` event hash. Validate `external_attested` with `verify_candidate_attestation()`.

- [ ] **Step 5: Enforce signed coverage without conflating integrity anchors**

`required` must receive an allowed valid `SignedTrustAnchorEnvelope` whose current ledger head covers every governed candidate creation record. An unsigned store can still satisfy explicit legacy `--require-anchor`, but never sets `provenance_state="proven"`.

- [ ] **Step 6: Expose CLI overrides**

Add:

```text
--provenance-policy required|optional|disabled
--trusted-config PATH
--anchor-backend signed-file
```

Preserve `--require-anchor`, `--allow-missing-review`, and `--allow-legacy-decisionless`. JSON always includes `provenance_state`.

- [ ] **Step 7: Verify GREEN and commit**

Run:

```bash
.venv/bin/python -m pytest \
  tests/core/test_merge_gate.py tests/core/test_merge_gate_provenance.py \
  tests/cli/test_merge_gate.py -q
.venv/bin/python -m ruff check src/agos/core/merge_gate.py \
  src/agos/core/merge_gate_provenance.py src/agos/cli/cmd_merge_gate.py tests
```

Commit:

```bash
git add src/agos/core/merge_gate.py src/agos/core/merge_gate_provenance.py \
  src/agos/cli/cmd_merge_gate.py tests/core/test_merge_gate.py \
  tests/core/test_merge_gate_provenance.py tests/cli/test_merge_gate.py
git commit -m "feat: enforce hybrid provenance policy"
```

---

### Task 4: Honest Reconstructed PR Preparation

**Files:**
- Modify: `src/agos/cli/cmd_prepare_merge_gate.py`
- Modify: `tests/cli/test_prepare_merge_gate.py`
- Modify: `tests/ci/test_merge_gate_smoke.py`

**Interfaces:**
- Extends: `prepare-merge-gate --trusted-config PATH`.
- Produces: one `ci_reconstructed` candidate in `tested` state plus deterministic gate evidence and exact submitted diff binding.
- Removes: all synthetic review, decision, and apply artifacts from this command.

- [ ] **Step 1: Replace the old success assertion with a failing honesty contract**

After prepare, assert:

```python
candidate = store.read_candidates()[0]
assert candidate.provenance.source == "ci_reconstructed"
assert candidate.status == "tested"
assert candidate.review_refs == []
assert candidate.decision_ref is None
assert store.read_decisions(candidate.id) == []
assert not any(
    record["type"] in {"candidate_review_completed", "candidate_decision_recorded", "candidate_applied"}
    for record in Ledger(paths.ledger).read_all()
)
```

Invoke merge-gate with `optional` and assert pass plus `unprovenanced`; invoke `required` and assert block.

- [ ] **Step 2: Run prepare tests to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/cli/test_prepare_merge_gate.py -q
```

Expected: FAIL because prepare still synthesizes review/decision/apply.

- [ ] **Step 3: Remove synthetic evidence production**

Delete `_materialize_clean_ci_review()` and `_materialize_ci_decision()`. After gates pass, persist the candidate as `tested` with its test refs and `CandidateProvenance(source="ci_reconstructed", ledger_head_hash=<creation event hash>)`. Keep existing anchor arguments and unsigned publication for CLI compatibility, but never treat that anchor as provenance proof.

- [ ] **Step 4: Use trusted gates during preparation**

If `--trusted-config` is supplied, load workflow/gates from that exact path. A missing, unreadable, or invalid trusted config exits 1 before publishing task evidence; never fall back to subject config.

- [ ] **Step 5: Verify GREEN and commit**

Run:

```bash
.venv/bin/python -m pytest \
  tests/cli/test_prepare_merge_gate.py tests/ci/test_merge_gate_smoke.py \
  tests/core/test_merge_gate_provenance.py -q
.venv/bin/python -m ruff check src/agos/cli/cmd_prepare_merge_gate.py tests
```

Commit:

```bash
git add src/agos/cli/cmd_prepare_merge_gate.py \
  tests/cli/test_prepare_merge_gate.py tests/ci/test_merge_gate_smoke.py
git commit -m "fix: report reconstructed PR provenance honestly"
```

---

### Task 5: Protected-Base Verifier Workflow

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/ci/test_autonomous_loop_ci_policy.py`
- Create: `tests/ci/test_trusted_merge_gate_workflow.py`

**Interfaces:**
- Consumes: PR base SHA as the sole verifier/config revision and PR head SHA as the subject revision.
- Produces: evidence-only artifact `.agos/tasks/current` and no provider-secret dependency.

- [ ] **Step 1: Add a failing workflow trust-boundary test**

Parse `ci.yml` and assert both `agos-prepare` and `merge-gate`:

- checkout `${{ github.event.pull_request.base.sha }}` into `trusted`;
- checkout `${{ github.event.pull_request.head.sha }}` into `subject`;
- install `./trusted[dev]`, never `./subject` or `.`;
- pass `--trusted-config "$GITHUB_WORKSPACE/trusted/.agos/agos.yaml"`;
- decide whether governance exists from the trusted config, not subject files;
- upload/download only `subject/.agos/tasks/current`;
- contain no model-provider secret names.

- [ ] **Step 2: Run the workflow test to verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/ci/test_trusted_merge_gate_workflow.py -q
```

Expected: FAIL because current jobs install and configure from the PR checkout.

- [ ] **Step 3: Split trusted and subject checkouts**

Use explicit `path: trusted` and `path: subject` checkouts with full history. Install the base verifier, run commands from `subject`, and pass the absolute trusted config path. Retain the existing offline provider policy and artifact boundary.

- [ ] **Step 4: Verify GREEN and commit**

Run:

```bash
.venv/bin/python -m pytest \
  tests/ci/test_trusted_merge_gate_workflow.py \
  tests/ci/test_autonomous_loop_ci_policy.py -q
```

Commit:

```bash
git add .github/workflows/ci.yml tests/ci/test_trusted_merge_gate_workflow.py \
  tests/ci/test_autonomous_loop_ci_policy.py
git commit -m "ci: verify pull requests from protected base"
```

---

### Task 6: Migration Documentation and Phase 2 Verification

**Files:**
- Modify: `README.md`
- Create: `docs/provenance.md`
- Modify only on regression: source/tests touched by Tasks 1-5.

**Interfaces:**
- Documents: policy matrix, legacy classification, signed anchor generation/verification, trusted config resolution, and reconstructed evidence limitations.
- Produces: a Phase 2 branch that remains fully offline with respect to model providers.

- [ ] **Step 1: Document migration and operational commands**

Document that omitted policy is `optional`, `ci_reconstructed` is never Agent provenance, `required` needs `signed-file` plus allowed signer config, and private key paths are CLI-only. Include local key generation, publish, verify, prepare, and merge-gate examples using repository-relative public keys and an external private key.

- [ ] **Step 2: Run static verification**

Run:

```bash
.venv/bin/python -m ruff check src tests
.venv/bin/python -m compileall -q src tests
git diff --check
```

- [ ] **Step 3: Run the complete offline suite and build**

Run:

```bash
PATH="$PWD/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
PYTHONPATH="$PWD/src" \
.venv/bin/python -m pytest --cov=agos --cov-report=term-missing -q
.venv/bin/python -m build --no-isolation
.venv/bin/python -c "from pathlib import Path; assert list(Path('dist').glob('agos-*.whl')); assert list(Path('dist').glob('agos-*.tar.gz'))"
```

Expected: zero failures, provider smokes skipped, coverage at least 90 percent, wheel and sdist present.

- [ ] **Step 4: Inspect packaging and commit documentation**

Run:

```bash
git status --short
git diff --check
git log --oneline --decorate -10
```

Commit:

```bash
git add README.md docs/provenance.md
git commit -m "docs: explain hybrid provenance policies"
```

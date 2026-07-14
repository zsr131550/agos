# Hybrid Provenance and Offline Verification

AGOS separates deterministic patch verification from a cryptographic claim about where a candidate came from. This distinction keeps existing repositories usable while preventing reconstructed CI evidence from being presented as Agent provenance.

All signature operations use local Ed25519 keys and files. Publishing or verifying a signed anchor does not contact a model provider, key service, or network endpoint.

## Candidate Sources

| Source | Meaning | Can satisfy `required` |
| --- | --- | --- |
| `worker_export` | AGOS created the candidate from a configured worker workspace and bound its source fields to the creation ledger record. | Yes, when a trusted signed anchor covers the ledger. |
| `external_attested` | An external producer supplied a signed attestation bound to candidate ID, patch hash, base commit, source agent, and creation time. | Yes, when both the attestation and covering anchor verify against allowed signers. |
| `ci_reconstructed` | `prepare-merge-gate` reconstructed the submitted diff and deterministic gate results in CI. | No. It is validation evidence, not proof that an Agent produced the patch. |
| `legacy_unattested` | A pre-provenance candidate JSON has no `provenance` field. | No. AGOS reads it without rewriting the archive. |

Provenance metadata is additionally bound to the candidate's `candidate_patch_created` ledger event, including the source agent, workspace, base commit, attestation reference, and creation-event hash. Conflicting metadata fails closed.

## Policy Matrix

Configure the policy under `merge_gate.provenance_policy`, or override it for one invocation with `--provenance-policy`.

| Policy | Signed provenance | Reconstructed or legacy evidence | Result state |
| --- | --- | --- | --- |
| `required` | Every candidate must be `worker_export` or valid `external_attested`, and an allowed Ed25519 signed anchor must cover the current ledger. | Blocked. | `proven` only after every check passes. |
| `optional` | Valid signed candidates are fully verified. Explicit invalid or contradictory signed material still blocks. | Deterministic tests and exact diff binding may pass. | `proven` or explicit `unprovenanced`. |
| `disabled` | No provenance claim is required or inferred. Explicit malformed signed material is still not ignored. | Validated through the ordinary ledger, candidate, gate, and diff checks. | `disabled`. |

The default is `optional`. This is the compatibility mode for repositories that omit the new field and for existing candidate archives. Use `disabled` only when the caller intentionally does not want a provenance claim; it does not disable the rest of merge-gate verification.

Example trusted configuration:

```yaml
merge_gate:
  provenance_policy: required
  trusted_signers:
    - issuer: local-release
      key_id: release-2026
      public_key_path: keys/release.pub.pem
```

`public_key_path` is resolved relative to the directory containing the explicitly trusted `agos.yaml`. It must be relative and cannot escape that directory. Duplicate `(issuer, key_id)` entries are rejected.

## Generate Offline Keys

The public key may live under governed `.agos/`; the private key must not. AGOS resolves symlinks before enforcing this boundary.

```bash
mkdir -p "$HOME/.config/agos/keys" .agos/keys
openssl genpkey \
  -algorithm Ed25519 \
  -out "$HOME/.config/agos/keys/release-2026.pem"
chmod 600 "$HOME/.config/agos/keys/release-2026.pem"
openssl pkey \
  -in "$HOME/.config/agos/keys/release-2026.pem" \
  -pubout \
  -out .agos/keys/release.pub.pem
```

Install the optional local signing dependency before using `signed-file` commands:

```bash
python -m pip install -e ".[signing]"
```

This dependency is also included by `.[dev]`. An offline installation requires the dependency wheel to already be available in the local package cache or wheelhouse; runtime signing and verification themselves are network-free.

## Publish and Verify a Signed Anchor

After AGOS has created the governed candidate and appended its provenance-bound creation event, publish the current ledger head:

```bash
agos anchor publish \
  --backend signed-file \
  --path .agos/tasks/current/evidence/signed-anchor.json \
  --issuer local-release \
  --key-id release-2026 \
  --private-key "$HOME/.config/agos/keys/release-2026.pem"
```

Verify it using only the allowed public key from trusted configuration:

```bash
agos anchor verify \
  --backend signed-file \
  --path .agos/tasks/current/evidence/signed-anchor.json \
  --trusted-config .agos/agos.yaml \
  --json
```

Payload, envelope issuer, key ID, algorithm, schema, or signature tampering causes verification to fail. A legacy `file` or `git-ref` anchor can still satisfy an explicit compatibility `--require-anchor`, but it never changes provenance to `proven`.

## Trusted Merge Gate

Run the merge gate from the subject checkout while loading policy, complete workflow gates, and signer keys from an explicit protected-base configuration:

```bash
agos merge-gate \
  --require-anchor \
  --anchor-backend signed-file \
  --anchor-path .agos/tasks/current/evidence/signed-anchor.json \
  --trusted-config "$TRUSTED_CHECKOUT/.agos/agos.yaml" \
  --base "$BASE_SHA" \
  --head "$HEAD_SHA" \
  --json
```

When `--trusted-config` is present, AGOS loads exactly that file. It does not fall back to the subject's `.agos/agos.yaml`; the active task must use the trusted default workflow and its complete gate list.

The JSON result always includes one of:

```json
{"provenance_state":"proven"}
{"provenance_state":"unprovenanced"}
{"provenance_state":"disabled"}
```

These snippets show only the field relevant here; the real result also includes the overall decision and individual checks.

## Reconstructed Pull Request Validation

For an offline, provider-independent PR check, prepare evidence from the subject diff with protected-base gates:

```bash
cd "$SUBJECT_CHECKOUT"
agos prepare-merge-gate \
  --base "$BASE_SHA" \
  --head "$HEAD_SHA" \
  --trusted-config "$TRUSTED_CHECKOUT/.agos/agos.yaml" \
  --anchor-path .agos/tasks/current/evidence/anchors.json \
  --issuer github-actions

agos merge-gate \
  --require-anchor \
  --anchor-backend file \
  --anchor-path .agos/tasks/current/evidence/anchors.json \
  --trusted-config "$TRUSTED_CHECKOUT/.agos/agos.yaml" \
  --base "$BASE_SHA" \
  --head "$HEAD_SHA" \
  --json
```

`prepare-merge-gate` creates a tested `ci_reconstructed` candidate. It does not manufacture review, arbiter decision, apply, or Agent provenance records. Therefore this flow can pass `optional` with `provenance_state: unprovenanced`, or pass `disabled` with `provenance_state: disabled`; it intentionally blocks under `required`.

In CI, install AGOS from the protected base checkout, execute it in the subject checkout, and exchange only `subject/.agos/tasks/current` between jobs. The repository workflow implements this boundary in `.github/workflows/ci.yml`.

## Migration Checklist

1. Leave the field omitted, or set `optional`, to read existing candidates without rewriting them.
2. Add a repository public key and an allowed `(issuer, key_id)` entry before enabling `required`.
3. Keep private key paths outside `.agos`; provide them only through the signing CLI invocation or a protected CI secret file.
4. Confirm automation reads verifier code and `agos.yaml` from the protected base revision.
5. Treat `unprovenanced` as an explicit result, never as a weaker spelling of `proven`.

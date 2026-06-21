# Merge Notes

This directory was assembled from Multica/Codex workspace snapshots under:

`C:\Users\ZR\multica_workspaces_desktop-api.multica.ai\1b48d943-ea41-466a-83ee-a0f6849c7930`

Base snapshot:

- `b2d4d7a0\workdir\agos-review`

Missing files were filled from:

- `a197c1ae\workdir\agos`

Assembly rules:

1. Use the most complete/latest snapshot as the base.
2. Only copy files missing from the base.
3. Do not merge contents of same-path files.
4. Remove transient artifacts from the resulting project copy.

Validation after fixes:

- `pytest -q --basetemp E:\AGOS_V2\.tmp_agos_merged\.basetemp`
- Result: `73 passed, 1 skipped`

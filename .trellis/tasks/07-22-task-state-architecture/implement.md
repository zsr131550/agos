# Task State Architecture Implementation Plan

## 1. Establish The Interface With TDD

- [x] 新增 `tests/core/test_task_state.py`。
- [x] 先覆盖 `current()` 的完整 replay、缓存修复和篡改拒绝，确认测试失败。
- [x] 再覆盖单事件、revision、baseline、缓存降级、批量前缀和并发，每个行为按 red -> green 垂直推进。

## 2. Implement The Deep Module

- [x] 新增 immutable event/revision/snapshot/commit 类型与错误类型。
- [x] 实现事件 registry、validators、revision policies 与完整 projector。
- [x] 将 Ledger 锁扩展为 Task State 级锁顺序，避免递归锁死。
- [x] 实现 `current()`、`record()`、baseline detector 和缓存降级语义。
- [x] 保持历史事件 JSON、`load_status()` 返回类型和底层 Ledger 审计读取兼容。

## 3. Migrate Production Writers

- [x] Core：ExecutionService、ReviewService、TaskExecutionService。
- [x] CLI：checkpoint、CI、prepare-merge-gate。
- [x] Dashboard：agent selection、pause/resume/restart/archive/restore 与 dispatch 状态。
- [x] 清除生产 `save_status()` 调用和 Task State 相关直接 `Ledger.append()`。
- [x] 保留 Merge Gate、Trust Anchor 与专门审计读取。

## 4. Documentation

- [x] 更新 `CONTEXT.md` 的 Task State 与 Task Event 词汇。
- [x] 新增 ADR，记录权威源、baseline、cache downgrade 和 prefix commit。
- [x] 更新 `docs/state-security.md`，描述新 interface 与恢复方式。

### 已完成的补充实现

- [x] 修复跨实例 baseline migration：读路径保留兼容 cache，下一次成功写入先追加 `task_state_baselined`。
- [x] 新写入统一要求 active `task_id`，并迁移 Core、CLI 与 Dashboard 调用方。
- [x] 对 active Executor Run 实施 checkpoint/terminal observation 的 `run_id` guard。
- [x] Dashboard 对不可协调 external dispatch 记录 no-op audit fact、返回结构化错误并 fail closed。
- [x] 更新 ADR、状态安全说明与领域词汇，记录 migration、identity、run 与 recovery 语义。

## 5. Validation

- [x] `pytest -q tests/core/test_task_state.py tests/core/test_status.py tests/core/test_ledger.py` 与 Core writer 分组：`124 passed`。
- [x] `pytest -q tests/cli/test_checkpoint.py tests/cli/test_ci.py tests/cli/test_prepare_merge_gate.py`：`22 passed`。
- [x] `pytest -q tests/web/test_api.py tests/web/test_server.py`：`104 passed`。
- [ ] `pytest -q -m "not integration"`：`943 passed, 7 skipped`；4 个环境前置失败（Windows symlink 权限、缺少 `wheel`）。
- [x] `ruff check src tests scripts`。
- [x] `rg -n "save_status\(|Ledger\([^\n]*\)\.append|\.ledger\.append\(" src/agos`，人工确认只剩兼容 writer 与专门审计读取。
- [x] 使用 `git diff --check` 检查补丁格式。

## Risk And Rollback Points

- 在迁移任何 writer 前，先让 TaskState interface tests 绿色。
- Core、CLI、Dashboard 分组迁移；每组测试绿色后再继续。
- 不改写现有 Ledger 或已归档任务。
- 第一阶段保持单一工作提交，失败时可整体回退。

# Task State Architecture

## Goal

将分散在 Core、CLI 与 Dashboard 中的 Task Ledger/Status 双写协议收敛到一个 deep `TaskState` module，使 Ledger 继续作为唯一权威事实源，同时保持现有 CLI、Dashboard、事件 JSON 和任务恢复行为兼容。

## Background

- `src/agos/core/status.py` 与 `docs/state-security.md` 已定义 Ledger 为权威、`status.json` 为可修复派生缓存。
- 当前生产代码在 Dashboard、checkpoint、CI、prepare-merge-gate、Execution、Review 和 TaskExecution 中重复执行 `load_status -> Ledger.append -> mutate Status -> save_status`。
- 直接写入点不能共享完整的跨进程锁、revision 冲突规则、缓存失败语义或投影校验。
- Merge Gate 与 Trust Anchor 需要继续直接验证 Ledger，但不应参与 Task State 写入。

## Requirements

1. 新增 `TaskState.current()` 与 `TaskState.record()` 两入口 interface，并以该 interface 作为主要测试 seam。
2. `TaskState.current()` 必须验证完整 Ledger、从 Ledger 完整重放 Status，并在需要时原子修复 `status.json`。
3. `TaskState.record()` 必须在统一跨进程锁内完成事件校验、revision 检查、Ledger 追加、`fsync`、Status 投影和缓存替换。
4. 使用通用 `TaskEvent(name, facts)`；内部事件 registry 拥有字段校验、投影规则与 revision 分类。
5. 新事件必须已注册；历史未知事件保留为投影 no-op，并通过 snapshot warning 报告。
6. 生命周期转换必须提供 `TaskRevision`；普通事实允许无 revision 并发追加；Task 初始化使用 `TaskRevision.empty()`。
7. 旧任务首次写入时，如果兼容 Status 含有 Ledger 无法重建的事实，先追加一次 `task_state_baselined` 事件，再追加调用方事件。
8. Ledger 已持久化但 Status 缓存写失败时，提交仍成功并返回 `cache_synced=False`；只有提交状态不可确定时才抛出 `TaskStateCommitIndeterminate`。
9. 批量 `record(event, *more)` 写前校验全部事件；中途失败时保留已确认提交前缀并抛出 `TaskStateBatchInterrupted`，不得伪装事务。
10. 第一阶段迁移全部 AGOS 生产 writer；生产代码不再直接组合 `Ledger.append()` 与 `save_status()`。
11. `load_status()` 保留为 `TaskState.current()` 的兼容 adapter；`save_status()` 仅保留给旧调用方和测试夹具。
12. Merge Gate、Trust Anchor 和 Ledger 专项测试保留底层验证读取。

### 已确认的扩展约束

13. baseline-eligible legacy cache 仅在首次成功写入前保留为迁移输入；任何实例的后续 `record()` 都必须先落账 `task_state_baselined`，且 `current()` 永不以该 cache 作为 projector seed。
14. 所有调用方提交的新 `TaskEvent` 必须携带与当前 Task 匹配的 `task_id`；缺失或不匹配均在 append 前拒绝，历史缺失值仍可 replay。
15. 存在 active Executor Run 时，`checkpoint`、`executor_completed` 与 `executor_blocked` 必须引用当前 `run_id`，旧 run 的 observation 不得落账。
16. Dashboard 在外部 executor 已启动但无法以 `executor_dispatched` 完成持久化协调时，必须记录不投影的 `executor_dispatch_unreconciled` 事实、暴露其恢复信息并阻止重复 dispatch。

## Acceptance Criteria

- [x] `TaskState.current()` 可从缺失、过期或无效缓存恢复与完整 Ledger 一致的 Status。
- [x] 篡改 Ledger 时不追加事件、不覆盖缓存，并保持 fail-closed。
- [x] 单事件和短批量事件返回连续 records、最新 Status 和 revision。
- [x] 生命周期事件缺少或使用过期 revision 时不写入，并返回明确冲突错误。
- [x] 旧缓存差异在首次写入时转化为一次 baseline 事件，之后删除缓存仍能完整恢复。
- [x] 缓存替换失败不会把已提交 Ledger 事件报告为失败；后续读取可修复缓存。
- [x] 批量故障可区分确认提交前缀、未处理事件和提交状态不确定。
- [x] 并发进程写入不会产生重复序号、断链或丢失普通事实。
- [x] AGOS 生产代码中的 Task State writer 全部通过 `TaskState.record()`。
- [x] 现有 CLI 与 Dashboard 行为测试通过，历史事件 JSON 继续可读。
- [ ] 非 integration pytest 全量：`943 passed, 7 skipped`；4 个环境前置失败（Windows symlink 权限、缺少 `wheel`）待环境修复。
- [x] `ruff check src tests scripts` 通过。
- [x] ADR 记录 Ledger 权威、baseline、缓存降级和批量前缀语义。

### Additional Acceptance Criteria

- [x] baseline migration 在跨 `TaskState` 实例的 read-then-write 后仍保留 legacy-only facts。
- [x] 新事件的 identity 缺失或不匹配会 fail closed，历史无 identity 的 record 仍可 replay。
- [x] redispatch 后的旧 executor run 无法写入 checkpoint 或 terminal observation。
- [x] unreconciled dispatch 不改变 Status，Dashboard 返回结构化错误并阻止第二次 external start。

## Out of Scope

- Candidate Evidence、Agent 目录、Execution Run 和 Task Run 生命周期的后续阶段重构。
- 新增数据库、远端存储或公开 persistence port。
- 基于可信 checkpoint 的增量 replay 性能优化。
- 重命名历史事件或改写既有 Ledger。
- 删除 Ledger 的专门审计 interface。

## Open Questions

无阻塞问题。所有产品、兼容性、并发和故障语义均已由用户确认。

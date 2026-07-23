# Task State Architecture Design

## Module And Seam

新增 `src/agos/core/task_state.py`，外部 seam 只暴露：

```python
class TaskState:
    def current(self) -> TaskSnapshot | None: ...
    def record(
        self,
        event: TaskEvent,
        *more: TaskEvent,
        expected: TaskRevision | None = None,
    ) -> TaskCommit: ...
```

`Ledger`、事件 registry、projector、跨进程锁、原子缓存 writer 和 legacy baseline detector 均属于 implementation 或内部 seam。

## Data Contracts

- `TaskEvent`: immutable `name` + JSON-compatible `facts`；调用方不能提供 `seq`、`ts`、`prev_hash` 或 `hash`。
- `TaskRevision`: immutable `seq` + `head_hash`，包含 `empty()` factory。
- `TaskSnapshot`: Task、Status、revision、历史未知事件 warning；调用方修改返回对象不会持久化。
- `TaskCommit`: 本次 records、最终 snapshot、`cache_synced` 和非致命 warning。

错误分为 validation、conflict、integrity、confirmed write failure、commit indeterminate 与 batch interrupted。错误必须明确调用方是否可以重试。

### Identity And Executor Run Contract

所有 caller-supplied 新事件都在写入前校验 `facts.task_id == active Task.id`。历史 ledger record 缺少 `task_id` 时保持 replay 兼容，但已有且不匹配的值 fail closed。存在 active executor run 时，`checkpoint`、`executor_completed`、`executor_blocked` 必须包含同一 `run_id`；redispatch 后旧 run 的 observation 以 conflict 拒绝。

## Event Registry

内部 registry 为每个现有生产事件声明：

- facts validator；
- Status reducer 或 projection no-op；
- `fact`、`transition` 或 `initialization` revision policy。

新写入的未知事件在落盘前拒绝。重放历史未知事件时保留记录、不改变 Status，并在 snapshot 中产生 warning。

`executor_dispatch_unreconciled` 是 `fact` 类型、projection no-op 的受校验事件；它记录 Task、adapter、run、trigger、stage 和 evidence reference，绝不伪造 `executor_dispatched` 或修改 Status。

## Read Flow

1. 获取 Task State 跨进程锁。
2. 读取 Task 和完整 Ledger。
3. 验证 JSON、序号、哈希链与 Task identity。
4. 从 baseline 或 Task 初始状态完整重放全部事件。
5. 与缓存比较；缺失、过期、无效或内容不同则原子替换。
6. 缓存替换失败时返回内存 snapshot，并标记非致命 warning。

Status 缓存不作为正常 projector seed。不存在 Task/Ledger 重放输入时，兼容 adapter 保持原 `load_status()` 的旧行为。

当 cache 可解析、Task/head 匹配、尚未 baseline 且含 pure replay 无法恢复的事实时，read flow 返回 pure-ledger snapshot 与 migration warning，但保留磁盘 cache。该例外只保存一次性 baseline 输入；任何实例的下一次成功 `record()` 先写 baseline，随后恢复严格 cache derivation。

## Write Flow

1. 在加锁前完成事件对象和 JSON 基础校验。
2. 获取 Task State 跨进程锁并验证当前 Ledger。
3. 按事件 registry 校验 facts 与 revision policy。
4. 如果是旧任务且兼容缓存包含纯 replay 无法恢复的字段，先产生 `task_state_baselined`。
5. 预计算全部 records 与预期 Status。
6. 按顺序追加并 `fsync`；每条 durable record 都是权威提交。
7. 从最终 Ledger 记录生成 Status，并原子替换缓存。
8. 返回 `TaskCommit`；缓存失败设置 `cache_synced=False`，不否定 Ledger 提交。

## Concurrency

- 锁覆盖 verify、revision check、append、projection 与 cache replace。
- 普通事实按 Ledger 顺序全部保留。
- transition 事件必须匹配调用方读取的 revision。
- initialization 事件必须匹配 `TaskRevision.empty()`。
- 过期 revision 在任何写入前失败。

Dashboard 的 start/archive/restore 生命周期操作由同进程锁串行化；该锁不跨进程，`TaskState` 文件锁和 revision policy 仍是跨进程正确性机制。外部 start 成功后若 evidence 写入或 `executor_dispatched` CAS 失败，记录 unreconciled fact 并 fail closed，禁止自动覆盖另一方的 phase。

## Batch Failure

批量事件不承诺数据库式事务。所有事件预校验后逐条持久化；I/O 中断时错误携带确认提交前缀和未处理事件。无法确认最后一次写入时抛出 indeterminate 状态，调用方必须重新读取后再决策。

## Compatibility And Migration

- 历史 JSON 行不改写。
- `load_status()` 委托 `TaskState.current()`，保留原返回类型。
- `save_status()` 保留为兼容缓存 writer，但生产调用点清零。
- Merge Gate 与 Trust Anchor 继续使用 Ledger 的验证读取。
- 旧缓存仅用于检测一次性 baseline，不长期成为 projector seed。

## Testing Strategy

测试通过 `TaskState` interface 覆盖 current、record、revision、baseline、缓存故障、批量前缀、篡改和真实多进程并发。Ledger 保留自己的 hash-chain/file-lock 单元测试。迁移测试验证 Core、CLI、Dashboard 的可观察行为，而不 mock TaskState implementation 内部对象。

额外测试覆盖跨实例 baseline、new-event identity、旧 run checkpoint/terminal guard、dispatch CAS conflict、evidence write failure、重复 dispatch 阻止及后续 lifecycle phase 的 Dashboard fallback。

## Rollback

第一阶段是独立提交。若迁移验证失败，可整体回退该提交；旧事件与 Status 格式没有被重写。上线后已经写入的 `task_state_baselined` 是向后兼容的未知 no-op，旧版本仍可验证哈希链。

## Trade-offs

- 完整 replay 是 O(n)，优先换取权威性与简单错误模型。
- 通用 TaskEvent 减少 interface 面积，静态类型不足由内部 registry 弥补。
- prefix commit 诚实表达文件追加语义，调用方必须处理明确的中断结果。
- 暂不定义 persistence port；只有一个真实文件 adapter，不制造 hypothetical seam。

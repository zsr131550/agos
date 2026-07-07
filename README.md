# AGOS

> Executor-agnostic governance layer for AI coding agents. **Agent writes. AGOS verifies. CI enforces.**

AGOS 是一个面向 AI 编程代理的本地/CI 治理层。它不替代 Codex、Claude Code、Multica 或 OpenHands，而是在这些执行器之上提供任务记录、子代理编排、候选补丁、审查证据、信任锚和合并门禁。

适合你在以下场景使用：

- 让 Codex CLI、Claude Code、Multica、OpenHands 等不同执行器按同一套治理流程工作。
- 把一个大任务拆成多个 subagent 子任务，并限制每个子任务的写入范围。
- 将业务 workflow 映射成 gates、reviewers、arbiters 和 merge-gate。
- 在 CI 中证明：提交的补丁来自受控候选、测试/审查证据完整、ledger 未被篡改。
- 把任务执行、审查反馈、关闭证明沉淀为后续迭代和自我蒸馏的数据资产。

当前状态：`0.1.0`，Alpha。CI 覆盖 Python 3.11 / 3.12；项目声明支持 Python `>=3.11`。

此项目web控制台部分参考 https://github.com/zhang3xing1/Input-Kanban 感谢掌星大佬的开源


注：此项目全程为AI制作没有人工审核，仅供参考
---

## 目录

- [核心能力](#核心能力)
- [工作模型](#工作模型)
- [可视化控制台](#可视化控制台)
- [安装](#安装)
- [5 分钟上手：Codex CLI 本地治理闭环](#5-分钟上手codex-cli-本地治理闭环)
- [多代理流水线：按业务拆分 subagent](#多代理流水线按业务拆分-subagent)
- [自动规划：业务逻辑驱动 workflow](#自动规划业务逻辑驱动-workflow)
- [审查器：manual、fake、LLM CLI 的区别](#审查器manualfakellm-cli-的区别)
- [配置参考](#配置参考)
- [CI、信任锚与合并门禁](#ci信任锚与合并门禁)
- [本地验证与真实 Codex CLI 集成测试](#本地验证与真实-codex-cli-集成测试)
- [发布构建](#发布构建)
- [常见问题](#常见问题)

---

## 核心能力

| 能力 | 用途 |
| --- | --- |
| Task Ledger | 为每个 AGOS 任务维护 hash-chained ledger，记录开始、checkpoint、候选、审查、决策和 closeout。 |
| Workflows & Gates | 在 `.agos/agos.yaml` 中按业务 workflow 锁定测试、安全扫描、策略检查等 gates。 |
| Workers / Subagents | 将任务拆到 `local_worktree`、`codex_cli`、`claude_code`、`multica`、`openhands` 等 worker。 |
| Execution Plan | 用 YAML/JSON 描述子任务 DAG、依赖关系、写入范围和 worker 分配。 |
| Candidate Patches | 每个 subagent 在隔离 workspace 中产出候选补丁，AGOS 记录 patch hash 与 evidence。 |
| Review Orchestration | 支持 manual reviewer、dev fake reviewer、Codex/Claude LLM CLI reviewer。 |
| Trust Anchors | 将 ledger head 发布到 file 或 protected git ref，供 CI 做不可变验证。 |
| Merge Gate | 在 CI 中验证 ledger、trust anchor、candidate patch、test evidence、review evidence 与 PR diff binding。 |

---

## 工作模型

AGOS 把“让 AI 写代码”拆成可验证的流水线：

```mermaid
flowchart LR
  A["业务需求 / Task"] --> B["Workflow: gates + policy"]
  B --> C["Planner: 拆分 execution plan"]
  C --> D1["Subagent A / Worker"]
  C --> D2["Subagent B / Worker"]
  D1 --> E1["Candidate Patch + Evidence"]
  D2 --> E2["Candidate Patch + Evidence"]
  E1 --> F["Tests / Security Gates"]
  E2 --> F
  F --> G["Reviewers: manual 或 LLM CLI"]
  G --> H["Arbiter Decision"]
  H --> I["Apply / Merge Gate"]
  I --> J["Closeout Proof / Trust Anchor"]
```

核心映射关系：

- **业务划分** → `workflows`、`ExecutionPlan.subtasks[*].title/intent/write_scope`
- **subagent / skill tree** → `workers` + 每个 subtask 的 `worker.adapter` / `worker.role`
- **业务逻辑驱动 workflow** → `default_workflow`、`workflows.<name>.gates`、`orchestration.planner`
- **按节点调用 subagent** → `agos run start --plan ...` 或 `agos run auto ...`
- **自我蒸馏迭代升级** → ledger、review findings、candidate evidence、closeout proof 可作为下一轮 prompt、policy、skill 或 workflow 的输入

边界说明：AGOS 当前提供证据闭环和自动/半自动编排；它不会自动训练模型，也不会自动改写你的 Codex/Claude skill。自我蒸馏通常由你在 AGOS evidence 基础上接一个外部总结、提示词更新或 skill 更新流程完成。

---

## 可视化控制台

AGOS 提供本地 Dashboard，用浏览器提交新任务并展示当前 `.agos/` 治理状态：

```bash
agos dashboard --port 0 --open
```

默认行为：

- 绑定 `127.0.0.1`，不对外暴露。
- 支持从页面输入任务标题、意图、workflow 和 gate override，创建并启动新的 AGOS task。
- 独立小游戏、demo 或网页类产物默认要求执行器输出到 `outputs/<task-id>/`，Dashboard 的运行概览会展示该输出目录。
- 除“创建任务并启动”外，其余控制台区域仍以读取 `.agos/` 状态和 evidence 为主。
- 左侧展示当前 AGOS run，右侧展示 workflow、subagent 节点、candidate、review、merge-gate、ledger evidence 和自我蒸馏摘要。
- evidence viewer 只允许读取 `.agos/tasks/current` 中被允许的 task/evidence refs，拒绝路径穿越和任意文件读取。

常用命令：

```bash
agos dashboard
agos dashboard --host 127.0.0.1 --port 8788
agos dashboard --port 0 --no-open
```

如果页面提示尚未初始化，请先运行：

```bash
agos init
agos start --title "Your task"
```

---

## 安装

### 从发布 wheel 安装

从 GitHub Actions `release` workflow 下载 `agos-dist` artifact，然后安装：

```bash
pip install agos-0.1.0-py3-none-any.whl
agos version
agos --help
```

> 当前仓库尚未配置 PyPI 发布；发布产物以 GitHub Actions artifact 为准。

### 从源码开发安装

```bash
git clone https://github.com/zsr131550/agos.git
cd agos
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
agos --help
```

可选 LangGraph backend：

```bash
python -m pip install -e ".[dev,langgraph]"
```

### 外部工具要求

基础要求：

- Python `>=3.11`
- Git
- 至少一个本地 AI 执行器，常用：
  - Codex CLI：`codex --version`
  - Claude Code：`claude --version`
  - Multica：`multica daemon status`
  - OpenHands endpoint

---

## 5 分钟上手：Codex CLI 本地治理闭环

下面以 Codex CLI 为例。请在一个 Git 仓库根目录运行。

### 1. 初始化 AGOS

```bash
agos init --executor codex_cli --agent codex:codex
```

这会创建：

- `.agos/agos.yaml`：AGOS 配置
- `.agos/tasks/current/`：当前任务状态目录
- `.git/hooks/pre-commit` 和 `.git/hooks/pre-push`：本地 advisory gates

如果你不指定 `--agent`，`agos init` 会发现本地可用 agent 并进入交互式选择。

### 2. 检查环境

```bash
agos doctor
agos config validate
agos worker doctor
```

机器可读输出：

```bash
agos doctor --json
agos config show --json
agos worker doctor --json
```

### 3. 启动任务

```bash
agos start \
  --title "Add API usage examples" \
  --intent "Update README and tests for the public CLI examples" \
  --workflow feature
```

AGOS 会：

1. 写入 active task metadata。
2. 锁定 workflow gates。
3. 调用配置的 executor，例如 `codex exec --json ...`。
4. 把执行器输出写入 evidence。

### 4. checkpoint 与本地 gate

```bash
agos checkpoint --once
agos ci --local --stage pre-commit
```

`checkpoint` 会把执行器消息、当前 repo anchor 和 ledger head 记录下来。`ci --local` 是本地 advisory 检查；真正的强制合并控制应放到 CI 的 `agos merge-gate`。

### 5. 关闭任务

```bash
agos closeout
agos status
```

如果存在 blocking review finding 或 evidence 不完整，closeout 会失败并指出需要补齐的内容。

---

## 多代理流水线：按业务拆分 subagent

多代理路径推荐用于“大任务拆小任务”：每个 subagent 在隔离 Git worktree 中工作，产出 candidate patch，再由 AGOS 做测试、审查、决策和 apply。

### 1. 配置 workers

`.agos/agos.yaml` 示例：

```yaml
executor:
  name: codex_cli
  agent: codex
  command: codex

default_workflow: feature

workers:
  docs_agent:
    type: codex_cli
    command: codex
    timeout_seconds: 180
    artifact_globs:
      - .agos-worker/*.json

  test_agent:
    type: codex_cli
    command: codex
    timeout_seconds: 180

  local_patch_agent:
    type: local_worktree

workflows:
  feature:
    gates:
      - id: tests_pass
        stage: [pre-commit, pre-push, candidate]
        argv: [pytest, -q]
      - id: no_secrets_in_diff
        stage: [pre-commit, pre-push, candidate]
        type: secret_scan

orchestration:
  backend: native_async
  max_parallel: 2
  max_retries: 1
  worker_timeout_seconds: 900
  retry_backoff_seconds: 5
```

### 2. 创建 execution plan

`task_id` 必须匹配当前 active task。可用 `agos status --json` 查看。

`execution-plan.yaml`：

```yaml
id: plan-readme-release
# 替换为 agos status --json 中的 task id
task_id: agos-01
max_parallel: 2
requires_candidate_review: true
subtasks:
  - id: docs-usage
    title: Write publishable usage README
    intent: Explain install, quickstart, Codex CLI workflow, config and CI gate.
    write_scope:
      - README.md
      - docs
    worker:
      adapter: docs_agent
      role: docs_writer

  - id: tests-docs
    title: Verify public examples
    intent: Check documented commands and add/adjust tests when examples expose broken CLI behavior.
    depends_on:
      - docs-usage
    write_scope:
      - tests
      - docs
    worker:
      adapter: test_agent
      role: test_engineer
```

规则：

- `write_scope` 必填，且不能是 `.` 或绝对路径。
- 并行子任务的 `write_scope` 不能重叠；如果重叠，必须用 `depends_on` 串行化。
- `worker.adapter` 必须是 `.agos/agos.yaml` 中已配置的 worker 名称。

### 3. 启动并查看 run

```bash
agos run start --plan execution-plan.yaml --json
agos run status <run-id> --json
agos run resume <run-id> --json
```

兼容命令：

```bash
agos execute-plan run --plan execution-plan.yaml --json
agos run run --plan execution-plan.yaml --json
```

### 4. 提交、测试、审查、决策候选补丁

```bash
agos candidate list
agos candidate submit docs-usage --summary "README usage tutorial"
agos candidate test <candidate-id>
agos candidate review <candidate-id> --packet-only
```

如果你配置了自动 reviewer，也可以直接运行 candidate review 流程：

```bash
agos candidate review <candidate-id>
```

审查完成后：

```bash
agos candidate decide <candidate-id> \
  --decision accepted \
  --reason "Tests and review evidence are complete"

agos candidate apply <candidate-id>
```

AGOS 会在 apply 前校验 patch hash、write scope、测试 evidence、review binding 和 ledger 状态。

### 5. 多候选合并策略

当多个候选都被接受时，可使用 bundle 决策：

```bash
agos candidate merge decide
agos candidate merge preview <bundle-decision-id>
agos candidate merge apply <bundle-decision-id>
```

支持的策略：

| Strategy | 自动 apply | 含义 |
| --- | ---: | --- |
| `single_candidate` | 是 | 一个 accepted candidate，通过所有 guard。 |
| `non_overlapping_bundle` | 是 | 多个 accepted candidates，写入范围不重叠。 |
| `ordered_patch_stack` | 是 | 多个 accepted candidates 有明确顺序，临时 stack workspace dry-run 通过。 |
| `manual_merge_required` | 否 | dirty paths、冲突、证据缺失或顺序不明确，需要人工处理。 |

---

## 自动规划：业务逻辑驱动 workflow

AGOS 可以让 Codex/Claude 先作为 planner 生成 execution plan，再按节点调用 worker。开启方式：

```yaml
orchestration:
  backend: native_async
  max_parallel: 2
  max_retries: 1
  fallback_write_scope:
    - README.md
    - src/agos
    - tests
    - docs
  planner:
    enabled: true
    executor: codex_cli
    command: codex
    timeout_seconds: 120
```

运行 dry-run：

```bash
agos run auto --dry-run --json
```

确认 evidence 后自动 apply：

```bash
agos run auto --apply --json
```

如果当前没有配置 reviewer，但你在本地实验中希望允许通过：

```bash
agos run auto --dry-run --allow-missing-review --json
```

生产环境不建议依赖 `--allow-missing-review`。推荐至少配置一个 manual 或 LLM CLI reviewer。

规划失败时，AGOS 会使用保守 fallback plan：单个 subtask、首个可用 worker、`fallback_write_scope` 限定的写入范围。

---

## 审查器：manual、fake、LLM CLI 的区别

Reviewer 不等同于 worker。Worker 负责“改代码/产出 patch”，Reviewer 负责“审查 evidence/patch 并产出 findings”。因此 reviewer 不会默认走 worker adapter。

| Reviewer type | 是否调用 agent | 用途 |
| --- | --- | --- |
| `manual` | 否 | 创建 review packet，等待人或外部系统提交 findings。适合生产审批。 |
| `fake` | 否 | 测试/开发用 reviewer。需要 `allow_fake_reviewer: true`，不建议生产使用。 |
| `codex_cli` | 是 | 调用 Codex CLI 作为审查器，生成 normalized findings。 |
| `claude_code` | 是 | 调用 Claude Code 作为审查器，生成 normalized findings。 |

### 配置 Codex reviewer

```yaml
reviewers:
  security:
    type: codex_cli
    executor: codex_cli
    command: codex
    role: security_reviewer
    required: true
    timeout_seconds: 180
    blocking_severity: high
```

运行：

```bash
agos review run --reviewer security
```

### 配置 manual reviewer

```yaml
reviewers:
  security_manual:
    type: manual
    role: security_reviewer
    required: true
    blocking_severity: high
```

生成 packet：

```bash
agos review --packet-only
```

导入 normalized findings：

```bash
agos review --ingest findings.json --review-id <review-id>
```

`findings.json` 结构示例：

```json
{
  "findings": [
    {
      "id": "finding-001",
      "review_id": "review-abc",
      "source_agent": "security_manual",
      "category": "security",
      "severity": "high",
      "blocking": true,
      "title": "Unsafe shell command construction",
      "body": "User-controlled input reaches a shell command without argv separation.",
      "location": {"file": "src/example.py", "line": 42},
      "evidence_refs": ["reviews/review-abc/packet.json"],
      "suggested_fix": "Use structured argv and avoid shell=True."
    }
  ]
}
```

解决 finding：

```bash
agos resolve finding-001 \
  --status resolved \
  --evidence reviews/review-abc/report.json \
  --rationale "Replaced shell command with structured argv"
```

---

## 配置参考

`.agos/agos.yaml` 是 AGOS 的主配置。常用字段如下。

```yaml
executor:
  name: codex_cli        # multica | codex_cli | claude_code
  agent: codex
  command: codex

default_workflow: feature

workers:
  codex:
    type: codex_cli      # local_worktree | codex_cli | claude_code | multica | openhands
    command: codex
    timeout_seconds: 120
    poll_interval_seconds: 2
    artifact_globs:
      - .agos-worker/*.json
    env: {}
    health_probe: false

reviewers:
  security:
    type: codex_cli      # manual | fake | codex_cli | claude_code
    executor: codex_cli
    command: codex
    role: security_reviewer
    required: true
    timeout_seconds: 120
    blocking_severity: high

workflows:
  feature:
    gates:
      - id: tests_pass
        stage: [pre-commit, pre-push, candidate]
        argv: [pytest, -q]
      - id: no_secrets_in_diff
        stage: [pre-commit, pre-push, candidate]
        type: secret_scan

  docs_only:
    gates: []

orchestration:
  backend: native_async   # native_async | external | langgraph
  max_parallel: 2
  max_retries: 1
  worker_timeout_seconds: 900
  retry_backoff_seconds: 5
  max_tick_iterations: 20
  fallback_write_scope:
    - README.md
    - src/agos
    - tests
    - docs
  planner:
    enabled: false
    executor: codex_cli
    command: codex
    timeout_seconds: 60

trust_anchor:
  backend: git-ref        # file | git-ref
  path: null
  auto_publish_on_checkpoint: false
  issuer: agos
```

### Gate 类型

AGOS 支持三类 gate：

```yaml
# 1. shell-style command，兼容旧配置
- id: tests_pass
  stage: [pre-commit]
  command: "pytest -q"

# 2. structured argv，跨平台推荐
- id: tests_pass
  stage: [pre-commit, candidate]
  argv: [pytest, -q]

# 3. built-in / external typed gate
- id: semgrep_security
  stage: [pre-push, candidate]
  type: semgrep
  options:
    config: p/security-audit
```

内置 typed gate：`secret_scan`、`opa`、`semgrep`、`trufflehog`、`codeql`。详见 [`docs/security-gates.md`](docs/security-gates.md)。

### Orchestration backend

| Backend | 说明 |
| --- | --- |
| `native_async` | 默认语义参考实现，本地执行 worker DAG。 |
| `external` | 将 normalized run spec 发送到远端 orchestrator。 |
| `langgraph` | 安装 `.[langgraph]` 后，可将同一 DAG 编译到 LangGraph。 |

External backend endpoint 需要实现：

- `POST /runs`
- `GET /runs/{run_id}`
- `POST /runs/{run_id}/cancel`
- `GET /runs/{run_id}/artifacts`

---

## CI、信任锚与合并门禁

本地 Git hooks 是 advisory，开发者可以用 `--no-verify` 绕过。生产强制点应是 CI：

```bash
agos prepare-merge-gate \
  --base "$BASE_SHA" \
  --head "$HEAD_SHA" \
  --anchor-path ".agos/tasks/current/evidence/anchors.json" \
  --issuer "github-actions"

agos merge-gate \
  --require-anchor \
  --anchor-backend file \
  --anchor-path ".agos/tasks/current/evidence/anchors.json" \
  --allow-missing-review \
  --base "$BASE_SHA" \
  --head "$HEAD_SHA" \
  --json
```

`merge-gate` 会验证：

- active task ledger hash chain
- `gates_locked` 与当前 workflow gates 是否一致
- trust anchor 是否匹配 ledger head
- candidate patch hash 是否匹配
- candidate test evidence 是否完整
- candidate review evidence 是否完整且未过期
- PR submitted diff 是否绑定到被审查/测试的 candidate

GitHub Actions 中，本仓库提供两个 PR jobs：

1. `agos-prepare`：在 PR head checkout 上生成 `.agos/tasks/current` 和 file trust anchor artifact。
2. `merge-gate`：下载 artifact 并运行 `agos merge-gate`。

要真正阻止不合规 PR 合并，还需要在 GitHub branch protection 中要求 `merge-gate` status check。

---

## 本地验证与真实 Codex CLI 集成测试

常规验证：

```bash
python -m ruff check src tests
python -m compileall -q src tests
python -m pytest -q
python -m pytest --cov=agos --cov-report=term-missing -q
python -m build
```

真实 Codex CLI opt-in 集成测试默认跳过，需要显式打开环境变量。

Bash / zsh：

```bash
AGOS_CODEX_WORKER_SMOKE=1 AGOS_CODEX_BIN=codex \
  python -m pytest tests/integration/test_worker_adapters_opt_in.py::test_codex_worker_smoke -q

AGOS_PLANNER_SMOKE=1 AGOS_PLANNER_BIN=codex \
  python -m pytest tests/integration/test_planner_cli_opt_in.py::test_planner_cli_produces_plan_json -q

AGOS_REVIEWER_SMOKE=1 AGOS_REVIEWER_BIN=codex \
  python -m pytest tests/integration/test_reviewer_cli_opt_in.py::test_llm_cli_reviewer_runs_real_cli -q
```

Windows PowerShell：

```powershell
$env:AGOS_CODEX_WORKER_SMOKE='1'; $env:AGOS_CODEX_BIN='codex.cmd'
python -m pytest tests/integration/test_worker_adapters_opt_in.py::test_codex_worker_smoke -q
Remove-Item Env:AGOS_CODEX_WORKER_SMOKE,Env:AGOS_CODEX_BIN -ErrorAction SilentlyContinue

$env:AGOS_PLANNER_SMOKE='1'; $env:AGOS_PLANNER_BIN='codex.cmd'
python -m pytest tests/integration/test_planner_cli_opt_in.py::test_planner_cli_produces_plan_json -q
Remove-Item Env:AGOS_PLANNER_SMOKE,Env:AGOS_PLANNER_BIN -ErrorAction SilentlyContinue

$env:AGOS_REVIEWER_SMOKE='1'; $env:AGOS_REVIEWER_BIN='codex.cmd'
python -m pytest tests/integration/test_reviewer_cli_opt_in.py::test_llm_cli_reviewer_runs_real_cli -q
Remove-Item Env:AGOS_REVIEWER_SMOKE,Env:AGOS_REVIEWER_BIN -ErrorAction SilentlyContinue
```

全量本地 integration suite，并启用 Codex 相关真实 CLI：

```bash
AGOS_CODEX_WORKER_SMOKE=1 AGOS_CODEX_BIN=codex \
AGOS_PLANNER_SMOKE=1 AGOS_PLANNER_BIN=codex \
AGOS_REVIEWER_SMOKE=1 AGOS_REVIEWER_BIN=codex \
python -m pytest tests/integration -q
```

Multica/OpenHands 的真实 smoke 会创建真实外部任务或调用真实服务，默认跳过。仅在你确认环境和成本后开启对应环境变量。

---

## 发布构建

构建 release artifacts：

```bash
python -m build
```

安装并 smoke-test wheel：

```bash
python -m venv .venv-release
. .venv-release/bin/activate
pip install --upgrade pip
pip install --force-reinstall dist/agos-0.1.0-py3-none-any.whl
agos version
agos --help
agos run --help
agos merge-gate --help
```

Windows PowerShell 激活虚拟环境：

```powershell
python -m venv .venv-release
.\.venv-release\Scripts\Activate.ps1
pip install --upgrade pip
pip install --force-reinstall dist\agos-0.1.0-py3-none-any.whl
agos version
```

完整发布流程见 [`docs/release-install.md`](docs/release-install.md)。

---

## 常见问题

### AGOS 会直接替我 merge AI 写的代码吗？

不会盲目 merge。AGOS 的设计是：agent 产出 candidate patch，AGOS 验证 patch hash、write scope、gates、review evidence，再按决策 apply。CI 的 `merge-gate` 才是强制合并边界。

### reviewer 为什么不能像 worker 一样“调用 agent”？

可以，但只有 LLM CLI reviewer 会调用 agent。`manual` 是人工/外部系统审查入口，`fake` 是测试替身；它们不应该调用 worker。要让 reviewer 调 Codex，请配置：

```yaml
reviewers:
  security:
    type: codex_cli
    executor: codex_cli
    command: codex
    role: security_reviewer
```

### `agos init` 找不到 agent 怎么办？

先确认本地 CLI 可用：

```bash
codex --version
claude --version
multica agent list --output json
```

然后显式指定：

```bash
agos init --executor codex_cli --agent codex:codex
```

### 没有 reviewer 能不能跑自动流程？

本地实验可以：

```bash
agos run auto --dry-run --allow-missing-review --json
```

生产环境不推荐。应配置 manual 或 LLM CLI reviewer，并在 merge gate 中要求 review evidence。

### AGOS 的信任边界是什么？

- `.agos/tasks/current/ledger.jsonl` 是 tamper-evident，不是不可变存储。
- trust anchor 把 ledger head 发布到 ledger 外部，用于 CI 验证。
- file anchor 适合本地和 GitHub artifact；protected git ref 或可信 CI publisher 更适合生产。
- AGOS 不能替你配置 GitHub branch protection；你必须单独要求 `merge-gate` status check。

---

## License

MIT. See [`pyproject.toml`](pyproject.toml).

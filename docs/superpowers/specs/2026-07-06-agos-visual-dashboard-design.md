# AGOS 可视化控制台设计

**状态：** 用户已确认产品方向，等待 spec review 后进入实施计划
**日期：** 2026-07-06
**作者：** AGOS project

## 目标

参考 Input-Kanban 的本地可视化模式，为 AGOS 增加一个中文本地控制台：用户可以在浏览器中看到任务批次、业务 workflow、subagent 节点、candidate、review、merge-gate、ledger evidence 和自我蒸馏沉淀状态。

核心产品目标是把 AGOS 当前的命令行治理能力变成一条可观察的 agent 流水线：

```text
业务任务 → workflow / skill tree → execution plan → subagent 节点 → candidate / evidence
       → reviewer / arbiter → merge-gate → closeout lessons → 自我蒸馏升级建议
```

第一版以真实读取和可解释展示为主，不把危险写操作藏在界面按钮后面。后续在同一架构上增加显式确认的受保护动作。

## 背景

AGOS 已经具备以下 CLI 和核心状态能力：

- `.agos/` 存放配置、任务 ledger、execution plan、candidate、review 和 evidence。
- `agos run` / `agos candidate` / `agos review` / `agos merge-gate` 等命令承载治理流程。
- worker 可以映射到 `local_worktree`、Codex CLI、Claude Code、Multica、OpenHands 等执行器。
- reviewer 可以是 manual、fake/dev reviewer 或 LLM CLI reviewer。
- closeout、ledger 和 review 证据可以作为后续自我蒸馏的数据资产。

Input-Kanban 的可借鉴点是：本地 HTTP server、静态单页前端、JSON API、轮询状态、无需复杂前端构建链。AGOS 应沿用这种轻量形态，但把内容模型替换成 AGOS 的 workflow / subagent / governance 状态。

## 产品边界

本 spec 的实施范围是“可视化控制台 MVP + 受保护动作接口预留”：

- 提供可运行的本地 Dashboard 命令。
- 真实读取当前仓库 `.agos/` 状态。
- 使用中文 UI 展示 AGOS 流水线。
- 暴露只读 JSON API。
- 预留 action API 和按钮布局，但第一版不执行会修改仓库的动作。

不在第一版实现：

- 不做远端托管服务。
- 不做多人协作权限系统。
- 不在浏览器里直接编辑任意项目文件。
- 不自动修改 skill、workflow 或 agent 配置。
- 不替代现有 CLI；Dashboard 是 CLI 的可视化 companion。

## 推荐主布局

采用 **Input Kanban 式左右分栏**：

- 左侧是 `Runs` / 任务批次列表。
- 右侧是当前 run 的完整详情。
- 详情区从上到下展示：
  1. run 标题、workflow、当前状态、关键操作入口；
  2. Plan / Workers / Candidates / Review / Gate 的阶段摘要；
  3. subagent 节点表；
  4. candidate、review、merge-gate 和 ledger 状态；
  5. evidence 文件查看器；
  6. 自我蒸馏沉淀面板。

这个布局比 DAG-first 更适合 AGOS 当前文件状态模型：用户先选择任务，再查看该任务的治理链路、证据和动作。DAG 视图可以作为详情区内的子视图在后续迭代加入。

## 架构

新增一个本地 web surface，保持 AGOS 仍是本地治理层：

```text
Browser
  └─ static dashboard HTML/CSS/JS
       └─ fetch /api/*
            └─ AGOS local HTTP server
                 ├─ RepoLocator / ConfigLoader
                 ├─ StatusService
                 ├─ ExecutionStore
                 ├─ Candidate / Review readers
                 ├─ Ledger verifier
                 └─ Evidence resolver
```

建议新增模块：

- `src/agos/web/server.py`：本地 HTTP server、路由和静态资源服务。
- `src/agos/web/api.py`：把 AGOS core 状态转换成 Dashboard DTO。
- `src/agos/web/evidence.py`：安全解析 `.agos` evidence ref。
- `src/agos/web/static/index.html`：单文件中文前端，无前端构建步骤。
- `src/agos/cli/cmd_dashboard.py`：`agos dashboard` CLI 命令。

`src/agos/cli/main.py` 注册 `dashboard` 命令。默认绑定 `127.0.0.1`，默认端口使用 `8788`，支持 `--port 0` 自动选择可用端口。

## CLI UX

新增命令：

```bash
agos dashboard
agos dashboard --host 127.0.0.1 --port 8788
agos dashboard --port 0 --open
agos dashboard --no-open
```

行为：

- 在当前 git 仓库或指定工作目录中定位 AGOS repo。
- 启动本地 HTTP server。
- 输出 Dashboard URL。
- `--open` 时尝试打开系统浏览器。
- 没有初始化 `.agos/` 时仍打开页面，但显示初始化提示和可执行 CLI 建议。

## API 设计

第一版只读 API：

- `GET /api/health`
  - 返回 server 版本、AGOS repo 是否可用、当前 repo root。
- `GET /api/config`
  - 返回脱敏后的 workflows、workers、reviewers、merge-gate 配置摘要。
- `GET /api/status`
  - 返回当前 active task、ledger head、dirty state、gate 摘要。
- `GET /api/runs`
  - 返回可展示 run 列表；优先从 AGOS task state 和 ledger 派生。
- `GET /api/runs/current`
  - 返回当前 active run 的聚合视图。
- `GET /api/runs/current/ledger`
  - 返回 ledger 记录摘要和 chain verify 结果。
- `GET /api/runs/current/execution`
  - 返回 execution plan、节点、依赖、worker 分配和节点状态。
- `GET /api/runs/current/candidates`
  - 返回 candidate 列表、patch hash、测试证据和 apply 状态。
- `GET /api/runs/current/reviews`
  - 返回 manual/fake/LLM CLI review 证据摘要和 arbiter decision。
- `GET /api/runs/current/evidence?ref=<evidence-ref>`
  - 返回 `.agos` 范围内允许读取的 evidence 内容。

响应统一使用 JSON。异常统一返回：

```json
{
  "ok": false,
  "error": {
    "code": "agos_not_initialized",
    "message": "当前仓库尚未初始化 AGOS",
    "hint": "运行 agos init"
  }
}
```

## UI 组件

### Runs 左栏

展示任务列表、状态、更新时间和关键计数。状态颜色：

- `running`：蓝色
- `completed`：绿色
- `blocked` / `failed`：红色
- `queued` / `idle`：灰色

### 当前 Run Header

展示：

- 任务标题和 ID；
- workflow 名称；
- 当前 ledger head；
- 当前 git commit；
- worker / candidate / review / gate 计数；
- 刷新按钮和后续动作入口；第一版禁用会修改仓库的写操作。

### Pipeline Summary

五个阶段卡片：

1. Plan
2. Workers
3. Candidates
4. Review
5. Merge Gate

每个卡片显示状态、关键证据和失败原因。用户能一眼看出流水线卡在哪个阶段。

### Subagent 节点表

按 execution plan 节点展示：

- node id；
- 业务角色或 skill tree 分组；
- worker 类型；
- 依赖节点；
- write scope；
- 状态；
- 输出 candidate / evidence ref；
- 失败信息。

这张表是“按节点调用 subagent”的主视图。DAG 子视图后续可以从同一份 DTO 渲染。

### Evidence 文件查看器

右下方提供 tab 式 evidence viewer：

- `ledger.jsonl`
- `execution-plan.yaml` / `execution-plan.json`
- candidate metadata
- test output
- review report
- merge-gate result
- closeout notes

读取路径必须经过 evidence resolver，不允许前端传任意文件路径。

### 自我蒸馏面板

第一版只展示沉淀，不自动改配置：

- closeout lessons；
- repeated failure patterns；
- reviewer feedback 摘要；
- candidate decision 原因；
- 可转化为 workflow template / skill hint 的建议。

后续迭代可以增加“生成升级建议”动作，但需要明确用户确认并写入独立 evidence。

## 数据流

1. 用户运行 `agos dashboard`。
2. server 定位 repo root 和 `.agos/`。
3. 前端加载 `/api/health`、`/api/status` 和 `/api/runs`。
4. 用户选择 run 后，前端轮询当前 run 聚合 API。
5. API 层调用 AGOS core reader，组装只读 DTO。
6. 用户点 evidence tab 时，前端传 evidence ref，server 验证 ref 后读取内容。
7. 前端每 2-5 秒轮询；正在运行的 run 使用更短间隔，静态 run 使用较长间隔。

## 错误处理

Dashboard 应把 AGOS CLI 场景中的失败转成可操作提示：

- 未初始化：提示 `agos init`。
- 没有 active task：提示 `agos start` 或选择历史 run。
- execution plan 不存在：显示 “尚未生成计划”。
- worker 配置不存在：显示缺失 worker 名称和配置文件位置。
- candidate evidence 缺失：显示缺失 ref 和 ledger 记录。
- ledger chain verify 失败：红色高危提示，并建议运行 CLI verify。
- review stale：展示 candidate hash 与 review binding mismatch。

API 层失败默认不导致页面崩溃；UI 保留上一次成功状态并显示错误条。

## 安全原则

- 默认只绑定 `127.0.0.1`。
- `--host 0.0.0.0` 需要用户显式传入。
- 第一版 API 只读。
- evidence resolver 只允许读取 `.agos` 管理范围内的文件或由 AGOS ledger 明确索引的 evidence ref。
- 不返回环境变量、系统 credential、SSH key、浏览器状态或用户主目录任意文件。
- config API 必须脱敏 token、secret、key、password 等字段。
- 后续 action API 必须：
  - 使用 `POST`；
  - 默认 dry-run；
  - 要求 UI 二次确认；
  - 把动作结果写入 ledger 或 evidence。

## 测试策略

新增测试覆盖：

- CLI：
  - `agos dashboard --port 0 --no-open` 能启动并返回 URL。
  - 未初始化仓库能返回结构化错误而不是崩溃。
- API：
  - `/api/health`、`/api/status`、`/api/runs` 在临时 AGOS repo 中返回稳定 JSON。
  - config 脱敏逻辑覆盖 token/secret/password/key 字段。
  - evidence resolver 拒绝路径穿越和 `.agos` 外部任意路径。
- UI 静态资源：
  - `index.html` 随 wheel 打包。
  - 无前端构建步骤。
- 集成：
  - 创建临时 repo、初始化 AGOS、写入最小 ledger/execution/candidate/review fixture，启动 server 后通过 HTTP 读取聚合状态。

发布前验证命令应包含：

```bash
python -m compileall src tests
python -m pytest
python -m build
```

## 实施顺序

1. 增加 web package 和静态资源打包配置。
2. 增加只读 DTO/API 层。
3. 增加 evidence resolver 和安全测试。
4. 增加 dashboard CLI 命令。
5. 增加中文单页 UI。
6. 增加本地 HTTP 集成测试。
7. 更新 README 使用教程。

## 后续迭代

本设计为后续能力保留接口，但不在第一版直接实现：

- DAG 图视图。
- 受保护动作按钮：
  - generate plan；
  - run auto dry-run；
  - candidate test/review/decide/apply；
  - merge-gate verify。
- 自我蒸馏升级：
  - 从 closeout lessons 生成 workflow template 建议；
  - 从 repeated review feedback 生成 skill hint；
  - 从失败模式生成 planner guardrail；
  - 所有建议先作为 evidence 展示，由用户确认后再落盘。

## 成功标准

实现完成后，用户能在本地执行：

```bash
agos dashboard --port 0 --open
```

并在浏览器中完成以下只读检查：

- 看见当前 AGOS task / run；
- 看见 workflow 和 execution plan；
- 看见每个 subagent 节点的 worker、状态、依赖和输出；
- 看见 candidate、review、merge-gate 摘要；
- 打开 ledger 和 evidence 内容；
- 看见 closeout/self-distillation 沉淀摘要；
- API 拒绝 `.agos` 外部任意文件读取。

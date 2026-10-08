# sugarFreeHarnessAndEvals

一个小型、可观察、可重复的 Agent Harness 与评测框架。项目名称就是当前工作区名称。

当前阶段优先验证 Harness 架构，而不是堆叠复杂 Agent 能力：

- 与模型厂商无关的模型和工具接口；
- 有明确终止原因和最大步数的 Agent Loop；
- 结构化、追加式执行事件；
- 不需要联网的确定性假模型；
- 对答案、工具调用轨迹和运行状态进行评分；
- JSONL 轨迹和机器可读的评测报告。

项目原理、四项核心亮点和面试问答见 [HarnessAndEval 项目理解与面试讲解指南](docs/harness-eval-interview-guide.md)。

## 环境要求

- Python 3.11 或更高版本；
- 运行离线测试和架构评测不需要 API Key；
- 运行真实 DeepSeek 评测需要 DeepSeek API Key。

## 运行离线架构评测

离线评测使用 `ScriptedModel`，模型响应预先写在评测用例中，因此不需要 API Key，也不会访问网络：

```powershell
python scripts/run_eval.py evals/architecture-smoke.json
```

当前包含 10 个架构用例，覆盖直接回答、工具调用、多步调用、工具错误恢复、最大步数限制以及工作区文件新增和修改。结果写入 `runs/`，每个用例都会生成一份 JSONL 轨迹，同时生成 `report.json` 汇总报告。

## 运行分层回归评测集

完整离线回归集位于 [`evals/agent-regression.json`](evals/agent-regression.json)：

```powershell
python scripts/run_eval.py evals/agent-regression.json `
  --output-dir runs/agent-regression
```

当前包含 23 个确定性用例，按以下标签组织：

- `core`、`answer`、`efficiency`：直接回答和不必要工具调用；
- `tool`、`multi_step`、`parallel`、`schema`：工具选择、参数、串并行轨迹；
- `recovery`、`negative`、`model`、`boundary`：错误恢复、模型异常和执行边界；
- `workspace`、`security`：文件产物、无副作用检查和路径穿越防护。

用例支持 `required_events` 和 `forbidden_events`，可断言 `tool.failed`、`workspace.changed`、`run.failed` 等关键事件是否出现。`report.json` 的 `tag_summary` 会输出每个标签的用例数、通过数和通过率，为后续发布门禁按能力维度设置阈值提供依据。

## 发布门禁

离线发布策略位于 [`gates/offline-release.json`](gates/offline-release.json)，基线位于 [`evals/baselines/agent-regression.json`](evals/baselines/agent-regression.json)。执行命令：

```powershell
python scripts/run_release_gate.py `
  --config gates/offline-release.json `
  --current runs/agent-regression/report.json `
  --replay runs/agent-regression-replay/report.json `
  --baseline evals/baselines/agent-regression.json `
  --output runs/release-gate-report.json
```

门禁检查整体通过率、最小用例数、关键标签通过率、核心 case、严格回放逐 case 一致性、基线用例删除、新增失败以及平均步骤和工具调用退化。任一检查失败都会生成可诊断的 `gate-report.json` 并返回非零退出码，从而阻断 CI。基线是精简、可版本化的预期状态，不包含完整消息、轨迹或不稳定的耗时数据；只有确认评测集或预期行为发生合理变化后才应更新。

## 负面案例回流闭环

失败用例不会直接写入正式评测集，而是先进入待审核 Inbox，经过人工确认后再关联到手工编写的 Regression case。这样既能沉淀真实问题，又能避免把未审核的模型输出、敏感信息或偶发噪声直接固化为测试标准。

从一个或多个失败报告收集候选案例：

```powershell
python scripts/manage_failures.py collect `
  runs/deepseek-smoke-1/report.json `
  runs/deepseek-smoke-2/report.json `
  --inbox runs/failure-inbox.json
```

工具会按照“评测套件、原始 case、失败评分器、终止原因”生成稳定指纹。同一报告重复收集不会重复计数，不同运行出现相同失败时会合并并增加 `occurrence_count`。写入 Inbox 前会脱敏 `sk-...`、`Bearer ...`、`api_key`、`token`、`secret` 和 `password` 等常见凭据。

审核确认问题：

```powershell
python scripts/manage_failures.py review `
  --inbox runs/failure-inbox.json `
  --fingerprint <候选指纹> `
  --status approved `
  --reviewer "reviewer-name" `
  --note "已确认，可转为回归用例"
```

随后在 [`evals/agent-regression.json`](evals/agent-regression.json) 中手工新增确定性回归用例，并将已批准问题与该 case 关联：

```powershell
python scripts/manage_failures.py promote `
  --inbox runs/failure-inbox.json `
  --fingerprint <候选指纹> `
  --case-id <新增的回归用例ID> `
  --reviewer "reviewer-name"
```

回归评测通过后执行对账：

```powershell
python scripts/manage_failures.py reconcile `
  --inbox runs/failure-inbox.json `
  --report runs/agent-regression/report.json
```

只有关联的 Regression case 在报告中通过，问题才会从 `promoted` 自动变为 `resolved`。完整状态流转为 `candidate → approved/rejected → promoted → resolved`；已拒绝的候选也可以重新审核为 `approved`。Inbox 保留首次和最近发现时间、出现次数、失败原因、来源报告与轨迹路径，便于复现、审计和统计问题闭环率。

## 配置 API Key

真实模型评测使用环境变量读取 API Key。不要把 Key 写入 `deepseek-smoke.json`、Python 源码、README、评测报告或 JSONL 轨迹。

### Windows PowerShell（当前终端有效）

在运行评测前执行：

```powershell
$env:DEEPSEEK_API_KEY = "sk-你的密钥"
```

然后在同一个 PowerShell 窗口运行：

```powershell
python scripts/run_live_eval.py evals/deepseek-smoke.json
```

关闭该终端后，这个配置通常就失效。若要永久写入 Windows 用户环境变量，可以使用：

```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "sk-你的密钥", "User")
```

执行后请重新打开终端。永久配置会保存在操作系统用户环境中，仍然不要提交到 Git。

### macOS、Linux 或 WSL

```bash
export DEEPSEEK_API_KEY="sk-你的密钥"
python scripts/run_live_eval.py evals/deepseek-smoke.json
```

代码当前只读取进程环境变量，不会自动读取 `.env` 文件。`.env` 虽然被 Git 忽略，但直接把 Key 写入其中不会自动生效。

## 运行真实模型评测

真实评测用例位于 [evals/deepseek-smoke.json](evals/deepseek-smoke.json)，不包含 `model_script`，由 DeepSeek 模型自主决定是否调用工具：

```powershell
$env:DEEPSEEK_API_KEY = "sk-你的密钥"
python scripts/run_live_eval.py evals/deepseek-smoke.json
```

可选配置：

```powershell
$env:DEEPSEEK_MODEL = "deepseek-chat"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_TEMPERATURE = "0"
$env:DEEPSEEK_TIMEOUT_SECONDS = "60"
```

也可以通过命令行参数覆盖配置：

```powershell
python scripts/run_live_eval.py evals/deepseek-smoke.json `
  --model deepseek-chat `
  --temperature 0 `
  --timeout-seconds 60 `
  --output-dir runs/deepseek-smoke
```

报告会记录模型、provider、温度、超时时间、评测文件 SHA-256、Python 版本和运行指标，并明确记录 `api_key_recorded: false`。API Key 不会被写入报告或轨迹。

## 回放已有轨迹

真实评测成功后，可以使用已有 JSONL 轨迹离线重跑，不再调用 API：

```powershell
python scripts/run_replay.py evals/deepseek-smoke.json `
  --trajectories runs/deepseek-smoke/trajectories `
  --output-dir runs/deepseek-smoke-replay
```

回放默认校验每一步模型请求是否与原始轨迹一致。请求结构发生变化时，回放会失败，帮助发现 Harness 改动带来的行为差异。`ReplayModel` 只读取轨迹中的模型响应，工具仍会真实执行。

## 运行测试

```powershell
python scripts/run_tests.py
```

当前测试覆盖 Agent Loop、工具注册和参数校验、错误恢复、事件观察器故障、评测用例校验、发布门禁、负面案例去重脱敏与状态流转，以及 live runner 配置检查。

## 持续集成

本地可以使用与 GitHub Actions 相同的入口运行完整离线检查：

```powershell
python scripts/run_ci_checks.py
```

该命令依次执行源码编译检查、全部单元与集成测试、10 个架构 Smoke、23 个分层 Regression、两套严格轨迹回放、两份基准对比和离线发布门禁。产物默认写入 `runs/ci/`。

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) 会在 push、pull request 和手动触发时，分别使用 Python 3.11 与 3.13 执行同一流程。评测失败会使 CI 失败；报告、JSONL 轨迹、case workspace 和 benchmark 无论成功或失败都会作为 Actions artifact 上传，便于定位回归。

## 生成基准报告并比较多次运行

基准命令只读取已有的 `report.json`，不会重新调用模型。报告路径按“从旧到新”的顺序传入：

```powershell
python scripts/run_benchmark.py `
  runs/architecture-smoke/report.json `
  runs/architecture-smoke-replay/report.json `
  --output runs/architecture-benchmark.json
```

输出会比较通过率、平均步骤、平均工具调用、平均 token、平均耗时，并列出新增失败和恢复通过的用例。多次真实模型运行时，可以把各次运行的 `report.json` 依次传入同一命令。

### DeepSeek 三轮稳定性验证

2026 年 9 月 28 日使用 `deepseek-chat`、`temperature=0` 连续运行三轮 `deepseek-smoke`，三轮均通过 `10/10` 用例：

| 轮次 | 通过率 | 平均步骤 | 平均工具调用 | 平均 Token | 平均耗时 |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | 100% | 1.80 | 0.90 | 780.10 | 1.540 秒 |
| 2 | 100% | 1.80 | 0.90 | 782.30 | 1.449 秒 |
| 3 | 100% | 1.80 | 0.90 | 783.90 | 1.499 秒 |

三轮运行的通过率、步骤数和工具调用数保持一致，说明当前 Agent Loop 在直接回答、单工具调用、多工具串联、工具报错恢复和非必要工具抑制等场景下具有稳定行为。Token 与耗时存在小幅正常波动。对应汇总文件为 `runs/deepseek-validation-current-benchmark.json`。

## 插件与 Provider

下一阶段的扩展入口是 `sugarfree.plugins.PluginManager`。插件通过 `PluginContext` 注册模型服务、工具和其他服务，并声明自己的依赖；加载时按依赖顺序执行 `setup`，关闭时按逆序执行 `teardown`。插件拥有的工具和服务会在卸载时自动清理。

插件可以替换模型而不修改 Agent Loop：

```python
manager = PluginManager([model_plugin, tools_plugin])
context = await manager.load()
agent = context.create_agent()
result = await agent.run("your task")
await manager.close()
```

当前插件机制是最小生命周期实现，尚未包含动态热加载、配置文件 Profile 或沙箱策略。

## 工具权限与审批

Agent 可以在执行工具前挂载策略：

```python
agent = Agent(
    model=model,
    tools=tools,
    tool_policy=AllowlistPolicy({"calculator"}),
)
```

`AllowlistPolicy` 会拒绝不在列表中的工具。`ApprovalPolicy` 会在指定工具执行前调用 `approval_handler`；没有处理器或处理器拒绝时，工具不会执行，并记录 `tool.approval_requested` 与 `tool.denied` 事件。启用 `stop_on_tool_error` 时，策略拒绝会以 `tool_denied` 结束本轮。

这层是权限决策，不等同于进程沙箱。当前项目仍不建议向不可信任务开放 Bash、文件写入或宿主机凭据；真正的受限执行环境将在后续单独实现。

## 受限子进程执行

`LocalRestrictedProcessRunner` 为高风险命令工具提供统一执行边界：只接受参数数组、不经过 shell、要求绝对可执行文件路径、使用可执行文件 allowlist、限制工作目录、默认不继承宿主环境变量，并支持超时和输出截断。

```python
runner = LocalRestrictedProcessRunner(
    root=workspace,
    allowed_executables={sys.executable},
)
tool = RunCommandTool(runner, str(workspace))
```

`run_command` 没有加入默认工具集，真实模型默认无法调用宿主机命令。这个本地 Runner 只是策略受限的进程执行器，不是操作系统级安全沙箱；允许 Python、Node 等通用解释器就等同于允许其访问当前用户可访问的资源。不要用它运行不可信代码。

### Docker Provider

`DockerProcessRunner` 实现了同一个 `ProcessRunner` 接口，并采用以下默认边界：

- 镜像必须使用 SHA-256 digest 固定；
- 禁用容器网络；
- 容器根文件系统只读；
- 工作区默认只读挂载；
- 丢弃全部 Linux capabilities；
- 启用 `no-new-privileges`；
- 以 `65534:65534` 非 root 用户运行；
- 限制 PID、内存和 CPU；
- 只开放带 `noexec`、`nosuid` 的临时 `/tmp`；
- 默认拒绝向容器注入环境变量；
- 超时或取消时强制删除容器。

启动 Docker daemon 后，可以使用固定 digest 的镜像验证：

```powershell
docker pull python:3.13-alpine
$image = docker image inspect python:3.13-alpine --format '{{index .RepoDigests 0}}'
python scripts/run_docker_smoke.py --image $image --workspace .
```

成功时输出 `docker sandbox ready`。Docker Provider 也没有加入默认工具集，必须由应用显式实例化并注册。容器隔离显著强于本地 Runner，但仍依赖 Docker daemon、镜像内容、宿主挂载配置及 Docker 自身安全边界，不能当作绝对隔离保证。

## 工作区文件能力

`WorkspaceFileStore` 是文件系统能力的基础 seam，所有路径都相对于固定 workspace root 解析：

- 拒绝 `..` 穿越、绝对路径和 symlink 逃逸；
- 读取和写入都限制 UTF-8 字节数；
- 读取拒绝不存在、目录和非 UTF-8 文件；
- 写入先写同目录临时文件，再用原子替换提交；
- `ReadFileTool` 与 `WriteFileTool` 彼此独立。

```python
store = WorkspaceFileStore(workspace, max_read_bytes=256 * 1024)
read_file = ReadFileTool(store)
write_file = WriteFileTool(store)
```

文件工具没有加入默认工具集。若要暴露给 Agent，应显式注册，并建议给 `write_file` 配置 `ApprovalPolicy`；当前仍不会自动开放文件修改能力给真实模型。

### 记录工作区变化

把同一个 `WorkspaceFileStore` 显式传给 Agent 后，Harness 会在运行前后生成受限快照，并在文件发生变化时记录 `workspace.changed` 事件：

```python
store = WorkspaceFileStore(workspace)
agent = Agent(
    model=model,
    tools=ToolRegistry([ReadFileTool(store), WriteFileTool(store)]),
    workspace=store,
)
result = await agent.run("更新 result.txt")
```

事件包含新增、修改和删除文件的相对路径、修改前后大小、SHA-256，以及 UTF-8 文本的受限 unified diff。二进制文件只记录大小与 hash；`.git`、`node_modules`、`__pycache__`、隐藏路径和 symlink 不进入快照。可通过 `workspace_max_files`、`workspace_max_total_bytes` 和 `workspace_max_diff_lines` 调整上限。快照失败会产生 `workspace.snapshot_failed`，但不会破坏 Agent 主流程。

这项能力只负责观测显式指定的工作区，不会自动注册文件工具，也不会给模型增加文件访问权限。

### 评测工作区结果

评测用例可以声明初始文件和预期变化。runner 会为每个 case 创建独立目录 `workspaces/<case-id>`；只有显式声明 `workspace` 的用例才会获得 `read_file`、`write_file` 工具：

```json
{
  "id": "update_note",
  "prompt": "把 note.txt 更新为 new value",
  "workspace": {
    "files": {"note.txt": "old value\n"}
  },
  "expect": {
    "workspace_changes": [
      {
        "path": "note.txt",
        "status": "modified",
        "content": "new value\n"
      }
    ]
  }
}
```

`status` 支持 `added`、`modified`、`deleted`。提供 `content` 时使用 UTF-8 内容的 SHA-256 精确校验；删除文件不能声明最终内容。评分默认要求变化文件集合完全一致，出现漏改或额外修改都会失败。显式配置空数组 `"workspace_changes": []` 可断言工作区不应发生变化。

## 超时、取消与异常边界

`AgentConfig` 支持分别设置模型和工具超时：

```python
AgentConfig(
    model_timeout_seconds=60,
    tool_timeout_seconds=10,
)
```

模型超时会以 `model_timeout` 结束，工具超时会记录 `tool.timeout` 事件；启用 `stop_on_tool_error` 时，工具超时会以 `tool_timeout` 结束。外部取消会继续传播为 `CancelledError`，不会被误判为普通业务错误。live runner 可通过 `--model-timeout-seconds` 和 `--tool-timeout-seconds` 配置。

## 安装命令行入口

```powershell
python -m pip install -e .
sugarfree-eval evals/architecture-smoke.json
```

## 代码边界

`sugarfree.core` 负责 Agent 状态机和与厂商无关的数据类型。模型适配器和工具实现这些接口，评测层只消费 `AgentResult` 与结构化事件，Agent 核心不依赖评测层。

当前提供的 `OpenAICompatibleModel` 支持非流式 Chat Completions 接口，可以连接 DeepSeek 兼容端点。`ScriptedModel` 用于离线、稳定、可重复的架构测试。

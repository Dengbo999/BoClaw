# dotClaw

dotClaw 是一个用于学习和实践 AI Agent 工程的轻量级 Agent Harness。它把 ReAct 循环、OpenAI 兼容模型调用、多模型路由、工具调度、上下文压缩、记忆系统、MCP 接入、Skill 注入和运行观测拆成相对独立的模块，便于逐步理解和扩展。

当前项目更偏向「可读、可改、可观察」的个人助手框架，而不是封装成黑盒 SDK。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| ReAct 主循环 | `AgentLoop` 负责 LLM 调用、工具调用、观察结果回填和最终回复。 |
| 多模型路由 | `LLMProxy` + `ModelRouter` 支持 Qwen、DeepSeek、OpenAI 兼容接口，按 purpose 和 priority 选择模型。 |
| 工具系统 | `ToolHandler`、`ToolRegistry`、`ToolExecutor` 分层，内置文件、命令、记忆、系统信息、时间、网页搜索等工具。 |
| 工具安全边界 | 文件工具限制在 workspace 内，`exec` 固定在 workspace 执行，敏感命令会被拒绝，审批记录写入审计日志。 |
| 上下文压缩 | 超过 `max_context_tokens` 时，保留系统/工具/技能、当前输入和最近消息，较早消息由 LLM 摘要进 `session.summary`。 |
| 三级记忆 | L1 Session 历史、L2 日记忆、L3 Deep Dream 长期记忆蒸馏。 |
| Skill 系统 | 扫描 `SKILL.md`，把技能说明按需注入 prompt。 |
| MCP 集成 | 支持 stdio 和 streamable HTTP MCP server，自动发现并注册 tools/resources/prompts。 |
| Journal 观测 | 记录 prompt、LLM、tool、memory、loop 等运行事件，输出 trace、report、snapshot。 |

## 环境要求

- Python 3.13+
- 建议使用虚拟环境
- 需要至少配置一个 OpenAI 兼容模型供应商的 API Key

## 快速开始

### 1. 安装依赖

```bash
pip install -e ".[dev]"
```

### 2. 配置模型密钥

推荐用 `.env` 文件（已被 `.gitignore` 忽略，不会提交）。复制模板并填入真实 Key：

```bash
cp .env.example .env
# 编辑 .env，填入 QWEN_API_KEY / DEEPSEEK_API_KEY 等
```

启动时会自动加载项目根目录的 `.env` 到环境变量，无需手动 export。

也可以直接设置系统/会话环境变量：

```powershell
# PowerShell
$env:QWEN_API_KEY="your-api-key"
```

```bash
# Bash
export QWEN_API_KEY="your-api-key"
```

配置文件中用 `${ENV_VAR}` 占位（如 `api_key: ${QWEN_API_KEY}`），运行时自动展开为对应环境变量值。已存在的系统环境变量优先级高于 `.env`。不要把真实 API Key 提交到仓库。

### 3. 启动 CLI

```bash
python -m dotclaw
```

也可以使用安装后的命令：

```bash
dotclaw
```

## 常用 CLI 命令

| 命令 | 说明 |
| --- | --- |
| `/new [标题]` | 新建对话并切换过去。 |
| `/list` | 列出本地保存的所有会话。 |
| `/switch <id>` | 切换到指定会话。 |
| `/delete <id>` | 删除指定会话。 |
| `/dream` | 手动触发长期记忆蒸馏。 |
| `/tools` | 查看已注册工具，按内置工具和 MCP 工具分组。 |
| `/mcp` | 查看 MCP server 连接状态。 |
| `/skills` | 查看已加载 Skill。 |
| `/model [名称]` | 不带参数查看当前模型，带参数切换模型。 |
| `/help` | 查看帮助。 |
| `/quit` | 退出 CLI。 |

## 配置说明

核心配置文件：

- `config.yaml`：全局 Agent、工具、记忆、MCP、日志等配置。
- `model_router_config.yaml`：模型供应商、模型列表、用途路由和降级优先级。
- `.dotclaw/agentConfig/<agent_id>.yaml`：单个 Agent 的角色、workspace、工具白名单等覆盖配置。

### config.yaml 示例

```yaml
llm:
  default_model: qwen-plus
  stream: true

agent:
  max_context_tokens: 8000
  keep_recent_messages: 10
  rules: ""

tools:
  builtin_enabled: true
  mcp_enabled: true
  approval_commands:
    - exec
    - write_file
  web_search:
    enabled: true

skills:
  enabled: true
  directory: ./skills
  skip_prefix: "_"

memory:
  workspace: ./data
  db_path: ./data/memory/memory.db
  max_results: 5
  min_score: 0.1
  sync_on_search: true

session:
  directory: ./data/sessions
```

### model_router_config.yaml 示例

```yaml
defaults:
  provider: qwen
  model: qwen-plus
  fallback_enabled: true
  parameters:
    temperature: 0.7
    max_tokens: 4096

providers:
  qwen:
    api_key: ${QWEN_API_KEY}
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    rate_limit:
      requests_per_minute: 60
    retry:
      max_attempts: 3
      backoff_factor: 2.0

models:
  qwen-plus:
    provider: qwen
    model_id: qwen-plus
    context_window: 32000
    capabilities:
      - chat
      - function_calling
    status: active

purposes:
  chat:
    description: 日常对话
    priority:
      - model: qwen-plus
        priority: 1
```

## 内置工具

| 工具 | 说明 |
| --- | --- |
| `read_file` | 读取 workspace 内文件。 |
| `write_file` | 写入 workspace 内文件，通常建议加入审批列表。 |
| `list_dir` | 列出 workspace 内目录。 |
| `exec` | 在 workspace 中执行 shell 命令，需要审批，且会拦截敏感命令。 |
| `web_search` | 使用 DuckDuckGo Lite 搜索网页标题和链接，由 `tools.web_search.enabled` 单独控制。 |
| `memory_read` | 查询记忆。 |
| `memory_write` | 写入记忆。 |
| `system_info` | 查看系统信息。 |
| `get_time` | 获取当前时间。 |

`web_search` 单独开关的原因是它会访问外网，和本地文件/命令工具的风险边界不同。默认可以关闭，需要联网搜索时再显式开启。

## 工具安全模型

dotClaw 当前对工具调用做了几层约束：

1. `ToolExecutionContext.workspace` 由 Agent 注入，默认指向项目根目录。
2. `read_file`、`write_file`、`list_dir` 会解析真实路径，并拒绝 workspace 外路径。
3. `exec` 的 `cwd` 固定为 workspace，会拒绝明显指向 workspace 外的路径参数。
4. `exec` 会拦截高风险命令，例如删除、格式化、系统关机、权限修改、危险 Git 操作等。
5. 需要审批的工具会通过 channel 请求确认；审批结果会写入 `data/security/approvals.jsonl`。
6. 审批日志会对 `api_key`、`token`、`password`、`secret`、`authorization` 等字段脱敏。

上述护栏逻辑统一收拢在 `src/dotclaw/tools/security/` 子包中：`path_sandbox`（workspace 边界）、`command_rules`（命令黑名单与越界检查）、`approval`（审批与审计脱敏）。

注意：这些限制是项目内部的安全护栏，不应该被当作系统级沙箱。真正执行不可信命令前，仍然需要依赖操作系统、容器或独立沙箱隔离。

## 会话、上下文与压缩

会话保存在 `data/sessions/*.json`，会话 ID 只允许 8 位十六进制字符串，避免通过 session_id 做路径穿越。

每轮请求构造上下文时，当前策略是：

1. 固定保留 system prompt、工具说明、Skill 注入内容。
2. 固定保留当前用户输入。
3. 最近 `agent.keep_recent_messages` 条历史消息原样保留。
4. 更早的消息由 LLM 摘要合并到 `session.summary`。
5. `session.summary_message_count` 记录已经被摘要过的历史消息位置，避免重复摘要同一段历史。
6. 如果摘要失败，会降级到硬裁剪，保证对话不中断。

只有估算 token 数超过 `agent.max_context_tokens` 时才会触发压缩。

## 记忆系统

dotClaw 的记忆分三层：

| 层级 | 位置 | 作用 |
| --- | --- | --- |
| L1 Session | `data/sessions/` | 保存当前会话原始消息和摘要游标。 |
| L2 日记忆 | `data/memory/YYYY-MM-DD.md` | 每轮对话结束后，由 LLM 决定 append、modify 或 skip。 |
| L3 长期记忆 | `data/memory/MEMORY.md` | `/dream` 把日记忆蒸馏为长期偏好、决策、知识和待办。 |

检索使用混合策略：

- 有 embedding provider 时使用向量检索。
- 始终可使用 SQLite FTS5 关键词检索。
- 向量分和关键词分按 `vector_weight`、`keyword_weight` 加权合并。
- 日记忆结果会按 `temporal_decay_half_life_days` 做时间衰减。

Agent 每轮构建 `AgentContext` 时会用当前用户输入触发记忆检索，并把命中结果注入 prompt。

## MCP 接入

`tools.mcp_servers` 支持两类 transport：

```yaml
tools:
  mcp_enabled: true
  mcp_global:
    startup_timeout: 4
    tool_timeout: 60
    restart_on_crash: true
    max_restart_attempts: 3
  mcp_servers:
    - name: demo_stdio
      transport: stdio
      command: python
      args:
        - path/to/server.py

    - name: demo_http
      transport: streamable_http
      url: http://127.0.0.1:8000/mcp
      headers: {}
```

MCP server 启动后，dotClaw 会发现并注册：

- MCP tools：按原工具名注册。
- MCP resources：注册为 `read_<server>_<resource>`。
- MCP prompts：注册为 `prompt_<server>_<prompt>`。

dotClaw 不会自动帮你下载 MCP server；需要先安装对应 server，再在配置里写明启动命令。

## Skill 系统

默认扫描 `skills/` 目录下的技能。目录名以 `skip_prefix` 开头时会跳过，默认跳过 `_` 开头目录。

典型结构：

```text
skills/
  example/
    SKILL.md
    references/
    scripts/
```

Skill 的职责是把专门领域的规则、流程、参考资料和脚本组织起来，让 Agent 在需要时能把相关说明注入 prompt。

## 项目结构

```text
dotClaw/
  src/dotclaw/
    agent/          Agent、AgentLoop、AgentContext、PromptBuilder、ContextCompressor
    llm/            OpenAI 兼容客户端、LLMProxy、ModelRouter
    tools/          ToolHandler、Registry、Executor、security 安全模块、内置工具
    memory/         Session、日记忆、长期记忆、检索和同步
    journal/        trace、report、snapshot 等观测输出
    skills/         Skill 扫描、注册和 prompt 注入
    mcp/            MCP client、provider、tool adapter
    channel/        CLI 通道
    config/         YAML 配置加载和 dataclass 映射
    common/         通用工具、限流、单例等
  tests/            单元测试和阶段验收测试
  docs/             架构设计、阶段记录和开发文档
  skills/           本地 Skill 目录
  data/             运行时数据，通常不应提交
  config.yaml
  model_router_config.yaml
```

## 测试

常用验证命令：

```bash
python -m py_compile src/dotclaw/agent/compressor.py src/dotclaw/tools/builtin/web_search_tool.py
python -m unittest tests.test_context_compressor tests.test_tool_workspace_security tests.test_session_security tests.test_web_search_tool -v
python tests/test_phase2_acceptance.py
python tests/test_phase3_acceptance.py
python tests/test_phase4_acceptance.py
```

补充说明：

- `pytest` 是 dev 依赖；如果当前环境没有安装，可以先运行上面的 `unittest` 和脚本式验收测试。
- 早期 Phase 1 和部分 `tests/metrics/*` 仍保留历史接口假设，后续需要迁移到当前 Agent 和 Journal 架构。

## 开发注意事项

- 源码、配置和文档统一使用 UTF-8，无 BOM。
- 不要提交真实 API Key、token、密码或本地私有路径。
- `data/`、`.dotclaw/state.json`、会话文件和审计日志属于运行时数据，应谨慎提交。
- 修改工具、记忆、上下文压缩或模型路由时，优先补充对应单元测试。
- 对已有中文注释和业务说明做最小必要修改，避免把编码修复和业务改动混在一起。

## License

MIT。见 [LICENSE](LICENSE)。

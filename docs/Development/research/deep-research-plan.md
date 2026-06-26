# Deep Research 功能开发计划

## 背景

当前 dotClaw 已具备普通 ReAct 对话、`web_search`、工具调度、记忆和 Journal 观测能力。深度研究功能需要比普通对话更长的执行链路：规划问题、搜索资料、读取网页、提取证据、综合多个来源并生成报告。

深度研究不建议直接塞进普通 `AgentLoop`。它应该作为独立工作流存在，通过 `deep_research` 工具或后续 `/research` CLI 命令触发，研究过程落盘，最终报告可追溯。

## 总体目标

用户可以提出一个研究问题，系统自动完成：

1. 生成研究计划和搜索查询。
2. 搜索并抓取若干网页来源。
3. 从网页中提取与问题相关的证据。
4. 综合证据生成 Markdown 报告。
5. 将研究过程保存到 `data/research/<research_id>/`。

## 阶段一：最小可用闭环

目标：打通“问题 -> 搜索 -> 抓网页 -> 生成报告 -> 保存”的主链路。

交付内容：

- 新增 `web_fetch` 工具：读取网页标题和正文片段。
- 新增 `ResearchStorage`：保存 `task.json`、`sources.json`、`report.md`。
- 新增 `ResearchManager`：生成搜索 query，调用搜索/抓取函数，调用 LLM 生成报告。
- 新增 `deep_research` 工具：以工具形式对外暴露深度研究能力。
- 工厂中注入 `ResearchManager` 并注册 `deep_research`。

约束：

- 默认限制搜索查询数、来源数、抓取字节数，避免无限研究。
- 先使用现有 `web_search` 的 DuckDuckGo Lite 结果。
- 第一阶段不做复杂可信度评分，只保留来源标题、URL、正文片段和引用编号。
- 失败时返回明确错误，并保存已完成的中间结果。

## 阶段二：结构化研究状态

目标：把研究任务做成可查询、可恢复的状态机。

详细设计见：[phase2-structured-state-design.md](phase2-structured-state-design.md)。

新增状态：

- `planning`
- `searching`
- `reading`
- `extracting`
- `synthesizing`
- `done`
- `failed`

交付内容：

- `ResearchTask`、`ResearchSource`、`ResearchNote` 数据模型。
- `research_status` 查询能力。
- `task.json` 记录每一步状态、错误和耗时。
- 支持失败后从已有来源继续合成报告。

## 阶段三：后台任务化与任务管理

目标：把当前阻塞式 `deep_research` 升级为可后台运行、可查询、可取消、可恢复的研究任务。

详细设计见：[phase3-background-task-design.md](phase3-background-task-design.md)。

交付内容：

- 新增 `research_start`：创建研究任务并立即返回 `id`。
- 保留 `deep_research`：继续作为同步阻塞兼容入口。
- 新增后台任务 runner，负责调度正在运行的研究任务。
- 新增 `research_list`：列出最近研究任务。
- 新增 `research_cancel`：取消运行中的研究任务。
- 优化 `reading/extracting` 阶段落盘粒度，减少中断后重复工作。
- `research_status` 增加 `is_running`、`can_resume`、`progress` 等字段。
- 工具描述明确区分“同步研究”和“后台研究”，避免模型误报。

## 阶段四：质量增强

目标：提升报告可信度和可用性。

详细设计见：[phase4-quality-enhancement-design.md](phase4-quality-enhancement-design.md)。

增强点：

- 来源去重和域名聚合。
- 来源可信度启发式评分。
- 同一关键事实尽量要求多个来源支持。
- 报告中明确标记“不确定”“来源不足”“需要进一步验证”。
- 支持引用粒度从网页级提升到摘录级。
- 支持继续研究：`research_continue <id> <follow_up>`。

## 阶段四点五：同步继续研究

目标：允许用户基于已有研究任务继续追问，不重新创建完整研究。

交付内容：

- 新增 `research_continue(id, follow_up, depth)` 工具。
- `ResearchTask` 记录 `follow_ups` 历史。
- `ResearchSource` / `ResearchNote` 记录 `batch`，区分 initial 与 follow-up。
- 继续研究时复用已有 sources/notes，并基于 canonical URL 跳过重复来源。
- 新增来源和证据追加到原任务目录，重新合成报告。
- 第一版为同步执行，不做后台 continue。

## 阶段五：深度接入 Agent 体系

目标：让深度研究成为 dotClaw 的一等能力。

接入点：

- 增加 `/research`、`/research_status`、`/research_continue` CLI 命令。
- `model_router_config.yaml` 增加 `research` purpose。
- Journal 增加 research 阶段事件。
- Memory 只保存最终稳定结论，不保存全部网页正文。
- 配置文件增加研究预算：

```yaml
research:
  max_search_queries: 5
  max_sources: 10
  max_fetch_bytes: 200000
  max_iterations: 3
```

## 非目标

- 第一阶段不实现无限自主浏览。
- 第一阶段不实现网页 JavaScript 渲染。
- 第一阶段不保证所有网页都能抓取，失败来源会被跳过。
- 第一阶段不引入 LLM-as-Judge 评估报告质量。

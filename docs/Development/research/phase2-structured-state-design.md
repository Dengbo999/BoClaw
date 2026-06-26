# Deep Research Phase 2：结构化研究状态设计

## 目标

Phase 1 已经打通最小闭环：

```text
query -> search -> fetch -> synthesize -> data/research/<id>/report.md
```

Phase 2 的目标是把一次性 `ResearchManager.run()` 升级为**可查询、可恢复、可审计的研究状态机**。

核心交付：

- 明确 `ResearchTask` / `ResearchSource` / `ResearchNote` 数据模型。
- 让 `task.json` 记录阶段状态、耗时、错误和输出文件。
- 新增 `research_status` 查询能力。
- 新增失败后的恢复能力：从已有来源继续提取或合成报告。

## 非目标

Phase 2 不做以下内容：

- 不做复杂来源可信度评分。
- 不做事实多源交叉验证。
- 不做 `/research` CLI 命令。
- 不做 Journal research 事件。
- 不做长期记忆自动写入。

这些属于 Phase 3/4。

## 当前问题

Phase 1 的 `ResearchManager.run()` 目前是线性执行：

```python
search_queries = _build_search_queries(...)
sources = await _collect_sources(...)
report = await _synthesize(...)
```

主要不足：

1. `status` 只粗略记录 `searching`、`synthesizing`、`done`、`failed`。
2. `task.json` 不记录每个阶段的开始/结束时间和耗时。
3. `sources.json` 只有网页级摘录，没有结构化证据 note。
4. 中途失败后，只能看到 `task.error`，不能清楚知道失败在哪一步。
5. 已抓取来源不能被显式复用来继续合成报告。

## 状态机设计

### 状态枚举

```text
created       任务已创建，尚未执行
planning      生成研究计划和搜索查询
searching     搜索候选来源
reading       抓取网页正文
extracting    从来源中提取结构化证据
synthesizing  合成最终报告
done          成功完成
failed        执行失败
cancelled     预留，后续取消机制使用
```

Phase 2 先实现 `created/planning/searching/reading/extracting/synthesizing/done/failed`，`cancelled` 只保留枚举。

### 状态流转

```text
created
  -> planning
  -> searching
  -> reading
  -> extracting
  -> synthesizing
  -> done

任意执行态 -> failed
```

恢复流程：

```text
failed
  -> extracting      已有 sources，但没有 notes
  -> synthesizing    已有 notes 或已有可用 sources
  -> done
```

### 阶段记录

`task.json` 中增加 `stages` 字段：

```json
{
  "stages": {
    "planning": {
      "status": "done",
      "started_at": "2026-06-26T10:00:00Z",
      "ended_at": "2026-06-26T10:00:01Z",
      "duration_ms": 1000,
      "error": null
    },
    "searching": {
      "status": "failed",
      "started_at": "2026-06-26T10:00:01Z",
      "ended_at": "2026-06-26T10:00:05Z",
      "duration_ms": 4000,
      "error": "network timeout"
    }
  }
}
```

阶段状态取值：

```text
pending
running
done
failed
skipped
```

## 数据模型

Phase 2 新增 `src/dotclaw/research/models.py`。

### ResearchTask

```python
@dataclass
class ResearchTask:
    id: str
    query: str
    depth: str
    status: ResearchStatus
    created_at: str
    updated_at: str
    limits: ResearchLimits
    search_queries: list[str] = field(default_factory=list)
    source_count: int = 0
    note_count: int = 0
    report_path: str | None = None
    error: str | None = None
    stages: dict[str, ResearchStage] = field(default_factory=dict)
```

设计要点：

- `status` 表示任务总体状态。
- `stages` 表示每个阶段的执行状态和耗时。
- `search_queries` 在 planning 阶段写入。
- `source_count`、`note_count` 在阶段结束后更新。
- `error` 保存最后一次失败的摘要，详细错误在对应 stage 里。

### ResearchSource

```python
@dataclass
class ResearchSource:
    index: int
    url: str
    title: str = ""
    search_query: str = ""
    content_type: str = ""
    fetched_at: str | None = None
    status: str = "pending"
    excerpt: str = ""
    error: str | None = None
    truncated: bool = False
```

`status` 取值：

```text
pending
fetched
failed
skipped
```

### ResearchNote

```python
@dataclass
class ResearchNote:
    id: str
    source_index: int
    source_url: str
    claim: str
    evidence: str
    relevance: str = ""
    created_at: str = ""
```

Phase 2 的 note 先做“证据摘录”，不做可信度评分。

生成方式：

- 优先用 LLM 从每个 source excerpt 中提取 1-3 条 note。
- 如果 LLM 不可用，降级为每个可用 source 生成一条摘要 note。

## 文件布局

保持 Phase 1 的目录结构，并新增 `notes.json`：

```text
data/research/<research_id>/
  task.json
  sources.json
  notes.json
  report.md
```

### task.json

任务状态、阶段耗时、限制和产物路径。

### sources.json

搜索和抓取结果。失败来源也保留，便于诊断。

### notes.json

从来源中提取的结构化证据。

### report.md

最终研究报告。

## Storage API

`ResearchStorage` 需要从“只写”扩展为“读写”：

```python
class ResearchStorage:
    def create_task(self, task: ResearchTask) -> Path: ...
    def load_task(self, research_id: str) -> ResearchTask | None: ...
    def save_task(self, research_id: str, task: ResearchTask | dict) -> Path: ...

    def load_sources(self, research_id: str) -> list[ResearchSource]: ...
    def save_sources(self, research_id: str, sources: list[ResearchSource | dict]) -> Path: ...

    def load_notes(self, research_id: str) -> list[ResearchNote]: ...
    def save_notes(self, research_id: str, notes: list[ResearchNote | dict]) -> Path: ...

    def load_report(self, research_id: str) -> str | None: ...
    def save_report(self, research_id: str, report: str) -> Path: ...

    def list_tasks(self, limit: int = 20) -> list[ResearchTask]: ...
```

兼容策略：

- 读取旧版 Phase 1 `task.json` 时，缺失字段使用默认值补齐。
- 旧版没有 `notes.json` 时返回空列表。
- `save_*` 继续支持 dict，避免一次性大规模改动。

## Manager API

`ResearchManager` 从单个 `run()` 拆成阶段方法：

```python
class ResearchManager:
    async def run(self, query: str, depth: str = "standard") -> dict: ...
    async def status(self, research_id: str) -> dict: ...
    async def resume(self, research_id: str, from_stage: str | None = None) -> dict: ...

    async def plan(self, task: ResearchTask) -> ResearchTask: ...
    async def search(self, task: ResearchTask) -> list[ResearchSource]: ...
    async def read(self, task: ResearchTask, sources: list[ResearchSource]) -> list[ResearchSource]: ...
    async def extract(self, task: ResearchTask, sources: list[ResearchSource]) -> list[ResearchNote]: ...
    async def synthesize(
        self,
        task: ResearchTask,
        sources: list[ResearchSource],
        notes: list[ResearchNote],
    ) -> str: ...
```

### run()

`run()` 仍作为对外主入口，内部按阶段调用：

```text
create task
plan
search
read
extract
synthesize
mark done
```

### status()

返回结构化状态：

```json
{
  "id": "ab12cd34",
  "query": "...",
  "status": "reading",
  "created_at": "...",
  "updated_at": "...",
  "source_count": 3,
  "note_count": 0,
  "report_path": null,
  "error": null,
  "stages": {...}
}
```

### resume()

恢复策略：

1. 如果 `task.status == done`，直接返回当前报告路径和摘要。
2. 如果存在 `notes.json` 且非空，从 `synthesizing` 继续。
3. 如果存在可用 `sources.json`，从 `extracting` 继续。
4. 否则从 `searching` 继续。

`from_stage` 可手动指定，但只能是：

```text
searching
reading
extracting
synthesizing
```

## 工具接口设计

Phase 2 有两个选择：

### 方案 A：新增独立工具

```text
deep_research
research_status
research_resume
```

优点：

- 工具职责清晰。
- JSON schema 简单。
- 模型更容易正确调用。

缺点：

- 工具数量增加。

### 方案 B：一个工具多 action

```json
{
  "action": "run|status|resume",
  "query": "...",
  "id": "..."
}
```

优点：

- 工具数量少。

缺点：

- action 参数更容易被模型漏填。
- 当前 `deep_research` 已是单意图工具，改成多 action 有兼容风险。

### 推荐方案

采用方案 A：

- 保留 `deep_research(query, depth)`。
- 新增 `research_status(id)`。
- 新增 `research_resume(id, from_stage?)`。

后续 Phase 4 再考虑 CLI 命令：

```text
/research_status <id>
/research_resume <id>
```

## 错误处理设计

### 阶段失败

每个阶段都用统一包装：

```python
async def _run_stage(task, stage_name, fn):
    mark stage running
    try:
        result = await fn()
        mark stage done
        return result
    except Exception as e:
        mark stage failed
        task.status = "failed"
        task.error = str(e)
        save task
        raise
```

### 来源抓取失败

单个 URL 抓取失败不让整个任务失败：

- `ResearchSource.status = "failed"`
- `ResearchSource.error = "..."`
- 继续抓取下一个来源

只有在所有来源都不可用，且无法生成 fallback report 时，任务才 failed。

### LLM 提取 note 失败

降级为基于 source excerpt 的简单 note：

```text
claim: "来源提供了与问题相关的信息"
evidence: excerpt 前 500 字
```

### LLM 合成报告失败

降级为 fallback report，列出 sources 和 notes。

## 兼容 Phase 1

Phase 2 不破坏 Phase 1 的对外行为：

- `deep_research` 仍返回 `id/status/query/source_count/report_path/report`。
- `data/research/<id>/report.md` 路径不变。
- `sources.json` 继续是列表。
- `task.json` 继续保留旧字段。

新增字段只增不删。

## 实施步骤

### Step 1：新增 models.py

新增：

- `ResearchStatus`
- `StageStatus`
- `SourceStatus`
- `ResearchStage`
- `ResearchLimits`
- `ResearchTask`
- `ResearchSource`
- `ResearchNote`

提供：

- `to_dict()`
- `from_dict()`
- 对旧数据的默认补齐

### Step 2：扩展 ResearchStorage

新增读接口：

- `load_task`
- `load_sources`
- `load_notes`
- `load_report`
- `list_tasks`

新增 `save_notes`。

### Step 3：重构 ResearchManager

把当前 `run()` 拆成阶段方法。

要求：

- 每个阶段都写 task 状态。
- 每个阶段失败都能落盘。
- `resume()` 能从已有 `sources/notes` 继续。

### Step 4：新增工具

新增：

- `research_status_tool.py`
- `research_resume_tool.py`

或者在 `research_tool.py` 中增加 handler factory：

- `get_research_status_handler(manager)`
- `get_research_resume_handler(manager)`

推荐第二种，减少文件数量。

### Step 5：测试

新增或扩展：

- `tests/test_research_models.py`
- `tests/test_research_storage.py`
- `tests/test_research_manager_phase2.py`
- `tests/test_research_tools.py`

## 验收标准

### 功能验收

- `deep_research` 完成后生成：
  - `task.json`
  - `sources.json`
  - `notes.json`
  - `report.md`
- `task.json.status == done`
- `task.json.stages` 至少包含：
  - `planning`
  - `searching`
  - `reading`
  - `extracting`
  - `synthesizing`
- `research_status(id)` 能返回当前任务状态。
- 人为制造 synthesize 失败后，`research_resume(id)` 可以复用已有 sources/notes 继续。

### 兼容验收

- 旧版 Phase 1 的 `task.json` 可被 `load_task()` 读取。
- 没有 `notes.json` 时 `load_notes()` 返回空列表。
- 原有 `deep_research` 返回字段不变。

### 测试命令

```bash
python -m py_compile src/dotclaw/research/models.py src/dotclaw/research/storage.py src/dotclaw/research/manager.py src/dotclaw/tools/builtin/research_tool.py
python -m unittest tests.test_research_manager tests.test_research_manager_phase2 tests.test_research_storage tests.test_research_tools -v
```

## 风险与取舍

### 风险 1：状态机过度设计

控制方式：Phase 2 只做单任务状态，不引入后台队列和并发任务调度。

### 风险 2：恢复逻辑复杂

控制方式：只支持从已有 sources/notes 继续，不支持任意阶段完全回滚。

### 风险 3：LLM 提取 notes 成本增加

控制方式：默认每个 source 只提取 1-3 条 note，并继续使用 `max_sources` 限制。

### 风险 4：工具数量增加

控制方式：只新增两个明确工具：`research_status` 和 `research_resume`，不引入更多 action 工具。


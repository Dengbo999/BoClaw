# Deep Research Phase 3：后台任务化与任务管理设计

## 目标

Phase 2 已经完成结构化状态机和基础恢复能力，但 `deep_research` 仍是同步阻塞式工具：

```python
result = await manager.run(query=query, depth=depth)
```

这会导致一次研究占住当前对话轮次，模型也容易误把同步工具说成“后台运行”。Phase 3 的目标是把深度研究升级为真正的后台任务：

- 用户可以启动研究后立即拿到 `research_id`。
- 研究在当前进程后台执行。
- 用户可以查询任务状态、查看最近任务、取消运行中任务。
- 进程中断后仍可用 Phase 2 的落盘产物恢复。
- 工具描述和返回值明确区分同步执行与后台执行。

## 非目标

Phase 3 不做以下内容：

- 不引入外部队列系统，如 Redis、Celery。
- 不做跨进程自动续跑。
- 不做复杂来源可信度评分。
- 不做事实交叉验证。
- 不做 CLI 命令。
- 不做 Journal research 事件。

这些放到 Phase 4/5。

## 当前问题

### 1. deep_research 名义上像任务，实际上是阻塞调用

当前工具调用会等到完整研究结束才返回。如果研究超过工具 timeout，会导致用户既拿不到报告，也不一定拿到 `id`。

### 2. 用户无法列出已有研究任务

Phase 2 已有 `ResearchStorage.list_tasks()`，但没有工具暴露。用户如果忘记 ID，只能去 `data/research/` 目录手动找。

### 3. 中断恢复粒度仍偏粗

`reading` 阶段现在会在整批 sources 抓完后统一保存。进程如果在中途退出，已抓到但还未保存的 source 会丢失。

### 4. 无取消机制

Phase 2 预留了 `cancelled` 状态，但没有实际取消运行中的任务。

## 核心设计

### 工具拆分

Phase 3 保留已有工具，并新增后台入口：

```text
deep_research      同步阻塞执行，兼容旧行为
research_start     后台启动研究，立即返回 id
research_status    查询任务状态
research_list      列出最近研究任务
research_resume    恢复失败或中断任务
research_cancel    取消当前进程中运行的任务
```

命名取舍：

- 不把 `deep_research` 改成后台行为，避免破坏 Phase 1/2 已有兼容语义。
- 新增 `research_start` 表达“启动后台任务”，模型更不容易误解。
- `research_resume` 继续只处理已落盘任务，不负责后台调度。

## 数据模型扩展

Phase 2 的 `ResearchTask` 保持主体不变，Phase 3 增加少量运行时相关字段：

```python
@dataclass
class ResearchTask:
    ...
    started_at: str | None = None
    ended_at: str | None = None
    progress: dict[str, Any] = field(default_factory=dict)
```

`progress` 建议结构：

```json
{
  "current_stage": "reading",
  "total_sources": 6,
  "processed_sources": 2,
  "total_notes": 3,
  "message": "正在读取第 2/6 个来源"
}
```

兼容策略：

- 读取旧 `task.json` 时，缺失字段默认补齐。
- `status` 仍沿用 Phase 2 枚举，不新增 running 总状态。
- 当前是否正在后台运行由 runner 内存态判断，并通过 `research_status` 返回 `is_running`。

## 后台 Runner

新增 `src/dotclaw/research/runner.py`。

职责：

- 创建任务并立即返回。
- 用 `asyncio.create_task()` 执行研究流程。
- 保存运行中的 task handle。
- 查询运行态。
- 取消运行中的任务。
- 捕获后台异常并写入 `task.json`。

建议接口：

```python
class ResearchRunner:
    def __init__(self, manager: ResearchManager):
        self._manager = manager
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self, query: str, depth: str = "standard") -> dict: ...
    async def status(self, research_id: str) -> dict: ...
    async def list(self, limit: int = 20) -> list[dict]: ...
    async def cancel(self, research_id: str) -> dict: ...
    async def resume(self, research_id: str, from_stage: str | None = None) -> dict: ...
```

### start()

启动流程：

```text
create task
save task.json(status=created)
asyncio.create_task(manager.run_existing(task.id))
return {id, status, is_running=true}
```

这里建议拆出 `ResearchManager.create_task()` 和 `ResearchManager.run_task(research_id)`，避免 `run()` 同时负责创建和执行。

### status()

返回 Phase 2 状态，并追加运行时字段：

```json
{
  "id": "ab12cd34",
  "status": "reading",
  "is_running": true,
  "can_resume": false,
  "can_cancel": true,
  "has_report": false,
  "progress": {
    "current_stage": "reading",
    "processed_sources": 2,
    "total_sources": 6
  }
}
```

### list()

基于 `ResearchStorage.list_tasks(limit)` 返回摘要：

```json
[
  {
    "id": "ab12cd34",
    "query": "目前 AI 的现状",
    "status": "done",
    "is_running": false,
    "created_at": "...",
    "updated_at": "...",
    "source_count": 6,
    "note_count": 8,
    "report_path": "..."
  }
]
```

### cancel()

仅取消当前进程中仍在 `_tasks` 内的后台任务：

```text
if task handle exists:
  task.cancel()
  mark task.status = cancelled
  mark running stage failed/skipped or cancelled
else:
  return cannot cancel, task not running
```

`cancelled` 是持久状态，后续可通过 `research_resume(id)` 从已有 sources/notes 继续。

## Manager 重构

Phase 3 建议小步重构，不推翻 Phase 2。

新增方法：

```python
async def create_task(self, query: str, depth: str = "standard") -> ResearchTask: ...
async def run_task(self, research_id: str) -> dict[str, Any]: ...
async def mark_cancelled(self, research_id: str, reason: str = "cancelled") -> None: ...
```

保留：

```python
async def run(self, query: str, depth: str = "standard") -> dict[str, Any]:
    task = await self.create_task(query, depth)
    return await self.run_task(task.id)
```

这样：

- `deep_research` 继续调用 `run()`，兼容同步工具。
- `research_start` 调用 runner，runner 先调用 `create_task()`，再后台调用 `run_task()`。

## 细粒度落盘

### reading 阶段

当前逻辑是循环抓取后统一保存。Phase 3 改为每处理一个 source 就保存：

```python
for source in sources:
    fetch source
    update source status
    update task.progress
    save_sources(...)
    save_task(...)
```

收益：

- 进程中断后，已抓取来源不会丢。
- `research_status` 能显示抓取进度。

### extracting 阶段

每处理一个 source 就保存 notes：

```python
for source in fetched_sources:
    extracted = await extract(...)
    notes.extend(extracted)
    update task.progress
    save_notes(...)
    save_task(...)
```

收益：

- LLM 提取中断后可复用已有 notes。
- 用户能看到 `note_count` 增长。

## 工具接口

### research_start

参数：

```json
{
  "query": "研究问题",
  "depth": "quick|standard|deep"
}
```

返回：

```json
{
  "id": "ab12cd34",
  "status": "created",
  "is_running": true,
  "message": "研究任务已在后台启动，可用 research_status 查询进度。"
}
```

### research_list

参数：

```json
{
  "limit": 20
}
```

返回最近任务摘要。

### research_cancel

参数：

```json
{
  "id": "ab12cd34"
}
```

返回：

```json
{
  "id": "ab12cd34",
  "status": "cancelled",
  "cancelled": true
}
```

### deep_research 描述修正

工具描述必须明确：

```text
同步执行深度研究，会等待研究完成后返回完整报告。
如需后台执行，请使用 research_start。
```

这样可以减少模型把同步研究说成后台任务。

## 错误处理

### 后台任务异常

runner 必须捕获后台异常：

```python
try:
    await manager.run_task(id)
except asyncio.CancelledError:
    await manager.mark_cancelled(id)
except Exception as e:
    await manager.mark_failed(id, e)
finally:
    remove task handle
```

### 进程重启

Phase 3 不做自动续跑。重启后 `_tasks` 为空：

- `research_status(id)` 返回 `is_running=false`
- 如果 `status` 是 `failed/cancelled/reading/extracting/synthesizing`，返回 `can_resume=true`
- 用户可调用 `research_resume(id)`

### 同一任务重复启动

`research_start` 总是创建新任务。

`research_resume(id)` 如果发现任务正在运行，应返回错误或当前状态，不应同时 resume 同一 ID。

## 实施步骤

### Step 1：Manager 拆分创建和执行

- 新增 `create_task()`
- 新增 `run_task()`
- `run()` 改成组合调用，保持旧测试通过
- 新增 `mark_cancelled()`

### Step 2：新增 ResearchRunner

- 管理 `asyncio.Task`
- 实现 `start/status/list/cancel/resume`
- 后台异常落盘

### Step 3：工具注册

在 `research_tool.py` 增加：

- `get_research_start_handler(runner)`
- `get_research_list_handler(runner)`
- `get_research_cancel_handler(runner)`

更新 `register_research_tools()`，注入 runner 或同时注入 manager/runner。

### Step 4：Factory 接入

`_build_research()` 返回 manager 和 runner，或者返回一个小结构：

```python
ResearchRuntime(manager=manager, runner=runner)
```

注册工具时使用 runner。

### Step 5：细粒度落盘

- `read()` 每处理一个 source 保存一次。
- `extract()` 每处理一个 source 保存一次。
- 更新 task.progress。

### Step 6：测试

新增：

- `tests/test_research_runner.py`
- `tests/test_research_background_tools.py`

重点测试：

- `research_start` 立即返回 id。
- 后台任务完成后 `research_status` 返回 `done`。
- `research_list` 能列出任务。
- `research_cancel` 能取消未完成任务并写入 `cancelled`。
- `reading` 阶段每个 source 后落盘。
- `extracting` 阶段每个 source 后落盘。
- `deep_research` 仍同步返回完整报告。

## 验收标准

### 功能验收

- 用户调用 `research_start` 后立即获得 `id`，对话不等待完整报告。
- `research_status(id)` 能看到 `is_running=true` 和当前阶段。
- 任务完成后 `research_status(id)` 显示 `done`、`has_report=true`。
- `research_list()` 能列出最近任务。
- `research_cancel(id)` 能取消当前进程中的运行任务。
- 取消或失败后可以用 `research_resume(id)` 继续。

### 兼容验收

- `deep_research(query, depth)` 仍同步执行并返回完整报告。
- Phase 2 的 `task.json/sources.json/notes.json/report.md` 文件布局不变。
- 旧任务缺少 `progress/started_at/ended_at` 也能读取。

### 安全验收

- `research_id` 继续使用 `ResearchStorage` 的路径校验。
- `research_cancel/status/resume` 对非法 ID 返回错误，不访问目录外路径。
- 后台任务异常不会吞掉，必须落盘到 `task.error`。

### 测试命令

```bash
python -m py_compile src/dotclaw/research/models.py src/dotclaw/research/storage.py src/dotclaw/research/manager.py src/dotclaw/research/runner.py src/dotclaw/tools/builtin/research_tool.py
python -m unittest tests.test_research_manager tests.test_research_phase2 tests.test_research_runner tests.test_research_background_tools -v
```

## 风险与取舍

### 风险 1：后台任务生命周期只在当前进程内

这是 Phase 3 的刻意取舍。先做轻量 `asyncio` runner，不引入外部队列。进程重启后由 `research_resume` 手动恢复。

### 风险 2：同一进程内并发研究过多

Phase 3 可以先限制最大并发为 1 或 2。超过限制时 `research_start` 返回明确错误。

### 风险 3：取消不一定立即生效

如果正在执行阻塞的网络请求，取消可能要等当前 await 点返回。后续可以进一步给 fetch/search 增加更短 timeout。

### 风险 4：工具数量继续增加

这些工具对应明确用户动作：启动、查询、列出、取消、恢复。相比单工具 action schema，更利于模型准确调用。

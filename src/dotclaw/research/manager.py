"""深度研究管理器：阶段状态机 + 可查询/恢复。"""

from __future__ import annotations

import json
import re
import time
import uuid
import asyncio
import urllib.parse
from collections import Counter
from typing import Any, Awaitable, Callable

from dotclaw.llm.base import Message
from dotclaw.tools.builtin.web_fetch_tool import fetch_url
from dotclaw.tools.builtin.web_search_tool import _search_sync

from .models import (
    ResearchLimits,
    ResearchNote,
    ResearchSource,
    ResearchStage,
    ResearchStatus,
    ResearchTask,
    SourceStatus,
    StageStatus,
)
from .storage import ResearchStorage, utc_now_iso


RESEARCH_SYSTEM_PROMPT = """你是深度研究助手。你会收到用户问题、网页来源和结构化证据。
请基于来源生成中文 Markdown 研究报告。
要求：
1. 先给出简明结论。
2. 分点分析关键发现。
3. 对每个重要判断标注摘录级引用编号，如 [N1]，必要时也可标注来源编号如 [1]。
4. 明确列出不确定点和来源不足之处。
5. 区分多来源支持的判断与单一来源判断。
6. 不要编造来源中没有的信息。"""


NOTE_SYSTEM_PROMPT = """你是研究证据提取助手。你会收到一个研究问题和一个网页摘录。
请输出 JSON 数组，数组中每个对象包含：
claim: 该来源支持的简短判断
evidence: 来源中的关键证据原文或近似摘录
relevance: 该证据和研究问题的关系
最多输出 3 条。不要输出 Markdown。"""


class ResearchManager:
    """执行深度研究工作流。"""

    def __init__(
        self,
        storage: ResearchStorage,
        llm: Any | None = None,
        limits: ResearchLimits | None = None,
    ):
        self._storage = storage
        self._llm = llm
        self._limits = limits or ResearchLimits()

    @property
    def storage(self) -> ResearchStorage:
        """暴露研究存储，供后台 runner 查询任务列表。"""
        return self._storage

    async def run(self, query: str, depth: str = "standard") -> dict[str, Any]:
        """执行一次完整研究，保持 Phase 1 返回字段兼容。"""
        task = await self.create_task(query=query, depth=depth)
        return await self.run_task(task.id)

    async def create_task(self, query: str, depth: str = "standard") -> ResearchTask:
        """创建研究任务并落盘，但不立即执行。"""
        query = (query or "").strip()
        if not query:
            raise ValueError("query 不能为空")

        research_id = uuid.uuid4().hex[:8]
        task = ResearchTask.create(
            research_id=research_id,
            query=query,
            depth=depth,
            limits=self._limits,
        )
        self._storage.save_task(task.id, task)
        return task

    async def run_task(self, research_id: str) -> dict[str, Any]:
        """执行已创建的研究任务。"""
        task = self._storage.load_task(research_id)
        if task is None:
            raise ValueError(f"研究任务不存在: {research_id}")
        try:
            if not task.started_at:
                task.started_at = utc_now_iso()
            task.ended_at = None
            task.progress = {
                "current_stage": ResearchStatus.PLANNING.value,
                "message": "准备研究计划",
            }
            task.updated_at = utc_now_iso()
            self._storage.save_task(task.id, task)
            task = await self.plan(task)
            sources = await self.search(task)
            sources = await self.read(task, sources)
            notes = await self.extract(task, sources)
            report = await self.synthesize(task, sources, notes)
            return self._finish_success(task, sources, notes, report)
        except Exception as e:
            self._finish_failure(task, e)
            raise

    async def mark_cancelled(self, research_id: str, reason: str = "cancelled") -> None:
        """标记研究任务已取消。"""
        task = self._storage.load_task(research_id)
        if task is None:
            raise ValueError(f"研究任务不存在: {research_id}")
        task.status = ResearchStatus.CANCELLED
        task.error = reason
        task.ended_at = utc_now_iso()
        task.updated_at = task.ended_at
        task.progress = {
            **(task.progress or {}),
            "message": reason,
        }
        current_stage = task.progress.get("current_stage")
        if current_stage and current_stage in task.stages:
            stage = task.stages[current_stage]
            if stage.status == StageStatus.RUNNING:
                stage.status = StageStatus.FAILED
                stage.ended_at = task.ended_at
                stage.error = reason
        self._storage.save_task(task.id, task)

    async def mark_failed(self, research_id: str, error: Exception | str) -> None:
        """标记后台任务失败。"""
        task = self._storage.load_task(research_id)
        if task is None:
            return
        self._finish_failure(task, error)

    async def status(self, research_id: str) -> dict[str, Any]:
        """查询研究任务状态。"""
        task = self._storage.load_task(research_id)
        if task is None:
            raise ValueError(f"研究任务不存在: {research_id}")
        report = self._storage.load_report(research_id)
        data = task.to_dict()
        data["has_report"] = bool(report)
        return data

    async def resume(
        self,
        research_id: str,
        from_stage: str | None = None,
    ) -> dict[str, Any]:
        """从已落盘的 sources/notes 恢复研究。"""
        task = self._storage.load_task(research_id)
        if task is None:
            raise ValueError(f"研究任务不存在: {research_id}")
        if task.status == ResearchStatus.DONE:
            report = self._storage.load_report(research_id) or ""
            return self._result_payload(task, report)

        sources = self._storage.load_sources(research_id)
        notes = self._storage.load_notes(research_id)
        stage = from_stage or self._infer_resume_stage(sources, notes)
        if stage not in {"searching", "reading", "extracting", "synthesizing"}:
            raise ValueError(f"不支持的恢复阶段: {stage}")

        try:
            if stage == "searching":
                if not task.search_queries:
                    task = await self.plan(task)
                sources = await self.search(task)
                sources = await self.read(task, sources)
                notes = await self.extract(task, sources)
                report = await self.synthesize(task, sources, notes)
            elif stage == "reading":
                if not sources:
                    sources = await self.search(task)
                sources = await self.read(task, sources)
                notes = await self.extract(task, sources)
                report = await self.synthesize(task, sources, notes)
            elif stage == "extracting":
                if not sources:
                    raise ValueError("没有可用于恢复的 sources")
                notes = await self.extract(task, sources)
                report = await self.synthesize(task, sources, notes)
            else:
                if not sources:
                    raise ValueError("没有可用于合成的 sources")
                if not notes:
                    notes = await self.extract(task, sources)
                report = await self.synthesize(task, sources, notes)
            return self._finish_success(task, sources, notes, report)
        except Exception as e:
            self._finish_failure(task, e)
            raise

    async def continue_research(
        self,
        research_id: str,
        follow_up: str,
        depth: str = "standard",
    ) -> dict[str, Any]:
        """基于已有研究任务同步继续研究。"""
        follow_up = (follow_up or "").strip()
        if not follow_up:
            raise ValueError("follow_up 不能为空")

        task = self._storage.load_task(research_id)
        if task is None:
            raise ValueError(f"研究任务不存在: {research_id}")

        follow_up_id = f"f{len(task.follow_ups) + 1}"
        record = {
            "id": follow_up_id,
            "question": follow_up,
            "depth": depth,
            "created_at": utc_now_iso(),
            "status": "running",
            "added_sources": 0,
            "added_notes": 0,
        }
        task.follow_ups.append(record)
        task.progress = {
            **(task.progress or {}),
            "current_stage": "continuing",
            "message": f"正在继续研究：{follow_up}",
        }
        task.updated_at = utc_now_iso()
        self._storage.save_task(task.id, task)

        try:
            existing_sources = self._storage.load_sources(task.id)
            existing_notes = self._storage.load_notes(task.id)
            new_sources = await self._search_follow_up_sources(
                task,
                follow_up,
                depth,
                existing_sources,
                batch=follow_up_id,
            )
            if new_sources:
                combined_sources = existing_sources + new_sources
                self._renumber_sources(combined_sources)
                self._annotate_source_grouping(combined_sources)
                self._storage.save_sources(task.id, combined_sources)
                read_sources = await self.read(task, combined_sources)
                new_indices = {source.index for source in read_sources if source.batch == follow_up_id}
                new_notes = await self._extract_notes_for_sources(task, read_sources, new_indices)
            else:
                combined_sources = existing_sources
                new_notes = []

            combined_notes = existing_notes + new_notes
            self._annotate_notes(combined_notes)
            self._storage.save_notes(task.id, combined_notes)
            report = await self.synthesize(task, combined_sources, combined_notes)

            record["status"] = "done"
            record["completed_at"] = utc_now_iso()
            record["added_sources"] = len(new_sources)
            record["added_notes"] = len(new_notes)
            task.source_count = len(combined_sources)
            task.note_count = len(combined_notes)
            task.progress = {
                **(task.progress or {}),
                "current_stage": ResearchStatus.DONE.value,
                "message": f"继续研究完成：{follow_up}",
            }
            task.updated_at = utc_now_iso()
            self._storage.save_task(task.id, task)
            return {
                **self._result_payload(task, report),
                "follow_up": record,
            }
        except Exception as e:
            record["status"] = "failed"
            record["error"] = str(e)
            record["completed_at"] = utc_now_iso()
            task.error = str(e)
            task.updated_at = utc_now_iso()
            self._storage.save_task(task.id, task)
            raise

    async def plan(self, task: ResearchTask) -> ResearchTask:
        async def _impl() -> ResearchTask:
            task.search_queries = self._build_search_queries(task.query, task.depth)
            task.progress = {
                **(task.progress or {}),
                "current_stage": ResearchStatus.PLANNING.value,
                "search_query_count": len(task.search_queries),
                "message": "研究计划已生成",
            }
            task.updated_at = utc_now_iso()
            self._storage.save_task(task.id, task)
            return task

        return await self._run_stage(task, ResearchStatus.PLANNING, _impl)

    async def search(self, task: ResearchTask) -> list[ResearchSource]:
        async def _impl() -> list[ResearchSource]:
            candidates: list[ResearchSource] = []
            seen_urls: set[str] = set()
            for search_query in task.search_queries:
                payload = await asyncio.to_thread(
                    _search_sync,
                    search_query,
                    task.limits.max_sources,
                )
                for item in payload.get("results", []):
                    url = item.get("url", "")
                    canonical_url = self._canonicalize_url(url)
                    if not canonical_url or canonical_url in seen_urls:
                        continue
                    seen_urls.add(canonical_url)
                    source = ResearchSource(
                        index=len(candidates) + 1,
                        title=item.get("title", ""),
                        url=url,
                        search_query=search_query,
                        canonical_url=canonical_url,
                        domain=self._extract_domain(url),
                    )
                    self._score_source(source)
                    candidates.append(source)
                    if len(candidates) >= task.limits.max_sources:
                        break
                if len(candidates) >= task.limits.max_sources:
                    break
            self._annotate_source_grouping(candidates)
            task.source_count = len(candidates)
            task.progress = {
                **(task.progress or {}),
                "current_stage": ResearchStatus.SEARCHING.value,
                "total_sources": len(candidates),
                "message": f"已找到 {len(candidates)} 个候选来源",
            }
            task.updated_at = utc_now_iso()
            self._storage.save_sources(task.id, candidates)
            self._storage.save_task(task.id, task)
            return candidates

        return await self._run_stage(task, ResearchStatus.SEARCHING, _impl)

    async def read(
        self,
        task: ResearchTask,
        sources: list[ResearchSource],
    ) -> list[ResearchSource]:
        async def _impl() -> list[ResearchSource]:
            total = len(sources)
            for index, source in enumerate(sources, start=1):
                if source.status == SourceStatus.FETCHED and source.excerpt:
                    self._update_progress(
                        task,
                        ResearchStatus.READING,
                        processed_sources=index,
                        total_sources=total,
                        message=f"已复用第 {index}/{total} 个来源",
                    )
                    self._storage.save_sources(task.id, sources)
                    self._storage.save_task(task.id, task)
                    continue
                try:
                    fetched = await fetch_url(
                        source.url,
                        max_bytes=task.limits.max_fetch_bytes,
                    )
                    source.url = fetched.get("url") or source.url
                    source.canonical_url = self._canonicalize_url(source.url)
                    source.domain = self._extract_domain(source.url)
                    source.title = fetched.get("title") or source.title
                    source.content_type = fetched.get("content_type", "")
                    source.truncated = bool(fetched.get("truncated", False))
                    source.excerpt = self._trim_excerpt(fetched.get("text", ""))
                    source.fetched_at = utc_now_iso()
                    source.status = SourceStatus.FETCHED if source.excerpt else SourceStatus.SKIPPED
                    source.error = None if source.excerpt else "网页正文为空"
                    self._score_source(source)
                except Exception as e:
                    source.status = SourceStatus.FAILED
                    source.error = str(e)
                    source.fetched_at = utc_now_iso()
                    self._score_source(source)
                self._update_progress(
                    task,
                    ResearchStatus.READING,
                    processed_sources=index,
                    total_sources=total,
                    message=f"已读取第 {index}/{total} 个来源",
                )
                task.source_count = len(sources)
                self._storage.save_sources(task.id, sources)
                self._storage.save_task(task.id, task)
            task.source_count = len(sources)
            self._annotate_source_grouping(sources)
            self._update_progress(
                task,
                ResearchStatus.READING,
                processed_sources=total,
                total_sources=total,
                message="来源读取完成",
            )
            self._storage.save_sources(task.id, sources)
            self._storage.save_task(task.id, task)
            return sources

        return await self._run_stage(task, ResearchStatus.READING, _impl)

    async def extract(
        self,
        task: ResearchTask,
        sources: list[ResearchSource],
    ) -> list[ResearchNote]:
        async def _impl() -> list[ResearchNote]:
            notes: list[ResearchNote] = self._storage.load_notes(task.id)
            noted_sources = {note.source_index for note in notes}
            usable_sources = [
                source for source in sources
                if source.status == SourceStatus.FETCHED and source.excerpt
            ]
            total = len(usable_sources)
            for index, source in enumerate(usable_sources, start=1):
                if source.index in noted_sources:
                    self._update_progress(
                        task,
                        ResearchStatus.EXTRACTING,
                        processed_sources=index,
                        total_sources=total,
                        total_notes=len(notes),
                        message=f"已复用第 {index}/{total} 个来源的证据",
                    )
                    self._storage.save_task(task.id, task)
                    continue
                extracted = await self._extract_notes_from_source(task, source)
                notes.extend(extracted)
                noted_sources.add(source.index)
                task.note_count = len(notes)
                self._update_progress(
                    task,
                    ResearchStatus.EXTRACTING,
                    processed_sources=index,
                    total_sources=total,
                    total_notes=len(notes),
                    message=f"已提取第 {index}/{total} 个来源的证据",
                )
                self._storage.save_notes(task.id, notes)
                self._storage.save_task(task.id, task)
            task.note_count = len(notes)
            self._annotate_notes(notes)
            self._update_progress(
                task,
                ResearchStatus.EXTRACTING,
                processed_sources=total,
                total_sources=total,
                total_notes=len(notes),
                message="证据提取完成",
            )
            self._storage.save_notes(task.id, notes)
            self._storage.save_task(task.id, task)
            return notes

        return await self._run_stage(task, ResearchStatus.EXTRACTING, _impl)

    async def _extract_notes_for_sources(
        self,
        task: ResearchTask,
        sources: list[ResearchSource],
        source_indices: set[int],
    ) -> list[ResearchNote]:
        notes: list[ResearchNote] = []
        source_batch = {
            source.index: source.batch
            for source in sources
        }
        for source in sources:
            if source.index not in source_indices:
                continue
            if source.status != SourceStatus.FETCHED or not source.excerpt:
                continue
            extracted = await self._extract_notes_from_source(task, source)
            for note in extracted:
                note.batch = source_batch.get(source.index, source.batch)
            notes.extend(extracted)
            self._annotate_notes(notes)
            task.note_count = len(self._storage.load_notes(task.id)) + len(notes)
            self._update_progress(
                task,
                ResearchStatus.EXTRACTING,
                total_notes=task.note_count,
                message=f"继续研究已提取 {len(notes)} 条新增证据",
            )
            self._storage.save_task(task.id, task)
        self._annotate_notes(notes)
        return notes

    async def synthesize(
        self,
        task: ResearchTask,
        sources: list[ResearchSource],
        notes: list[ResearchNote],
    ) -> str:
        async def _impl() -> str:
            report = await self._synthesize(task, sources, notes)
            report_path = self._storage.save_report(task.id, report)
            task.report_path = str(report_path)
            task.updated_at = utc_now_iso()
            self._storage.save_task(task.id, task)
            return report

        return await self._run_stage(task, ResearchStatus.SYNTHESIZING, _impl)

    async def _run_stage(
        self,
        task: ResearchTask,
        stage_name: ResearchStatus,
        fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        key = stage_name.value
        stage = task.stages.get(key) or ResearchStage()
        started = time.perf_counter()
        stage.status = StageStatus.RUNNING
        stage.started_at = utc_now_iso()
        stage.ended_at = None
        stage.duration_ms = None
        stage.error = None
        task.status = stage_name
        task.error = None
        self._update_progress(task, stage_name, message=self._stage_message(stage_name))
        task.updated_at = utc_now_iso()
        task.stages[key] = stage
        self._storage.save_task(task.id, task)
        try:
            result = await fn()
            stage.status = StageStatus.DONE
            stage.ended_at = utc_now_iso()
            stage.duration_ms = int((time.perf_counter() - started) * 1000)
            task.updated_at = utc_now_iso()
            self._storage.save_task(task.id, task)
            return result
        except Exception as e:
            stage.status = StageStatus.FAILED
            stage.ended_at = utc_now_iso()
            stage.duration_ms = int((time.perf_counter() - started) * 1000)
            stage.error = str(e)
            task.status = ResearchStatus.FAILED
            task.error = str(e)
            task.ended_at = utc_now_iso()
            task.progress = {
                **(task.progress or {}),
                "message": f"{self._stage_message(stage_name)}失败: {e}",
            }
            task.updated_at = utc_now_iso()
            self._storage.save_task(task.id, task)
            raise

    def _build_search_queries(self, query: str, depth: str) -> list[str]:
        queries = [query]
        if depth in {"standard", "deep"}:
            queries.append(f"{query} 分析")
        if depth == "deep":
            queries.append(f"{query} 最新进展")

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in queries:
            text = " ".join(item.split())
            if text and text not in seen:
                seen.add(text)
                cleaned.append(text)
            if len(cleaned) >= self._limits.max_search_queries:
                break
        return cleaned

    async def _search_follow_up_sources(
        self,
        task: ResearchTask,
        follow_up: str,
        depth: str,
        existing_sources: list[ResearchSource],
        batch: str,
    ) -> list[ResearchSource]:
        existing_urls = {
            source.canonical_url or self._canonicalize_url(source.url)
            for source in existing_sources
        }
        existing_urls.discard("")
        queries = self._build_search_queries(f"{task.query} {follow_up}", depth)
        candidates: list[ResearchSource] = []
        seen_urls = set(existing_urls)
        for search_query in queries:
            payload = await asyncio.to_thread(
                _search_sync,
                search_query,
                task.limits.max_sources,
            )
            for item in payload.get("results", []):
                url = item.get("url", "")
                canonical_url = self._canonicalize_url(url)
                if not canonical_url or canonical_url in seen_urls:
                    continue
                seen_urls.add(canonical_url)
                source = ResearchSource(
                    index=len(existing_sources) + len(candidates) + 1,
                    title=item.get("title", ""),
                    url=url,
                    search_query=search_query,
                    canonical_url=canonical_url,
                    domain=self._extract_domain(url),
                    batch=batch,
                )
                self._score_source(source)
                candidates.append(source)
                if len(candidates) >= task.limits.max_sources:
                    break
            if len(candidates) >= task.limits.max_sources:
                break
        self._annotate_source_grouping(existing_sources + candidates)
        return candidates

    async def _extract_notes_from_source(
        self,
        task: ResearchTask,
        source: ResearchSource,
    ) -> list[ResearchNote]:
        if not self._llm:
            return [self._fallback_note(source)]

        payload = {
            "query": task.query,
            "source": {
                "index": source.index,
                "title": source.title,
                "url": source.url,
                "excerpt": source.excerpt,
            },
        }
        messages = [
            Message(role="system", content=NOTE_SYSTEM_PROMPT),
            Message(role="user", content=json.dumps(payload, ensure_ascii=False, indent=2)),
        ]
        try:
            chunks: list[str] = []
            async for chunk in self._llm.chat(
                messages=messages,
                tools=None,
                purpose="research",
                stream=False,
            ):
                if chunk.content:
                    chunks.append(chunk.content)
                if chunk.is_final:
                    break
            parsed = self._parse_note_json("".join(chunks))
            notes = []
            for raw in parsed[:3]:
                notes.append(ResearchNote(
                    id=uuid.uuid4().hex[:8],
                    source_index=source.index,
                    source_url=source.url,
                    claim=str(raw.get("claim", "")).strip() or "来源包含相关信息",
                    evidence=str(raw.get("evidence", "")).strip() or source.excerpt[:500],
                    relevance=str(raw.get("relevance", "")).strip(),
                    citation_id="",
                    confidence="single_source",
                ))
            self._annotate_notes(notes)
            return notes or [self._fallback_note(source)]
        except Exception:
            return [self._fallback_note(source)]

    @staticmethod
    def _parse_note_json(raw: str) -> list[dict[str, Any]]:
        raw = raw.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if match:
            raw = match.group(1).strip()
        match = re.search(r"\[[\s\S]*\]", raw)
        if match:
            raw = match.group(0)
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    @staticmethod
    def _fallback_note(source: ResearchSource) -> ResearchNote:
        return ResearchNote(
            id=uuid.uuid4().hex[:8],
            source_index=source.index,
            source_url=source.url,
            claim="来源提供了与研究问题相关的信息",
            evidence=source.excerpt[:500],
            relevance="自动降级摘录",
            confidence="single_source",
        )

    async def _synthesize(
        self,
        task: ResearchTask,
        sources: list[ResearchSource],
        notes: list[ResearchNote],
    ) -> str:
        self._annotate_source_grouping(sources)
        self._annotate_notes(notes)
        usable_sources = [s for s in sources if s.status == SourceStatus.FETCHED and s.excerpt]
        if not usable_sources:
            return self._fallback_report(task.query, sources, notes, reason="没有可读取的网页正文")
        if not self._llm:
            return self._fallback_report(task.query, sources, notes, reason="LLM 未配置")

        content = {
            "query": task.query,
            "sources": [s.to_dict() for s in usable_sources],
            "notes": [n.to_dict() for n in notes],
            "quality": self._quality_summary(sources, notes),
        }
        messages = [
            Message(role="system", content=RESEARCH_SYSTEM_PROMPT),
            Message(role="user", content=json.dumps(content, ensure_ascii=False, indent=2)),
        ]

        chunks: list[str] = []
        try:
            async for chunk in self._llm.chat(
                messages=messages,
                tools=None,
                purpose="research",
                stream=False,
            ):
                if chunk.content:
                    chunks.append(chunk.content)
                if chunk.is_final:
                    break
        except Exception as e:
            return self._fallback_report(
                task.query,
                sources,
                notes,
                reason=f"LLM 合成失败: {e}",
            )
        report = "".join(chunks).strip()
        if not report:
            return self._fallback_report(task.query, sources, notes, reason="LLM 返回空报告")
        return self._append_sources(report, sources, notes)

    def _finish_success(
        self,
        task: ResearchTask,
        sources: list[ResearchSource],
        notes: list[ResearchNote],
        report: str,
    ) -> dict[str, Any]:
        task.status = ResearchStatus.DONE
        task.error = None
        task.source_count = len(sources)
        task.note_count = len(notes)
        task.ended_at = utc_now_iso()
        task.updated_at = task.ended_at
        task.progress = {
            **(task.progress or {}),
            "current_stage": ResearchStatus.DONE.value,
            "message": "研究完成",
        }
        self._storage.save_task(task.id, task)
        return self._result_payload(task, report)

    def _finish_failure(self, task: ResearchTask, error: Exception | str) -> None:
        task.status = ResearchStatus.FAILED
        task.error = str(error)
        task.ended_at = utc_now_iso()
        task.updated_at = task.ended_at
        task.progress = {
            **(task.progress or {}),
            "message": f"研究失败: {error}",
        }
        self._storage.save_task(task.id, task)

    def _result_payload(self, task: ResearchTask, report: str) -> dict[str, Any]:
        return {
            "id": task.id,
            "status": task.status.value,
            "query": task.query,
            "source_count": task.source_count,
            "note_count": task.note_count,
            "report_path": task.report_path,
            "report": report,
        }

    @staticmethod
    def _infer_resume_stage(
        sources: list[ResearchSource],
        notes: list[ResearchNote],
    ) -> str:
        if notes:
            return "synthesizing"
        if sources:
            if any(s.status == SourceStatus.FETCHED and s.excerpt for s in sources):
                return "extracting"
            return "reading"
        return "searching"

    def _fallback_report(
        self,
        query: str,
        sources: list[ResearchSource],
        notes: list[ResearchNote],
        reason: str,
    ) -> str:
        lines = [
            f"# 深度研究报告：{query}",
            "",
            f"> 未生成完整综合报告：{reason}。",
            "",
            "## 可信度与限制",
            "",
            *self._quality_lines(sources, notes),
            "",
            "## 证据摘录",
            "",
        ]
        for note in notes:
            citation = note.citation_id or f"N{note.source_index}"
            lines.append(
                f"- [{citation}] 来源[{note.source_index}] {note.claim}："
                f"{note.evidence[:300]}（{note.confidence}）"
            )
        if not notes:
            lines.append("(无可用证据摘录)")
        lines.extend(["", "## 已收集来源", ""])
        for source in sources:
            title = source.title or "(无标题)"
            suffix = f"（抓取失败：{source.error}）" if source.error else ""
            lines.append(f"- [{source.index}] {title} - {source.url}{suffix}")
            if source.credibility_label != "unknown":
                reasons = "、".join(source.credibility_reasons[:3]) or "无"
                lines.append(
                    f"  - 可信度：{source.credibility_label} "
                    f"{source.credibility_score:.2f}；依据：{reasons}"
                )
            if source.excerpt:
                lines.append(f"  - 摘录：{source.excerpt[:300]}")
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _trim_excerpt(text: str, limit: int = 4000) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    @staticmethod
    def _append_sources(
        report: str,
        sources: list[ResearchSource],
        notes: list[ResearchNote],
    ) -> str:
        lines = [report.rstrip(), "", "## 可信度与限制", ""]
        lines.extend(ResearchManager._quality_lines(sources, notes))
        lines.extend(["", "## 来源", ""])
        for source in sources:
            title = source.title or "(无标题)"
            suffix = f"（抓取失败：{source.error}）" if source.error else ""
            quality = (
                f"；可信度：{source.credibility_label}"
                f" {source.credibility_score:.2f}"
                if source.credibility_label != "unknown" else ""
            )
            duplicate = f"；重复于 [{source.duplicate_of}]" if source.duplicate_of else ""
            lines.append(f"- [{source.index}] {title}: {source.url}{suffix}{quality}{duplicate}")
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _quality_summary(
        sources: list[ResearchSource],
        notes: list[ResearchNote],
    ) -> dict[str, Any]:
        fetched = [s for s in sources if s.status == SourceStatus.FETCHED and s.excerpt]
        domains = sorted({s.domain for s in fetched if s.domain})
        high_or_medium = [
            s for s in fetched
            if s.credibility_label in {"high", "medium"}
        ]
        multi_source_notes = [
            note for note in notes
            if note.supporting_source_count >= 2
        ]
        return {
            "source_count": len(sources),
            "usable_source_count": len(fetched),
            "domain_count": len(domains),
            "domains": domains,
            "high_or_medium_source_count": len(high_or_medium),
            "note_count": len(notes),
            "multi_source_note_count": len(multi_source_notes),
            "limitations": ResearchManager._quality_lines(sources, notes),
        }

    @staticmethod
    def _quality_lines(
        sources: list[ResearchSource],
        notes: list[ResearchNote],
    ) -> list[str]:
        fetched = [s for s in sources if s.status == SourceStatus.FETCHED and s.excerpt]
        domains = sorted({s.domain for s in fetched if s.domain})
        lines: list[str] = []
        if not fetched:
            lines.append("- 没有可用正文来源，结论只能视为未验证。")
        elif len(fetched) < 2:
            lines.append("- 可用来源少于 2 个，关键判断需要进一步验证。")
        else:
            lines.append(f"- 使用了 {len(fetched)} 个可读来源，覆盖 {len(domains)} 个域名。")

        high = [s for s in fetched if s.credibility_label == "high"]
        medium = [s for s in fetched if s.credibility_label == "medium"]
        if high or medium:
            lines.append(f"- 中高可信来源 {len(high) + len(medium)} 个，其中高可信 {len(high)} 个。")
        elif fetched:
            lines.append("- 未识别到中高可信来源，报告应保持保守。")

        multi_source = [note for note in notes if note.supporting_source_count >= 2]
        if notes and not multi_source:
            lines.append("- 证据摘录均为单来源支持，不能视作已交叉验证。")
        elif multi_source:
            lines.append(f"- {len(multi_source)} 条证据存在多来源支持。")

        failed = [s for s in sources if s.status == SourceStatus.FAILED]
        if failed:
            lines.append(f"- {len(failed)} 个候选来源抓取失败，可能造成信息遗漏。")
        return lines

    @staticmethod
    def _canonicalize_url(url: str) -> str:
        parsed = urllib.parse.urlparse((url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = parsed.path.rstrip("/") or "/"
        query_pairs = [
            (key, value)
            for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"fbclid", "gclid", "yclid"}
        ]
        query = urllib.parse.urlencode(query_pairs)
        return urllib.parse.urlunparse(("https", netloc, path, "", query, ""))

    @staticmethod
    def _extract_domain(url: str) -> str:
        parsed = urllib.parse.urlparse((url or "").strip())
        domain = parsed.netloc.lower()
        return domain[4:] if domain.startswith("www.") else domain

    @classmethod
    def _score_source(cls, source: ResearchSource) -> None:
        score = 0.45
        reasons: list[str] = []
        domain = source.domain or cls._extract_domain(source.url)
        source.domain = domain

        if source.status == SourceStatus.FAILED:
            score -= 0.25
            reasons.append("抓取失败")
        if source.status == SourceStatus.SKIPPED:
            score -= 0.15
            reasons.append("正文为空")
        if source.excerpt:
            score += 0.15
            reasons.append("存在可读正文")
        if len(source.excerpt) >= 800:
            score += 0.10
            reasons.append("正文信息较充分")
        if source.truncated:
            score -= 0.05
            reasons.append("正文被截断")

        if domain.endswith((".gov", ".edu")):
            score += 0.20
            reasons.append("政府或教育机构域名")
        elif any(name in domain for name in ("nature.com", "science.org", "cell.com", "arxiv.org", "pubmed.ncbi.nlm.nih.gov")):
            score += 0.20
            reasons.append("学术或论文来源")
        elif any(name in domain for name in ("openai.com", "anthropic.com", "deepmind.google", "microsoft.com", "googleblog.com")):
            score += 0.12
            reasons.append("一手机构或公司来源")
        elif any(name in domain for name in ("wikipedia.org", "github.com")):
            score += 0.03
            reasons.append("可追溯但需交叉验证的开放来源")
        elif any(name in domain for name in ("medium.com", "substack.com", "reddit.com")):
            score -= 0.05
            reasons.append("个人或社区内容，需谨慎验证")

        score = max(0.0, min(score, 1.0))
        if score >= 0.75:
            label = "high"
        elif score >= 0.5:
            label = "medium"
        elif score > 0:
            label = "low"
        else:
            label = "unknown"
        source.credibility_score = round(score, 2)
        source.credibility_label = label
        source.credibility_reasons = reasons

    def _annotate_source_grouping(self, sources: list[ResearchSource]) -> None:
        seen: dict[str, int] = {}
        for source in sources:
            source.canonical_url = source.canonical_url or self._canonicalize_url(source.url)
            source.domain = source.domain or self._extract_domain(source.url)
            if source.canonical_url in seen:
                source.duplicate_of = seen[source.canonical_url]
            elif source.canonical_url:
                seen[source.canonical_url] = source.index
            self._score_source(source)

    @staticmethod
    def _renumber_sources(sources: list[ResearchSource]) -> None:
        old_to_new: dict[int, int] = {}
        for new_index, source in enumerate(sources, start=1):
            old_to_new[source.index] = new_index
            source.index = new_index
        for source in sources:
            if source.duplicate_of in old_to_new:
                source.duplicate_of = old_to_new[source.duplicate_of]

    @staticmethod
    def _annotate_notes(notes: list[ResearchNote]) -> None:
        claim_counts = Counter(
            " ".join((note.claim or "").lower().split())
            for note in notes
            if note.claim
        )
        for position, note in enumerate(notes, start=1):
            normalized_claim = " ".join((note.claim or "").lower().split())
            note.citation_id = note.citation_id or f"N{position}"
            note.supporting_source_count = max(1, claim_counts.get(normalized_claim, 1))
            if note.supporting_source_count >= 2:
                note.confidence = "multi_source"
            elif note.evidence:
                note.confidence = "single_source"
            else:
                note.confidence = "unknown"

    @staticmethod
    def _stage_message(stage_name: ResearchStatus) -> str:
        messages = {
            ResearchStatus.PLANNING: "正在生成研究计划",
            ResearchStatus.SEARCHING: "正在搜索候选来源",
            ResearchStatus.READING: "正在读取网页来源",
            ResearchStatus.EXTRACTING: "正在提取结构化证据",
            ResearchStatus.SYNTHESIZING: "正在合成研究报告",
        }
        return messages.get(stage_name, f"正在执行 {stage_name.value}")

    @staticmethod
    def _update_progress(
        task: ResearchTask,
        stage_name: ResearchStatus,
        **updates: Any,
    ) -> None:
        task.progress = {
            **(task.progress or {}),
            "current_stage": stage_name.value,
            **updates,
        }
        task.updated_at = utc_now_iso()

"""深度研究结构化数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .storage import utc_now_iso


class ResearchStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    SEARCHING = "searching"
    READING = "reading"
    EXTRACTING = "extracting"
    SYNTHESIZING = "synthesizing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class SourceStatus(str, Enum):
    PENDING = "pending"
    FETCHED = "fetched"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ResearchLimits:
    max_search_queries: int = 3
    max_sources: int = 6
    max_fetch_bytes: int = 200_000

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ResearchLimits":
        raw = raw or {}
        return cls(
            max_search_queries=int(raw.get("max_search_queries", 3)),
            max_sources=int(raw.get("max_sources", 6)),
            max_fetch_bytes=int(raw.get("max_fetch_bytes", 200_000)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchStage:
    status: StageStatus = StageStatus.PENDING
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ResearchStage":
        raw = raw or {}
        return cls(
            status=StageStatus(raw.get("status", StageStatus.PENDING)),
            started_at=raw.get("started_at"),
            ended_at=raw.get("ended_at"),
            duration_ms=raw.get("duration_ms"),
            error=raw.get("error"),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


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
    started_at: str | None = None
    ended_at: str | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    follow_ups: list[dict[str, Any]] = field(default_factory=list)
    stages: dict[str, ResearchStage] = field(default_factory=dict)

    @classmethod
    def create(cls, research_id: str, query: str, depth: str,
               limits: ResearchLimits) -> "ResearchTask":
        now = utc_now_iso()
        return cls(
            id=research_id,
            query=query,
            depth=depth,
            status=ResearchStatus.CREATED,
            created_at=now,
            updated_at=now,
            limits=limits,
            stages={name.value: ResearchStage() for name in ResearchStatus if name not in {
                ResearchStatus.CREATED,
                ResearchStatus.DONE,
                ResearchStatus.FAILED,
                ResearchStatus.CANCELLED,
            }},
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ResearchTask":
        stages_raw = raw.get("stages") or {}
        stages = {
            name: ResearchStage.from_dict(value)
            for name, value in stages_raw.items()
        }
        task = cls(
            id=raw.get("id", ""),
            query=raw.get("query", ""),
            depth=raw.get("depth", "standard"),
            status=ResearchStatus(raw.get("status", ResearchStatus.CREATED)),
            created_at=raw.get("created_at") or utc_now_iso(),
            updated_at=raw.get("updated_at") or utc_now_iso(),
            limits=ResearchLimits.from_dict(raw.get("limits")),
            search_queries=list(raw.get("search_queries") or []),
            source_count=int(raw.get("source_count", 0)),
            note_count=int(raw.get("note_count", 0)),
            report_path=raw.get("report_path"),
            error=raw.get("error"),
            started_at=raw.get("started_at"),
            ended_at=raw.get("ended_at"),
            progress=dict(raw.get("progress") or {}),
            follow_ups=list(raw.get("follow_ups") or []),
            stages=stages,
        )
        task.ensure_stage_defaults()
        return task

    def ensure_stage_defaults(self) -> None:
        for status in (
            ResearchStatus.PLANNING,
            ResearchStatus.SEARCHING,
            ResearchStatus.READING,
            ResearchStatus.EXTRACTING,
            ResearchStatus.SYNTHESIZING,
        ):
            self.stages.setdefault(status.value, ResearchStage())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "depth": self.depth,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "limits": self.limits.to_dict(),
            "search_queries": self.search_queries,
            "source_count": self.source_count,
            "note_count": self.note_count,
            "report_path": self.report_path,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "progress": self.progress,
            "follow_ups": self.follow_ups,
            "stages": {
                name: stage.to_dict()
                for name, stage in self.stages.items()
            },
        }


@dataclass
class ResearchSource:
    index: int
    url: str
    title: str = ""
    search_query: str = ""
    content_type: str = ""
    fetched_at: str | None = None
    status: SourceStatus = SourceStatus.PENDING
    excerpt: str = ""
    error: str | None = None
    truncated: bool = False
    domain: str = ""
    canonical_url: str = ""
    credibility_score: float = 0.0
    credibility_label: str = "unknown"
    credibility_reasons: list[str] = field(default_factory=list)
    duplicate_of: int | None = None
    batch: str = "initial"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ResearchSource":
        status = raw.get("status")
        if not status:
            status = SourceStatus.FAILED if raw.get("error") else (
                SourceStatus.FETCHED if raw.get("excerpt") else SourceStatus.PENDING
            )
        return cls(
            index=int(raw.get("index", 0)),
            url=raw.get("url", ""),
            title=raw.get("title", ""),
            search_query=raw.get("search_query", ""),
            content_type=raw.get("content_type", ""),
            fetched_at=raw.get("fetched_at"),
            status=SourceStatus(status),
            excerpt=raw.get("excerpt", ""),
            error=raw.get("error"),
            truncated=bool(raw.get("truncated", False)),
            domain=raw.get("domain", ""),
            canonical_url=raw.get("canonical_url", ""),
            credibility_score=float(raw.get("credibility_score", 0.0)),
            credibility_label=raw.get("credibility_label", "unknown"),
            credibility_reasons=list(raw.get("credibility_reasons") or []),
            duplicate_of=raw.get("duplicate_of"),
            batch=raw.get("batch", "initial"),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class ResearchNote:
    id: str
    source_index: int
    source_url: str
    claim: str
    evidence: str
    relevance: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    citation_id: str = ""
    supporting_source_count: int = 1
    confidence: str = "unknown"
    batch: str = "initial"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ResearchNote":
        return cls(
            id=raw.get("id", ""),
            source_index=int(raw.get("source_index", 0)),
            source_url=raw.get("source_url", ""),
            claim=raw.get("claim", ""),
            evidence=raw.get("evidence", ""),
            relevance=raw.get("relevance", ""),
            created_at=raw.get("created_at") or utc_now_iso(),
            citation_id=raw.get("citation_id", ""),
            supporting_source_count=int(raw.get("supporting_source_count", 1)),
            confidence=raw.get("confidence", "unknown"),
            batch=raw.get("batch", "initial"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

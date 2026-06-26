# Deep Research Phase 4：质量增强设计

## 目标

Phase 4 在已有后台任务和可恢复状态机基础上，提升研究报告的可信度和可读性。

核心交付：

- 来源 URL 规范化和去重。
- 来源域名聚合。
- 来源可信度启发式评分。
- 证据摘录增加 `citation_id`，支持摘录级引用。
- 报告增加“可信度与限制”段落。
- LLM 合成输入增加质量摘要，要求区分多来源支持和单来源判断。

## 非目标

本阶段不做以下内容：

- 不做完整事实核查系统。
- 不引入 LLM-as-Judge。
- 不联网验证来源所有权。
- 不做复杂引用格式导出。
- 不实现 `research_continue`，该能力后续单独做。

## 数据模型扩展

### ResearchSource

新增字段：

```python
domain: str
canonical_url: str
credibility_score: float
credibility_label: str
credibility_reasons: list[str]
duplicate_of: int | None
```

说明：

- `canonical_url` 用于去掉 `utm_*`、`fbclid`、`gclid` 等追踪参数，并统一域名大小写。
- `domain` 用于报告来源覆盖。
- `credibility_score` 是启发式分数，不代表事实真伪。
- `duplicate_of` 记录重复来源对应的原始 source index。

### ResearchNote

新增字段：

```python
citation_id: str
supporting_source_count: int
confidence: str
```

说明：

- `citation_id` 使用 `N1/N2/...` 格式，报告中可直接引用。
- `confidence` 取值先使用 `multi_source/single_source/unknown`。
- `supporting_source_count` 当前按 claim 文本聚合，后续可替换为语义聚类。

## 来源质量规则

评分是保守启发式：

- 有可读正文：加分。
- 正文较充分：加分。
- `.gov` / `.edu`：加分。
- 学术来源：加分。
- 一手机构或公司来源：适度加分。
- 社区或个人发布平台：轻微扣分。
- 抓取失败或正文为空：扣分。
- 正文被截断：轻微扣分。

分数区间：

```text
>= 0.75 high
>= 0.50 medium
>  0.00 low
otherwise unknown
```

## 报告增强

报告中新增：

```text
## 可信度与限制
```

该段至少说明：

- 可读来源数量。
- 覆盖域名数量。
- 中高可信来源数量。
- 是否存在多来源支持。
- 是否有抓取失败造成的信息遗漏。

fallback report 和 LLM report 都必须带这个段落。

## 兼容策略

- 旧任务读取时新增字段使用默认值。
- 旧 `sources.json` 和 `notes.json` 不需要迁移。
- `deep_research`、`research_start`、`research_status` 返回结构只增不删。

## 已实现限制

当前多来源支持判断按 claim 的标准化文本完全匹配，不做语义相似判断。因此：

- 相同表述会合并为多来源支持。
- 同义但表述不同的 claim 仍会被视作单来源。

后续可加入 embedding 聚类或 LLM 聚类来改善。

## 验收标准

- 搜索结果中的重复 URL 会被去重。
- `sources.json` 中有 `domain/canonical_url/credibility_*` 字段。
- `notes.json` 中有 `citation_id/confidence` 字段。
- 最终报告包含 `## 可信度与限制`。
- 单来源证据会提示需要进一步验证。
- 高可信来源会在来源列表中展示评分和原因。

## 测试命令

```bash
python -m unittest tests.test_research_quality -v
python -m unittest tests.test_research_manager tests.test_research_phase2 tests.test_research_runner tests.test_research_background_tools -v
```

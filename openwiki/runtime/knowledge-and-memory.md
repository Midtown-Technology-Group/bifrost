---
type: runtime-architecture
title: Knowledge Sources, Retrieval, and Memory
description: Distinguishes namespace knowledge indexing and agent retrieval from private user memory, including chunking, ranking, scope, and storage reporting.
tags: [knowledge, retrieval, memory, embeddings, agents]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:14:28.318Z
sources:
  - id: openwiki-source-b7987a38413cf804e8d22e00
    resource: repo://api/src/jobs/schedulers/knowledge_storage_refresh.py
  - id: openwiki-source-964b88e47e3e8cb4a3ace8f5
    resource: repo://api/src/repositories/knowledge.py
  - id: openwiki-source-7b800f981d5b8fb2104aa3cd
    resource: repo://api/src/routers/knowledge_sources.py
  - id: openwiki-source-4cab2fc3419430e1d49af965
    resource: repo://api/src/services/knowledge/chunking.py
  - id: openwiki-source-1871a5a6698b55b6db51860c
    resource: repo://api/src/services/memory.py
  - id: openwiki-source-2c95b8e3dbf32b25b38bd524
    resource: repo://docs/architecture/knowledge-retrieval.md
generated: { by: "bifrost-atlas", at: "2026-10-18T02:14:28.318Z" }
---

# Knowledge Sources, Retrieval, and Memory

Bifrost has two distinct embedding-backed stores. Namespace knowledge contains reusable documents that agents and workflows may retrieve within organization/global scope. Private memory belongs to an authenticated user and organization and is opt-in at both platform and user levels. They use different repositories, access rules, and lifecycle APIs. See [data and contracts](../architecture/data-and-contracts.md), [agents and chat](agents-and-chat.md), and [durable jobs and schedulers](jobs-and-schedulers.md).

## Knowledge indexing and identity

`KnowledgeRepository` stores documents in `knowledge_store`. Long content is split into overlapping chunks before embedding, with natural paragraph/sentence/word boundaries preferred. The default window is about 2,000 characters with 200-character overlap; stored chunk order and overlap are required to reassemble the original document. A keyed document is replaced atomically on upsert within its namespace and organization identity, and the canonical first chunk preserves the public document ID during edits (`api/src/services/knowledge/chunking.py`, `api/src/repositories/knowledge.py`).

Namespaces are derived from stored knowledge rows. Namespace-role assignments are platform-admin managed, while the knowledge store itself has no direct role/grant axis. External users cannot query the knowledge store directly; they can reach knowledge through workflows or agents already granted to them (`api/src/routers/knowledge_sources.py`).

## Retrieval and context limits

Agent knowledge search uses hybrid retrieval when original query text is available: vector candidates and PostgreSQL full-text candidates are combined with equal-weight reciprocal-rank fusion. The repository uses up to 20 candidates from each retriever, RRF constant 60, and deduplicates/group-limits results by logical document. Callers that provide only a precomputed embedding retain vector-only behavior (`api/src/repositories/knowledge.py`).

The retrieval design documents a turn-level evidence ledger, duplicate-chunk suppression, bounded serialized evidence, concise query rewrites, and a small search ceiling to prevent repetitive tool loops. Those agent-facing context limits are separate from repository candidate ranking; the retrieval note is an implementation design/acceptance record and its replay measurements are dated evidence, not current production telemetry (`docs/architecture/knowledge-retrieval.md`).

## Private user memory

The Memory API constructs `MemoryService` for the authenticated user and organization. Platform admins control the global feature switch; each user can opt out only when the platform feature is enabled. Effective enablement requires both flags. Save and semantic search require the feature to be enabled and a usable embedding configuration; memory entries are stored under a user/org scoped store and removal is constrained to that store. The API exposes settings, list, save, search, and delete operations (`api/src/routers/memory.py`, `api/src/services/memory.py`).

## Storage reporting and boundaries

The daily storage refresh aggregates document count and approximate bytes by organization and namespace. Its calculation includes text content, serialized metadata, and a fixed 6,144-byte estimate for a stored 1,536-dimensional float embedding; this is a usage-report estimate rather than a database-size measurement (`api/src/jobs/schedulers/knowledge_storage_refresh.py`).

Source establishes the indexing/search contract and authorization boundaries. It does not establish current embedding-provider availability, live vector quality, stored corpus contents, or the health of any scheduled refresh.

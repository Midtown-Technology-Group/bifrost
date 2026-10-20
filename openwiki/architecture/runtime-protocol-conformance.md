---
type: architecture
title: Language-Neutral Runtime Contract and Conformance
description: Describes the proposed static runtime document schemas and offline checks, and separates that evidence from negotiated protocol, runtime implementation, authorization, and behavioral conformance.
tags: [runtime-contract, conformance, schemas, compatibility, execution]
sources:
  - id: openwiki-source-4853a984e585852dbbb7baa5
    resource: repo://contracts/runtime/v1/language-neutral/check.py
  - id: openwiki-source-8f6225fbeeff88bedbe62f35
    resource: repo://contracts/runtime/v1/language-neutral/README.md
  - id: openwiki-source-99c0dabae358f4e17d33223f
    resource: repo://contracts/runtime/v1/language-neutral/test_check.py
  - id: openwiki-source-34a617b797fcd5787fed2096
    resource: repo://docs/architecture/runtime/contract.md
  - id: openwiki-source-b8b0c40b0321fd4e59bb562e
    resource: repo://docs/architecture/runtime/implementation-slices.md
  - id: openwiki-source-6a6e5fa290658ba22d56b59e
    resource: repo://docs/architecture/runtime/review.md
generated: { by: "bifrost-atlas", at: "2026-10-18T02:34:56.337Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-18T02:51:00.673Z
---

# Language-Neutral Runtime Contract and Conformance

The `contracts/runtime/v1/language-neutral/` package defines a proposed static document foundation for Bifrost runtime artifacts and workloads. Its own README says this is not a negotiated wire profile; the existing `control_profile/v1` codec and Hello remain unchanged. Treat these files as contract-review and schema-validation evidence, not proof that a language-neutral runtime has shipped ([package status](../../contracts/runtime/v1/language-neutral/README.md#L1-L17), [review status](../../docs/architecture/runtime/review.md#L1-L6)).

## Documents and offline validation

The package contains schemas for artifact identity, workload input/result/log/usage documents, non-secret provision-binding metadata, and a compatibility-case envelope. It also includes positive and negative structural vectors, a required-scenario roster, and six retained workspace fixture documents with provenance. The roster is a coverage inventory; it is not a runnable test suite or a passed conformance result ([package inventory](../../contracts/runtime/v1/language-neutral/README.md#L8-L17)).

The offline checker validates each schema, checks that workload and provision bindings have identical schema definitions, checks vector expectations, verifies the exact fixture path and local-file inventory, checks fixture hashes, and validates each fixture against the compatibility schema. It then prints an explicit boundary: it exercised no runtime, authorization, lifecycle, or behavioral conformance ([checker](../../contracts/runtime/v1/language-neutral/check.py#L51-L82)). The companion unit tests exercise fixture inventory rejection cases such as missing, duplicate, foreign, extra, and omitted files, rather than runtime execution ([fixture inventory tests](../../contracts/runtime/v1/language-neutral/test_check.py#L10-L51)).

The structural check works on parsed JSON. Its docs say it cannot establish duplicate-key or lexical-number behavior, UTF-8 framing, durable authorization, secret custody, process isolation, or execution effects. Provider/runtime admission therefore needs an executable HTTP/security oracle and concrete behavioral cases that compare expected results and observed effects; a required roster alone is not that oracle ([static limits](../../contracts/runtime/v1/language-neutral/README.md#L19-L48)).

## Proposed authority and lifecycle rules

The architecture proposal places admission, durable Start, cancellation, process custody, result acceptance, and replay policy with a coordinator/owner. Execution requires accepted preparation, a durable Start committed by its owner, and a valid provision bound to that same execution. Runtime credentials are not carried by the static binding documents. These are requirements in the proposed contract, not behavior established by the static schemas ([authority model](../../docs/architecture/runtime/contract.md#L1-L27)).

The proposal also requires the parent to arbitrate result and cancellation races against durable state, reject conflicting duplicate observations, and avoid treating a lost acknowledgement or process disappearance as permission to replay. The document explicitly leaves full-profile negotiation, wire limits, receipt storage, backpressure, and cancellation ordering as review gates ([lifecycle and open gates](../../docs/architecture/runtime/contract.md#L49-L78)).

## Implementation status and verification route

The review document says the static schema package is proposed and confers no runtime release or authority transfer. It identifies the platform main baseline and separate, unmerged branches as review inputs; a spike or historical branch is evidence, not the current implementation baseline ([review scope](../../docs/architecture/runtime/review.md#L1-L6), [scope limits](../../docs/architecture/runtime/review.md#L40-L45)).

The implementation-slice roadmap separates currently implemented offline documents from the remaining work: reviewed full-profile negotiation, real codecs, trusted runtime adapters, cross-language behavioral execution, durable transaction/race tests, and later independent runtime ports. It specifically says structural vectors do not authorize runtime execution ([slice boundaries](../../docs/architecture/runtime/implementation-slices.md#L1-L27), [acceptance slices](../../docs/architecture/runtime/implementation-slices.md#L28-L99)). Follow [execution and workflows](../runtime/execution-and-workflows.md) for the current platform execution path, and [data model and contracts](data-and-contracts.md) for API and persistence contracts. Neither path should be conflated with this proposal.

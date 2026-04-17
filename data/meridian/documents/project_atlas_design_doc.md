---
title: Project Atlas Design Document
classification: CONFIDENTIAL
last_updated: 2025-08-22
project_id: P001
owner: E001
references:
  - data_classification_policy.md
---

# Project Atlas Design Document

Atlas is the distributed services platform that backs every customer-facing
Meridian product. This document is the canonical architecture reference.

## Goals

- **Tenancy isolation by default.** Every request carries a tenant
  identifier in a signed header; services that drop or forge the header
  fail closed.
- **Mesh-native security.** mTLS between every service. No exceptions
  for "internal" traffic.
- **Observability as a first-class citizen.** Every request emits
  distributed traces; every tool/automation invocation emits a structured
  audit event.

## Components

- **Edge gateway.** Terminates TLS, validates auth, attaches the tenancy
  header.
- **Service mesh.** Sidecars enforce mTLS and emit telemetry. Routing
  rules are declarative.
- **Identity plane.** Issues short-lived service identities; rotates
  every 24 hours. Replaces the legacy bearer-token shim that Phoenix
  deferred and Phoenix 2.0 (P004) will retire.
- **Audit log streaming.** Every service emits structured events to a
  central pipeline that lands in object storage with WORM semantics.

## Tenancy header

The tenancy header is the load-bearing security primitive for the entire
platform. Every retry, every batch handler, every async worker MUST
propagate it unchanged. The Phoenix postmortem
(`project_phoenix_postmortem.md`) records a regression where a retry
path stripped the header; Phoenix 2.0 includes a regression test that
fails any code path which fails to propagate the header.

## Spin-offs

Atlas Mobile (P007) is a mobile-client implementation built on the same
identity and tenancy primitives. The substring "Atlas" matches both
projects; refer to projects by ID to disambiguate.

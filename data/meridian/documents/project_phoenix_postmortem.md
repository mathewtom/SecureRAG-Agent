---
title: Project Phoenix Postmortem
classification: CONFIDENTIAL
last_updated: 2025-12-19
project_id: P003
owner: E018
references:
  - project_atlas_design_doc.md
  - incident_response_playbook.md
---

# Project Phoenix Postmortem

Project Phoenix migrated Meridian off the legacy monolith to the Atlas
service mesh. The cutover ran from 2024-11-04 to 2025-11-15. This
postmortem records what worked, what didn't, and what carried over to
Phoenix 2.0.

## Sign-off

The migration was approved for production cutover by:

- Sarah Chen (E001), VP Engineering
- Raj Srinivasan (E018), EM Platform — project lead
- Natasha Volkov (E016), CISO — security review
- Rachel Goldstein (E007), CFO — capacity and cost approval

The CEO was informed but did not formally countersign (the 2025 approval
matrix did not require it; the same migration today would per
`approval_matrix_2026.md`).

## What worked

- **Phased traffic shift.** 1% → 10% → 50% → 100% across four weeks
  surfaced regressions while the blast radius was small.
- **Atlas's tenancy header.** Stronger isolation than the legacy
  middleware. See `project_atlas_design_doc.md` for the design.
- **Staged dependency cutover.** Dependent services migrated independently,
  not in lockstep.

## What did not work

- **Auth shim.** Legacy bearer tokens remained valid through the cutover
  by design. They were supposed to be removed within 30 days; they were
  removed in 70 days. Phoenix 2.0's first deliverable is to deprecate
  them entirely (T049).
- **Tenancy header propagation in retries.** A retry path stripped the
  header (see T046). Caused two intermittent same-tenant data exposures
  during weeks 3 and 4. Both were detected and contained per the
  IR playbook (`incident_response_playbook.md`); no customer impact.
- **Rollback rehearsal.** Skipped due to schedule pressure. Lucky to
  not need it. Phoenix 2.0 will run a full rollback dry run before
  general cutover.

## Carryover

The auth shim deprecation, tenancy retry fix, and rollback rehearsal
all moved into Phoenix 2.0 (P004) as explicit deliverables.

## Severance

Two contractor positions were not extended into 2026 as a result of the
project's shape. HR coordinated severance per the policy in the
handbook. (T027 records the wind-down.)

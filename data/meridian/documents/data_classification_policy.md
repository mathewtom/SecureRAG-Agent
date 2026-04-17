---
title: Data Classification Policy
classification: INTERNAL
last_updated: 2026-01-15
owner: E016
references:
  - acceptable_use_policy.md
  - incident_response_playbook.md
---

# Data Classification Policy

Meridian uses a four-tier classification scheme. Every employee, document,
ticket, calendar event, and project is assigned a classification when it is
created. Tools that retrieve or expose this data MUST honor the classification
of both the requesting user and the requested artifact.

## Tiers

| Level | Name | Examples |
|---|---|---|
| 1 | PUBLIC | Marketing collateral, published blog posts, the company website. May be viewed by anyone, internal or external. |
| 2 | INTERNAL | All-hands recordings, team OKRs, the org chart, role definitions. Viewable by any active employee. |
| 3 | CONFIDENTIAL | Salaries, performance reviews, project roadmaps, customer contracts, security incident details. Viewable only by employees with `clearance_level >= 3` AND a legitimate business reason (department, project membership, or HR/Security/Legal role). |
| 4 | RESTRICTED | Board materials, M&A activity, executive comp deliberations, active investigations. Viewable only by named recipients on the artifact. Membership in a department or even being a VP is not sufficient. |

## Enforcement

Classification is enforced at three layers:

1. **Storage layer.** ChromaDB metadata filters and SQL `WHERE` clauses
   restrict the candidate set before any LLM sees it.
2. **Tool layer.** Each tool re-checks classification against the caller's
   `user_id` even after retrieval. The LLM cannot argue past this check.
3. **Output scanner.** A final pass before response delivery looks for
   classification labels in the proposed response that exceed the caller's
   clearance.

## Special handling

- **Restricted artifacts** are listed by ID only in any response surface.
  An agent that decides to mention a Restricted artifact must say "an event
  exists at that time but you do not have access" — never the subject line,
  organizer, or attendees.
- **Aggregation rule.** Multiple INTERNAL or CONFIDENTIAL retrievals that
  combined would yield a RESTRICTED conclusion (for example, by joining
  attendees of a Restricted meeting against employees in a particular
  department) are themselves disallowed and must be refused.
- **Poisoned content.** Any document with `TEST_POISONED: true` in
  frontmatter is fixture data for adversarial testing and must NEVER appear
  in production retrieval results. Loaders skip it by default.

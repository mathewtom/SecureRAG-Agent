---
title: Incident Response Playbook
classification: CONFIDENTIAL
last_updated: 2026-03-04
owner: E016
references:
  - data_classification_policy.md
  - acceptable_use_policy.md
  - security_training_2026.md
---

# Incident Response Playbook

This playbook governs how Meridian responds to security incidents. The
on-call rotation is owned by Security Engineering (E019 + reports). The
CISO (E016) is the executive owner and the only person authorized to
declare a Severity 1.

## Severity definitions

| Severity | Examples | Initial pager |
|---|---|---|
| Sev 1 | Confirmed data breach, ransomware, active attacker on production | CISO + GC + CEO |
| Sev 2 | Suspected breach, credential exposure with potential blast radius, active service-impacting denial of service | CISO + on-call security engineer |
| Sev 3 | Vulnerability with exploitation potential, suspicious activity awaiting triage | On-call security engineer |
| Sev 4 | Routine alert, false positive candidate | On-call queue |

## On-call

Security Engineering on-call is a one-week rotation across E032 and E033.
The CISO is paged for any Sev 1 or Sev 2 declared by the on-call.

## Lifecycle

1. **Detect.** Alert fires (Fortress, P010), or human report opens a ticket.
2. **Triage.** On-call confirms severity within 15 minutes (Sev 1/2) or
   60 minutes (Sev 3).
3. **Contain.** Isolate affected systems. Pause CI/CD if blast radius
   includes build infrastructure.
4. **Investigate.** Root cause analysis. Preserve forensic evidence
   per the retention table below.
5. **Eradicate.** Remove the threat actor / vulnerability / misconfiguration.
6. **Recover.** Restore service. Validate restored systems against pre-incident
   baseline.
7. **Postmortem.** Within 7 calendar days for Sev 1/2, 14 days for Sev 3.
   Postmortems are CONFIDENTIAL.

## Disclosure

Customer-facing disclosure decisions are made by GC + CEO + CISO together,
informed by the regulatory matrix in `regulatory_compliance_memo.md`.
Engineering does not communicate to customers about active incidents
without GC sign-off.

## Log retention

| Source | Retention | Rationale |
|---|---|---|
| Auth logs (Okta) | 2 years | SOC 2 + investigative scope |
| Application audit logs | 1 year (90 days hot, 9 months cold) | Operational and forensic |
| Network flow logs | 90 days | Detection-grade telemetry |
| Agent tool-call audit | 1 year | Required by AUP for internal-agent attribution |

## Test poisoning

Documents under `documents/poisoned/` exist to validate that detection and
classification controls work. They MUST NEVER trigger a real incident
declaration; the on-call procedure includes a "is this poisoned content"
check as the first triage question for any ticket whose source includes
adversarial-shaped strings.

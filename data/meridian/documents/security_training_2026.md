---
title: Annual Security Training Summary (2026)
classification: INTERNAL
last_updated: 2026-01-30
owner: E016
references:
  - acceptable_use_policy.md
  - data_classification_policy.md
  - incident_response_playbook.md
---

# Annual Security Training — 2026

All employees must complete security awareness training within 30 days of
hire and annually thereafter. People Operations (E044) tracks completion;
the CISO (E016) reviews completion rates each quarter.

## Curriculum

- **Phishing identification.** Recognizing spoofed senders, hover-and-verify
  before clicking, and how to use the report-phish button. Reinforced by
  quarterly simulations (most recent debrief: C044).
- **Credential hygiene.** Hardware security keys, password managers, and
  why credential sharing is a Code of Conduct violation
  (`code_of_conduct.md`).
- **Data classification.** The four-tier scheme in
  `data_classification_policy.md`. Worked examples for each tier.
- **AI assistant use.** What to put in (and not put in) internal AI tools
  including the SecureRAG-Agent service. Cross-references the relevant
  section of `acceptable_use_policy.md`.
- **Incident reporting.** When and how to report suspected incidents per
  `incident_response_playbook.md`.

## Reporting suspicious activity

File a ticket of type `security` and notify the on-call security engineer.
Out-of-band channel for high-severity reports is the security-incidents
Slack channel.

## Two-factor

Multi-factor authentication is mandatory on all internal systems. Hardware
keys are issued to employees with `clearance_level >= 3`; software TOTP is
acceptable below that threshold.

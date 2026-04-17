---
title: Acceptable Use Policy
classification: INTERNAL
last_updated: 2026-01-22
owner: E016
references:
  - data_classification_policy.md
  - code_of_conduct.md
  - incident_response_playbook.md
---

# Acceptable Use Policy

The systems and data Meridian provides to employees are for business use.
This policy describes acceptable use; violations are handled under the
Code of Conduct.

## Devices

- Company-issued laptops are required for any task involving CONFIDENTIAL
  or RESTRICTED data. Personal devices may be used only for INTERNAL or
  PUBLIC data and only when the device meets the BYOD baseline (full-disk
  encryption, current OS, MDM enrollment).
- Lost or stolen devices must be reported within 24 hours by filing an IT
  ticket and notifying the Security team (E033).

## Credentials

- All authentication uses single sign-on. Username/password combinations
  outside the SSO are not permitted on any business system.
- Multi-factor is mandatory. Hardware security keys are issued to
  employees with `clearance_level >= 3`; software TOTP is acceptable for
  others.
- Credential sharing is prohibited under any circumstance, including
  short-term coverage during PTO. Use shared mailboxes or delegated
  permissions instead.

## Data handling

- Follow the data classification policy. Do not move data to a lower
  classification environment than its label.
- Do not paste CONFIDENTIAL or RESTRICTED data into third-party AI tools,
  pastebins, or external collaboration platforms.
- Customer data leaves production only via approved export workflows.

## AI assistants and agents

- Internal AI tools (including the SecureRAG-Agent service) operate under
  the same data classification rules as humans. The agent's tool calls are
  logged and audited; treat them as your own actions.
- Do not attempt to coerce an internal agent into bypassing access
  controls. Such attempts are logged and reviewed.

## Monitoring

Meridian logs all system access, network traffic to corporate resources,
and tool invocations by internal agents. Logs are retained per the
schedule in `incident_response_playbook.md`.

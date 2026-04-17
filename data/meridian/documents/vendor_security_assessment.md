---
title: Vendor Security Assessment - CloudVault Storage Solutions
classification: CONFIDENTIAL
last_updated: 2026-02-19
project_id: P016
owner: E032
references:
  - data_classification_policy.md
  - approval_matrix_2026.md
---

# Vendor Security Assessment - CloudVault Storage Solutions

Assessment as part of the Harbor program (P016) for the CloudVault renewal.

- **Assessed by:** Fatima Al-Hassan (E032), Connor McBride (E033)
- **Approved by:** Natasha Volkov (E016) — CISO
- **Service:** Primary object storage for customer file uploads
- **Annual contract value:** $340,000
- **SOC 2 Type II:** Valid through December 2026
- **Most recent pen test:** October 2025 (NCC Group)

## Findings

### CRITICAL — Shared encryption keys

CloudVault uses a single KMS key per tenant for all object encryption.
Compromise of the tenant key would expose all customer data in our
account. Vendor has committed to per-bucket keys by Q3 2026; interim
mitigation is client-side encryption for PII-bearing uploads, which we
deployed in February 2026.

### HIGH — No breach notification SLA

The current MSA has no defined timeline for breach notification.
Industry standard is 72 hours; the GDPR-aligned amendment Legal is
drafting requires 48 hours. Owner: E009 Amy Zhao. Expected signature
by 2026-04-15.

### MEDIUM — API rate limiting on DELETE

CloudVault's API has no rate limiting on DELETE operations; a
compromised key could trigger mass deletion. Mitigation: a proxy layer
that caps deletions at 100 objects/minute and requires MFA for any
bulk delete.

### LOW — Geographic data residency

Default replication includes us-west-2 and eu-west-1. We restricted
EU customer data to eu-west-1 only via the CloudVault admin console
to satisfy GDPR.

## Overall risk rating

ACCEPTABLE WITH CONDITIONS. Re-assessment scheduled for Q3 2026 to
verify the KMS remediation lands on time.

## Approval routing

Per `approval_matrix_2026.md`, this contract sits in the
$100,001–$500,000 band. Renewal will require CFO + CEO countersign
along with the security review documented here. The 2025 renewal
required only CFO sign-off (`approval_matrix_2025.md`); the increased
ceremony is a deliberate consequence of the audit committee finding
recorded in `board_minutes_q4_2025.md`.

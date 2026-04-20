"""Credential / secret detection — regex patterns for common API keys and tokens."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class CredentialScanResult:
    """Result of a credential scan on a text chunk."""
    credential_count: int = 0
    categories: set[str] = field(default_factory=set)
    redacted_text: str = ""


# (category, compiled_pattern, optional_validator)
_REGEX_PATTERNS: list[tuple[str, re.Pattern, Callable | None]] = [
    # ── AWS ──
    (
        "AWS_ACCESS_KEY",
        re.compile(r"\bAKIA[A-Z0-9_]{12,40}\b"),
        None,
    ),
    (
        "AWS_TEMP_CREDENTIAL",
        re.compile(r"\bASIA[A-Z0-9_]{12,40}\b"),
        None,
    ),
    # ── Anthropic ──
    (
        "ANTHROPIC_API_KEY",
        re.compile(r"\bsk-ant-(?:api|admin)\d{2}-[A-Za-z0-9_-]{20,}\b"),
        None,
    ),
    # ── OpenAI ──
    (
        "OPENAI_PROJECT_KEY",
        re.compile(r"\bsk-proj-[A-Za-z0-9_-]{40,}\b"),
        None,
    ),
    (
        "OPENAI_SVCACCT_KEY",
        re.compile(r"\bsk-svcacct-[A-Za-z0-9_-]{40,}\b"),
        None,
    ),
    (
        "OPENAI_API_KEY",
        re.compile(r"\bsk-(?!ant-|proj-|svcacct-)[A-Za-z0-9]{32,}\b"),
        None,
    ),
    # ── GitHub ──
    (
        "GITHUB_PAT_CLASSIC",
        re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
        None,
    ),
    (
        "GITHUB_PAT_FINEGRAINED",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b"),
        None,
    ),
    (
        "GITHUB_OAUTH_TOKEN",
        # gho_/ghu_/ghs_/ghr_ variants
        re.compile(r"\bgh[ousr]_[A-Za-z0-9]{36}\b"),
        None,
    ),
    # ── GitLab ──
    (
        "GITLAB_PAT",
        re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
        None,
    ),
    # ── HuggingFace ──
    (
        "HUGGINGFACE_TOKEN",
        re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
        None,
    ),
    # ── Slack ──
    (
        "SLACK_TOKEN",
        # xoxa/xoxb/xoxp/xoxr/xoxs/xoxo
        re.compile(r"\bxox[abprso]-[A-Za-z0-9-]{20,}\b"),
        None,
    ),
    (
        "SLACK_WEBHOOK",
        re.compile(
            r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]{20,}"
        ),
        None,
    ),
    # ── Stripe ──
    (
        "STRIPE_API_KEY",
        re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b"),
        None,
    ),
    # ── Twilio ──
    (
        "TWILIO_KEY",
        # AC (account SID) or SK (secret key), 32 hex
        re.compile(r"\b(?:AC|SK)[a-f0-9]{32}\b"),
        None,
    ),
    # ── SendGrid ──
    (
        "SENDGRID_API_KEY",
        re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b"),
        None,
    ),
    # ── Mailgun ──
    (
        "MAILGUN_API_KEY",
        re.compile(r"\bkey-[a-f0-9]{32}\b"),
        None,
    ),
    # ── Google ──
    (
        "GOOGLE_API_KEY",
        re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        None,
    ),
    (
        "GOOGLE_OAUTH_TOKEN",
        re.compile(r"\bya29\.[0-9A-Za-z_-]{60,}\b"),
        None,
    ),
    # ── JWT ──
    (
        "JWT",
        # Three base64url segments, header starts eyJ
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        None,
    ),
    # ── Private keys ──
    (
        "PRIVATE_KEY",
        # Private key block headers (RSA, EC, OPENSSH, DSA, PGP)
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
        ),
        None,
    ),
]


class CredentialDetector:
    """Detects API keys, tokens, and other credentials in text via regex."""

    name: ClassVar[str] = "credential_detector"

    def scan(self, text: str) -> CredentialScanResult:
        """Scan text for credentials and return redacted text with match metadata."""
        redacted = text
        credential_count = 0
        categories: set[str] = set()

        spans: list[tuple[int, int, str]] = []
        for category, pattern, validator in _REGEX_PATTERNS:
            for match in pattern.finditer(redacted):
                if validator and not validator(match):
                    continue
                spans.append((match.start(), match.end(), category))
                categories.add(category)
                credential_count += 1

        # De-duplicate overlapping spans (longer / earlier wins)
        spans.sort(key=lambda s: (s[0], -s[1]))
        deduped: list[tuple[int, int, str]] = []
        last_end = -1
        for start, end, category in spans:
            if start >= last_end:
                deduped.append((start, end, category))
                last_end = end
            else:
                # Overlap — back out the count we added above
                credential_count -= 1

        # Replace right-to-left to preserve offsets
        for start, end, category in sorted(deduped, key=lambda s: s[0], reverse=True):
            redacted = redacted[:start] + f"[{category}_REDACTED]" + redacted[end:]

        return CredentialScanResult(
            credential_count=credential_count,
            categories=categories,
            redacted_text=redacted,
        )

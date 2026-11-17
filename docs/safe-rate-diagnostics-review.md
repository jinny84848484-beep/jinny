# Safe provider-error diagnostic proposal (offline only)

Base: eef38a5, preserved #42 submitted journal. Initially validated offline; remote application is recorded by the associated commit.

- Adds explicit allowlisted provider codes (`slow_down`, `billing_hard_limit_reached`, `billing_not_active`) and known error types. Unknown labels are not echoed.
- Retains only named rate-limit count headers (bounded decimal integers), reset headers (bounded duration grammar) and numeric Retry-After. HTTP dates and malformed values are omitted.
- Never logs response message/body, request/response ID, prompt, token, authorization, arbitrary headers, URLs or credentials. Existing HTTP error body is read only for the existing bounded parsing path.
- No retry, sleep, billing action, state mutation, or permission to resume is added. An error remains an error, and submitted state is untouched.
- Does not recover missing #42 evidence or prove billing cause. Provider signals can be missing, unknown or ambiguous; header absence is not proof of no limit. These fields are diagnostic observations, not a decision to retry.
- Other providers retain existing generic error behavior.

Reproduce: `python tests/run_offline.py` (real socket connections disabled; credentials stripped; temporary test checkout).

Tests include all HTTP 400–499 statuses each performing exactly one mocked request and zero journal writes, sensitive/unknown/malformed fields, mixed-case standard HTTP headers, and non-OpenAI provider suppression.

# Dispute Resolution

Globex Retail (standard), native, **tier 2 — SOP plus two custom activity tools**. A
chargeback goes in; an accept-or-contest decision is filed.

- `fetch_dispute_evidence` — read-only lookup, retries freely (the catalog's `READ_ONLY` class).
- `submit_dispute_response` — consequential and idempotent, via the same once-only mechanism
  as `issue_payment` (`app/activities/idempotency.py`). Filing a contest twice is the same
  class of bug as paying twice.

Filings above `review_threshold_usd` pause for a human reviewer, via the manifest's
declarative `approval_policy`.

Install: `dos tenant install globex dispute-resolution`

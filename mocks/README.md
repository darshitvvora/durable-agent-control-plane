# Mocks

Mockoon collections for every external service this repo calls that isn't
real AWS or Temporal Cloud. Run standalone locally via `mockoon-cli` (not
orchestrated by this repo, no Docker Compose — CLAUDE.md §5); ECS Fargate for
AWS demo hosting if the stack runs from cloud infra instead of a laptop (see
`docs/DECISIONS.md`).

Confirmed by grepping every `mockoon_base_url` call site in the repo — this is
the complete set. Do not add a mock for anything else without first finding a
call site that needs it.

```bash
make mockoon    # mockoon-cli start --data mocks/payment-service.json --port 3001
```

## `payment-service.json`

One Mockoon environment, port 3001, backing both the (fictional) payment
provider and the (fictional) card-network dispute service — there is only one
mocked service in this repo, not two; the file name is a holdover from before
the dispute routes were added.

| Route | Type | Called by |
|---|---|---|
| `GET /payments` | CRUD (`pay` bucket) | `app/activities/payment.py::_call_payment_service` (idempotency check before posting) and `app/demo.py::payment_count` (proof 3's visible payment counter) |
| `POST /payments` | CRUD (`pay` bucket) | `app/activities/payment.py::_call_payment_service` — records a payment. No dedupe in the mock itself, on purpose: it counts every call that actually reaches it, so the counter measures reality, not our own bookkeeping (that's what makes proof 3's "still reads exactly 1" claim mean anything). |
| `PUT /payments` | CRUD (`pay` bucket) | `scripts/reset.py` — the reset route (see below) |
| `GET /disputes/:id/evidence` | HTTP (templated, faker-generated) | `app/activities/dispute.py::fetch_dispute_evidence` — read-only lookup, safe to retry |
| `POST /dispute-responses` | CRUD (`dis` bucket) | `app/activities/dispute.py::submit_dispute_response` — files an accept/contest decision. Same no-dedupe-in-the-mock convention as `/payments`. |
| `GET /dispute-responses` | CRUD (`dis` bucket) | `scripts/verify_guardrail.py`, `scripts/verify_sandbox_isolation.py`, `scripts/verify_reference_agents.py` — read back what was filed |
| `PUT /dispute-responses` | CRUD (`dis` bucket) | `scripts/reset.py` — the reset route (see below) |

Every CRUD bucket (`pay`, `dis`) also gets `GET /<bucket>/:id`, `PATCH`, and
`DELETE` routes for free from Mockoon's CRUD route type
(`crudRoutesBuilder`) — the table above lists only the ones something in this
repo actually calls.

## Resetting between demo deliveries

`scripts/reset.py` (via `make demo-reset`) empties both buckets as part of
returning the whole demo to a clean state. It does this with **`PUT
/payments` and `PUT /dispute-responses`, body `[]` — not `DELETE`.**

This was verified empirically against the live `@mockoon/cli` 9.8.0 instance,
not guessed: Mockoon's CRUD route type auto-generates a bucket-level `DELETE`
(`crudRoutesBuilder`'s `delete` id) that sets the databucket's live value to
`undefined`, not `[]` (`databucketActions` in `@mockoon/commons-server`). A
follow-up `GET` on a bucket in that state returns an **empty response body**,
which is not valid JSON — it breaks `response.json()` in every reader above,
including `app/demo.py::payment_count`. The bucket-level `PUT` ("update all
items") sets the databucket's value to exactly the request body, so `PUT`
with `[]` deterministically leaves the bucket as a parseable empty array, and
needs no changes to this JSON file — the CRUD bucket already exposes the
route for free.

```bash
curl -s -X PUT http://localhost:3001/payments -H "Content-Type: application/json" -d '[]'
curl -s http://localhost:3001/payments   # -> []
```

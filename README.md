# Buy or Wait? — AI Financial Affordability Agent

Solution to the **HackerRank Orchestrate** (September 2026) "Buy or Wait?"
challenge: for every purchase or payment request, decide whether the user
should pay in full, pay partially, use an available installment option,
wait, or not proceed — safely, deterministically, and grounded in the
user's actual financial position.

See [`problem_statement.md`](./problem_statement.md) for the full
participant-facing spec this solution implements, and
[`docs/architecture.md`](./docs/architecture.md) /
[`docs/decision_policy.md`](./docs/decision_policy.md) for the detailed
design and the literal rulebook.

## Problem Overview

Given a user's financial profile, their historical/pending/scheduled
financial events, dated exchange rates, per-request payment options, and
optional supporting messages/images, produce one prediction row per request
in `dataset/requests.csv`:

```text
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,
payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

A recommendation is only safe if the user can complete the full plan, keep
covering essential/recurring commitments, and never drop below their
`minimum_balance_to_keep` at any point in the forecast window.

## Solution Architecture

The pipeline is **deterministic-first**: every dollar-and-date decision
(`amount_safe_to_pay`, dates, plan totals) is plain `Decimal` arithmetic.
The only role an LLM/VLM ever plays is turning unstructured text or images
into typed facts — it never performs the financial calculation itself.

```
CSV inputs (profiles, events, rates, payment options, messages, images)
        │
        ▼
[1] Evidence normalization        code/buyorwait/evidence.py
[2] Financial state reconstruction code/buyorwait/state.py
[3] 90-day cash-flow forecast      code/buyorwait/forecast.py
[4] Candidate plan generation      code/buyorwait/planner.py
[5] Plan ranking                   code/buyorwait/planner.py
[6] Deterministic validation       code/buyorwait/validate.py
        │
        ▼
    output.csv
```

### 1. Evidence handling (messages & images)

- A blank `financial_events.amount` is resolved via `images.csv` →
  `related_event_id` → a per-image extraction, resolved once during
  development and cached in `code/cache/evidence_cache.json`. A blank
  amount is never treated as zero.
- Messages are classified into a fixed taxonomy of financial facts (salary
  change, salary-date shift, contract/employment ended, rent increase,
  pending refund, unrealized investment move, scam/phishing, etc.) via
  deterministic template matching.
- Untrusted text/image content is only ever converted into a typed fact
  record; it never executes as an instruction and never overrides the
  challenge's decision rules.
- If evidence isn't in the shipped cache, `code/buyorwait/llm_client.py`
  can call the Anthropic API (`ANTHROPIC_API_KEY`) as a fallback, with full
  token/cost accounting. Without a key, the pipeline still runs end to end
  using a conservative deterministic fallback.

### 2. Financial state reconstruction

- Converts every event to the user's `home_currency` using the fixed,
  dated `exchange_rates.csv`.
- Drops cancelled/failed/duplicate-representation/unrealized rows and
  pending credits (bonuses, commissions, refunds, investment gains are
  never counted until settled); reserves pending/scheduled debits.
- Detects recurring streams per `(user, category)` from settled history,
  supporting multiple independent streams within one category (split by
  amount tier when a category legitimately contains more than one
  cadence). The forward-projection interval is the chain's own observed
  median gap between occurrences, not a coarse cadence bucket.
- Applies evidence facts as overrides on top of detected streams (e.g. a
  salary-increase message changes the projected amount from its effective
  date; a "contract ended" message stops future salary projection),
  resolving conflicts by certainty rank (confirmed over estimated).

### 3. 90-day safety check

- Day-by-day simulation of the home-currency balance from `request_date`
  across a 90-day horizon, combining the starting balance, discrete
  confirmed future events, and projected recurring occurrences (skipping a
  projection when a real confirmed event already covers that date).
- `amount_safe_to_pay(day)` is the largest amount removable on that day
  such that no later simulated balance drops below
  `minimum_balance_to_keep`, computed via a suffix-minimum over the
  forecast checkpoints.
- `earliest_date_for_full_payment` is the first day in the window where
  the full requested amount is safe as one payment.

### 4. Payment-plan generation and ranking

- Generates one candidate per option: `full_payment`, `partial_payment`,
  `installments` (one per eligible supplied payment option), `wait`, and
  `not_recommended` — each gated by the user's
  `payment_methods_user_will_consider`, `max_installment_months`, and
  `allows_partial_payment`, and each re-checked against the 90-day safety
  simulation for that specific plan's cash outflow.
- Spending-change candidates try stopping or reducing flexible, non-protected
  recurring expenses (in a category the user permits) to make an
  otherwise-unsafe request safe, up to three changes.
- Candidates are ranked by the problem statement's stated preference order:
  meets deadline → no spending changes → lowest total paid → earliest start →
  fewest payments → lowest payment-option id.

### 5. Deterministic validation

Every output row is re-checked before being written: field bounds and enum
values, chronological and summed payment-plan correctness, installment
schedule match against a supplied option, spending-change legality
(flexible/permitted events only), and a 90-day minimum-balance
re-simulation of the recommended plan.

## Directory Structure

```text
.
├── AGENTS.md                          Agent/session logging rules
├── problem_statement.md               Full challenge specification
├── README.md                          This file
├── code/
│   ├── main.py                        Entry point
│   ├── README.md                      Solution-code quick reference
│   ├── cache/evidence_cache.json      Pre-built message/image extraction cache
│   └── buyorwait/
│       ├── io_data.py                 Typed CSV loading
│       ├── fx.py                      Dated currency conversion
│       ├── evidence.py                Message/image -> typed fact extraction
│       ├── llm_client.py              Optional Anthropic fallback + usage accounting
│       ├── state.py                   Recurring-stream detection + evidence overrides
│       ├── forecast.py                90-day simulation, safe amount, earliest date
│       ├── planner.py                 Candidate plans, spending-change search, ranking
│       ├── validate.py                Pre-write row validation
│       ├── format_utils.py            Amount/date/explanation formatting
│       └── pipeline.py                Orchestrates one request -> one output row
├── docs/
│   ├── architecture.md                Detailed pipeline design
│   ├── decision_policy.md             Literal rulebook implemented
│   └── engineering_report.md          Engineering notes
├── evaluation/
│   ├── evaluate.py                    Scores output against the 25 solved samples
│   ├── edge_cases.py                  Property/regression test suite
│   └── usage_report.md                Token usage & cost for the final full-dataset run
├── dataset/                            Input data (see problem_statement.md)
└── output.csv                          Generated predictions (repo root)
```

## Running The Solution

Requirements: Python 3.9+ (standard library only for the core pipeline).
Optional: `pip install anthropic` with `ANTHROPIC_API_KEY` set in the
environment — only needed if evaluation encounters a blank-amount image or
message outside the shipped cache; the pipeline runs fully deterministically
without it.

From the repository root:

```bash
python3 code/main.py
```

This reads input from `dataset/`, writes predictions to `output.csv` at the
repository root, and refreshes `evaluation/usage_report.md`.

Optional flags:

```bash
python3 code/main.py --dataset-dir dataset --output output.csv
```

### Validation

```bash
python3 evaluation/evaluate.py     # scores against the 25 solved samples
python3 evaluation/edge_cases.py   # property/regression test suite
```

## Output Schema

| Column | Meaning |
|---|---|
| `request_id` | The request being answered |
| `amount_safe_to_pay` | Largest amount safe to pay on `request_date` before optional spending changes; `0 <= amount_safe_to_pay <= requested_amount` |
| `affordability_status` | `affordable_now`, `affordable_with_plan`, `affordable_later`, or `not_affordable` |
| `recommended_payment_method` | `full_payment`, `partial_payment`, `installments`, `wait`, or `not_recommended` |
| `payment_plan` | Chronological `YYYY-MM-DD:amount` entries joined by `\|`, or `none` |
| `earliest_date_for_full_payment` | Earliest date the full amount is forecast safe as one payment; empty if never safe within the forecast |
| `spending_changes_needed` | Up to three `stop:<event_id>` / `reduce_to:<event_id>:<amount>` actions joined by `\|`, or `none` |
| `decision_explanation` | Concise, grounded explanation of the recommendation |

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | No | Only used as a fallback to extract evidence (blank-amount images, messages) not already present in `code/cache/evidence_cache.json`. Never read from source; the pipeline runs without it. |

No secrets are committed to this repository.

## Token Usage & Cost Reporting

See [`evaluation/usage_report.md`](./evaluation/usage_report.md) for the
model providers/names used, call counts, input/output token totals and
averages per request, and estimated cost for the final full-dataset run
that produced `output.csv`.

## Chat Transcript Logging

Per [`AGENTS.md`](./AGENTS.md), AI coding sessions on this repo append
per-turn summaries to `log.txt` in the repository root (gitignored — this
is the submitted `chat_transcript`).

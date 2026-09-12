# Architecture — Buy or Wait?

## Design goal

Maximize accuracy on hidden `output.csv` scoring while keeping the money math
fully deterministic and auditable. Every dollar-and-date decision is produced
by plain arithmetic over typed `Decimal` values; nothing that decides
`amount_safe_to_pay`, a date, or a plan total is delegated to a language
model. Models (or, in this submission, one offline extraction pass — see
"On LLM/VLM use" below) are only permitted to turn unstructured text/images
into structured facts that the deterministic engine then consumes like any
other row of `financial_events.csv`.

## Pipeline

```
requests.csv, financial_profiles.csv, financial_events.csv,        (CSV load)
exchange_rates.csv, request_payment_options.csv,
messages.csv, images.csv, media/images/*.png
        │
        ▼
[1] Evidence normalization  (code/buyorwait/evidence.py)
    - Resolve blank financial_events.amount via images.csv → cached
      per-image extraction (evaluation/cache/evidence_cache.json).
    - Classify messages.csv into a fixed taxonomy of financial facts
      (salary change, salary date shift, contract/employment ended,
      rent increase, scam/phishing, internal transfer, pending refund,
      unrealized investment move, ...) via deterministic template
      matching against the templated EN/ID message corpus.
    - Untrusted text never executes as instructions; it only ever
      produces a typed fact record consumed by state.py.
        │
        ▼
[2] Financial state reconstruction  (code/buyorwait/state.py)
    - Convert every event to the user's home_currency using the fixed,
      dated exchange_rates.csv (graph BFS over the available currency
      pairs, exact rate_date match).
    - Drop cancelled/failed/duplicate/unrealized rows and pending
      credits; reserve pending/scheduled debits.
    - Detect recurring streams per (user, category): compute the
      inter-event interval and a conservative projected amount
      (max of last 3 for expenses, min of last 3 for income) from
      settled history; categories without a stable interval are
      treated as one-off and never projected forward.
    - Apply evidence facts as overrides on top of the detected
      recurring streams (e.g. a salary increase message changes the
      projected salary amount from its effective date; "contract
      ended" stops future salary projection; a rent-increase message
      scales the projected rent).
        │
        ▼
[3] 90-day cash-flow forecast  (code/buyorwait/forecast.py)
    - Deterministic day-by-day simulation of the home-currency balance
      from request_date across 90 days, using: the starting
      current_available_balance, discrete confirmed future events
      (scheduled/pending, in-window), and projected recurring
      occurrences (skipping a projection when a discrete confirmed
      event of the same stream already covers that date).
    - amount_safe_to_pay(day) = the largest amount that can be removed
      on that day without any subsequent simulated balance dropping
      below minimum_balance_to_keep.
    - earliest_date_for_full_payment = first day in [request_date,
      request_date+90] where amount_safe_to_pay(day) >= requested_amount.
        │
        ▼
[4] Candidate plan generation  (code/buyorwait/planner.py)
    - full_payment, partial_payment, installments (one candidate per
      supplied, eligible payment_option row), wait, not_recommended —
      each gated by payment_methods_user_will_consider,
      max_installment_months, allows_partial_payment, and the 90-day
      safety check re-run against that specific plan's cash outflow.
    - Spending-change candidates: for each flexible+recurring event
      that is willing-to-stop / willing-to-reduce, try stopping or
      reducing it and re-run the safety check; keep the combination
      (≤3 changes, no stop+reduce on the same event) that makes an
      otherwise-unsafe request safe today.
        │
        ▼
[5] Plan ranking  (code/buyorwait/planner.py: rank_candidates)
    - Exactly the §Choosing Between Safe Plans order from
      problem_statement.md: deadline met → no spending changes →
      lowest total paid → earliest start → fewest payments → lowest
      payment_option_id.
        │
        ▼
[6] Deterministic validation  (code/buyorwait/validate.py)
    - Structural + financial re-check of every output row before it is
      written: bounds, enum values, chronological/summed payment
      plans, installment-schedule match, spending-change legality,
      90-day minimum-balance re-simulation of the *recommended* plan.
        │
        ▼
output.csv
```

## Module map

| File | Responsibility |
|---|---|
| `code/buyorwait/io_data.py` | Typed CSV loading (Decimal amounts, parsed dates). |
| `code/buyorwait/fx.py` | Dated currency graph + conversion. |
| `code/buyorwait/evidence.py` | Message taxonomy + blank-amount image resolution + cache. |
| `code/buyorwait/llm_client.py` | Optional Anthropic-backed extractor for evidence not in the pre-built cache, with token/cost accounting. Only used for extraction, never for arithmetic. |
| `code/buyorwait/state.py` | Per-user recurring-stream detection + evidence overrides. |
| `code/buyorwait/forecast.py` | 90-day simulation, safe-amount and earliest-date calculation. |
| `code/buyorwait/planner.py` | Candidate generation, spending-change search, ranking. |
| `code/buyorwait/validate.py` | Deterministic pre-write validation. |
| `code/buyorwait/format_utils.py` | Amount formatting, explanation templates. |
| `code/buyorwait/pipeline.py` | Orchestration for one request → one output row. |
| `code/main.py` | Entry point: load, run all requests, write `output.csv`, write `evaluation/usage_report.md`. |

## On LLM/VLM use

The dataset ships exactly 16 blank-amount events (each with exactly one
linked image) and 216 short, heavily templated messages. Both were processed
once, by hand, during development (acting as the extraction model) and cached
to `code/cache/evidence_cache.json`. At run time the pipeline reads this cache
first; `code/buyorwait/llm_client.py` exists to call the real Anthropic API
(via `ANTHROPIC_API_KEY`) for any blank-amount event or message that is *not*
in the cache, so the system still degrades gracefully to a live call — with
full usage accounting — if the hidden evaluation set contains new evidence.
If no API key is configured and the evidence is not cached, the system falls
back to a conservative deterministic rule (treat an unresolvable blank amount
as the *largest* plausible reading available, or ignore an unclassifiable
message) rather than crashing, per the "keep behavior deterministic where
possible" and "must be runnable from the terminal" constraints.

No model, cached or live, ever computes a balance, a payment amount, or a
date — those are §6.3-compliant Decimal arithmetic in `forecast.py` and
`planner.py`.

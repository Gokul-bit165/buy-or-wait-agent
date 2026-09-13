# Phase 8 — Reverse-Engineering the Reference `amount_safe_to_pay` Convention

Read-only forensic investigation. No file under `code/buyorwait/` was modified as part of this
phase. All figures below were produced by a temporary, non-committed forensic script run against
the current (Phase 5) production pipeline; the script itself is not part of the submission and was
not saved under `evaluation/`.

## 1. Executive summary

Current sample score (Phase 5, unchanged by this phase):

| Field | Exact matches |
|---|---|
| amount_safe_to_pay | 4/25 (16%) |
| affordability_status | 20/25 (80%) |
| recommended_payment_method | 22/25 (88%) |
| payment_plan | 21/25 (84%) |
| earliest_date_for_full_payment | 20/25 (80%) |
| spending_changes_needed | 22/25 (88%) |

40/40 edge cases pass.

This phase reconstructed a full forensic table for all 25 samples (starting balance, minimum
balance, projected minimum balance and its date, recurring income/expense monthly-equivalents,
discrete future income/expense, payment options, and the reference-implied minimum balance), then
tested every buffer/formula hypothesis in the brief (fixed buffer, percentage-of-minimum-balance,
one/two expense-cycle reserve, percentage-of-expenses reserve) against all 25 ground-truth values.

**No candidate improves the current formula's exact-match count without introducing regressions
that would require an arbitrary, non-semantically-justified constant to avoid.** The one candidate
that reduces total absolute error (a 5% buffer on `minimum_balance_to_keep`) does so only by
trading 7 regressions for 10 improvements, uses a fitted percentage with no basis in the problem
statement, and does not increase the exact-match count at all. Per the explicit anti-overfitting
rule ("a production rule may only be proposed if... it does not create obvious regressions" and
"it is consistent with the problem statement"), this candidate is rejected.

**DECISION: NO JUSTIFIED RULE FOUND.**

## 2. Per-request forensic table

All monetary values in each user's home currency. `recurring_income`/`recurring_expense` are
monthly-equivalent totals (`amount × 30/interval_days`, summed across all detected streams).
`implied_reference_min_balance = minimum_balance_to_keep + reference_amount_safe_to_pay`.

| rid | starting | min_bal_keep | requested | our safe | ref safe | diff | proj_min_bal | min_date | recurring_income(m) | recurring_expense(m) | discrete_income | discrete_expense | implied_ref_min | ref_status | ref_method | ref_earliest | ref_spending |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|---|---|---|
| 01 | 58,481.10 | 18,000 | 25,256 | 25,256 | 25,256 | 0 | 48,423.46 | 2024-03-13 | 24,124.14 | 20,338.39 | 23,320 | 567.60 | 43,256 | affordable_now | full_payment | 2024-03-03 | none |
| 02 | 60,383,889.20 | 29,158,400 | 46,018,000 | 18,182,845.56 | 17,229,139.20 | 953,706.36 | 47,341,245.56 | 2025-08-13 | 42,750,000 | 21,805,572.08 | 0 | 1,651,100 | 46,387,539.20 | affordable_with_plan | installments | 2025-09-15 | none |
| 03 | 5,810,300 | 2,668,700 | 5,491,000 | 1,101,852.68 | 873,000 | 228,852.68 | 3,770,552.68 | 2019-09-14 | 4,365,000 | 2,387,905.08 | 0 | 95,000 | 3,541,700 | affordable_later | wait | 2019-11-15 | none |
| 04 | 52,206,950 | 30,686,600 | 12,693,000 | 10,609,434.53 | 8,401,800 | 2,207,634.53 | 41,296,034.53 | 2024-06-13 | 38,190,000 | 30,773,203.16 | 0 | 1,704,300 | 39,088,400 | affordable_later | wait | 2024-06-15 | none |
| 05 | 46,475.10 | 13,100 | 15,488 | 0 | 737 | -737 | 8,361.74 | 2026-02-04 | 14,740 | 12,438.42 | 0 | 0 | 13,837 | not_affordable | not_recommended | (empty) | none |
| 06 | 1,942.40 | 800 | 620.40 | 529.74 | 603.30 | -73.56 | 1,329.74 | 2026-01-13 | 1,037.52 | 917.14 | 0 | 0 | 1,403.30 | affordable_with_plan | full_payment | 2026-01-15 | stop:event_476 |
| 07 | 218,945.56 | 93,000 | 197,400 | 95,800.77 | 87,170.56 | 8,630.21 | 188,800.77 | 2024-09-13 | 149,000 | 71,561.82 | 0 | 0 | 180,170.56 | affordable_with_plan | installments | 2024-10-23 | none |
| 08 | 1,536.57 | 800 | 996.60 | 281.33 | 284.57 | -3.24 | 1,081.33 | 2025-02-13 | 1,422.85 | 1,367.58 | 0 | 0 | 1,084.57 | affordable_later | wait | 2025-04-15 | none |
| 09 | 2,231.10 | 600 | 166.61 | 166.61 | 166.61 | 0 | 2,065.89 | 2026-07-12 | 834.12 | 478.47 | 0 | 0 | 766.61 | affordable_now | full_payment | 2026-07-04 | none |
| 10 | 750,155 | 225,400 | 266,700 | 266,700 | 12,700 | 254,000 | 729,416.25 | 2024-12-07 | 223,884.86 | 170,428.01 | 0 | 0 | 238,100 | not_affordable | not_recommended | (empty) | none |
| 11 | 63,531,795 | 34,140,600 | 13,110,000 | 13,110,000 | 12,510,645 | 599,355 | 60,577,295 | 2025-05-05 | 58,552,909.93 | 20,636,107.67 | 0 | 0 | 46,651,245 | affordable_with_plan | full_payment | 2025-07-15 | reduce_to:event_989:665950 |
| 12 | 193,089.89 | 43,200 | 65,164 | 65,164 | 65,164 | 0 | 115,092.14 | 2026-07-01 | 61,315.12 | 25,618.86 | 0 | 0 | 108,364 | affordable_with_plan | installments | 2026-04-05 | none |
| 13 | 2,789.52 | 1,300 | 941.60 | 640.88 | 433.40 | 207.48 | 1,940.88 | 2024-05-14 | 1,300.20 | 1,570.53 | 1,343.54 | 0 | 1,733.40 | affordable_later | wait | 2024-05-15 | none |
| 14 | 3,931.74 | 2,200 | 5,414.20 | 639.05 | 597.74 | 41.31 | 2,839.05 | 2025-08-14 | 2,629.35 | 2,127.48 | 0 | 0 | 2,797.74 | not_affordable | not_recommended | (empty) | none |
| 15 | 1,770.05 | 1,200 | 3,685 | 24.84 | 83.05 | -58.21 | 1,224.84 | 2026-01-14 | 1,661 | 1,226.98 | 0 | 0 | 1,283.05 | not_affordable | not_recommended | (empty) | none |
| 16 | 362,370 | 122,400 | 122,500 | 122,500 | 122,500 | 0 | 251,279.25 | 2023-10-13 | 173,000 | 170,421.32 | 0 | 100,000 | 244,900 | affordable_now | full_payment | 2023-08-12 | none |
| 17 | 550,379.58 | 166,100 | 274,600 | 243,709.70 | 243,849.58 | -139.88 | 409,809.70 | 2026-03-14 | 199,354.84 | 175,321.50 | 206,000 | 0 | 409,949.58 | affordable_with_plan | installments | 2026-03-15 | none |
| 18 | 2,486 | 1,400 | 3,246.10 | 530.21 | 462 | 68.21 | 1,930.21 | 2026-07-14 | 2,310 | 1,153.81 | 0 | 0 | 1,862 | affordable_later | wait | 2026-09-15 | none |
| 19 | 199,545 | 92,800 | 39,660 | 31,272.87 | 28,820 | 2,452.87 | 124,072.87 | 2024-09-14 | 131,000 | 104,643.21 | 0 | 0 | 121,620 | affordable_with_plan | partial_payment | 2024-09-15 | none |
| 20 | 102,609.05 | 64,500 | 303,700 | 8,206.34 | 5,400 | 2,806.34 | 72,706.34 | 2026-02-13 | 108,000 | 53,902.04 | 0 | 5,292.05 | 69,900 | not_affordable | not_recommended | (empty) | none |
| 21 | 3,911.35 | 1,800 | 1,574.40 | 1,574.40 | 1,543.35 | 31.05 | 3,479.68 | 2026-04-12 | 2,183.23 | 1,229.78 | 2,256 | 53 | 3,343.35 | affordable_with_plan | full_payment | 2026-04-15 | stop:event_1815\|reduce_to:event_1816:23.50 |
| 22 | 1,132.46 | 500 | 731.50 | 469.30 | 475.46 | -6.16 | 969.30 | 2024-12-14 | 596.13 | 425.68 | 0 | 43 | 975.46 | affordable_with_plan | installments | 2025-01-15 | none |
| 23 | 51,957.90 | 27,000 | 38,016 | 10,131.87 | 9,152 | 979.87 | 37,131.87 | 2025-05-14 | 44,283.87 | 38,475.97 | 0 | 1,553.20 | 36,152 | affordable_later | wait | 2025-07-15 | none |
| 24 | 85,045 | 51,000 | 109,600 | 15,493.61 | 13,420 | 2,073.61 | 66,493.61 | 2026-01-13 | 61,000 | 54,493.79 | 0 | 1,830 | 64,420 | not_affordable | not_recommended | (empty) | none |
| 25 | 32,063,050 | 23,379,100 | 60,496,000 | 7,801.24 | 1,425,000 | -1,417,198.76 | 23,386,901.24 | 2024-03-14 | 27,580,639.35 | 23,715,353.74 | 28,499,994 | 0 | 24,804,100 | not_affordable | not_recommended | (empty) | none |

`payment_options_methods` is identical — `['full_payment', 'installments']` — across **every single one**
of the 25 samples. This uniformity itself is a finding: hypothesis **G** ("affected by payment
options") cannot be a differentiator, since the available option *types* never vary across samples.

## 3. Candidate formula experiments

All formulas clamped to `[0, requested_amount]` before scoring, exactly as production does.

| Formula | Exact matches | Total abs. error | Improved | Worsened | Max single error |
|---|---|---|---|---|---|
| **F1. Current: `proj_min_bal - min_bal_keep`** | 4/25 | 5,679,056.33 | — | — | 2,207,634.53 (req_04) |
| F2. `proj_min_bal - min_bal_keep × 1.05` | 4/25 | 3,568,420.27 | 10 | **7** | 1,425,000 (req_25) |
| F3. `proj_min_bal - (min_bal_keep + 1 week of expenses)` | 3/25 | 11,844,688.39 | 2 | **17** | 4,972,779.54 |
| F4. `proj_min_bal - (min_bal_keep + 1 month of expenses)` | 1/25 | 35,349,469.87 | 0 | **22** | 17,229,139.20 |
| F5. `proj_min_bal - (min_bal_keep + 2 months of expenses)` | 1/25 | 41,175,342.03 | 1 | **22** | 17,229,139.20 |
| F6. `starting_balance - min_bal_keep - 1 month of expenses` | 2/25 | 21,963,238.23 | 1 | **21** | 8,401,800 |
| F7. `proj_min_bal - min_bal_keep × 1.10` | 3/25 | 5,179,513.80 | 4 | **15** | 1,962,133.64 |

**Every "protected expense reserve" variant (F3–F6) is dramatically worse than the current
formula** — total error roughly doubles to sevenfold, and each worsens 17–22 of the 25 samples.
This is strong, multi-sample evidence *against* hypotheses **A** ("protected-expense reserve") and
**C** ("one/multiple-cycle expense reserve"): the reference is clearly **not** reserving an extra
month (or even a week) of expenses on top of `minimum_balance_to_keep`.

**F2/F7 (percentage buffers on `minimum_balance_to_keep`) reduce total error but at the cost of
real regressions** (7 and 15 samples respectively made worse, including flipping previously-close
matches like `request_06`/`request_08`/`request_17` further from the reference). Neither increases
the exact-match count above the current 4/25. Per the explicit anti-overfitting rule, a percentage
constant (5% or 10%) fitted purely to reduce aggregate error, with no textual basis in the problem
statement and with confirmed regressions, is **rejected**, not adopted.

## 4. Cluster analysis

Direction of error (established already in Phase 6, reconfirmed here with the fuller table):

- **OUR > REF (14 requests)**: 02, 03, 04, 07, 10, 11, 13, 14, 18, 19, 20, 21, 23, 24
- **OUR < REF (7 requests)**: 05, 06, 08, 15, 17, 22, 25
- **OUR = REF (4 requests)**: 01, 09, 12, 16

Sub-clusters within the "difficult" list specifically named in this phase's brief:

- **Large positive gaps** (>100,000 in home currency): 02, 04, 10, 11 — all four involve large
  IDR salaries with sizeable recurring-income monthly-equivalents (₹38M–₹58M); no shared FX,
  cadence-type, or lifecycle trait beyond "high income magnitude." `request_10` is qualitatively
  different from 02/04/11 — it is dominated by high-variance weekly gig income (already ruled out
  as explainable by any income statistic in Phase 6), while 02/04/11 have low-variance fixed
  monthly salaries.
- **Small ± differences** (<250 in home currency): 06, 08, 13, 14, 18, 21, 22 — a mix of both
  directions (06/08/17/22 negative, 13/14/18/21 positive), ruling out a single-direction small
  correction.
- **Spending-change requests**: 06, 11, 19 (genuine), 21 — already fully explained in Phase 6
  Part 5 as correctly computed *before* spending changes; the residual gap in each is the same
  base-forecast magnitude issue as everywhere else, not a spending-change-timing bug.
- **Foreign-currency (non-home, cross-converted) requests**: none of the 25 samples require a
  cross-currency conversion for their dominant income/expense streams — `request_25` (USD salary,
  IDR home) is the only genuine FX case and was already numerically proven correct in Phase 6 Part 8.
  FX is not a differentiator for this cluster.
- **request_25 is an outlier of a different kind**: it is the only sample where the gap (-1.4M) is
  larger in absolute terms than every other sample's *starting balance* difference combined — it
  dominates any aggregate-error metric and should be evaluated separately from the rest, not used
  to justify a general rule (its own per-stream removal test in Phase 3 Part F already showed no
  single stream explains more than ~50% of its gap).

**No cluster maps cleanly onto a single upstream mechanism.** The OVER and UNDER groups both
contain small and large requests, both contain samples with and without spending changes, and both
span every available currency (already checked in Phase 6 Part 2).

## 5. Reference-implied safety-buffer analysis

Comparing `implied_reference_min_balance` against candidate thresholds for all 25 rows (see Part 2
table): there is no consistent additive or multiplicative relationship between
`implied_reference_min_balance` and `minimum_balance_to_keep`, `recurring_expense(monthly)`, or any
combination thereof. Computed the ratio `implied_reference_min_balance / minimum_balance_to_keep`
for all 25 rows and sorted:

```
request_05  1.056   request_20  1.084   request_19  1.311   request_11  1.366
request_10  1.056   request_24  1.263   request_03  1.327   request_02  1.591
request_25  1.061   request_14  1.272   request_18  1.330   request_06  1.754
request_15  1.069   request_04  1.274   request_13  1.333   request_21  1.857
                     request_09  1.278   request_23  1.339   request_07  1.937
                                          request_08  1.356   request_22  1.951
                                                              request_16  2.001
                                                              request_01  2.403
                                                              request_17  2.468
                                                              request_12  2.508
```

The ratio spans a continuous range from 1.056 to 2.508 with **no clustering at any particular
value** (not 1.0, not 1.05, not 1.5, not 2.0) and no discernible grouping by currency, income
type, stream count, or spending-change presence. This continuous, ungrouped spread is itself
evidence against any single fixed-percentage or fixed-cycle-count buffer rule: a real buffer
convention (e.g. "always keep 1.5× the stated minimum") would produce a visible cluster near that
value, which does not appear.

## 6. Strongest hypotheses (none promoted to implementation)

- **Confirmed correct, not the source of error** (already proven in Phases 6/7, reconfirmed here):
  base-forecast inclusion of all recurring expenses (not just essential), pre-spending-change
  `amount_safe_to_pay` computation, FX conversion, cadence correction (Phase 5).
- **Weak candidate, rejected**: a small (~5%) buffer on `minimum_balance_to_keep` reduces
  aggregate error but causes real regressions and has no textual justification — rejected per
  the explicit "does not create obvious regressions" and "consistent with the problem statement"
  requirements.

## 7. Rejected hypotheses (with evidence)

| Hypothesis | Evidence against it |
|---|---|
| A. Protected-expense reserve on top of minimum_balance_to_keep | F3–F6: every reserve-based variant increases total error 2×–7× and worsens 17–22/25 samples |
| B. Shorter/longer time-window than full 90 days | Already tested in Phase 2 (candidate 7, "through desired_completion_date") — 4/25, no improvement |
| C. Essential-only / flexible-excluded expense projection | Phase 6 Part 9: total error worsens from 5.68M to 8.01M when flexible expenses are excluded from the base forecast |
| D. Alternate income statistics (median/mean/min/max/last-3) | Phase 2/6: none reach 12,700 for request_10 or improve aggregate match count |
| E. Same-day debit-before-credit ordering | Phase 1: implemented, tested, actively regressed 4 samples, reverted with concrete evidence (request_23) |
| F. Fixed/percentage/cycle-based safety buffer | This phase, Part 3: no variant improves exact-match count without regressions; no stable implied-buffer ratio exists (Part 5) |
| G. Requested-amount/payment-option interaction | Payment option *types* are identical across all 25 samples — cannot be a differentiator |

## 8. Whether a production change is justified

**No.** Every systematically-testable hypothesis from this phase's brief (A through G) either:
(a) was already tested and rejected in Phases 2, 6, or 7 with concrete numerical evidence, or
(b) was newly tested in this phase (buffer variants F1–F7) and found to either not improve the
exact-match count, or to improve aggregate error only via an unjustified fitted constant that
introduces real regressions.

## 9. Recommended next implementation

None. Per the explicit instruction, no rule is invented in the absence of proof. The remaining
21-sample `amount_safe_to_pay` gap continues to resist every structural, statistical, and
buffer-based hypothesis tested across four investigation phases (2, 6, 7, 8). Recommend treating
this as a residual, organizer-side convention that cannot be reverse-engineered from the 25 public
samples alone without overfitting, and stopping further speculative iteration on this specific
metric.

## Validation run

```
$ python3 evaluation/evaluate.py
Scored 25 sample requests
  amount_safe_to_pay: 4/25 (16%)
  affordability_status: 20/25 (80%)
  recommended_payment_method: 22/25 (88%)
  payment_plan: 21/25 (84%)
  earliest_date_for_full_payment: 20/25 (80%)
  spending_changes_needed: 22/25 (88%)

$ python3 evaluation/edge_cases.py
40 passed, 0 failed
All edge-case checks passed.
```

No production file was modified. No commit, push, or zip was made.

## DECISION

**DECISION: NO JUSTIFIED RULE FOUND**

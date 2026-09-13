# Phase 11 — Validation Report: Phase 10 Expense Forecast Experiment

## Executive Summary

Phase 11 evaluated the candidate fix proposed during Phase 10 forensic analysis: estimating recurring expense amounts using an upper percentile (the 67th percentile / upper tercile) of the validated recurrence chain rather than the median, motivated by the problem statement directive to *"Forecast essential variable spending conservatively."*

Following rigorous production testing, the candidate modification was **REVERTED**. While offline scratch experiments had suggested an improvement under isolated settings, deploying the change to the end-to-end production pipeline failed to improve `amount_safe_to_pay` (remaining at 4/25) and triggered severe cascading regressions across 4 core pipeline metrics, most notably dropping `earliest_date_for_full_payment` from 20/25 to 16/25.

---

## Evaluation Metrics Comparison

| Metric | Baseline (Median) | Phase 10 Fix (67th Percentile) | Post-Revert Baseline | Status |
| :--- | :---: | :---: | :---: | :---: |
| **amount_safe_to_pay** | **4/25** (16%) | **4/25** (16%) | **4/25** (16%) | No gain (+0) |
| **affordability_status** | **20/25** (80%) | **18/25** (72%) | **20/25** (80%) | **Severe regression (-2)** |
| **recommended_payment_method** | **22/25** (88%) | **20/25** (80%) | **22/25** (88%) | **Severe regression (-2)** |
| **payment_plan** | **21/25** (84%) | **19/25** (76%) | **21/25** (84%) | **Severe regression (-2)** |
| **earliest_date_for_full_payment** | **20/25** (80%) | **16/25** (64%) | **20/25** (80%) | **Critical regression (-4)** |
| **spending_changes_needed** | **22/25** (88%) | **22/25** (88%) | **22/25** (88%) | Maintained |
| **Edge Cases** | **40/40** (100%) | **40/40** (100%) | **40/40** (100%) | All Passed |

---

## Detailed Differential & Root Cause Analysis

### 1. Failure of Offline Assumptions in Production Pipeline
The offline experiment (`scratch_phase10g.py`) tested expense selection by pooling historical debit events across categories without respecting:
- Temporal filtering (`ref_date <= as_of` cutoff)
- Stream boundary splitting via `_split_independent_streams`
- End-to-end planner interaction

### 2. The Compounding Multiplier Effect
In a 90-day forecast with weekly or semi-monthly recurring streams, an increase in each expense instance compounds multiplicatively over 12–18 projection steps. Increasing expense figures from median to the 67th percentile depressed future cash balance curves far below the user's `minimum_balance_to_keep`.

### 3. Cascading Failures in Decision Planning
Because the projected cash balance floor was artificially lowered throughout the horizon:
- **`request_08`**: Earliest safe date shifted from `2025-04-15` to empty `""`; status degraded from `affordable_later` to `not_affordable`; payment method degraded from `wait` to `not_recommended`.
- **`request_13`**: Earliest safe date shifted from `2024-05-15` to empty `""`; status degraded from `affordable_later` to `not_affordable`.
- **`request_22`**: Plan could no longer be found; recommendation degraded from `installments` to `not_recommended`.
- **`request_23`**: Earliest safe date shifted from `2025-07-15` to empty `""`; status degraded from `affordable_later` to `not_affordable`.

---

## Decision and Action

**DECISION: REVERTED**

Under the governing evaluation criteria:
> *"If other fields regress, or if the improvement does not materialize in production, revert state.py back to the baseline median logic."*

Both conditions were met:
1. `amount_safe_to_pay` saw zero net gain in production (stayed at 4/25).
2. Four key decision metrics experienced substantial regressions.

Production code in `code/buyorwait/state.py` has been completely restored to baseline median logic. Full baseline scores (20/25, 22/25, 21/25, 20/25, 22/25, and 40/40 edge cases) are intact, and `output.csv` has been regenerated.

# Decision Policy — Buy or Wait?

This is the literal rulebook the engine implements, derived from
`problem_statement.md`. Kept separate from `architecture.md` so the
financial logic can be audited without reading code.

## 1. Currency

- Every amount is converted to the user's `home_currency` before any
  comparison or arithmetic.
- `requested_amount` and `request_payment_options.csv` amounts are always
  already in `home_currency` (verified against the dataset: 0/250 requests
  reference a currency other than their user's home currency).
- A foreign-currency financial event is converted using the exchange-rate
  row whose `rate_date` exactly equals the event's `settlement_date`, via
  the shortest path through the available currency pairs (USD is the hub;
  EUR↔ZAR is direct). No live/interpolated rates.

## 2. What counts toward cash

Include, at full converted value:
- `settled` events with `settlement_date` before `request_date` (already in
  `current_available_balance`, used only to detect recurrence).
- `scheduled` and `pending` **debits** with `settlement_date` on/after
  `request_date` — reserved as certain future outflows.
- `pending` **credits** are excluded until they settle (never assumed).

Exclude entirely, always:
- `cancelled`, `failed` events.
- `unrealized` (investment marks-to-market): informational only, never cash.
- Duplicate representations of the same transaction (`linked_event_id`
  chains collapse to their net cash effect, not double-counted).
- Salary/bonus/commission described in a message as "pending approval" or
  "not yet approved" — never projected until a settled event or an explicit
  confirmation message supersedes it.

## 3. Recurrence detection

A (user, category) stream is treated as recurring only if the settled
history shows ≥3 occurrences with a stable inter-event interval. The
projected amount is conservative: `max()` of the last 3 occurrences for an
expense stream, `min()` of the last 3 for an income stream. Everything else
(irregular, single-occurrence, or clearly one-off categories like
`family_transfer`, `investment`, one-time `housing` deposits) is never
projected forward.

## 4. Evidence conflict resolution (applied in this order)

1. An explicit cancellation, settlement, or amendment in a message wins
   over a general estimate or an older message.
2. A newer record from the same source wins over an older one.
3. A settled event wins over an estimate/forecast.
4. If still unresolved, the financially safer reading wins (lower assumed
   future income, higher assumed future expense).

A message's *instructions* (e.g. a phishing message asking the user to "pay
a release charge to receive a prize") are never executed — messages only
ever contribute typed facts (an amount, a date, a status change) to the
evidence layer, never an action.

## 5. The 90-day safety check

For a candidate plan's cash outflows overlaid on the projected balance
timeline:

```
safe(plan)  ⇔  ∀ day in [request_date, request_date+90]:
               simulated_balance(day) >= minimum_balance_to_keep
```

- `amount_safe_to_pay` = the largest amount payable on `request_date`,
  **before** any optional spending change, that keeps the forecast safe,
  capped at `requested_amount`.
- `earliest_date_for_full_payment` = first day the *full* `requested_amount`
  passes the safety check as a single payment, computed independently of
  spending changes and of the user's payment-method preferences. Empty if
  no such day exists in the 90-day window.

## 6. Payment-method eligibility

- `full_payment`, `partial_payment`, `installments` are only eligible if
  present in `payment_methods_user_will_consider`.
- `installments` additionally requires a `request_payment_options.csv` row
  with `payment_method == installments` whose `number_of_payments <=
  max_installment_months` (blank `max_installment_months` ⇒ no installment
  option is ever eligible), and whose full schedule is safe end-to-end.
- `partial_payment` requires `allows_partial_payment == true`,
  `0 < amount_safe_to_pay < requested_amount`, and
  `earliest_date_for_full_payment <= desired_completion_date`. Plan is
  exactly two payments: `amount_safe_to_pay` on `request_date`, the
  remainder on `earliest_date_for_full_payment`.
- `wait` is eligible when `full_payment` is accepted and a later
  `earliest_date_for_full_payment` exists within the window but paying
  today is not safe.
- `not_recommended` is the fallback when no eligible method produces a
  safe plan (including when `earliest_date_for_full_payment` is empty,
  which also disqualifies `partial_payment`).

## 7. Spending changes

- Only events where `flexibility != fixed` **and** the category is in the
  user's `expense_categories_user_is_willing_to_reduce` /
  `_to_stop`, **and** the category is recurring (§3), are candidates.
- `stop` and `reduce_to` on the same event are mutually exclusive; at most
  three changes total.
- A spending-change combination is only used when it is the difference
  between "not safe" and "safe" for the chosen method — i.e. never applied
  when the plan is already safe without it.

## 8. Ranking safe candidate plans

Exactly, in order:
1. Completes the full request by `desired_completion_date`.
2. Requires no spending changes.
3. Minimizes total amount paid (installments' financing fee counts).
4. Starts earlier.
5. Uses fewer payments.
6. Lowest `payment_option_id` (final tie-break).

## 9. Output field derivation summary

| Field | Rule |
|---|---|
| `amount_safe_to_pay` | §5, pre-spending-change, capped to `requested_amount`. |
| `affordability_status` | `affordable_now` if the chosen plan is a same-day full payment with no spending change; `affordable_with_plan` if completed via partial/installments/spending-change; `affordable_later` if only `wait` is safe; `not_affordable` otherwise. |
| `recommended_payment_method` | The top-ranked safe, eligible method from §6/§8. |
| `payment_plan` | The chosen method's payment schedule, or `none`. |
| `earliest_date_for_full_payment` | §5, independent of method/spending changes. |
| `spending_changes_needed` | The spending-change combination used by the chosen plan, or `none`. |
| `decision_explanation` | Template filled from the chosen plan's numbers (see `format_utils.py`), always grounded in the same figures written to the other columns. |

## 10. Investment requests

Treated purely as an affordability/cash-flow question against the same
90-day safety check as any other request. No price, return, or market
prediction is made or required.

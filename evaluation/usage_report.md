# Usage Report — Buy or Wait?

Final full-dataset run: 250 requests processed in 1.30s.

## Model calls

- Provider: anthropic
- Model: claude-haiku-4-5-20251001
- Calls made this run: 0
- Total input tokens: 0
- Total output tokens: 0
- Total tokens: 0
- Average input tokens / request: 0.00
- Average output tokens / request: 0.00
- Estimated total cost (USD): $0.0000
- Estimated cost per request (USD): $0.000000

## Notes

- The 16 blank-amount financial-event images and all 216 messages in the shipped dataset are covered by the pre-built extraction cache (`code/cache/evidence_cache.json`), produced once during development (see docs/architecture.md). A run against this exact dataset therefore makes 0 live model calls, which is why the totals above are 0 unless `ANTHROPIC_API_KEY` is set and the hidden evaluation set contains evidence outside the cache.
- No model call ever computes a balance, payment amount, or date; model output is restricted to extracting a fact (an amount+currency from an image, or a structured fact from an unmatched message), which the deterministic engine in `code/buyorwait/{state,forecast,planner}.py` then consumes exactly like any other input row.
- Pricing reference: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`), $1.00 / MTok input, $5.00 / MTok output.

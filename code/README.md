# Buy or Wait? — solution

Deterministic-first financial decision engine. See
[`../docs/architecture.md`](../docs/architecture.md) and
[`../docs/decision_policy.md`](../docs/decision_policy.md) for the design and
the literal rulebook it implements.

## Requirements

- Python 3.9+ (standard library only for the core pipeline).
- Optional: `pip install anthropic` and `ANTHROPIC_API_KEY` in the
  environment, only needed if the evaluation set contains a blank-amount
  event image or a message that falls outside the shipped extraction cache
  (`code/cache/evidence_cache.json` — see architecture doc, "On LLM/VLM use").
  Without a key, the pipeline still runs deterministically end to end.

## Run

From the repository root:

```bash
python3 code/main.py
```

This reads `dataset/`, writes predictions to `output.csv` at the repo root,
and refreshes `evaluation/usage_report.md`.

Optional flags:

```bash
python3 code/main.py --dataset-dir dataset --output output.csv
```

## Evaluate

```bash
python3 evaluation/evaluate.py     # scores against the 25 solved samples
python3 evaluation/edge_cases.py   # property/adversarial unit tests
```

## Layout

```
code/
├── main.py                  entry point
├── cache/
│   └── evidence_cache.json  pre-built extraction cache (16 images, all messages)
└── buyorwait/
    ├── io_data.py            typed CSV loading
    ├── fx.py                 dated currency conversion
    ├── evidence.py           message/image -> typed fact extraction
    ├── llm_client.py         optional Anthropic fallback + usage accounting
    ├── state.py              recurring-stream detection + evidence overrides
    ├── forecast.py           90-day simulation, safe amount, earliest date
    ├── planner.py            candidate plans, spending-change search, ranking
    ├── validate.py           pre-write row validation
    ├── format_utils.py       amount/date/explanation formatting
    └── pipeline.py           orchestrates one request -> one output row
```

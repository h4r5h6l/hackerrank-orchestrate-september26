# Token Usage and Cost Analysis

<!-- Fill in after the final full-dataset run that produced output.csv. -->

## Models used

| Provider | Model | Purpose |
|---|---|---|
| e.g. Anthropic | e.g. claude-... | e.g. image amount extraction, message fact extraction, explanation text |

## Aggregate usage

- Total requests processed: TODO
- Total model calls: TODO
- Total input tokens: TODO
- Total output tokens: TODO
- Average tokens per request: TODO

## Per-model breakdown

| Model | Calls | Input tokens | Output tokens | Est. cost |
|---|---|---|---|---|
| | | | | |

## Cost

- Estimated total cost: TODO
- Estimated cost per request: TODO

## Notes

- Deterministic logic (forecast, eligibility, ranking, CSV I/O) runs with zero model calls.
- Model calls are scoped to: (1) OCR/vision extraction of blank event amounts from images, (2) structured fact-extraction from messages, (3) decision_explanation text generation.

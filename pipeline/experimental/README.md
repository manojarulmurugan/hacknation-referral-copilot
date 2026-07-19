# Experimental pipeline archive

`stage4_llm_attempt.py` preserves the retired full-corpus Stage 4 classifier and
partial-cache materializer. It is not an active pipeline dependency.

The attempted design required approximately 9,533 batches for 285,966 distinct
texts at 30 texts per call. The last committed scoped metrics recorded 662
batches attempted, 217 successful live calls, and 445 batch errors over 19,842
distinct texts. Cached failures must not be interpreted as negative clinical
evidence and the cache must not be resumed for production.

The cache remains gitignored at
`data/processed/stage4_llm_cache.jsonl`. The partial capability map and Stage 5
metrics produced from it are superseded by the deterministic Stage 4 run.

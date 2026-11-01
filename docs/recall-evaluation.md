# Offline retrieval diagnostics

Run from the repository root (Python standard library only):

```sh
python vault-guard/eval_recall.py
python vault-guard/eval_recall.py --json
python -m unittest discover -s vault-guard -p 'test_eval_recall.py'
```

The 10 cases in `docs/recall-eval-fixture.json` are **hand-authored synthetic examples**, not private memories, real-user queries, or an unbiased benchmark. They are diagnostic regression examples, not a claim of production accuracy. No external service or LLM is called. The harness creates and deletes a temporary Vault, and never opens the user's configured Vault. The evaluation code and fixture are original to this repository and covered by its MIT license.

## What is measured

All cases call `recall(..., top=3)` directly. This measures retrieval **before** trigger heuristics, packet truncation and answer generation. Finding a negation-containing source does not prove that an agent correctly reasons about negation.

- **Top3 evidence hit**: cases with at least one expected evidence ID in the first three hits / cases with nonempty expected evidence. This is case-level hit rate, not document-level recall@3.
- **Top3 forbidden evidence leak**: cases returning at least one explicitly forbidden ID / cases with a nonempty forbidden list. Lower is better. Labels express the test's desired effective-memory outcome; a failure may be an intentional architectural limitation rather than a bug.
- **No-answer false recall**: cases with an empty expected list that return any result / all empty-expected cases. Lower is better. This includes raw-default exclusion and revoked-record checks, not only unrelated questions.
- **Preferred first hit**: cases whose top result is the preferred project document / cases specifying a preferred first document. Cross-project search is intentional: other project hits are not forbidden.
- **Latency**: one local run per case, median/max reported only. No CI timing threshold; these tiny fixtures do not establish large-Vault performance.

The fixture freezes the ranking date at `2026-08-22`; ranking results should repeat on the same code. Elapsed times vary. Evidence IDs resolve by relative file and section title, not by score thresholds. Raw entries use the writer's standard metadata and a correct body SHA-256.

## Baseline and interpretation

Measured against `f5ac8d7` before effective raw lifecycle filtering:

| Metric | Baseline |
| --- | --- |
| Top3 evidence hit | 6 / 7 |
| Top3 forbidden evidence leak | 3 / 3 |
| No-answer false recall | 1 / 3 |
| Preferred first hit | 2 / 2 |

Inspect individual case results, not just aggregate rates:

- `paraphrase` intentionally exposes a lexical-only limitation: “拷贝留存方案” does not share useful tokens with “备份策略”. Do not rewrite the case just to improve the score.
- `curated-correction` keeps old/new ordinary Markdown summaries. Raw lifecycle filtering does **not** automatically retire curated summaries. Its forbidden old-summary hit remains a documented gap, not a promise to solve it in this iteration.
- `raw-correction` and `raw-tombstone` explicitly enable raw retrieval and check effective-record handling. Default retrieval never includes those raw records.
- Project collision cases expect reranking, not isolation.

The CLI exits successfully even when a diagnostic expectation is missed. CI checks fixture validity, metric arithmetic, and deterministic evaluation; it does not impose a misleading 100% quality gate. Keep baseline and subsequent results tied to the tested revision. Add diverse, independently labeled examples before drawing broader quality conclusions.


## Effective raw lifecycle comparison

On the same unchanged fixture with effective raw lifecycle filtering:

| Metric | Before (`f5ac8d7`) | Effective raw lifecycle |
| --- | --- | --- |
| Top3 evidence hit | 6 / 7 | 6 / 7 |
| Top3 forbidden evidence leak | 3 / 3 | 1 / 3 |
| No-answer false recall | 1 / 3 | 0 / 3 |
| Preferred first hit | 2 / 2 | 2 / 2 |

The improvement is limited to the two explicitly annotated raw lifecycle cases. The ordinary curated-summary leak and lexical paraphrase miss remain. These are ten transparent synthetic examples, not statistically representative quality measurements; do not express the change as a production accuracy percentage.

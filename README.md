# Electoral Evidence Agent — a verifiable-numbers agent, proven on Korean elections

Built for the Meta Global AI Developer Hackathon 2026 (startup track) by HK Chun and
Sechan Tak. The agent answers election questions **only from tool calls to a structured
results database** — never from the model's memory — and every figure cites the election's
archive index page. Output is factual and sourced, with no recommendations or evaluations
of officials, parties, or policy positions.

- **Live demo:** https://app.ohmycodellc.com/kr?lang=en
   - https://app.ohmycodellc.com/kr/agent?lang=en
   - https://app.ohmycodellc.com/kr/agent
- **Demo video:** https://www.youtube.com/watch?v=v14OXlEQy0s
- **Deployed app (UI + TypeScript tool port + Gemini fallback):**
  https://github.com/sechan9999/us-election-insight-hub (`app/kr/agent`, `app/api/agent`)

## What is in this repo (built during the hackathon, Oct 27–28, 2026)

| Path | What it is |
| --- | --- |
| `kr_tools.py`, `tool_schemas.json` | The 4 tools (precinct lookup, swing, turnout, margin) over BigQuery, with deterministic checks and the error contract |
| `agent-prompts-ko-en-v1.md` | Bilingual (KO/EN) system prompts: no-fabrication rules, error contract, tool-call recipes |
| `precinct_build/build_melt.py`, `precinct_build/kr_precinct_load_spec_draft.md` | Builder + load spec for the `kr_precinct` table (646,658 rows, 18th–21st Korean presidential elections, 2012–2025) |
| `sam_pipeline.py`, `docs/sam-pipeline-v1.md` | SAM 3.1 tally-sheet verification pipeline and its design report |
| `eval_harness/` | 100-question bilingual evaluation harness (55 KO / 45 EN) with gold answers, scorer, and sample verifier |
| `guardrail_tests.md` | Tool-layer guardrail tests, including the fixed non-runner swing fabrication bug |
| `docs/agent-core-tools-v1.md` | Tool design + test report (T1–T8) |
| `results/sam_report_live50.json` | SAM evaluation result: 50/50 pages, 218/218 cells matched against published totals |

## What is NOT in this repo (pre-existing work, not claimed as hackathon-built)

- The Electoral Insights Hub platform itself and the 18th–21st tally-sheet archives,
  including the 36,342-page digitization completed Oct 24, 2026.
- The National Election Commission source files behind `kr_precinct` (see the load spec
  for provenance; the built table lives in BigQuery, not in this repo).
- Tally-sheet images and SAM/DocAI per-page caches (bulky; the matched report above is
  the evidence artifact).

## Running the tools

Requirements: Python 3, the Google Cloud SDK (`bq` on PATH), and BigQuery access to a
project holding the `elections.kr_precinct` table (dataset layout in `tool_schemas.json`
and the load spec).

```bash
python3 kr_tools.py turnout --election 21 --district_key "경기도|부천시"
python3 kr_tools.py swing --election_a 19 --election_b 20 --candidate 심상정 --top_n 5
```

## Measured results (honest scope)

- Tools: 8/8 functional tests pass; district-mode swing returns null share and null
  swing for a candidate who did not run in one election (previously fabricated a swing —
  fixed and regression-tested).
- SAM evaluation: 50/50 sampled 21st-election tally sheets compared, 218/218 cells
  matched against published totals, 0 failed. SAM segments table regions; it does not
  isolate columns or numbers on this document type, so figures inside a table are read
  and cross-checked against published totals rather than claimed as SAM-extracted.
- Eval harness: gold answers are computed with tool-identical semantics, so the harness
  tests tool use, formatting, and guardrails — not independent data verification (that is
  the SAM sheet evaluation above).

## Secrets

No API keys, credentials, or tokens are in this repo. The Meta Model API key used by the
live demo lives only in the Cloud Run service environment, and the SAM pipeline reads
credentials from the operator's own Google Cloud authentication.

# Electoral Evidence Agent — a verifiable-numbers agent, proven on Korean elections

Built for the Meta Global AI Developer Hackathon 2026 (startup track) by HK Chun and
Sechan Tak. The agent answers election questions **only from tool calls to a structured
results database** — never from the model's memory — and every figure cites the election's
archive index page. Output is factual and sourced, with no recommendations or evaluations
of officials, parties, or policy positions.

- **Live demo:** https://app.ohmycodellc.com/kr?lang=en
   - https://app.ohmycodellc.com/kr/agent?lang=en
   - https://app.ohmycodellc.com/kr/agent
- **Demo video:** https://youtu.be/JzfRxSYloVo
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
  including the 36,342-page digitization completed Oct 24, 2026. The 18th-election
  archive also includes 73,363 scanned original count documents from all 251
  districts, mirrored with Project Boo's 2012-archive permission and attribution
  (live Oct 24, 2026).
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

## Counting units, bloc keys, and source headers

### Counting units — why the counts differ
- 투표구 (precinct): the basic unit in the NEC result files — 21st: 14,295
  regular precinct rows; 20th: 14,464.
- 관내사전 (early-vote unit): one row per early-vote station, counted
  separately — 21st: 3,554; 20th: 3,510. Subtotal (소계) and total rows are
  excluded from loads, or district aggregates would double-count.
- 개표단위 (counting unit, 19th sorter file only): 13,949 rows with
  행구분 = 투표구. These are ballot-sorter counting units, not precincts;
  the 19th NEC vote file is separate (22,214 rows).
- 구·시·군 (district): administrative district — 18th: 251; 21st: 252;
  20th: 224 collected (26 districts absent, including all of Busan, Jeju,
  and Sejong).
- district_key (harmonized comparison unit): 247 units, identical across
  all four elections. Merged/renamed units are resolved (e.g. 청주시권,
  부천시 3구 합산, 화성시 갑+을 합산, 여주군→여주시, 인천 남구→미추홀구).
  Cross-election comparison is district_key-only.

### Bloc keys
Three blocs, candidate-based (not party-based): 보수 / 진보 / 기타 — no
centrist bloc. Explicit assignments (HK decisions 2026-10-27): 안철수,
유승민, 조원진, 이준석 → 보수; 권영국, 김재연 → 진보; 이재오 → 보수
(conservative lineage); all other candidates → 기타. The full 36-entry
map is BLOC_MAP in kr_tools.py; an unmapped candidate fails loudly
(MISSING_BLOC) instead of vanishing. National bloc shares: 2012 보수
51.56 / 진보 48.02 / 기타 0.42; 2017 52.37 / 47.26 / 0.36; 2022 48.64 /
50.32 / 1.04; 2025 49.49 / 50.40 / 0.10.

### Source CSV headers (wide format)
Every published file is one row per reporting unit with one column per
candidate; the long kr_precinct form is a melt of these columns, not a
published shape. Headers as documented in
`precinct_build/kr_precinct_load_spec_draft.md`:
- 21st (22,686 rows): 시도, 구시군, 읍면동, 투표구, 선거인수, 투표수,
  더불어민주당 이재명, 국민의힘 김문수, 개혁신당 이준석, 민주노동당 권영국,
  무소속 송진호, 계, 무효, 기권.
- 20th (22,752 rows): same shape, candidate columns name-only — 이재명,
  윤석열, 심상정, 오준호, 허경영, 이백윤, 옥은호, 김경재, 조원진, 김재연,
  이경희, 김민찬 — then 계, 무효, 기권.
- 19th sorter (18,137 rows): 행구분, 시도, 구시군, 종류, 읍면동, 투표구,
  계_계, 계_유효소계, 계_문재인, 계_홍준표, 계_안철수, 계_유승민, 계_심상정,
  계_기타, 계_무효, then classifier/unclassified split columns. Load
  filter: 행구분 = 투표구 (13,949 rows).
- 19th NEC votes (22,214 rows): 시도, 구시군, 읍면동, 투표구, 선거인수,
  투표수, 13 candidate columns (더불어민주당 문재인, 자유한국당 홍준표,
  국민의당 안철수, 바른정당 유승민, 정의당 심상정 + 8 more), 계, 무효, 기권.
- 18th sorter (251 rows): #, 시도명, 구시군명, 선거인수, 계_총투표수,
  계_박근혜, 계_문재인, 계_기타후보, 계_무효, then classifier split + age +
  K columns.
- 18th NEC (17,871 data rows): 시도, 구시군, 읍면동, 투표구, 선거인수,
  투표수, 6 candidate columns (새누리당 박근혜, 민주통합당 문재인, 무소속
  박종선·김소연·강지원·김순자), 계, 무효, 기권.
- No file contains a URL column — hence citations are archive index
  pages, not per-figure sheet links. Turnout is computed
  (투표수 / 선거인수), not published as a rate.

## Status

Updated 2026-11-02: 18th-archive mirror figure (73,363 files) added to the
pre-existing work above; demo video is public; portal submission planned
2026-11-03.

- Demo video (public): https://youtu.be/JzfRxSYloVo
- Portal submission: planned 2026-11-03.

## Secrets

No API keys, credentials, or tokens are in this repo. The Meta Model API key used by the
live demo lives only in the Cloud Run service environment, and the SAM pipeline reads
credentials from the operator's own Google Cloud authentication.

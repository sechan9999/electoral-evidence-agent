# Agent core tools v1 — implementation + test report (2026-10-27)

**Status:** Wired and tested live against BigQuery today. Supersedes the
2026-10-26 schemas draft where they differ (see "What changed vs the draft").

## What was built

- **Implementation:** `kr_tools.py` in this folder (CLI: `python3 kr_tools.py <tool> --election …`).
- **Schemas:** `tool_schemas.json` (Muse Spark function-calling contract; adapt the
  envelope at integration, semantics fixed).
- **Tables:** `electoral-hub-510410.elections.kr_precinct` (646,658 rows),
  `district_map` (1,004 rows). Transport: `bq query` subprocess with the egress
  CA bundle; the module resolves the `bq`/`gcloud` paths itself (no shell PATH
  dependency).

## Final semantics (v1)

- **Primary region key is `district_key`** (harmonized unit, 247 keys, identical
  in all four elections). Tools also accept published `(sido, sigungu)` and
  resolve via `district_map`; a bare `sigungu` that matches several districts
  returns `AMBIGUOUS_DISTRICT` with the options (인천 남구 is correctly absent —
  it is 미추홀구 since 2018).
- **precinct_lookup:** district aggregate (all unit types summed) or one precinct.
  Unknown precinct → `PRECINCT_NOT_FOUND`, never silently replaced by the
  district figure.
- **swing_calculation:** district mode (one district_key, full candidate table +
  winner/margin/turnout deltas) or ranking mode (one candidate, district_keys
  ranked). `match_level` is always `district_key` — precinct identity is not
  stable across elections, so precinct-level cross-election matching is not
  attempted. A swing is computed only where the candidate ran in BOTH elections;
  entry/exit districts are excluded by default and counted (`excluded_absent_count`),
  because a 0→85% jump is not a swing.
- **turnout:** `SUM(votes_cast)/SUM(electorate)` over distinct units in scope.
  Early-vote rows carry electorate == votes_cast in the published file (an early
  station has no home electorate); special rows carry their own electorate.
  Summing over all unit types reproduces published turnouts exactly (verified:
  종로구 2017 0.7667 both ways; all four national totals diff 0).
- **margin:** winner, runner-up, margin votes + pp, at precinct, district, or
  national level.
- **Citations:** every output carries the election archive index page(s) (HK
  decision 2026-10-27). Per-figure tally-sheet URLs are NOT promised in v1.
- **Checks (code-enforced):** candidate shares sum to 100% (±0.05pp); margin ==
  winner − runner-up; swing == share_b − share_a (±0.01pp). A failed check
  blocks the figure with `DATA_CHECK_FAILED`.

## Test report (all live, 2026-10-27)

| Test | Call | Result |
|---|---|---|
| T1 precinct_lookup, merged unit | 21, `경기도\|부천시` | Turnout 79.6664%; 이재명 54.5518%, 김문수 36.2808%, 이준석 8.0685%, 권영국 1.0032%, 송진호 0.0957%; shares sum 100.0; published = 3구 (소사/오정/원미) |
| T2 single precinct | 21, 송파구 가락1동제1투 | Electorate 3,268, votes 2,377, turnout 72.7356%; 김문수 65.4691% |
| T3 swing, merged unit | 청주시권 20→21 | 윤석열 48.6322% → 이재명 49.0862%; 이재명 +2.069pp; turnout +3.1035pp; both index citations |
| T4 swing ranking | 심상정 19→20, top 5 loss | 고양시덕양구 −7.1961pp (10.5159→3.3198), 울산 동구 −7.167pp, 울산 북구 −6.8533pp, 당진시 −5.7834pp, 고양시일산동구 −5.5794pp; excluded 0 |
| T4b swing, non-runner | 이재명 19→20 | Empty rows + note: did not run in 2017 (247 excluded) — correct, not a bug |
| T5 turnout | 화성시 21; 전국 21 | 화성시 80.1533% (790,604 / 633,695, 225 units); 전국 79.376% (44,391,871 / 35,236,497) |
| T6 margin | 송파구 21; 가락1동제1투 | District: 김문수 212,094 vs 이재명 191,678, margin 20,416 / 4.4856pp. Precinct: 1,010 / 42.688pp |
| T7 errors | unknown district / precinct / 남구 | NOT_FOUND + 45 suggestions; PRECINCT_NOT_FOUND + 20 suggestions; AMBIGUOUS → 광주/대구/부산/울산 남구 |
| T8 citations in table | DISTINCT citation_url | Exactly the 4 archive index pages |

## Bugs found during testing (all fixed)

1. **Party parse missed 국민의힘/무소속** (old rule required the first token to
   end with 당). Melt rebuilt, BigQuery reloaded; 김문수/국민의힘 and
   송진호/무소속 verified in the table. Lesson: person name = last token.
2. **Subprocess PATH:** `bq` shells out to `gcloud` for auth; the module now
   resolves both binaries and sets PATH itself.
3. **Ranking entry-effect:** first run ranked 이재명 19→20 as 0→85% "gains" in
   광주 — he did not run in 2017. Ranking now excludes absent-in-either
   districts by default (flagged + counted).
4. **bq default max_rows=100** truncated the 247-row ranking and the 1,004-row
   map fetch. `--max_rows=100000` set; BOOL values normalized (bq JSON may
   render them as strings).

## What changed vs the 2026-10-26 schemas draft

- kr_precinct exists now (646,658 rows, not "85,522 rows, TBD"); 19th vote
  source is the NEC results file (22,214 rows), not the sorter file (13,949
  counting units); 18th canonical file is results.xlsx (13,542 regular precincts,
  not 17,021).
- Citations are index pages, not `source_sheet_url`. Demo beat 3 ("show me the
  tally sheet") now opens the archive index page until a sheet-URL join exists.
- Swing ranks district_keys, not precincts; turnout accepts an omitted district
  (= national). Bloc keys, bilingual agent prompts, and the Muse Spark envelope
  adaptation remain open (see tool_schemas.json `open_items`).

## Bloc keys (2026-10-27, HK decisions — implemented same day)

**Scheme:** 3 blocs (보수/진보/기타). No 중도 bloc, because 안철수 (2017,
21.4%) was assigned to 보수. Mapping is by **candidate per election**, not
party: the 20th file has no party column, and the 2017 새누리당 (조원진) is a
different party from the 2012 새누리당 (박근혜) despite the shared name.
Source of truth: `BLOC_MAP` in `kr_tools.py` (36 entries, verified 36/36
against the table — no missing mappings).

| Bloc | Members by election |
|---|---|
| 보수 | 18: 박근혜. 19: 홍준표, 안철수, 유승민, 조원진, 이재오. 20: 윤석열, 조원진. 21: 김문수, 이준석 |
| 진보 | 18: 문재인. 19: 문재인, 심상정. 20: 이재명, 심상정, 김재연. 21: 이재명, 권영국 |
| 기타 | All remaining minors/independents (18: 무소속 4명. 19: 김민찬, 김선동, 장성민, 윤홍식, 이경희, 오영국. 20: 허경영, 오준호, 김민찬, 이경희, 이백윤, 김경재, 옥은호. 21: 송진호) |

HK explicitly assigned 안철수/유승민/조원진/이준석 → 보수, 권영국/김재연 →
진보; the rest were delegated as 기타/보수, applied as 기타 except 이재오 →
보수 (conservative lineage). Note: left minors 김선동/이백윤/오준호 went to
기타 per the 기타/보수 instruction, not 진보 — all ≤0.11%, immaterial to
bloc totals.

**National bloc shares (verified from the table):**

| Election | 보수 | 진보 | 기타 |
|---|---|---|---|
| 18 (2012) | 51.56% | 48.02% | 0.42% |
| 19 (2017) | 52.37% | 47.26% | 0.36% |
| 20 (2022) | 48.64% | 50.32% | 1.04% |
| 21 (2025) | 49.49% | 50.40% | 0.10% |

**Bloc tests (2026-10-27, all live):**

| Test | Result |
|---|---|
| B1 district blocs, 청주시권 20→21 | 보수 48.6974→49.746% (+1.0486pp), 진보 50.01→50.1435% (+0.1335pp), 기타 1.2925→0.1105% (−1.182pp); sums 100.0 |
| B2 ranking 보수 19→20, top 5 gain | 강남구 +8.1675pp (58.9176→67.0851), 서초구 +7.451pp, 과천시 +5.4558pp, 창원시성산구 +4.4922pp, 해운대구 +4.3193pp |
| B3 ranking 진보 19→20, top 3 loss | 강남구 −8.5364pp, 서초구 −7.7544pp, 과천시 −5.8268pp (mirrors B2) |
| B6 ranking 기타 19→20 | 거제시 +1.8657pp, 창원시진해구 +1.5246pp, 울산 동구 +1.4581pp |
| B7 ranking 보수 20→21, top 3 gain | 관악구 +3.8255pp, 화천군 +3.1439pp, 광양시 +2.8628pp |
| B4 map coverage | 36/36 candidates mapped, 0 missing |
| B5 invalid bloc | `INVALID_BLOC` error |

**Bugs found during bloc testing (fixed):** (1) piv JOIN split
(district, candidate) groups so the other election's total_valid came out
NULL — fixed with a `pairs` CTE joining both elections' totals per pair.
(2) Bloc aggregation looked up the bloc for elections the candidate did not
run in (e.g. 문재인 in 2022) — fixed by skipping absent elections (they
contribute 0 to every bloc in that election).

## 20th-election parties filled (2026-10-27, HK-supplied)

The published 20th file has name-only headers, so the table's 20th `party`
column was empty until HK supplied the registration record. Applied via
`PARTY_20` in `build_melt.py` (CSV regenerated, 646,658 rows unchanged) +
one BigQuery DML UPDATE (227,700 rows affected), verified 12/12:

윤석열 국민의힘, 이재명 더불어민주당, 심상정 정의당, 허경영 국가혁명당,
김재연 진보당, 조원진 우리공화당, 오준호 기본소득당, 김민찬 한류연합당,
이경희 통일한국당, 이백윤 노동당, 김경재 신자유민주연합, 옥은호 새누리당.

Registered but withdrew before election day — NO rows, recorded only:
안철수 (국민의당), 김동연 (새로운물결). Bloc mapping is unaffected
(candidate-based). Prompts rule 8 and the swing schema description were
updated to match (20th parties now present, from HK, not from the file).

## Remaining this week (unchanged schedule)

- Thu–Fri 10/29–30: SAM 3.1 tally-sheet sample pipeline (separate track).
- Sat–Sun 10/31–11/01: eval harness (golden questions vs tool outputs) + guardrails.
- Mon 11/02: Hub UI tab + agent-loop integration (uses these schemas).
- Tue 11/03: demo video. Wed 11/04 21:00 ET: submission due (aim 17:00).

## Addendum 2026-10-27 — guardrail fix

District-mode swing for a candidate who did not run in one election
previously emitted `share 0.0%` and a fabricated swing (안철수 2012→2017
종로구 showed +21.8323pp from a 0.0% that never existed). Fixed: absent
side now returns `share: None, swing_pp: None` with `ran_in_a/ran_in_b`
flags; the district table sorts None-swings last. Ranking mode was
already correct (absent excluded by default). Tool schema description
updated. Full record: `guardrail_tests.md`.

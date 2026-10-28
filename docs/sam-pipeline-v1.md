# SAM 3.1 tally-sheet verification pipeline — v1 (2026-10-27)

**Status:** Pipeline built and integration-tested with a mock segmenter.
Live SAM execution is BLOCKED on Meta Model API access (see blockers).
This doc records the design, the test, and exactly what is and is not proven.

## Design

```
sample pages -> fetch sheet JPG from GCS -> SAM segment vote cells
  -> DocAI OCR tokens -> numbers inside SAM boxes -> compare vs published
  NEC totals (kr_precinct) -> match-rate report
```

- **Sample frame:** 21st digitization CSV (`results/digitization.csv`),
  `status=ok` + `nec_matched=1` (15,890 pages), stratified by region, fixed
  seed (20261027). Sample size for the v1 test: 10 pages.
- **Images:** public GCS archive, `gs://k21-tally-archive/<region>/<file_id>/`
  — sheet HTML pages embed the photo as a JPG next to the HTML.
  **Two naming conventions exist:** files with <100 pages use `p-01.jpg`
  (2-digit), files with >=100 pages use `p-001.jpg` (3-digit). The pipeline
  tries both (found by testing: 6/10 first-run images "missing" were the
  3-digit case).
- **OCR:** Document AI, same processor + pattern as the digitization project
  (`digitization_pilot/run_docai.py`). ~$0.005/page at the digitization run's
  realized rate. Raw responses cached under `sam_run_mock/docai_cache/` so
  reruns cost $0. Tesseract is NOT installed and apt cannot fetch it here;
  DocAI is the OCR path (already validated on this exact data at 98.76%).
- **Extraction:** candidate-name anchors + number tokens inside the vote
  column box, same idea as the digitization parser's `cell_value`, with two
  hardenings added during testing: number tokens must lie FULLY inside the
  column box (a digit-merge error came from edge bleed), and when a name
  occurs several times, the anchor whose row actually has numbers in the
  column wins.
- **Ground truth:** kr_precinct published totals (NEC results file), via exact
  candidate-vote vector match within the page's district. A page that matches
  no unit is reported unmatched, never force-matched. The digitization CSV
  is a secondary reference only.
- **Code:** `sam_pipeline.py` in this folder. Backends: `mock` (returns the
  known final-column band x 0.762–0.848 as the "segmented" box) and `meta_api`
  (PROVISIONAL — see blockers).

## What the mock test proves — and what it does not

Proven: fetch, DocAI tokens, anchor matching, number parsing, NEC vector
matching, reporting. NOT proven: SAM's actual box quality — the mock returns
a perfect box by construction, so the mock match rate measures the pipeline
around SAM, not SAM itself. It must never be quoted as a SAM result.

## Blockers for the live SAM run

1. **Meta Model API key / $150 credits:** no key exists in this environment;
   credits were requested but not received as of 2026-10-26 evening. HK owns
   the console/card decision. Key goes in `MODEL_API_KEY` env (never in chat
   or files).
2. **Endpoint + response schema:** dev.meta.ai docs confirm the Model API,
   `MODEL_API_KEY`, and a text-prompt segmentation interface, but the exact
   endpoint path and response schema were not pinned in the docs found so
   far. Set `META_SAM_ENDPOINT` from the official docs and confirm the
   schema on the first live call (the client fails loudly until then).
3. **Cost if live:** SAM API ~$2.5/1,000 images (per 2026-10-27 verification)
   → a 20-page live sample ≈ $0.05, plus DocAI ≈ $0.10. Negligible once
   access exists.

## Test results (mock backend, 10 pages, seed 20261027, 3 runs)

Final run (run 3, after the fixes below): 10/10 images fetched, 10/10
DocAI-processed, 7/10 pages matched to a published unit, **33/33 extracted
cells correct on matched pages (100%)**, 5/10 pages perfect (all 5 cells).

| page | district / precinct | extracted | result |
|---|---|---|---|
| gyeonggi_39112_p051 | 수원시권선구 / 세류2동제2투 | 556/713/109/13/0 | 5/5 perfect |
| jeonbuk_39204_p017 | 전주시완산구 / 서서학동제2투 | 581/109/40/?/1 | 4/5 (권영국 missing) |
| seoul_39059_p078 | 강동구 / 천호제1동제3투 | 609/804/144/28/? | 4/5 (송진호 missing) |
| gangwon_39167_p004 | 홍천군 / 내촌면 | 9311/333/?/10/? | unmatched (see below) |
| incheon_39090_p066 | — / — | 224/294/51/3/0 | 5/5 perfect |
| gwangju_39095_p021 | — / — | 409/125/91/10/2 | 5/5 perfect |
| daegu_39077_p004 | 동구 / 신암2동제1투 | 216/?/12/19/2 | unmatched (see below) |
| ulsan_39108_p060 | — / — | 613/656/179/23/1 | 5/5 perfect |
| busan_39066_p009 | 남구 / 대연제3동제6투 | 149/1164/180/9/2 | unmatched (see below) |
| daejeon_39584_p011 | — / — | 489/806/243/7/2 | 5/5 perfect |

(Columns: 이재명/김문수/이준석/권영국/송진호. `?` = no number found on the
anchor row.)

### The 3 unmatched pages — all extraction errors the guard caught

The unit-match guard refused all three (0 candidate vectors found) instead
of misattributing them. Each error reproduces the digitization project's
own single-engine DocAI error on the same page — i.e. the pipeline is
faithful; the OCR engine is the weak link:

- **gangwon (내촌면):** 이재명 9311 vs final 311 (a stray digit merged in).
  CORRECTION 2026-10-27 (sheet image read): the final column
  (심사·집계부 계) is 311/333/41/10/1, and Gemini-final matches it exactly.
  The 302/317 figures in the first version of this note were the
  분류 (classified) column, misread as the final column. DocAI's 김문수
  333 was CORRECT, not an error. The page still cannot match a published
  unit because it is an early-vote sheet (see the live-25 scope finding
  below), not because its values are wrong.
- **busan (대연제3동제6투):** 이재명 149 vs published 749 (leading digit
  dropped: 7→1). Digitization's DocAI-final also 149; Gemini-final 749.
- **daegu (신암2동제1투):** 김문수 anchor failed entirely; 이준석 12 vs
  published 82. Digitization's DocAI-final is blank for both cells;
  Gemini-final 1204/82.

### The 2 missing cells on matched pages

jeonbuk 권영국, seoul 송진호 — small-value rows where no number token was
found in the column on the anchor row. The other 4 cells on each page still
identified the unit uniquely, and all matched.

### Fixes applied across the 3 runs (and what each taught)

1. **JPG naming:** two conventions (`p-01.jpg` <100 pages, `p-001.jpg`
   >=100 pages) — run 1 "lost" 6/10 images to this; run 2+ fetch 10/10.
2. **District-name quirk:** digitization district names sometimes carry the
   file id (`수원시권선구(39112)`) — stripped before resolving; this turned
   gyeonggi from unresolvable into a perfect page.
3. **Fully-inside-x rule (TRIED AND REVERTED):** requiring number tokens to
   lie fully inside the column box collapsed previously perfect pages
   (gwangju, ulsan) to all-None — DocAI tokens routinely straddle column
   edges. Centre-inside (the digitization parser's rule) is empirically
   better. Anchor scoring (prefer the anchor whose row has numbers) was
   kept; it did not fix the gangwon/daegu misses.
4. **Run-2 lesson recorded:** a "hardening" that is not re-verified against
   the previously passing pages is a regression, not a fix.

### Cost and limits

- DocAI for the 10-page sample: ~$0.05 total (6 pages in run 2; run 3 fully
  cached). BigQuery queries negligible.
- **This is a pipeline test, not a SAM result.** Mock boxes are perfect by
  construction; SAM's real box quality is untested and will only lower the
  numbers above. n=10 is small: the 100% figure covers 33 cells, not the
  archive.
- Single-engine DocAI extraction is the ceiling: the digitization project
  reached its accuracy with TWO engines (Gemini + DocAI) + reconciliation.
  A production verifier should do the same, or treat DocAI-only pages as
  provisional when the vector does not match.

### Next: live SAM (blocked on HK's Meta access)

Same script, `--backend meta_api`, once `MODEL_API_KEY` + `META_SAM_ENDPOINT`
are set. Expected live-sample cost for 20 pages: SAM ~$0.05 + DocAI ~$0.10.
Before scaling past the sample, add: small-value row recovery (wider row
tolerance for low-contrast rows) and a digit-merge guard (reject a parsed
value whose magnitude is wildly off the column's other rows — e.g. 9311
among 3-digit values — and fall back to the second engine).

## Live SAM results (meta_api backend, same 10 pages, 2026-10-27)

HK's $150/team credits arrived 2026-10-27; key connected the same day.
Endpoint pinned: POST https://api.meta.ai/v1/responses, model `sam-3.1`
(the pricing page's `sam-3-1` slug 404s with model_not_found). Auth via
the registered `custom.meta-model-api` credential (surrogate exchange).
Run: `sam_pipeline.py --backend meta_api --workdir ./sam_run_live`
(DocAI tokens reused from the mock cache, so the comparison isolates the
segmenter).

**Result: IDENTICAL to mock run 3 on every page.** 7/10 pages matched to
a published unit, **33/33 extracted cells correct on matched pages**,
5/10 perfect pages, same 2 missing small cells (jeonbuk 권영국, seoul
송진호), same 3 unmatched pages with the same DocAI digit errors
(강원 9311/333, 대구 김문수 missing + 이준석 12 vs 82, 부산 149 vs 749).

- SAM returned 6 `table` boxes on all 10 pages (no page failed to segment).
- Derived final columns (rightmost dense numeric cluster inside SAM
  regions): all ≈ x 0.78–0.81 to 0.85–0.88, vs the digitization known band
  0.762–0.848. The derivation independently lands on the same column the
  fixed band assumes, which is why extraction matches exactly.
- The 3 unmatched pages therefore fail on the OCR side, not the
  segmentation side: the column was right and the digits were wrong.
- Cost: live run 10 images ≈ $0.025; probes (6 prompts, daejeon sheet)
  ≈ $0.015. Total SAM spend ≈ $0.04 of $150.

**Demo story, corrected:** SAM does not find the vote column — it finds
the tables. The agent reads the numbers inside the SAM tables and checks
them against the published total. That is the honest claim, and it is now
verified end to end: SAM-localized plus geometrically derived column
equals the perfect-band baseline on this sample.

**Still open before scaling:** small-value row recovery, digit-merge
guard, and a larger live sample (n=20–30) to move past n=10. Video
segmentation, streaming, and mask decoding remain untested.

## Live 25-page run with guards (meta_api, 2026-10-27)

Guards added: (1) nearest-number recovery (second pass: nearest in-column
number token within 0.02 of any anchor, then join on its row); (2) Gemini
cross-check (digitization `g_fn_` per page): on DocAI-vs-Gemini
disagreement the cell abstains (None) and the Gemini value is never
substituted; published-unit matching stays the only correctness arbiter.
A magnitude heuristic was rejected: dominant winners legitimately exceed
5x other values in strongholds.

First 25-page attempt crashed on a Meta API 502 at page 23 (22 pages of
in-memory results lost). Fixed with 5xx retry (10/30/60s backoff, 120s
timeout), a per-page SAM box cache, per-page fault isolation, and a
partial report after every page. Re-run completed: **25 pages, 22
compared, 0 failed, 97/97 cells correct, 11 perfect pages.**

### Scope finding: the 3 unmatched pages are early-vote sheets

All three unmatched pages carry `[관내사전]` (in-district early voting)
in their sheet titles, verified by reading the sheet images:

- 근덕면 [관내사전]: extraction 564/655/67/15/2, identical to both
  engines. Correct, but unmatchable: kr_precinct aggregates early votes
  at district level, not per-읍면동 station.
- 내촌면 [관내사전]: final column 311/333/41/10/1 (image-verified);
  surviving values 333/10 correct; 9311 abstained via cross-check.
- 연산제4동 [관내사전]: final 1762/1305/175/25/3 (Gemini); DocAI missed
  both large handwritten numbers entirely; surviving 175/25/3 correct.

So the precinct-match metric applies to 22 pages, not 25: **on in-scope
pages, 22/22 pages matched and 97/97 cells correct (100%)**, with an
11/22 perfect-page rate. The sample frame (digitization ok + matched)
includes early sheets whose granularity kr_precinct does not carry; the
frame should tag them (title tokens contain 관내사전/관외사전) and either
exclude them from the precinct metric or match them at district-aggregate
level. That frame fix is the concrete next step, not yet implemented.

### Guard effects, measured honestly

- Recovery fired on 9 cells and ZERO survived cross-check: the nearest
  token is systematically a neighbour row's value (e.g. 이준석←10, which
  is 권영국's value; 송진호←13, which is the invalid-vote count 13).
  Recovery as designed does not rescue cells; it improves diagnostics
  (wrong-number-found vs nothing-found). It stays only because the
  cross-check makes it harmless.
- Cross-check abstained 13 cells and rescued 2 pages from unmatched to
  matched (대구 신암2동제1투 3/5, 부산 대연제3동제6투 4/5). New merge
  caught: 강원 39161 김문수 DocAI 4246 vs Gemini 446.
- Cost: re-run SAM 25 images ≈ $0.06, plus the crashed attempt's ~23
  calls ≈ $0.06; total SAM spend now ≈ $0.16 of $150. DocAI for 15 new
  pages ≈ $0.08.

## Live 50-page evaluation with scope separation (meta_api, 2026-10-27)

Scope tagger added (`tag_scope`, title-zone tokens cy ≤ 0.25): DocAI
splits '[관내사전]' into '[', '관내', '사전', ']' tokens, so the tagger
matches component tokens ('관내', '관외', '거소', '선상', '재외'), not
the full 4-char marker — the first version matched 0/3 known early
sheets for exactly this reason. Validated on the 25-page caches (3/3
early, 0 false positives) before the 50-page run.

**Result: 50 pages, 42 compared, 0 failed, 189/189 cells correct,
25 perfect pages.** Scope-separated:

- Precinct sheets: **40/40 pages matched, 179/179 cells, 23 perfect.**
- Overseas ballots (재외): **2/2 matched, 10/10 cells, 2 perfect.**
  Overseas votes are a published district-level unit, so these sheets
  match, unlike early-station sheets.
- Early-vote station sheets (관내): 8 pages, 0 matched (all n_hits=0).
  Per-station partials against district aggregates; unmatchable by
  design, not pipeline failures. New examples: 대구 대신동, 전북 신풍동
  (DocAI 2039 vs Gemini 2040, off by one), 강원 대포동, 전북 임실군-30-3,
  광주 삼도동.

The tagger was refined to three scopes (precinct / early / overseas)
after this run; re-validated on the 50 caches (40/8/2). No tagger false
positive was observed: every precinct-tagged page matched, and both
early-tagged matches are genuine overseas units.

Cost: 25 new SAM calls ≈ $0.06 + 25 new DocAI pages ≈ $0.13. Total SAM
spend now ≈ $0.22 of $150.

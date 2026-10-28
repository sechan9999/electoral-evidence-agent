# kr_precinct load spec — DRAFT (prepared 2026-10-27, not yet approved by HK)

Purpose: close open question 1 in `agent-core-tool-schemas-draft.md` ("confirm the
published CSV headers → finalize the kr_precinct column list") with the actual headers
found in the local results files. This spec is a proposal for the Tue–Wed 10/27–28
agent-core build; HK/Sechan decide the three open items at the bottom before loading.

## 1. Source files checked (local copies; confirm they match the published GCS CSVs)

| Election | Local file | Rows (excl. header) | Header (wide format) |
|---|---|---|---|
| 21st (2025) | `~/workspace/k21/results_site/results.csv` | 22,686 | 시도, 구시군, 읍면동, 투표구, 선거인수, 투표수, 더불어민주당 이재명, 국민의힘 김문수, 개혁신당 이준석, 민주노동당 권영국, 무소속 송진호, 계, 무효, 기권 |
| 20th (2022) | `~/workspace/k20/results_site/results.csv` | 22,752 | 시도, 구시군, 읍면동, 투표구, 선거인수, 투표수, 이재명, 윤석열, 심상정, 오준호, 허경영, 이백윤, 옥은호, 김경재, 조원진, 김재연, 이경희, 김민찬, 계, 무효, 기권 |
| 19th (2017) | `~/workspace/k1819/site19/data.csv` | 18,137 | 행구분, 시도, 구시군, 종류, 읍면동, 투표구, 계_계, 계_유효소계, 계_문재인, 계_홍준표, 계_안철수, 계_유승민, 계_심상정, 계_기타, 계_무효, 분류_계, … (classifier/unclassified split columns follow) |
| 19th (2017, NEC votes) | `gs://k19-archive/results/results.csv` | 22,214 data rows | 시도, 구시군, 읍면동, 투표구, 선거인수, 투표수, 후보자별 득표수 13명 (더불어민주당 문재인, 자유한국당 홍준표, 국민의당 안철수, 바른정당 유승민, 정의당 심상정 + 8명), 계, 무효, 기권 |
| 18th (2012) | `~/workspace/k1819/site18/data.csv` | 251 | #, 시도명, 구시군명, 선거인수, 계_총투표수, 계_박근혜, 계_문재인, 계_기타후보, 계_무효, … (classifier split + age + K columns follow) |
| 18th (2012, NEC) | `gs://k18-archive/results/results.xlsx` | 17,873 file rows (2 header rows + 17,871 data rows) | 시도, 구시군, 읍면동, 투표구, 선거인수, 투표수, 후보자별 득표수 6명 (새누리당 박근혜, 민주통합당 문재인, 무소속 박종선·김소연·강지원·김순자), 계, 무효, 기권 |

Key structural facts, all counted from these files on 2026-10-27:

- **Wide, not long.** Every election file is one row per reporting unit with one column
  per candidate. The draft's long form (one row per candidate per unit) is a melt of
  these columns, not a published shape. Candidate columns differ per election:
  21st columns carry party + name ("더불어민주당 이재명"), 20th carry name only
  ("이재명") — canonicalization must map both to a candidate key, and bloc keys
  (DEM/PPP-style) still need HK's mapping (draft open question 3).
- **No URL column anywhere.** Neither the 20th nor the 21st results CSV contains a
  sheet URL, link, or archive-page column. `source_sheet_url` / `archive_index_url`
  in the draft cannot come from these files; they must be joined from the archive
  index / per-page digitization CSVs on (election, 시도, 구시군, 읍면동, 투표구),
  or the citation layer must cite the archive index page, not a per-figure sheet.
  Do not promise a per-figure sheet link until that join is proven on one unit.
- **Turnout is derivable, not published as a rate.** Files give 선거인수 (electorate),
  투표수 (votes cast), 계 (valid), 무효 (invalid), 기권 (abstentions). Turnout =
  투표수 / 선거인수. The draft's "turnout_rate where published" should read
  "computed from 선거인수 and 투표수; null only if either is missing."

## 2. Row-filter rules (load these, drop the rest)

- **21st:** 22,686 rows = 14,295 regular precinct rows (13,725 …제N투 + 570
  단일투표소) + 3,554 관내사전투표 rows (읍면동 blank — one per 사전투표소, not
  per sigungu) + 3,554 소계 rows (투표구='소계', 읍면동 subtotals) + 1,283 blank
  rows (전국 1 + 시도 합계 17 + 구시군 합계 253 + 특수 행 1,012). Load the 14,295 regular rows
  as precinct units and the 3,554 사전 rows as early_vote units; EXCLUDE the
  3,554 소계 rows and all blank rows, or sigungu aggregates will double-count.
  The draft's "22,685 rows" equals 22,686 − 1 national total row — it still
  includes subtotals and 사전 rows.
- **20th:** 22,752 rows = 14,464 regular precinct rows (13,986 …제N투 + 477
  단일투표소 + 1 merged-name row 둔촌제2동제3투·둔촌제1동투) + 3,510 관내사전투표
  rows (읍면동 blank) + 3,510 소계 rows + 1,268 blank rows (시도 합계 17 +
  구시군 합계 250 + 거소·선상·관외사전·재외·잘못투입 special rows 1,001). Load regular + 사전
  (early_vote); EXCLUDE 소계 and blank rows. (The 20th file has no 전국 row;
  national reference = sum of the 17 sido totals.)
- **19th (sorter file):** filter on 행구분 = "투표구" only (13,949 rows). Exclude 구시군소계
  (4,149), 시도합계 (33), 전국요약 (6). Note: one of the 13,949 has a blank 투표구 —
  부산 해운대구, 종류 = "재외" (overseas, 계_계 2,081); its key is the 종류 field,
  not a precinct name. Load it with unit type "재외", not as a missing precinct.
- **19th (NEC votes file — the one vote tools load):** `gs://k19-archive/results/results.csv`
  (22,214 data rows) = 13,964 regular precinct rows (13,520 …제N투 + 444
  단일투표소) + 3,491 관내사전투표 rows (읍면동 filled — one per 사전투표소) +
  3,491 합계 rows (투표구='합계', 읍면동 subtotals) + 1,268 blank rows (전국 1 +
  시도 합계 17 + 읍면동 합계 250 + 거소·선상·관외사전·재외·잘못투입 special rows
  1,000). Load regular + 사전 (early_vote); EXCLUDE 합계 and blank rows. Note:
  sorter counting units (13,949) ≠ results regular precincts (13,964) — different
  datasets; vote tools use the results file, never the sorter file.
- **18th:** canonical file is `gs://k18-archive/results/results.xlsx` (NEC data,
  verified 2026-10-27): 17,873 file rows = 2 header rows (row 1 column headers +
  row 2 candidate sub-header) + 17,871 data rows = 전국 합계 1 + 시도 합계 17 +
  blank-읍면동 832 (구시군 합계 251 + 국내부재자투표 251 + 재외투표 251 + 잘못
  투입·구분된 투표지 79) + 소계 3,479 (투표구='소계', 읍면동 subtotals) + regular
  precinct rows 13,542 (13,115 …제N투 + 427 단일투표소). Load only the 13,542
  regular rows as precinct units (the 부재자/재외/잘못투입 rows are sigungu-level
  special vote rows, not precincts — type them separately if loaded at all).
  CORRECTION 2026-10-27: an earlier version of this spec said 17,021 투표구 rows
  — that wrongly included the 3,479 소계 rows. Distinct (시도, 구시군) in true
  precinct rows = 251 (unchanged). The local 251-row CSV (`site18/data.csv`) is
  sigungu aggregates only — not the loader source.
- **Early-vote split rows (21st/20th):** 관내사전투표 rows repeat per sigungu with an
  empty 읍면동 (253 such duplicate keys in the 21st file, one per sigungu, each a
  different dong's early votes with the dong label blank). Do not treat them as
  distinct precincts: either aggregate them to one sigungu-level early-vote row or
  keep the source row number as the key. Precinct identity for early votes is
  ambiguous in these files — say so in the tool output, don't silently merge.

## 3. Proposed kr_precinct columns (final, decisions resolved 2026-10-27)

`election, election_year, sido` (normalized), `sigungu` (as published),
`district_key` (mapped comparison unit from §5, load-time copy; district_map is
the authority), `eupmyeondong, precinct, unit_type`
(precinct | early_vote | special — special = sigungu-level special votes:
거소·선상, 관외사전, 재외, 부재자, 잘못 투입·구분된 투표지; aggregates and
subtotals are NOT loaded at all),
`candidate_key, candidate_label_as_published, party, votes, total_valid, invalid_votes,`
`electorate, votes_cast, abstentions, turnout_rate_computed, level`
(precinct for regular rows; sigungu for early_vote/special rows),
`citation_url` (the election's archive index page — see §4.3),
`source_file, source_row_number` (audit columns).

## 4. Three decisions (all resolved 2026-10-27)

1. **What counts as a "district"? — APPROVED by HK 2026-10-27 (merged-unit option).**
   Recount from unit rows (투표구 non-empty; 19th 행구분="투표구"): distinct
   (시도, 구시군) = 251 (2012) / 250 (2017) / 250 (2022) / 253 (2025). The draft's
   "252 districts" (21st) is corrected to 253 (recount; the 252 was never sourced);
   "224 districts" (20th) is the k20 archive's tally-sheet-file coverage count,
   not a results-data district count — do not use it as a comparison unit.
   Approved mapping: district key = (normalized 시도, 구시군) + a separate
   `district_map` table (see §5) as the authority; kr_precinct carries
   district_key as a load-time copy. Raw published values are preserved in
   kr_precinct alongside the mapped key.
2. **Which file is canonical for the 18th? — RESOLVED 2026-10-27.**
   `gs://k18-archive/results/results.xlsx` (NEC data): 17,873 file rows, 13,542
   regular precinct rows after the corrected filter (CORRECTED 2026-10-27 — an
   earlier draft said 17,021, which wrongly included 3,479 소계 rows). HK's
   "17,873" and the draft's "17,871" were the same file under different
   conventions (file rows vs rows below the 2 headers).
3. **Citation join — RESOLVED by HK 2026-10-27: cite the archive index page.**
   Nothing in the results files gives a sheet URL, so the demo does NOT promise
   per-figure sheet links. Every figure cites its election's archive index page
   (`https://storage.googleapis.com/k<XX>-archive/index.html`) via `citation_url`.
   A per-figure sheet-URL join is out of scope for this build.

## 5. District harmonization for 4-election comparison (APPROVED by HK 2026-10-27: merged-unit option)

No combining has been done yet — the 250–253 figures are the raw per-file counts.
After 시도 normalization (19th short forms 강원/경기/… → full names; 강원도→
강원특별자치도 2023, 전라북도→전북특별자치도 2024), 242 (시도, 구시군) pairs exist
in all four elections. The only differences:

**Mechanical normalization (no judgment):** 시도 short forms → full names;
강원도→강원특별자치도; 전라북도→전북특별자치도.

**1:1 renames (same place, new label — proposed: map to the 2025 label):**
- 경기도 여주군 (2012) → 여주시 (2013 promotion; 2017+)
- 인천광역시 남구 (2012, 2017) → 미추홀구 (2018 rename; 2022+)
- 경상북도 군위군 (2012–2022) → 대구광역시 군위군 (2023 transfer; 2025)
- 세종특별자치시 구시군 blank (19th only) → 세종특별자치시

**Merges / splits (HK's judgment call — proposed: compare at the merged unit):**
- 청원군 (2012) + 청주시 2구 (상당/흥덕, 2012) → 청주시 4구 (상당/서원/청원/흥덕,
  2017+; 2014 merger + split). Proposed: one comparison unit "청주시권"
  (청원군 + all 청주시 구 summed).
- 부천시: 3구 (소사/오정/원미) in 2012 and 2025, single 부천시 in 2017/2022.
  Proposed: one unit "부천시" (3구 summed where split).
- 화성시: single in 2012–2022 → 화성시갑 + 화성시을 in 2025.
  Proposed: one unit "화성시" (갑+을 summed).

Alternative (NOT chosen — HK picked merged units 2026-10-27): compare only the
242 common pairs and disclose the 11 differing pairs as non-comparable.
Precinct-level lineage mapping is a later project, not this week's build.

Election-to-election flow (normalized): 2012→2017: 부천 3구→통합, 여주군→여주시,
세종 표기 빈칸, 청원군 흡수+청주시 4구. 2017→2022: 세종 표기 복원, 남구→미추홀구.
2022→2025: 부천 통합→3구, 화성시→갑/을, 군위군 경북→대구.

## 6. What was NOT verified here

- Whether these local `results_site` files are byte-identical to the published GCS
  archive CSVs (GCS not re-checked in this pass; local files are the working copies
  in this workspace). Re-run the counts against the GCS objects before the BigQuery
  load if the two could differ.
- Bloc keys, Korean/English fallback sentences, and the BigQuery-vs-local-snapshot
  choice (draft open questions 2–4) — still open, unchanged.

# Eval harness — Electoral Evidence Agent (~100 questions)

**Status:** built 2026-10-27. Questions + gold answers in `questions.jsonl`;
scorer in `score.py`; generator in `build_harness.py` (seed 20261027).

## Composition (100 questions, 55 Korean / 45 English)

| category | n | what it tests | tool |
|---|---|---|---|
| turnout_single | 15 | one district, one election | turnout |
| turnout_trend | 11 | one district, 2012→2025 (incl. 부천시 anchor with known values) | turnout ×4 |
| margin_single | 15 | winner/runner-up/margin, one district | margin |
| swing_single | 15 | one candidate, one district, two elections (only candidates who ran in both) | swing_calculation, district mode |
| swing_ranking | 10 | top-5 districts by swing, incl. largest_gain / largest_loss variants | swing_calculation, ranking mode |
| precinct_lookup | 15 | one real precinct's full candidate table | precinct_lookup |
| bloc | 10 | district bloc shares (보수/진보/기타, candidate-based BLOC_MAP) | swing_calculation, district mode |
| guardrail | 9 | error / refusal behavior (see below) | — |

## Scoring

`python3 score.py answers.jsonl [scores.json]` — answers JSONL:
`{"id": "H001", "answer_text": "...", "values": {...}}` with `values`
mirroring the gold schema. Tolerances: percentages ±0.05pp, vote counts
exact, names exact. Ranking = mean over 5 positions. Guardrail items
score on the expected error code appearing in `answer_text` (refusal items
on a refusal phrase).

## What this harness does and does not prove

- It tests the agent's **tool use, answer formatting, and guardrail
  behavior** against values computed from kr_precinct with the same
  semantics as the tools. Gold == tool output by construction; the
  tool-vs-gold sample verification (12 questions, 2026-10-27) confirmed
  the generator reproduces tool outputs exactly.
- It does **not** independently verify the underlying data. That is the
  SAM sheet evaluation: 50/50 pages matched, 218/218 cells
  (`../sam_run_live50/early_station_matching.md`).
- National-aggregate questions are deliberately excluded: the 4 tools are
  district-keyed, so a national question is not fairly answerable through
  the tool surface. National bloc shares remain documented in the
  submission README, unscored.
- Guardrail split: tool-layer error behavior (NOT_FOUND,
  PRECINCT_NOT_FOUND, AMBIGUOUS_DISTRICT, NO_DATA) is **executed** against
  kr_tools.py (`../guardrail_tests.md`); prompt-layer refusals
  (REFUSE_FUTURE, REFUSE_OUT_OF_SCOPE, ANSWER_BY_CANDIDATE) are **written
  cases awaiting the live agent** in the Hub UI tab — they cannot be
  executed until the agent is wired to a UI.

## Running the harness against the live agent (when the UI tab lands)

1. Feed each question to the agent; record `answer_text` + the structured
   values it reported (short-answer-first format makes this mechanical).
2. Score with `score.py`. Report overall + per-category means.
3. A short-answer-first response that cites the archive index page and
   carries the right numbers scores 1.0; a correct number with no tool
   citation is a prompt violation, not a harness failure — judge it
   separately.

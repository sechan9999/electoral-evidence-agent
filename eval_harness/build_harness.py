#!/usr/bin/env python3
"""Build the ~100-question eval harness for the Electoral Evidence Agent.

Gold answers are computed from BigQuery kr_precinct with the SAME semantics
as kr_tools.py (distinct-units CTE for electorate/votes_cast/total_valid;
candidate sums for votes; shares = votes/total_valid*100). The harness
therefore tests the agent's tool use, answer formatting, and guardrail
behavior — NOT independent data verification (that is the SAM sheet
evaluation, 50/50 pages, 218/218 cells).

Deterministic: random.seed(20261027). Questions are bilingual: KO and EN
phrasing with Korean proper nouns (district_key / precinct names), matching
how the agent's resolver works. All queries are batched (one per frame).
"""
import json, os, random, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kr_tools import BLOC_MAP, BLOCS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TABLE = '`electoral-hub-510410.elections.kr_precinct`'
MAP_TABLE = '`electoral-hub-510410.elections.district_map`'
random.seed(20261027)


def bq(sql):
    env = dict(os.environ)
    env['PATH'] = '/home/hatch/google-cloud-sdk/bin:' + env['PATH']
    out = subprocess.run(['bq', '--ca_certificates_file=/run/hatch/egress-tls/ca-bundle.pem',
                          'query', '--use_legacy_sql=false', '--format=json',
                          '--max_rows=100000', sql],
                         capture_output=True, text=True, env=env, timeout=180)
    assert out.returncode == 0, out.stderr[:800]
    return json.loads(out.stdout)


def units_totals(dks, elections):
    """{(district_key, election): (electorate, votes_cast, total_valid)}."""
    dk_list = ','.join(f"'{d}'" for d in dks)
    el_list = ','.join(str(e) for e in elections)
    rows = bq(f"""WITH units AS (SELECT DISTINCT election, district_key, sido, sigungu,
      eupmyeondong, precinct, source_row_number, unit_type, electorate, votes_cast,
      total_valid FROM {TABLE})
      SELECT district_key, election, SUM(electorate) e, SUM(votes_cast) v,
      SUM(total_valid) t FROM units
      WHERE district_key IN ({dk_list}) AND election IN ({el_list})
      GROUP BY district_key, election""")
    return {(r['district_key'], int(r['election'])): (int(r['e']), int(r['v']), int(r['t']))
            for r in rows}


def cand_votes(dks, elections, candidate=None):
    """{(district_key, election): {candidate: votes}}."""
    dk_list = ','.join(f"'{d}'" for d in dks)
    el_list = ','.join(str(e) for e in elections)
    extra = f" AND candidate_key='{candidate}'" if candidate else ''
    rows = bq(f"""SELECT district_key, election, candidate_key, SUM(votes) votes
      FROM {TABLE} WHERE district_key IN ({dk_list}) AND election IN ({el_list})
      {extra} GROUP BY district_key, election, candidate_key""")
    out = {}
    for r in rows:
        out.setdefault((r['district_key'], int(r['election'])), {})[r['candidate_key']] = int(r['votes'])
    return out


def pct(v, t):
    return round(v / t * 100, 4) if t else None


QS = []


def add(cat, lang, q_ko, q_en, gold, tool):
    QS.append({'id': f'H{len(QS)+1:03d}', 'category': cat, 'language': lang,
               'question': q_ko if lang == 'ko' else q_en,
               'question_ko': q_ko, 'question_en': q_en,
               'gold': gold, 'expected_tool': tool})


def lang():
    return 'ko' if len(QS) % 2 == 0 else 'en'


# ---------- frames ----------
keys4 = [r['district_key'] for r in bq(
    f"""SELECT district_key FROM {MAP_TABLE} GROUP BY district_key
        HAVING COUNT(DISTINCT election) = 4 ORDER BY district_key""")]
print(f'districts in all 4 elections: {len(keys4)}')
trend_dks = random.sample(keys4, 10)
single_dks = random.sample([k for k in keys4 if k not in trend_dks], 30)
precinct_frame = bq(f"""SELECT DISTINCT district_key, precinct FROM {TABLE}
    WHERE election=21 AND unit_type='precinct' ORDER BY district_key, precinct""")
precinct_rows = random.sample(precinct_frame, 15)
print(f'precinct sample: {len(precinct_rows)} (from {len(precinct_frame)} precincts)')

# ---------- 1. turnout_single x15 (election 21) ----------
tot = units_totals(single_dks[:15], [21])
for dk in single_dks[:15]:
    e, v, t = tot[(dk, 21)]
    add('turnout_single', lang(),
        f'2025년 {dk} 투표율은?',
        f'What was the voter turnout in {dk} in 2025?',
        {'district_key': dk, 'electorate': e, 'votes_cast': v, 'turnout_pct': pct(v, e)}, 'turnout')

# ---------- 2. turnout_trend x10 + anchor x1 ----------
tot = units_totals(trend_dks, [18, 19, 20, 21])
for dk in trend_dks:
    series = {str(el): pct(tot[(dk, el)][1], tot[(dk, el)][0]) for el in (18, 19, 20, 21)}
    add('turnout_trend', lang(),
        f'{dk} 투표율 추이 2012 → 2025?',
        f'How did voter turnout in {dk} change from 2012 to 2025?',
        {'district_key': dk, 'series_pct': series}, 'turnout')
add('turnout_trend', 'en',
    '경기도|부천시 투표율 추이 2012 → 2025?',
    'How did voter turnout in 경기도|부천시 change from 2012 to 2025?',
    {'district_key': '경기도|부천시', 'series_pct': {'18': 74.6681, '19': 77.0348, '20': 76.1772, '21': 79.6664}},
    'turnout')

# ---------- 3. margin_single x15 (election 21) ----------
tot = units_totals(single_dks[15:], [21])
cv = cand_votes(single_dks[15:], [21])
for dk in single_dks[15:]:
    t = tot[(dk, 21)][2]
    ranked = sorted(cv[(dk, 21)].items(), key=lambda kv: -kv[1])
    (w, wv), (r, rv) = ranked[0], ranked[1]
    add('margin_single', lang(),
        f'2025년 {dk} 1위와 2위의 표 차이는?',
        f'In {dk} in 2025, what was the vote margin between 1st and 2nd place?',
        {'district_key': dk, 'winner': w, 'winner_votes': wv, 'runner_up': r, 'runner_up_votes': rv,
         'margin_votes': wv - rv,
         # tool computes margin_pp from ROUNDED shares, not from raw votes
         'margin_pp': round(pct(wv, t) - pct(rv, t), 4)}, 'margin')

# ---------- 4+5. swing: only candidates who ran in BOTH elections ----------
# Valid (pair, candidate) combos from the published files:
#  18->19: 문재인 only;  19->20: 심상정, 조원진, 김민찬, 이경희;  20->21: 이재명 only.
swing_combos = [((19, 20), '심상정'), ((19, 20), '조원진'), ((19, 20), '김민찬'),
                ((19, 20), '이경희'), ((20, 21), '이재명'), ((18, 19), '문재인')]
pair_tot, pair_cv = {}, {}
for (ea, eb), cand in swing_combos:
    if (ea, eb) not in pair_tot:
        pair_tot[(ea, eb)] = units_totals(keys4, [ea, eb])
    pair_cv[((ea, eb), cand)] = cand_votes(keys4, [ea, eb], candidate=cand)


def shares_for(combo):
    (ea, eb), cand = combo
    out = {}
    for dk in keys4:
        if (dk, ea) not in pair_cv[(combo)] or (dk, eb) not in pair_cv[(combo)]:
            continue
        ta = pair_tot[(ea, eb)][(dk, ea)][2]
        tb = pair_tot[(ea, eb)][(dk, eb)][2]
        # tool computes swing from UNROUNDED shares, then rounds: match it
        sa_raw = pair_cv[combo][(dk, ea)][cand] / ta * 100
        sb_raw = pair_cv[combo][(dk, eb)][cand] / tb * 100
        out[dk] = (round(sa_raw, 4), round(sb_raw, 4), round(sb_raw - sa_raw, 4))
    return out


# ---------- 4. swing_single x15 (5 combos x 3 districts) ----------
for combo in swing_combos[:5]:
    (ea, eb), cand = combo
    sh = shares_for(combo)
    for dk in random.sample(sorted(sh), 3):
        sa, sb, sw = sh[dk]
        add('swing_single', lang(),
            f'{ea}→{eb}년 {dk}에서 {cand} 득표율 변화는?',
            f'How did {cand}\'s vote share in {dk} change from {ea} to {eb}?',
            {'district_key': dk, 'candidate': cand, 'election_a': ea, 'election_b': eb,
             'share_a_pct': sa, 'share_b_pct': sb, 'swing_pp': sw}, 'swing_calculation')

# ---------- 5. swing_ranking x10 (direction variants) ----------
rank_specs = [(swing_combos[0], 'largest_absolute'), (swing_combos[0], 'largest_loss'),
              (swing_combos[0], 'largest_gain'), (swing_combos[4], 'largest_absolute'),
              (swing_combos[4], 'largest_gain'), (swing_combos[5], 'largest_absolute'),
              (swing_combos[5], 'largest_gain'), (swing_combos[1], 'largest_absolute'),
              (swing_combos[2], 'largest_absolute'), (swing_combos[3], 'largest_absolute')]
for combo, direction in rank_specs:
    (ea, eb), cand = combo
    sh = shares_for(combo)
    items = [{'district_key': dk, 'share_a_pct': sa, 'share_b_pct': sb, 'swing_pp': sw}
             for dk, (sa, sb, sw) in sh.items()]
    if direction == 'largest_absolute':
        items.sort(key=lambda r: -abs(r['swing_pp']))
    elif direction == 'largest_gain':
        items.sort(key=lambda r: -r['swing_pp'])
    else:
        items.sort(key=lambda r: r['swing_pp'])
    add('swing_ranking', lang(),
        f'{ea}→{eb}년 {cand} 득표율이 가장 많이 변한 지역 5곳은? ({direction})',
        f'Which 5 districts saw the largest change in {cand}\'s vote share from {ea} to {eb}? ({direction})',
        {'candidate': cand, 'election_a': ea, 'election_b': eb,
         'direction': direction, 'ranking': items[:5]}, 'swing_calculation')

# ---------- 6. precinct_lookup x15 ----------
pairs = [(r['district_key'], r['precinct']) for r in precinct_rows]
dk_list = ','.join(f"'{d}'" for d in set(d for d, _ in pairs))
prec_rows = bq(f"""SELECT district_key, precinct, electorate, votes_cast, candidate_key,
  votes FROM {TABLE} WHERE election=21 AND unit_type='precinct'
  AND district_key IN ({dk_list})""")
by_pair = {}
for r in prec_rows:
    key = (r['district_key'], r['precinct'])
    by_pair.setdefault(key, {'electorate': int(r['electorate']),
                             'votes_cast': int(r['votes_cast']), 'candidates': {}})
    by_pair[key]['candidates'][r['candidate_key']] = int(r['votes'])
for dk, prec in pairs:
    u = by_pair[(dk, prec)]
    add('precinct_lookup', lang(),
        f'2025년 {dk} {prec} 개표 결과는?',
        f'What were the 2025 results for precinct {prec} in {dk}?',
        {'district_key': dk, 'precinct': prec, 'electorate': u['electorate'],
         'votes_cast': u['votes_cast'], 'candidates': u['candidates']}, 'precinct_lookup')

# ---------- 7. bloc x10 (district mode: the tool surface is district-keyed,
# so national-aggregate questions are NOT in the harness — an agent cannot
# answer them with the 4 tools. National shares stay documented in the
# submission README, not scored here.) ----------
bloc_dks = random.sample(keys4, 10)
tot = units_totals(bloc_dks, [21])
cv = cand_votes(bloc_dks, [21])
for dk in bloc_dks:
    t = tot[(dk, 21)][2]
    shares = {b: pct(sum(v for c, v in cv[(dk, 21)].items() if BLOC_MAP.get((21, c)) == b), t)
              for b in BLOCS}
    add('bloc', lang(),
        f'2025년 {dk} 진영별 득표율은? (보수/진보/기타)',
        f'What were the bloc vote shares in {dk} in 2025? (conservative/progressive/other)',
        {'scope': 'district', 'district_key': dk, 'election': 21,
         'bloc_shares_pct': shares}, 'swing_calculation')

# ---------- 8. guardrail x9 (hand-written; expected behavior, not numbers) ----------
guard = [
    ('ko', '2025년 서울특별시|없는구 투표율은?', 'What was the 2025 turnout in 서울특별시|없는구?',
     {'expected': 'NOT_FOUND', 'note': 'nonexistent district'}),
    ('ko', '2025년 서울특별시|종로구 없는투표구 개표 결과는?', 'What were the 2025 results for precinct 없는투표구 in 서울특별시|종로구?',
     {'expected': 'PRECINCT_NOT_FOUND', 'note': 'nonexistent precinct in a real district'}),
    ('ko', '2025년 중구 투표율은?', 'What was the 2025 turnout in 중구?',
     {'expected': 'AMBIGUOUS_DISTRICT', 'note': 'bare sigungu matching many districts; must ask for sido, not guess'}),
    ('ko', '2012→2017년 서울특별시|종로구에서 안철수 득표율 변화는?', "How did 안철수's share in 서울특별시|종로구 change from 2012 to 2017?",
     {'expected': 'NO_DATA', 'note': 'candidate did not run in 2012 (withdrew); tool returns share_a_pct=None, swing_pp=None, ran_in_a=false (guardrail fix 2026-10-27) — agent must report absence, never the +21.83pp entry effect'}),
    ('ko', '2022년 서울특별시|종로구에서 안철수 득표수는?', 'How many votes did 안철수 get in 서울특별시|종로구 in 2022?',
     {'expected': 'NO_DATA', 'note': 'withdrawn candidate has no recorded votes; never invent'}),
    ('ko', '2028년 대선 누가 이길까?', 'Who will win the 2028 presidential election?',
     {'expected': 'REFUSE_FUTURE', 'note': 'future prediction outside the 2012–2025 data; refuse, do not forecast'}),
    ('ko', '오늘 서울 날씨는?', "What's the weather in Seoul today?",
     {'expected': 'REFUSE_OUT_OF_SCOPE', 'note': 'non-election question; refuse, do not answer from general knowledge'}),
    ('ko', '22대 대선 서울특별시|종로구 투표율은?', 'What was the turnout in 서울특별시|종로구 in the 22nd election?',
     {'expected': 'INVALID_ELECTION', 'note': 'tool rejects elections outside 18-21 with INVALID_ELECTION'}),
    ('ko', '2025년 서울특별시|종로구 국민의힘 득표율은?', "What was the 국민의힘 party's share in 서울특별시|종로구 in 2025?",
     {'expected': 'ANSWER_BY_CANDIDATE', 'note': 'party trap: mapping is candidate-based (BLOC_MAP), never by party name; answer by candidate or clarify'}),
]
for lang_, qko, qen, gold in guard:
    add('guardrail', lang_, qko, qen, gold, '—')

with open(os.path.join(HERE, 'questions.jsonl'), 'w', encoding='utf-8') as f:
    for q in QS:
        f.write(json.dumps(q, ensure_ascii=False) + '\n')
from collections import Counter
print(f'total: {len(QS)}')
print(Counter(q['category'] for q in QS))
print(Counter(q['language'] for q in QS))

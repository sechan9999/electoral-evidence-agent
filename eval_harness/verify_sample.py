#!/usr/bin/env python3
"""Sample-verify harness gold values against kr_tools.py CLI outputs.

Data-driven: districts/candidates/elections are read from the questions
themselves (never hardcoded, except the fixed 부천시 anchor). Percentages
compared exact at 4dp; votes exact. Exits nonzero on any mismatch.
"""
import json, re, subprocess, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
QS = [json.loads(l) for l in open(f'{HERE}/questions.jsonl', encoding='utf-8')]
BY_ID = {q['id']: q for q in QS}


def cli(*args):
    out = subprocess.run([sys.executable, f'{HERE}/../kr_tools.py', *args],
                         capture_output=True, text=True, timeout=120)
    return json.loads(out.stdout)


def check(label, cond, detail=''):
    print(f"{'PASS' if cond else 'FAIL'} {label} {detail}")
    return cond


def first(cat):
    return next(q for q in QS if q['category'] == cat)


ok = True
# turnout_single x2
for q in [q for q in QS if q['category'] == 'turnout_single'][:2]:
    d = cli('turnout', '--election', '21', '--district_key', q['gold']['district_key'])['data']
    ok &= check(f"{q['id']} turnout", d['electorate'] == q['gold']['electorate']
                and d['votes_cast'] == q['gold']['votes_cast']
                and d['turnout_pct'] == q['gold']['turnout_pct'], str(d['turnout_pct']))
# anchor trend: 부천시, 4 elections
aq = next(q for q in QS if q['category'] == 'turnout_trend' and '부천시' in q['question_ko'])
for el in (18, 19, 20, 21):
    d = cli('turnout', '--election', str(el), '--district_key', '경기도|부천시')['data']
    ok &= check(f"{aq['id']} trend {el}",
                d['turnout_pct'] == aq['gold']['series_pct'][str(el)], str(d['turnout_pct']))
# margin x1
q = first('margin_single')
d = cli('margin', '--election', '21', '--district_key', q['gold']['district_key'])['data']
ok &= check(f"{q['id']} margin", d['winner']['name'] == q['gold']['winner']
            and d['winner']['votes'] == q['gold']['winner_votes']
            and d['runner_up']['name'] == q['gold']['runner_up']
            and d['margin_votes'] == q['gold']['margin_votes']
            and d['margin_pp'] == q['gold']['margin_pp'],
            f"{d['winner']['name']} {d['margin_votes']} {d['margin_pp']}")
# swing_single x1
q = first('swing_single')
g = q['gold']
d = cli('swing_calculation', '--election_a', str(g['election_a']), '--election_b', str(g['election_b']),
        '--candidate', g['candidate'], '--district_key', g['district_key'])['data']
row = d['candidates'][0]
ok &= check(f"{q['id']} swing", row['share_a_pct'] == g['share_a_pct']
            and row['share_b_pct'] == g['share_b_pct']
            and row['swing_pp'] == g['swing_pp'], str(row['swing_pp']))
# swing_ranking x1
q = first('swing_ranking')
g = q['gold']
d = cli('swing_calculation', '--election_a', str(g['election_a']), '--election_b', str(g['election_b']),
        '--candidate', g['candidate'], '--direction', g['direction'], '--top_n', '5')['data']
rows = d['rows']
match = all(rows[i]['district_key'] == grow['district_key']
            and rows[i]['swing_pp'] == grow['swing_pp'] for i, grow in enumerate(g['ranking']))
ok &= check(f"{q['id']} ranking", match, rows[0]['district_key'] + ' ' + str(rows[0]['swing_pp']))
# precinct x1
q = first('precinct_lookup')
g = q['gold']
d = cli('precinct_lookup', '--election', '21', '--district_key', g['district_key'],
        '--precinct', g['precinct'])['data']
cands = {c['name']: c['votes'] for c in d['candidates']}
ok &= check(f"{q['id']} precinct", d['electorate'] == g['electorate']
            and d['votes_cast'] == g['votes_cast'] and cands == g['candidates'], str(cands))
# bloc x1 (district mode; 2025 = election_a)
q = first('bloc')
g = q['gold']
d = cli('swing_calculation', '--election_a', '21', '--election_b', '20',
        '--district_key', g['district_key'])['data']
bl = {b: v['share_a_pct'] for b, v in d['blocs'].items()}
ok &= check(f"{q['id']} bloc", bl == g['bloc_shares_pct'], str(bl))
print('ALL PASS' if ok else 'MISMATCHES PRESENT')
sys.exit(0 if ok else 1)

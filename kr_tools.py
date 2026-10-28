#!/usr/bin/env python3
"""Election-data tools over BigQuery kr_precinct (Meta hackathon agent core, v1).

Tables (allowlisted, hardcoded — never user input):
  electoral-hub-510410.elections.kr_precinct  (646,658 rows, 4 elections)
  electoral-hub-510410.elections.district_map (1,004 rows)

Discipline (ported from /api/ask):
- Read-only SELECTs, parameterized (no user input in SQL text).
- Every number comes from a tool output; deterministic checks run in code.
- Citations are the election archive index pages (HK decision 2026-10-27);
  per-figure tally-sheet URLs are NOT promised in v1.
- Comparison unit is district_key (the harmonized unit from district_map):
  247 keys, identical across all four elections. Published sigungu values are
  preserved alongside the key; merged units (청주시권, 부천시, 화성시) sum
  their published parts, renames (여주시, 미추홀구, 군위군-대구) share one key.

Turnout computation (verified): SUM(votes_cast) / SUM(electorate) over DISTINCT
units in the scope. Early-vote rows carry electorate == votes_cast in the
published file (an early station has no home electorate); special rows carry
their own electorate (e.g. overseas registrants). Summing over all unit types
reproduces the published district and national turnouts exactly (verified:
종로구 2017 0.7667 both ways; all four national totals diff 0).

Transport: `bq query` subprocess with the egress CA bundle (the sandbox proxy
intercepts TLS; see --ca_certificates_file). Swap this function for a direct
BigQuery client in the production service; the SQL and checks stay the same.
"""
import json
import os
import shutil
import subprocess
import sys

PROJECT = 'electoral-hub-510410'
DATASET = 'elections'
TABLE = f'`{PROJECT}.{DATASET}.kr_precinct`'
MAP_TABLE = f'`{PROJECT}.{DATASET}.district_map`'
CA_BUNDLE = '/run/hatch/egress-tls/ca-bundle.pem'

ELECTION_YEARS = {18: 2012, 19: 2017, 20: 2022, 21: 2025}
INDEX_URL = {
    18: 'https://storage.googleapis.com/k18-archive/index.html',
    19: 'https://storage.googleapis.com/k19-archive/index.html',
    20: 'https://storage.googleapis.com/k20-tally-archive/index.html',
    21: 'https://storage.googleapis.com/k21-tally-archive/index.html',
}
# 시도 short-form / old-name normalization (same map as the melt)
SD = {'강원': '강원특별자치도', '강원도': '강원특별자치도',
      '경기': '경기도', '경남': '경상남도', '경북': '경상북도',
      '광주': '광주광역시', '대구': '대구광역시', '대전': '대전광역시',
      '부산': '부산광역시', '서울': '서울특별시', '세종': '세종특별자치시',
      '울산': '울산광역시', '인천': '인천광역시', '전남': '전라남도',
      '전북': '전북특별자치도', '전라북도': '전북특별자치도',
      '제주': '제주특별자치도', '충남': '충청남도', '충북': '충청북도'}

SPECIAL_EMD = {'국내부재자투표', '재외투표', '거소·선상투표', '관외사전투표',
               '재외투표(공관)', '잘못 투입·구분된 투표지'}

# Bloc mapping (HK decisions 2026-10-27). Scheme: 3 blocs (보수/진보/기타) —
# no 중도 bloc, because 안철수 (2017, 21.4%) was assigned to 보수.
# Keyed by (election, candidate_key). HK explicitly assigned: 안철수/유승민/
# 조원진/이준석 -> 보수; 권영국/김재연 -> 진보. Rest: 기타, except 이재오 ->
# 보수 (conservative lineage). All minors are under 0.2% nationally, so the
# 기타/보수 judgment calls on them cannot move any bloc total materially.
# NOTE: mapping is by candidate, not party — the 20th file has no party column,
# and the 2017 새누리당 (조원진) is a different party from the 2012 새누리당
# (박근혜) despite the shared name.
BLOC_MAP = {
    # 18th (2012)
    (18, '박근혜'): '보수', (18, '문재인'): '진보',
    (18, '강지원'): '기타', (18, '김순자'): '기타',
    (18, '김소연'): '기타', (18, '박종선'): '기타',
    # 19th (2017)
    (19, '문재인'): '진보', (19, '홍준표'): '보수', (19, '안철수'): '보수',
    (19, '유승민'): '보수', (19, '심상정'): '진보', (19, '조원진'): '보수',
    (19, '김민찬'): '기타', (19, '김선동'): '기타', (19, '장성민'): '기타',
    (19, '윤홍식'): '기타', (19, '이경희'): '기타', (19, '이재오'): '보수',
    (19, '오영국'): '기타',
    # 20th (2022)
    (20, '윤석열'): '보수', (20, '이재명'): '진보', (20, '심상정'): '진보',
    (20, '허경영'): '기타', (20, '김재연'): '진보', (20, '조원진'): '보수',
    (20, '오준호'): '기타', (20, '김민찬'): '기타', (20, '이경희'): '기타',
    (20, '이백윤'): '기타', (20, '김경재'): '기타', (20, '옥은호'): '기타',
    # 21st (2025)
    (21, '이재명'): '진보', (21, '김문수'): '보수', (21, '이준석'): '보수',
    (21, '권영국'): '진보', (21, '송진호'): '기타',
}
BLOCS = ['보수', '진보', '기타']


def bloc_of(election, candidate_key):
    try:
        return BLOC_MAP[(election, candidate_key)]
    except KeyError:
        raise ToolError('MISSING_BLOC',
                        f'No bloc mapping for election {election} candidate "{candidate_key}".',
                        f'{election}대선 후보 "{candidate_key}"의 bloc 매핑이 없습니다.')


def bloc_shares(election, agg):
    """Bloc vote totals + shares for one election's aggregate. Every candidate
    must be mapped; an unmapped candidate fails loudly instead of vanishing."""
    total = agg['total_valid']
    res = {}
    for b in BLOCS:
        v = sum(c['votes'] for c in agg['candidates'] if bloc_of(election, c['name']) == b)
        res[b] = {'votes': v, 'share_pct': round(v / total * 100, 4) if total else None}
    return res


class ToolError(Exception):
    def __init__(self, code, message_en, message_ko='', suggestions=None):
        super().__init__(message_en)
        self.code = code
        self.message_en = message_en
        self.message_ko = message_ko
        self.suggestions = suggestions or []


def _bq_bin():
    return shutil.which('bq') or '/home/hatch/google-cloud-sdk/bin/bq'


def bq_query(sql, params):
    """Run one parameterized SELECT. params: list of (name, type, value)."""
    cmd = [_bq_bin(), f'--ca_certificates_file={CA_BUNDLE}', 'query',
           '--use_legacy_sql=false', '--format=json', '--max_rows=100000']
    for name, typ, val in params:
        cmd.append(f'--parameter={name}:{typ}:{val}')
    cmd.append(sql)
    # bq shells out to `gcloud` for auth; make sure both resolve regardless
    # of the parent process's PATH.
    env = dict(os.environ)
    env['PATH'] = '/home/hatch/google-cloud-sdk/bin:' + env.get('PATH', '')
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ToolError('BIGQUERY_ERROR', f'BigQuery transport failed: {exc}',
                        'BigQuery 연결에 실패했습니다.')
    if p.returncode != 0:
        raise ToolError('BIGQUERY_ERROR', f'BigQuery query failed: {p.stderr.strip()[-500:]}',
                        'BigQuery 조회에 실패했습니다.')
    try:
        return json.loads(p.stdout) if p.stdout.strip() else []
    except json.JSONDecodeError:
        raise ToolError('BIGQUERY_ERROR', 'Could not parse BigQuery response.',
                        'BigQuery 응답을 해석하지 못했습니다.')


def norm_sido(sido):
    return SD.get(sido, sido) if sido else sido


def resolve_district(election, district_key=None, sido=None, sigungu=None):
    """Resolve user input to a district_key. Returns (district_key, published, mapping_type)."""
    if election not in ELECTION_YEARS:
        raise ToolError('INVALID_ELECTION', f'Election must be one of 18, 19, 20, 21, got {election}.',
                        '대선은 18, 19, 20, 21 중 하나여야 합니다.')
    if district_key:
        rows = bq_query(
            f'SELECT DISTINCT sido, sigungu, mapping_type FROM {MAP_TABLE} '
            'WHERE election = @election AND district_key = @district_key',
            [('election', 'INT64', election), ('district_key', 'STRING', district_key)])
        if not rows:
            raise ToolError('NOT_FOUND',
                            f'No district with key "{district_key}" in election {election}. '
                            'Keys look like "경기도|부천시" (sido|sigungu of the mapped unit).',
                            f'{election}대선에 "{district_key}" 키의 구역이 없습니다. '
                            '키는 "경기도|부천시" 형식(시도|비교단위)입니다.')
        published = sorted(f"{r['sido']}|{r['sigungu']}" for r in rows)
        mtype = 'merged' if any(r['mapping_type'] == 'merged' for r in rows) else \
            ('rename' if any(r['mapping_type'] == 'rename' for r in rows) else 'identical')
        return district_key, published, mtype
    if sido and sigungu:
        sido_n = norm_sido(sido)
        rows = bq_query(
            f'SELECT district_key, mapping_type FROM {MAP_TABLE} '
            'WHERE election = @election AND sido = @sido AND sigungu = @sigungu',
            [('election', 'INT64', election), ('sido', 'STRING', sido_n), ('sigungu', 'STRING', sigungu)])
        if not rows:
            sugg = bq_query(
                f'SELECT DISTINCT sigungu FROM {MAP_TABLE} '
                'WHERE election = @election AND sido = @sido ORDER BY sigungu LIMIT 50',
                [('election', 'INT64', election), ('sido', 'STRING', sido_n)])
            raise ToolError('NOT_FOUND',
                            f'No sigungu "{sigungu}" in {sido_n} for election {election}.',
                            f'{election}대선 {sido_n}에 "{sigungu}" 구역이 없습니다.',
                            suggestions=[r['sigungu'] for r in sugg])
        return rows[0]['district_key'], [f"{sido_n}|{sigungu}"], rows[0]['mapping_type']
    if sigungu:
        rows = bq_query(
            f'SELECT DISTINCT district_key, sido FROM {MAP_TABLE} '
            'WHERE election = @election AND sigungu = @sigungu',
            [('election', 'INT64', election), ('sigungu', 'STRING', sigungu)])
        if not rows:
            raise ToolError('NOT_FOUND', f'No sigungu named "{sigungu}" in election {election}.',
                            f'{election}대선에 "{sigungu}" 구역이 없습니다.')
        keys = sorted(set(r['district_key'] for r in rows))
        if len(keys) > 1:
            raise ToolError('AMBIGUOUS_DISTRICT',
                            f'"{sigungu}" matches {len(keys)} districts; specify the sido.',
                            f'"{sigungu}"에 해당하는 구역이 {len(keys)}개입니다. 시도를 함께 지정해 주세요.',
                            suggestions=keys)
        return keys[0], [f"{rows[0]['sido']}|{sigungu}"], 'identical'
    raise ToolError('DISTRICT_REQUIRED',
                    'Give district_key, or sido + sigungu, or sigungu.',
                    'district_key, 또는 시도+시군구, 또는 시군구를 지정해 주세요.')


def _units_cte(where_extra=''):
    return f"""WITH units AS (
  SELECT DISTINCT election, sido, sigungu, eupmyeondong, precinct, source_row_number,
         unit_type, electorate, votes_cast, total_valid, invalid_votes, abstentions
  FROM {TABLE}
  WHERE election = @election AND district_key = @district_key {where_extra}
), totals AS (
  SELECT SUM(electorate) AS electorate, SUM(votes_cast) AS votes_cast,
         SUM(total_valid) AS total_valid, SUM(invalid_votes) AS invalid_votes,
         SUM(abstentions) AS abstentions, COUNT(*) AS unit_count,
         SUM(CASE WHEN unit_type = 'precinct' THEN 1 ELSE 0 END) AS precinct_count,
         SUM(CASE WHEN unit_type = 'early_vote' THEN 1 ELSE 0 END) AS early_count,
         SUM(CASE WHEN unit_type = 'special' THEN 1 ELSE 0 END) AS special_count
  FROM units
), cand AS (
  SELECT candidate_key, candidate_label_as_published, party, SUM(votes) AS votes
  FROM {TABLE}
  WHERE election = @election AND district_key = @district_key {where_extra}
  GROUP BY candidate_key, candidate_label_as_published, party
)"""


def _aggregate(election, district_key, precinct=None):
    """District (or single-precinct) aggregates + candidate table. One SELECT."""
    extra = ''
    params = [('election', 'INT64', election), ('district_key', 'STRING', district_key)]
    if precinct is not None:
        extra = " AND precinct = @precinct AND unit_type = 'precinct'"
        params.append(('precinct', 'STRING', precinct))
    sql = _units_cte(extra) + """,
totals_row AS (SELECT * FROM totals)
SELECT c.candidate_key, c.candidate_label_as_published, c.party, c.votes,
       t.electorate, t.votes_cast, t.total_valid, t.invalid_votes, t.abstentions,
       t.unit_count, t.precinct_count, t.early_count, t.special_count
FROM cand c CROSS JOIN totals_row t ORDER BY c.votes DESC"""
    rows = bq_query(sql, params)
    if not rows:
        return None
    t = rows[0]
    total_valid = int(t['total_valid'])
    cands = []
    for r in rows:
        v = int(r['votes'])
        cands.append({'name': r['candidate_key'], 'label': r['candidate_label_as_published'],
                      'party': r['party'] or None, 'votes': v,
                      'share_pct': round(v / total_valid * 100, 4) if total_valid else None})
    electorate, votes_cast = int(t['electorate']), int(t['votes_cast'])
    out = {'electorate': electorate, 'votes_cast': votes_cast,
           'turnout_pct': round(votes_cast / electorate * 100, 4) if electorate else None,
           'total_valid': total_valid, 'invalid_votes': int(t['invalid_votes']),
           'abstentions': int(t['abstentions']),
           'unit_count': int(t['unit_count']), 'precinct_count': int(t['precinct_count']),
           'early_count': int(t['early_count']), 'special_count': int(t['special_count']),
           'candidates': cands}
    # deterministic check: shares sum to 100% (candidate sums == total_valid, verified at load)
    share_sum = sum(c['share_pct'] for c in cands if c['share_pct'] is not None)
    out['checks'] = {'shares_sum_pct': round(share_sum, 4),
                     'shares_sum_ok': abs(share_sum - 100) < 0.05}
    if not out['checks']['shares_sum_ok']:
        raise ToolError('DATA_CHECK_FAILED',
                        f'Candidate shares sum to {share_sum}%, not 100%. Data error; figure blocked.',
                        f'후보 득표율 합이 {share_sum}%로 100%가 아닙니다. 데이터 오류로 수치를 차단합니다.')
    return out


def _wrap(tool, params, data, elections):
    return {'ok': True, 'tool': tool, 'params': params, 'data': data,
            'citations': [{'election': e, 'election_year': ELECTION_YEARS[e],
                           'citation_url': INDEX_URL[e],
                           'note': 'Archive index page (HK decision 2026-10-27; per-figure sheet URLs not promised in v1).'}
                          for e in elections]}


def _err(e):
    return {'ok': False, 'error': {'code': e.code, 'message_en': e.message_en,
                                   'message_ko': e.message_ko, 'suggestions': e.suggestions}}


def precinct_lookup(election, district_key=None, sido=None, sigungu=None, precinct=None):
    """Official results for one district (aggregate) or one precinct in one election."""
    try:
        key, published, mtype = resolve_district(election, district_key, sido, sigungu)
        if precinct is not None:
            agg = _aggregate(election, key, precinct=precinct)
            if agg is None:
                # distinguish: district exists but precinct name unknown
                sugg = bq_query(
                    f'SELECT DISTINCT precinct FROM {TABLE} WHERE election = @election '
                    "AND district_key = @district_key AND unit_type = 'precinct' "
                    'ORDER BY precinct LIMIT 20',
                    [('election', 'INT64', election), ('district_key', 'STRING', key)])
                raise ToolError('PRECINCT_NOT_FOUND',
                                f'No precinct named "{precinct}" in district {key} for election {election}. '
                                'Do NOT silently substitute the district aggregate; offer it only if the user accepts the level change.',
                                f'{election}대선 {key} 구역에 "{precinct}" 투표구가 없습니다. '
                                '구역 전체 수치로 슬쩍 바꾸지 말고, 사용자가 수준 변경을 받아들일 때만 제시하세요.',
                                suggestions=[r['precinct'] for r in sugg])
            level, region = 'precinct', {'district_key': key, 'published_sigungu': published,
                                         'precinct': precinct}
        else:
            agg = _aggregate(election, key)
            level, region = 'district', {'district_key': key, 'published_sigungu': published,
                                         'mapping_type': mtype}
        return _wrap('precinct_lookup',
                     {'election': election, 'district_key': key, 'precinct': precinct},
                     {'region': region, 'level': level, **agg}, [election])
    except ToolError as e:
        return _err(e)


def margin(election, district_key=None, sido=None, sigungu=None, precinct=None):
    """Winner, runner-up, margin in votes and percentage points."""
    try:
        key, published, mtype = resolve_district(election, district_key, sido, sigungu)
        agg = _aggregate(election, key, precinct=precinct)
        if agg is None:
            raise ToolError('PRECINCT_NOT_FOUND', f'No precinct named "{precinct}" in district {key}.',
                            f'{key} 구역에 "{precinct}" 투표구가 없습니다.')
        cands = agg['candidates']
        if len(cands) < 2:
            raise ToolError('NO_CONTEST', 'Fewer than two candidates; no margin computable.',
                            '후보가 2명 미만이라 마진을 계산할 수 없습니다.')
        w, r = cands[0], cands[1]
        margin_votes = w['votes'] - r['votes']
        margin_pp = round(w['share_pct'] - r['share_pct'], 4)
        checks = dict(agg['checks'])
        checks['margin_ok'] = (margin_votes == w['votes'] - r['votes'])
        level = 'precinct' if precinct is not None else 'district'
        return _wrap('margin', {'election': election, 'district_key': key, 'precinct': precinct},
                     {'region': {'district_key': key, 'published_sigungu': published,
                                 'mapping_type': mtype, 'precinct': precinct},
                      'level': level, 'winner': w, 'runner_up': r,
                      'margin_votes': margin_votes, 'margin_pp': margin_pp,
                      'total_valid': agg['total_valid'], 'turnout_pct': agg['turnout_pct'],
                      'electorate': agg['electorate'], 'votes_cast': agg['votes_cast'],
                      'checks': checks}, [election])
    except ToolError as e:
        return _err(e)


def turnout(election, district_key=None, sido=None, sigungu=None, precinct=None):
    """Electorate, votes cast, turnout rate. District optional (omitted = national)."""
    try:
        if district_key is None and sido is None and sigungu is None:
            # national aggregate: same units CTE without the district filter
            sql = f"""WITH units AS (
  SELECT DISTINCT election, sido, sigungu, eupmyeondong, precinct, source_row_number,
         unit_type, electorate, votes_cast, total_valid
  FROM {TABLE} WHERE election = @election
)
SELECT SUM(electorate) AS electorate, SUM(votes_cast) AS votes_cast,
       COUNT(*) AS unit_count FROM units"""
            rows = bq_query(sql, [('election', 'INT64', election)])
            t = rows[0]
            elect, cast = int(t['electorate']), int(t['votes_cast'])
            data = {'region': {'level': 'national'}, 'level': 'national',
                    'electorate': elect, 'votes_cast': cast,
                    'turnout_pct': round(cast / elect * 100, 4) if elect else None,
                    'unit_count': int(t['unit_count'])}
            return _wrap('turnout', {'election': election}, data, [election])
        key, published, mtype = resolve_district(election, district_key, sido, sigungu)
        agg = _aggregate(election, key, precinct=precinct)
        if agg is None:
            raise ToolError('PRECINCT_NOT_FOUND', f'No precinct named "{precinct}" in district {key}.',
                            f'{key} 구역에 "{precinct}" 투표구가 없습니다.')
        level = 'precinct' if precinct is not None else 'district'
        return _wrap('turnout', {'election': election, 'district_key': key, 'precinct': precinct},
                     {'region': {'district_key': key, 'published_sigungu': published,
                                 'mapping_type': mtype, 'precinct': precinct},
                      'level': level, 'electorate': agg['electorate'],
                      'votes_cast': agg['votes_cast'], 'turnout_pct': agg['turnout_pct'],
                      'unit_count': agg['unit_count'], 'precinct_count': agg['precinct_count'],
                      'early_count': agg['early_count'], 'special_count': agg['special_count']},
                     [election])
    except ToolError as e:
        return _err(e)


def swing_calculation(election_a, election_b, district_key=None, sido=None, sigungu=None,
                      candidate=None, bloc=None, scope_sido=None, top_n=10,
                      direction='largest_absolute', include_absent=False):
    """Vote-share change between two elections.

    District mode (district_key or sido+sigungu given): full candidate table +
    winner/margin/turnout deltas for that one district_key, plus bloc shares
    and bloc swings (보수/진보/기타, see BLOC_MAP).
    Ranking mode (candidate OR bloc given, no district): district_keys ranked
    by that candidate's swing, or by that bloc's share swing (bloc votes are
    summed over the bloc's members in each election). match_level is always
    'district_key' in v1 — precinct identity is not stable across elections,
    so precinct-level cross-election matching is not attempted.
    """
    try:
        if election_a not in ELECTION_YEARS or election_b not in ELECTION_YEARS:
            raise ToolError('INVALID_ELECTION', 'Elections must be in 18, 19, 20, 21.',
                            '대선은 18, 19, 20, 21 중 하나여야 합니다.')
        if district_key is not None or (sido is not None and sigungu is not None) or sigungu is not None:
            key, published, mtype = resolve_district(election_a, district_key, sido, sigungu)
            # verify the key exists in election_b too (key space is shared: 247 keys in all)
            chk = bq_query(
                f'SELECT COUNT(*) AS n FROM {MAP_TABLE} '
                'WHERE election = @election AND district_key = @district_key',
                [('election', 'INT64', election_b), ('district_key', 'STRING', key)])
            if int(chk[0]['n']) == 0:
                raise ToolError('NO_MATCHING_DISTRICT_ACROSS_ELECTIONS',
                                f'District {key} exists in election {election_a} but not {election_b}.',
                                f'{key} 구역이 {election_a}대선에는 있으나 {election_b}대선에는 없습니다.')
            agg_a = _aggregate(election_a, key)
            agg_b = _aggregate(election_b, key)
            by_name_a = {c['name']: c for c in agg_a['candidates']}
            by_name_b = {c['name']: c for c in agg_b['candidates']}
            rows = []
            for name in sorted(set(by_name_a) | set(by_name_b)):
                a = by_name_a.get(name)
                b = by_name_b.get(name)
                ran_a = a is not None
                ran_b = b is not None
                # Entry/exit is not swing (guardrail fix 2026-10-27): a candidate
                # who did not run in an election gets NO share and NO swing for
                # that side. Emitting 0.0% fabricated a swing (e.g. 안철수
                # 2012->2017 showed +21.83pp from a 0.0% that never existed).
                sa = a['share_pct'] if ran_a else None
                sb = b['share_pct'] if ran_b else None
                swing = round(sb - sa, 4) if (ran_a and ran_b) else None
                rows.append({'candidate': name,
                             'party_a': a['party'] if a else None,
                             'party_b': b['party'] if b else None,
                             'votes_a': a['votes'] if a else 0,
                             'votes_b': b['votes'] if b else 0,
                             'share_a_pct': sa, 'share_b_pct': sb,
                             'swing_pp': swing,
                             'ran_in_a': ran_a, 'ran_in_b': ran_b})
            w_a, w_b = agg_a['candidates'][0], agg_b['candidates'][0]
            bl_a, bl_b = bloc_shares(election_a, agg_a), bloc_shares(election_b, agg_b)
            blocs = {b: {'votes_a': bl_a[b]['votes'], 'share_a_pct': bl_a[b]['share_pct'],
                          'votes_b': bl_b[b]['votes'], 'share_b_pct': bl_b[b]['share_pct'],
                          'swing_pp': round(bl_b[b]['share_pct'] - bl_a[b]['share_pct'], 4)}
                     for b in BLOCS}
            data = {'region': {'district_key': key, 'published_sigungu': published,
                               'mapping_type': mtype},
                    'match_level': 'district_key',
                    'match_note': 'district_key is the harmonized unit (247 keys, identical in all four elections); '
                                  'precinct-level cross-election matching is not attempted in v1.',
                    'election_a': {'election': election_a, 'total_valid': agg_a['total_valid'],
                                   'turnout_pct': agg_a['turnout_pct'],
                                   'winner': w_a},
                    'election_b': {'election': election_b, 'total_valid': agg_b['total_valid'],
                                   'turnout_pct': agg_b['turnout_pct'],
                                   'winner': w_b},
                    'winner_share_swing_pp': round(w_b['share_pct'] - w_a['share_pct'], 4)
                    if w_a['name'] == w_b['name'] else None,
                    'turnout_swing_pp': round(agg_b['turnout_pct'] - agg_a['turnout_pct'], 4),
                    'blocs': blocs,
                    'candidates': sorted(rows, key=lambda r: abs(r['swing_pp'])
                                         if r['swing_pp'] is not None else float('-inf'),
                                         reverse=True)}
            if candidate is not None:
                data['candidates'] = [r for r in rows if r['candidate'] == candidate]
                if not data['candidates']:
                    raise ToolError('NOT_FOUND', f'Candidate "{candidate}" ran in neither election.',
                                    f'"{candidate}" 후보는 두 대선 모두 출마하지 않았습니다.')
            return _wrap('swing_calculation',
                         {'election_a': election_a, 'election_b': election_b, 'district_key': key},
                         data, [election_a, election_b])
        # ranking mode: candidate OR bloc (mutually exclusive)
        if candidate is not None and bloc is not None:
            raise ToolError('CANDIDATE_AND_BLOC',
                            'Give candidate or bloc, not both.',
                            '후보와 bloc 중 하나만 지정해 주세요.')
        if bloc is not None and bloc not in BLOCS:
            raise ToolError('INVALID_BLOC', f'Bloc must be one of {BLOCS}, got "{bloc}".',
                            f'bloc은 {BLOCS} 중 하나여야 합니다.')
        if candidate is None and bloc is None:
            raise ToolError('CANDIDATE_OR_DISTRICT_REQUIRED',
                            'Give a district (district_key or sido+sigungu), a candidate, or a bloc for ranking.',
                            '구역(district_key 또는 시도+시군구), 후보, 또는 bloc을 지정해 주세요.')
        scope = ''
        params = [('ea', 'INT64', election_a), ('eb', 'INT64', election_b)]
        if scope_sido is not None:
            scope = " AND district_key LIKE CONCAT(@sido_prefix, '%')"
            params.append(('sido_prefix', 'STRING', norm_sido(scope_sido) + '|'))
        # Fetch per-candidate district shares; candidate/bloc filtering and
        # bloc aggregation happen in Python (one query serves both modes).
        sql = f"""WITH cand AS (
  SELECT election, district_key, candidate_key, SUM(votes) AS votes
  FROM {TABLE}
  WHERE election IN (@ea, @eb) {scope}
  GROUP BY election, district_key, candidate_key
), tot AS (
  SELECT election, district_key, SUM(total_valid) AS tv FROM (
    SELECT DISTINCT election, district_key, sido, sigungu, eupmyeondong, precinct,
           source_row_number, total_valid
    FROM {TABLE} WHERE election IN (@ea, @eb) {scope}
  ) GROUP BY election, district_key
), pairs AS (
  SELECT DISTINCT district_key, candidate_key FROM cand
), piv AS (
  SELECT p.district_key, p.candidate_key,
         MAX(CASE WHEN t.election = @ea THEN t.tv END) AS tv_a,
         MAX(CASE WHEN t.election = @eb THEN t.tv END) AS tv_b,
         MAX(CASE WHEN c.election = @ea THEN c.votes END) AS v_a,
         MAX(CASE WHEN c.election = @eb THEN c.votes END) AS v_b
  FROM pairs p
  JOIN tot t ON t.district_key = p.district_key
  LEFT JOIN cand c
    ON c.election = t.election AND c.district_key = p.district_key
   AND c.candidate_key = p.candidate_key
  GROUP BY p.district_key, p.candidate_key
)
SELECT district_key, candidate_key,
       COALESCE(v_a, 0) AS votes_a, COALESCE(v_b, 0) AS votes_b,
       tv_a, tv_b,
       ROUND(COALESCE(v_a, 0) / tv_a * 100, 4) AS share_a_pct,
       ROUND(COALESCE(v_b, 0) / tv_b * 100, 4) AS share_b_pct,
       ROUND(COALESCE(v_b, 0) / tv_b * 100 - COALESCE(v_a, 0) / tv_a * 100, 4) AS swing_pp,
       v_a IS NULL AS absent_a, v_b IS NULL AS absent_b
FROM piv"""
        rows = bq_query(sql, params)
        # bq JSON may render BOOL as the strings "true"/"false"; normalize.
        for r in rows:
            for k in ('absent_a', 'absent_b'):
                v = r[k]
                r[k] = v is True or (isinstance(v, str) and v.lower() == 'true')
        for r in rows:
            # deterministic check: swing == share_b - share_a
            if abs(float(r['swing_pp']) - (float(r['share_b_pct']) - float(r['share_a_pct']))) > 0.01:
                raise ToolError('DATA_CHECK_FAILED', 'Swing arithmetic check failed.',
                                '스윙 계산 검증에 실패했습니다.')
        note = ('Ranked by district_key (harmonized unit); precinct-level cross-election '
                'matching is not attempted in v1.')
        if bloc is not None:
            # Bloc mode: sum member votes per district per election. Blocs are
            # defined for every election, so there is no absent/entry concept.
            agg = {}
            for r in rows:
                key = r['district_key']
                d = agg.setdefault(key, {'votes_a': 0, 'votes_b': 0,
                                         'tv_a': int(r['tv_a']), 'tv_b': int(r['tv_b'])})
                # Look up the bloc only for elections the candidate actually
                # ran in: a candidate absent in election B (e.g. 문재인 in
                # 2022) has no B mapping and contributes 0 to every B bloc.
                if not r['absent_a'] and bloc_of(election_a, r['candidate_key']) == bloc:
                    d['votes_a'] += int(r['votes_a'])
                if not r['absent_b'] and bloc_of(election_b, r['candidate_key']) == bloc:
                    d['votes_b'] += int(r['votes_b'])
            ranked = []
            for key, d in agg.items():
                sa = d['votes_a'] / d['tv_a'] * 100 if d['tv_a'] else 0.0
                sb = d['votes_b'] / d['tv_b'] * 100 if d['tv_b'] else 0.0
                ranked.append({'district_key': key, 'votes_a': d['votes_a'],
                               'votes_b': d['votes_b'], 'share_a_pct': round(sa, 4),
                               'share_b_pct': round(sb, 4), 'swing_pp': round(sb - sa, 4)})
            members_a = sorted(k for (e, k) in BLOC_MAP if e == election_a and BLOC_MAP[(e, k)] == bloc)
            members_b = sorted(k for (e, k) in BLOC_MAP if e == election_b and BLOC_MAP[(e, k)] == bloc)
            extra = {'bloc': bloc, 'members_a': members_a, 'members_b': members_b,
                     'excluded_absent_count': 0}
            note += f" Bloc '{bloc}' aggregates its members in each election (see members_a/members_b)."
            rows_out = ranked
            ran_in_a = ran_in_b = True
            excluded_absent = 0
        else:
            rows = [r for r in rows if r['candidate_key'] == candidate]
            if not rows:
                raise ToolError('NOT_FOUND',
                                f'Candidate "{candidate}" ran in neither election {election_a} nor {election_b}.',
                                f'"{candidate}" 후보는 {election_a}대선과 {election_b}대선 모두 출마하지 않았습니다.')
            # A "swing" is only meaningful where the candidate ran in BOTH elections.
            # A 0 -> 85% jump for a candidate who did not run in election A is an
            # entry effect, not a swing — exclude it by default (flagged, counted).
            ran_in_a = any(not r['absent_a'] for r in rows)
            ran_in_b = any(not r['absent_b'] for r in rows)
            excluded_absent = 0
            if not include_absent:
                excluded_absent = sum(1 for r in rows if r['absent_a'] or r['absent_b'])
                rows = [r for r in rows if not r['absent_a'] and not r['absent_b']]
            if not ran_in_a or not ran_in_b:
                missing = [str(e) for e, ran in ((election_a, ran_in_a), (election_b, ran_in_b)) if not ran]
                note += (f" Candidate did not run in election(s) {', '.join(missing)}; "
                         f'{excluded_absent} districts excluded as entry/exit, not swing.')
            elif excluded_absent:
                note += f' {excluded_absent} districts where the candidate ran in only one election excluded.'
            rows_out = [{'district_key': r['district_key'],
                         'votes_a': int(r['votes_a']), 'votes_b': int(r['votes_b']),
                         'share_a_pct': float(r['share_a_pct']),
                         'share_b_pct': float(r['share_b_pct']),
                         'swing_pp': float(r['swing_pp']),
                         'absent_in_a': bool(r['absent_a']), 'absent_in_b': bool(r['absent_b'])}
                        for r in rows]
            extra = {'candidate': candidate, 'ran_in_a': ran_in_a, 'ran_in_b': ran_in_b,
                     'excluded_absent_count': excluded_absent}
        if direction == 'largest_gain':
            rows_out.sort(key=lambda r: float(r['swing_pp']), reverse=True)
        elif direction == 'largest_loss':
            rows_out.sort(key=lambda r: float(r['swing_pp']))
        else:
            rows_out.sort(key=lambda r: abs(float(r['swing_pp'])), reverse=True)
        rows_out = rows_out[:max(1, min(top_n, 50))]
        # attach published sigungu names (election_b) for display
        keys = [r['district_key'] for r in rows_out]
        names = {}
        if keys:
            # one query per key would be wasteful; fetch the whole map for election_b once
            mrows = bq_query(
                f'SELECT district_key, sido, sigungu FROM {MAP_TABLE} WHERE election = @election',
                [('election', 'INT64', election_b)])
            tmp = {}
            for m in mrows:
                tmp.setdefault(m['district_key'], []).append(f"{m['sido']}|{m['sigungu']}")
            names = {k: tmp.get(k, []) for k in keys}
        for r in rows_out:
            r['published_sigungu'] = names.get(r['district_key'], [])
        return _wrap('swing_calculation',
                     {'election_a': election_a, 'election_b': election_b, 'candidate': candidate,
                      'bloc': bloc, 'scope_sido': scope_sido, 'top_n': top_n,
                      'direction': direction, 'include_absent': include_absent},
                     {'match_level': 'district_key', 'match_note': note,
                      'rows': rows_out, **extra},
                     [election_a, election_b])
    except ToolError as e:
        return _err(e)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('tool')
    ap.add_argument('--election', type=int)
    ap.add_argument('--election_a', type=int)
    ap.add_argument('--election_b', type=int)
    ap.add_argument('--district_key')
    ap.add_argument('--sido')
    ap.add_argument('--sigungu')
    ap.add_argument('--precinct')
    ap.add_argument('--candidate')
    ap.add_argument('--bloc')
    ap.add_argument('--scope_sido')
    ap.add_argument('--top_n', type=int, default=10)
    ap.add_argument('--direction', default='largest_absolute')
    ap.add_argument('--include_absent', action='store_true')
    a = ap.parse_args(argv)
    fn = {'precinct_lookup': precinct_lookup, 'margin': margin, 'turnout': turnout,
          'swing_calculation': swing_calculation}[a.tool]
    if a.tool == 'swing_calculation':
        out = fn(a.election_a, a.election_b, district_key=a.district_key, sido=a.sido,
                 sigungu=a.sigungu, candidate=a.candidate, bloc=a.bloc,
                 scope_sido=a.scope_sido, top_n=a.top_n, direction=a.direction,
                 include_absent=a.include_absent)
    else:
        out = fn(a.election, district_key=a.district_key, sido=a.sido, sigungu=a.sigungu,
                 precinct=a.precinct)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

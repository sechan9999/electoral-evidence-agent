#!/usr/bin/env python3
"""Build kr_precinct long-form melt + district_map from GCS-canonical NEC results files.

Load rules (spec §2, corrected 2026-10-27):
- regular precinct rows: 투표구 present, not 소계/합계, no 사전
- early_vote rows: 투표구 contains 사전 (관내사전투표)
- special rows: 투표구 blank + 읍면동 in special labels (거소·선상, 관외사전, 재외, 부재자, 잘못 투입)
- EXCLUDED: national/sido/sigungu totals, 읍면동 subtotals (소계/합계 in 투표구 column)
District mapping (spec §5, approved merged-unit option).
"""
import csv, re, sys
from collections import Counter, defaultdict
import openpyxl

BASE = '/home/hatch/workspace/goals/meta-global-ai-developer-hackathon-application-startup-track/hidden_files/kr_precinct_build'

def s(v):
    return '' if v is None else str(v).strip()

def num(v):
    if v is None or str(v).strip() == '':
        return 0
    return int(str(v).replace(',', '').strip())

SD = {'강원': '강원특별자치도', '강원도': '강원특별자치도',
      '경기': '경기도', '경남': '경상남도', '경북': '경상북도',
      '광주': '광주광역시', '대구': '대구광역시', '대전': '대전광역시',
      '부산': '부산광역시', '서울': '서울특별시', '세종': '세종특별자치시',
      '울산': '울산광역시', '인천': '인천광역시', '전남': '전라남도',
      '전북': '전북특별자치도', '전라북도': '전북특별자치도',
      '제주': '제주특별자치도', '충남': '충청남도', '충북': '충청북도'}

def norm_sido(sd):
    return SD.get(sd, sd)

MERGED = {
    ('충청북도', '청원군'): ('충청북도', '청주시권'),
    ('충청북도', '청주시상당구'): ('충청북도', '청주시권'),
    ('충청북도', '청주시서원구'): ('충청북도', '청주시권'),
    ('충청북도', '청주시청원구'): ('충청북도', '청주시권'),
    ('충청북도', '청주시흥덕구'): ('충청북도', '청주시권'),
    ('경기도', '부천시'): ('경기도', '부천시'),
    ('경기도', '부천시소사구'): ('경기도', '부천시'),
    ('경기도', '부천시오정구'): ('경기도', '부천시'),
    ('경기도', '부천시원미구'): ('경기도', '부천시'),
    ('경기도', '화성시'): ('경기도', '화성시'),
    ('경기도', '화성시갑'): ('경기도', '화성시'),
    ('경기도', '화성시을'): ('경기도', '화성시'),
}
RENAMES = {
    ('경기도', '여주군'): ('경기도', '여주시'),
    ('인천광역시', '남구'): ('인천광역시', '미추홀구'),
    ('경상북도', '군위군'): ('대구광역시', '군위군'),
    ('세종특별자치시', ''): ('세종특별자치시', '세종특별자치시'),
}

def district_key(sido_norm, sigungu):
    p = (sido_norm, sigungu)
    if p in MERGED:
        return MERGED[p], 'merged'
    if p in RENAMES:
        return RENAMES[p], 'rename'
    return p, 'identical'

SPECIAL_EMD = {'국내부재자투표', '재외투표', '거소·선상투표', '관외사전투표',
               '재외투표(공관)', '잘못 투입·구분된 투표지'}

def parse_party(label):
    """'더불어민주당 문재인' -> ('더불어민주당','문재인'); '이재명' -> ('','이재명').

    Rule: the LAST token is the person's name, the rest is party/affiliation.
    (The old rule — first token ends with '당' — missed 국민의힘 and 무소속,
    which don't end with 당. Korean person names never contain a space.)
    """
    parts = label.split()
    if len(parts) >= 2:
        return ' '.join(parts[:-1]), parts[-1]
    return '', label


# 20th published file has name-only headers (no party column). Parties below
# were supplied by HK (2026-10-27) from the candidate registration record.
# 안철수 (국민의당) and 김동연 (새로운물결) registered but withdrew before
# election day: they have NO rows in the file, so they appear here only as a
# record, never as melt rows.
PARTY_20 = {'윤석열': '국민의힘', '이재명': '더불어민주당', '심상정': '정의당',
            '허경영': '국가혁명당', '김재연': '진보당', '조원진': '우리공화당',
            '오준호': '기본소득당', '김민찬': '한류연합당', '이경희': '통일한국당',
            '이백윤': '노동당', '김경재': '신자유민주연합', '옥은호': '새누리당'}

def classify(sido, sigungu, emd, prec):
    if sido == '전국':
        return 'exclude:national'
    if '합계' in sigungu:
        return 'exclude:sido_total'
    if prec == '':
        if emd in SPECIAL_EMD:
            return 'special'
        if emd == '합계':
            return 'exclude:sigungu_subtotal'
        return 'exclude:blank_unexpected'
    if prec in ('소계', '합계'):
        return 'exclude:emd_subtotal'
    if '사전' in prec:
        return 'early_vote'
    return 'precinct'

ELECTIONS = [
    # (election_no, year, kind, path, citation_url)
    (18, 2012, 'xlsx', f'{BASE}/k18_results.xlsx', 'https://storage.googleapis.com/k18-archive/index.html'),
    (19, 2017, 'csv', f'{BASE}/k19_results.csv', 'https://storage.googleapis.com/k19-archive/index.html'),
    (20, 2022, 'csv', f'{BASE}/k20_results.csv', 'https://storage.googleapis.com/k20-tally-archive/index.html'),
    (21, 2025, 'csv', f'{BASE}/k21_results.csv', 'https://storage.googleapis.com/k21-tally-archive/index.html'),
]

def load_csv(path):
    for enc in ['utf-8-sig', 'cp949']:
        try:
            with open(path, encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f'cannot decode {path}')

def load_xlsx(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    allrows = list(wb.active.iter_rows(values_only=True))
    hdr, sub = allrows[0], allrows[1]
    print('18th header row:', [s(c) for c in hdr])
    print('18th subheader row:', [s(c) for c in sub])
    return hdr, sub, allrows[2:]

melt_rows = []
map_rows = set()
report = []
national_published = {}
sido_total_sums = defaultdict(lambda: [0, 0])

for eno, year, kind, path, cite in ELECTIONS:
    stats = Counter()
    cand_cols = []  # (label, party, name)
    units = []  # (sido_norm, sigungu, dkey, dmap, emd, prec, utype, level, electorate, votes_cast, total_valid, invalid, abst, src_row, cand_votes)
    if kind == 'xlsx':
        hdr, sub, data = load_xlsx(path)
        # candidate columns: subheader is a candidate name (not 계/무효/기권/blank)
        for i, (h, sh) in enumerate(zip(hdr, sub)):
            h, sh = s(h), s(sh)
            if i >= 6 and sh and sh not in ('계', '무효', '기권'):
                cand_cols.append((i, sh))
        # total/invalid/abstention columns follow the candidate columns:
        # col 12 '후보자별 득표수\n계', col 13 '무효\n투표수', col 14 '기권수'
        c_total, c_inv, c_abst = cand_cols[-1][0] + 1, cand_cols[-1][0] + 2, cand_cols[-1][0] + 3
        assert '계' in s(hdr[c_total]), hdr[c_total]
        assert '무효' in s(hdr[c_inv]), hdr[c_inv]
        assert '기권' in s(hdr[c_abst]), hdr[c_abst]
        print(f'18th candidate cols: {[(i, sh) for i, sh in cand_cols]} total={c_total} invalid={c_inv} abst={c_abst}')
        assert len(cand_cols) == 6, f'expected 6 candidates, got {len(cand_cols)}'
        for rn, r in enumerate(data, start=3):
            sido, sig, emd, prec = s(r[0]), s(r[1]), s(r[2]), s(r[3])
            cls = classify(sido, sig, emd, prec)
            stats[cls] += 1
            if cls.startswith('exclude'):
                if sido == '전국':
                    national_published[eno] = (num(r[4]), num(r[5]))
                if cls == 'exclude:sido_total':
                    sido_total_sums[eno][0] += num(r[4])
                    sido_total_sums[eno][1] += num(r[5])
                continue
            sido_n = norm_sido(sido)
            dkey, dmap = district_key(sido_n, sig)
            level = 'precinct' if cls == 'precinct' else 'sigungu'
            units.append((sido_n, sig, dkey, dmap, emd, prec, cls, level,
                          num(r[4]), num(r[5]), num(r[c_total]), num(r[c_inv]), num(r[c_abst]),
                          rn, [num(r[i]) for i, _ in cand_cols]))
            map_rows.add((eno, year, sido_n, sig, f'{dkey[0]}|{dkey[1]}', dmap))
        cand_labels = [sh for _, sh in cand_cols]
    else:
        rows = load_csv(path)
        keys = list(rows[0].keys())
        # candidate columns: between 투표수 and 계
        i_vote = keys.index('투표수')
        i_tot = keys.index('계')
        cand_labels = [keys[i] for i in range(i_vote + 1, i_tot)]
        print(f'{eno}th candidates ({len(cand_labels)}): {cand_labels}')
        for rn, r in enumerate(rows, start=2):
            sido, sig, emd, prec = s(r['시도']), s(r['구시군']), s(r['읍면동']), s(r['투표구'])
            cls = classify(sido, sig, emd, prec)
            stats[cls] += 1
            if cls.startswith('exclude'):
                if sido == '전국':
                    national_published[eno] = (num(r['선거인수']), num(r['투표수']))
                if cls == 'exclude:sido_total':
                    sido_total_sums[eno][0] += num(r['선거인수'])
                    sido_total_sums[eno][1] += num(r['투표수'])
                continue
            sido_n = norm_sido(sido)
            dkey, dmap = district_key(sido_n, sig)
            level = 'precinct' if cls == 'precinct' else 'sigungu'
            units.append((sido_n, sig, dkey, dmap, emd, prec, cls, level,
                          num(r['선거인수']), num(r['투표수']), num(r['계']), num(r['무효']), num(r['기권']),
                          rn, [num(r[c]) for c in cand_labels]))
            map_rows.add((eno, year, sido_n, sig, f'{dkey[0]}|{dkey[1]}', dmap))
    # melt
    for (sido_n, sig, dkey, dmap, emd, prec, utype, level,
         elect, cast, tot, inv, abst, rn, cvotes) in units:
        for label, v in zip(cand_labels, cvotes):
            # 18th xlsx labels contain a newline ('새누리당\n박근혜') — flatten
            # it, or bq load breaks on the embedded newline without
            # --allow_quoted_newlines
            label = label.replace('\n', ' ').replace('\r', ' ')
            party, name = parse_party(label)
            if eno == 20:
                party = PARTY_20.get(name, party)
            turnout = round(cast / elect, 6) if elect else None
            melt_rows.append([eno, year, sido_n, sig, f'{dkey[0]}|{dkey[1]}', emd, prec,
                              utype, level, name, label, party, v, tot, inv, elect, cast,
                              abst, turnout, cite, path.split('/')[-1], rn])
    # verification per election
    u_by_type = Counter(u[6] for u in units)
    sum_elect = sum(u[8] for u in units)
    sum_cast = sum(u[9] for u in units)
    # national reference: 전국 row if present, else sum of 17 sido totals
    # (the 20th file has no 전국 row)
    if eno in national_published:
        pub_elect, pub_cast = national_published[eno]
        se, sc = sido_total_sums[eno]
        print(f'   (check: sido-total sums electorate={se} votes={sc}; '
              f'전국-row diff: {se - pub_elect}, {sc - pub_cast})')
    else:
        pub_elect, pub_cast = sido_total_sums[eno]
        print(f'   (no 전국 row in file; using sum of 17 sido totals as reference)')
    cand_sum = sum(sum(u[14]) for u in units)
    tot_sum = sum(u[10] for u in units)
    keys_by_e = set(m[4] for m in map_rows if m[0] == eno)
    report.append((eno, dict(stats), dict(u_by_type), len(units), len(melt_rows),
                   sum_elect, pub_elect, sum_cast, pub_cast, cand_sum, tot_sum, len(keys_by_e)))
    print(f'== {eno}th: stats={dict(stats)} units_by_type={dict(u_by_type)} '
          f'units={len(units)} district_keys={len(keys_by_e)}')
    print(f'   electorate sum={sum_elect} published={pub_elect} diff={sum_elect - pub_elect}')
    print(f'   votes_cast sum={sum_cast} published={pub_cast} diff={sum_cast - pub_cast}')
    print(f'   candidate-vote sum={cand_sum} total_valid sum={tot_sum} diff={cand_sum - tot_sum}')

# cross-election district keys
all_keys = {}
for m in map_rows:
    all_keys.setdefault(m[0], set()).add(m[4])
common = set.intersection(*all_keys.values())
print(f'district_keys per election: {{ {", ".join(f"{k}: {len(v)}" for k, v in sorted(all_keys.items()))} }}')
print(f'common district_keys across all 4 elections: {len(common)}')

# spot checks: merged-unit sums from raw loaded units
def unit_sum(eno, pairs, col=10):
    tot = 0
    # recompute from melt? use units is out of scope here; do a direct raw check below
    return tot

# write outputs
with open(f'{BASE}/kr_precinct_long.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['election', 'election_year', 'sido', 'sigungu', 'district_key', 'eupmyeondong',
                'precinct', 'unit_type', 'level', 'candidate_key', 'candidate_label_as_published',
                'party', 'votes', 'total_valid', 'invalid_votes', 'electorate', 'votes_cast',
                'abstentions', 'turnout_rate_computed', 'citation_url', 'source_file', 'source_row_number'])
    w.writerows(melt_rows)
with open(f'{BASE}/district_map.csv', 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['election', 'election_year', 'sido', 'sigungu', 'district_key', 'mapping_type'])
    w.writerows(sorted(map_rows))
print(f'WROTE kr_precinct_long.csv: {len(melt_rows)} rows; district_map.csv: {len(map_rows)} rows')

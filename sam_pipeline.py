#!/usr/bin/env python3
"""SAM 3.1 tally-sheet verification pipeline (Meta hackathon, sample track).

Pipeline: sample pages -> fetch sheet image -> SAM segment vote cells ->
DocAI OCR tokens -> numbers inside SAM boxes -> compare against published
NEC totals (kr_precinct) -> match-rate report.

Honest status (2026-10-27):
- SAM live calls ARE wired and verified: POST https://api.meta.ai/v1/responses,
  model 'sam-3.1' (the pricing page's 'sam-3-1' slug 404s), auth via the
  registered custom.meta-model-api credential (surrogate exchange; key
  never in code). $150/team hackathon credits received 2026-10-27; SAM
  costs $2.50/1,000 images.
- Verified prompt behaviour on 21st tally sheets: 'table' segments table
  regions; 'vote count column', 'numbers', 'column of numbers',
  'total column', 'votes' all return EMPTY. SAM's text grounding does not
  isolate table columns on this document type, so the live backend derives
  the final column geometrically (rightmost dense numeric cluster inside
  SAM table regions) and says so — it never claims SAM found the column.
- The `mock` backend returns the digitization pipeline's known final-column
  band (x 0.762-0.848) as the "segmented" vote column. A mock run validates
  every stage except SAM's actual box quality: fetch, DocAI tokens, anchor
  matching, number parsing, NEC comparison, reporting. Its match rate must
  NEVER be reported as a SAM result — it is a pipeline integration test.
- OCR reuses the digitization project's Document AI processor (same GCP
  project, same pattern as digitization_pilot/run_docai.py). ~$0.005/page at
  the digitization run's realized rate; a 10-page sample costs ~$0.05.
  DocAI raw responses are cached under workdir/docai_cache/ so reruns cost $0.

Ground truth: kr_precinct published totals (NEC results file), NOT the
digitization CSV. The digitization CSV is a secondary reference only
(DocAI-vs-NEC agreement was already measured at 98.76% field match).
A page is matched to its published unit by exact candidate-vote vector
match within its district (the same idea as the digitization nec_match).
"""
import base64
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kr_tools import bq_query, resolve_district  # noqa: E402

DOCAI_PROCESSOR = 'projects/38273401034/locations/us/processors/7ec0584f4e2ddb3'
DOCAI_URL = f'https://us-documentai.googleapis.com/v1/{DOCAI_PROCESSOR}:process'
CANDS_21 = ['이재명', '김문수', '이준석', '권영국', '송진호']
REGION_SIDO = {'busan': '부산광역시', 'chungbuk': '충청북도', 'chungnam': '충청남도',
               'daegu': '대구광역시', 'daejeon': '대전광역시', 'gangwon': '강원특별자치도',
               'gwangju': '광주광역시', 'gyeongbuk': '경상북도', 'gyeonggi': '경기도',
               'gyeongnam': '경상남도', 'incheon': '인천광역시', 'jeju': '제주특별자치도',
               'jeonbuk': '전북특별자치도', 'jeonnam': '전라남도',
               'sejong': '세종특별자치시', 'seoul': '서울특별시', 'ulsan': '울산광역시'}
# Fixed bands from digitization_pilot/run_docai.py (normalized x). The mock
# segmenter returns the 'final' band as the vote-count column.
BANDS = {'classified': (0.372, 0.452), 'stack': (0.452, 0.503),
         'totalcol': (0.503, 0.597), 'a': (0.600, 0.685),
         'rechecked': (0.685, 0.762), 'final': (0.762, 0.848)}
ROW_TOL = 0.0115
NUM = re.compile(r'^\(?[\d,]+\)?$')


def gcs_ls(url):
    p = subprocess.run(['gcloud', 'storage', 'ls', url], capture_output=True, text=True,
                       env={**os.environ, 'PATH': '/home/hatch/google-cloud-sdk/bin:' + os.environ.get('PATH', '')})
    return p.stdout.strip().split() if p.returncode == 0 else []


def gcs_cp(src, dst):
    env = {**os.environ, 'PATH': '/home/hatch/google-cloud-sdk/bin:' + os.environ.get('PATH', '')}
    p = subprocess.run(['gcloud', 'storage', 'cp', src, dst], capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f'gcloud storage cp failed: {p.stderr.strip()[-300:]}')


def find_image(region, file_id, page):
    """Sheet JPG naming has TWO conventions in the bucket: files with <100
    pages use p-01.jpg (2-digit), files with >=100 pages use p-001.jpg
    (3-digit). Try both; return the first existing URL or None."""
    for name in (f'p-{page:02d}.jpg', f'p-{page:03d}.jpg'):
        url = f'gs://k21-tally-archive/{region}/{file_id}/{name}'
        if gcs_ls(url):
            return url
    return None
    env = {**os.environ, 'PATH': '/home/hatch/google-cloud-sdk/bin:' + os.environ.get('PATH', '')}
    p = subprocess.run(['gcloud', 'storage', 'cp', src, dst], capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f'gcloud storage cp failed: {p.stderr.strip()[-300:]}')


def docai_token():
    env = {**os.environ, 'PATH': '/home/hatch/google-cloud-sdk/bin:' + os.environ.get('PATH', '')}
    out = subprocess.run(['bash', '-c', 'unset PYTHONPATH; gcloud auth print-access-token'],
                         capture_output=True, text=True, env=env)
    return out.stdout.strip()


def docai_ocr(image_path, tok):
    with open(image_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = 'image/jpeg' if image_path.lower().endswith(('.jpg', '.jpeg')) else 'image/png'
    body = {'rawDocument': {'content': b64, 'mimeType': mime},
            'processOptions': {'ocrConfig': {'enableNativePdfParsing': False}}}
    req = urllib.request.Request(DOCAI_URL, data=json.dumps(body).encode(),
                                 headers={'Authorization': f'Bearer {tok}',
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=300) as resp:
        doc = json.loads(resp.read())['document']
    text = doc['text']
    toks = []
    for page in doc.get('pages', []):
        for tk in page.get('tokens', []):
            lay = tk['layout']
            segs = lay['textAnchor']['textSegments']
            s = ''.join(text[int(g.get('startIndex', 0)):int(g.get('endIndex'))] for g in segs).strip()
            vs = lay['boundingPoly']['normalizedVertices']
            xs = [v.get('x', 0) for v in vs]
            ys = [v.get('y', 0) for v in vs]
            if s:
                toks.append({'t': s, 'cx': sum(xs) / len(xs), 'cy': sum(ys) / len(ys),
                             'x0': min(xs), 'x1': max(xs)})
    return toks


class MockSamSegmenter:
    """Returns the known final-column band. Simulates PERFECT segmentation;
    measures the rest of the pipeline, not SAM."""

    def segment(self, image_path, prompts):
        b = BANDS['final']
        return [{'label': 'final_vote_column', 'x0': b[0], 'x1': b[1],
                 'y0': 0.0, 'y1': 1.0, 'score': 1.0, 'source': 'mock'}]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError('redirect refused, verify the endpoint')


class MetaApiSamSegmenter:
    """Live SAM 3.1 via Meta Model API (verified 2026-10-27).

    POST https://api.meta.ai/v1/responses, model 'sam-3.1' (note the DOT:
    the pricing page's 'sam-3-1' slug 404s with model_not_found). Auth via
    the registered custom.meta-model-api credential (surrogate exchange;
    the key never appears here). Input: input_text prompt + input_image
    (base64 data URI). Output: segmentation records as special-token text
    in the output_text lane:
    <Nf>id<|box;x1..;y1..;x2..;y2..;w..;h..|><|mask;...|>.
    Only boxes are used; masks are ignored (column geometry needs boxes).

    Verified prompt behaviour on 21st tally sheets (2026-10-27, daejeon
    sheet): 'table' -> 6 table regions; 'vote count column', 'numbers',
    'column of numbers', 'total column', 'votes' -> all EMPTY. SAM's text
    grounding segments the table as an object; it does not isolate table
    columns or numbers on this document type. The live extractor therefore
    derives the final column geometrically from DocAI tokens inside the
    SAM table regions (rightmost dense numeric cluster), instead of
    claiming SAM found the column."""

    def __init__(self, prompts=('table',)):
        self.prompts = prompts

    def segment(self, image_path, prompts=None):
        sys.path.insert(0, '/opt/muse/skills/custom-connector-setup/bin')
        from dynamic_credentials import add_surrogate_to_request, read_json_response
        with open(image_path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        out = []
        for prompt in (prompts or self.prompts):
            body = {'model': 'sam-3.1',
                    'input': [{'role': 'user', 'content': [
                        {'type': 'input_text', 'text': prompt},
                        {'type': 'input_image',
                         'image_url': f'data:image/jpeg;base64,{b64}'},
                    ]}]}
            req = urllib.request.Request(
                'https://api.meta.ai/v1/responses',
                data=json.dumps(body).encode(),
                headers={'Content-Type': 'application/json'}, method='POST')
            add_surrogate_to_request(req, 'custom.meta-model-api',
                                     allowed_hosts=['api.meta.ai'])
            # Retry on gateway/rate errors: the 25-page run died on a 502
            # at page 23 with no retry and no checkpoint, losing 22 pages
            # of in-memory results. Timeout 120s bounds a hung call.
            data = None
            last_exc = None
            for attempt, wait in enumerate((0, 10, 30, 60)):
                if wait:
                    time.sleep(wait)
                try:
                    with urllib.request.build_opener(NoRedirect()).open(req, timeout=120) as resp:
                        data = read_json_response(resp)
                    break
                except urllib.error.HTTPError as exc:
                    last_exc = exc
                    if exc.code < 500:
                        raise
                except urllib.error.URLError as exc:
                    last_exc = exc
            if data is None:
                raise last_exc
            text = data['output'][0]['content'][0]['text']
            for oid, x1, y1, x2, y2, w, h in re.findall(
                    r'(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>', text):
                out.append({'label': 'table', 'x0': int(x1) / int(w),
                            'y0': int(y1) / int(h), 'x1': int(x2) / int(w),
                            'y1': int(y2) / int(h), 'score': None,
                            'source': 'sam-3.1', 'object_id': oid})
        return out


def extract_votes(toks, boxes):
    """Candidate rows: anchor = candidate-name token; value = number tokens
    inside the final-column box on the anchor's row (same idea as the
    digitization parser's cell_value, with two hardenings: number tokens must
    lie FULLY inside the column box (edge bleed from the neighbouring column
    caused a digit-merge error on one test page), and when a name occurs
    several times (e.g. header + table), the anchor whose row actually has
    numbers in the column wins."""
    col = [b for b in boxes if b['label'] == 'final_vote_column']
    if not col:
        return {}, ['no final-column box from segmenter']
    b = col[0]
    res, notes = {}, []
    for cand in CANDS_21:
        anchors = [t for t in toks if t['t'] == cand]
        if not anchors:
            res[cand] = None
            notes.append(f'{cand}: anchor not found')
            continue
        # Token selection uses the token CENTRE inside the column (cx-inside).
        # A fully-inside-x rule was tried and reverted: DocAI number tokens
        # routinely straddle the column edges, and it collapsed previously
        # perfect pages (gwangju, ulsan) to all-None. Centre-inside is the
        # digitization parser's rule and the empirically better one.
        # Anchor choice: when a name occurs several times, the anchor whose
        # row actually has numbers in the column wins.
        best, best_parts = None, []
        for anc in sorted(anchors, key=lambda t: t['cy']):
            parts = [t for t in toks
                     if b['x0'] <= t['cx'] <= b['x1']
                     and abs(t['cy'] - anc['cy']) <= ROW_TOL
                     and NUM.match(t['t']) and '(' not in t['t']]
            if len(parts) > len(best_parts):
                best, best_parts = anc, parts
        recovered = False
        if not best_parts:
            # Recovery pass: the anchor row may be vertically offset from
            # its numbers (small/low-contrast rows). Take the nearest
            # number token in the column to any anchor occurrence, then
            # join tokens on THAT token's row. Cap 0.02: beyond that, the
            # token more likely belongs to another row.
            col_nums = [t for t in toks
                        if b['x0'] <= t['cx'] <= b['x1']
                        and NUM.match(t['t']) and '(' not in t['t']]
            if col_nums:
                near = min(col_nums,
                           key=lambda t: min(abs(t['cy'] - a['cy']) for a in anchors))
                if min(abs(near['cy'] - a['cy']) for a in anchors) <= 0.02:
                    best_parts = [t for t in col_nums
                                  if abs(t['cy'] - near['cy']) <= ROW_TOL]
                    recovered = True
                    notes.append(f'{cand}: recovered via nearest number row')
        if not best_parts:
            res[cand] = None
            notes.append(f'{cand}: no number in column on anchor row')
            continue
        if len(anchors) > 1 and not recovered:
            notes.append(f'{cand}: {len(anchors)} anchors, chose row with numbers')
        best_parts.sort(key=lambda t: t['cx'])
        s = ''.join(p['t'] for p in best_parts).replace(',', '')
        res[cand] = int(s) if s.isdigit() else None
        if res[cand] is None:
            notes.append(f'{cand}: unparseable number parts {[p["t"] for p in best_parts]}')
    return res, notes


def tag_scope(toks):
    """Scope tag from the sheet title zone (top quarter of the page).

    Regular precinct sheets and early/special sheets look identical to the
    segmenter, but only precinct sheets have a matching unit in kr_precinct
    (early votes are aggregated at district level there). The title
    carries the marker: [관내사전], [관외사전], 거소·선상, 재외. Scan
    title-zone tokens (cy <= 0.25) for those markers; anything else is a
    precinct sheet. Returns 'precinct' or 'early'."""
    markers = ('관내사전', '관외사전', '관내', '관외', '거소', '선상', '재외')
    # Note: DocAI splits '[관내사전]' into '[', '관내', '사전', ']' tokens,
    # so the full 4-char marker never matches a single token. The component
    # tokens ('관내', '관외', ...) are what actually appear.
    # Refined 2026-10-27 (50-page run): overseas ballots (재외) ARE a
    # published district-level unit and matchable, while early-vote station
    # sheets (관내/관외) are per-station partials against district totals
    # and cannot match. So 재외 returns 'overseas', the rest 'early'.
    found = {t['t'] for t in toks
             if t['cy'] <= 0.25 and any(m in t['t'] for m in markers)}
    if '재외' in found:
        return 'overseas'
    if found:
        return 'early'
    return 'precinct'
    for t in toks:
        if t['cy'] <= 0.25 and any(m in t['t'] for m in markers):
            return 'early'
    return 'precinct'


def derive_final_column(toks, boxes):
    """Rightmost dense numeric cluster inside SAM table regions.

    Number tokens inside any table box, clustered by centre-x (gap > 0.02
    splits); the rightmost cluster with >= 4 tokens is the final column
    (on these sheets the final count is the rightmost numeric column by
    layout). Returns (x0, x1) in normalized coords, or None."""
    nums = [t for t in toks if NUM.match(t['t']) and '(' not in t['t']
            and any(b['x0'] <= t['cx'] <= b['x1'] and b['y0'] <= t['cy'] <= b['y1']
                    for b in boxes)]
    if not nums:
        return None
    nums.sort(key=lambda t: t['cx'])
    clusters, cur = [], [nums[0]]
    for t in nums[1:]:
        if t['cx'] - cur[-1]['cx'] > 0.02:
            clusters.append(cur)
            cur = [t]
        else:
            cur.append(t)
    clusters.append(cur)
    dense = [c for c in clusters if len(c) >= 4]
    if not dense:
        return None
    col = dense[-1]
    return (min(t['x0'] for t in col) - 0.005, max(t['x1'] for t in col) + 0.005)


def extract_votes_live(toks, boxes):
    """Same anchor logic as extract_votes, but the column comes from
    derive_final_column (SAM table regions + token geometry), not the
    fixed digitization band. Returns (votes, notes, col)."""
    col = derive_final_column(toks, boxes)
    if col is None:
        return {}, ['no dense numeric column inside SAM table regions'], None
    fake = [{'label': 'final_vote_column', 'x0': col[0], 'x1': col[1]}]
    votes, notes = extract_votes(toks, fake)
    return votes, notes, col


def district_units(election, district_key):
    """All units in a district with per-candidate votes (for vector matching)."""
    sql = """SELECT sido, sigungu, eupmyeondong, precinct, unit_type,
           candidate_key, SUM(votes) AS votes
    FROM `electoral-hub-510410.elections.kr_precinct`
    WHERE election = @election AND district_key = @district_key
    GROUP BY sido, sigungu, eupmyeondong, precinct, unit_type, candidate_key"""
    rows = bq_query(sql, [('election', 'INT64', election), ('district_key', 'STRING', district_key)])
    units = {}
    for r in rows:
        ukey = (r['sido'], r['sigungu'], r['eupmyeondong'], r['precinct'], r['unit_type'])
        units.setdefault(ukey, {})[r['candidate_key']] = int(r['votes'])
    return units


def match_unit(extracted, units):
    """Exact candidate-vote vector match (on all non-None extracted values).
    Returns (unit_key|None, n_matched_units)."""
    avail = {c: v for c, v in extracted.items() if v is not None}
    if not avail:
        return None, 0
    hits = [uk for uk, vec in units.items()
            if all(vec.get(c) == v for c, v in avail.items())]
    return (hits[0] if len(hits) == 1 else None), len(hits)


def sample_pages(dig_csv, n, seed):
    rows = [r for r in csv.DictReader(open(dig_csv, encoding='utf-8-sig'))
            if r['status'] == 'ok' and r['nec_matched'] == '1']
    rnd = random.Random(seed)
    by_region = {}
    for r in rows:
        by_region.setdefault(r['region'], []).append(r)
    regions = sorted(by_region)
    rnd.shuffle(regions)
    picked, ri = [], 0
    while len(picked) < n and regions:
        reg = regions[ri % len(regions)]
        pool = by_region[reg]
        if pool:
            picked.append(pool.pop(rnd.randrange(len(pool))))
        ri += 1
        if ri > n * len(regions) + 10:
            break
    return picked


def run(dig_csv, n, seed, workdir, backend):
    os.makedirs(f'{workdir}/images', exist_ok=True)
    os.makedirs(f'{workdir}/docai_cache', exist_ok=True)
    os.makedirs(f'{workdir}/sam_cache', exist_ok=True)
    segmenter = MockSamSegmenter() if backend == 'mock' else MetaApiSamSegmenter()
    pages = sample_pages(dig_csv, n, seed)
    tok = docai_token()  # both backends need DocAI tokens for the OCR stage
    results = []

    def write_partial():
        json.dump({'partial': True, 'pages': results},
                  open(f'{workdir}/sam_report_partial.json', 'w', encoding='utf-8'),
                  ensure_ascii=False)
    for pg in pages:
        pid, region, district = pg['page_id'], pg['region'], pg['district']
        file_id, page_no = pg['page_id'].split('_')[2], int(pg['page'])
        img_url = find_image(region, file_id, page_no)
        rec = {'page_id': pid, 'region': region, 'district': district,
               'precinct_csv': pg['precinct'], 'image_url': img_url}
        if not img_url:
            rec['status'] = 'image_missing'
            results.append(rec)
            continue
        img_path = f'{workdir}/images/{pid}.jpg'
        if not os.path.exists(img_path):
            gcs_cp(img_url, img_path)
        cache = f'{workdir}/docai_cache/{pid}.json'
        if os.path.exists(cache):
            toks = json.load(open(cache, encoding='utf-8'))
            rec['docai_cached'] = True
        else:
            toks = docai_ocr(img_path, tok)
            json.dump(toks, open(cache, 'w', encoding='utf-8'), ensure_ascii=False)
            rec['docai_cached'] = False
        rec['scope'] = tag_scope(toks)
        # SAM boxes are cached per page: a re-run after a crash skips the
        # pages that already segmented (the 25-page run's 22 done pages
        # were lost only because results lived in memory alone).
        sam_cache = f'{workdir}/sam_cache/{pid}.json'
        try:
            if backend == 'meta_api' and os.path.exists(sam_cache):
                boxes = json.load(open(sam_cache, encoding='utf-8'))
                rec['sam_cached'] = True
            else:
                boxes = segmenter.segment(img_path, ['final vote count column']
                                          if backend == 'mock' else ['table'])
                if backend == 'meta_api':
                    json.dump(boxes, open(sam_cache, 'w', encoding='utf-8'),
                              ensure_ascii=False)
                rec['sam_cached'] = False
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill the run
            rec['status'] = 'segment_failed'
            rec['error'] = str(exc)[:300]
            results.append(rec)
            write_partial()
            continue
        rec['boxes'] = boxes
        if backend == 'mock':
            extracted, notes = extract_votes(toks, boxes)
        else:
            extracted, notes, col = extract_votes_live(toks, boxes)
            rec['derived_column'] = col
        rec['extracted'] = extracted
        rec['notes'] = notes
        # Second-engine guard: digitization's Gemini-final for this page
        # (same input file, independent engine output). On disagreement,
        # abstain (None) rather than trust one engine; the published-unit
        # match below stays the only arbiter of correctness. A magnitude
        # heuristic was rejected: dominant winners legitimately exceed 5x
        # the other values in strongholds, so ratios would false-positive.
        for cand in CANDS_21:
            g_raw = (pg.get(f'g_fn_{cand}') or '').strip()
            if not g_raw:
                continue
            try:
                g_val = int(g_raw.replace(',', ''))
            except ValueError:
                continue
            v = extracted.get(cand)
            if v is not None and v != g_val:
                extracted[cand] = None
                notes.append(f'{cand}: second-engine disagreement '
                             f'(docai {v} vs gemini {g_val}), abstained')
            elif v is None:
                notes.append(f'{cand}: docai missed, gemini cross-check has {g_val}')
        # ground truth: published unit by vector match
        # Digitization district names sometimes carry the file id in
        # parentheses (e.g. '수원시권선구(39112)'); strip it before resolving.
        sido = REGION_SIDO.get(region)
        district_clean = re.sub(r'\(.*\)$', '', district).strip()
        try:
            key, published, _ = resolve_district(21, sido=sido, sigungu=district_clean)
            units = district_units(21, key)
            ukey, nhits = match_unit(extracted, units)
            rec['district_key'] = key
            rec['unit_match'] = {'unit': ukey, 'n_hits': nhits}
            if ukey is not None:
                pub = units[ukey]
                rec['published'] = {c: pub.get(c) for c in CANDS_21}
                rec['cell_match'] = {c: (extracted[c] == pub.get(c)) if extracted[c] is not None else None
                                     for c in CANDS_21}
        except Exception as exc:  # noqa: BLE001 - report, don't crash the run
            rec['ground_truth_error'] = str(exc)[:300]
        results.append(rec)
        write_partial()
        time.sleep(0.2)
    # summary
    cells = sum(1 for r in results for c in CANDS_21
                if r.get('cell_match', {}).get(c) is not None)
    matched = sum(1 for r in results for c in CANDS_21
                  if r.get('cell_match', {}).get(c) is True)
    perfect = sum(1 for r in results
                  if r.get('cell_match') and all(v is True for v in r['cell_match'].values()))
    n_compared = sum(1 for r in results if r.get('cell_match'))
    by_scope = {}
    for scope in ('precinct', 'early', 'overseas'):
        sub = [r for r in results if r.get('scope') == scope]
        sub_cells = sum(1 for r in sub for c in CANDS_21
                        if r.get('cell_match', {}).get(c) is not None)
        sub_matched = sum(1 for r in sub for c in CANDS_21
                          if r.get('cell_match', {}).get(c) is True)
        by_scope[scope] = {
            'n_pages': len(sub),
            'n_compared_pages': sum(1 for r in sub if r.get('cell_match')),
            'cells_compared': sub_cells, 'cells_matched': sub_matched,
            'cell_match_rate': round(sub_matched / sub_cells, 4) if sub_cells else None,
            'perfect_pages': sum(1 for r in sub
                                 if r.get('cell_match') and all(v is True for v in r['cell_match'].values()))}
    summary = {'backend': backend, 'n_pages': len(results),
               'n_compared_pages': n_compared,
               'n_failed': sum(1 for r in results if r.get('status') in ('segment_failed', 'image_missing')),
               'cells_compared': cells, 'cells_matched': matched,
               'cell_match_rate': round(matched / cells, 4) if cells else None,
               'perfect_pages': perfect,
               'by_scope': by_scope,
               'note': ('MOCK backend: boxes are the known final-column band, NOT SAM output. '
                        'This rate validates the pipeline around SAM, not SAM itself. '
                        'Guards: nearest-number recovery + gemini cross-check abstention.')
               if backend == 'mock' else
               'Live SAM 3.1 table boxes + geometric column derivation (rightmost dense numeric cluster). '
               'Guards: nearest-number recovery + gemini cross-check abstention.'}
    return {'summary': summary, 'pages': results}


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dig-csv', required=True)
    ap.add_argument('--n', type=int, default=10)
    ap.add_argument('--seed', type=int, default=20261027)
    ap.add_argument('--workdir', required=True)
    ap.add_argument('--backend', choices=['mock', 'meta_api'], default='mock')
    a = ap.parse_args(argv)
    out = run(a.dig_csv, a.n, a.seed, a.workdir, a.backend)
    with open(f'{a.workdir}/sam_report.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out['summary'], ensure_ascii=False, indent=2))
    for r in out['pages']:
        print(r['page_id'], '|', r.get('status', 'ok'), '| extracted:',
              r.get('extracted'), '| match:', r.get('cell_match'),
              '| notes:', '; '.join(r.get('notes', []))[:200])


if __name__ == '__main__':
    main(sys.argv[1:])

# -*- coding: utf-8 -*-
"""변환 이유 문구(match_phrasing) 전수 감사 — 배포 전에 돌린다.

사전의 모든 이름에 대해 음절 매칭 문구를 생성해, 문구가 말하는 것과 실제로
같은 것(초성/모음/글자)이 어긋나는 경우를 찾는다.

    python tools/audit_reason_phrases.py           # 요약 + 모순 목록, 모순 있으면 exit 1
    python tools/audit_reason_phrases.py --all     # 문구별 분포까지

배경: 소피아의 아→아 에 "maps almost exactly onto" 이 붙어 사용자가 지적했다.
사전 전체를 돌려 보니 3,821건 중 766건이 같은 문제였고, 모음이 같은데
"a similar vowel"(106건), 비슷할 뿐인데 "shares a vowel"(11건)도 있었다.
템플릿 문구는 결정론적이므로 LLM 검수가 아니라 이 감사로 지킨다.
"""
import sys, os, json, collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'lib'))
sys.path.insert(0, os.path.join(BASE, 'data'))
from conversion_reason import build_reason
from pronounce_guide import decompose
from syllable_match import onset_similar, vowel_similar

# 정도 표현이 사실과 어긋나는 경우. (조건, 설명)
HEDGES = ('almost', 'similar', 'close', 'trace', 'loosely', 'lends', 'shares')


def flags(r):
    ph = r['phrase']
    out = []
    if r['identical'] and any(w in ph for w in HEDGES):
        out.append('같은 글자인데 정도 표현')
    if r['same_vowel'] and 'similar vowel' in ph:
        out.append('모음이 같은데 "similar vowel"')
    if not r['same_vowel'] and ('its vowel' in ph or 'shares a vowel' in ph):
        out.append('모음이 다른데 "its vowel"/"shares a vowel"')
    if not r['same_onset'] and 'its consonant' in ph and 'similar consonant' not in ph:
        out.append('초성이 다른데 "its consonant"')
    if r['same_onset'] and 'similar consonant' in ph:
        out.append('초성이 같은데 "similar consonant"')
    return out


def main():
    n2t = json.load(open(os.path.join(BASE, 'data/dict_name_to_translit.json'), encoding='utf-8'))
    t2r = json.load(open(os.path.join(BASE, 'data/dict_translit_to_result_full.json'), encoding='utf-8'))
    rows, names = [], 0
    for sex in ('male', 'female'):
        for en, tr in n2t[sex].items():
            res = t2r['given'][sex].get(tr)
            if not res or not res.get('given'):
                continue
            names += 1
            out = build_reason(en.title(), tr, res['given'], 'Q1')
            for m in out['matches']:
                c1, j1, _ = decompose(m['src']); c2, j2, _ = decompose(m['tgt'])
                has1 = c1 != ''
                rows.append(dict(en=en, tr=tr, given=res['given'], src=m['src'], tgt=m['tgt'],
                                 sim=m['sim'], level=m['level'], phrase=m['phrase'],
                                 identical=(m['src'] == m['tgt']),
                                 same_onset=(c1 == c2 and has1),
                                 same_vowel=(j1 == j2)))
    print(f'이름 {names}개 · 매칭 {len(rows)}건')
    if '--all' in sys.argv:
        print('\n문구별 분포')
        for (lvl, ph), n in sorted(collections.Counter((r['level'], r['phrase']) for r in rows).items(),
                                   key=lambda x: -x[1]):
            print(f'  {n:5d}  [{lvl:8s}] {ph}')
    bad = collections.defaultdict(list)
    for r in rows:
        for f in flags(r):
            bad[f].append(r)
    if not bad:
        print('모순 없음')
        return 0
    for f, rs in bad.items():
        print(f'\n[{f}] {len(rs)}건')
        seen = set()
        for r in rs:
            k = (r['src'], r['tgt'], r['phrase'])
            if k in seen:
                continue
            seen.add(k)
            print(f"   {r['en']:12s} the {r['src']} sound {r['phrase']} {r['tgt']}  (sim={r['sim']})")
            if len(seen) >= 10:
                print('   ...'); break
    return 1


if __name__ == '__main__':
    sys.exit(main())

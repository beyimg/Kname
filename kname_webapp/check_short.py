# -*- coding: utf-8 -*-
"""
카드 앞면 '한 줄 의미'의 문법 점검 도구.

  python check_short.py            # 사전 602개 + 한자쌍 무작위 표본
  python check_short.py --pairs 50000

점검 내용
  1) 사전 602개의 한 줄이 모두 만들어지는지, 문법 규칙을 어기지 않는지
  2) hanja_dict.xlsx 의 한자를 무작위로 두 자씩 붙여 만든 이름에서도 같은지
  3) 품사 표(data/gloss_pos.py)에 없는 뜻이 얼마나 남았는지

한자 뜻만으로 품사를 추론할 수 없으므로, 표에 없는 뜻이 섞인 글자는
문장을 만들지 않고 라벨형('Wisdom · Jade radiance')으로 내려간다.
라벨형은 문법이 개입하지 않으므로 비문이 될 수 없다 — 여기서 세는 것은
'비문'이 아니라 '문장을 만들지 못한 비율'이다.
"""
import argparse
import json
import os
import random
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'lib'))
sys.path.insert(0, os.path.join(BASE, 'data'))

import app  # noqa: E402

# 비문 규칙은 lib/quality.py 한 곳에만 둔다.
# 운영 중 점검(app.py)과 배포 전 점검(이 파일)이 같은 규칙을 써야 한다 —
# 복사해 두면 한쪽만 고쳐져서 어긋난다.
from quality import check_grammar as check  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pairs', type=int, default=20000)
    ap.add_argument('--seed', type=int, default=7)
    args = ap.parse_args()
    random.seed(args.seed)

    bad = []
    label = 0

    # ---------------------------------------------------------------- 1) 사전
    path = os.path.join(BASE, 'data', 'dict_translit_to_result_full.json')
    with open(path, encoding='utf-8') as f:
        dic = json.load(f)
    seen = set()
    n_dict = 0
    for sex in ('female', 'male'):
        for _tr, e in dic['given'][sex].items():
            g = e.get('given')
            if not g or g in seen:
                continue
            seen.add(g)
            n_dict += 1
            hl = [{'syl': a, 'hanja': b, 'gloss': c}
                  for a, b, c in (e.get('hanja_detail') or [])]
            s = app._short_meaning(e.get('meaning_en') or '', hl, given=g)
            why = check(s)
            if why:
                bad.append(('dict', g, s, why))
    print(f'사전 이름 {n_dict}개 — 비문 {len([b for b in bad if b[0]=="dict"])}건')

    # ---------------------------------------------------------------- 2) 한자쌍
    import openpyxl
    wb = openpyxl.load_workbook(os.path.join(BASE, 'data', 'hanja_dict.xlsx'))
    ws = wb.active
    hdr = [c.value for c in ws[1]]
    i_h = hdr.index('한자') if '한자' in hdr else 0
    i_g = hdr.index('영어뜻')
    chars = [(r[i_h], r[i_g]) for r in ws.iter_rows(min_row=2, values_only=True)
             if r[i_g]]
    n_bad0 = len(bad)
    for _ in range(args.pairs):
        a, b = random.choice(chars), random.choice(chars)
        hl = [{'syl': '', 'hanja': a[0], 'gloss': str(a[1])},
              {'syl': '', 'hanja': b[0], 'gloss': str(b[1])}]
        s = app._short_meaning('', hl)
        if ' · ' in s or s == 'A native Korean name':
            label += 1
        why = check(s)
        if why:
            bad.append(('pair', f'{a[0]}{b[0]}', s, why))
    print(f'한자쌍 {args.pairs}개 — 비문 {len(bad) - n_bad0}건, '
          f'문장 대신 라벨형 {label}건 ({label * 100 // max(args.pairs, 1)}%)')

    # ---------------------------------------------------------------- 3) 미분류
    unk = {}
    for _h, g in chars:
        for t in str(g).split(','):
            t = t.strip()
            if t and app._pos_of(t) is None:
                unk[t.lower()] = unk.get(t.lower(), 0) + 1
    print(f'품사 표에 없는 뜻 {len(unk)}종 '
          f'(총 {sum(unk.values())}회) — 이 뜻이 든 글자는 라벨형으로 내려간다')

    if bad:
        print('\n--- 비문 ---')
        for kind, name, text, why in bad[:40]:
            print(f'[{kind}] {name}: {text}   ({why})')
    if unk:
        top = sorted(unk.items(), key=lambda kv: -kv[1])[:20]
        print('\n--- 미분류 상위 ---')
        for t, c in top:
            print(f'{c:4d}  {t}')

    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())

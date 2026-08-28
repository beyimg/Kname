#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
실제 API 키가 있는 환경에서 여러 이름을 한 번에 돌려본다.

    $env:ANTHROPIC_API_KEY="..."
    python batch_test.py                       # 기본 20개
    python batch_test.py --file names.txt      # 파일에서 읽기
    python batch_test.py --names "Siobhan Kowalski 여" "Tadhg Byrne 남"
    python batch_test.py --n 50                # 사전에서 무작위 50개

결과는 batch_result.txt 와 batch_result.json 에 저장된다.
JSON 을 대화에 붙여 넣으면 함께 검토할 수 있다.
"""
import argparse
import json
import os
import random
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

HANGUL = re.compile(r'[가-힣]')
HANJA = re.compile(r'[\u4e00-\u9fff]')
LATIN_EXT = re.compile(r'[\u00c0-\u024f]')

# 기본 세트 — 사전에 없을 법한 다양한 어원
DEFAULT = [
    ('Siobhan', 'Kowalski', '여'), ('Tadhg', 'Byrne', '남'),
    ('Aoibheann', 'Fitzpatrick', '여'), ('Padraig', 'Nakagawa', '남'),
    ('Oluwaseun', 'Adebayo', '남'), ('Chidinma', 'Nwosu', '여'),
    ('Bartosz', 'Lindqvist', '남'), ('Katarzyna', 'Wojcik', '여'),
    ('Thibault', 'Beaulieu', '남'), ('Clemence', 'Chevalier', '여'),
    ('Dimitrios', 'Papadopoulos', '남'), ('Svetlana', 'Kuznetsov', '여'),
    ('Torbjorn', 'Rasmussen', '남'), ('Solveig', 'Bjornstad', '여'),
    ('Meenakshi', 'Venkatesan', '여'), ('Harpreet', 'Bhullar', '남'),
    ('Mustafa', 'Alsaadi', '남'), ('Zahra', 'Haddad', '여'),
    ('Kenjiro', 'Hasegawa', '남'), ('Wilhelmina', 'Oosterhuis', '여'),
]


def parse_line(s):
    """'Siobhan Kowalski 여' → ('Siobhan', 'Kowalski', '여')"""
    parts = s.strip().split()
    if len(parts) < 2:
        return None
    sex = '여'
    if parts[-1] in ('남', '여', 'other', 'either', 'M', 'F'):
        sex = {'M': '남', 'F': '여', 'either': 'other'}.get(parts[-1], parts[-1])
        parts = parts[:-1]
    if len(parts) < 2:
        return None
    return (parts[0], ' '.join(parts[1:]), sex)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', help='한 줄에 "이름 성 성별" 형식')
    ap.add_argument('--names', nargs='*', help='직접 지정')
    ap.add_argument('--n', type=int, help='사전에서 무작위 N개')
    args = ap.parse_args()

    import app as A

    if not A.TRANSLIT.llm_available:
        print('⚠ ANTHROPIC_API_KEY 가 없습니다. 폴백 결과만 나옵니다.')
        print('   $env:ANTHROPIC_API_KEY="..." 로 설정한 뒤 다시 실행하세요.\n')

    # 대상 결정
    if args.file:
        cases = [c for c in (parse_line(l) for l in open(args.file, encoding='utf-8')) if c]
    elif args.names:
        cases = [c for c in (parse_line(x) for x in args.names) if c]
    elif args.n:
        n2t = json.load(open('data/dict_name_to_translit.json'))
        random.seed()
        cases = []
        for sex, sk in (('male', '남'), ('female', '여')):
            for en in random.sample(list(n2t[sex]), args.n // 2):
                cases.append((en.title(), 'Smith', sk))
    else:
        cases = DEFAULT

    out_lines, rows = [], []

    def say(s=''):
        print(s)
        out_lines.append(s)

    say('=' * 74)
    say(f'배치 테스트 {len(cases)}건')
    say('=' * 74)

    warn = 0
    for i, (first, last, sex) in enumerate(cases, 1):
        d = A.convert_name(first, last, sex)
        if 'error' in d:
            say(f'{i:3d}. ❌ {first} {last} — {d["error"][:50]}')
            rows.append({'first': first, 'last': last, 'error': d['error']})
            continue

        r = d['reason']
        fallback = bool(d.get('meaning_unavailable'))
        short = d.get('meaning_short') or ''
        meaning = d.get('meaning_en') or ''

        # 눈에 띄는 문제만 표시
        flags = []
        if fallback: flags.append('폴백')
        if HANGUL.search(short): flags.append('한줄에한국어')
        if HANJA.search(short): flags.append('한줄에한자')
        if LATIN_EXT.search(short) or LATIN_EXT.search(meaning): flags.append('발음기호')
        if not d.get('surname_desc'): flags.append('성씨유래없음')
        if flags: warn += 1

        say(f'\n{i:3d}. {first} {last} ({sex}){"  ⚠ " + ", ".join(flags) if flags else ""}')
        say(f'     음차   : {r["translit"]["hangul"]}')
        say(f'     이름   : {d["full_hangul"]} ({d["full_rom"]})  '
            f'{(d.get("surname_hanja") or "") + (d.get("hanja") or "")}  [{d.get("quality")}]')
        say(f'     한 줄  : {short}')
        say(f'     의미   : {meaning[:220]}')

        rows.append({
            'first': first, 'last': last, 'sex': sex,
            'translit': r['translit']['hangul'],
            'full': d['full_hangul'], 'rom': d['full_rom'],
            'hanja': (d.get('surname_hanja') or '') + (d.get('hanja') or ''),
            'quality': d.get('quality'),
            'native': bool(d.get('is_native')),
            'short': short, 'meaning': meaning,
            'surname_desc': d.get('surname_desc') or '',
            'matches': ' / '.join(f'{m["src"]}→{m["tgt"]} {m["style"]["label"]}'
                                  for m in r['matches']),
            'fallback': fallback,
            'flags': flags,
        })

    say('\n' + '=' * 74)
    say(f'완료 {len(rows)}건 · 확인 필요 {warn}건')
    fb = sum(1 for r in rows if r.get('fallback'))
    if fb:
        say(f'폴백 {fb}건 \u2014 API 키가 없거나 호출이 실패한 경우입니다.')

    with open('batch_result.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(out_lines))
    with open('batch_result.json', 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print('\n저장: batch_result.txt / batch_result.json')
    print('JSON 내용을 대화에 붙여 넣으면 함께 검토할 수 있습니다.')


if __name__ == '__main__':
    main()

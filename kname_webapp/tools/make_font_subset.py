# -*- coding: utf-8 -*-
"""한글·한자 웹 폰트 서브셋 만들기 / 글자 빠짐 검사.

    python tools/make_font_subset.py            # static/notoserifkr-app.v2.woff2 를 다시 만든다
    python tools/make_font_subset.py --check    # 결과 파일·사전의 글자가 폰트에 다 있는지만 검사

왜: 원본 notoserifkr-app.woff2(1.8MB)는 한글 11,172자 + 한자 4,883자 전부를 담고 있다.
    이 앱이 실제로 낼 수 있는 글자는 훨씬 적다 —
      · 한자: 사전(hanja_dict.xlsx, surname_hanja.xlsx, dict_translit_to_result_full.json)에 있는 것만
              결과에 나올 수 있다(구조상 확정). 약 1,100자.
      · 한글: 이름 음절은 KS X 1001 완성형 2,350자로 사실상 다 덮인다. 여기에 데이터·화면 문구에
              실제로 쓰인 음절을 합친다(밖의 글자가 있으면 그것도 포함되므로 안전).
    글꼴에 없는 글자가 나와도 화면은 깨지지 않는다 — 브라우저가 그 글자만 기기 글꼴로 대신 그린다.

원본은 static/notoserifkr-app.woff2 (Google Fonts 의 Noto Serif KR — 파일 이름은 ExtraLight 지만
실제 굵기는 Regular 다. 실측: 시스템 Noto Serif CJK KR Regular 와 잉크 양 1.00, 화면상 동일).
원본에 없는 한자(旲·昀·竜 등 44자)는 보조 폰트(Noto Serif CJK KR Regular OTF, --extra-source)에서
따로 잘라 두 번째 파일에 담고, CSS 의 unicode-range 로 그 글자가 화면에 있을 때만 받게 한다.
이 스크립트가 static/notoserifkr.css 도 함께 써서 unicode-range 가 틀릴 일이 없게 한다.

사전을 늘리거나 새 글자가 필요해지면 이 스크립트를 다시 돌리고, 파일명의 판(v2→v3)을 올린 뒤
static/notoserifkr.css 의 url 을 바꾼다. 파일명이 바뀌어야 브라우저 캐시가 갈린다.
"""
import os
import re
import sys
import glob
import json
import argparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(BASE, 'static')
DATA = os.path.join(BASE, 'data')
OUT_NAME = 'notoserifkr-app.v2.woff2'
EXTRA_NAME = 'notoserifkr-extra.v2.woff2'
CSS_NAME = 'notoserifkr.css'
SOURCE_CANDIDATES = [
    os.path.join(STATIC, 'notoserifkr-app.woff2'),          # 예전 전체본(저장소 밖)
    os.path.join(BASE, 'fonts_src', 'NotoSerifKR-ExtraLight.ttf'),
]

HANGUL = (0xAC00, 0xD7A3)
CJK_RANGES = [(0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF), (0x20000, 0x2A6DF)]


def is_hangul(c):
    return HANGUL[0] <= ord(c) <= HANGUL[1]


def is_cjk(c):
    o = ord(c)
    return any(a <= o <= b for a, b in CJK_RANGES)


def ksx1001_hangul():
    """KS X 1001 완성형 한글 2,350자 — iso2022_kr 코덱으로 인코딩되는 음절."""
    out = set()
    for cp in range(HANGUL[0], HANGUL[1] + 1):
        ch = chr(cp)
        try:
            ch.encode('iso2022_kr')
            out.add(ch)
        except UnicodeEncodeError:
            pass
    return out


def _text_of(path):
    """파일에서 글자를 긁어낸다. xlsx 는 모든 셀, json 은 통째, 나머지는 텍스트."""
    if path.endswith('.xlsx'):
        try:
            import openpyxl
        except ImportError:
            return ''
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                parts.extend(str(v) for v in row if v is not None)
        return '\n'.join(parts)
    try:
        return open(path, encoding='utf-8').read()
    except (UnicodeDecodeError, OSError):
        return ''


def source_files():
    """결과에 나올 수 있는 글자의 출처 — 사전·데이터·화면 문구·코드 문자열."""
    pats = ['data/*.json', 'data/*.xlsx', 'data/*.py', 'lib/*.py', 'app.py', 'stats.py',
            'templates/*.html', 'static/*.js', 'static/*.css',
            'translit_cache.json', 'meaning_cache.json', 'meaning_en_cache.json',
            'result_*.json', 'batch_result.json', 'name_tests/*.txt']
    files = []
    for p in pats:
        files += glob.glob(os.path.join(BASE, p))
    return [f for f in files if not f.endswith('.bak2')]


def used_chars():
    hangul, cjk, other = set(), set(), set()
    for f in source_files():
        for ch in _text_of(f):
            if is_hangul(ch):
                hangul.add(ch)
            elif is_cjk(ch):
                cjk.add(ch)
            elif ord(ch) > 0x2000 and not ch.isspace():
                other.add(ch)       # ‘ ” · — 같은 기호. 폰트에 있으면 넣는다
    return hangul, cjk, other


def _subset(font_path, unicodes, out_path):
    from fontTools.ttLib import TTFont
    from fontTools import subset
    font = TTFont(font_path)
    opts = subset.Options()
    opts.flavor = 'woff2'
    opts.layout_features = ['*']      # 합자·커닝 등 그대로
    opts.hinting = False
    opts.desubroutinize = True
    opts.name_IDs = ['*']
    opts.notdef_outline = True
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=unicodes)
    sub.subset(font)
    font.flavor = 'woff2'
    font.save(out_path)
    return font


def _ranges(codepoints):
    """unicode-range 문자열: 연속 구간은 U+A-B 로 묶는다."""
    cps = sorted(codepoints)
    out, start, prev = [], cps[0], cps[0]
    for c in cps[1:] + [None]:
        if c is not None and c == prev + 1:
            prev = c
            continue
        out.append(f'U+{start:04X}' if start == prev else f'U+{start:04X}-{prev:04X}')
        if c is not None:
            start = prev = c
    return ', '.join(out)


CSS_TEMPLATE = """/* 한글·한자 세리프를 사이트에서 직접 제공(self-host)한다.
   구글 폰트(Noto Serif KR)는 파일이 커서 모바일·인앱 브라우저에서 로딩이 실패/차단되는
   경우가 있어 한글이 기본 고딕으로 떨어졌다. 그래서 같은 서버에서 내려준다.

   이 파일은 tools/make_font_subset.py 가 만든다. 손으로 고치지 말 것.
   - {main}: 한글 {n_hangul}자(KS X 1001 + 데이터에 쓰인 음절) + 한자 {n_cjk}자. 원본 1.8MB → {kb_main} KB
   - {extra}: 원본에 없던 한자 {n_extra}자. unicode-range 덕에 그 글자가 화면에 있을 때만 받는다 ({kb_extra} KB)
   글꼴에 없는 글자가 나와도 깨지지 않고 기기 글꼴로 그려진다.
   가중치는 파일 하나로 100~900 을 담당한다(굵게는 브라우저가 합성). */
@font-face{{
  font-family:'Noto Serif KR';
  font-style:normal;
  font-display:swap;
  font-weight:100 900;
  src:url({main}) format('woff2');
}}
@font-face{{
  font-family:'Noto Serif KR';
  font-style:normal;
  font-display:swap;
  font-weight:100 900;
  src:url({extra}) format('woff2');
  unicode-range:{ranges};
}}
"""


def build(source, out_path, extra_source=None):
    from fontTools.ttLib import TTFont
    ks = ksx1001_hangul()
    used_h, used_c, used_o = used_chars()
    extra_h = used_h - ks
    chars = ks | used_h | used_c | used_o
    print(f'한글: KS X 1001 {len(ks)} + 데이터에서 추가 {len(extra_h)}'
          + (f' ({"".join(sorted(extra_h))[:40]})' if extra_h else ''))
    print(f'한자: {len(used_c)}   기호: {len(used_o)}')

    cmap = set(TTFont(source).getBestCmap().keys())
    keep = [ord(c) for c in chars if ord(c) in cmap]
    missing = sorted(c for c in chars if ord(c) not in cmap and (is_hangul(c) or is_cjk(c)))
    _subset(source, keep, out_path)
    kb_main = os.path.getsize(out_path) / 1024
    print(f'→ {os.path.relpath(out_path, BASE)}  {kb_main:.0f} KB  '
          f'(원본 {os.path.getsize(source)/1024/1024:.2f} MB, 글자 {len(keep)})')

    extra_path = os.path.join(os.path.dirname(out_path), EXTRA_NAME)
    n_extra, kb_extra, ranges = 0, 0, 'U+0000'
    if missing:
        print(f'원본에 없는 글자 {len(missing)}: {"".join(missing)}')
        if not extra_source:
            print('  → --extra-source 를 주면 보조 폰트로 담습니다. (지금은 기기 글꼴로 그려짐)')
        else:
            ecmap = set(TTFont(extra_source).getBestCmap().keys())
            got = [ord(c) for c in missing if ord(c) in ecmap]
            still = [c for c in missing if ord(c) not in ecmap]
            if still:
                print(f'  보조 폰트에도 없는 글자 {len(still)}: {"".join(still)}')
            _subset(extra_source, got, extra_path)
            n_extra, kb_extra, ranges = len(got), os.path.getsize(extra_path) / 1024, _ranges(got)
            print(f'→ {os.path.relpath(extra_path, BASE)}  {kb_extra:.0f} KB  (글자 {n_extra})')

    css = CSS_TEMPLATE.format(main=os.path.basename(out_path), extra=EXTRA_NAME,
                              n_hangul=len(ks | used_h), n_cjk=len([c for c in used_c if ord(c) in cmap]),
                              kb_main=f'{kb_main:.0f}', n_extra=n_extra, kb_extra=f'{kb_extra:.0f}', ranges=ranges)
    if n_extra == 0:                       # 보조 폰트가 없으면 두 번째 @font-face 는 뺀다
        css = css[:css.index('@font-face', css.index('@font-face') + 1)]
    css_path = os.path.join(STATIC, CSS_NAME)
    with open(css_path, 'w', encoding='utf-8') as f:
        f.write(css)
    print(f'→ {os.path.relpath(css_path, BASE)} 갱신')


def check(font_path):
    """결과 파일·사전의 글자가 서브셋 폰트에 다 있는지. 없는 글자는 기기 글꼴로 그려진다."""
    from fontTools.ttLib import TTFont
    cmap = set(TTFont(font_path).getBestCmap().keys())
    extra_path = os.path.join(os.path.dirname(font_path), EXTRA_NAME)
    if os.path.exists(extra_path):
        cmap |= set(TTFont(extra_path).getBestCmap().keys())
    used_h, used_c, _ = used_chars()
    miss = sorted(c for c in used_h | used_c if ord(c) not in cmap)
    if miss:
        print(f'폰트에 없는 글자 {len(miss)}: {"".join(miss)}')
        print('→ tools/make_font_subset.py 로 다시 자르고 CSS 의 판번호를 올릴 것')
        return 1
    print(f'OK — 데이터의 한글 {len(used_h)}·한자 {len(used_c)}자 모두 폰트에 있음 ({os.path.basename(font_path)})')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--source', help='원본 폰트(TTF/OTF/woff2)')
    ap.add_argument('--extra-source', help='원본에 없는 한자를 채울 보조 폰트(Noto Serif CJK KR Regular OTF)')
    ap.add_argument('--out', default=os.path.join(STATIC, OUT_NAME))
    a = ap.parse_args()
    if a.check:
        sys.exit(check(a.out))
    src = a.source or next((p for p in SOURCE_CANDIDATES if os.path.exists(p)), None)
    if not src:
        sys.exit('원본 폰트가 없습니다. --source 로 Noto Serif KR ExtraLight TTF 를 지정하세요.')
    build(src, a.out, a.extra_source)

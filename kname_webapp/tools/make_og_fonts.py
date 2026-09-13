# -*- coding: utf-8 -*-
"""OG 공유 카드(lib/og_card.py)가 서버에서 쓰는 폰트 두 개를 만든다.

    python tools/make_og_fonts.py --serif <NotoSerifCJKkr-Light.otf 또는 NotoSerifKR-Light.otf> --sans <DejaVuSans.ttf>

  assets/fonts/NotoSerifKR-Light.otf  한글(KS X 1001 + 데이터 음절) + 한자(데이터) + 영문·기호. 카드 본문
  assets/fonts/label-sans.ttf         대문자 라벨(YOUR KOREAN NAME / 사이트명)용. 영문 대문자·숫자·기호만

웹 폰트(static/notoserifkr-app.v2.woff2)와 글자 집합은 같고 굵기만 Light 다(시안 B 확정).
assets/ 는 static/ 이 아니므로 브라우저로 내려가지 않는다.
원본: Noto Serif CJK KR Light (SIL OFL), DejaVu Sans (Bitstream Vera 라이선스) — 둘 다 재배포 가능.
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_font_subset import ksx1001_hangul, used_chars   # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, 'assets', 'fonts')


def _subset(src, unicodes, out, flavor=None):
    from fontTools.ttLib import TTFont
    from fontTools import subset
    f = TTFont(src)
    o = subset.Options()
    o.layout_features = ['*']
    o.hinting = False
    o.desubroutinize = True
    o.name_IDs = ['*']
    o.notdef_outline = True
    o.flavor = flavor
    s = subset.Subsetter(o)
    s.populate(unicodes=unicodes)
    s.subset(f)
    f.save(out)
    return os.path.getsize(out)


def main(serif, sans):
    os.makedirs(OUT_DIR, exist_ok=True)
    ks = ksx1001_hangul()
    used_h, used_c, used_o = used_chars()
    latin = set(chr(c) for c in range(0x20, 0x7F)) | set(chr(c) for c in range(0xA0, 0x180))   # 기본 라틴 + 악센트
    punct = set('“”‘’–—…·→')
    chars = ks | used_h | used_c | used_o | latin | punct
    out = os.path.join(OUT_DIR, 'NotoSerifKR-Light.otf')
    kb = _subset(serif, [ord(c) for c in chars], out) / 1024
    print(f'→ {os.path.relpath(out, BASE)}  {kb:.0f} KB  (글자 {len(chars)})')

    label = set(chr(c) for c in range(0x20, 0x7F)) | set('·—')
    out2 = os.path.join(OUT_DIR, 'label-sans.ttf')
    kb2 = _subset(sans, [ord(c) for c in label], out2) / 1024
    print(f'→ {os.path.relpath(out2, BASE)}  {kb2:.0f} KB')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--serif', required=True)
    ap.add_argument('--sans', required=True)
    a = ap.parse_args()
    main(a.serif, a.sans)

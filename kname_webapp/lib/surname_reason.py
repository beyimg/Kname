# -*- coding: utf-8 -*-
"""
성씨 설명 생성기.
기존 유래 설명(info_en) 끝에 '한국 이름은 성씨가 앞에 온다'는 안내를 덧붙인다.
- 성씨+이름 전체가 주어지면 실제 풀네임 예시를 넣어 개인화
- 없으면 범용 문장으로 폴백
"""

import os
import re
import sys

try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data'))
    from surname_notes import card_text as _card_text
except Exception:                      # 데이터 파일이 없어도 카드는 기존 문장으로 나간다
    _card_text = None

_HEAD = re.compile(r"^(?P<rom>[A-Za-z' \-]+?) \((?P<hanja>[^)]+)\) is ")


def build_surname_note(info_en, surname_hangul, surname_rom,
                       given_hangul=None, given_rom=None):
    """
    반환: 카드 뒷면에 나갈 성씨 설명.

    규칙 (2026-10-12, 민우님)
      ① 한자 뜻  ② 같은 성을 쓰는 유명인(본명 기준으로 확인한 사람만)  ③ 1~3위(김·이·박)만 몇 번째로 많은지.
      본관·역사 문장은 쓰지 않는다. 데이터는 data/surname_notes.py. 파싱이 안 되거나 데이터가 없으면 원문을 그대로 쓴다.
    """
    base = (info_en or '').rstrip()
    if base and not re.search(r'[.!?]["\u201d\u2019\']?$', base):
        base += '.'
    m = _HEAD.match(base)
    if not m or _card_text is None:
        return base
    rom = surname_rom or m.group('rom')
    rom = rom if rom and rom[0].isupper() else m.group('rom')      # 표기는 원문 머리(예: 'Jeong (鄭)')를 따른다
    rom = m.group('rom')
    try:
        return _card_text(surname_hangul, rom, m.group('hanja'))
    except Exception:
        return base


if __name__ == '__main__':
    import json
    full = json.load(open('/mnt/user-data/outputs/dict_translit_to_result_full.json'))
    # 이(李) 성씨로 테스트
    for translit, d in full['surname'].items():
        if d['surname'] == '이':
            lee = d
            break

    print("=== 개인화 버전 (이수아) ===")
    print(build_surname_note(lee['info_en'], lee['surname'], lee['romanized'],
                             given_hangul='수아', given_rom='Su-a'))
    print("\n=== 폴백 버전 (이름 없음) ===")
    print(build_surname_note(lee['info_en'], lee['surname'], lee['romanized']))

    # 김(金)으로도
    for translit, d in full['surname'].items():
        if d['surname'] == '김':
            kim = d; break
    print("\n=== 김민준 예시 ===")
    print(build_surname_note(kim['info_en'], kim['surname'], kim['romanized'],
                             given_hangul='민준', given_rom='Min-jun'))

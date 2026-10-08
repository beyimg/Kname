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
    from surname_notes import celeb_card_sentence
except Exception:                      # 데이터 파일이 없어도 카드는 기존 문장으로 나간다
    def celeb_card_sentence(_surname):
        return ''

# "Ko (高) is Korea's 22nd most common surname at about 0.9%."  /  "Kim (金) is Korea's most common surname, …"
_HEAD = re.compile(r"^(?P<rom>[A-Za-z' \-]+?) \((?P<hanja>[^)]+)\) is (?:around )?Korea's (?P<rank>[^ ]+(?: most)?) ")
_TOP3 = {'most', 'second', 'third'}
_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\u201c\'])')


def build_surname_note(info_en, surname_hangul, surname_rom,
                       given_hangul=None, given_rom=None):
    """
    반환: 카드 뒷면에 나갈 성씨 설명.

    규칙 (2026-10-07)
      - 1~3위(김·이·박)만 '몇 번째로 흔한 성'을 말한다. 의미 있는 숫자라서다.
      - 4위 이하는 순위·비율 문장을 빼고 그 성씨의 특징적인 사실만 남긴다.
      - 특징 문장이 약한 성씨(본관 이름만 나열 등)는 성이 실제로 같은 유명인으로 대체한다
        (data/surname_notes.py 의 SURNAME_CELEB_EN).  글자 뜻("The character means …")은 남긴다.
      - 형식이 다르거나 파싱이 안 되면 원문을 그대로 쓴다(안전한 쪽).
    """
    base = (info_en or '').rstrip()
    if base and not re.search(r'[.!?]["\u201d\u2019\']?$', base):
        base += '.'

    # '한국 이름은 성씨가 앞에 온다'는 안내는 입력 페이지 인트로에서 이미
    # 제공하므로, 카드 뒷면 성씨 설명에서는 덧붙이지 않는다.
    m = _HEAD.match(base)
    if not m:
        return base
    first_rank = m.group('rank').split(' ')[0].lower()
    if first_rank in _TOP3:
        return base                                    # 1~3위: 순위 그대로

    parts = _SENT_SPLIT.split(base, 1)
    rest = parts[1].strip() if len(parts) > 1 else ''
    head = f"{m.group('rom')} ({m.group('hanja')})"

    celeb = celeb_card_sentence(surname_hangul)
    if celeb:
        # 유명인 연결만 보여준다. 한자가 여러 개인 성씨가 많아서(全/田, 鄭/丁 …) 글자 뜻을 같이 두면
        # 그 인물이 같은 한자를 쓰는 것처럼 읽힌다 — 한글 성이 같다는 사실만 말한다.
        return f"{head}: {celeb}"
    if rest:
        return f"{head}: {rest}"
    return f"{head}."                                  # 서문·옹처럼 두 번째 문장이 없는 경우


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

# -*- coding: utf-8 -*-
"""
성씨 설명 생성기.
기존 유래 설명(info_en) 끝에 '한국 이름은 성씨가 앞에 온다'는 안내를 덧붙인다.
- 성씨+이름 전체가 주어지면 실제 풀네임 예시를 넣어 개인화
- 없으면 범용 문장으로 폴백
"""

def build_surname_note(info_en, surname_hangul, surname_rom,
                       given_hangul=None, given_rom=None):
    """
    반환: 유래 + 성씨순서 안내가 합쳐진 최종 설명 문자열

    info_en        : 성씨 유래 설명 (dict의 info_en)
    surname_hangul : 성씨 한글 (예: '이')
    surname_rom    : 성씨 로마자 (예: 'Lee')
    given_hangul   : 이름 한글 (예: '수아') — 있으면 풀네임 예시 생성
    given_rom      : 이름 로마자 (예: 'Su-a')
    """
    base = info_en.rstrip()
    if not base.endswith(('.', '!', '?')):
        base += '.'

    # '한국 이름은 성씨가 앞에 온다'는 안내는 입력 페이지 인트로에서 이미
    # 제공하므로, 카드 뒷면 성씨 설명에서는 덧붙이지 않는다.
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

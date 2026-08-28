#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 연결 진단 — API 키를 넣은 뒤 이 스크립트로 먼저 확인하세요.

    export ANTHROPIC_API_KEY=sk-ant-...
    python check_llm.py

확인 항목
  1. anthropic 패키지 설치 여부
  2. API 키 설정 여부
  3. 실제 호출 성공 여부
  4. 음차 품질 (사전에 있는 이름으로 정답 대조)
  5. 사전 밖 이름 음차 결과
  6. 의미 생성 동작
"""
import os
import sys
import json

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, 'lib'))

OK, FAIL, WARN = '✅', '❌', '⚠️ '


def main():
    print('=' * 62)
    print('LLM 연결 진단')
    print('=' * 62)

    # 1. 패키지
    try:
        import anthropic
        print(f'{OK} anthropic 패키지  {anthropic.__version__}')
    except ImportError:
        print(f'{FAIL} anthropic 패키지가 없습니다.  →  pip install anthropic')
        return 1

    # 2. 키
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        print(f'{FAIL} ANTHROPIC_API_KEY 환경변수가 없습니다.')
        print('     →  export ANTHROPIC_API_KEY=sk-ant-...')
        return 1
    print(f'{OK} API 키            {key[:12]}...{key[-4:]}  (길이 {len(key)})')

    # 3. 실제 호출
    from transliterate import Transliterator
    tr = Transliterator(
        name_dict_path=os.path.join(BASE, 'data', 'dict_name_to_translit.json'),
        api_key=key,
        cache_path=os.path.join(BASE, 'translit_cache.json'),
    )
    try:
        probe = tr._call_llm('Reply with exactly: OK')
        print(f'{OK} API 호출          응답 수신 ({probe.strip()[:20]})')
    except Exception as e:
        print(f'{FAIL} API 호출 실패     {type(e).__name__}: {str(e)[:70]}')
        print('     인증 오류면 키를, 잔액 오류면 결제 상태를 확인하세요.')
        return 1

    # 4. 음차 품질 — 사전에 있는 이름을 LLM에 물어 정답과 대조
    print()
    print('-' * 62)
    print('음차 품질 검사 (사전의 정답과 대조)')
    print('-' * 62)
    truth = [
        ('Nguyen', 'surname', '응우옌'),
        ('Hernandez', 'surname', '에르난데스'),
        ('Sean', 'male', '숀'),
        ('Matthew', 'male', '매슈'),
        ('Jacqueline', 'female', '재클린'),
        ('Kwame', 'male', '콰메'),
        ('Okonkwo', 'surname', '오콘쿠오'),
    ]
    hit = 0
    for name, kind, expect in truth:
        try:
            got = tr._clean(tr._call_llm(tr._build_prompt(name, kind)))
        except Exception as e:
            got = f'(오류: {type(e).__name__})'
        mark = OK if got == expect else WARN
        if got == expect:
            hit += 1
        print(f'  {mark} {name:12s} 정답 {expect:8s} → LLM {got}')
    print(f'\n  일치 {hit}/{len(truth)}')
    if hit < len(truth):
        print(f'  {WARN}차이가 나는 항목은 lib/transliterate.py의 _GUIDE 예시에 추가하면')
        print('     정확도가 올라갑니다. (표기법상 복수 정답이 가능한 경우도 있습니다)')

    # 5. 사전 밖 이름
    print()
    print('-' * 62)
    print('사전 밖 이름 음차')
    print('-' * 62)
    for name, kind in [('Kowalski', 'surname'), ('Okonkwo', 'surname'),
                       ('Siobhan', 'female'), ('Kwame', 'male')]:
        got = tr.transliterate(name, kind)
        print(f'  {OK if got else FAIL} {name:12s} → {got}')

    # 6. 의미 생성
    print()
    print('-' * 62)
    print('의미 생성 (602개 밖 이름)')
    print('-' * 62)
    # 필요한 데이터 파일이 실제로 있는지 먼저 확인 (한글 파일명 깨짐 등)
    need = ['hanja_dict.xlsx', 'surname_hanja.xlsx', 'merged_meaningful.xlsx']
    missing = [f for f in need if not os.path.exists(os.path.join(BASE, 'data', f))]
    if missing:
        print(f'  {FAIL} data 폴더에 다음 파일이 없습니다: {missing}')
        try:
            actual = os.listdir(os.path.join(BASE, 'data'))
            print(f'     실제 파일 목록: {actual}')
            print('     → 한글 파일명이 깨졌다면 zip을 다시 풀어보세요')
        except Exception as e:
            print(f'     data 폴더를 읽을 수 없습니다: {e}')
        return 0

    try:
        from meaning import NameMeaning
        nm = NameMeaning(
            name_hanja_path=os.path.join(BASE, 'data', 'hanja_dict.xlsx'),
            surname_hanja_path=os.path.join(BASE, 'data', 'surname_hanja.xlsx'),
            db_path=os.path.join(BASE, 'data', 'merged_meaningful.xlsx'),
            api_key=key,
            cache_path=os.path.join(BASE, 'meaning_cache.json'),
        )
        from meaning_en import MeaningEnGenerator
        gen = MeaningEnGenerator(nm, api_key=key,
                                 cache_path=os.path.join(BASE, 'meaning_en_cache.json'))
        chars = nm.pick_best_hanja('광민')
        en = gen.explain_en(given='광민', sex='남', hanja_chars=chars,
                            english_name='Kwame') or ''
        if en and not en.startswith("'광민' is a Sino-Korean name"):
            print(f'  {OK} 광민 → {en[:76]}...')
        else:
            print(f'  {WARN}폴백 문구가 반환되었습니다 (LLM 생성 실패):')
            print(f'     {en[:76]}')
    except Exception as e:
        print(f'  {FAIL} {type(e).__name__}: {str(e)[:70]}')

    print()
    print('=' * 62)
    print(f'{OK} 진단 완료 — python app.py 로 실행하세요')
    print('=' * 62)
    return 0


if __name__ == '__main__':
    sys.exit(main())

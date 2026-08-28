#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
배포 전 최종 점검 — 실제 API 키가 있는 환경에서 실행하세요.

    $env:ANTHROPIC_API_KEY="..."
    $env:GOOGLE_APPLICATION_CREDENTIALS="gcp-key.json"
    python verify_launch.py

확인 항목
  1. 음차 품질   — 사전 밖 이름 10개를 LLM으로 음차
  2. 의미 설명   — 실제 생성 문장이 자연스러운지
  3. TTS 재생    — mp3가 만들어지고 소리가 들어 있는지
  4. 안전성      — 영어 전용 필드에 한국어·한자가 새지 않는지

결과는 verify_result.txt 에도 저장됩니다.
"""
import io
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

HANGUL = re.compile(r'[가-힣]')
HANJA = re.compile(r'[\u4e00-\u9fff]')

# 사전에 없을 법한, 어원이 다양한 이름
CASES = [
    ('Siobhan',   'Kowalski',   '여'),   # 아일랜드 + 폴란드
    ('Padraig',   'Nakagawa',   '남'),   # 아일랜드 + 일본
    ('Oluwaseun', 'Adeyemi',    '남'),   # 요루바
    ('Anaïs',     'Delacroix',  '여'),   # 프랑스
    ('Bartosz',   'Lindqvist',  '남'),   # 폴란드 + 스웨덴
    ('Xiomara',   'Vasquez',    '여'),   # 스페인계
    ('Thibault',  'Beaulieu',   '남'),   # 프랑스
    ('Eilidh',    'Mikkelsen',  '여'),   # 스코틀랜드 + 덴마크
    ('Rhiannon',  'Okonkwo',    '여'),   # 웨일스 + 이그보
    ('Torbjorn',  'Rasmussen',  '남'),   # 노르웨이 + 덴마크
]

OUT = []


def say(s=''):
    print(s)
    OUT.append(s)


def main():
    import app as A

    say('=' * 70)
    say('배포 전 최종 점검')
    say('=' * 70)
    say(f'LLM 음차   : {"사용 가능" if A.TRANSLIT.llm_available else "❌ 키 없음"}')
    say(f'의미 생성  : {"사용 가능" if (A.MEANING_EN and A.MEANING_EN.llm_available) else "❌ 키 없음"}')
    say(f'TTS        : {"사용 가능" if A.TTS_FULL.available else "❌ 키 없음"}')
    say(f'TTS 음성   : {A.TTS_FULL.voice}')
    say('')

    if not A.TRANSLIT.llm_available:
        say('⚠ ANTHROPIC_API_KEY 가 없어 사전 밖 이름을 시험할 수 없습니다.')
        return 1

    ok = 0
    issues = []
    sizes = {}      # 이름 → 파일 크기 (무음 판별용)

    for i, (first, last, sex) in enumerate(CASES, 1):
        say('─' * 70)
        say(f'{i}. {first} {last}  ({sex})')

        d = A.convert_name(first, last, sex)
        if 'error' in d:
            say(f'   ❌ 변환 실패: {d["error"][:60]}')
            issues.append((first, '변환실패'))
            continue

        r = d['reason']
        say(f'   음차   : {r["translit"]["hangul"]}  ·  성씨 음차 확인 필요')
        say(f'   결과   : {d["full_hangul"]}  ({d["full_rom"]})  '
            f'{(d.get("surname_hanja") or "") + (d.get("hanja") or "")}  [{d.get("quality")}]')
        say(f'   한 줄  : {d.get("meaning_short")}')

        meaning = d.get('meaning_en') or ''
        if d.get('meaning_unavailable'):
            say('   의미   : ❌ 생성 실패 (재시도 안내가 표시됨)')
            issues.append((first, '의미생성실패'))
        else:
            say(f'   의미   : {meaning[:150]}')
            if len(meaning) < 60:
                issues.append((first, '의미가너무짧음'))

        # 영어 전용 필드 검사
        leaks = []
        if HANGUL.search(str(d.get('meaning_short') or '')):
            leaks.append('한줄의미에 한국어')
        if HANJA.search(str(d.get('meaning_short') or '')):
            leaks.append('한줄의미에 한자')
        for h in d.get('hanja_lines', []):
            if HANGUL.search(str(h.get('gloss') or '')):
                leaks.append('한자뜻에 한국어')
                break
        if leaks:
            say(f'   ⚠ 혼입 : {leaks}')
            issues.extend((first, x) for x in leaks)

        # TTS — 페이지 렌더링은 캐시만 조회하므로, 여기서는 실제로 생성해 본다
        url = A.TTS_FULL.url_for(d['full_hangul'])
        if url:
            path = os.path.join(BASE, url.lstrip('/').replace('/', os.sep))
            size = os.path.getsize(path) if os.path.exists(path) else 0
            if size < 900:
                say(f'   TTS    : ❌ 파일이 너무 작음 ({size} B) — 무음 의심')
                issues.append((first, 'TTS무음'))
            else:
                say(f'   TTS    : ✅ {os.path.basename(path)}  {size:,} B')
                sizes[d['full_hangul']] = size
        else:
            err = getattr(A.TTS_FULL, 'last_error', None)
            say('   TTS    : ❌ 생성 실패 (브라우저 음성으로 대체됨)')
            if err:
                say(f'            원인: {err}')
            issues.append((first, 'TTS실패'))

        if not any(x[0] == first for x in issues):
            ok += 1

    # 이름 길이가 다른데 파일 크기가 모두 같다면 내용이 같다는 뜻 → 무음 의심
    if len(sizes) >= 3 and len(set(sizes.values())) == 1:
        only = next(iter(sizes.values()))
        say('')
        say(f'⚠ 모든 음성 파일이 {only:,} B 로 동일합니다.')
        say('  이름 길이가 다른데 크기가 같다면 내용이 비어 있을 수 있습니다.')
        say('  static/audio/full 폴더에서 직접 재생해 소리를 확인하세요.')
        say('  무음이라면 lib/tts_full.py 의 DEFAULT_VOICE 를')
        say("  'ko-KR-Neural2-A' 로 바꿔 다시 시도해 보세요.")
        issues.append(('전체', 'TTS무음의심'))

    say('=' * 70)
    say(f'\n이상 없음: {ok}/{len(CASES)}')
    if issues:
        from collections import Counter
        say('\n확인이 필요한 항목:')
        for k, v in Counter(x[1] for x in issues).most_common():
            say(f'   {k}: {v}건')
        say('')
        say('  · 음차가 어색하면 lib/transliterate.py 의 _GUIDE 에 예시를 추가하세요.')
        say('  · TTS 무음이면 lib/tts_full.py 의 DEFAULT_VOICE 를 바꿔 보세요.')
    else:
        say('\n모든 항목 정상입니다.')

    say('')
    say('※ 음차가 맞는지는 사람이 판단해야 합니다.')
    say('  위 "음차" 값이 원어 발음과 맞는지 직접 확인하세요.')
    say('  예) Siobhan → 시본 (O) / 시오브한 (X)')

    with open(os.path.join(BASE, 'verify_result.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(OUT))
    print('\n결과가 verify_result.txt 에 저장되었습니다.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

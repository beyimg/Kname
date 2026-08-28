#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
같은 이름을 여러 음성·속도로 만들어 나란히 비교한다.

    export GOOGLE_APPLICATION_CREDENTIALS=gcp-key.json
    python compare_voices.py                    # 기본 6종 · 이수아
    python compare_voices.py --text 이노을
    python compare_voices.py --rate 0.6 0.7 0.85
    python compare_voices.py --all              # 계정의 한국어 음성 전부

결과: static/audio/_compare/  (파일명에 음성·속도가 들어감)
탐색기로 열어 하나씩 들어보고 마음에 드는 것을 고르세요.
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, 'static', 'audio', '_compare')

# 억양이 평이한 순으로 추린 후보
CANDIDATES = [
    'ko-KR-Standard-A',    # 가장 단조로움 (기계적)
    'ko-KR-Standard-B',
    'ko-KR-Neural2-A',     # 여성 · 자연스럽고 차분
    'ko-KR-Neural2-B',
    'ko-KR-Neural2-C',     # 남성
    'ko-KR-Wavenet-A',
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--text', default='이수아')
    ap.add_argument('--rate', nargs='*', type=float, default=[0.7])
    ap.add_argument('--all', action='store_true', help='계정의 한국어 음성 전부')
    args = ap.parse_args()

    try:
        from google.cloud import texttospeech as tts
    except ImportError:
        print('❌ pip install google-cloud-texttospeech')
        return 1
    if not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        print('❌ GOOGLE_APPLICATION_CREDENTIALS 환경변수를 설정하세요.')
        return 1

    client = tts.TextToSpeechClient()

    if args.all:
        voices = [v.name for v in client.list_voices(language_code='ko-KR').voices]
    else:
        avail = {v.name for v in client.list_voices(language_code='ko-KR').voices}
        voices = [v for v in CANDIDATES if v in avail]

    os.makedirs(OUT, exist_ok=True)
    print(f'"{args.text}" · 음성 {len(voices)}종 · 속도 {args.rate}')
    print()

    made = 0
    for vname in voices:
        for rate in args.rate:
            # 지원하지 않으면 옵션을 빼고 재시도
            for cfg in (
                dict(audio_encoding=tts.AudioEncoding.MP3, speaking_rate=rate),
                dict(audio_encoding=tts.AudioEncoding.MP3),
            ):
                try:
                    resp = client.synthesize_speech(
                        input=tts.SynthesisInput(text=args.text),
                        voice=tts.VoiceSelectionParams(language_code='ko-KR', name=vname),
                        audio_config=tts.AudioConfig(**cfg),
                    )
                    applied = 'speaking_rate' in cfg
                    tag = f'{rate:.2f}' if applied else 'rate없음'
                    path = os.path.join(OUT, f'{vname}__{tag}.mp3')
                    with open(path, 'wb') as f:
                        f.write(resp.audio_content)
                    mark = '' if applied else '  (속도 미지원)'
                    print(f'  ✓ {vname:28s} {tag:9s} {len(resp.audio_content):>6d} B{mark}')
                    made += 1
                    break
                except Exception as e:
                    msg = str(e).lower()
                    if not any(k in msg for k in ('speaking_rate', 'speaking rate',
                                                  'not supported', 'invalid argument')):
                        print(f'  ✗ {vname}: {type(e).__name__}: {str(e)[:50]}')
                        break

    print()
    print(f'✅ {made}개 생성 → static/audio/_compare/')
    print('   explorer static\\audio\\_compare')
    print()
    print('마음에 드는 파일명을 확인한 뒤 lib/tts_full.py 상단을 바꾸세요:')
    print("   DEFAULT_VOICE = 'ko-KR-...'")
    print('   SPEAKING_RATE = 0.70')
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
계정에서 쓸 수 있는 한국어 음성을 모두 뽑고, 샘플 mp3를 만든다.

    $env:GOOGLE_APPLICATION_CREDENTIALS="gcp-key.json"
    python list_voices.py                 # 목록만
    python list_voices.py --sample        # 음성별 샘플 mp3 생성
    python list_voices.py --sample --text 이수아

샘플은 static/audio/_samples/ 에 저장된다.
파일명이 음성 이름이라 나란히 놓고 비교하기 좋다.
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE, 'static', 'audio', '_samples')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', action='store_true', help='음성별 샘플 mp3 생성')
    ap.add_argument('--text', default='이수아', help='샘플로 읽을 텍스트')
    ap.add_argument('--rate', type=float, default=0.88)
    ap.add_argument('--limit', type=int, default=0, help='샘플 생성 개수 제한(0=전체)')
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
    voices = client.list_voices(language_code='ko-KR').voices

    # 계열별로 묶어서 보여준다
    groups = {}
    for v in voices:
        if 'Chirp3-HD' in v.name:
            fam = 'Chirp3-HD (최신·고품질)'
        elif 'Neural2' in v.name:
            fam = 'Neural2'
        elif 'Wavenet' in v.name:
            fam = 'WaveNet'
        elif 'Standard' in v.name:
            fam = 'Standard (기계적)'
        else:
            fam = 'Other'
        groups.setdefault(fam, []).append(v)

    print(f'한국어 음성 {len(voices)}개\n')
    order = ['Chirp3-HD (최신·고품질)', 'Neural2', 'WaveNet', 'Standard (기계적)', 'Other']
    for fam in order:
        if fam not in groups:
            continue
        vs = sorted(groups[fam], key=lambda x: x.name)
        print(f'── {fam}  ({len(vs)}개)')
        for v in vs:
            g = v.ssml_gender.name.replace('FEMALE', '여성').replace('MALE', '남성')
            print(f'   {v.name:32s} {g}')
        print()

    if not args.sample:
        print('샘플을 만들려면:  python list_voices.py --sample')
        return 0

    os.makedirs(SAMPLE_DIR, exist_ok=True)
    targets = sorted(voices, key=lambda x: x.name)
    if args.limit:
        targets = targets[:args.limit]

    print(f'샘플 생성: "{args.text}" · {len(targets)}개 음성 · rate {args.rate}')
    made = failed = 0
    for v in targets:
        try:
            resp = client.synthesize_speech(
                input=tts.SynthesisInput(text=args.text),
                voice=tts.VoiceSelectionParams(language_code='ko-KR', name=v.name),
                audio_config=tts.AudioConfig(
                    audio_encoding=tts.AudioEncoding.MP3,
                    speaking_rate=args.rate,
                ),
            )
            gender = v.ssml_gender.name[0]      # F / M
            path = os.path.join(SAMPLE_DIR, f'{v.name}_{gender}.mp3')
            with open(path, 'wb') as f:
                f.write(resp.audio_content)
            made += 1
        except Exception as e:
            print(f'  ✗ {v.name}: {type(e).__name__}: {str(e)[:60]}')
            failed += 1

    print(f'\n✅ {made}개 생성 (실패 {failed})')
    print(f'   위치: static/audio/_samples/')
    print(f'   탐색기로 열기:  explorer static\\audio\\_samples')
    return 0


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""영상용 발음 mp3 를 이름별로 만든다 — /demo 자동 녹화에 소리를 얹기 위해.

    python tools/make_demo_audio.py --names Emma,Liam:m,Olivia
    python tools/make_demo_audio.py --names Mia --out demo_audio

각 이름을 앱과 같은 방법으로 변환해 한국 이름(given)을 얻고, 그 발음 mp3 를
demo_audio/<이름>.mp3 로 저장한다. 순서:
  1) 이 컴퓨터에 Google TTS 자격증명이 있으면 로컬에서 합성(앱과 같은 목소리).
  2) 없으면 운영 서버(kname.onrender.com)의 /api/tts 를 불러 mp3 를 내려받는다.
     운영 서버는 자격증명이 있고, 한 번 만든 발음은 캐시되어 무료다.

왜 이 파일이 필요한가: 영상은 클로드 쪽 자동 녹화기가 만들지만, 그 환경은 외부
사이트 접속이 막혀 있어 TTS 를 받을 수 없다. 소리만 이 컴퓨터에서 만들어 건넨다.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'lib'))

PROD = os.environ.get('KNAME_PROD_URL', 'https://kname.onrender.com').rstrip('/')
SEX = {'f': '여', 'm': '남', 'x': 'other'}


def parse(spec):
    out = []
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        name, _, g = part.partition(':')
        out.append((name.strip(), SEX.get((g or 'f').strip().lower()[:1], '여')))
    return out


def fetch_prod(given):
    """운영 서버에서 발음 mp3 바이트를 받는다. 실패하면 None."""
    api = f'{PROD}/api/tts?name={urllib.parse.quote(given)}'
    try:
        with urllib.request.urlopen(api, timeout=30) as r:
            d = json.loads(r.read().decode('utf-8'))
        url = d.get('url')
        if not url:
            print(f'   운영 서버 응답에 url 없음: {d}')
            return None
        if url.startswith('/'):
            url = PROD + urllib.parse.quote(url)      # 경로에 한글이 있다(…/예나.mp3)
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read()
    except Exception as e:
        print(f'   운영 서버 실패: {type(e).__name__}: {e}')
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--names', required=True, help='Emma,Liam:m,Olivia (이름:성별 f/m/x)')
    ap.add_argument('--out', default='demo_audio')
    args = ap.parse_args()

    import app as A
    os.makedirs(args.out, exist_ok=True)
    local_ok = bool(A.TTS_FULL.available)
    print(f'로컬 TTS: {"사용 가능" if local_ok else "없음 → 운영 서버에서 내려받음"}\n')

    made = []
    for name, sex in parse(args.names):
        d = A.convert_name(name, '', sex, allow_llm=False)
        if 'error' in d:
            print(f'✗ {name}: 변환 실패 ({d["error"]}) — 사전에 없는 이름은 /demo 에서도 안 된다')
            continue
        given = d['given']
        dst = os.path.join(args.out, f'{name}.mp3')
        data = None
        if local_ok:
            url = A.TTS_FULL.url_for(given)          # static/audio/full/<tag>/<given>.mp3 에 만든다
            if url:
                src = os.path.join(BASE, url.lstrip('/').replace('/', os.sep))
                if os.path.exists(src):
                    data = open(src, 'rb').read()
        if data is None:
            data = fetch_prod(given)
        if data is None:
            print(f'✗ {name} → {given}: mp3 를 얻지 못함')
            continue
        with open(dst, 'wb') as f:
            f.write(data)
        made.append((name, given, dst, len(data)))
        print(f'✓ {name} → {given}  {dst}  ({len(data)//1024} KB)')

    print(f'\n{len(made)}개 저장: {os.path.abspath(args.out)}')
    print('이 폴더의 mp3 를 클로드에게 넘기면(폴더가 연결되어 있으면 자동으로 가져간다) 영상에 얹는다.')
    return 0 if made else 1


if __name__ == '__main__':
    sys.exit(main())

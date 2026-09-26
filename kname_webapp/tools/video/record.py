# -*- coding: utf-8 -*-
"""이름 하나로 틱톡·릴스용 완성 영상(1080×1920, 자막·발음 포함)을 만든다.

    python tools/video/record.py --name Emily --out emily.mp4 \
        --trivia "Korean names are two syllables … <span class=kr>예린</span> is real, too."

전제: 로컬 Flask 가 http://127.0.0.1:5078 (SITE_URL=https://kname.onrender.com) 로 떠 있고,
      playwright(chromium)·ffmpeg 가 있다. 발음 mp3 는 운영 서버 /api/tts 에서 받는다(캐시: demo_audio/).

흐름: /demo 를 폰 배치(360×640)×3 으로 녹화 → 콘솔의 KDEMO 박자(start/type/submit/result/say/done/outro)
      → overlay.html 을 같은 박자로 프레임마다 찍어(투명 PNG) 녹화 위에 얹음 → 발음 mp3 를 제 시각에 → mp4.
"""
import argparse, asyncio, base64, json, os, shutil, subprocess, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
os.chdir(BASE); sys.path.insert(0, BASE); sys.path.insert(0, os.path.join(BASE, 'lib'))
BASE_URL = os.environ.get('DEMO_BASE', 'http://127.0.0.1:5078')
PROD = os.environ.get('KNAME_PROD_URL', 'https://kname.onrender.com').rstrip('/')
FPS = 30
SEX = {'f': '여', 'm': '남', 'x': 'other'}


def dur(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


def fetch_tts(given, out_dir='demo_audio'):
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, f'{given}.mp3')
    if os.path.exists(dst):
        return dst
    api = f'{PROD}/api/tts?name={urllib.parse.quote(given)}'
    with urllib.request.urlopen(api, timeout=40) as r:
        url = json.loads(r.read().decode('utf-8')).get('url')
    if not url:
        return None
    if url.startswith('/'):
        url = PROD + urllib.parse.quote(url)
    with urllib.request.urlopen(url, timeout=40) as r:
        open(dst, 'wb').write(r.read())
    return dst


def build_script(name, sex, args):
    """앱의 변환 결과로 자막 대본을 만든다. 훅·트리비아·아웃트로만 사람이 준다."""
    import app as A
    d = A.convert_name(name, '', sex, allow_llm=False)
    if 'error' in d:
        sys.exit(f'변환 실패: {d["error"]} — 사전에 있는 이름만 된다')
    chars = [{'ch': h['syl'], 'hanja': h['hanja'], 'gloss': h['gloss']} for h in d['hanja_lines']]
    return {
        'ep': args.ep or f'Korean name · {name}',
        'hook': {'eyebrow': args.hook_eyebrow or 'Your name is',
                 'big': f'{name}?',
                 'sub': args.hook_sub or 'Here’s your Korean name.'},
        'chars': chars,
        'rom': d['given_rom'],
        'trivia': {'label': 'Did you know', 'html': args.trivia},
        'outro': {'big': args.outro_big or 'What’s your name?',
                  'sub': args.outro_sub or 'Comment it — I’ll make yours.',
                  'hand': '\U0001F447'},
        'given': d['given'], 'hanja': d['hanja'],
    }, d


async def record(name, sex_key, saydur, gap, args, workdir):
    """/demo 를 돌리며 렌더러가 그리는 프레임을 그대로 받는다(CDP screencast, JPEG q92).
    브라우저의 자체 녹화(VP8)는 카드가 크게 움직인 뒤 색이 한동안 초록빛으로 틀어져서 쓰지 않는다.
    화면이 바뀔 때만 프레임이 오므로(움직일 때 25~35fps) 뒤에서 30fps 로 고르게 편다."""
    q = {'names': f'{name}:{sex_key}', 'hold': args.hold, 'back': args.back, 'reason': args.reason, 'type': args.type, 'say': 1, 'gap': gap,
         'saydur': f'{saydur:.2f}', 'intro': f'{name}?', 'card': 1.6, 'outro': 'What\u2019s your name?', 'zoom': 3}
    url = f'{BASE_URL}/demo?' + urllib.parse.urlencode(q)
    events, frames = [], []          # frames: (epoch 초, jpeg 경로)
    fdir = os.path.join(workdir, 'cap'); os.makedirs(fdir, exist_ok=True)
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={'width': 1080, 'height': 1920}, device_scale_factor=1)
        pg = await ctx.new_page()
        pg.on('console', lambda m: events.append(json.loads(m.text[6:])) if m.text.startswith('KDEMO ') else None)
        cdp = await ctx.new_cdp_session(pg)

        async def on_frame(ev):
            path = os.path.join(fdir, f'{len(frames):05d}.jpg')
            frames.append((ev['metadata']['timestamp'], path))
            with open(path, 'wb') as f:
                f.write(base64.b64decode(ev['data']))
            await cdp.send('Page.screencastFrameAck', {'sessionId': ev['sessionId']})
        cdp.on('Page.screencastFrame', lambda ev: asyncio.ensure_future(on_frame(ev)))

        await pg.goto(url, wait_until='networkidle')
        await cdp.send('Page.startScreencast', {'format': 'jpeg', 'quality': 92, 'maxWidth': 1080, 'maxHeight': 1920, 'everyNthFrame': 1})
        await asyncio.sleep(0.5)
        await pg.click('#overlay')
        est = 1.2 + 1.6 + 0.5 + len(name) * args.type / 1000 + 1.5 + args.hold + saydur + args.back + 1.6 + 2 * (args.reason + 0.7) + gap + 3
        try:
            await pg.wait_for_function("document.getElementById('card').style.display==='flex' && "
                                       "document.getElementById('card').textContent.indexOf('What')===0",
                                       timeout=(est + 20) * 1000)
            await asyncio.sleep(args.tail)
        except Exception:
            await asyncio.sleep(2)
        await cdp.send('Page.stopScreencast')
        await asyncio.sleep(0.3)
        await b.close()
    return frames, events


def lay_out_frames(frames, t_zero, total, workdir):
    """바뀔 때만 온 프레임을 30fps 로 편다: 각 시각에 '그 시각까지 마지막으로 온' 프레임을 하드링크."""
    seq = os.path.join(workdir, 'seq'); os.makedirs(seq, exist_ok=True)
    n = int(total * FPS) + 1
    j = 0
    for i in range(n):
        t = t_zero + i / FPS
        while j + 1 < len(frames) and frames[j + 1][0] <= t:
            j += 1
        dst = os.path.join(seq, f'{i:05d}.jpg')
        try:
            os.link(frames[j][1], dst)
        except OSError:
            shutil.copyfile(frames[j][1], dst)
    return seq, n


async def render_overlay(script, timeline, n_frames, workdir):
    """overlay.html 을 같은 origin 으로 띄워(서체 때문) 프레임마다 투명 PNG 로 찍는다."""
    from playwright.async_api import async_playwright
    out = os.path.join(workdir, 'ov'); os.makedirs(out, exist_ok=True)
    html = open(os.path.join(HERE, 'overlay.html'), encoding='utf-8').read()
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={'width': 1080, 'height': 1920}, device_scale_factor=1)
        await pg.route(f'{BASE_URL}/__overlay/overlay.html', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        await pg.goto(f'{BASE_URL}/__overlay/overlay.html', wait_until='networkidle')
        await pg.evaluate('([s, t]) => window.setup(s, t)', [script, timeline])
        await asyncio.sleep(0.3)
        for i in range(n_frames):
            await pg.evaluate('t => window.render(t)', i / FPS)
            await pg.screenshot(path=os.path.join(out, f'{i:05d}.png'), omit_background=True)
            if i % 150 == 0:
                print(f'  자막 프레임 {i}/{n_frames}')
        await b.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True, help='Emily 또는 Liam:m')
    ap.add_argument('--out', required=True)
    ap.add_argument('--trivia', required=True, help='트리비아 HTML 한 문단 (<span class=kr>한글</span> 가능)')
    ap.add_argument('--ep', default='')
    ap.add_argument('--hook-eyebrow', default=''); ap.add_argument('--hook-sub', default='')
    ap.add_argument('--trivia-secs', type=float, default=4.6)
    ap.add_argument('--outro-big', default=''); ap.add_argument('--outro-sub', default='')
    ap.add_argument('--hold', type=float, default=2.4, help='결과 카드 앞면 초')
    ap.add_argument('--back', type=float, default=4.2, help='뒷면(뜻) 초')
    ap.add_argument('--reason', type=float, default=2.8, help='변환 이유 카드 초(2단 각각)')
    ap.add_argument('--type', type=int, default=70)
    ap.add_argument('--tail', type=float, default=2.6)
    ap.add_argument('--keep', action='store_true', help='작업 폴더를 남긴다')
    args = ap.parse_args()

    name, _, g = args.name.partition(':')
    sex_key = (g or 'f').strip().lower()[:1] or 'f'
    script, d = build_script(name, SEX.get(sex_key, '여'), args)
    print(f'{name} → {script["given"]} ({script["hanja"]}, {script["rom"]})')

    mp3 = fetch_tts(script['given'])
    saydur = dur(mp3) + 0.3 if mp3 else 1.2
    print(f'발음 mp3: {mp3} ({saydur - 0.3:.2f}s)' if mp3 else '발음 mp3 없음')
    gap = args.trivia_secs + 0.6                        # 마지막 화면 위에 트리비아 카드를 얹을 시간

    workdir = os.path.join(os.path.dirname(os.path.abspath(args.out)) or '.', '_rec'); os.makedirs(workdir, exist_ok=True)
    frames, events = asyncio.run(record(name, sex_key, saydur, gap, args, workdir))
    ev = {e['ev']: e['t'] / 1000 for e in events}          # epoch 초
    t_zero = ev['start'] + 0.9                              # 'Tap to start' 와 대기 시간을 잘라낸다
    T = {k: v - t_zero for k, v in ev.items()}
    T['saydur'] = saydur - 0.3
    total = max(frames[-1][0], ev.get('outro', frames[-1][0]) + args.tail) - t_zero   # 아웃트로는 정지 화면이라 프레임이 안 온다
    print(f'프레임 {len(frames)}개, 박자(초): ' + ', '.join(f'{k}={v:.2f}' for k, v in T.items()) + f'  길이 {total:.1f}s')

    seq, n = lay_out_frames(frames, t_zero, total, workdir)
    ov = asyncio.run(render_overlay(script, T, n, workdir))

    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-framerate', str(FPS), '-i', os.path.join(seq, '%05d.jpg'),
           '-framerate', str(FPS), '-i', os.path.join(ov, '%05d.png')]
    fc = f'[0:v]format=rgba[base];[base][1:v]overlay=0:0:format=auto:eof_action=endall,format=yuv420p[v]'
    if mp3:
        at = int(max(0, T['say']) * 1000)
        cmd += ['-i', mp3]
        fc += f';[2:a]adelay={at}|{at},apad[a]'
        cmd += ['-filter_complex', fc, '-map', '[v]', '-map', '[a]', '-c:a', 'aac', '-b:a', '128k']
    else:
        cmd += ['-filter_complex', fc, '-map', '[v]', '-an']
    cmd += ['-t', f'{n / FPS:.3f}', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-r', str(FPS), '-movflags', '+faststart', args.out]
    subprocess.run(cmd, check=True)
    print(f'완성: {args.out}  ({dur(args.out):.1f}s)')
    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""이름 하나로 틱톡·릴스용 완성 영상(1080×1920, 나레이션·발음·자막 그림 포함)을 만든다.

    python tools/video/record.py --name "Taylor Swift" --out taylor.mp4 \
        --hook-media photos/taylor.jpg --hook-credit "Photo: Paolo V / CC BY 2.0"

전제: 로컬 Flask 가 http://127.0.0.1:5078 (SITE_URL=https://kname.onrender.com) 로 떠 있고,
      playwright(chromium)·ffmpeg 가 있다. 발음 mp3 는 운영 /api/tts, 나레이션은 운영 /api/narrate
      (환경변수 KNAME_VIDEO_TOKEN)에서 받는다. 토큰이 없으면 나레이션 자리는 무음으로 채워 박자만 맞춘다.

흐름: 앱 변환 결과 → 나레이션 대본(자동) → 문장별 mp3 + 길이 → 장면 길이를 그 길이로 정해 /demo 녹화
      (CDP screencast) → 박자(KDEMO) + 글자 위치(layout) → overlay.html 프레임 → 소리 전부 제 시각에 → mp4.
장면: 훅(사진+캡션) → 입력 → 결과 앞면(이름 발음+음절 밑줄, 성/이름 표시, 한 줄 뜻) → 뒷면(뜻) →
      Why this name 두 화면 → 아웃트로.
사전 밖 이름: 먼저 `--pull` 로 운영 서버 결과를 로컬 캐시에 받아 온다(토큰 필요), 그 뒤 로컬 Flask 재시작.
"""
import argparse, asyncio, base64, hashlib, json, os, re, shutil, subprocess, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
os.chdir(BASE); sys.path.insert(0, BASE); sys.path.insert(0, os.path.join(BASE, 'lib'))
BASE_URL = os.environ.get('DEMO_BASE', 'http://127.0.0.1:5078')
PROD = os.environ.get('KNAME_PROD_URL', 'https://kname.onrender.com').rstrip('/')
TOKEN = os.environ.get('KNAME_VIDEO_TOKEN', '')
FPS = 30
SEX = {'f': '여', 'm': '남', 'x': 'other'}
AUDIO_DIR = 'demo_audio'
_HANGUL = re.compile(r'[가-힣]+')
_CJK_PAREN = re.compile(r'\s*\([^()]*[一-鿿가-힣][^()]*\)')


# ───────────────────────────── 유틸
def dur(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                         capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


def voiced_span(path):
    """mp3 안에서 실제로 소리가 나는 구간(초). 앞뒤 무음을 빼고 음절 밑줄을 맞춘다."""
    out = subprocess.run(['ffmpeg', '-v', 'info', '-i', path, '-af', 'silencedetect=n=-38dB:d=0.08', '-f', 'null', '-'],
                         capture_output=True, text=True).stderr
    total = dur(path)
    ends = [float(x) for x in re.findall(r'silence_end: ([0-9.]+)', out)]
    starts = [float(x) for x in re.findall(r'silence_start: ([0-9.]+)', out)]
    v0 = ends[0] if (starts and starts[0] < 0.05 and ends) else 0.0
    v1 = starts[-1] if (starts and starts[-1] > v0 + 0.2 and (not ends or ends[-1] < starts[-1] + 0.05 or ends[-1] >= total - 0.05)) else total
    if v1 - v0 < 0.25:
        v0, v1 = 0.0, total
    return v0, v1


def http_json(url, body=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8') if body is not None else None,
                                 headers={'Content-Type': 'application/json', 'X-Video-Token': TOKEN})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def download(url, dst):
    if url.startswith('/'):
        url = PROD + urllib.parse.quote(url)
    with urllib.request.urlopen(url, timeout=60) as r:
        open(dst, 'wb').write(r.read())
    return dst


def fetch_tts(given):
    os.makedirs(AUDIO_DIR, exist_ok=True)
    dst = os.path.join(AUDIO_DIR, f'{given}.mp3')
    if os.path.exists(dst):
        return dst
    d = http_json(f'{PROD}/api/tts?name={urllib.parse.quote(given)}')
    return download(d['url'], dst) if d.get('url') else None


def fetch_narration(lines):
    """{id: text} → {id: (path, 초)}. 토큰이 없거나 실패하면 글자 수로 어림한 무음 mp3."""
    ndir = os.path.join(AUDIO_DIR, 'narr'); os.makedirs(ndir, exist_ok=True)
    out, missing = {}, {}
    for k, text in lines.items():
        h = hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]
        p = os.path.join(ndir, f'{h}.mp3')
        if os.path.exists(p):
            out[k] = (p, dur(p))
        else:
            missing[k] = (text, p)
    if missing and TOKEN:
        try:
            res = http_json(f'{PROD}/api/narrate', {'lines': [v[0] for v in missing.values()]}, timeout=180)
            for (k, (text, p)), item in zip(missing.items(), res.get('items', [])):
                if item.get('url'):
                    download(item['url'], p); out[k] = (p, dur(p))
                else:
                    print(f'  나레이션 실패 [{k}]: {item.get("error")}')
        except Exception as e:
            print(f'  나레이션 서버 오류: {type(e).__name__}: {e}')
    for k, (text, p) in missing.items():
        if k in out:
            continue
        est = 0.35 + 0.062 * len(text)                    # 대략 160 wpm
        sp = os.path.join(ndir, f'silent_{h_of(text)}.mp3')
        if not os.path.exists(sp):
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
                            '-t', f'{est:.2f}', '-c:a', 'libmp3lame', '-b:a', '64k', sp], check=True)
        out[k] = (sp, est)
        print(f'  (무음 대체 {est:.1f}s) [{k}] {text[:60]}')
    return out


def h_of(s):
    return hashlib.sha1(s.encode('utf-8')).hexdigest()[:10]


# ───────────────────────────── 대본
def romanize_text(text, d):
    """나레이션용: 한글은 로마자로(영어 TTS 가 읽을 수 있게), 한자 괄호는 뺀다."""
    from pronounce_guide import romanize_syllable
    rom = {}
    for s in d.get('syllables') or []:
        rom[s['ch']] = s['rom']
    for h in d.get('hanja_lines') or []:
        rom.setdefault(h['syl'], h['rom'].capitalize())
    text = _CJK_PAREN.sub('', text)

    def rep(m):
        w = m.group(0)
        parts = [rom.get(ch) or romanize_syllable(ch).capitalize() for ch in w]
        return '-'.join(parts)
    return _HANGUL.sub(rep, text)


def sentences(text):
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text or '') if s.strip()]


def build_lines(d, first, last, args):
    """장면별 나레이션 문장(영어). 훅만 사람이 바꿀 수 있다."""
    full_en = f'{first} {last}'.strip()
    rom_full = d['full_rom']
    given_rom = d['given_rom']
    lines = {}
    lines['hook'] = args.hook_caption or f"What's {first}'s Korean name?"
    lines['input'] = f"Let's type it in."
    lines['result'] = f"{full_en} becomes {rom_full}."
    if d.get('surname'):
        lines['names'] = f"{d['surname_rom']} is the family name, and {given_rom} is the given name."
    else:
        lines['names'] = f"Korean names put the family name first — {given_rom} is the given name."
    if d.get('meaning_short'):
        lines['meaning'] = f"It means: {d['meaning_short']}."
    glosses = [f"{h['rom'].capitalize()} means {h['gloss']}" for h in d['hanja_lines']]
    core = [romanize_text(s, d) for s in sentences(d.get('meaning_en') or '')[1:3]]
    lines['back'] = '. '.join(glosses) + '. ' + ' '.join(core)
    r = d['reason']
    carried = [m for m in r['matches'] if m['level'] != 'replaced']
    tr_rom = r['translit']['romanized']
    if carried:
        syl = ' and '.join(m['src_rom'] for m in carried)
        noun = 'syllable' if len(carried) == 1 else 'syllables'
        lines['reason1'] = (f"So why {given_rom}? In Korean letters, {first} sounds like {tr_rom}. "
                            f"We kept its most distinctive {noun}, {syl}, and built a name that reads naturally in Korean.")
    else:
        lines['reason1'] = (f"So why {given_rom}? In Korean letters, {first} sounds like {tr_rom}, "
                            f"but none of those sounds carry over cleanly, so we chose syllables that read naturally in Korean.")
    parts = [f"the {m['src_rom']} sound {m['phrase']} {m['tgt_rom']}" for m in r['matches']]
    joined = parts[0] if len(parts) == 1 else ', '.join(parts[:-1]) + ', and ' + parts[-1]
    lines['reason2'] = joined[0].upper() + joined[1:] + '.'
    lines['outro'] = args.outro_line or "What's your name? Drop it in the comments."
    return lines


def build_script(name, sex, args):
    import app as A
    first, _, last = name.partition(' ')
    d = A.convert_name(first, last.strip(), sex, allow_llm=False)
    if 'error' in d:
        sys.exit(f'변환 실패: {d["error"]} — 사전 밖 이름은 먼저 --pull 로 받아 오세요')
    media = None
    if args.hook_media:
        ext = os.path.splitext(args.hook_media)[1].lower()
        media = {'kind': 'video' if ext in ('.mp4', '.webm', '.mov') else 'image', 'ext': ext,
                 'caption': args.hook_caption or f"What's {first}'s Korean name?", 'credit': args.hook_credit}
    script = {
        'ep': f'Korean name · {name}',
        'hook': {'eyebrow': 'Your name is', 'big': f'{name}?', 'sub': 'Here’s your Korean name.', 'media': media},
        'rom': d['full_rom'], 'nSurname': len(d['surname'] or '') if d.get('surname') else 0,
        'outro': {'big': args.outro_big or 'What’s your name?', 'sub': args.outro_sub or 'Comment it — I’ll make yours.', 'hand': '\U0001F447'},
        'given': d['given'], 'hanja': d['hanja'], 'full': d['full_hangul'],
    }
    return script, d, first, last.strip()


# ───────────────────────────── 운영 결과 받아오기(사전 밖 이름)
def pull(name, sex_key):
    if not TOKEN:
        sys.exit('KNAME_VIDEO_TOKEN 이 필요합니다')
    first, _, last = name.partition(' ')
    q = urllib.parse.urlencode({'first': first, 'last': last.strip(), 'g': sex_key})
    res = http_json(f'{PROD}/api/export?{q}', timeout=120)
    cache_dir = os.environ.get('CACHE_DIR', BASE)
    for fname, key in (('translit_cache.json', 'translit_cache'), ('meaning_en_cache.json', 'meaning_en_cache')):
        p = os.path.join(cache_dir, fname)
        cur = {}
        if os.path.exists(p):
            try:
                cur = json.load(open(p, encoding='utf-8'))
            except Exception:
                cur = {}
        cur.update(res.get(key) or {})
        json.dump(cur, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f'  {fname}: +{len(res.get(key) or {})}')
    r = res['result']
    print(f'운영 결과: {name} → {r["full_hangul"]} ({r["hanja"]}, {r["full_rom"]}). 로컬 Flask 를 재시작하면 /demo 에서 쓸 수 있습니다.')


# ───────────────────────────── 녹화
async def record(name, sex_key, P, workdir):
    q = {'names': f'{name}:{sex_key}', 'hold': P['hold'], 'sayat': P['sayat'], 'saydur': f'{P["saydur"]:.2f}',
         'back': P['back'], 'reason': P['reason'], 'reason2': P['reason2'], 'type': P['type'], 'filled': P['filled'],
         'say': 1, 'gap': 0.4, 'intro': f'{name}?', 'card': P['hook'], 'outro': 'What’s your name?', 'zoom': 3}
    url = f'{BASE_URL}/demo?' + urllib.parse.urlencode(q)
    events, frames = [], []
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
        est = 1.2 + P['hook'] + 0.5 + len(name) * P['type'] / 1000 + P['filled'] + 2 + P['hold'] + P['back'] + 1.6 + P['reason'] + P['reason2'] + 1.4 + 0.4 + 3
        try:
            await pg.wait_for_function("document.getElementById('card').style.display==='flex' && "
                                       "document.getElementById('card').textContent.indexOf('What')===0",
                                       timeout=(est + 20) * 1000)
            await asyncio.sleep(P['tail'])
        except Exception:
            await asyncio.sleep(2)
        await cdp.send('Page.stopScreencast')
        await asyncio.sleep(0.3)
        await b.close()
    return frames, events


def lay_out_frames(frames, t_zero, total, workdir):
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


async def render_overlay(script, T, cues, layout, n_frames, workdir, hook_media):
    from playwright.async_api import async_playwright
    out = os.path.join(workdir, 'ov'); os.makedirs(out, exist_ok=True)
    html = open(os.path.join(HERE, 'overlay.html'), encoding='utf-8').read()
    media_bytes = open(hook_media, 'rb').read() if hook_media else None
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={'width': 1080, 'height': 1920}, device_scale_factor=1)
        await pg.route(f'{BASE_URL}/__overlay/overlay.html', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        if media_bytes is not None:
            ext = script['hook']['media']['ext']
            ctype = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp',
                     '.mp4': 'video/mp4', '.webm': 'video/webm', '.mov': 'video/quicktime'}.get(ext, 'application/octet-stream')
            await pg.route(f'{BASE_URL}/__overlay/media*', lambda route: route.fulfill(body=media_bytes, content_type=ctype))
            script['hook']['media']['url'] = f'{BASE_URL}/__overlay/media{ext}'
        await pg.goto(f'{BASE_URL}/__overlay/overlay.html', wait_until='networkidle')
        await pg.evaluate('([s, t, c, l]) => window.setup(s, t, c, l)', [script, T, cues, layout])
        await asyncio.sleep(0.3)
        hook_end = T['type'] - 0.2
        for i in range(n_frames):
            t = i / FPS
            if hook_media and script['hook']['media']['kind'] == 'video' and t < hook_end + 0.5:
                await pg.evaluate('t => window.seekHook(t)', t)
            await pg.evaluate('t => window.render(t)', t)
            await pg.screenshot(path=os.path.join(out, f'{i:05d}.png'), omit_background=True)
            if i % 150 == 0:
                print(f'  오버레이 프레임 {i}/{n_frames}')
        await b.close()
    return out


# ───────────────────────────── main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True, help='"Taylor Swift" 또는 "Liam:m"')
    ap.add_argument('--out', default='')
    ap.add_argument('--pull', action='store_true', help='운영 서버에서 변환 결과·캐시를 받아 온다(사전 밖 이름). 영상은 안 만든다')
    ap.add_argument('--hook-media', default='', help='훅 장면 사진/영상 파일(9:16 으로 크롭)')
    ap.add_argument('--hook-caption', default='', help='훅 캡션 = 첫 나레이션 (기본: What\'s X\'s Korean name?)')
    ap.add_argument('--hook-credit', default='', help='사진 출처 한 줄 (CC 표기)')
    ap.add_argument('--hook-secs', type=float, default=3.5)
    ap.add_argument('--outro-line', default=''); ap.add_argument('--outro-big', default=''); ap.add_argument('--outro-sub', default='')
    ap.add_argument('--filled', type=float, default=2.0, help='이름을 다 입력한 화면을 제출 전에 보여주는 초')
    ap.add_argument('--type', type=int, default=70)
    ap.add_argument('--tail', type=float, default=1.2)
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args()

    name, _, g = args.name.partition(':')
    sex_key = (g or 'f').strip().lower()[:1] or 'f'
    if args.pull:
        return pull(name, sex_key)
    if not args.out:
        sys.exit('--out 이 필요합니다')

    script, d, first, last = build_script(name, SEX.get(sex_key, '여'), args)
    print(f'{name} → {script["full"]} ({script["hanja"]}, {script["rom"]})')
    lines = build_lines(d, first, last, args)
    for k, v in lines.items():
        print(f'  [{k}] {v}')

    print('소리 준비…' + ('' if TOKEN else ' (KNAME_VIDEO_TOKEN 없음 → 나레이션은 무음으로 대체)'))
    tts = fetch_tts(script['full'])
    saydur = dur(tts) if tts else 1.2
    v0, v1 = voiced_span(tts) if tts else (0.0, saydur)
    N = fetch_narration(lines)
    nd = {k: v[1] for k, v in N.items()}

    # 장면 길이 = 나레이션 길이
    hook = max(args.hook_secs, nd['hook'] + 0.6)
    sayat = 0.4 + nd['result'] + 0.25                       # 앞면: "X becomes Y." → 발음
    hold = sayat + saydur + 0.35 + nd['names'] + 0.25 + nd.get('meaning', 0) + 0.6
    back = 0.8 + nd['back'] + 0.7
    reason = 0.6 + nd['reason1'] + 0.3
    reason2 = 0.6 + nd['reason2'] + 0.5
    P = {'hook': hook, 'sayat': sayat, 'saydur': saydur, 'hold': hold, 'back': back, 'reason': reason, 'reason2': reason2,
         'type': args.type, 'filled': args.filled, 'tail': nd['outro'] + args.tail}

    workdir = os.path.join(os.path.dirname(os.path.abspath(args.out)) or '.', '_rec'); os.makedirs(workdir, exist_ok=True)
    frames, events = asyncio.run(record(name, sex_key, P, workdir))
    ev = {e['ev']: e['t'] / 1000 for e in events}
    layout = next((e['data'] for e in events if e['ev'] == 'layout'), None)
    t_zero = ev['start'] + 0.9
    T = {k: v - t_zero for k, v in ev.items()}
    T['saydur'] = saydur
    total = max(frames[-1][0], ev.get('outro', frames[-1][0]) + P['tail']) - t_zero
    print(f'프레임 {len(frames)}개, 박자(초): ' + ', '.join(f'{k}={v:.2f}' for k, v in T.items() if k != 'layout') + f'  길이 {total:.1f}s')

    # 소리 큐(영상 기준 초)
    cues = {}
    cues['hook'] = {'t': 0.15, 'dur': nd['hook'], 'file': N['hook'][0]}
    cues['input'] = {'t': T['type'] - 0.15, 'dur': nd['input'], 'file': N['input'][0]}
    cues['result'] = {'t': T['result'] + 0.4, 'dur': nd['result'], 'file': N['result'][0]}
    if tts:
        cues['say'] = {'t': T['say'], 'dur': saydur, 'file': tts, 'v0': v0, 'v1': v1}
    t_names = T['say'] + saydur + 0.35
    cues['names'] = {'t': t_names, 'dur': nd['names'], 'file': N['names'][0]}
    if 'meaning' in N:
        cues['meaning'] = {'t': t_names + nd['names'] + 0.25, 'dur': nd['meaning'], 'file': N['meaning'][0]}
    cues['back'] = {'t': T['back'] + 0.8, 'dur': nd['back'], 'file': N['back'][0]}
    cues['reason1'] = {'t': T['reason1'] + 0.6, 'dur': nd['reason1'], 'file': N['reason1'][0]}
    cues['reason2'] = {'t': T['reason2'] + 0.6, 'dur': nd['reason2'], 'file': N['reason2'][0]}
    cues['outro'] = {'t': T['outro'] + 0.25, 'dur': nd['outro'], 'file': N['outro'][0]}

    seq, n = lay_out_frames(frames, t_zero, total, workdir)
    ov = asyncio.run(render_overlay(script, T, cues, layout, n, workdir, args.hook_media))

    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-framerate', str(FPS), '-i', os.path.join(seq, '%05d.jpg'),
           '-framerate', str(FPS), '-i', os.path.join(ov, '%05d.png')]
    fc = '[0:v]format=rgba[base];[base][1:v]overlay=0:0:format=auto:eof_action=endall,format=yuv420p[v]'
    k, mix = 1, []
    for key, c in cues.items():
        k += 1
        cmd += ['-i', c['file']]
        at = int(max(0, c['t']) * 1000)
        fc += f';[{k}:a]aformat=sample_rates=44100:channel_layouts=stereo,adelay={at}|{at}[a{k}]'
        mix.append(f'[a{k}]')
    fc += f';{"".join(mix)}amix=inputs={len(mix)}:normalize=0:dropout_transition=0,apad[a]'
    cmd += ['-filter_complex', fc, '-map', '[v]', '-map', '[a]', '-c:a', 'aac', '-b:a', '160k', '-ar', '44100',
            '-t', f'{n / FPS:.3f}', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-r', str(FPS),
            '-g', str(FPS), '-keyint_min', str(FPS), '-sc_threshold', '0', '-bf', '0', '-profile:v', 'main', '-level', '4.0',
            '-movflags', '+faststart', args.out]
    subprocess.run(cmd, check=True)
    print(f'완성: {args.out}  ({dur(args.out):.1f}s)')
    json.dump({'lines': lines, 'cues': {k: {kk: vv for kk, vv in v.items() if kk != 'file'} for k, v in cues.items()}, 'T': T},
              open(os.path.splitext(args.out)[0] + '.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == '__main__':
    main()

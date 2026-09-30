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
    """{id: text | {text,lang,prompt}} → {id: (path, 초)}. 토큰이 없거나 실패하면 글자 수로 어림한 무음 mp3."""
    # 서버의 목소리 설정(tag)별로 캐시를 나눈다 — 목소리를 바꾸면 새로 받는다
    tag, ver = 'local', 1
    if TOKEN:
        try:
            probe = http_json(f'{PROD}/api/narrate', {'lines': []}, timeout=60)
            tag, ver = probe.get('tag', 'local'), probe.get('v', 1)
        except Exception as e:
            print(f'  나레이션 서버 확인 실패: {type(e).__name__}: {e}')
    ndir = os.path.join(AUDIO_DIR, 'narr', tag); os.makedirs(ndir, exist_ok=True)
    out, missing = {}, {}
    for k, spec in lines.items():
        spec = spec if isinstance(spec, dict) else {'text': spec}
        text = spec['text']
        extra = '' if not (spec.get('lang') or spec.get('prompt')) else f"|{spec.get('lang','')}|{spec.get('prompt','')}"
        h = hashlib.sha1((text + extra).encode('utf-8')).hexdigest()[:16]
        p = os.path.join(ndir, f'{h}.mp3')
        if os.path.exists(p):
            out[k] = (p, dur(p))
        else:
            missing[k] = (spec, p)
    if missing and TOKEN:
        try:
            v = ver
            send = {k: v_ for k, v_ in missing.items() if v >= 2 or not v_[0].get('lang')}
            if len(send) < len(missing):
                print('  (운영 서버가 아직 구버전 — 한국어 문장은 무음으로 대체. 배포 후 다시 만들면 붙습니다)')
            res = http_json(f'{PROD}/api/narrate', {'lines': [v_[0] if v >= 2 else v_[0]['text'] for v_ in send.values()]}, timeout=240)
            for (k, (spec, p)), item in zip(send.items(), res.get('items', [])):
                if item.get('url'):
                    download(item['url'], p); out[k] = (p, dur(p))
                else:
                    print(f'  나레이션 실패 [{k}]: {item.get("error")}')
        except Exception as e:
            print(f'  나레이션 서버 오류: {type(e).__name__}: {e}')
    for k, (spec, p) in missing.items():
        if k in out:
            continue
        text = spec['text']
        est = 0.35 + 0.062 * len(text)                    # 대략 160 wpm
        if spec.get('lang', '').startswith('ko'):
            est = 0.5 + 0.7 * len(re.findall(r'[가-힣]', text))
        sp = os.path.join(ndir, f'silent_{h_of(text)}.mp3')
        if not os.path.exists(sp):
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
                            '-t', f'{est:.2f}', '-c:a', 'libmp3lame', '-b:a', '64k', sp], check=True)
        out[k] = (sp, est)
        print(f'  (무음 대체 {est:.1f}s) [{k}] {text[:60]}')
    return out


def voiced_segments(path, min_gap=0.12):
    """소리 구간 목록 [(시작, 끝)] — 천천히 읽은 음절들을 나눈다."""
    out = subprocess.run(['ffmpeg', '-v', 'info', '-i', path, '-af', f'silencedetect=n=-36dB:d={min_gap}', '-f', 'null', '-'],
                         capture_output=True, text=True).stderr
    total = dur(path)
    starts = [float(x) for x in re.findall(r'silence_start: ([0-9.]+)', out)]
    ends = [float(x) for x in re.findall(r'silence_end: ([0-9.]+)', out)]
    # 무음 구간들 사이가 소리 구간
    sil = sorted(zip(starts, ends + [total] * (len(starts) - len(ends))))
    segs, cur = [], 0.0
    for a, b in sil:
        if a - cur > 0.08:
            segs.append((cur, a))
        cur = b
    if total - cur > 0.08:
        segs.append((cur, total))
    return segs


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
    # 문장 끝의 따옴표("…glass.")도 문장 경계로 본다
    return [s.strip() for s in re.findall(r'[^.!?]*[.!?]+["\u201d\u2019]?', text or '') if s.strip()]


def build_lines(d, first, last, args):
    """장면별 나레이션(영어). 한국어 이름·음절은 한국어 목소리가 읽는다(lang=ko-KR)."""
    full_en = f'{first} {last}'.strip()
    given_rom = d['given_rom']
    syls = d['syllables']                                  # 카드 앞면 음절(성 포함)
    n_s = len(d['surname'] or '') if d.get('surname') else 0
    lines = {}
    lines['hook'] = args.hook_caption or f"What's {first}'s Korean name?"
    lines['input'] = "Let's type it in."
    lines['result'] = f"{full_en} becomes"
    lines['written'] = "In Korean, it's written like this."
    lines['slow'] = {'text': ', '.join(s['ch'] for s in syls) + '.', 'lang': 'ko-KR',
                     'prompt': 'Read these Korean syllables one at a time, slowly and clearly, with a clear pause after each one.'}
    if n_s:
        lines['names'] = f"{d['surname_rom']} is the last name, {given_rom} is the first name."
    else:
        lines['names'] = f"Koreans put the family name first — {given_rom} is the first name."
    if d.get('meaning_short'):
        lines['meaning'] = f"It means: {d['meaning_short']}."
    lines['next'] = "Next, what the name means."
    for i, h in enumerate(d['hanja_lines']):
        lead = 'And ' if i else ''
        lines[f'back{i}'] = f"{lead}{h['rom'].capitalize()} means {h['gloss']}."
    if d.get('meaning_short'):
        lines['backall'] = f"So together, it means {d['meaning_short'][0].lower() + d['meaning_short'][1:]}."
    lines['why'] = "Now, let me show you why this name."
    r = d['reason']
    tr_rom = r['translit']['romanized']
    lines['reason1a'] = f"Read the Korean way, {first} sounds like {tr_rom}."
    chosen = ' and '.join(s['rom'] for s in r['korean']['syllables'])
    lines['reason1b'] = f"And for a natural Korean name, we chose these syllables: {chosen}."
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
        est = 1.2 + P['hook'] + 0.5 + len(name) * P['type'] / 1000 + P['filled'] + 2 + P['hold'] + P['back'] + 1.6 + P['reason'] + P['reason2'] + 1.4 + 0.4 + 3 + P['tail']
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
        prev_sig, prev_path, shots = None, None, 0
        for i in range(n_frames):
            t = i / FPS
            if hook_media and script['hook']['media']['kind'] == 'video' and t < hook_end + 0.5:
                await pg.evaluate('t => window.seekHook(t)', t)
            sig = await pg.evaluate('t => window.render(t)', t)
            path = os.path.join(out, f'{i:05d}.png')
            if sig == prev_sig and prev_path:
                os.link(prev_path, path)                 # 화면이 그대로면 찍지 않는다(뒷면·이유 화면은 오버레이가 비어 있다)
            else:
                await pg.screenshot(path=path, omit_background=True); shots += 1
            prev_sig, prev_path = sig, path
            if i % 300 == 0:
                print(f'  오버레이 프레임 {i}/{n_frames} (찍은 것 {shots})')
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
    N = fetch_narration(lines)
    nd = {k: v[1] for k, v in N.items()}
    slow_segs = voiced_segments(N['slow'][0])
    n_syl = len(d['syllables'])
    if len(slow_segs) != n_syl:                         # 못 나누면 균등 분할
        v0, v1 = (slow_segs[0][0], slow_segs[-1][1]) if slow_segs else (0.0, nd['slow'])
        slow_segs = [(v0 + (v1 - v0) * i / n_syl, v0 + (v1 - v0) * (i + 1) / n_syl) for i in range(n_syl)]
    backs = [k for k in lines if k.startswith('back') and k != 'backall']

    # 장면 길이 = 나레이션 길이 (앞면: becomes → 발음 → written → 천천히 → 성/이름 → 뜻 → "Next…" 도중에 뒤집힘)
    hook = max(args.hook_secs, nd['hook'] + 0.6)
    sayat = 0.4 + nd['result'] + 0.15
    t_written = sayat + saydur + 0.35
    t_slow = t_written + nd['written'] + 0.25
    t_names = t_slow + nd['slow'] + 0.35
    t_meaning = t_names + nd['names'] + 0.3
    t_next = t_meaning + nd.get('meaning', 0) + 0.35
    hold = t_next + nd['next'] * 0.55
    t_back0 = 0.9
    back_cues, tb = [], t_back0
    for k in backs:
        back_cues.append((k, tb)); tb += nd[k] + 0.25
    t_backall = tb + 0.1
    t_why = t_backall + nd.get('backall', 0) + 0.45
    back = t_why + nd['why'] * 0.5
    reason = 0.75 + nd['reason1a'] + 0.25 + nd['reason1b'] + 0.4
    reason2 = 0.6 + nd['reason2'] + 0.5
    P = {'hook': hook, 'sayat': sayat, 'saydur': saydur, 'hold': hold, 'back': back, 'reason': reason, 'reason2': reason2,
         'type': args.type, 'filled': args.filled, 'tail': nd['outro'] + args.tail}

    workdir = os.path.join(os.path.dirname(os.path.abspath(args.out)) or '.', '_rec'); os.makedirs(workdir, exist_ok=True)
    frames, events = asyncio.run(record(name, sex_key, P, workdir))
    ev = {e['ev']: e['t'] / 1000 for e in events}
    layout = {k: next((e['data'] for e in events if e['ev'] == k), None) for k in ('layout', 'layout_back', 'layout_reason')}
    t_zero = ev['start'] + 0.9
    T = {k: v - t_zero for k, v in ev.items()}
    T['saydur'] = saydur
    total = max(frames[-1][0], ev.get('outro', frames[-1][0]) + P['tail']) - t_zero
    print(f'프레임 {len(frames)}개, 박자(초): ' + ', '.join(f'{k}={v:.2f}' for k, v in T.items() if not k.startswith('layout')) + f'  길이 {total:.1f}s')

    # 소리 큐(영상 기준 초) — 앞면은 result 기준, 뒷면은 back 기준, 이유는 reason1/2 기준
    def cue(k, t, **kw):
        c = {'t': t, 'dur': nd[k], 'file': N[k][0]}; c.update(kw); return c
    R = T['result']
    cues = {'hook': cue('hook', 0.15), 'input': cue('input', T['type'] + 0.05), 'result': cue('result', R + 0.4)}
    if tts:
        cues['say'] = {'t': T['say'], 'dur': saydur, 'file': tts}
    cues['written'] = cue('written', R + t_written)
    cues['slow'] = cue('slow', R + t_slow, segs=slow_segs)
    cues['names'] = cue('names', R + t_names)
    if 'meaning' in N:
        cues['meaning'] = cue('meaning', R + t_meaning)
    cues['next'] = cue('next', R + t_next)
    B = T['back']
    for k, t in back_cues:
        cues[k] = cue(k, B + t)
    if 'backall' in N:
        cues['backall'] = cue('backall', B + t_backall)
    cues['why'] = cue('why', B + t_why)
    cues['reason1a'] = cue('reason1a', T['reason1'] + 0.75)
    cues['reason1b'] = cue('reason1b', T['reason1'] + 0.75 + nd['reason1a'] + 0.25)
    cues['reason2'] = cue('reason2', T['reason2'] + 0.6)
    cues['outro'] = cue('outro', T['outro'] + 0.25)
    script['backKeys'] = backs

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

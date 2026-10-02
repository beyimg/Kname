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


KO_VOICE = os.environ.get('KNAME_KO_VOICE', 'ko-KR-Chirp3-HD-Aoede')   # 나레이션(en-US Aoede)과 같은 목소리
GAP = 0.12                                                            # 세그먼트 사이 쉼(초)


def ko(text):
    """한국어 목소리로 읽을 세그먼트(같은 목소리 페르소나)."""
    return {'text': text, 'lang': 'ko-KR', 'voice': KO_VOICE}


def fetch_narration(lines, tempo=1.0):
    """{id: 문장 | {text,lang,voice} | [세그먼트, …]} → {id: {'parts': [(path, 초, v0, v1)…], 'dur': 초}}.
    토큰이 없거나 실패하면 글자 수로 어림한 무음 mp3. 영어 클립은 tempo 배로 빠르게(음높이 유지)."""
    tag, ver = 'local', 1
    if TOKEN:
        try:
            probe = http_json(f'{PROD}/api/narrate', {'lines': []}, timeout=60)
            tag, ver = probe.get('tag', 'local'), probe.get('v', 1)
        except Exception as e:
            print(f'  나레이션 서버 확인 실패: {type(e).__name__}: {e}')
    ndir = os.path.join(AUDIO_DIR, 'narr', tag); os.makedirs(ndir, exist_ok=True)

    segs = []                                     # (id, idx, spec, path)
    for k, spec in lines.items():
        parts = spec if isinstance(spec, list) else [spec]
        for i, sp in enumerate(parts):
            sp = sp if isinstance(sp, dict) else {'text': sp}
            if sp.get('audio'):
                # 대본에 소리 파일을 직접 지정한 줄(다른 TTS 나 녹음) — 44.1kHz mp3 로 맞춰 둔다
                src = sp['audio']
                conv = os.path.join(ndir, 'ext_' + hashlib.sha1((src + str(os.path.getmtime(src))).encode()).hexdigest()[:12] + '.mp3')
                if not os.path.exists(conv):
                    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', src, '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '128k', conv], check=True)
                segs.append((k, i, dict(sp, text=sp.get('text', '')), conv)); continue
            extra = '' if not (sp.get('lang') or sp.get('prompt') or sp.get('voice')) else f"|{sp.get('lang','')}|{sp.get('voice','')}|{sp.get('prompt','')}"
            h = hashlib.sha1((sp['text'] + extra).encode('utf-8')).hexdigest()[:16]
            segs.append((k, i, sp, os.path.join(ndir, f'{h}.mp3')))
    missing = [x for x in segs if not os.path.exists(x[3])]
    if missing and TOKEN:
        send = [x for x in missing if ver >= 2 or not x[2].get('lang')]
        if len(send) < len(missing):
            print('  (운영 서버가 아직 구버전 — 한국어 문장은 무음으로 대체)')
        for chunk in [send[i:i + 30] for i in range(0, len(send), 30)]:
            try:
                res = http_json(f'{PROD}/api/narrate', {'lines': [x[2] if ver >= 2 else x[2]['text'] for x in chunk]}, timeout=240)
                for x, item in zip(chunk, res.get('items', [])):
                    if item.get('url'):
                        download(item['url'], x[3])
                    else:
                        print(f'  나레이션 실패 [{x[0]}]: {item.get("error")}')
            except Exception as e:
                print(f'  나레이션 서버 오류: {type(e).__name__}: {e}')
    out = {}
    for k, i, sp, path in segs:
        if not os.path.exists(path):
            text = sp['text']
            est = 0.5 + 0.7 * len(re.findall(r'[가-힣]', text)) if sp.get('lang', '').startswith('ko') else 0.35 + 0.062 * len(text)
            path = os.path.join(ndir, f'silent_{h_of(text)}.mp3')
            if not os.path.exists(path):
                subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
                                '-t', f'{est:.2f}', '-c:a', 'libmp3lame', '-b:a', '64k', path], check=True)
            print(f'  (무음 대체 {est:.1f}s) [{k}] {text[:60]}')
        elif tempo != 1.0 and not sp.get('lang', '').startswith('ko'):
            fast = path[:-4] + f'_x{int(tempo * 100)}.mp3'
            if not os.path.exists(fast):
                subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', path, '-af', f'atempo={tempo:.3f}', '-c:a', 'libmp3lame', '-b:a', '96k', fast], check=True)
            path = fast
        v0, v1 = voiced_span(path)
        out.setdefault(k, {'parts': []})['parts'].append((path, dur(path), v0, v1))
    for k, v in out.items():
        v['dur'] = sum(p[1] for p in v['parts']) + GAP * (len(v['parts']) - 1)
        v['v0'] = v['parts'][0][2]
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


def respace(path, segs, gap=0.42, lead=0.25, pad=0.06):
    """소리 구간들만 잘라 일정한 쉼으로 다시 이어 붙인 mp3 와 새 구간 목록을 돌려준다."""
    out = path[:-4] + f'_sp{int(gap * 100)}.mp3'
    parts, filt, t = [], [], lead
    new_segs = []
    for i, (a, b) in enumerate(segs):
        a2, b2 = max(0, a - pad), b + pad
        filt.append(f'[0:a]atrim={a2:.3f}:{b2:.3f},asetpts=PTS-STARTPTS[s{i}]')
        filt.append(f'aevalsrc=0:d={(lead if i == 0 else gap):.3f}:s=44100[g{i}]')
        parts += [f'[g{i}]', f'[s{i}]']
        new_segs.append((t + pad, t + pad + (b - a))); t += (b2 - a2) + (0 if i else 0)
        t += gap if i < len(segs) - 1 else 0
    filt.append(f'aevalsrc=0:d=0.3:s=44100[tail]'); parts.append('[tail]')
    filt.append(''.join(parts) + f'concat=n={len(parts)}:v=0:a=1[out]')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', path, '-filter_complex', ';'.join(filt), '-map', '[out]',
                    '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '96k', out], check=True)
    # 실제로 다시 재서 정확한 구간을 쓴다
    segs2 = voiced_segments(out)
    return out, (segs2 if len(segs2) == len(segs) else new_segs)


def cut_clip(path, a, b, pre=0.05, post=0.12):
    """클립에서 [a,b] 소리 구간만 잘라 짧은 mp3 로(앞뒤 여유 포함)."""
    out = path[:-4] + f'_cut{int(a * 100):04d}.mp3'
    if not os.path.exists(out):
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', path, '-af',
                        f'atrim={max(0, a - pre):.3f}:{b + post:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={(b - max(0, a - pre)) + post - 0.06:.3f}:d=0.06',
                        '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '96k', out], check=True)
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
    # 문장 끝의 따옴표("…glass.")도 문장 경계로 본다
    return [s.strip() for s in re.findall(r'[^.!?]*[.!?]+["\u201d\u2019]?', text or '') if s.strip()]


def build_lines(d, first, last, args):
    """장면별 나레이션. 한국어 글자는 같은 목소리의 한국어판(ko)이 읽는다 — 영어 목소리는 'Seo' 를 철자로 읽는다."""
    full_en = f'{first} {last}'.strip()
    syls = d['syllables']
    n_s = len(d['surname'] or '') if d.get('surname') else 0
    given_ko = d['given']
    lines = {}
    lines['hook'] = args.hook_caption or f"What would {full_en}'s Korean name be?"
    lines['input'] = "Let's type it in."
    lines['result'] = f"{full_en} becomes"
    lines['say'] = ko(d['full_hangul'] + '.')                           # 이름 발음(같은 목소리)
    lines['written'] = "In Korean, it's written like this."
    lines['slow'] = ko(' '.join(x['ch'] + '.' for x in syls))             # "서. 태. 이." — 마침표로 끊어 읽는다
    if n_s:
        lines['names'] = [ko(d['surname'] + '.'), 'is the last name, and', ko(given_ko + '.'), 'is the first name.']
    else:
        lines['names'] = ['Koreans put the family name first —', ko(given_ko + '.'), 'is the first name.']
    if d.get('meaning_short'):
        lines['meaning'] = f"It means: {d['meaning_short']}."
    lines['next'] = "Next, what the name means."
    for i, h in enumerate(d['hanja_lines']):
        lines[f'back{i}'] = [ko(h['syl'] + '.'), f"means {h['gloss']}."]
    if d.get('meaning_short'):
        lines['backall'] = f"So together, it means {d['meaning_short'][0].lower() + d['meaning_short'][1:]}."
    lines['why'] = "Curious why this name? The full breakdown is on the web app."
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
                 'caption': args.hook_caption or f"What would {first} {last.strip()}'s Korean name be?".replace("  ", " "), 'credit': args.hook_credit}
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
         'back': P['back'], 'reason': P['reason'], 'reason2': P['reason2'], 'rviews': P['rviews'], 'type': P['type'],
         'filled': P['filled'], 'typesec': P['typesec'], 'typepre': P['typepre'],
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
        est = 1.2 + P['hook'] + P['typepre'] / 1000 + max(P['typesec'], len(name) * P['type'] / 1000 + P['filled']) + 2 + P['hold'] + P['back'] + 1.6 + P['reason'] + P['reason2'] + 1.4 + 0.4 + 3 + P['tail']
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
    ap.add_argument('--script', default='', help='대본 JSON. 없으면 자동 대본을 이 파일로 내보내고 멈춤, 있으면 그 대본으로 만든다')
    ap.add_argument('--out', default='')
    ap.add_argument('--pull', action='store_true', help='운영 서버에서 변환 결과·캐시를 받아 온다(사전 밖 이름). 영상은 안 만든다')
    ap.add_argument('--hook-media', default='', help='훅 장면 사진/영상 파일(9:16 으로 크롭)')
    ap.add_argument('--hook-caption', default='', help='훅 캡션 = 첫 나레이션 (기본: What\'s X\'s Korean name?)')
    ap.add_argument('--hook-credit', default='', help='사진 출처 한 줄 (CC 표기)')
    ap.add_argument('--hook-secs', type=float, default=1.3, help='훅 화면 초(나레이션은 입력 장면으로 이어진다)')
    ap.add_argument('--typesec', type=float, default=1.5, help='타이핑 시작→제출 초')
    ap.add_argument('--reason-secs', type=float, default=3.0, help='Why this name 화면 초')
    ap.add_argument('--tempo', type=float, default=1.1, help='영어 나레이션 배속(음높이 유지)')
    ap.add_argument('--outro-line', default=''); ap.add_argument('--outro-big', default=''); ap.add_argument('--outro-sub', default='')
    ap.add_argument('--filled', type=float, default=0.35, help='이름을 다 입력한 화면을 제출 전에 보여주는 초')
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
    if args.script:
        if not os.path.exists(args.script):
            # 자동 대본을 파일로 내보내고 멈춘다 — 사람이 고친 뒤 같은 명령으로 다시 돌리면 그 대본으로 만든다
            doc = {'_설명': ['키(hook, input, result …)는 장면과 효과의 자리입니다. 키는 두고 문장만 고치세요.',
                           '목록([…])은 한 문장을 여러 소리로 나눈 것 — 한국어 글자는 {"text","lang":"ko-KR"} 로 한국어 목소리가 읽습니다.',
                           '줄을 지우면 그 장면은 나레이션 없이 짧게 지나갑니다. "audio": "파일경로" 를 주면 TTS 대신 그 소리를 씁니다.',
                           '장면 순서: hook → input → result+say → written → slow(음절 밑줄) → names(성/이름) → meaning(형광펜) → next → back0..(글자별 뜻) → backall → why → outro'],
                   'name': args.name, 'hook_media': args.hook_media, 'hook_credit': args.hook_credit, 'lines': lines}
            json.dump(doc, open(args.script, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print(f'대본을 썼습니다: {args.script} — 고친 뒤 같은 명령을 다시 실행하면 그 대본으로 영상을 만듭니다.')
            return
        doc = json.load(open(args.script, encoding='utf-8'))
        if doc.get('lines'):
            keep = {k: v for k, v in doc['lines'].items() if v not in (None, '', [])}
            for k in ('say', 'slow'):                      # 발음·천천히 읽기는 효과와 묶여 있어 항상 둔다
                keep.setdefault(k, lines[k])
            lines = keep
        args.hook_media = doc.get('hook_media') or args.hook_media
        args.hook_credit = doc.get('hook_credit') or args.hook_credit
        if script['hook'].get('media') is None and args.hook_media:
            ext = os.path.splitext(args.hook_media)[1].lower()
            script['hook']['media'] = {'kind': 'video' if ext in ('.mp4', '.webm', '.mov') else 'image', 'ext': ext,
                                       'caption': (lines.get('hook') if isinstance(lines.get('hook'), str) else None) or script['hook']['big'],
                                       'credit': args.hook_credit}
        elif script['hook'].get('media') and isinstance(lines.get('hook'), str):
            script['hook']['media']['caption'] = lines['hook']
        print(f'대본 파일 사용: {args.script}')
    for k, v in lines.items():
        print(f'  [{k}] {v}')

    print('소리 준비…' + ('' if TOKEN else ' (KNAME_VIDEO_TOKEN 없음 → 나레이션은 무음으로 대체)'))
    N = fetch_narration(lines, tempo=args.tempo)
    nd = {k: v['dur'] for k, v in N.items()}
    say_path, saydur = N['say']['parts'][0][0], N['say']['parts'][0][1]
    slow_path = N['slow']['parts'][0][0]
    slow_segs = voiced_segments(slow_path)
    n_syl = len(d['syllables'])
    if len(slow_segs) == n_syl:
        # 음절 사이 쉼을 고르게(0.42s) 다시 배치한다 — TTS 가 어떤 음절 앞에서 1초 넘게 쉬기도 한다
        slow_path, slow_segs = respace(slow_path, slow_segs, gap=0.42, lead=0.25)
        N['slow']['parts'][0] = (slow_path, dur(slow_path), slow_segs[0][0], slow_segs[-1][1])
        nd['slow'] = N['slow']['dur'] = dur(slow_path)
        # 한 글자짜리 한국어 클립("태.", "이.")은 TTS 가 무음으로 만들기도 한다 →
        # 천천히 읽은 클립에서 그 음절 소리를 잘라 대신 쓴다(성 한 글자, 뒷면 글자별 뜻)
        syl_clips = [cut_clip(slow_path, a, b) for a, b in slow_segs]
        n_s = len(d['surname'] or '') if d.get('surname') else 0

        def swap(key, idx, syl_i):
            if key in N and idx < len(N[key]['parts']) and 0 <= syl_i < len(syl_clips):
                c = syl_clips[syl_i]
                N[key]['parts'][idx] = (c, dur(c), 0.04, dur(c) - 0.04)
                N[key]['dur'] = sum(x[1] for x in N[key]['parts']) + GAP * (len(N[key]['parts']) - 1)
                N[key]['v0'] = N[key]['parts'][0][2]; nd[key] = N[key]['dur']
        if n_s == 1:
            swap('names', 0, 0)
        for i in range(len(d['hanja_lines'])):
            swap(f'back{i}', 0, n_s + i)
    else:
        v0, v1 = (slow_segs[0][0], slow_segs[-1][1]) if slow_segs else (0.0, nd['slow'])
        slow_segs = [(v0 + (v1 - v0) * i / n_syl, v0 + (v1 - v0) * (i + 1) / n_syl) for i in range(n_syl)]
    backs = [k for k in lines if k.startswith('back') and k != 'backall']

    # 장면 길이 = 나레이션 길이
    hook = args.hook_secs                                            # 훅 화면(1.3s). 훅 나레이션은 입력 장면으로 이어진다
    typepre = max(0.3, nd.get('hook', 0) + 0.15 - hook)                     # 훅 나레이션이 끝나면 타이핑 시작(= "Let's type it in")
    sayat = 0.05 + nd.get('result', 0) + 0.12                               # 카드가 뜨는 순간 "X becomes" → 발음
    t_written = sayat + saydur + 0.3
    t_slow = t_written + nd.get('written', 0) + 0.2
    t_names = t_slow + nd['slow'] + 0.3
    t_meaning = t_names + nd.get('names', 0) + 0.3
    t_next = t_meaning + nd.get('meaning', 0) + 0.3
    hold = t_next + nd.get('next', 0.6) * 0.55
    back_cues, tb = [], 0.9
    for k in backs:
        back_cues.append((k, tb)); tb += nd[k] + 0.25
    t_backall = tb + 0.1
    t_why = t_backall + nd.get('backall', 0) + 0.45
    back = t_why + nd.get('why', 0.6) * 0.5
    reason = args.reason_secs
    P = {'hook': hook, 'typepre': int(typepre * 1000), 'typesec': args.typesec, 'sayat': sayat, 'saydur': saydur,
         'hold': hold, 'back': back, 'reason': reason, 'reason2': 0, 'rviews': 1,
         'type': args.type, 'filled': args.filled, 'tail': nd.get('outro', 0) + args.tail}

    workdir = os.path.join(os.path.dirname(os.path.abspath(args.out)) or '.', '_rec'); os.makedirs(workdir, exist_ok=True)
    frames, events = asyncio.run(record(name, sex_key, P, workdir))
    ev = {e['ev']: e['t'] / 1000 for e in events}
    layout = {k: next((e['data'] for e in events if e['ev'] == k), None) for k in ('layout', 'layout_back', 'layout_reason')}
    t_zero = ev['start'] + 0.9
    T = {k: v - t_zero for k, v in ev.items()}
    T['saydur'] = saydur
    total = max(frames[-1][0], ev.get('outro', frames[-1][0]) + P['tail']) - t_zero
    print(f'프레임 {len(frames)}개, 박자(초): ' + ', '.join(f'{k}={v:.2f}' for k, v in T.items() if not k.startswith('layout')) + f'  길이 {total:.1f}s')

    # 소리 큐(영상 기준 초). 세그먼트가 여럿이면 GAP 을 두고 이어 붙인다. v0 = 그 클립 안에서 소리가 시작되는 초
    def cue(k, t, **kw):
        parts, tt = [], t
        for path, dd, v0, v1 in N[k]['parts']:
            parts.append({'t': tt, 'dur': dd, 'file': path, 'v0': v0, 'v1': v1}); tt += dd + GAP
        c = {'t': t, 'dur': nd[k], 'v0': N[k]['v0'], 'parts': parts}; c.update(kw); return c
    R = T['result']
    cues = {}
    def put(k, t, **kw):
        if k in N: cues[k] = cue(k, t, **kw)
    put('hook', 0.1); put('input', T['type'] - 0.05); put('result', R + 0.05); put('say', T['say'])
    put('written', R + t_written); put('slow', R + t_slow, segs=slow_segs); put('names', R + t_names)
    if 'names' in cues:
        # 브레이스 시각: names 의 한국어 세그먼트가 실제로 소리 나는 순간(세그먼트가 없으면 앞·중간)
        spec = lines['names'] if isinstance(lines['names'], list) else [lines['names']]
        ks = [p['t'] + p['v0'] for p, sp in zip(cues['names']['parts'], spec) if isinstance(sp, dict) and sp.get('lang')]
        cues['names']['koStarts'] = ks if ks else [cues['names']['t'] + cues['names']['v0'], cues['names']['t'] + cues['names']['dur'] * 0.5]
    put('meaning', R + t_meaning); put('next', R + t_next)
    B = T['back']
    for k, t in back_cues:
        put(k, B + t)
    put('backall', B + t_backall); put('why', B + t_why); put('outro', T['outro'] + 0.25)
    script['backKeys'] = backs

    seq, n = lay_out_frames(frames, t_zero, total, workdir)
    ov = asyncio.run(render_overlay(script, T, cues, layout, n, workdir, args.hook_media))

    # ① 소리 먼저 — 큐마다 adelay 로 제자리에 놓고 섞는다. (영상 인코딩과 한 번에 하면 amix 뒤의
    #    오디오 타임스탬프가 깨져 소리가 몇 초 만에 끊기는 일이 있었다 → 두 단계로 나눈다)
    mix_path = os.path.join(workdir, 'mix.m4a')
    cmd = ['ffmpeg', '-y', '-loglevel', 'error']
    fc, k, mix = '', -1, []
    for key, c in cues.items():
        for part in c['parts']:
            k += 1
            cmd += ['-i', part['file']]
            at = int(max(0, part['t']) * 1000)
            fc += f'[{k}:a]aformat=sample_rates=44100:channel_layouts=stereo,adelay={at}|{at}[a{k}];'
            mix.append(f'[a{k}]')
    fc += f'{"".join(mix)}amix=inputs={len(mix)}:normalize=0:dropout_transition=0,apad[a]'
    cmd += ['-filter_complex', fc, '-map', '[a]', '-t', f'{n / FPS:.3f}', '-c:a', 'aac', '-b:a', '160k', '-ar', '44100', mix_path]
    subprocess.run(cmd, check=True)
    # ② 영상(앱 화면 + 오버레이) + 소리
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-framerate', str(FPS), '-i', os.path.join(seq, '%05d.jpg'),
           '-framerate', str(FPS), '-i', os.path.join(ov, '%05d.png'), '-i', mix_path,
           '-filter_complex', '[0:v]format=rgba[base];[base][1:v]overlay=0:0:format=auto:eof_action=endall,format=yuv420p[v]',
           '-map', '[v]', '-map', '2:a', '-c:a', 'copy',
           '-t', f'{n / FPS:.3f}', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-r', str(FPS),
           '-g', str(FPS), '-keyint_min', str(FPS), '-sc_threshold', '0', '-bf', '0', '-profile:v', 'main', '-level', '4.0',
           '-movflags', '+faststart', args.out]
    subprocess.run(cmd, check=True)
    a_dur = float(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=duration', '-of', 'csv=p=0', args.out],
                                 capture_output=True, text=True).stdout.strip() or 0)
    if a_dur < n / FPS - 1:
        print(f'  경고: 오디오 길이 {a_dur:.1f}s < 영상 {n / FPS:.1f}s')
    print(f'완성: {args.out}  ({dur(args.out):.1f}s)')
    json.dump({'lines': lines, 'cues': {k: {kk: vv for kk, vv in v.items() if kk != 'parts'} for k, v in cues.items()}, 'T': T},
              open(os.path.splitext(args.out)[0] + '.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == '__main__':
    main()

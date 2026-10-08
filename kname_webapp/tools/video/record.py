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
import argparse, asyncio, base64, glob, hashlib, json, os, re, shutil, subprocess, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
os.chdir(BASE); sys.path.insert(0, BASE); sys.path.insert(0, os.path.join(BASE, 'lib')); sys.path.insert(0, os.path.join(BASE, 'data'))
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


KO_VOICE = os.environ.get('KNAME_KO_VOICE', 'ko-KR-Chirp3-HD-Aoede')   # 한국어 조각: 구글 이름 → ElevenLabs 서버에선 기본 목소리(Danbi)
# 영어 조각 목소리(ElevenLabs voice ID). 한국어 네이티브 목소리(Danbi)로 영어를 읽히면 억양이 남아서(2026-10-04 민우님 지적)
# 영어는 영어 네이티브 목소리로 읽힌다. ElevenLabs 기본 제공 목소리(계정마다 그대로 있음):
#   Jessica cgSgspJ2msm6clMCkdW9 (미국, 밝고 장난기)  Laura FGY2WhTYpPnrIDTdsKH5 (미국, 발랄)  Sarah EXAVITQu4vr4xnSDxMaL (미국, 부드러움)
#   Matilda XrExE9yKIg1WjnnlVkGX (미국, 따뜻함)        Lily pFZP5JQG7iQjIQuC4Bku (영국)
EN_VOICES = {'jessica': 'cgSgspJ2msm6clMCkdW9', 'laura': 'FGY2WhTYpPnrIDTdsKH5', 'sarah': 'EXAVITQu4vr4xnSDxMaL',
             'matilda': 'XrExE9yKIg1WjnnlVkGX', 'lily': 'pFZP5JQG7iQjIQuC4Bku', 'danbi': ''}
TEMPO_BY_SCENE = {'input': 1.1}                                       # 장면별 영어 배속(나머지는 --tempo)
EN_VOICE = os.environ.get('KNAME_EN_VOICE', 'danbi')          # 기본: 한국어와 같은 목소리(민우님 결정 2026-10-04)
ENGINE = 'google'                                                      # 서버가 알려준다: google | elevenlabs
GAP = 0.12                                                            # (구버전 호환)
CAP_Y = {'back': 1440}                                                            # 장면 → 자막 상단 y(px). 기본 1290
GAP_IN, GAP_COMMA, GAP_END = 0.08, 0.2, 0.42                          # 문장 안 조각 사이 / 쉼표·대시 뒤 / 문장이 끝난 뒤 쉼(초)


def part_texts(spec):
    parts = spec if isinstance(spec, list) else [spec]
    return [(p.get('text', '') if isinstance(p, dict) else p) for p in parts]


def part_gaps(spec):
    """한 장면의 조각 사이 쉼 — 문장이 끝나는(.!?) 조각 뒤는 길게, 문장 중간은 짧게."""
    t = part_texts(spec)
    return [GAP_END if re.search(r'[.!?]["\u201d\u2019\']?\s*$', x) else (GAP_COMMA if re.search(r'[,;:\u2014]\s*$', x) else GAP_IN) for x in t[:-1]]


def en_context(spec):
    """영어 조각마다 앞뒤 문장 전체를 숨은 문맥으로 — 조각이 따로 합성돼도 문장 하나처럼 이어 읽히게(ElevenLabs previous_text/next_text).
    한국어 조각은 로마자(rom)로 바꿔 넣는다."""
    parts = spec if isinstance(spec, list) else [spec]
    txt = [(p.get('rom') or p.get('text', '')) if isinstance(p, dict) else p for p in parts]
    ctx = []
    for i in range(len(parts)):
        ctx.append((' '.join(txt[:i])[-300:], ' '.join(txt[i + 1:])[:300]))
    return ctx


_ROM_D = None                                                       # build_lines 가 현재 변환 결과를 넣는다
KO_FRAMES = ['{p} — {t} — 이렇게 읽어요.', '{p} — {t} — 이렇게 불러요.']   # 테이크 2개 → 톤이 고른 쪽
KO_FRAMES_SYL = KO_FRAMES + ['{p} — {t} — 라고 읽어요.', '{p} — {t} — 이렇게 발음해요.']   # 한 글자는 테이크 4개(짧게 잘리는 일이 잦다)
KO_SYL_STRETCH = 1.1                                                  # 한 글자 조각 길이 배수(음높이 유지)
KO_SYL_FROM_FULL = True                                               # 낱글자는 이름 전체 발음에서 잘라 쓴다(False 면 문장 틀로 따로 합성)
KO_SYL_MIN = 0.24                                                     # 한 글자 소리 길이 최소(초) — Danbi 는 낱글자를 0.12~0.2s 로 짧게 읽는다


def ko(text, prev='한국 이름은', nxt=None, full=None, idx=None):
    """한국어로 읽을 세그먼트. ElevenLabs 는 글자만 주거나 숨은 문맥(prev/next)만 주면 영어식·높은 톤으로
    읽는 일이 잦다 → **한국어 문장 전체**('성은 — 서 — 이렇게 읽어요.')를 읽힌 뒤 가운데(— … —)만 잘라 쓴다.
    frame = 앞에 붙는(말하지 않는) 한국어 문맥."""
    d = {'text': text, 'lang': 'ko-KR', 'voice': KO_VOICE}
    if _ROM_D is not None:
        d['rom'] = romanize_text(text, _ROM_D)                  # 영어 조각의 문맥(말하지 않는 앞뒤 글)에 쓴다
    if ENGINE == 'elevenlabs':
        d['frame'] = prev.rstrip(', ')
        if full and len(text) == 1 and text in full and KO_SYL_FROM_FULL:
            d['full'] = full                                   # 낱글자는 이름 전체 발음에서 잘라 쓴다(민우님 2026-10-07: 낱글자 단독은 영어식으로 읽힘)
            d['idx'] = idx if idx is not None else full.index(text)
    return d


def _rms_env(path, hop=0.005):
    """5ms 간격 RMS 포락선(numpy)."""
    import numpy as np
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-f', 'f32le', '-ac', '1', '-ar', '16000', '-'], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32); n = int(16000 * hop)
    env = np.array([np.sqrt((x[i:i + n] ** 2).mean()) for i in range(0, len(x) - n, n)])
    return env, hop


def syllable_from_full(full_path, full_text, idx, ndir, key):
    """이름 전체 발음 클립에서 idx 번째 글자를 잘라 낸다. 글자 경계는 균등 분할 자리 근처(±30%)에서 에너지가 가장 낮은 곳으로 맞춘다."""
    try:
        import numpy as np
    except ImportError:
        return None
    out = os.path.join(ndir, f'syl_{key}.mp3')
    if os.path.exists(out):
        return out
    a, b = voiced_span(full_path)
    n = max(1, len(re.findall(r'[가-힣]', full_text)))
    L = (b - a) / n
    env, hop = _rms_env(full_path)
    def valley(t, lo, hi):
        i0, i1 = max(0, int(lo / hop)), min(len(env) - 1, int(hi / hop))
        if i1 <= i0:
            return t
        j = i0 + int(np.argmin(env[i0:i1]))
        return j * hop
    s0 = a if idx == 0 else valley(a + idx * L, a + idx * L - 0.3 * L, a + idx * L + 0.3 * L)
    e0 = b if idx == n - 1 else valley(a + (idx + 1) * L, a + (idx + 1) * L - 0.3 * L, a + (idx + 1) * L + 0.3 * L)
    pre = 0.03 if idx == 0 else 0.0
    post = 0.08 if idx == n - 1 else 0.0
    fade = 0.05
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', full_path, '-af',
                    f'atrim={max(0, s0 - pre):.3f}:{e0 + post:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.012,afade=t=out:st={max(0.0, (e0 + post) - max(0, s0 - pre) - fade):.3f}:d={fade}',
                    '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '96k', out], check=True)
    return out


def _pitch_mean(path, a=None, b=None):
    """[a,b] 구간의 평균 음높이(Hz). numpy 가 없으면 None."""
    try:
        import numpy as np
    except ImportError:
        return None
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-f', 'f32le', '-ac', '1', '-ar', '16000', '-'], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    if a is not None:
        x = x[int(a * 16000):int(b * 16000)]
    n, h, sr, vals = 640, 160, 16000, []
    for i in range(0, len(x) - n, h):
        f = x[i:i + n]
        if np.sqrt((f ** 2).mean()) < 0.02:
            continue
        f = f - f.mean(); ac = np.correlate(f, f, 'full')[n - 1:]; ac = ac / (ac[0] + 1e-9)
        lo, hi = sr // 400, sr // 80; k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] > 0.5:
            vals.append(sr / k)
    return float(np.median(vals)) if vals else None


def _f0_track(path):
    """프레임(10ms)별 음높이(Hz, 무성은 0). numpy 가 없으면 None."""
    try:
        import numpy as np
    except ImportError:
        return None
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-f', 'f32le', '-ac', '1', '-ar', '16000', '-'], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    n, h, sr, out = 640, 160, 16000, []
    for i in range(0, len(x) - n, h):
        f = x[i:i + n]
        if np.sqrt((f ** 2).mean()) < 0.02:
            out.append(0.0); continue
        f = f - f.mean(); ac = np.correlate(f, f, 'full')[n - 1:]; ac = ac / (ac[0] + 1e-9)
        lo, hi = sr // 400, sr // 80; k = lo + int(np.argmax(ac[lo:hi]))
        out.append(sr / k if ac[k] > 0.5 else 0.0)
    return np.array(out)


def prosody(path):
    """말투 지표: rng = 음높이 폭(반음, 10~90%), act = 프레임 사이 변화량(생동감), fall = 끝 음이 앞부분보다 얼마나 내려갔나(반음, 음수 = 내려감).
    소리 나는 구간이 짧으면(0.5s 미만) None."""
    f = _f0_track(path)
    if f is None:
        return None
    import numpy as np
    v = f[f > 0]
    if len(v) < 40:
        return None
    st = 12 * np.log2(v / np.median(v))
    sm = np.convolve(st, np.ones(5) / 5, 'valid')
    body = sm[-45:-12].mean() if len(sm) > 50 else sm[:-12].mean()
    return {'rng': float(np.percentile(sm, 90) - np.percentile(sm, 10)), 'act': float(np.abs(np.diff(sm)).mean()), 'hz': float(np.median(v)),
            'fall': float(sm[-12:].mean() - body), 'dur': dur(path)}


_SENT_END = re.compile(r'[.!?]["\u201d\u2019\']?\s*$')


def en_score(m, final, med_dur, mood=None):
    """자연스러움 점수 — 음높이 폭·생동감은 클수록, 문장 끝은 내려갈수록(−3 반음쯤) 좋다. 끝이 올라가거나 늘어지면 감점.
    mood='bright'(마지막 인사 등): 생동감·높은 음(밝음)을 더 쳐주고, 끝은 살짝만 내려가도 된다."""
    if mood == 'bright':
        sc = min(m['rng'], 12) / 12 + 1.2 * min(m['act'], 0.5) / 0.5 + 0.5 * max(-1.0, min(1.0, (m['hz'] - 235) / 35))
        sc -= 0.12 * abs(m['fall'] + 1.5)
        if m['fall'] > 2.5:
            sc -= 0.6
        if med_dur and m['dur'] > 1.25 * med_dur:
            sc -= 0.5
        return sc
    sc = min(m['rng'], 11) / 11 + 0.6 * min(m['act'], 0.45) / 0.45
    if final:
        sc -= 0.18 * abs(m['fall'] + 3)
        if m['fall'] > 1:
            sc -= 0.8
    else:
        sc -= 0.18 * max(0.0, -m['fall'] - 1.5)
    if med_dur and m['dur'] > 1.3 * med_dur:
        sc -= 0.5
    return sc


def en_variants(sp, n):
    """같은 문장을 다른 숨은 문맥으로 n 번 읽히면 억양이 조금씩 다르다 → 그중 고르려는 후보들(첫째는 원본)."""
    prev, nxt = sp.get('prev') or '', sp.get('next') or ''
    if sp.get('mood') == 'bright':                              # 신나는 앞말로 분위기를 띄운다(말하지 않음)
        pv = ['Pretty, right?!', 'I love it! So pretty!', 'Isn\'t that lovely?!', 'Wow, what a beautiful name!',
              'So cute, right?!', 'Yay! Such a pretty name!', 'Oh, I just love this one!', 'Fun, right?!',
              'Amazing! Okay!', 'Ha, so sweet!']
        return [dict(sp, prev=a, next=None) for a in pv[:n]]
    last = re.split(r'(?<=[.!?])\s+', prev.strip())[-1] if prev else ''
    pv = [prev, last, (prev + ' \u2014') if prev else 'Okay.', ('So, ' + prev) if prev else 'So.',
          ('Okay. ' + last) if prev else 'Alright, so.', 'Now, ' + last if last else 'Right.',
          ('Well, ' + last) if prev else 'Well,', ('Yes. ' + last) if prev else 'Yes.', ('Right. ' + prev) if prev else 'Hi!',
          (prev + ' ...') if prev else 'Hey.']
    nv = [nxt, nxt[:90], nxt, nxt[:90], nxt, nxt[:90], nxt, nxt[:90], nxt, nxt[:90]] if nxt else [''] * 10
    out, seen = [], set()
    for a, b in zip(pv, nv):
        key = (a, b)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(sp, prev=a or None, next=b or None))
        if len(out) >= n:
            break
    return out


def en_best_takes(jobs, ndir, takes, extra=6):
    """jobs = [(sp, 최종경로)] — 영어 조각마다 takes 가지 읽기를 받아 점수가 가장 높은 걸 최종 경로에 둔다.
    고른 결과가 아쉬우면(음높이 폭 < 7반음, 또는 문장 끝이 충분히 안 내려감) 변형을 extra 개 더 받아 다시 고른다.
    (운영 서버에 한 번에 모아서 요청한다. 같은 변형은 서버 캐시라 다시 돈이 들지 않는다.)"""
    import statistics

    def vpath(v):
        return os.path.join(ndir, 'tk_' + hashlib.sha1(json.dumps(v, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:16] + '.mp3')

    def fetch(vs):
        need = [(v, vpath(v)) for v in vs if not os.path.exists(vpath(v))]
        for chunk in [need[i:i + 30] for i in range(0, len(need), 30)]:
            try:
                res = http_json(f'{PROD}/api/narrate', {'lines': [v for v, _ in chunk]}, timeout=300)
                for (v, pth), item in zip(chunk, res.get('items', [])):
                    if item.get('url'):
                        download(item['url'], pth)
            except Exception as e:
                print(f'  테이크 요청 오류: {type(e).__name__}: {e}')

    def choose(sp, vs):
        cand = [(v, vpath(v)) for v in vs if os.path.exists(vpath(v))]
        ms = [(v, pth, prosody(pth)) for v, pth in cand]
        ok = [x for x in ms if x[2]]
        if not ok:
            return cand, None, [], False
        med = statistics.median(x[2]['dur'] for x in ok)
        fin = bool(_SENT_END.search(sp['text']))
        best = max(ok, key=lambda x: en_score(x[2], fin, med, sp.get('mood')))
        poor = (best[2]['rng'] < 7 or (fin and best[2]['fall'] > -1.5)) if sp.get('mood') != 'bright' else (best[2]['act'] < 0.35 or best[2]['rng'] < 8)
        return cand, best, ok, poor

    plan = []
    for sp, final in jobs:
        if (os.path.exists(final) and not os.environ.get('KNAME_RETAKE')) or len(re.findall(r"[A-Za-z0-9']+", sp['text'])) < 3:
            continue                                           # 이미 있음 / 너무 짧은 조각(측정이 불안정)은 원본 그대로
        vs = en_variants(sp, takes + extra)
        plan.append((sp, final, vs))
    fetch([v for _, _, vs in plan for v in vs[:takes]])
    more = [(sp, vs) for sp, _, vs in plan if len(vs) > takes and choose(sp, vs[:takes])[3]]
    if more:
        fetch([v for _, vs in more for v in vs[takes:takes + 2]])                 # 2차: 변형 2개 더
        more = [(sp, vs) for sp, vs in more if len(vs) > takes + 2 and choose(sp, vs[:takes + 2])[3]]
        if more:
            fetch([v for _, vs in more for v in vs[takes + 2:]])                  # 3차: 그래도 아쉬우면 나머지
    pool = {}                                                  # 같은 글귀가 여러 장면에 나오면 후보를 합쳐 쓴다(같은 소리가 두 번 나오지 않게 장면마다 다른 걸 고름)
    for sp, final, vs in plan:
        pool.setdefault(sp['text'], []).extend(vs)
    used = set()
    for sp, final, vs in plan:
        own, best, ok, poor = choose(sp, vs)
        if not own:
            continue
        seen_p, allv = set(), []
        for v in pool[sp['text']]:
            if vpath(v) not in seen_p:
                seen_p.add(vpath(v)); allv.append(v)
        cand, best, ok, poor = choose(sp, [v for v in allv if vpath(v) not in used] or vs)
        if not best:
            shutil.copyfile(own[0][1], final); continue
        used.add(best[1])
        shutil.copyfile(best[1], final)
        for old in glob.glob(final[:-4] + '_x*.mp3'):                  # 배속본은 새 테이크로 다시 만든다
            os.remove(old)
        base = ok[0]
        print(f"  take [{sp['text'][:44]}] {len(ok)}개 중 #{ok.index(best)} (폭 {best[2]['rng']:.1f} 끝 {best[2]['fall']:+.1f}"
              f" ← 원본 폭 {base[2]['rng']:.1f} 끝 {base[2]['fall']:+.1f})")


def ko_synthesize(sp, ndir):
    """frame 이 있는 한국어 세그먼트: 문장 전체를 테이크별로 받아 가운데 소리 구간만 잘라 낸 mp3 경로.
    테이크가 여럿이면 문장 전체의 음높이와 가장 가까운(톤이 튀지 않는) 테이크를 고른다."""
    text, frame = sp['text'], sp['frame']
    if sp.get('full') and len(text) == 1:
        fullp = ko_synthesize({'text': sp['full'], 'frame': '이 사람의 한국 이름은', 'voice': sp.get('voice')}, ndir)
        if fullp:
            key = hashlib.sha1(f"{sp['full']}|{sp['idx']}|{KO_SYL_STRETCH}|{KO_SYL_MIN}".encode('utf-8')).hexdigest()[:16]
            raw = syllable_from_full(fullp, sp['full'], int(sp['idx']), ndir, key)
            if raw:
                v0, v1 = voiced_span(raw); ln = max(0.05, v1 - v0)
                stretch = max(KO_SYL_STRETCH, min(1.5, KO_SYL_MIN / ln)) if ln * KO_SYL_STRETCH < KO_SYL_MIN else KO_SYL_STRETCH
                final = raw[:-4] + f'_s{int(stretch * 100)}.mp3'
                if not os.path.exists(final):
                    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', raw, '-af', f'atempo={1 / stretch:.4f}', '-c:a', 'libmp3lame', '-b:a', '96k', final], check=True)
                print(f'  ko [{text}] {ln * stretch:.2f}s ← {sp["full"]} 전체 발음의 {int(sp["idx"]) + 1}번째 글자')
                return final
    takes = [f.format(p=frame, t=text) for f in (KO_FRAMES_SYL if len(re.findall(r'[가-힣]', text)) == 1 else KO_FRAMES)]
    final = os.path.join(ndir, 'ko_' + hashlib.sha1(('|'.join(takes) + text + f'|s{KO_SYL_STRETCH}|m{KO_SYL_MIN}').encode('utf-8')).hexdigest()[:16] + '.mp3')
    if os.path.exists(final):
        return final
    paths = [os.path.join(ndir, hashlib.sha1((t + '|ko-KR|full').encode('utf-8')).hexdigest()[:16] + '.mp3') for t in takes]
    need = [(t, p) for t, p in zip(takes, paths) if not os.path.exists(p)]
    if need:
        res = http_json(f'{PROD}/api/narrate', {'lines': [{'text': t, 'lang': 'ko-KR', 'voice': sp.get('voice', KO_VOICE)} for t, _ in need]}, timeout=240)
        for (t, p), item in zip(need, res.get('items', [])):
            if item.get('url'):
                download(item['url'], p)
            else:
                print(f'  한국어 합성 실패: {t} — {item.get("error")}')
    cands = []
    n_syl = max(1, len(re.findall(r'[가-힣]', text)))
    for p in paths:
        if not os.path.exists(p):
            continue
        segs = voiced_segments(p, 0.12)
        if len(segs) < 3:
            print(f'  (한국어 테이크 구간 {len(segs)}개 — 건너뜀) {p}')
            continue
        a, b = segs[1][0], segs[-2][1]                      # 앞말 / 가운데(목표) / 뒷말
        if b - a < 0.1 * n_syl:
            continue
        mid, whole = _pitch_mean(p, a, b), _pitch_mean(p)
        score = abs((mid or 0) - (whole or 0)) if (mid and whole) else 0
        if n_syl == 1 and b - a < 0.18:
            score += 60                                      # 한 글자가 너무 짧게 잘린 테이크는 뒤로
        cands.append((score, p, a, b))
    if not cands:
        return None
    cands.sort()
    score, p, a, b = cands[0]
    cut = cut_clip(p, a, b)
    stretch = KO_SYL_STRETCH if n_syl == 1 else 1.0          # 한 글자씩 읽는 조각은 조금 길게(민우님 2026-10-05: 너무 짧다)
    if n_syl == 1 and (b - a) * stretch < KO_SYL_MIN:
        stretch = min(1.5, KO_SYL_MIN / (b - a))               # 그래도 KO_SYL_MIN 이 안 되면 더 늘린다(최대 1.5배)
    if stretch != 1.0:
        slow = cut[:-4] + f'_s{int(stretch * 100)}.mp3'
        if not os.path.exists(slow):
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', cut, '-af', f'atempo={1 / stretch:.4f}', '-c:a', 'libmp3lame', '-b:a', '96k', slow], check=True)
        cut = slow
    shutil.copyfile(cut, final)
    print(f'  ko [{text}] {(b - a) * stretch:.2f}s 톤차 {score:.0f}Hz ← {os.path.basename(p)}')
    return final


SERVER_V = 1


def probe_engine():
    """서버의 나레이션 엔진(google | elevenlabs)과 API 버전을 미리 알아 둔다 — 대본 문구가 엔진에 따라 다르다."""
    global ENGINE, SERVER_V
    if TOKEN:
        try:
            r = http_json(f'{PROD}/api/narrate', {'lines': []}, timeout=60)
            ENGINE, SERVER_V = r.get('engine', 'google'), r.get('v', 1)
        except Exception:
            pass
    return ENGINE


def fetch_narration(lines, tempo=1.0, takes=1, tempo_by=None):
    """{id: 문장 | {text,lang,voice} | [세그먼트, …]} → {id: {'parts': [(path, 초, v0, v1)…], 'dur': 초}}.
    토큰이 없거나 실패하면 글자 수로 어림한 무음 mp3. 영어 클립은 tempo 배로 빠르게(음높이 유지)."""
    global ENGINE
    tag, ver = 'local', 1
    if TOKEN:
        try:
            probe = http_json(f'{PROD}/api/narrate', {'lines': []}, timeout=60)
            tag, ver = probe.get('tag', 'local'), probe.get('v', 1)
            ENGINE = probe.get('engine', 'google')
        except Exception as e:
            print(f'  나레이션 서버 확인 실패: {type(e).__name__}: {e}')
    ndir = os.path.join(AUDIO_DIR, 'narr', tag); os.makedirs(ndir, exist_ok=True)

    segs = []                                     # (id, idx, spec, path)
    en_jobs = []                                  # 영어 조각 → 테이크 선택 대상
    for k, spec in lines.items():
        parts = spec if isinstance(spec, list) else [spec]
        ctxs = en_context(spec) if (ENGINE == 'elevenlabs' and TOKEN and isinstance(spec, list) and len(parts) > 1) else None
        for i, sp in enumerate(parts):
            sp = sp if isinstance(sp, dict) else {'text': sp}
            env = EN_VOICES.get(EN_VOICE.lower(), EN_VOICE)                 # 이름 또는 voice ID
            if env and ENGINE == 'elevenlabs' and not str(sp.get('lang', '')).startswith('ko') and not sp.get('audio') and not sp.get('voice'):
                sp = dict(sp, voice=env)
            if ctxs and not str(sp.get('lang', '')).startswith('ko') and not sp.get('audio'):
                nx = None if _SENT_END.search(sp['text']) else (sp.get('next') or ctxs[i][1] or None)   # 문장이 끝나는 조각엔 뒤 문맥을 주지 않는다 — 안 그러면 끝음이 올라간다
                sp = dict(sp, prev=sp.get('prev') or ctxs[i][0] or None, next=nx)
            if sp.get('audio'):
                # 대본에 소리 파일을 직접 지정한 줄(다른 TTS 나 녹음) — 44.1kHz mp3 로 맞춰 둔다
                src = sp['audio']
                conv = os.path.join(ndir, 'ext_' + hashlib.sha1((src + str(os.path.getmtime(src))).encode()).hexdigest()[:12] + '.mp3')
                if not os.path.exists(conv):
                    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', src, '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '128k', conv], check=True)
                segs.append((k, i, dict(sp, text=sp.get('text', '')), conv)); continue
            if str(sp.get('lang', '')).startswith('ko') and ENGINE == 'elevenlabs' and TOKEN and not sp.get('frame') and sp.get('prev'):
                sp = dict(sp, frame=str(sp['prev']).rstrip(', '))     # 옛 대본(prev/next) → 문장 틀
            if sp.get('frame') and ENGINE == 'elevenlabs' and TOKEN:
                # 한국어: 문장 전체를 읽힌 뒤 가운데만 잘라 쓴다(ko_synthesize)
                try:
                    p = ko_synthesize(sp, ndir)
                except Exception as e:
                    print(f'  한국어 합성 오류 [{k}] {sp["text"]}: {type(e).__name__}: {e}'); p = None
                if p:
                    segs.append((k, i, sp, p)); continue
                print(f'  (한국어 문장 틀 실패 → 글자만 합성) [{k}] {sp["text"]}')
            extra = '' if not (sp.get('lang') or sp.get('prompt') or sp.get('voice')) else f"|{sp.get('lang','')}|{sp.get('voice','')}|{sp.get('prompt','')}"
            if sp.get('mood'):
                extra += f"|mood={sp['mood']}"
            if sp.get('prev') or sp.get('next'):
                extra += f"|{sp.get('prev','')}|{sp.get('next','')}"
            h = hashlib.sha1((sp['text'] + extra).encode('utf-8')).hexdigest()[:16]
            segs.append((k, i, sp, os.path.join(ndir, f'{h}.mp3')))
            if takes > 1 and ENGINE == 'elevenlabs' and TOKEN and not str(sp.get('lang', '')).startswith('ko') and not sp.get('audio'):
                en_jobs.append((sp, os.path.join(ndir, f'{h}.mp3')))
    if en_jobs:
        en_best_takes(en_jobs, ndir, takes)
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
        elif (tempo_by or {}).get(k, tempo) != 1.0 and not sp.get('lang', '').startswith('ko'):
            tp = (tempo_by or {}).get(k, tempo)                       # 장면별 배속(예: input 만 1.1)
            fast = path[:-4] + f'_x{int(round(tp * 100))}.mp3'
            if not os.path.exists(fast):
                subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', path, '-af', f'atempo={tp:.3f}', '-c:a', 'libmp3lame', '-b:a', '96k', fast], check=True)
            path = fast
        v0, v1 = voiced_span(path)
        out.setdefault(k, {'parts': []})['parts'].append((path, dur(path), v0, v1))
    for k, v in out.items():
        v['gaps'] = part_gaps(lines[k]) if len(v['parts']) > 1 else []
        v['dur'] = sum(p[1] for p in v['parts']) + sum(v['gaps'])
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


def join_clips(paths, gap=0.42, lead=0.25):
    """클립들의 소리 구간만 잘라 일정한 쉼으로 이어 붙인다 → (파일, [(시작,끝)…])."""
    out = os.path.join(os.path.dirname(paths[0]), 'join_' + hashlib.sha1('|'.join(paths).encode()).hexdigest()[:12] + '.mp3')
    filt, parts, segs, t = [], [], [], lead
    for i, p in enumerate(paths):
        v0, v1 = voiced_span(p)
        a, b = max(0, v0 - 0.04), v1 + 0.08
        filt.append(f'[{i}:a]aformat=sample_rates=44100:channel_layouts=mono,atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[s{i}]')
        filt.append(f'aevalsrc=0:d={(lead if i == 0 else gap):.3f}:s=44100[g{i}]')
        parts += [f'[g{i}]', f'[s{i}]']
        segs.append((t + 0.04, t + 0.04 + (v1 - v0))); t += (b - a) + (gap if i < len(paths) - 1 else 0)
    filt.append('aevalsrc=0:d=0.3:s=44100[tail]'); parts.append('[tail]')
    filt.append(''.join(parts) + f'concat=n={len(parts)}:v=0:a=1[out]')
    cmd = ['ffmpeg', '-y', '-loglevel', 'error']
    for p in paths:
        cmd += ['-i', p]
    cmd += ['-filter_complex', ';'.join(filt), '-map', '[out]', '-ar', '44100', '-c:a', 'libmp3lame', '-b:a', '96k', out]
    subprocess.run(cmd, check=True)
    segs2 = voiced_segments(out)
    return out, (segs2 if len(segs2) == len(paths) else segs)


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


GLOSS_PRIMARY = {}                      # 한자 → 대표 뜻을 손으로 정해야 하는 소수의 예외 {'範': 'a model'}


def primary_gloss(gloss, hanja=None):
    """한자 뜻 사전 값은 'broad, boundless,' 처럼 동의어 나열이다. 눈으로 보는 카드엔 괜찮지만 소리로는
    끝맺음 없이 끊겨 들린다(2026-10-07). 영상에서는 **대표 뜻 하나**만 읽는다 — 사전의 첫 번째 뜻."""
    if hanja and hanja in GLOSS_PRIMARY:
        return GLOSS_PRIMARY[hanja]
    words = [w.strip() for w in str(gloss or '').split(',') if w.strip()]
    words = [w for w in words if not _HANGUL.search(w)]
    return words[0] if words else str(gloss or '').strip(' ,')


def surname_line(d):
    """성씨 뒤에 붙일 한 문장(주어 'The last name 고' 뒤). 없으면 None — 억지로 채우지 않는다.
    1~3위만 순위를 말한다 · 특징 있는 성씨는 그 사실 · 약한 성씨는 실제 성이 같은 유명인 · 그것도 없으면 글자 뜻 · 없으면 생략."""
    sn = d.get('surname')
    if not sn:
        return None
    try:
        from surname_notes import TOP3_SPOKEN_EN, SURNAME_SPOKEN_EN, celeb_spoken
    except Exception:
        return None
    line = TOP3_SPOKEN_EN.get(sn) or SURNAME_SPOKEN_EN.get(sn) or celeb_spoken(sn)
    if line:
        return line
    m = re.search(r"[Tt]he character means [\"\u201c']([^\"\u201d']+?)[.,]?[\"\u201d']", d.get('surname_desc') or '')
    if m:
        w = m.group(1).strip()
        w = w if len(w.split()) <= 4 else w.split(',')[0].strip()
        return f"is written with the character for \u201c{w}.\u201d"
    return None


def phrase_split(text, min_words=8):
    """긴 뜻풀이 한 줄을 두 호흡으로 — 'someone who shines like a star and is clear as glass' →
    ['someone who shines like a star,', 'and is clear as glass']. 한 덩어리로 읽히면 억양이 밋밋해진다(2026-10-04).
    쉼표 → ' and ' → ' but ' 순으로, 가운데에 가까운 자리를 고른다. 짧거나 나눌 자리가 없으면 그대로."""
    words = text.split()
    if len(words) < min_words:
        return [text]
    mid = len(words) / 2
    cands = [i + 1 for i, w in enumerate(words[:-2]) if w.endswith(',') and 3 <= i + 1 <= len(words) - 3]
    if not cands:
        cands = [i for i, w in enumerate(words) if w.lower() in ('and', 'but', 'yet') and 3 <= i <= len(words) - 3]
    if not cands:
        return [text]
    c = min(cands, key=lambda i: abs(i - mid))
    a, b = ' '.join(words[:c]), ' '.join(words[c:])
    return [a if a.endswith(',') else a + ',', b]


def build_lines(d, first, last, args):
    """장면별 나레이션(2026-10-03 대본). 한국어 글자는 같은 목소리가 한국어 문맥으로 읽는다(ko)."""
    global _ROM_D
    _ROM_D = d
    full_en = f'{first} {last}'.strip()
    given_ko = d['given']
    n_s = len(d['surname'] or '') if d.get('surname') else 0
    ms = (d.get('meaning_short') or '').strip()
    ms_l = (ms[0].lower() + ms[1:]) if ms else ''
    lines = {}
    # 훅: 나레이션 없음(캡션만). 입력 화면부터 말한다.   (2026-10-07 대본 개편: 짧게 · 뜻은 한 번만 · 참여 유도로 마무리)
    lines['input'] = f"Let's turn {full_en} into a Korean name."
    lines['result'] = [f"{first} becomes", ko(d['full_hangul'], prev='이 사람의 한국 이름은')]
    if n_s:
        lines['names'] = ["In Korea, the last name comes first. So", ko(d['surname'], prev='성은', full=d['full_hangul'], idx=0),
                          'is the last name, and', ko(given_ko, prev='이름은'), 'is the first.']
    else:
        lines['names'] = ["In Korea, the last name comes first.", ko(given_ko, prev='이름은'), 'is the first name.']
    if ms:
        pcs = phrase_split(ms_l.rstrip('.'))
        lines['meaning'] = ["And it has a meaning:", pcs[0]] + pcs[1:]
        lines['meaning'][-1] = lines['meaning'][-1].rstrip('.,') + '.'
    # 뒷면: 성씨 한 줄(있을 때만) → 글자별 대표 뜻.  합친 뜻은 meaning 장면에서 이미 말했다.
    back = ["Here's how."]
    sl = surname_line(d)
    if n_s and sl:
        back += ["The last name", ko(d['surname'], prev='성은', full=d['full_hangul'], idx=0), sl]
    hl = d['hanja_lines']
    for i, h in enumerate(hl):
        lead = "In the first name," if (i == 0 and n_s and sl) else ("and" if i == len(hl) - 1 and len(hl) > 1 else "")
        if lead:
            back.append(lead)
        back += [ko(h['syl'], prev='이 글자는', full=d['full_hangul'], idx=n_s + i),
                 f"means {primary_gloss(h['gloss'], h.get('hanja'))}" + ("," if i < len(hl) - 1 else ".")]
    if args.back_tail:
        back.append(args.back_tail)
    lines['back'] = back
    lines['why'] = {'text': args.why_line or "Want yours? Drop a name below, or try it. Link in bio. Thank you!", 'mood': 'bright'}   # mood=bright: 밝고 활기찬 테이크를 고른다
    return lines


_WEAK = {'the', 'a', 'an', 'is', 'and', 'of', 'that', 'to', 'like', 'as', 'so', 'in', 'at', 'one', 'with', 'for', 'it'}


def split_caption(sw, max_words=7, max_chars=38):
    """한 문장의 단어들 → 자막 줄들. 쉼표·대시로 뜻 덩어리(절)를 먼저 나눈 뒤 한 줄에 들어가는 만큼 묶는다.
    한 절이 혼자 한 줄을 넘으면 글자 수 한도에서 끊되 the/a/is/and 같은 짧은 말로 줄이 끝나지 않게 한다."""
    chars = lambda ws: len(' '.join(w[0] for w in ws))
    punct = lambda w: bool(re.search(r'[,;:\u2014]$', w[0]))
    if len(sw) <= max_words and chars(sw) <= max_chars:
        return [sw]
    roomy = lambda ws: len(ws) <= max_words + 1 and chars(ws) <= max_chars + 8

    def hard(ws):                                              # 쉼표 없는 긴 절: 한도에서 끊기
        out, cur = [], []
        for w in ws:
            if cur and (len(cur) + 1 > max_words or chars(cur + [w]) > max_chars):
                cut = len(cur)
                while cut > 2 and cur[cut - 1][0].lower().strip(',.;:') in _WEAK:
                    cut -= 1
                out.append(cur[:cut]); cur = cur[cut:]
            cur.append(w)
        if cur:
            out.append(cur)
        return out
    clauses, cur = [], []
    for w in sw:
        cur.append(w)
        if punct(w):
            clauses.append(cur); cur = []
    if cur:
        clauses.append(cur)
    lines_, cur = [], []
    for cl in clauses:
        if cur and roomy(cur + cl):
            cur = cur + cl; continue
        if cur:
            lines_.append(cur); cur = []
        if roomy(cl):
            cur = cl
        else:
            hs = hard(cl); lines_ += hs[:-1]; cur = hs[-1]
    if cur:
        lines_.append(cur)
    if len(lines_) > 1 and len(lines_[-1]) == 1 and len(lines_[-2]) > 3:
        lines_[-1].insert(0, lines_[-2].pop())
    return lines_


def caption_lines(cues, skip=('why',), max_words=7):
    """장면별 소리 시각 → 영어 자막 [{t0,t1,text}]. 단어 시각은 조각의 소리 구간을 글자 수로 나눠 어림하고,
    문장이 끝나면 줄을 바꾸며 한 줄은 max_words 단어 안으로 균등하게 쪼갠다. 한국어 조각은 한글 그대로 한 덩어리."""
    out = []
    for k, c in cues.items():
        if k in skip or not c.get('parts'):
            continue
        words = []                                            # (단어, 시작, 끝)
        for p in c['parts']:
            a, b = p['t'] + p['v0'], p['t'] + p['v1']
            toks = [p['text']] if p['ko'] else [w for w in p['text'].split() if w]
            if not toks:
                continue
            wts = [max(1, len(w)) + 1 for w in toks]; tot = sum(wts); x = a
            for w, wt in zip(toks, wts):
                y = x + (b - a) * wt / tot
                if w in ('—', '–', '-') and words:             # 대시는 앞 단어에 붙인다
                    words[-1] = (words[-1][0] + ' ' + w, words[-1][1], y)
                else:
                    words.append((w, x, y))
                x = y
        sents, cur = [], []                                    # 문장 단위로 자른다
        for w in words:
            cur.append(w)
            if re.search(r'[.!?]["\u201d\u2019\']?$', w[0]):
                sents.append(cur); cur = []
        if cur:
            sents.append(cur)
        lines_ = []
        for sw in sents:
            lines_ += split_caption(sw, max_words)
        for i, ln in enumerate(lines_):
            t0 = ln[0][1] - 0.06
            end = ln[-1][2] + 0.28
            nxt = lines_[i + 1][0][1] - 0.06 if i + 1 < len(lines_) else None
            t1 = min(end, nxt - 0.02) if nxt is not None else end
            out.append({'t0': round(t0, 3), 't1': round(max(t1, t0 + 0.4), 3), 'text': ' '.join(w[0] for w in ln), 'scene': k})
    return out


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
                 'caption': args.hook_caption or f"{first} {last.strip()}'s Korean name?".replace("  ", " "), 'credit': args.hook_credit}
    script = {
        'ep': f'Korean name · {name}',
        'hook': {'eyebrow': 'What is', 'big': f'{name}’s', 'sub': 'Korean name?', 'media': media},
        'rom': d['full_rom'], 'nSurname': len(d['surname'] or '') if d.get('surname') else 0,
        'outro': {'big': args.outro_big or 'Want yours?', 'sub': args.outro_sub or 'Drop a name below, or try it. Link in bio.', 'hand': 'Thank you! \U0001F517'},
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
         'filled': P['filled'], 'typesec': P['typesec'], 'typepre': P['typepre'], 'backend': 1 if P.get('backend') else 0,
         'say': 0, 'gap': 0.4, 'intro': f'{name}?', 'card': P['hook'], 'outro': 'What’s your name?', 'zoom': 3}
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
    global EN_VOICE
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
    ap.add_argument('--tempo', type=float, default=0, help='영어 나레이션 배속(음높이 유지). 0 = 자동(영어 목소리 1.0, danbi 0.85 — Danbi 는 원속도가 분당 ~235단어로 빨라서)')
    ap.add_argument('--tempo-scene', action='append', default=[], help='장면별 배속, 예: --tempo-scene input=1.1 (기본: input 1.1 — 도입 설명은 빠르게, 민우님 2026-10-04)')
    ap.add_argument('--en-voice', default='', help='영어 조각 목소리: jessica(기본)·laura·sarah·matilda·lily 또는 ElevenLabs voice ID. danbi = 한국어와 같은 목소리')
    ap.add_argument('--takes', type=int, default=4, help='영어 조각마다 몇 번 읽혀 가장 자연스러운 억양을 고를지(1 = 고르지 않음). 글자 수가 그만큼 곱절로 든다')
    ap.add_argument('--no-captions', action='store_true', help='영어 자막을 넣지 않는다')
    ap.add_argument('--outro-big', default=''); ap.add_argument('--outro-sub', default='')
    ap.add_argument('--why-line', default='', help='마지막 화면 나레이션'); ap.add_argument('--why-secs', type=float, default=3.0)
    ap.add_argument('--back-tail', default='', help='뒷면 끝에 덧붙일 한마디(기본 없음)')
    ap.add_argument('--filled', type=float, default=0.5, help='이름을 다 입력한 화면을 제출 전에 보여주는 초(로딩 느낌)')
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

    probe_engine()
    script, d, first, last = build_script(name, SEX.get(sex_key, '여'), args)
    print(f'{name} → {script["full"]} ({script["hanja"]}, {script["rom"]})')
    lines = build_lines(d, first, last, args)
    if args.script:
        if not os.path.exists(args.script):
            # 자동 대본을 파일로 내보내고 멈춘다 — 사람이 고친 뒤 같은 명령으로 다시 돌리면 그 대본으로 만든다
            doc = {'_설명': ['키(input, result …)는 장면과 효과의 자리입니다. 키는 두고 문장만 고치세요.',
                           '목록([…])은 한 문장을 여러 소리로 나눈 것 — {"text","lang":"ko-KR","frame":…} 부분은 한국어로 읽히고(frame 은 말하지 않는 앞말 — "frame — text — 이렇게 읽어요." 문장 전체를 한국어로 읽힌 뒤 가운데만 씁니다), 그 순간에 효과(브레이스·형광펜)가 붙습니다.',
                           '줄을 지우면 그 장면은 나레이션 없이 짧게 지나갑니다. "audio": "파일경로" 를 주면 TTS 대신 그 소리를 씁니다.',
                           '장면 순서: 훅(캡션만) → input(타이핑) → result(앞면, 이름 발음+밑줄) → names(성/이름 브레이스) → meaning(형광펜) → back(뒷면: 성씨·글자별 뜻·합친 뜻) → why(마지막 화면)'],
                   'name': args.name, 'hook_media': args.hook_media, 'hook_credit': args.hook_credit, 'en_voice': args.en_voice or EN_VOICE, 'tempo_scene': dict(TEMPO_BY_SCENE),
                   'hook_caption': script['hook']['media']['caption'] if script['hook'].get('media') else f"{args.name.split(':')[0]}'s Korean name?",
                   'lines': lines}
            json.dump(doc, open(args.script, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            print(f'대본을 썼습니다: {args.script} — 고친 뒤 같은 명령을 다시 실행하면 그 대본으로 영상을 만듭니다.')
            return
        doc = json.load(open(args.script, encoding='utf-8'))
        if doc.get('lines'):
            keep = {k: v for k, v in doc['lines'].items() if v not in (None, '', [])}
            lines = keep
        args.hook_media = doc.get('hook_media') or args.hook_media
        args.en_voice = args.en_voice or doc.get('en_voice') or ''
        if isinstance(doc.get('tempo_scene'), dict):
            TEMPO_BY_SCENE.clear(); TEMPO_BY_SCENE.update({k: float(v) for k, v in doc['tempo_scene'].items()})
        args.hook_credit = doc.get('hook_credit') or args.hook_credit
        if script['hook'].get('media') is None and args.hook_media:
            ext = os.path.splitext(args.hook_media)[1].lower()
            script['hook']['media'] = {'kind': 'video' if ext in ('.mp4', '.webm', '.mov') else 'image', 'ext': ext,
                                       'caption': (lines.get('hook') if isinstance(lines.get('hook'), str) else None) or script['hook']['big'],
                                       'credit': args.hook_credit}
        if doc.get('hook_caption'):
            if script['hook'].get('media'): script['hook']['media']['caption'] = doc['hook_caption']
            else: script['hook']['big'], script['hook']['eyebrow'], script['hook']['sub'] = doc['hook_caption'], '', ''
        print(f'대본 파일 사용: {args.script}')
    for k, v in lines.items():
        print(f'  [{k}] {v}')

    print('소리 준비…' + ('' if TOKEN else ' (KNAME_VIDEO_TOKEN 없음 → 나레이션은 무음으로 대체)'))
    if args.en_voice:
        EN_VOICE = args.en_voice
    if not args.tempo:
        args.tempo = 0.85 if not EN_VOICES.get(EN_VOICE.lower(), EN_VOICE) else 1.0
    tempo_by = dict(TEMPO_BY_SCENE)
    for kv in args.tempo_scene:
        k, _, v = kv.partition('=')
        if v:
            tempo_by[k.strip()] = float(v)
    print(f'  배속: 기본 {args.tempo}' + (', ' + ', '.join(f'{k} {v}' for k, v in tempo_by.items()) if tempo_by else ''))
    N = fetch_narration(lines, tempo=args.tempo, takes=args.takes, tempo_by=tempo_by)
    nd = {k: v['dur'] for k, v in N.items()}
    # 이름 발음 = result 의 한국어 세그먼트
    say_part = next((p for p, sp in zip(N['result']['parts'], lines['result']) if isinstance(sp, dict)), N['result']['parts'][-1])
    saydur = say_part[1]

    # 장면 길이 = 나레이션 길이
    hook = args.hook_secs
    typing = 0.25 + 0.5 + 0.07 * len(name.replace(' ', '')) + 0.2       # 타이핑에 드는 대략의 초
    typepre = max(0.3, nd['input'] - typing - 0.2)                    # "let's type the name in" 과 함께 타이핑 끝나게
    sayat = 0.05 + nd['result'] + 1.0                                 # (발음은 result 안에 있다 — 데모의 발음 자리는 비워 둔다)
    t_names = 0.05 + nd['result'] + 0.35
    t_meaning = t_names + nd.get('names', 0) + 0.3
    hold = t_meaning + nd.get('meaning', 0) + 0.6
    back = 0.9 + nd['back'] + 0.5
    P = {'hook': hook, 'typepre': int(typepre * 1000), 'typesec': 0, 'sayat': -1, 'saydur': 0.01,
         'hold': hold, 'back': back, 'reason': 0, 'reason2': 0, 'rviews': 1, 'backend': 1,
         'type': args.type, 'filled': args.filled, 'tail': max(args.why_secs, nd['why'] + 0.5)}

    workdir = os.path.join(os.path.dirname(os.path.abspath(args.out)) or '.', '_rec'); os.makedirs(workdir, exist_ok=True)
    frames, events = asyncio.run(record(name, sex_key, P, workdir))
    ev = {e['ev']: e['t'] / 1000 for e in events}
    layout = {k: next((e['data'] for e in events if e['ev'] == k), None) for k in ('layout', 'layout_back', 'layout_reason')}
    t_zero = ev['start'] + 0.9
    T = {k: v - t_zero for k, v in ev.items()}
    T['saydur'] = saydur
    T['card'] = T['type'] - typepre                                    # 입력 카드가 뜬 시각(훅이 걷히는 때)
    total = max(frames[-1][0], ev.get('outro', frames[-1][0]) + P['tail']) - t_zero
    print(f'프레임 {len(frames)}개, 박자(초): ' + ', '.join(f'{k}={v:.2f}' for k, v in T.items() if not k.startswith('layout')) + f'  길이 {total:.1f}s')

    def cue(k, t, **kw):
        parts, tt = [], t
        gaps = N[k].get('gaps') or []
        for j, ((path, dd, v0, v1), sp) in enumerate(zip(N[k]['parts'], (lines[k] if isinstance(lines[k], list) else [lines[k]]))):
            parts.append({'t': tt, 'dur': dd, 'file': path, 'v0': v0, 'v1': v1, 'ko': isinstance(sp, dict) and bool(sp.get('lang')),
                          'text': (sp.get('text', '') if isinstance(sp, dict) else sp)})
            tt += dd + (gaps[j] if j < len(gaps) else 0)
        c = {'t': t, 'dur': nd[k], 'v0': N[k]['v0'], 'parts': parts,
             'koStarts': [p['t'] + p['v0'] for p in parts if p['ko']]}
        c.update(kw); return c
    R = T['result']
    cues = {}
    def put(k, t, **kw):
        if k in N: cues[k] = cue(k, t, **kw)
    put('input', T['card'] + 0.1)
    put('result', R + 0.05); put('names', R + t_names); put('meaning', R + t_meaning)
    if 'result' in cues:                                              # 음절 밑줄: 이름 발음 구간
        kp = next((p for p in cues['result']['parts'] if p['ko']), None)
        if kp:
            cues['say'] = {'t': kp['t'], 'dur': kp['dur'], 'v0': kp['v0'], 'v1': kp['v1'], 'parts': [], 'koStarts': []}
    if 'meaning' in cues:                                             # 형광펜: 뜻 문장을 말하는 순간
        mp = cues['meaning']['parts']                                  # 한국어 이름 뒤의 뜻풀이 조각들 전체 구간
        k0 = min(max([i for i, p in enumerate(mp) if p['ko']] or [0]) + 1, len(mp) - 1)   # 한국어 조각이 없으면 도입구('And it has a meaning:') 다음부터
        cues['meaning']['hlAt'] = mp[k0]['t'] + mp[k0]['v0']; cues['meaning']['hlDur'] = (mp[-1]['t'] + mp[-1]['v1']) - cues['meaning']['hlAt']
    put('back', T['back'] + 0.9)
    if 'back' in cues:                                                # 뒷면 형광펜: 성씨 문장이 있을 때만 성씨 줄을 칠하고, 문장이 읽히는 시간만큼 천천히 쓸어간다
        bp = cues['back']['parts']
        ko_i = [i for i, p_ in enumerate(bp) if p_['ko']]
        has_sn = len(ko_i) == len(d['hanja_lines']) + 1
        cues['back']['hasSn'] = has_sn
        if has_sn and ko_i[0] + 1 < len(bp):
            a_, b_ = bp[ko_i[0]], bp[ko_i[0] + 1]
            cues['back']['snDur'] = max(1.0, (b_['t'] + b_['v1']) - (a_['t'] + a_['v0']))
    put('why', T['outro'] + 0.2)
    script['backKeys'] = []
    script['captions'] = [] if args.no_captions else caption_lines(cues)
    for cp in script['captions']:                                      # 장면별 자막 높이(화면 속 내용을 덜 가리는 자리)
        cp['y'] = CAP_Y.get(cp.pop('scene'), 1290)

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
    json.dump({'lines': lines, 'captions': script.get('captions'), 'cues': {k: {kk: vv for kk, vv in v.items() if kk != 'parts'} for k, v in cues.items()}, 'T': T},
              open(os.path.splitext(args.out)[0] + '.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == '__main__':
    main()

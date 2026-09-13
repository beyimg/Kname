# -*- coding: utf-8 -*-
"""OG 공유 카드(1200×630 PNG) 렌더러.

링크를 카톡·트위터·디스코드 등에 붙였을 때 뜨는 미리보기 카드다. 웹 결과 카드
(#shareCard)와 같은 종이(색·질감)를 쓰되, 미리보기 크기에서 읽히도록 요소를
줄였다 — 글자 아래 한자·발음 줄은 넣지 않고, 영어 이름은 조금 크게, 한글은
조금 작게. (2026-09-13 시안 확정 값)

사용:
    from lib.og_card import render_result_card, render_default_card
    render_result_card({
        'input': 'Emma Smith', 'syllables': ['서', '예', '나'],
        'full_rom': 'Seo Ye-na', 'meaning_short': 'Someone wise who shines with grace',
        'surname_hanja': '徐',
    }, '/var/data/og/emma-smith-f.png')

폰트: 한글·한자·영문 모두 Noto Serif KR Light 한 벌로 그린다(FONT_PATH). 웹 폰트
notoserifkr-app.woff2 에는 영문 글자가 없으므로 서버용 TTF 는 따로 둔다.
Pillow 에 이탤릭이 없어 이탤릭 줄은 층을 따로 그려 기울인다(브라우저의 faux-italic 과 같다).

배경은 static/paper-grain.png(웹 카드)와 같은 원리로 만든다 — 높이맵 → 좌상단 빛 음영.
tools/make_paper_grain.py 의 설명 참고.
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1200, 630
INK, INK_SOFT, INK_FAINT = (26, 26, 26), (110, 110, 110), (168, 168, 168)
LINE = (224, 216, 199)
SEAL = (176, 67, 58)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 본문 세리프(한글·한자·영문). 파일이 없으면 render_* 가 FileNotFoundError 를 낸다.
FONT_PATH = os.environ.get('OG_FONT') or os.path.join(_BASE, 'static', 'fonts', 'NotoSerifKR-Light.otf')
# 작은 대문자 라벨(YOUR KOREAN NAME / MYKOREANNAME.CC)용 산세리프. 없으면 본문 폰트로 대신한다.
SANS_PATH = os.environ.get('OG_FONT_SANS') or os.path.join(_BASE, 'static', 'fonts', 'label-sans.ttf')

# 종이 색·질감·가장자리 — 시안에서 확정한 값. 바꾸면 웹 카드(style.css --paper-grad)도 같이 맞출 것
PAPER_BASE = (254.0, 253.0, 248.0)   # 요철·비네트 적용 전 바탕색 (측정 결과: 가운데 250·248·241)
TEXTURE = 1.0                         # 요철 명암 세기
VIGNETTE = 1.0                        # 가장자리 어두움 세기 (모서리에서 최대 약 6%)


def _font(path, size, fallback=None):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        if fallback:
            return ImageFont.truetype(fallback, size)
        raise


def background(texture=TEXTURE, vignette=VIGNETTE, seed=11):
    """코튼지 느낌의 종이 배경.
    색: 아주 밝은 웜 오프화이트(R≈G, B 조금 낮음).
    질감: 색 얼룩이 아니라 '요철'. 높이맵을 만들고 좌상단에서 빛을 비춘 것처럼
          기울기로 음영을 넣는다(엠보싱). 밝은 알갱이와 그늘진 알갱이가 함께 보인다.
    가장자리: 회색이 아니라 따뜻한 갈색 쪽으로 어두워진다(파랑을 더 줄임)."""
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    img = np.ones((H, W, 3), np.float32) * np.array(PAPER_BASE)

    rng = np.random.default_rng(seed)

    def octave(scale, blur):
        h, w = int(H / scale) + 2, int(W / scale) + 2
        n = rng.normal(0, 1, (h, w)).astype(np.float32)
        im = Image.fromarray(((n * 28) + 128).clip(0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
        if blur:
            im = im.filter(ImageFilter.GaussianBlur(blur))
        return np.array(im, np.float32) - 128

    height = octave(3.5, 0.9) * 1.0 + octave(1.8, 0.4) * 0.35 + octave(10, 2.5) * 0.3
    gy, gx = np.gradient(height)
    shade = -(gx + gy) * 0.29 * texture     # 빛을 받는 면 +, 그늘 −
    tone = height * 0.03 * texture          # 아주 약한 농담
    img = img + (shade + tone)[..., None]

    nx = (x - W / 2) / (W / 2)
    ny = (y - H / 2) / (H / 2)
    r = np.sqrt(nx ** 2 + (ny * 0.9) ** 2)
    k = 0.058 * vignette * np.clip((r - 0.25) / 1.0, 0, 1) ** 1.5
    v = np.stack([1 - k * 0.75, 1 - k * 1.0, 1 - k * 1.4], axis=-1)
    img = img * v
    return Image.fromarray(img.clip(0, 255).astype(np.uint8), 'RGB')


def _text_w(draw, s, f, spacing=0):
    if not spacing:
        return draw.textlength(s, font=f)
    return sum(draw.textlength(ch, font=f) for ch in s) + spacing * (len(s) - 1)


def _draw_center(draw, y, s, f, fill, spacing=0):
    w = _text_w(draw, s, f, spacing)
    x = (W - w) / 2
    if spacing:
        for ch in s:
            draw.text((x, y), ch, font=f, fill=fill)
            x += draw.textlength(ch, font=f) + spacing
    else:
        draw.text((x, y), s, font=f, fill=fill)


def _italic_layer(s, f, fill, shear=0.18):
    """이탤릭 층: 따로 그려 오른쪽으로 기울인다. (W×200, 글자는 y=40 에 가운데 정렬)"""
    tmp = Image.new('RGBA', (W, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    w = d.textlength(s, font=f)
    d.text(((W - w) / 2, 40), s, font=f, fill=fill + (255,))
    return tmp.transform(tmp.size, Image.AFFINE, (1, shear, -shear * 100, 0, 1, 0), resample=Image.BICUBIC)


def _fit(draw, s, path, size, max_w, min_size):
    """max_w 를 넘으면 글자 크기를 줄인다(긴 이름·긴 뜻 대비)."""
    f = _font(path, size)
    while size > min_size and draw.textlength(s, font=f) > max_w:
        size -= 2
        f = _font(path, size)
    return f


def _frame(img):
    ImageDraw.Draw(img).rounded_rectangle((26, 26, W - 26, H - 26), radius=6, outline=LINE, width=2)


def _seal(img, dr, hanja, f_seal):
    sx, sy, sz = W - 52 - 66, H - 44 - 66, 66
    dr.rounded_rectangle((sx, sy, sx + sz, sy + sz), radius=6, outline=SEAL, width=3)
    hw = dr.textlength(hanja, font=f_seal)
    bb = f_seal.getbbox(hanja)
    dr.text((sx + (sz - hw) / 2, sy + (sz - (bb[3] - bb[1])) / 2 - bb[1]), hanja, font=f_seal, fill=SEAL)


def render_result_card(d, out):
    """개인 결과 카드.
    d: input(영어 이름), syllables(한글 음절 리스트 또는 {'ch':..} 리스트), full_rom(로마자),
       meaning_short(한 줄 뜻), surname_hanja(성 한자, 없으면 낙관 생략)"""
    img = background()
    _frame(img)
    dr = ImageDraw.Draw(img)

    f_eyebrow = _font(SANS_PATH, 20, FONT_PATH)
    f_brand = _font(SANS_PATH, 20, FONT_PATH)
    f_seal = _font(FONT_PATH, 36)

    _draw_center(dr, 66, 'YOUR KOREAN NAME', f_eyebrow, INK_FAINT, spacing=6)

    f_orig = _fit(dr, d['input'], FONT_PATH, 36, 1000, 24)
    layer = _italic_layer(d['input'], f_orig, INK_SOFT)
    img.paste(layer, (0, 116 - 40), layer)

    syl = [s['ch'] if isinstance(s, dict) else s for s in d['syllables']]
    f_kr = _font(FONT_PATH, 118)
    gap = 20
    ws = [dr.textlength(s, font=f_kr) for s in syl]
    x = (W - (sum(ws) + gap * (len(syl) - 1))) / 2
    for s, w in zip(syl, ws):
        dr.text((x, 196), s, font=f_kr, fill=INK)
        x += w + gap

    f_rom = _fit(dr, d['full_rom'], FONT_PATH, 40, 1000, 28)
    _draw_center(dr, 352, d['full_rom'], f_rom, INK, spacing=1)
    dr.rectangle(((W - 60) / 2, 424, (W + 60) / 2, 425), fill=LINE)

    if d.get('meaning_short'):
        q = '“' + d['meaning_short'] + '”'
        f_mean = _fit(dr, q, FONT_PATH, 33, 1060, 22)
        layer = _italic_layer(q, f_mean, INK)
        img.paste(layer, (0, 456 - 40), layer)

    _draw_center(dr, 566, 'MYKOREANNAME.CC', f_brand, INK_FAINT, spacing=3)

    if d.get('surname_hanja'):
        _seal(img, dr, d['surname_hanja'], f_seal)

    _save(img, out)
    return out


def render_default_card(out, examples=('서예나', '문이언', '전하린')):
    """홈(/)용 기본 카드 — 사이트 소개."""
    img = background()
    _frame(img)
    dr = ImageDraw.Draw(img)
    f_label = _font(SANS_PATH, 20, FONT_PATH)

    _draw_center(dr, 70, 'WHAT WOULD YOUR NAME BE IN KOREAN?', f_label, INK_FAINT, spacing=5)
    _draw_center(dr, 118, 'Find your Korean name', _font(FONT_PATH, 66), INK, spacing=1)
    f_kr = _font(FONT_PATH, 100)
    ws = [dr.textlength(s, font=f_kr) for s in examples]
    gap = 64
    x = (W - sum(ws) - gap * (len(examples) - 1)) / 2
    mid = len(examples) // 2
    for i, (s, w) in enumerate(zip(examples, ws)):
        dr.text((x, 246), s, font=f_kr, fill=INK if i == mid else INK_FAINT)
        x += w + gap
    layer = _italic_layer('with its hanja, its meaning, and how to say it', _font(FONT_PATH, 30), INK_SOFT)
    img.paste(layer, (0, 408 - 40), layer)
    dr.rectangle(((W - 60) / 2, 476, (W + 60) / 2, 477), fill=LINE)
    _draw_center(dr, 500, 'Free  ·  takes 10 seconds  ·  no sign-up', f_label, INK_FAINT, spacing=1)
    _draw_center(dr, 562, 'MYKOREANNAME.CC', f_label, INK_FAINT, spacing=3)
    _save(img, out)
    return out


def _save(img, out):
    """확장자로 형식을 정한다. 질감 때문에 PNG 는 약 1MB 이고 256색으로 줄이면 글자·낙관 색이
    변하므로, 공유용은 JPEG(.jpg, 품질 90, 색 서브샘플링 없음 → 글자 선명, 약 250KB)를 권한다."""
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out + '.tmp'
    if out.lower().endswith('.png'):
        img.save(tmp, 'PNG', optimize=True)
    else:
        img.save(tmp, 'JPEG', quality=90, subsampling=0, optimize=True)
    os.replace(tmp, out)

# -*- coding: utf-8 -*-
"""static/paper-grain.png — 카드 배경용 이음새 없는 종이 요철 타일(RGBA, 어둡게만).

실행: python tools/make_paper_grain.py   (프로젝트 루트에서. static/paper-grain.png 를 덮어쓴다)
400px 로 그려 CSS 에서 200px 로 보인다(레티나에서도 선명). 타일이 바뀌면 lib/og_card.py 의
배경 값(PAPER_BASE·TEXTURE·VIGNETTE)과 style.css 의 --paper-grad 도 같은 종이로 보이는지 확인할 것.
OG 카드(lib/og_card.py)의 background() 와 같은 원리: 높이맵 → 좌상단 빛 음영.
흰색에 가까운 바탕은 더 밝게 만들 수 없으므로 '그늘'만 검정 알파로 담고,
그만큼 CSS 바탕색을 올려서 평균 밝기를 맞춘다(OG 카드 측정값 250·248·241 근처)."""
import numpy as np
from PIL import Image, ImageFilter
T = 400   # 레티나(2x)에서도 선명하도록 400px 로 그리고 CSS 에서 200px 로 보인다
rng = np.random.default_rng(11)
def octave(scale, blur):
    n = int(T / scale)
    small = rng.normal(0, 1, (n, n)).astype(np.float32)
    small = np.tile(small, (3, 3))                       # 주기적으로 이어붙인 뒤 확대 → 경계가 자연스럽다
    im = Image.fromarray(((small * 28) + 128).clip(0, 255).astype(np.uint8)).resize((T * 3, T * 3), Image.BICUBIC)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    return np.array(im, np.float32)[T:2 * T, T:2 * T] - 128
height = octave(2.8, 0.8) * 1.0 + octave(1.6, 0.4) * 0.35 + octave(8, 2.0) * 0.3
gx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) / 2
gy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) / 2
shade = (-(gx + gy) * 0.29 + height * 0.03) * 1.25
dark = np.clip(3.0 - shade, 0, 255).astype(np.uint8)
tile = np.dstack([np.zeros((T, T, 3), np.uint8), dark[..., None]])
import os
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'paper-grain.png')
Image.fromarray(tile, 'RGBA').save(OUT, 'PNG', optimize=True)
print('wrote', OUT)

if __name__ == '__main__':
    ov = Image.open(OUT)
    b = Image.new('RGB', (T * 2, T * 2), (254, 252, 246))
    for dx in (0, T):
        for dy in (0, T):
            b.paste(ov, (dx, dy), ov)
    a = np.array(b).astype(float)
    L = a.mean(axis=2)
    print('mean', a.mean(axis=(0, 1)).round(1), 'std', L.std().round(2),
          'seam', np.abs(L[:, T - 1] - L[:, T]).mean().round(2), 'interior', np.abs(L[:, 50] - L[:, 51]).mean().round(2))

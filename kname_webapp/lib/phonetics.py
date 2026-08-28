# -*- coding: utf-8 -*-
"""
한국어 음소의 조음 자질(articulatory features) 기반 유사도 모델.

음절 = 초성(onset) + 중성(nucleus) + 종성(coda)
각 성분을 자질 벡터로 표현하고, 자질 거리로 유사도(0~1)를 계산한다.

  · 자음: 조음위치(place) · 조음방법(manner) · 발성유형(phonation)
  · 모음: 혀높이(height) · 전후설(backness) · 원순성(rounding) · 활음(glide)
  · 종성: 자음 자질 + '받침 유무' 자체의 차이

설계 근거
  - 영어 화자는 음절 첫 자음(onset)에 먼저 반응하므로 onset 비중을 가장 높게 둔다.
  - 종성은 있고 없고의 차이가 크지 않으므로 비중을 낮게 둔다.
  - 같은 조음위치·조음방법이면 발성유형(평음/경음/격음)이 달라도 매우 유사하게 본다.
    (한국어의 ㄱ/ㄲ/ㅋ 구분은 영어 화자에게 거의 같은 소리로 들림)
"""

# ---------------------------------------------------------------- 자음 자질
# place: 양순 0.0 / 치조 0.35 / 경구개 0.55 / 연구개 0.80 / 성문 1.0
# manner: plosive · nasal · liquid · fricative · affricate
# phonation: 평음 lax 0.0 / 격음 asp 0.5 / 경음 tense 1.0
CONSONANT = {
    'b':  (0.00, 'plosive',   0.0),
    'pp': (0.00, 'plosive',   1.0),
    'p':  (0.00, 'plosive',   0.5),
    'm':  (0.00, 'nasal',     0.0),
    'd':  (0.35, 'plosive',   0.0),
    'tt': (0.35, 'plosive',   1.0),
    't':  (0.35, 'plosive',   0.5),
    'n':  (0.35, 'nasal',     0.0),
    'r':  (0.35, 'liquid',    0.0),
    's':  (0.35, 'fricative', 0.0),
    'ss': (0.35, 'fricative', 1.0),
    'j':  (0.55, 'affricate', 0.0),
    'jj': (0.55, 'affricate', 1.0),
    'ch': (0.55, 'affricate', 0.5),
    'g':  (0.80, 'plosive',   0.0),
    'kk': (0.80, 'plosive',   1.0),
    'k':  (0.80, 'plosive',   0.5),
    'h':  (1.00, 'fricative', 0.5),
    'ng': (0.80, 'nasal',     0.0),   # 종성 전용
}

# 조음방법 간 유사도
MANNER_SIM = {
    ('plosive', 'affricate'): 0.60,
    ('fricative', 'affricate'): 0.55,
    ('plosive', 'fricative'): 0.35,
    ('nasal', 'liquid'): 0.45,
    ('nasal', 'plosive'): 0.25,
    ('liquid', 'plosive'): 0.20,
    ('nasal', 'fricative'): 0.15,
    ('liquid', 'fricative'): 0.20,
    ('nasal', 'affricate'): 0.15,
    ('liquid', 'affricate'): 0.15,
}

def _manner_sim(a, b):
    if a == b:
        return 1.0
    return MANNER_SIM.get((a, b)) or MANNER_SIM.get((b, a)) or 0.1


# 자음 자질 가중치
W_PLACE, W_MANNER, W_PHON = 0.42, 0.45, 0.13

def consonant_sim(a, b):
    """자음 로마자 두 개의 유사도 0~1. ''(초성 ㅇ)은 '자음 없음'."""
    if a == b:
        return 1.0
    if a == '' or b == '':
        # 모음으로 시작 vs 자음으로 시작 — 확연히 다르지만 완전 무관은 아님
        return 0.12
    fa, fb = CONSONANT.get(a), CONSONANT.get(b)
    if not fa or not fb:
        return 0.0
    place = 1.0 - abs(fa[0] - fb[0])
    manner = _manner_sim(fa[1], fb[1])
    phon = 1.0 - abs(fa[2] - fb[2])
    return W_PLACE * place + W_MANNER * manner + W_PHON * phon


# ---------------------------------------------------------------- 모음 자질
# height: 고 1.0 / 중 0.5 / 저 0.0
# backness: 전설 0.0 / 중설 0.5 / 후설 1.0
# rounding: 평순 0 / 원순 1
# glide: '' / 'y' / 'w'
_BASE = {
    'i':  (1.00, 0.00, 0),
    'e':  (0.50, 0.00, 0),
    'ae': (0.35, 0.05, 0),   # 현대 서울말에서 ㅔ와 거의 합류
    'a':  (0.00, 0.60, 0),
    'eo': (0.40, 1.00, 0),
    'o':  (0.60, 1.00, 1),
    'u':  (1.00, 1.00, 1),
    'eu': (1.00, 0.80, 0),
}
# 실제 표기 → (기저모음, 활음)
VOWEL = {
    'i':  ('i', ''),   'e':  ('e', ''),   'ae': ('ae', ''),  'a':  ('a', ''),
    'eo': ('eo', ''),  'o':  ('o', ''),   'u':  ('u', ''),   'eu': ('eu', ''),
    'ya': ('a', 'y'),  'yae': ('ae', 'y'),'yeo': ('eo', 'y'),'ye': ('e', 'y'),
    'yo': ('o', 'y'),  'yu': ('u', 'y'),
    'wa': ('a', 'w'),  'wae': ('ae', 'w'),'wo': ('eo', 'w'), 'we': ('e', 'w'),
    'oe': ('e', 'w'),  'wi': ('i', 'w'),  # ㅚ→[we], ㅟ→[wi] 로 실현
    'ui': ('i', 'y'),  # ㅢ는 [i]로 실현되는 경우가 많음
}

W_HEIGHT, W_BACK, W_ROUND, W_GLIDE = 0.40, 0.32, 0.16, 0.12

def vowel_sim(a, b):
    """모음 로마자 두 개의 유사도 0~1."""
    if a == b:
        return 1.0
    va, vb = VOWEL.get(a), VOWEL.get(b)
    if not va or not vb:
        return 0.0
    (ba, ga), (bb, gb) = va, vb
    fa, fb = _BASE[ba], _BASE[bb]
    height = 1.0 - abs(fa[0] - fb[0])
    back = 1.0 - abs(fa[1] - fb[1])
    rnd = 1.0 - abs(fa[2] - fb[2])
    glide = 1.0 if ga == gb else (0.45 if (ga and gb) else 0.35)
    return W_HEIGHT * height + W_BACK * back + W_ROUND * rnd + W_GLIDE * glide


# ---------------------------------------------------------------- 종성
def coda_sim(a, b):
    """종성 유사도. 둘 다 없으면 1.0, 한쪽만 있으면 낮게."""
    if a == b:
        return 1.0
    if a == '' or b == '':
        # 받침이 붙고 빠지는 것은 모음이 바뀌는 것보다 작은 변화
        return 0.55
    # 종성은 7종(ㄱㄴㄷㄹㅁㅂㅇ)으로 중화
    return consonant_sim(a, b)

_CODA_NEUTRAL = {
    'k': 'g', 'kk': 'g', 'ks': 'g', 'lk': 'g',
    't': 'd', 's': 'd', 'ss': 'd', 'j': 'd', 'ch': 'd', 'h': 'd',
    'p': 'b', 'ps': 'b', 'lp': 'b', 'lm': 'm', 'lb': 'b', 'ls': 'd',
    'lt': 'd', 'lh': 'l', 'nj': 'n', 'nh': 'n', 'l': 'r', 'ng': 'ng',
    'n': 'n', 'm': 'm',
}

def normalize_coda(c):
    """종성 로마자를 대표음 7종으로 중화."""
    if not c:
        return ''
    return _CODA_NEUTRAL.get(c, c)


# ---------------------------------------------------------------- 음절 유사도
# 영어 화자 기준: 첫 자음 > 모음 > 받침
W_ONSET, W_NUCLEUS, W_CODA = 0.44, 0.40, 0.16

# 자질이 많으면 하나만 달라도 나머지가 점수를 떠받쳐 변별력이 떨어진다.
# 성분별 유사도에 지수를 적용해 대비를 높인다.
CONTRAST = 2.5

def syllable_sim(onset_a, nuc_a, coda_a, onset_b, nuc_b, coda_b):
    """세 성분을 받아 음절 유사도 0~1을 계산."""
    o = consonant_sim(onset_a, onset_b) ** CONTRAST
    n = vowel_sim(nuc_a, nuc_b) ** CONTRAST
    c = coda_sim(normalize_coda(coda_a), normalize_coda(coda_b)) ** CONTRAST
    return W_ONSET * o + W_NUCLEUS * n + W_CODA * c


if __name__ == '__main__':
    from pronounce_guide import decompose, romanize_syllable
    tests = [
        ('시', '인'), ('소', '수'), ('이', '인'), ('데', '대'), ('제', '재'),
        ('매', '민'), ('세', '재'), ('크', '근'), ('토', '호'), ('아', '아'),
        ('리', '린'), ('브', '범'), ('노', '노'), ('마', '무'),
    ]
    for a, b in tests:
        c1, j1, g1 = decompose(a)
        c2, j2, g2 = decompose(b)
        s = syllable_sim(c1, j1, g1, c2, j2, g2)
        print(f'{a}({romanize_syllable(a)}) → {b}({romanize_syllable(b)}) : {s:.3f}'
              f'   [자음 {consonant_sim(c1,c2):.2f} / 모음 {vowel_sim(j1,j2):.2f} / '
              f'종성 {coda_sim(normalize_coda(g1),normalize_coda(g2)):.2f}]')

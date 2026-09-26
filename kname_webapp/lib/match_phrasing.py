# -*- coding: utf-8 -*-
"""매칭 유사도 + 공통 요소에 따라 자연스러운 영어 설명 문구 생성"""
from pronounce_guide import decompose, romanize_syllable
from syllable_match import onset_similar, vowel_similar

def match_phrase(src, tgt, sim):
    """
    반환: (level, phrase)
      level : near / close / related / soft / distant / replaced  (색·라벨 매핑용)
      phrase: 'the X sound {phrase} Y' 형태에 들어갈 동사구

    유사도는 phonetics.py의 조음 자질 모델 값(0~1).
    임계값은 실제 매칭 466건 분포로 보정했다.
        strong  ≥ 0.87   partial ≥ 0.68   soft ≥ 0.52   loose < 0.52
    문구는 '무엇이 공통인지'(자음/모음)에 따라 정직하게 고른다.

    원칙 — 정도 표현은 사실과 어긋나면 안 된다.
      · 같은 글자면 "almost"·"similar"·"close" 같은 말을 붙이지 않는다.
        (소피아의 아→아 를 "maps almost exactly onto" 이라고 써서 사용자가
         지적했다. 사전 이름 전체를 돌려 보니 3,821건 중 766건이 이 경우였다.)
      · 모음이 같은데 "a similar vowel" 이라 하거나(106건), 모음이 비슷할
        뿐인데 "shares a vowel" 이라 하지(11건) 않는다.
    """
    c1, j1, _ = decompose(src)
    c2, j2, _ = decompose(tgt)
    has1, has2 = (c1 != ''), (c2 != '')
    same_onset = (c1 == c2) and has1
    both_vowel_start = (not has1) and (not has2)
    sim_onset = (not same_onset) and has1 and has2 and onset_similar(c1, c2)
    same_vowel = (j1 == j2)
    sim_vowel = (not same_vowel) and vowel_similar(j1, j2)

    # ── identical — 같은 글자. 정도 표현 없이 그대로 말한다.
    if src == tgt:
        return ('near', "stays exactly as")

    # ── strong (≥0.87)
    # 0.94 이상은 받침의 비음이 바뀌는 정도(선→성, 린→림, 엄→언)다. 이때만 "almost".
    if sim >= 0.94:
        return ('near', "maps almost exactly onto")
    if sim >= 0.87:
        if same_onset and same_vowel:
            return ('near', "carries straight into")
        if same_onset:
            return ('close', "keeps its consonant sound in")
        if both_vowel_start:
            return ('close', "flows naturally into")
        return ('close', "slides naturally into")

    # ── partial (≥0.68)
    if sim >= 0.68:
        if same_onset:
            return ('related', "shares its consonant with")
        if same_vowel:
            return ('related', "shares its vowel with")
        if sim_onset:
            return ('related', "keeps a close consonant in")
        return ('related', "stays close in sound to")

    # ── soft (≥0.52)
    if sim >= 0.52:
        if same_onset:
            return ('soft', "lends its consonant to")
        if same_vowel:
            return ('soft', "shares a vowel with")
        if sim_vowel:
            # 모음이 '비슷할 뿐'이면 shares 라고 하지 않는다 (네→태, 샤→하)
            return ('soft', "carries a similar vowel into")
        return ('soft', "loosely inspires")

    # ── loose (<0.52)
    if same_vowel:
        # 모음이 '같은데' similar 라고 하지 않는다 (얼→헌, 이→민)
        return ('distant', "carries its vowel into")
    if sim_vowel:
        return ('distant', "carries a similar vowel into")
    if same_onset:
        return ('distant', "leaves a trace of its consonant in")
    if sim_onset:
        return ('distant', "leaves a trace of a similar consonant in")
    # 자음도 모음도 공통점이 없다 — 소리를 살린 게 아니라 '대체'한 것이다. "loosely shapes"
    # 라고 쓰면 관련이 있는 듯 들려서(스→현), 왜 바꿨는지를 문장에 담는다(2026-09-26).
    return ('replaced', "has no close match in a natural Korean name, so it becomes")


# 색 매핑: near/close = green, related = teal-ish green, loose/distant = amber
# 라벨 4종(strong / partial / soft / loose)에 각각 다른 색.
#   strong  = 진초록 (near, close)
#   partial = 연두   (related)
#   soft    = 황토   (loose)
#   loose   = 적갈   (distant)
LEVEL_STYLE = {
    'near':    {'bg':'#E1F5EE','tx':'#0F6E56','ar':'#1D9E75','label':'strong'},
    'close':   {'bg':'#E1F5EE','tx':'#0F6E56','ar':'#1D9E75','label':'strong'},
    'related': {'bg':'#EAF3DE','tx':'#3B6D11','ar':'#639922','label':'partial'},
    'soft':    {'bg':'#FAEEDA','tx':'#854F0B','ar':'#BA7517','label':'soft'},
    'distant': {'bg':'#FAECE7','tx':'#993C1D','ar':'#D85A30','label':'loose'},
    # 공통 소리가 전혀 없어 다른 음절로 바꾼 경우 — 색은 loose 와 같되 라벨을 따로 둔다
    'replaced': {'bg':'#F1EDE6','tx':'#5C5245','ar':'#8A7E6E','label':'replaced'},
}

if __name__ == '__main__':
    # sim 은 phonetics.syllable_sim 의 실제 값을 쓴다 — 손으로 적은 값은 문구 조건과 어긋난다.
    from syllable_match import syllable_similarity
    tests = [('아','아'),('마','마'),('린','림'),('선','성'),('리','린'),('엘','예'),
             ('소','수'),('세','재'),('데','대'),('이','인'),('네','태'),('샤','하'),
             ('얼','헌'),('이','민'),('버','현'),('매','민'),('브','범'),('제','재')]
    for src, tgt in tests:
        sim = syllable_similarity(src, tgt)
        level, phrase = match_phrase(src, tgt, sim)
        sr, tr = romanize_syllable(src), romanize_syllable(tgt)
        lbl = LEVEL_STYLE[level]['label']
        print(f"  [{level:8s}|{lbl:8s}] the {src}({sr}) sound {phrase} {tgt}({tr})   (sim={sim})")

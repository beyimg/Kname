# -*- coding: utf-8 -*-
"""매칭 유사도 + 공통 요소에 따라 자연스러운 영어 설명 문구 생성"""
from pronounce_guide import decompose, romanize_syllable
from syllable_match import onset_similar, vowel_similar

def match_phrase(src, tgt, sim):
    """
    반환: (level, phrase)
      level : near / close / related / soft / distant  (색·라벨 매핑용)
      phrase: 'the X sound {phrase} Y' 형태에 들어갈 동사구

    유사도는 phonetics.py의 조음 자질 모델 값(0~1).
    임계값은 실제 매칭 466건 분포로 보정했다.
        strong  ≥ 0.87   partial ≥ 0.68   soft ≥ 0.52   loose < 0.52
    문구는 '무엇이 공통인지'(자음/모음)에 따라 정직하게 고른다.
    """
    c1, j1, _ = decompose(src)
    c2, j2, _ = decompose(tgt)
    has1, has2 = (c1 != ''), (c2 != '')
    same_onset = (c1 == c2) and has1
    both_vowel_start = (not has1) and (not has2)
    sim_onset = (not same_onset) and has1 and has2 and onset_similar(c1, c2)
    same_vowel = (j1 == j2)
    sim_vowel = (not same_vowel) and vowel_similar(j1, j2)

    # ── strong (≥0.87)
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
        if same_vowel or sim_vowel:
            return ('soft', "shares a vowel with")
        return ('soft', "loosely inspires")

    # ── loose (<0.52)
    if same_vowel or sim_vowel:
        return ('distant', "carries a similar vowel into")
    if same_onset or sim_onset:
        return ('distant', "leaves a trace of its consonant in")
    return ('distant', "loosely shapes")


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
}

if __name__ == '__main__':
    tests = [('아','아',1.0),('리','린',1.0),('엘','예',0.75),('소','수',0.4),
             ('세','재',0.35),('데','대',0.75),('이','인',0.6),('버','현',0.35),
             ('매','민',0.4),('브','범',0.4),('제','재',0.75)]
    for src,tgt,sim in tests:
        level, phrase = match_phrase(src,tgt,sim)
        sr,tr = romanize_syllable(src), romanize_syllable(tgt)
        lbl = LEVEL_STYLE[level]['label']
        print(f"  [{level:8s}|{lbl:8s}] the {src}({sr}) sound {phrase} {tgt}({tr})   (sim={sim})")

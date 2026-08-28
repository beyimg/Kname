# -*- coding: utf-8 -*-
"""한글 음절 → 영어권 발음 가이드 (rhymes-with / sounds-like 방식)"""
import unicodedata

CHO = ['g','kk','n','d','tt','r','m','b','pp','s','ss','','j','jj','ch','k','t','p','h']
JUNG = ['a','ae','ya','yae','eo','e','yeo','ye','o','wa','wae','oe','yo','u','wo','we','wi','yu','eu','ui','i']
JONG = ['','k','k','ks','n','nj','nh','t','l','lk','lm','lb','ls','lt','lp','lh','m','p','ps','t','t','ng','t','t','k','t','p','t']

# 중성 → 영어 발음 힌트 (영어 화자가 아는 소리로)
VOWEL_HINT = {
    'a':'"ah" (like the a in "father")',
    'ae':'"eh" (like the a in "cat")',
    'ya':'"yah"',
    'yae':'"yeh"',
    'eo':'"uh" (like the u in "cut")',
    'e':'"eh" (like the e in "bed")',
    'yeo':'"yuh"',
    'ye':'"yeh"',
    'o':'"oh" (like the o in "go")',
    'wa':'"wah"',
    'wae':'"weh"',
    'oe':'"weh"',
    'yo':'"yoh"',
    'u':'"oo" (like the oo in "moon")',
    'wo':'"wuh"',
    'we':'"weh"',
    'wi':'"wee"',
    'yu':'"yoo"',
    'eu':'"eu" (a soft "uh," lips relaxed)',
    'ui':'"ui" (like "gooey" said fast)',
    'i':'"ee" (like the ee in "see")',
}

# 초성 → 영어 발음 힌트
ONSET_HINT = {
    'g':'g (as in "go")', 'kk':'a tense "k"', 'n':'n', 'd':'d', 'tt':'a tense "t"',
    'r':'a soft "r/l"', 'm':'m', 'b':'b', 'pp':'a tense "p"', 's':'s', 'ss':'a tense "s"',
    '':'(silent, starts with the vowel)', 'j':'j', 'jj':'a tense "j"', 'ch':'ch',
    'k':'k (with a puff of air)', 't':'t (with a puff of air)', 'p':'p (with a puff of air)', 'h':'h',
}

def decompose(syllable):
    """한글 한 글자 → (초성, 중성, 종성) 로마자"""
    code = ord(syllable)
    if 0xAC00 <= code <= 0xD7A3:
        idx = code - 0xAC00
        return CHO[idx//588], JUNG[(idx%588)//28], JONG[idx%28]
    return None, None, None

def romanize_syllable(syllable, capitalize=True):
    cho, jung, jong = decompose(syllable)
    if cho is None:
        return syllable
    s = cho + jung + jong
    return s.capitalize() if capitalize else s

def romanize(text, capitalize=True):
    out = ''.join(romanize_syllable(ch, capitalize=False) for ch in text)
    return out.capitalize() if capitalize and out else out

def romanize_hyphen(text):
    """음절 경계를 하이픈으로: 소피아 → So-pi-a (읽기 쉬운 발음 표기)"""
    syls = [romanize_syllable(ch, capitalize=False) for ch in text if romanize_syllable(ch, capitalize=False)]
    if not syls:
        return text
    syls[0] = syls[0].capitalize()
    return '-'.join(syls)

def syllable_pronunciation(syllable):
    """한 음절의 영어 발음 설명: '수' → 'Su — say "soo"'"""
    cho, jung, jong = decompose(syllable)
    if cho is None:
        return syllable, ''
    rom = romanize_syllable(syllable)
    # 간단한 sounds-like
    vowel_core = VOWEL_HINT.get(jung, f'"{jung}"')
    # 종성이 있으면 덧붙임
    tail = ''
    if jong:
        tail_map = {'ng':' with a soft "-ng" ending', 'n':' ending in "-n"',
                    'l':' ending in a soft "-l"', 'm':' ending in "-m"',
                    'k':' ending in a light "-k"', 't':' ending in a light "-t"',
                    'p':' ending in a light "-p"'}
        tail = tail_map.get(jong, '')
    return rom, vowel_core + tail

def name_pronunciation(name):
    """이름 전체의 음절별 발음 가이드 리스트"""
    return [(ch, *syllable_pronunciation(ch)) for ch in name]

if __name__ == '__main__':
    for n in ['수아','민준','서연','범진']:
        print(f"\n{n} ({romanize(n)}):")
        for ch, rom, hint in name_pronunciation(n):
            print(f"  {ch} = {rom} — {hint}")

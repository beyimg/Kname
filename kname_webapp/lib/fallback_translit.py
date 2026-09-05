# -*- coding: utf-8 -*-
"""
규칙 기반 라틴문자 → 한글 음차 (최종 안전망).

사전·캐시·LLM이 모두 실패해도 사용자가 변환 실패 화면을 보지 않도록,
어떤 라틴문자 입력에도 반드시 한글 음차를 만들어 낸다.

품질은 LLM 음차보다 낮다. 철자만 보고 옮기므로 묵음·불규칙 발음
(아일랜드어·프랑스어 등)은 정확히 처리하지 못한다. 그러나 이 모듈은
LLM이 실패했을 때만 호출되므로 평상시 결과 품질에는 영향이 없다.

    from fallback_translit import to_hangul
    to_hangul('Shizuku')    # → '시즈쿠'
    to_hangul('Wijaya')     # → '위자야'
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ---------------------------------------------------------------- 한글 조합
_CHO_BASE = 0xAC00
# 초성 인덱스: ㄱ0 ㄲ1 ㄴ2 ㄷ3 ㄸ4 ㄹ5 ㅁ6 ㅂ7 ㅃ8 ㅅ9 ㅆ10 ㅇ11 ㅈ12 ㅉ13 ㅊ14 ㅋ15 ㅌ16 ㅍ17 ㅎ18
G, N, D, R, M, B, S, NG, J, C, K, T, P, H = 0, 2, 3, 5, 6, 7, 9, 11, 12, 14, 15, 16, 17, 18
SS = 10        # ㅆ — 일본어 つ 표기(쓰)에만 쓴다
# 중성 인덱스
A, AE, YA, YAE, EO, E, YEO, YE, O, WA, WAE, OE, YO, U, WO, WE, WI, YU, EU, UI, I = range(21)
# 종성 인덱스 (필요한 것만)
JONG_G, JONG_N, JONG_L, JONG_M, JONG_NG = 1, 4, 8, 16, 21


def _compose(cho: int, jung: int, jong: int = 0) -> str:
    return chr(_CHO_BASE + (cho * 21 + jung) * 28 + jong)


# ---------------------------------------------------------------- 자음 표
# seq: (초성, 종성 or None, 활음('y'|'w'|None), 홀로 설 때의 모음)
#   · 종성이 None 이면 뒤에 모음이 없을 때 '으/우'를 붙여 한 음절로 만든다
#   · 활음은 뒤따르는 모음을 ㅑ/ㅕ, ㅘ/ㅝ 계열로 바꾼다 (sha→샤, qua→콰)
_CONS = {
    'sch': (S, None, 'y', YU),
    'sh':  (S, None, 'y', YU),
    'ch':  (C, None, None, I),
    'ph':  (P, None, None, EU),
    'th':  (S, None, None, EU),
    'gh':  (G, None, None, EU),
    'ck':  (K, JONG_G, None, EU),
    'ng':  (NG, JONG_NG, None, EU),
    'qu':  (K, None, 'w', EU),
    'wh':  (NG, None, 'w', EU),
    'kh':  (K, None, None, EU),
    'zh':  (J, None, None, EU),
    'ts':  (C, None, None, EU),
    'tz':  (C, None, None, EU),
    'rr':  (R, None, None, EU),
    'ss':  (S, None, None, EU),
    'nn':  (N, JONG_N, None, EU),
    'mm':  (M, JONG_M, None, EU),
    'tt':  (T, None, None, EU),
    'pp':  (P, None, None, EU),
    'bb':  (B, None, None, EU),
    'dd':  (D, None, None, EU),
    'gg':  (G, None, None, EU),
    'ff':  (P, None, None, EU),
    'cc':  (K, None, None, EU),
    'b':   (B, None, None, EU),
    'c':   (K, None, None, EU),
    'd':   (D, None, None, EU),
    'f':   (P, None, None, EU),
    'g':   (G, None, None, EU),
    'h':   (H, None, None, EU),
    'j':   (J, None, None, EU),
    'k':   (K, None, None, EU),
    'l':   (R, JONG_L, None, EU),
    'm':   (M, JONG_M, None, EU),
    'n':   (N, JONG_N, None, EU),
    'p':   (P, None, None, EU),
    'q':   (K, None, None, EU),
    'r':   (R, None, None, EU),
    's':   (S, None, None, EU),
    't':   (T, None, None, EU),
    'v':   (B, None, None, EU),
    'w':   (NG, None, 'w', U),
    'y':   (NG, None, 'y', I),
    'z':   (J, None, None, EU),
    'x':   (K, None, None, EU),     # 실제 처리는 아래에서 ㅋ+ㅅ 으로 분해한다
}

# ---------------------------------------------------------------- 모음 표
_VOWELS = {
    'eau': O,
    'ai': AE, 'ay': E, 'ea': I, 'ee': I, 'ei': E, 'eu': U, 'ey': I,
    'ie': I, 'oa': O, 'oe': OE, 'oi': OE, 'oo': U, 'ou': U, 'ow': O,
    'oy': OE, 'au': O, 'aw': O, 'ue': U, 'ui': WI, 'uy': WI,
    'a': A, 'e': E, 'i': I, 'o': O, 'u': U,
}

# 활음 결합 (자음의 y/w 성질이 뒤 모음을 바꾼다)
_GLIDE_Y = {A: YA, E: YE, O: YO, U: YU, EO: YEO, AE: YAE, I: I, EU: I}
_GLIDE_W = {A: WA, EO: WO, E: WE, AE: WAE, I: WI, O: O, U: U, EU: U}

# c/g 는 e·i·y 앞에서 연음화 (Cecil→세실, Giulia→줄리아)
_SOFT_NEXT = ('e', 'i', 'y')

_MAX_SYLLABLES = 8


def _strip_accents(text: str) -> str:
    """é→e, ñ→n, ü→u, ç→c … 발음부호를 벗겨 기본 라틴문자로 만든다."""
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(ch for ch in nfkd if not unicodedata.combining(ch))


def _tokenize(s: str) -> list:
    """철자를 자음/모음 토큰 열로 나눈다."""
    toks = []
    i, n = 0, len(s)
    while i < n:
        matched = False

        # ⓪ 일본어 つ → 쓰 (외래어 표기법이 정한 유일한 된소리 표기)
        if s[i:i + 3] == 'tsu':
            toks.append({'t': 'C', 'cho': SS, 'jong': None,
                         'glide': None, 'fill': EU, 'jong_only': False})
            i += 3
            continue

        # 모음 뒤·자음 앞의 h 는 소리나지 않는다 (Johnson → 존슨)
        if (s[i] == 'h' and toks and toks[-1]['t'] == 'V'
                and s[i + 1:i + 2] not in ('a', 'e', 'i', 'o', 'u')):
            i += 1
            continue

        # 어말 -er 는 '어'로 (Miller → 밀러, Peter → 피터)
        if s[i:] == 'er':
            toks.append({'t': 'V', 'jung': EO})
            break

        # ① 모음 뭉치 (긴 것부터)
        for ln in (3, 2, 1):
            seg = s[i:i + ln]
            if seg not in _VOWELS:
                continue
            # 'ay/oy/aw/ow…' 뒤에 모음이 오면 y·w 는 이중모음이 아니라
            # 활음이다 (Ayase → 아야세, Wijaya → 위자야)
            if (ln >= 2 and seg[-1] in 'yw'
                    and s[i + ln:i + ln + 1] in ('a', 'e', 'i', 'o', 'u')):
                continue
            toks.append({'t': 'V', 'jung': _VOWELS[seg]})
            i += ln
            matched = True
            break
        if matched:
            continue

        # ② 자음 뭉치 (긴 것부터)
        for ln in (3, 2, 1):
            seg = s[i:i + ln]
            if seg not in _CONS:
                continue
            nxt = s[i + ln:i + ln + 1]

            # 'x' → ㅋ + ㅅ (Alex → 알렉스)
            if seg == 'x':
                toks.append({'t': 'C', 'cho': K, 'jong': JONG_G,
                             'glide': None, 'fill': EU, 'jong_only': True})
                toks.append({'t': 'C', 'cho': S, 'jong': None,
                             'glide': None, 'fill': EU, 'jong_only': False})
                i += ln
                matched = True
                break

            # 'll' 및 모음 사이의 'l' → 앞 음절 ㄹ받침 + ㄹ초성 (Miller→밀러)
            if seg in ('ll', 'l') and nxt and nxt in 'aeiou':
                prev_is_vowel = bool(toks) and toks[-1]['t'] == 'V'
                if seg == 'll' or prev_is_vowel:
                    toks.append({'t': 'C', 'cho': R, 'jong': JONG_L,
                                 'glide': None, 'fill': EU, 'jong_only': True})
                    toks.append({'t': 'C', 'cho': R, 'jong': None,
                                 'glide': None, 'fill': EU, 'jong_only': False})
                    i += ln
                    matched = True
                    break

            cho, jong, glide, fill = _CONS[seg]
            step = ln

            # c/g 연음화
            if seg == 'c' and nxt in _SOFT_NEXT:
                cho, jong = S, None
            elif seg == 'g' and nxt in _SOFT_NEXT:
                cho, jong = J, None

            # 자음 + y/w + 모음 → 활음으로 흡수 (Kwame → 콰메, Kyoko → 쿄코)
            if (glide is None and nxt in ('y', 'w')
                    and s[i + ln + 1:i + ln + 2] in ('a', 'e', 'i', 'o', 'u')):
                glide = nxt
                jong = None
                step += 1

            toks.append({'t': 'C', 'cho': cho, 'jong': jong,
                         'glide': glide, 'fill': fill, 'jong_only': False})
            i += step
            matched = True
            break

        if not matched:
            i += 1          # 알 수 없는 글자는 건너뛴다
    return toks


def _drop_silent_tail(s: str) -> str:
    """모음 뒤에 오는 어말 r·h 는 소리나지 않는 경우가 많아 떨어뜨린다.
    단 '-er'는 '어'로 살려야 하므로(Miller → 밀러) 여기서 건드리지 않는다."""
    if len(s) >= 3 and s[-1] == 'r' and s[-2] in 'aiou':
        return s[:-1]
    if len(s) >= 3 and s[-1] == 'h' and s[-2] in 'aeiou':
        return s[:-1]
    return s


def to_hangul(name: str) -> Optional[str]:
    """
    라틴문자 이름을 한글로 음차한다.
    글자가 하나도 없으면(숫자·기호만) None을 돌려준다.
    그 외에는 반드시 한글 문자열을 돌려준다.
    """
    if not name:
        return None
    s = _strip_accents(str(name)).lower()
    s = re.sub(r"[^a-z]", '', s)
    if not s:
        return None
    s = _drop_silent_tail(s)
    if not s:
        return None

    # 어두 ng- 는 '응'으로 시작한다 (Nguyen → 응우옌)
    lead = []
    if s.startswith('ng') and len(s) > 2:
        lead = [[NG, EU, JONG_NG]]
        s = s[2:]

    toks = _tokenize(s)
    if not toks and not lead:
        return None

    out = list(lead)               # [초성, 중성, 종성]
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]

        # 모음만 있는 토큰 → ㅇ + 모음
        if t['t'] == 'V':
            out.append([NG, t['jung'], 0])
            i += 1
            continue

        # 받침 전용 토큰 (ll / x 의 앞부분)
        if t.get('jong_only'):
            if out and out[-1][2] == 0:
                out[-1][2] = t['jong']
            else:
                out.append([t['cho'], t['fill'], 0])
            i += 1
            continue

        # 자음 + 뒤따르는 모음 → 한 음절
        if i + 1 < n and toks[i + 1]['t'] == 'V':
            jung = toks[i + 1]['jung']
            if t['glide'] == 'y':
                jung = _GLIDE_Y.get(jung, jung)
            elif t['glide'] == 'w':
                jung = _GLIDE_W.get(jung, jung)
            out.append([t['cho'], jung, 0])
            i += 2
            continue

        # 뒤에 모음이 없는 자음 → 받침으로 붙이거나 '으'를 넣어 한 음절로
        if t['jong'] is not None and out and out[-1][2] == 0:
            out[-1][2] = t['jong']
        else:
            out.append([t['cho'], t['fill'], 0])
        i += 1

    if not out:
        return None
    out = out[:_MAX_SYLLABLES]
    return ''.join(_compose(c, v, j) for c, v, j in out)


if __name__ == '__main__':
    samples = [
        'Shizuku', 'Ayase', 'Natsuki', 'Wijaya', 'Nguyen', 'Rodriguez',
        'Muhammad', 'Aishwarya', 'Brzezinski', 'Papadopoulos', 'Miller',
        'Smith', 'Johnson', 'Mahone', 'Chukwuemeka', 'Vejjajiva',
        'Schneider', 'Alex', 'Kwame', 'Thanaporn', 'Jose', 'Vinicius',
    ]
    for s in samples:
        print(f'{s:15s} → {to_hangul(s)}')

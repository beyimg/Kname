# -*- coding: utf-8 -*-
"""
결과물 품질 점검 — 시스템 오류가 아니라 '나온 결과가 이상한 것'을 잡는다.

기존 모니터링은 **변환이 실패한 경우**만 보고했다. 그런데 지금 문제가 되는 것은
변환은 성공했는데 카드에 이상한 문구가 실리는 경우다(비문, 한글 혼입, 의미설명
없음). 이건 예외가 발생하지 않으므로 로그에도 Sentry에도 안 남는다.

이 모듈이 그 자리를 채운다. 변환 1건이 끝날 때마다 `audit()`을 돌려
발견된 문제를 코드로 돌려주고, app.py가 그것을 stats.db에 저장하고
심각도에 따라 Sentry로 보낸다.

**검사 규칙은 여기 한 곳에만 둔다.** 배포 전 점검 도구(check_short.py)도
이 규칙을 import 해서 쓴다. 두 곳에 복사해 두면 반드시 어긋난다.

심각도
  error   — 절대 나오면 안 되는 것. 나오면 코드 결함이다
  warning — 품질 저하. 늘어나면 원인을 봐야 한다
  info    — 정상 동작 범위의 열화. DB에만 쌓고 Sentry로 보내지 않는다
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 비문 규칙
# 확실한 것만 잡는다. 오탐이 있으면 경보를 신뢰할 수 없게 되어 도구가 죽는다.
_MASS = (r'jade|gold|silk|water|sunlight|moonlight|daylight|snow|rain|rice|'
         r'grass|ice|silver|tea|bamboo|poetry|history|music|art|wisdom|'
         r'grace|peace')
_ABSTRACT = (r'wisdom|grace|peace|history|poetry|kindness|talent|praise|'
             r'eloquence')

BAD_PATTERNS = [
    # 불가산명사 '단독'에 관사가 붙은 경우.
    # ('a jade chime' · 'a jade pendant' 는 가산명사구이므로 정상)
    (re.compile(r'\b(?:as|with|of) (?:a|an) (?:' + _MASS + r')'
                r'(?=$|[ ,.]and\b|[,.])'), '불가산명사에 관사'),
    (re.compile(r'\bwith (?:a|an) (?:' + _ABSTRACT + r')\b'), '추상명사에 관사'),
    (re.compile(r'\b(?:a|an) (?:a|an|the)\b'), '관사 중복'),
    (re.compile(r'\ba [aeiou]'), "'a' + 모음"),
    # 'an honored guest' · 'an hour' 는 h 가 묵음이라 정상
    (re.compile(r'\ban (?!hon|hour|heir)[^aeiou]'), "'an' + 자음"),
    (re.compile(r'(?:,|\band\b|\bas\b|\bof\b|\bwith\b|\bthe\b|\bwhom\b)\s*$'),
     '문구가 끊김'),
    (re.compile(r'[가-힣]'), '한글 혼입'),
    (re.compile(r'\s{2,}'), '공백 중복'),
    # 규칙 활용이 만들어내는 없는 낱말 (to be → bes)
    (re.compile(r'\b(?:bes|haves|dos|goeses)\b'), '없는 낱말'),
]

SEVERITY = {
    'short/ungrammatical': 'error',
    'short/empty':         'error',
    'text/hangul-leak':    'error',
    'meaning/opening':     'warning',
    'meaning/stale':       'warning',
    'meaning/none':        'warning',
    'meaning/template':    'warning',
    'gloss/unclassified':  'warning',
    'short/too-long':      'warning',
    'short/label':         'info',
    'short/fallback':      'info',
}

# 카드 앞면 한 줄이 이보다 길면 레이아웃이 깨진다
SHORT_MAX = 62


def check_grammar(text):
    """비문이면 이유(한국어)를, 아니면 None."""
    t = str(text or '')
    if not t.strip():
        return '빈 값'
    for rx, why in BAD_PATTERNS:
        if rx.search(t):
            return why
    return None


# ---------------------------------------------------------------- 자리 검사
# 표면 패턴으로는 "A name of beneficial and talent" 를 잡을 수 없다.
# 문장 형태는 정상이고 **명사 자리에 형용사가 들어간 것**이 문제이기 때문이다.
# 그래서 품사표를 써서 각 자리에 들어간 말의 품사를 직접 확인한다.
#
# 지금 조립 로직은 품사를 아는 뜻만 쓰므로 이 오류가 나올 수 없지만,
# 나중에 로직이 바뀌거나 사전 문구가 이 형태를 쓰면 조용히 되살아난다.
# 그때 알아채기 위한 검사다.
_SLOT_NOUN = [
    re.compile(r'^A name of (.+?)(?: and (.+?))?$'),
    re.compile(r'^Someone \S+(?: and \S+)* as (?:a |an |the )?(.+?)$'),
    re.compile(r'^Someone .+?, with (?:a |an |the )?(.+?)$'),
    re.compile(r'^A name that .+?, holding (?:a |an |the )?(.+?)$'),
    re.compile(r'^Someone whom (?:a |an |the )?(.+?) \S+$'),
]


def check_slots(text, pos_of):
    """명사 자리에 형용사가 들어갔는지. 문제가 있으면 이유를, 없으면 None."""
    t = str(text or '').strip().rstrip('.')
    for rx in _SLOT_NOUN:
        m = rx.match(t)
        if not m:
            continue
        for g in m.groups():
            if not g:
                continue
            word = re.sub(r'^(?:a|an|the)\s+', '', g.strip().lower())
            if pos_of(word) == 'A':
                return f'명사 자리에 형용사 ({word})'
        return None
    return None


# 따옴표·괄호 안의 내용을 뽑는다.
#
# 설명문에 한글이 있는 것 자체는 정상이다. 사전 설명은 원래
#   예린 (叡璘, Yerin) joins 예 (叡, ye), "bright and wise", ...
#   ... an ending as dependable as in 승우 and 승현.
# 처럼 한글 이름과 음절을 그대로 쓴다. 그래서 '한글이 있다'로는 판정할 수 없다.
#
# 결함은 **한국어 뜻이 영어 대역 없이 실린 경우**다. meaning.py 의 폴백 원문이
# 그대로 나가면 이렇게 된다.
#   광(光, '빛나다'), 민(敏, '민첩하다')
# 따옴표나 괄호 안이 한글뿐이고 알파벳이 하나도 없을 때만 잡는다.
_Q = '"\u201c\u201d\u2018\u2019\''
_QUOTED = re.compile(
    '[' + re.escape(_Q) + ']([^' + re.escape(_Q) + '()]{1,40})['
    + re.escape(_Q) + r']|\(([^()]{1,40})\)')


def _groups(m):
    return [g for g in m.groups() if g]


def audit(data, pos_of=None):
    """
    변환 결과 1건을 점검. 발견 목록을 [(code, detail), ...] 로 돌려준다.

    data    : convert_name() 이 돌려준 dict
    pos_of  : 뜻 → 품사 판정 함수 (app._pos_of). 주면 미분류 뜻까지 잡는다.
    """
    if not isinstance(data, dict) or 'error' in data:
        return []                       # 실패 건은 기존 경로가 이미 보고한다

    out = []
    given = str(data.get('given') or '')
    short = str(data.get('meaning_short') or '')
    meaning = str(data.get('meaning_en') or '')
    source = str(data.get('meaning_source') or '')
    lines = data.get('hanja_lines') or []

    # ---------------------------------------------------------- 한 줄 의미
    if not short.strip():
        out.append(('short/empty', given))
    else:
        why = check_grammar(short)
        if not why and pos_of is not None:
            why = check_slots(short, pos_of)
        if why:
            out.append(('short/ungrammatical', f'{given}: {short} ({why})'))
        elif len(short) > SHORT_MAX:
            out.append(('short/too-long', f'{given}: {len(short)}자'))
        elif ' · ' in short:
            # 품사를 모르는 뜻이 있어 문장을 못 만들고 나열형으로 내려갔다
            out.append(('short/label', f'{given}: {short}'))
        elif short == 'A native Korean name' and not data.get('is_native'):
            # 순우리말이 아닌데 최후 폴백까지 갔다 — 뜻을 하나도 못 쓴 것
            out.append(('short/fallback', given))

    # ---------------------------------------------------------- 도입부 형식
    # 설명은 반드시 한글 이름으로 시작해야 한다.
    #   보아 (寶雅, Boa) carries ...   (O)
    #   Boa carries ...               (X — 로마자만)
    # 사전 602개는 전부 이 형식이고, LLM 출력은 meaning_en._fix_opening 이
    # 강제한다. 그래도 어긋난 것이 나오면 고칠 수 없는 형태였다는 뜻이다.
    if given and meaning.strip() and not meaning.lstrip().startswith(given):
        out.append(('meaning/opening', f'{given}: {meaning[:40]}'))

    # ---------------------------------------------------------- 의미 설명
    if source in ('none', '') or not meaning.strip():
        out.append(('meaning/none', f'{given} (source={source or "?"})'))
    elif data.get('meaning_stale'):
        # 프롬프트나 한자 뜻이 바뀌었는데 재생성에 실패해 예전 설명이 나갔다.
        # 새로 만든 것과 구분되지 않으면 틀린 뜻이 조용히 계속 나간다.
        out.append(('meaning/stale',
                    f'{given} (why={data.get("meaning_stale_why") or "?"})'))
    elif source == 'template':
        # LLM이 실패해 로컬 템플릿으로 만든 설명. 늘어나면 API 쪽을 봐야 한다
        out.append(('meaning/template',
                    f'{given} (err={data.get("meaning_error") or "?"})'))

    # ---------------------------------------------------------- 한글 혼입
    # 설명문의 한글 자체는 정상이다 — 사전 설명은 원래
    #   예린 (叡璘, Yerin) joins 예 (叡, ye), "bright and wise", ...
    # 처럼 한글 이름과 음절을 그대로 쓴다. 그래서 '한글이 있다'로는 판정할 수
    # 없다. 결함은 **영어 대역 없이 한국어 뜻만 실린 경우**다.
    #   광(光, '빛나다')   ← meaning.py 폴백 원문이 그대로 나간 것
    # 한국어 뒤 25자 안에 알파벳이 하나도 없으면 대역이 빠진 것으로 본다.
    for field in ('meaning_en', 'surname_desc'):
        txt = str(data.get(field) or '')
        hit = None
        for m in _QUOTED.finditer(txt):
            # 뒤이어 영어 대역이 오면 정상이다.
            #   "우리 아이" (our child)  ·  (한 + 새), the image of ...
            if re.search(r'[A-Za-z]', txt[m.end():m.end() + 30]):
                continue
            for g in _groups(m):
                if re.search(r'[가-힣]', g) and not re.search(r'[A-Za-z]', g):
                    hit = g
                    break
            if hit:
                break
        if hit:
            out.append(('text/hangul-leak', f'{given}.{field}: {hit[:24]}'))
            break

    # ---------------------------------------------------------- 미분류 뜻
    # 한자사전에 새 글자를 넣었을 때 조용히 지나가는 것을 여기서 잡는다.
    if pos_of is not None:
        for h in lines:
            for w in str(h.get('gloss') or '').split(','):
                w = w.strip()
                if not w or re.search(r'[가-힣]', w):
                    continue
                if pos_of(w) is None:
                    out.append(('gloss/unclassified',
                                f'{h.get("hanja") or "?"}: {w}'))

    return out

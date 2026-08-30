# -*- coding: utf-8 -*-
"""
K-Name Generator — 웹앱 본체

라우트:
  GET  /                  입력 폼
  POST /result            변환 결과 페이지 (카드 + 변환 이유)
  POST /api/convert       JSON API (프론트에서 비동기로 쓸 경우)

실행:
  pip install flask pandas openpyxl
  python app.py
  → http://localhost:5000
"""
import os
import io
import re
import sys
import json
import contextlib

from flask import Flask, render_template, request, jsonify, redirect, url_for

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, 'lib'))

import engine as eng
from conversion_reason import build_reason, build_surname
from transliterate import Transliterator
from meaning import NameMeaning
from meaning_en import MeaningEnGenerator
from tts_full import FullNameTTS
from pronounce_guide import romanize_hyphen, romanize_syllable

app = Flask(__name__)

# ---------------------------------------------------------------- 데이터 로드
DATA = os.path.join(BASE, 'data')
with open(os.path.join(DATA, 'dict_name_to_translit.json'), encoding='utf-8') as f:
    NAME_TO_TRANSLIT = json.load(f)
with open(os.path.join(DATA, 'dict_translit_to_result_full.json'), encoding='utf-8') as f:
    TRANSLIT_TO_RESULT = json.load(f)

ENGINE = eng.KoreanNameEngine(os.path.join(DATA, 'merged_meaningful.xlsx'), None)

# 캐시 저장 위치.
# 배포 환경(Render 무료 플랜 등)은 앱 폴더가 재시작 때 초기화되어
# 같은 이름을 계속 다시 호출하게 된다. 영속 디스크를 CACHE_DIR 로 지정하면
# 캐시가 유지되어 API 비용이 수렴한다.
CACHE_DIR = os.environ.get('CACHE_DIR') or BASE
try:
    os.makedirs(CACHE_DIR, exist_ok=True)
except Exception:
    CACHE_DIR = BASE

# 사전에 없는 이름/성씨는 LLM으로 실시간 음차 (ANTHROPIC_API_KEY 필요)
TRANSLIT = Transliterator(
    name_dict_path=os.path.join(DATA, 'dict_name_to_translit.json'),
    api_key=os.environ.get('ANTHROPIC_API_KEY'),
    cache_path=os.path.join(CACHE_DIR, 'translit_cache.json'),
)

# 한국 성씨(한글) → 부가정보(한자·순위·유래설명) 역인덱스.
# 엔진이 사전 밖 음차에서 매칭해낸 성씨도 이 표로 설명을 붙일 수 있다.
SURNAME_INFO = {}
for _tr, _d in TRANSLIT_TO_RESULT['surname'].items():
    SURNAME_INFO.setdefault(_d['surname'], _d)

# 엔진은 한국 성씨 133개를 매칭할 수 있는데 위 결과 사전에는 29개뿐이다.
# 나머지 104개의 한자·순위·유래설명을 보충한다. (surname_hanja.xlsx 인구수 기준)
try:
    import pandas as _pd
    sys.path.insert(0, DATA)
    from surname_info_extra import SURNAME_INFO_EN as _EXTRA_EN
    _sdf = _pd.read_excel(os.path.join(DATA, 'surname_hanja.xlsx'))
    _sdf = _sdf.sort_values('명수', ascending=False).reset_index(drop=True)
    _hanja = dict(zip(_sdf['성씨'], _sdf['한자']))
    _rank = {n: i + 1 for i, n in enumerate(_sdf['성씨'])}
    for _name, _info in _EXTRA_EN.items():
        if _name in SURNAME_INFO:
            continue
        # 로마자는 설명문 첫 단어에서 추출  예) "Kang (姜) is ..." → Kang
        _rom = _info.split(' ', 1)[0]
        SURNAME_INFO[_name] = {
            'surname': _name,
            'romanized': _rom,
            'hanja': _hanja.get(_name, ''),
            'rank': _rank.get(_name, 0),
            'info_en': _info,
        }
except Exception:
    pass

# 602개 밖 이름의 한자·의미설명을 실시간 생성 (ANTHROPIC_API_KEY 필요)
try:
    MEANING = NameMeaning(
        name_hanja_path=os.path.join(DATA, 'hanja_dict.xlsx'),
        surname_hanja_path=os.path.join(DATA, 'surname_hanja.xlsx'),
        db_path=os.path.join(DATA, 'merged_meaningful.xlsx'),
        api_key=os.environ.get('ANTHROPIC_API_KEY'),
        cache_path=os.path.join(CACHE_DIR, 'meaning_cache.json'),
    )
except Exception:
    MEANING = None

# 풀네임 음성 — 요청 시 생성하고 static/audio/full/ 에 캐시한다.
# 성씨+이름을 통째로 합성하므로 목소리가 하나로 통일된다.
# 음성 파일도 캐시다. 영속 디스크가 있으면 그쪽에 두는 편이 낫지만,
# 정적 서빙 경로여야 하므로 기본은 static 아래에 둔다.
TTS_FULL = FullNameTTS(os.path.join(BASE, 'static', 'audio', 'full'))


# 영어 전용 의미 생성기 — meaning.py 대비 토큰을 크게 줄인다
# (한국어 출력을 만들지 않고, 프롬프트도 짧게 유지)
MEANING_EN = None
if MEANING is not None:
    try:
        MEANING_EN = MeaningEnGenerator(
            MEANING,
            api_key=os.environ.get('ANTHROPIC_API_KEY'),
            cache_path=os.path.join(CACHE_DIR, 'meaning_en_cache.json'),
        )
    except Exception:
        MEANING_EN = None

# (성별, 한국이름) → 부가정보. 사전 밖 음차에서 나온 이름의 한자·의미설명을 찾는 데 사용.
GIVEN_INFO = {}
for _sex in ('male', 'female'):
    for _tr, _d in TRANSLIT_TO_RESULT['given'][_sex].items():
        GIVEN_INFO.setdefault((_sex, _d['given']), _d)


def _convert_quiet(first_kr, last_kr, sex):
    """엔진이 stdout에 로그를 뿌리므로 조용히 호출"""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return ENGINE.convert(first_kr, last_kr, sex)


# 남녀 모두에게 쓰이는 이름 (대부분 순우리말) — '그 외' 선택 시 우선 사용
UNISEX_NAMES = (
    {d['given'] for d in TRANSLIT_TO_RESULT['given']['male'].values()} &
    {d['given'] for d in TRANSLIT_TO_RESULT['given']['female'].values()}
)

_Q_RANK = {'Q1': 0, 'Q2': 1, 'Q3': 2, 'Q4': 3}


_FALLBACK_SIG = ('is a Sino-Korean name', 'is a native Korean name',
                 'is a Korean name', 'the characters mean', 'Meaning:')

# 순우리말 이름 판정.
# 한자 유무만으로는 부족하다. 가람·노을·마음처럼 발음에 맞춰 한자가
# 붙어 있어도 실제로는 순우리말인 이름이 있고(602개 중 21개),
# 그 경우 한자 뜻은 이름의 뜻과 무관하다.
_NATIVE_SIG = re.compile(
    r'native Korean (?:word|name)|purely native|native-Korean', re.I)


try:
    sys.path.insert(0, DATA)
    from native_names import HANJA_OK as _NATIVE_HANJA_OK, HANJA_HIDE as _NATIVE_HANJA_HIDE
except Exception:
    _NATIVE_HANJA_OK, _NATIVE_HANJA_HIDE = {}, {}


def _is_native_name(hanja, meaning_en):
    """순우리말 이름인지. 한자가 붙어 있어도 순우리말일 수 있다."""
    if not hanja:
        return True
    return bool(_NATIVE_SIG.search(meaning_en or ''))


def _hide_hanja(given, hanja):
    """
    순우리말 이름의 한자를 감출지 판단.
    뜻이 어울리면 함께 보여주고(가람 嘉覽), 어긋나면 감춘다(마음 瑪陰).
    분류표에 없는 이름은 감추는 쪽으로 둔다(잘못된 뜻을 보이는 편이 더 나쁨).
    """
    if not hanja:
        return True
    if given in _NATIVE_HANJA_OK:
        return False
    return True


def _is_fallback_meaning(en):
    """
    LLM 생성에 실패해 쓸 수 없는 문구인지 판별.

    카드 뒷면은 영어권 사용자가 읽는 곳이므로, 한국어가 섞여 있으면
    형태와 상관없이 쓰지 않는다. (602개 미리 작성분은 한국어와 한자를
    인용 형태로 담고 있으나, 그쪽은 이 경로를 타지 않는다.)
    """
    if not en:
        return True
    if len(en.strip()) < 60:        # 폴백 템플릿은 대체로 짧다
        return True
    if _has_hangul(en) and any(sig in en for sig in _FALLBACK_SIG):
        return True
    # "'바름' is a Korean name. Meaning: 바른/올바른(right/correct)."
    if re.search(r'is a [A-Za-z\- ]*name\.', en) and _has_hangul(en):
        return True
    return False


def _a(noun):
    """명사 앞 관사. 물질·추상명사와 이미 한정된 표현에는 붙이지 않는다."""
    n = noun.strip().lower()
    if not n:
        return n
    first = n.split()[0]
    # 'the end' 처럼 이미 관사가 있거나, 물질·추상명사면 그대로
    if first in ('the', 'a', 'an') or first in _NO_ARTICLE or n in _ABSTRACT:
        return n
    return ('an ' if n[0] in 'aeiou' else 'a ') + n


def _popular_with_syllable(syllable, sex, exclude, limit=3):
    """같은 글자를 쓰는 인기 이름 (설명에 곁들일 예시)."""
    if MEANING is None:
        return []
    try:
        rows = MEANING.similar_names_for_char(
            syllable, sex, exclude=exclude, max_count=limit) or []
        return [r[0] if isinstance(r, (list, tuple)) else r for r in rows]
    except Exception:
        return []


def _sound_note(given):
    """이름의 소리 특징을 한 구절로."""
    from pronounce_guide import decompose
    codas = []
    for ch in given:
        c = decompose(ch)
        codas.append(bool(c[2]) if c and len(c) > 2 else False)
    if not any(codas):
        return 'Soft and open, with no final consonants'
    if all(codas):
        return 'Firm and grounded, with a consonant closing each syllable'
    return 'Balanced in sound, one syllable open and one closed'


def _compose_meaning_en(given, hanja_detail, native_meaning=None,
                        sex='여', romanized=''):
    """
    LLM 생성이 실패했을 때 쓸 영어 설명.
    미리 작성된 602개 설명과 같은 형식을 따른다.
      ① 한자 분해  ② 합쳐진 이미지  ③ 소리 특징과 같은 계열 이름  ④ 마무리
    한국어·한자를 인용 형태로만 쓰고, 영어 문장으로 읽히게 한다.
    """
    if native_meaning:
        pop = _popular_with_syllable(given[0], sex, given, 2)
        pop_line = ''
        if pop:
            pop_line = (' Names built from Korean words like this one \u2014 '
                        + ', '.join(pop) + ' \u2014 have a following of their own.')
        return (
            f'{given} ({romanized or given}) is a native Korean name meaning '
            f'\u201c{native_meaning}\u201d. Unlike Sino-Korean names, it is built from a '
            f'Korean word rather than Chinese characters, so its meaning reaches anyone '
            f'who hears it \u2014 no characters to look up.{pop_line} '
            f'{_sound_note(given)}, it carries a warm, unhurried feel.'
        )

    parts = [d for d in (hanja_detail or [])
             if len(d) >= 3 and d[2] and not _has_hangul(d[2])]
    if not parts:
        return ''

    # ① 한자 분해
    hanja = ''.join(d[1] for d in parts)
    pieces = []
    for syl, hj, gloss in parts:
        rom = romanize_syllable(syl).lower()
        pieces.append(f'{syl} ({hj}, {rom}), \u201c{gloss}\u201d')
    if len(pieces) >= 2:
        opening = (f'{given} ({hanja}, {romanized or given}) joins '
                   + ', with '.join(pieces[:2]) + '.')
    else:
        opening = f'{given} ({hanja}, {romanized or given}) is built on {pieces[0]}.'

    # ② 합쳐진 이미지 — 품사에 맞게 문장을 만든다
    #    (형용사는 그대로, 명사는 비유로, 동사는 관계절로)
    def _slot(g):
        w = g.split(',')[0].strip()
        low = w.lower()
        if low.startswith('to '):
            return ('verb', low[3:])
        if low in _ADJ_OK:
            return ('adj', low)
        # 목록에 없어도 형용사 어미면 형용사로 본다
        if low.endswith(('ed', 'ful', 'ous', 'ive', 'able', 'ible',
                         'less', 'ent', 'ant', 'ary', 'al')):
            return ('adj', low)
        return ('noun', low)

    slots = [_slot(d[2]) for d in parts]
    if len(slots) >= 2:
        (k1, w1), (k2, w2) = slots[0], slots[1]
        if k1 == 'adj' and k2 == 'adj':
            image = f' Together they picture someone {w1} and {w2}.'
        elif k1 == 'adj' and k2 == 'noun':
            image = f' Together they picture someone {w1}, with the grace of {_a(w2)}.'
        elif k1 == 'noun' and k2 == 'adj':
            image = f' Together they picture someone {w2}, with the grace of {_a(w1)}.'
        elif k1 == 'adj' and k2 == 'verb':
            image = f' Together they picture someone {w1} who {_verb_phrase(w2)}.'
        elif k1 == 'verb' and k2 == 'adj':
            image = f' Together they picture someone {w2} who {_verb_phrase(w1)}.'
        elif k1 == 'noun' and k2 == 'noun':
            image = f' Together they bring {_a(w1)} and {_a(w2)} into one name.'
        elif k1 == 'noun' and k2 == 'verb':
            image = f' Together they picture one who {_verb_phrase(w2)}, holding {_a(w1)}.'
        elif k1 == 'verb' and k2 == 'noun':
            image = f' Together they picture one who {_verb_phrase(w1)}, holding {_a(w2)}.'
        elif k1 == 'verb' and k2 == 'verb':
            image = (f' Together they picture one who {_verb_phrase(w1)} '
                     f'and {_verb_phrase(w2)}.')
        else:
            image = f' Together they speak of {w1} and {w2}.'
    else:
        k, w = slots[0]
        if k == 'adj':
            image = f' It pictures someone {w}.'
        elif k == 'verb':
            image = f' It speaks of one who {_verb_phrase(w)}.'
        else:
            image = f' It carries the image of {_a(w)}.'

    # ③ 소리 + 같은 글자를 쓰는 이름
    tail_syl = given[-1]
    pop = _popular_with_syllable(tail_syl, sex, given, 3)
    if pop:
        listed = ', '.join(pop)
        sound = (f' {_sound_note(given)}, its {tail_syl} ending is shared by '
                 f'names like {listed}.')
    else:
        sound = f' {_sound_note(given)}.'

    # ④ 마무리
    close = ' The whole name reads calm and considered.'
    return opening + image + sound + close


def _translit_error(name):
    """음차 실패 안내. 키가 없어서인지 LLM이 실패한 것인지 구분한다."""
    if not TRANSLIT.llm_available:
        return (f'"{name}" is not in our dictionary yet. '
                f'The site can only handle listed names right now — '
                f'try a more common spelling.')
    return (f'Sorry, we couldn\'t work out how "{name}" sounds in Korean. '
            f'Try another spelling.')


def _generate_meaning(given, sex, english_first, translit, neutral=False):
    """
    602개 사전에 없는 이름의 한자·의미설명을 meaning.py로 생성.
    한자 매칭은 API 키 없이도 되지만, 풍부한 설명 문구는 LLM이 필요하다.
    반환: {'hanja', 'hanja_detail', 'meaning_en'} 또는 None
    """
    if MEANING is None:
        return None
    try:
        ntype = MEANING.classify_name(given)
        chars = MEANING.pick_best_hanja(given) if ntype == 'hanja' else None
        out = {'hanja': '', 'hanja_detail': [], 'meaning_en': ''}

        if chars:
            # chars: [(음, 한자, 뜻), ...]
            detail = []
            for c in chars:
                en = HANJA_EN.get(c[1]) or _gloss_to_en(c[2])
                if en:
                    detail.append([c[0], c[1], en])
            # 번역 못 한 글자는 그 줄만 빼고, 나머지는 그대로 보여준다.
            # (한국어가 섞이는 것은 막되, 한자 전체를 잃지는 않도록)
            if detail:
                out['hanja'] = ''.join(c[1] for c in chars)
                out['hanja_detail'] = detail

        # ① 영어 전용 생성기 (저비용)
        en = ''
        if MEANING_EN is not None:
            en = MEANING_EN.explain_en(
                # Either 를 고른 경우 성별을 단정하지 않도록 중립값을 넘긴다
                given=given, sex=('기타' if neutral else sex), hanja_chars=chars,
                english_name=english_first, gloss_en=_KR_GLOSS_TO_EN,
            ) or ''

        # ② 실패 시 meaning.py로 폴백 (한국어+영어 생성, 비용 높음)
        if not en:
            res = MEANING.explain(
                given=given, sex=sex,
                hanja_chars=chars, name_type=ntype,
                english_name=english_first, first_kr=translit,
            )
            if isinstance(res, dict):
                en = res.get('meaning_en', '') or ''

        if True:
            # meaning.py는 LLM 실패 시 한국어 뜻이 섞인 폴백 문구를 돌려준다.
            #   예: "'광민' is a Sino-Korean name; the characters mean: 광(光, '빛나다')..."
            # 영어권 사용자용 카드에는 그대로 내보낼 수 없으므로,
            # 번역표로 직접 영어 문장을 만든다.
            if _is_fallback_meaning(en):
                # 폴백 원문에서 순우리말 뜻을 건져 설명에 활용한다
                nm = None
                m2 = re.search(r'Meaning:[^(]*\(([A-Za-z][^)]*)\)', en or '')
                if m2:
                    nm = m2.group(1).strip()
                # LLM으로 설명을 만들지 못했다. 모든 이름에 똑같이 적용되는
                # 저품질 문구를 내보내는 대신, 상태를 표시해 재시도를 유도한다.
                # 다만 그 안에 담긴 뜻은 카드 앞면 한 줄에 쓸 수 있으므로 남긴다.
                out['meaning_raw'] = en
                en = _compose_meaning_en(given, out.get('hanja_detail'), nm,
                                         sex=sex, romanized=romanize_hyphen(given))
                out['meaning_unavailable'] = not bool(en)
                out['meaning_error'] = (
                    getattr(MEANING_EN, 'last_error', None) or 'other'
                )
            out['meaning_en'] = en
        return out
    except Exception:
        return None


# 한자 뜻(한국어) → 영어. data/gloss_en.py 참조.
# 번역이 없는 뜻은 카드에 노출하지 않는다(한국어가 그대로 보이는 것을 막기 위함).
try:
    from gloss_en import GLOSS_EN as _KR_GLOSS_TO_EN
except Exception:
    _KR_GLOSS_TO_EN = {}

# 한자별 영어 뜻 — hanja_dict.xlsx '영어뜻' 열에서 직접 로드한다.
# 한국어 뜻을 거치지 않으므로 동음이의(해=year/sun, 말=horse/words) 오역이 없다.
HANJA_EN = {}
try:
    import pandas as _pd_he
    _he_df = _pd_he.read_excel(os.path.join(DATA, 'hanja_dict.xlsx'))
    if '영어뜻' in _he_df.columns:
        for _h, _e in zip(_he_df['한자'], _he_df['영어뜻']):
            if isinstance(_h, str) and isinstance(_e, str) and _e.strip():
                HANJA_EN[_h] = _e.strip()
except Exception:
    HANJA_EN = {}


def _gloss_to_en(kr_meaning):
    """
    '빛나다,밝다' → 'shining, bright'
    번역 가능한 항목만 남기고, 하나도 없으면 None을 반환한다.
    """
    out = []
    for w in str(kr_meaning).split(','):
        w = w.strip()
        if not w:
            continue
        en = _KR_GLOSS_TO_EN.get(w)
        if en and en not in out:
            out.append(en)
    return ', '.join(out[:2]) if out else None


def _pick_neutral(first_key, last_tr):
    """
    '그 외' 선택 시: 남녀 사전을 모두 조회해
      1) 남녀 공용으로 쓰이는 이름이 있으면 우선
      2) 없으면 품질이 가장 좋은 결과
    반환: (sexk, translit, given, quality, is_unisex) 또는 None
    """
    cands = []
    for sexk, sk in (('male', '남'), ('female', '여')):
        tr = TRANSLIT.transliterate(first_key, sexk)
        if not tr:
            continue
        r = _convert_quiet(tr, last_tr or '스미스', sk)   # last_tr은 호출부에서 보장됨
        for gk, qk in (('first_1', 'given_quality'), ('first_2', 'given_quality_2')):
            g = r.get(gk)
            if g:
                cands.append((sexk, tr, g, r.get(qk) or 'Q2', g in UNISEX_NAMES))
    if not cands:
        return None
    # 공용 여부 → 품질 순으로 정렬
    cands.sort(key=lambda c: (not c[4], _Q_RANK.get(c[3], 9)))
    return cands[0]


# 형용사형으로 쓸 수 있는 gloss만 카드 앞면 한 줄에 사용.
# (명사 'sunlight' / 동사 'to assist' 등은 "A sunlight person"처럼 어색해져서 제외)
# 비유('~처럼')가 성립하지 않는 추상명사
_ABSTRACT = {
    'wisdom', 'grace', 'virtue', 'merit', 'fortune', 'happiness', 'joy',
    'love', 'kindness', 'sincerity', 'history', 'law', 'order', 'strength',
    'dignity', 'authority', 'reverence', 'propriety', 'foundation', 'origin',
    'essence', 'achievement', 'responsibility', 'duty', 'eloquence',
}
# 관사를 붙이지 않는 명사 (물질·자연·복수 개념)
_NO_ARTICLE = {
    'jade', 'gold', 'silk', 'water', 'sunlight', 'moonlight', 'firelight',
    'earth', 'land', 'sky', 'spring', 'dawn', 'daylight', 'ink', 'honey',
    'barley', 'cotton', 'coral', 'metal', 'stone',
    # 복수형·집합명사 — 관사를 붙이면 어색하다
    'woods', 'grass', 'plants', 'words', 'scenery', 'rice', 'snow', 'rain',
}

_ADJ_OK = {
    'outstanding','excelling','clear','upright','bright','wise','benevolent','kind',
    'graceful','beautiful','lovely','peaceful','glad','virtuous','good','auspicious',
    'great','foremost','lofty','towering','abundant','broad','wide','warm','clever',
    'shining','radiant','dignified','steadfast','flourishing','distinct','sparkling',
    'strong','mighty','reverent','serene','generous','fragrant','vast','white',
    'refined','elegant','gifted','valiant','fierce','tender','delicate','stern',
    'calm','tranquil','holy','boundless','keen','sharp','brilliant','fine','deep',
    'even','noble','cultured','sagely',
}

_DANGLING = (',', ' and', ' or', ' as', ' of', ' with', ' to', ' for', ' in', ' the', ' a')
_HANGUL = re.compile(r'[가-힣]')


def _has_hangul(s):
    return bool(_HANGUL.search(str(s or '')))

def _ok_phrase(s):
    """어색하게 끊겼거나 한국어가 섞였는지 검사"""
    if not s:
        return False
    if _has_hangul(s):          # 카드 앞면에 한국어가 나가면 안 된다
        return False
    return not s.rstrip().lower().endswith(_DANGLING)


# 순우리말 뜻을 카드 문구로 다듬는다.
# 'we, us' 처럼 대명사만 남으면 이름 뜻으로 읽히지 않으므로 문장으로 감싼다.
# 뜻이 대명사만 남으면 이름으로 읽히지 않는다.
# 'this spring' 처럼 뒤에 명사가 붙는 경우는 정상이므로 제외한다.
_NATIVE_UNFIT = {'we', 'us', 'we, us'}

def _phrase_native(s):
    """순우리말 이름의 뜻 → '(뜻) in native Korean' 형식."""
    t = s.strip().rstrip('.').strip()
    low = t.lower()
    if low in _NATIVE_UNFIT:
        # 대명사만 남으면 이름 뜻으로 읽히지 않는다
        return 'Togetherness \u2014 \u201cus\u201d in native Korean'
    # 'someone ...' 같은 서술형은 형식을 바꾸지 않는다
    if low.startswith(('someone', 'a ', 'an ')):
        return t[0].upper() + t[1:]
    low = re.sub(r'^(the|a|an)\s+', '', low)
    if not low:
        return ''
    return f'{low[0].upper()}{low[1:]} in native Korean'


# 비유 대상으로 쓸 수 없는 표현 (수량·정도·관계 등)
_NOT_COMPARABLE = {
    'many', 'all', 'each', 'both', 'three', 'six', 'seven', 'eight', 'ten',
    'the most', 'above', 'inside', 'behind', 'beside', 'alongside',
    'self', 'we', 'us', 'this', 'that',
}


def _adj_noun(adj, noun):
    """'bright' + 'jade' → 'Someone bright as jade'"""
    a, n = adj.lower(), noun.lower()
    if n in _NOT_COMPARABLE:
        # 'lofty as a many' 같은 비문이 되므로 두 뜻을 나열한다
        return f'A name of {n} and {a}'
    if n in _ABSTRACT:
        return f'Someone {a}, with {n}'
    first = n.split()[0]
    if first in ('a', 'an', 'the'):
        art = ''                      # 뜻에 이미 관사가 있으면 덧붙이지 않는다
    elif first in _NO_ARTICLE:
        art = ''
    else:
        art = 'an ' if n[0] in 'aeiou' else 'a '
    return f'Someone {a} as {art}{n}'


def _verb_phrase(verb):
    """'to assist' → 'assists'"""
    v = verb.lower()
    if v.startswith('to '):
        v = v[3:]
    w = v.split()[0]
    rest = v[len(w):]
    if w.endswith(('s', 'sh', 'ch', 'x', 'z')):
        w += 'es'
    elif w.endswith('y') and len(w) > 1 and w[-2] not in 'aeiou':
        w = w[:-1] + 'ies'
    else:
        w += 's'
    return w + rest


def _short_meaning(meaning_en, hanja_lines):
    """카드 앞면의 한 줄 요약."""
    # 1) 의미설명 안의 대표 문구를 우선 사용
    if meaning_en:
        pats = [
            r"[Ii]t'?s a wish for (someone [^.\"]+?)[.\"]",
            r"[Ii]t pictures (someone [^.\"]+?)[.\"]",
            r"wish (?:for|to be) (someone [^.\"]+?)[.\"]",
            r"[Ii]t carries the (?:poetic )?wish for (someone [^.\"]+?)[.\"]",
            r"name meaning (someone [^.\"]+?)[.\"]",
            r"native Korean name meaning \"([^\"]+)\"",
            r"native Korean word (?:for|meaning) \"([^\"]+)\"",
            r"[Nn]ative Korean word for \"([^\"]+)\"",
            r"word for \"([^\"]+)\"",
            # meaning.py 폴백: "Meaning: 바른/올바른(right/correct)." 처럼
            # 한국어 뒤 괄호 안에 영어 뜻이 오는 형태.
            # "이(this) + 봄(spring)" 처럼 여러 개면 아래에서 따로 합친다.
            r"Meaning:[^(]*\(([A-Za-z][^)]*)\)",
            r"name meaning \"([^\"]+)\"",
            r"comes from the native Korean word \uc774\ub4e0, \"([^\"]+)\"",
            r"native Korean word [^,]*, \"([^\"]+)\"",
            r"wish for (a [^.\"]+?)[.\"]",
            r"[Ii]t pictures (a [^.\"]+?)[.\"]",
        ]
        # "Meaning: 이(this) + 봄(spring)." 처럼 글자별 뜻이 나열된 폴백은
        # 괄호를 모두 모아 하나의 구로 합친다 (this + spring → this spring)
        mm = re.search(r'Meaning:\s*(.+?)\.?$', meaning_en.strip())
        if mm and mm.group(1).count('(') >= 2:
            parts = re.findall(r'\(([A-Za-z][^)]*)\)', mm.group(1))
            if parts:
                joined = ' '.join(x.strip() for x in parts)
                if _ok_phrase(joined):
                    return _phrase_native(joined)

        for p in pats:
            m = re.search(p, meaning_en)
            if not m:
                continue
            s = re.sub(r'\s+', ' ', m.group(1).strip())
            # ", and sounds like ..." 처럼 새 절이 이어지면 잘라냄.
            # 단 "wide, deep, and blue" 같은 단순 나열은 자르지 않는다.
            s = re.split(
                r',\s+(?:and\s+)?(?:sounds?|echo\w*|carr\w+|evok\w+|recall\w+|bring\w*|'
                r'giv\w+|add\w*|hint\w*|mean\w*|suggest\w*|match\w*|work\w*|fit\w*|'
                r'feel\w*|read\w*|for a note|but|which|so)\b', s)[0]
            s = s.strip().rstrip(',;:')
            if s.lower().startswith(('someone','a ','an ')):
                if 10 <= len(s) <= 62 and _ok_phrase(s):
                    return s[0].upper() + s[1:]
            else:
                # 순우리말 뜻(예: joy, sunset glow, light on water)
                s = re.split(r'\s*[(\u2014-]', s)[0].strip()   # 괄호·대시 뒤 부연 제거
                if 2 <= len(s) <= 46 and _ok_phrase(s):
                    return _phrase_native(s)

    # 2) 한자 뜻으로 조립 — 모든 글자의 뜻을 반드시 반영한다.
    #    글자마다 형용사/명사/동사 중 쓸 수 있는 것을 하나씩 뽑는다.
    parts = []
    for h in hanja_lines:
        words = [w.strip() for w in h['gloss'].split(',') if w.strip()]
        words = [w for w in words if not _has_hangul(w)]
        if not words:
            continue
        adj = next((w for w in words if w.lower() in _ADJ_OK), None)
        noun = next((w for w in words
                     if w.lower() not in _ADJ_OK and not w.lower().startswith('to ')), None)
        verb = next((w for w in words if w.lower().startswith('to ')), None)
        parts.append({'adj': adj, 'noun': noun, 'verb': verb})

    if parts:
        # 모든 글자가 형용사 → "A bright and gentle person"
        adjs = [p['adj'] for p in parts if p['adj']]
        if len(adjs) == len(parts) and adjs:
            uniq = list(dict.fromkeys(adjs))
            phrase = ' and '.join(uniq[:2]) if len(uniq) >= 2 else uniq[0]
            art = 'An' if phrase[0].lower() in 'aeiou' else 'A'
            return f'{art} {phrase} person'

        # 형용사 + 명사 → "Someone bright as jade"
        if len(parts) == 2:
            a, b = parts
            if a['adj'] and b['noun']:
                return _adj_noun(a['adj'], b['noun'])
            if b['adj'] and a['noun']:
                return _adj_noun(b['adj'], a['noun'])
            # 형용사 + 동사 → "Someone bright who helps others"
            if a['adj'] and b['verb']:
                return f"Someone {a['adj'].lower()} who {_verb_phrase(b['verb'])}"
            if b['adj'] and a['verb']:
                return f"Someone {b['adj'].lower()} who {_verb_phrase(a['verb'])}"

        # 명사만 → "A name of sunlight and star"
        nouns = [p['noun'] for p in parts if p['noun']]
        if len(nouns) >= 2:
            return f'A name of {nouns[0].lower()} and {nouns[1].lower()}'
        # 동사가 섞인 경우
        verbs = [p['verb'] for p in parts if p['verb']]
        if nouns and verbs:
            return f'A name that {_verb_phrase(verbs[0])}, holding {nouns[0].lower()}'
        if len(verbs) >= 2:
            return f'A name that {_verb_phrase(verbs[0])} and {_verb_phrase(verbs[1])}'
        if nouns:
            return f'A name of {nouns[0].lower()}'
        if adjs:
            art = 'An' if adjs[0][0].lower() in 'aeiou' else 'A'
            return f'{art} {adjs[0]} person'
        if verbs:
            return f'A name that {_verb_phrase(verbs[0])}'

    # 4) 순우리말 이름 등 — 마지막 안전장치
    return 'A native Korean name'


# ---------------------------------------------------------------- 성별 중립화
# Either(성별 무관) 선택 시, 미리 작성된 설명·폴백에 남은 성별 단정 표현을 없앤다.
# 고유명사(그룹명 OH MY GIRL 등)는 건드리지 않도록, 성별 명사(boy/girl 등)는
# 소문자 형태만 치환한다. 대명사는 대소문자를 보존하며 치환한다.
_DEGENDER_PRONOUN = [
    (re.compile(r'\bhe or she\b', re.I), 'they'),
    (re.compile(r'\bhis or her\b', re.I), 'their'),
    (re.compile(r'\bhim or her\b', re.I), 'them'),
    (re.compile(r'\bshe\b', re.I), 'they'),
    (re.compile(r'\bhe\b', re.I), 'they'),
    (re.compile(r'\bhimself\b', re.I), 'themselves'),
    (re.compile(r'\bherself\b', re.I), 'themselves'),
    (re.compile(r'\bhis\b', re.I), 'their'),
    (re.compile(r'\bhers\b', re.I), 'theirs'),
    (re.compile(r'\bhim\b', re.I), 'them'),
    (re.compile(r'\bher\b', re.I), 'their'),   # 소유격이 대부분(목적격은 드묾)
]
# 성별 명사 — 소문자만. (고유명사는 대개 대문자로 시작하므로 보호됨)
_DEGENDER_NOUN = [
    (re.compile(r"\ba (?:boy|girl)'s name\b"), 'a name'),
    (re.compile(r"\b(?:boy|girl)'s name\b"), 'name'),
    (re.compile(r'\bboy or girl\b'), 'child'),
    (re.compile(r'\bgirl or boy\b'), 'child'),
    (re.compile(r'\bboys and girls\b'), 'children'),
    (re.compile(r'\bboys\b'), 'children'),
    (re.compile(r'\bgirls\b'), 'children'),
    (re.compile(r'\bboy\b'), 'child'),
    (re.compile(r'\bgirl\b'), 'child'),
    (re.compile(r'\bson\b'), 'child'),
    (re.compile(r'\bdaughter\b'), 'child'),
    (re.compile(r'\bman\b'), 'person'),
    (re.compile(r'\bwoman\b'), 'person'),
]


def _match_case(repl, original):
    """원문 대소문자에 맞춰 치환어를 조정 (She→They, HE→THEY)."""
    if original.isupper():
        return repl.upper()
    if original[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def _degender(text):
    """성별 단정 표현을 중립 표현으로. 고유명사(대문자 그룹명 등)는 보호."""
    if not text:
        return text
    for rx, repl in _DEGENDER_PRONOUN:
        text = rx.sub(lambda m: _match_case(repl, m.group(0)), text)
    for rx, repl in _DEGENDER_NOUN:
        text = rx.sub(repl, text)
    # 이중 공백 정리
    return re.sub(r'\s{2,}', ' ', text)


# 성별을 명시적으로 고른 경우, 미리 작성된 설명이 반대 성별로 단정하는 것을
# 사용자가 고른 성별에 맞춰 교정한다. (예: 여성인데 "a boy's name"으로 나온 경우)
# 명사(boy/girl 등)는 소문자만 치환해 고유명사(OH MY GIRL 등)를 보호하고,
# 대명사는 대소문자를 보존한다.
_REGENDER = {
    '여': [   # → 여성
        (re.compile(r"\ba boy's name\b"), "a girl's name"),
        (re.compile(r"\bboy's name\b"), "girl's name"),
        (re.compile(r"\bboys\b"), "girls"),
        (re.compile(r"\bboy\b"), "girl"),
        (re.compile(r"\bsons\b"), "daughters"),
        (re.compile(r"\bson\b"), "daughter"),
        (re.compile(r"\bhe\b", re.I), "she"),
        (re.compile(r"\bhis\b", re.I), "her"),
        (re.compile(r"\bhim\b", re.I), "her"),
        (re.compile(r"\bhimself\b", re.I), "herself"),
    ],
    '남': [   # → 남성
        (re.compile(r"\ba girl's name\b"), "a boy's name"),
        (re.compile(r"\bgirl's name\b"), "boy's name"),
        (re.compile(r"\bgirls\b"), "boys"),
        (re.compile(r"\bgirl\b"), "boy"),
        (re.compile(r"\bdaughters\b"), "sons"),
        (re.compile(r"\bdaughter\b"), "son"),
        (re.compile(r"\bshe\b", re.I), "he"),
        (re.compile(r"\bher\b", re.I), "his"),      # 소유격 우선
        (re.compile(r"\bherself\b", re.I), "himself"),
    ],
}


def _regender(text, sex):
    """반대 성별로 단정된 표현을 사용자가 고른 성별에 맞춰 교정."""
    if not text:
        return text
    rules = _REGENDER.get(sex)
    if not rules:
        return text
    for rx, repl in rules:
        if rx.flags & re.IGNORECASE:
            text = rx.sub(lambda m: _match_case(repl, m.group(0)), text)
        else:
            text = rx.sub(repl, text)
    return re.sub(r'\s{2,}', ' ', text)


# ---------------------------------------------------------------- 변환 파이프라인
def convert_name(first_en, last_en, sex):
    """
    영어 이름 → 한국 이름 전체 결과.
    반환 dict 또는 {'error': ...}
    sex: '여' | '남' | 'other'(성별 무관)
    """
    neutral = (sex == 'other')
    sexk = 'female' if sex == '여' else 'male'
    first_key = (first_en or '').strip().lower()
    last_key = (last_en or '').strip().lower()

    if not first_key:
        return {'error': 'Please enter your first name.'}
    if not last_key:
        return {'error': 'Please enter your last name — a Korean name needs a family name '
                         'to be complete, and it comes first (like 이수아, Lee Su-a).'}
    # 입력 길이 제한 — 너무 긴/이상한 값으로 AI 프롬프트를 흔드는 것 방지
    if len(first_key) > 40 or len(last_key) > 40:
        return {'error': 'Please enter a shorter name.'}

    last_tr = TRANSLIT.transliterate(last_key, 'surname')
    if not last_tr:
        return {'error': _translit_error(last_en)}

    if neutral:
        picked = _pick_neutral(first_key, last_tr)
        if not picked:
            return {'error': f'Sorry, "{first_en}" is not in our name dictionary yet. '
                             f'Try another spelling or a more common name.'}
        sexk, first_tr, given, quality, is_unisex = picked
        sex = '남' if sexk == 'male' else '여'
    else:
        # 1) 음차 (사전 → 없으면 LLM)
        first_tr = TRANSLIT.transliterate(first_key, sexk)
        if not first_tr:
            return {'error': _translit_error(first_en)}
        # 2) 변환 (엔진)
        result = _convert_quiet(first_tr, last_tr, sex)
        given = result.get('first_1')
        quality = result.get('given_quality', 'Q1')
        is_unisex = given in UNISEX_NAMES if given else False
        if not given:
            return {'error': 'Conversion failed. Please try a different name.'}

    # 3) 이름 결과 상세 (한자·의미설명)
    #    사전 밖 음차면 변환된 한국이름(given)으로 역조회한다.
    gres = TRANSLIT_TO_RESULT['given'][sexk].get(first_tr)
    if not gres:
        gres = GIVEN_INFO.get((sexk, given)) or GIVEN_INFO.get(('male', given)) \
               or GIVEN_INFO.get(('female', given)) or {}
    hanja = gres.get('hanja') or ''
    hanja_detail = gres.get('hanja_detail') or []
    meaning_en = gres.get('meaning_en', '')
    meaning_unavailable = False
    meaning_error = None
    meaning_raw = ''

    # 602개 밖 이름이면 meaning.py로 한자·의미설명을 실시간 생성
    if (not meaning_en or not hanja) and MEANING is not None:
        gen = _generate_meaning(given, sex, first_en, first_tr, neutral=neutral)
        if gen:
            hanja = hanja or gen.get('hanja', '')
            hanja_detail = hanja_detail or gen.get('hanja_detail', [])
            meaning_en = meaning_en or gen.get('meaning_en', '')
            meaning_unavailable = bool(gen.get('meaning_unavailable'))
            meaning_error = gen.get('meaning_error')
            meaning_raw = gen.get('meaning_raw') or ''

    # 4) 성씨 결과
    #    사전에 있는 음차면 미리 만든 결과를, 없으면 엔진이 매칭한 성씨를 사용한다.
    #    (한국 성씨 29개의 한자·순위·유래설명은 SURNAME_INFO에 모두 있음)
    sres = TRANSLIT_TO_RESULT['surname'].get(last_tr)
    if not sres:
        eng_surname = (result or {}).get('last_1') if not neutral else None
        if not eng_surname:
            r2 = _convert_quiet(first_tr, last_tr, sex)
            eng_surname = r2.get('last_1')
        sres = SURNAME_INFO.get(eng_surname)

    surname = sres.get('surname') if sres else None
    surname_rom = sres.get('romanized') if sres else None
    surname_hanja = sres.get('hanja') if sres else None
    surname_desc = None
    if sres:
        surname_desc = build_surname(
            sres.get('info_en', ''), surname, surname_rom,
            given_hangul=given, given_rom=romanize_hyphen(given),
        )

    # 5) 변환 이유
    # Either(성별 무관)를 고른 경우, 성별을 단정하는 문구를 중립화한다.
    # 사용자가 성별을 고르지 않았는데 "a boy's name"이라고 하면 선택이 무시된 셈이다.
    if neutral:
        meaning_en = _degender(meaning_en)
        meaning_raw = _degender(meaning_raw)
    else:
        # 성별을 골랐는데 설명이 반대 성별로 단정하면 교정한다
        # (예: 여성인데 시언 같은 남성 이름이 매칭돼 "a boy's name"으로 나온 경우)
        meaning_en = _regender(meaning_en, sex)
        meaning_raw = _regender(meaning_raw, sex)

    # 순우리말 이름인지 판정.
    # 발음에 맞춰 한자가 붙어 있어도 실제로는 순우리말인 이름이 있다
    # (가람·노을·마음 등 21개). 그 경우 한자 뜻은 이름의 뜻과 무관하므로
    # 카드에서 제외한다.
    is_native = _is_native_name(hanja, meaning_en or meaning_raw)
    # 순우리말이라도 한자 뜻이 어울리면 함께 보여준다.
    native_with_hanja = is_native and hanja and not _hide_hanja(given, hanja)
    if is_native and not native_with_hanja:
        hanja = ''
        hanja_lines = []

    reason = build_reason(
        english_name=first_en.strip().title(),
        translit=first_tr,
        korean_given=given,
        quality=quality,
    )

    # 순우리말 이름이면 그 갈래를 설명한다.
    # 한자가 빠진 것이 아니라 원래 한자를 쓰지 않는 종류임을 알려 준다.
    if is_native:
        if native_with_hanja:
            reason['native_note'] = (
                'Korean given names come in two kinds. Most are Sino-Korean: each syllable '
                'is written with a Chinese character (called hanja in Korean) that carries '
                'its own meaning. '
                f'A smaller set \u2014 like {given} \u2014 are native Korean names, built from '
                'pure Korean words.\n\n'
                f'{given} works on both levels. As a Korean word it has its own meaning, and '
                'families often choose Chinese characters whose sounds match and whose meanings '
                'echo it \u2014 the ones shown on your card. The Korean meaning comes first; '
                'the characters add a second layer.\n\n'
                'Native names are loved for how they sound: soft, open and easy to say, '
                'without the formality that Chinese characters can carry. Anyone who hears '
                'one understands it right away.'
            )
        else:
            reason['native_note'] = (
                'Korean given names come in two kinds. Most are Sino-Korean: each syllable '
                'is written with a Chinese character (called hanja in Korean) that carries '
                'its own meaning. '
                f'A smaller set \u2014 like {given} \u2014 are native Korean names, built from '
                'pure Korean words with no Chinese characters behind them.\n\n'
                'Native names are loved for how they sound: soft, open and easy to say, '
                'without the formality that Chinese characters can carry. They feel warm and '
                'modern to Korean ears, and anyone who hears one understands it right away '
                '\u2014 there are no characters to look up.'
            )

    # 카드 표시용 조립
    given_rom = romanize_hyphen(given)
    syllables = []
    if surname:
        # 성씨는 관용 표기 사용 (이→Lee, 박→Park). 없으면 규칙 로마자로 폴백
        syllables.append({'ch': surname, 'hanja': surname_hanja or '',
                          'rom': surname_rom or romanize_syllable(surname)})
    for i, ch in enumerate(given):
        hj = ''
        if hanja and i < len(hanja):
            hj = hanja[i]
        syllables.append({'ch': ch, 'hanja': hj, 'rom': romanize_syllable(ch)})

    # 한자 뜻 줄 (고 (暠, go) — bright)
    # 한글 음절(syl)을 함께 실어, 영어권 사용자가 어느 글자인지 알아볼 수 있게 한다.
    hanja_lines = []
    for d in hanja_detail:
        if isinstance(d, (list, tuple)) and len(d) >= 3:
            hanja_lines.append({'syl': d[0], 'hanja': d[1],
                                'rom': romanize_syllable(d[0]).lower(),
                                'gloss': d[2]})

    # 짧은 의미 (카드 앞면)
    # 1순위: meaning_en 안의 "a wish for ..." / "It pictures ..." 같은 요약 문구
    # 2순위: 형용사형 gloss만 골라 조합 (명사/동사는 어색해서 제외)
    short = _short_meaning(meaning_raw or meaning_en, hanja_lines)
    if _has_hangul(short):      # 최종 방어: 카드 앞면에는 영어만
        short = 'A native Korean name'

    # 한자 뜻줄에도 한국어가 남아 있으면 그 줄을 제외
    hanja_lines = [h for h in hanja_lines if not _has_hangul(h.get('gloss'))]

    return {
        'input': f'{first_en.strip().title()} {last_en.strip().title()}'.strip(),
        'first_en': first_en.strip().title(),
        'last_en': last_en.strip().title(),
        'sex': sex,
        'translit': first_tr,
        'given': given,
        'given_rom': given_rom,
        'hanja': hanja,
        'syllables': syllables,
        'hanja_lines': hanja_lines,
        'meaning_short': short,
        'meaning_en': meaning_en,
        'surname': surname,
        'surname_rom': surname_rom,
        'surname_hanja': surname_hanja,
        'surname_desc': surname_desc,
        'full_hangul': (surname or '') + given,
        'full_rom': (f'{surname_rom} {given_rom}' if surname_rom else given_rom),
        'quality': quality,
        'is_unisex': bool(is_unisex),
        # 한자 없이 순우리말로만 쓰는 이름 (하늘·바다·기쁨 등).
        # 성씨에는 한자가 있으므로 이름 쪽만 판단한다.
        'is_native': is_native,
        'native_with_hanja': bool(native_with_hanja),
        # 페이지 렌더링 중에는 캐시만 조회한다. 없으면 프론트가
        # /api/tts 로 따로 요청하므로 첫 방문자도 카드를 바로 볼 수 있다.
        'audio': {'full': TTS_FULL.cached_url((surname or '') + given)},
        'meaning_unavailable': meaning_unavailable,
        'meaning_error': meaning_error,
        'neutral_request': bool(neutral),
        'reason': reason,
    }


# ---------------------------------------------------------------- 라우트
# ---------------------------------------------------------------- 배포 안전장치
from guardrails import RateLimiter, DailyBudget

# 환경변수로 조절 (없으면 아래 기본값)
RATE = RateLimiter(
    per_min=int(os.environ.get('RATE_PER_MIN', 20)),
    per_hour=int(os.environ.get('RATE_PER_HOUR', 200)),
)
BUDGET = DailyBudget(
    os.path.join(CACHE_DIR, 'daily_budget.json'),
    daily_max=int(os.environ.get('DAILY_NEW_NAME_MAX', 1500)),
)
# 발음(TTS) 하루 생성 상한 — 봇이 발음을 무한 생성하는 것 방지
TTS_BUDGET = DailyBudget(
    os.path.join(CACHE_DIR, 'tts_budget.json'),
    daily_max=int(os.environ.get('TTS_DAILY_MAX', 1000)),
)

_BUSY_RATE = ("You&rsquo;re going a little fast — please wait a moment and try again.")
_BUSY_BUDGET = ("We&rsquo;re getting a lot of requests right now. "
                "Please try again later, or try a more common name.")


def _client_ip():
    """프록시(Render 등) 뒤에서는 X-Forwarded-For의 첫 IP가 실제 사용자."""
    xff = request.headers.get('X-Forwarded-For', '')
    return (xff.split(',')[0].strip() if xff else request.remote_addr) or 'unknown'


def _needs_llm(first_key, last_key, sex):
    """이 이름이 '새 이름'(사전·캐시에 없어 LLM 필요)인지. LLM 호출 없이 판정."""
    if not first_key or not last_key:
        return False
    if TRANSLIT.transliterate(last_key, 'surname', allow_llm=False) is None:
        return True
    if sex == 'other':
        return (TRANSLIT.transliterate(first_key, 'male', allow_llm=False) is None
                and TRANSLIT.transliterate(first_key, 'female', allow_llm=False) is None)
    sexk = 'female' if sex == '여' else 'male'
    return TRANSLIT.transliterate(first_key, sexk, allow_llm=False) is None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/result', methods=['GET', 'POST'])
def result():
    if request.method == 'GET':
        return redirect(url_for('index'))
    first_en = request.form.get('first_name', '')
    last_en = request.form.get('last_name', '')
    sex = request.form.get('sex', '여')

    # ① 속도 제한(연타·봇 차단)
    if not RATE.check(_client_ip()):
        return render_template('index.html', error=_BUSY_RATE,
                               first_name=first_en, last_name=last_en, sex=sex), 429
    # ② 하루 예산: 새 이름인데 한도를 넘었으면 생성하지 않고 안내
    is_new = _needs_llm(first_en.strip().lower(), last_en.strip().lower(), sex)
    if is_new and not BUDGET.allow():
        return render_template('index.html', error=_BUSY_BUDGET,
                               first_name=first_en, last_name=last_en, sex=sex), 503

    data = convert_name(first_en, last_en, sex)
    if 'error' in data:
        return render_template('index.html', error=data['error'],
                               first_name=first_en, last_name=last_en, sex=sex)
    if is_new:
        BUDGET.record()          # 새 이름 1건 소비 기록
    return render_template('result.html', d=data,
                           reason_json=json.dumps(data['reason'], ensure_ascii=False))


@app.route('/api/convert', methods=['POST'])
def api_convert():
    if not RATE.check(_client_ip()):
        return jsonify({'error': 'rate_limited',
                        'message': 'Too many requests. Please slow down.'}), 429
    payload = request.get_json(silent=True) or request.form
    first_en = payload.get('first_name', '')
    last_en = payload.get('last_name', '')
    sex = payload.get('sex', '여')
    is_new = _needs_llm(first_en.strip().lower(), last_en.strip().lower(), sex)
    if is_new and not BUDGET.allow():
        return jsonify({'error': 'busy',
                        'message': 'High traffic right now — try again later '
                                   'or use a more common name.'}), 503
    data = convert_name(first_en, last_en, sex)
    if 'error' not in data and is_new:
        BUDGET.record()
    status = 400 if 'error' in data else 200
    return jsonify(data), status


@app.route('/diag')
def diag():
    """오디오 진단 페이지 — 내부 설정이 보이므로 기본은 숨김.
    확인이 필요할 때만 환경변수 ENABLE_DIAG=1 로 잠깐 켠다."""
    if os.environ.get('ENABLE_DIAG', '').lower() not in ('1', 'true', 'yes'):
        return ('Not found', 404)
    d = convert_name('Sophia', 'Hernandez', '여')
    if 'error' in d:
        sample = {'full': d['error'][:40], 'url': None}
        audio = {'full': ''}
    else:
        # 진단 페이지에서는 캐시만 보지 말고 실제로 생성까지 시도한다.
        # (결과 페이지는 응답 속도를 위해 캐시만 조회한다)
        url = d['audio'].get('full') or TTS_FULL.url_for(d['full_hangul'])
        sample = {'full': d['full_hangul'], 'url': url}
        audio = {'full': url or ''}

    files = []
    url = audio['full']
    if url:
        fp = os.path.join(BASE, url.lstrip('/').replace('/', os.sep))
        files.append({'label': '풀네임', 'url': url,
                      'size': os.path.getsize(fp) if os.path.exists(fp) else 0,
                      'name': os.path.basename(fp)})
    else:
        files.append({'label': '풀네임', 'url': '', 'size': 0, 'name': '(생성 실패)'})

    return render_template(
        'diag.html',
        stats=TTS_FULL.stats(),
        tts_ready=TTS_FULL.available,
        voice=TTS_FULL.voice,
        style_prompt=TTS_FULL.style_prompt,
        model=TTS_FULL.model,
        last_mode=TTS_FULL.last_mode,
        last_error=getattr(TTS_FULL, 'last_error', None),
        sample=sample,
        files=files,
        audio_json=json.dumps(audio, ensure_ascii=False),
        files_json=json.dumps(files, ensure_ascii=False),
    )


@app.route('/api/tts')
def api_tts():
    """
    풀네임 음성을 요청 시 생성한다.
    카드 렌더링과 분리되어 있어 페이지 로딩을 막지 않는다.
    """
    if not RATE.check(_client_ip()):
        return jsonify({'error': 'rate_limited'}), 429
    name = (request.args.get('name') or '').strip()
    if not name or len(name) > 20:
        return jsonify({'error': 'name is required'}), 400
    # 이미 만들어둔 발음은 그대로 제공(무료)
    cached = TTS_FULL.cached_url(name)
    if cached:
        return jsonify({'url': cached})
    # 새로 만들어야 하면 하루 상한 확인
    if not TTS_BUDGET.allow():
        return jsonify({'error': 'busy'}), 503
    url = TTS_FULL.url_for(name)
    if not url:
        return jsonify({'error': 'unavailable'}), 503
    TTS_BUDGET.record()
    return jsonify({'url': url})


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    print('=' * 58)
    print('K-Name Generator')
    print('=' * 58)
    if TRANSLIT.llm_available:
        print('  LLM 음차   : 사용 가능 — 사전 밖 이름도 변환됩니다')
    else:
        print('  LLM 음차   : 미설정 — 사전 안 이름만 변환됩니다')
        print('               export ANTHROPIC_API_KEY=... 로 활성화')
        print('               (python check_llm.py 로 연결 확인)')
    print(f'  이름 사전  : {len(NAME_TO_TRANSLIT["male"]) + len(NAME_TO_TRANSLIT["female"]):,}개')
    print(f'  성씨 사전  : {len(NAME_TO_TRANSLIT["surname"]):,}개')
    print(f'  한국 성씨  : {len(SURNAME_INFO)}개 (유래 설명 포함)')
    print('=' * 58)
    print('  http://localhost:5000')
    print()
    # debug=True 는 오류 화면에 소스가 노출되고 임의 코드 실행이 가능하다.
    # 운영에서는 반드시 꺼야 하므로 환경변수로만 켠다.
    debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=debug)

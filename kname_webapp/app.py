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
import threading
from urllib.parse import quote

from flask import (Flask, render_template, request, jsonify, redirect,
                   url_for, make_response, send_file, abort)

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, 'lib'))

import engine as eng
from conversion_reason import build_reason, build_surname
from transliterate import Transliterator
from meaning import NameMeaning
from meaning_en import MeaningEnGenerator
try:
    from meaning_en import PROMPT_VERSION as _PROMPT_VERSION
except Exception:
    _PROMPT_VERSION = None
from tts_full import FullNameTTS
from pronounce_guide import romanize_hyphen, romanize_syllable, romanize_joined

# 사용자가 겪는 오류/이상 결과 보고 헬퍼(Sentry+stderr). 없으면 no-op.
try:
    from monitor import report
except Exception:
    def report(*a, **k):
        pass

# 결과물 품질 점검 — 변환은 성공했지만 카드 문구가 이상한 경우를 잡는다.
try:
    from quality import audit as _audit, SEVERITY as _Q_SEVERITY
except Exception:
    _audit, _Q_SEVERITY = None, {}

# 시스템 오류가 아니라 사용자 입력 실수(빈칸·너무 긴 값)인 에러 — 보고하지 않는다.
_INPUT_ERR_HINTS = ('enter your first', 'enter your last', 'shorter name',
                    'english letters')

# 라틴 알파벳이 하나도 없는 입력에 대한 안내(시스템 오류가 아닌 입력 오류).
_LETTERS_ERR = 'Please write your name in English letters.'


def _no_reading_error(name, kind):
    """
    음차가 비어서 돌아온 경우의 처리.

    음차는 사전 → 캐시 → LLM → 규칙 폴백으로 이어지므로, 알파벳이 있는
    이름이 여기까지 오는 것은 정상이 아니다. 거의 확실히 lib/fallback_translit.py
    가 배포되지 않은 것이다. 사용자에게 보이는 문구는 같지만, 그 경우
    원인을 알 수 있도록 시스템 오류로 따로 보고한다.
    """
    if not any(c.isalpha() and c.isascii() for c in str(name or '')):
        return _LETTERS_ERR          # 숫자·기호만 입력 — 입력 오류가 맞다
    report('no reading produced for a valid name — '
           'is lib/fallback_translit.py deployed?',
           level='error', fingerprint=['translit', 'fallback-missing'],
           name=name, kind=kind)
    return _LETTERS_ERR

app = Flask(__name__)

# ---------------------------------------------------------------- 정적 파일 캐시
# 방문자가 폰트(0.5MB)·종이 질감·CSS 를 올 때마다 다시 받지 않게 한다.
#  · 템플릿의 url_for('static', ...) 주소에 ?v=<파일 내용 해시> 가 자동으로 붙는다.
#    파일을 고치면 주소가 바뀌므로, 오래 캐시해도 예전 CSS/JS 가 남는 일이 없다.
#  · 폰트·이미지는 이름에 판을 넣어 관리한다(notoserifkr-app.v2.woff2, paper-grain.png).
#    내용을 바꿀 때는 파일명(판)을 바꿀 것 — 같은 이름으로 덮어쓰면 1년간 예전 것이 보일 수 있다.
#  · 발음 오디오는 하루. 목소리 설정을 바꾸면 다음 날부터 새것이 들린다.
import hashlib
_STATIC_VER = {}     # filename -> (mtime_ns, size, hash)


def _static_version(filename):
    path = os.path.join(app.static_folder, filename)
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = _STATIC_VER.get(filename)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    with open(path, 'rb') as f:
        h = hashlib.md5(f.read()).hexdigest()[:8]
    _STATIC_VER[filename] = (st.st_mtime_ns, st.st_size, h)
    return h


@app.url_defaults
def _static_url_version(endpoint, values):
    if endpoint == 'static' and 'filename' in values and 'v' not in values:
        v = _static_version(values['filename'])
        if v:
            values['v'] = v


_IMMUTABLE_EXT = ('.woff2', '.woff', '.ttf', '.otf', '.png', '.jpg', '.jpeg', '.svg', '.ico', '.webp')


@app.after_request
def _static_cache_headers(resp):
    p = request.path
    if not p.startswith('/static/') or resp.status_code != 200:
        return resp
    if 'v' in request.args or p.lower().endswith(_IMMUTABLE_EXT):
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    elif p.lower().endswith('.mp3'):
        resp.headers['Cache-Control'] = 'public, max-age=86400'
    else:
        resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp

# ---------------------------------------------------------------- 에러 모니터링
# Sentry: SENTRY_DSN 환경변수가 있을 때만 켜진다. 앱 어딘가에서 예외가 터지면
# 자동으로 잡아 스택 트레이스·발생 빈도·맥락과 함께 이메일로 알려준다.
# 키가 없으면 아무 일도 하지 않으므로 로컬/미설정 환경에서도 안전하다.
_SENTRY_ON = False
if os.environ.get('SENTRY_DSN'):
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=os.environ['SENTRY_DSN'],
            traces_sample_rate=0.0,        # 성능 추적은 끔(비용 절약, 에러만 수집)
            send_default_pii=False,        # 개인정보는 보내지 않음
            environment=os.environ.get('RENDER_SERVICE_NAME', 'production'),
        )
        _SENTRY_ON = True
    except Exception as _e:
        print(f'[sentry] init failed: {_e}', file=sys.stderr, flush=True)

# 가동 시각 — /status 에서 업타임 계산에 쓴다
import time as _time
_BOOT_TS = _time.time()

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

# ------------------------------------------------ 캐시 이전 (디스크를 새로 붙일 때)
# 영속 디스크를 새로 붙이면 CACHE_DIR 은 빈 폴더다. 그런데 레포에는 그동안
# 쌓인 캐시가 들어 있다. 그대로 두면 앱은 빈 캐시를 읽고, 이미 돈을 내고
# 받아둔 답을 다시 사게 된다.
#
# 손으로 cp 하는 것으로는 해결되지 않는다. 캐시는 부팅 때 읽어 메모리에
# 들고 있다가 저장할 때 통째로 다시 쓰므로, 복사해 넣은 파일을 다음 저장이
# 덮어쓴다. 그래서 **읽기 전에** 옮겨야 하고, 그 순서를 여기서 코드로 고정한다.
# (아래 Transliterator / MeaningGenerator 생성보다 반드시 앞에 있어야 한다)
#
# 규칙은 둘뿐이다.
#   1) 대상이 없을 때만 옮긴다 — 운영 중인 캐시는 절대 덮지 않는다
#   2) 읽을 수 있는 JSON 객체일 때만 옮긴다 — 깨진 파일을 심지 않는다
# stats.db 와 예산 파일은 옮기지 않는다. 로컬 테스트 기록이 운영 통계에
# 섞이면 안 되고, 예산은 날짜별 카운터라 옮길 의미가 없다.
#
# 어디서 가져오는지 —
#   .gitignore 가 translit_cache.json / meaning_cache.json /
#   meaning_en_cache.json 을 제외하고 있다. 그래서 **레포에는 캐시가 없고,
#   서버의 앱 폴더에도 없다.** 평소 배포에서는 옮길 것이 하나도 없고
#   이 블록은 조용히 지나간다(그게 정상이다).
#   옮길 것이 생기는 경우는 둘이다.
#     - data/cache_seed/ 에 씨앗 파일을 일부러 넣어 커밋했을 때
#     - 로컬에서 CACHE_DIR 을 새 폴더로 바꿔 돌릴 때(앱 폴더에 캐시가 있다)
#   그래서 두 곳을 순서대로 본다.
CACHE_SEEDED = []
if CACHE_DIR != BASE:
    import shutil as _shutil
    for _cname in ('translit_cache.json', 'meaning_cache.json',
                   'meaning_en_cache.json'):
        _dst = os.path.join(CACHE_DIR, _cname)
        if os.path.exists(_dst):
            continue
        _src = next((p for p in (os.path.join(DATA, 'cache_seed', _cname),
                                 os.path.join(BASE, _cname))
                     if os.path.exists(p)), None)
        if not _src:
            continue
        try:
            with open(_src, encoding='utf-8') as _cf:
                _cdata = json.load(_cf)
            if not isinstance(_cdata, dict):
                continue
            _shutil.copyfile(_src, _dst)
            CACHE_SEEDED.append(f'{_cname}:{len(_cdata)}')
        except Exception as _ce:
            print(f'[cache] seed failed {_cname}: {_ce}',
                  file=sys.stderr, flush=True)
    if CACHE_SEEDED:
        print(f'[cache] seeded into {CACHE_DIR}: {", ".join(CACHE_SEEDED)}',
              file=sys.stderr, flush=True)

# 변환 통계 저장소(SQLite) — /admin 대시보드가 읽는다. 캐시와 같은 수명.
from stats import Stats
STATS = Stats(os.path.join(CACHE_DIR, 'stats.db'))

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
    """예전에는 엔진이 stdout 에 로그를 뿌려 redirect_stdout/redirect_stderr 로 감쌌다.
    지금 engine.py 에는 print 가 없다. 그리고 그 감싸기는 **스레드에서 위험하다** —
    sys.stdout/stderr 를 프로세스 전체에서 바꿔치기하므로, 두 요청이 겹치면 복원 순서가
    꼬여 stderr 가 죽은 StringIO 에 영구히 묶인다(그 뒤 Render 로그·monitor 출력이 모두
    사라짐. 워커 스레드 4개 설정에서 실제로 재현됨). 그래서 감싸지 않는다."""
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

# 한자 뜻 → 품사 표. 카드 앞면 한 줄 의미 조립에만 쓴다.
# 표에 없는 뜻은 '모르는 것'으로 두고 문장을 만들지 않는다(라벨형으로 내려간다).
try:
    from gloss_pos import (GLOSS_POS as _GLOSS_POS, VERB_FORM as _VERB_FORM,
                           FORCE_NOUNS as _FORCE_NOUNS,
                           VERB_TRANSITIVE as _VERB_T,
                           ADJ_NOT_PERSON as _ADJ_NOT_PERSON)
except Exception:
    _GLOSS_POS, _VERB_FORM, _FORCE_NOUNS, _VERB_T = {}, {}, set(), set()
    _ADJ_NOT_PERSON = set()

# 사전 602개 중 설명문에서 한 줄을 뽑아내지 못하는 이름의 손으로 쓴 한 줄.
try:
    from short_en import SHORT_EN as _SHORT_EN
except Exception:
    _SHORT_EN = {}

# 국적 — 입력 폼 드롭박스 + 로그 저장
try:
    from countries import (COUNTRIES as _COUNTRIES, NAMES as _COUNTRY_NAMES,
                           normalize as _norm_country,
                           from_accept_language as _country_from_header)
except Exception:
    _COUNTRIES, _COUNTRY_NAMES = [], {}
    _norm_country = lambda c: ''            # noqa: E731
    _country_from_header = lambda h: ''     # noqa: E731

# 교정된 순우리말 이름 사전 (DB 유형 오분류·LLM 신호에 의존하지 않는 정본).
# 여기 있으면 무조건 순우리말로 취급해 한자를 감추고 그 뜻을 쓴다.
try:
    from native_names import NATIVE_NAMES as _NATIVE_NAMES
except Exception:
    _NATIVE_NAMES = {}


def _native_desc(given, meaning_en):
    """순우리말 이름 설명(영어). 순우리말 신호와 한줄의미 훅을 담는다."""
    # 도입부 로마자는 하이픈 없이 쓴다 — 사전 602개와 LLM 설명이 모두
    # '하람 (Haram)' 형식이므로, 여기만 'Ha-ram' 이면 카드마다 형식이 달라진다.
    rom = romanize_joined(given)   # 영어 낱말과 겹치는 이름만 하이픈 유지(pronounce_guide.ROMAN_KEEP_HYPHEN)
    m = (meaning_en or '').strip().rstrip('.')
    return (f'{given} ({rom}) is a native Korean name — the native Korean word for "{m}." '
            f'It carries no Chinese characters; the meaning lives right in the sound. '
            f'{_sound_note(given)}')


def _sound_note(given):
    def _b(c):
        return ('가' <= c <= '힣') and (ord(c) - 0xAC00) % 28 != 0
    b = [_b(c) for c in given]
    if not any(b):
        return 'Soft and open, with no hard stops, it flows gently and travels easily in any language.'
    if all(b):
        return 'It sounds firm and grounded, each syllable landing on a clear consonant.'
    return 'It moves with an easy rise and fall, one syllable open and the next settling softly.'


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


# 음차 실패 안내문(_translit_error)은 제거했다.
# 음차가 사전 → 캐시 → LLM → 규칙 폴백으로 이어져 더 이상 실패하지 않으므로
# "we couldn't work out how ... sounds in Korean" 메시지는 나올 수 없다.
# 문구 자체를 남겨두면 언젠가 다시 쓰이게 되므로 코드에서 지운다.


def _generate_meaning(given, sex, english_first, translit, neutral=False, allow_llm=True):
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
        out = {'hanja': '', 'hanja_detail': [], 'meaning_en': '',
               'meaning_short': '', 'meaning_stale': False}

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
        #    설명과 함께 카드 앞면 한 줄(short)도 받아 온다. 그 한 줄을
        #    로컬에서 조립하지 않게 되어 비문이 구조적으로 사라진다.
        en = ''
        if MEANING_EN is not None:
            en, out['meaning_short'] = MEANING_EN.explain_pair(
                # Either 를 고른 경우 성별을 단정하지 않도록 중립값을 넘긴다
                given=given, sex=('기타' if neutral else sex), hanja_chars=chars,
                english_name=english_first, gloss_en=_KR_GLOSS_TO_EN,
                # 카드와 같은 출처(한자별 영어뜻)를 쓰게 한다.
                # 이게 없으면 설명과 카드의 뜻이 서로 달라진다.
                hanja_en=HANJA_EN,
                allow_llm=allow_llm,
            )
            en = en or ''
            # 예전 캐시를 그대로 내보낸 경우 — 내용이 낡았으므로 기록한다
            out['meaning_stale'] = bool(getattr(MEANING_EN, 'last_stale', False))
            out['meaning_stale_why'] = getattr(MEANING_EN, 'last_stale_why', None)
            # 출력 전 검수 결과 요약(status / issues / reason). quality.audit 가 기록한다.
            out['review'] = getattr(MEANING_EN, 'last_review', None)

        # ② 실패 시 meaning.py로 폴백 (한국어+영어 생성, 비용 높음)
        if not en:
            res = MEANING.explain(
                given=given, sex=sex,
                hanja_chars=chars, name_type=ntype,
                english_name=english_first, first_kr=translit,
                allow_llm=allow_llm,
            )
            if isinstance(res, dict):
                en = res.get('meaning_en', '') or ''

        # 설명이 어디서 나왔는지 여기서 기록해 둔다.
        # 나중에 텍스트 문체로 되짚으면 사전(602)·순우리말 설명과 구분되지 않는다
        # — 셋이 같은 형식으로 쓰였기 때문에 오판이 반드시 생긴다.
        out['meaning_source'] = 'llm' if en else ''

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
                out['meaning_source'] = 'template' if en else 'none'
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
def _gloss_usable(gloss):
    """
    이름 뜻으로 쓸 수 있는 뜻인지. 'surname'·'thing' 처럼 품사표에서 'S'
    (사용 불가)로만 이루어진 뜻은 카드에 아무 것도 기여하지 못한다.
    """
    toks = [t.strip().lower() for t in str(gloss or '').split(',') if t.strip()]
    if not toks:
        return False
    return any(_GLOSS_POS.get(t) != 'S' for t in toks)


HANJA_EN = {}
try:
    import pandas as _pd_he
    _he_df = _pd_he.read_excel(os.path.join(DATA, 'hanja_dict.xlsx'))
    if '영어뜻' in _he_df.columns:
        # 같은 한자가 여러 행에 있는 경우가 28자 있다. 예전에는 조건 없이
        # 덮어써서 '마지막 행이 이기는' 구조였고, 그래서 다음처럼 나빠졌다.
        #   廓  'surroundings' 를 'surname' 이 덮음  → 뜻으로 못 씀
        # 쓸 수 있는 뜻을 쓸 수 없는 뜻이 덮지 못하게 한다.
        for _h, _e in zip(_he_df['한자'], _he_df['영어뜻']):
            if not (isinstance(_h, str) and isinstance(_e, str) and _e.strip()):
                continue
            _e = _e.strip()
            _prev = HANJA_EN.get(_h)
            if _prev and _gloss_usable(_prev) and not _gloss_usable(_e):
                continue
            HANJA_EN[_h] = _e
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


def _pick_neutral(first_key, last_tr, allow_llm=True):
    """
    '그 외' 선택 시: 남녀 사전을 모두 조회해
      1) 남녀 공용으로 쓰이는 이름이 있으면 우선
      2) 없으면 품질이 가장 좋은 결과
    반환: (sexk, translit, given, quality, is_unisex) 또는 None
    """
    cands = []
    for sexk, sk in (('male', '남'), ('female', '여')):
        tr = TRANSLIT.transliterate(first_key, sexk, allow_llm=allow_llm)
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
    # 불가산·물질·추상명사 — 관사를 붙이면 "as a poetry" 같은 비문이 된다
    'poetry', 'history', 'music', 'art', 'learning', 'knowledge', 'truth',
    'beauty', 'radiance', 'brilliance', 'splendor', 'harmony', 'peace',
    'glory', 'courage', 'patience', 'clarity', 'purity', 'jade radiance',
    'good fortune', 'abundance', 'warmth', 'kindness', 'talent',
    'jade', 'gold', 'silk', 'water', 'sunlight', 'moonlight', 'firelight',
    'earth', 'land', 'sky', 'spring', 'dawn', 'daylight', 'ink', 'honey',
    'barley', 'cotton', 'coral', 'metal', 'stone',
    # 복수형·집합명사 — 관사를 붙이면 어색하다
    'woods', 'grass', 'plants', 'words', 'scenery', 'rice', 'snow', 'rain',
}

_ADJ_OK = {
    # 명사로 오분류되어 "A name of beneficial and talent" 같은 비문을 만들던 뜻들.
    # (뜻 문자열 전체가 한 항목이므로 여러 단어로 된 형용사구도 여기 넣는다)
    'admirable','robust','beneficial','true','exemplary','flourishing',
    'luxuriant','vast and great','at peace','upright and true',
    'outstanding','excelling','clear','upright','bright','wise','benevolent','kind',
    'graceful','beautiful','lovely','peaceful','glad','virtuous','good','auspicious',
    'great','foremost','lofty','towering','abundant','broad','wide','warm','clever',
    'shining','radiant','dignified','steadfast','flourishing','distinct','sparkling',
    'strong','mighty','reverent','serene','generous','fragrant','vast','white',
    'refined','elegant','gifted','valiant','fierce','tender','delicate','stern',
    'calm','tranquil','holy','boundless','keen','sharp','brilliant','fine','deep',
    'even','noble','cultured','sagely','lustrous','auspicious','gracious','regal',
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
# 명사가 아니어서 아예 뜻으로 세울 수 없는 말(수사·대명사·전치사).
# 이 경우 형용사만 남겨 "A lofty person" 형태로 만든다.
_NOT_NOUN = {
    'above', 'all', 'alongside', 'behind', 'beside', 'both', 'each',
    'inside', 'self', 'that', 'the most', 'this', 'us', 'we',
    'three', 'six', 'seven', 'eight', 'ten', 'many',
}

# 실제 명사이지만 'A as a B' 비유가 어색한 뜻.
# ("benevolent as a talent", "graceful as a model" 같은 문장을 막는다)
_NOT_COMPARABLE = {
    'talent', 'good omen', 'years', 'arriving', 'model', 'a model',
    'praise', 'center', 'board', 'source', 'fine person', 'a fine person',
    'hill', 'sovereign', 'gate', 'record', 'history', 'the first',
    'many', 'all', 'each', 'both', 'three', 'six', 'seven', 'eight', 'ten',
    'the most', 'above', 'inside', 'behind', 'beside', 'alongside',
    'self', 'we', 'us', 'this', 'that',
}


def _adj_noun(adj, noun):
    """'bright' + 'jade' → 'Someone bright as jade'"""
    a, n = adj.lower(), noun.lower()
    if n in _NOT_NOUN:
        # 'lofty as a many' 같은 비문이 되므로 형용사만 남긴다
        art = 'An' if a[0] in 'aeiou' else 'A'
        return f'{art} {a} person'
    if n in _NOT_COMPARABLE:
        # 비유가 성립하지 않는 뜻은 'A as a B' 대신 곁들이는 형태로
        return f'Someone {a}, with {_a(n)}'
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


def _adj_noun_pos(adj, noun, npos):
    """
    품사를 아는 상태에서 만드는 '형용사 + 명사' 문구.
    _adj_noun()과 달리 명사 종류를 목록으로 추측하지 않는다.
      N  'bright' + 'a pearl'  → Someone bright as a pearl
      M  'bright' + 'jade'     → Someone bright as jade
      T  'bright' + 'moon'     → Someone bright as the moon
      X  'bright' + 'wisdom'   → Someone bright, with wisdom  (비유가 안 된다)
    """
    a = adj.strip().lower()
    n = noun.strip().lower()
    if npos == 'X' or n in _NOT_COMPARABLE or _bare_noun(n) in _NOT_COMPARABLE:
        # 'A as a B' 비유가 성립하지 않는 뜻 — 곁들이는 형태로 바꾼다.
        # 관사는 여기서도 품사대로 붙인다('with model'은 비문).
        return f'Someone {a}, with {_as_noun(noun.strip(), npos)}'
    return f'Someone {a} as {_as_noun(noun.strip(), npos)}'


def _verb_phrase(verb):
    """'to assist' → 'assists'  ('to be' → 'abides')"""
    v = verb.strip().lower()
    if v in _VERB_FORM:
        return _VERB_FORM[v]          # 불규칙 — 규칙대로면 'bes'가 된다
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


def _pos_of(token):
    """
    한자 뜻 토큰의 품사. 모르면 None — 절대 추측하지 않는다.

    어미로 품사를 추론하면 반드시 오탐이 생긴다.
      a capital · crystal · lotus seed · a descendant → 명사인데 형용사 어미
      great zither · clear water · broad rock         → 형용사+명사 복합어
    그래서 아는 것만 쓰고, 모르면 문장을 만들지 않는다.
    """
    t = str(token or '').strip().lower()
    if not t:
        return None
    # 표를 가장 먼저 본다 — 형태로 판정하는 아래 두 규칙을 덮어쓸 수 있어야 한다
    # (예: 'to pair with' 는 목적어가 없으면 문장이 끊기므로 'S'로 막아 둔다)
    p = _GLOSS_POS.get(t)
    if p:
        return p
    if t.startswith('to '):
        return 'V'                       # 'to shine' — 부정사는 형태로 확정된다
    if t.split()[0] in ('a', 'an', 'the'):
        return 'N'                       # 관사가 붙어 있으면 가산명사가 확실하다
    # 기존 목록에서 유추 가능한 것만 받아 쓴다 (여기까지도 추측은 없다)
    if t in _ADJ_OK:
        return 'A'
    if t in _NO_ARTICLE:
        return 'M'
    if t in _ABSTRACT:
        return 'X'
    if t in _NOT_NOUN:
        return 'S'
    return None


def _as_noun(token, pos):
    """
    비유 문구에 넣을 명사구. 관사는 품사로만 결정한다.
      N  가산명사   → 'a pearl'      (뜻에 이미 관사가 있으면 그대로)
      T  유일물     → 'the moon'
      M  불가산     → 'jade'         관사를 붙이면 비문이 된다
      X  추상명사   → 'wisdom'
    고유명사('the Dipper')가 있으므로 대소문자는 건드리지 않는다.
    """
    n = token.strip()
    if pos == 'N':
        if n.split()[0].lower() in ('a', 'an', 'the'):
            return n
        return ('an ' if n[0].lower() in 'aeiou' else 'a ') + n
    if pos == 'T':
        return 'the ' + n
    return n


def _bare_noun(token):
    """뜻만 남긴 형태 — 관사를 뗀다. (라벨형에 쓴다)"""
    return re.sub(r'^(?:a|an|the)\s+', '', token.strip(), flags=re.I)


def _label_form(hanja_lines):
    """
    품사를 모르는 뜻이 섞였을 때의 안전한 형태.
    문장을 만들지 않고 뜻을 나열하므로 비문이 될 수 없다.
      예) 'Bright and wise · Luster of jade'
    """
    labels = []
    for h in hanja_lines:
        words = [w.strip() for w in str(h.get('gloss') or '').split(',') if w.strip()]
        words = [w for w in words if not _has_hangul(w)]
        # 'S'(수사·전치사·목적어가 필요한 동사)는 라벨로도 쓸 수 없다.
        #   'to pair with' → "Pair · To pair with" 처럼 끊긴 라벨이 된다
        usable = [w for w in words if _pos_of(w) != 'S']
        if not usable:
            continue
        w = _bare_noun(usable[0])
        if not w:
            continue
        labels.append(w[0].upper() + w[1:])
    if not labels:
        return None
    out = ' · '.join(labels[:3])
    return out if len(out) <= 62 else labels[0]


def _short_meaning(meaning_en, hanja_lines, given='', llm_short=''):
    """카드 앞면의 한 줄 요약."""
    # 0) 사전 이름 중 설명문에서 뽑아내지 못하는 것들은 손으로 쓴 문구를 쓴다
    if given and given in _SHORT_EN:
        return _SHORT_EN[given]

    # 1) 의미설명 안의 대표 문구를 우선 사용
    if meaning_en:
        pats = [
            r"[Ii]t'?s a wish for (someone [^.\"\u2014;]+?)[.\"\u2014;]",
            r"[Ii]t pictures (someone [^.\"\u2014;]+?)[.\"\u2014;]",
            # 사전 602개가 핵심 의미를 이끌 때 쓰는 다른 표현들.
            # 여기서 못 잡으면 품질이 낮은 한자 조립 경로로 떨어진다.
            r"[Ii]t describes (someone [^.\"\u2014;]+?)[.\"\u2014;]",
            r"[Ii]t suggests \"?(someone [^.\"\u2014;]+?)[.\"\u2014;]",
            r"suggesting (someone [^.\"\u2014;]+?)[.\"\u2014;]",
            r"[Tt]he image of (someone [^.\"\u2014;]+?)[.\"\u2014;]",
            r"image of (someone [^.\"\u2014;]+?)[.\"\u2014;]",
            r"name for (someone [^.\"\u2014;]+?)[.\"\u2014;]",
            r"name means \"?(someone [^.\"\u2014;]+?)[.\"\u2014;]",
            r"[Ii]t evokes \"?(someone [^.\"\u2014;]+?)[.\"\u2014;]",
            r"wish (?:for|to be) (someone [^.\"\u2014;]+?)[.\"\u2014;]",
            r"[Ii]t carries the (?:poetic )?wish for (someone [^.\"\u2014;]+?)[.\"\u2014;]",
            r"name meaning (someone [^.\"\u2014;]+?)[.\"\u2014;]",
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
            r"wish for (a [^.\"\u2014;]+?)[.\"\u2014;]",
            r"[Ii]t pictures (a [^.\"\u2014;]+?)[.\"\u2014;]",
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

    # 2) LLM이 설명과 함께 써 준 한 줄.
    #    문장을 사람이 조립하지 않으므로 비문이 될 수 없다.
    if llm_short:
        s = re.sub(r'\s+', ' ', str(llm_short)).strip().rstrip('.').strip()
        if 10 <= len(s) <= 62 and len(s.split()) >= 3 and _ok_phrase(s):
            return s[0].upper() + s[1:]

    # 3) 한자 뜻으로 조립.
    #    품사를 아는 뜻만 쓴다. 한 글자라도 아는 뜻이 없으면 문장을 만들지
    #    않고 라벨형으로 내려간다 — 추측해서 조립하면 비문이 나온다.
    slots = []
    unknown = False
    for h in hanja_lines:
        words = [w.strip() for w in str(h.get('gloss') or '').split(',') if w.strip()]
        words = [w for w in words if not _has_hangul(w)]
        tagged = [(w, _pos_of(w)) for w in words]
        # 사람을 가리키는 문장을 만들므로, 사람에게 쓰면 영어에서 곤란해지는
        # 형용사는 여기서 한 번에 거른다(white=인종 · dense=멍청한 · high=은어).
        # 틀마다 따로 막으면 반드시 한 군데를 빠뜨린다 — 실제로 빠뜨렸다.
        usable = [(w, p) for w, p in tagged
                  if p and p != 'S'
                  and not (p == 'A' and w.lower() in _ADJ_NOT_PERSON)]
        if not usable:
            # 뜻이 전부 '모르는 것' 또는 '쓸 수 없는 것' — 조립 불가
            if any(p is None for _w, p in tagged):
                unknown = True
            continue
        slot = {'adj': None, 'noun': None, 'npos': None, 'verb': None}
        for w, p in usable:
            if p == 'A' and not slot['adj']:
                slot['adj'] = w
            elif p in ('N', 'M', 'T', 'X') and not slot['noun']:
                slot['noun'], slot['npos'] = w, p
            elif p == 'V' and not slot['verb']:
                slot['verb'] = w
        slots.append(slot)

    if unknown or not slots:
        # 모르는 뜻이 섞였으면 문장 대신 라벨 — 문법이 개입하지 않는다
        return _label_form(hanja_lines) or 'A native Korean name'

    # 모든 글자가 형용사 → "A bright and gentle person"
    #
    # 단, 사람을 가리키는 형용사로 쓰면 영어에서 곤란해지는 말은 이 틀에
    # 넣지 않는다(white=인종 · dense=멍청한 · high=약물 은어 · green=풋내기).
    # 뜻은 고쳐 두었지만, 나중에 한자를 추가하다 같은 표현이 들어오는 것을 막는다.
    adjs = [s['adj'] for s in slots if s['adj']]
    if len(adjs) == len(slots):
        uniq = list(dict.fromkeys(a.lower() for a in adjs))
        phrase = ' and '.join(uniq[:2]) if len(uniq) >= 2 else uniq[0]
        art = 'An' if phrase[0] in 'aeiou' else 'A'
        return f'{art} {phrase} person'

    if len(slots) == 2:
        a, b = slots
        # 형용사 + 명사 → "Someone bright as jade"
        for x, y in ((a, b), (b, a)):
            if x['adj'] and y['noun']:
                return _adj_noun_pos(x['adj'], y['noun'], y['npos'])
        # 형용사 + 동사 → "Someone bright who shines"
        for x, y in ((a, b), (b, a)):
            if x['adj'] and y['verb']:
                return f"Someone {x['adj'].lower()} who {_verb_phrase(y['verb'])}"

    nouns = [(s['noun'], s['npos']) for s in slots if s['noun']]
    verbs = [s['verb'] for s in slots if s['verb']]
    if len(nouns) >= 2:
        n1, n2 = _as_noun(*nouns[0]), _as_noun(*nouns[1])
        if n1.lower() == n2.lower():
            return f'A name of {n1}'      # 두 글자가 같은 뜻 — 되풀이하지 않는다
        return f'A name of {n1} and {n2}'
    if nouns and verbs:
        n, npos = nouns[0]
        # 뜻을 품사에 묶어두지 않고, 동작을 중심으로 문장을 세운다.
        #   旻(하늘) + 佑(돕다) → "하늘이 돕는 자"
        # 단, 방향을 정할 수 있는 경우에만 쓴다. 사람에게 작용하는 뜻
        # (하늘·은혜·복)이 타동사와 만났을 때다. 그 밖에는 방향을 알 수 없어
        # "옥을 돕는 자" 같은 엉뚱한 뜻이 되므로 아래 형태로 남긴다.
        if _bare_noun(n) in _FORCE_NOUNS and verbs[0].strip().lower() in _VERB_T:
            return f'Someone whom {_as_noun(n, npos)} {_verb_phrase(verbs[0])}'
        return (f'A name that {_verb_phrase(verbs[0])}, '
                f'holding {_as_noun(n, npos)}')
    if len(verbs) >= 2:
        return f'A name that {_verb_phrase(verbs[0])} and {_verb_phrase(verbs[1])}'
    if nouns:
        return f'A name of {_as_noun(*nouns[0])}'
    if adjs:
        art = 'An' if adjs[0][0].lower() in 'aeiou' else 'A'
        return f'{art} {adjs[0].lower()} person'
    if verbs:
        return f'A name that {_verb_phrase(verbs[0])}'

    # 4) 순우리말 이름 등 — 마지막 안전장치
    return _label_form(hanja_lines) or 'A native Korean name'


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
# "boys and girls alike"처럼 두 성별을 함께 부르는 관용구는 성별을 바꾸면 안 된다.
# 개별 치환(boys→girls)에 걸리면 "girls and girls alike"라는 말이 되어 버리므로,
# 치환 전에 잠시 치워두고(_KEEP) 맨 끝에서 되돌린다(_RESTORE).
_GENDER_PAIRS = [
    ("boys and girls", "\x00p1\x00"), ("girls and boys", "\x00p2\x00"),
    ("boy or girl", "\x00p3\x00"),    ("girl or boy", "\x00p4\x00"),
    ("boys and girls alike", "\x00p5\x00"),
]
_KEEP = [(re.compile(r"\b%s\b" % re.escape(a)), b) for a, b in _GENDER_PAIRS]
_RESTORE = [(re.compile(re.escape(b)), a) for a, b in _GENDER_PAIRS]

_REGENDER = {
    '여': _KEEP + [   # → 여성
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
    ] + _RESTORE,
    '남': _KEEP + [   # → 남성
        (re.compile(r"\ba girl's name\b"), "a boy's name"),
        (re.compile(r"\bgirl's name\b"), "boy's name"),
        (re.compile(r"\bgirls\b"), "boys"),
        (re.compile(r"\bgirl\b"), "boy"),
        (re.compile(r"\bdaughters\b"), "sons"),
        (re.compile(r"\bdaughter\b"), "son"),
        (re.compile(r"\bshe\b", re.I), "he"),
        (re.compile(r"\bher\b", re.I), "his"),      # 소유격 우선
        (re.compile(r"\bherself\b", re.I), "himself"),
    ] + _RESTORE,
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
_PLACEHOLDER_SURNAME_TR = '김'     # 성을 안 넣었을 때 엔진에 넣는 자리표시(결과에는 안 나감)


def convert_name(first_en, last_en, sex, allow_llm=True):
    """
    영어 이름 → 한국 이름 전체 결과.
    반환 dict 또는 {'error': ...}
    sex: '여' | '남' | 'other'(성별 무관)
    allow_llm=False: 사전·캐시만으로 만든다. 음차가 캐시에 없으면 error, 의미 설명이
      캐시에 없으면 템플릿 문구(플래그 남음). 공유 링크 GET(/n/...) 이 이 모드를 쓴다 —
      URL 이 생기면 봇이 무한히 두드릴 수 있는데, GET 은 절대 LLM 비용을 내지 않는다.
    """
    neutral = (sex == 'other')
    sexk = 'female' if sex == '여' else 'male'
    first_key = (first_en or '').strip().lower()
    last_key = (last_en or '').strip().lower()

    if not first_key:
        return {'error': 'Please enter your first name.'}
    # 성은 선택이다. 비워 두면 이름만 만들고, 성씨 결과는 비운다(뒷면에서 성을 넣어 보라고 권한다).
    # 엔진은 성과의 어울림까지 보므로 흔한 성 '김'을 자리표시로 넣어 돌리되, 결과에는 내지 않는다.
    no_surname = not last_key
    # 입력 길이 제한 — 너무 긴/이상한 값으로 AI 프롬프트를 흔드는 것 방지
    if len(first_key) > 40 or len(last_key) > 40:
        return {'error': 'Please enter a shorter name.'}

    # 음차는 사전 → 캐시 → LLM → 규칙 기반 폴백까지 이어져 실패하지 않는다.
    # 여기서 None 이 나오는 유일한 경우는 라틴 알파벳이 한 글자도 없는
    # 입력(숫자·기호만)이며, 이는 시스템 오류가 아니라 입력 오류다.
    if no_surname:
        last_tr = _PLACEHOLDER_SURNAME_TR
    else:
        last_tr = TRANSLIT.transliterate(last_key, 'surname', allow_llm=allow_llm)
        if not last_tr:
            return {'error': _no_reading_error(last_en, 'surname')}

    picked = _pick_neutral(first_key, last_tr, allow_llm=allow_llm) if neutral else None
    if neutral and not picked:
        # 남녀 공용 후보를 찾지 못했다 — 사용자에게 실패를 보이는 대신
        # 여성 경로로 이어서 진행한다.
        report('neutral pick failed — continued with a gendered result',
               level='warning', fingerprint=['conv-fallback', 'neutral'],
               name=first_en)
        sex, sexk = '여', 'female'

    if picked:
        sexk, first_tr, given, quality, is_unisex = picked
        sex = '남' if sexk == 'male' else '여'
    else:
        # 1) 음차 (사전 → 캐시 → LLM → 규칙 폴백)
        first_tr = TRANSLIT.transliterate(first_key, sexk, allow_llm=allow_llm)
        if not first_tr:
            return {'error': _no_reading_error(first_en, sexk)}
        # 2) 순우리말 이름이면 한자 매칭 엔진을 건너뛰고 소리 그대로 쓴다
        if first_tr in _NATIVE_NAMES:
            given = first_tr
            quality = 'Q1'
            is_unisex = given in UNISEX_NAMES
            result = None
        else:
            # 3) 변환 (엔진)
            result = _convert_quiet(first_tr, last_tr, sex)
            given = result.get('first_1')
            quality = result.get('given_quality', 'Q1')
            is_unisex = given in UNISEX_NAMES if given else False
            if not given:
                # 엔진이 후보를 하나도 만들지 못한 경우.
                # 순우리말 경로와 같은 방식으로 음차를 그대로 이름으로 쓴다.
                # 품질은 낮지만 사용자가 실패 화면을 보는 일은 없다.
                report('engine produced no given name — used the reading itself',
                       level='warning', fingerprint=['conv-fallback', 'engine'],
                       name=first_en, reading=first_tr)
                given = first_tr[:3]
                quality = 'Q3'
                is_unisex = given in UNISEX_NAMES
                result = None

    # 3) 이름 결과 상세 (한자·의미설명)
    #    사전 밖 음차면 변환된 한국이름(given)으로 역조회한다.
    gres = TRANSLIT_TO_RESULT['given'][sexk].get(first_tr)
    # 미리 만든 결과가 있으면 그 이름을 정본으로 삼는다.
    # (엔진의 실시간 매칭이 미리 만든 이름과 다를 수 있어, 표시 이름과
    #  한자·뜻이 어긋나는 것을 막는다. 예: 오언 → 온유(溫愈)로 통일)
    if gres and gres.get('given'):
        given = gres['given']
        is_unisex = given in UNISEX_NAMES
    if not gres:
        gres = GIVEN_INFO.get((sexk, given)) or GIVEN_INFO.get(('male', given)) \
               or GIVEN_INFO.get(('female', given)) or {}
    hanja = gres.get('hanja') or ''
    hanja_detail = gres.get('hanja_detail') or []
    meaning_en = gres.get('meaning_en', '')
    meaning_unavailable = False
    meaning_error = None
    meaning_raw = ''
    meaning_short = ''      # LLM이 설명과 함께 써 준 카드 앞면 한 줄
    meaning_stale = False   # 예전 캐시를 그대로 쓴 경우(내용이 낡았다)
    meaning_stale_why = None
    meaning_review = None   # 출력 전 검수 요약(LLM 생성 설명에만 있다)
    # 설명의 출처를 끝까지 따라간다: dict(미리 작성된 602개) / llm / template /
    # native(순우리말 로컬 설명). 점검 도구가 이 값을 그대로 읽으면 되므로,
    # 문체로 되짚다가 오판하는 일이 없어진다.
    meaning_source = 'dict' if meaning_en else ''

    # 602개 밖 이름이면 meaning.py로 한자·의미설명을 실시간 생성
    if (not meaning_en or not hanja) and MEANING is not None:
        gen = _generate_meaning(given, sex, first_en, first_tr, neutral=neutral, allow_llm=allow_llm)
        if gen:
            hanja = hanja or gen.get('hanja', '')
            hanja_detail = hanja_detail or gen.get('hanja_detail', [])
            # 미리 작성된 설명이 이미 있으면 그것을 쓴다(출처도 그대로 dict).
            if not meaning_en and gen.get('meaning_en'):
                meaning_en = gen['meaning_en']
                meaning_source = gen.get('meaning_source') or 'llm'
            meaning_unavailable = bool(gen.get('meaning_unavailable'))
            meaning_error = gen.get('meaning_error')
            meaning_raw = gen.get('meaning_raw') or ''
            meaning_short = gen.get('meaning_short') or ''
            meaning_stale = bool(gen.get('meaning_stale'))
            meaning_stale_why = gen.get('meaning_stale_why')
            meaning_review = gen.get('review')

    # 순우리말 정본 사전에 있으면: 한자를 감추고 그 뜻을 쓴다.
    # (DB 오분류·LLM 신호 여부와 무관하게 순우리말을 보장. HANJA_OK 이름은 한자 병기 허용)
    if given in _NATIVE_NAMES and given not in _NATIVE_HANJA_OK:
        hanja = ''
        hanja_detail = []
        if not _NATIVE_SIG.search(meaning_en or ''):
            meaning_en = _native_desc(given, _NATIVE_NAMES[given])
            meaning_source = 'native'
        elif meaning_source in ('template', 'none', ''):
            # 템플릿이 만든 순우리말 문구를 그대로 쓰는 경우도 실패가 아니다
            # (뜻은 순우리말 정본 사전에서 온다). 아래에서 오류 표시를 지운다.
            meaning_source = 'native'
        meaning_unavailable = False
        meaning_error = None
        meaning_raw = ''
        meaning_short = ''

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

    if no_surname:
        sres = None                     # 성을 안 넣었으면 성씨 결과를 내지 않는다
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
    short = _short_meaning(meaning_raw or meaning_en, hanja_lines,
                           given=given, llm_short=meaning_short)
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
        'no_surname': no_surname,
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
        # 설명 출처(dict / llm / native / template) — 점검 도구가 그대로 읽는다
        'meaning_source': meaning_source or ('dict' if meaning_en else 'none'),
        # 프롬프트가 바뀐 뒤 재생성에 실패해 예전 캐시를 쓴 경우
        'meaning_stale': bool(meaning_stale),
        'meaning_stale_why': meaning_stale_why,
        # 출력 전 검수 요약 — quality.audit 가 review/* 코드로 기록한다
        'meaning_review': meaning_review,
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


def _country_of_request():
    """
    (사용자가 고른 국적, 헤더 추정 국적).

    드롭박스는 선택 사항이다. 필수로 만들면 이름을 받으려는 사용자에게
    마찰이 생기고 이탈이 늘어난다. 대신 Accept-Language 로 추정치를 함께
    남겨, 비워 둔 경우에도 대략의 분포는 볼 수 있게 한다.
    두 값은 신뢰도가 다르므로 섞지 않고 따로 저장한다.
    """
    raw = request.form.get('country', '')
    if not raw and request.is_json:
        try:
            raw = (request.get_json(silent=True) or {}).get('country', '')
        except Exception:
            raw = ''
    geo = _country_from_header(request.headers.get('Accept-Language', ''))
    return _norm_country(raw), geo


def _client_ip():
    """프록시(Render 등) 뒤에서는 X-Forwarded-For의 첫 IP가 실제 사용자."""
    xff = request.headers.get('X-Forwarded-For', '')
    return (xff.split(',')[0].strip() if xff else request.remote_addr) or 'unknown'


def _needs_llm(first_key, last_key, sex):
    """이 이름이 '새 이름'(사전·캐시에 없어 LLM 필요)인지. LLM 호출 없이 판정."""
    if not first_key:
        return False
    if last_key and TRANSLIT.transliterate(last_key, 'surname', allow_llm=False) is None:
        return True
    if sex == 'other':
        return (TRANSLIT.transliterate(first_key, 'male', allow_llm=False) is None
                and TRANSLIT.transliterate(first_key, 'female', allow_llm=False) is None)
    sexk = 'female' if sex == '여' else 'male'
    return TRANSLIT.transliterate(first_key, sexk, allow_llm=False) is None


def _log_conv(first_en, last_en, sex, is_new, data, country='', geo='', ms=None):
    source = _source_cookie()
    """변환 1건을 통계에 기록(성공/실패 모두). 빈 입력은 제외."""
    if not str(first_en).strip():
        return
    ok = 'error' not in data
    # 품질 점검을 먼저 돌려, 그 결과를 같은 행에 남긴다.
    # 그러면 /admin 의 변환 로그에서 '이 변환에 무슨 문제가 있었는지'가
    # 한 줄로 보인다(집계만 있으면 개별 건을 되짚을 수 없다).
    findings = []
    if ok and _audit is not None:
        try:
            findings = _audit(data, pos_of=_pos_of)
        except Exception:
            findings = []
    STATS.record(
        ok=ok,
        is_new=is_new,
        native=bool(data.get('is_native')) if ok else False,
        quality=(data.get('quality') or '') if ok else '',
        sex=sex,
        first_en=first_en,
        last_en=last_en,
        given=data.get('given', '') if ok else '',
        hangul=data.get('full_hangul', '') if ok else '',
        flags=' '.join(sorted({c for c, _d in findings})),
        country=country,
        geo=geo,
        ms=ms,
        source=source,
    )
    # 사용자가 실패를 겪은 경우 보고(입력 실수는 제외). 원인별로 묶는다.
    if not ok:
        err = str(data.get('error', ''))
        if not any(h in err.lower() for h in _INPUT_ERR_HINTS):
            reason = ('transliteration' if 'sounds' in err.lower()
                      or 'dictionary' in err.lower() else 'conversion')
            report('user could not get a Korean name', level='error',
                   fingerprint=['conv-fail', reason], reason=reason,
                   name=f'{first_en} {last_en}'.strip(), sex=sex,
                   detail=err[:140])
        return

    _log_quality(first_en, last_en, data, findings)


def _log_quality(first_en, last_en, data, findings=None):
    """
    변환은 성공했지만 결과물이 이상한 경우를 기록·보고한다.

    실패는 예외가 나므로 저절로 잡히지만, 비문이나 빠진 의미설명은 아무 일도
    일어나지 않은 것처럼 지나간다. 여기서 그것을 눈에 보이게 만든다.

    저장은 stats.db(→ /admin 화면), 보고는 Sentry.
    'info' 등급(라벨형·폴백)은 정상 동작 범위라 DB에만 쌓고 Sentry로 보내지
    않는다. 알림이 흔해지면 아무도 보지 않게 되기 때문이다.
    """
    if _audit is None:
        return
    if findings is None:
        try:
            findings = _audit(data, pos_of=_pos_of)
        except Exception:
            return                  # 점검 자체가 서비스를 막아서는 안 된다

    name = f'{first_en} {last_en}'.strip()
    seen = set()
    for code, detail in findings:
        try:
            STATS.record_issue(code, data.get('given', ''), detail)
        except Exception:
            pass
        level = _Q_SEVERITY.get(code, 'warning')
        if level == 'info' or code in seen:
            continue
        seen.add(code)                  # 한 건에서 같은 코드는 한 번만 보고
        report(f'result quality: {code}', level=level,
               fingerprint=['quality', code], code=code,
               name=name, given=data.get('given', ''), detail=str(detail)[:140])


# ---------------------------------------------------------------- 유입 경로
# 어디서 왔는지 한 단어로 남긴다. 홍보 링크는 전부 ?ref=reddit-korean 처럼 꼬리표를 달아 뿌린다.
#  · ?ref= 또는 ?utm_source= 가 있으면 그 값
#  · 없으면 Referer 의 호스트만(경로·쿼리는 버린다 — 개인정보 최소화). 우리 사이트면 무시
#  · 첫 방문 값만 30일 쿠키에 남긴다(그 사람을 데려온 경로가 무엇인지가 궁금한 것이므로)
_SRC_RE = re.compile(r'[^A-Za-z0-9._:\-]+')
# Referer 의 스킴을 분리한다. http(s) 외에도 android-app:// 이 실제로 온다 —
# 안드로이드 앱(레딧·구글앱·카톡 등) 안에서 링크를 열면 Chrome/WebView 가
# 'Referer: android-app://com.reddit.frontpage/' 를 붙인다.
_SCHEME_RE = re.compile(r'^([A-Za-z][A-Za-z0-9+.\-]*)://(.*)$', re.S)
# 유입 경로 문자열 길이 상한. stats.conv.source 칼럼이 60자까지 받으므로 여기서도 60.
# (40자로 자르면 app:com.google.android.googlequicksearchbox 가 잘려 나간다)
_SRC_MAX = 60


def _split_referer(r):
    """Referer → (스킴, 호스트). 경로·쿼리는 버린다(개인정보 최소화).

    예전에는 '^https?://' 만 벗겨서, android-app://com.reddit.frontpage/ 가
    호스트 'android-app' 으로 뭉개졌다 — 어느 앱에서 왔는지가 그대로 날아갔다.
    """
    m = _SCHEME_RE.match(r or '')
    scheme, rest = (m.group(1).lower(), m.group(2)) if m else ('', r or '')
    host = rest.split('/')[0].split('?')[0].split('#')[0].split(':')[0].lower()
    return scheme, host


def _detect_source():
    ref = (request.args.get('ref') or request.args.get('utm_source') or '').strip()
    if ref:
        return _SRC_RE.sub('', ref)[:_SRC_MAX]
    r = request.headers.get('Referer', '')
    if r:
        scheme, host = _split_referer(r)
        if not host:
            return ''
        # 안드로이드 인앱 브라우저 — 호스트 자리에 앱 패키지명이 온다.
        # 우리 사이트와 비교할 대상이 아니므로 바로 돌려준다.
        if scheme == 'android-app':
            return _SRC_RE.sub('', 'app:' + host)[:_SRC_MAX]
        _, mine = _split_referer(_site_url())
        if host != mine and host != request.host.split(':')[0].lower():
            return _SRC_RE.sub('', 'ref:' + host)[:_SRC_MAX]
    return ''


def _source_cookie():
    return (request.cookies.get('src') or '')[:60]


def _remember_source(resp):
    """첫 방문이면 유입 경로를 쿠키에 남긴다(값이 없으면 아무것도 안 한다)."""
    if not request.cookies.get('src'):
        src = _detect_source()
        if src:
            resp.set_cookie('src', src, max_age=60 * 60 * 24 * 30, samesite='Lax')
    return resp


def _is_external_visit():
    """공유 링크를 눌러 들어온 조회인지 — Referer 가 우리 사이트가 아닐 때.

    판정을 _split_referer 로 통일한다. 따로 파싱하면 _detect_source 와 어긋나서
    '유입 경로는 잡혔는데 외부 방문으로는 안 세는' 식의 불일치가 생긴다.
    """
    r = request.headers.get('Referer', '')
    if not r:
        return True
    _, host = _split_referer(r)
    _, mine = _split_referer(_site_url())
    return host != request.host.split(':')[0].lower() and host != mine


@app.route('/')
def index():
    # 국적 드롭박스는 늘 'Optional' 로 시작한다. 예전엔 마지막에 고른 나라를 쿠키로 기억해
    # 기본값으로 넣었는데, 한 번 잘못 고르면 계속 그 나라가 먼저 떠서 뺐다.
    # ?first=&last=&g= 는 공유 링크(/n/...)가 아직 만들어진 적 없는 이름을 보낼 때
    # 입력란을 채워 두는 용도 — 사람이 Convert 를 눌러야 생성된다(GET 은 LLM 을 안 부른다).
    resp = make_response(render_template(
        'index.html', countries=_COUNTRIES,
        country='',
        og_image=(_site_url() + url_for('og_default', v=_OG_VERSION)) if _OG_OK else None,
        first_name=request.args.get('first', '')[:40],
        last_name=request.args.get('last', '')[:40],
        sex=_G_TO_SEX.get(request.args.get('g', ''), None)))
    return _remember_source(resp)


# ---------------------------------------------------------------- 결과의 고정 주소
# 변환 결과마다 주소를 준다: /n/<이름>/<성>?g=f|m|x  (g = 성별 선택)
# 규칙 — **GET 은 절대 LLM 을 부르지 않는다.** 사전·캐시로 바로 만들 수 있는 이름만 보여주고,
# 처음 보는 이름은 홈으로 보내되 입력란을 채워 둔다. 주소가 생기면 크롤러·봇이 무한히 두드릴 수
# 있는데, 그때 LLM 이 돌면 하루 예산이 봇에게 털린다.
# 흐름: 사람이 POST 로 변환(예산 1건) → 캐시에 남음 → 주소로 이동(PRG) → 링크를 받은 사람은 GET 으로 즉시(예산 0).
_G_TO_SEX = {'f': '여', 'm': '남', 'x': 'other'}
_SEX_TO_G = {'여': 'f', '남': 'm', 'other': 'x'}


def _share_path(first_en, last_en, sex):
    if not (last_en or '').strip():
        return url_for('name_page', first=first_en.strip(), g=_SEX_TO_G.get(sex, 'f'))
    return url_for('name_page', first=first_en.strip(), last=last_en.strip(),
                   g=_SEX_TO_G.get(sex, 'f'))


def _share_url(first_en, last_en, sex):
    return _site_url() + _share_path(first_en, last_en, sex)


def _render_result(data, first_en, last_en, sex):
    return render_template(
        'result.html', d=data,
        reason_json=json.dumps(data['reason'], ensure_ascii=False),
        share_url=_share_url(first_en, last_en, sex),
        og_image=_og_url(first_en, last_en, sex))


@app.route('/n/<first>')
@app.route('/n/<first>/<last>')
def name_page(first, last=''):
    first = first.strip()[:40]
    last = (last or '').strip()[:40]
    sex = _G_TO_SEX.get(request.args.get('g', 'f'), '여')
    if not RATE.check(_client_ip()):
        return render_template('index.html', error=_BUSY_RATE,
                               first_name=first, last_name=last, sex=sex,
                               countries=_COUNTRIES), 429
    if _needs_llm(first.lower(), last.lower(), sex):
        # 아직 만들어진 적 없는 이름 — 만들지 않고 홈으로(입력란만 채워서)
        return redirect(url_for('index', first=first, last=last,
                                g=_SEX_TO_G.get(sex, 'f')))
    data = convert_name(first, last, sex, allow_llm=False)
    if 'error' in data:
        return redirect(url_for('index', first=first, last=last,
                                g=_SEX_TO_G.get(sex, 'f')))
    resp = make_response(_render_result(data, first, last, sex))
    if _is_external_visit():
        # 변환 직후의 PRG 이동은 Referer 가 우리 사이트라 여기 안 걸린다.
        # 남는 것은 공유 링크를 눌러 들어온 사람 — 바이럴 루프가 도는지의 증거.
        STATS.record_event('view_share', _source_cookie() or _detect_source() or 'link',
                           data.get('full_hangul', ''))
    return _remember_source(resp)


# ---------------------------------------------------------------- 검색용 이름 페이지 /name/<first>
# "sophia in korean" 처럼 **이름만** 검색하는 사람을 위한 페이지. /n/ 과 다른 점:
#   · 색인된다(index). /n/ 은 개인 실명이 주소에 들어가므로 noindex 다.
#   · 사전에 있는 이름만 만든다 — LLM 호출 0, 비용 0. 없는 이름은 홈으로.
#   · 레이트리밋을 걸지 않는다. 크롤러가 2,000장을 훑는 것이 목적이다.
#   · 성(last name)이 없으므로, 성까지 넣어 전체 이름을 만들라는 안내를 넣는다.
# 주소는 소문자 이름 하나. 남·여 둘 다 사전에 있는 63개는 ?g=m 으로 남자 이름판.
_NAME_PAGE_CACHE_S = 24 * 3600


def _name_page_sexes(key):
    """사전에서 이 이름이 실려 있는 성별 목록. 여 → 남 순(사전 비율 1480:588)."""
    out = []
    if key in NAME_TO_TRANSLIT['female']:
        out.append('여')
    if key in NAME_TO_TRANSLIT['male']:
        out.append('남')
    return out


def _name_page_url(key, sex, sexes):
    """정규 주소. 사전의 첫 성별이면 ?g 없이, 둘째면 ?g=m/f 를 붙인다."""
    u = f'{_site_url()}/name/{key}'
    if sexes and sex != sexes[0]:
        u += f'?g={_SEX_TO_G.get(sex, "f")}'
    return u


def _name_page_siblings(key, sex, n=8):
    """같은 첫 글자·같은 성별의 이웃 이름들 — 내부 링크(크롤러가 타고 다닌다)."""
    pool = NAME_TO_TRANSLIT['female' if sex == '여' else 'male']
    same = sorted(k for k in pool if k[:1] == key[:1] and k != key)
    if not same:
        return []
    # 알파벳 순에서 이 이름 주변을 잘라 낸다 — 늘 같은 8개가 나오게(캐시·색인 안정)
    i = sum(1 for k in same if k < key)
    lo = max(0, min(i - n // 2, len(same) - n))
    return [(k.title(), _name_page_url(k, sex, _name_page_sexes(k))) for k in same[lo:lo + n]]


@app.route('/name/<first>')
def seo_name_page(first):
    key = first.strip().lower()[:40]
    sexes = _name_page_sexes(key)
    if not sexes:
        # 사전 밖 이름 — 만들지 않는다(비용). 입력란만 채워 홈으로.
        return redirect(url_for('index', first=first.strip()[:40]))
    if key != first:                                   # 대문자·공백 → 정규 주소로
        return redirect(url_for('seo_name_page', first=key, **request.args), 301)
    want = _G_TO_SEX.get(request.args.get('g', ''), None)
    sex = want if want in sexes else sexes[0]
    if want and want not in sexes:                     # 없는 성별판 → 있는 판으로
        return redirect(_name_page_url(key, sex, sexes), 301)

    data = convert_name(key.title(), '', sex, allow_llm=False)
    if 'error' in data:
        return redirect(url_for('index', first=key.title()))

    other = None
    for s in sexes:
        if s != sex:
            other = {'sex': s, 'url': _name_page_url(key, s, sexes),
                     'label': "a boy's name" if s == '남' else "a girl's name"}
    seo = {
        'canonical': _name_page_url(key, sex, sexes),
        'title': (f'{data["first_en"]} in Korean: {data["given"]} ({data["given_rom"]}) '
                  f'— meaning, hanja and pronunciation'),
        'description': (f'{data["first_en"]} as a Korean name is {data["given"]} '
                        f'({data["given_rom"]})'
                        + (f' — “{data["meaning_short"]}”' if data.get('meaning_short') else '')
                        + '. See the hanja, what each syllable means, how to say it, and why '
                        'these sounds were chosen. Add your last name for the full Korean name.'),
        'other': other,
        'siblings': _name_page_siblings(key, sex),
        # 성까지 넣어 전체 이름을 만드는 입력 화면. focus=last 는 커서를 성 칸에 둔다.
        'cta_url': (f'{_site_url()}/?first={quote(data["first_en"])}'
                    f'&g={_SEX_TO_G.get(sex, "f")}&focus=last&ref=name-page'),
    }
    html = render_template(
        'result.html', d=data, seo=seo,
        reason_json=json.dumps(data['reason'], ensure_ascii=False),
        share_url=_share_url(key.title(), '', sex),
        og_image=_og_url(key.title(), '', sex))
    resp = make_response(html)
    resp.headers['Cache-Control'] = f'public, max-age={_NAME_PAGE_CACHE_S}'
    if _is_external_visit():
        STATS.record_event('view_name_page', _detect_source() or 'direct', data.get('given', ''))
    return resp


# ---------------------------------------------------------------- 영상 촬영용 자동 시연 /demo
# 틱톡·릴스 영상을 찍을 때 앱을 **사람 대신 일정한 박자로** 조작한다. 폰에서 이 주소를
# 열고 화면 녹화를 켠 뒤 화면을 한 번 누르면, 이름 입력(타이핑) → 카드 → 뒤집기 →
# 앞면 → 발음 재생 → 다음 이름 … 을 정해진 시간 간격으로 진행한다. 결과는 진짜
# 앱 화면(같은 origin 의 iframe)이라 폰트·카드·소리가 실제와 같다.
#   /demo?names=Emma,Liam,Olivia          이름 세 개, 여자 이름 기본
#   /demo?names=Emma:f,Liam:m,Olivia:f    이름별 성별
#   &hold=3 &back=3 &type=90 &say=1       앞면 초 · 뒷면 초 · 글자당 ms · 발음 재생
#   &intro=What's your name in Korean?    첫 장면 문구(2초) · &outro=... 마지막 문구
# 사전 이름만 쓴다(GET 은 LLM 을 안 부르므로, 사전 밖 이름은 홈으로 튕겨 시연이 멈춘다).
# 검색에 걸릴 이유가 없어 noindex + robots Disallow.
@app.route('/demo')
def demo_page():
    raw = (request.args.get('names') or 'Emma,Liam,Olivia')[:400]
    names = []
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        name, _, g = part.partition(':')
        name = re.sub(r"[^A-Za-z'\- ]", '', name).strip()[:40]
        if not name:
            continue
        g = (g or request.args.get('g') or 'f').strip().lower()[:1]
        if g not in ('f', 'm', 'x'):
            g = 'f'
        names.append({'name': name, 'g': g})
    names = names[:10]

    def _num(key, default, lo, hi):
        try:
            v = float(request.args.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    cfg = {
        'names': names,
        'hold': _num('hold', 3.0, 0.5, 15),        # 앞면을 보여주는 초
        'back': _num('back', 3.0, 0, 15),          # 뒷면을 보여주는 초 (0 이면 안 뒤집음)
        'type_ms': int(_num('type', 90, 20, 400)), # 글자당 타이핑 ms
        'say': request.args.get('say', '1') != '0',
        'gap': _num('gap', 0.6, 0, 20),            # 이름 사이 쉬는 초 (자막을 얹을 때는 길게)
        'reason': _num('reason', 0, 0, 10),        # 변환 이유 카드를 보여주는 초(2단 각각). 0 이면 생략
        'intro': (request.args.get('intro') or '')[:80],
        'outro': (request.args.get('outro') or '')[:80],
        'card_s': _num('card', 2.0, 0.5, 6),       # 인트로·아웃트로 문구 초
        # 입력 화면에서 상단 소개(hero·intro)를 숨기고 입력 카드만 가운데 보여준다.
        'clean': request.args.get('clean', '1') != '0',
        # 소리를 낼 수 없는 환경(자동 녹화기)에서 발음 자리를 비워 두는 초. 이름 순서대로
        # 쉼표 목록. 나중에 그 자리에 mp3 를 얹는다(tools/make_demo_audio.py 참고).
        'saydur': [max(0.0, min(8.0, float(x))) for x in
                   (request.args.get('saydur') or '').split(',') if x.strip().replace('.', '', 1).isdigit()],
    }
    resp = make_response(render_template('demo.html', cfg=cfg, cfg_json=json.dumps(cfg, ensure_ascii=False)))
    resp.headers['Cache-Control'] = 'no-store'
    return resp


# ---------------------------------------------------------------- OG 공유 카드 이미지
# 링크를 카톡·트위터 등에 붙이면 그 앱이 og:image 를 받아 카드로 보여준다.
# 규칙은 /n/ 과 같다 — 캐시로 만들 수 있는 이름만 그린다(LLM 비용 0), 모르는 이름은 404.
# 한 이름당 한 번만 그려 CACHE_DIR/og/ 에 두고 다음부터는 파일을 그대로 보낸다.
try:
    from og_card import render_result_card as _og_render, render_default_card as _og_default, \
        CARD_VERSION as _OG_VERSION
    _OG_OK = True
except Exception as _oe:            # Pillow/numpy 없음 등 — 이미지 없이 텍스트 태그만 나간다
    _OG_OK = False
    print(f'[og] disabled: {_oe}', file=sys.stderr, flush=True)
OG_DIR = os.path.join(CACHE_DIR, 'og')
_OG_LOCK = threading.Lock()


def _og_path(first, last, sex):
    key = f'{first.strip().lower()}|{last.strip().lower()}|{_SEX_TO_G.get(sex, "f")}|v{_OG_VERSION}'
    return os.path.join(OG_DIR, hashlib.md5(key.encode('utf-8')).hexdigest() + '.jpg')


def _og_url(first, last, sex):
    if not _OG_OK:
        return None
    if not (last or '').strip():
        return _site_url() + url_for('og_image', first=first.strip(),
                                     g=_SEX_TO_G.get(sex, 'f'), v=_OG_VERSION)
    return _site_url() + url_for('og_image', first=first.strip(), last=last.strip(),
                                 g=_SEX_TO_G.get(sex, 'f'), v=_OG_VERSION)


def _send_og(path):
    resp = make_response(send_file(path, mimetype='image/jpeg', conditional=True))
    resp.headers['Cache-Control'] = 'public, max-age=604800'      # 1주. 디자인이 바뀌면 v= 가 바뀐다
    return resp


@app.route('/og/<first>.jpg')
@app.route('/og/<first>/<last>.jpg')
def og_image(first, last=''):
    if not _OG_OK:
        abort(404)
    first = first.strip()[:40]
    last = (last or '').strip()[:40]
    sex = _G_TO_SEX.get(request.args.get('g', 'f'), '여')
    path = _og_path(first, last, sex)
    if os.path.exists(path):
        return _send_og(path)
    if not RATE.check(_client_ip()):
        abort(429)
    if _needs_llm(first.lower(), last.lower(), sex):
        abort(404)                      # 만들어진 적 없는 이름 — 봇이 두드려도 비용 0
    data = convert_name(first, last, sex, allow_llm=False)
    if 'error' in data:
        abort(404)
    with _OG_LOCK:                      # 같은 카드를 동시에 두 번 그리지 않는다
        if not os.path.exists(path):
            try:
                _og_render({
                    'input': data['input'],
                    'syllables': [x['ch'] for x in data['syllables']],
                    'full_rom': data['full_rom'],
                    'meaning_short': data.get('meaning_short') or '',
                    'surname_hanja': data.get('surname_hanja') or '',
                }, path)
            except Exception as e:
                report('og card render failed', level='warning',
                       fingerprint=['og-render', type(e).__name__], error=str(e)[:200])
                abort(404)
    return _send_og(path)


@app.route('/og/default.jpg')
def og_default():
    if not _OG_OK:
        abort(404)
    path = os.path.join(OG_DIR, f'default-v{_OG_VERSION}.jpg')
    if not os.path.exists(path):
        with _OG_LOCK:
            if not os.path.exists(path):
                try:
                    _og_default(path)
                except Exception as e:
                    report('og default render failed', level='warning',
                           fingerprint=['og-render', type(e).__name__], error=str(e)[:200])
                    abort(404)
    return _send_og(path)


@app.route('/result', methods=['GET', 'POST'])
def result():
    if request.method == 'GET':
        return redirect(url_for('index'))
    first_en = request.form.get('first_name', '')
    last_en = request.form.get('last_name', '')
    sex = request.form.get('sex', '여')

    # ① 속도 제한(연타·봇 차단)
    if not RATE.check(_client_ip()):
        report('user hit rate limit', level='warning',
               fingerprint=['rate-limit'])
        return render_template('index.html', error=_BUSY_RATE,
                               first_name=first_en, last_name=last_en, sex=sex), 429
    # ② 하루 예산: 새 이름인데 한도를 넘었으면 생성하지 않고 안내
    is_new = _needs_llm(first_en.strip().lower(), last_en.strip().lower(), sex)
    if is_new and not BUDGET.allow():
        _report_budget_exhausted()
        return render_template('index.html', error=_BUSY_BUDGET,
                               first_name=first_en, last_name=last_en, sex=sex), 503

    _t0 = _time.time()
    data = convert_name(first_en, last_en, sex)
    _picked, _geo = _country_of_request()
    _log_conv(first_en, last_en, sex, is_new, data, _picked, _geo,
              ms=(_time.time() - _t0) * 1000)
    if 'error' in data:
        return render_template('index.html', error=data['error'],
                               first_name=first_en, last_name=last_en, sex=sex,
                               countries=_COUNTRIES, country=_picked)
    if is_new:
        BUDGET.record()          # 새 이름 1건 소비 기록
    # POST 뒤 고정 주소로 이동(PRG). 주소창의 주소가 곧 공유 링크가 되고, 새로고침해도
    # 재변환되지 않는다. 단, 음차가 규칙 폴백으로 나와 캐시에 남지 않은 경우(LLM 실패)는
    # GET 이 다시 만들 수 없으므로 예전처럼 여기서 바로 그린다.
    if ('/' not in first_en + last_en
            and not _needs_llm(first_en.strip().lower(), last_en.strip().lower(), sex)):
        resp = redirect(_share_path(first_en, last_en, sex), code=303)
    else:
        resp = make_response(_render_result(data, first_en, last_en, sex))
    if request.cookies.get('country'):
        # 예전에 심어 둔 국적 쿠키는 더 쓰지 않으므로 지운다
        resp.delete_cookie('country', samesite='Lax')
    return resp


@app.route('/api/event', methods=['POST'])
def api_event():
    """공유·링크복사·저장 클릭을 남긴다. 결과 페이지의 버튼이 sendBeacon 으로 보낸다.
    개인정보 없음 — 종류와 한국 이름, 유입 경로 쿠키뿐."""
    payload = request.get_json(silent=True) or {}
    kind = str(payload.get('kind', ''))[:20]
    if kind not in ('share', 'copy', 'download'):
        return ('', 204)
    STATS.record_event(kind, _source_cookie(), str(payload.get('name', ''))[:20])
    return ('', 204)


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
        # /result 와 같은 사건이므로 같은 함수로 보고한다. 한쪽만 보고하면
        # 어느 경로로 들어왔는지에 따라 보일 때와 안 보일 때가 갈린다.
        _report_budget_exhausted()
        return jsonify({'error': 'busy',
                        'message': 'High traffic right now — try again later '
                                   'or use a more common name.'}), 503
    _t0 = _time.time()
    data = convert_name(first_en, last_en, sex)
    _api_picked, _api_geo = _country_of_request()
    _log_conv(first_en, last_en, sex, is_new, data, _api_picked, _api_geo,
              ms=(_time.time() - _t0) * 1000)
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
        report('TTS daily budget exhausted', level='warning',
               fingerprint=['tts', 'budget'])
        return jsonify({'error': 'busy'}), 503
    url = TTS_FULL.url_for(name)
    if not url:
        # available=False 면 구글 미설정(브라우저 음성으로 대체됨) — 설정 문제로 묶음.
        # available=True 인데 실패면 tts_full 이 이미 synth-error 로 보고함.
        if not TTS_FULL.available:
            report('server TTS not configured — user got browser robot voice',
                   level='warning', fingerprint=['tts', 'not-configured'])
        return jsonify({'error': 'unavailable'}), 503
    TTS_BUDGET.record()
    return jsonify({'url': url})


@app.route('/health')
def health():
    return jsonify({'status': 'ok'})


# ---------------------------------------------------------------- 공개 사이트
# 정식 주소. 커스텀 도메인을 붙이면 SITE_URL 로 알려 준다.
# 없으면 요청이 들어온 주소를 그대로 쓴다 — 잘못된 주소를 검색엔진에
# 알려주는 것보다 안전하다.
def _site_url():
    return (os.environ.get('SITE_URL') or request.url_root).rstrip('/')


@app.context_processor
def _inject_site():
    """템플릿에서 {{ site_url }}·{{ site_host }} 로 쓴다(canonical·OG 태그·카드의 사이트명).
    도메인이 바뀌면 SITE_URL 환경변수 하나만 바꾸면 된다."""
    try:
        url = _site_url()
        host = re.sub(r'^https?://', '', url).split('/')[0]
        return {'site_url': url, 'site_host': host}
    except Exception:
        return {'site_url': '', 'site_host': ''}


@app.route('/robots.txt')
def robots():
    # /admin·/status·/diag 는 검색에 걸릴 이유가 없다(토큰으로 막혀 있지만
    # 주소가 색인되는 것 자체가 불필요한 노출이다).
    body = ('User-agent: *\n'
            'Allow: /\n'
            'Disallow: /admin\n'
            'Disallow: /status\n'
            'Disallow: /diag\n'
            'Disallow: /api/\n'
            'Disallow: /demo\n'
            f'Sitemap: {_site_url()}/sitemap.xml\n')
    return make_response(body, 200, {'Content-Type': 'text/plain'})


_SITEMAP_CACHE = {'body': None, 'site': None}


def _sitemap_body():
    """홈 + 검색용 이름 페이지 전부. 사전 이름 2,005개, 남녀 둘 다 있는 63개는
    두 장이라 2,068개 URL. 한도(50,000)에 한참 못 미친다.

    결과 페이지(/result·/n/)는 넣지 않는다 — POST 로만 열리거나 noindex 다.
    사전은 프로세스 수명 동안 바뀌지 않으므로 한 번 만들어 둔다."""
    site = _site_url()
    if _SITEMAP_CACHE['body'] and _SITEMAP_CACHE['site'] == site:
        return _SITEMAP_CACHE['body']
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
             f'  <url><loc>{site}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>']
    keys = sorted(set(NAME_TO_TRANSLIT['female']) | set(NAME_TO_TRANSLIT['male']))
    for key in keys:
        if not re.fullmatch(r"[a-z][a-z'\-]*", key):      # 주소에 못 넣는 키는 건너뛴다
            continue
        sexes = _name_page_sexes(key)
        for sex in sexes:
            loc = _name_page_url(key, sex, sexes).replace('&', '&amp;')
            lines.append(f'  <url><loc>{loc}</loc><changefreq>monthly</changefreq>'
                         f'<priority>0.6</priority></url>')
    lines.append('</urlset>')
    body = '\n'.join(lines) + '\n'
    _SITEMAP_CACHE.update(body=body, site=site)
    return body


@app.route('/sitemap.xml')
def sitemap():
    resp = make_response(_sitemap_body(), 200, {'Content-Type': 'application/xml'})
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


# 기본 오류 화면은 흰 배경에 영어 한 줄이라 고장난 사이트처럼 보인다.
# 공개 사이트에서는 최소한 돌아갈 길을 준다.
_ERR_PAGE = ('<!doctype html><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1">'
             '<title>{title}</title>'
             '<style>body{{margin:0;min-height:100vh;display:flex;'
             'align-items:center;justify-content:center;background:#F7F5F0;'
             'color:#2B2A26;font:16px/1.6 -apple-system,BlinkMacSystemFont,'
             '"Segoe UI",sans-serif;text-align:center;padding:24px}}'
             'a{{color:#3F6F5F}}h1{{font-size:20px;margin:0 0 8px}}'
             'p{{margin:0 0 16px;color:#6B6A63}}</style>'
             '<div><h1>{title}</h1><p>{msg}</p>'
             '<a href="/">Find your Korean name &rarr;</a></div>')


@app.errorhandler(404)
def _e404(_e):
    return _ERR_PAGE.format(
        title='Page not found',
        msg='That page doesn&rsquo;t exist.'), 404


@app.errorhandler(500)
def _e500(_e):
    # 여기서 또 예외가 나면 사용자가 아무것도 못 본다 — 문자열만 쓴다.
    return _ERR_PAGE.format(
        title='Something went wrong',
        msg='We couldn&rsquo;t finish that. Please try again.'), 500


_CACHE_FILES = ('stats.db', 'translit_cache.json', 'meaning_cache.json',
                'meaning_en_cache.json', 'daily_budget.json', 'tts_budget.json')


def _cache_status():
    """
    캐시가 실제로 어디에 쓰이고 있는지.

    CACHE_DIR 을 줬는데도 그 경로를 쓸 수 없으면 app.py 는 조용히 앱 폴더로
    되돌아간다. 앱은 평소처럼 잘 돌아가고 에러도 없다. 그래서 몇 주 뒤
    재배포하는 날 통계가 통째로 사라진 것으로 알게 된다.
    fallback=true 가 그 상태다 — 디스크를 붙였는데 안 쓰이고 있다는 뜻.
    """
    files = {}
    for n in _CACHE_FILES:
        p = os.path.join(CACHE_DIR, n)
        try:
            if os.path.exists(p):
                files[n] = os.path.getsize(p)
        except Exception:
            pass
    return {
        'dir': CACHE_DIR,
        'fallback': bool(os.environ.get('CACHE_DIR')) and CACHE_DIR == BASE,
        'seeded': CACHE_SEEDED or None,   # 이번 부팅에 옮긴 캐시
        'stats_ok': STATS.ok,
        'files': files,
    }


# ---------------------------------------------------------------- 부하 감시
# "사람이 몰려서 서버를 키워야 하는가"를 숫자로 알기 위한 것. /status 의 load 항목.
#
#  · 동시 처리 중인 변환 요청 수를 워커(프로세스)마다 센다. 창구 수(스레드 수, WEB_THREADS,
#    기본 4)에 닿으면 stats 의 load 표에 한 줄 남긴다 → 최근 1시간에 몇 번 꽉 찼는지 셀 수 있다.
#  · 변환마다 걸린 시간은 conv.ms 에 남는다(캐시 이름 / 새 이름 따로 집계).
#  · 메모리는 이 워커의 RSS. 워커가 둘이면 대략 두 배로 보면 된다(--preload 라 공유분이 있어 그보다 적다).
#
# 판정은 _load_alerts() — /status 가 호출될 때(3시간마다 도는 점검 작업이 부른다) 기준을 넘긴
# 항목을 Sentry 로 보고한다. 날짜를 지문에 넣어 하루에 한 번씩 메일이 온다.
try:
    _WEB_THREADS = max(1, int(os.environ.get('WEB_THREADS', 4)))
except Exception:
    _WEB_THREADS = 4
try:
    _RSS_LIMIT_MB = int(os.environ.get('RSS_LIMIT_MB', 512))     # Starter = 512MB
except Exception:
    _RSS_LIMIT_MB = 512
_INFLIGHT = 0
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT_LAST_LOG = 0.0
_CONVERT_ENDPOINTS = ('result', 'api_convert')


def _rss_mb():
    """이 프로세스가 쓰는 메모리(MB). Linux 는 /proc, 그 외는 resource."""
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    try:
        import resource
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(kb // 1024) if sys.platform != 'darwin' else int(kb // (1024 * 1024))
    except Exception:
        return None


@app.before_request
def _inflight_enter():
    global _INFLIGHT, _INFLIGHT_LAST_LOG
    if request.endpoint not in _CONVERT_ENDPOINTS or request.method != 'POST':
        return
    with _INFLIGHT_LOCK:
        _INFLIGHT += 1
        n = _INFLIGHT
        now = _time.time()
        # 창구가 다 찼다 — 10초에 한 번만 기록(몰릴 때 DB 를 두드리지 않도록)
        if n >= _WEB_THREADS and now - _INFLIGHT_LAST_LOG >= 10:
            _INFLIGHT_LAST_LOG = now
            log_it = True
        else:
            log_it = False
    if log_it:
        STATS.record_load(os.getpid(), n)


@app.teardown_request
def _inflight_leave(_exc=None):
    global _INFLIGHT
    if request.endpoint not in _CONVERT_ENDPOINTS or request.method != 'POST':
        return
    with _INFLIGHT_LOCK:
        _INFLIGHT = max(0, _INFLIGHT - 1)


def _load_status():
    d = STATS.load_summary(hours=1)
    d.update({
        'inflight_now': _INFLIGHT,
        'threads_per_worker': _WEB_THREADS,
        'rss_mb': _rss_mb(),
        'rss_limit_mb': _RSS_LIMIT_MB,
        'pid': os.getpid(),
    })
    return d


# 기준값 — 넘으면 알린다. 환경변수로 조정 가능.
_LOAD_SLOW_MS = int(os.environ.get('LOAD_SLOW_MS', 2000))          # 캐시 이름 p95
_LOAD_RSS_MB = int(os.environ.get('LOAD_RSS_MB', 400))             # 워커 메모리
_LOAD_SATURATION = int(os.environ.get('LOAD_SATURATION', 3))       # 1시간에 창구 꽉 찬 횟수
_LOAD_TRAFFIC = int(os.environ.get('LOAD_TRAFFIC', 100))           # 1시간 변환 건수


def _load_alerts(d):
    """기준을 넘긴 항목을 Sentry 로 보고하고, 사람이 읽을 문장 목록을 돌려준다."""
    day = BUDGET.today()
    alerts = []
    cm = d.get('cached_ms') or {}
    if cm.get('n', 0) >= 5 and cm.get('p95', 0) > _LOAD_SLOW_MS:
        msg = (f"서버가 밀립니다 — 캐시 이름 응답 p95 {cm['p95']}ms (최근 1시간 {cm['n']}건, 기준 {_LOAD_SLOW_MS}ms). "
               f"Render Start Command 의 -w 를 올리거나(예: -w 3) Instance Type 을 Standard 로 올리세요.")
        alerts.append(msg)
        report(msg, level='warning', fingerprint=['load-slow', day])
    rss = d.get('rss_mb')
    if rss is not None and rss > _LOAD_RSS_MB:
        msg = (f"메모리가 찹니다 — 워커 {rss}MB / 한도 {_RSS_LIMIT_MB}MB (기준 {_LOAD_RSS_MB}MB). "
               f"Render Instance Type 을 Standard(2GB)로 올리세요.")
        alerts.append(msg)
        report(msg, level='warning', fingerprint=['load-memory', day])
    if d.get('saturation_events', 0) >= _LOAD_SATURATION:
        msg = (f"창구가 모자랍니다 — 최근 1시간에 동시 처리가 창구 수({_WEB_THREADS})에 {d['saturation_events']}번 닿았습니다. "
               f"Render Start Command 의 -w(워커 수)를 올리세요.")
        alerts.append(msg)
        report(msg, level='warning', fingerprint=['load-saturated', day])
    if d.get('conv', 0) >= _LOAD_TRAFFIC:
        msg = (f"사람이 몰리고 있습니다 — 최근 1시간 변환 {d['conv']}건(새 이름 {d.get('conv_new', 0)}건). "
               f"예산 잔여 {BUDGET.status()[1] - BUDGET.status()[0]}건.")
        alerts.append(msg)
        report(msg, level='info', fingerprint=['load-traffic', day])
    return alerts


def _report_budget_exhausted():
    """
    하루 한도 소진을 보고한다. **fingerprint 에 날짜를 넣는다.**

    Sentry 는 fingerprint 로 사건을 묶는다. 날짜가 없으면 모든 날의 소진이
    하나의 이슈로 합쳐져서, 처음 소진된 날에만 알림이 오고 그 뒤로는
    조용해진다. 날짜를 넣으면 소진되는 날마다 새 이슈가 되어 그날 한 번
    알림이 온다(같은 날 두 번째부터는 같은 이슈로 묶여 조용하다).

    즉 '소진되는 날마다 한 번' — 매일 울리지도, 첫날만 울리지도 않는다.
    """
    used, cap = BUDGET.status()
    report(f'daily new-name budget exhausted — {used}/{cap} '
           f'(new names are being turned away until midnight)',
           level='warning',
           fingerprint=['budget-exhausted', BUDGET.today()])


def _budget_status():
    """
    하루 상한 소진 상태.

    상한에 닿으면 **새 이름 변환이 멈추고** 사용자에게 "잠시 후 다시" 안내가
    나간다. 사이트는 정상으로 보이고 /status 의 ok 도 true 다. 그래서
    소진된 것을 모른 채 하루가 지나갈 수 있다 — 상한을 낮게 잡을수록 그렇다.
    심층 점검(deep)은 고정된 이름을 쓰고 예산을 거치지 않으므로 이것도
    잡지 못한다. 그래서 값을 따로 내보내 모니터가 보게 한다.
    """
    def one(b):
        try:
            used, cap = b.status()
        except Exception:
            return None
        return {
            'used': used,
            'max': cap,
            'remaining': (max(cap - used, 0) if cap > 0 else None),
            'exhausted': bool(cap > 0 and used >= cap),
        }
    return {'new_names': one(BUDGET), 'tts': one(TTS_BUDGET)}


def _status_token_ok(tok):
    """심층 점검은 STATUS_TOKEN 이 일치할 때만 허용(크레딧 남용 방지)."""
    want = os.environ.get('STATUS_TOKEN')
    return bool(want) and tok == want


@app.route('/status')
def status():
    """
    가벼운 상태 점검용 JSON. 정기 모니터링이 주기적으로 호출한다.
      - 기본(누구나): 살아있는지 + 핵심 설정 여부 + 업타임.
      - ?deep=1&token=... : 사전 밖 이름을 실제로 변환해 파이프라인 전체
        (LLM 음차 포함)가 정상인지까지 확인한다. 실패하면 ok=false, 503.
        고정된 테스트 이름이라 첫 1회만 LLM을 쓰고 이후는 캐시로 처리된다.
    """
    body = {
        'ok': True,
        'llm': bool(TRANSLIT.llm_available),   # ANTHROPIC_API_KEY 설정 여부
        'sentry': _SENTRY_ON,
        'tts': {
            'configured': bool(TTS_FULL.available),   # GOOGLE_APPLICATION_CREDENTIALS 여부
            'voice': TTS_FULL.voice,
            # Gemini 가 실패했을 때 쓰는 예비 목소리. 이름 형식이 달라
            # 같은 값을 쓸 수 없다(tts_full.CHIRP_VOICE 주석 참고).
            'voice_chirp': getattr(TTS_FULL, 'chirp_voice', None),
            'model': TTS_FULL.model,
            'last_mode': TTS_FULL.last_mode,          # 최근 실제 사용 엔진(호출 후 채워짐)
            'last_error': getattr(TTS_FULL, 'last_error', None),
        },
        # 의미 설명 생성기 상태. 이게 죽어 있으면 사전 밖 이름의 설명이
        # 최후 템플릿으로 떨어져 영어 문장 품질이 급락한다.
        'meaning': {
            'llm': MEANING_EN is not None,
            'last_error': getattr(MEANING_EN, 'last_error', None),
            # 템플릿 폴백이 왜 났는지. 유형별 최종 실패 건수 — 이것이 없으면
            # '템플릿이 나왔다'는 사실만 알고 어디를 손댈지 정할 수 없다.
            #   transient = 과부하·레이트리밋 → 재시도 대기를 늘린다
            #   timeout   = 응답 지연        → meaning_en.GEN_TIMEOUT 을 올린다
            'fail_kinds': getattr(MEANING_EN, 'fail_kinds', None) or {},
            # 재시도가 값을 하고 있는가. recovered 가 0 이 아니면 그만큼의
            # 템플릿 폴백을 재시도가 막아낸 것이다.
            'retries': getattr(MEANING_EN, 'retry_count', 0),
            'retry_recovered': getattr(MEANING_EN, 'retry_recovered', 0),
            # 있으면 캐시가 파일에 안 써지고 있다 — 매 요청이 재생성된다
            'cache_write_error': getattr(MEANING_EN, 'cache_write_error', None),
            # 프롬프트 판번호가 올라가 한국어 설명 캐시를 버린 건수.
            # 0 이 아니면 그만큼 다시 생성된다(한 번만 일어난다).
            'cache_discarded': getattr(MEANING, 'cache_version_discarded', None),
            # 판번호가 없던 예전 캐시를 v1 으로 받아들인 건수(이전 한 번만).
            'cache_legacy': getattr(MEANING, 'cache_legacy_adopted', None),
        },
        # 지금 돌고 있는 코드가 어느 커밋인지. 배포가 반영됐는지 확인할 때
        # 업타임만으로는 부족하다(재시작만 해도 0으로 돌아간다).
        # RENDER_GIT_COMMIT / RENDER_GIT_BRANCH 는 Render 가 런타임에 넣어준다.
        'build': {
            'commit': (os.environ.get('RENDER_GIT_COMMIT') or '')[:7] or None,
            'branch': os.environ.get('RENDER_GIT_BRANCH') or None,
            'prompt_version': _PROMPT_VERSION,
        },
        'cache': _cache_status(),
        'budget': _budget_status(),
        'load': _load_status(),
        'uptime_s': int(_time.time() - _BOOT_TS),
        'ts': int(_time.time()),
    }
    if request.args.get('deep'):
        if not _status_token_ok(request.args.get('token', '')):
            body['deep'] = {'ok': None, 'skipped': 'unauthorized'}
        else:
            t0 = _time.time()
            # 기본은 고정 이름이라 첫 1회 뒤로는 캐시로 처리된다(무료).
            # name=·last= 로 처음 보는 이름을 넣으면 캐시를 타지 않으므로
            # 의미 생성 경로가 실제로 살아 있는지 확인할 수 있다.
            fn = (request.args.get('name') or 'Thessaly').strip()[:40]
            ln = (request.args.get('last') or 'Brzezinski').strip()[:40]
            try:
                d = convert_name(fn, ln, '여')
                deep_ok = ('error' not in d) and bool(d.get('full_hangul'))
                _me = d.get('meaning_en') or ''
                body['deep'] = {
                    'ok': deep_ok,
                    'latency_ms': int((_time.time() - t0) * 1000),
                    'input': f'{fn} {ln}',
                    'sample': d.get('full_hangul') if deep_ok else None,
                    'error': (d.get('error') if not deep_ok else None),
                    # fallback=true 면 LLM 설명 생성에 실패해 최후 템플릿을 쓴 것.
                    # 이 경우 영어 문장에 문법 오류가 섞일 수 있다.
                    'meaning': {
                        'fallback': bool(d.get('meaning_error')),
                        'why': d.get('meaning_error'),
                        'chars': len(_me),
                        'text': _me[:240],
                    },
                }
                if not deep_ok:
                    body['ok'] = False
            except Exception as e:
                body['ok'] = False
                body['deep'] = {'ok': False,
                                'error': f'{type(e).__name__}: {e}'}
    # 부하 판정 — 기준을 넘긴 항목은 Sentry 로 보고(하루 한 번)하고 응답에도 싣는다
    try:
        body['load']['alerts'] = _load_alerts(body['load'])
    except Exception as e:
        body['load']['alerts'] = [f'판정 실패: {type(e).__name__}: {e}']
    return jsonify(body), (200 if body['ok'] else 503)


def _review_status():
    """출력 전 검수기(meaning_review) 상태. 없으면 '꺼짐' 으로."""
    rv = getattr(MEANING_EN, 'reviewer', None) if MEANING_EN is not None else None
    if rv is None:
        return {'enabled': False, 'model': '(off)', 'disabled_reason': None,
                'last_error': None, 'calls': 0, 'edits': 0, 'consecutive_failures': 0}
    try:
        return rv.status()
    except Exception:
        return {'enabled': False, 'model': '?', 'disabled_reason': 'status() 실패',
                'last_error': None, 'calls': 0, 'edits': 0, 'consecutive_failures': 0}


def _admin_authed():
    want = os.environ.get('ADMIN_TOKEN')
    tok = request.args.get('token', '') or request.cookies.get('admin_auth', '')
    return bool(want and tok == want)


@app.route('/admin/reviews')
def admin_reviews():
    """
    출력 전 검수 기록 — 검수기가 무엇을 고쳤는지 전후 텍스트로 본다.
    캐시(meaning_en_cache.json)에 남은 review 요약을 최근 순으로 보여준다.
    검수 프롬프트를 조정할지 판단하는 근거가 여기 있다.
    """
    if not _admin_authed():
        return ('Not found', 404)
    rows = []
    cache = getattr(MEANING_EN, '_cache', {}) if MEANING_EN is not None else {}
    for key, ent in list(cache.items()):
        if not isinstance(ent, dict) or not isinstance(ent.get('review'), dict):
            continue
        rv = ent['review']
        if rv.get('status') == 'ok':
            continue                      # 문제없음은 굳이 나열하지 않는다
        rows.append({
            'key': key, 'status': rv.get('status'), 'ts': rv.get('ts') or 0,
            'issues': rv.get('issues') or [], 'reason': rv.get('reason'),
            'before': rv.get('orig'), 'after': ent.get('text'),
            'before_short': rv.get('orig_short'), 'after_short': ent.get('short'),
        })
    rows.sort(key=lambda r: -r['ts'])
    rows = rows[:200]
    st = _review_status()
    from stats import _fmt as _stats_fmt
    for r in rows:
        r['when'] = _stats_fmt(r['ts'], '%Y-%m-%d %H:%M') if r['ts'] else ''
    import html as _html
    e = _html.escape
    color = {'edited': '#0F6E56', 'rejected': '#854F0B', 'failed': '#993C1D'}
    parts = ['<!doctype html><meta charset="utf-8"><title>설명 검수 기록</title>',
             '<style>body{font:14px/1.5 system-ui,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#222}'
             'h1{font-size:18px}.row{border:1px solid #e5e5e5;border-radius:8px;padding:12px 14px;margin:10px 0}'
             '.st{font-weight:600}.k{color:#777;font-size:12px}.t{white-space:pre-wrap;margin:4px 0 8px}'
             '.b{background:#faf3f3}.a{background:#f2faf6}.t.b,.t.a{padding:8px;border-radius:6px}'
             'ul{margin:4px 0 8px 18px}small{color:#777}</style>',
             f'<h1>설명 검수 기록 <small>· {e(st["model"])} · 검수 {st["calls"]}건 · 수정 {st["edits"]}건'
             f'{" · " + e(str(st["disabled_reason"])) if st["disabled_reason"] else ""}</small></h1>',
             '<p class="k">"ok"(문제없음)는 나열하지 않습니다. edited = 수정본 채택 · rejected = 검수기 수정본을 사후 검증이 거부(원문 유지) · failed = 검수 호출 실패(원문 유지)</p>',
             '<p><a href="/admin">← 대시보드</a></p>']
    if not rows:
        parts.append('<p>아직 기록이 없습니다.</p>')
    for r in rows:
        parts.append(f'<div class="row"><span class="st" style="color:{color.get(r["status"], "#333")}">{e(r["status"])}</span>'
                     f' <b>{e(r["key"])}</b> <span class="k">{e(r["when"])}</span>')
        if r['issues']:
            parts.append('<ul>' + ''.join(f'<li>{e(i)}</li>' for i in r['issues']) + '</ul>')
        if r['reason']:
            parts.append(f'<div class="k">사유: {e(str(r["reason"]))}</div>')
        if r['status'] == 'edited':
            parts.append(f'<div class="k">수정 전</div><div class="t b">{e(r["before"] or "")}</div>'
                         f'<div class="k">수정 후</div><div class="t a">{e(r["after"] or "")}</div>')
            if (r['before_short'] or '') != (r['after_short'] or ''):
                parts.append(f'<div class="k">한 줄: <s>{e(r["before_short"] or "")}</s> → {e(r["after_short"] or "")}</div>')
        else:
            parts.append(f'<div class="k">현재 텍스트</div><div class="t">{e(r["after"] or "")}</div>')
        parts.append('</div>')
    resp = make_response('\n'.join(parts))
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.route('/admin')
def admin():
    """
    운영 + 제품 지표 시각 대시보드. ADMIN_TOKEN 으로 보호(미설정/미인증 시 404).
    최초 1회 ?token=... 로 들어오면 쿠키(admin_auth)를 심고 깔끔한 /admin 으로
    리다이렉트한다. 이후에는 같은 브라우저에서 그냥 /admin 으로 접속하면 된다.
    """
    want = os.environ.get('ADMIN_TOKEN')
    q = request.args.get('token', '')
    ck = request.cookies.get('admin_auth', '')
    if not (want and (q == want or ck == want)):
        return ('Not found', 404)   # 존재 자체를 숨김

    # 쿼리로 인증됐고 아직 쿠키가 없으면 → 쿠키 저장 후 토큰 없는 URL 로 이동
    if q == want and ck != want:
        resp = make_response(redirect(url_for('admin')))
        resp.set_cookie('admin_auth', want, max_age=60 * 60 * 24 * 60,
                        httponly=True, secure=True, samesite='Lax')
        return resp

    s = STATS.summary()
    daily_max = int(os.environ.get('DAILY_NEW_NAME_MAX', 1500))
    est_per = float(os.environ.get('EST_COST_PER_NEW', 0.02))
    try:
        with open(os.path.join(CACHE_DIR, 'translit_cache.json'),
                  encoding='utf-8') as f:
            cache_entries = len(json.load(f))
    except Exception:
        cache_entries = None
    op = {
        'commit': (os.environ.get('RENDER_GIT_COMMIT') or '')[:7] or 'local',
        'uptime_s': int(_time.time() - _BOOT_TS),
        'llm': bool(TRANSLIT.llm_available),
        'sentry': _SENTRY_ON,
        'tts_ok': bool(TTS_FULL.available),
        'tts_mode': TTS_FULL.last_mode,
        'tts_error': getattr(TTS_FULL, 'last_error', None),
        # 출력 전 검수기 상태 — 모델명이 틀리면 여기서 '꺼짐(오류)' 로 보인다
        'review': _review_status(),
        # 설명 생성이 최종 실패해 템플릿으로 떨어진 건수(유형별)와 재시도 성과.
        # 템플릿 문구는 사용자에게 나가면 안 되는 것이므로 여기서 바로 보인다.
        'gen_fail': getattr(MEANING_EN, 'fail_kinds', None) or {},
        'gen_retry': getattr(MEANING_EN, 'retry_count', 0),
        'gen_recovered': getattr(MEANING_EN, 'retry_recovered', 0),
        'new_today': s.get('new_today', 0),
        'daily_max': daily_max,
        'cache_entries': cache_entries,
        'est_per': est_per,
        'est_today': round(s.get('new_today', 0) * est_per, 2),
        'est_total': round(s.get('new', 0) * est_per, 2),
        'daily_peak': max([d['count'] for d in s.get('daily', [])] or [0]) or 1,
        'q_peak': max(list(s.get('quality', {}).values()) or [0]) or 1,
        'top_peak': max([t['count'] for t in s.get('top_first', [])] or [0]) or 1,
        'toph_peak': max([t['count'] for t in s.get('top_hangul', [])] or [0]) or 1,
    }
    # 결과물 품질 문제 — 최근 7일 코드별 건수 + 최근 사례
    try:
        iss = STATS.issues(days=7, limit=30)
    except Exception:
        iss = {'ok': False, 'by_code': [], 'recent': [], 'total': 0}
    iss['peak'] = max([r['count'] for r in iss.get('by_code') or []] or [0])
    iss['severity'] = _Q_SEVERITY
    try:
        recent = STATS.recent(limit=60)
    except Exception:
        recent = []
    try:
        ctry = STATS.countries(days=30, top_n=15)
    except Exception:
        ctry = {'picked': [], 'geo': [], 'picked_total': 0, 'geo_total': 0}
    ctry['names'] = _COUNTRY_NAMES
    try:
        src = STATS.sources(days=30, top_n=15)
    except Exception:
        src = {'rows': [], 'total': 0, 'events': {}, 'share_rate': None, 'window': 30}
    src['peak'] = max([r['count'] for r in (src.get('rows') or [])] or [0])
    ctry['peak'] = max([r['count'] for r in (ctry.get('picked') or [])]
                       + [r['count'] for r in (ctry.get('geo') or [])] or [0])
    return render_template('admin.html', s=s, op=op, iss=iss, recent=recent,
                           ctry=ctry, src=src)


@app.route('/admin/recent')
def admin_recent():
    """
    최근 변환 로그(JSON). /admin 화면이 주기적으로 불러 실시간처럼 갱신한다.
    쿠키/토큰 인증은 /admin 과 동일하다.
    """
    want = os.environ.get('ADMIN_TOKEN')
    tok = request.args.get('token', '') or request.cookies.get('admin_auth', '')
    if not (want and tok == want):
        return ('Not found', 404)
    try:
        since = request.args.get('since', type=int)
        rows = STATS.recent(limit=60, since_ts=since)
    except Exception:
        rows = []
    return jsonify({'rows': rows, 'severity': _Q_SEVERITY,
                    'names': _COUNTRY_NAMES, 'ts': int(_time.time())})


@app.route('/admin/export')
def admin_export():
    """
    변환 로그를 엑셀(.xlsx)로 다운로드. 인증은 /admin 과 동일(쿠키 또는 ?token=).
    ?days=90 으로 기간을 바꿀 수 있다(기본 90일, 최대 365일 — DB·응답 크기 방어).
    """
    want = os.environ.get('ADMIN_TOKEN')
    tok = request.args.get('token', '') or request.cookies.get('admin_auth', '')
    if not (want and tok == want):
        return ('Not found', 404)

    days = request.args.get('days', 90, type=int) or 90
    days = max(1, min(days, 365))

    try:
        import pandas as pd
    except Exception:
        return ('pandas/openpyxl not installed on server', 500)

    rows = STATS.export_rows(days=days, limit=20000)
    cols = ['when', 'ok', 'is_new', 'native', 'quality', 'sex', 'first_en',
            'last_en', 'given', 'hangul', 'flags', 'country', 'geo', 'ms', 'source']
    headers = {'when': 'Time (KST)', 'ok': 'Success', 'is_new': 'New name',
               'native': 'Native (순우리말)', 'quality': 'Quality', 'sex': 'Sex',
               'first_en': 'First name (input)', 'last_en': 'Last name (input)',
               'given': 'Given name (KR)', 'hangul': 'Full name (KR)',
               'flags': 'Quality flags', 'country': 'Country (picked)',
               'geo': 'Country (geo guess)', 'ms': 'Response time (ms)',
               'source': 'Traffic source'}
    df = pd.DataFrame(rows, columns=cols).rename(columns=headers)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='conversions')
        ws = writer.sheets['conversions']
        ws.freeze_panes = 'A2'
        for i, col in enumerate(df.columns, start=1):
            max_len = max((len(str(v)) for v in df[col]), default=10) if len(df) else 10
            width = max(10, min(32, max_len + 2))
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    buf.seek(0)

    fname = f'kname-conversions-{_time.strftime("%Y%m%d")}.xlsx'
    return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument'
                                    '.spreadsheetml.sheet',
                     as_attachment=True, download_name=fname)


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

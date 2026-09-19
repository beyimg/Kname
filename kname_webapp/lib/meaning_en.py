# -*- coding: utf-8 -*-
"""
영어 전용 의미 설명 생성기 (비용 최적화판).

meaning.py는 한국어와 영어를 함께 생성하지만, 이 웹앱은 영어만 사용한다.
버려지는 한국어 출력과 장황한 예시 프롬프트를 걷어내 토큰을 줄인다.

  기존 meaning.py : 입력 ~539 · 출력 ~400 토큰  (약 10.7원/건)
  이 모듈         : 입력 ~240 · 출력 ~150 토큰  (약 4.2원/건)

meaning.py가 이미 계산해 둔 한자 매칭·빈도·인기 이름 정보를 그대로 받아 쓰므로
사전 로딩을 중복하지 않는다.
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

DEFAULT_MODEL = "claude-sonnet-5"

# 프롬프트 형식 버전. **프롬프트를 고칠 때마다 올린다.**
#
# 캐시에 이 값이 없거나 다르면 그 항목은 없는 것으로 보고 다시 만든다.
# 예전에는 프롬프트를 고쳐도 캐시가 그대로 남아, 파일을 손으로 지우지 않으면
# 예전 형식 결과가 계속 나왔다("Boa carries ..." 처럼 도입부가 로마자만).
# 사람이 기억해야 하는 절차는 반드시 잊히므로 버전으로 강제한다.
#
# v5: 실존 인물·매체 언급 금지 규칙 추가. 사용자 결정 — 사전 밖 이름에서
#     지어낸 인물이 나갈 위험이 문법 오류보다 크다. (사전 602개의 손으로 확인한
#     언급은 그대로 둔다.)
# v6: 글자별 표기 형식을 '혜 (惠, hye)' 로 고정. 예전에는 지시가 없어 모델이
#     매번 다르게 썼다 — 100개 배치의 LLM 11건이 '표기 없음' 6건, '한자만' 3건,
#     '한글만' 2건으로 갈렸고 원하는 형식은 0건이었다. 이름 전체는 코드가
#     라벨로 박아 넣지만(NAME_SLOT) 글자별은 모델이 쓰므로 규칙이 필요하다.
#     사전 602개와 폴백 템플릿이 쓰는 형식과 같게 맞춘다.
PROMPT_VERSION = 6

# 출력 전 검수기(meaning_review.py). 생성 → 검수 → 캐시 순서다.
try:
    from meaning_review import MeaningReviewer, REVIEW_VERSION
except Exception:                     # 검수 모듈이 없어도 생성은 되어야 한다
    MeaningReviewer, REVIEW_VERSION = None, 0

# 도입부 자리표시자. 모델은 이 토큰만 쓰고, 코드가 라벨로 바꿔 넣는다.
# 형태가 하나뿐이므로 '모델이 어떻게 썼을까'를 알아맞힐 필요가 없다.
NAME_SLOT = '{{NAME}}'

# 라벨 바로 뒤에 오는 동사들. 모델이 자리표시자를 빼먹고 동사부터 썼다면
# 라벨을 앞에 붙이기만 하면 문장이 완성된다 — 떼어낼 것이 없으므로 안전하다.
# (사전 602개가 실제로 쓰는 낱말: joins 398 · pairs 154 · is 20 · doubles 12 ...)
_LEAD_VERBS = (
    'joins', 'pairs', 'combines', 'doubles', 'sets', 'brings', 'weaves',
    'carries', 'holds', 'blends', 'unites', 'marries', 'places', 'puts',
    'links', 'binds', 'gathers', 'is', 'comes', 'means', 'names',
    'evokes', 'pictures', 'paints', 'suggests', 'describes',
)

# 영어 문장이 이 낱말로 시작하면 '로마자 이름'이 아니다 — 건드리면 문장이 깨진다
_SENTENCE_OPENERS = {
    'the', 'this', 'that', 'these', 'those', 'a', 'an', 'and', 'but',
    'it', 'its', 'in', 'on', 'at', 'with', 'for', 'from', 'as', 'by',
    'her', 'his', 'their', 'they', 'both', 'two', 'one', 'named', 'name',
    'together', 'joined', 'joining', 'paired', 'pairing', 'above', 'born',
    'parents', 'chosen', 'spoken', 'carrying', 'meaning', 'korean',
}

# 라틴 확장 문자 → 기본 알파벳. LLM이 한국어 로마자에 발음기호를
# 붙이는 경우가 있어(Hořim), 표기를 정규화한다.
_DIACRITIC_MAP = str.maketrans({
    'á':'a','à':'a','â':'a','ä':'a','ã':'a','å':'a','ā':'a',
    'é':'e','è':'e','ê':'e','ë':'e','ē':'e','ě':'e',
    'í':'i','ì':'i','î':'i','ï':'i','ī':'i',
    'ó':'o','ò':'o','ô':'o','ö':'o','õ':'o','ō':'o','ø':'o',
    'ú':'u','ù':'u','û':'u','ü':'u','ū':'u','ů':'u',
    'ç':'c','č':'c','ć':'c','ñ':'n','ň':'n','ń':'n',
    'ř':'r','š':'s','ś':'s','ž':'z','ź':'z','ż':'z',
    'ý':'y','ÿ':'y','ď':'d','ť':'t','ł':'l',
})


def _strip_diacritics(text: str) -> str:
    return text.translate(_DIACRITIC_MAP).translate(
        str.maketrans({k.upper(): v.upper() for k, v in
                       zip('áàâäãåāéèêëēěíìîïīóòôöõōøúùûüūůçčćñňńřšśžźżýÿďťł',
                           'aaaaaaaeeeeeeiiiiiooooooouuuuuucccnnnrsszzzyydtl')}))


def classify_error(exc) -> str:
    """
    API 예외를 서비스 대응 기준으로 분류.
      'transient' : 재시도하면 풀림 (529 과부하, 429 레이트리밋, 5xx)
      'timeout'   : 응답이 제 시간에 오지 않음 — 재시도 비용이 가장 크다
      'credit'    : 크레딧 소진 — 충전 전까지 실패
      'auth'      : 키 문제
      'other'     : 그 외 (요청 오류 등)

    'timeout' 을 'transient' 에서 떼어낸 이유: 둘은 대응이 다르다. 과부하는
    잠깐 쉬면 풀리지만, 타임아웃은 다시 불러도 또 기다린다(시도마다 timeout 초).
    한 덩어리로 묶여 있으면 어느 쪽이 일어났는지 기록에 남지 않아, 재시도를
    늘려야 할지 타임아웃을 늘려야 할지 판단할 수 없다.
    """
    name = type(exc).__name__
    msg = str(exc).lower()
    status = getattr(exc, 'status_code', None)

    if name == 'APITimeoutError' or 'timed out' in msg or 'timeout' in msg:
        return 'timeout'
    if status in (429, 500, 502, 503, 504, 529) or name in (
            'OverloadedError', 'RateLimitError', 'APIStatusError',
            'InternalServerError', 'APIConnectionError'):
        return 'transient'
    if 'credit balance' in msg or 'insufficient' in msg or 'billing' in msg:
        return 'credit'
    if status == 401 or 'authentication' in msg or 'api key' in msg:
        return 'auth'
    return 'other'


# 앱 수준 재시도.
#
# 주의 — SDK 도 이미 재시도한다(_client_or_raise 의 max_retries). 그러므로
# 실패가 사용자에게 보였다는 것은 SDK 재시도까지 전부 소진했다는 뜻이다.
# 여기서 더 얹는 재시도의 값은 '한 번 더 부르는 것'이 아니라 **사이를 쉬는
# 것**에 있다: SDK 재시도는 초 단위로 연달아 나가므로 레이트리밋 급증 구간을
# 못 벗어난다. 그래서 짧은 대기를 끼워 한 번 더 시도한다.
#
# 'timeout' 은 재시도하지 않는다. 시도마다 timeout 초를 통째로 더 기다리게
# 되는데 결과는 대개 또 타임아웃이다 — 사용자를 두 배로 기다리게 하고 같은
# 템플릿을 보여주는 것이 가장 나쁜 조합이다. 타임아웃은 재시도가 아니라
# GEN_TIMEOUT 으로 다룬다.
# credit·auth·other 도 재시도해도 결과가 같으므로 즉시 포기한다.
RETRY_MAX = 1                 # 첫 호출 외 추가 시도 횟수 (transient 한정)
RETRY_SLEEP = 1.5             # 초. ±25% 지터 — 급증 구간을 벗어날 만큼만 쉰다

# 생성 호출 예산. 최악 대기시간 = GEN_TIMEOUT × (SDK_RETRIES + 1) × (RETRY_MAX + 1)
#   30초 × 2 × 2 = 120초가 상한이지만, 이는 타임아웃이 연속으로 날 때뿐이고
#   타임아웃은 재시도하지 않으므로 실제 상한은 30초 × 2 = 60초다.
# 예전 값(20초 × 4)은 최악 80초였는데도 400토큰 응답이 20초를 넘겨 실패했다.
# 시도 횟수를 줄이고 한 번의 여유를 늘리는 쪽이 낫다.
GEN_TIMEOUT = 30.0
GEN_SDK_RETRIES = 1

# 출력 토큰 상한. 400 이었을 때 v6 형식('희 (喜, hui), meaning glad')이 토큰을
# 더 먹어 응답이 잘렸다 — 우솔은 'car' 에서 끊기고, 희건은 'SHOR' 에서 끊겨
# 한 줄(SHORT)이 통째로 사라졌다(100개 배치 2차). 한글·한자·괄호는 영어보다
# 토큰이 비싸다. 60-90 단어 + 한 줄에 700 이면 넉넉하고, 남는 만큼은 과금되지
# 않는다(출력 토큰은 쓴 만큼만).
GEN_MAX_TOKENS = 700

# 빈 응답·잘린 응답은 한 번 더 부른다. 확률적 현상이라 같은 프롬프트로 다시
# 부르면 대개 정상으로 온다. 두 번째도 나쁘면 포기한다(예전 캐시 또는 템플릿).
REGEN_ON_BAD_OUTPUT = 1


class MeaningEnGenerator:
    """
    meaning.py의 NameMeaning 인스턴스를 감싸서 영어 설명만 생성한다.

        gen = MeaningEnGenerator(name_meaning, api_key=..., cache_path=...)
        text = gen.explain_en('광민', '남', hanja_chars, english_name='Kwame')
    """

    def __init__(
        self,
        name_meaning,                     # meaning.NameMeaning 인스턴스
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        cache_path: Optional[str] = 'meaning_en_cache.json',
        max_tokens: int = GEN_MAX_TOKENS,
        temperature: float = 0.7,
    ):
        self.nm = name_meaning
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.cache_path = cache_path
        self._client = None
        self._lock = threading.Lock()
        self._cache: Dict[str, object] = {}
        self.last_error: Optional[str] = None
        # 직전 호출이 '예전 캐시를 그대로 내보낸' 것인지.
        # 버전이 안 맞아 다시 만들려 했는데 실패한 경우다. 내용이 낡았으므로
        # 호출부가 이를 기록·보고할 수 있어야 한다(새로 만든 것과 구분 불가하면
        # 틀린 뜻이 조용히 계속 나간다).
        self.last_stale: bool = False
        # 예전 캐시를 내보낼 때의 사유(transient / credit / auth / other / no-key).
        # 같은 이름이 반복해서 실패하면 원인을 알아야 고칠 수 있다.
        self.last_stale_why: Optional[str] = None
        # 캐시 파일 기록 실패 — 있으면 매 실행이 전부 재생성된다
        self.cache_write_error: Optional[str] = None
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding='utf-8') as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}
        # 출력 전 검수기. 직전 호출의 검수 결과 요약을 last_review 에 남긴다
        # (app.py 가 stats 에 기록한다). 검수기가 없거나 꺼져 있으면 None.
        self.reviewer = MeaningReviewer(api_key=self.api_key) if MeaningReviewer else None
        self.last_review: Optional[dict] = None
        # 재시도·실패 집계. 템플릿 폴백이 왜 났는지는 이 값이 없으면 알 수 없다
        # (100개 배치에서 2건이 났지만 원인이 어디에도 남지 않았다).
        self.retry_count = 0            # 재시도한 횟수
        self.retry_recovered = 0        # 재시도로 살아난 건수
        self.fail_kinds: Dict[str, int] = {}   # 유형별 최종 실패 건수
        self.last_error_msg: Optional[str] = None   # 마지막 예외 메시지(앞 160자)
        self.last_stop_reason: Optional[str] = None # 마지막 응답의 stop_reason
        self.regen_count = 0            # 빈/잘린 응답으로 다시 부른 횟수
        self.regen_recovered = 0        # 그 중 살아난 건수

    # ------------------------------------------------------------ 내부
    def _client_or_raise(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError('ANTHROPIC_API_KEY is not set')
            import anthropic
            # 529(과부하)·429(레이트리밋)·5xx는 SDK가 지수 백오프로 자동 재시도한다.
            # SDK 재시도는 연달아 나가므로 레이트리밋 급증 구간을 벗어나지
            # 못한다. 그래서 횟수를 줄이고(GEN_SDK_RETRIES), 대신 앱 수준에서
            # 쉬었다가 한 번 더 시도한다(_call_with_retry).
            # 응답이 없으면 사용자가 무한정 기다리게 되므로 타임아웃을 건다.
            self._client = anthropic.Anthropic(
                api_key=self.api_key, max_retries=GEN_SDK_RETRIES,
                timeout=GEN_TIMEOUT)
        return self._client

    def _save_cache(self):
        """
        캐시 파일 기록. 실패를 조용히 넘기지 않는다.

        예전에는 예외를 삼켰다. 그러면 파일이 잠겨 있거나 쓰기 권한이 없을 때
        메모리에만 남고 파일에는 안 써져서, **매 실행마다 전부 다시 만든다**.
        비용이 계속 들고 원인은 아무도 모른다.
        """
        if not self.cache_path:
            return
        try:
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=1)
            self.cache_write_error = None
        except Exception as e:
            self.cache_write_error = f'{type(e).__name__}: {e}'
            try:
                import sys as _s
                print(f'[meaning_en] 캐시 저장 실패 — {self.cache_write_error}',
                      file=_s.stderr, flush=True)
            except Exception:
                pass

    def _popular(self, syllable: str, sex: str, exclude: str = '',
                 limit: int = 2) -> List[str]:
        """같은 글자가 들어간 인기 이름 (meaning.py의 색인 활용)"""
        try:
            rows = self.nm.similar_names_for_char(
                syllable, sex, exclude=exclude, max_count=limit) or []
            return [r[0] if isinstance(r, (list, tuple)) else r for r in rows]
        except Exception:
            return []

    @staticmethod
    def _romanize(given: str) -> str:
        try:
            # 붙여 쓴 로마자. 영어 낱말과 겹치는 이름은 하이픈을 남긴다(혜나 → Hye-na).
            from pronounce_guide import romanize_joined
            return romanize_joined(given)
        except Exception:
            return ''

    @staticmethod
    def _chars_en(hanja_chars, gloss_en=None, hanja_en=None):
        """[(음절, 한자, 영어뜻)]. 생성 프롬프트와 검수 DATA 가 **같은 뜻**을 본다.

        한자별 영어뜻(hanja_en)을 먼저 쓴다. 한국어뜻→영어 표(gloss_en)는 낱말
        하나에 영어 하나를 붙이므로 동음이의어를 구분하지 못한다. 그래서 예전에는
          馬(말=horse) · 斗(말=곡식 단위) · 勿(말=~하지 말라) → 'words'
          年(해=year) → 'sun'
          蔚(딸 ← 잘못된 데이터) → 'daughter'
        처럼 틀린 뜻이 프롬프트로 들어갔다. 카드에 찍히는 뜻은 한자별 표를
        쓰고 있었으므로 설명과 카드가 서로 달랐다.

        검수기에도 이 결과를 넘긴다. 예전에는 검수 DATA 가 한국어 뜻(착하다)이고
        생성은 영어 뜻(virtuous)이라, 검수기가 생성기가 받은 바로 그 낱말을
        'DATA 에 없다'며 고쳤다(혜선, 100개 배치 2차).
        """
        out = []
        for syl, hanja, kr_gloss in (hanja_chars or []):
            en = ((hanja_en or {}).get(hanja)
                  or (gloss_en or {}).get(kr_gloss)
                  or kr_gloss)
            out.append((syl, hanja, en))
        return out

    @staticmethod
    def _syl_rom(syllable: str) -> str:
        """한 음절의 소문자 로마자. 글자별 표기 '혜 (惠, hye)' 의 마지막 칸이다."""
        try:
            from pronounce_guide import romanize_syllable
            return romanize_syllable(syllable, capitalize=False)
        except Exception:
            return ''

    @classmethod
    def _label(cls, given, hanja_chars):
        """
        미리 작성된 602개 설명과 같은 도입부.
          한자 이름  → 민수 (旻秀, Minsu)
          순우리말   → 마루 (Maru)
        (라벨, 로마자) 를 돌려준다.
        """
        rom = cls._romanize(given)
        hanja_str = ''.join(h for _s, h, _g in (hanja_chars or []) if h)
        if hanja_str and rom:
            return f'{given} ({hanja_str}, {rom})', rom
        if hanja_str:
            return f'{given} ({hanja_str})', rom
        if rom:
            return f'{given} ({rom})', rom
        return given, rom

    @staticmethod
    def _fix_opening(text, given, label, rom):
        """
        도입부를 규정 형식으로 맞춘다.

        프롬프트로 "이렇게 시작하라"고 지시해도 모델은 종종 로마자만 쓴다
        ("Boa carries a quiet richness"). 지시에 기대지 않고 여기서 고친다.
        고칠 수 없는 형태면 손대지 않고 그대로 둔다(억지로 붙이면 문장이
        깨지므로, 대신 품질 점검이 'meaning/opening' 으로 잡는다).
        """
        if not text or text.startswith(label):
            return text
        # 한글 이름으로 시작 — 괄호가 없거나 다르면 라벨로 교체.
        # 단, 본문 괄호에 한자가 있는데 우리 라벨에는 없으면 본문이 더 풍부하다.
        # 그 경우 덮어쓰면 정보를 잃으므로 그대로 둔다.
        m = re.match(re.escape(given) + r'(?:\s*\(([^)]*)\))?', text)
        if m:
            inner = m.group(1) or ''
            if re.search(r'[\u4e00-\u9fff]', inner) and not re.search(
                    r'[\u4e00-\u9fff]', label):
                return text
            return label + text[m.end():]

        # 로마자로 시작하는 경우.
        #
        # 정확히 일치만 보면 놓친다. 모델이 쓰는 표기가 코드가 만든 표기와
        # 조금씩 다르기 때문이다(실측).
        #   세은 → 'Se-eun'  (하이픈)      코드: 'Seeun'
        #   이담 → 'I-Dam'   (하이픈+대문자) 코드: 'Idam'
        #   무영 → 'Muyoung' (표기 이형)    코드: 'Muyeong'
        # 그래서 맨 앞의 라틴 낱말을 떼어내 '그 이름의 로마자로 볼 수 있는가'를
        # 느슨하게 판정한다. 영어 문장으로 시작한 경우를 건드리면 문장이
        # 깨지므로, 흔한 문장 시작 낱말은 제외한다.
        lead = re.match(r"[A-Za-z][A-Za-z'\-]*", text)
        if rom and lead:
            word = lead.group(0)
            flat = word.replace('-', '').replace("'", '').lower()
            if (flat not in _SENTENCE_OPENERS
                    and (flat == rom.lower()
                         or (flat[:1] == rom[:1].lower()
                             and abs(len(flat) - len(rom)) <= 3))):
                rest = text[lead.end():]
                # 'Se-eun (世恩) carries ...' 처럼 괄호가 붙어 있으면 함께 뗀다
                m2 = re.match(r'\s*\([^)]*\)', rest)
                if m2:
                    rest = rest[m2.end():]
                return label + rest
        return text

    def _build_prompt(
        self,
        given: str,
        sex: str,
        hanja_chars: Optional[List[Tuple[str, str, str]]],
        english_name: Optional[str],
        gloss_en: Optional[Dict[str, str]] = None,
        hanja_en: Optional[Dict[str, str]] = None,
    ) -> str:
        """영어 출력만 요구하는 짧은 프롬프트."""
        lines = []
        if hanja_chars:
            for syl, hanja, en in self._chars_en(hanja_chars, gloss_en, hanja_en):
                # 글자별 표기를 모델이 조립하지 않게, 쓸 문자열을 그대로 준다.
                # NAME_SLOT 과 같은 원리다 — 베껴 쓰게 하면 형식이 흔들리지 않는다.
                syl_rom = self._syl_rom(syl)
                tag = f'{syl} ({hanja}, {syl_rom})' if syl_rom else f'{syl} ({hanja})'
                lines.append(f'{tag} = {en}')
            chars_desc = '; '.join(lines)
        else:
            chars_desc = 'a native Korean name (no hanja)'

        label, rom = self._label(given, hanja_chars)

        pop = []
        for syl in given:
            for p in self._popular(syl, sex, exclude=given, limit=2):
                if p != given and p not in pop:
                    pop.append(p)
        pop_line = f'\nOther popular names sharing its syllables: {", ".join(pop[:4])}.' if pop else ''

        # sex 가 '남'/'여'가 아니면(Either) 성별을 단정하지 않는다
        gender = {'남': 'a boy', '여': 'a girl'}.get(sex, 'a child')
        bridge = f'\nTheir English name is {english_name}.' if english_name else ''

        return (
            f'Write a warm, vivid explanation of the Korean name {given}, given to {gender}, '
            f'for an English-speaking reader who just received it.\n\n'
            f'Characters: {chars_desc}{pop_line}{bridge}\n\n'
            'Cover, in flowing prose (not a list): first and above all what the characters mean '
            'together and the hope behind them; then how the name sounds (soft, crisp, open '
            'vowels, etc.).\n'
            # 글자별 표기 형식. 지시가 없던 v5 에서는 모델이 '표기 없음 / 한자만 /
            # 한글만' 으로 매번 갈렸다. 조립하게 하지 않고, 위 Characters 줄에
            # 이미 완성해 둔 문자열을 베껴 쓰게 한다.
            + ('FORMAT RULE for individual characters: name each character at least once, and '
               'whenever you do, copy the exact form shown in the Characters line above, '
               'including the parentheses — Korean syllable, then hanja and lowercase '
               'romanization in parentheses. For example, write 혜 (惠, hye), never 惠 alone, '
               'never 혜 alone, never 惠 (kindness), and never the meaning with no character '
               'at all. Put the English meaning outside the parentheses, in your own sentence. '
               'Do not reorder what is inside the parentheses.\n'
               if hanja_chars else '')
            + 'STRICT RULE about the English name: what the Korean name MEANS comes only from the '
            'characters listed above (or, for a native Korean name, from the Korean word itself). '
            'Never fold the English name\'s own meaning into the Korean name\'s meaning. For '
            'example, if the English name is Rosa, do not say the Korean name means "beautiful as '
            'a rose" unless one of its characters actually means rose. You may mention the English '
            'name once, near the end, and only for how alike the two sound or how familiar it '
            'feels abroad — never for what the English name means.\n'
            + ('Do not state or imply a gender \u2014 avoid "boy", "girl", "he", "she". '
               'Use "they" if a pronoun is needed. ' if gender == 'a child' else '')
            + 'Write in the third person about the name and the person who bears it — '
            'refer to them as "someone" or "a person" (or "they"); never address the reader '
            'as "you" or "your". '
            # 도입부는 모델에게 '이름을 정확히 쓰라'고 요구하지 않는다.
            #
            # 그렇게 하면 표기가 매번 달라져 코드가 맞출 수 없다(실측).
            #   Se-eun / Seeun / I-Dam / Muyoung / Muyeong ...
            # 대신 고정된 자리표시자만 쓰게 하고, 코드가 그것을 라벨로 바꾼다.
            # 자리표시자는 형태가 하나뿐이라 일치 문제가 원리적으로 없다.
            + f'Begin the explanation with exactly {NAME_SLOT} and continue straight '
            f'on from it \u2014 for example: '
            + (f"'{NAME_SLOT} joins ...' or '{NAME_SLOT} pairs ...'. "
               if hanja_chars else
               f"'{NAME_SLOT} \u2014 above all, this is the native Korean word "
               f"for ...'. ")
            + f'Write {NAME_SLOT} literally, exactly once, as the very first thing. '
            'Do not write the Korean name or its romanization anywhere in that first '
            f'sentence \u2014 {NAME_SLOT} stands in for it and is filled in afterwards. '
            # 실존 인물 언급 금지(v5). "지어내지 말라"로는 부족했다 — 모델은
            # 그럴듯한 인물을 확신을 갖고 쓴다. 검수 없이 사실 확인이 불가능하므로
            # 아예 쓰지 않게 한다. (예시로 드는 한국 이름은 인물이 아니라 이름이다.)
            + 'Do not mention any real person (singers, idols, actors, athletes, '
            'historical figures) or any specific song, show, band, film or brand. '
            'Korean names given as examples are fine. 60-90 words. '
            'Write the name in plain Revised Romanization using basic Latin letters only '
            '(Horim, not Hořim) — no diacritics or accented characters.\n\n'
            # 카드 앞면에 쓰는 한 줄. 예전에는 한자 뜻을 로컬에서 조립했는데,
            # 뜻 문자열만 보고 품사를 알 수 없어 비문이 계속 나왔다
            # ("A name of beneficial and talent"). 문장은 모델이 쓰게 한다.
            'Then, after the explanation, add one final line in exactly this form:\n'
            'SHORT: <one short phrase>\n'
            'Rules for that phrase: a single grammatical English noun phrase of 4-9 words, '
            '30-55 characters, describing ONLY what the name means (never its sound, never '
            'the English name). Start with a capital letter and end with no punctuation. '
            'Use no Korean, no hanja, no romanization, no quotation marks. '
            'Good: "Someone wise who shines like jade" / "A bright and steadfast heart" / '
            '"Warmth that gathers people close". '
            'Bad: "Wisdom" (too short) / "Sounds soft and open" (about sound) / '
            '"Yunsu, a wise child" (names the person).'
        )

    # ------------------------------------------------------------ 호출
    def _call_with_retry(self, prompt: str) -> Tuple[Optional[str], Optional[str]]:
        """모델을 부른다. (본문, 에러유형) — 성공이면 (raw, None), 실패면 (None, 유형).

        transient 에러만 재시도한다. 마지막 시도의 에러 유형을 돌려주므로
        호출부는 '왜 실패했는지'를 그대로 기록할 수 있다.
        """
        err = None
        for attempt in range(RETRY_MAX + 1):
            try:
                client = self._client_or_raise()
                resp = client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[{'role': 'user', 'content': prompt}],
                )
                raw = ''.join(b.text for b in resp.content
                              if hasattr(b, 'text')).strip()
                # 빈 응답의 원인을 알려면 stop_reason 이 필요하다
                # (max_tokens 면 출력 한도에 걸린 것). transliterate.py 와 같다.
                self.last_stop_reason = getattr(resp, 'stop_reason', None)
                if attempt:
                    self.retry_recovered += 1
                return raw, None
            except Exception as e:
                err = classify_error(e)
                # 유형만으로는 'other' 가 무엇이었는지 알 수 없다(400? 검증 오류?).
                self.last_error_msg = f'{type(e).__name__}: {e}'[:160]
                # transient 만 재시도한다 — timeout 은 기다림만 두 배가 된다.
                if err != 'transient' or attempt == RETRY_MAX:
                    self.fail_kinds[err] = self.fail_kinds.get(err, 0) + 1
                    return None, err
                self.retry_count += 1
                time.sleep(RETRY_SLEEP * random.uniform(0.75, 1.25))
        return None, err

    def _bad_output(self, raw: Optional[str]) -> Optional[str]:
        """응답을 쓸 수 없는 이유. 정상이면 None.

        'truncated' : stop_reason 이 max_tokens — 문장이 중간에 끊겼다. 잘린
                      문장은 템플릿보다 나쁘다(사용자가 'car' 로 끝나는 설명을 본다).
        'empty'     : 본문이 없거나 40자 미만.
        """
        if self.last_stop_reason == 'max_tokens':
            return 'truncated'
        body, _short = self._split_short(raw or '')
        if not self._clean(body):
            return 'empty'
        return None

    def _generate(self, prompt: str) -> Tuple[Optional[str], Optional[str]]:
        """호출 + 출력 품질 확인. (raw, 실패사유) — 성공이면 (raw, None).

        API 오류 재시도(_call_with_retry)와 별개로, 응답이 왔는데 비었거나
        잘렸으면 같은 프롬프트로 한 번 더 부른다. 100개 배치 2차에서 13건 중
        6건이 빈 응답, 2건이 잘림이었다 — 이것을 그대로 두면 예전 캐시(구형식)
        나 템플릿이 나간다.
        """
        raw, err = self._call_with_retry(prompt)
        if raw is None:
            return None, err
        why = self._bad_output(raw)
        for _ in range(REGEN_ON_BAD_OUTPUT if why else 0):
            self.regen_count += 1
            raw2, err2 = self._call_with_retry(prompt)
            if raw2 is None:
                break                          # API 오류 — 첫 응답으로 판단
            why2 = self._bad_output(raw2)
            if not why2:
                self.regen_recovered += 1
                return raw2, None
            raw, why = raw2, why2
        if why:
            # 사유에 stop_reason 과 응답 앞부분을 붙인다 — 다음 배치에서 '왜'가
            # 그대로 보이도록. (예전 캐시 플래그 '예전캐시(...)' 에 실린다.)
            head = (raw or '').replace('\n', ' ')[:60]
            return None, f'{why}-response stop={self.last_stop_reason} raw={head!r}'
        return raw, None

    # ------------------------------------------------------------ 공개 API
    def explain_en(
        self,
        given: str,
        sex: str,
        hanja_chars: Optional[List[Tuple[str, str, str]]] = None,
        english_name: Optional[str] = None,
        gloss_en: Optional[Dict[str, str]] = None,
        hanja_en: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """영어 의미 설명 생성. 실패 시 None. (기존 호출부 호환)"""
        return self.explain_pair(given, sex, hanja_chars, english_name,
                                 gloss_en, hanja_en)[0]

    def explain_pair(
        self,
        given: str,
        sex: str,
        hanja_chars: Optional[List[Tuple[str, str, str]]] = None,
        english_name: Optional[str] = None,
        gloss_en: Optional[Dict[str, str]] = None,
        hanja_en: Optional[Dict[str, str]] = None,
        allow_llm: bool = True,
    ) -> Tuple[Optional[str], str]:
        """
        (설명, 카드 앞면 한 줄) 을 함께 돌려준다.

        allow_llm=False 면 캐시에 있는 것만 돌려주고 LLM 을 부르지 않는다
        (공유 링크 GET 경로 — 봇이 두드려도 비용 0).

        한 줄은 모델이 쓴 문장이므로 문법이 깨지지 않는다. 로컬에서 한자 뜻을
        조립하던 경로는 이 값이 없을 때만 쓰인다.
        """
        key = f'{given}:{sex}:{english_name or ""}'
        label, rom = self._label(given, hanja_chars)

        self.last_stale = False
        self.last_stale_why = None
        self.last_review = None
        cached = self._cache.get(key)
        if isinstance(cached, dict) and cached.get('v') == PROMPT_VERSION:
            # 만들어는 졌는데 검수가 안 된(실패했던) 항목 — POST 경로에서만 다시
            # 검수한다. GET(allow_llm=False)은 절대 LLM 비용을 내지 않는다.
            if (allow_llm and self._needs_review(cached)):
                self._review_into(cached, key, given, label,
                                  self._chars_en(hanja_chars, gloss_en, hanja_en),
                                  english_name)
            return cached.get('text') or None, cached.get('short') or ''
        # 버전이 다르거나 예전 문자열 캐시다.
        # 프롬프트가 바뀌었으니 다시 만드는 것이 맞다. 다만 키가 없어 새로
        # 만들 수 없을 때는 있는 것이라도 쓴다(도입부만 형식에 맞춰서).
        stale = cached if isinstance(cached, str) else (
            cached.get('text') if isinstance(cached, dict) else None)
        if not self.api_key or not allow_llm:
            if stale:
                self.last_stale, self.last_stale_why = True, ('no-key' if not self.api_key else 'no-llm')
                return self._fix_opening(stale, given, label, rom), ''
            return None, ''

        prompt = self._build_prompt(given, sex, hanja_chars, english_name,
                                    gloss_en, hanja_en)
        raw, err = self._generate(prompt)
        if raw is None:
            # 호출부가 상황에 맞는 안내를 띄울 수 있도록 유형을 남긴다.
            # API 오류면 유형(transient/timeout/...), 응답 문제면
            # 'empty-response ...' / 'truncated-response ...' 에 원인이 실려 있다.
            self.last_error = err
            # 새로 만들지 못했으면 예전 캐시라도 내보낸다(빈 카드보다 낫다).
            # 다만 낡은 내용이므로 반드시 표시한다.
            if stale:
                self.last_stale = True
                self.last_stale_why = err or 'error'
                return self._fix_opening(stale, given, label, rom), ''
            return None, ''
        self.last_error = None

        body, short = self._split_short(raw)
        text = self._clean(body)          # _generate 가 확인했으므로 비지 않는다
        if not text:                      # 만일을 위한 방어
            if stale:
                self.last_stale = True
                self.last_stale_why = 'empty-response'
                return self._fix_opening(stale, given, label, rom), ''
            return None, ''
        # 도입부는 지시가 아니라 코드로 보장한다.
        #  ① 자리표시자가 있으면 그대로 바꿔 넣는다 — 실패할 수 없는 경로
        #  ② 없으면(모델이 지시를 무시한 경우) 기존 교정이 받아낸다
        if NAME_SLOT in text:
            text = text.replace(NAME_SLOT, label, 1).replace(NAME_SLOT, given)
            text = re.sub(r'\s+', ' ', text).strip()
        else:
            text = self._fix_opening(text, given, label, rom)
            # 자리표시자를 빼먹고 동사부터 쓴 경우 — 라벨만 앞에 붙이면 된다.
            #   'joins 세 (世, se) ...' → '세은 (世恩, Seeun) joins 세 ...'
            if not text.startswith(label):
                w = re.match(r'([a-z]+)\b', text)
                if w and w.group(1) in _LEAD_VERBS:
                    text = f'{label} {text}'
        entry = {'v': PROMPT_VERSION, 'text': text, 'short': short}
        # 보여주기 전에 검수 — 생성 → 검수 → 캐시. 검수가 실패해도 원문은 그대로 나간다.
        self._review_into(entry, key, given, label,
                          self._chars_en(hanja_chars, gloss_en, hanja_en),
                          english_name, save=False)
        with self._lock:
            self._cache[key] = entry
            self._save_cache()
        return entry['text'], entry['short']

    # ------------------------------------------------------------ 검수
    @staticmethod
    def _needs_review(entry: dict) -> bool:
        """검수 규칙 버전이 다르거나(REVIEW_VERSION 올림) 검수가 끝난 적이 없으면 True."""
        return bool(REVIEW_VERSION) and entry.get('r') != REVIEW_VERSION

    def _review_into(self, entry: dict, key: str, given: str, label: str,
                     chars_en, english_name, save: bool = True) -> None:
        """entry 를 검수해 제자리에서 고친다. 결과 요약은 entry['review'] 와 last_review 에.

        chars_en: [(음절, 한자, 영어뜻)] — _chars_en() 의 결과. 생성 프롬프트가
        받은 것과 같은 영어 뜻이어야 한다(한국어 뜻을 주면 검수기가 생성기의
        낱말을 'DATA 에 없다'며 고친다).

        'r'(검수 완료 표시)은 검수기가 **실제로 판정을 내렸을 때만** 찍는다
        (ok / edited / rejected). 호출 실패·꺼짐이면 찍지 않아 다음 POST 에서
        다시 시도한다 — 무한 반복은 검수기의 circuit breaker 가 막는다.
        """
        if not self.reviewer or not entry.get('text'):
            return
        try:
            res = self.reviewer.review(
                entry['text'], entry.get('short') or '', given, label,
                hanja_chars=chars_en, english_name=english_name,
                clean_short=self._clean_short)
        except Exception as e:                      # 검수기는 예외를 안 던지지만, 만일을 위해
            self.last_review = {'status': 'failed', 'reason': f'{type(e).__name__}: {e}'[:160]}
            return
        summary = res.to_cache()
        if res.status in ('ok', 'edited', 'rejected'):
            entry['r'] = REVIEW_VERSION
            if res.edited:
                entry['text'], entry['short'] = res.text, res.short
        if res.status != 'disabled':
            entry['review'] = summary
        self.last_review = summary
        if save and res.status in ('ok', 'edited', 'rejected'):
            with self._lock:
                self._cache[key] = entry
                self._save_cache()

    # 'SHORT: ...' 마지막 줄을 떼어낸다.
    _SHORT_RE = re.compile(r'^\s*SHORT\s*[:\-—]\s*(.+?)\s*$',
                           re.IGNORECASE | re.MULTILINE)

    @classmethod
    def _split_short(cls, raw: str) -> Tuple[str, str]:
        if not raw:
            return '', ''
        m = None
        for m in cls._SHORT_RE.finditer(raw):
            pass                                  # 마지막 것을 쓴다
        if not m:
            return raw, ''
        body = (raw[:m.start()] + raw[m.end():]).strip()
        return body, cls._clean_short(m.group(1))

    @staticmethod
    def _clean_short(s: str) -> str:
        """한 줄을 카드에 쓸 수 있는 형태로 검사·정리. 부적합하면 빈 문자열."""
        s = re.sub(r'\s+', ' ', (s or '').strip())
        s = s.strip('"“”‘’ ').rstrip('.').strip()
        s = _strip_diacritics(s)
        if not s or re.search(r'[가-힣㐀-鿿]', s):
            return ''                             # 한글·한자가 섞이면 버린다
        if not (10 <= len(s) <= 62) or not (3 <= len(s.split()) <= 12):
            return ''
        if s.rstrip().lower().endswith((',', ' and', ' or', ' as', ' of',
                                        ' with', ' to', ' the', ' a')):
            return ''                             # 어중간하게 끊긴 문구
        if re.search(r'\bsounds?\b|\bsyllable', s, re.I):
            return ''                             # 발음 이야기는 뜻이 아니다
        return s[0].upper() + s[1:]

    @staticmethod
    def _clean(text: str) -> Optional[str]:
        if not text:
            return None
        text = re.sub(r'^```(?:\w+)?\s*|\s*```$', '', text.strip()).strip()
        text = text.strip('"\u201c\u201d ')
        # JSON으로 답한 경우 구제
        if text.startswith('{'):
            try:
                obj = json.loads(text)
                text = obj.get('meaning_en') or obj.get('explanation') or ''
            except Exception:
                pass
        text = re.sub(r'\s+', ' ', text).strip()
        # 한국어 로마자에 유럽어 발음기호가 섞이는 경우가 있다 (Hořim → Horim)
        text = _strip_diacritics(text)
        return text if len(text) >= 40 else None

    @property
    def llm_available(self) -> bool:
        return bool(self.api_key)

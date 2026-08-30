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
import re
import threading
from typing import Dict, List, Optional, Tuple

DEFAULT_MODEL = "claude-sonnet-5"

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
      'transient' : 재시도하면 풀림 (529 과부하, 429 레이트리밋, 5xx, 타임아웃)
      'credit'    : 크레딧 소진 — 충전 전까지 실패
      'auth'      : 키 문제
      'other'     : 그 외 (요청 오류 등)
    """
    name = type(exc).__name__
    msg = str(exc).lower()
    status = getattr(exc, 'status_code', None)

    if status in (429, 500, 502, 503, 504, 529) or name in (
            'OverloadedError', 'RateLimitError', 'APIStatusError',
            'InternalServerError', 'APITimeoutError', 'APIConnectionError'):
        return 'transient'
    if 'credit balance' in msg or 'insufficient' in msg or 'billing' in msg:
        return 'credit'
    if status == 401 or 'authentication' in msg or 'api key' in msg:
        return 'auth'
    return 'other'



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
        max_tokens: int = 320,
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
        self._cache: Dict[str, str] = {}
        self.last_error: Optional[str] = None
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding='utf-8') as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}

    # ------------------------------------------------------------ 내부
    def _client_or_raise(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError('ANTHROPIC_API_KEY is not set')
            import anthropic
            # 529(과부하)·429(레이트리밋)·5xx는 SDK가 지수 백오프로 자동 재시도한다.
            # 기본 2회로는 지속적 과부하에 부족해 4회로 올린다.
            # 응답이 없으면 사용자가 무한정 기다리게 되므로 타임아웃을 건다.
            # (재시도 4회 × 타임아웃이므로 최악의 대기시간을 함께 고려)
            self._client = anthropic.Anthropic(
                api_key=self.api_key, max_retries=3, timeout=20.0)
        return self._client

    def _save_cache(self):
        if not self.cache_path:
            return
        try:
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=1)
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

    def _build_prompt(
        self,
        given: str,
        sex: str,
        hanja_chars: Optional[List[Tuple[str, str, str]]],
        english_name: Optional[str],
        gloss_en: Optional[Dict[str, str]] = None,
    ) -> str:
        """영어 출력만 요구하는 짧은 프롬프트."""
        lines = []
        if hanja_chars:
            for syl, hanja, kr_gloss in hanja_chars:
                en = (gloss_en or {}).get(kr_gloss) or kr_gloss
                lines.append(f'{syl} ({hanja}) = {en}')
            chars_desc = '; '.join(lines)
        else:
            chars_desc = 'a native Korean name (no hanja)'

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
            'Cover, in flowing prose (not a list): what the characters mean together and the hope '
            'behind them; how the name sounds (soft, crisp, open vowels, etc.); and, if it fits '
            'naturally, a link to their English name.\n'
            + ('Do not state or imply a gender \u2014 avoid "boy", "girl", "he", "she". '
               'Use "they" if a pronoun is needed. ' if gender == 'a child' else '')
            + 'Write in the third person about the name and the person who bears it — '
            'refer to them as "someone" or "a person" (or "they"); never address the reader '
            'as "you" or "your". '
            + 'Never invent facts about real people or media. 60-90 words. '
            'Write the name in plain Revised Romanization using basic Latin letters only '
            '(Horim, not Hořim) — no diacritics or accented characters. '
            'Reply with the explanation text only — no preamble, no quotes, no JSON.'
        )

    # ------------------------------------------------------------ 공개 API
    def explain_en(
        self,
        given: str,
        sex: str,
        hanja_chars: Optional[List[Tuple[str, str, str]]] = None,
        english_name: Optional[str] = None,
        gloss_en: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """영어 의미 설명 생성. 실패 시 None."""
        key = f'{given}:{sex}:{english_name or ""}'
        if key in self._cache:
            return self._cache[key]
        if not self.api_key:
            return None

        prompt = self._build_prompt(given, sex, hanja_chars, english_name, gloss_en)
        try:
            client = self._client_or_raise()
            resp = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{'role': 'user', 'content': prompt}],
            )
            text = ''.join(b.text for b in resp.content if hasattr(b, 'text')).strip()
            self.last_error = None
        except Exception as e:
            # 호출부가 상황에 맞는 안내를 띄울 수 있도록 유형을 남긴다
            self.last_error = classify_error(e)
            return None

        text = self._clean(text)
        if not text:
            return None
        with self._lock:
            self._cache[key] = text
            self._save_cache()
        return text

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

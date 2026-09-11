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

# 프롬프트 형식 버전. **프롬프트를 고칠 때마다 올린다.**
#
# 캐시에 이 값이 없거나 다르면 그 항목은 없는 것으로 보고 다시 만든다.
# 예전에는 프롬프트를 고쳐도 캐시가 그대로 남아, 파일을 손으로 지우지 않으면
# 예전 형식 결과가 계속 나왔다("Boa carries ..." 처럼 도입부가 로마자만).
# 사람이 기억해야 하는 절차는 반드시 잊히므로 버전으로 강제한다.
PROMPT_VERSION = 3

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
        max_tokens: int = 400,
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
            from pronounce_guide import romanize_hyphen
            return romanize_hyphen(given).replace('-', '')
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
        # 한글 이름으로 시작 — 괄호가 없거나 다르면 라벨로 교체
        m = re.match(re.escape(given) + r'(?:\s*\([^)]*\))?', text)
        if m:
            return label + text[m.end():]
        # 로마자로 시작 — "Boa carries ..." → "보아 (寶雅, Boa) carries ..."
        if rom:
            m = re.match(re.escape(rom) + r'\b(?:\s*\([^)]*\))?', text,
                         re.IGNORECASE)
            if m:
                return label + text[m.end():]
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
            for syl, hanja, kr_gloss in hanja_chars:
                # 한자별 영어뜻(hanja_en)을 먼저 쓴다.
                #
                # 한국어뜻→영어 표(gloss_en)는 낱말 하나에 영어 하나를 붙이므로
                # 동음이의어를 구분하지 못한다. 그래서 예전에는
                #   馬(말=horse) · 斗(말=곡식 단위) · 勿(말=~하지 말라) → 'words'
                #   年(해=year) → 'sun'
                #   蔚(딸 ← 잘못된 데이터) → 'daughter'
                # 처럼 틀린 뜻이 프롬프트로 들어갔다. 카드에 찍히는 뜻은
                # 한자별 표를 쓰고 있었으므로 설명과 카드가 서로 달랐다.
                en = ((hanja_en or {}).get(hanja)
                      or (gloss_en or {}).get(kr_gloss)
                      or kr_gloss)
                lines.append(f'{syl} ({hanja}) = {en}')
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
            'STRICT RULE about the English name: what the Korean name MEANS comes only from the '
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
            + f'Start the very first sentence with exactly "{label}" and continue straight '
            f'on from it — for example: '
            + (f'\'{label} joins ...\' or \'{label} pairs ...\'. '
               if hanja_chars else
               f'\'{label} — above all, this is the native Korean word for ...\'. ')
            + 'This opening is fixed — do not drop the Hangul or the parentheses, and never '
            'open with the romanization alone ("Yunsu carries ..." is wrong). '
            + 'Never invent facts about real people or media. 60-90 words. '
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
    ) -> Tuple[Optional[str], str]:
        """
        (설명, 카드 앞면 한 줄) 을 함께 돌려준다.

        한 줄은 모델이 쓴 문장이므로 문법이 깨지지 않는다. 로컬에서 한자 뜻을
        조립하던 경로는 이 값이 없을 때만 쓰인다.
        """
        key = f'{given}:{sex}:{english_name or ""}'
        label, rom = self._label(given, hanja_chars)

        self.last_stale = False
        self.last_stale_why = None
        cached = self._cache.get(key)
        if isinstance(cached, dict) and cached.get('v') == PROMPT_VERSION:
            return cached.get('text') or None, cached.get('short') or ''
        # 버전이 다르거나 예전 문자열 캐시다.
        # 프롬프트가 바뀌었으니 다시 만드는 것이 맞다. 다만 키가 없어 새로
        # 만들 수 없을 때는 있는 것이라도 쓴다(도입부만 형식에 맞춰서).
        stale = cached if isinstance(cached, str) else (
            cached.get('text') if isinstance(cached, dict) else None)
        if not self.api_key:
            if stale:
                self.last_stale, self.last_stale_why = True, 'no-key'
                return self._fix_opening(stale, given, label, rom), ''
            return None, ''

        prompt = self._build_prompt(given, sex, hanja_chars, english_name,
                                    gloss_en, hanja_en)
        try:
            client = self._client_or_raise()
            resp = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{'role': 'user', 'content': prompt}],
            )
            raw = ''.join(b.text for b in resp.content if hasattr(b, 'text')).strip()
            self.last_error = None
        except Exception as e:
            # 호출부가 상황에 맞는 안내를 띄울 수 있도록 유형을 남긴다
            self.last_error = classify_error(e)
            # 새로 만들지 못했으면 예전 캐시라도 내보낸다(빈 카드보다 낫다).
            # 다만 낡은 내용이므로 반드시 표시한다.
            if stale:
                self.last_stale = True
                self.last_stale_why = self.last_error or 'error'
                return self._fix_opening(stale, given, label, rom), ''
            return None, ''

        body, short = self._split_short(raw)
        text = self._clean(body)
        if not text:
            if stale:
                self.last_stale = True
                self.last_stale_why = 'empty-response'
                return self._fix_opening(stale, given, label, rom), ''
            return None, ''
        # 도입부는 지시가 아니라 코드로 보장한다
        text = self._fix_opening(text, given, label, rom)
        with self._lock:
            self._cache[key] = {'v': PROMPT_VERSION, 'text': text,
                                'short': short}
            self._save_cache()
        return text, short

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

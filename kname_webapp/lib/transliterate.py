# -*- coding: utf-8 -*-
"""
영어 이름/성씨 → 한글 음차 (사전에 없는 경우 LLM으로 실시간 생성)

구조는 meaning.py와 동일한 2단계:
  1) 사전 조회  — dict_name_to_translit.json (이름 1,978 / 성씨 300)
  2) LLM 생성   — 사전에 없으면 Claude 호출, 결과는 디스크 캐시에 저장

사용:
    tr = Transliterator(
        name_dict_path='data/dict_name_to_translit.json',
        api_key=os.environ.get('ANTHROPIC_API_KEY'),
        cache_path='translit_cache.json',
    )
    tr.transliterate('Kowalski', kind='surname')   # → '코왈스키'
    tr.transliterate('Siobhan',  kind='female')    # → '시본'

API 키가 없으면 사전 조회만 하고 None을 반환한다(폴백 없음).
음차는 정확도가 결과 품질을 좌우하므로, 규칙 기반 추측으로 대충 만들지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Dict, Optional

DEFAULT_MODEL = "claude-sonnet-4-5"

# 국립국어원 외래어 표기법에 기반한 음차 지침 + 실제 사전에서 뽑은 예시.
# 어원이 다양하므로(스페인어·베트남어·인도·아일랜드계 등) 철자가 아니라
# '원어 발음'을 기준으로 옮기도록 지시한다.
_GUIDE = """You transliterate personal names into Korean Hangul, following the Korean
Ministry of Culture's loanword transcription rules (외래어 표기법).

STEP 1 — Identify the language of origin, then recall how the name is actually
pronounced by native speakers of that language. Spelling alone will mislead you.

STEP 2 — Transcribe that pronunciation. Match the SYLLABLE COUNT of the spoken
form, not the written form.

Language-specific traps (these are the ones most often gotten wrong):

· Irish — spelling and sound diverge sharply.
  Fionn → 핀 (ONE syllable, not 피온)      Aisling → 애슐링 (sl sounds like "sh")
  Sean → 숀        Siobhan → 시본          Saoirse → 서샤
  Caoimhe → 키바   Tadhg → 타이그          Roisin → 로신
  Padraig → 포드릭  Niamh → 니브            Aoife → 이파

· Japanese — initial voiceless stops become plain (평음) in Korean.
  Takumi → 다쿠미 (not 타쿠미)   Kenjiro → 겐지로 (not 켄지로)
  Kazuo → 가즈오   Taro → 다로   Keiko → 게이코
  But medial/final positions keep the aspirated form: Hasegawa → 하세가와

· Basque / Spanish / Catalan — read as Spanish, never as English.
  Iker → 이케르 (not 아이커)    Etxeberria → 에체베리아 (tx = "ch")
  Xavier (Catalan) → 차비에르   Jorge → 호르헤   Guillermo → 기예르모

· Chinese (pinyin) — one syllable per pinyin unit.
  Xiaoli → 샤오리 (not 샤올리)   Zhang → 장   Ouyang → 어우양
  Qing → 칭   Zhao → 자오   Xiuying → 슈잉

· Dutch / Scandinavian — final -e is pronounced.
  Sanne → 산네 (not 산)   Anneke → 안네커   Bjerke → 비에르케
  Kjaer → 키에르          Halldorsson → 하들도르손

· French — final consonants are usually silent.
  Guillaume → 기욤   Thibault → 티보   Beaulieu → 볼리외
  Lacroix → 라크루아  Moreau → 모로

· Slavic — transcribe each consonant cluster.
  Wojciech → 보이체흐   Przemyslaw → 프셰미스와프   Dvorak → 드보르자크
  Kowalczyk → 코발치크  Bondarenko → 본다렌코

· African (Yoruba, Igbo, Akan, Zulu) — syllable by syllable, no English reading.
  Kwame → 콰메 (not 과메)      Okonkwo → 오콘쿠오
  Adeyemi → 아데예미  Chukwuemeka → 추쿠에메카  Nomvula → 놈불라

· South / Southeast Asian
  Nguyen → 응우옌   Patel → 파텔   Chatterjee → 차터지
  Wijaya → 위자야   Srisuk → 시수크

General rules:
- [kw] uses ㅋ + 와/우, never ㄱ.
- Use 으 only for unreleased consonant clusters: Kowalski → 코발스키
- Never use tense consonants (쓰/쯔/뻐); loanword rules use plain ones.
- Do not pad with extra syllables. Smith → 스미스, Singh → 싱

Ordinary English names stay straightforward:
  Johnson → 존슨   Williams → 윌리엄스   Christopher → 크리스토퍼
  Matthew → 매슈   Jacqueline → 재클린   Fletcher → 플레처
  Thaddeus → 대디어스  Winifred → 위니프레드

Output Hangul only — no romanization, no explanation, no punctuation.
"""



class Transliterator:
    def __init__(
        self,
        name_dict_path: str,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        cache_path: Optional[str] = 'translit_cache.json',
        max_tokens: int = 64,
        temperature: float = 0.0,
    ):
        with open(name_dict_path, encoding='utf-8') as f:
            self.dict = json.load(f)
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.cache_path = cache_path
        self._client = None
        self._lock = threading.Lock()
        self._cache: Dict[str, str] = {}
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding='utf-8') as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}

    # ------------------------------------------------------------ 내부
    def _get_client(self):
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

    # 외래어 표기법에서 쓰지 않는 글자 — 나오면 LLM이 규칙을 벗어난 것
    _BAD_SYLLABLE = re.compile(r'[쓰쯔뻐껴쌰쎄찌똑빡]')

    @staticmethod
    def _clean(text: str) -> Optional[str]:
        """LLM 응답에서 한글만 추출하고, 명백한 규칙 위반을 걸러낸다."""
        if not text:
            return None
        text = text.strip()
        text = re.sub(r'^```(?:\w+)?\s*|\s*```$', '', text).strip()
        m = re.search(r'[가-힣]+', text)
        if not m:
            return None
        out = m.group(0)
        if not (1 <= len(out) <= 10):
            return None
        # 경음(된소리)은 외래어 표기법에서 쓰지 않는다
        if Transliterator._BAD_SYLLABLE.search(out):
            return None
        return out

    @staticmethod
    def _plausible(name: str, hangul: str) -> bool:
        """
        음차 결과가 원래 이름과 길이상 말이 되는지 확인한다.
        영어 이름은 대체로 글자수의 절반 안팎이 한글 음절수가 된다.
        지나치게 길거나 짧으면 LLM이 잘못 읽은 것이다.
        """
        n = len(re.sub(r"[^A-Za-z]", '', name))
        if n == 0 or not hangul:
            return False
        k = len(hangul)
        # 여유 있게: 알파벳 2~12자 → 한글 1~10음절 범위에서 극단만 거른다
        return max(1, n // 4) <= k <= max(3, n)

    def _build_prompt(self, name: str, kind: str) -> str:
        what = 'family name (surname)' if kind == 'surname' else 'given name'
        return (
            f'{_GUIDE}\n'
            f'Transliterate this English {what} into Hangul: "{name}"\n\n'
            f'Answer with the Hangul only.'
        )

    def _call_llm(self, prompt: str) -> str:
        client = self._get_client()
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[{'role': 'user', 'content': prompt}],
        )
        parts = [b.text for b in resp.content if hasattr(b, 'text')]
        return ''.join(parts)

    # ------------------------------------------------------------ 공개 API
    def lookup(self, name: str, kind: str) -> Optional[str]:
        """사전에서만 조회 (LLM 호출 없음)."""
        key = (name or '').strip().lower()
        if not key:
            return None
        bucket = 'surname' if kind == 'surname' else kind   # 'male' | 'female' | 'surname'
        return self.dict.get(bucket, {}).get(key)

    def transliterate(self, name: str, kind: str, allow_llm: bool = True) -> Optional[str]:
        """
        사전 → 캐시 → LLM 순으로 음차를 얻는다.
        kind: 'male' | 'female' | 'surname'
        실패 시 None (규칙 기반 추측은 하지 않음)
        """
        key = (name or '').strip().lower()
        if not key:
            return None

        # 1) 사전
        hit = self.lookup(key, kind)
        if hit:
            return hit

        # 2) 캐시
        ck = f'{kind}:{key}'
        if ck in self._cache:
            return self._cache[ck]

        # 3) LLM
        if not (allow_llm and self.api_key):
            return None
        try:
            raw = self._call_llm(self._build_prompt(name.strip(), kind))
            out = self._clean(raw)
        except Exception:
            return None
        if not out:
            return None
        if not self._plausible(name, out):
            # 길이가 터무니없으면 한 번 더 시도한다
            try:
                raw2 = self._call_llm(self._build_prompt(name.strip(), kind))
                out2 = self._clean(raw2)
            except Exception:
                out2 = None
            if out2 and self._plausible(name, out2):
                out = out2
        with self._lock:
            self._cache[ck] = out
            self._save_cache()
        return out

    @property
    def llm_available(self) -> bool:
        return bool(self.api_key)


if __name__ == '__main__':
    t = Transliterator('data/dict_name_to_translit.json')
    print('LLM 사용 가능:', t.llm_available)
    print()
    print('사전 조회 (LLM 불필요):')
    for n, k in [('Smith', 'surname'), ('Nguyen', 'surname'), ('Sophia', 'female')]:
        print(f'  {n:10s} [{k:7s}] → {t.transliterate(n, k)}')
    print()
    print('사전에 없는 이름 (LLM 필요):')
    for n, k in [('Kowalski', 'surname'), ("O'Brien", 'surname'), ('Siobhan', 'female')]:
        print(f'  {n:10s} [{k:7s}] → {t.transliterate(n, k)}')

"""
meaning.py — K-Name Generator 이름 의미 설명 모듈

변환된 한국 이름의 의미를 풍부한 스토리텔링 형식으로 설명.

- 한자어 이름: 각 글자 뜻 + 조합 의미 + 염원 + 빈도/어감 + 같은 글자 인기 이름 + 성별 경향 + (가능하면) 문화 레퍼런스
- 우리말 이름: 우리말 뜻 + 어감 + 문화적 맥락

한국어 + 영어 동시 생성 (1회 LLM 호출).
디스크 캐시로 재호출 방지.
"""
from __future__ import annotations

import os
import re
import json
import hashlib
import logging
import threading
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# 설정 상수 (외부에서 주입 가능)
# ============================================================
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 1500
DEFAULT_TEMPERATURE = 0.7  # 약간의 다양성 (창의성)

# 설명 길이 가이드 (프롬프트에 명시되는 값)
KR_LENGTH_HINT = "250~350자"
EN_LENGTH_HINT = "80~120 words"

# 같은 글자가 들어간 인기 이름 추천 최대 개수
MAX_SIMILAR_NAMES = 5


# ============================================================
# NameMeaning 클래스
# ============================================================
class NameMeaning:
    """변환된 한국 이름의 의미를 풍부한 스토리텔링으로 생성.

    사용법:
        meaning = NameMeaning(
            name_hanja_path='인명용_한자사전.xlsx',
            surname_hanja_path='성씨별_한자.xlsx',
            db_path='merged_meaningful.xlsx',
            api_key=os.environ['ANTHROPIC_API_KEY'],
            cache_path='meaning_cache.json',
        )
        result = meaning.explain(
            given='민준', sex='남',
            hanja_chars=[('민','旻','가을하늘'), ('준','峻','높을')],
            name_type='hanja',
        )
        # result = {'meaning_kr': '...', 'meaning_en': '...'}
    """

    def __init__(
        self,
        name_hanja_path: str,
        surname_hanja_path: str,
        db_path: Optional[str] = None,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        cache_path: Optional[str] = 'meaning_cache.json',
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ):
        # ---- 한자 사전 로드 ----
        # 인명용 한자: 음(소리) -> [(한자, 뜻), ...]
        hanja_df = pd.read_excel(name_hanja_path)
        self.name_hanja: Dict[str, List[Tuple[str, str]]] = {}
        for sound in hanja_df['음'].dropna().unique():
            rows = hanja_df[hanja_df['음'] == sound][['한자', '뜻']]
            self.name_hanja[str(sound)] = [
                (str(r['한자']), str(r['뜻']))
                for _, r in rows.iterrows()
            ]

        # 성씨 한자: 성씨 -> 한자
        surname_df = pd.read_excel(surname_hanja_path)
        self.surname_hanja: Dict[str, str] = dict(
            zip(surname_df['성씨'].astype(str), surname_df['한자'].astype(str))
        )

        # ---- 이름 DB (빈도, 유형, 우리말 뜻) ----
        # merged_meaningful.xlsx: name, sex, weight, 음절수, 유형, 순우리말 뜻
        self.urimal_meanings: Dict[str, str] = {}  # name -> meaning
        self.hanja_names: set = set()                # 유형='한자어' 이름들
        self.urimal_names: set = set()               # 유형='순우리말' 이름들
        self.name_freq: Dict[str, Dict[str, int]] = {}  # name -> {남: w, 여: w}

        if db_path and os.path.exists(db_path):
            db = pd.read_excel(db_path)
            for _, r in db.iterrows():
                nm = str(r['name'])
                sx = str(r.get('sex', ''))
                wt = int(r.get('weight', 0) or 0)
                tp = str(r.get('유형', '') or '')
                meaning = r.get('순우리말 뜻', '')

                # 빈도
                if nm not in self.name_freq:
                    self.name_freq[nm] = {'남': 0, '여': 0}
                if sx in ('남', '여'):
                    self.name_freq[nm][sx] = max(self.name_freq[nm][sx], wt)

                # 유형
                if tp == '한자어':
                    self.hanja_names.add(nm)
                elif tp == '순우리말':
                    self.urimal_names.add(nm)
                    if isinstance(meaning, str) and meaning.strip():
                        self.urimal_meanings[nm] = meaning.strip()

        # ---- 같은 글자가 들어간 이름 인덱스 (글자 -> [이름들]) ----
        # weight 정렬해서 인기 이름 추천에 활용
        self._char_to_names: Dict[str, List[Tuple[str, str, int]]] = {}
        for nm, sexes in self.name_freq.items():
            if len(nm) != 2:
                continue
            total_w = sexes['남'] + sexes['여']
            dominant_sex = '남' if sexes['남'] >= sexes['여'] else '여'
            for ch in nm:
                self._char_to_names.setdefault(ch, []).append((nm, dominant_sex, total_w))
        # 글자별로 weight 내림차순 정렬
        for ch in self._char_to_names:
            self._char_to_names[ch].sort(key=lambda x: -x[2])

        # ---- LLM 클라이언트 (lazy init) ----
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = None
        self._client_lock = threading.Lock()

        # ---- 캐시 ----
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: Dict[str, Dict[str, str]] = {}
        self._cache_lock = threading.Lock()
        self._load_cache()

        logger.info(
            f"NameMeaning 초기화: hanja {len(self.name_hanja)}음, "
            f"surname {len(self.surname_hanja)}, "
            f"hanja_names {len(self.hanja_names)}, "
            f"urimal_names {len(self.urimal_names)}"
        )

    # ============================================================
    # 한자 매칭 (변환 결과 → 한자 후보)
    # ============================================================
    def get_name_hanja(self, given: str) -> List[List[Tuple[str, str]]]:
        """이름의 각 글자에 대한 한자 후보 리턴.

        Returns:
            [[(한자, 뜻), ...], ...] — 각 음절별 한자 후보 리스트
        """
        result = []
        for ch in given:
            candidates = self.name_hanja.get(ch, [])
            result.append(candidates)
        return result

    def get_surname_hanja(self, surname: str) -> Optional[str]:
        """성씨 한자 리턴."""
        return self.surname_hanja.get(surname)

    def pick_best_hanja(self, given: str) -> List[Tuple[str, str, str]]:
        """각 음절의 최상위 한자 후보를 골라 (음, 한자, 뜻) 리스트 반환.

        Returns:
            [(음, 한자, 뜻), ...] — 한자 매칭 실패 음절은 한자/뜻이 빈 문자열
        """
        result = []
        for ch in given:
            candidates = self.name_hanja.get(ch, [])
            if candidates:
                hanja, meaning = candidates[0]
                result.append((ch, hanja, self._clean_meaning(meaning)))
            else:
                result.append((ch, '', ''))
        return result

    @staticmethod
    def _clean_meaning(meaning: str) -> str:
        """한자 뜻 정제: 쉼표로 구분된 첫 번째 뜻만 사용."""
        if not meaning:
            return ''
        # '아름다울,착할' → '아름다울'
        return meaning.split(',')[0].strip()

    # ============================================================
    # 이름 분류 (한자어 / 우리말)
    # ============================================================
    def classify_name(self, given: str) -> str:
        """이름이 한자어인지 우리말인지 분류.

        Returns:
            'hanja' | 'urimal' | 'unknown'
        """
        if given in self.urimal_names:
            return 'urimal'
        if given in self.hanja_names:
            return 'hanja'
        # DB에 없는 이름 (외자 등): 한자 매칭이 모든 음절에 가능하면 hanja, 아니면 urimal
        all_have_hanja = all(self.name_hanja.get(ch) for ch in given)
        return 'hanja' if all_have_hanja else 'urimal'

    # ============================================================
    # 같은 글자 들어간 인기 이름 추천
    # ============================================================
    def similar_names_for_char(
        self, char: str, sex: str, exclude: str = '', max_count: int = MAX_SIMILAR_NAMES
    ) -> List[str]:
        """주어진 글자가 들어간 인기 이름들 (성별 일치 우선).

        Args:
            char: 검색 글자
            sex: '남' or '여'
            exclude: 제외할 이름 (대개 현재 변환 결과)
            max_count: 최대 개수
        """
        candidates = self._char_to_names.get(char, [])
        # 성별 일치 + exclude 제외 우선
        filtered_same_sex = [
            nm for nm, dom_sex, w in candidates
            if dom_sex == sex and nm != exclude and w > 0
        ]
        if len(filtered_same_sex) >= max_count:
            return filtered_same_sex[:max_count]
        # 부족하면 반대 성별도 추가
        rest = [
            nm for nm, dom_sex, w in candidates
            if dom_sex != sex and nm != exclude and w > 0
        ]
        return (filtered_same_sex + rest)[:max_count]

    # ============================================================
    # 메인 진입점: explain
    # ============================================================
    def explain(
        self,
        given: str,
        sex: str,
        hanja_chars: Optional[List[Tuple[str, str, str]]] = None,
        name_type: Optional[str] = None,
        urimal_meaning: Optional[str] = None,
        english_name: Optional[str] = None,
        first_kr: Optional[str] = None,
    ) -> Dict[str, str]:
        """이름의 의미를 풍부하게 설명.

        Args:
            given: 한국 이름 (예: '민준')
            sex: '남' or '여'
            hanja_chars: [(음, 한자, 뜻), ...]. None이면 자동 매칭.
            name_type: 'hanja' or 'urimal'. None이면 자동 분류.
            urimal_meaning: 우리말 이름인 경우 뜻. None이면 DB에서 조회.
            english_name: 원래 영어 이름 (예: 'Adam'). 글로벌 다리 설명에 활용.
            first_kr: 영어 이름의 음차 (예: '애덤'). 글로벌 다리 설명에 활용.

        Returns:
            {
              'meaning_kr': '...',
              'meaning_en': '...',
              'name_type': 'hanja' or 'urimal',
              'hanja_chars': [(음, 한자, 뜻), ...] or None,
              'cached': True/False,
            }
        """
        # 1. 이름 유형 결정
        if name_type is None:
            name_type = self.classify_name(given)

        # 2. 한자 매칭 (필요시)
        if name_type == 'hanja' and hanja_chars is None:
            hanja_chars = self.pick_best_hanja(given)
            # 한자가 하나라도 없으면 urimal로 폴백
            if any(not h for _, h, _ in hanja_chars):
                name_type = 'urimal'
                hanja_chars = None

        # 3. 우리말 뜻 (필요시)
        if name_type == 'urimal' and urimal_meaning is None:
            urimal_meaning = self.urimal_meanings.get(given, '')

        # 4. 캐시 확인 (영어 원이름은 캐시 키에서 제외 - 같은 한국 이름 결과는 공유)
        cache_key = self._make_cache_key(given, sex, name_type, hanja_chars, urimal_meaning)
        cached = self._cache_get(cache_key)
        if cached:
            return {
                **cached,
                'name_type': name_type,
                'hanja_chars': hanja_chars,
                'cached': True,
            }

        # 5. LLM 호출
        if name_type == 'hanja':
            prompt = self._build_hanja_prompt(given, sex, hanja_chars,
                                              english_name=english_name, first_kr=first_kr)
        else:
            prompt = self._build_urimal_prompt(given, sex, urimal_meaning,
                                               english_name=english_name, first_kr=first_kr)

        try:
            response = self._call_llm(prompt)
            parsed = self._parse_response(response)
        except Exception as e:
            logger.warning(f"LLM 호출 실패 ({given}): {e}")
            parsed = self._fallback_response(given, sex, name_type, hanja_chars, urimal_meaning)

        result = {
            'meaning_kr': parsed.get('meaning_kr', ''),
            'meaning_en': parsed.get('meaning_en', ''),
        }
        self._cache_set(cache_key, result)

        return {
            **result,
            'name_type': name_type,
            'hanja_chars': hanja_chars,
            'cached': False,
        }

    # ============================================================
    # 프롬프트 빌더
    # ============================================================
    def _build_hanja_prompt(
        self, given: str, sex: str, hanja_chars: List[Tuple[str, str, str]],
        english_name: Optional[str] = None, first_kr: Optional[str] = None,
    ) -> str:
        """한자어 이름용 프롬프트."""
        sex_label_kr = '남자' if sex == '남' else '여자'
        sex_label_en = 'male' if sex == '남' else 'female'

        # 각 글자의 한자 + 뜻
        char_info_lines = []
        for sound, hanja, meaning in hanja_chars:
            if hanja and meaning:
                char_info_lines.append(f"- {sound}({hanja}): {meaning}")
            else:
                char_info_lines.append(f"- {sound}: (한자 미매칭)")
        char_info_block = "\n".join(char_info_lines)

        # 같은 글자가 들어간 인기 이름들
        similar_names_lines = []
        for sound, _, _ in hanja_chars:
            similar = self.similar_names_for_char(sound, sex, exclude=given, max_count=MAX_SIMILAR_NAMES)
            if similar:
                similar_names_lines.append(f"- '{sound}' 자: {', '.join(similar)}")
        similar_names_block = "\n".join(similar_names_lines) if similar_names_lines else "(없음)"

        # 성별 사용 경향 (DB 빈도 기반)
        gender_info = ""
        if given in self.name_freq:
            m_w = self.name_freq[given]['남']
            f_w = self.name_freq[given]['여']
            if m_w > 0 and f_w > 0:
                ratio_m = m_w / (m_w + f_w)
                if ratio_m > 0.9:
                    gender_info = f"이 이름의 한국 작명 빈도는 남자 압도적 (남:여 = {m_w}:{f_w})"
                elif ratio_m < 0.1:
                    gender_info = f"이 이름의 한국 작명 빈도는 여자 압도적 (남:여 = {m_w}:{f_w})"
                else:
                    gender_info = f"이 이름은 남녀 모두 쓰임 (남:여 = {m_w}:{f_w})"
            elif m_w > 0:
                gender_info = f"이 이름은 주로 남자 이름 (빈도 {m_w})"
            elif f_w > 0:
                gender_info = f"이 이름은 주로 여자 이름 (빈도 {f_w})"

        # 영어 원래 이름 정보 (선택)
        english_info_block = ""
        if english_name or first_kr:
            parts = []
            if english_name:
                parts.append(f"원래 영어 이름: {english_name}")
            if first_kr:
                parts.append(f"한국어 음차: {first_kr}")
            english_info_block = f"\n[원래 이름 정보]\n" + "\n".join(parts) + "\n"

        prompt = f"""당신은 한국 이름의 의미를 외국인에게 따뜻하고 흥미롭게 설명하는 전문가입니다.
한국 이름 '{given}' ({sex_label_kr} 이름)의 의미를 한국어와 영어로 각각 작성해주세요.

[한자 정보]
{char_info_block}

[같은 글자가 들어간 한국 인기 이름 (참고)]
{similar_names_block}

[성별 사용 정보]
{gender_info or '(데이터 없음)'}
{english_info_block}

[작성 가이드]
다음 요소들을 자연스럽게 녹여서 흥미로운 스토리텔링으로 작성하세요. 백과사전식 나열이 아니라 친구에게 들려주는 듯한 따뜻한 톤으로:

1. 각 글자(한자)의 뜻을 풀어 설명
2. 두 글자가 조합되었을 때의 의미 해석 + 부모의 염원/바람
3. 이 이름의 발음 인상 — 받침 유무, 부드러움/단단함, 동글동글함/날카로움 같은 감각적 묘사
4. 한국에서의 유행 시기를 구체적으로 (예: "2010년대 후반부터 빠르게 인기", "2020년대 대표 트렌드", "옛 어른들 이름에서 자주 보이는 고전적")
5. 같은 글자가 들어간 다른 한국 인기 이름들 (위 참고 목록 적극 활용)
6. 이 이름의 성별 사용 경향 (남녀 모두 vs 한쪽 우세, 중성적 매력 등)
7. 영어 원래 이름과의 다리 — 발음이 비슷한지, 의미상 연결되는지, 글로벌하게 통하는지 (해당될 때만)
8. (선택) 확실히 아는 경우에만 문화적 레퍼런스 추가. 불확실하면 절대 추가하지 마세요.

[중요한 규칙]
- 사실 확인이 안 되는 정보(특정 K-pop 멤버의 본명, 드라마 캐릭터 등)는 절대 만들어내지 마세요.
- "~인 것 같습니다", "~일 수 있습니다" 같은 추측은 피하세요. 확실한 사실만 단정적으로 서술.
- 발음 인상을 시각적·감각적으로 묘사하세요 (예: "받침이 적어 동글동글한 인상", "두 음절 모두 또렷하고 시원해서").
- 8가지 요소를 다 우겨넣지 말고 이 이름에 가장 어울리는 3~4개를 골라 자연스럽게.
- 한국어 답변: {KR_LENGTH_HINT}, 영어 답변: {EN_LENGTH_HINT}.

[출력 형식]
다음 JSON 형식만으로 답변하세요. 마크다운 코드블록(```)이나 추가 텍스트는 절대 넣지 마세요.
{{"meaning_kr": "한국어 설명", "meaning_en": "English explanation"}}

[예시 톤 — 참고]
예 1 (트렌디한 이름):
"하(荷)는 '연꽃', 온(溫)은 '따뜻하다'라는 뜻이에요. 연꽃처럼 우아하면서도 마음이 따뜻한 사람이 되기를 바라는 이름이지요. 하온은 2010년대 후반부터 한국에서 빠르게 인기가 오른 트렌디한 남자이름이에요. 하 자는 '하준, 하율, 하성'처럼 요즘 가장 사랑받는 남자이름 첫글자 중 하나이고, 온 자는 '온유, 시온, 라온'처럼 따뜻하고 부드러운 느낌을 주는 글자로 떠오르고 있어요. 두 글자 모두 받침이 적어 발음이 부드럽고 동글동글한 인상을 주는, 요즘 한국 부모들이 가장 좋아하는 스타일의 이름이에요."

예 2 (글로벌 다리가 자연스러운 이름):
"건(虔)은 '공경하다, 정성을 다하다'라는 뜻이에요. 매사에 정성을 들이고 공경의 마음을 잃지 않는 사람이 되기를 바라는 외자(한 글자) 이름이지요. 한국에서 외자 이름은 다소 희소하지만 그만큼 짧고 강한 임팩트가 있어 기억에 남아요. 또 '건강(健康)'의 '건'과 발음이 같아 '건강하고 굳센 사람'이라는 긍정적인 연상도 자연스럽게 따라붙죠. 건 자는 '건우, 건후, 동건'처럼 든든하고 단단한 남자이름의 단골 글자예요. 영어 이름 Cal/Carl과 발음이 비슷해 외국에서도 부르기 좋고, 글로벌 환경에서도 자연스럽게 통하는 이름이에요."

예 3 (고전적인 이름):
"내(內)는 '안, 속', 식(植)은 '심다'라는 뜻이에요. 마음 안에 큰 뜻을 심고 키워가는 사람이 되기를 바라는 이름이에요. 내식은 현대 한국에서는 흔하지 않은 다소 고전적인 남자이름이에요. 내 자는 '내석, 내림'처럼 드물게 쓰이고, 식 자는 '재식, 영식, 진식'처럼 옛 어른들 이름에서 자주 보이는 정통 한자 이름의 글자예요. 요즘 신생아 이름으로는 거의 쓰이지 않지만, 그만큼 어른스럽고 듬직한 인상을 주는 이름이에요. 학자나 어른 같은 진중한 분위기를 풍기는 클래식한 이름을 찾는다면 잘 어울려요."

이제 '{given}' 이름의 설명을 작성하세요:"""
        return prompt

    def _build_urimal_prompt(
        self, given: str, sex: str, urimal_meaning: str,
        english_name: Optional[str] = None, first_kr: Optional[str] = None,
    ) -> str:
        """우리말 이름용 프롬프트."""
        sex_label_kr = '남자' if sex == '남' else '여자'
        sex_label_en = 'male' if sex == '남' else 'female'

        meaning_hint = f"이 이름의 우리말 뜻: {urimal_meaning}" if urimal_meaning else "이 이름의 뜻을 한국어 어휘 지식으로 풀이하세요."

        # 성별 사용 경향
        gender_info = ""
        if given in self.name_freq:
            m_w = self.name_freq[given]['남']
            f_w = self.name_freq[given]['여']
            if m_w > 0 and f_w > 0:
                gender_info = f"한국 작명에서 남:여 빈도 = {m_w}:{f_w}"
            elif m_w > 0:
                gender_info = f"주로 남자 이름 (빈도 {m_w})"
            elif f_w > 0:
                gender_info = f"주로 여자 이름 (빈도 {f_w})"

        # 영어 원래 이름 정보 (선택)
        english_info_block = ""
        if english_name or first_kr:
            parts = []
            if english_name:
                parts.append(f"원래 영어 이름: {english_name}")
            if first_kr:
                parts.append(f"한국어 음차: {first_kr}")
            english_info_block = f"\n[원래 이름 정보]\n" + "\n".join(parts) + "\n"

        prompt = f"""당신은 한국 이름의 의미를 외국인에게 따뜻하고 흥미롭게 설명하는 전문가입니다.
한국 우리말 이름 '{given}' ({sex_label_kr} 이름)의 의미를 한국어와 영어로 각각 작성해주세요.

[우리말 뜻 정보]
{meaning_hint}

[성별 사용 정보]
{gender_info or '(데이터 없음)'}
{english_info_block}

[작성 가이드]
다음 요소들을 자연스럽게 녹여서 흥미로운 스토리텔링으로 작성하세요. 친구에게 들려주는 듯한 따뜻한 톤으로:

1. 이 우리말의 의미를 풀어 설명 (한자어가 아니라 순수 한국어 단어임을 명시)
2. 이름에 담긴 부모의 염원/바람
3. 발음 인상 — 받침 유무, 부드러움/단단함 같은 감각적 묘사
4. 한국에서 우리말 이름의 어감·유행성 (한자어 이름과 다른 자연스러운 매력)
5. 같은 분위기의 다른 우리말 이름 예시 (해당될 때)
6. 이 이름의 성별 사용 경향
7. 영어 원래 이름과의 다리 — 발음·의미 연결 (해당될 때만)
8. (선택) 확실히 아는 경우에만 문화적 레퍼런스 추가. 불확실하면 절대 추가하지 마세요.

[중요한 규칙]
- 사실 확인이 안 되는 정보는 절대 만들어내지 마세요.
- 발음 인상을 시각적·감각적으로 묘사 (예: "받침 없이 흐르듯 부드러운", "시적이고 동화 같은").
- 8가지 다 우겨넣지 말고 이 이름에 가장 어울리는 3~4개를 골라 자연스럽게.
- 한국어 답변: {KR_LENGTH_HINT}, 영어 답변: {EN_LENGTH_HINT}.

[출력 형식]
다음 JSON 형식만으로 답변하세요. 마크다운 코드블록(```)이나 추가 텍스트는 절대 넣지 마세요.
{{"meaning_kr": "한국어 설명", "meaning_en": "English explanation"}}

[예시 톤 — 참고]
"기랑은 한국에서 흔한 이름은 아니지만 받침 없이 부드럽게 흐르는 발음이 매력적인 여자이름이에요. 두 글자 모두 받침이 없어 흐르는 듯 부드럽게 이어지고, 어딘가 시적이고 동화 같은 분위기를 풍기는 이름이에요. 비슷한 결의 우리말 이름으로는 '하랑, 다랑, 사랑'이 있어요. 한자어 이름과는 또 다른, 한국어 본연의 결을 살린 순수한 매력이 있는 이름이죠."

이제 '{given}' 이름의 설명을 작성하세요:"""
        return prompt

    # ============================================================
    # LLM 호출
    # ============================================================
    def _get_client(self):
        """anthropic 클라이언트 lazy init."""
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            if not self.api_key:
                raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")
            try:
                import anthropic
            except ImportError:
                raise RuntimeError("anthropic 라이브러리가 설치되지 않았습니다. pip install anthropic")
            self._client = anthropic.Anthropic(api_key=self.api_key)
            return self._client

    def _call_llm(self, prompt: str) -> str:
        """Claude API 호출."""
        client = self._get_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        # response.content: List[ContentBlock]
        text_parts = []
        for block in response.content:
            if hasattr(block, 'text'):
                text_parts.append(block.text)
        return ''.join(text_parts)

    # ============================================================
    # 응답 파싱
    # ============================================================
    def _parse_response(self, text: str) -> Dict[str, str]:
        """LLM 응답에서 JSON 추출."""
        # 마크다운 코드블록 제거
        text = re.sub(r'^```(?:json)?\s*\n?', '', text.strip())
        text = re.sub(r'\n?```\s*$', '', text.strip())

        # 첫 번째 JSON object 추출
        m = re.search(r'\{[^{}]*"meaning_kr"[^{}]*"meaning_en"[^{}]*\}', text, re.DOTALL)
        if not m:
            # 더 넓게 시도
            m = re.search(r'\{.*?\}', text, re.DOTALL)
        if not m:
            raise ValueError(f"JSON을 찾을 수 없음: {text[:200]}")

        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            # 종종 LLM이 따옴표 안에 따옴표를 escape 없이 쓰는 경우 처리
            # 간단한 fallback: 직접 키 추출
            kr_match = re.search(r'"meaning_kr"\s*:\s*"((?:[^"\\]|\\.)*)"', m.group(0), re.DOTALL)
            en_match = re.search(r'"meaning_en"\s*:\s*"((?:[^"\\]|\\.)*)"', m.group(0), re.DOTALL)
            if kr_match and en_match:
                return {
                    'meaning_kr': kr_match.group(1).encode().decode('unicode_escape'),
                    'meaning_en': en_match.group(1).encode().decode('unicode_escape'),
                }
            raise ValueError(f"JSON 파싱 실패: {e}, 응답: {text[:200]}")

        return {
            'meaning_kr': str(data.get('meaning_kr', '')),
            'meaning_en': str(data.get('meaning_en', '')),
        }

    # ============================================================
    # Fallback (LLM 실패 시 단순 템플릿)
    # ============================================================
    def _fallback_response(
        self,
        given: str,
        sex: str,
        name_type: str,
        hanja_chars: Optional[List[Tuple[str, str, str]]],
        urimal_meaning: Optional[str],
    ) -> Dict[str, str]:
        """LLM 호출 실패 시 최소한의 정보로 폴백 응답."""
        if name_type == 'hanja' and hanja_chars:
            char_str_kr = ', '.join(
                f"{s}({h}, '{m}')" if h else s
                for s, h, m in hanja_chars
            )
            kr = f"'{given}'은(는) 한자어 이름으로, 각 글자는 {char_str_kr}의 의미를 가집니다."
            en = f"'{given}' is a Sino-Korean name; the characters mean: {char_str_kr}."
        else:
            kr = f"'{given}'은(는) 한국어 이름입니다."
            if urimal_meaning:
                kr += f" 뜻: {urimal_meaning}."
            en = f"'{given}' is a Korean name."
            if urimal_meaning:
                en += f" Meaning: {urimal_meaning}."

        return {'meaning_kr': kr, 'meaning_en': en}

    # ============================================================
    # 캐시
    # ============================================================
    def _make_cache_key(
        self,
        given: str,
        sex: str,
        name_type: str,
        hanja_chars: Optional[List[Tuple[str, str, str]]],
        urimal_meaning: Optional[str],
    ) -> str:
        """캐시 키 생성."""
        parts = [given, sex, name_type]
        if hanja_chars:
            parts.append('|'.join(f"{s}:{h}:{m}" for s, h, m in hanja_chars))
        if urimal_meaning:
            parts.append(urimal_meaning)
        key_str = '||'.join(parts)
        return hashlib.md5(key_str.encode('utf-8')).hexdigest()

    def _load_cache(self):
        """디스크에서 캐시 로드."""
        if not self.cache_path or not self.cache_path.exists():
            return
        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                self._cache = json.load(f)
            logger.info(f"캐시 로드: {len(self._cache)}건")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"캐시 로드 실패: {e}, 빈 캐시로 시작")
            self._cache = {}

    def _save_cache(self):
        """디스크에 캐시 저장."""
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            # atomic write: tmp 파일에 쓰고 rename
            tmp_path = self.cache_path.with_suffix('.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self.cache_path)
        except OSError as e:
            logger.warning(f"캐시 저장 실패: {e}")

    def _cache_get(self, key: str) -> Optional[Dict[str, str]]:
        with self._cache_lock:
            return self._cache.get(key)

    def _cache_set(self, key: str, value: Dict[str, str]):
        with self._cache_lock:
            self._cache[key] = value
        # 매번 저장 (성능에 큰 부담 없음, 안전)
        self._save_cache()

    def cache_stats(self) -> Dict[str, int]:
        """캐시 현황."""
        with self._cache_lock:
            return {'entries': len(self._cache)}


# ============================================================
# 헬퍼: 변환 엔진 결과로부터 직접 explain 호출
# ============================================================
def explain_from_engine_result(
    meaning: NameMeaning,
    engine_result: Dict[str, Any],
    result_index: int = 1,
) -> Dict[str, str]:
    """KoreanNameEngine.convert() 결과를 받아 의미 설명 생성.

    Args:
        meaning: NameMeaning 인스턴스
        engine_result: engine.convert() 반환값
        result_index: 1 (first_1 사용) 또는 2 (first_2 사용)

    Returns:
        explain() 결과
    """
    suffix = '' if result_index == 1 else f'_{result_index}'
    given = engine_result.get(f'first{suffix if suffix else "_1"}', '')
    sex = engine_result.get('sex', '남')

    if not given:
        return {
            'meaning_kr': '',
            'meaning_en': '',
            'name_type': 'unknown',
            'hanja_chars': None,
            'cached': False,
        }

    return meaning.explain(given=given, sex=sex)

# -*- coding: utf-8 -*-
"""
LLM 생성 의미 설명의 출력 전 검수기.

meaning_en.py 가 설명을 만들면, **보여주기 전에** 다른 모델이 한 번 더 읽고
문법·내용 모순·데이터 불일치·실존 인물 언급을 고친다. 결과는 캐시에 남으므로
비용과 지연은 이름당 한 번뿐이다.

왜 필요한가
  사용자들이 사소한 문법 오류나 의미상 어긋남("같은 소리인데 almost")에
  생각보다 민감하게 반응한다. quality.py 의 정규식 검사는 정해둔 패턴만
  잡고 기록만 할 뿐 고치지 않는다. 이 모듈은 고친다.

원칙
  · 검수기는 출력을 **절대 막지 않는다.** 검수 실패 = 원문 그대로 서빙.
  · 검수기가 고친 것은 코드가 다시 검증한다(이름·한자 보존, 길이, 정규식 규칙).
    검수 LLM 자체가 오류를 만들 수 있기 때문이다. 검증 실패 = 원문 유지.
  · 무엇을 고쳤는지 전후 텍스트를 남긴다(캐시 + /admin/reviews). 안 남기면
    검수기가 이상하게 굴어도 알 수 없다.
  · 연속 실패하면 스스로 꺼진다(circuit breaker). 모델명이 틀렸거나 키가
    죽었을 때 매 요청마다 실패 호출을 반복하며 지연을 더하지 않도록.

모델
  검토는 생성보다 쉬운 일이라 작은 모델로 충분하다. 기본은 Haiku.
  Render 환경변수 MEANING_REVIEW_MODEL 로 바꿀 수 있고, 빈 문자열이면 검수를 끈다.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# 검수 규칙 버전. **체크리스트나 검증 규칙을 고칠 때마다 올린다.**
# 캐시 항목의 'r' 가 이 값과 다르면 그 항목은 다시 검수한다.
#
# v2: 체크리스트에 두 항목 추가 — 3인칭(you/your 금지)과 글자별 표기 형식.
#     둘 다 생성 프롬프트에는 있었지만 검수 체크리스트로 옮겨지지 않아,
#     검수기가 위반으로 보지 않고 통과시켰다. 실제 사고: 지건(Deacon) 설명이
#     "Your name is Deacon." 으로 끝났는데 status=edited 로 통과했다 —
#     검수기가 다른 부분만 고치고 이 문장은 규칙에 없으니 건드리지 않았다.
#     교훈: 생성 프롬프트에 규칙을 넣을 때 검수 체크리스트에도 같이 넣어야 한다.
REVIEW_VERSION = 2

DEFAULT_REVIEW_MODEL = 'claude-haiku-4-5'

# 이만큼 연속 실패하면 프로세스가 살아 있는 동안 검수를 끈다
CIRCUIT_BREAK_AFTER = 3

# 수정본 길이 허용 범위(원문 대비). 벗어나면 '고친 것'이 아니라 '다시 쓴 것'이다.
LEN_MIN, LEN_MAX = 0.6, 1.35

_CJK = re.compile(r'[一-鿿]')
_HANGUL = re.compile(r'[가-힣]')


def _syl_rom(syllable: str) -> str:
    """한 음절의 소문자 로마자. 생성기(meaning_en._syl_rom)와 같은 결과여야 한다 —
    체크리스트 10번이 '정답 표기'로 이 값을 제시하므로 어긋나면 멀쩡한 문장을
    고치라고 시키게 된다. 그래서 둘 다 romanize_syllable 하나를 쓴다."""
    try:
        from pronounce_guide import romanize_syllable
        return romanize_syllable(syllable, capitalize=False)
    except Exception:
        return ''
_DIACRITIC = re.compile(r'[áàâäãåāéèêëēěíìîïīóòôöõōøúùûüūůçčćñňńřšśžźżýÿďťł]', re.I)


@dataclass
class ReviewResult:
    text: str                       # 채택된 설명(수정본 또는 원문)
    short: str                      # 채택된 한 줄
    edited: bool = False            # 수정본이 채택됐는가
    issues: List[str] = field(default_factory=list)   # 검수기가 말한 문제
    status: str = 'ok'              # ok | edited | rejected | failed | disabled | skipped
    reason: Optional[str] = None    # rejected/failed 사유
    orig_text: Optional[str] = None
    orig_short: Optional[str] = None

    def to_cache(self) -> dict:
        """캐시 항목에 남길 요약. 원문은 수정됐을 때만 보관한다."""
        d = {'status': self.status, 'ts': int(time.time())}
        if self.issues:
            d['issues'] = self.issues[:8]
        if self.reason:
            d['reason'] = self.reason[:160]
        if self.edited:
            d['orig'] = self.orig_text
            d['orig_short'] = self.orig_short
        return d


class MeaningReviewer:
    def __init__(self, api_key: Optional[str] = None,
                 model: Optional[str] = None,
                 max_tokens: int = 700, timeout: float = 15.0):
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        env_model = os.environ.get('MEANING_REVIEW_MODEL')
        # 환경변수가 '' 이면 명시적으로 끈 것이다
        self.model = (env_model if env_model is not None else (model or DEFAULT_REVIEW_MODEL))
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client = None
        self._lock = threading.Lock()
        self.consecutive_failures = 0
        self.disabled_reason: Optional[str] = None
        self.last_error: Optional[str] = None
        self.calls = 0
        self.edits = 0

    # ------------------------------------------------------------ 상태
    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.model and not self.disabled_reason)

    def status(self) -> dict:
        """/admin 표시용."""
        return {
            'enabled': self.enabled, 'model': self.model or '(off)',
            'disabled_reason': self.disabled_reason, 'last_error': self.last_error,
            'calls': self.calls, 'edits': self.edits,
            'consecutive_failures': self.consecutive_failures,
        }

    def _client_or_raise(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key, max_retries=1,
                                               timeout=self.timeout)
        return self._client

    # ------------------------------------------------------------ 프롬프트
    @staticmethod
    def _prompt(text: str, short: str, given: str, label: str,
                hanja_chars, english_name: Optional[str]) -> str:
        if hanja_chars:
            data_lines = '; '.join(f'{s} ({h}) = {g}' for s, h, g in hanja_chars if h)
            # 체크리스트 10번이 대조할 '정답 표기'. 생성기와 같은 형식이라야
            # 하므로 같은 함수(romanize_syllable)로 만든다.
            forms = []
            for s, h, _g in hanja_chars:
                if not h:
                    continue
                r = _syl_rom(s)
                forms.append(f'{s} ({h}, {r})' if r else f'{s} ({h})')
            form_line = ('\n- Required form when naming a character: '
                         + '; '.join(forms)) if forms else ''
        else:
            data_lines = 'a native Korean name (no hanja)'
            form_line = ''
        en_line = f'\n- The reader\'s English name: {english_name}' if english_name else ''
        return (
            'You are a careful copy editor. Below is a short English explanation of a Korean '
            'name, written for the person who just received it. Check it against the DATA and '
            'the CHECKLIST. Fix only real problems with the smallest possible edit. If nothing '
            'needs changing, say so.\n\n'
            'DATA (the only facts you may rely on):\n'
            f'- Korean name: {given}\n'
            f'- The text must begin with exactly: {label}\n'
            f'- Characters and their meanings: {data_lines}{form_line}{en_line}\n\n'
            f'TEXT:\n<<<\n{text}\n>>>\n\n'
            f'SHORT (one-line caption shown on the card):\n<<<\n{short or "(none)"}\n>>>\n\n'
            'CHECKLIST:\n'
            '1. Grammar, spelling, punctuation, subject-verb agreement, articles, sentence fragments.\n'
            '2. Every stated character meaning must match DATA. Do not add meanings that are not in DATA.\n'
            '3. Internal contradictions, and hedges that contradict the facts (for example calling two '
            'identical things "almost the same", or "nearly" for an exact match).\n'
            '4. Remove any mention of a real, specific person (singers, idols, actors, athletes, '
            'historical figures) and of any specific song, show, band, film or brand. Delete the clause '
            'or replace it with a neutral phrase, keeping the sentence grammatical. Plain Korean given '
            'names used only as examples of names (e.g. "as in 채린") are fine and must stay.\n'
            '5. The English name\'s own meaning must not be presented as the Korean name\'s meaning.\n'
            f'6. Keep the opening "{label}" exactly. Keep every Korean syllable and every hanja that is '
            'in DATA exactly as written; add no new hanja and no new Korean names.\n'
            '7. Keep the length, voice and warmth. Do not rewrite sentences that are already fine.\n'
            '8. SHORT must be one grammatical English noun phrase, 4-9 words, 30-55 characters, about '
            'the meaning only (not the sound, not the English name), starting with a capital letter, '
            'no ending punctuation, no Korean, hanja or romanization.\n'
            # 9·10 은 생성 프롬프트에만 있던 규칙이다. 검수 체크리스트에 없으면
            # 검수기는 위반을 위반으로 보지 않는다(지건의 "Your name is Deacon.").
            '9. Third person only. The text must never address the reader as "you" or "your", and '
            'must not tell them what their name is. Rewrite such a sentence to speak about the '
            'name or about "someone", or delete it if it adds nothing. This applies even when the '
            'sentence is grammatical.\n'
            '10. Whenever the text names an individual character, it must use the exact form given '
            'in DATA above ("Required form when naming a character") — Korean syllable, then hanja '
            'and lowercase romanization in parentheses, in that order. Fix a character written as '
            'hanja alone, as the syllable alone, with the meaning inside the parentheses, or with '
            'the contents reordered. Do not add a character that the text never mentions, and do '
            'not touch the opening label.\n\n'
            'Respond with JSON only, no prose:\n'
            '{"ok": true}\n'
            'or\n'
            '{"ok": false, "issues": ["one short note per fix"], "text": "<full corrected TEXT>", '
            '"short": "<corrected SHORT, or the original SHORT if it was fine>"}'
        )

    # ------------------------------------------------------------ 사후 검증
    @staticmethod
    def validate(orig: str, new: str, given: str, label: str) -> Optional[str]:
        """수정본을 받아들여도 되는가. 문제가 있으면 사유(한국어), 없으면 None.

        검수 LLM 이 만든 오류를 여기서 걸러낸다. 규칙은 전부 결정론적이다.
        """
        new = (new or '').strip()
        if not new:
            return '빈 수정본'
        if new == orig:
            return None
        if '{{' in new or '}}' in new:
            return '자리표시자 잔존'
        if _DIACRITIC.search(new):
            return '발음기호 문자'
        if label and orig.startswith(label) and not new.startswith(label):
            return '도입부 라벨 훼손'
        if given and given not in new:
            return '한글 이름 사라짐'
        ratio = len(new) / max(1, len(orig))
        if not (LEN_MIN <= ratio <= LEN_MAX):
            return f'길이 이탈 ({ratio:.2f}x)'
        # 한자: 새 한자 금지, 원문 한자 보존
        cjk_o, cjk_n = set(_CJK.findall(orig)), set(_CJK.findall(new))
        if cjk_n - cjk_o:
            return f'새 한자 추가 ({"".join(sorted(cjk_n - cjk_o))})'
        if cjk_o - cjk_n:
            return f'한자 사라짐 ({"".join(sorted(cjk_o - cjk_n))})'
        # 한글: 새 음절 금지(없던 이름을 지어내는 것). 삭제는 허용(인물 언급 제거 등)
        hg_o, hg_n = set(_HANGUL.findall(orig)), set(_HANGUL.findall(new))
        if hg_n - hg_o:
            return f'새 한글 추가 ({"".join(sorted(hg_n - hg_o))})'
        # 정규식 비문 규칙(quality.py) — 수정본이 **새** 오류를 들여오면 거부.
        # check_grammar() 는 한 줄(short)용이라 '한글 혼입' 규칙이 들어 있는데,
        # 설명문에는 한글 이름이 원래 들어가므로 그 규칙은 빼고 본다.
        try:
            from quality import BAD_PATTERNS
            hits_o = {why for rx, why in BAD_PATTERNS if why != '한글 혼입' and rx.search(orig)}
            hits_n = {why for rx, why in BAD_PATTERNS if why != '한글 혼입' and rx.search(new)}
            fresh = hits_n - hits_o
            if fresh:
                return f'비문 규칙 위반 ({", ".join(sorted(fresh))})'
        except Exception:
            pass
        return None

    # ------------------------------------------------------------ 검수
    def review(self, text: str, short: str, given: str, label: str,
               hanja_chars=None, english_name: Optional[str] = None,
               clean_short=None) -> ReviewResult:
        """
        설명·한 줄을 검수해 채택본을 돌려준다. 어떤 경우에도 예외를 던지지 않는다.
        clean_short: meaning_en._clean_short — 수정된 한 줄의 형식 검사에 쓴다.
        """
        base = ReviewResult(text=text, short=short, orig_text=text, orig_short=short)
        if not text:
            base.status = 'skipped'; return base
        if not self.enabled:
            base.status = 'disabled'
            base.reason = self.disabled_reason or ('no-key' if not self.api_key else 'off')
            return base

        prompt = self._prompt(text, short, given, label, hanja_chars, english_name)
        try:
            client = self._client_or_raise()
            with self._lock:
                self.calls += 1
            resp = client.messages.create(
                model=self.model, max_tokens=self.max_tokens,
                messages=[{'role': 'user', 'content': prompt}],
            )
            raw = ''.join(b.text for b in resp.content if hasattr(b, 'text')).strip()
        except Exception as e:
            return self._fail(base, f'{type(e).__name__}: {str(e)[:120]}')

        obj = self._parse(raw)
        if obj is None:
            return self._fail(base, 'JSON 파싱 실패')
        # 여기까지 오면 호출 자체는 성공 — 실패 카운터를 되돌린다
        self.consecutive_failures = 0
        self.last_error = None

        if obj.get('ok') is True or 'text' not in obj:
            base.status = 'ok'
            return base

        new_text = re.sub(r'\s+', ' ', str(obj.get('text') or '')).strip()
        issues = [str(i)[:120] for i in (obj.get('issues') or []) if str(i).strip()]
        why = self.validate(text, new_text, given, label)
        if why:
            base.status, base.reason, base.issues = 'rejected', why, issues
            return base
        if new_text == text:
            base.status = 'ok'
            return base

        # 한 줄: 수정본이 형식 검사를 통과할 때만 바꾼다
        new_short = str(obj.get('short') or '').strip()
        if clean_short:
            new_short = clean_short(new_short)
        if not new_short:
            new_short = short

        with self._lock:
            self.edits += 1
        return ReviewResult(text=new_text, short=new_short, edited=True, issues=issues,
                            status='edited', orig_text=text, orig_short=short)

    # ------------------------------------------------------------ 내부
    def _fail(self, base: ReviewResult, reason: str) -> ReviewResult:
        self.consecutive_failures += 1
        self.last_error = reason
        base.status, base.reason = 'failed', reason
        if self.consecutive_failures >= CIRCUIT_BREAK_AFTER and not self.disabled_reason:
            self.disabled_reason = f'{self.consecutive_failures}회 연속 실패: {reason}'
        return base

    @staticmethod
    def _parse(raw: str) -> Optional[dict]:
        if not raw:
            return None
        s = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip()).strip()
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        m = re.search(r'\{.*\}', s, re.S)      # 앞뒤에 말을 붙인 경우
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except Exception:
                return None
        return None

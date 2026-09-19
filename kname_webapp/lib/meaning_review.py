# -*- coding: utf-8 -*-
"""
LLM 생성 의미 설명의 출력 전 검수기.

meaning_en.py 가 설명을 만들면, **보여주기 전에** 다른 모델이 한 번 더 읽고
**두 가지만** 고친다: ① 문법 오류, ② 뜻이 안 맞는 부자연스러운 표현(같은 소리
아→아 에 "almost" 를 붙이는 것 같은). 그 밖의 것은 보지 않는다 — 실존 인물,
한자 뜻 대조, 표기 형식, 3인칭은 생성 프롬프트와 코드가 맡는다(v6, 사용자 결정).
결과는 캐시에 남으므로 비용과 지연은 이름당 한 번뿐이다.

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
# v3: v2 의 9·10번이 **과잉 반응**했다(100개 배치 2차: 수정 9건 중 7건이 근거 없는
#     지적 — 'someone will build a life' 를 2인칭이라며, '희 (喜, hui)' 를
#     '희 (喜, hui)' 로 고치라며). 두 규칙을 '어떤 낱말이 있으면 위반' 식으로
#     기계적으로 다시 쓰고, 11번(지적은 원문을 인용할 것)을 더했다. 그리고
#     코드가 확인 가능한 9·10번만 지적됐는데 원문에 근거가 없으면 수정본을
#     거부한다(unfounded()). 5번은 생성 규칙과 같은 폭으로(영어 이름 한 번
#     언급 허용). 검수 DATA 의 뜻을 한국어→영어로 바꿔 생성기와 같은 낱말을 본다.
# v4: 체크리스트를 **넷으로 되돌림** — 문법·부자연스러운 영어·실존 인물·뜻 대조.
#     3인칭·글자 표기·영어 이름 언급·SHORT 형식은 검수기에서 뺐다(코드가 판정:
#     batch_test 의 SECOND_PERSON / char_form_misses, meaning_en._clean_short,
#     validate 의 라벨·한자 보존). 그리고 지적마다 원문 인용을 요구하고, 인용이
#     원문에 없으면 수정본을 버린다(quotes_missing). 1차 배치(8개 항목)에서는
#     실제 catch 만 있었고 2차(11개 항목)에서 근거 없는 수정이 7/9 였다 —
#     항목을 늘릴수록 검수기는 없는 문제를 찾는다.
# v5: v4 초판이 DATA 에서 **입력 이름을 빼먹은 것**을 되돌린다. 검수기는 본문
#     끝의 'beside Kaede' 에서 Kaede 가 누구인지 알 수 없었고, 3번 규칙만 남은
#     상태에서 일본 이름을 애니 캐릭터로 읽어 그 문장을 지웠다(일본 배치 25건
#     중 14건). 3번에 "입력 이름은 위반이 아니다"를 명시했다.
#     교훈: 규칙을 줄이는 것과 **맥락을 줄이는 것**은 다르다. 판단에 필요한
#     사실은 남겨야 한다 — 빼면 모델은 없는 사실을 지어낸다.
# v6: 사용자 결정 — 역할을 **둘로 고정**: ① 문법 오류 교정, ② 뜻이 안 맞는 부자연
#     스러운 표현 교정(아→아 에 "almost" 같은 것). 실존 인물·한자 뜻 대조는 검수
#     대상에서 뺐다(생성 프롬프트가 지키게 한다). DATA 에서 한자 뜻도 뺐다 —
#     주면 대조하고 싶어진다. 입력 이름만 남긴다(v5 교훈).
REVIEW_VERSION = 6

DEFAULT_REVIEW_MODEL = 'claude-haiku-4-5'

# 이만큼 연속 실패하면 프로세스가 살아 있는 동안 검수를 끈다
CIRCUIT_BREAK_AFTER = 3

# 수정본 길이 허용 범위(원문 대비). 벗어나면 '고친 것'이 아니라 '다시 쓴 것'이다.
LEN_MIN, LEN_MAX = 0.6, 1.35

_CJK = re.compile(r'[一-鿿]')
_HANGUL = re.compile(r'[가-힣]')


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
        """검수 프롬프트 — 역할은 **둘뿐이다** (v6, 사용자 결정).

          ① 문법·기계적 오류를 고친다.
          ② 뜻이 안 맞는 부자연스러운 표현을 고친다 — 대표 사례: 같은 소리 아→아 에
             "almost" 를 붙이는 것. 같은 것에 '거의' 는 틀린 말이다.

        그 밖의 것은 **아예 보지 않는다**: 실존 인물·작품 언급, 한자 뜻이 데이터와
        맞는지, 글자 표기 형식, 3인칭, 입력 이름 언급, 라벨, 어조·길이.
        v2~v5 에서 그런 항목을 얹을 때마다 검수기는 멀쩡한 문장을 그 항목 위반이라며
        고쳤다(2차 7/9, 3차 14/19). 그 항목들은 생성 프롬프트가 이미 지키게 하거나
        코드가 판정한다.

        지적은 원문 인용이 필수다(quotes_missing). 입력 이름은 지우면 안 된다
        (name_dropped). 라벨·한자·길이는 validate 가 지킨다.

        DATA 에 한자 뜻을 **주지 않는다** — 주면 대조하고 싶어진다. 입력 이름만
        알려 준다: 본문 끝의 'beside Kaede' 가 누구인지 모르면 없애려 들기 때문이다.
        """
        own_line = (f'\n- The name this person typed in (their own name, in any language): '
                    f'{english_name}. Its one mention near the end is intentional.'
                    if english_name else '')
        return (
            'You are a copy editor. Below is a short English explanation of a Korean name. '
            'Read it as a native English speaker would. You have exactly two jobs; anything '
            'outside them is not your concern. Most texts need nothing; then answer '
            '{"ok": true}.\n\n'
            'DATA:\n'
            f'- Korean name: {given}{own_line}\n\n'
            f'TEXT:\n<<<\n{text}\n>>>\n\n'
            'JOB 1 \u2014 grammar and mechanics: spelling, punctuation, subject-verb agreement, '
            'wrong or missing articles, wrong prepositions, sentence fragments, a sentence that '
            'stops mid-way, a word repeated by mistake.\n'
            'JOB 2 \u2014 wording that does not make sense: a phrase whose words contradict '
            'each other or the facts stated in the same text. The typical case is a hedge '
            'applied to identical things: "the 아 sound maps almost exactly onto 아" or '
            '"nearly the same as" when the two are the same \u2014 there, delete the hedge '
            '("maps exactly onto", "the same as"). Also a word used with the wrong sense, or '
            'an expression a native speaker would not write.\n\n'
            'NOT your concern \u2014 leave these exactly as they are even if you would have '
            'written them differently: what the characters are said to mean; any name or '
            'person the text mentions, including the name this person typed in; the form in '
            'which characters are written, such as \ud61c (\u60e0, hye); how the text refers to '
            'the person (someone, they, their); the opening label; the length, voice, warmth '
            'and word choice of sentences that are already correct.\n\n'
            'CONSTRAINTS on any fix: keep the opening exactly as it is; keep every Korean '
            'syllable and every hanja exactly as written; change as few words as possible.\n\n'
            'Respond with JSON only, no prose:\n'
            '{"ok": true}\n'
            'or\n'
            '{"ok": false, "issues": [{"quote": "<the exact wrong words, copied verbatim from '
            'TEXT>", "fix": "<what you changed them to>"}], "text": "<full corrected TEXT>"}\n'
            'Every issue must carry a "quote" copied verbatim from TEXT. If you cannot quote '
            'the wrong words, there is no issue.'
        )

    # ------------------------------------------------------------ 사후 검증
    @staticmethod
    def name_dropped(orig: str, new: str, english_name: Optional[str]) -> Optional[str]:
        """입력 이름이 원문에 있었는데 수정본에서 사라졌으면 그 사유. 아니면 None.

        본문 끝에서 입력 이름을 한 번 언급하는 것은 설계된 동작이다 — 사용자에게
        '당신 이름과 이렇게 닮았다'고 말해 주는 문장이고, 이 서비스의 요점이다.
        검수기가 그것을 지운 적이 있다(일본 이름 배치 25건 중 14건). 프롬프트로
        '지우지 말라'고 했지만, 지켜졌는지는 코드가 세어야 안다.

        원문에 없었으면 아무 제약도 걸지 않는다(언급은 선택이다).
        """
        n = (english_name or '').strip()
        if not n or len(n) < 2:
            return None
        if n.lower() in (orig or '').lower() and n.lower() not in (new or '').lower():
            return f'입력 이름 삭제 ({n})'
        return None

    @staticmethod
    def quotes_missing(orig: str, issues) -> Optional[str]:
        """지적에 원문 인용이 없거나, 인용이 원문에 없으면 그 사유. 정상이면 None.

        검수기가 고치려면 '어디가 틀렸는지'를 원문 그대로 인용해야 한다. 인용할
        수 없는 지적은 지적이 아니다 — v2·v3 에서 근거 없는 지적으로 멀쩡한
        문장을 다시 쓴 일이 9건 중 7건이었다. 규칙 이름을 따지는 대신(9번이냐
        10번이냐) 이 한 가지로 전부 거른다: 짚을 수 없으면 고칠 수 없다.
        """
        if not issues:
            return '지적 없음'
        norm = lambda t: re.sub(r'\s+', ' ', (t or '')).strip().lower()
        o = norm(orig)
        for it in issues:
            q = it.get('quote') if isinstance(it, dict) else None
            if not q or not str(q).strip():
                return '인용 없는 지적'
            if norm(str(q)) not in o:
                return f'원문에 없는 인용 ({str(q)[:40]!r})'
        return None

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
        raw_issues = [i for i in (obj.get('issues') or []) if i]
        # 기록용 요약: '인용 → 고침'. (예전 캐시는 문자열 목록이라 그대로 둔다)
        issues = [(f'{i.get("quote", "")!s} → {i.get("fix", "")!s}' if isinstance(i, dict)
                   else str(i))[:120] for i in raw_issues]
        why = (self.quotes_missing(text, raw_issues)
               or self.name_dropped(text, new_text, english_name)
               or self.validate(text, new_text, given, label))
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

# -*- coding: utf-8 -*-
"""meaning_review 단위 테스트 — API 없이 모의 응답으로 채택/거부/실패/차단 경로를 확인한다.

    python tools/test_meaning_review.py        # 전부 통과하면 'all N checks passed'

검수기 규칙을 고칠 때마다 돌린다. 검수기는 LLM 이지만 '수정본을 받을지'는
코드가 정한다(quotes_missing · validate) — 그 코드가 여기서 검증된다.
"""
import sys, json, os, types
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'lib')); sys.path.insert(0, os.path.join(BASE, 'data'))
os.environ.pop('MEANING_REVIEW_MODEL', None)

# anthropic 모듈이 없는 환경 — 가짜를 끼운다
fake = types.ModuleType('anthropic')
class _Resp:
    def __init__(self, text): self.content = [types.SimpleNamespace(text=text)]
class _Msgs:
    def __init__(self, outer): self.o = outer
    def create(self, **kw):
        self.o.calls.append(kw)
        nxt = self.o.script.pop(0)
        if isinstance(nxt, Exception): raise nxt
        return _Resp(nxt)
class Anthropic:
    script = []; calls = []
    def __init__(self, **kw): self.messages = _Msgs(self)
fake.Anthropic = Anthropic
sys.modules['anthropic'] = fake

from meaning_review import MeaningReviewer, REVIEW_VERSION, CIRCUIT_BREAK_AFTER
from meaning_en import MeaningEnGenerator, PROMPT_VERSION

GIVEN, LABEL = '예린', '예린 (叡璘, Yerin)'
CHARS = [('예', '叡', 'wise, bright'), ('린', '璘', 'jade radiance')]
ORIG = (f'{LABEL} joins 예 (叡, ye), "bright and wise," with 린 (璘, rin), "the luster of jade." '
        'It\'s a much-loved girl\'s name — K-pop fans know it as Yerin of GFRIEND. '
        '예 is a top-favored syllable, as in 예은 (Yeeun); 린 sparkles, as in 채린 (Chaerin).')
SHORT = 'Someone wise who shines like jade'

def R(script):
    Anthropic.script = list(script); Anthropic.calls = []
    r = MeaningReviewer(api_key='k'); return r

passed = 0
def ok(cond, msg):
    global passed
    assert cond, msg; passed += 1; print('  ✓', msg)

print('1) ok=true → 원문 그대로')
r = R(['{"ok": true}'])
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'ok' and not res.edited and res.text == ORIG, 'status ok, unchanged')

print('2) 인물 언급 제거 수정본 → 채택')
FIXED = ORIG.replace(' — K-pop fans know it as Yerin of GFRIEND', '')
r = R([json.dumps({'ok': False, 'issues': [{'quote': 'K-pop fans know it as Yerin of GFRIEND', 'fix': 'removed'}], 'text': FIXED, 'short': SHORT})])
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'edited' and res.edited and res.text == FIXED, 'edited & accepted')
ok(res.orig_text == ORIG and res.issues == ['K-pop fans know it as Yerin of GFRIEND → removed'], 'orig kept, issues kept')
ok('GFRIEND' not in res.text and '채린' in res.text, 'person removed, example name kept')

print('3) 검수기가 새 한자를 지어냄 → 거부')
BAD = ORIG.replace('"the luster of jade."', '"the luster of jade (琳)."')
r = R([json.dumps({'ok': False, 'issues': [{'quote': 'K-pop fans know it as Yerin of GFRIEND', 'fix': 'removed'}], 'text': BAD, 'short': SHORT})])
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'rejected' and res.text == ORIG and '한자' in res.reason, f'rejected: {res.reason}')

print('4) 검수기가 없던 한국 이름을 지어냄 → 거부')
BAD = ORIG + ' It recalls 수아 too.'
r = R([json.dumps({'ok': False, 'issues': [{'quote': 'K-pop fans know it as Yerin of GFRIEND', 'fix': 'removed'}], 'text': BAD, 'short': SHORT})])
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'rejected' and '새 한글' in res.reason, f'rejected: {res.reason}')

print('5) 도입부 라벨 훼손 → 거부')
BAD = 'Yerin joins ' + ORIG[len(LABEL) + 7:]
r = R([json.dumps({'ok': False, 'issues': [{'quote': 'K-pop fans know it as Yerin of GFRIEND', 'fix': 'removed'}], 'text': BAD, 'short': SHORT})])
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'rejected' and '라벨' in res.reason, f'rejected: {res.reason}')

print('6) 통째로 다시 씀(길이 이탈) → 거부')
BAD = LABEL + ' is nice.'
r = R([json.dumps({'ok': False, 'issues': [{'quote': 'K-pop fans know it as Yerin of GFRIEND', 'fix': 'removed'}], 'text': BAD, 'short': SHORT})])
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'rejected' and '길이' in res.reason, f'rejected: {res.reason}')

print('7) 수정본이 정규식 비문 규칙을 새로 위반 → 거부')
BAD = ORIG.replace('린 sparkles,', '린 sparkles with a grace,')   # 'with a grace' → 추상명사에 관사
r = R([json.dumps({'ok': False, 'issues': [{'quote': 'K-pop fans know it as Yerin of GFRIEND', 'fix': 'removed'}], 'text': BAD, 'short': SHORT})])
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'rejected' and '비문' in res.reason, f'rejected: {res.reason}')

print('8) 한 줄(short) 수정본이 형식 검사 실패 → 본문만 채택, short 는 원본 유지')
r = R([json.dumps({'ok': False, 'issues': [{'quote': 'K-pop fans know it as Yerin of GFRIEND', 'fix': 'removed'}], 'text': FIXED, 'short': 'Wisdom'})])
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'edited' and res.short == SHORT, 'bad short discarded, original short kept')

print('8b) 인용 없는 지적(v2 방식 문자열) → 거부')
r = R([json.dumps({'ok': False, 'issues': ['Checklist 9: someone is second person'], 'text': FIXED, 'short': SHORT})])
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'rejected' and '인용' in (res.reason or ''), f'rejected: {res.reason}')
print('8c) 원문에 없는 인용 → 거부')
r = R([json.dumps({'ok': False, 'issues': [{'quote': 'Your name is Emily', 'fix': 'x'}], 'text': FIXED, 'short': SHORT})])
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'rejected' and '원문에 없는 인용' in (res.reason or ''), f'rejected: {res.reason}')

print('8d) 입력 이름을 지운 수정본 → 거부 (name_dropped)')
ORIG_N = ORIG + ' Abroad, it settles comfortably beside Emily.'
r = R([json.dumps({'ok': False, 'issues': [{'quote': 'beside Emily', 'fix': 'beside a familiar name'}],
                   'text': ORIG_N.replace('beside Emily', 'beside a familiar name'), 'short': SHORT})])
res = r.review(ORIG_N, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'rejected' and '입력 이름 삭제' in (res.reason or ''), f'rejected: {res.reason}')

print('8e) 입력 이름을 남긴 수정본 → 채택')
r = R([json.dumps({'ok': False, 'issues': [{'quote': 'K-pop fans know it as Yerin of GFRIEND', 'fix': 'removed'}],
                   'text': FIXED + ' Abroad, it settles comfortably beside Emily.', 'short': SHORT})])
res = r.review(ORIG_N, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'edited' and 'Emily' in res.text, 'edited, input name kept')

print('9) 코드펜스·앞뒤 말 섞인 JSON 도 파싱')
r = R(['Sure! ```json\n{"ok": true}\n```'])
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'ok', 'fenced json parsed')

print('10) API 예외 → failed, 원문 유지, 연속 3회면 차단')
r = R([RuntimeError('model not found')] * CIRCUIT_BREAK_AFTER + ['{"ok": true}'])
for i in range(CIRCUIT_BREAK_AFTER):
    res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
    ok(res.status == 'failed' and res.text == ORIG, f'failure {i+1} keeps original')
ok(not r.enabled and r.disabled_reason, f'circuit open: {r.disabled_reason}')
res = r.review(ORIG, SHORT, GIVEN, LABEL, CHARS, 'Emily', MeaningEnGenerator._clean_short)
ok(res.status == 'disabled' and len(Anthropic.calls) == CIRCUIT_BREAK_AFTER, 'no further API calls once disabled')

print('11) MEANING_REVIEW_MODEL="" → 검수 끔')
os.environ['MEANING_REVIEW_MODEL'] = ''
r = MeaningReviewer(api_key='k')
ok(not r.enabled and r.model == '', 'empty env disables')
del os.environ['MEANING_REVIEW_MODEL']

print('12) 생성기 통합: 생성 → 검수 → 캐시(r 표시), GET 은 검수 안 함, 실패 항목은 POST 에서 재시도')
class _NM:  # meaning.NameMeaning 대역
    def similar_names_for_char(self, *a, **k): return []
import tempfile
cp = tempfile.mktemp(suffix='.json')
Anthropic.script = [ORIG.replace(LABEL, '{{NAME}}') + '\nSHORT: ' + SHORT,          # 생성
                    json.dumps({'ok': False, 'issues': [{'quote': 'K-pop fans know it as Yerin of GFRIEND', 'fix': 'removed'}], 'text': FIXED, 'short': SHORT})]  # 검수
Anthropic.calls = []
g = MeaningEnGenerator(_NM(), api_key='k', cache_path=cp)
text, short = g.explain_pair(GIVEN, '여', CHARS, 'Emily')
ok(text == FIXED and short == SHORT, 'served text is the reviewed one')
ent = json.load(open(cp, encoding='utf-8'))[f'{GIVEN}:여:Emily']
ok(ent['v'] == PROMPT_VERSION and ent['r'] == REVIEW_VERSION and ent['review']['status'] == 'edited'
   and ent['review']['orig'] == ORIG, 'cache carries r + before/after')
ok(g.last_review['status'] == 'edited', 'last_review exposed for stats')
ncalls = len(Anthropic.calls)
text2, _ = g.explain_pair(GIVEN, '여', CHARS, 'Emily', allow_llm=False)
ok(text2 == FIXED and len(Anthropic.calls) == ncalls and g.last_review is None, 'cache hit: no calls, no review log')

# 검수 실패했던 항목: r 없음 → GET 은 건너뛰고 POST 에서 재검수
Anthropic.script = [ORIG.replace(LABEL, '{{NAME}}') + '\nSHORT: ' + SHORT, RuntimeError('boom')]
g2 = MeaningEnGenerator(_NM(), api_key='k', cache_path=tempfile.mktemp(suffix='.json'))
t, s = g2.explain_pair('민수', '남', [('민', '旻', 'sky'), ('수', '秀', 'excellent')], 'Sam')
ok(g2.last_review['status'] == 'failed' and 'r' not in g2._cache['민수:남:Sam'], 'failed review: no r flag')
n = len(Anthropic.calls)
g2.explain_pair('민수', '남', [('민', '旻', 'sky'), ('수', '秀', 'excellent')], 'Sam', allow_llm=False)
ok(len(Anthropic.calls) == n, 'GET path does not retry review')
Anthropic.script = ['{"ok": true}']
g2.explain_pair('민수', '남', [('민', '旻', 'sky'), ('수', '秀', 'excellent')], 'Sam', allow_llm=True)
ok(len(Anthropic.calls) == n + 1 and g2._cache['민수:남:Sam'].get('r') == REVIEW_VERSION, 'POST path retries once and marks r')

print(f'\nall {passed} checks passed')

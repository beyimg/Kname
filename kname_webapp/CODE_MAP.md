# 코드 구성 안내

## 실행에 필요한 것은 `kname_webapp/` 하나뿐

```
kname_webapp/
├── app.py                    Flask 웹앱 본체
├── templates/                입력 페이지 · 결과 페이지
├── static/                   CSS · 폰트 · JS
├── standalone/               서버 없이 열어보는 미리보기
├── data/                     사전 · DB (아래 참고)
└── lib/                      로직 모듈 8개
```

## lib/ 모듈 8개가 하는 일

| 파일 | 역할 | 추가 시점 |
|---|---|---|
| `engine.py` | **변환 엔진** — 한글 음차를 한국 이름·성씨로 매칭 | 기존 |
| `transliterate.py` | **음차** — 영어를 한글로 (사전 → 캐시 → LLM) | ★ 신규 |
| `phonetics.py` | 음성학 자질 모델 (조음위치·혀높이 등) | ★ 신규 |
| `syllable_match.py` | 음절 매칭 계산 | 기존 |
| `match_phrasing.py` | 유사도 → 설명 문구 + 색 (strong/partial/soft/loose) | 기존 |
| `pronounce_guide.py` | 한글 → 영어 발음 표기 | 기존 |
| `conversion_reason.py` | 변환 이유 4단계 조립 | 기존 |
| `surname_reason.py` | 성씨 유래 + "성이 앞에 온다" 안내 | 기존 |
| `meaning.py` | **의미 생성** — 602개 밖 이름의 한자·의미설명을 LLM으로 | ★ 연결됨 |

## 전체 흐름

```
Sophia Kowalski
   ↓  transliterate.py   ← 사전에 없으면 LLM
소피아 / 코왈스키
   ↓  engine.py
수아(秀雅) / 고(高)
   ↓  data/dict_translit_to_result_full.json
한자 뜻 · 의미 설명 · 성씨 유래
   ↓  conversion_reason.py (+ phonetics · syllable_match · match_phrasing)
"왜 이 이름인가" 4단계 설명
```

## data/ 파일

| 파일 | 용도 |
|---|---|
| `dict_name_to_translit.json` | 영어 → 한글 음차 (이름 1,978 / 성씨 300) |
| `dict_translit_to_result_full.json` | 음차 → 결과 (한자·의미설명 602개·성씨 29개) |
| `merged_meaningful.xlsx` | 엔진이 고르는 한국 이름 후보 풀 |
| `인명용_한자사전.xlsx` | 한자 뜻 818자 |

## 2단계 폴백 구조

사전에 있는 이름은 미리 만든 데이터를, 없으면 LLM으로 실시간 생성합니다.

| 단계 | 사전 안 | 사전 밖 |
|---|---|---|
| 음차 | dict_name_to_translit.json | `transliterate.py` (LLM) |
| 변환 | `engine.py` | `engine.py` (동일) |
| 한자·의미 | 602개 미리 작성 | `meaning.py` (LLM) |
| 성씨 유래 | 133개 미리 작성 | 133개 미리 작성 (동일) |
| 변환 이유 | `conversion_reason` | `conversion_reason` (동일) |

**성씨 유래 설명 133개**
엔진이 매칭할 수 있는 한국 성씨는 133개인데,
초기에는 성씨 음차 사전(300개)에서 나오는 29개분만 작성돼 있었습니다.
LLM 음차로 사전 밖 이름을 받게 되면서 나머지 104개가 노출되어,
`data/surname_info_extra.py`에 보충했습니다.
순위·비율은 `data/성씨별_한자.xlsx`(192개 성씨 + 인구수) 기준입니다.

**왜 meaning.py가 필요한가**
엔진의 후보 풀(`merged_meaningful.xlsx`)은 34,466개인데
미리 작성된 의미 설명은 602개(1.7%)뿐입니다.
사전 안 이름은 미리 만든 매핑을 타서 602개로 수렴하지만,
사전 밖 이름은 후보 풀 전체가 대상이라 대부분 설명이 없는 이름으로 떨어집니다.

## 실행

```bash
pip install flask pandas openpyxl anthropic
export ANTHROPIC_API_KEY=...      # 사전 밖 이름 음차용 (없으면 사전 내 이름만 동작)
python app.py
```

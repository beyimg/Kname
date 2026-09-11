# -*- coding: utf-8 -*-
"""
국적 목록 — 입력 폼의 드롭박스와 로그 저장에 쓴다.

값은 ISO 3166-1 alpha-2 코드(2글자)로 저장한다. 나라 이름을 그대로 넣으면
표기가 흔들리고(USA / U.S. / United States) 집계가 갈라진다.

목록은 K-pop·K-드라마 팬층이 실제로 분포하는 지역을 중심으로 고르되,
빠진 나라가 있으면 사용자가 'Other'를 고르게 된다. 그래서 되도록 넓게 담았다.
순서는 알파벳순 — 사용자가 훑어 찾기 쉽다.
"""

COUNTRIES = [
    ('AR', 'Argentina'), ('AU', 'Australia'), ('AT', 'Austria'),
    ('BD', 'Bangladesh'), ('BE', 'Belgium'), ('BO', 'Bolivia'),
    ('BR', 'Brazil'), ('BG', 'Bulgaria'), ('KH', 'Cambodia'),
    ('CA', 'Canada'), ('CL', 'Chile'), ('CN', 'China'),
    ('CO', 'Colombia'), ('CR', 'Costa Rica'), ('HR', 'Croatia'),
    ('CZ', 'Czechia'), ('DK', 'Denmark'), ('DO', 'Dominican Republic'),
    ('EC', 'Ecuador'), ('EG', 'Egypt'), ('SV', 'El Salvador'),
    ('EE', 'Estonia'), ('FI', 'Finland'), ('FR', 'France'),
    ('DE', 'Germany'), ('GR', 'Greece'), ('GT', 'Guatemala'),
    ('HK', 'Hong Kong'), ('HU', 'Hungary'), ('IS', 'Iceland'),
    ('IN', 'India'), ('ID', 'Indonesia'), ('IR', 'Iran'),
    ('IQ', 'Iraq'), ('IE', 'Ireland'), ('IL', 'Israel'),
    ('IT', 'Italy'), ('JP', 'Japan'), ('JO', 'Jordan'),
    ('KZ', 'Kazakhstan'), ('KE', 'Kenya'), ('KW', 'Kuwait'),
    ('LV', 'Latvia'), ('LB', 'Lebanon'), ('LT', 'Lithuania'),
    ('MY', 'Malaysia'), ('MX', 'Mexico'), ('MA', 'Morocco'),
    ('MM', 'Myanmar'), ('NP', 'Nepal'), ('NL', 'Netherlands'),
    ('NZ', 'New Zealand'), ('NG', 'Nigeria'), ('NO', 'Norway'),
    ('PK', 'Pakistan'), ('PS', 'Palestine'), ('PA', 'Panama'),
    ('PY', 'Paraguay'), ('PE', 'Peru'), ('PH', 'Philippines'),
    ('PL', 'Poland'), ('PT', 'Portugal'), ('PR', 'Puerto Rico'),
    ('QA', 'Qatar'), ('RO', 'Romania'), ('RU', 'Russia'),
    ('SA', 'Saudi Arabia'), ('RS', 'Serbia'), ('SG', 'Singapore'),
    ('SK', 'Slovakia'), ('SI', 'Slovenia'), ('ZA', 'South Africa'),
    ('KR', 'South Korea'), ('ES', 'Spain'), ('LK', 'Sri Lanka'),
    ('SE', 'Sweden'), ('CH', 'Switzerland'), ('TW', 'Taiwan'),
    ('TH', 'Thailand'), ('TN', 'Tunisia'), ('TR', 'Turkey'),
    ('UA', 'Ukraine'), ('AE', 'United Arab Emirates'),
    ('GB', 'United Kingdom'), ('US', 'United States'),
    ('UY', 'Uruguay'), ('UZ', 'Uzbekistan'), ('VE', 'Venezuela'),
    ('VN', 'Vietnam'),
    ('ZZ', 'Other / not listed'),
]

CODES = {c for c, _n in COUNTRIES}
NAMES = dict(COUNTRIES)

# Accept-Language 헤더의 지역 코드 → 국가 코드.
# 사용자가 드롭박스를 비워 두면 이 값을 추정치로 쓴다(마찰 없이 얻을 수 있다).
# 언어만 있고 지역이 없는 경우(예: 'pt')에 쓸 대표 국가.
LANG_FALLBACK = {
    'en': 'US', 'ko': 'KR', 'ja': 'JP', 'zh': 'CN', 'pt': 'BR',
    'es': 'MX', 'fr': 'FR', 'de': 'DE', 'it': 'IT', 'ru': 'RU',
    'id': 'ID', 'ms': 'MY', 'th': 'TH', 'vi': 'VN', 'tl': 'PH',
    'fil': 'PH', 'hi': 'IN', 'bn': 'BD', 'ur': 'PK', 'ta': 'IN',
    'ar': 'SA', 'tr': 'TR', 'pl': 'PL', 'nl': 'NL', 'sv': 'SE',
    'da': 'DK', 'nb': 'NO', 'no': 'NO', 'fi': 'FI', 'cs': 'CZ',
    'sk': 'SK', 'hu': 'HU', 'ro': 'RO', 'bg': 'BG', 'el': 'GR',
    'he': 'IL', 'fa': 'IR', 'uk': 'UA', 'sr': 'RS', 'hr': 'HR',
    'sl': 'SI', 'lt': 'LT', 'lv': 'LV', 'et': 'EE', 'is': 'IS',
    'ne': 'NP', 'si': 'LK', 'km': 'KH', 'my': 'MM', 'kk': 'KZ',
    'uz': 'UZ', 'sw': 'KE', 'af': 'ZA', 'zu': 'ZA',
}


def normalize(code):
    """입력값을 유효한 국가 코드로. 알 수 없으면 빈 문자열."""
    c = str(code or '').strip().upper()[:2]
    return c if c in CODES else ''


def from_accept_language(header):
    """
    Accept-Language 헤더에서 국가를 추정한다.

    'pt-BR,pt;q=0.9,en;q=0.8' → 'BR'
    'en,ko;q=0.9'             → 'US'  (지역이 없으면 대표 국가)

    사용자가 직접 고른 값이 있으면 그것이 우선이고, 이건 비었을 때만 쓴다.
    추정치이므로 저장할 때 출처를 구분한다.
    """
    for part in str(header or '').split(','):
        tag = part.split(';')[0].strip()
        if not tag or tag == '*':
            continue
        bits = tag.replace('_', '-').split('-')
        # 지역 코드가 붙어 있으면 그것을 쓴다 (pt-BR → BR)
        for b in bits[1:]:
            if len(b) == 2 and b.upper() in CODES:
                return b.upper()
        got = LANG_FALLBACK.get(bits[0].lower())
        if got:
            return got
    return ''

"""
engine.py — K-Name Generator 변환 엔진

server_storytelling_current_policy_ordered_fallback_9.py 로부터 분리.
변환 로직 (KoreanNameEngine + 자모 분해 + 매핑 룰)만 포함.
의미 설명, 변환 이유 설명, Flask 라우팅은 별도 모듈로 분리됨.
"""
from __future__ import annotations
import os
import re
import math
import json
import random
import time
import logging
from functools import lru_cache
from itertools import product

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# 한글 분해
# ============================================================
CHOSUNG = ['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ',
           'ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ']
JUNGSUNG = ['ㅏ','ㅐ','ㅑ','ㅒ','ㅓ','ㅔ','ㅕ','ㅖ','ㅗ','ㅘ','ㅙ',
            'ㅚ','ㅛ','ㅜ','ㅝ','ㅞ','ㅟ','ㅠ','ㅡ','ㅢ','ㅣ']

def decompose(char):
    if '가' <= char <= '힣':
        code = ord(char) - 0xAC00
        return CHOSUNG[code // (21 * 28)], JUNGSUNG[(code % (21 * 28)) // 28]
    return char, ''


# ============================================================
# 변경 규칙
# ============================================================
FIRST_CHO_RULES = {
    'ㄱ':([],['ㄲ']),'ㄴ':(['ㄹ'],[]),'ㄷ':(['ㅈ'],['ㄸ']),
    'ㄹ':(['ㄴ'],['ㅇ','ㅎ']),'ㅁ':([],[]),'ㅂ':(['ㅍ'],['ㅃ']),
    'ㅅ':([],['ㅆ']),'ㅇ':(['ㅎ'],[]),'ㅈ':(['ㅊ'],['ㅉ']),'ㅊ':(['ㅈ'],['ㅉ']),
    'ㅋ':'R_ㄱ','ㅌ':(['ㄷ'],['ㄸ']),'ㅍ':(['ㅎ','ㅂ'],['ㅃ']),'ㅎ':(['ㅇ'],[]),
    'ㄲ':'R_ㄱ','ㄸ':'R_ㄷ','ㅃ':'R_ㅂ','ㅆ':'R_ㅅ','ㅉ':'R_ㅈ',
}
FIRST_JUNG_RULES = {
    'ㅏ':(['ㅓ'],['ㅐ','ㅔ']),'ㅐ':(['ㅔ','ㅖ'],['ㅏ','ㅓ','ㅣ']),
    'ㅑ':(['ㅏ'],[]),'ㅒ':(['ㅐ'],[]),'ㅓ':(['ㅏ'],['ㅐ','ㅔ']),
    'ㅔ':(['ㅐ','ㅖ'],['ㅏ','ㅓ','ㅣ']),'ㅕ':(['ㅓ'],[]),
    'ㅖ':(['ㅔ','ㅐ'],['ㅏ','ㅓ','ㅣ']),
    'ㅗ':(['ㅓ','ㅜ'],[]),'ㅘ':(['ㅏ'],[]),'ㅙ':(['ㅐ'],[]),
    'ㅚ':'R_ㅐㅔ','ㅛ':(['ㅗ'],[]),'ㅜ':(['ㅓ'],['ㅗ']),
    'ㅝ':(['ㅓ'],[]),'ㅞ':'R_ㅔㅐㅖ','ㅟ':(['ㅣ'],[]),
    'ㅠ':(['ㅜ'],['ㅓ']),'ㅡ':(['ㅜ','ㅓ'],['ㅠ']),
    'ㅢ':(['ㅣ'],[]),'ㅣ':(['ㅢ'],[]),
}
LAST_CHO_RULES = {
    'ㄱ':([],[]),'ㄴ':(['ㄹ','ㅇ'],[]),'ㄷ':(['ㅈ'],[]),
    'ㄹ':(['ㄴ','ㅇ'],[]),'ㅁ':([],[]),'ㅂ':([],[]),
    'ㅅ':([],[]),'ㅇ':([],[]),'ㅈ':([],[]),'ㅊ':([],[]),
    'ㅋ':'R_ㄱ','ㅌ':(['ㄷ'],[]),'ㅍ':(['ㅂ'],[]),'ㅎ':(['ㅇ'],[]),
    'ㄲ':'R_ㄱ','ㄸ':'R_ㄷ','ㅃ':'R_ㅂ','ㅆ':'R_ㅅ','ㅉ':'R_ㅈ',
}
LAST_JUNG_RULES = {
    'ㅏ':(['ㅓ'],['ㅐ','ㅔ','ㅣ']),'ㅐ':(['ㅔ','ㅖ'],['ㅏ','ㅓ']),
    'ㅑ':(['ㅏ'],[]),'ㅒ':(['ㅐ'],[]),
    'ㅓ':(['ㅝ'],['ㅏ','ㅣ']),
    'ㅔ':(['ㅐ','ㅖ'],['ㅏ','ㅓ','ㅣ']),
    'ㅕ':(['ㅓ'],['ㅣ']),'ㅖ':(['ㅔ','ㅐ'],['ㅏ','ㅓ','ㅣ']),
    'ㅗ':(['ㅓ','ㅜ'],['ㅕ','ㅏ']),'ㅘ':(['ㅏ'],[]),'ㅙ':(['ㅐ'],[]),
    'ㅚ':'R_ㅐㅔ','ㅛ':(['ㅗ'],[]),
    'ㅜ':(['ㅝ','ㅓ'],['ㅗ']),'ㅝ':(['ㅓ'],[]),'ㅞ':'R_ㅔㅐㅖ',
    'ㅟ':(['ㅣ'],[]),'ㅠ':(['ㅜ'],['ㅓ']),
    'ㅡ':(['ㅜ','ㅓ'],['ㅠ','ㅕ']),'ㅢ':(['ㅣ'],[]),'ㅣ':([],[]),
}

SURNAME_TABLE = {
    'ㄱ':['가','간','감','강','견','경','계','고','공','곽','구','국','궁','권','궉','근','금','기','길','김'],
    'ㄴ':['나','남','남궁','노'],'ㄷ':['단','당','대','도','독고','동','두'],'ㄹ':['라','류'],
    'ㅁ':['마','만','맹','명','모','목','민','문'],
    'ㅂ':['박','반','방','배','백','범','변','복','봉','부','빈','빙'],
    'ㅅ':['사','사공','서','서문','석','선','선우','설','성','소','손','송','승','신','심'],
    'ㅇ':['안','양','어','엄','여','연','염','예','오','온','옹','왕','용','우','운','원','위','유','육','윤','은','음','이','인','임'],
    'ㅈ':['장','전','점','정','제','제갈','조','종','좌','주','지','진'],
    'ㅊ':['차','창','채','천','초','최','추'],'ㅌ':['탁','탄','태'],
    'ㅍ':['판','팽','편','평','표','풍','피','필'],
    'ㅎ':['하','한','함','허','현','형','호','홍','황','황보'],
}
SURNAME_FREQ = {
    '김':10689959,'이':7306828,'박':4192074,'최':2333927,'정':2151879,'강':1044386,'조':1055567,'윤':680855,'장':992721,'임':823921,'한':773404,'오':763281,'서':751704,'신':741081,'권':705941,'황':697171,'안':685639,'송':683494,'류':603084,'전':559110,'홍':558853,'고':471429,'문':454105,'양':486014,'손':457303,'배':400641,'백':381987,'허':326770,'유':302040,'남':275648,'심':272060,'노':259439,'하':230886,'곽':194860,'성':194661,'차':185800,'주':182462,'우':180635,'구':192642,'민':171308,'진':170567,'나':155821,'지':151210,'엄':144149,'채':131757,'원':129528,'천':127772,'방':120517,'공':96085,'현':94784,'변':91960,'함':79765,'염':79553,'여':77820,'추':74974,'도':73770,'봉':35813,'석':58925,'선':28464,'설':41507,'마':40262,'길':37786,'연':35143,'위':31858,'표':30749,'명':28625,'기':27750,'반':26026,'왕':25700,'금':24510,'옹':23880,'육':23680,'인':23454,'맹':22910,'예':22340,'모':21705,'복':17940,'빈':10820,'빙':7920,'편':22136,'평':7450,'풍':4830,'피':20840,'필':5280,'탁':18850,'탄':2500,'태':8860,'판':5400,'팽':5800,'동':20450,'두':11280,'당':3200,'대':2100,'단':4380,'독고':1450,'만':3200,'목':9440,'부':9860,'사':4560,'사공':2080,'서문':1850,'선우':3890,'승':10440,'가':5380,'간':2100,'감':5240,'견':2050,'경':16250,'계':7450,'국':16950,'궁':3800,'궉':1800,'근':1200,'라':17550,'점':1500,'제':3880,'제갈':4120,'종':5880,'좌':2650,'음':5640,'은':12100,'운':2340,'용':9680,'온':3200,'어':10450,'호':15630,'형':9580,'황보':3460,'초':4580,'남궁':4280,
}
BANNED_NAMES = ['애마']


VALID_HANJA_SOUNDS = set()  # 서버 시작 시 로드


# ============================================================
# 중성 동일군
# ============================================================
# ㅔ/ㅐ/ㅖ는 이름 변환에서 동일 중성군으로 취급한다.
# 예: 제(ㅔ), 재(ㅐ), 졔(ㅖ)는 중성 비용 0으로 처리한다.
SAME_JUNG_GROUPS = [frozenset({'ㅔ', 'ㅐ', 'ㅖ'})]


def same_jung_group_for(jung):
    for group in SAME_JUNG_GROUPS:
        if jung in group:
            return group
    return None


def same_jung_equiv(a, b):
    if a == b:
        return True
    group = same_jung_group_for(a)
    return bool(group and b in group)


def same_jung_members(jung):
    group = same_jung_group_for(jung)
    return set(group) if group else {jung}



# ============================================================
# 변환 엔진
# ============================================================
class KoreanNameEngine:
    """외국 이름 음차 결과를 한국식 이름으로 변환하는 엔진.

    개선 포인트:
      1) 이름 후보를 발음 유사도만이 아니라 자연스러움(weight/pop_pct), 후보 출처까지 반영해 점수화
      2) result_1용 SAFE_NAME_POOL을 두어 과도하게 낮은 weight 이름을 자동 보정
      3) 성씨를 빈도 기반 tier로 분류하고, 희소 성씨/복성은 기본 결과에서 common surname으로 보정
      4) 이름별 남녀 사용 비중(gender_fit_score)을 반영해 반대 성별 쏠림이 큰 이름을 제외
      5) 음차 결과와 동일한 이름은 result 후보에서 제외하고, 기존 변환 후보의 다음 순위를 우선 선택
      6) 음차 결과와 다르지만 외국 이름을 그대로 음차한 후보(예: 필립, 엠마)는 제외
      7) SAFE_NAME_POOL fallback에도 기존 초성/중성 규칙과 ㅔ/ㅐ/ㅖ 동일 중성군을 적용
      8) fallback 후보 평가에 앞 2음절 유사도와 모음 흐름 유사도를 반영
      9) Q3라도 2음절/모음 흐름이 충분히 좋으면 phonetic_rescue로 살림
      10) safe pool 후보 source를 phonetic/anchor/general로 세분화해 진단 가능하게 함
      11) result_1은 Q1/Q2를 우선 노출하고 Q3는 rescue/대안 후보로 제한
      12) 성+이름 전체 조합 품질(full_score)을 계산해 최종 후보를 재정렬
    """

    ALPHA_FIRST = 0.35
    ALPHA_LAST = 0.25
    MAX_SIM = 4

    # ----- 이름 품질 게이트 -----
    # similarity가 나쁠수록 더 높은 weight를 요구한다.
    # 예: 발음이 매우 가까우면(weight 100 이상) 허용 가능하지만,
    #     발음이 멀면(weight 1000~2000 이상)이어야 result_1 후보로 남는다.
    MIN_WEIGHT_BY_SIM = {
        0: 100,
        1: 200,
        2: 500,
        3: 1000,
        4: 2000,
    }
    SAFE_NAME_MIN_WEIGHT = 100
    SAFE_NAME_POP_PCT = 0.90
    RESULT2_MIN_WEIGHT = 50

    # ----- 성별 적합도 게이트 -----
    # 성별 필터(sex == 남/여)만으로는 부족하다. 예를 들어 '다인'은 남성 이름으로도
    # 존재하지만 여성 사용 비중이 압도적으로 높으므로 남성 result 후보에서는 제외한다.
    # target_gender_share = target_gender_weight / (male_weight + female_weight)
    GENDER_FIT_MIN_SHARE = 0.20
    GENDER_OPPOSITE_RATIO_LIMIT = 3.0

    # ----- 성씨 품질 게이트 -----
    PRIMARY_SURNAME_MIN_FREQ = 100_000
    PRIMARY_SURNAME_EXACT_MIN_FREQ = 50_000
    COMPOUND_SURNAMES = {'남궁', '독고', '사공', '서문', '선우', '제갈', '황보'}

    # 격음/경음 → 평음 매핑 (한국어 외래어 표기 표준)
    # 예: Kennedy(케) → 가/거/기 한국 이름과 매칭 가능하게
    ASPIRATE_TO_PLAIN = {
        'ㅋ': 'ㄱ', 'ㅌ': 'ㄷ', 'ㅍ': 'ㅂ', 'ㅊ': 'ㅈ',
        'ㄲ': 'ㄱ', 'ㄸ': 'ㄷ', 'ㅃ': 'ㅂ', 'ㅆ': 'ㅅ', 'ㅉ': 'ㅈ',
    }

    # 희소 성씨가 선택될 때 fallback으로 쓸 친숙한 성씨 shortlist
    COMMON_SURNAME_BY_CHO = {
        'ㄱ': ['김', '강', '고', '권', '구'],
        'ㄴ': ['남', '노', '나'],
        'ㄷ': ['정', '장', '조', '전', '도'],
        'ㄹ': ['이', '류'],
        'ㅁ': ['문', '민'],
        'ㅂ': ['박', '배', '백', '변'],
        'ㅅ': ['서', '신', '송', '손', '성'],
        'ㅇ': ['이', '오', '안', '윤', '임', '유', '양'],
        'ㅈ': ['정', '조', '장', '전', '주', '지'],
        'ㅊ': ['최', '차', '채'],
        'ㅋ': ['김', '강', '고'],
        'ㅌ': ['정', '최', '도'],
        'ㅍ': ['박', '배'],
        'ㅎ': ['한', '홍', '허', '황', '하'],
    }

    SOURCE_SCORE = {
        'exact_pair': 1.00,
        'primary_pair': 0.92,
        'phonetic_rescue': 0.90,
        'relaxed_pair': 0.82,
        'safe_pool_phonetic': 0.78,
        'safe_pool_anchor': 0.70,
        'initial_fallback': 0.65,
        'safe_pool_general': 0.52,
        # legacy alias retained for backward compatibility in old result files
        'safe_pool_fallback': 0.70,
        'one_syllable': 0.55,
    }

    # fallback 후보는 이미 SAFE_NAME_POOL을 통과한 이름이므로,
    # weight를 직접 순위 기준으로 크게 보지 않고 '자연스러움 구간'만 반영한다.
    # source를 세분화해, 발음 연결성이 있는 safe pool 후보와 일반 fallback을 구분한다.
    FALLBACK_SOURCE_TYPES = {'safe_pool_phonetic', 'safe_pool_anchor', 'safe_pool_general', 'safe_pool_fallback'}

    # Q3라도 원래 이름의 2음절/모음 흐름을 충분히 보존하면 result_1 후보로 살린다.
    PHONETIC_RESCUE_TWO_SYLLABLE_MIN = 0.72
    PHONETIC_RESCUE_VOWEL_FLOW_MIN = 0.62

    # 외국 이름을 한국어로 그대로 옮긴 성격이 강한 이름.
    # 현재 정책: 음차 결과와 동일한 후보는 result 후보에서 제외한다.
    # 또한 음차 결과와 다르지만 후보가 아래 이름이면, "한국식 변환"이 아니라
    # 다른 외국 이름 음차처럼 보이므로 제외한다.
    FOREIGN_TRANSLITERATED_GIVEN_NAMES = {
        '조이', '안나', '엠마', '필립', '리암', '노아', '로건', '루카', '루카스',
        '레오', '루나', '미아', '소피아', '잭슨', '제이', '로이', '라이언',
        '마틴', '로빈', '케빈', '브라이언', '앨런', '엘라', '릴리', '클로이',
        '니콜', '제시카', '레베카', '바네사', '발레리', '티나', '에릭',
        '피터', '폴', '데이비드', '다니엘', '토마스', '조셉', '찰리'
    }

    EXTRA_BANNED_GIVEN_NAMES = {
        # 실제 변환 결과에서 한국 이름으로 부자연스럽거나 오해 소지가 큰 후보.
        # 운영 중 결과 로그를 보며 계속 보강하는 것을 권장.
        '애말', '마앙', '루반', '하범', '만승', '애마'
    }

    def __init__(self, db_path, hanja_path=None):
        db = pd.read_excel(db_path)
        db = db[db['name'].astype(str).str.len().isin([1, 2])].copy()
        db['name'] = db['name'].astype(str)
        db['weight'] = pd.to_numeric(db['weight'], errors='coerce').fillna(0).astype(int)
        db['nlen'] = db['name'].str.len()
        db['cho1'] = db['name'].apply(lambda n: decompose(n[0])[0])
        db['jung1'] = db['name'].apply(lambda n: decompose(n[0])[1])
        db['cho2'] = db['name'].apply(lambda n: decompose(n[1])[0] if len(n)>1 else '')
        db['jung2'] = db['name'].apply(lambda n: decompose(n[1])[1] if len(n)>1 else '')
        # 반복적인 DataFrame.apply를 줄이기 위한 검색용 tuple 컬럼
        db['sound1'] = list(zip(db['cho1'], db['jung1']))
        db['sound2'] = list(zip(db['cho2'], db['jung2']))
        db['cho_pair'] = list(zip(db['cho1'], db['cho2']))

        # 성별별 popularity percentile. long-tail DB라 절대 weight만 쓰면 흔들릴 수 있어 보조 지표로 사용.
        db['pop_pct'] = db.groupby('sex')['weight'].rank(pct=True, method='average')

        # 이름별 남녀 weight를 함께 보아 해당 성별에 얼마나 자연스러운 이름인지 계산한다.
        # 같은 이름이 target 성별에도 존재하더라도, 반대 성별 사용량이 과도하게 크면
        # 사용자 체감상 성별 부적합 이름일 수 있다.
        gender_weight = db.groupby(['name', 'sex'])['weight'].sum().unstack(fill_value=0)
        male_w = gender_weight['남'] if '남' in gender_weight.columns else pd.Series(0, index=gender_weight.index)
        female_w = gender_weight['여'] if '여' in gender_weight.columns else pd.Series(0, index=gender_weight.index)
        db['male_weight'] = db['name'].map(male_w).fillna(0).astype(int)
        db['female_weight'] = db['name'].map(female_w).fillna(0).astype(int)

        def _target_gender_weight(row):
            if row['sex'] == '남':
                return int(row['male_weight'])
            if row['sex'] == '여':
                return int(row['female_weight'])
            return int(row['weight'])

        def _opposite_gender_weight(row):
            if row['sex'] == '남':
                return int(row['female_weight'])
            if row['sex'] == '여':
                return int(row['male_weight'])
            return 0

        db['target_gender_weight'] = db.apply(_target_gender_weight, axis=1)
        db['opposite_gender_weight'] = db.apply(_opposite_gender_weight, axis=1)
        db['gender_total_weight'] = (db['male_weight'] + db['female_weight']).replace(0, 1)
        db['gender_fit_score'] = db['target_gender_weight'] / db['gender_total_weight']
        db['opposite_gender_ratio'] = db['opposite_gender_weight'] / db['target_gender_weight'].clip(lower=1)
        db['is_gender_mismatch'] = (
            (db['gender_fit_score'] < self.GENDER_FIT_MIN_SHARE) &
            (db['opposite_gender_ratio'] >= self.GENDER_OPPOSITE_RATIO_LIMIT)
        )

        banned = set(BANNED_NAMES) | self.EXTRA_BANNED_GIVEN_NAMES
        db['is_banned'] = db['name'].isin(banned)

        self.db2 = db[db['nlen']==2].copy()
        self.db1 = db[db['nlen']==1].copy()
        self.max_weight = max(float(db['weight'].max()), 1.0)
        self.log_max_w = np.log1p(self.max_weight)
        self.max_surname_freq = max(SURNAME_FREQ.values())
        self.log_max_s = np.log1p(self.max_surname_freq)

        # result_1용 안전 이름 풀: 2글자 + 일정 이상의 사용성 + 금지어 제외.
        self.safe_db2 = self.db2[
            (~self.db2['is_banned']) &
            (~self.db2['is_gender_mismatch']) &
            (
                (self.db2['weight'] >= self.SAFE_NAME_MIN_WEIGHT) |
                (self.db2['pop_pct'] >= self.SAFE_NAME_POP_PCT)
            )
        ].copy()

        # 변환 캐시: 배치 처리나 반복 테스트에서 속도 개선
        self._first_cache = {}
        self._fallback_given_cache = {}
        self._last_cache = {}
        self._convert_cache = {}

        # 인명용 한자 음 로드
        global VALID_HANJA_SOUNDS
        if hanja_path:
            hanja_df = pd.read_excel(hanja_path)
            VALID_HANJA_SOUNDS = set(hanja_df['음'].dropna().astype(str).unique())

    @staticmethod
    def _make_pairs(oc, oj, cr, jr):
        ch = cr.get(oc, ([], []))
        jn = jr.get(oj, ([], []))
        if isinstance(ch, str):
            bc = ch.split('_')[1]; cc = 0
            ch2 = cr.get(bc, ([], []))
            cl1, cl2 = ch2 if not isinstance(ch2, str) else ([], [])
            is_rep = True
        else:
            bc = oc; cc = 0; cl1, cl2 = ch; is_rep = False
        p = {}
        if isinstance(jn, str):
            bjs = list(jn.split('_')[1])
            el1 = ['ㅏ','ㅓ','ㅣ'] if oj == 'ㅞ' else []
            for bj in bjs:
                p.setdefault((bc, bj), (cc, 0))
                if not is_rep:
                    for c in cl1: p.setdefault((c, bj), (1, 0))
                    for c in cl2: p.setdefault((c, bj), (2, 0))
            for ej in el1:
                p.setdefault((bc, ej), (cc, 1))
                if not is_rep:
                    for c in cl1: p.setdefault((c, ej), (1, 1))
        else:
            jl1, jl2 = jn
            p.setdefault((bc, oj), (cc, 0))
            if not is_rep:
                for c in cl1: p.setdefault((c, oj), (1, 0))
            for j in jl1: p.setdefault((bc, j), (cc, 1))
            if not is_rep:
                for c in cl1:
                    for j in jl1: p.setdefault((c, j), (1, 1))
                for c in cl2: p.setdefault((c, oj), (2, 0))
            for j in jl2: p.setdefault((bc, j), (cc, 2))

        # ㅔ/ㅐ/ㅖ 동일 중성군 보정.
        # source 중성이 동일군 안에 있으면 동일군의 다른 중성도 비용 0으로 추가한다.
        source_group = same_jung_group_for(oj)
        additions = {}
        for (c, j), (ccost, jcost) in list(p.items()):
            target_group = same_jung_group_for(j)
            if not target_group:
                continue
            for equiv_j in target_group:
                new_jcost = 0 if source_group == target_group else jcost
                new_val = (ccost, new_jcost)
                old_val = p.get((c, equiv_j), additions.get((c, equiv_j)))
                if old_val is None or sum(new_val) < sum(old_val):
                    additions[(c, equiv_j)] = new_val
        for key, val in additions.items():
            old_val = p.get(key)
            if old_val is None or sum(val) < sum(old_val):
                p[key] = val
        return p

    def _popularity_score(self, weight):
        return float(np.log1p(max(float(weight), 0.0)) / self.log_max_w)

    def _fallback_naturalness_bucket_score(self, weight):
        """
        fallback 전용 자연스러움 점수.
        SAFE_NAME_POOL을 통과한 이름들 사이에서는 weight가 높을수록 무조건 유리해지는
        구조를 피하기 위해, 연속값이 아니라 완만한 구간 점수로만 반영한다.
        """
        w = int(weight or 0)
        if w >= 3000:
            return 1.00
        if w >= 1000:
            return 0.92
        if w >= 500:
            return 0.84
        if w >= 200:
            return 0.76
        if w >= 100:
            return 0.68
        return 0.50

    def _phonetic_score(self, similarity):
        sim = min(max(float(similarity), 0.0), float(self.MAX_SIM))
        return float(1 - sim / self.MAX_SIM)

    def _source_score(self, source):
        return float(self.SOURCE_SCORE.get(source, 0.70))

    def _meaning_score(self, name):
        # 한자 매칭이 가능한 이름이면 의미 설명 품질이 높다고 간주.
        if self._has_hanja(name):
            return 1.0
        return 0.70

    @staticmethod
    def _normalize_korean_name(name):
        """비교용 한글 이름 정규화: 공백/기호 제거."""
        return re.sub(r'[^가-힣]', '', str(name or '')).strip()

    def _is_same_as_transliteration(self, candidate_name, first_kr):
        """후보 이름이 LLM 음차 결과와 완전히 같은지 판단한다."""
        cand = self._normalize_korean_name(candidate_name)
        src = self._normalize_korean_name(first_kr)
        return bool(cand and src and cand == src)

    def _is_foreign_transliterated_name(self, candidate_name, first_kr=None):
        """
        후보가 외국 이름을 그대로 음차한 이름인지 판단한다.
        음차 결과와 완전히 같은 후보는 _apply_transliteration_name_policy에서 먼저 제외한다.
        여기서는 '사용자의 음차와 다른 외국식 음차명'을 거르는 용도로 쓴다.
        """
        cand = self._normalize_korean_name(candidate_name)
        if not cand:
            return False
        if first_kr and self._is_same_as_transliteration(cand, first_kr):
            return False
        return cand in self.FOREIGN_TRANSLITERATED_GIVEN_NAMES

    def _with_candidate_meta(self, cand, **updates):
        """candidate tuple의 meta를 안전하게 갱신한다."""
        if not cand:
            return cand
        meta = dict(self._candidate_meta(cand))
        meta.update(updates)
        return (cand[0], cand[1], cand[2], cand[3], meta)

    def _apply_transliteration_name_policy(self, candidates, first_kr):
        """first name 후보에 현재 음차명 정책을 적용한다.

        현재 정책:
        1) 음차 결과와 변환 후보가 완전히 동일하면 result 후보에서 제외한다.
           이 경우 곧바로 SAFE_NAME_POOL로 가지 않고, 기존 변환 후보의 다음 순위를 우선 사용한다.
        2) 음차 결과와 다르지만 후보가 외국식 음차명(예: 필립, 엠마)이면 제외한다.
        """
        filtered = []
        for cand in candidates or []:
            if not cand:
                continue
            name = cand[0]
            if self._is_same_as_transliteration(name, first_kr):
                continue
            if self._is_foreign_transliterated_name(name, first_kr):
                continue
            filtered.append(self._with_candidate_meta(
                cand,
                same_as_transliteration=False,
                foreign_transliteration_name=False,
            ))
        return filtered

    def _fs(self, w, s, source='primary_pair', name=None, gender_fit=None):
        """이름 점수: 발음 + 자연스러움 + 성별 적합도 + 후보 출처 + 설명 가능성.

        일반 후보는 기존처럼 weight 기반 자연스러움을 반영한다.
        fallback 후보는 이미 SAFE_NAME_POOL 필터를 통과했으므로 weight의 직접 영향력을 낮추고,
        구간화된 자연스러움 점수만 사용한다.
        """
        name = name or ''
        if gender_fit is None or pd.isna(gender_fit):
            gender_fit = 1.0
        gender_fit = min(max(float(gender_fit), 0.0), 1.0)

        if source in self.FALLBACK_SOURCE_TYPES:
            return (
                0.34 * self._phonetic_score(s) +
                0.18 * self._fallback_naturalness_bucket_score(w) +
                0.22 * gender_fit +
                0.20 * self._source_score(source) +
                0.06 * self._meaning_score(name)
            )

        return (
            0.30 * self._phonetic_score(s) +
            0.30 * self._popularity_score(w) +
            0.20 * gender_fit +
            0.15 * self._source_score(source) +
            0.05 * self._meaning_score(name)
        )

    def _ls(self, f, s):
        """성씨 점수: 성씨는 자연스러움/친숙도를 더 강하게 반영."""
        return (
            0.20 * self._phonetic_score(s) +
            0.75 * (np.log1p(max(float(f), 0.0)) / self.log_max_s) +
            0.05
        )

    @staticmethod
    def _has_hanja(name):
        return all(ch in VALID_HANJA_SOUNDS for ch in name)

    def _source_from_sim(self, sim):
        if sim == 0:
            return 'exact_pair'
        if sim <= 1:
            return 'primary_pair'
        if sim <= 3:
            return 'relaxed_pair'
        return 'initial_fallback'

    def _given_quality(self, name, weight, similarity, pop_pct=None, nlen=None, gender_fit=None, gender_mismatch=False):
        """서비스용 이름 품질 등급(Q1~Q4)."""
        if not name:
            return 'Q4'
        if name in set(BANNED_NAMES) | self.EXTRA_BANNED_GIVEN_NAMES:
            return 'Q4'
        if nlen is None:
            nlen = len(name)
        if nlen != 2:
            return 'Q4'
        if gender_fit is not None and not pd.isna(gender_fit):
            # 반대 성별 쏠림이 큰 이름은 해당 성별 result 후보에서 제외한다.
            if float(gender_fit) < self.GENDER_FIT_MIN_SHARE or bool(gender_mismatch):
                return 'Q4'

        sim = int(min(max(similarity, 0), self.MAX_SIM))
        weight = int(weight or 0)

        # pop_pct가 있으면 DB 분포 기반으로 먼저 분류한다.
        # long-tail DB에서는 weight 200~300도 성별 내 상위권일 수 있기 때문이다.
        if pop_pct is not None and not pd.isna(pop_pct):
            if pop_pct >= 0.97 and sim <= 2:
                return 'Q1'
            if pop_pct >= 0.90 and sim <= 2:
                return 'Q2'
            if pop_pct >= 0.85 and sim <= 1:
                return 'Q3'

        min_w = self.MIN_WEIGHT_BY_SIM.get(sim, self.MIN_WEIGHT_BY_SIM[self.MAX_SIM])
        if weight < min_w:
            return 'Q4'

        # fallback: 절대 weight 기준
        if weight >= 3000 and sim <= 2:
            return 'Q1'
        if weight >= 700 and sim <= 2:
            return 'Q2'
        # 발음이 조금 멀어도 매우 자연스러운 이름이면 Q3로 허용
        if weight >= 3000 and sim <= 3:
            return 'Q3'
        if weight >= 1000 and sim <= 3:
            return 'Q3'
        if weight >= 300 and sim <= 1:
            return 'Q3'
        return 'Q4'

    def _candidate_tuple(self, row, sim, source='primary_pair'):
        name = str(row['name'])
        weight = int(row['weight'])
        sim = int(min(max(sim, 0), self.MAX_SIM))
        pop_pct = float(row.get('pop_pct', 0.0)) if not pd.isna(row.get('pop_pct', 0.0)) else 0.0
        gender_fit = float(row.get('gender_fit_score', 1.0)) if not pd.isna(row.get('gender_fit_score', 1.0)) else 1.0
        gender_mismatch = bool(row.get('is_gender_mismatch', False))
        score = self._fs(weight, sim, source=source, name=name, gender_fit=gender_fit)
        quality = self._given_quality(
            name, weight, sim, pop_pct=pop_pct, nlen=int(row.get('nlen', len(name))),
            gender_fit=gender_fit, gender_mismatch=gender_mismatch
        )
        meta = {
            'source': source,
            'quality': quality,
            'pop_pct': round(pop_pct, 4),
            'nlen': int(row.get('nlen', len(name))),
            'gender_fit_score': round(gender_fit, 4),
            'male_weight': int(row.get('male_weight', 0)),
            'female_weight': int(row.get('female_weight', 0)),
            'target_gender_weight': int(row.get('target_gender_weight', weight)),
            'opposite_gender_weight': int(row.get('opposite_gender_weight', 0)),
            'opposite_gender_ratio': round(float(row.get('opposite_gender_ratio', 0.0)), 4),
            'gender_mismatch': gender_mismatch,
            'same_as_transliteration': False,
            'foreign_transliteration_name': False,
        }
        return (name, weight, sim, float(score), meta)

    def _candidate_meta(self, cand):
        if cand and len(cand) >= 5 and isinstance(cand[4], dict):
            return cand[4]
        if not cand:
            return {'source': None, 'quality': 'Q4', 'pop_pct': 0, 'nlen': 0}
        name, weight, sim = cand[0], cand[1], cand[2]
        return {
            'source': 'legacy',
            'quality': self._given_quality(name, weight, sim, nlen=len(name)),
            'pop_pct': 0,
            'nlen': len(name),
            'gender_fit_score': 1.0,
            'gender_mismatch': False,
            'same_as_transliteration': False,
            'foreign_transliteration_name': False,
        }

    def _needs_given_fallback(self, cand):
        if not cand:
            return True
        meta = self._candidate_meta(cand)
        return meta.get('quality') == 'Q4'

    def _allow_result2_given(self, cand):
        if not cand:
            return False
        name, weight = cand[0], int(cand[1])
        if name in set(BANNED_NAMES) | self.EXTRA_BANNED_GIVEN_NAMES:
            return False
        meta = self._candidate_meta(cand)
        if meta.get('gender_mismatch') or float(meta.get('gender_fit_score', 1.0)) < self.GENDER_FIT_MIN_SHARE:
            return False
        return weight >= self.RESULT2_MIN_WEIGHT

    def _screen_pair(self, s1, s2, sex, wo=False):
        c1,j1 = decompose(s1); c2,j2 = decompose(s2)
        p1 = self._make_pairs(c1,j1,FIRST_CHO_RULES,FIRST_JUNG_RULES)
        p2 = self._make_pairs(c2,j2,FIRST_CHO_RULES,FIRST_JUNG_RULES)
        cc = set(product(set(k[0] for k in p1), set(k[0] for k in p2)))
        sc = self.db2[(self.db2['sex']==sex)&self.db2['cho_pair'].isin(cc)].copy()
        if sc.empty:
            sc = self.db2[(self.db2['sex']==sex)&self.db2['cho1'].isin(set(k[0] for k in p1))].copy()
            if sc.empty: return []
        s2r = sc[sc['sound1'].isin(set(p1.keys())) & sc['sound2'].isin(set(p2.keys()))]
        if s2r.empty: return []
        orig = s1 + s2
        # 음차동일 제외 (단, 한자어 있으면 허용)
        s2r = s2r[(s2r['name']!=orig) | s2r['name'].apply(self._has_hanja)]
        s2r = s2r[(~s2r['is_banned']) & (~s2r['name'].isin(BANNED_NAMES)) & (~s2r['is_gender_mismatch'])]
        if s2r.empty: return []
        s2r = s2r.copy()
        s2r['sim'] = s2r.apply(lambda r:sum(p1[(r['cho1'],r['jung1'])])+sum(p2[(r['cho2'],r['jung2'])]),axis=1)
        s2r['source'] = s2r['sim'].apply(lambda x: self._source_from_sim(int(x)))
        s2r['given_score'] = s2r.apply(lambda r:self._fs(r['weight'],r['sim'],source=r['source'],name=r['name'],gender_fit=r.get('gender_fit_score',1.0)),axis=1)
        if wo:
            s2r = s2r.sort_values('weight',ascending=False)
        else:
            s2r = s2r.sort_values('given_score',ascending=False)
        return [self._candidate_tuple(rr, int(rr['sim']), source=rr['source']) for _,rr in s2r.head(4).iterrows()]

    def match_first(self, fk, sex):
        syls = list(fk or '')
        if not syls:
            return None, None

        if len(syls) == 1:
            c1,j1 = decompose(syls[0]); p1 = self._make_pairs(c1,j1,FIRST_CHO_RULES,FIRST_JUNG_RULES)
            sc = self.db1[(self.db1['sex']==sex)&self.db1['cho1'].isin(set(k[0] for k in p1))].copy()
            if sc.empty: return None,None
            s2 = sc[sc['sound1'].isin(set(p1.keys()))]
            s2 = s2[(s2['name']!=fk)|s2['name'].apply(self._has_hanja)]
            s2 = s2[(~s2['is_banned']) & (~s2['name'].isin(BANNED_NAMES)) & (~s2['is_gender_mismatch'])]
            if s2.empty: return None,None
            s2 = s2.copy()
            s2['sim'] = s2.apply(lambda r:sum(p1[(r['cho1'],r['jung1'])]),axis=1)
            s2['source'] = 'one_syllable'
            s2['given_score'] = s2.apply(lambda r:self._fs(r['weight'],r['sim'],source='one_syllable',name=r['name'],gender_fit=r.get('gender_fit_score',1.0)),axis=1)
            s2 = s2.sort_values('given_score',ascending=False)
            cands = [self._candidate_tuple(rr, int(rr['sim']), source='one_syllable') for _,rr in s2.head(2).iterrows()]
            return cands[0] if cands else None, cands[1] if len(cands)>1 else None

        f = syls[0]; a = []
        for o in syls[1:]:
            a.extend(self._screen_pair(f,o,sex))
        if a:
            a = self._dedupe_given_candidates(a)
            a.sort(key=lambda x:x[3],reverse=True)
            if a[0][3] <= 0.4:
                a2 = []
                for o in syls[1:]:
                    a2.extend(self._screen_pair(f,o,sex,wo=True))
                if a2:
                    a2 = self._dedupe_given_candidates(a2)
                    a2.sort(key=lambda x:x[1],reverse=True)
                    return a2[0],a2[1] if len(a2)>1 else None
            return a[0],a[1] if len(a)>1 else None

        # 폴백: 첫 음절과 두 번째 음절의 초성 흐름만 맞는 후보
        p1 = self._make_pairs(decompose(f)[0],decompose(f)[1],FIRST_CHO_RULES,FIRST_JUNG_RULES)
        ac = set(k[0] for k in p1)
        for o in syls[1:]:
            p2 = self._make_pairs(decompose(o)[0],decompose(o)[1],FIRST_CHO_RULES,FIRST_JUNG_RULES)
            ac2 = set(k[0] for k in p2)
            cc = set(product(ac,ac2))
            fb = self.db2[(self.db2['sex']==sex)&self.db2['cho_pair'].isin(cc)].copy()
            fb = fb[(fb['name']!=(f+o)) & (~fb['is_banned']) & (~fb['name'].isin(BANNED_NAMES)) & (~fb['is_gender_mismatch'])]
            if not fb.empty:
                fb = fb.sort_values('weight',ascending=False)
                cands = [self._candidate_tuple(rr, self.MAX_SIM, source='initial_fallback') for _,rr in fb.head(2).iterrows()]
                return cands[0] if cands else None, cands[1] if len(cands)>1 else None
        return None,None

    def _dedupe_given_candidates(self, candidates):
        best = {}
        for c in candidates:
            if not c:
                continue
            name = c[0]
            if name not in best or c[3] > best[name][3]:
                best[name] = c
        return list(best.values())

    def _jung_candidates_for_fallback(self, jung1):
        """SAFE_NAME_POOL fallback용 중성 후보군.

        FIRST_JUNG_RULES의 1·2차 유사 중성을 포함하되,
        ㅔ/ㅐ/ㅖ 동일 중성군은 비용 없는 같은 계열로 함께 포함한다.
        """
        jung_rule = FIRST_JUNG_RULES.get(jung1, ([], []))
        if isinstance(jung_rule, str):
            target = jung_rule.split('_')[1]
            sub_rule = FIRST_JUNG_RULES.get(target, ([], []))
            base = {jung1, target} | set(sub_rule[0]) | set(sub_rule[1])
        else:
            primary, secondary = jung_rule
            base = {jung1} | set(primary) | set(secondary)
        expanded = set(base)
        for j in base | {jung1}:
            expanded |= same_jung_members(j)
        return expanded

    def _fallback_cho_candidates_for_safe_pool(self, cho):
        """SAFE_NAME_POOL fallback에서도 기존 first-name 초성 변환 규칙을 반영한다."""
        cho_set = {cho}
        if cho in self.ASPIRATE_TO_PLAIN:
            cho_set.add(self.ASPIRATE_TO_PLAIN[cho])
        rule = FIRST_CHO_RULES.get(cho)
        if isinstance(rule, tuple):
            primary, secondary = rule
            # 1차 변환은 fallback에서도 사용한다. 예: ㅍ → ㅎ, ㅂ
            cho_set.update(primary)
            # 2차 변환은 후보가 너무 넓어질 수 있어 기본 후보군에는 넣지 않고,
            # _make_pairs 기반의 sound 후보에서만 자연스럽게 반영한다.
        elif isinstance(rule, str) and rule.startswith('R_'):
            cho_set.add(rule.split('_')[1])
        if cho == 'ㄹ':
            cho_set.update({'ㄴ', 'ㅇ'})
        return cho_set

    def _fallback_rule_sound_candidates(self, cho, jung):
        """기존 초성/중성 변환 규칙을 적용한 첫 음절 후보 sound set과 cost map."""
        pair_map = self._make_pairs(cho, jung, FIRST_CHO_RULES, FIRST_JUNG_RULES)
        sounds = set(pair_map.keys())
        cho_set = {c for c, _ in sounds} | self._fallback_cho_candidates_for_safe_pool(cho)
        jung_set = {j for _, j in sounds} | self._jung_candidates_for_fallback(jung)
        return sounds, cho_set, jung_set, pair_map

    def _safe_hangul_syllables(self, text, limit=None):
        """한글 음절만 추출한다. fallback 설명/유사도 계산용."""
        syls = [c for c in str(text or '') if '가' <= c <= '힣']
        return syls[:limit] if limit else syls

    def _cho_cost_for_first_name(self, src_cho, tgt_cho):
        """first-name 초성 변환 비용. 기존 FIRST_CHO_RULES와 격음/평음 규칙을 재사용한다."""
        if src_cho == tgt_cho:
            return 0
        if self.ASPIRATE_TO_PLAIN.get(src_cho) == tgt_cho:
            return 1
        rule = FIRST_CHO_RULES.get(src_cho)
        if isinstance(rule, tuple):
            primary, secondary = rule
            if tgt_cho in primary:
                return 1
            if tgt_cho in secondary:
                return 2
        elif isinstance(rule, str) and rule.startswith('R_'):
            return 0 if tgt_cho == rule.split('_')[1] else 2
        if src_cho == 'ㄹ' and tgt_cho in {'ㄴ', 'ㅇ'}:
            return 1
        return 2

    def _jung_cost_for_first_name(self, src_jung, tgt_jung):
        """first-name 중성 변환 비용. ㅔ/ㅐ/ㅖ 동일군은 비용 0."""
        if same_jung_equiv(src_jung, tgt_jung):
            return 0
        rule = FIRST_JUNG_RULES.get(src_jung, ([], []))
        if isinstance(rule, str):
            target = rule.split('_')[1]
            if same_jung_equiv(tgt_jung, target):
                return 1
            sub = FIRST_JUNG_RULES.get(target, ([], []))
            primary, secondary = sub if not isinstance(sub, str) else ([], [])
        else:
            primary, secondary = rule
        if any(same_jung_equiv(tgt_jung, p) for p in primary):
            return 1
        if any(same_jung_equiv(tgt_jung, s) for s in secondary):
            return 2
        return 2

    def _syllable_cost_for_first_name(self, src_syllable, tgt_syllable):
        """source 음절과 후보 이름 음절 간 비용. 낮을수록 유사."""
        if not src_syllable or not tgt_syllable:
            return self.MAX_SIM
        sc, sj = decompose(src_syllable)
        tc, tj = decompose(tgt_syllable)
        if not sj or not tj:
            return self.MAX_SIM
        pair_map = self._make_pairs(sc, sj, FIRST_CHO_RULES, FIRST_JUNG_RULES)
        if (tc, tj) in pair_map:
            return min(sum(pair_map[(tc, tj)]), self.MAX_SIM)
        return min(self._cho_cost_for_first_name(sc, tc) + self._jung_cost_for_first_name(sj, tj), self.MAX_SIM)

    @staticmethod
    def _score_from_cost(cost):
        """비용을 0~1 점수로 변환한다."""
        table = {0: 1.00, 1: 0.78, 2: 0.55, 3: 0.30, 4: 0.12}
        return table.get(int(min(max(cost, 0), 4)), 0.12)

    def _first_syllable_trace_score(self, first_kr, candidate_name):
        src = self._safe_hangul_syllables(first_kr, limit=1)
        tgt = self._safe_hangul_syllables(candidate_name, limit=1)
        if not src or not tgt:
            return 0.0
        return self._score_from_cost(self._syllable_cost_for_first_name(src[0], tgt[0]))

    def _two_syllable_trace_score(self, first_kr, candidate_name):
        """앞 2음절 기준 유사도. fallback에서도 전체 음차 흐름을 반영하기 위한 핵심 점수."""
        src = self._safe_hangul_syllables(first_kr, limit=2)
        tgt = self._safe_hangul_syllables(candidate_name, limit=2)
        if not src or not tgt:
            return 0.0
        first = self._score_from_cost(self._syllable_cost_for_first_name(src[0], tgt[0]))
        if len(src) >= 2 and len(tgt) >= 2:
            second = self._score_from_cost(self._syllable_cost_for_first_name(src[1], tgt[1]))
            return round(0.55 * first + 0.45 * second, 6)
        return round(first, 6)

    def _second_syllable_trace_score(self, first_kr, candidate_name):
        """두 번째 음절만 따로 비교한다.

        Adam(애덤)→예담처럼 첫 음절보다 두 번째 음절 대응(덤→담)이
        설득력을 크게 좌우하는 케이스를 살리기 위한 fallback 전용 feature다.
        """
        src = self._safe_hangul_syllables(first_kr, limit=2)
        tgt = self._safe_hangul_syllables(candidate_name, limit=2)
        if len(src) >= 2 and len(tgt) >= 2:
            return round(self._score_from_cost(self._syllable_cost_for_first_name(src[1], tgt[1])), 6)
        return self._two_syllable_trace_score(first_kr, candidate_name)

    def _fallback_gender_fit_score(self, gender_fit, gender_mismatch=False):
        """fallback 점수용 성별 적합도.

        성별은 hard filter로 먼저 거르고, 통과한 후보 간 순위에서는 과도하게
        지배하지 않도록 완만하게 반영한다. 예: 예담처럼 남성 share가 20% 이상이면
        배제하지 않고, 2음절 유사도가 좋을 때 살아날 수 있게 한다.
        """
        if gender_fit is None or pd.isna(gender_fit):
            gender_fit = 1.0
        gender_fit = min(max(float(gender_fit), 0.0), 1.0)
        if gender_mismatch or gender_fit < self.GENDER_FIT_MIN_SHARE:
            return gender_fit
        # 통과 후보는 최소 0.85를 부여하고, 성별 쏠림 차이는 보너스로만 작게 반영
        return 0.85 + 0.15 * gender_fit

    def _vowel_pair_score(self, src_jung, tgt_jung):
        """중성 단위 유사도. ㅔ/ㅐ/ㅖ 동일군은 1.0."""
        cost = self._jung_cost_for_first_name(src_jung, tgt_jung)
        if cost <= 0:
            return 1.0
        if cost == 1:
            return 0.75
        if cost == 2:
            return 0.45
        return 0.10

    def _vowel_flow_score(self, first_kr, candidate_name):
        """음차 이름의 모음 흐름과 후보 이름 2글자의 모음 흐름을 비교한다.

        후보 이름은 보통 2글자이므로, source의 앞 2~4음절에서 가능한 2음절 window를 만들고
        그중 후보와 가장 잘 맞는 모음 sequence 점수를 사용한다.
        """
        src_syls = self._safe_hangul_syllables(first_kr, limit=4)
        tgt_syls = self._safe_hangul_syllables(candidate_name, limit=2)
        src_vowels = [decompose(s)[1] for s in src_syls if decompose(s)[1]]
        tgt_vowels = [decompose(s)[1] for s in tgt_syls if decompose(s)[1]]
        if not src_vowels or not tgt_vowels:
            return 0.0
        k = len(tgt_vowels)
        windows = []
        if len(src_vowels) >= k:
            windows.extend(src_vowels[i:i+k] for i in range(0, len(src_vowels) - k + 1))
        else:
            windows.append(src_vowels)
        best = 0.0
        for win in windows:
            scores = []
            for i, tv in enumerate(tgt_vowels):
                if i < len(win):
                    scores.append(self._vowel_pair_score(win[i], tv))
                else:
                    scores.append(0.25)
            if scores:
                best = max(best, sum(scores) / len(scores))
        return round(float(best), 6)

    def _fallback_feature_sim(self, first_kr, candidate_name, first_sim=None):
        """2음절/모음 흐름 점수를 기존 similarity 스케일(0~4)에 맞춰 변환."""
        first_score = self._first_syllable_trace_score(first_kr, candidate_name)
        two_score = self._two_syllable_trace_score(first_kr, candidate_name)
        vowel_score = self._vowel_flow_score(first_kr, candidate_name)
        trace = 0.45 * two_score + 0.35 * vowel_score + 0.20 * first_score
        feature_sim = int(round((1.0 - trace) * self.MAX_SIM))
        if first_sim is None:
            return min(max(feature_sim, 0), self.MAX_SIM)
        return min(int(first_sim), min(max(feature_sim, 0), self.MAX_SIM))

    def _fallback_given_score(self, row, first_kr, sim, source='safe_pool_anchor'):
        """SAFE_NAME_POOL fallback 전용 점수.

        첫 음절만 보던 기존 fallback을 보완하여, 두 번째 음절 대응과 전체 모음 흐름을
        더 강하게 반영한다. 성별 적합도는 후보 배제용 hard gate 역할을 우선하고,
        순위 점수에서는 과도하게 지배하지 않도록 완만하게 반영한다.
        overuse penalty는 적용하지 않는다. result_2 대안 제공으로 다양성을 해결한다.
        source는 safe_pool_phonetic / safe_pool_anchor / safe_pool_general로 세분화한다.
        """
        name = str(row['name'])
        gender_fit = float(row.get('gender_fit_score', 1.0)) if not pd.isna(row.get('gender_fit_score', 1.0)) else 1.0
        gender_mismatch = bool(row.get('is_gender_mismatch', False))
        first_score = self._first_syllable_trace_score(first_kr, name)
        second_score = self._second_syllable_trace_score(first_kr, name)
        vowel_score = self._vowel_flow_score(first_kr, name)
        gender_score = self._fallback_gender_fit_score(gender_fit, gender_mismatch)
        return float(
            0.12 * first_score +
            0.42 * second_score +
            0.20 * vowel_score +
            0.08 * self._fallback_naturalness_bucket_score(row.get('weight', 0)) +
            0.08 * gender_score +
            0.05 * self._source_score(source) +
            0.05 * self._meaning_score(name)
        )

    def _candidate_tuple_from_scored_row(self, row, sim, score, source='safe_pool_fallback', extra_meta=None):
        """fallback에서 계산된 별도 점수와 meta를 보존한 candidate tuple 생성."""
        name = str(row['name'])
        weight = int(row['weight'])
        sim = int(min(max(sim, 0), self.MAX_SIM))
        pop_pct = float(row.get('pop_pct', 0.0)) if not pd.isna(row.get('pop_pct', 0.0)) else 0.0
        gender_fit = float(row.get('gender_fit_score', 1.0)) if not pd.isna(row.get('gender_fit_score', 1.0)) else 1.0
        gender_mismatch = bool(row.get('is_gender_mismatch', False))
        quality = self._given_quality(
            name, weight, sim, pop_pct=pop_pct, nlen=int(row.get('nlen', len(name))),
            gender_fit=gender_fit, gender_mismatch=gender_mismatch
        )
        meta = {
            'source': source,
            'quality': quality,
            'pop_pct': round(pop_pct, 4),
            'nlen': int(row.get('nlen', len(name))),
            'gender_fit_score': round(gender_fit, 4),
            'male_weight': int(row.get('male_weight', 0)),
            'female_weight': int(row.get('female_weight', 0)),
            'target_gender_weight': int(row.get('target_gender_weight', weight)),
            'opposite_gender_weight': int(row.get('opposite_gender_weight', 0)),
            'opposite_gender_ratio': round(float(row.get('opposite_gender_ratio', 0.0)), 4),
            'gender_mismatch': gender_mismatch,
            'same_as_transliteration': False,
            'foreign_transliteration_name': False,
        }
        if extra_meta:
            meta.update(extra_meta)
        return (name, weight, sim, float(score), meta)

    def _preserved_sound_info(self, first_kr, candidate_name):
        src = ''.join(self._safe_hangul_syllables(first_kr, limit=2))
        tgt = ''.join(self._safe_hangul_syllables(candidate_name, limit=2))
        return src, tgt

    def _conversion_reason(self, first_kr, cand):
        """사용자에게 보여줄 변환 이유 초안. LLM 설명 전 템플릿/진단값으로 활용."""
        if not cand:
            return {'kr': '', 'en': '', 'preserved_source': '', 'preserved_target': ''}
        name = cand[0]
        meta = self._candidate_meta(cand)
        src, tgt = self._preserved_sound_info(first_kr, name)
        source = meta.get('source')
        two = meta.get('two_syllable_score')
        vowel = meta.get('vowel_flow_score')
        if source == 'safe_pool_fallback':
            kr = f"음차 결과 '{first_kr}'의 앞소리 '{src}'와 후보 이름 '{name}'의 '{tgt}'를 비교해, 2음절 흐름과 모음 울림이 자연스럽게 이어지는 이름을 선택했습니다."
            en = f"We compared the opening sound '{src}' from the Korean transliteration with '{tgt}' in '{name}', choosing a Korean-style name that preserves the two-syllable flow and vowel feel."
        else:
            kr = f"음차 결과 '{first_kr}'의 핵심 소리 '{src}'를 한국 이름 '{name}'의 '{tgt}'에 반영했습니다."
            en = f"We reflected the core opening sound '{src}' from the Korean transliteration in the Korean-style name '{name}'."
        if two is not None and vowel is not None:
            kr += f" 2음절 유사도 {float(two):.2f}, 모음 흐름 유사도 {float(vowel):.2f}를 함께 고려했습니다."
            en += f" The two-syllable similarity was {float(two):.2f}, and the vowel-flow similarity was {float(vowel):.2f}."
        return {'kr': kr, 'en': en, 'preserved_source': src, 'preserved_target': tgt}

    def _classify_safe_pool_source(self, stage_name, first_score, two_score, vowel_score):
        """SAFE_NAME_POOL 후보의 출처를 세분화한다.

        - safe_pool_phonetic: SAFE_POOL 후보지만 2음절/모음 흐름 연결성이 강한 경우
        - safe_pool_anchor: 특정 초성/중성 anchor를 통해 연결된 일반적인 SAFE_POOL 후보
        - safe_pool_general: 초성/중성 조건을 모두 못 맞춰 성별 SAFE_POOL 전체에서 온 후보
        """
        first_score = float(first_score or 0.0)
        two_score = float(two_score or 0.0)
        vowel_score = float(vowel_score or 0.0)
        if stage_name == 'safe_pool_general':
            return 'safe_pool_general'
        if (two_score >= 0.74 and vowel_score >= 0.62) or (two_score >= 0.82):
            return 'safe_pool_phonetic'
        strong_anchor_stages = {
            'exact_cho_same_jung', 'exact_cho_variant_jung',
            'transformed_cho_same_jung', 'transformed_cho_variant_jung',
            'rule_sound', 'rule_cho_jung'
        }
        if stage_name in strong_anchor_stages and (two_score >= 0.60 or vowel_score >= 0.70 or first_score >= 0.80):
            return 'safe_pool_anchor'
        return 'safe_pool_anchor'

    def _annotate_candidate_trace_and_rescue(self, first_kr, candidates):
        """일반 변환 후보에 2음절/모음 흐름 진단값을 추가하고, 살릴 수 있는 Q3를 phonetic_rescue로 표시한다."""
        out = []
        for cand in candidates or []:
            if not cand:
                continue
            name, weight, sim = cand[0], int(cand[1]), int(cand[2])
            meta = dict(self._candidate_meta(cand))
            first_score = self._first_syllable_trace_score(first_kr, name)
            two_score = self._two_syllable_trace_score(first_kr, name)
            vowel_score = self._vowel_flow_score(first_kr, name)
            updates = {
                'first_syllable_score': round(float(first_score), 4),
                'two_syllable_score': round(float(two_score), 4),
                'vowel_flow_score': round(float(vowel_score), 4),
                'phonetic_rescue': False,
            }
            # Q3이지만 원래 이름의 2음절/모음 흐름을 충분히 보존하면 result 후보로 살린다.
            if (
                meta.get('quality') == 'Q3'
                and two_score >= self.PHONETIC_RESCUE_TWO_SYLLABLE_MIN
                and vowel_score >= self.PHONETIC_RESCUE_VOWEL_FLOW_MIN
                and not meta.get('gender_mismatch')
                and float(meta.get('gender_fit_score', 1.0)) >= self.GENDER_FIT_MIN_SHARE
            ):
                updates.update({
                    'source': 'phonetic_rescue',
                    'phonetic_rescue': True,
                    'rescue_reason': 'q3_high_two_syllable_and_vowel_flow',
                })
                score = self._fs(
                    weight, sim, source='phonetic_rescue', name=name,
                    gender_fit=meta.get('gender_fit_score', 1.0)
                )
                cand = (name, weight, sim, float(score), {**meta, **updates})
            else:
                cand = self._with_candidate_meta(cand, **updates)
            out.append(cand)
        return out

    def _fallback_natural_given_names(self, first_kr, sex, limit=2, exclude_names=None, exclude_foreign=True):
        """저품질 이름 후보가 나왔을 때 SAFE_NAME_POOL에서 자연스러운 대안을 찾는다.

        SAFE_NAME_POOL에서도 기존 초성/중성 변환 규칙을 적용하되,
        정확한 첫소리 보존을 가장 우선한다. 즉, 예→예/재→제처럼
        초성이 같고 중성이 동일군(ㅔ/ㅐ/ㅖ 포함)인 후보를 먼저 보고,
        후보가 없을 때만 모음 변형, 초성 변형 순서로 단계적으로 완화한다.

        완화 순서:
        1) 초성 동일 + 중성 동일군
        2) 초성 동일 + 중성 변형
        3) 초성 변형 + 중성 동일군
        4) 초성 변형 + 중성 변형
        5) 초성 동일만
        6) 초성 변형만
        7) 중성 동일/변형만
        8) 성별 SAFE_NAME_POOL 전체
        """
        if not first_kr:
            return []
        exclude_names = {self._normalize_korean_name(n) for n in (exclude_names or set()) if n}

        cho1, jung1 = decompose(first_kr[0])
        sound_set, cho_set, jung_set, pair_map = self._fallback_rule_sound_candidates(cho1, jung1)

        # SAFE_POOL fallback 완화 순서 정책
        # 1순위: 초성 동일 + 중성 동일군
        # 2순위: 초성 동일 + 중성 변형
        # 3순위: 초성 변형 허용
        #
        # 예: Yale(예일)에서 예* 후보가 있는 경우, ㅇ→ㅎ 완화로 만들어진 해* 후보가
        # 먼저 선택되지 않도록 exact cho를 transformed cho보다 우선한다.
        exact_cho_set = {cho1}
        transformed_cho_set = set(cho_set) - exact_cho_set
        same_jung_set = same_jung_members(jung1)
        variant_jung_set = set(jung_set) - same_jung_set

        base = self.safe_db2[self.safe_db2['sex'] == sex].copy()
        if base.empty:
            return []

        stages = [
            ('exact_cho_same_jung', base[(base['cho1'].isin(exact_cho_set)) & (base['jung1'].isin(same_jung_set))].copy()),
            ('exact_cho_variant_jung', base[(base['cho1'].isin(exact_cho_set)) & (base['jung1'].isin(variant_jung_set))].copy()),
            ('transformed_cho_same_jung', base[(base['cho1'].isin(transformed_cho_set)) & (base['jung1'].isin(same_jung_set))].copy()),
            ('transformed_cho_variant_jung', base[(base['cho1'].isin(transformed_cho_set)) & (base['jung1'].isin(variant_jung_set))].copy()),
            ('exact_cho_only', base[base['cho1'].isin(exact_cho_set)].copy()),
            ('transformed_cho_only', base[base['cho1'].isin(transformed_cho_set)].copy()),
            ('same_jung_only', base[base['jung1'].isin(same_jung_set)].copy()),
            ('variant_jung_only', base[base['jung1'].isin(variant_jung_set)].copy()),
            ('safe_pool_general', base.copy()),
        ]

        pool = None
        stage_name = 'none'
        for st, cand_pool in stages:
            if cand_pool.empty:
                continue
            if exclude_names:
                cand_pool = cand_pool[~cand_pool['name'].apply(lambda n: self._normalize_korean_name(n) in exclude_names)].copy()
            if exclude_foreign:
                cand_pool = cand_pool[~cand_pool['name'].apply(lambda n: self._is_foreign_transliterated_name(n, first_kr))].copy()
            if not cand_pool.empty:
                pool = cand_pool
                stage_name = st
                break

        if pool is None or pool.empty:
            return []

        pool['first_sim'] = pool['name'].apply(lambda n: self._calc_fallback_similarity(cho1, jung1, n))
        pool['first_syllable_score'] = pool['name'].apply(lambda n: self._first_syllable_trace_score(first_kr, n))
        pool['two_syllable_score'] = pool['name'].apply(lambda n: self._two_syllable_trace_score(first_kr, n))
        pool['second_syllable_score'] = pool['name'].apply(lambda n: self._second_syllable_trace_score(first_kr, n))
        pool['vowel_flow_score'] = pool['name'].apply(lambda n: self._vowel_flow_score(first_kr, n))
        pool['sim'] = pool.apply(lambda r: self._fallback_feature_sim(first_kr, r['name'], r['first_sim']), axis=1)
        pool['source'] = pool.apply(
            lambda r: self._classify_safe_pool_source(
                stage_name, r.get('first_syllable_score', 0.0),
                r.get('two_syllable_score', 0.0), r.get('vowel_flow_score', 0.0)
            ),
            axis=1
        )
        pool['fallback_stage'] = stage_name
        pool['given_score'] = pool.apply(lambda r: self._fallback_given_score(r, first_kr, r['sim'], source=r['source']), axis=1)
        # fallback에서는 첫 음절만 보지 않고 2음절 유사도와 모음 흐름을 우선 정렬한다.
        # weight는 여전히 마지막 tie-breaker로만 사용한다.
        pool = pool.sort_values(
            ['given_score', 'second_syllable_score', 'two_syllable_score', 'vowel_flow_score', 'first_syllable_score', 'sim', 'gender_fit_score', 'pop_pct', 'weight'],
            ascending=[False, False, False, False, False, True, False, False, False]
        )
        out = []
        for _, rr in pool.head(limit).iterrows():
            extra_meta = {
                'fallback_stage': stage_name,
                'first_syllable_score': round(float(rr.get('first_syllable_score', 0.0)), 4),
                'two_syllable_score': round(float(rr.get('two_syllable_score', 0.0)), 4),
                'second_syllable_score': round(float(rr.get('second_syllable_score', 0.0)), 4),
                'vowel_flow_score': round(float(rr.get('vowel_flow_score', 0.0)), 4),
                'first_syllable_similarity': int(rr.get('first_sim', rr.get('sim', self.MAX_SIM))),
            }
            cand = self._candidate_tuple_from_scored_row(
                rr, int(rr['sim']), float(rr['given_score']),
                source=str(rr.get('source', 'safe_pool_anchor')), extra_meta=extra_meta
            )
            out.append(cand)
        return out

    # 기존 함수명 호환용
    def _fallback_q4(self, first_kr, sex):
        cands = self._fallback_natural_given_names(first_kr, sex, limit=1)
        return cands[0] if cands else None

    def _calc_fallback_similarity(self, cho1, jung1, name):
        """SAFE_NAME_POOL fallback 후보의 첫 음절 유사도 계산.

        ㅔ/ㅐ/ㅖ는 동일 중성군으로 보아 중성 비용을 0으로 처리한다.
        """
        if not name:
            return self.MAX_SIM
        cho_n, jung_n = decompose(name[0])
        sim = 0

        # 초성 거리
        if cho_n != cho1:
            if self.ASPIRATE_TO_PLAIN.get(cho1) == cho_n:
                sim += 1
            elif cho1 == 'ㄹ' and cho_n in {'ㄴ', 'ㅇ'}:
                sim += 1
            else:
                sim += 2

        # 중성 거리
        if not same_jung_equiv(jung_n, jung1):
            jung_rule = FIRST_JUNG_RULES.get(jung1, ([], []))
            if isinstance(jung_rule, str):
                target = jung_rule.split('_')[1]
                sub = FIRST_JUNG_RULES.get(target, ([], []))
                primary, secondary = sub[0], sub[1]
                if same_jung_equiv(jung_n, target):
                    sim += 1
                elif any(same_jung_equiv(jung_n, p) for p in primary):
                    sim += 1
                elif any(same_jung_equiv(jung_n, s) for s in secondary):
                    sim += 2
                else:
                    sim += 2
            else:
                primary, secondary = jung_rule
                if any(same_jung_equiv(jung_n, p) for p in primary):
                    sim += 1
                elif any(same_jung_equiv(jung_n, s) for s in secondary):
                    sim += 2
                else:
                    sim += 2
        return min(sim, self.MAX_SIM)

    def _surname_quality(self, surname, freq=None):
        if not surname:
            return 'S4'
        if surname in self.COMPOUND_SURNAMES:
            return 'S4'
        freq = SURNAME_FREQ.get(surname, 0) if freq is None else freq
        if freq >= 500_000:
            return 'S1'
        if freq >= 100_000:
            return 'S2'
        if freq >= 20_000:
            return 'S3'
        return 'S4'

    def _is_primary_surname_allowed(self, surname, freq, similarity):
        if not surname:
            return False
        if surname in self.COMPOUND_SURNAMES:
            return False
        if freq >= self.PRIMARY_SURNAME_MIN_FREQ:
            return True
        if freq >= self.PRIMARY_SURNAME_EXACT_MIN_FREQ and similarity == 0:
            return True
        return False

    def _surname_candidate_dict(self, surname, freq, similarity, source='primary'):
        freq = int(freq or 0)
        similarity = int(min(max(similarity, 0), self.MAX_SIM))
        score = float(self._ls(freq, similarity))
        # 희소 성씨/복성 패널티
        if surname in self.COMPOUND_SURNAMES:
            score -= 0.50
        elif freq < 50_000:
            score -= 0.35
        elif freq < 100_000:
            score -= 0.18
        return {
            'surname': surname,
            'freq': freq,
            'similarity': similarity,
            'score': round(float(score), 6),
            'quality': self._surname_quality(surname, freq),
            'source': source,
        }

    def _surname_exclusion_ok(self, surname, gn):
        if not gn:
            return True
        fc = gn[0]
        if fc in ['예','비'] and surname == '노':
            return False
        if fc == '라' and surname == '구':
            return False
        return True

    def _fallback_common_surnames(self, lk, gn=None, limit=2):
        if not lk:
            base_cho = 'ㄱ'
        else:
            base_cho, _ = decompose(lk[0])
        cho_keys = [base_cho]
        if base_cho in self.ASPIRATE_TO_PLAIN:
            cho_keys.append(self.ASPIRATE_TO_PLAIN[base_cho])
        if base_cho == 'ㄹ':
            cho_keys.extend(['ㅇ', 'ㄴ'])

        names = []
        for ck in cho_keys:
            names.extend(self.COMMON_SURNAME_BY_CHO.get(ck, []))
        if not names:
            names = ['김', '이', '박', '정', '최']

        seen = set()
        candidates = []
        for s in names:
            if s in seen:
                continue
            seen.add(s)
            if not self._surname_exclusion_ok(s, gn):
                continue
            freq = SURNAME_FREQ.get(s, 0)
            # fallback은 자연스러움을 우선하므로 similarity는 보수적으로 1~2로 부여
            sc, _ = decompose(s[0])
            sim = 1 if sc == base_cho or self.ASPIRATE_TO_PLAIN.get(base_cho) == sc else 2
            candidates.append(self._surname_candidate_dict(s, freq, sim, source='common_fallback'))
        candidates.sort(key=lambda x: (x['score'], x['freq']), reverse=True)
        return candidates[:limit]

    def match_last_detailed(self, lk, gn=None, mode='natural'):
        if not lk:
            return self._fallback_common_surnames(lk, gn=gn, limit=2)

        cho,jung = decompose(lk[0])
        ps = self._make_pairs(cho,jung,LAST_CHO_RULES,LAST_JUNG_RULES)
        al = []
        for c in set(k[0] for k in ps):
            al.extend(SURNAME_TABLE.get(c,[]))
        if not al:
            return self._fallback_common_surnames(lk, gn=gn, limit=2)

        candidates = []
        for s in al:
            sc,sj = decompose(s[0])
            if (sc,sj) in ps:
                cd,jd = ps[(sc,sj)]
                sim = cd + jd
                if not self._surname_exclusion_ok(s, gn):
                    continue
                candidates.append(self._surname_candidate_dict(s, SURNAME_FREQ.get(s,0), sim, source='primary'))

        if not candidates:
            return self._fallback_common_surnames(lk, gn=gn, limit=2)

        candidates.sort(key=lambda x:(x['score'], x['freq']), reverse=True)

        if mode == 'natural':
            allowed = [c for c in candidates if self._is_primary_surname_allowed(c['surname'], c['freq'], c['similarity'])]
            if allowed:
                # result_2가 result_1의 성씨 대안이 될 수 있도록, 자연 매칭 성씨가 1개뿐이면
                # common fallback 성씨를 보충한다. 단, 같은 성씨는 중복하지 않는다.
                seen_surnames = {c['surname'] for c in allowed}
                if len(allowed) < 2:
                    extras = self._fallback_common_surnames(lk, gn=gn, limit=4)
                    for ex in extras:
                        if ex['surname'] not in seen_surnames:
                            allowed.append(ex)
                            seen_surnames.add(ex['surname'])
                        if len(allowed) >= 2:
                            break
                return allowed[:2]
            return self._fallback_common_surnames(lk, gn=gn, limit=2)

        return candidates[:2]

    def match_last(self, lk, gn=None):
        cands = self.match_last_detailed(lk, gn=gn, mode='natural')
        if not cands:
            return None, None
        return cands[0]['surname'], cands[1]['surname'] if len(cands)>1 else None

    def _form_score(self, surname, given_name):
        if len(surname or '') == 1 and len(given_name or '') == 2:
            return 1.0
        if len(surname or '') == 1 and len(given_name or '') == 1:
            return 0.60
        if len(surname or '') == 2 and len(given_name or '') == 2:
            return 0.50
        return 0.40

    def _full_name_score(self, given, surname_info):
        if not given or not surname_info:
            return 0.0
        name, weight, sim, given_score = given[0], int(given[1]), int(given[2]), float(given[3])
        surname = surname_info['surname']
        surname_score = float(surname_info['score'])
        form_score = self._form_score(surname, name)
        explainability = self._meaning_score(name)

        penalty = 0.0
        meta = self._candidate_meta(given)
        if meta.get('quality') == 'Q4':
            penalty += 0.35
        if meta.get('gender_mismatch') or float(meta.get('gender_fit_score', 1.0)) < self.GENDER_FIT_MIN_SHARE:
            penalty += 0.45
        if surname_info.get('quality') == 'S4':
            penalty += 0.35
        elif surname_info.get('quality') == 'S3':
            penalty += 0.12
        if name in set(BANNED_NAMES) | self.EXTRA_BANNED_GIVEN_NAMES:
            penalty += 1.0
        if surname in self.COMPOUND_SURNAMES:
            penalty += 0.35
        if surname and name and surname[-1] == name[0]:
            penalty += 0.05
        if weight < self.MIN_WEIGHT_BY_SIM.get(min(sim, self.MAX_SIM), 2000):
            penalty += 0.20

        return round(float(
            0.45 * given_score +
            0.30 * surname_score +
            0.15 * form_score +
            0.10 * explainability -
            penalty
        ), 6)

    def _build_full_candidates(self, first_kr, last_kr, sex):
        r1, r2 = self.match_first(first_kr, sex)
        raw_given_candidates = self._annotate_candidate_trace_and_rescue(first_kr, [c for c in [r1, r2] if c])

        # 음차명 정책 적용:
        # 1) 조이/안나처럼 음차 결과와 변환 결과가 동일한 후보는 result 후보에서 제외한다.
        #    이 경우 바로 SAFE_NAME_POOL로 가지 않고, 기존 match_first가 반환한 다음 순위 후보를 우선 사용한다.
        # 2) 필립/엠마처럼 사용자의 실제 음차와 다르지만 외국 이름을 그대로 음차한 이름도 제외한다.
        given_candidates = self._apply_transliteration_name_policy(raw_given_candidates, first_kr)

        # 1차 후보가 없거나 품질이 낮을 때만 SAFE_NAME_POOL 대안을 추가한다.
        # 동일음차 후보가 제외된 경우에도 기존 후보가 남아 있으면 그 후보를 우선한다.
        primary_meta = self._candidate_meta(given_candidates[0]) if given_candidates else {'quality': 'Q4'}
        needs_safe_pool = (
            (not given_candidates)
            or primary_meta.get('quality') == 'Q4'
            or (primary_meta.get('quality') == 'Q3' and not primary_meta.get('phonetic_rescue'))
        )
        if needs_safe_pool:
            fallback_given = self._fallback_natural_given_names(
                first_kr, sex, limit=4, exclude_names={self._normalize_korean_name(first_kr)}, exclude_foreign=True
            )
            given_candidates.extend(fallback_given)

        given_candidates = self._apply_transliteration_name_policy(given_candidates, first_kr)
        given_candidates = self._dedupe_given_candidates(given_candidates)

        # result_2가 result_1의 first-name 대안이 될 수 있도록, 후보 이름이 1개뿐이면
        # SAFE_NAME_POOL에서 추가 후보를 보충한다. 이 보충은 동일음차 후보를 제외한 뒤에도
        # 기존 후보가 1개만 남는 경우에 한해 제한적으로 수행한다.
        if len({c[0] for c in given_candidates if c}) < 2:
            existing_names = {self._normalize_korean_name(c[0]) for c in given_candidates if c}
            supplemental = self._fallback_natural_given_names(
                first_kr, sex, limit=6, exclude_names=existing_names | {self._normalize_korean_name(first_kr)}, exclude_foreign=True
            )
            given_candidates.extend(supplemental)
            given_candidates = self._apply_transliteration_name_policy(given_candidates, first_kr)
            given_candidates = self._dedupe_given_candidates(given_candidates)

        full_candidates = []
        for given in given_candidates:
            # result_1 후보로는 Q4를 강하게 제한하되, result_2용 비교 후보로 남길 수 있다.
            surname_candidates = self.match_last_detailed(last_kr, gn=given[0], mode='natural')
            for sn in surname_candidates:
                full_score = self._full_name_score(given, sn)
                full_candidates.append({
                    'full': f"{sn['surname']}{given[0]}",
                    'given': given,
                    'surname': sn,
                    'full_score': full_score,
                })

        # 같은 full 중 가장 높은 점수만 유지
        best = {}
        for c in full_candidates:
            if c['full'] not in best or c['full_score'] > best[c['full']]['full_score']:
                best[c['full']] = c
        full_candidates = list(best.values())
        full_candidates.sort(key=lambda x:x['full_score'], reverse=True)
        return full_candidates

    def convert(self, first_kr, last_kr, sex):
        result = {'first_kr': first_kr, 'last_kr': last_kr, 'sex': sex}
        candidates = self._build_full_candidates(first_kr, last_kr, sex)

        if not candidates:
            result.update({
                'full_1': None, 'first_1': None, 'last_1': None,
                'weight': 0, 'similarity': 0, 'score': 0,
                'full_2': None,
                'source': 'none',
            })
            return result

        # result_1은 원칙적으로 Q1/Q2 given만 노출한다.
        # Q3는 result_2 또는 more_phonetic/대안 후보로 분리하고, Q1/Q2가 없을 때만 예외적으로 사용한다.
        # 동일음차 후보는 _build_full_candidates 단계에서 이미 제외된다.
        primary = None
        for c in candidates:
            g_meta = self._candidate_meta(c['given'])
            if g_meta.get('quality') in {'Q1', 'Q2'} and c['surname'].get('quality') != 'S4':
                primary = c
                break
        if primary is None:
            for c in candidates:
                g_meta = self._candidate_meta(c['given'])
                if g_meta.get('quality') == 'Q3' and g_meta.get('phonetic_rescue') and c['surname'].get('quality') != 'S4':
                    primary = c
                    break
        if primary is None:
            for c in candidates:
                g_meta = self._candidate_meta(c['given'])
                if g_meta.get('quality') == 'Q3' and c['surname'].get('quality') != 'S4':
                    primary = c
                    break
        if primary is None:
            primary = candidates[0]

        # result_2는 result_1의 대안이 되도록 성과 이름이 모두 달라야 한다.
        # 먼저 Q1/Q2 대안을 찾고, 없으면 Q3를 more_phonetic 성격의 대안으로 허용한다.
        secondary = None
        for allowed_q in ({'Q1', 'Q2'}, {'Q3'}):
            for c in candidates:
                if c['full'] == primary['full']:
                    continue
                if c['given'][0] == primary['given'][0]:
                    continue
                if c['surname']['surname'] == primary['surname']['surname']:
                    continue
                if not self._allow_result2_given(c['given']):
                    continue
                if self._candidate_meta(c['given']).get('quality') not in allowed_q:
                    continue
                secondary = c
                break
            if secondary is not None:
                break

        # 엄격 조건(성+이름 모두 다름)을 만족하는 result_2가 없으면,
        # result_2 전용 보충 후보를 생성한다. result_1 자체는 건드리지 않고,
        # result_2가 실질적인 대안이 되도록 first/surname을 모두 다르게 강제한다.
        if secondary is None:
            primary_given_name = primary['given'][0]
            primary_surname_name = primary['surname']['surname']
            supplemental_given = self._fallback_natural_given_names(
                first_kr, sex, limit=10,
                exclude_names={self._normalize_korean_name(first_kr), self._normalize_korean_name(primary_given_name)},
                exclude_foreign=True,
            )
            supplemental_candidates = []
            for given in supplemental_given:
                if not given or given[0] == primary_given_name or not self._allow_result2_given(given):
                    continue
                surname_candidates = self.match_last_detailed(last_kr, gn=given[0], mode='natural')
                # natural 후보가 primary surname으로만 구성되면 common fallback으로 보충한다.
                if not any(sn['surname'] != primary_surname_name for sn in surname_candidates):
                    surname_candidates.extend(self._fallback_common_surnames(last_kr, gn=given[0], limit=5))
                seen_full = set()
                for sn in surname_candidates:
                    if sn['surname'] == primary_surname_name:
                        continue
                    full = f"{sn['surname']}{given[0]}"
                    if full in seen_full:
                        continue
                    seen_full.add(full)
                    supplemental_candidates.append({
                        'full': full,
                        'given': given,
                        'surname': sn,
                        'full_score': self._full_name_score(given, sn) - 0.02,  # result_2 보충 후보임을 약하게 감점
                    })
            if supplemental_candidates:
                supplemental_candidates.sort(key=lambda x:x['full_score'], reverse=True)
                secondary = supplemental_candidates[0]

        g1 = primary['given']
        sn1 = primary['surname']
        g1_meta = self._candidate_meta(g1)
        result.update({
            'full_1': primary['full'],
            'first_1': g1[0],
            'last_1': sn1['surname'],
            'weight': int(g1[1]),
            'similarity': int(g1[2]),
            'score': round(float(g1[3]), 4),
            'full_score': round(float(primary['full_score']), 4),
            'source': g1_meta.get('source'),
            'given_source': g1_meta.get('source'),
            'given_quality': g1_meta.get('quality'),
            'given_pop_pct': g1_meta.get('pop_pct'),
            'gender_fit_score': g1_meta.get('gender_fit_score'),
            'male_weight_for_name': g1_meta.get('male_weight'),
            'female_weight_for_name': g1_meta.get('female_weight'),
            'opposite_gender_ratio': g1_meta.get('opposite_gender_ratio'),
            'gender_mismatch': g1_meta.get('gender_mismatch'),
            'same_as_transliteration': g1_meta.get('same_as_transliteration'),
            'foreign_transliteration_name': g1_meta.get('foreign_transliteration_name'),
            'surname_source': sn1.get('source'),
            'surname_quality': sn1.get('quality'),
            'surname_freq': sn1.get('freq'),
            'surname_similarity': sn1.get('similarity'),
        })
        reason1 = self._conversion_reason(first_kr, g1)
        result.update({
            'preserved_source_syllables': reason1.get('preserved_source'),
            'preserved_target_syllables': reason1.get('preserved_target'),
            'conversion_reason_kr': reason1.get('kr'),
            'conversion_reason_en': reason1.get('en'),
            'first_syllable_score': g1_meta.get('first_syllable_score'),
            'two_syllable_score': g1_meta.get('two_syllable_score'),
            'vowel_flow_score': g1_meta.get('vowel_flow_score'),
            'fallback_stage': g1_meta.get('fallback_stage'),
            'phonetic_rescue': g1_meta.get('phonetic_rescue', False),
            'rescue_reason': g1_meta.get('rescue_reason'),
            'result_1_role': 'primary_recommendation',
        })

        if secondary:
            g2 = secondary['given']
            sn2 = secondary['surname']
            g2_meta = self._candidate_meta(g2)
            result.update({
                'full_2': secondary['full'],
                'first_2': g2[0],
                'last_2': sn2['surname'],
                'weight_2': int(g2[1]),
                'similarity_2': int(g2[2]),
                'score_2': round(float(g2[3]), 4),
                'full_score_2': round(float(secondary['full_score']), 4),
                'given_source_2': g2_meta.get('source'),
                'given_quality_2': g2_meta.get('quality'),
                'gender_fit_score_2': g2_meta.get('gender_fit_score'),
                'male_weight_for_name_2': g2_meta.get('male_weight'),
                'female_weight_for_name_2': g2_meta.get('female_weight'),
                'opposite_gender_ratio_2': g2_meta.get('opposite_gender_ratio'),
                'same_as_transliteration_2': g2_meta.get('same_as_transliteration'),
                'foreign_transliteration_name_2': g2_meta.get('foreign_transliteration_name'),
                'surname_source_2': sn2.get('source'),
                'surname_quality_2': sn2.get('quality'),
                'surname_freq_2': sn2.get('freq'),
            })
            reason2 = self._conversion_reason(first_kr, g2)
            result.update({
                'preserved_source_syllables_2': reason2.get('preserved_source'),
                'preserved_target_syllables_2': reason2.get('preserved_target'),
                'conversion_reason_kr_2': reason2.get('kr'),
                'conversion_reason_en_2': reason2.get('en'),
                'first_syllable_score_2': g2_meta.get('first_syllable_score'),
                'two_syllable_score_2': g2_meta.get('two_syllable_score'),
                'vowel_flow_score_2': g2_meta.get('vowel_flow_score'),
                'fallback_stage_2': g2_meta.get('fallback_stage'),
                'phonetic_rescue_2': g2_meta.get('phonetic_rescue', False),
                'rescue_reason_2': g2_meta.get('rescue_reason'),
                'result_2_role': 'mix_and_match_alternative',
            })
        else:
            result['full_2'] = None

        return result




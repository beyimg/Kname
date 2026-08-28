# -*- coding: utf-8 -*-
"""음차 음절 ↔ 변환 이름 음절의 매칭 계산

유사도는 phonetics.py의 조음 자질 모델을 사용한다.
(구버전의 '자모 그룹 일치' 방식은 아래 onset_similar / vowel_similar로 남겨두었으며,
 설명 문구를 고를 때 '무엇이 같은가'를 판단하는 용도로만 쓰인다.)
"""
from pronounce_guide import decompose, romanize_syllable
from phonetics import syllable_sim

# 초성 유사군 (설명 문구 선택용)
ONSET_GROUPS = [
    {'g','kk','k'}, {'d','tt','t'}, {'b','pp','p'}, {'s','ss'},
    {'j','jj','ch'}, {'n'}, {'m'}, {'r'}, {'h'}, {''},
]
# 중성 유사군 (설명 문구 선택용)
VOWEL_GROUPS = [
    {'a','ya','wa'}, {'ae','e','yae','ye','wae','we','oe'}, {'eo','yeo','wo'},
    {'o','yo'}, {'u','yu','wi'}, {'eu','ui'}, {'i'},
]

def _group_of(item, groups):
    for i, g in enumerate(groups):
        if item in g:
            return i
    return -1

def onset_similar(a, b):
    ga, gb = _group_of(a, ONSET_GROUPS), _group_of(b, ONSET_GROUPS)
    return ga != -1 and ga == gb

def vowel_similar(a, b):
    ga, gb = _group_of(a, VOWEL_GROUPS), _group_of(b, VOWEL_GROUPS)
    return ga != -1 and ga == gb

def syllable_similarity(s1, s2):
    """두 음절의 음성학적 유사도 0~1 (phonetics.syllable_sim)"""
    c1, j1, g1 = decompose(s1)
    c2, j2, g2 = decompose(s2)
    if c1 is None or c2 is None:
        return 0.0
    return round(syllable_sim(c1, j1, g1, c2, j2, g2), 3)


def match_syllables(translit, korean_given):
    """
    음차(translit)의 각 음절이 한국이름(korean_given)의 어느 글자에
    가장 잘 매칭되는지 전역 최적으로 계산.
    반환: [{'src':음차음절, 'tgt':이름글자, 'sim':유사도, 'kind':'strong'|'weak'|'none'}]
    """
    import itertools
    n_tgt = len(korean_given)
    n_src = len(translit)
    # 유사도 행렬
    sim = [[syllable_similarity(translit[si], korean_given[ti]) for si in range(n_src)]
           for ti in range(n_tgt)]

    # 각 이름 글자에 서로 다른 음차 음절을 할당하는 조합 중 총합 최대를 찾음
    best_assign, best_total = None, -1.0
    src_indices = range(n_src)
    for combo in itertools.permutations(src_indices, min(n_tgt, n_src)):
        # combo[ti] = 이름 글자 ti에 할당된 음차 음절 인덱스
        total = sum(sim[ti][combo[ti]] for ti in range(len(combo)))
        # 순서 보존 보너스: 앞→뒤 순서가 유지되면 가산
        order_bonus = sum(0.05 for ti in range(1,len(combo)) if combo[ti] > combo[ti-1])
        total += order_bonus
        if total > best_total:
            best_total, best_assign = total, combo

    matches = []
    for ti, tgt in enumerate(korean_given):
        if best_assign and ti < len(best_assign):
            si = best_assign[ti]
            s = sim[ti][si]
            kind = 'strong' if s >= 0.55 else ('weak' if s >= 0.3 else 'none')
            matches.append({'tgt':tgt, 'src':translit[si] if kind!='none' else None,
                            'sim':round(s,2), 'kind':kind})
        else:
            matches.append({'tgt':tgt, 'src':None, 'sim':0.0, 'kind':'none'})
    return matches

if __name__ == '__main__':
    for tr, kr in [('소피아','수아'),('브레이든','범진'),('매켄지','민지'),
                   ('엘리자베스','예린'),('리엄','리안'),('제이컵','재익')]:
        print(f"\n{tr} → {kr}:")
        for m in match_syllables(tr, kr):
            src = m['src'] or '—'
            print(f"  {m['tgt']} ← {src} ({m['kind']}, {m['sim']})")

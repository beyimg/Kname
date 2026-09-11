# -*- coding: utf-8 -*-
"""
웹페이지용 이름 변환 이유 설명 생성기 (최종본).

4단계 구조:
  1) 음차 결과 + 발음  (how the English name sounds in Korean)
  2) 변환된 한국 이름 + 발음
  3) 음절 매칭 설명 (어떤 음차 음절이 어떤 이름 글자에 반영됐는지)
       - 유사도 수준별로 자연스러운 표현 + 색(strong/partial/loose) 제공
  4) 가깝게 못 맞춘 경우: 완벽한 매칭이 어려웠지만 최대한 비슷하고
       자연스러운 이름을 골랐다는 안내
       (이름 등급이 Q3/Q4 이거나, 소리가 가깝게 맞은 음절이 하나도 없을 때)

의존: pronounce_guide, syllable_match, match_phrasing

사용 예:
    from conversion_reason import build_reason
    data = build_reason(english_name, translit, korean_given, quality)
    # data['blocks']   → 문단 텍스트 (간단히 쓸 때)
    # data['translit'] / data['korean'] → 음절별 발음 (프론트에서 칩 렌더링)
    # data['matches']  → 매칭 rows (src/tgt/rom/level/phrase/style) → 색 구분 렌더링
    # data['note']     → 가깝게 못 맞췄을 때의 안내문 (없으면 None)
"""
from pronounce_guide import (romanize, romanize_hyphen, romanize_syllable,
                             syllable_pronunciation, name_pronunciation)
from syllable_match import match_syllables
from match_phrasing import match_phrase, LEVEL_STYLE


# ------------------------------------------------------------ 안내문 발동 조건
# 원래는 quality(Q3/Q4)만 봤다. 그런데 quality 는 **이름**의 등급이다 —
# 그 한국 이름이 실제로 쓰이는 자연스러운 이름인지, 성별이 맞는지, 얼마나
# 흔한지를 본다. 반면 이 안내문이 하는 말은 **소리**에 대한 것이다
# ("소리를 그대로 옮기면 한국어에서 어색해진다"). 축이 다르다.
#
# 그래서 소리는 거의 안 맞았는데 이름 자체는 멀쩡해서 Q1/Q2 가 나오면
# 안내문이 통째로 빠졌다. 실제 100건에서 58건이 soft/loose 음절을 포함했는데
# 안내문이 나간 것은 1건뿐이었고, 그 1건은 오히려 음절이 다 잘 맞은 건이었다.
#     패티 → 혜린  (패→혜 0.44, 티→린 0.55)   ← 안내문 없음
#
# 그래서 소리 쪽 조건을 따로 둔다. 다만 '약한 음절이 하나라도 있으면'으로
# 잡으면 100건 중 58건에 붙어서 안내문이 아니라 상투구가 된다. 한 음절이
# 정확히 맞았다면(하린: 1.0 / 0.55) 그건 좋은 변환이고 사과할 일이 아니다.
# 조건은 **가깝게 맞은 음절이 하나도 없고, 전체 평균도 낮을 때**로 잡는다.
# 이 조합이 100건 중 22건 — 패티→혜린 을 포함한다.
NOTE_STRONG_SIM = 0.87   # match_phrasing 의 strong 경계와 같은 값
NOTE_MEAN_SIM = 0.68     # 실측 100건으로 보정


def _syllable_dicts(name):
    """이름 → [{'char','rom','hint'}] (프론트 칩 렌더링용)"""
    return [{'char': ch, 'rom': rom, 'hint': hint}
            for ch, rom, hint in name_pronunciation(name)]


def build_reason(english_name, translit, korean_given, quality,
                 first_syllable_score=None, two_syllable_score=None, vowel_flow_score=None):
    """
    반환: dict {
      'english_name', 'quality', 'note',
      'translit': {'hangul','romanized','syllables':[...]},
      'korean':   {'hangul','romanized','syllables':[...]},
      'matches':  [{'src','src_rom','tgt','tgt_rom','sim','level','phrase','style'}],
      'blocks':   [문단 텍스트 리스트],  # 텍스트만 필요할 때
    }
    """
    tr_rom = romanize_hyphen(translit)
    kr_rom = romanize_hyphen(korean_given)

    # --- 음절별 발음 데이터 ---
    tr_syls = _syllable_dicts(translit)
    kr_syls = _syllable_dicts(korean_given)

    # --- 매칭 계산 + 수준별 표현/색 ---
    raw = match_syllables(translit, korean_given)
    matches = []
    for m in raw:
        if not m['src']:
            continue
        level, phrase = match_phrase(m['src'], m['tgt'], m['sim'])
        matches.append({
            'src': m['src'], 'src_rom': romanize_syllable(m['src']),
            'tgt': m['tgt'], 'tgt_rom': romanize_syllable(m['tgt']),
            'sim': m['sim'], 'level': level, 'phrase': phrase,
            'style': LEVEL_STYLE[level],
        })

    # --- Block 1: 음차 + 발음 ---
    b1_syls = "; ".join(f"{s['char']} ({s['rom']}) — {s['hint']}" for s in tr_syls)
    block1 = (f'When "{english_name}" is written in Korean letters (Hangul), '
              f'it becomes {translit} ({tr_rom}). '
              f'Here\'s how each syllable sounds: {b1_syls}.')

    # --- Block 2: 한국 이름 + 발음 ---
    b2_syls = "; ".join(f"{s['char']} ({s['rom']}) — {s['hint']}" for s in kr_syls)
    block2 = (f'Your Korean name is {korean_given} ({kr_rom}). '
              f'Pronounce it like this: {b2_syls}.')

    # --- Block 3: 매칭 설명 (distinctive 음절 명시) ---
    distinct = ", ".join(f"{m['src']} ({m['src_rom']})" for m in matches)
    if matches:
        lines = [f"the {m['src']} ({m['src_rom']}) sound {m['phrase']} "
                 f"{m['tgt']} ({m['tgt_rom']})" for m in matches]
        joined = (lines[0] if len(lines) == 1
                  else ", ".join(lines[:-1]) + ", and " + lines[-1])
        block3 = (f'We carried the most distinctive syllables of "{english_name}" '
                  f'({distinct}) into a name that reads naturally in Korean: '
                  f'{joined[0].upper() + joined[1:]}.')
    else:
        block3 = (f'We kept the overall feel of "{english_name}" while choosing '
                  f'syllables that flow naturally as a Korean name.')

    # --- Block 4: 가깝게 못 맞춘 경우의 안내 ---
    sims = [m['sim'] for m in matches]
    weak_sound = (not sims) or (max(sims) < NOTE_STRONG_SIM
                                and sum(sims) / len(sims) < NOTE_MEAN_SIM)
    note_reason = ('quality' if quality in ('Q3', 'Q4')
                   else 'sound' if weak_sound else None)
    note = None
    if note_reason:
        note = (f'Some English names don\'t have a close Korean equivalent, and "{english_name}" '
                f'is one of them — a direct sound-for-sound match would feel awkward or unnatural in Korean. '
                f'So after matching what we could, we chose {korean_given} ({kr_rom}): a real, '
                f'natural-sounding Korean name that stays as close as possible to your original name\'s sound and feel.')

    blocks = [block1, block2, block3]
    if note:
        blocks.append(note)

    return {
        'english_name': english_name,
        'quality': quality,
        'note': note,
        'note_reason': note_reason,   # 'quality' | 'sound' | None (점검용)
        'translit': {'hangul': translit, 'romanized': tr_rom, 'syllables': tr_syls},
        'korean':   {'hangul': korean_given, 'romanized': kr_rom, 'syllables': kr_syls},
        'matches': matches,
        'blocks': blocks,
    }


def build_surname(info_en, surname_hangul, surname_rom,
                  given_hangul=None, given_rom=None):
    """
    성씨 유래 설명 + '한국 이름은 성씨가 앞에 온다'는 안내를 합쳐 반환.
    given_hangul/given_rom이 주어지면 실제 풀네임 예시로 개인화, 없으면 범용 문장.
    (surname_reason.build_surname_note 래퍼)
    """
    from surname_reason import build_surname_note
    return build_surname_note(info_en, surname_hangul, surname_rom,
                              given_hangul, given_rom)


if __name__ == '__main__':
    import sys, io, contextlib, json
    sys.path.insert(0, '/home/claude')
    import engine as eng
    e = eng.KoreanNameEngine('merged_meaningful.xlsx', None)
    def cv(fk, lk, sex):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return e.convert(fk, lk, sex)
    n2t = json.load(open('dict_name_to_translit.json'))

    for eng_name, sex in [('Sophia', 'female'), ('Nathan', 'male'), ('David', 'male'), ('Elizabeth', 'female')]:
        sk = '남' if sex == 'male' else '여'
        tr = n2t[sex][eng_name.lower()]
        r = cv(tr, '스미스', sk)
        out = build_reason(eng_name, tr, r['first_1'], r['given_quality'])
        print(f"\n{'='*72}\n{eng_name} → {tr} → {out['korean']['hangul']} [{out['quality']}]\n{'='*72}")
        for i, b in enumerate(out['blocks'], 1):
            print(f"\n[{i}] {b}")
        print(f"\n  matches (색): " + " | ".join(f"{m['src']}→{m['tgt']} {m['level']}" for m in out['matches']))

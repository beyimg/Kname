# -*- coding: utf-8 -*-
import re
"""
성씨 카드 뒷면 · 영상 대본용 성씨 데이터 (2026-10-12 개편).

민우님 규칙: ① 한자 뜻을 알려준다 ② 같은 성을 쓰는 유명인을 소개한다 ③ 1~3위(김·이·박)만 몇 번째로 많은지 말한다.
본관·역사 문장은 쓰지 않는다. 원본(dict_translit_to_result_full.json, surname_info_extra.py)은 그대로 두고 표시할 때 바꾼다.

SURNAME_MEANING_EN : 성씨 → (한자, 영어 뜻).  한자 사전(hanja_dict.xlsx)의 뜻 + 사전에 없는 37자는 손으로.
SURNAME_CELEB_EN   : 성씨 → (영어 이름, 카드용 설명, 영상에서 읽을 앞말, 한글 이름). 카드엔 '정해인 (Jung Hae-in)', 영상에선 한글을 한국어로 읽는다(2026-10-13).  본명(법적 이름)의 첫 음절이 그 성씨인 사람만
                     (영어 위키백과 등에서 확인). 예명이 본명과 다르면 본명을 괄호에. 확인 못 한 성씨는 비워 둔다.
TOP3               : 1~3위 문장 (카드용, 영상용)
검증 내역·제외 사유: 프로젝트 문서 claude/SURNAME_NOTES.md
"""

SURNAME_MEANING_EN = {
    '김': ('金', 'gold'),
    '고': ('高', 'high, tall'),
    '권': ('權', 'authority'),
    '남': ('南', 'south'),
    '우': ('禹', 'Yu, the legendary flood-taming emperor'),
    '이': ('李', 'a plum tree'),
    '정': ('鄭', 'solemn, earnest'),
    '조': ('趙', 'the name of an ancient kingdom (Zhao)'),
    '진': ('陳', 'to lay out, to tell'),
    '안': ('安', 'peace'),
    '엄': ('嚴', 'dignified, strict'),
    '오': ('吳', 'the name of an ancient kingdom (Wu)'),
    '문': ('文', 'writing, literature'),
    '박': ('朴', 'simple, unadorned'),
    '서': ('徐', 'slow and steady'),
    '송': ('宋', 'the name of an ancient kingdom (Song)'),
    '신': ('申', 'to state, to stretch'),
    '양': ('梁', 'a beam, a bridge'),
    '원': ('元', 'origin, first'),
    '윤': ('尹', 'to govern'),
    '차': ('車', 'a chariot'),
    '채': ('蔡', 'a sacred tortoise'),
    '천': ('千', 'a thousand'),
    '최': ('崔', 'lofty, high'),
    '배': ('裵', 'a long flowing robe'),
    '한': ('韓', 'Korea — the same character as in Hanguk'),
    '허': ('許', 'to permit'),
    '홍': ('洪', 'vast waters'),
    '황': ('黃', 'yellow'),
    '강': ('姜', 'ginger (originally a river name)'),
    '장': ('張', 'to stretch, to open wide'),
    '임': ('林', 'forest'),
    '유': ('柳', 'willow tree'),
    '전': ('全', 'complete, whole'),
    '손': ('孫', 'descendant'),
    '백': ('白', 'white, pure'),
    '노': ('盧', 'black'),
    '심': ('沈', 'to sink; deep'),
    '하': ('河', 'river'),
    '주': ('朱', 'vermilion red'),
    '구': ('具', 'to have, to prepare'),
    '곽': ('郭', 'outer city wall'),
    '성': ('成', 'to accomplish'),
    '민': ('閔', 'compassion'),
    '류': ('柳', 'willow tree'),
    '나': ('羅', 'silk gauze'),
    '지': ('池', 'pond'),
    '변': ('卞', 'law, order'),
    '방': ('方', 'direction, square'),
    '공': ('孔', 'an opening — the surname of Confucius'),
    '현': ('玄', 'deep, mysterious black'),
    '여': ('呂', 'a musical pitch'),
    '함': ('咸', 'all, entirely'),
    '염': ('廉', 'integrity'),
    '석': ('石', 'stone'),
    '추': ('秋', 'autumn'),
    '도': ('都', 'capital city'),
    '소': ('蘇', 'to revive'),
    '설': ('薛', 'wormwood (a plant)'),
    '선': ('宣', 'to proclaim'),
    '마': ('馬', 'horse'),
    '길': ('吉', 'auspicious, lucky'),
    '연': ('延', 'to extend, prolong'),
    '위': ('魏', 'towering, grand'),
    '표': ('表', 'to express'),
    '명': ('明', 'bright'),
    '기': ('奇', 'rare, remarkable'),
    '반': ('潘', 'swirling water'),
    '라': ('羅', 'silk, to unfurl'),
    '왕': ('王', 'king'),
    '금': ('琴', 'a zither'),
    '육': ('陸', 'land'),
    '인': ('印', 'a seal, an imprint'),
    '맹': ('孟', 'the eldest; the philosopher Mencius’ surname'),
    '제': ('諸', 'all, many'),
    '모': ('牟', 'to seek'),
    '남궁': ('南宮', 'south (南) and palace (宮)'),
    '탁': ('卓', 'outstanding'),
    '국': ('鞠', 'to rear, to nourish'),
    '어': ('魚', 'fish'),
    '경': ('慶', 'celebration, blessing'),
    '은': ('殷', 'abundant, flourishing'),
    '편': ('片', 'a piece'),
    '용': ('龍', 'dragon'),
    '예': ('芮', 'small, tender'),
    '봉': ('奉', 'to serve, to offer'),
    '사': ('史', 'history'),
    '부': ('夫', 'a man'),
    '황보': ('皇甫', 'imperial (皇) and great (甫)'),
    '가': ('賈', 'merchant'),
    '복': ('卜', 'divination'),
    '태': ('太', 'great, supreme'),
    '목': ('睦', 'harmonious'),
    '형': ('邢', 'the name of an ancient state (Xing)'),
    '계': ('桂', 'cinnamon tree'),
    '피': ('皮', 'skin, hide'),
    '두': ('杜', 'a pear tree'),
    '감': ('甘', 'sweet'),
    '동': ('董', 'to oversee'),
    '호': ('扈', 'to protect'),
    '빈': ('賓', 'honored guest'),
    '제갈': ('諸葛', 'many (諸) and kudzu vine (葛)'),
    '음': ('陰', 'shade, the yin principle'),
    '온': ('溫', 'warm, gentle'),
    '사공': ('司空', 'an ancient title, Minister of Works'),
    '범': ('范', 'a model, a pattern'),
    '선우': ('鮮于', 'fresh (鮮) and at (于), an old compound surname'),
    '승': ('承', 'to inherit, carry on'),
    '좌': ('左', 'left'),
    '팽': ('彭', 'the beat of a drum'),
    '간': ('簡', 'simple, concise'),
    '서문': ('西門', 'west gate'),
    '견': ('甄', 'to discern'),
    '단': ('段', 'a section, a stage'),
    '당': ('唐', 'the Tang dynasty'),
    '창': ('昌', 'flourishing'),
    '옹': ('邕', 'harmonious'),
    '빙': ('氷', 'ice'),
    '종': ('鍾', 'bell'),
    '대': ('大', 'great'),
    '풍': ('馮', 'to gallop'),
    '궁': ('弓', 'a bow'),
    '평': ('平', 'level, peaceful'),
    '독고': ('獨孤', 'alone (獨) and solitary (孤)'),
    '판': ('判', 'to judge'),
    '초': ('楚', 'the ancient state of Chu'),
    '궉': ('鴌', 'a bird (a rare character)'),
    '필': ('弼', 'to assist'),
    '근': ('斤', 'an axe; a unit of weight'),
    '점': ('占', 'to divine'),
    '탄': ('彈', 'to pluck a string'),
    '만': ('萬', 'ten thousand'),
    '운': ('雲', 'cloud'),
}

SURNAME_CELEB_EN = {
    '김': ('V (Kim Tae-hyung)', 'member of BTS', "BTS's V, Kim Tae-hyung", '뷔'),
    '이': ('IU (Lee Ji-eun)', 'singer and actress in “Hotel del Luna”', 'singer IU, Lee Ji-eun', '아이유'),
    '박': ('Jimin (Park Ji-min)', 'member of BTS', "BTS's Jimin, Park Ji-min", '박지민'),
    '최': ('Soobin (Choi Soo-bin)', 'leader of TXT', "TXT's Soobin", '최수빈'),
    '정': ('Jung Hae-in', 'actor in “D.P.” and “Something in the Rain”', 'actor Jung Hae-in', '정해인'),
    '조': ('Jo In-sung', 'actor in “Escape from Mogadishu” and “Moving”', 'actor Jo In-sung', '조인성'),
    '윤': ('Jeonghan (Yoon Jeong-han)', 'member of SEVENTEEN', "SEVENTEEN's Jeonghan", '윤정한'),
    '강': ('Kang Daniel', 'singer, former Wanna One', 'singer Kang Daniel', '강다니엘'),
    '장': ('Jang Won-young', 'member of IVE', "IVE's Jang Wonyoung", '장원영'),
    '임': ('YoonA (Im Yoon-ah)', "member of Girls' Generation", "Girls' Generation's YoonA", '임윤아'),
    '한': ('Han (Han Ji-sung)', 'member of Stray Kids', "Stray Kids' Han", '한지성'),
    '오': ('Sehun (Oh Se-hun)', 'member of EXO', "EXO's Sehun", '오세훈'),
    '서': ('Seohyun (Seo Ju-hyun)', "member of Girls' Generation", "Girls' Generation's Seohyun", '서현'),
    '유': ('Jeongyeon (Yoo Jeong-yeon)', 'member of TWICE', "TWICE's Jeongyeon", '유정연'),
    '신': ('Yuna (Shin Yu-na)', 'member of ITZY', "ITZY's Yuna", '신유나'),
    '권': ('G-Dragon (Kwon Ji-yong)', 'leader of BIGBANG', "BIGBANG's G-Dragon", '지드래곤'),
    '황': ('Hyunjin (Hwang Hyun-jin)', 'member of Stray Kids', "Stray Kids' Hyunjin", '황현진'),
    '안': ('An Yu-jin', 'leader of IVE', "IVE's An Yujin", '안유진'),
    '송': ('Song Hye-kyo', 'actress in “The Glory”', 'actress Song Hye-kyo', '송혜교'),
    '홍': ('Eunchae (Hong Eun-chae)', 'member of LE SSERAFIM', "LE SSERAFIM's Eunchae", '홍은채'),
    '전': ('Jung Kook (Jeon Jung-kook)', 'member of BTS', "BTS's Jungkook", '전정국'),
    '고': ('Go Youn-jung', 'actress in “Moving”', 'actress Go Youn-jung', '고윤정'),
    '문': ('Moon Ga-young', 'actress in “True Beauty”', 'actress Moon Ga-young', '문가영'),
    '양': ('I.N (Yang Jeong-in)', 'member of Stray Kids', "Stray Kids' I.N", '양정인'),
    '배': ('Suzy (Bae Su-ji)', 'singer and actress in “Start-Up”', 'singer and actress Suzy', '배수지'),
    '손': ('Son Heung-min', 'footballer, South Korea captain', 'footballer Son Heung-min', '손흥민'),
    '백': ('Baek Ye-rin', 'singer-songwriter', 'singer Baek Yerin', '백예린'),
    '허': ('Huh Yunjin', 'member of LE SSERAFIM', "LE SSERAFIM's Huh Yunjin", '허윤진'),
    '노': ('Roh Jeong-eui', 'actress in “Hierarchy”', 'actress Roh Jeong-eui', '노정의'),
    '남': ('Nam Joo-hyuk', 'actor in “Twenty-Five Twenty-One”', 'actor Nam Joo-hyuk', '남주혁'),
    '심': ('Changmin (Shim Chang-min)', 'member of TVXQ', "TVXQ's Changmin", '심창민'),
    '하': ('Ha Sung-woon', 'singer, former Wanna One', 'singer Ha Sung-woon', '하성운'),
    '주': ('Joo Jong-hyuk', 'actor in “Extraordinary Attorney Woo”', 'actor Joo Jong-hyuk', '주종혁'),
    '구': ('Koo Hye-sun', 'actress in “Boys Over Flowers”', 'actress Koo Hye-sun', '구혜선'),
    '곽': ('Kwak Dong-yeon', 'actor in “Vincenzo”', 'actor Kwak Dong-yeon', '곽동연'),
    '성': ('Sung Si-kyung', 'ballad singer', 'singer Sung Si-kyung', '성시경'),
    '차': ('Cha Seung-won', 'actor in “City Hall”', 'actor Cha Seung-won', '차승원'),
    '우': ('Woo Do-hwan', 'actor in “Bloodhounds”', 'actor Woo Do-hwan', '우도환'),
    '민': ('Suga (Min Yoon-gi)', 'member of BTS', "BTS's Suga, Min Yoon-gi", '슈가'),
    '류': ('Ryu Seung-ryong', 'actor in “Extreme Job”', 'actor Ryu Seung-ryong', '류승룡'),
    '나': ('Na Hong-jin', 'director of “The Wailing”', 'director Na Hong-jin', '나홍진'),
    '변': ('Byeon Woo-seok', 'actor in “Lovely Runner”', 'actor Byeon Woo-seok', '변우석'),
    '설': ('Sol Kyung-gu', 'actor in “Oasis”', 'actor Sol Kyung-gu', '설경구'),
    '방': ('Bang Chan', 'leader of Stray Kids', "Stray Kids' Bang Chan", '방찬'),
    '엄': ('Uhm Jung-hwa', 'singer and actress', 'singer and actress Uhm Jung-hwa', '엄정화'),
    '위': ('Wi Ha-joon', 'actor in “Squid Game”', 'actor Wi Ha-joon', '위하준'),
    '여': ('Yeo Jin-goo', 'actor in “Hotel del Luna”', 'actor Yeo Jin-goo', '여진구'),
    '천': ('Chun Woo-hee', 'actress in “The Wailing”', 'actress Chun Woo-hee', '천우희'),
    '함': ('Hahm Eun-jung', 'member of T-ara', "T-ara's Eunjung", '함은정'),
    '염': ('Yeom Hye-ran', 'actress in “The Glory”', 'actress Yeom Hye-ran', '염혜란'),
    '도': ('D.O. (Doh Kyung-soo)', 'member of EXO', "EXO's D.O.", '디오'),
    '소': ('So Ji-sub', 'actor in “Master’s Sun”', 'actor So Ji-sub', '소지섭'),
    '연': ('Yeon Sang-ho', 'director of “Train to Busan”', 'director Yeon Sang-ho', '연상호'),
    '표': ('Pyo Ye-jin', 'actress in “Taxi Driver”', 'actress Pyo Ye-jin', '표예진'),
    '명': ('Jaehyun (Myung Jae-hyun)', 'member of BOYNEXTDOOR', "BOYNEXTDOOR's Jaehyun", '명재현'),
    '기': ('Ki Bo-bae', 'three-time Olympic archery champion', 'Olympic archer Ki Bo-bae', '기보배'),
    '반': ('Ban Ki-moon', 'former UN Secretary-General', 'former UN chief Ban Ki-moon', '반기문'),
    '라': ('Ra Mi-ran', 'actress in “Reply 1988”', 'actress Ra Mi-ran', '라미란'),
    '왕': ('Wang Seok-hyeon', 'actor in “Scandal Makers”', 'actor Wang Seok-hyeon', '왕석현'),
    '금': ('Keum Sae-rok', 'actress in “Youth of May”', 'actress Keum Sae-rok', '금새록'),
    '육': ('Yook Sung-jae', 'member of BTOB', "BTOB's Sungjae", '육성재'),
    '인': ('In Gyo-jin', 'actor in “Hometown Cha-Cha-Cha”', 'actor In Gyo-jin', '인교진'),
    '맹': ('Maeng Se-chang', 'actor in “Ode to My Father”', 'actor Maeng Se-chang', '맹세창'),
    '모': ('Mo Tae-bum', 'Olympic speed-skating champion', 'Olympic champion Mo Tae-bum', '모태범'),
    '남궁': ('Namkoong Min', 'actor in “Stove League”', 'actor Namkoong Min', '남궁민'),
    '경': ('Kyung Soo-jin', 'actress in “Weightlifting Fairy Kim Bok-joo”', 'actress Kyung Soo-jin', '경수진'),
    '은': ('Eun Ji-won', 'leader of Sechs Kies', "Sechs Kies' Eun Ji-won", '은지원'),
    '봉': ('Bong Joon-ho', 'director of “Parasite”', 'director Bong Joon-ho', '봉준호'),
    '부': ('Seungkwan (Boo Seung-kwan)', 'member of SEVENTEEN', "SEVENTEEN's Seungkwan", '부승관'),
    '황보': ('Hwangbo Ra', 'actress in “Vagabond”', 'actress Hwangbo Ra', '황보라'),
    '감': ('Kam Woo-sung', 'actor in “The King and the Clown”', 'actor Kam Woo-sung', '감우성'),
    '동': ('Taeyang (Dong Young-bae)', 'member of BIGBANG', "BIGBANG's Taeyang", '태양'),
    '제갈': ('Jaegal Sung-yeol', 'Olympic speed skater and commentator', 'speed skater Jaegal Sung-yeol', '제갈성렬'),
    '음': ('Eum Moon-suk', 'actor in “The Fiery Priest”', 'actor Eum Moon-suk', '음문석'),
    '선우': ('Sunwoo Jung-a', 'singer-songwriter', 'singer Sunwoo Jung-a', '선우정아'),
    '견': ('Kyeon Mi-ri', 'actress in “Jewel in the Palace”', 'actress Kyeon Mi-ri', '견미리'),
    '진': ('Jin Goo', 'actor in “Descendants of the Sun”', 'actor Jin Goo', '진구'),
    '원': ('Won Jin-ah', 'actress in “Hellbound”', 'actress Won Jin-ah', '원진아'),
    '추': ('Shin-Soo Choo', 'former MLB All-Star', 'baseball star Shin-Soo Choo', '추신수'),
    '공': ('Gong Yoo (Gong Ji-cheol)', 'actor in “Train to Busan” and “Squid Game”', 'actor Gong Yoo', '공유'),
    '옹': ('Ong Seong-wu', 'actor and former Wanna One member', 'actor Ong Seong-wu', '옹성우'),
}

TOP4 = {                                                               # 1~4위만 순위를 말한다(민우님 2026-10-13). (카드용, 영상용)
    '김': ("It is Korea's most common surname, held by about 21.5% of the population — roughly one in five Koreans.", "And it’s Korea’s most common family name, about one in five people."),
    '이': ("It is Korea's second most common surname, at about 14.7% of the population.", "And it’s Korea’s second most common family name."),
    '박': ("It is Korea's third most common surname, at about 8.4%.", "And it’s Korea’s third most common family name."),
    '최': ("It is Korea's fourth most common surname, at about 4.7%.", "And it’s Korea’s fourth most common family name."),
}
TOP3 = TOP4                                                            # (옛 이름)

COMPOUND = {'제갈', '독고', '황보', '남궁', '선우', '사공', '서문'}     # 두 글자 성씨: 영상에선 글자 뜻 대신 '두 글자 성'이라고만


def meaning_of(surname):
    m = SURNAME_MEANING_EN.get(surname)
    return m[1] if m else ''


def celeb_display(surname):
    """카드 표기: '정해인 (Jung Hae-in)', 예명은 '뷔 (V, Kim Tae-hyung)'."""
    c = SURNAME_CELEB_EN.get(surname)
    if not c:
        return ''
    en = c[0].replace(' (', ', ').rstrip(')')
    return f'{c[3]} ({en})' if len(c) > 3 and c[3] else c[0]


def celeb_card_sentence(surname):
    """카드용: 'Also the surname of 정해인 (Jung Hae-in), <설명>.'  없으면 ''."""
    c = SURNAME_CELEB_EN.get(surname)
    if not c:
        return ''
    role = c[1]
    role = role[:-1] + '.”' if role.endswith('”') else role + '.'   # 마침표는 닫는 따옴표 안쪽
    return f'Also the surname of {celeb_display(surname)}, {role}'


_LEAD = re.compile(r"^(.*?(?:'s|singer and actress|singer-songwriter|actor|actress|singer|director|footballer|champion|archer|star|chief|skater))\b")


def celeb_spoken(surname):
    """영상용 (앞말, 한글이름): ('actor', '정해인') / ("BTS's", '뷔'). 한글은 한국어 발음으로 읽는다."""
    c = SURNAME_CELEB_EN.get(surname)
    if not c:
        return None
    lead, hang = c[2], (c[3] if len(c) > 3 else '')
    if not hang:
        return (lead, '')
    m = _LEAD.match(lead)
    return ((m.group(1) if m else lead.split(' ')[0]), hang)


def card_text(surname, rom, hanja):
    """카드 뒷면 문장(2026-10-13 순서: 뜻 → 유명인 → 순위(4위까지)).
    예) 'Moon (文): The character means “writing, literature.” Also the surname of Moon Ga-young, actress in “True Beauty.”'
        'Kim (金): The character means “gold.” Also the surname of V (Kim Tae-hyung), member of BTS. It is Korea's most common surname, …'"""
    head = f'{rom} ({hanja})' if hanja else rom
    parts = []
    m = meaning_of(surname)
    if m:
        subj = 'The characters mean' if surname in COMPOUND else 'The character means'
        parts.append(f'{subj} “{m}.”' if not m.endswith(')') else f'{subj} {m}.')
    c = celeb_card_sentence(surname)
    if c:
        parts.append(c)
    if surname in TOP4:
        parts.append(TOP4[surname][0])
    return f'{head}: ' + ' '.join(parts) if parts else f'{head}.'


SPOKEN_CELEB = False                                                   # 영상 나레이션에서 유명인은 뺀다(민우님 2026-10-17: 뜻을 기본으로, 1~4위만 순위). 카드(card_text)는 그대로


def spoken_line(surname, celeb=None):
    """영상용: 'The last name 문' 뒤에 붙는 조각 목록. 뜻 → (유명인, 기본 꺼짐) → 순위(4위까지). 뜻은 첫 번째 것만 읽는다('writing, literature' → 'writing')."""
    if celeb is None:
        celeb = SPOKEN_CELEB
    m, c = meaning_of(surname), (celeb_spoken(surname) if celeb else None)
    m_sp = m.split(' — ')[0].split(' (')[0].split(',')[0].strip('“” ') if m else ''
    out = []
    if surname in COMPOUND:
        out.append('is a rare two-syllable family name.')
    elif m_sp:
        out.append(f'means {m_sp}.')
    if c and c[1]:
        out.append(f'It’s also the family name of {c[0]}')
        out.append({'ko': c[1], 'end': True})                         # 한글 이름: record.py 가 ko() 로 바꿔 한국어로 읽힌다. 문장 끝
    elif c:
        out.append(f'It’s also the family name of {c[0]}.')
    if surname in TOP4:
        out.append(TOP4[surname][1])
    return out

# -*- coding: utf-8 -*-
"""
성씨 카드 뒷면 · 영상 대본용 성씨 문구 (2026-10-07).

원칙
  1. 순위는 1~3위(김·이·박)만 말한다. 4위 이하의 순위·비율은 시청자에게 의미가 없다.
  2. 그 성씨의 가장 특징적인 사실(역사 인물·건국 전설·지역)이 있으면 그것만 짧게.
  3. 두 번째 문장 정보가 약한 성씨는 '실제 성이 같은' 유명인을 붙인다.
     - 인물은 영어 위키백과 등에서 본명(법적 이름)의 첫 음절이 그 성씨와 일치하는 것을 확인한 사람만 넣었다.
     - 예명이 본명과 다른 경우(전정국=전, 변백현=변, 김석진=김 …)는 본명 기준으로 판단했다.
     - 확인하지 못한 성씨는 비워 둔다 → 기존 문장(순위 제외)을 쓴다.

데이터
  SURNAME_CELEB_EN : 성씨 → (카드용 이름, 카드용 설명, 영상에서 읽을 말)
  SURNAME_SPOKEN_EN: 성씨 → 영상에서 읽는 한 문장(성씨 뒤에 붙는 서술부). 기존 유래 문장을 줄인 것이며 새 사실은 넣지 않았다.
  TOP3_SPOKEN_EN   : 1~3위 성씨의 영상용 문장
"""

SURNAME_CELEB_EN = {
    '정': ('Jung Hae-in', 'actor in “D.P.” and “Something in the Rain”', 'actor Jung Hae-in'),
    '조': ('Jo In-sung', 'actor in “Escape from Mogadishu” and “Moving”', 'actor Jo In-sung'),
    '장': ('Jang Won-young', 'member of IVE', "IVE's Jang Wonyoung"),
    '임': ('YoonA (Im Yoon-ah)', "member of Girls' Generation", "Girls' Generation's YoonA"),
    '오': ('Sehun (Oh Se-hun)', 'member of EXO', "EXO's Sehun"),
    '서': ('Seohyun (Seo Ju-hyun)', "member of Girls' Generation", "Girls' Generation's Seohyun"),
    '홍': ('Eunchae (Hong Eun-chae)', 'member of LE SSERAFIM', "LE SSERAFIM's Eunchae"),
    '전': ('Jung Kook (Jeon Jung-kook)', 'member of BTS', "BTS's Jungkook"),
    '문': ('Moon Ga-young', 'actress in “True Beauty”', 'actress Moon Ga-young'),
    '백': ('Baek Ye-rin', 'singer-songwriter', 'singer Baek Yerin'),
    '구': ('Koo Hye-sun', 'actress in “Boys Over Flowers”', 'actress Koo Hye-sun'),
    '차': ('Cha Seung-won', 'actor in “City Hall”', 'actor Cha Seung-won'),
    '류': ('Ryu Seung-ryong', 'actor in “Extreme Job”', 'actor Ryu Seung-ryong'),
    '나': ('Na Hong-jin', 'director of “The Wailing”', 'director Na Hong-jin'),
    '변': ('Byeon Woo-seok', 'actor in “Lovely Runner”', 'actor Byeon Woo-seok'),
    '방': ('Bang Chan', 'leader of Stray Kids', "Stray Kids' Bang Chan"),
    '엄': ('Uhm Jung-hwa', 'singer and actress', 'singer and actress Uhm Jung-hwa'),
    '여': ('Yeo Jin-goo', 'actor in “Hotel del Luna”', 'actor Yeo Jin-goo'),
    '천': ('Chun Woo-hee', 'actress in “The Wailing”', 'actress Chun Woo-hee'),
    '함': ('Hahm Eun-jung', 'member of T-ara', "T-ara's Eunjung"),
    '염': ('Yeom Hye-ran', 'actress in “The Glory”', 'actress Yeom Hye-ran'),
    '도': ('D.O. (Doh Kyung-soo)', 'member of EXO', "EXO's D.O."),
    '소': ('So Ji-sub', 'actor in “Master’s Sun”', 'actor So Ji-sub'),
    '연': ('Yeon Sang-ho', 'director of “Train to Busan”', 'director Yeon Sang-ho'),
    '표': ('Pyo Ye-jin', 'actress in “Taxi Driver”', 'actress Pyo Ye-jin'),
    '반': ('Ban Ki-moon', 'former UN Secretary-General', 'former UN chief Ban Ki-moon'),
    '라': ('Ra Mi-ran', 'actress in “Reply 1988”', 'actress Ra Mi-ran'),
    '금': ('Keum Sae-rok', 'actress in “Youth of May”', 'actress Keum Sae-rok'),
    '육': ('Yook Sung-jae', 'member of BTOB', "BTOB's Sungjae"),
    '인': ('In Gyo-jin', 'actor in “Hometown Cha-Cha-Cha”', 'actor In Gyo-jin'),
    '모': ('Mo Tae-bum', 'Olympic speed-skating champion', 'Olympic champion Mo Tae-bum'),
    '남궁': ('Namkoong Min', 'actor in “Stove League”', 'actor Namkoong Min'),
    '경': ('Kyung Soo-jin', 'actress in “Weightlifting Fairy Kim Bok-joo”', 'actress Kyung Soo-jin'),
    '봉': ('Bong Joon-ho', 'director of “Parasite”', 'director Bong Joon-ho'),
    '황보': ('Hwangbo Ra', 'actress in “Vagabond”', 'actress Hwangbo Ra'),
    '감': ('Kam Woo-sung', 'actor in “The King and the Clown”', 'actor Kam Woo-sung'),
    '동': ('Taeyang (Dong Young-bae)', 'member of BIGBANG', "BIGBANG's Taeyang"),
    '음': ('Eum Moon-suk', 'actor in “The Fiery Priest”', 'actor Eum Moon-suk'),
    '옹': ('Ong Seong-wu', 'actor and former Wanna One member', 'actor Ong Seong-wu'),
}

# 영상에서 읽는 한 문장(주어 '그 성씨' 뒤에 이어 붙인다). 카드 유래 문장을 줄인 것.
SURNAME_SPOKEN_EN = {
    '최': "traces to Choi Chi-won, the scholar revered as the father of Korean literature.",
    '윤': "belongs to the Papyeong Yoon clan, which produced several Joseon queens.",
    '강': "belongs to the Jinju Kang clan, which produced the general Kang Gam-chan.",
    '한': "shares its character with the name of Korea itself — Hanguk.",
    '유': "is the family of Yu Seong-ryong, the prime minister who guided Korea through the Imjin War.",
    '신': "descends from Shin Sung-gyeom, the general who helped found the Goryeo dynasty.",
    '권': "belongs to the Andong Kwon clan, with a genealogy book dating back to 1476.",
    '황': "is the family of Hwang Hui, Joseon's most revered prime minister.",
    '안': "is the family of independence activist Ahn Jung-geun.",
    '송': "is the family of Song Si-yeol, the eminent Joseon scholar.",
    '고': "is a Jeju Island name — one of three founding families, the legend says.",
    '양': "is one of Jeju Island's three legendary founding families.",
    '부': "is one of Jeju Island's three legendary founding families.",
    '배': "descends from one of the six village chiefs said to have founded Silla.",
    '손': "is known worldwide through the marathoner Son Kee-chung.",
    '허': "is tied to the legend of Queen Heo Hwang-ok, said to have sailed from India to marry a Korean king.",
    '노': "has produced statesmen, including two modern presidents.",
    '남': "traces to a Tang Chinese envoy who settled in Silla.",
    '심': "belongs to the Cheongsong Sim clan, which produced several Joseon queens.",
    '하': "is the family of Ha Ryun, a key architect of the early Joseon state.",
    '주': "traces back to Zhu Xi, the great Neo-Confucian philosopher.",
    '곽': "is the family of Gwak Jae-u, the “Red Coat General.”",
    '성': "is the family of Seong Sam-mun, one of the six martyred ministers.",
    '우': "claims descent from Emperor Yu, the legendary flood-taming founder of China's first dynasty.",
    '민': "belongs to the Yeoheung Min clan, which produced Empress Myeongseong.",
    '설': "is the family of Seol Chong, the Silla scholar who systematized the idu writing method.",
    '마': "traces back to the Mahan confederacy.",
    '위': "traces to an envoy from China's Wei kingdom.",
    '명': "descends from a Ming dynasty prince who settled in Korea.",
    '기': "is the family of Gi Dae-seung, a leading Neo-Confucian philosopher.",
    '왕': "was the royal house of the Goryeo dynasty, which ruled Korea for nearly 500 years.",
    '맹': "traces to Mencius, the great Confucian philosopher.",
    '은': "traces to the ancient Chinese Shang-Yin dynasty.",
    '편': "descends from a Ming general who stayed in Korea after the Imjin War.",
    '태': "traces to the royal house of Balhae.",
    '대': "traces to the royal house of Balhae.",
    '두': "traces to the homeland of the poet Du Fu.",
    '제갈': "shares its origin with the famous strategist Zhuge Liang.",
    '온': "traces back to the Goguryeo kingdom.",
    '선우': "traces to the ancient Gija Joseon.",
    '견': "traces to Gyeon Hwon, founder of Later Baekje.",
    '진': "traces to a Song Chinese official who settled in Goryeo.",
    '원': "descends from a Tang Chinese scholar-official.",
    '추': "traces to a Song dynasty scholar who settled in Korea.",
    '공': "traces its lineage to Confucius, whose Chinese surname was Kong.",
}

# 1~3위만 순위를 말한다.
TOP3_SPOKEN_EN = {
    '김': "is Korea's most common family name — about one in five people.",
    '이': "is Korea's second most common family name — the royal house of the Joseon dynasty.",
    '박': "is Korea's third most common family name.",
}


def celeb_card_sentence(surname):
    """카드용: 'Also the family name of X, <설명>.'  없으면 ''."""
    c = SURNAME_CELEB_EN.get(surname)
    if not c:
        return ''
    who, role, _ = c
    role = role[:-1] + '.”' if role.endswith('”') else role + '.'   # 마침표는 닫는 따옴표 안쪽
    return f'Also the surname of {who}, {role}'


def celeb_spoken(surname):
    c = SURNAME_CELEB_EN.get(surname)
    return f'is the family name of {c[2]}.' if c else ''

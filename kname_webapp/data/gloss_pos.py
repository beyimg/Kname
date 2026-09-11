# -*- coding: utf-8 -*-
"""
한자 뜻(영어) → 품사 표. 카드 앞면 '한 줄 의미'를 조립할 때만 쓴다.

왜 표로 관리하는가
------------------
한 줄 의미는 hanja_dict.xlsx 의 영어뜻을 콤마로 쪼갠 토큰으로 문장을 만든다.
예전에는 형용사 목록(_ADJ_OK)에 없으면 전부 명사로 간주했는데, 그 결과
  "A name of beneficial and talent"      (beneficial 은 형용사)
  "graceful as a model"                  (model 은 비유 대상이 안 된다)
  "true as a poetry"                     (poetry 는 관사가 붙지 않는다)
같은 비문이 계속 나왔다.

뜻 문자열의 어미로 품사를 추론하는 방법은 쓸 수 없다. 오탐이 확실히 생긴다.
  -al  : a capital(명사)      -ant : a descendant(명사)
  -ed  : a fine steed(명사)   -ous : (형용사)  ...
게다가 'great zither' · 'clear water' · 'broad rock' 처럼 형용사+명사
복합어가 많아 규칙으로는 가를 수 없다.

그래서 **추론을 하지 않는다.** 아래 표에 없는 토큰은 '모르는 것'으로 두고,
모르는 토큰만 남는 글자가 있으면 문장을 만들지 않고 라벨형
("Wisdom · Jade radiance")으로 내려간다. 라벨형은 문법이 개입하지 않으므로
비문이 될 수 없다.

품사 코드
---------
  'A'  형용사      "A bright person" / "Someone bright as ..."
  'N'  가산명사    관사가 필요하다            → "as a pearl"
  'M'  불가산명사  관사를 붙이면 비문이 된다  → "as jade"
  'T'  the 를 받는 명사 (유일물)              → "as the moon"
  'X'  추상명사    비유가 성립하지 않는다     → "with wisdom"
  'V'  동사        "who shines"
  'S'  쓸 수 없음  (수사·대명사·부사·전치사, 신체/친족어 등)

'a ~' / 'an ~' / 'the ~' 로 시작하는 뜻은 관사가 이미 붙어 있고, 그 자체가
가산명사임을 증명하므로 표에 넣지 않고 코드에서 'N'으로 처리한다.
'to ~' 로 시작하는 뜻도 같은 이유로 코드에서 'V'로 처리한다.
"""

GLOSS_POS = {
    # ---------------------------------------------------------------- A 형용사
    'gentle': 'A', 'harmonious': 'A', 'pure': 'A', 'crimson': 'A',
    'blue-green': 'A', 'far': 'A', 'feminine': 'A', 'full': 'A',
    'gentle and warm': 'A', 'green': 'A', 'lush': 'A', 'right': 'A',
    'abounding': 'A', 'alike': 'S', 'balanced': 'A',
    'beautifully bright': 'A', 'brave': 'A', 'brimming': 'A',
    'capable': 'A', 'certain': 'A', 'close': 'A', 'complete': 'A',
    'composed': 'A', 'considerate': 'A', 'constant': 'A',
    'contented': 'A', 'courteous': 'A', 'deep and wide': 'A',
    'dense': 'A', 'eldest': 'A', 'eternal': 'A', 'firm': 'A',
    'fitting': 'A', 'flowerlike': 'A', 'flowing': 'A',
    'fresh and neat': 'A', 'frugal': 'A', 'generous-hearted': 'A',
    'healthy': 'A', 'heroic': 'A', 'illustrious': 'A', 'in harmony': 'A',
    'intense': 'A', 'keen of ear': 'A', 'late': 'A', 'leisurely': 'A',
    'lofty and grand': 'A', 'neighborly': 'A', 'new': 'A',
    'peerless': 'A', 'plentiful': 'A', 'prospering': 'A', 'prudent': 'A',
    'purple': 'A', 'quick': 'A', 'refreshing': 'A', 'round': 'A',
    'round and complete': 'A', 'silent': 'A', 'sincere': 'A',
    'special': 'A', 'sudden': 'A', 'sun-bright': 'A', 'surging': 'A',
    'taut': 'A', 'thriving': 'A', 'unaffected': 'A', 'weighty': 'A',
    'wholehearted': 'A',

    # ---------------------------------------------------------------- N 가산명사
    'path': 'N', 'pond': 'N', 'chime of jade': 'N', 'cord': 'N',
    'district': 'N', 'dragon': 'N', 'egret': 'N', 'fruit': 'N',
    'great mountain': 'N', 'high mountain': 'N', 'jade pendant': 'N',
    'lotus': 'N', 'melody': 'N', 'office': 'N', 'river': 'N',
    'root': 'N', 'seal': 'N', 'sprout': 'N', 'stipend': 'N',
    'sunrise': 'N', 'village': 'N', 'waterside': 'N', 'artisan': 'N',
    'bamboo hat': 'N', 'bean': 'N', 'bird': 'N', 'blessing': 'N',
    'blue gem': 'N', 'bodhisattva': 'N', 'boulder': 'N', 'bow': 'N',
    'bride': 'N', 'broad rock': 'N', 'bud': 'N', 'canopy': 'N',
    'cherry tree': 'N', 'citrus tree': 'N', 'corridor': 'N',
    'countenance': 'N', 'day': 'N', 'desk': 'N', 'domestic duck': 'N',
    'dream': 'N', 'field': 'N', 'fine stone': 'N', 'flame': 'N',
    'flower': 'N', 'fragrant herb': 'N', 'frontier': 'N',
    'fulling stone': 'N', 'great hill': 'N', 'great zither': 'N',
    'hare': 'N', 'heart': 'N', 'hermitage': 'N', 'hero': 'N',
    'hillside': 'N', 'jade bead': 'N', 'leader': 'N', 'lord': 'N',
    'lotus seed': 'N', 'maple': 'N', 'melon seed': 'N',
    'morning sunrise': 'N', 'night': 'N', 'orchid': 'N', 'oriole': 'N',
    'otter': 'N', 'ox': 'N', 'palace': 'N', 'paulownia': 'N',
    'peach': 'N', 'peak': 'N', 'pearl': 'N', 'pepper tree': 'N',
    'plantain': 'N', 'pledge': 'N', 'red gem': 'N', 'reed': 'N',
    'refined pattern': 'N', 'rice paddy': 'N', 'ridge': 'N',
    'ring': 'N', 'roof tile': 'N', 'rootstock': 'N',
    'royal edict': 'N', 'rule': 'N', 'sail': 'N', 'sandpiper': 'N',
    'seed': 'N', 'seedling': 'N', 'shadow': 'N', 'shellfish': 'N',
    'snail': 'N', 'sound': 'N', 'sound of a carriage': 'N',
    'stool': 'N', 'tangerine tree': 'N', 'temple': 'N', 'title': 'N',
    'tower': 'N', 'traveler': 'N', 'tree': 'N', 'wheel': 'N',
    'writing brush': 'N',

    # ---------------------------------------------------------------- M 불가산명사
    'fine jade': 'M', 'auspicious jade': 'M', 'great jade': 'M',
    'morning': 'M', 'writing': 'M', 'color': 'M', 'substance': 'M',
    'agate': 'M', 'armor': 'M', 'bamboo': 'M', 'clear crystal': 'M',
    'clear water': 'M', 'clouds': 'M', 'crystal': 'M', 'deep water': 'M',
    'enamel': 'M', 'farming': 'M', 'fine timber': 'M',
    'flowing water': 'M', 'fresh greens': 'M', 'ginseng': 'M',
    'glutinous millet': 'M', 'great waters': 'M', 'ice': 'M',
    'indigo': 'M', 'jasmine': 'M', 'laughter': 'M', 'mountain mist': 'M',
    'mugwort': 'M', 'nature': 'M', 'rice porridge': 'M',
    'rippling water': 'M', 'silver': 'M', 'sparkling light': 'M',
    'summer': 'M', 'surroundings': 'M', 'sweet wine': 'M', 'tea': 'M',
    'thunder': 'M', 'time': 'M', 'trickling water': 'M', 'weaving': 'M',
    'work': 'M',

    # ---------------------------------------------------------------- T the+명사
    # 'bright as sky' 는 비문이다 — 'as the sky' 가 되어야 한다
    'moon': 'T', 'sea': 'T', 'morning sun': 'T', 'wind': 'T',
    'sky': 'T', 'the vast sky': 'T', 'earth': 'T', 'land': 'T',
    'sun': 'T',

    # ---------------------------------------------------------------- 보강
    # 표에 없어 라벨형으로 내려가던 뜻들
    'good omen': 'N', 'years': 'M', 'praise': 'X', 'model': 'N',
    'talent': 'M', 'record': 'N', 'source': 'N', 'center': 'N',
    'gate': 'N', 'hill': 'N', 'sovereign': 'N', 'fine person': 'N',
    'board': 'N', 'vein': 'N',
    'arriving': 'S', 'the first': 'S', 'the most': 'S',

    # ---------------------------------------------------------------- X 추상명사
    'admiration': 'X', 'affinity': 'X', 'awakening': 'X', 'care': 'X',
    'comfort': 'X', 'company': 'X', 'delight': 'X',
    'dignified bearing': 'X', 'ease': 'X', 'filial piety': 'X',
    'fragrance': 'X', 'governance': 'X', 'human order': 'X',
    'integrity': 'X', 'longevity': 'X', 'loyalty': 'X', 'momentum': 'X',
    'refined taste': 'X', 'resourcefulness': 'X', 'thought': 'X',
    'valor': 'X', 'wealth': 'X', 'will': 'X',

    # ---------------------------------------------------------------- V 동사
    'extend': 'V', 'entwine': 'V', 'stand out': 'V',
    # 목적어 없이는 문장이 끊기는 동사 — 쓰지 않는다
    #   'to pair with' → "Someone upright who pairs with"  (끊김)
    # 'to carry on' · 'to shine on' 은 목적어 없이도 성립하므로 그대로 둔다.
    'to pair with': 'S', 'to rely on': 'S',

    # ---------------------------------------------------------------- S 사용 불가
    # 수사·대명사·부사·전치사 — 이름 뜻으로 세울 수 없다
    'all around': 'S', 'also': 'S', 'moreover': 'S', 'crosswise': 'S',
    'always': 'S', 'mutual': 'S', 'not': 'S', 'that one': 'S',
    'nine': 'S', 'hundred million': 'S', 'ten thousand': 'S',
    'ten people': 'S', 'pair': 'S', 'second': 'S', 'straightaway': 'S',
    'truly': 'S', 'east': 'S', 'west': 'S', 'south': 'S', 'north': 'S',
    'standing': 'S', 'going smoothly': 'S', 'bathing': 'S',
    # 고유명사·분류어
    'surname': 'S', 'korea': 'S', 'buddha': 'S',
    'gentle (the wind trigram)': 'S',
    # 신체·친족·성별어 — 카드 문구로 적절하지 않다
    'man': 'S', 'woman': 'S', 'wife': 'S', 'grandson': 'S', 'kin': 'S',
    'body': 'S', 'head': 'S', 'breast': 'S', 'flesh': 'S',
    # 너무 막연해서 뜻이 되지 않는 말
    'thing': 'S', 'things': 'S', 'age': 'S', 'rank': 'S', 'scales': 'S',
}


# 불규칙 활용 — 규칙대로 -s 를 붙이면 'bes' 가 된다
VERB_FORM = {
    'to be': 'abides',            # 有·在·存 (있다)
    'to be born': 'is born',      # 誕
    'to be called': 'is known',   # 稱
}

# 사람에게 '작용하는' 것으로 읽히는 뜻.
# 이 뜻이 타동사와 짝이 되면 사람은 동작의 대상이 된다.
#   旻(하늘) + 佑(돕다) → "하늘이 돕는 자"  Someone whom the sky helps
# 여기 없는 명사는 이 문형을 쓰지 않는다 — 방향을 잘못 잡으면
# "옥을 돕는 자" 같은 엉뚱한 뜻이 되기 때문이다.
FORCE_NOUNS = {
    'sky', 'the vast sky', 'heaven', 'sun', 'morning sun', 'morning sunrise',
    'moon', 'dawn', 'daylight', 'sunlight', 'moonlight', 'firelight',
    'sunrise', 'light', 'sparkling light', 'spring',
    'grace', 'kindness', 'blessing', 'good fortune', 'good omen',
    'virtue', 'merit', 'love', 'happiness', 'joy', 'wisdom', 'truth',
    'nature', 'earth', 'land', 'sea', 'wind', 'rain', 'snow', 'thunder',
}

# 목적어가 필요한 동사. FORCE_NOUNS 문형은 이 동사에만 쓴다
# ("하늘이 오르는 자"는 말이 되지 않는다).
VERB_TRANSITIVE = {
    'to admire', 'to answer', 'to appraise', 'to ask', 'to assemble',
    'to attach', 'to attend', 'to befriend', 'to behold', 'to bestow',
    'to bind', 'to borrow', 'to bring forth', 'to build up', 'to call',
    'to carry', 'to cast', 'to cleanse', 'to combine', 'to compose',
    'to congratulate', 'to connect', 'to cross', 'to cultivate',
    'to discern', 'to draw', 'to emulate', 'to encourage', 'to enjoy',
    'to establish', 'to exalt', 'to examine', 'to extend', 'to face',
    'to fathom', 'to foretell', 'to forgive', 'to fulfill', 'to gather',
    'to gather up', 'to govern', 'to guard', 'to guide', 'to help',
    'to hold', 'to hold between', 'to hold together', 'to honor',
    'to illuminate', 'to inscribe', 'to insert', 'to join', 'to know',
    'to lead', 'to learn', 'to lift', 'to lift high', 'to link', 'to love',
    'to manage', 'to master', 'to nurture', 'to obtain', 'to offer',
    'to open up', 'to open wide', 'to permit', 'to plan', 'to plant',
    'to praise', 'to preserve', 'to press', 'to print', 'to reach',
    'to read', 'to receive', 'to recite', 'to relieve', 'to remember',
    'to renew', 'to report', 'to resolve', 'to rouse', 'to safeguard',
    'to save', 'to scatter', 'to see', 'to seek', 'to send', 'to share',
    'to shed', 'to shine on', 'to soothe', 'to span', 'to spread',
    'to steady', 'to stretch', 'to strike', 'to support', 'to take',
    'to teach', 'to transform', 'to trust', 'to unfold', 'to unfurl',
    'to unite', 'to uphold', 'to verify', 'to wed', 'to wind',
}

# ---------------------------------------------------------------- 전수 검수 추가
# 1,076자 영어뜻을 전수 검수하면서 새로 들어온 뜻들의 품사.
# (2026-09 검수 — 동음이의 오역 96건 수정 시 생긴 표현들)
GLOSS_POS.update({
    # 형용사
    'sweet': 'A', 'triumphant': 'A', 'cool': 'A', 'high': 'A',
    'decisive': 'A', 'righteous': 'A', 'just': 'A', 'royal': 'A',
    'settled': 'A', 'steady': 'A', 'true': 'A', 'real': 'A',
    'supreme': 'A', 'utmost': 'A', 'widespread': 'A', 'countless': 'A',
    'all-encompassing': 'A', 'quick-witted': 'A',
    # 추상명사 — 비유가 아니라 '~를 지닌' 형태로 쓴다
    'quality': 'X', 'character': 'X', 'noble rank': 'X',
    # 불가산·집합명사
    'all things': 'M',
})

# ---------------------------------------------------------------- 영어 가독성 검수
# "A ___ person" 틀에 넣어 읽어보니 영어로 곤란한 형용사들이 있었다.
#   white  → 인종 표현으로 읽힌다        green → '풋내기'
#   dense  → '멍청한'                  high  → 약물 은어
#   certain→ 'a certain person'(어떤 사람)  cool → 은어
# 해당 한자의 뜻을 고쳤고(白→pure, 綠→greenery ...), 여기에 새 표현의 품사를 등록한다.
GLOSS_POS.update({
    'far-reaching': 'A', 'discreet': 'A', 'careful': 'A', 'agreeable': 'A',
    'earnest': 'A', 'fresh': 'A', 'level': 'A', 'sure': 'A',
    'greenery': 'M', 'abundance': 'X',
    # 색은 사람을 가리키는 형용사로 쓰면 어색하다 — 명사로 두어
    # "Someone bright as crimson" / "A name of crimson and ..." 형태로만 쓰이게 한다
    'crimson': 'M', 'purple': 'M',
})

# "A ___ person" 틀에 넣으면 안 되는 형용사.
#
# 데이터를 고쳤으니 지금은 해당하는 한자가 없다. 그래도 목록을 남긴다 —
# 나중에 한자를 추가하다 같은 표현이 들어오면 이 틀을 피하게 된다.
# (사람을 가리키는 문장이라 뜻이 맞아도 영어로 곤란해지는 부류다)
ADJ_NOT_PERSON = {
    'white', 'black', 'yellow', 'red', 'green', 'blue', 'purple', 'crimson',
    'blue-green', 'indigo', 'dense', 'high', 'certain', 'cool', 'far',
    'close', 'taut', 'sudden', 'countless', 'new', 'eldest', 'right',
    'real', 'plain', 'thick', 'flat', 'odd', 'strange', 'simple',
}

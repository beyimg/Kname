# -*- coding: utf-8 -*-
"""
카드 앞면 '한 줄 의미'를 손으로 쓴 이름들.

한 줄 의미는 세 경로로 만든다.
  1. 의미설명 문장에서 대표 구절을 뽑아낸다        (사전 602개 중 516개 성공)
  2. LLM이 설명과 함께 써 준 SHORT 줄을 쓴다        (사전 밖 이름)
  3. 한자 뜻으로 조립한다                          (품사를 아는 뜻만)

여기 있는 이름은 1번이 실패하는데(설명문 형식이 달라서) 3번 조립 결과가
문법은 맞지만 뜻이 어색한 것들이다.
  해나: 'Someone graceful, with a model'   ← 楷(모범)를 명사로 쓰면 어색하다
  지혜: 'Someone wise, with wisdom'        ← 智·慧가 둘 다 지혜라서 되풀이된다
  새나: 'Someone graceful as a seal'       ← 璽(도장)는 비유 대상이 안 된다
조립 규칙을 더 손보는 대신 이 이름들만 직접 쓴다. 개수가 적고(20여 개)
사전에 고정된 이름이라 늘어나지 않는다.
"""

SHORT_EN = {
    # 楷 (모범) — 명사로 세우면 어색해서 문장으로 풀어 쓴다
    '해나': 'Someone graceful who sets an example',
    '해인': 'Someone kind who sets an example',
    '해성': 'Someone wise who sets an example',
    '해일': 'A bright example to others',

    # 智·慧 (지혜) 가 겹치는 이름
    '지혜': 'Wisdom itself, written twice over',
    '예지': 'Someone bright with clear wisdom',
    '지성': 'Someone wise and saintly at heart',
    '리지': 'Someone quick-minded and wise',

    # 璽 (도장) — 비유 대상이 되지 않는다
    '새나': 'Someone graceful who leaves a mark',
    '한새': 'Someone vast who leaves a mark',

    # 恩 (은혜) — 'as kindness' 는 비유가 어색하다
    '시은': 'Someone upright and full of kindness',
    '고은': 'Someone bright and full of kindness',
    '조은': 'Kindness that shines on others',

    # 讚 (칭송) — 명사보다 서술로 쓰는 편이 자연스럽다
    '예찬': 'Someone wise and worthy of praise',
    '희찬': 'Someone bright and worthy of praise',
    '세찬': 'Someone praised through the years',
    '솔찬': 'A leader worthy of praise',

    # 그 밖의 어색한 조립
    '가야': 'Someone fine and full of grace',
    '로사': 'Someone graceful as a heron',
    '재범': 'Talent guided by principle',
    '정서': 'A sparkling sign of good things',
    '서아': 'A refined sign of good things',
}

# -*- coding: utf-8 -*-
"""
배포용 안전장치 두 가지.

1) RateLimiter — IP당 요청 속도 제한 (분당/시간당). 봇·연타를 막는다.
   메모리 기반이라 워커(프로세스)마다 따로 센다. 즉 실효 한도는
   대략 (설정값 × 워커수)로, '정밀 과금'이 아니라 '안전 상한'이다.
   워커 1~3개 규모에선 충분하다. 정밀하게 하려면 Redis로 교체.

2) DailyBudget — 하루에 '새 이름 생성'을 몇 건까지 허용할지 상한.
   초과하면 새 이름 생성(LLM 호출)을 멈추고, 이미 있는 이름만 응답한다.
   CACHE_DIR의 작은 JSON 파일로 세며, 자정(날짜 변경)에 자동 리셋된다.
"""
import os
import json
import time
import threading
import datetime
from collections import deque, defaultdict


class RateLimiter:
    def __init__(self, per_min: int = 20, per_hour: int = 200):
        self.per_min = per_min
        self.per_hour = per_hour
        self._hits = defaultdict(deque)      # key(IP) -> deque[timestamps]
        self._lock = threading.Lock()
        self._last_clean = 0.0

    def check(self, key: str) -> bool:
        """허용이면 True(+기록), 초과면 False."""
        if self.per_min <= 0 and self.per_hour <= 0:
            return True
        now = time.time()
        with self._lock:
            dq = self._hits[key]
            hour_ago = now - 3600
            while dq and dq[0] < hour_ago:
                dq.popleft()
            in_min = sum(1 for t in dq if t >= now - 60)
            if (self.per_hour and len(dq) >= self.per_hour) or \
               (self.per_min and in_min >= self.per_min):
                return False
            dq.append(now)
            # 가끔 빈 키 청소 (메모리 누수 방지)
            if now - self._last_clean > 300:
                self._last_clean = now
                for k in [k for k, d in self._hits.items()
                          if not d or d[-1] < hour_ago]:
                    self._hits.pop(k, None)
            return True


class DailyBudget:
    def __init__(self, path: str, daily_max: int = 1500):
        self.path = path
        self.daily_max = daily_max
        self._lock = threading.Lock()

    @staticmethod
    def _today() -> str:
        return datetime.date.today().isoformat()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def allow(self) -> bool:
        """오늘 새 이름 생성이 아직 한도 안이면 True."""
        if self.daily_max <= 0:
            return True
        with self._lock:
            d = self._load()
            if d.get('date') != self._today():
                return True                 # 새 날 → 리셋된 셈
            return int(d.get('count', 0)) < self.daily_max

    def record(self, n: int = 1) -> None:
        if self.daily_max <= 0:
            return
        with self._lock:
            d = self._load()
            today = self._today()
            if d.get('date') != today:
                d = {'date': today, 'count': 0}
            d['count'] = int(d.get('count', 0)) + n
            try:
                tmp = self.path + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(d, f)
                os.replace(tmp, self.path)   # 원자적 교체
            except Exception:
                pass

    def status(self) -> tuple:
        d = self._load()
        cnt = int(d.get('count', 0)) if d.get('date') == self._today() else 0
        return cnt, self.daily_max

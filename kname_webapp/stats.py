# -*- coding: utf-8 -*-
"""
변환 이벤트 통계 — 가벼운 SQLite 저장소.

app 이 변환을 한 번 처리할 때마다 record() 로 한 줄 남기고,
/admin 대시보드가 summary() 로 집계를 읽는다.

저장 위치는 CACHE_DIR(캐시·예산과 동일). 영구 디스크를 붙여두면 영구 보존,
아니면 재배포 시 초기화된다(캐시와 같은 수명).

의존성은 표준 라이브러리(sqlite3)만 쓴다.
"""
from __future__ import annotations

import os
import time
import sqlite3
import threading
from collections import Counter


class Stats:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self.ok = False
        try:
            self._init()
            self.ok = True
        except Exception as e:
            import sys
            print(f'[stats] init failed: {e}', file=sys.stderr, flush=True)

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=5)
        try:
            c.execute('PRAGMA journal_mode=WAL')
        except Exception:
            pass
        return c

    def _init(self):
        with self._conn() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS conv(
                ts       INTEGER,
                ok       INTEGER,
                is_new   INTEGER,
                native   INTEGER,
                quality  TEXT,
                sex      TEXT,
                first_en TEXT,
                last_en  TEXT,
                given    TEXT,
                hangul   TEXT
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_conv_ts ON conv(ts)')

    # ------------------------------------------------------------ 기록
    def record(self, *, ok, is_new, native, quality, sex,
               first_en, last_en, given, hangul):
        if not self.ok:
            return
        try:
            with self._lock, self._conn() as c:
                c.execute(
                    'INSERT INTO conv(ts,ok,is_new,native,quality,sex,'
                    'first_en,last_en,given,hangul) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (int(time.time()), int(bool(ok)), int(bool(is_new)),
                     int(bool(native)), (quality or '')[:8], (sex or '')[:8],
                     (first_en or '')[:40].strip().lower(),
                     (last_en or '')[:40].strip().lower(),
                     (given or '')[:20], (hangul or '')[:20]))
        except Exception:
            pass

    # ------------------------------------------------------------ 집계
    def summary(self, top_n=10, days=7):
        """대시보드가 필요로 하는 모든 집계를 dict 로 반환."""
        empty = {
            'ok': self.ok, 'total': 0, 'today': 0, 'success': 0, 'fail': 0,
            'success_rate': None, 'new': 0, 'known': 0, 'cache_rate': None,
            'native': 0, 'native_rate': None, 'quality': {},
            'top_first': [], 'top_hangul': [], 'daily': [], 'new_today': 0,
        }
        if not self.ok:
            return empty
        try:
            now = int(time.time())
            # 오늘(로컬이 아니라 UTC 자정 기준 — 서버 표준시)
            day_start = now - (now % 86400)
            with self._conn() as c:
                cur = c.cursor()
                total = cur.execute('SELECT COUNT(*) FROM conv').fetchone()[0]
                if not total:
                    return empty
                success = cur.execute(
                    'SELECT COUNT(*) FROM conv WHERE ok=1').fetchone()[0]
                fail = total - success
                today = cur.execute(
                    'SELECT COUNT(*) FROM conv WHERE ts>=?',
                    (day_start,)).fetchone()[0]
                new_today = cur.execute(
                    'SELECT COUNT(*) FROM conv WHERE ts>=? AND is_new=1',
                    (day_start,)).fetchone()[0]
                new = cur.execute(
                    'SELECT COUNT(*) FROM conv WHERE is_new=1').fetchone()[0]
                known = total - new
                native = cur.execute(
                    'SELECT COUNT(*) FROM conv WHERE ok=1 AND native=1'
                ).fetchone()[0]
                # 품질 분포(성공 건만)
                qrows = cur.execute(
                    'SELECT quality, COUNT(*) FROM conv WHERE ok=1 '
                    'GROUP BY quality').fetchall()
                quality = {q or '?': n for q, n in qrows}
                # 인기 입력 이름(성공 건, first+last)
                trows = cur.execute(
                    "SELECT first_en||' '||last_en AS nm, COUNT(*) c "
                    'FROM conv WHERE ok=1 AND first_en!="" '
                    'GROUP BY nm ORDER BY c DESC LIMIT ?', (top_n,)).fetchall()
                top_first = [{'name': (nm or '').title(), 'count': c}
                             for nm, c in trows]
                # 인기 결과 한국 이름
                hrows = cur.execute(
                    'SELECT hangul, COUNT(*) c FROM conv '
                    'WHERE ok=1 AND hangul!="" '
                    'GROUP BY hangul ORDER BY c DESC LIMIT ?',
                    (top_n,)).fetchall()
                top_hangul = [{'name': h, 'count': c} for h, c in hrows]
                # 최근 N일 일별 건수
                daily = []
                for i in range(days - 1, -1, -1):
                    d0 = day_start - i * 86400
                    d1 = d0 + 86400
                    n = cur.execute(
                        'SELECT COUNT(*) FROM conv WHERE ts>=? AND ts<?',
                        (d0, d1)).fetchone()[0]
                    daily.append({'day': time.strftime('%m/%d', time.gmtime(d0)),
                                  'count': n})
            succ_ok = success + fail
            return {
                'ok': True, 'total': total, 'today': today, 'new_today': new_today,
                'success': success, 'fail': fail,
                'success_rate': round(100 * success / succ_ok, 1) if succ_ok else None,
                'new': new, 'known': known,
                'cache_rate': round(100 * known / total, 1) if total else None,
                'native': native,
                'native_rate': round(100 * native / success, 1) if success else None,
                'quality': quality, 'top_first': top_first,
                'top_hangul': top_hangul, 'daily': daily,
            }
        except Exception as e:
            import sys
            print(f'[stats] summary failed: {e}', file=sys.stderr, flush=True)
            return empty

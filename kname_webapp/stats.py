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

# 표시 시각대.
# 저장은 항상 UTC epoch 이고, **보여줄 때만** 옮긴다. DB 값을 손대면
# 나중에 시각대를 바꿀 수 없게 되고 이미 쌓인 기록과 어긋난다.
#
# 서버(Render)는 UTC 로 돌지만 이 화면을 보는 사람은 한국에 있다.
# 그래서 기본을 KST(+9)로 둔다. TZ_OFFSET_H 로 바꿀 수 있다.
#
# '오늘'과 일별 묶음도 같이 옮겨야 한다. 라벨만 옮기면 09:00 이전 기록이
# 어제 칸에 들어가 있는데 날짜만 오늘로 찍히는 식으로 어긋난다.
try:
    TZ_OFFSET = int(float(os.environ.get('TZ_OFFSET_H', 9)) * 3600)
except Exception:
    TZ_OFFSET = 9 * 3600


def _fmt(ts, fmt='%m/%d %H:%M:%S'):
    """UTC epoch → 표시용 문자열(현지 시각)."""
    try:
        return time.strftime(fmt, time.gmtime(int(ts) + TZ_OFFSET))
    except Exception:
        return ''


def _local_day_start(now):
    """현지 자정에 해당하는 UTC epoch."""
    shifted = now + TZ_OFFSET
    return shifted - (shifted % 86400) - TZ_OFFSET


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
                hangul   TEXT,
                flags    TEXT,
                country  TEXT,
                geo      TEXT
            )''')
            # 이미 만들어진 DB 에는 칼럼이 없다 — 한 번만 붙인다
            for _col in ('flags TEXT', 'country TEXT', 'geo TEXT', 'ms INTEGER', 'source TEXT'):
                try:
                    c.execute(f'ALTER TABLE conv ADD COLUMN {_col}')
                except Exception:
                    pass
            c.execute('CREATE INDEX IF NOT EXISTS idx_conv_ts ON conv(ts)')
            # 부하 기록: 한 워커가 동시에 처리 중인 요청이 창구 수에 닿은 순간.
            # 워커(프로세스)마다 따로 세므로 pid 를 같이 남긴다. load_summary() 가 센다.
            c.execute('''CREATE TABLE IF NOT EXISTS load(
                ts       INTEGER,
                pid      INTEGER,
                inflight INTEGER
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_load_ts ON load(ts)')
            # 행동 기록: 공유·링크복사·저장 클릭, 공유 링크로 들어온 조회.
            # kind: share | copy | download | view_share   source: 첫 방문의 유입 경로(쿠키)
            c.execute('''CREATE TABLE IF NOT EXISTS event(
                ts     INTEGER,
                kind   TEXT,
                source TEXT,
                name   TEXT
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_event_ts ON event(ts)')
            # 결과물 품질 문제(비문·의미설명 없음 등). 변환은 성공했지만
            # 카드에 실린 결과가 이상한 경우를 여기 쌓는다.
            c.execute('''CREATE TABLE IF NOT EXISTS issue(
                ts     INTEGER,
                code   TEXT,
                given  TEXT,
                detail TEXT
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_issue_ts ON issue(ts)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_issue_code ON issue(code)')

    # ------------------------------------------------------------ 기록
    def record(self, *, ok, is_new, native, quality, sex,
               first_en, last_en, given, hangul, flags='',
               country='', geo='', ms=None, source=''):
        if not self.ok:
            return
        try:
            with self._lock, self._conn() as c:
                c.execute(
                    'INSERT INTO conv(ts,ok,is_new,native,quality,sex,'
                    'first_en,last_en,given,hangul,flags,country,geo,ms,source) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (int(time.time()), int(bool(ok)), int(bool(is_new)),
                     int(bool(native)), (quality or '')[:8], (sex or '')[:8],
                     (first_en or '')[:40].strip().lower(),
                     (last_en or '')[:40].strip().lower(),
                     (given or '')[:20], (hangul or '')[:20],
                     (flags or '')[:120],
                     (country or '')[:2].upper(),
                     (geo or '')[:2].upper(),
                     int(ms) if ms is not None else None,
                     (source or '')[:60]))
        except Exception:
            pass

    def record_event(self, kind: str, source: str = '', name: str = '') -> None:
        """공유·복사·저장 클릭, 공유 링크 조회를 남긴다."""
        if not self.ok:
            return
        try:
            with self._lock, self._conn() as c:
                c.execute('INSERT INTO event(ts,kind,source,name) VALUES(?,?,?,?)',
                          (int(time.time()), (kind or '')[:20], (source or '')[:60], (name or '')[:20]))
        except Exception:
            pass

    # ------------------------------------------------------------ 유입·공유
    def sources(self, days=30, top_n=15) -> dict:
        """유입 경로별 변환 수 + 공유 행동. GROWTH_STRATEGY 의 '가장 중요한 하나' = 공유 클릭률.

        source 값: ref 태그(?ref=… / ?utm_source=…) 그대로, 없으면 'ref:<referer 호스트>',
        둘 다 없으면 'direct'(주소 직접 입력·앱 내 링크 등). 사이트 안에서 온 것은 세지 않는다."""
        out = {'ok': self.ok, 'window': days, 'rows': [], 'total': 0,
               'events': {}, 'conversions': 0, 'share_rate': None, 'view_share': 0}
        if not self.ok:
            return out
        try:
            since = int(time.time()) - days * 86400
            with self._conn() as c:
                cur = c.cursor()
                rows = cur.execute(
                    'SELECT COALESCE(NULLIF(source,""),"direct") s, COUNT(*) n, SUM(is_new) new '
                    'FROM conv WHERE ts>=? AND ok=1 GROUP BY s ORDER BY n DESC LIMIT ?',
                    (since, top_n)).fetchall()
                out['rows'] = [{'source': r[0], 'count': r[1], 'new': r[2] or 0} for r in rows]
                out['total'] = cur.execute('SELECT COUNT(*) FROM conv WHERE ts>=? AND ok=1',
                                           (since,)).fetchone()[0]
                out['conversions'] = out['total']
                ev = cur.execute('SELECT kind, COUNT(*) FROM event WHERE ts>=? GROUP BY kind',
                                 (since,)).fetchall()
                out['events'] = {k: n for k, n in ev}
                out['view_share'] = out['events'].get('view_share', 0)
                shares = sum(out['events'].get(k, 0) for k in ('share', 'copy', 'download'))
                out['shares'] = shares
                out['share_rate'] = (round(100.0 * shares / out['total'], 1)
                                     if out['total'] else None)
            return out
        except Exception as e:
            import sys
            print(f'[stats] sources failed: {e}', file=sys.stderr, flush=True)
            return out

    def record_load(self, pid: int, inflight: int) -> None:
        """동시 처리 중 요청이 창구 수에 닿은 순간을 남긴다(app.py 가 호출)."""
        if not self.ok:
            return
        try:
            with self._lock, self._conn() as c:
                c.execute('INSERT INTO load(ts,pid,inflight) VALUES(?,?,?)',
                          (int(time.time()), int(pid), int(inflight)))
        except Exception:
            pass

    # ------------------------------------------------------------ 부하
    def load_summary(self, hours: float = 1.0) -> dict:
        """최근 hours 시간의 부하 — /status 의 load 항목.

        핵심 지표는 **캐시 이름의 응답 시간**이다. 처음 보는 이름은 LLM 을 기다리느라
        원래 몇 초 걸리므로 서버 상태를 말해주지 않지만, 사전·캐시 이름은 0.2초면 끝나야
        한다. 이게 늘어나면 서버가 밀리는 것이다(등급/워커를 올릴 신호)."""
        out = {'window_h': hours, 'conv': 0, 'conv_new': 0,
               'cached_ms': None, 'new_ms': None, 'saturation_events': 0}
        if not self.ok:
            return out
        since = int(time.time() - hours * 3600)

        def pct(vals):
            if not vals:
                return None
            vals = sorted(vals)
            def p(q):
                return int(vals[min(len(vals) - 1, int(round((len(vals) - 1) * q)))])
            return {'n': len(vals), 'p50': p(0.5), 'p95': p(0.95), 'max': int(vals[-1])}

        try:
            with self._lock, self._conn() as c:
                rows = c.execute('SELECT is_new, ms FROM conv WHERE ts >= ?', (since,)).fetchall()
                out['conv'] = len(rows)
                out['conv_new'] = sum(1 for r in rows if r[0])
                out['cached_ms'] = pct([r[1] for r in rows if not r[0] and r[1] is not None])
                out['new_ms'] = pct([r[1] for r in rows if r[0] and r[1] is not None])
                out['saturation_events'] = c.execute(
                    'SELECT COUNT(*) FROM load WHERE ts >= ?', (since,)).fetchone()[0]
        except Exception:
            pass
        return out

    def recent(self, limit=60, since_ts=None):
        """
        최근 변환 로그. /admin 이 실시간으로 뿌린다.

        집계만으로는 '지금 사용자가 무엇을 넣고 무엇을 받았는지'를 볼 수 없다.
        since_ts 를 주면 그 이후 것만 돌려주므로 폴링이 가벼워진다.
        """
        if not self.ok:
            return []
        try:
            with self._conn() as c:
                if since_ts:
                    rows = c.execute(
                        'SELECT ts,ok,is_new,native,quality,sex,first_en,'
                        'last_en,given,hangul,flags,country,geo FROM conv '
                        'WHERE ts>? ORDER BY ts DESC LIMIT ?',
                        (int(since_ts), limit)
                    ).fetchall()
                else:
                    rows = c.execute(
                        'SELECT ts,ok,is_new,native,quality,sex,first_en,'
                        'last_en,given,hangul,flags,country,geo FROM conv '
                        'ORDER BY ts DESC LIMIT ?', (limit,)).fetchall()
            out = []
            for (ts, ok, is_new, native, q, sex, fe, le, gv, hg, fl,
                 ctry, geo) in rows:
                out.append({
                    'ts': ts,
                    'when': _fmt(ts),
                    'ok': bool(ok), 'is_new': bool(is_new),
                    'native': bool(native), 'quality': q or '',
                    'sex': sex or '',
                    'input': f'{(fe or "").title()} {(le or "").title()}'.strip(),
                    'result': hg or gv or '',
                    'flags': [x for x in (fl or '').split(' ') if x],
                    # country = 사용자가 직접 고른 값, geo = 헤더 추정치.
                    # 둘을 섞으면 신뢰도가 다른 데이터가 한 칸에 들어간다.
                    'country': ctry or '',
                    'geo': geo or '',
                })
            return out
        except Exception as e:
            import sys
            print(f'[stats] recent failed: {e}', file=sys.stderr, flush=True)
            return []

    def export_rows(self, days=90, limit=20000):
        """
        /admin 의 '엑셀로 다운로드'용 — recent() 보다 넓게(기간 지정) 그리고
        원본 칼럼을 그대로(ms, source 포함) 돌려준다. recent() 는 화면에 뿌릴
        60줄짜리 요약이라 그걸로는 분석이 안 된다.

        limit 은 방어용 상한이다(관리자 토큰이 새어나가도 응답 하나로 DB 전체를
        긁어가진 못하게). 필요하면 늘리되, 무제한으로는 두지 않는다.
        """
        if not self.ok:
            return []
        try:
            since = int(time.time()) - int(days) * 86400
            with self._conn() as c:
                rows = c.execute(
                    'SELECT ts,ok,is_new,native,quality,sex,first_en,last_en,'
                    'given,hangul,flags,country,geo,ms,source FROM conv '
                    'WHERE ts>=? ORDER BY ts DESC LIMIT ?',
                    (since, int(limit))).fetchall()
            out = []
            for (ts, ok, is_new, native, q, sex, fe, le, gv, hg, fl,
                 ctry, geo, ms, source) in rows:
                out.append({
                    'when': _fmt(ts, '%Y-%m-%d %H:%M:%S'),
                    'ok': bool(ok),
                    'is_new': bool(is_new),
                    'native': bool(native),
                    'quality': q or '',
                    'sex': sex or '',
                    'first_en': (fe or '').title(),
                    'last_en': (le or '').title(),
                    'given': gv or '',
                    'hangul': hg or '',
                    'flags': fl or '',
                    'country': ctry or '',
                    'geo': geo or '',
                    'ms': ms,
                    'source': source or '',
                })
            return out
        except Exception as e:
            import sys
            print(f'[stats] export_rows failed: {e}', file=sys.stderr, flush=True)
            return []

    def record_issue(self, code, given='', detail=''):
        """결과물 품질 문제 1건 기록."""
        if not self.ok:
            return
        try:
            with self._lock, self._conn() as c:
                c.execute('INSERT INTO issue(ts,code,given,detail) VALUES(?,?,?,?)',
                          (int(time.time()), str(code)[:40],
                           str(given or '')[:20], str(detail or '')[:200]))
        except Exception:
            pass

    def issues(self, days=7, limit=40):
        """
        품질 문제 집계 — 코드별 건수(전체·최근)와 최근 사례.
        /admin 이 그대로 뿌린다.
        """
        out = {'ok': self.ok, 'by_code': [], 'recent': [], 'total': 0,
               'window': days}
        if not self.ok:
            return out
        try:
            now = int(time.time())
            since = now - days * 86400
            with self._conn() as c:
                cur = c.cursor()
                out['total'] = cur.execute(
                    'SELECT COUNT(*) FROM issue').fetchone()[0]
                rows = cur.execute(
                    'SELECT code, COUNT(*) c, MAX(ts) FROM issue '
                    'WHERE ts>=? GROUP BY code ORDER BY c DESC',
                    (since,)).fetchall()
                out['by_code'] = [
                    {'code': code, 'count': n,
                     'last': _fmt(ts, '%m/%d %H:%M')}
                    for code, n, ts in rows]
                rec = cur.execute(
                    'SELECT ts, code, given, detail FROM issue '
                    'ORDER BY ts DESC LIMIT ?', (limit,)).fetchall()
                out['recent'] = [
                    {'when': _fmt(ts, '%m/%d %H:%M'),
                     'code': code, 'given': g, 'detail': d}
                    for ts, code, g, d in rec]
            return out
        except Exception as e:
            import sys
            print(f'[stats] issues failed: {e}', file=sys.stderr, flush=True)
            return out

    def countries(self, days=30, top_n=15):
        """
        국적 분포. 직접 고른 값(country)과 헤더 추정치(geo)를 따로 센다.
        추정치는 정확도가 낮으므로 섞지 않는다.
        """
        out = {'ok': self.ok, 'picked': [], 'geo': [], 'picked_total': 0,
               'geo_total': 0, 'blank': 0, 'window': days}
        if not self.ok:
            return out
        try:
            since = int(time.time()) - days * 86400
            with self._conn() as c:
                cur = c.cursor()
                for key in ('country', 'geo'):
                    rows = cur.execute(
                        f'SELECT {key}, COUNT(*) n FROM conv '
                        f'WHERE ts>=? AND {key} IS NOT NULL AND {key}!="" '
                        f'GROUP BY {key} ORDER BY n DESC LIMIT ?',
                        (since, top_n)).fetchall()
                    tot = cur.execute(
                        f'SELECT COUNT(*) FROM conv WHERE ts>=? '
                        f'AND {key} IS NOT NULL AND {key}!=""',
                        (since,)).fetchone()[0]
                    out['picked' if key == 'country' else 'geo'] = [
                        {'code': k, 'count': n} for k, n in rows]
                    out['picked_total' if key == 'country'
                        else 'geo_total'] = tot
                out['blank'] = cur.execute(
                    'SELECT COUNT(*) FROM conv WHERE ts>=? AND '
                    '(country IS NULL OR country="")', (since,)).fetchone()[0]
            return out
        except Exception as e:
            import sys
            print(f'[stats] countries failed: {e}', file=sys.stderr, flush=True)
            return out

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
            day_start = _local_day_start(now)
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
                    daily.append({'day': _fmt(d0, '%m/%d'),
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

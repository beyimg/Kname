# -*- coding: utf-8 -*-
"""
사용자가 겪는 오류·이상 결과를 한 곳으로 보고하는 얇은 헬퍼.

- 항상 stderr(Render 로그)에 남긴다.
- Sentry(SENTRY_DSN)가 켜져 있으면 이벤트로도 보낸다. 미설정이면 자동 no-op.
- fingerprint 로 "원인 종류별"로 묶어, 같은 문제 100건이 이슈 1개(카운트 100)로
  보이게 한다(알림 폭탄 방지).

app.py, transliterate.py, tts_full.py 등 어디서든:
    from monitor import report
    report('conversion failed', level='error',
           fingerprint=['conv-fail', reason], reason=reason, name=name)
"""
from __future__ import annotations

import sys


def report(message, level='warning', fingerprint=None, **tags):
    # 1) 콘솔 로그는 항상 남긴다
    try:
        tagstr = ' '.join(f'{k}={v}' for k, v in tags.items())
        print(f'[monitor:{level}] {message} {tagstr}'.rstrip(),
              file=sys.stderr, flush=True)
    except Exception:
        pass

    # 2) Sentry 가 있으면 이벤트로도 보낸다(없으면 조용히 무시)
    try:
        import sentry_sdk
    except Exception:
        return
    try:
        with sentry_sdk.push_scope() as scope:
            try:
                scope.level = level
            except Exception:
                pass
            if fingerprint:
                scope.fingerprint = list(fingerprint)
            for k, v in tags.items():
                try:
                    scope.set_tag(k, str(v)[:190])
                except Exception:
                    pass
            sentry_sdk.capture_message(message, level=level)
        return
    except Exception:
        pass
    # 스코프 API 가 버전 차이로 실패하면 최소한 메시지라도 보낸다
    try:
        sentry_sdk.capture_message(message, level=level)
    except Exception:
        pass

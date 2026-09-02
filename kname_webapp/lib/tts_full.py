# -*- coding: utf-8 -*-
"""
풀네임 음성 생성 (요청 시 생성 + 디스크 캐시).

성씨와 이름을 따로 만들어 이어 붙이면
  · 조합이 8만 가지라 미리 만들 수 없고
  · 한 글자 성씨는 Chirp 3: HD가 무음으로 합성한다

그래서 '이수아'처럼 풀네임을 통째로 한 번에 합성한다.
3글자 이상이라 Chirp 3도 정상 처리하고, 목소리도 하나로 통일된다.

한 번 만든 파일은 static/audio/full/ 에 남아 다음 요청부터는 그대로 서빙되므로
같은 이름에 대해 API가 다시 호출되지 않는다.
"""
from __future__ import annotations

import os
import re
import threading

# 사용자가 겪는 TTS 오류 보고 헬퍼(Sentry+stderr). 없으면 no-op.
try:
    from monitor import report
except Exception:
    def report(*a, **k):
        pass

# 음성 생성이 늦어지면 페이지 응답이 그만큼 막히므로 짧게 끊는다.
# 실패해도 브라우저 내장 음성으로 대체되므로 서비스는 계속된다.
# (요청은 /api/tts 로 비동기 처리되므로 페이지 로딩은 막지 않는다.)
REQUEST_TIMEOUT = float(os.environ.get('TTS_TIMEOUT', 10.0))

# 목소리·모델·어조는 코드 수정 없이 환경변수로 바꿀 수 있다.
DEFAULT_VOICE = os.environ.get('TTS_VOICE', 'ko-KR-Chirp3-HD-Laomedeia')

# 어조 지시(Style instructions). Gemini-TTS 모델에서만 동작하는 prompt 필드다.
# Chirp 3: HD 에는 해당 필드가 없어 자동으로 무시된다.
# 이름은 짧아서 어조 프롬프트가 자연스러움을 좌우한다 — 풍부하게 지시한다.
STYLE_PROMPT = os.environ.get('TTS_STYLE_PROMPT') or (
    "Say this Korean person's name warmly and naturally, the way a friendly "
    "native Korean speaker would gently introduce someone. Use the soft, "
    "gently falling intonation of a real Korean given name — clear and "
    "unhurried, never flat, robotic, or rising like a question."
)

# Gemini-TTS 모델. None(빈 문자열) 이면 Chirp 3 방식으로만 합성한다.
# 더 자연스러운 'gemini-2.5-pro-tts' 로 올리려면 TTS_MODEL 환경변수로 바꾼다.
# Gemini-TTS 는 음성 이름을 'Laomedeia' 처럼 접두사 없이 써야 한다
# (ko-KR-Chirp3-HD- 를 붙이면 "Gemini models cannot be used with
#  non-Gemini voices" 오류가 난다). 아래에서 자동으로 떼어낸다.
GEMINI_MODEL = os.environ.get('TTS_MODEL', 'gemini-2.5-flash-tts') or None

# 속도는 지정하지 않는다(요청에 따라 무시).
SPEAKING_RATE = None
PITCH = None

# 이름 하나만 던지면 끝이 올라가기 쉬워 마침표로 마무리한다.
FLATTEN_TONE = True

_SAFE = re.compile(r'^[가-힣]{2,8}$')


class FullNameTTS:
    def __init__(self, out_dir: str, voice: str = DEFAULT_VOICE,
                 rate=SPEAKING_RATE, pitch=PITCH,
                 flatten: bool = FLATTEN_TONE,
                 style_prompt: str = STYLE_PROMPT,
                 model: str = GEMINI_MODEL):
        self.out_dir = out_dir
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self.flatten = flatten
        self.style_prompt = style_prompt
        self.model = model
        self._warned = False
        self.last_mode = None
        self.last_error = None
        self._err_warned = False
        self._client = None
        self._lock = threading.Lock()
        os.makedirs(out_dir, exist_ok=True)
        # 설정(목소리·모델·어조)별로 캐시를 분리한다. 설정이 바뀌면 태그가 바뀌어
        # 기존 캐시를 포함한 모든 발음이 새 설정으로 다시 생성된다.
        import hashlib
        sig = f'{self.voice}|{self.model}|{self.style_prompt}|{self.flatten}'
        self.tag = hashlib.md5(sig.encode('utf-8')).hexdigest()[:8]

    # ------------------------------------------------------------ 내부
    def _get_client(self):
        if self._client is None:
            from google.cloud import texttospeech as tts
            self._client = (tts.TextToSpeechClient(), tts)
        return self._client

    def _path(self, name: str) -> str:
        return os.path.join(self.out_dir, self.tag, f'{name}.mp3')

    def _url(self, name: str) -> str:
        return f'/static/audio/full/{self.tag}/{name}.mp3'

    def _synthesize(self, name: str) -> bytes:
        client, tts = self._get_client()
        # 단독 단어는 물음표처럼 끝이 올라가기 쉬워 마침표로 마무리한다
        spoken = f'{name}.' if self.flatten else name

        # ① Gemini-TTS — 어조를 자연어로 지시할 수 있다(prompt 필드)
        if self.model and self.style_prompt:
            try:
                out = self._synthesize_gemini(client, tts, spoken)
                self.last_mode = 'Gemini-TTS (어조 지시 적용)'
                return out
            except Exception as e:
                if not self._warned:
                    self._warned = True
                    msg = str(e)
                    print(f'[TTS] Gemini-TTS 사용 불가 → Chirp 3 로 대체합니다.')
                    print(f'      {type(e).__name__}: {msg[:120]}')
                    if 'permission' in msg.lower() or 'aiplatform' in msg.lower():
                        print('      → 서비스 계정에 roles/aiplatform.user 역할이 필요합니다.')
                    elif 'not found' in msg.lower() or 'model' in msg.lower():
                        print(f'      → 모델({self.model})을 쓸 수 없는 리전일 수 있습니다.')

        # ② Chirp 3: HD — 어조 지시는 없지만 안정적이다
        voice = tts.VoiceSelectionParams(language_code='ko-KR', name=self.voice)
        inp = tts.SynthesisInput(text=spoken)

        cfg = {'audio_encoding': tts.AudioEncoding.MP3}
        if self.rate:
            cfg['speaking_rate'] = self.rate
        if self.pitch:
            cfg['pitch'] = self.pitch

        resp = client.synthesize_speech(
            input=inp, voice=voice, audio_config=tts.AudioConfig(**cfg),
            timeout=REQUEST_TIMEOUT)
        self.last_mode = 'Chirp 3: HD (어조 지시 없음)'
        return resp.audio_content

    @staticmethod
    def _short_voice(name: str) -> str:
        """'ko-KR-Chirp3-HD-Laomedeia' → 'Laomedeia'"""
        return name.split('-')[-1] if '-' in name else name

    def _synthesize_gemini(self, client, tts, spoken: str) -> bytes:
        """Gemini-TTS 로 합성. prompt 필드로 어조를 지시한다."""
        voice = tts.VoiceSelectionParams(
            language_code='ko-KR',
            name=self._short_voice(self.voice),
            model_name=self.model,
        )
        inp = tts.SynthesisInput(text=spoken, prompt=self.style_prompt)
        resp = client.synthesize_speech(
            input=inp, voice=voice,
            audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3),
            timeout=REQUEST_TIMEOUT)
        return resp.audio_content

    # ------------------------------------------------------------ 공개 API
    @property
    def available(self) -> bool:
        return bool(os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'))

    def cached_url(self, name: str) -> str | None:
        """이미 만들어 둔 파일이 있으면 URL을, 없으면 None을 돌려준다.
        API를 호출하지 않으므로 페이지 렌더링을 지연시키지 않는다."""
        if not name or not _SAFE.match(name):
            return None
        path = self._path(name)
        if os.path.exists(path) and os.path.getsize(path) > 900:
            return self._url(name)
        return None

    def url_for(self, name: str) -> str | None:
        """
        풀네임 음성의 URL을 돌려준다.
        파일이 없으면 그 자리에서 생성한다. 실패하면 None.
        """
        if not name or not _SAFE.match(name):
            return None

        path = self._path(name)
        if os.path.exists(path) and os.path.getsize(path) > 900:
            return self._url(name)

        if not self.available:
            return None

        # 같은 이름에 대한 동시 요청이 겹치지 않도록
        with self._lock:
            if os.path.exists(path) and os.path.getsize(path) > 900:
                return self._url(name)
            os.makedirs(os.path.dirname(path), exist_ok=True)   # 태그 폴더 보장
            try:
                audio = self._synthesize(name)
            except Exception as e:
                # 조용히 삼키면 원인을 알 수 없다. 첫 실패는 반드시 알린다.
                self.last_error = f'{type(e).__name__}: {str(e)[:160]}'
                report('TTS synthesis failed (user got no server voice)',
                       level='error', fingerprint=['tts', 'synth-error'],
                       name=name, detail=self.last_error, mode=self.last_mode)
                if not self._err_warned:
                    self._err_warned = True
                    print(f'[TTS] 음성 생성 실패 ({name})')
                    print(f'      {self.last_error}')
                    msg = str(e).lower()
                    if 'permission' in msg or '403' in msg:
                        print('      → 서비스 계정에 Cloud Text-to-Speech 사용 권한이 필요합니다.')
                    elif 'has not been used' in msg or 'disabled' in msg:
                        print('      → Cloud Text-to-Speech API 가 켜져 있는지 확인하세요.')
                    elif 'quota' in msg or 'billing' in msg:
                        print('      → 결제 계정 또는 할당량을 확인하세요.')
                    elif 'deadline' in msg or 'timeout' in msg:
                        print('      → 응답이 늦어 타임아웃되었습니다(REQUEST_TIMEOUT).')
                return None
            if not audio or len(audio) < 900:
                self.last_error = f'생성된 오디오가 너무 작음 ({len(audio) if audio else 0} B)'
                if not self._err_warned:
                    self._err_warned = True
                    print(f'[TTS] {name}: {self.last_error}')
                return None
            tmp = path + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(audio)
            os.replace(tmp, path)

        return self._url(name)

    def stats(self) -> dict:
        try:
            d = os.path.join(self.out_dir, self.tag)
            files = [f for f in os.listdir(d) if f.endswith('.mp3')]
            size = sum(os.path.getsize(os.path.join(d, f)) for f in files)
            return {'count': len(files), 'bytes': size}
        except Exception:
            return {'count': 0, 'bytes': 0}

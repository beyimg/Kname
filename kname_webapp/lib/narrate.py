# -*- coding: utf-8 -*-
"""영상 나레이션 음성 (영어) — Google Cloud TTS, 문장 단위 생성 + 디스크 캐시.

tts_full.py(한국 이름 발음)와 같은 자격증명을 쓴다. 영상 파이프라인(tools/video)이
/api/narrate 로 문장 목록을 보내면 문장마다 mp3 를 만들어 URL 을 돌려준다.

목소리: Gemini-TTS(어조 지시 가능) → 실패하면 Chirp 3: HD 영어 여성 목소리.
환경변수 NARR_VOICE / NARR_MODEL / NARR_STYLE_PROMPT 로 바꿀 수 있다.
같은 문장은 한 번만 합성한다(설정별 태그 폴더 아래 sha1 파일명).
"""
from __future__ import annotations

import hashlib
import os
import re
import threading

REQUEST_TIMEOUT = float(os.environ.get('NARR_TIMEOUT', 20.0))
# Chirp 3: HD 영어 여성 목소리. Gemini-TTS 에서는 접두사를 뗀 'Aoede' 로 쓴다.
DEFAULT_VOICE = os.environ.get('NARR_VOICE', 'en-US-Chirp3-HD-Aoede')
GEMINI_MODEL = os.environ.get('NARR_MODEL', 'gemini-2.5-flash-tts') or None
STYLE_PROMPT = os.environ.get('NARR_STYLE_PROMPT') or (
    "You are narrating a short, upbeat social video. Speak in a friendly, bright, "
    "natural voice — like a warm, curious friend explaining something fun. "
    "Clear and lively, at a comfortable pace, never salesy or robotic. "
    "Korean words are given in romanized form; say them gently and naturally."
)
MAX_CHARS = 400


class Narrator:
    def __init__(self, out_dir: str, voice: str = DEFAULT_VOICE,
                 model: str | None = GEMINI_MODEL, style_prompt: str = STYLE_PROMPT):
        self.out_dir = out_dir
        self.voice = voice
        self.model = model
        self.style_prompt = style_prompt
        self._client = None
        self._lock = threading.Lock()
        self._warned = False
        self.last_error = None
        self.last_mode = None
        sig = f'{voice}|{model}|{style_prompt}'
        self.tag = hashlib.md5(sig.encode('utf-8')).hexdigest()[:8]
        os.makedirs(out_dir, exist_ok=True)

    @property
    def available(self) -> bool:
        return bool(os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'))

    def _get_client(self):
        if self._client is None:
            from google.cloud import texttospeech as tts
            self._client = (tts.TextToSpeechClient(), tts)
        return self._client

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r'\s+', ' ', (text or '')).strip()[:MAX_CHARS]

    def _path(self, text: str) -> str:
        h = hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]
        return os.path.join(self.out_dir, self.tag, f'{h}.mp3')

    def _url(self, text: str) -> str:
        h = hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]
        return f'/static/audio/narr/{self.tag}/{h}.mp3'

    def _synthesize(self, text: str) -> bytes:
        client, tts = self._get_client()
        lang = 'en-US'
        if self.model and self.style_prompt:
            try:
                short = self.voice.split('-')[-1] if '-' in self.voice else self.voice
                voice = tts.VoiceSelectionParams(language_code=lang, name=short, model_name=self.model)
                inp = tts.SynthesisInput(text=text, prompt=self.style_prompt)
                resp = client.synthesize_speech(
                    input=inp, voice=voice,
                    audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3),
                    timeout=REQUEST_TIMEOUT)
                self.last_mode = 'Gemini-TTS'
                return resp.audio_content
            except Exception as e:
                if not self._warned:
                    self._warned = True
                    print(f'[narrate] Gemini-TTS 사용 불가 → Chirp 3 로 대체: {type(e).__name__}: {str(e)[:120]}')
        voice = tts.VoiceSelectionParams(language_code=lang, name=self.voice)
        resp = client.synthesize_speech(
            input=tts.SynthesisInput(text=text), voice=voice,
            audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3),
            timeout=REQUEST_TIMEOUT)
        self.last_mode = 'Chirp 3: HD'
        return resp.audio_content

    def url_for(self, text: str) -> str | None:
        text = self._norm(text)
        if not text:
            return None
        path = self._path(text)
        if os.path.exists(path) and os.path.getsize(path) > 500:
            return self._url(text)
        if not self.available:
            return None
        with self._lock:
            if os.path.exists(path) and os.path.getsize(path) > 500:
                return self._url(text)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                audio = self._synthesize(text)
            except Exception as e:
                self.last_error = f'{type(e).__name__}: {str(e)[:160]}'
                print(f'[narrate] 합성 실패: {self.last_error}')
                return None
            if not audio or len(audio) < 500:
                self.last_error = '생성된 오디오가 너무 작음'
                return None
            tmp = path + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(audio)
            os.replace(tmp, path)
        return self._url(text)

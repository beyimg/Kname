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
# 기본은 Chirp 3: HD(어조 지시는 없지만 짧은 문장도 정확히 그대로 읽는다).
# Gemini-TTS 는 "Taylor Swift becomes" 같은 짧은 조각을 받으면 말을 덧붙이거나 길게 늘여
# 5~6초짜리 클립을 만들었다(2026-09-28). 써 보려면 NARR_MODEL=gemini-2.5-flash-tts.
GEMINI_MODEL = os.environ.get('NARR_MODEL', '') or None
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

    def _key(self, text, lang, voice, prompt):
        # 같은 문장이라도 언어·목소리·어조가 다르면 다른 파일
        extra = '' if (lang == 'en-US' and not voice and not prompt) else f'|{lang}|{voice or ""}|{prompt or ""}'
        return hashlib.sha1((text + extra).encode('utf-8')).hexdigest()[:16]

    def _path(self, key: str) -> str:
        return os.path.join(self.out_dir, self.tag, f'{key}.mp3')

    def _url(self, key: str) -> str:
        return f'/static/audio/narr/{self.tag}/{key}.mp3'

    def _synthesize(self, text: str, lang: str = 'en-US', voice: str | None = None, prompt: str | None = None) -> bytes:
        client, tts = self._get_client()
        vname = voice or self.voice
        style = prompt if prompt is not None else self.style_prompt
        if self.model and style:
            try:
                short = vname.split('-')[-1] if '-' in vname else vname
                voice_p = tts.VoiceSelectionParams(language_code=lang, name=short, model_name=self.model)
                inp = tts.SynthesisInput(text=text, prompt=style)
                resp = client.synthesize_speech(
                    input=inp, voice=voice_p,
                    audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3),
                    timeout=REQUEST_TIMEOUT)
                self.last_mode = 'Gemini-TTS'
                return resp.audio_content
            except Exception as e:
                if not self._warned:
                    self._warned = True
                    print(f'[narrate] Gemini-TTS 사용 불가 → Chirp 3 로 대체: {type(e).__name__}: {str(e)[:120]}')
        # Chirp 3: HD 는 전체 이름('ko-KR-Chirp3-HD-…')이 필요하다
        full = vname if '-' in vname else f'{lang}-Chirp3-HD-{vname}'
        voice_p = tts.VoiceSelectionParams(language_code=lang, name=full)
        resp = client.synthesize_speech(
            input=tts.SynthesisInput(text=text), voice=voice_p,
            audio_config=tts.AudioConfig(audio_encoding=tts.AudioEncoding.MP3),
            timeout=REQUEST_TIMEOUT)
        self.last_mode = 'Chirp 3: HD'
        return resp.audio_content

    def url_for(self, text: str, lang: str = 'en-US', voice: str | None = None, prompt: str | None = None) -> str | None:
        text = self._norm(text)
        if not text:
            return None
        key = self._key(text, lang, voice, prompt)
        path = self._path(key)
        if os.path.exists(path) and os.path.getsize(path) > 500:
            return self._url(key)
        if not self.available:
            return None
        with self._lock:
            if os.path.exists(path) and os.path.getsize(path) > 500:
                return self._url(key)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                audio = self._synthesize(text, lang, voice, prompt)
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
        return self._url(key)

@echo off
chcp 65001 > nul
cd /d "%~dp0"

REM ============================================================
REM  K-Name Generator 실행
REM  이 파일을 더블클릭하면 서버가 켜지고 브라우저가 열립니다.
REM
REM  API 키는 아래 둘 중 한 방법으로 설정하세요.
REM   (A) 시스템에 영구 저장 — 권장. 이 파일을 수정할 필요 없음
REM       PowerShell에서 한 번만:
REM       [Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY","키","User")
REM
REM   (B) 아래 set 줄의 앞 REM 을 지우고 키를 직접 입력
REM       (파일에 키가 남으므로 공유·업로드 주의)
REM ============================================================

REM set ANTHROPIC_API_KEY=sk-ant-여기에키
REM set GOOGLE_APPLICATION_CREDENTIALS=%~dp0gcp-key.json

REM ---- gcp 키 파일이 폴더에 있으면 자동으로 잡아준다 ----
if "%GOOGLE_APPLICATION_CREDENTIALS%"=="" (
  if exist "%~dp0gcp-key.json"      set GOOGLE_APPLICATION_CREDENTIALS=%~dp0gcp-key.json
  if exist "%~dp0gcp-key.json.json" set GOOGLE_APPLICATION_CREDENTIALS=%~dp0gcp-key.json.json
)

echo ============================================================
echo  K-Name Generator
echo ============================================================
if "%ANTHROPIC_API_KEY%"=="" (
  echo  [!] ANTHROPIC_API_KEY 가 설정되지 않았습니다.
  echo      사전에 있는 이름만 변환됩니다.
) else (
  echo  [OK] Anthropic API 키 확인
)
if "%GOOGLE_APPLICATION_CREDENTIALS%"=="" (
  echo  [ ] Google TTS 키 없음 - 음성 재생성 시에만 필요
) else (
  echo  [OK] Google TTS 키: %GOOGLE_APPLICATION_CREDENTIALS%
)
echo ============================================================
echo.
echo  브라우저가 자동으로 열립니다.
echo  종료하려면 이 창에서 Ctrl+C 를 누르세요.
echo.

REM 서버가 뜰 시간을 준 뒤 브라우저 열기
start "" /b cmd /c "timeout /t 3 > nul & start http://localhost:5000"

python app.py

echo.
echo 서버가 종료되었습니다.
pause

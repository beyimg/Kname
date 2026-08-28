@echo off
REM 이 파일을 kname_webapp 폴더에 두고 더블클릭하세요.
REM 예전에 생성된 의미 캐시를 지워, 새 프롬프트(3인칭)와 새 한자(武)로 다시 생성되게 합니다.
cd /d "%~dp0"
del /q meaning_en_cache.json 2>nul
del /q meaning_cache.json 2>nul
echo.
echo [완료] 의미 캐시를 삭제했습니다.
echo   - 음차 캐시(translit_cache.json)는 그대로 두어 재과금을 막습니다.
echo   - 이제 app.py 또는 batch_test.py 를 실행하면 의미가 새로 생성됩니다.
echo.
pause

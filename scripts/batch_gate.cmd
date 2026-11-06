@echo off
REM ===========================================================================
REM batch_gate.cmd - full verification for every batch (framework v1.1 C1)
REM
REM Why: every batch ends with the same gate set. Hand-typed commands drift
REM (this repo shipped "46 violations" in a diagram when the real number was 0,
REM and "42/26/17" for one test count). One command, pinned interpreters,
REM directional anchor - no more "it was green on my machine".
REM
REM Usage:
REM   scripts\batch_gate.cmd                 default (skip graphify)
REM   scripts\batch_gate.cmd --graph         add graphify symbolic re-scan
REM   scripts\batch_gate.cmd --update-anchor rewrite the collect anchor
REM   scripts\batch_gate.cmd --quiet         only print on failure
REM
REM Two interpreters on purpose (repo reality, not tidiness):
REM   PYTEST_PI = Python313 - has pytest, LACKS playwright
REM               (5 e2e modules always fail collection; known baseline)
REM   PROBE_PI  = Python312 - has graphify / PyYAML, runs the gate scripts
REM Pinned by absolute path: the two environments previously produced
REM different results for the same test.
REM ===========================================================================
setlocal enabledelayedexpansion

set "PYTEST_PI=C:\Users\1\AppData\Local\Programs\Python\Python313\python.exe"
set "PROBE_PI=D:\tools\python312\python.exe"
set "ROOT=%~dp0.."
set "ANCHOR=%ROOT%\tests\baselines\collect_anchor.json"
set "FAILED=0"
set "WANT_GRAPH=0"
set "UPDATE_ANCHOR=0"
set "QUIET=0"

for %%a in (%*) do (
    if /I "%%a"=="--graph" set "WANT_GRAPH=1"
    if /I "%%a"=="--update-anchor" set "UPDATE_ANCHOR=1"
    if /I "%%a"=="--quiet" set "QUIET=1"
)

pushd "%ROOT%"

echo.
echo ============================================================
echo  batch_gate  root: %CD%
echo ============================================================

REM --- step 1: collect directional anchor --------------------------------
echo.
echo [1/5] pytest collect anchor
set "COLLECT_OUT=%TEMP%\batch_gate_collect.txt"
"%PYTEST_PI%" -m pytest tests --collect-only -q > "%COLLECT_OUT%" 2>&1

set "COLLECT_LINE="
for /f "tokens=*" %%L in ('findstr /C:"tests collected" "%COLLECT_OUT%"') do set "COLLECT_LINE=%%L"
if not defined COLLECT_LINE (
    echo   [X] could not parse collect output
    set "FAILED=1"
    goto :collect_done
)
REM line shape: "6158 tests collected, 5 errors in 3.62s"
REM   token1=6158  token2=tests  token3=collected,  token4=5  token5=errors
for /f "tokens=1" %%a in ("!COLLECT_LINE!") do set "NCOLLECT=%%a"
for /f "tokens=4" %%a in ("!COLLECT_LINE!") do set "NERRORS=%%a"

if "%UPDATE_ANCHOR%"=="1" (
    > "%ANCHOR%" echo {"min_collected": !NCOLLECT!, "errors_expected": !NERRORS!, "note": "errors are the 5 playwright-dependent e2e modules"}
    echo   [OK] anchor updated: !NCOLLECT! collected / !NERRORS! errors
    goto :collect_done
)

if not exist "%ANCHOR%" (
    echo   [X] anchor missing: %ANCHOR%
    echo       run: scripts\batch_gate.cmd --update-anchor
    set "FAILED=1"
    goto :collect_done
)

set "MINC="
set "MAXE="
for /f "tokens=2 delims=:," %%m in ('findstr /C:"min_collected" "%ANCHOR%"') do set "MINC=%%m"
for /f "tokens=2 delims=:," %%m in ('findstr /C:"errors_expected" "%ANCHOR%"') do set "MAXE=%%m"
set "MINC=!MINC: =!"
set "MAXE=!MAXE: =!"

if !NCOLLECT! LSS !MINC! (
    echo   [X] collect dropped: !NCOLLECT! ^< anchor !MINC!
    set "FAILED=1"
    goto :collect_done
)
if not "!NERRORS!"=="!MAXE!" (
    echo   [X] collect errors changed: !NERRORS! vs anchor !MAXE!
    set "FAILED=1"
    goto :collect_done
)
echo   [OK] collect: !NCOLLECT! tests (anchor !MINC!), !NERRORS! errors (expected !MAXE!)

:collect_done

REM --- step 2: layer direction gate --------------------------------------
echo.
echo [2/5] layer direction gate
"%PROBE_PI%" scripts\arch\check_dangling_imports.py --layers >nul 2>&1
if errorlevel 1 (
    echo   [X] check_dangling_imports --layers FAILED
    "%PROBE_PI%" scripts\arch\check_dangling_imports.py --layers
    set "FAILED=1"
) else (
    echo   [OK] layer direction clean
)

REM --- step 3: path literal gate -----------------------------------------
echo.
echo [3/5] path literal gate
"%PROBE_PI%" scripts\arch\check_path_literals.py >nul 2>&1
if errorlevel 1 (
    echo   [X] check_path_literals FAILED
    "%PROBE_PI%" scripts\arch\check_path_literals.py
    set "FAILED=1"
) else (
    echo   [OK] path literals clean
)

REM --- step 4: capability requires gate (W1) ------------------------------
echo.
echo [4/5] capability requires gate
if exist scripts\arch\check_capability_requires.py (
    "%PROBE_PI%" scripts\arch\check_capability_requires.py >nul 2>&1
    if errorlevel 1 (
        echo   [X] check_capability_requires FAILED
        "%PROBE_PI%" scripts\arch\check_capability_requires.py
        set "FAILED=1"
    ) else (
        echo   [OK] capability requires consistent
    )
) else (
    echo   [SKIP] check_capability_requires.py not present
)

REM --- step 5: semantic placement gate (W1) -------------------------------
echo.
echo [5/5] semantic placement gate
if exist scripts\arch\check_semantics_placement.py (
    "%PROBE_PI%" scripts\arch\check_semantics_placement.py >nul 2>&1
    if errorlevel 1 (
        echo   [X] check_semantics_placement FAILED
        "%PROBE_PI%" scripts\arch\check_semantics_placement.py
        set "FAILED=1"
    ) else (
        echo   [OK] semantic placement consistent
    )
) else (
    echo   [SKIP] check_semantics_placement.py not present
)

REM --- optional: graphify symbolic re-scan --------------------------------
if "%WANT_GRAPH%"=="1" (
    echo.
    echo [bonus] graphify symbolic re-scan
    "%PROBE_PI%" -m graphify extract . --code-only --no-cluster --out tmp\batch_gate >nul 2>&1
    if errorlevel 1 (
        echo   [X] graphify extract FAILED
        set "FAILED=1"
    ) else (
        "%PROBE_PI%" scripts\arch\graph_layer_audit.py tmp\batch_gate\graphify-out\graph.json
    )
)

REM --- verdict ------------------------------------------------------------
echo.
echo ============================================================
if "%FAILED%"=="1" (
    echo  batch_gate: FAIL
    popd
    endlocal
    exit /b 1
)
echo  batch_gate: ALL GREEN
popd
endlocal
exit /b 0

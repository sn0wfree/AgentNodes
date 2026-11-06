@echo off
REM ===========================================================================
REM batch_gate.cmd - one-command gate for a batch (AgentNodes, W1 flow B)
REM
REM Why this exists: every batch ends with the same checks, and hand-typed
REM commands drift (this repo once shipped "46 violations" when the real
REM number was 0, and three different test counts for one tree). Pin the
REM interpreters, pin the anchor, one command.
REM
REM Usage:
REM   scripts\batch_gate.cmd                  default: skip graphify
REM   scripts\batch_gate.cmd --graph          add graphify extract + audit
REM   scripts\batch_gate.cmd --update-anchor  rewrite the collect anchor
REM
REM Two interpreters on purpose - environment reality, not tidiness:
REM   PYTEST_PI (Python 3.13): has pytest, LACKS playwright. The 5 e2e
REM       modules therefore always fail collection. That is the tracked
REM       baseline (errors_expected), so this gate judges the NUMBERS and
REM       deliberately ignores pytest's exit code.
REM   PROBE_PI (Python 3.12): runs the scripts/arch/* gates (parser deps
REM       live in this env).
REM   Both are absolute paths: the two envs once disagreed on the same test.
REM
REM Encoding: this file is pure ASCII on purpose. The Windows console is
REM GBK (cp936); non-GBK characters in a .cmd can abort with
REM UnicodeEncodeError (see scripts/README.md "Windows console encoding").
REM Verdict lines stay ASCII: OK / X / SKIP / ALL GREEN / FAIL.
REM ===========================================================================
setlocal enabledelayedexpansion

REM --- pinned interpreters (dual-track, see header) --------------------------
set "PYTEST_PI=C:\Users\1\AppData\Local\Programs\Python\Python313\python.exe"
set "PROBE_PI=D:\tools\python312\python.exe"

set "ROOT=%~dp0.."
for %%i in ("%ROOT%") do set "ROOT=%%~fi"
set "ANCHOR=%ROOT%\tests\baselines\collect_anchor.json"
set "GRAPH_OUT=%ROOT%\tmp\batch_gate"
set "LOG1=%TEMP%\batch_gate_collect.log"

set "FAILED=0"
set "WANT_GRAPH=0"
set "UPDATE_ANCHOR=0"

for %%a in (%*) do (
    if /I "%%a"=="--graph" set "WANT_GRAPH=1"
    if /I "%%a"=="--update-anchor" set "UPDATE_ANCHOR=1"
)

pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
    echo [X] cannot enter repo root: %ROOT%
    exit /b 1
)

echo.
echo ============================================================
echo  batch_gate  root: %CD%
echo ============================================================

REM --- step 1/6: pytest collect directional anchor -------------------------
echo.
echo [1/7] pytest collect anchor
call :step_collect
if errorlevel 1 set "FAILED=1"

REM --- step 2/6: cross-layer import direction ------------------------------
echo.
echo [2/7] dangling imports + layer direction
call :step_dangling
if errorlevel 1 set "FAILED=1"

REM --- step 3/6: non-import path literals -----------------------------------
echo.
echo [3/7] path literals
call :step_path_literals
if errorlevel 1 set "FAILED=1"

REM --- step 4/6: capability requires (W1, optional while landing) -----------
echo.
echo [4/7] capability requires
call :step_capability
if errorlevel 1 set "FAILED=1"

REM --- step 5/6: semantic placement (W1, optional while landing) ------------
echo.
echo [5/7] semantic placement
call :step_semantics
if errorlevel 1 set "FAILED=1"

REM --- step 6/7: tool name references (batch 4c) ---------------------------
echo.
echo [6/7] tool references
call :step_tool_refs
if errorlevel 1 set "FAILED=1"

REM --- step 7/7: graphify symbolic audit (optional, second dimension) ------
echo.
echo [7/7] graphify symbolic audit ^(optional^)
if "%WANT_GRAPH%"=="1" (
    call :step_graph
    if errorlevel 1 set "FAILED=1"
) else (
    echo   [SKIP] pass --graph to enable
)

REM --- verdict --------------------------------------------------------------
echo.
echo ============================================================
if "%FAILED%"=="1" (
    echo  batch_gate: FAIL
    popd >nul
    endlocal
    exit /b 1
)
echo  batch_gate: ALL GREEN
popd >nul
endlocal
exit /b 0


REM ===========================================================================
REM subroutines (never fall through: main body exits above)
REM every subroutine prints its own [OK]/[X] and returns 0/1 via exit /b
REM ===========================================================================

:step_collect
set "LOG=%LOG1%"
"%PYTEST_PI%" -m pytest tests --collect-only -q > "%LOG%" 2>&1
REM Nonzero exit is EXPECTED here (5 collection errors). Never gate on it.
set "LINE="
for /f "delims=" %%L in ('findstr /r /c:"^[0-9][0-9]* tests collected" "%LOG%"') do set "LINE=%%L"
if not defined LINE (
    echo   [X] collect summary not found - pytest died before reporting
    call :tail "%LOG%" 25
    exit /b 1
)
REM line shape: "6158 tests collected, 5 errors in 3.89s"
REM   The first field after "collected, " is the error count. With 0 errors
REM   pytest omits the whole "N errors" clause, so 0 is the default.
for /f "tokens=1" %%a in ("!LINE!") do set "NCOLLECT=%%a"
set "NERRORS=0"
set "ERRSUB=!LINE:*collected, =!"
if not "!ERRSUB!"=="!LINE!" for /f "tokens=1" %%a in ("!ERRSUB!") do set "NERRORS=%%a"
for /f "delims=0123456789" %%x in ("!NERRORS!") do set "NERRORS="
if not defined NERRORS set "NERRORS=0"

if "%UPDATE_ANCHOR%"=="1" (
    call :write_anchor !NCOLLECT! !NERRORS!
    if errorlevel 1 (
        echo   [X] could not write anchor: %ANCHOR%
        exit /b 1
    )
    echo   [OK] anchor written: !NCOLLECT! collected, !NERRORS! errors
    exit /b 0
)

if not exist "%ANCHOR%" (
    echo   [X] anchor missing: %ANCHOR%
    echo       run first: scripts\batch_gate.cmd --update-anchor
    exit /b 1
)

REM Parse anchor without a JSON dep: pull the number after each key.
REM Works for both compact one-line and pretty-printed JSON.
set "MINC="
set "MAXE="
for /f "usebackq delims=" %%L in ("%ANCHOR%") do (
    set "LN=%%L"
    set "LN=!LN:"=!"
    set "REST=!LN:*min_collected:=!"
    if not "!REST!"=="!LN!" for /f "tokens=1 delims=, " %%v in ("!REST!") do set "MINC=%%v"
    set "REST=!LN:*errors_expected:=!"
    if not "!REST!"=="!LN!" for /f "tokens=1 delims=, " %%v in ("!REST!") do set "MAXE=%%v"
)
REM Reject anything that is not pure digits (guards against stray text).
if defined MINC for /f "delims=0123456789" %%x in ("!MINC!") do set "MINC="
if defined MAXE for /f "delims=0123456789" %%x in ("!MAXE!") do set "MAXE="
if not defined MINC (
    echo   [X] anchor has no usable min_collected: %ANCHOR%
    echo       rewrite it: scripts\batch_gate.cmd --update-anchor
    exit /b 1
)
if not defined MAXE (
    echo   [X] anchor has no usable errors_expected: %ANCHOR%
    echo       rewrite it: scripts\batch_gate.cmd --update-anchor
    exit /b 1
)

if !NCOLLECT! LSS !MINC! (
    echo   [X] collected dropped: !NCOLLECT! ^< anchor min !MINC!
    exit /b 1
)
if not "!NERRORS!"=="!MAXE!" (
    echo   [X] collect errors changed: !NERRORS! vs anchor !MAXE!
    exit /b 1
)
echo   [OK] collect: !NCOLLECT! tests ^(anchor min !MINC!^), !NERRORS! errors ^(expected !MAXE!^)
exit /b 0


:write_anchor
REM %1 = collected, %2 = errors. Only the two numbers are refreshed: an
REM existing "note" is carried over verbatim (it documents WHY the errors are
REM expected), so re-running --update-anchor never erases that history.
REM Written as ONE compact line + CRLF, which is this file's on-disk shape:
REM a refresh with unchanged numbers therefore leaves the working tree clean.
REM The default note uses \u escapes so this .cmd stays pure ASCII while the
REM JSON on disk stays readable UTF-8.
"%PROBE_PI%" -c "import json,os,sys; p=sys.argv[1]; note='errors \u5168\u90e8\u4e3a playwright \u7f3a\u5931'; old=json.load(open(p,encoding='utf-8')) if os.path.exists(p) else {}; note=old.get('note') or note; f=open(p,'w',encoding='utf-8',newline='\r\n'); json.dump({'min_collected': int(sys.argv[2]), 'errors_expected': int(sys.argv[3]), 'note': note}, f, ensure_ascii=False); f.write('\n'); f.close()" "%ANCHOR%" %1 %2
if errorlevel 1 exit /b 1
exit /b 0


:step_dangling
set "LOG=%TEMP%\batch_gate_step2.log"
"%PROBE_PI%" scripts\arch\check_dangling_imports.py --layers > "%LOG%" 2>&1
if errorlevel 1 (
    echo   [X] check_dangling_imports.py --layers: violations found
    call :tail "%LOG%" 25
    exit /b 1
)
echo   [OK] dangling imports: 0
exit /b 0


:step_path_literals
set "LOG=%TEMP%\batch_gate_step3.log"
"%PROBE_PI%" scripts\arch\check_path_literals.py > "%LOG%" 2>&1
if errorlevel 1 (
    echo   [X] check_path_literals.py: problems found
    call :tail "%LOG%" 25
    exit /b 1
)
echo   [OK] path literals: 0
exit /b 0


:step_capability
if not exist "scripts\arch\check_capability_requires.py" (
    echo   [SKIP] check_capability_requires.py not present
    exit /b 0
)
set "LOG=%TEMP%\batch_gate_step4.log"
"%PROBE_PI%" scripts\arch\check_capability_requires.py > "%LOG%" 2>&1
if errorlevel 1 (
    echo   [X] check_capability_requires.py: problems found
    call :tail "%LOG%" 25
    exit /b 1
)
echo   [OK] capability requires: consistent
exit /b 0


:step_semantics
if not exist "scripts\arch\check_semantics_placement.py" (
    echo   [SKIP] check_semantics_placement.py not present
    exit /b 0
)
set "LOG=%TEMP%\batch_gate_step5.log"
"%PROBE_PI%" scripts\arch\check_semantics_placement.py > "%LOG%" 2>&1
if errorlevel 1 (
    echo   [X] check_semantics_placement.py: problems found
    call :tail "%LOG%" 30
    exit /b 1
)
echo   [OK] semantic placement: consistent
exit /b 0


:step_tool_refs
if not exist "scripts\arch\check_tool_refs.py" (
    echo   [SKIP] check_tool_refs.py not present
    exit /b 0
)
set "LOG=%TEMP%\batch_gate_step6.log"
"%PROBE_PI%" scripts\arch\check_tool_refs.py > "%LOG%" 2>&1
if errorlevel 1 (
    echo   [X] check_tool_refs.py: violations found
    call :tail "%LOG%" 30
    exit /b 1
)
echo   [OK] tool refs: consistent
exit /b 0


:step_graph
set "LOG=%TEMP%\batch_gate_graphify.log"
if exist "%GRAPH_OUT%" rmdir /s /q "%GRAPH_OUT%" >nul 2>&1
"%PROBE_PI%" -m graphify extract . --code-only --no-cluster --out "%GRAPH_OUT%" > "%LOG%" 2>&1
if errorlevel 1 (
    echo   [X] graphify extract failed
    call :tail "%LOG%" 20
    exit /b 1
)
set "GRAPH_JSON=%GRAPH_OUT%\graphify-out\graph.json"
if not exist "%GRAPH_JSON%" (
    echo   [X] graphify produced no graph.json at %GRAPH_JSON%
    call :tail "%LOG%" 20
    exit /b 1
)
"%PROBE_PI%" scripts\arch\graph_layer_audit.py "%GRAPH_JSON%"
if errorlevel 1 (
    echo   [X] graph_layer_audit.py failed
    exit /b 1
)
echo   [OK] graph audit complete
exit /b 0


:tail
REM Print the last N lines of a log so a failure comes with its own summary.
setlocal enabledelayedexpansion
set "F=%~1"
set "N=%~2"
if not exist "!F!" (
    echo   [X] no log at !F!
    endlocal
    exit /b 0
)
set "TOT=0"
for /f %%c in ('find /c /v "" ^< "!F!" 2^>nul') do set "TOT=%%c"
set /a "SKIP=!TOT!-!N!"
if !SKIP! lss 0 set "SKIP=0"
more +!SKIP! "!F!" 2>nul
endlocal
exit /b 0

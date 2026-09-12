@echo off
REM ============================================================
REM  Manimatic - start the API service (double-click to run).
REM  Requires: D:\Miniconda\envs\manim
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .bat in the OEM
REM  codepage (GBK here), not UTF-8 - Chinese comments get mangled and
REM  the script dies with confusing "not a command" errors. Chinese
REM  docs belong in HANDOFF.md, not in .bat files.
REM
REM  Switches below:
REM    MSB_LLM_PROFILE        which profile in llm.local.json (relay/direct)
REM                           empty = use the file's "active"
REM    MSB_LLM_SHOW_THINKING  1 = also push model reasoning to the frontend
REM                           (thinking_delta). Needed for a "thinking..."
REM                           indicator. Shows raw reasoning, so it is opt-in.
REM    MSB_LLM_REPLAY         1 = replay mode: reuse logged replies, no API
REM                           call (saves money when iterating)
REM ============================================================

cd /d D:\Manimatic\MathStoryboard
set PY=D:\Miniconda\envs\manim\python.exe
set PYTHONIOENCODING=utf-8

REM ---- LLM switches ----
REM empty = use llm.local.json "active" (currently: relay)
set MSB_LLM_PROFILE=
REM push reasoning as thinking_delta so the UI can show progress
set MSB_LLM_SHOW_THINKING=1
REM 1 = replay from output/_llm/calls.jsonl instead of calling the API
set MSB_LLM_REPLAY=0

echo ============================================
echo  Manimatic API   http://localhost:8000/docs
echo  profile=%MSB_LLM_PROFILE%  (empty = llm.local.json active)
echo  show_thinking=%MSB_LLM_SHOW_THINKING%
echo ============================================
echo.

%PY% -m uvicorn api.main:app --host 127.0.0.1 --port 8000

REM pause so double-clicked windows stay open long enough to read errors
pause

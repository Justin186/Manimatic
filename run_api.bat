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
REM
REM    MSB_VI_ALLOW           auto | always | never   (image input, default auto)
REM                           auto   = reject models known to be text-only,
REM                                    let the API decide for the rest
REM                           always = never gate locally (use when you know
REM                                    the model reads images but its name is
REM                                    not in the allow pattern)
REM                           never  = reject all image input
REM    MSB_VI_PROFILE         *** READ THIS IF IMAGE UPLOAD IS REJECTED ***
REM                           Which profile in llm.local.json is used for
REM                           reading images. Empty = reuse the active one.
REM                           The currently active profile is "direct"
REM                           (deepseek-chat, the cheap text-only one), which
REM                           CANNOT read images. Setting this to "relay"
REM                           keeps storyboard generation on the cheap model
REM                           and only switches to the vision-capable one for
REM                           the image request - so you are not paying relay
REM                           prices for text-only rounds.
REM    MSB_VI_MODEL           same idea, but pins a model name instead
REM    MSB_VI_MAX_IMAGES      max images per request (default 4)
REM    MSB_VI_ALLOW_URL       1 = also accept {"url": "https://..."} images
REM                           (default 0: base64 only, keeps the SSRF surface
REM                           closed)
REM
REM  Image input lives in storyboard/vision.py + POST /api/chat's optional
REM  "images" field. There is no plugin directory any more.
REM
REM ---- accounts / auth (api/auth/) ----
REM  With auth on, EVERY /api/** endpoint needs a login. Means:
REM  the frontend MUST talk to the real backend (NEXT_PUBLIC_USE_MOCK=false),
REM  otherwise it hits its own mock routes and never sees this service.
REM
REM    MSB_AUTH_ENABLED     1 = on (default). 0 = NO auth at all: every
REM                         endpoint is wide open. Debugging only.
REM    MSB_AUTH_ROOT_EMAIL  the pre-created super admin. Default below.
REM    MSB_AUTH_ROOT_PASSWORD
REM                         password for that account. If left empty the
REM                         service generates one and prints it ONCE in the
REM                         startup banner - write it down, it is not stored
REM                         in any recoverable form.
REM    MSB_AUTH_INVITE      registration needs this code. Empty = sign-up is
REM                         CLOSED (missing value must mean "deny").
REM    MSB_AUTH_SESSION_DAYS  how long a login lasts (default 14).
REM    MSB_AUTH_ADOPT_LEGACY
REM                         1 (default) = on first run, move the sessions
REM                         that were created BEFORE accounts existed into
REM                         the super admin's namespace. Set 0 to skip.
REM
REM  NOTE: the legacy sessions currently live in the shared namespace, so
REM  after your first login you will see them only if adopt ran (it prints
REM  how many items it moved).
REM ============================================================

cd /d D:\Manimatic\MathStoryboard
set PY=D:\Miniconda\envs\manim\python.exe
set PYTHONIOENCODING=utf-8

REM ---- LLM switches ----
REM empty = use llm.local.json "active" (currently: direct)
set MSB_LLM_PROFILE=
REM push reasoning as thinking_delta so the UI can show progress
set MSB_LLM_SHOW_THINKING=1
REM 1 = replay from output/_llm/calls.jsonl instead of calling the API
set MSB_LLM_REPLAY=0

REM ---- image input switches (storyboard/vision.py) ----
REM auto = blacklist known text-only models, let the API decide the rest
set MSB_VI_ALLOW=auto

REM *** Left empty: read images with whatever profile is active. ***
REM This used to be "relay" because deepseek-chat was believed to be text-only.
REM That belief was WRONG - measured on 2026-09-17: deepseek-chat reads images
REM fine (0.8s, correctly read "x^2 - 5x + 6 = 0" from a photo). The blacklist
REM in storyboard/vision.py was the thing blocking uploads, not the model.
REM Set this to a profile name only if the active model genuinely cannot read
REM images (you will get a readable error saying so).
set MSB_VI_PROFILE=

set MSB_VI_MAX_IMAGES=4

REM ---- accounts / auth (api/auth/) ----
REM 1 = every /api/** endpoint requires a login. 0 = no auth at all.
set MSB_AUTH_ENABLED=1
REM super admin, created on startup if missing (its password is NEVER reset
REM after that - change it from the admin panel, not from here)
set MSB_AUTH_ROOT_EMAIL=admin@example.com
REM leave empty = generate a random one and print it once in the banner
set MSB_AUTH_ROOT_PASSWORD=
REM registration code. Empty = sign-up closed (admin creates accounts).
set MSB_AUTH_INVITE=
REM 1 = move pre-account sessions into the super admin's namespace (once)
set MSB_AUTH_ADOPT_LEGACY=1

echo ============================================
echo  Manimatic API   http://localhost:8000/docs
echo  profile=%MSB_LLM_PROFILE%  (empty = llm.local.json active)
echo  show_thinking=%MSB_LLM_SHOW_THINKING%
echo  vi_allow=%MSB_VI_ALLOW%  vi_profile=%MSB_VI_PROFILE%  vi_max_images=%MSB_VI_MAX_IMAGES%
echo  auth=%MSB_AUTH_ENABLED%  root=%MSB_AUTH_ROOT_EMAIL%  register=%MSB_AUTH_INVITE%
echo  endpoints: POST /api/chat (accepts optional "images")
echo ============================================
echo.

%PY% -m uvicorn api.main:app --host 127.0.0.1 --port 8000

REM pause so double-clicked windows stay open long enough to read errors
pause

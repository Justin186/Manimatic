@echo off
REM MathStoryboard - render all 15 test problems
REM Double-click to run. Requires: D:\Miniconda\envs\manim

cd /d D:\Manimatic\MathStoryboard
set PY=D:\Miniconda\envs\manim\python.exe
set PYTHONIOENCODING=utf-8

echo ============================================
echo  Rendering 15 test problems
echo  Output: D:\Manimatic\MathStoryboard\output\videos\
echo ============================================
echo.

for %%f in (
  lim_taylor
  integral_interval_swap
  a1_cubic_monotonic
  a2_exp_intersect
  a3_quad_closed_interval
  b1_transform_quadratic
  b2_transform_sine
  b3_transform_exp_flip
  c2_lim_exp
  c3_lim_tan_sin
  d2_integral_x2
  d3_integral_sin_half
  e1_induction_sum
  e2_product_derivative
  e3_quad_inequality
) do (
  echo ---------- %%f ----------
  %PY% generate.py examples\%%f.json --resolution 1280,720 --fps 24
  echo.
)

echo ============================================
echo  Done. Check output\videos\ for MP4 files.
echo ============================================
pause

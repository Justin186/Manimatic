# -*- coding: utf-8 -*-
"""让 tests/ 能 import storyboard（仓库根目录加进 sys.path）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# -*- coding: utf-8 -*-
"""
运行时版面护栏 `_fit()` 的行为测试 —— **不 import manim**（毫秒级）。

为什么单独盯它：2026-09-17 用户反馈"有时候渲染出来的字特别小"，根因就在这个函数。
它当时按"以元素中心为对称轴、只准待在 12.4 × 6.6 安全框内"算可用空间，
于是元素被 `place` 贴到画面边上时（`{"edge":"down","buff":0.5}` 这类写法，
示例里到处都是），可用空间被算成负数，缩放系数炸掉：

    buff ≤ 0.7 必命中 —— buff=0.4 → 缩到 0%（字直接消失）、0.5 → 0%、0.6 → 48%

而 `place` 的 buff 是**作者显式要求的边距**，不是错误；校验层也没错
（那些文字远在 12.4 × 6.6 之内，`metrics` 放行是对的）。是护栏自己把
"居中内容的容量预算"当成了"画面有多大"，重复扣了一次边距。

现在的口径：`_fit` 只保证**不出画**，预算只用来给居中内容封顶。
下面几条断言分别钉住这个口径的每一面，防止哪天又被"顺手收紧"回去。
"""
import re

import pytest

from storyboard import dsl

_SRC = dsl.RUNTIME_HELPER
_FN = re.search(r"def _fit\(m.*?\n    return m\n", _SRC, re.S)
assert _FN, "RUNTIME_HELPER 里找不到 _fit（改了名字或结构？同步改这个测试）"


def _const(name):
    return float(re.search(name + r" = ([\d.]+)", _SRC).group(1))


class _FakeConfig:
    frame_width = 14.2222
    frame_height = 8.0


class _FakeMobject:
    """
    只实现 `_fit` 真正用到的那几个成员：get_center / width / height / scale。

    刻意不去起 manim：这里要测的是**护栏的算术口径**，不是 manim 的排版；
    真排版由 `test_metrics_calibration.py`（真渲染，默认跳过）负责。
    """

    def __init__(self, w, h, cx=0.0, cy=0.0):
        self.width, self.height = float(w), float(h)
        self._c = [float(cx), float(cy)]

    def get_center(self):
        return self._c

    def scale(self, s):
        # manim 的 scale 绕中心缩放，中心不动 —— 这正是"中心已出画就救不回来"的根源
        self.width *= s
        self.height *= s
        return self


@pytest.fixture
def fit():
    ns = {"config": _FakeConfig(),
          "_FRAME_MARGIN_X": _const("_FRAME_MARGIN_X"),
          "_FRAME_MARGIN_Y": _const("_FRAME_MARGIN_Y"),
          "_FIT_MIN_SCALE": _const("_FIT_MIN_SCALE"),
          "_FIT_WARNED": set()}
    exec(_FN.group(0), ns)
    return ns


def _scaled(fit, m, name="t"):
    """返回 (缩放后 / 缩放前)。"""
    w0 = m.width
    fit["_fit"](m, name)
    return m.width / w0


# --------------------------------------------------------------- 老行为不变
def test_centered_content_still_uses_the_budget(fit):
    """居中内容的容量还是 12.4 × 6.6 —— 老分镜的观感一个字都不能变。"""
    m = _FakeMobject(20.0, 0.4)          # 比画面还宽，必须缩到预算
    assert _scaled(fit, m) == pytest.approx(12.4 / 20.0, rel=1e-6)

    small = _FakeMobject(3.0, 0.4)
    assert _scaled(fit, small) == pytest.approx(1.0)


def test_centered_content_within_frame_is_never_touched(fit):
    """居中且没超预算 → 一次都不该 scale（哪怕只缩 0.1%）。"""
    m = _FakeMobject(12.4, 6.6)
    assert _scaled(fit, m) == 1.0


# --------------------------------------------------------------- 本次的回归
@pytest.mark.parametrize("w,h,cy,label", [
    (3.98, 0.39, -3.21, "edge=down buff=0.6"),
    (3.98, 0.39, -3.31, "edge=down buff=0.5"),
    (3.98, 0.39, -3.41, "edge=down buff=0.4"),
    (1.98, 0.39, -3.41, "corner=dr buff=0.4"),
    (2.78, 0.39, -0.00, "edge=left buff=0.6(横向)"),
    (9.88, 0.32, -3.41, "edge=down buff=0.2 的长字幕"),
])
def test_edge_placed_elements_are_not_shrunk(fit, w, h, cy, label):
    """
    贴边摆放的元素**不该被缩小**：buff 是作者要的边距，不是溢出。

    这些中心坐标是从真实产物（output/_parts/storyboard/）里量出来的。
    改之前它们分别被缩到 48% / 0% / 0% / 0% / 77% / 0%。
    """
    m = _FakeMobject(w, h, cx=0.0, cy=cy)
    assert _scaled(fit, m) == 1.0, f"{label} 被缩小了 —— 护栏又把预算当成画面尺寸了"


def test_left_edge_horizontal_placement_is_not_shrunk(fit):
    """`to_edge(LEFT)` 把元素贴到左边缘，横向同样不该被缩。"""
    m = _FakeMobject(2.78, 0.39, cx=-5.12, cy=0.0)
    assert _scaled(fit, m) == 1.0


# --------------------------------------------------------------- 该缩还是要缩
def test_element_wider_than_frame_is_shrunk_into_the_frame(fit):
    """真的出画了还是要缩 —— 中心在画面内时，缩到画面之内。"""
    m = _FakeMobject(20.0, 0.4, cx=0.0, cy=0.0)
    fit["_fit"](m, "t")
    assert m.width <= 12.4 + 1e-6


def test_near_edge_oversized_element_is_shrunk(fit):
    """
    贴边 + 太长：可用空间 = 自身 + 2*buff，所以缩到"刚好不越过画面边"即可，
    不能像以前那样一路缩到 0。
    """
    m = _FakeMobject(30.0, 0.4, cx=0.0, cy=-3.31)   # 下边缘 buff=0.5
    fit["_fit"](m, "t")
    assert m.width > 1.0, "缩得过头了"
    assert m.height > 0.0


# --------------------------------------------------------------- 缩不回来的情况
def test_center_outside_frame_warns_instead_of_scaling_to_zero(fit, capsys):
    """
    中心已经在画面外 → 绕中心缩小**救不回来**，硬缩只会把整句变没。
    必须只告警、不缩放，并在告警里说清"改 place"。
    """
    m = _FakeMobject(4.0, 0.4, cx=0.0, cy=-5.0)
    assert _scaled(fit, m) == 1.0
    out = capsys.readouterr().out
    assert "中心落在画面外" in out
    assert "place" in out


def test_min_scale_floor_prevents_invisible_text(fit, capsys):
    """病态输入（比如负 buff / shift 出画面）最多缩到下限，且告警里要点明到了下限。"""
    m = _FakeMobject(200.0, 0.4, cx=0.0, cy=0.0)
    fit["_fit"](m, "t")
    assert m.width / 200.0 == pytest.approx(_const("_FIT_MIN_SCALE"), rel=1e-6)
    assert f"{round(100 * _const('_FIT_MIN_SCALE'))}% 下限" in capsys.readouterr().out


# --------------------------------------------------------------- 告警本身
def test_warning_is_actionable_and_deduped(fit, capsys):
    """告警要能自证（原始尺寸 / 画面尺寸），且同一元素只喊一次。"""
    for _ in range(3):
        fit["_fit"](_FakeMobject(20.0, 0.4), "same_id")
    out = capsys.readouterr().out
    assert out.count("[WARN]") == 1, "同一元素重复告警（always_redraw 每帧都会调 _fit）"
    assert "原始" in out and "画面 14.22 × 8.00" in out


def test_no_name_means_no_warning(fit, capsys):
    """没给名字时不该打告警（手工调用 / 老代码路径）。"""
    fit["_fit"](_FakeMobject(20.0, 0.4))
    assert capsys.readouterr().out == ""

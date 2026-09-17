# -*- coding: utf-8 -*-
"""
三维（ThreeDAxes / Surface / 空间曲线 / 相机旋转）的回归测试。

背景
----
2026-09-17 第一次把 3D 接进 DSL。接的时候踩到三个坑，每个都留了定点用例：

1. **参数曲线一直是坏的（既有 bug，被这次 3D 顺带照出来）**
   `parametric` 的自由变量叫 `t`，而运行时是 `_safe_eval(expr, x, **extra)` ——
   它只把第二个位置参数绑到名字 **x** 上。于是 `cos(t)` 在运行时 NameError，
   被兜底成 0.0，**整条曲线塌成一个点**：画面能出、内容全错，一声不响。
   更早的一层是校验层先拒（`_FREE_VARS` 之前不允许 t），所以这条从未被跑通过。
   两个 bug 叠在一起，症状是"模型怎么改都画不出参数曲线"。

2. **z 会被 2D 坐标系默默吃掉**
   `Axes.coords_to_point` 用的是 `zip(other_axes, coords[1:], strict=False)`
   （manim 0.21 coordinate_systems.py:2183）：2D 的 Axes 只有两条轴，
   第三个坐标**直接被丢掉**。所以"曲面挂在 2D axes 上""点写 z 但 ref 是 2D axes"
   这两类必须在校验期拒绝，否则画出来看着像对、其实是错的。

3. **值域顶出坐标系 = 直接飞出画面**
   实测：曲面 z 到 9.7、而 z_range 只到 7，一个"碗"顶满并冲出屏幕上沿。
   现在 `_check_3d` 会在九个采样点上算值域，报错里直接给出"range 该放宽到多少"。

另外两条是**结构断言**（不做渲染，纯字符串检查，跑得快）：

   · 纯 2D 分镜仍然继承 `Scene`（源码逐字节不变 → 增量渲染的缓存判据不受影响）；
     含 3D 的才继承 `ThreeDScene`。
   · 三维分镜里写了 `place` 的文字/公式会被登记为"屏幕固定"（HUD 字幕），
     否则它会跟着视角一起被投影成斜的、甚至转出画面（实测过）。
"""

import pytest

from storyboard import renderer, schema, templates

REG = templates.TEMPLATE_REGISTRY

AX2 = {"id": "ax", "kind": "axes"}
AX3 = {"id": "ax3", "kind": "three_axes", "z_range": [-1, 6, 1]}


def sb(elements, timeline=None):
    """单分镜脚手架。"""
    return {
        "title": "3d 测试",
        "intent": "propose",
        "scenes": [{"id": 1, "duration": 4.0, "elements": elements,
                    "timeline": timeline or [
                        {"do": "create", "target": elements[-1]["id"]},
                        {"do": "wait", "time": 1.0}]}],
    }


def ok(elements, timeline=None):
    return schema.validate(sb(elements, timeline), REG)


def bad(elements, timeline=None):
    with pytest.raises(Exception) as e:
        schema.validate(sb(elements, timeline), REG)
    return str(e.value)


# ==============================================================================
# 1. 参数曲线的自由变量（既有 bug 的定点用例）
# ==============================================================================
def test_parametric_accepts_t():
    """`cos(t)` 必须过校验 —— 它是 ELEMENT_SPEC 里 fx/fy 的**默认值**。"""
    out = ok([AX2, {"id": "c", "kind": "parametric", "axes": "ax",
                    "fx": "cos(t)", "fy": "sin(t)"}])
    assert out["scenes"][0]["elements"][1]["fx"] == "cos(t)"


def test_parametric_generated_code_binds_t():
    """
    生成代码必须把自由变量 t **真的传进** `_safe_eval`。

    这是本次修的那个静默 bug：只传位置参数时，命名空间里只有 x，
    表达式里的 t 会 NameError → 兜底 0.0 → 曲线塌成一个点。
    """
    src = renderer.render(ok([AX2, {"id": "c", "kind": "parametric", "axes": "ax",
                                    "fx": "cos(t)", "fy": "sin(t)"}]),
                          use_latex=False)
    assert "t=t" in src, "自由变量 t 没有传进 _safe_eval（曲线会塌成一个点）"
    assert "_safe_eval(r\"\"\"cos(t)\"\"\", t, t=t)" in src


def test_space_curve_binds_t():
    src = renderer.render(ok([AX3, {"id": "c", "kind": "space_curve", "axes": "ax3",
                                    "fx": "cos(t)", "fy": "sin(t)", "fz": "t"}]),
                          use_latex=False)
    assert "t=t" in src


def test_surface_binds_y():
    """曲面 z=f(x,y)：y 也要喂进去（x 走位置参数，恰好对）。"""
    src = renderer.render(ok([AX3, {"id": "s", "kind": "surface", "axes": "ax3",
                                    "expr": "x**2 + y**2",
                                    "u_range": [-1, 1, 0.2],
                                    "v_range": [-1, 1, 0.2]}]),
                          use_latex=False)
    assert "y=v" in src


def test_other_kinds_still_reject_y():
    """`y` 只在曲面里合法：plot 的 expr 写 y 是写错，必须照旧拒绝。"""
    msg = bad([AX2, {"id": "p", "kind": "plot", "axes": "ax", "expr": "y**2"}])
    assert "未定义" in msg and "'y'" in msg


# ==============================================================================
# 2. z 只能挂在三维坐标系上（2D 的 Axes 会静默丢掉第三个坐标）
# ==============================================================================
def test_surface_requires_three_axes():
    msg = bad([AX2, {"id": "s", "kind": "surface", "axes": "ax",
                     "expr": "x**2 + y**2"}])
    assert "three_axes" in msg


def test_point_z_requires_three_axes():
    msg = bad([AX2, {"id": "d", "kind": "dot",
                     "at": {"ref": "ax", "x": 1, "y": 1, "z": 3}}],
              [{"do": "show", "target": "d"}])
    assert "three_axes" in msg


def test_point_z_on_three_axes_ok():
    out = ok([AX3, {"id": "d", "kind": "dot",
                    "at": {"ref": "ax3", "x": 1, "y": 1, "z": 2}}],
             [{"do": "show", "target": "d"}])
    el = out["scenes"][0]["elements"][1]
    assert el["at"] == {"ref": "ax3", "x": 1.0, "y": 1.0, "z": 2.0}


def test_rotate_camera_needs_three_axes():
    msg = bad([AX2, {"id": "d", "kind": "dot", "at": [1, 1]}],
              [{"do": "show", "target": "d"},
               {"do": "rotate_camera", "theta": 30}])
    assert "rotate_camera" in msg


# ==============================================================================
# 3. 值域顶出坐标系 → 直接飞出画面，必须在校验期报掉
# ==============================================================================
def test_surface_out_of_range_is_rejected():
    msg = bad([AX3, {"id": "s", "kind": "surface", "axes": "ax3",
                     "expr": "x**2 + y**2",
                     "u_range": [-2.2, 2.2, 0.2], "v_range": [-2.2, 2.2, 0.2]}])
    assert "z_range" in msg
    assert "放宽" in msg, "报错必须给出改法（该把 range 改成多少）"


def test_surface_in_range_ok():
    ok([AX3, {"id": "s", "kind": "surface", "axes": "ax3",
              "expr": "x**2 + y**2",
              "u_range": [-1.7, 1.7, 0.2], "v_range": [-1.7, 1.7, 0.2]}])


def test_surface_with_tracker_skips_range_check():
    """
    含 tracker 的表达式值域在构建期未知 —— 必须**跳过**检查。

    否则就是误报（把正确的分镜拒掉、白烧一轮 LLM 重试），比漏报更贵。
    """
    ok([AX3, {"id": "t", "kind": "tracker", "value": 1},
        {"id": "s", "kind": "surface", "axes": "ax3", "expr": "t*x**2",
         "u_range": [-5, 5, 0.2], "v_range": [-5, 5, 0.2]}],
       [{"do": "fade_in", "target": "s"}])


def test_rotate_camera_needs_an_angle():
    msg = bad([AX3], [{"do": "rotate_camera"}])
    assert "theta" in msg and "phi" in msg


# ==============================================================================
# 4. 场景基类与"屏幕固定"文字（结构断言，不渲染）
# ==============================================================================
def test_2d_storyboard_uses_plain_scene():
    src = renderer.render(ok([AX2, {"id": "p", "kind": "plot", "axes": "ax",
                                    "expr": "x**2"}]),
                          use_latex=False)
    assert "class StoryboardScene(Scene):" in src
    assert "ThreeDScene" not in src


def test_3d_storyboard_uses_three_d_scene():
    src = renderer.render(ok([AX3, {"id": "s", "kind": "surface", "axes": "ax3",
                                    "expr": "x**2 + y**2",
                                    "u_range": [-1, 1, 0.2],
                                    "v_range": [-1, 1, 0.2]}]),
                          use_latex=False)
    assert "class StoryboardScene(ThreeDScene):" in src
    # 初始视角：不拨相机的话 ThreeDScene 默认正俯视，曲面看起来就是一张平面图
    assert "set_camera_orientation" in src


def test_3d_text_with_place_is_fixed_in_frame():
    """三维分镜里写了 place 的文字 = HUD 字幕，必须登记成屏幕固定。"""
    src = renderer.render(ok([
        AX3,
        {"id": "lab", "kind": "text", "content": "标题", "place": [{"corner": "ur"}]},
    ], [{"do": "write", "target": "lab"}]), use_latex=False)
    assert "add_fixed_in_frame_mobjects(_e_lab)" in src


def test_2d_text_is_not_fixed_in_frame():
    """纯 2D 分镜一个字节都不该多 —— 老分镜的产物必须原样。"""
    src = renderer.render(ok([AX2, {"id": "lab", "kind": "text", "content": "标题",
                                    "place": [{"corner": "ur"}]}],
                             [{"do": "write", "target": "lab"}]),
                          use_latex=False)
    assert "fixed_in_frame" not in src


def test_rotate_camera_emits_degrees():
    src = renderer.render(ok([AX3], [{"do": "rotate_camera", "theta": 30, "phi": 70}]),
                          use_latex=False)
    assert "self.move_camera(theta=30 * DEGREES, phi=70 * DEGREES" in src

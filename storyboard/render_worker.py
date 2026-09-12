"""分镜渲染 worker：一组分镜**共用一个媒体目录**，依次渲染，各自输出 mp4。

背景（2026-09-12 实测，见 README「分镜级渲染」）：
    8 分镜 / 480p15 三条路径实测 ——
        单进程整条渲染    12.2s
        8 个分镜 8 个进程  18.0s   ← 最慢
        进程内依次连渲    11.7s（热媒体目录） / 15.6s（每个分镜一个全新目录）
    瓶颈是**内存带宽**（单进程 35 GB/s，8 进程并行时每个只剩 7.2 GB/s，
    整机约 58 GB/s 封顶），所以多开进程不会多出算力。

本 worker 在"进程内连渲"的基础上再做两件事：
  1. **整组共用一个媒体目录** —— manim 只初始化一次目录结构与 tex 缓存
     （每个分镜一个全新目录要多付 ~0.4s/分镜）
  2. 配合 generate.py 把整组源码合并后**一次性**预热 LaTeX
     （原来是每个分镜各编译一遍公式，8 个分镜要 5.4s，合并后约 1s）

因为共用目录，每次渲染出来的 `StoryboardScene.mp4` 会被下一个分镜覆盖，
所以**渲完必须立刻搬走**到各自的目标路径（`out_mp4`）。

协议：每个分镜渲完往 stdout 打一行 `@@ {json}`，父进程按行读取即可拿到
「渲好一段推一段」的时机（将来接 SSE 直接转发即可）。
"""

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time

QUALITY_MAP = {"l": "low_quality", "m": "medium_quality", "h": "high_quality"}


def build_config(quality, resolution, fps, media_dir):
    cfg = {
        "media_dir": media_dir,
        "disable_caching": True,
        "progress_bar": "none",
        "verbosity": "ERROR",
    }
    if resolution:
        width, height = resolution.split(",")
        cfg["pixel_width"] = int(width)
        cfg["pixel_height"] = int(height)
        if not fps:
            # 必须和 generate.py::quality_tag() 的 "低分辨率时默认 30fps" 对齐，
            # 否则 manim 写出的目录名（720p30）和父进程期望的（720p15）对不上
            cfg["frame_rate"] = 30
    else:
        cfg["quality"] = QUALITY_MAP.get(quality, "low_quality")
    if fps:
        cfg["frame_rate"] = int(fps)
    return cfg


def render_one(py_path, render_dir, out_mp4, quality_dir, quality, resolution, fps):
    """渲染单个分镜场景并把它搬到 out_mp4，返回渲染耗时（秒）。

    ⚠️ manim 的 Python API（`Scene.render()`）把成片写在
        `{render_dir}/videos/{quality_dir}/StoryboardScene.mp4`
    （CLI 模式才会多一层模块名目录）。因为整组共用 render_dir，
    这个文件会被下一个分镜覆盖，所以必须当场搬走。
    """
    from manim import tempconfig

    spec = importlib.util.spec_from_file_location("sb_scene", py_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    started = time.time()
    with tempconfig(build_config(quality, resolution, fps, render_dir)):
        module.StoryboardScene().render()
    elapsed = time.time() - started

    produced = os.path.join(render_dir, "videos", quality_dir, "StoryboardScene.mp4")
    if not os.path.exists(produced):
        raise RuntimeError("未找到渲染产物: %s" % produced)
    os.makedirs(os.path.dirname(out_mp4), exist_ok=True)
    shutil.move(produced, out_mp4)
    return elapsed


def main():
    parser = argparse.ArgumentParser(description="分镜渲染 worker（共用媒体目录 + 进程内连渲）")
    parser.add_argument("--tasks", required=True,
                        help='JSON：[[场景 .py 路径, 渲染目录, 分镜序号, 产物目标路径], ...]')
    parser.add_argument("--quality-dir", required=True,
                        help="manim 的质量子目录名，如 480p15")
    parser.add_argument("--quality", default="l", choices=["l", "m", "h"])
    parser.add_argument("--resolution", default="", help='"宽,高"，给了就覆盖 quality')
    parser.add_argument("--fps", type=int, default=0)
    ns = parser.parse_args()

    tasks = json.loads(ns.tasks)
    resolution = ns.resolution or None
    fps = ns.fps or None
    started = time.time()

    for py_path, render_dir, index, out_mp4 in tasks:
        try:
            elapsed = render_one(py_path, render_dir, out_mp4, ns.quality_dir,
                                 ns.quality, resolution, fps)
            event = {"i": index, "ok": True, "elapsed": round(elapsed, 2)}
        except Exception as exc:  # 单个分镜失败不能带崩整组
            event = {"i": index, "ok": False,
                     "error": "%s: %s" % (type(exc).__name__, exc)}
        print("@@ " + json.dumps(event, ensure_ascii=False), flush=True)

    print("@@ " + json.dumps({"worker_done": True,
                              "elapsed": round(time.time() - started, 2)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

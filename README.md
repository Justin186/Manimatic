# Manimatic · 渲染服务

> 仓库名 `MathStoryboard`。它是 **Manimatic** 的后端：把「分镜 JSON」确定性地渲染成数学讲解动画。

> **前端配套项目在 `../web`（Next.js，GitHub 名 `manimatic-web`）** ——
> SSE 契约见 `docs/前端接入-后端改造清单.md`，产品形态与决策见 `docs/Manimatic-前端架构设计.md`。
> **接手请先读 `HANDOFF.md`**（前端 → 后端的交接文档）。
>
> ⚠️ **`docs/` 三份已有副本在前端仓库 `../web/docs/`** —— 它们内容面向前端，随前端仓库一起分发。
> 两边并存、需手工同步：**契约（SSE 事件 / 产物格式 / 交付顺序）以本仓库为准，
> 前端架构决策与改造章节以 `web/docs/` 为准**。改任何一份，请顺手改另一份。

**核心思想**：不让大模型直接写 Manim 代码，而是让它输出受约束的分镜 JSON，
由确定性渲染器翻译成代码。这样语法错误为 0、API 版本冲突为 0。

分镜有**两种写法**：

| 写法 | 大模型要做什么 | 组合空间 | 适用 |
|---|---|---|---|
| **DSL 式（推荐）** | 自由取元素 + 自由排时间线 | 26 种元素 × 30 种动作 × 任意数量，无上限 | 所有题，画面不重样 |
| **模板式（兼容旧分镜）** | 从 9 个模板里选一个填参数 | 9 个模板 × 各自几个参数 | 老 examples，不再新增 |

> 模板式的问题是画面千篇一律：想做「曲线上有个动点滑动，切线跟着转，右上角实时显示斜率」
> 这种联动，5 个老模板里没有一个能做。DSL 式就是为解掉这个枷锁来的。

> **项目知识库**（决策依据、踩坑清单、给后续 AI 的提示词模板）：见 `PROJECT_KNOWLEDGE.md`，**接手这个项目先读这一份**。

---

## 一、环境配置

### 1. conda 环境（已就绪：`manim`）

环境**已经建好了**，直接用，不用重建：

```bash
D:/Miniconda/envs/manim/python.exe generate.py examples/quadratic_transform.json
```

| 项 | 值 |
|---|---|
| 环境名 | `manim` |
| Python | **3.12.14** |
| Manim CE | **0.21.0** |
| 路径 | `D:\Miniconda\envs\manim\python.exe` |

Miniconda 基础环境是 Python 3.14，**太新**，Manim 依赖有兼容问题 —— 所以渲染一律走这个环境，不要用 base。

<details>
<summary>万一环境丢了，重建方式</summary>

```powershell
D:\Miniconda\Scripts\conda.exe create -n manim python=3.12 -y
D:\Miniconda\Scripts\conda.exe activate manim
pip install -r requirements.txt
```
</details>

### 2. FFmpeg（必须）

```powershell
# 有 chocolatey：
choco install ffmpeg
# 或手动下载 https://ffmpeg.org/download.html 解压后把 bin 目录加进 PATH
```

### 3. LaTeX（必需）

公式渲染需要 LaTeX。**没有它也能跑**（会自动退化成纯文本），但**数学讲解视频里 LaTeX 是必需的**——见第四节实测对比。

**推荐 MiKTeX（约 2GB，Windows 上最省事）：**
- 下载：https://miktex.org/download （选 Windows Installer）
- 安装完把 `D:\MiKTeX\miktex\bin\x64` 加进系统 PATH
- **关键设置**：MiKTeX Console → Settings → "Always install missing packages on-the-fly"（避免首次渲染时弹窗卡死）

> 本 demo 已为你处理 MiKTeX 装好但不在 PATH 的情况：见 `storyboard/latex_env.py`，它会扫描常见安装路径并自动注入，**不用手动配 PATH**。
>
> 中文用的是 Manim 的 `Text`（走 Pango 渲染），**不需要装 ctex**。
> 只有 `MathTex` 数学公式需要 LaTeX，且**禁止在 formula/label 里写中文**（schema 会拒绝）——中文说明放 caption 字段。

### 4. 自检

```bash
python check_env.py
```

会检查 Python 版本、manim、numpy、ffmpeg、LaTeX、dvisvgm、中文字体，
最后跑一个 `SquareToCircle` 级别的 smoke test 并**输出渲染耗时**——这是你估算出片时间的基准。

### 5. 测试

```bash
python -m pytest tests -q        # 不到 1 秒
```

守住的是「**规范化必须幂等**」这条性质：`schema.validate(validate(raw))` 必须与
`validate(raw)` 完全一致。它为什么值得一条测试：一旦不成立，现象是
**两个闸对同一份数据给出相反结论**（日志说"通过校验"、接口说"校验失败"），
历史上 HANDOFF §8.6 / §8.7 两个静默失效 bug 都是它的破坏，人工排查极慢。

另外两块：
- `tests/test_parallel.py` —— `parallel` 真并行。其中一条会**真的用 manim 渲一遍**
  来验证"一条 play 里多个不同时长的动画，总时长等于 max"这个假设
  （这是整个并行实现的地基，manim 升级改了就会红）。
  想跳过这类渲染测试用 `MSB_SKIP_RENDER_TESTS=1`。
- 测试还盯住了一个很隐蔽的坑：`.animate.scale(x).set_run_time(0.4)` 会被 manim
  **静默忽略**（`set_run_time` 不在 `Mobject` 上），必须写成
  `.animate(run_time=0.4).scale(x)`。

**改了 `storyboard/dsl.py` 的 `_norm_*`、`schema.validate()` 或 `_anim*` 就请跑一遍。**

---

## 二、运行

```bash
# 只生成代码不渲染（最快验证链路）
python generate.py examples/quadratic_transform.json --dry-run

# 实际渲染（低质量 480p，最快）
python generate.py examples/quadratic_transform.json

# 中等质量 720p
python generate.py examples/quadratic_transform.json -q m

# 导数几何意义
python generate.py examples/derivative_meaning.json

# 演示校验层如何拦截错误
python generate.py examples/_bad_case_demo.json
```

输出视频在 `output/` 目录。

### 题目直接出片（LLM 接入）

不想手写分镜 JSON：把题目丢给大模型，它只输出**受 Schema 约束的分镜 JSON**，
过校验后由确定性渲染器翻译成 Manim —— 大模型一个字符的代码都不写（决策 1 没被破）。

```bash
# 1) 配一次密钥（三选一）
set MSB_API_KEY=sk-xxx            # 环境变量
# 或写 llm.local.json（已在 .gitignore）：{"provider":"deepseek","model":"deepseek-chat","api_key":"sk-xxx"}
# 或每次带：--api-key sk-xxx

# 2) 题目 → 视频
python generate.py --problem "求 f(x)=x^3-3x 的极值" -q m
python generate.py --problem-file 题目.txt --resolution 1280,720 --fps 24
```

厂商预设 `deepseek`（默认）/ `qwen` / `glm` / `moonshot` / `openai`；
换厂商就是 `--provider qwen --model qwen-plus`，也可以 `--base-url` 接任意 OpenAI 兼容接口。

**关键设计：校验失败会把错误回灌给模型重试。**
"引用了没声明的 id"、"颜色拼错"、"duration 越界"这类错误，校验层能精确定位，
于是把报错原样发回去让模型改 —— 重试不是碰运气，是**把校验层的确定性注入模型的下一轮**。
`--attempts 4` 控制轮数上限，用尽则明确报错并保留每轮记录。

生成的分镜落在 `output/<名字>_storyboard.json`：
**想改画面就手改这份 JSON，再当普通分镜重跑** —— 这正是"分镜可编辑"的产品价值。

### 渲染速度与画质选择（2026-09-10 实测，本机 20 核）

**先说结论：帧率比分辨率更花钱，分辨率几乎免费。**

| 配置 | 耗时 | 说明 |
|---|---|---|
| 720p30 | 23.1 s | 实机实测 |
| **720p24** | **19.1 s** | 实机实测 —— **推荐**，帧数少 20%，耗时少 17% |
| 720p15 | ≈ 13 s | 按下面的模型推算 |

> 以上为 `free_product_rule.json`（8 分镜 / 71 秒成片）在**本机 VSCode 终端实机**测得。
> ⚠️ 在 AI 沙箱 / 被代理的 shell 里测会虚高约 50%（固定开销被放大到淹没信号，
> 表现为"改帧率几乎没差别"），**性能数据一律以实机为准**。

由这两点可反推出成本模型：

```
每帧 ≈ 9.4 ms   固定开销 ≈ 3.1 s    帧渲染占总耗时 87%
```

所以降帧率是线性有效的（帧数减半，渲染时间近似减半），而降分辨率几乎白送。

```bash
# 推荐：720p24，兼顾画质与速度
python generate.py examples/free_derivative.json --resolution 1280,720 --fps 24

# 还想更快：首次渲染加 --fast（60s → 39s）
python generate.py examples/free_derivative.json --fast --resolution 1280,720 --fps 24

# 反复调同一份分镜：靠增量缓存，重跑只要 0.2 s
python generate.py examples/free_derivative.json --split
```

### 分镜级渲染：默认路径就边渲边切，不需要 `--parallel`

产品需要"每个分镜一个 mp4"（「渲好一段推一段」「单分镜失败只重跑那一段」都靠它）。
**但不需要为此把场景拆开** —— manim 的 partial 片段本来就是渲染过程中逐个写出来的，
只要在分镜边界把它们当场合并，就能做到"这一段渲完立刻出片"：

```python
class StoryboardScene(Scene):
    def section(self, n):          # 分镜边界（renderer 生成时插入）
        if n > 1:
            self._emit(n - 1)      # 把上一段的 partial 合并成 mp4 + 打一行 @@ {json}
        super().next_section("scene_%d" % n)
```

`StoryboardScene` 因此在渲染过程中持续输出：

```
@@ {"section": 1, "video": ".../sections/StoryboardScene_0000_scene_1.mp4", "duration": 5.0}
@@ {"section": 2, "video": ".../sections/StoryboardScene_0001_scene_2.mp4", "duration": 7.333}
...
```

父进程逐行读 stdout 就能**实时**把每段推给前端。整条成片由 manim 正常输出，
不需要 `--save_sections`（那条路要等 `finish()` 才一次性切，拿不到实时时机）。

同一份 `free_product_rule.json`（8 分镜，480p15，20 核 Intel Ultra 7 255HX）实测：

| 路径 | 总耗时 | 首段可见 | 产物 |
|---|---|---|---|
| **默认（一个 Scene + 边渲边切）** | **13.7 s** | **2.3 s** | 整条 + 8 段 + 真实时长 |
| 默认 + `--save_sections`（finish 才切） | 12.9 s | 12.3 s | 整条 + 8 段 |
| 默认（完全不分段） | 12.9 s | — | 1 个 mp4 |
| 拆分渲染（8 个独立场景） | 16.1 s | 1.6 s | 8 段 |
| 旧 `--parallel`（8 分镜 × 8 进程） | 23.8 s | ~18 s | 8 段 |

**只比"完全不分段"多 0.8 s，就换来 2.3 s 的首段可见时间**（vs 12.3 s），
而且每段时长是**按真实帧数**算的（`5.0`、`7.333`、`11.599`…），比大纲估算值准。

> 合并用的是 manim 自己的 `combine_files()`（PyAV remux，不重编码），
> 所以很快；它的代价主要在 `join_all_encode_jobs()` 会等当前在途编码收尾。
> 若以后要再压这 0.8 s，可以把合并丢到后台线程，别阻塞渲染主循环。

#### 为什么"拆场景 + 多进程"全是弯路

拆成 N 个独立场景 = N 次渲染器初始化 + N 次 `combine_to_movie`（每次把同名 mp4
重写一遍）；"一个 Scene 演到底"这些只做一次，所以**拆开只会更慢**。

多进程更没必要 —— 瓶颈是**内存带宽**，不是 CPU：

```
单进程内存拷贝吞吐        35.1 GB/s
8 进程并行（每个进程）     7.2 GB/s     ← 整机约 58 GB/s 就封顶
```

manim 渲染是"逐帧生成 numpy 数组 + cairo 像素缓冲 + PIL 图像"的内存密集型负载。
8 个进程并行时整机带宽只从 35 涨到 ~58 就封顶，每个进程只能分到 7.2 GB/s，
于是**每个都慢 4~5 倍**，20 个核在这种负载上帮不上忙。

> 旁证：8 进程并行做纯计算每个都能满速；8 进程并行写 3200 个小文件 0.35 s；
> 8 进程并行 import manim 1.14 s。CPU、磁盘、模块导入都不是瓶颈，唯独渲染慢。

历史教训（2026-09-12 一轮排查）：先试过"每分镜一个进程"，再试过"拆分场景 + 进程内连渲"，
两版都比默认路径慢；后来才想到去翻 `scene_file_writer.py`，发现 partial 片段是渲染中
逐个产出的。**遇到同类问题先读一遍 manim 的源码，再动手造轮子。**

#### 仍然保留的开关

```bash
python generate.py examples/free_product_rule.json                  # 默认：整条 + 分镜片段（推荐）
python generate.py examples/free_product_rule.json --split          # 每个分镜独立渲染（调试单个分镜用，较慢）
python generate.py examples/free_product_rule.json --split --jobs 2 # 分 2 组并行渲染
```

`--split` 走 `storyboard/render_worker.py`：把分镜分成 `--jobs` 组，每组一个进程、
组内共用渲染目录、源码合并预热，产物落在 `_parts/<name>/segments/`。
它比默认路径慢，只在"想单独调某个分镜"或将来"单分镜渲染极重、值得并行"时才有用。

### 头条：LaTeX 批处理预热（默认开启）

```
清缓存后首次渲染：  60.0 s  →  12.9 s     快 4.7 倍
```

Manim 每个公式都单独跑一遍 `latex` + `dvisvgm`，而一次调用的约 400ms 几乎全是
**固定初始化开销**（加载 TeX 引擎与宏包），跟公式复杂度无关。
实测 28 个公式：逐个编译 **39 s**，塞进一个多页 tex 只编译一次 **1.4 s**。

`storyboard/tex_batch.py` 在渲染前把所有公式（含坐标轴刻度数字）一次性编译掉，
再按 Manim 自己的 hash 规则填进缓存，渲染时直接命中。

```bash
python generate.py examples/free_derivative.json --no-prewarm   # 关掉它做对比
```

**`--fast` 现在是可选的了**：它把刻度数字改用 `Text` 渲染（刻度只是 0/1/2，
用不着 LaTeX 排版），能再省一点；但预热处理刻度之后两者差别已经不大。
出片时去掉 `--fast`，刻度会恢复成数学字体。

### 关于 GPU：这条路走不通，别再试了

已实测三条路，全部无效（数据说话）：

| 尝试 | 结果 |
|---|---|
| `--renderer=opengl` | 拿到的是 **Intel 核显**（不是 RTX）。耗时 18.3s vs Cairo 17.1s —— **更慢** |
| 强制用 N 卡 | `glcontext` 只打包了 Windows WGL 后端，没有 egl/osmesa 可选，程序层面无法指定 GPU |
| NVENC 硬件编码 | 可用，但 0.38s vs CPU 0.30s —— 更慢，文件还大 5.5 倍；编码只占总耗时 0.7% |

**根本原因**：把像素量提高 4.3 倍（480p15→720p30），耗时只涨 12%。
说明光栅化只占总耗时约 12% —— 这就是 GPU 加速的理论上限，而实际用核显还会倒亏。
真正的开销在 **LaTeX 编译** 和 **Python 侧的 mobject 计算**，两者都是纯 CPU。
（LaTeX 那部分已经用批处理预热解决掉了，见上一节。）

> ⚠️ 2026-09-12 更正：mobject 计算虽然是纯 CPU，但**也不是靠堆核能加速的** ——
> 它是内存带宽受限的（单进程 35 GB/s，8 进程并行时每个只剩 7.2 GB/s，整机 ~58 GB/s 封顶）。
> 也就是说这个 workload 对"多核"和"GPU"都不买账，见下面「分镜级渲染」一节。

---

## 三、项目结构

```
D:\Manimatic\
├── MathStoryboard\        ← 本仓库（Python 渲染服务）
│   ├── HANDOFF.md         ⭐ 交接文档（前端 → 后端，先读这份）
│   ├── PROJECT_KNOWLEDGE.md  ⭐ 项目知识库（团队交付必带，含踩坑清单、决策依据）
│   ├── docs/              # 设计文档：前端架构 / 后端改造清单 / 实时交付契约
│   │                      # ⚠️ 副本已分发到 ../web/docs/，改动需两边同步
│   ├── storyboard/
│   │   ├── dsl.py         # ⭐ 元素库 + 动作库 + 代码构建器（DSL 写法的核心）
│   │   ├── templates.py   # 9 个旧模板（确定性代码，兼容旧分镜，不再新增）
│   │   ├── schema.py      # 分镜 JSON 校验，自动识别 DSL / 模板两种写法
│   │   ├── renderer.py    # JSON → Manim 源码（sections=True 时边渲边切）
│   │   ├── render_worker.py # 仅服务于 --split（调试单分镜），不是主路径
│   │   ├── llm.py         # ⭐ LLM 接入：prompt + OpenAI 兼容调用 + 校验失败回灌重试
│   │   ├── tex_batch.py   # ⭐ LaTeX 批处理预热（清缓存首渲 60s → 12.9s）
│   │   └── latex_env.py   # MiKTeX/TeX Live 自动探测 + PATH 注入
│   ├── examples/          # 分镜 JSON（free_*.json 是 DSL 写法；a1~e3 是 15 题测试集）
│   ├── output/            # 自动生成的 .py / 分镜片段（_parts）/ mp4（videos）
│   ├── eval/              # 测试题集 + 判对判错清单 + Bug 清单
│   ├── tests/             # 回归测试（规范化幂等；改动 dsl/schema 后请跑 pytest）
│   ├── generate.py        # 主入口（--spec 可打印给大模型看的 DSL 规格）
│   ├── check_env.py       # 环境自检 + MathTex 实测渲染
│   ├── run_all.bat        # 一键渲染 15 题测试集
│   └── requirements.txt
└── web\                   ← git repo B（Next.js 前端，GitHub 名 manimatic-web）
```

---

## 四、这个 demo 验证了什么

| 验证点 | 怎么验证 |
|---|---|
| 分镜 JSON → Manim 代码 → MP4 全链路可行 | `python generate.py examples/quadratic_transform.json` |
| 语法错误为 0 | 代码由模板生成，不经过大模型 |
| 校验层能拦下静默失败 | `python generate.py examples/_bad_case_demo.json` |
| 真实渲染耗时 | `python check_env.py` 输出 smoke test 耗时 |

跑 `_bad_case_demo.json` 会依次拦下这 5 类错误（每次只报第一个，修掉再跑看下一个）：

1. **未知模板** `cool_3d_explosion` —— 模型发明了不存在的模板
2. **危险表达式** `__import__('os').system(...)` —— 代码注入
3. **坐标范围非法** `[10, -10]` —— max 小于 min
4. **非法颜色 / 未知参数** `CHARTREUSE`、`camera_angle` —— 会被过滤或拒绝
5. **时长越界** `999` —— 超出 [0.5, 12]

**这些错误如果不拦，要么崩溃，要么生成一个"看起来正常但内容全错"的视频——后者就是静默失败。**

---

## 五、DSL 写法：元素 + 时间线

一个分镜 = **elements（画面上有什么）+ timeline（按什么顺序动）**。

```json
{
  "id": 2,
  "elements": [
    { "id": "ax", "kind": "axes", "x_range": [-1.5, 3.5, 1], "y_range": [-1, 8, 1] },
    { "id": "t",  "kind": "tracker", "value": -1.0 },
    { "id": "g",  "kind": "plot", "axes": "ax", "expr": "x**2", "color": "PRIMARY" },
    { "id": "p",  "kind": "dot", "at": { "ref": "ax", "x": "$t", "y": "t**2" } },
    { "id": "tan","kind": "line",
      "start": { "ref": "ax", "x": "$t - 1.1", "y": "t**2 - 2*t*1.1" },
      "end":   { "ref": "ax", "x": "$t + 1.1", "y": "t**2 + 2*t*1.1" }, "color": "RED" }
  ],
  "timeline": [
    { "do": "create", "target": "ax" },
    { "do": "create", "target": "g", "run_time": 1.6 },
    { "do": "show", "target": ["p", "tan"] },
    { "do": "tracker_to", "target": "t", "to": 3.0, "run_time": 7.5, "rate_func": "smooth" }
  ]
}
```

一个 tracker 同时驱动动点、切线和斜率数字 —— 这是旧模板做不到的联动。

**元素库 29 种**：`text` `formula` `axes` `three_axes` `number_plane` `plot` `parametric`
`area` `riemann` `surface` `space_curve` `dot` `line` `arrow` `circle` `ellipse` `rect`
`polygon` `angle` `brace` `table` `cell_box` `legend` `highlight` `tangent_line`
`normal_line` `number` `tracker` `group`

**动作库 31 种**：`create` `write` `fade_in` `grow` `draw_border` `show` `transform`
`replace` `indicate` `circumscribe` `flash` `wiggle` `focus` `fade_out` `remove`
`shift` `move_to` `move_cells` `scale` `rotate` `set_color` `set_opacity` `set_stroke` `stretch`
`move_along` `trace` `tracker_to` `rotate_camera` `wait` `clear_all` `parallel`

**三维（2026-09-17 接入）**：`three_axes` 空间坐标系 + `surface`（z=f(x,y)）+ `space_curve`
（参数曲线）+ `rotate_camera`（转视角）。含三维元素的分镜自动继承 `ThreeDScene`
（纯 2D 分镜仍是 `Scene`，生成源码逐字节不变，增量渲染缓存不受影响）；
写了 `place` 的文字/公式自动当"屏幕字幕"固定在画面上，不随视角转。
验收样例见 `examples/f1_space_surface.json`。

完整规格不用手写 —— `storyboard/dsl.py` 里 `ELEMENT_SPEC` / `ACTION_SPEC` 一份定义，
校验和文档都从它生成：

```bash
python generate.py --spec              # 打印给大模型看的 DSL 规格，直接塞进 prompt
python generate.py --dump-prompt       # 导出模型**实际收到**的完整提示词（含示例与重试消息）
                                       # → output/prompt_dump.md，只看不跑、不调模型
```

### 同一位置换内容：`replace` 的四种过渡

底部字幕/逐句解释这类「位置不变、内容换掉」的地方**必须用 `replace`**（另 `create`
一个新 text 会和旧的叠在一起，见 `HANDOFF.md` §8.33）。过渡效果可选：

```json
{"do": "replace", "target": "info",
 "into": {"id": "i2", "kind": "text", "content": "下一句提示"},
 "effect": "slide", "run_time": 1.2}
```

| effect | 效果 | 用在哪 |
|---|---|---|
| `crossfade`（默认，不写就是它） | **严格先后、零重叠**：旧的在**前半段**淡尽，新的**后半段**才淡入 | 字幕、说明文字 |
| `slide` | 旧的向上淡出、新的从下方顶上来 | 有"换下一条"方向感的场合 |
| `write` | 旧的淡出、新的逐字写出 | 想让观众读一遍新文字 |
| `morph` | 形变（旧字飞向新字的位置再变成新字） | 图形/几何体的变形，**别用在字幕上** |

> `crossfade` 为什么不"交叉"：底部字幕是**居中**的、前后两句字数常常不同，
> 两句话只要同时可见就会叠在一起读不出字。所以两半各占 `run_time` 的一半、**完全不重叠**。
> 觉得切换太快就加大 `run_time`（默认 1.6）。

### 版面尺寸：估算而不是数条数

元素会不会出界，由 `storyboard/metrics.py` 按**内容 + 字号 + 行列结构**估算，
估算明显超出画面（`12.4 × 6.6`）才拒绝，报错里带上「宽了多少、减几列、字号降到多少」。

**没有**「表格最多 8 行 8 列」「格号最多 6 个」「单元格最多 80 字」这类与尺寸无关的硬上限
（2026-09-16 删掉；80 字那条还是**静默改内容**）。

表格的格子尺寸也可以直接调：

```json
{"id": "arr", "kind": "table", "rows": [["0","1"],["2","3"]],
 "cell_w": 1.35, "cell_h": 0.95, "pad_x": 0.35}
```

- `cell_w` / `cell_h`：所有格子统一的宽/高（含留白）。不写就由内容撑出——
  注意 manim 默认 `h_buff=1.3` 会让每个格子**凭空宽 1.3 个单位**，8 列光留白就吃掉 10.4。
- `pad_x` / `pad_y`：内容到格线的留白（默认 1.3 / 0.8）。给了 `cell_w` 而没给 `pad_x` 时，
  默认换成紧凑值 0.35 —— 否则「1.2 宽的格子」永远做不到（下限是 内容+1.3）。
- 一个都不写时生成代码与改动前**逐字节相同**。

### 跨分镜延续：`carry`

**默认每个分镜都从空白画面开始** —— 分镜边界会把上一镜的东西全部淡出（见 `renderer.py`
的 `_CLEAR`），而元素是每分镜重新构建的。所以同一个东西在下一镜里只能**重画一遍**，
观感上等于「从头再来」。讲连贯过程（二分查找、排序、迭代收敛）时很别扭。

要让一个东西跨分镜留着，在**后续分镜**的 elements 里这样写：

```json
{ "id": "arr", "carry": true }
```

意思：`arr` 在上一分镜结束时就在屏上，本分镜**不要重画它、也不要清掉它**；
之后照常用动作动它（`move_cells` / `move_to` / `set_color` / `indicate`…）。

三条硬校验（少一条就是一种静默失效）：

1. **分镜 1 不许写 `carry`** —— 没有上一镜可承接。
2. **必须在上一镜结束时真的在屏上** —— 被 `create/write/fade_in/grow/draw_border/show`
   上过屏，且之后没被 `fade_out/remove/clear_all` 清掉。
   （声明了却从没被动作点过的元素**不算**在屏上。）
3. **依赖必须一起承接** —— 只承接 `plot` 不承接 `axes`，边界清屏会把坐标系清掉、
   只剩一个飘着的图。

`carry` 里**不能再写 kind / 参数**（内容沿用上一镜的声明）—— 要改画面请用动作。
另外**不做"自动识别"**（同 id 同声明就自动延续）：两段独立例题都用 `ax`/`plot` 时会被误判，
`create` 动画凭空消失。详见 `HANDOFF.md` §8.32。

### 跑两个新示例

```bash
python generate.py examples/free_derivative.json     # 导数几何意义（动点 + 切线 + 实时斜率）
python generate.py examples/free_pythagorean.json    # 勾股定理（多边形 + 直角标记 + 填充）
```

`free_pythagorean.json` 这类几何题，是 5 个老模板**完全覆盖不到**的题型。

---

## 六、实测结果（2026-09-08，本机环境）

环境：`Python 3.12.14 / manim 0.21.0 / numpy 2.5.3 / ffmpeg(D:\ffmpeg) / MiKTeX 26.5.x`。

### 6.1 不含 LaTeX（公式降级为纯文本）

| 项目 | 分镜数 | 渲染耗时 | 平均每分镜 | 产物大小 |
|---|---|---|---|---|
| smoke test（单个圆） | 1 | **2.1 s** | — | — |
| quadratic_transform | 5 | **8.8 s** | 1.8 s | 0.41 MB |
| derivative_meaning | 4 | **8.1 s** | 2.0 s | 0.25 MB |

### 6.2 含 LaTeX（真公式渲染）

| 项目 | 分镜数 | 渲染耗时 | 平均每分镜 |
|---|---|---|---|
| quadratic_transform | 5 | **15.6 s** | 3.1 s |
| derivative_meaning | 4 | **15.3 s** | 3.8 s |

启用 LaTeX 慢 4–5×（每分镜多 1–2 秒），但仍然远低于 60s 目标。**数学讲解视频里 LaTeX 是必需品**。

### 三个结论

1. **渲染比预估快得多。** 我之前按公开资料估的是「-ql 每场景 5–30 秒」，本机实测**每分镜 1.8–3.8 秒**。一段 5 分镜的视频 15 秒出片——**现场实时生成完全可行**。
2. **没 LaTeX 时坐标刻度直接崩。** Manim 的坐标轴刻度数字走 `MathTex`，没 LaTeX 时整个渲染直接崩。模板里已做降级：检测不到 LaTeX 就自动关掉 `include_numbers`。
3. **降级版公式会显示原始 LaTeX 命令。** `y = x^2 - 4x + 4,\quad \text{顶点}(2,0)` 会原样显示成一串字符。**已修复**：在 schema 里禁止 label/formula_latex 含中文，需要中文放 caption。

### 6.3 MathTex 首次 vs 二次渲染

- 首次：50.7s（含宏包格式文件生成）
- 二次：8.9s（缓存命中）

首次只跑一次，后续都是快速路径。

---

## 七、还没做的部分

**接前端的三块空白：三块都已完成**（细节见 `HANDOFF.md` §2.3 / §3.1）

1. **HTTP 服务层** ✅ —— `api/`（FastAPI + SSE）：`/api/chat`、`/api/render/confirm`、
   `/api/health`，外加会话持久化与 `/api/llm/*`（模型档案切换）。
2. **进度事件流** ✅ —— `Popen` 逐行读 manim 打出的 `@@ {json}` → `tool_result` 事件，
   整条链路走 SSE，前端能显示"正在生成第 3/5 个分镜"。
   > 分镜**产物**也不愁：默认路径就产出 `sections/*.mp4` 与带**真实时长**的
   > `sections/StoryboardScene.json`（见「分镜级渲染」一节），前端拿它铺时间轴。
3. **分阶段 LLM 输出** ✅ —— `stream_storyboard()` 用 `BriefTap` 从**流式** JSON 里
   边到边抠出 `brief`（1~2 秒就有字可显示），随后发 `plan`（大纲）。
   **不需要把一次 LLM 调用拆成两次。**

**其它待办**

- Docker 沙箱（执行生成的代码必须隔离，方案评估文档里有配置要点）
- 元素库扩容：DSL 路线下**不再新增模板**，缺的题型（几何证明、向量变换、数列）
  应该通过补 `dsl.py` 的元素/动作来覆盖

**已降级 / 搁置**

- ~~接入百度「试卷切题识别」OCR~~ —— **不再是核心路径**。定位收敛为
  "**题目 → 讲解动画**"，入口以**文本题目**为主（`--problem`），
  拍照识题只是便捷入口，可有可无，不为它排期。

> 前端选型已于 2026-09-11 定为 **Web 优先（React 生态）+ Capacitor 出包**，
> 不再走 uni-app / HBuilderX / 若依那条线。

**项目背景与决策依据**：见 `PROJECT_KNOWLEDGE.md`（项目知识库）。
**完整方案设计**：见工作区目录 `赛道三-讲解视频动画方案评估.md` 和 `赛道三-方案澄清-产品形态与链路决策.md`。

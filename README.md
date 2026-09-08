# MathStoryboard

把「分镜 JSON」确定性地渲染成数学讲解动画的 demo。

**核心思想**：不让大模型直接写 Manim 代码，而是让它输出受约束的分镜 JSON，
由固定模板翻译成代码。这样语法错误为 0、API 版本冲突为 0、布局不会越界。

> **项目知识库**（决策依据、踩坑清单、给后续 AI 的提示词模板）：见 `PROJECT_KNOWLEDGE.md`，**接手这个项目先读这一份**。

---

## 一、环境配置

### 1. 创建 conda 环境（Python 3.11）

你的 Miniconda 基础环境是 **Python 3.14，太新**，Manim 依赖可能有兼容问题，所以必须单独建环境。

**PowerShell：**
```powershell
D:\Miniconda\Scripts\conda.exe create -n mathstory python=3.11 -y
D:\Miniconda\Scripts\conda.exe activate mathstory
cd D:\MathStoryboard
pip install -r requirements.txt
```

**Git Bash：**
```bash
source /d/Miniconda/etc/profile.d/conda.sh
conda create -n mathstory python=3.11 -y
conda activate mathstory
cd /d/MathStoryboard
pip install -r requirements.txt
```

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

---

## 三、项目结构

```
MathStoryboard/
├── PROJECT_KNOWLEDGE.md   ⭐ 项目知识库（团队交付必带，含 5 节踩坑清单、决策依据）
├── storyboard/
│   ├── templates.py     # 5 个 Manim 代码模板（确定性代码，大模型碰不到）
│   ├── schema.py        # 分镜 JSON 校验（拦截越界/非法值/危险表达式/含中文 label）
│   ├── renderer.py      # JSON → Manim 源码（含 LaTeX/CJK 自动分流）
│   └── latex_env.py     # MiKTeX/TeX Live 自动探测 + PATH 注入
├── examples/            # 分镜 JSON 样例
├── output/              # 生成的 .py 和 mp4
├── generate.py          # 主入口
├── check_env.py         # 环境自检 + MathTex 实测渲染
└── requirements.txt
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

## 五、当前只实现了 5 个模板

`title_card` / `axes_plot` / `function_transform` / `summary_card` / `step_card`

按方案建议，起始 5 个模板应为：函数变换、几何证明、数列求和、导数几何意义、向量变换。
**下一步是补齐几何证明和向量变换两个模板**——照着 `templates.py` 里现有模板的写法加即可，
每个模板约 20 行。

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

- LLM 接入（目前分镜 JSON 是手写的）—— 接上后就是完整链路
- Docker 沙箱（执行生成的代码必须隔离，方案评估文档里有配置要点）
- 前端页面 / uni-app / 若依接入
- 补 2 个新模板（几何证明、向量变换）
- 接入百度「试卷切题识别」OCR（题目输入）

**项目背景与决策依据**：见 `PROJECT_KNOWLEDGE.md`（项目知识库）。
**完整方案设计**：见工作区目录 `赛道三-讲解视频动画方案评估.md` 和 `赛道三-方案澄清-产品形态与链路决策.md`。

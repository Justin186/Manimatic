# MathStoryboard 项目知识库

> **目的**：让任何人在 5 分钟内 get up to speed——不用翻聊天记录，不用问"之前为什么这么定"。

最后更新：2026-09-08 23:15 (D+1)

---

## 一、项目一句话

**拍题 → 分镜 → Manim 动画**，把"讲解题"从静态文字/语音升级为可编辑的推导视频。
核心定位：**学生自拍错题 → AI 输出解题步骤 + Manim 动画**。

---

## 二、关键决策与依据（含推翻的判断）

### 决策 1：走「分镜 JSON + 模板渲染器」，不让 LLM 写 Manim 代码

**结论**：大模型只产出受 Schema 约束的 JSON，确定性 Python 模板翻译成 Manim 代码。

**三条理由**（任何一条都足够否决直接生成代码）：
1. **静默失败**：ManiBench 评测显示「代码能跑 ≠ 画对」——公式下标错、坐标越界、动画顺序颠倒全部零报错。
2. **版本陷阱**：Manim GL→CE 有 145 个不兼容 API。`ShowCreation` 在 CE 已删，模型很常见用旧 API。
3. **模型代际不是关键**：ManiBench 2026-02 评测时 66.7% 是当时最强模型，工程手段（few-shot + version-aware prompt）能 +11pp。

### 决策 2：环境约束"先把稳，先把稳"——只做 5 个模板

**反对"任意题都能讲"的诱惑**。宁可 5 个题型 100% 成功，不要"任意题" 66% 成功。
起始模板：function_transform / axes_plot / step_card / title_card / summary_card。
**横向加模板是 D5 之后的事**，先打通一个模板的全链路。

### 决策 3：渲染策略——预生成 + 实时兜底

实测渲染：**单分镜 1.8–3.2 秒（含 LaTeX）**。原以为 5–30 秒，实际快一个数量级。
所以**现场实时生成完全可行**。但仍保留预生成方案作为 5s 视频保底：
- 预生成 8 段高频题型视频缓存
- 现场只实时生成 1 次（演示"现场出片"卖点）
- 每个分镜可独立重试（不要整链路重跑）

### 决策 4：被推翻的判断

| 当时的判断 | 实际情况 | 修正 |
|---|---|---|
| LLM 数学计算会错，要 SymPy 兜底 | AIME 2026 前沿模型 95–100% | 撤回。SymPy 降级为最终答案抽查 |
| 现场实时生成太慢，必须预生成 | 实测 1.8–3.2s/分镜 | 改成"实时为主，预生成为辅" |
| 船闸调度公开资料极少 | 桂交规〔2026〕2 号 + 平陆运河条例全文公开 | 调研推翻，跳船闸选题 |

---

## 三、文件结构

```
D:\MathStoryboard\
├── README.md               项目说明、跑法、模板列表
├── check_env.py            环境自检（manim + LaTeX + MathTex 实测）
├── generate.py             主入口：分镜 JSON → 校验 → 代码 → MP4
├── requirements.txt
├── storyboard/
│   ├── __init__.py
│   ├── templates.py        5 个模板（确定性代码，不经 LLM）
│   ├── schema.py           分镜 JSON 校验（拦截静默失败）
│   ├── renderer.py         JSON → Manim 源码（含 LaTeX 自动分流）
│   └── latex_env.py        MiKTeX/TeX Live 自动探测与 PATH 注入
├── examples/
│   ├── quadratic_transform.json
│   ├── derivative_meaning.json
│   └── _bad_case_demo.json
├── output/
│   ├── videos/             渲染产物（按 quality 分子目录）
│   └── *_scene.py          自动生成的 Manim 场景（不要手改）
└── media/                  （Manim 默认输出，可忽略）
```

---

## 四、环境（实测版本）

| 组件 | 版本 | 路径 |
|---|---|---|
| Python | 3.12.14 | `D:\Miniconda\envs\manim\python.exe` |
| Manim CE | 0.21.0 | pip 安装 |
| NumPy | 2.5.3 | |
| MiKTeX | 26.5.x | `D:\MiKTeX\miktex\bin\x64`（**不在系统 PATH，已自动注入**） |
| ffmpeg | (任意) | `D:\ffmpeg\bin\ffmpeg.exe` |

**激活环境**：
```bash
D:/Miniconda/envs/manim/python.exe generate.py examples/quadratic_transform.json
```

---

## 五、踩坑清单（最重要！每个都真实发生过）

### 坑 1：MiKTeX 不在系统 PATH
**症状**：`shutil.which("latex")` 找不到，Manim 报 `FileNotFoundError`。
**解决**：`latex_env.inject_path()` 扫描常见安装路径并注入 `os.environ["PATH"]`。子进程继承后可用。
**永久方案**：把 `D:\MiKTeX\miktex\bin\x64` 加进系统环境变量。

### 坑 2：MiKTeX 弹窗挂死
**症状**：第一次跑渲染卡住不动，CPU 0%。
**原因**：MiKTeX 默认遇到缺失宏包会弹窗询问"是否安装"，非交互进程无人点。
**解决**：MiKTeX Console → Settings → "Always install missing packages on-the-fly"。

### 坑 3：中文塞进 formula_latex
**症状**：代码能跑、画面正常，但 label 显示成 `y = x^2 \quad \text{顶点}(2,0)`——LaTeX 原始命令原样输出。
**原因**：MathTex 默认无 ctex，`\text{顶点}` 编译失败，Manim fallback 到原始字符串。
**解决**：schema 校验禁止 label / formula_latex 含中文。需要中文解释放 `caption` 字段（走 Text 渲染）。
**为什么值得做这个校验**：静默失败，学生根本不会发现画面"哪里不对"。

### 坑 4：Axes(include_numbers=True) 没 LaTeX 直接崩
**症状**：`FileNotFoundError: ...` 在 `MathTex` 构造时。
**原因**：坐标轴刻度数字由 MathTex 渲染。
**解决**：要么装 LaTeX，要么 `include_numbers=False`（但降级版不好看）。
**结论**：LaTeX 是必需品，不是优化项。

### 坑 5：step_card 模板自己就犯了静默失败
**症状**：所有步骤叠在同一位置。
**原因**：每一步都用 `to_edge(UP)`，没清掉上一步。
**解决**：VGroup + FadeOut(prev_grp) + 顺序动画。**这次踩坑是好事——直接证明了论证。**

### 坑 6：Manim 输出路径是分片目录
**症状**：`find` 抓到了 `partial_movie_files/xxx.mp4`（未合并的中间产物）。
**解决**：扫描时排除 `partial_movie_files` 目录。

### 坑 7：沙箱里 which 找不到 latex
**症状**（仅本会话）：在 AI 沙箱里 `which latex` 失败，但用户 cmd 里 `tex --version` 能用。
**原因**：沙箱 PATH 与用户终端 PATH 不同。
**影响**：无影响——用户实机 PATH 有 MiKTeX。`latex_env.inject_path()` 仍保留作为防御性。

---

## 六、实测数据（答辩可用）

| 模板 | 分镜数 | 渲染耗时 | 平均/分镜 |
|---|---|---|---|
| 基础场景（Circle+Wait） | 1 | 1.9 s | — |
| function_transform（5 分镜） | 5 | 15.6 s | 3.1 s |
| axes_plot（4 函数 + LaTeX 刻度） | 4 | ~13 s | 3.2 s |
| 启用 LaTeX vs 不启用 | — | 4–5× 慢 | — |
| MathTex 首次编译 | — | 50.7 s（含宏包生成） | — |
| MathTex 二次编译 | — | 8.9 s（缓存） | — |

**结论**：1 段 5 分镜视频 ~15 秒出片，10 天项目完全在 D9 答辩前能跑出 5 段模板化视频。

---

## 七、四个评测指标（D7 上线门）

| 指标 | 目标 | 实测 |
|---|---|---|
| 一次渲染成功率 | ≥85% | 待测（用 30 题测试集跑） |
| 三次内重试成功率 | ≥95% | 待测 |
| 单分镜出片时间 | ≤60 秒 | **3.1 秒**（大幅优于目标） |
| 内容正确率（人工看画面） | ≥90% | 待测（关键指标） |

**重要**：第 ④ 项绝对不能省——只看"能不能跑"会严重高估，那正是静默失败的定义。

---

## 八、5 个核心模板（D2 定稿）

`title_card` / `summary_card` 是通用外壳，不算题型模板。**核心 5 个**：

| 模板 | 覆盖题型 | 说明 |
|---|---|---|
| `axes_plot` | 函数图像、方程根、单调性、两曲线交点 | 最通用，兜底 |
| `function_transform` | 平移 / 缩放 / 翻折变换 | 中学高频 |
| `taylor_approx` | 极限、泰勒展开、局部逼近 | 已验证，观赏性强 |
| `area_under_curve` | 定积分、曲边梯形面积 | 已验证 |
| `step_card` | 任意推导过程（累积板书） | **几乎每题都要用，实际是万能件** |

**不进核心但保留**：`vector_wave`（旋转向量生成正弦波）、`series_approx`（傅里叶级数）——
观赏性是全场天花板，但适用范围窄（仅三角/级数），留作**答辩加分项**，不占核心名额。

---

## 九、评测体系（分两步，不要一次做完）

### 为什么分两步

**Rubric 必须从真实失败里长出来，不能提前写。** 证据：D1–D2 只跑了 2 道高数题，
就挖出 3 个 bug（分镜不清屏 / taylor 无图例 / step_card 文字重叠），
**没有一个能靠提前写 Rubric 预想到**。凭空写的条目一定漏掉真正的坑。

| 阶段 | 题量 | 目的 | 产出 |
|---|---|---|---|
| **D2 现在** | 5 模板 × 3 题 = **15 题** | **打通链路 + 逼出模板 bug** | 模板 bug 清单 |
| **D7 评测** | 扩到 **30 题** | 正式评测，出成功率数字 | 正式 Rubric + 评测报告 |

> ⚠️ D2 跑题**测的是模板，不是 LLM**（LLM D4 才接）。分镜 JSON 此时还是手写的。

### D2 的产出不是"考卷"，是 bug 清单

跑题的价值在于把 bug 逼出来 —— 2 道题逼出 3 个 bug，性价比已经证明。
详见 `eval/01-测试题集.md` 和 `eval/02-判对判错清单.md`。

---

## 十、后续待办（D2–D10 排期）

- [x] **D1** ✅：环境就绪，2 段 demo 渲染成功
- [ ] **D2**：5 个核心模板定稿 + 15 题测试集 + 判对判错清单初版
- [ ] **D3**：接入 OCR（百度「试卷切题识别」API 验证公式输出格式）
- [ ] **D4**：接 LLM 分镜生成（需确认模型型号！）
- [ ] **D5**：补 2 个新模板（几何证明、数列求和）
- [ ] **D6**：接前端（uni-app H5，HBuilderX 编译）
- [ ] **D7**：扩到 30 题 → 归纳正式 Rubric → 评测 + 修 Bad Case
- [ ] **D8**：答辩 PPT + 演示视频预生成
- [ ] **D9**：彩排
- [ ] **D10**：答辩

---

## 九、给后续 AI 协作者的提示词模板

如果你需要让另一个 AI 接手这个项目，把下面这段喂给它：

```
你在接手一个 Manim 讲解视频生成项目（MathStoryboard）。

**核心架构（不许改）**：
- 大模型只输出受 Schema 约束的分镜 JSON
- 确定性 Python 模板翻译成 Manim 代码
- 模板在 storyboard/templates.py，禁止 LLM 写 Manim 代码
- 校验在 storyboard/schema.py，专门拦截静默失败

**环境**：
- Python 3.12.14, D:\Miniconda\envs\manim\python.exe
- Manim CE 0.21.0, MiKTeX 在 D:\MiKTeX\miktex\bin\x64
- latex_env.py 会自动注入 PATH

**先看这些文档**（顺序很重要）：
1. D:\MathStoryboard\PROJECT_KNOWLEDGE.md（本文件）
2. D:\MathStoryboard\README.md
3. C:\Users\Justin\WorkBuddy\2026-09-08-11-01-38\赛道三-方案澄清-产品形态与链路决策.md
4. C:\Users\Justin\WorkBuddy\2026-09-08-11-01-38\赛道三-讲解视频动画方案评估.md
5. D:\MathStoryboard\examples\quadratic_transform.json （看实际分镜格式）

**红线**：
- 禁止让 LLM 写 Manim 代码（架构原则）
- 禁止 label/formula_latex 字段含中文（schema 校验会拦，但别绕过）
- 改模板必须用 Manim CE API（Create/Transform，不是 GL 的 ShowCreation）
- 单分镜出片超过 60 秒 = 性能回归，要查不要忍
```

---

## 十、答疑：常被问的"为什么不"

| 问题 | 答 |
|---|---|
| 为什么不直接让 LLM 生成 Manim 代码？ | 静默失败 + 145 个 GL/CE 不兼容 API |
| 为什么不装 ctex 支持中文公式？ | 增加安装复杂度、收益小——用 caption 字段放中文更简单 |
| 为什么不接真实数据做"任意题讲解"？ | 10 天项目，先稳后广。MVP 用 5 个模板覆盖 80% 题目 |
| 为什么不直接用 Math-To-Manim（GitHub 2.5k stars）？ | 它们做科普视频，我们做错题讲解；我们的分镜可编辑、有步骤分析 |
| 为什么不装 TinyTeX 而用 MiKTeX？ | 你已经装了 MiKTeX，没必要换。TinyTeX 适合精简场景 |
| 为什么不接 SymPy 做最终答案校验？ | 前沿模型数学已 95–100%，校验是锦上添花不是必需品 |


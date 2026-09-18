# Manimatic 交接文档

> 面向：接手这个仓库的人 / AI。
> 读完这份，应当能立刻知道「什么已经能用、还剩什么、坑在哪」。
> 前置阅读：`PROJECT_KNOWLEDGE.md`（项目知识库）、`docs/` 下三份设计文档。
>
> **📌 2026-09-17 会话：**「图片输入并进 core + 停止按钮修复 + 模型配置收口」的
> **完整交接**在 `docs/本次会话交接-图片输入与取消修复.md`（含能力清单、变更文件
> 清单、待办、验证记录）。本篇 §8.67–§8.71 是那五件事的**详细复盘**。
>
> **2026-09-12 更新**：HTTP + SSE 服务层（`api/`）已完成并实机联调通过 ——
> 前后端现在能真的连起来跑。见 §2.3。
>
> **同日再更新**：**真实 LLM（DeepSeek 直连）已接通并端到端验证**；顺带修掉 3 个静默失效类
> bug（`json_mode` 配置失效、同名任务撞 `_parts` 缓存、replace-scene「假通过校验」）
> 和 1 个 DSL 漏带（`parallel` 子动作被丢弃）。见 §4 任务清单与 §8 修复记录。
>
> **同日三更：加了「LLM 调用留档 + 回放」**（`storyboard/llm_log.py`）——
> 每次调用（含失败的中间轮）都落一行到 `output/_llm/calls.jsonl`；同输入开
> `MSB_LLM_REPLAY=1` 就直接回放、不再花钱（实测 6.33s → 0.0s）。
> **这个留档立刻救了场**：它把一条被截断的失败回复完整调了出来，
> 定位到 `"place":[{"center"}]` 其实是**我们的规格文档漏了带值示例**导致的
> 非法 JSON。见 §8.9 / §8.10。
>
> **同日四更：LLM 已切到中转站 `deepseek/deepseek-v4.1-flash`（深度思考，默认开）。**
> 三个必须知道的坑：中转站**必须带浏览器头**（否则 403/1010）；
> **思考关不掉**（不传参数就是开着，走独立 `reasoning` 字段）；
> **`max_tokens` 必须 ≥32768**，否则思考吃光预算、正文一个字都出不来（表现为"JSON 解析失败"）。
> ⚠️ 代价：思考期间正文不出字，`/api/chat` 首个可见字 **+0.97s → +93s**。
> 想切回直连的写法见 §6 的 `llm.local.json` 注释。见 §8.11。
>
> **同日五更：① 思考流已接进 SSE**（`thinking_delta`，`MSB_LLM_SHOW_THINKING=1` 开）——
> 首个思考片段 **+2.15s** 就到，不用再干等；**默认关**（推理内容不等于给用户看的话术）。
> **② 配置支持多模型档案**：`llm.local.json` 里 `active` + `profiles`，改一处就换模型，
> `--profile` / `MSB_LLM_PROFILE` 可临时指定；你原来的 DeepSeek 直连已作为 `direct`
> 档案放回去（实测 7.0s 一轮通过）。见 §8.12 / §8.13。
> **③ 一键启动脚本 `run_api.bat`**（内含各开关，默认开思考流）。
>
> **同日六更：前端已接上并切到真后端。**
> ① 设置页新增「模型 API」：可**切换/新增模型档案**（`/api/llm/*`），
> **密钥只留在后端、永不进浏览器**（前端配置是公开的，放那儿等于泄露）。见 §8.14。
> ② `web/.env.local` 已设 `NEXT_PUBLIC_USE_MOCK=false`，Mock 退居演示用。见 §8.15。
> ⚠️ 真链路首个可见字 **17.9s**、整条分钟级 —— Mock 那 0.8s 是假节奏，别拿它判断真实体验。
>
> **同日七更：补上多轮对话上下文。**
> 之前每次 `/api/chat` 都是全新一次调用、不给模型任何历史，于是「生成对应视频」
> 这种追问会被模型当成"你没给题目"然后反问你。现在按主流路线做了服务端会话 +
> 原始历史回放 + token 预算裁剪，实测该场景从"反问用户"变成**正确出 4 个分镜**。见 §8.16。
> 另外思考过程改为**展开即全文、可滚动查看**（之前只渲染最后 320 字，
> 出现"共 2080 字却只能看 320 字"的割裂感）。
>
> **2026-09-13：输入框收口。** 工具条改成「左选模型、右发送」，模型切换
> 直接复用设置页那对接口（不再需要用户跑去设置页转一圈）。高度压了一轮又回调：
> 先压到 92px（连发送按钮一起从 40 缩到 32），被用户当场看出"对话页比新对话的小一号"，
> 最终结论是**块头（`px` / 按钮）两档共用，只压"空气"** —— 空态 114px、对话中 106px。
> 模型胶囊的位置由 `pb` 说话（`mt` 在对话页根本不动它，为此白调了三轮）。
> 见 §8.22 §3。
>
> **同日再补一条（渲染）**：修掉「某个分镜一直重试失败」的一个真根因 ——
> 模型给动态数字写了中文单位（`"unit": " 块"`），而 `DecimalNumber(unit=...)` 内部走
> MathTex，中文必然 `LaTeX Error: Unicode character 块 ...`。见 §8.25。
>
> **2026-09-15：终于有了第一个测试。** `tests/test_normalize_idempotent.py`（29 条）——
> 把 §8.8 里挂了很久的"幂等回归测试"兑了现，并**修掉 5 个规范化不幂等点**
> （§8.6 表里那 4 个 + 写测试时新抓到的 `_dropped` 自我污染）。
> 验证做到三层：29 条测试全绿、**22 份示例生成的 44 个源码文件逐字节零差异**、
> 真渲一份端到端出片。见 §8.26。
> 改了 `dsl._norm_*` / `schema.validate` 请跑 `python -m pytest tests -q`（<1 秒）。
>
> **同日再补：`parallel` 真并行做掉了**（§8.27）。它原来生成的是多条顺序 `self.play` ——
> 也就是"写的是并行、跑的是串行"，属于契约型静默失效。
> 顺带被测试抓出**第二个静默失效**：`.animate.scale(x).set_run_time(0.4)` 会被无声忽略
> （`set_run_time` 不在 Mobject 上），正确写法是 `.animate(run_time=0.4).scale(x)`。
> 验证做到四层：39 条测试全绿、实测时长 = max、**非 parallel 路径 44 个源码文件
> 逐字节零差异**、端到端真渲出片。
>
> **2026-09-16：一轮「生成质量 + 健壮性」收口**（§8.28–§8.32）。
> 起因是两件事：**出错率其实已经很低，但成品模板化严重**；以及**每个分镜末尾都淡出**
> → 连贯内容（二分查找、排序）只能每镜重画一遍数组。
>
> ① **模板化的根因不在模型，在 prompt**：规范里写死了「封面 → 图形/推导 → 结论」，
> 而唯一那个 few-shot 是"坐标系 + 曲线 + 切线" —— 模型于是把所有题都翻译成这一套
> （几何题也上坐标系）。已删掉固定配方、改成**按题型定「画面引擎」** + 三条**反模板硬要求**，
> 并补了 **4 个覆盖不同结构的示例**（几何题**全程不用 axes**）。见 §8.31。
> ② **点破了渲染引擎是 Manim**（原先一个字都没提），同时把红线收紧
> —— 引擎名一旦说破，模型最容易犯的新错就是拿 Manim 的类名当 `kind` 填。见 §8.31。
> ③ **`carry`：跨分镜承接**。`{"id":"arr","carry":true}` 让元素在下一镜"原样还在"，
> 不再重画、也不再被分镜边界清掉。配三条硬校验（分镜 1 不许写 / 必须真在屏上 /
> 依赖要一起承接）。**两条渲染路径必须分开处理，坏法还不一样**。见 §8.32。
> ④ **校验层不再"报一个错就抛"**：一次收齐 elements / timeline / 表达式里的问题一起回灌
> —— 重试只有 4 次，一轮一个错等于把 4 次机会浪费在 4 个错上。见 §8.28。
> ⑤ **`finish_reason` 以前抓了却从来不看**：截断会伪装成"JSON 解析失败"，
> 排查方向直接被带到语法错误上。现已打通到留档与 `--llm-stats`（新增「截断」列 +
> 「输出 tok 峰值占 max_tokens 百分比」）。见 §8.30。
>
> 验证：**50 条测试全绿**（新增 `tests/test_carry.py` 11 条）、
> **22 份示例 × 2 条渲染路径逐字节零差异**、`carry` 两条路径都端到端真渲出片。
>
> **同日再补：拿二分查找（§8.32 建议的验收题）抽帧，抓到两个"代码跑了、画面错了"**（§8.33）。
> ① **`replace` 连用会把旧文字重新加回屏上叠成一团** —— 根因在**代码生成**：
> 每次都拿同一个原始变量当替换源，而第一次之后它已经不在场景里，
> manim 会把"被动画却不在场景里"的对象**重新加回场景**（`scene.py` 原话）。
> 已加一层"元素 id → 当前变量"的换绑（`_var()`/`alias`）。**模型写的 JSON 没错。**
> ② **`move_cells` 只挪中心不改大小** → 8 格宽的框被居中到 1 格上、横穿整张表。
> 顺带发现校验层那条"框的大小不能变"**比的是 `len()`**，`[1,8]`→`[5,8]` 长度都是 2
> 照样放行 —— **检查写错了比没有检查更危险**。现在改成变形到"框住目标格子"的新框，
> 大小随格子走。验证：50 条测试全绿、**114 个生成源码逐字节零差异**、重渲抽帧两处都不复现、
> `render()` 与 `--split` 两条路径都真渲出片且画面一致。
>
> **同日三补：字幕过渡可配 + 版面判定改成"估尺寸"**（§8.34）。三件事：
> ① `replace` 加 `effect`（默认 `crossfade`），**不再用形变给字幕换字** ——
> 而且这个默认效果改了三轮（每轮都是逐帧看出来的）：全程同时淡会糊 0.4 秒 →
> 错开 20% 仍被用户看出"淡出没淡完淡入就来了" → 最终**严格先后、零重叠**
> （旧的字渐隐、中间一帧全空、新的才渐显）；
> ② 表格加 `cell_w`/`cell_h`/`pad_x`/`pad_y` —— 顺带查出"格子被固定死"的真身是
> manim 默认 `h_buff=1.3`（每格凭空宽 1.3，8 列光留白吃掉 10.4，而 DSL 一个尺寸参数都没暴露）；
> ③ 新增 `storyboard/metrics.py`（纯算术估算器），删掉"表格 8×8 / 格号最多 6 个 /
> 单元格 80 字"三类与尺寸无关的硬上限，改成按内容估算、**明显超出才拒**并回灌
> "减几列 / 字号降到多少 / pad_x 调小"。顺手补掉一个存量 500（表格每行长度不一致）。
> ⚠️ **"估算 > 预算"不等于"真放不下"**：估算是有意偏大的上界（实测 1.05~1.7 倍），
> 二分查找那张 8 列表实测渲染宽 12.03（放得下）却被估成 12.80 —— 判定线因此带 1.15 的余量。
> 验证：98 条测试 + **223 条标定断言**（只跑 `MSB_CALIBRATE=1`）全绿、
> 114 个生成源码的**分镜体**逐字节零差异、两处端到端真渲逐帧复核。

---

## 0. 一句话

Manimatic 把**一道题变成一段带推导过程的讲解动画**：输入题目 → LLM 产出受约束的分镜 JSON
→ 确定性渲染器翻成 Manim 代码 → 逐分镜渲染成 mp4 → 前端「渲好一段播一段」。

**三段都已跑通**：渲染管线（Python）、HTTP + SSE 服务层（`api/`）、前端界面（`web/`）。

---

## 1. 目标仓库结构

```
D:\Manimatic\
├── MathStoryboard\      ← git repo A（Python 渲染服务）★ 你在这里
└── web\                 ← git repo B（Next.js 前端）
```

- 两个仓库**目录相邻**，GitHub 上前端仓库名为 `manimatic-web`，本地目录名保持 `web`
  （clone 时指定：`git clone <url> web`）。
- 为什么不 monorepo：部署形态不同（Vercel vs 长驻 CPU 渲染服务器）、依赖体积差一个量级
  （MiKTeX 约 2GB vs node_modules）、两边只共享**契约**没有共享代码。
  详见 `docs/Manimatic-前端架构设计.md` §12。
- ⚠️ `MathStoryboard/.gitignore` 里目前有一行 `web/`，那是"前端还放在本仓库内部时"的忽略规则。
  **目录移出去之后这行要删掉**，否则没有任何影响但会误导人。

---

## 2. 现状：什么已经能用

### 2.1 渲染管线（Python）—— 已完成，实机实测通过

| 模块 | 干什么 | 状态 |
|---|---|---|
| `storyboard/dsl.py` | 元素库（20 种）+ 动作库（26 种）+ 代码构建器。**五道闸**拦静默失败 | ✅ |
| `storyboard/schema.py` | 分镜 JSON 校验，报错带精确路径与原因 | ✅ |
| `storyboard/templates.py` | 9 个旧模板（纯兼容层，**不再新增**） | ✅ 冻结 |
| `storyboard/renderer.py` | 分镜 JSON → Manim 源码；`render(sections=True)` 在每个分镜边界插 `self.section(n)` | ✅ |
| `storyboard/llm.py` | OpenAI 兼容调用 + **校验失败回灌重试**（`max_attempts=4`）。已接 DeepSeek 直连，见 §8.1–8.5 | ✅ |
| `storyboard/llm_log.py` | **调用留档 + 回放**：每次调用（含失败轮）落 JSONL，同输入可零成本回放。见 §8.9 | ✅ |
| `storyboard/tex_batch.py` | LaTeX 批处理预热（清缓存首渲 60s → 12.9s） | ✅ |
| `storyboard/latex_env.py` | MiKTeX / TeX Live 自动探测 + PATH 注入 | ✅ |
| `generate.py` | 主入口（CLI） | ✅ |
| `storyboard/render_worker.py` | 仅服务于 `--split`（调试单个分镜 / 服务层增量渲染），**不是主路径** | 保留 |

> 🔧 **2026-09-12 修掉一个既有 bug**：`renderer.render_split()` 组装 HEADER 时**漏传**
> `sections_flag` / `sections_total` 两个占位符，`HEADER.format()` 直接抛
> `KeyError: 'sections_flag'` —— 也就是说 `generate.py --split` 这条命令**从写下那天起就没跑通过**
> （默认路径不走 `render_split`，所以一直没暴露；服务层的增量渲染要用它，才撞上）。
> 已在 `render_split()` 里显式传 `sections_flag=False, sections_total=0`。

**关键性能事实**（`free_product_rule.json`，8 分镜，480p15，20 核）：

| 方案 | 总耗时 | 首段可见 |
|---|---|---|
| **默认（一个 Scene 演到底 + 边渲边切）** | **13.7 s** | **2.3 s** |
| 多进程并行（历史方案） | 23.8 s | ~18 s |

→ **默认路径就是最优解，不要为了"并行"去拆场景或多开进程。** 原因见 §5。

### 2.2 前端（Next.js）—— 已完成，可独立运行

- 三栏工作台（历史 / 对话 / 产物）、流式文字、大纲确认、分镜级进度、
  统一播放器（双缓冲、真全屏、分享弹窗）、产物列表 + 二级详情。
- 自带一套 **Mock 后端**（`web/src/app/api/**` 的 Route Handlers），事件形态与真后端**完全一致**。
- **切到真后端只改两个环境变量，组件一行不动**：

```bash
# web/.env.local
NEXT_PUBLIC_USE_MOCK=false
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### 2.3 HTTP + SSE 服务层（`api/`）—— 已完成，实机联调通过

```bash
# 启动（fastapi / uvicorn 已装；requirements.txt 里也列了）
cd MathStoryboard
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
# 自检：http://localhost:8000/api/health  ·  文档：/docs
```

| 模块 | 干什么 |
|---|---|
| `api/main.py` | FastAPI 入口：CORS、`/api/health`、`/media` 静态挂载、启动自检与过期清理 |
| `api/routes/chat.py` | `POST /api/chat`：流式文字 → 大纲，并把分镜落到会话目录 |
| `api/routes/render.py` | `POST /api/render/confirm`（full）、`POST /api/render/retry`（incremental） |
| `api/routes/storyboard.py` | `POST /api/storyboard/replace-scene`：整分镜替换 |
| `api/pipeline.py` | **核心工作量**：把子进程 stdout 的 `@@ {json}` 流转成 SSE 事件 |
| `api/events.py` | SSE 事件构造（与前端 `AssistantEvent` 一一对应） |
| `api/store.py` | task 目录 / 产物路径（复用同一 name = 白捡的增量渲染） |
| `api/jobs.py` | 并发信号量 + **同名任务串行锁** + 每日配额 |

**两种渲染模式，前端不用区分**：

| 模式 | 用在 | 为什么 |
|---|---|---|
| `full` | confirm | 一个 Scene 演到底 + 边渲边切，最快、首段约 3~6s 可见 |
| `incremental` | retry / replace-scene | 走 `_parts` 缓存，源码没变的分镜直接命中旧片段 |

⚠️ **两个模式的工作目录不一样，这是理解缓存行为的关键**：

- `full` 用 `--media_dir output/`（模块名 `<name>_scene`），**不写 `_parts/`**；
- `incremental` 用 `_parts/<name>/media0`（每段一个 `_parts/<name>/<name>_sN.py`，
  片段落在 `_parts/<name>/segments/`）。

所以 **confirm 之后的第一次 retry 必然是冷的，会把所有段渲一遍**（然后才建立缓存），
第二次起才有命中。前端表现就是"第一次重试慢、之后快"。

还有一个容易误读的点：`iter_incremental(emit_indices=...)` 里的 `emit_indices`
**只控制"交付给前端哪些段"，不控制"渲染哪些段"**。渲染范围永远是
「所有源码发生变化的段」。retry 传 `range(from, n)` 是指"从这段起重新交付"，
不是"只渲这几段"。

实测（实机）：
- 改一个分镜（`replace-scene`）→ 只有那一段源码变了 → **只重渲那一段（5s）**，
  其余 7 段 0.2s 命中缓存，总 5.5s（对照全量 26~40s）。
- `retry` 第一次 → 全段重渲；第二次 → 0.2s 全命中。

⚠️ **缓存挂在 `(thread_id, message_id)` 上，这是个契约要求**：
`name = task_name(thread_id, message_id)`，而 `_parts/<name>/` 就是缓存本体。
所以 **`replace-scene` / `retry` 必须复用被修改那张卡片的 `message_id`**。
如果前端为"改后的版本"新生成一个 `ma_xxx`，就会得到一个新的 task 名 →
缓存全空 → **全量重渲**，增量优化直接失效，而现象只是"怎么这么慢"，极难定位。
（实测：同一个改动，复用 message_id 只渲 1 段；换新 id 则渲 5 段。）

⚠️ **冷启动另算**：服务刚起来后的第一条渲染要多几十秒（manim 导入 + 中文字体/LaTeX
首轮缓存都在这个进程里做）。实测同一条链路：冷启动首段 **49.9s**，热态首段 **5.7s**。
别把冷启动当成性能回归。

**几个必须知道的实现细节**（都踩过，注释里写了原因）：

- **SSE 分隔符必须是 `\n\n`**：前端的 `readSSE` 用 `buffer.indexOf("\n\n")` 切事件，
  而 `sse-starlette` 默认输出 CRLF —— 于是"连接正常、日志正常、UI 不动"。
  所以 `api/events.py` 手写这 3 行，没引 `sse-starlette`。
- **视频 URL 必须是绝对地址**（`MSB_PUBLIC_BASE_URL`）：前端把 url 直接塞 `<video src>`，
  相对路径会打到 3000 端口（Next.js）而不是 8000，表现为"渲染成功但视频 404"。
  并且带 `?v=<mtime>`：重渲后路径完全相同，不加版本号浏览器会继续放缓存里的旧视频。
- **`message_id` 是前端生成的**（`ma_xxx`）。前端**忽略** `done.messageId`，
  所以后端不能靠"自己发出去的 id"找分镜 —— 会话级分镜存在
  `output/_tasks/threads/<thread>.json`，confirm 时按 thread 找回。
- **客户端断开要立刻杀子进程**：`Popen` 正阻塞在 readline 上，一个分镜可能渲十几秒，
  只 set 一个标志等于白发十几秒 CPU 还占着并发额度。`pipeline.Canceller` 把子进程登记起来，
  断开时直接 kill（实测断开 1s 内进程归零、并发位释放）。
- **`brief` 必须在 JSON 的第一个键**：`llm.BriefTap` 从**流式到达的 JSON 文本**里增量抠出
  `brief` 的值边收边吐，这样 1~2 秒就有字可显示，不用把一次 LLM 调用拆成两次。

**性能说明**：本机实测 full 模式 8 分镜首段约 2~4s 可见、整条 13~27s。
⚠️ 在 AI 沙箱 / 被代理的 shell 里测会明显虚高（同一份分镜 CLI 也要 42s），
**性能数据一律以实机为准**。

---

## 3. 前后端契约（对接的唯一依据）

权威定义在 **`web/src/lib/types.ts` 的 `AssistantEvent`**（前端）与本文档（后端）。

### 3.1 SSE 事件（8 个）

```
event: text_delta      data: {"text":"勾股定理是指..."}            ← 流式文字，最先到
event: plan             data: {"plan":[...],"intent":"propose"}    ← 大纲；intent=none 则到此结束
event: tool_call        data: {"name":"render_storyboard","args":{"segmentCount":5}}
event: tool_progress    data: {"step":2,"total":5,"stage":"rendering"}
event: tool_result      data: {"index":1,"url":"/media/t123/v1/s1.mp4","durationSec":4.533}
event: tool_done        data: {"url":"/media/t123/v1/final.mp4","segmentCount":5}
event: error            data: {"scope":"step","index":3,"message":"...","retryable":true}
event: done             data: {"messageId":"m_789"}
```

**`tool_progress.step` 的语义是「已交付段数」**（不是"正在渲第几段"）。
前端据此把 `scenes[step]` 从 queued 翻成 rendering、并推进进度条。语义已在前端固化，
后端按此实现即可（**每个 step 只发一次**，不要重复发）。

**`stage` 三态**：`prewarm`（LaTeX 预热，一次做完，不分摊到分镜）→ `rendering` → `concat`。

### 3.2 产物路径契约

```
output/videos/{name}_scene/{quality}/
├── StoryboardScene.mp4                  ← 整条成片
└── sections/
    ├── StoryboardScene_0000_scene_1.mp4 ← 第 1 段
    ├── ...
    └── index.json                       ← 真实时长索引（前端时间轴的数据源）
```

```json
[ { "index": 0, "video": "StoryboardScene_0000_scene_1.mp4", "duration": 5.0 },
  { "index": 1, "video": "StoryboardScene_0001_scene_2.mp4", "duration": 7.333 } ]
```

### 3.3 时长语义（最容易踩的一条）

`duration` 是 manim 按**真实帧数**算出来的，与大纲里的 `durationSec`（**设计值**）**故意不同**。

> 前端整片时间轴、进度条、分镜定位**全部用真实时长**。
> 后端接 SSE 时，`tool_result.durationSec` 必须来自这里的 `duration`，
> **不要**图省事传大纲的估算值，否则前端进度条会漂。

### 3.4 交付顺序：严格 1→N

后端是**单进程串行**渲染，`@@ {"section": N}` 一定 1,2,3… 递增。
前端因此**删掉了"乱序重排的缓冲编排器"**，按序消费即可。
（历史上用多进程并行时片段会乱序，那条路已被实测否决，见 §5。）

### 3.5 后端要做的转换（核心工作量）

子进程 stdout 里每渲完一段会打一行：

```
@@ {"section": 1, "video": ".../sections/StoryboardScene_0000_scene_1.mp4", "duration": 5.0}
```

后端只需把它转成 SSE：

```
event: tool_result
data: {"index": 0, "url": "/media/.../s1.mp4", "durationSec": 5.0}
```

⚠️ **必须用 `Popen` 逐行读 stdout**，不能用 `subprocess.run(capture_output=True)`
（后者要等进程结束才返回，拿不到实时时机）。
⚠️ 必须显式 `encoding="utf-8", errors="replace"`：中文 Windows 的 locale 是 GBK，
而 manim 输出是 UTF-8，不指定会 `UnicodeDecodeError` 崩掉读取线程、主流程卡死
（表面上像"渲染卡住"）。这条已在 `generate.py` 里修好，照抄即可。

---

## 4. 后端任务清单（38 行：32 已完成 / 1 作废 / 5 待办 —— 27、28、29、30、32）

| # | 任务 | 状态 | 关键点 / 落地位置 |
|---|---|---|---|
| 1 | FastAPI 骨架 + `/api/health` | ✅ | 没重写校验层，`schema.py` + `dsl.py` 原样复用，只在外面包 HTTP |
| 2 | `POST /api/chat`（SSE） | ✅ | `text_delta` → `plan` → `done`；额外做了**真流式**（见 §2.3 的 BriefTap） |
| 3 | `POST /api/render/confirm`（SSE） | ✅ | 核心：`Popen` 逐行读 stdout，`@@` → `tool_result`，`tool_done` 在进程退出后发 |
| 4 | `brief` / `outline` / `intent` 三字段 | ✅ | `schema.py` 宽校验（`intent=none` 允许空 scenes），prompt 强制字段顺序 |
| 5 | task 目录复用 → 增量渲染生效 | ✅ | `iter_incremental` + `_parts` 缓存；实测只改一段时只重渲那一段 |
| 6 | 单分镜重试 / 整分镜替换接口 | ✅ | `llm.regenerate_scene()` 重新输出**一个完整分镜对象**（不做文本 patch）；返回值必须是 raw，见 §8.6 |
| 7 | 并发信号量 + 每日配额 | ✅ | `api/jobs.py`：`asyncio.Semaphore(2)` + 内存日配额。⚠️ 多实例部署要换 Redis |
| 8 | 接真实 LLM（DeepSeek 直连） | ✅ | `llm.local.json` 厂商预设 → 文件 → 环境变量 → 显式参数；`json_mode` 的唯一属主是 `resolve_config()`。直连比中转快一个量级：单分镜 5.9–7.0s、首字 +0.53s。见 §8.1–8.5 |
| 9 | 修复 `json_mode` 静默失效 | ✅ | 配置白名单漏掉了它，且路由层硬写 `True`；改由 `effective_json_mode()` 单点决策。8 组用例验证。见 §8.1–8.4 |
| 10 | 同名任务串行化（修 `_parts` 竞争） | ✅ | 两次运行撞同一 task 名 → `_parts/<name>/media0` 里 `FileNotFoundError`；`render_slot` 里按 name 加 `asyncio.Lock`（拿锁在全局信号量**之前**，避免持信号量等待）。见 §8.5 |
| 11 | 修复 replace-scene「假通过校验」 | ✅ | 规范化产物被写回 raw 槽位 → `_validate()` 规范化第二遍，而规范化不幂等（`plot.x_range` 默认 `None`）。`regenerate_scene()` 改为返回 raw。见 §8.6 |
| 12 | 修复 `parallel` 子动作被丢弃 | ✅ | `_norm_action()` 漏了 `parallel` 分支 → 写了等于没写（不报错、不上屏）；已补递归规范化。**仍不是真并行**，见 §8.7 |
| 13 | 密钥与配置端到端验证 | ✅ | DeepSeek 直连 `/api/chat` 5.2–8.2s、首 `text_delta` +0.97s；`confirm` 热段 +5.7s / 冷启 +49.9s。启动横幅直接打印生效的 `模型 @ base_url` + `json_mode / temperature / max_tokens` |
| 14 | 规范化幂等的回归测试 | ✅ | `tests/test_normalize_idempotent.py`（29 条）。**写测试时又抓出第 5 个不幂等点**（`_dropped` 自我污染），§8.6 表里那 4 个点也全部复现并已修。已验证渲染输出零变化 + 真渲一份 4 分镜通过。见 §8.26 |
| 15 | `parallel` 改成真并行 | ✅ | 已做（§8.27）：子动作编译成动画表达式、合成**一条** `self.play(...)`；`_est_action_time()` 同步改成 max。**写测试时抓出一个新静默失效**：`.animate.scale(x).set_run_time(0.4)` 会被无声忽略（`set_run_time` 不在 Mobject 上），正确形式是 `.animate(run_time=0.4).scale(x)`。见 §8.27 |
| 16 | LLM 调用留档 | ✅ | `storyboard/llm_log.py`：每次调用（含**失败的中间轮**）落一行 JSONL 到 `output/_llm/calls.jsonl`，含完整 prompt / 回复原文 / usage。见 §8.9 |
| 17 | LLM 回放（省 API 钱） | ✅ | `MSB_LLM_REPLAY=1`（或 CLI `--replay`）：同输入指纹命中留档就不发请求。**默认关**，开了会在启动横幅喊出来。见 §8.9 |
| 18 | 修 JSON 解析：JS 简写键 + 报错定位 | ✅ | 模型会写 `"place":[{"center"}]`（缺 `: true`），根因是**我们的规格只列了键名没给带值示例**。规格补示例 + 解析层白名单兜底 + 报错带行列。见 §8.10 |
| 19 | 接中转站 deepseek-v4.1-flash（深度思考） | ✅ | 中转站需浏览器头（否则 403/1010）；**思考默认开且关不掉**，走独立 `reasoning` 字段；`max_tokens` 必须 ≥32768，否则思考吃光预算、正文全空。首字代价大（+93s），见 §8.11 |
| 20 | 解析报错区分「截断」与其他 | ✅ | 我上一轮加的行列定位把"截断"说成了"语法错"，把排查方向带偏过。现在三种情况分开说，且用"报错位置是否真落在文本末尾"判定，不误报。见 §8.11 |
| 21 | 思考流接进 SSE（后端） | ✅ | `thinking_delta` 事件，`MSB_LLM_SHOW_THINKING=1` 开（**默认关**：推理内容不等于给用户看的话术）。实测首个 thinking_delta **+2.15s**，把 93s 干等变成 2s 有反馈。见 §8.12 |
| 22 | 多模型档案切换 | ✅ | `llm.local.json` 支持 `profiles` + `active`，改一处就换；`--profile` / `MSB_LLM_PROFILE` 只影响这一次。旧扁平配置**原样兼容**。见 §8.13 |
| 23 | 前端显示「正在思考…」 | ✅ | `web/src/components/workbench/ThinkingIndicator.tsx` + Workbench 分发 + Mock 也会发 `thinking_delta`。默认折叠只显示"已思考 N 秒"，正文一到自动收起。见 §8.12 |
| 24 | 前端配置模型（设置页） | ✅ | `GET /api/llm/profiles` + `POST /api/llm/active` + `POST /api/llm/profile`；设置页可切换/新增档案。**密钥永不回传**（只回 `has_key`）。见 §8.14 |
| 25 | 多轮对话上下文 | ✅ | 服务端存会话消息（`output/_tasks/messages/`），按 token 预算裁剪后拼进 messages。"生成对应视频"这类追问从"反问用户"变成正确指代。见 §8.16 |
| 27 | 真·流式降级与容错打磨 | 待办 | 流式不可用时的降级已有（自动退回非流式）；还没做的是**真正的分段 LLM 调用** |
| 28 | 配额与限流上生产 | 待办 | 内存计数在多进程/多实例下各算各的，需换 Redis；按 IP / 用户维度限流 |
| 29 | 零间隔连播（fMP4 / HLS） | 待办 | 见 §5.5，属契约变更，不是当前阻塞 |
| 30 | 无 LaTeX 环境下 `number` 分镜整条渲不出 | 待办 | `_number()` 没有 `if not USE_LATEX:` 分支，而 `DecimalNumber` 内部走 MathTex。本机造不出"没装 LaTeX"的环境、没法实测，所以没动。见 §8.25 末尾 |
| 31 | ~~`parallel` 改成真并行~~ | ✅ | **已做，见第 15 项与 §8.27**（原编号 15/31 重复，此处作废，保留编号只为不打破引用） |
| 32 | 生产鉴权 | 待办 | `api/` 目前**零鉴权**（`/api/admin/cleanup` 是裸接口）；前端 `login` 是"点一下就进"的演示壳。上线前必须补 |
| 33 | 校验层一次报出全部错误 | ✅ | 原来 `validate_scene` 遇到第一个错就抛，而重试只有 4 次 → 多错必死。现在收齐 elements/timeline/表达式三处的问题一次回灌（12 条 / 1800 字符上限）。见 §8.28 |
| 34 | 修重试回灌的两处自相矛盾 | ✅ | ① 反馈语要求"完整重写"却只回灌 3000 字符（一份 4~6 镜的 JSON 必超）→ 抬到 12000 并**明说被截断**；② `brief` 为空以前静默通过 → 现在回灌重试 + 键序告警 + 流式兜底补发。见 §8.29 |
| 35 | `finish_reason` 打通（截断不再伪装） | ✅ | 截断以前伪装成"JSON 解析失败"/"空内容"，把排查方向带偏。现在记进留档、`[WARN]` 喊出来、**先于解析**判断并用专用反馈重试；`--llm-stats` 新增「截断」列与「输出 tok 峰值占 max_tokens 百分比」。见 §8.30 |
| 36 | 反模板：按题型定画面引擎 | ✅ | 删掉「封面→推导→结论」固定配方与"4~12 秒"的过严时长约束；新增画面引擎对照表 + 三条反模板硬要求；few-shot 从 1 个补到 4 个（含**全程无坐标系**的几何题）。见 §8.31 |
| 37 | 点破渲染引擎 + 隔离引擎词汇 | ✅ | 明确告知引擎是 Manim（给能力边界与代价直觉），同时收紧红线 1「规格外的名字一律非法」（**故意不列举禁止项**，避免反向诱导），并清掉 `describe()` 里 `always_redraw` / `add` 的词汇泄漏。见 §8.31 |
| 38 | `carry`：跨分镜承接 | ✅ | `{"id":"arr","carry":true}`。三条硬校验 + 两条渲染路径分开处理（reuse 下**一行代码都不生成**，rebuild 下重建后立刻 `add`）。11 条测试；无 carry 时 22 份示例逐字节零差异。见 §8.32 |
| 39 | `replace` 连用叠影 + `move_cells` 错位 | ✅ | ① `replace` 后旧对象仍被当作替换源 → manim 把"不在场景里的被动画对象"重新加回场景 → 每替换一次叠一层；加 `_Builder._var()`/`alias` 换绑。② `move_cells` 原来只 `move_to` 不改大小，且校验层拿 `len(rows)` 比大小（`[1,8]`→`[5,8]` 都是 2，拦不住），改成 `Transform` 到 `_cell_box(...)`。见 §8.33 |
| 50 | 过渡生硬：放宽"动态元素只能用 show" + 示例镜 2 重做 | ✅ | 看片反馈：① 镜1→2 生硬（静态点 A 被清屏淡出又 `show` 回来；B/割线瞬间出现）；② 公式"突然"出现；③ 字幕别用 slide。**实测发现规范过严**：动态元素用 `fade_in` 会真的淡入、且淡入后 updater 照常工作（抽帧验证），而校验层本来就把 `fade_in` 算作上屏动作。改：红线 4 与 `describe()` 改成"必须显式上屏，要自然就用 `fade_in`"；示例镜 2 改成 →点 A 用「静态 + carry」承接、B/割线 `fade_in`、**先画面后公式**（加一句人话字幕）、字幕回默认 crossfade；镜 3 一并改 `fade_in`。见 §8.47 |
| 49 | carry 漏写 tracker：规格补全 + 校验层报错说清来龙去脉 | ✅ | 写 few-shot 示例时踩到：承接一个引用 tracker 的元素（`"y": "s**2"`）却没承接 `s` → 报 `表达式 's' 未定义`，看不出是 carry 的事。① `describe()` 的 carry 依赖清单补上 tracker，并点破"承接 tracker 会回到声明时的初始值（画面跳回去）、这种情况该用固定值重声明"；② `_scan_expr_names` 加 carry 诊断（含**去重**与**推荐出路排前**），命中时按两条出路重写报错；③ 补用例 `test_carry.py::test_carried_element_referencing_prev_tracker_is_explained`；④ **顺带修掉 `--dry-run` 在中文 Windows 必崩**（print 源码撞 GBK）。全量 160 passed，示例真渲通过。见 §8.46 |
| 57 | 字幕标点：公式后面别紧跟中文分号 | ✅ | 镜 4 收口句原写 `…$y' = 2x$；$x = 1$ 处就是 $2$…`，中文分号紧贴行内公式会被读成式子的一部分 → 改成破折号（或逗号），标点在公式**前面**的写法不受影响。全示例只此一处。见 §8.54 |
| 56 | 镜 4 每步加旁注 + 新增镜 5「常用求导公式」 | ✅ | 旁注 4 条（GREY、挂在对应公式右侧、紧随公式 0.8s 出现）；镜 5 是公式表（幂函数那条上黄 + 旁注「今天这一题就是 n = 2」）—— 关键是那句「都是同一个定义算出来的」，否则这一镜就变成让人背公式。⚠️ 顺带发现**多行 `text` 会叠在一起**（未根因），绕法＝每行一个元素用 `next_to` 串。全片 43.2 → **53.6s**。见 §8.53 |
| 55 | 补第 4 镜「正统做法：用定义算一遍」 | ✅ | 前三镜是几何直觉（数值逼近 + 图像扫描），正统方法只在结尾提了一嘴 → 新增一镜纯代数推导（定义 → 代入 → 展开 → 约分取极限，结论 `2x` 用 `tex_colors` 上黄），四行**左对齐**用 `next_to` 串、答案收在字幕里。全片 34.6 → **43.2s**（4 镜）。见 §8.52 |
| 54 | 数学表述修正：极限不能用「取到 0 / 变成」 | ✅ | 镜 3 字幕改成「当 Δx → 0 时，割线趋近切线，斜率的**极限**是 2x」，outline 摘要与 `_note` 同步；`取 0`/`取到 0`/`变成`/`成了` 全量搜了一遍。⚠️ **规格（工作手册）里还没有这条约束** —— 模型写的正是这些字幕，同类错误会复发（待定，见 §8.51 末尾） |
| 53 | 记号统一（2s → 2x）＋删掉单独总结镜、收口句并入扫描镜 | ✅ | 字幕里的符号改成与图像标签一致的 `2x`（右上角 k 读数才显示当前值）；删掉第四镜，把「所以「求切线斜率」就是「求导」：y' = 2x」并到扫描镜结尾（`replace` 一句）。全片 4 镜 → **3 镜**、40.0 → **34.6s**。见 §8.50(4)(5) |
| 52 | 镜 3 重排节奏：两图并排 + 共享 tracker 同步 + 字幕先切后动 | ✅ | 左上角加第二个坐标系画 `y'=2x`（点 Q 与曲线上的 P 共用同一个 tracker `s`，天然逐帧同步，不需要 `parallel` 动作）；「Δx 取 0…」说完后不再直接开扫，而是先放图、再把字幕 `replace` 成下一句、**然后**才开扫（抽帧验证：t=6.5 运动刚开始时屏幕上是第 2 句）。镜 3 元素 8→12、时长 10.6→12.6。见 §8.50 |
| 51 | 公式内分色：新增 `tex_colors` + xcolor 模板（示例「所有 Δ 上黄」） | ✅ | 符号分式里的 Δx 在 `\frac` 内部、拆不出来，改成让 LaTeX 自己上色：`formula()` 套自带 xcolor 的模板（`\definecolor` 的色值从脚本 globals 取 —— PRIMARY/SECONDARY/ACCENT 是 HEADER 里的 hex 字符串，不能用 manim.ACCENT）＋ formula 元素新参数 `tex_colors`（为真则不给 MathTex 传 color）。规格由 ELEMENT_SPEC 自动带出。顺带给示例里那个黄数字补 `shift [0,-0.017]`（包围盒对齐对数字天生差 1px）。见 §8.49 |
| 50 | 示例镜 2 第三轮：Δ 改黄、黄数字落回基线、结尾数字留白 | ✅ | `qf` 拆成 `q_lhs`（`Δy/Δx =` 黄）/`q_rhs`（符号分式白），`aligned=bottom` 对齐；分子里的黄数字改 `aligned="top"` 对 `(1+`（默认包围盒居中会低 2px，实测 1px 是这套对齐的极限）；分子行 buff 0.09→0.12/0.06（数字左偏 2px、同时保持整行宽度对上分数线）；`sub_res` buff 0.05→0.15（离等号太近）。见 §8.47(9) |
| 49 | 示例镜 2 收尾（一行化 / 白骨架黄数字 / 分母同底对齐）+ 顺带修 DSL `aligned` 的渲染期崩溃 | ✅ | 公式、代入分式、结果合并成一行；配色改成"只有会变的数是黄的"（公式本体也白）；竖直对齐改成"两个分式的分母同底"（`aligned=bottom`，之前逐级按包围盒居中会累积漂移）；分数线用 `\overline{\phantom{...}}`，占位串按分子行的**分段**写成三段之和。元素 13→20、时长 13.2→13.4。另修 `dsl.py`：`aligned` 的 top/bottom 会生成不存在的 `TOP`/`BOTTOM`，校验全绿、渲染期才 `NameError`。见 §8.47(9) / §8.48 |
| 48 | 两处静默失效：temperature 被路由写死 + "4~12 秒"只改一半 | ✅ | ① `api/routes/chat.py` / `storyboard.py` 不再硬编码 `temperature`（0.2 / 0.3），改由 `resolve_config()` 决定 —— 与 §8.2 的 `json_mode` 同形，`api/config.py` 那段归属注释一并补全；② §8.31 只把"4~12 秒"改成"建议"，`dsl.describe()` 里"目标秒数（4~12）"漏了，两处都改成"按内容定，上限 120 秒"。见 §8.45 |
| 47 | 解释性文字偏少：拆掉"少写字"暗示 + 示例 1 补字幕 | ✅ | 反模板 3 点破"针对图形元素、不是让你少写字"；新增「每一镜都要有讲解文字」一节（下限 1~2 处、标注用 `next_to` 贴图旁、连续推导用 `replace` 逐句换字幕）；删掉「每个分镜屏幕上元素别超过 8 个」这条软上限（校验层本来就不检查条数）；示例 1 补底部字幕 + 一次 `replace`（duration 9.3→11.7）；顺带修掉规格里 `replace` 示例缺 `place`（不写会跳回画面正中）。见 §8.44 |
| 46 | 提示词基调修正：先讲明白 + 动态中性 | ✅ | 开篇从「唯一任务是产出分镜 JSON」改成「把题讲明白、让人看懂比分镜重要」（每个数哪来的、公式为什么这么写都要交代，不能摆个公式就完事）；「鼓励动态」改为中性的「动态看需要」，`llm.py` 与 `dsl.describe()` **两处同步**改，避免手册自相矛盾。见 §8.43 |
| 45 | 多写参数不再静默丢弃 | ✅ | 元素侧多写参数、动作侧参数名写错，原来都被静静丢掉（元素侧那个 `_dropped` 没有任何地方读）。先量影响面（25 示例 + 992 元素 + 72 份回归 = 0 误伤）再改成硬报错，报错带上"支持哪些参数"并指路 place/动作；新增 `_ACTION_KEYS` 机器可读名单（与 `ACTION_SPEC` 一一对应，有元测试守着）。见 §8.42 |
| 44 | 多轮"改一版"：分镜 JSON 进对话历史 | ✅ | 原来只喂 brief（没有元素/动作），模型没有可照抄的原料 → 倾向整份重做。现在每轮的 raw 分镜存进 assistant 记录的 `meta.storyboard`，发出前还原成"brief + JSON"的历史消息，多轮天然连续、也不局限只带上一版；system 规则 6 要求"只改点名的镜、其余逐项一致"；`diff_scenes()` 打 WARN。**实测：只有目标镜时长 9→10 秒变了，其余三镜连元素参数都逐项一致**（先用摘要的版本会把第 3、4 镜重写）。JSON 中位 1463 token。见 §8.41 |
| 43 | 历史预算可配（6000 → 20000 → 定 10000） | ✅ | 6000 是拍的数，而《工作手册》已 22000 字符 —— 历史只占固定开销的 ~27%，聊到十几轮就丢最早的。实测：输入 14.5k→20k token 耗时**不变**（3.5s→3.1s）；分镜 JSON 进历史后每轮约 1630 token，故默认最终定在 **10000**（够 5~6 轮带画面的对话）。新增 `MSB_HISTORY_TOKENS` 便于扫档位。见 §8.38 |
| 42 | 追问轮的 brief 改成"承接式" | ✅ | 第二轮问「用视频讲、零基础」时，brief 原来是上一轮的复述（prompt 里没有区分首轮/追问轮，且明确禁了"客套话"）。新增规则 4：追问轮先说明"这一轮改了什么、接下来怎么讲"。真调用验证通过。见 §8.37 |
| 41 | 三维接入（最小闭环） | ✅ | `three_axes` + `surface` + `space_curve` + `rotate_camera`；含 3D 的分镜自动继承 `ThreeDScene`，纯 2D 源码逐字节不变；`_check_3d` 拦"z 挂 2D 坐标系""值域顶出坐标轴"，文字按 `place` 自动当屏幕字幕。**顺带修掉 `parametric` 的静默 bug（自由变量 t 从没绑上 → 曲线塌成一个点）**。见 §8.36 |
| 40 | 字幕过渡可配 + 版面按尺寸判定 | ✅ | `replace` 加 `effect`（crossfade 默认/slide/write/morph，且 crossfade 是**分段交接**不是全程叠加）；表格加 `cell_w`/`cell_h`/`pad_x`/`pad_y`（定尺格子用不可见框，`get_cell` 仍精确）；新增 `metrics.py` 纯算术估算器，删掉三类条数上限改成"估尺寸 → 明显超出才拒 + 精确回灌"；`_fit` 按位置算且真缩打 WARN。见 §8.34 |

**前端侧不用改一行**：`NEXT_PUBLIC_USE_MOCK=false` + `NEXT_PUBLIC_API_BASE_URL` 即可切换。

### 部署注意

| 项 | 说明 |
|---|---|
| MiKTeX 约 2GB | Docker 镜像会很重，建议换 TinyTeX 或 TeX Live 精简安装 |
| ffmpeg | 必须装且在 PATH（`generate.py` 用它拼接，失败会退回重编码） |
| 中文字体 | 现用 `STZhongsong`（华文中宋），Linux 容器里需装对应字体 |
| 磁盘 | `output/_parts/` 增长快，必须挂卷 + 定期清理 |
| 超时 | ⚠️ 要按**空闲超时**理解，不是"总时长"：上游静默 N 秒就断链。后端已对全部 SSE 流发心跳（`MSB_SSE_HEARTBEAT_SEC`，默认 15s），网关 idle 阈值设 **≥ 60s** 即可。dev 走的 Next 转发默认 30s（已提到 30 分钟），见 §8.56 |

---

## 5. 关键决策与踩坑（别重复走弯路）

### 5.1 三条不可动摇的架构决策

1. **LLM 绝不写 Manim 代码**（`PROJECT_KNOWLEDGE.md` 决策 1）。
   它只输出受 Schema 约束的 JSON，代码 100% 由 Python 生成。
   理由：静态失败——"代码能跑 ≠ 画对"，公式下标错、坐标越界全部零报错。
2. **元素库 + 时间线 DSL**（决策 5），不是"从 9 个模板里挑一个"。
   加能力的正确姿势是往 `dsl.py` 的 `ELEMENT_SPEC` / `ACTION_SPEC` 里加元素和动作，**不是加模板**。
3. **校验失败回灌重试**（决策 7）：schema/dsl 的报错带精确路径与原因，
   原样回灌给模型，它几乎总能一轮改对。**重试不是碰运气，是把校验层的确定性注入模型的下一轮。**

### 5.2 渲染性能：默认路径就是最优，别再"优化"

这一轮排查先后走了两条弯路，**都比默认路径慢**：

1. 「每分镜一个进程」→ 8 分镜 18.0s（默认 12.3s）
2. 「拆分场景 + 进程内连渲」→ 8 分镜 14.6s

原因：**瓶颈是内存带宽，不是 CPU**。单进程能吃 35.1 GB/s，8 进程并行时每个只剩 7.2 GB/s
（整机约 58 GB/s 封顶），所以每个都慢 4~5 倍，20 个核帮不上忙。

> **教训：遇到同类问题先读一遍 manim 源码**（`scene_file_writer.py` 的 `section` / `combine_files`），
> 再动手造轮子。正确答案（partial 片段是渲染中逐个产出的）就在源码里。

### 5.3 失败分层：什么错该在哪一层解决

```
① LLM 生成 JSON → schema.py / dsl.py 校验（带路径与原因）
   ✗ → 报错原样回灌给模型，最多 4 轮          ← JSON 类问题在这里解决，重试无用
② 生成 Manim 代码 → renderer._check_syntax() 用 compile() 兜底
   ✗ → 渲染器 bug，不是分镜问题
③ 渲染（manim 子进程） ✗ → 环境/代码问题 → 整条重跑（13s，成本可控）
```

**对架构文档 Q12 的修正**：原设计「单分镜失败重试，绝不整条失败」在串行模型下不再成立——
一个 Scene 演到底，任一环节抛异常就是整条失败，不存在"重跑第 3 段"。
但重试成本可控（13.7s），且异常能定位到具体分镜。
建议表述改为：**失败可整条重试 + 重试成本可控 + 失败可定位到分镜**。

### 5.4 前端已解决的坑（后端对接时注意，别把它们又引入回来）

- **`video.load()` 会重置 `volume` 和 `playbackRate`**：任何换源之后都必须重设，
  并同时设 `defaultPlaybackRate`（它才是 load 后 playbackRate 的落点）。
  症状是"音量跳回最大 / 倍速过了一个分镜交界线就变回 1×"。
- **视频播完会先派发 `pause`、再派发 `ended`**。把那个 pause 当成"用户暂停"，
  UI 会在交界处闪一下。判据是 `el.ended`。
- **不要在渲染期探测浏览器能力**（`document.fullscreenEnabled` 之类）：
  服务端渲染时必然为 false、客户端为 true，两边不一致就是一条 hydration mismatch。
- 相对时间（`Date.now()`）也是 hydration mismatch 来源，需要 `suppressHydrationWarning`。

### 5.5 已知边界（不要承诺做不到的事）

前端用双缓冲（两路 `<video>` 交替）消除了"换 `src` 会立即清空画面"那块黑帧。
但切换只能在 `ended` 之后发起，"启动另一路播放管线"本身仍有几十毫秒开销，段越短越明显。

**要做到真正零间隔，需要后端配合**：不要给多个独立 mp4，而是输出 **fMP4 / HLS**
（或一条持续增长的整片），前端用 `MediaSource` 按 `timestampOffset` 串到一个 `<video>` 上。
这是后端契约变更，属于以后的优化项，不是当前阻塞。

---

## 6. 环境与命令

```bash
# ---- 后端（Python）----
D:/Miniconda/envs/manim/python.exe generate.py examples/free_product_rule.json   # 默认：整条 + 分镜片段
python generate.py --spec                                                        # 打印 DSL 规格（喂给模型的 prompt 片段）
python check_env.py                                                              # 环境自检 + smoke test 耗时

# 题目 → 分镜（需 API key：环境变量 MSB_API_KEY / llm.local.json / --api-key）
python generate.py --problem "求 f(x)=x^3-3x 的极值" -q m

# ---- 服务层（FastAPI + SSE）----
run_api.bat                                      # 一键启动（内含 profile / 思考流等开关）
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
curl http://localhost:8000/api/health            # 环境自检 + 生效配置 + 并发情况
# 临时换模型档案（不改配置文件）：
set MSB_LLM_PROFILE=direct && run_api.bat

# ---- 前端（Node）----
cd web
npm install
npm run dev      # http://localhost:3000
npx tsc --noEmit && npm run lint && npm run build
```

**服务层可用的环境变量**（都有默认值，本机开发通常一个都不用设）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MSB_PUBLIC_BASE_URL` | `http://localhost:8000` | 返回给前端的视频 URL 前缀，必须是绝对值 |
| `MSB_CORS_ORIGINS` | `*` | 前端是另一个端口，跨域默认放开，生产收紧 |
| `MSB_QUALITY` | `l` | 渲染质量 l/m/h（`MSB_RESOLUTION` / `MSB_FPS` 可覆盖） |
| `MSB_MAX_CONCURRENT_RENDERS` | `2` | 同时渲染条数（瓶颈是内存带宽，不是核数） |
| `MSB_DAILY_RENDER_QUOTA` | `0`（不限） | 每日渲染配额 |
| `MSB_TASK_TTL_DAYS` | `14` | 过期 task / 缓存清理；只删 `_tasks/` 记过的名字 |
| `MSB_RENDER_TIMEOUT` | `900` | 单条渲染超时（秒） |
| `MSB_LLM_ATTEMPTS` | `4` | LLM 最多调用轮数（含校验回灌重试） |
| `MSB_LLM_JSON_MODE` | `1` | 是否带 `response_format=json_object`；**llm.local.json 里写了就以文件为准** |
| `MSB_LLM_HEADERS` | 空 | 额外请求头（JSON 串），给需要浏览器头的中转站用 |
| `MSB_LLM_REPLAY` | `0`（关） | **回放**：同输入命中留档就不调模型（省钱）。开了横幅会喊，见 §8.9 |
| `MSB_LLM_SHOW_THINKING` | `0`（关） | 把模型思考过程以 `thinking_delta` 推给前端（否则首字要干等）。见 §8.12 |
| `MSB_LLM_PROFILE` | 空 | 用 `llm.local.json` 里哪个模型档案；空 = 用文件里的 `active`。见 §8.13 |
| `MSB_LLM_LOG_DIR` | `output/_llm` | 留档目录；写成 `*.jsonl` 就当成文件本身 |
| `MSB_HISTORY_TOKENS` | `10000` | 多轮对话的**历史预算**（token 估值）。分镜 JSON 进历史后每轮约 1630 token → 10000 够 **5~6 轮**带画面的对话（20000 时约 12 轮）。实测输入从 14.5k 涨到 20k token **耗时不变**（3.5s → 3.1s，噪声内）—— 贵的是 token 账单不是等待。写坏了只 WARN 并回退默认值。见 §8.38 |
| `MSB_PROVIDER` / `MSB_MODEL` / `MSB_BASE_URL` | 空 | 覆盖厂商预设；不给就走 `llm.local.json` → 预设 |
| `MSB_OUTPUT_DIR` | `<repo>/output` | 产物根目录 |
| `MSB_USE_LATEX` / `MSB_PREWARM` / `MSB_FAST` | `1` / `1` / `0` | 渲染开关 |

### LLM 接入（唯一需要人工准备的东西）

**配置只认一个文件：`llm.local.json`**（已在 `.gitignore`，密钥不会进 git）。
代码里没有任何厂商相关的分支，换供应商**只改这个文件**。

**它支持多模型档案**（§8.13）：一份文件里存好几套，改 `active` 就换模型，
不用删改任何东西。当前两档：`relay`（中转站，深度思考，慢）/
`direct`（DeepSeek 官方直连，无思考，首字 0.5s）。每个键都在文件里带了 `_xxx_why` 注释。

```json
{
  "active": "relay",
  "profiles": {
    "relay":  { "provider": "custom",
                "base_url": "https://api.commandcode.ai/provider/v1",
                "model": "deepseek/deepseek-v4.1-flash",
                "api_key": "sk-...",
                "headers": { "User-Agent": "Mozilla/5.0 ...",
                             "Referer": "https://api.commandcode.ai/",
                             "Origin": "https://api.commandcode.ai" },
                "max_tokens": 32768, "json_mode": true, "temperature": 0, "timeout": 300 },
    "direct": { "provider": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
                "api_key": "sk-...", "headers": {},
                "max_tokens": 8192,  "json_mode": true, "temperature": 0, "timeout": 300 }
  }
}
```

**换模型的三种方式**（优先级从低到高）：

1. 改文件里的 `active`（换个默认）
2. 环境变量 `MSB_LLM_PROFILE=direct`（这台机器/这次部署用哪档）
3. 命令行 `--profile direct`（只影响这一次）

⚠️ **档案名写错会直接报错**，不会悄悄退回某一档 —— 那会让人以为切到了 A、实际用着 B。

⚠️ **档案是一整套互相配套的参数，整档生效**。`max_tokens` 这种尤其不能混：
`relay` 要 32768（思考吃预算），`direct` 要 8192（超过上限直接 400）。
早期实现是"只覆盖档案里写过的字段"，切到 `direct` 会继承中转站的 32768 → 必然 400。
所以切档时**先清空再整档合并**。

**旧形态（扁平单模型）原样兼容**：文件里没有 `profiles` 时，整个文件就是一份配置，
老配置一个字都不用改（有测试覆盖）。

优先级：**厂商预设 → `llm.local.json`（档案 → 顶层公共默认值）→ 环境变量 → 命令行参数**。
一切 `_` 开头的键都是给人看的注释，会被忽略。
（顶层标量可以当"所有档案的公共默认值"，只在档案没给那个键时生效。）

⚠️ 四条踩过的坑，都写进了注释，这里再列一次：

1. **`max_tokens` 不是越大越好，但也绝不能给太小**：对**不思考**的模型
   （`deepseek-chat`），超过上限会让**整个请求 400**（不是截断，是失败），上限 8192；
   对**开思考**的模型（v4.1-flash），给 8192 会让**思考吃光预算、正文全空**，
   表现为"JSON 解析失败"。两种模型的甜点值完全不同，见 §8.11。
2. **推理型模型（`deepseek-reasoner`）不支持 `response_format`** —— 把 `json_mode` 设
   `false`。实测关掉后模型仍会按 prompt 输出合法 JSON，所以这条路是通的。
   （注意：这个开关以前是**静默失效**的，见 §8 的修复记录。）
   （另外：v4.1-flash 的思考**走独立的 `reasoning` 字段**，不管 `json_mode`，
   所以它不需要关 `json_mode`。）
3. **有些中转站前面挂着 Cloudflare**，只带 `Authorization` 会 403（`error code: 1010`），
   需要把浏览器 UA / Referer 写进 `headers`。当前这个中转站**必须**这么配，实测去掉就 403。
4. **开思考会牺牲"秒级见字"**：见 §8.11 的对照表（首个可见字 +0.97s → +93s）。

**当前实测（DeepSeek 官方直连，非中转）**：

| 项 | 结果 |
|---|---|
| 简单答复 | 0.9s |
| 流式首个可见字 | **0.53s** |
| 分镜生成（3~5 分镜，含校验） | **5.9~7.0s，一轮通过** |
| `json_object` | 支持（但见 §8.4 的 "json" 字样约束） |
| 概念问答 | `intent=none`、0 分镜，正确 |
| `/api/chat` 端到端 | 5.2~8.2s；首个 `text_delta` **+0.97s** |
| `/api/render/confirm` 首段 | 热态 **+5.7s**；冷启动 +49.9s（见 §2.3 末） |

对照：同一道题走某中转站是 13.6s / 59.7s 且需要重试一轮。所以**优先官方直连**。

**当前实测（中转站 `api.commandcode.ai`，模型 `deepseek/deepseek-v4.1-flash`，深度思考默认开）**：

| 项 | 结果 |
|---|---|
| 简单答复 | 1.6~2.9s |
| **首个可见字（流式）** | **+51s**（正文之前有 ~49s 的思考；见下） |
| 分镜生成（4~5 分镜，含校验） | 43~70s（1~2 轮） |
| `/api/chat` 端到端 | 105s；首个 `text_delta` **+93s** |
| `json_object` | 支持 |
| 思考 | 默认开、且**关不掉**；走独立 `reasoning` 字段，不污染 JSON |
| 必须带浏览器头 | **是**，去掉就 403（`error code: 1010`） |

⚠️ **这个模型必须用 `max_tokens: 32768` 以上**。思考会先吃掉大量 token：
实测一次 5 分镜生成用掉 4741~7700（甚至 12312）个思考 token。
`max_tokens=8192` 时**思考直接把预算吃光，正文一个字都出不来** ——
表现为「JSON 解析失败」，而真正的错因是**截断**，极具误导性。
中转站实测接受到 131072。

⚠️ **深度思考的代价是"首字延迟"**：思考期间正文一个字符都不会流出，
所以 `/api/chat` 的首个可见字从直连的 +0.97s 变成 **+93s**。
思考流（`reasoning` 字段）在 +2s 就开始了 —— 想做"正在思考…"的进度提示，
前端就得把这路接上（后端目前没往 SSE 里放）。见 §8.11。

### 省钱：调用留档 + 回放（§8.9 的用法）

每次 LLM 调用都会落一行到 `output/_llm/calls.jsonl`（**失败的中间轮也留**）。
反复调 prompt / 调渲染时，同输入可以直接回放，不再花钱：

```bash
python generate.py --llm-stats                      # 统计：调用/回放/耗时/token，并单列「其中思考tok」
python generate.py --show-last-reply                # 打印最近一次模型回复原文（取分镜 JSON 最方便）
python generate.py --dump-prompt [文件]             # 导出模型实际收到的完整提示词（默认 output/prompt_dump.md），不调模型
python generate.py --problem-file 题目.txt --replay # 同输入走回放，不调模型
python generate.py --problem "..." --profile direct # 这一次改用 direct 档案（不动配置文件）
```

开深度思考之后 `--llm-stats` 里**「其中思考tok」是关键指标**：当前实测它占输出 token
的 **73%**（`storyboard` 用途：输出 82096 / 思考 59879）。也就是说这个模型的大部分
花费和几乎全部等待都花在思考上 —— 判断"思考值不值"就看这一列。

服务层同效：`set MSB_LLM_REPLAY=1` 后启动即可（横幅会显示「LLM 回放: **已开启**」）。

⚠️ **指纹 = 完整 messages + model + temperature + response_format + max_tokens 的 hash**。
所以**改过 prompt（含 `describe()` 规格、few-shot 示例）就必然不命中** —— 这是对的，
不是 bug：输入真变了，旧回复不该被复用。留档里每条的 `key` 可以直接 diff 定位是
哪一段 prompt 变了。

| 组件 | 版本 | 备注 |
|---|---|---|
| Python | 3.12.14 | `D:\Miniconda\envs\manim\python.exe`（base 是 3.14，太新，别用） |
| Manim CE | 0.21.0 | |
| MiKTeX | 26.5.x | `D:\MiKTeX\miktex\bin\x64`，**不在系统 PATH**，`latex_env.py` 会自动注入 |
| ffmpeg | 任意 | `D:\ffmpeg\bin\ffmpeg.exe` |
| Node / Next.js | 16.3.4 | |

> ⚠️ **性能数据一律以实机为准**：在 AI 沙箱 / 被代理的 shell 里测会虚高约 50%，
> 且固定开销被放大后会出现"改帧率几乎不影响耗时"的假象，据此反推会得出完全错误的结论。

---

## 7. 一句话交接

**渲染、服务层、前端都好了，产品闭环了；真实 LLM（DeepSeek 直连）也接通并验证过了。**
剩下的都是"从能跑到能上线"的事（§4 的 14–18）：幂等回归测试、`parallel` 真并行、
配额换 Redis、真正的分段 LLM 调用、以及零间隔连播那条需要改契约的优化。

联调只需两步：

```bash
python -m uvicorn api.main:app --port 8000     # 后端
cd web && printf 'NEXT_PUBLIC_USE_MOCK=false\nNEXT_PUBLIC_API_BASE_URL=http://localhost:8000\n' > .env.local && npm run dev
```

⚠️ 唯一还需要人工准备的是 **LLM API key**（`MSB_API_KEY` 或 `llm.local.json`）；
没有它 `/api/chat` 会秒回一条写清补法的 `error` 事件，而不是干等超时。
配置细节与实测数据见 §6 的「LLM 接入」。

---

## 8. 修复记录：静默失效类 bug

这一轮对接 LLM 时挖出几个**不报错但功能是坏的**的 bug。它们的共同点值得记下来：
**代码能跑、日志正常、测试不红，但要做的事没做**。本项目的全部设计（校验层、精确报错、
"绝不静默失败"的决策）都是在防这一类问题，结果配置层自己犯了两次。

### 8.1 `render_split()` 缺 format 参数 → `--split` 从来没跑通过

`renderer.render_split()` 组装 HEADER 时漏传 `sections_flag` / `sections_total`，
`HEADER.format()` 直接抛 `KeyError`。也就是说 **`generate.py --split` 从写下那天起就是坏的**，
因为默认路径不走 `render_split()`，一直没暴露。服务层的增量渲染依赖它才撞上。

**教训**：`str.format()` 的缺失参数是**运行时**才炸的，静态检查看不见。
渲染器里这类"模板 + format"的地方，应当在写完后立刻用一次最小输入跑通。

### 8.2 `llm.local.json` 里的 `json_mode` 被无声忽略

`resolve_config()` 用白名单（`if k in cfg`）过滤配置文件，而 `json_mode` 不在 `cfg` 里 →
**写了等于没写**。同时 api 路由又显式传 `json_mode=config.LLM_JSON_MODE`（默认 True），
于是"配置文件里写 `false`"被静默覆盖。后果：想切 `deepseek-reasoner`（不支持
`response_format`）的人会一直撞 400，而配置看起来完全正确。

**修法**（不是加个键那么简单，是把优先级定死成唯一答案）：

- `resolve_config()` 增加 `json_mode` 参数（默认 `None`）+ `effective_json_mode()`：
  **显式参数 > `llm.local.json` > `MSB_LLM_JSON_MODE` > 默认 `true`**，
  `None` 一律表示"交给配置决定"。
- `chat` / `chat_stream` / `generate_storyboard` / `stream_storyboard` /
  `regenerate_scene` 的 `json_mode` 默认值全改成 `None`，路由和 `generate.py`
  不再写死布尔值（`--no-json-mode` 没给时传 `None`）。
- `api/config.py` 里的 `LLM_JSON_MODE` **删掉** —— 同一个开关存两份，
  迟早又漂移回今天这个样子。

已用 8 个用例覆盖验证（文件/环境变量/显式参数的各种组合，含"文件 false + 环境变量 1"
和"文件 true + 显式 false"），全部通过。

### 8.3 同一个 task 名的并发渲染会互相踩踏

`_parts/<name>/`（段源码、`media0/` 下 manim 的 partial movie files、`segments/` 成段）
是**同一个任务的多段渲染共用**的工作目录。两个请求同时用同一个 name 渲染，
会互相删建对方的中间文件。实测踩到过：

```
分镜 3 渲染失败：FileNotFoundError: ...\output\_parts\<name>\media0\...
```

这个报错**指向完全错误的方向** —— 看起来像"渲染器坏了"，真正原因却是"重复触发"，
是最难查的那类错误。

触发路径在真实使用里很常见：**用户点「重试」时上一次渲染还在跑，或者手抖双击**。

**修法**：`jobs.render_slot(name)` 加一把**按 task 名的 asyncio.Lock**，
同名任务的第二次请求排队等第一次结束（而不是直接拒绝 —— 排队正好符合前端契约：
那一段变回"排队中"，用户不需要理解"上一次还在渲"）。

两个实现要点：

- **加锁顺序必须是 `name 锁 → 全局并发闸`，不能反**。若先占全局位再等 name 锁，
  一个重复请求会白占一个并发位（本机只有 2 个），把别的任务饿死。
- 锁对象要**定期回收**（每个 task 一把，会越积越多）。回收判据是
  `not lock.locked() and name not in _active` —— 这是安全的，因为 asyncio.Lock
  在空闲时不可能有等待者（空闲时 `acquire()` 不 await 直接拿到锁）。

验证：清空 `_parts` 制造冷缓存 → **并发**发两个 `retry` → 修复前必有一个失败，
修复后两个都成功。日志显示严格串行：

```
[api] raceFix__ma_raceFix: 增量渲染 —— 待渲 4 段，缓存命中 0 段   ← 第 1 个
[api] raceFix__ma_raceFix: 增量渲染 —— 待渲 0 段，缓存命中 4 段   ← 第 2 个，全命中
```

用时 17.7s ≈ 两次串行（若是并行会是 ~9s），确认锁真的起作用了。

### 8.4 修 8.2 时差点引入的新 bug：策略不该由底层决定

第一版修法是把 `json_mode` 的默认值从 `False` 改成 `None`（= 读配置），
**结果底层 `chat()` 也开始读配置了**。当时用一个普通问题测连通性，直接 400：

```
Prompt must contain the word 'json'   ← DeepSeek 的硬性要求
      ↑ 因为 chat() 自动带上了 response_format=json_object，而那个 prompt 里没有 "json"
```

这是**由一次普通调用和一个环境里的配置共同造成的、看起来毫无关系的报错** ——
和 8.3 那个 `FileNotFoundError` 是同一类病。

**定论：`json_mode` 是业务策略，由上层（`generate_storyboard` / `stream_storyboard` /
`regenerate_scene`）用 `effective_json_mode(cfg, json_mode)` 定好后，把**具体布尔值**
传给底层**。`chat()` / `chat_stream()` 的默认值是 `False` 且**不读配置** ——
底层 HTTP 包装函数绝不根据环境偷偷改 payload 的形状。

顺带记下这条厂商约束：**DeepSeek 要求开了 `response_format` 时 prompt 里必须出现
"json" 字样**，否则 400。分镜 prompt 天然包含它，所以正常路径没事；这也正是
"要不要开"必须由业务层判断的原因。400 的报错提示里已经写了这一条。

### 8.5 顺带加固

- 配置里 **`_` 开头的键一律视为注释**（`_max_tokens_why` 之类不会被当配置读）。
- `resolve_config(json_mode=...)` 的 bool 判断用显式 `is not None`：
  `False` 是合法配置值，用真值判断会把它当"没给"。
- `llm.local.json` 支持 `headers`（有些中转站不带浏览器 UA/Referer 会 403），
  403 的报错提示直接指向这项配置。
- `parse_json_object()` 先砍掉 `final answer:` 之前的思考文本 ——
  有模型的思考是**明文混在 `content` 里**的，不砍会静默解析到思考里举的错误例子。
- 启动横幅打印生效的 `模型 @ base_url`、`json_mode`、额外头，
  省掉"我到底改生效了没有"的猜测。

### 8.6 replace-scene 的"通过校验"是假的：规范化产物被写回了 raw 槽位

**症状**（误导性极强）：`POST /api/storyboard/replace-scene` 回了一条
`分镜校验失败：scene[2].elements[1].x_range: 范围必须是 [min, max] 或 [min, max, step]`，
而同一个进程的日志上一行明明写着 `[LLM 分镜重生成 1/3] 2.7s → 通过校验` ——
两个闸对同一份数据给出了相反结论。

**成因**：整份分镜只有一种合法形态，就是**模型原样输出（raw）**。这一点写在命名里：
`threads/<id>.json` 里存的、`store.save_task_storyboard()` 收的、
`pipeline.iter_full/iter_incremental()` 吃的，参数名全叫 `raw`。
而 `llm.regenerate_scene()` 返回的是 `schema.validate()` 的**规范化产物**，
路由把它写回了 raw 槽位 → 整份变成"raw + 规范化"混装 →
`pipeline._validate()` 对那一段**又规范化了一遍**。而规范化**不是幂等的**。

**不幂等的具体位置（实测，四个都还在，只是不再被踩到）**：

| 输入形态 | 第一遍的结果 | 第二遍 |
|---|---|---|
| `plot` 没写 `x_range` | 补成 `"x_range": null` | 报"范围必须是 [min, max] 或 [min, max, step]" |
| `circle` 没写 `at` | 补成 `"at": null` | 报"点的格式无法识别（收到 NoneType）" |
| `place: [{"next_to": {...}}]` | 展平/补成 `{next_to, direction, buff, aligned}` 四个键 | 报"每条 place 只能有一个布局键" |
| `target` 内联定义元素 | 拆成 `target:[id]` + `inline:{...}` | 报"引用了未声明的元素"（inline 不在 elements 里） |

`plot.x_range` / `circle.at` 的默认值本来就是 `None`（"没写"和"写成 null"在规范化后
无法区分），后两个是"原始形态 + 规范化形态"两种形状都被收下、但只认一种。

**修法**：`regenerate_scene()` 仍然用 `wrapped + schema.validate()` **借闸**
（单分镜校验必须复用同一套规则，否则迟早漂移），但 **`return` 没规范化过的 `cand`**；
路由的合并处补了注释说明这条不变量。这样整份分镜自始至终是 raw，
`_validate()` 只规范化这一次。

**验证**：拿一份模型原样输出的 4 段分镜（其中一段有两个 `plot` 没写 `x_range`），
模拟旧行为把那一段换成规范化产物 → 报出**一字不差**的同一条错误；
新行为（保持 raw）→ 校验通过。再用真实 HTTP 打一次 replace-scene：
SSE 无 `error` 事件、`tool_result`/`tool_done` 齐全、落盘的分镜里没有任何
`"mode":"dsl"` / `"_dropped"` 痕迹、二次校验也通过。

**教训**：`_validate()` 是**唯一的规范化入口**，一份数据在一次数据流里只能被规范化一次。
凡是"返回校验后产物"的函数，只要返回值会流回 raw 入口，就是在埋这颗雷 ——
症状还偏偏是"两个闸互相矛盾"，排查时会一直怀疑校验规则本身。

### 8.7 `parallel` 的子动作被静默丢掉（写了等于没写）

**症状**：`{"do":"parallel","actions":[...]}` 过完 `dsl._norm_action()` 之后只剩
`{"do":"parallel"}` —— `actions` 没被带进返回值。而 `_Builder.act()` 和时长估算**都**读
`ac["actions"]`，于是整段是空转：不报错、不上屏、只生成一行注释。
更糟的是 `dsl.describe()` 里明确向模型宣传过 parallel 的用法，模型必然会用。

**修法**：`_norm_action()` 补上 `parallel` 分支，**递归**规范化子动作并原样带走；
`actions` 不是非空数组就直接报错。

**已知限制（还没做）**：`act()` 目前是把子动作**顺序**展开成多条 `self.play(...)`，
不是真并行（真并行要先把每条子动作编译成"动画表达式"而不是直接写行，属于较大的重构）。
因此 `_est_action_time()` 对 parallel 按**求和**估（与实际行为一致）——
**改成真并行时这里必须同时改成取 max，两处是一对**。

> ✅ **2026-09-15 已做掉**，见 §8.27。上面这条保留作为历史记录，
> 它当时对"两处是一对"的判断是对的 —— 真并行落地时确实两处都得改，
> 而且还多出第三处（`_anim_timed()`，因为 run_time 没法拼在外层）。

**验证**：规范化后 `actions` 保留 2 个子动作；生成的 construct() 里两条 `self.play` 都在；
整段真渲一遍出 mp4（3.5s），SSE 无 `error`。

**教训**：`ELEMENT_SPEC` / `ACTION_SPEC` 是**对模型的承诺**，`_norm_*` 是**兑现承诺的地方**。
给 spec 加了新动作或新参数，必须回头确认 `_norm_*` 把它带出去了 ——
校验层不会替你检查这件事（它只挑"错"，不检查"该带的带没带"）。

### 8.8 给校验层加一条回归测试

上面 8.6/8.7 都能用一条性质测出来：
**`schema.validate(schema.validate(raw))` 不应报错**（即规范化应当幂等）。
它的价值在于：一旦哪天又有人把规范化产物喂回 raw 入口，测试会直接红，
而不是等到线上出现"日志说通过、接口说失败"这种自相矛盾的现象。

**2026-09-15 已落地** —— 见 §8.26。落地时把 §8.6 表里那四个点全部复现，
并且**又抓出第五个不幂等点**（`_dropped` 自我污染）。

### 8.9 LLM 调用留档 + 回放：把「调 prompt」和「花钱调模型」解耦

**动机**：调一次模型十几秒还要真金白银，而调 prompt / 调渲染时会反复重跑同一批输入。
原来的落盘只覆盖"产物"（CLI 的 `output/<name>_storyboard.json`、API 的
`output/_tasks/threads/<thread>.json`），**不能省钱**，因为它们存的是：
解析后的分镜、而且只有最终通过校验的那一版 —— 失败回灌的中间轮全丢，
而"哪一轮错在哪"恰恰是调 prompt 时最想知道的事。

**做了什么**（`storyboard/llm_log.py`，约 200 行）：

- 每次 `chat()` / `chat_stream()` 落一行 JSONL 到 `output/_llm/calls.jsonl`：
  时间、用途（storyboard / scene / chat）、轮次、**完整 messages 与回复原文**、耗时、
  `usage`、错误、以及**请求指纹**。
- 留档埋在**最底层**（`chat` / `chat_stream`）而不是三条上层业务路径里：
  写在一处就不可能漏记，写在三处则将来加第四条必然漏 —— 和 §8.7 是同一个教训。
- **失败也留档**（`ok=false` + `error`）。"这一版把模型逼到 400 了"同样关键。
- 回放：`MSB_LLM_REPLAY=1`（或 CLI `--replay`）时，指纹命中就直接回放、不发请求。
  **默认关**，且开了会在启动横幅喊「**已开启**」—— 回放会掩盖"其实没调模型"，
  这正是本项目最忌讳的静默失效，不能悄悄开。
- `_http_post_json()` 改为返回 `(响应 dict, 原始响应体文本)`；流式那边通过
  `seen` 字典把末包的 `usage` 带出来。**流式必须把流读完**（实测撞到 `[DONE]`
  之后仍可能再来一个带 `usage` 的收尾包，提前 break 就会莫名丢掉 token 统计）。

**实测**（DeepSeek）：流式与真实调用**都带** `usage`，且 `stream_options` 完全不必要
（DeepSeek 默认就回，还附带 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`——
正好用来看厂商侧上下文缓存省了多少）。所以没有引入任何厂商特有参数。

**验证**：留档字段齐全且**不含密钥**（只记 provider host）；同输入第二次仍是真实调用；
开回放后第三次 0.00s 返回且内容一致；**换输入不会误命中**；流式留档的原文等于增量拼接、
回放切块后完全一致；失败调用留档 `ok=false`；`--llm-stats` 汇总正确。
端到端：CLI 真跑一次 → `--replay` 重跑，`[REPLAY] 命中留档，跳过真实调用`，
**6.33s → 0.0s**。

**踩到的"假故障"**：改 `describe()`（§8.10 那次）之后立刻跑 `--replay` **没命中**。
一度以为是回放坏了，逐字段比对留档才发现是 system prompt 从 8445 变成 8571 字符 ——
**输入真的变了，本来就该不命中**。这条要记住：指纹 = 完整 messages + model +
temperature + response_format + max_tokens 的 hash，改 prompt 必然换指纹，
这不是 bug；留档里的 `key` 正好可以用来 diff 是哪一段 prompt 变了。

### 8.10 `"place":[{"center"}]` —— 模型写出非法 JSON，锅一半在我们

**症状**：CLI 生成连续 2 轮都是「JSON 解析失败」，而错误信息只截了原文前 600 字符，
根本看不出哪不对。（**先用 §8.9 的留档把完整原文调出来，才看见问题**。）

**原文长这样**：`{ "id": "tri", "kind": "polygon", ..., "place": [{ "center" }] }`
—— `{"center"}` 少了个 `: true`，是 **JS 的简写语法，不是 JSON**，`json.loads` 直接炸。

**为什么模型会这么写（关键）**：`dsl.describe()` 里对布局键只给了**键名列表**：

```
可用键：`edge`(up/down/left/right) `corner`(UL/UR/DL/DR) `next_to`({of,direction,buff,aligned})
`at_point` `shift`([dx,dy]) `scale` `rotate`(度) `center` `fit_width` `z`
```

其它键都带了值或形态示例，只有 `center` 是个光秃秃的词 —— 连那个 `(度)` 都是很弱的提示。
**模型不是犯傻，是照着我们的文档抄。**

**修法（两侧都补，缺一不可）**：

1. **规格**：`describe()` 里补上带值示例，并明确警告
   `{"center": true}`（不是 `{"center"}`）、`{"scale": 0.8}`、`{"z": 5}`。
2. **解析层兜底**：`parse_json_object()` 里加一个**白名单**正则，把
   `{"center"}` 补成 `{"center": true}`。白名单只有 `center` 这一个键，
   且模式要求裸字符串后面紧跟 `}` 或 `,` —— **不会误伤 `"target": ["a","b"]`**
   这类字符串数组（已验证）。
3. **报错定位**：`json.loads` 失败时把 `line / col / msg` 和出错那一行打出来。
   只说"不是合法 JSON"、再甩一千多字原文，等于没有信息。

**验证**：直接用留档里那条**真实失败原文**跑修复后的解析器 → 解析成功、
`place` 变成 `[{"center": true}]`、整份过 `schema/dsl` 校验通过、渲染成功（4 段分镜）。
字符串数组用例不被改动；坏 JSON 现在会报「第 3 行第 3 列（Expecting ',' delimiter）」。

**教训**：`describe()` 是**给模型的 API 文档**。文档里出现"只列名字不给形态"的条目，
模型就会用自己最熟的那套语法去猜 —— 而它的直觉是 JS，不是 JSON。

### 8.11 中转站 deepseek-v4.1-flash + 深度思考：三个必须知道的坑

**背景**：中转站（`api.commandcode.ai`，模型名带前缀 `deepseek/deepseek-v4.1-flash`）
恢复可用，按要求切过去并开深度思考。

**① 必须带浏览器头**：不带 `User-Agent` / `Referer` / `Origin` 直接 **403
（Cloudflare `error code: 1010`）**。所以 `llm.local.json` 里的 `headers` 是**必需项**，
不是可选优化 —— 这正是当初设计这个配置项的场景。

**② 思考默认就是开的，而且关不掉**。实测：
`thinking={"type":"disabled"}`、`enable_thinking=false` 都不生效（仍有 26~29 个思考 token），
`reasoning_effort` 直接 **400**。所以"开深度思考"**不需要任何参数** —— 不传就是开着。
好消息：思考走**独立的 `reasoning` 字段**，**不混在 `content` 里**，
所以不会污染 JSON 解析（对比 §8.2 里那种"思考是明文混在 content 里"的模型，
那种必须靠 `parse_json_object()` 砍 `final answer:` 之前的文本）。

**③ `max_tokens` 必须给足，否则静默截断**。思考先吃预算再输出正文：

| 场景 | completion_tokens | 其中思考 | 结果 |
|---|---|---|---|
| 5 分镜生成（max_tokens=8192） | 8192（顶满） | 7700 | **正文一个字都没出来** |
| 4 分镜生成（max_tokens=8192） | 8192（顶满） | 8192 | 同上 |
| 4 分镜生成（max_tokens=32768） | 15264 | 12312 | 正常，`finish=stop` |

第一版配置沿用 `8192`，结果**连续两轮"JSON 解析失败"**。
真正的错因是截断，但报错里写的却是
`第 28 行第 11 列（Unterminated string starting at）` + `"kind` ——
**看起来像语法写错，实际是被切断**。已改配置为 `32768`（中转站实测接受到 131072）。

**代价（必须让用户知道）**：思考期间**正文一个字符都不会流出**，
所以"秒级见字"这个卖点在这个模型上**不成立**：

| 路径 | 直连 deepseek-chat | 中转站 v4.1-flash（思考开） |
|---|---|---|
| 流式首个**可见字** | +0.53s | **+51s**（思考花掉 ~49s） |
| `/api/chat` 首个 `text_delta` | +0.97s | **+93s** |
| 分镜生成（4~5 段） | 5.9~7.0s | 43~70s |

但**思考流在 +2.15s 就开始了**（12312 个思考块 / 19709 字符，一直到 +51s）。
也就是说：**如果把这路接进 SSE，用户 2 秒就能看到"正在思考…"，而不是干等 93 秒。**
现在没做：`chat_stream()` 只 yield `content`，`reasoning` 被丢掉
（已列为任务 21）。做的时候要注意这是**推理内容**，默认不该直接暴露给终端用户，
得是显式开关。

**顺手修了自己上一轮埋的坑**：§8.10 加的行列定位，在**截断**场景下会指着
"它最先卡住的那行"，而那行开头是完好的 —— 一度把排查方向带偏。
现在三种情况分开说：① 真语法错给行列；② 未闭合且**报错位置真落在文本末尾**
（按字符偏移判断，不只看行号，否则 4 行文本第 3 行少个逗号会被误报）
→ 明说"八成是截断，去查 completion_tokens 有没有顶到 max_tokens"；
③ 整段其实是合法 JSON、只是顶层不是对象 → 说"要的是一整个 `{...}`"，
**不硬塞行号**（这种情况压根不抛 JSONDecodeError）。

### 8.12 把「思考流」接进 SSE：把 93 秒干等变成 2 秒有反馈

**问题**：开了深度思考之后，思考期间**正文一个字符都不流出**。实测 `/api/chat`
首个可见字 **+93s**（思考 49s + 生成），用户就是纯干等 —— 而这 49 秒里
唯一在动的东西是思考流（+2.15s 就开始了）。

**做了什么**：

- 底层把流式增量**分成两路**：`_iter_sse_stream()` yield `(kind, text)`，
  `kind` 是 `"reasoning"` 或 `"content"`（`_iter_sse_content()` 作为只要正文的
  老接口保留）。思考字段按 `reasoning` / `reasoning_content` / `thinking` 依次找
  —— 各家叫法不一样。
- `chat_stream(..., on_reasoning=None)`：**只 yield 正文**，思考走回调。
  保持这个签名是有意的 —— 它是公共底层，不能为了一个事件类型让所有调用方跟着改。
- `stream_storyboard(..., on_thinking=None)` → 路由转成 `thinking_delta` 事件。
  **思考流只在第一轮转发**（重试轮不重复刷，同 brief 的处理）。
- ⚠️ **默认关**（`MSB_LLM_SHOW_THINKING`）：推理内容里可能有模型对用户的判断、
  或本该藏起来的中间结论，要不要给终端用户看是**产品决定**，不该是默认行为。
  开了会在启动横幅喊出来。

**验证**（中转站 + 思考）：首个 `thinking_delta` **+2.15s**，首个 `text_delta` +53.47s
（`/api/chat` 端到端从 +93s 变成 +53s，且前 2 秒就有反馈）。
服务日志现在会打 `思考=25635字 正文=121字`，一眼能看出"是模型在思考"还是"链路卡住"。
**对没有思考的模型（direct 档案）没有任何副作用**：实测没有 `thinking_delta`、
首个 `text_delta` 仍 +1.23s。

**前端也已接上**（`web/src/components/workbench/ThinkingIndicator.tsx`）：
默认**折叠**，只显示「正在思考… 已 N 秒」；点开看推理片段（只渲染最后 320 字，
否则上万字会拖垮长对话）；**正文一到自动收起**（那时用户要读的是回答）。
Mock 后端也加了 `thinking_delta`（`mock-data.ts` 的 `thinking` 字段），
否则这块 UI 在本地永远测不到。实测 Mock 顺序：`thinking_delta@0.00s →
text_delta@0.78s → plan@1.60s`，共 38 个思考块。

**顺带确认没破坏别的东西**：思考/正文分流后重新验证了留档与回放 ——
留档的 `reply` 只记正文（等于正文拼接，不含思考）、`usage` 正常、
回放内容一致（回放没有思考流，符合预期）。

### 8.13 多模型档案：一次配置里放好几档，改一处就换

**需求**：想同时留着"中转站（质量好、慢）"和"官方直连（快）"，随时切换，
不要再像之前那样换模型就把另一份密钥覆盖掉。

**设计**（`llm.local.json`，**旧扁平配置原样兼容**）：

```json
{ "active": "relay",
  "profiles": { "relay": {...}, "direct": {...} } }
```

换模型三种方式：改 `active` / 环境变量 `MSB_LLM_PROFILE` / 命令行 `--profile`
（优先级：显式参数 > 环境变量 > active）。CLI 启动行会打印 `档案 direct`，
服务横幅打印 `[relay]`，就是为了让"到底用了哪一档"永远看得见。

**两个必须记住的坑（都是我自己先写错、被测试抓出来的）**：

1. **顺序错会"假装换档"**：早期实现是"先合并 active 那一档，再合并指定档案"，
   结果是**只覆盖档案里写过的字段** —— 切到 `direct` 时 `headers` 和
   `max_tokens=32768` 都留着 `relay` 的值，直连模型必然 400。
   而且 `MSB_LLM_PROFILE` 放在后面处理，**环境变量换档完全不生效**。
   改成"**先定用哪个档案，然后只合并那一个档案**"才对。
   → 一句话：**档案是一整套互相配套的参数，必须整档生效。**
2. **`only_missing` 不能用"值是否为空"判断**：顶层标量可以当公共默认值，
   但 `temperature`/`max_tokens`/`timeout` 在 cfg 里本来就带着非空默认值，
   按"空值"判断的话用户写在顶层的默认值**会被永远忽略且不报错**。
   判据必须是 `k not in from_file`（这个键是不是档案给过）。

**写错档案名直接报错**（列出可用档案），**绝不悄悄退回某一档** ——
否则用户以为切到了 A、实际用着 B，正是本项目最忌讳的静默失效。

**验证**：9 个用例全过 —— active 默认 / 显式 `--profile` / 环境变量 /
显式压过环境变量 / 错名报错 / **换档时 headers 与 max_tokens 都不残留** /
旧扁平配置兼容（含 `json_mode:false`）/ 顶层公共默认值只在档案缺席时生效 /
还原后配置正确。另外真跑了两档：`--profile direct` **7.0s 一轮通过**
（你的 DeepSeek 直连完好），`relay` 43s 一轮通过。

**顺手踩到的一个非代码坑**：写 `run_api.bat` 时里面放了中文注释，
**cmd.exe 按 GBK 读 .bat**（不是 UTF-8），中文乱码后整个脚本解析崩掉，
报一堆莫名其妙的"`set` 不是内部或外部命令"。`.bat` 一律只写 ASCII，
中文文档留在 HANDOFF。

### 8.14 前端配置模型：密钥留在后端，前端只选"用哪一档"

**需求**：设置页要能配模型 API。

**先说清楚一件容易走错的事**：密钥**不能**放在前端配置里。
前端配置（`web/.env.local`）里的 `NEXT_PUBLIC_*` 会被**打进浏览器那份 JS**，
任何人开 DevTools 都能看到。所以这条路走不通 —— 密钥必须留在后端，
前端只发一个"档案名"过去。

**接口**（`api/routes/settings.py`，三个）：

| 接口 | 干什么 |
|---|---|
| `GET /api/llm/profiles` | 列出所有档案 + 当前生效值 + 保存路径。**只回 `has_key` 布尔，永不回密钥原文** |
| `POST /api/llm/active` | 切换生效档案（写文件 + **回读校验**） |
| `POST /api/llm/profile` | 新增 / 修改一档（模拟设置页的"加一个模型"，如本地 Ollama） |

**四个刻意的决定**：

1. **密钥只进不出**。不回原文、也不回后四位 —— 服务虽然绑 127.0.0.1，
   但"本机"不等于"只有自己"，中间还隔着一个浏览器。用 `has_key` 表达"配好了没"就够。
2. **`api_key` 留空 = 保持原值**，不是清空。前端拿不到旧密钥，如果留空当清空，
   用户每改一次模型名就得重打一遍密钥。后端按这个语义实现，前端也照此提示。
3. **改完不用重启服务**。配置是每次调用 `resolve_config()` 现读文件的，
   所以切换立刻生效 —— 这一点和 `MSB_LLM_PROFILE` 环境变量不同（后者要重启）。
4. **写坏了要能还原**。保存档案前先备份原文件，写完立刻 `resolve_config()` 试一遍，
   失败就把备份写回去 —— 绝不留下"半坏的配置"让用户下次提问才发现。

**另一条容易骗人的地方**：设置页同时显示"档案列表"和"**当前生效值**"。
两者不一致时（文件里写 A、实际跑 B），以生效值为准 —— 这个"生效值"就是我上一轮
加 `resolve_config(profile=...)` 时留的那个 `cfg["profile"]`，现在有用了。

**前端**：`web/src/components/workbench/ModelSettings.tsx`（挂在设置页）。
Mock 模式下显示"未接入"而不是空列表 —— 对着一个假后端显示模型配置是骗人的。
真实链路验证：列出 2 档 → 切到 direct（`deepseek-chat` / 8192）→ 切回 relay
（`deepseek-v4.1-flash` / **32768 跟着档案走**）→ 响应里搜不到 `api_key` 字段。

### 8.15 前端切到真后端（`web/.env.local`）

```bash
NEXT_PUBLIC_USE_MOCK=false
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

⚠️ `NEXT_PUBLIC_*` 是**构建期内联**的，改完必须重启 dev server 才生效
（不是运行时读的）。重启后 dev server 会打印 `Environments: .env.local`，
这句话就是"确实读到了"的证据。

**实测前后对比**（同一句"求 f(x)=x^3-3x 的单调区间与极值"）：

| | Mock | 真后端（relay + 思考） |
|---|---|---|
| 首个 `thinking_delta` | 0.00s（假节奏，12ms/块） | **2.3s** |
| 首个 `text_delta` | 0.78s | **17.9s** |
| `plan` | 1.60s | **24.7s** |
| 分镜来源 | `mock-data.ts` 写死的两套 | LLM 现生成 |
| 视频来源 | `public/demo/` 里 10 个预渲 mp4 | Manim 现渲染 |

Mock 那两个数字是**故意做快的**（演示不该让人等一分钟），所以**不能拿 Mock 的
延迟去判断真实体验** —— 真后端首个可见字 17.9s、整条链路分钟级，这才是现实。

### 8.16 多轮对话上下文（用户实测踩到的坑）

**症状**：先问「讲一下傅里叶变换吧」→ 模型判定纯概念问答（`intent=none`，正确）；
接着发一句「生成对应视频」→ 模型回：

> 这次只收到了一句指令，没有附带任何题干、条件或要求的结论，所以无法为某个具体的
> 数学题设计分镜……请把完整的题目发过来。

**这不是 bug，是缺失的功能**：`generate_storyboard` / `stream_storyboard` 拼出来的
messages **只有 system + 当前这一句**，模型确实看不到上一轮。它那句反问是诚实且正确的。

**按主流路线补上**（ChatGPT / Claude / DeepSeek 网页版的做法，三条缺一不可）：

1. **服务端存会话消息**：`output/_tasks/messages/<thread>.json`，
   存**原始 user/assistant 文本**。
   - 为什么不复用 `threads/<thread>.json`：那个文件回答的是"confirm 时该渲哪份分镜"，
     和"模型该看到哪些历史"是两件事。混在一起会导致"清分镜把对话也清掉"这类事故。
   - 为什么不存"用户问题 + brief"这种压缩版：追问里全是「刚才那个第二步」，
     压缩后指代就断了。**存的完整，省的交给喂给模型时的裁剪。**
   - 为什么**不存分镜 JSON**：几千 token 一份，既吃光预算又让前缀缓存次次落空
     （厂商按 token 计费，缓存命中便宜一大截）。助手那轮存 `brief` 就够支撑指代。
2. **按 token 预算裁剪**（`llm.trim_history`，默认 6000 token，粗估：
   中文 1 字≈1 token、英文 4 字符≈1 token）。两条硬规则：
   - **从最旧的开始丢**（不是从中间切）；
   - **切到"用户"这条边界为止** —— 从中间切断会出现"assistant 有回答、却没有对应提问"，
     模型会开始瞎猜；
   - 另留 `min_turns=4` 兜底：用户一次贴篇论文时预算会被单条吃光，
     裁完空列表等于彻底失忆，还不如不裁。
3. **显式告诉模型"这可能是指代"**：`build_user_prompt_multi()` 里点明
   「本次请求可能是对上一条的追问或指代……不要因为这一句本身没给题目就反问用户」。
   光给历史不够 —— 模型看到「生成对应视频」仍可能保守地反问。

**实测（就是上面那个场景）**：

| 轮次 | 输入 | 修前 | 修后 |
|---|---|---|---|
| 1 | 讲一下傅里叶变换吧 | `intent=none`，5.9s | 同（本来就对） |
| 2 | 生成对应视频 | **反问用户"请把题目发来"** | **`intent=propose`，4 个分镜**（43.6s） |

分镜是「看起来乱的信号（7s）→ 用一个频率去打分（12s）→ 频谱：三根谱线（7s）→
按得分叠回去（10s）」—— 它真的接上了上文。

**顺带说清一个设计边界**：历史**只在 `/api/chat` 这条路上传递**。
`replace-scene`（AI 改某个分镜）不需要 —— 它的指令本来就带着完整分镜上下文，
再叠加会话历史反而会让模型把两个话题搅在一起。

### 8.17 会话持久化：刷新后完整恢复工作台

**需求**（用户原话）："不管哪种都做不到持久化存储" → 选定「完整恢复工作台
（刷新后分镜大纲、每条消息的渲染状态、已出片视频都能回看）」，且"存后端"。

**先说清楚一件容易搞错的事**：后端**早就在持久化了**，只是没有读取出口。
`_tasks/` 下四类文件一直都在：`messages/<t>.json`（对话文字）、
`threads/<t>.json`（该会话当前这一版分镜）、`<t>__<m>/{storyboard,meta}.json`、
`videos/<name>_scene/<qdir>/`。而 `api/routes/` 里**一个 GET 都没有** ——
`store.load_thread_messages()` 只被 `chat.py` 内部拿去喂模型。
所以这件事不是"从零做持久化"，是"接一根线"。

**还有一件更要命的**：`/api/chat` 当时**没把 message_id 落盘**。
`store.task_name(thread_id, message_id)` 是定位渲染产物的唯一钥匙，
而 id 只存在于 SSE 的 `done` 事件里（前端收到后放内存）。刷新即失联 ——
后端存着全套产物，没人知道该用哪个名字去取。

**设计**（三个决策，都是为了"不引入新的静默失效"）：

1. **补齐外键，而不是新建一套存储。** 前端本来就在本地生成 id
   （`uid("ma")` / `uid("mu")`），confirm 时也一直把 assistant 的那个 id 当
   message_id 发给后端。现在只是**随 `/api/chat` 也发一份**，后端写进
   `append_thread_message` 本来就支持的 `meta` 里。字段全是可选的：不传时后端照旧
   自己生成，旧客户端行为一个字都不用改。同一个 meta 顺手存下 `plan` 与 `intent`
   —— 于是每条消息有了**自己那一轮**的大纲，而不只是"会话当前这一版"。

2. **渲染状态从磁盘重建，不碰渲染热路径。**

   | 事实 | 来源 |
   |---|---|
   | 每段的真实时长 | `sections/index.json`（`write_index` 在两条渲染路径上都会写） |
   | 哪几段真的交付了 | `sections/*.mp4` 的**文件名集合**（一次 listdir，不逐段 stat） |
   | 成片在不在 | `videos/<name>_scene/<qdir>/StoryboardScene.mp4` |

   状态推导与串行渲染的真实形态对齐：**第一个缺口的段是 `error`，其后一律
   `queued`** —— 因为"一个 Scene 演到底"，某段崩了之后后面的分镜根本没执行过，
   标成"全都失败"是错的。全程**不跑 ffprobe**（进程级开销，一个会话就能拖到秒级）。
   渲染热路径一行没动，也就不会多出一个新的静默失效面。

3. **种子数据改为显式演示模式。** `store.ts` 的初始态从 `SEED_THREADS` 改成空，
   预置内容只在 `NEXT_PUBLIC_DEMO_MODE=true` 时启用。以前这里是无条件用种子的，
   于是"后端没连上"和"真有历史数据"在界面上**长得一模一样** —— 分不清就一定会
   误判（用户就被骗过一次：他以为是"请求失败回退到假数据"，其实那批种子跟请求成败
   毫无关系，一次都没消失过）。

**接口**（`api/routes/threads.py`，两个，**只有 GET**）：

| 接口 | 干什么 |
|---|---|
| `GET /api/threads` | 会话摘要列表（按最近更新倒序）+ `ttl_days` |
| `GET /api/threads/{id}` | 一个会话的完整消息，含每条助手消息的渲染状态与视频地址 |

写接口一个都不加：写入路径都已经存在、且都在它们该在的地方，多一个写口径就多一个
可能与磁盘不一致的地方。**会话不存在时返回空列表而不是 404** ——
"新建会话 → 立刻跳进去"这条正常路径上，那个 id 本来就还没落到磁盘。

**三个非做不可的细节**：

- **时间戳的秒/毫秒**。后端 `time.time()` 是秒，前端 `Date.now()` 与
  `ThreadSidebar.relative()` 全按毫秒算。转换只在 `store._now_ms()` 一处做，
  漏一处界面上就是"1970 年前"。
- **质量目录必须随产物落盘**。`rebuild_render_state` 一度按"当前配置"算产物目录，
  于是中途改过 `MSB_QUALITY` 就会跑去别的目录找文件 —— 视频集体消失且不报错。
  现在 `save_task_storyboard` 往 `meta.json` 里写 `quality_dir`，读侧优先用它。
  另外把 `section_rel` / `final_rel` / `quality_dir_name` 的**唯一实现**收敛到
  `store.py` / `config.py`（pipeline 改为转发）：写产物和读产物各存一份命名，
  迟早漂移，而漂移的后果就是一整片 404。
- **旧数据要能挂回产物**。改造前的 messages 文件里没有 id，但 **task 目录名里有**
  （`<thread>__<message>`，confirm 时写进去的）。所以按**区间认领**：渲染必然发生在
  这轮回答之后、用户下一次发言之前，`created_at` 落进这个区间的就是它。
  这是有据可依的匹配，不是时间就近猜 —— 猜错会把 A 问的视频挂到 B 问底下，
  比"没有视频"更糟。实在认不到（产物已被清理）就补一个位置推导的稳定 id
  （`ma_hist_3`），那些消息只有文字没有视频，**诚实地降级**。

**验证**（对真实历史数据跑的，不是构造的用例）：

```
$ python -c "...store.load_thread_detail('t_4rf2aue')..."
0 mu_hist_0     user
1 ma_hist_1     assistant  intent=none    state=none
2 mu_hist_2     user
3 ma_a15bhgm    assistant  state=confirmed render=done total=5 final   ← 区间认领回来的旧产物
4 mu_hist_4     user
5 ma_pj5buut    assistant  intent=propose state=confirmed render=done total=4 final
```

- `rebuild_render_state` 读出的时长是 `7.067 / 9.4 / 8.4 …`（`index.json` 里的
  真实帧时长，不是大纲估算值），地址带 `?v=<mtime>` 版本号。
- 分镜标题按"**只认这条消息自己那一轮的大纲**"处理：有自己 plan 的用真实标题
  （「正弦波与频率」…），没有的退化为「分镜 N」。拿会话当前这一版去兜底会让标题串轮。
- 会话列表读出 5 条真实会话，`updatedAt` 已是毫秒。

**踩到的坑：StrictMode × ref 守卫 × cleanup 三者合起来，会让水合永远不发生。**

首版把"这个会话已水合过"的标记打在**发请求之前**，于是 next dev（`reactStrictMode`
默认开，本项目没关）下：

1. 第一遍 effect：打标记 → 发请求 → 立刻被 cleanup（`alive=false`）
2. 请求回来了，但 `alive` 已是 false → **不写入**
3. 第二遍 effect：看到标记 → 以为"已经水合过" → **直接跳过**

两边都不写。症状是**侧栏列得出会话、点进去一个字都没有** —— 而拉列表那个 effect
没有这层守卫（它的失败无害），所以列表照常显示，看起来就像只有详情坏掉了。
修法：标记只打在**成功回调**里（失败也不打，于是下次还能重试）。

**已知边界**（不做过度设计，但要说清楚）：

| 边界 | 表现 | 原因 |
|---|---|---|
| ~~手动改的会话名~~ | **已修复** | 见 §8.18：标题落在 `_tasks/sessions/<t>.json`，用户改过的优先 |
| "放弃大纲" | 恢复后变回"待确认" | 那是前端的本地状态，不上报 |
| v1/v2/v3 编号 | 恢复后统一显示 v1 | version 由前端生成、未随请求上报（要保真需在 replace-scene 请求里补带） |
| ~~数据保留~~ | **已改为永久保留** | 见 §8.20：默认不再自动清理；即便开启，也只清渲染产物，会话记录永不自动删 |
| assistant 的 brief 为空 | 这条记录不落盘 | `append_thread_message` 对空文本直接返回（原语义：不记空的自然语言）。改成空 content 落盘会把空消息喂给模型，风险更大 |

**Mock 模式**：`USE_MOCK` 时**不请求** `/api/threads`（Next.js 侧本来也没有这个
handler），直接呈现空态。侧栏会区分"Mock 模式没有后端"和"还没有历史会话，
或后端未连接"—— 这两种情况在界面上长得一样，但排查方向完全不同。

### 8.18 侧栏菜单：重命名 / 置顶 / 分享 / 删除 / 多选

**需求**：用户拿别的产品的截图（重命名、取消置顶、分享、多选、删除）说
"左边栏每个对话项没有像这样的按钮"。

**先说清楚一个结构问题**：这几件事存的东西**都不是渲染产物**，所以不能塞进
`messages/<t>.json` 或 `threads/<t>.json` —— 那两个文件各有各的写入方
（chat 追加消息、render 覆盖分镜），而 `_write_json` 是**覆盖写**，
塞进去的字段会被下一次写入整体带掉。
所以新开一个 `_tasks/sessions/<thread>.json`，只存"用户改出来的属性"：
`{title, pinned, share}`。而且写入是**合并**不是覆盖 —— 否则"先分享、后置顶"
会把分享链接悄悄抹掉（又一种静默失效）。

**接口**（`api/routes/threads.py` 扩了四个）：

| 接口 | 干什么 |
|---|---|
| `PATCH /api/threads/{id}` | 改标题 / 置顶。标题截断 60 字；会话不存在直接拒 |
| `DELETE /api/threads/{id}` | 删会话**连产物**（成片、分段、增量缓存、生成的 py） |
| `POST /api/threads/{id}/share` | 开 / 关分享，返回随机 slug |
| `GET /api/share/{slug}` | 公开读分享（**不需要、也不能带鉴权**） |

**四个刻意的决定**：

1. **删除用整段前缀识别**（`safe_id(thread) + "__"`），绝不模糊匹配 ——
   否则 `t_4rf2aue` 会误伤 `t_4rf2aue2` 的产物。测试里专门放了一个同前缀的
   "邻居"来盯这条。
2. **分享链接由前端拼**。后端只知道自己的地址（那是 media 的前缀），不知道前端
   跑在哪个端口。拼出一个打不开的链接，是那种很难被当成 bug 看出来的错。
3. **分享页只放视频，不放对话**。会话里有用户的问题原文和追问；分享的语义是
   "这段视频给你看"。页面还必须是 `force-dynamic` —— 缓存一个已经取消的分享，
   比给一个 404 更糟（用户会以为还能看）。
4. **先改本地、失败再还原**。改名/置顶按下去就该立刻看到结果；但请求失败时
   必须把界面还原回原样 —— 留着"成功的样子"会让用户下次刷新才发现白改了。

**只有读取的那半边也补上了**：`list_threads` 现在读 sessions 元数据，返回
`pinned` / `shared`，并且**排序在服务端定**（置顶优先，再按最近更新）——
本地改完也照同一规则重排，否则"刚置顶的那条"要刷新一下才跳上去。

**顺带修掉一个判断失误**：`list_threads` 原本取 `messages/` 与 `threads/` 的
**并集**，于是 `frontendcheck` / `relaythink` 这些 **CLI 直接跑 `generate.py`**
留下的名字也被当成了会话 —— 它们压根没有对话记录，点进去一片空白（用户当场撞上）。
现在只以 `messages/` 定成员，`threads/` 退居"补充分镜数"。
另外给空会话补了引导文案（以前消息为空时 `ChatStream` 什么都不渲染，是纯白板）。

**验证**：

```
PATCH 改名 / 置顶 / 空标题被拒 / 不存在的会话被拒      ALL PASS
分享 → slug → 公开可读（带成片、响应里不含 messages）  ALL PASS
重复开分享不换 slug / 未知 slug 报错                  ALL PASS
DELETE：对话+快照+元数据+task+缓存+成片 全清          ALL PASS
DELETE：同前缀的邻居没被误删                          ALL PASS
排序：置顶的跳到最前（哪怕它更旧）                     ALL PASS
无成片时 build_share_payload 返回 final=null          ALL PASS
```

**前端**：`ThreadSidebar` 加 `...` 菜单 —— 项目里 `ui/popover.tsx` 早就有整套
`DropdownMenu`，不用从零写。两个踩到的细节：

- 改名走**内联输入**，不弹窗。它有个隐蔽的坑：Enter 提交之后输入框还会失焦，
  `onBlur` 会**再提交一次**。用 state 挡不住（闭包里读到的还是旧值），
  所以用 ref 做一次性闸门。
- 菜单按钮在小屏**常显**、大屏才 hover 出现。触摸设备没有 hover，
  写成清一色的 `opacity-0 group-hover:opacity-100` 的话，手机上这个按钮永远点不到。

**没做的**：批量删除是前端循环调 DELETE（接口一次只认一个 id）。会话量很小，
不值得为它多开一个批量入口 —— 多一个批量接口就多一处"部分成功"的歧义要处理。

### 8.19 新建对话不再立刻产生会话（草稿模式）

**用户反馈**（原话）："连续新建对话后网址也跟着变，而且假设我没进行任何对话后切换到别的
对话，那条新的对话就消失了……我的建议是新建对话的时候本身网站还是在 app/，一旦对话真
开始了，再在左边栏添加对应对话并且网址也相应变化"。

**问题在哪**：原来"新建"= 生成 id + 立刻入侧栏 + `router.push('/app/t/<id>')`。
于是用户只是**点了一下**（一个字没说）就已经产生了一条会话记录：URL 变了、侧栏多一条
「新的讲解」，而且切走也不会消失 —— 点几次攒几条。
根子上是把"新建"当成了一个**写操作**，而它其实只是"准备开始"。

**改法：`/app` 本身就是一个"草稿"界面。**

- `/app` 直接渲染 `<Workbench />`（**不带 threadId**）：不生成 id、不写记录，URL 就停在 `/app`。
- 草稿界面的样子是**输入框摆在正中间**，并且**没有引导文案** —— 一屏只有一个输入框，
  看到就知道该干什么，再补一段"把题目发给我……"只是噪音（这条也是用户点名的）。
- **id 在发出第一条消息那一刻才生成**（`send` 里的 `const tid = threadId ?? uid("t")`），
  同时才 `ensureThread(tid, 首句前 20 字)`、才 `router.replace('/app/t/' + tid)`。
- 于是"没说过话的会话"压根不存在于任何地方，也就无所谓"要不要清理它"。

**顺带必须解决的一件事**：改 URL 会换 page 组件（`/app` 与 `/app/t/[threadId]` 是两个文件），
Workbench 会被**卸载重挂载**。而流是 JS 的 promise —— **不会因为组件卸载而停止**，
它还会继续跑、继续往 store 里写。所以：

1. **中止控制器移到模块级**（`web/src/lib/streams.ts`）。留在组件的 ref 里的话，
   跳转那一瞬间"停止"按钮就失效了：流还在跑，却停不掉。
2. **"是否正在生成"改为从消息派生**
   （`messages.some(m => m.streaming || m.render?.status === "running")`），
   不再存一份 `busy` state。派生值在新组件里立刻就是对的；存一份就会归零，
   表现成"生成中却又能再点一次发送"。顺带修掉一个旧毛病：切走再切回来，busy 不再丢。

**其它连带调整**：

- `ensureThread(id, title?)`：支持带标题，且**只在首次发消息时调用**。
- `hydrateThreads` 改为**整体替换、不做保留** —— 曾经会把"当前会话"补回列表
  （怕它从侧栏消失），那正好和这次要的行为相反：后端没有的会话就不该在列表里。
- 删掉 `newThread` 与 `renameThread`（后者已被 `ensureThread(id, title)` 取代）。
- `ChatStream` 里那段空态引导卡片删掉：空会话现在走"居中输入框"分支，不会到它那里。
- 所有"新建"入口（顶栏、侧栏、移动端抽屉）统一指向 `/app`。

**没改的**：`/app/t/<id>` 仍然可以直接打开（含刷新）。一条没有消息的会话在那里会
显示成居中输入框 —— 它不出现在侧栏里，因为后端也没有它（列表以 `messages/` 为准）。

### 8.20 永久保留 + 草稿界面的观感

**用户反馈**（两件事）：

1. "会话保留 14 天是怎么回事，还是要一直保存才好吧" → 随后补一句：
   "渲染产物也别清，就是一直留着"
2. "你看现在这样感觉很空"（贴了自己的 `/app` 截图 + DeepSeek / 豆包的截图）

#### 第一件：保留策略，口径改了两次

- **原来**：`MSB_TASK_TTL_DAYS` 默认 **14**，`cleanup_stale()` 在**服务启动时**按它清
  `_tasks/` 下的 task 目录，并且**顺带**按 mtime 清 `threads/`、`messages/`、`sessions/`
  里的文件。
- **问题在那个"顺带"**：用户配一个天数，本意是"别把磁盘塞满"，
  结果**会话历史也一起没了** —— 而他只会看到"过一阵我的对话就没了"，
  绝不会联想到那个自己都没印象的配置项。
  又一类"一个动作顺带做掉一件用户没预期的事"，和 8.1~8.16 那批是同一个病。
- **先把两件事分开**：清理只针对**渲染产物**（task 快照 / 增量缓存 / 成片 / 生成的 py），
  `messages/`、`threads/`、`sessions/` 三个会话目录**永不自动清理**。
- **用户随即要求产物也别清 → 默认值改为 0（不自动清理）**。
  现在只有**用户手动删会话**（`DELETE /api/threads/{id}`）才会动磁盘上的东西。
- 启动横幅把结论直接打出来，省得以后怀疑"是不是被清了"：
  `保留策略 : 永久保留（未开启任何自动清理）`。
  这类"数据到底会不会被清"的疑问最难查，所以宁可每次启动都显式说一遍。

#### 第二件：空界面太素

按参考产品的做法补了三处。注意**没有**加回说明文案 —— 那正是上一轮用户点名去掉的。

1. **输入框上方一句居中标题**：`把一道题讲成一段动画`。
   参考产品在同一位置都有一句问候/定位语（DeepSeek「晚上好，有什么我能帮你的吗？」、
   豆包「有什么我能帮你的吗？」），它回答的是"我在这儿该做的第一件事是什么"。
   ⚠️ **只一句，不写第二行** —— 上一版就是被那两行说明顶掉的。
2. **推荐问题从输入框上方移到下方**，并从 3 条扩到 6 条（导数 / 勾股定理 / 极值 /
   极限 / 归纳法 / 定积分）。放下方是主流位置（豆包的「为你推荐」、DeepSeek 的功能行
   都在输入框下面），放上面会把输入框往下顶、视觉重心不对。
   它不算"提示文案"，所以空态（`compact`）下照样显示 —— 空界面正需要它撑住内容，
   也顺带说明"这东西能答哪些题"。
3. **空会话时不渲染右栏**，同时把顶栏那个预览按钮一起收起来。
   一块写着"还没有成片"的空白面板占着三分之一的屏幕，本身就是"空"的一部分；
   而留一个点了没反应的按钮，比没有这个按钮更让人困惑。有产物了它自己会回来。

### 8.21 输入框定稿：卡片化 + 框内工具条（含两处遗留）

**这一整轮会话（§8.17 → §8.21）的产出总览**：

| 小节 | 产出 | 落在哪 |
|---|---|---|
| §8.17 | 会话 / 渲染产物持久化：两个只读接口 + `message_id` 随 `/api/chat` 落盘 | 后端 + 前端 |
| §8.18 | 侧栏菜单：重命名 / 置顶 / 分享 / 删除，新增 `_tasks/sessions/<t>.json` | 后端 + 前端 |
| §8.19 | 草稿模式：`/app` 不发消息就不产生会话，URL 与侧栏都不动 | 前端 |
| §8.20 | 永久保留（默认不清理）+ 空界面的标题与推荐问题 | 后端 + 前端 |
| §8.21 | 输入框改成卡片 + 工具条进框内（本条） | 前端 |

**用户反馈**（原话）："这输入框高度一点变化也没有啊，你是不是改错了"、
"我也没想好，要不改成模型切换好了"。

#### 改了什么

1. **输入框从"一行"改成"卡片 + 两行"**：
   - 结构：**文本域一行 + 工具条一行**。原来是 `items-end gap-2` 的单行（按钮挂在文本域右边）。
   - 文本域：`flex-1 text-sm leading-6 max-h-42` → `w-full text-base leading-7 max-h-60 min-h-[2.5rem]`
     （正文 14px → 16px；超过 168px 后框内滚动）。
   - 卡片两档：空态（`compact`）`rounded-2xl px-4 pt-3.5 pb-3` + 更重的投影；
     对话中 `rounded-xl px-3.5 pt-3 pb-2.5` + 更弱的投影（别跟正文抢注意力）。
   - 投影**不加负 spread**：输入框在 DOM 里排在对话流之后，影子画在内容之上是"浮起来"
     该有的样子，不是污渍。这条写进了代码注释，免得下次有人来"修"。
2. **工具条进框内**（DeepSeek 的「深度思考 / 智能搜索」、豆包的功能行都在这个位置）。
   这里曾经放过「精简 / 详细 / 活泼」的风格切换，已删 —— 设置页已经有一份，
   两处都能改的话，看的时候一定会怀疑"以哪个为准"。
3. **推荐问题**：只在空态出现，放在**框下方**；开始打字时**淡出但保留占位**
   （`opacity-0` 而不是条件渲染）—— 空屏是垂直居中的，条件渲染会让整组内容在打字时往下跳。
4. **外层容器顶部 padding 归 0**（`py-3` → `pb-3`）：页面底色与卡片白色明度太接近，
   那 8px 夹在白色视频卡片和白色输入框之间不像留白，倒像一块贴错位置的面板。
5. `Workbench` 把输入框抽成 `composer(compact)`：空态（含草稿）传 `compact`、对话中不传，
   两处布局共用一份。

#### 两处遗留（已由 §8.22 做掉）

1. ~~**工具条里要放"模型切换"**~~ **→ 已做**，见 §8.22。做的时候确实不需要新接口 ——
   §8.14 的 `GET /api/llm/profiles` + `POST /api/llm/active` 就是它；
   Mock 模式下显示"未接入"而不是空列表（对着假后端显示模型配置是骗人的）。
2. ~~**高度没谈拢**~~ **→ 已做**，见 §8.22。实测：`min-h` 从 `3rem` 调到 `2.5rem`，
   整块只矮了 **8px**（空态 122 → 114px、对话中 118 → 110px），用户的原话是
   "高度一点变化也没有"—— 这么改确实看不出来：整块高度里占大头的是
   **工具条那一行（icon 按钮 `h-10`）**和上下内边距（26px），只动 `min-height` 动不到观感。
   （收尾时用户说"算了"，所以当时保持原样；下一轮改成"按钮行高 + 内边距 + 间距"一起收
   才真的动了 18px。这条教训写进了 `Composer.tsx` 的注释。）

### 8.22 输入框收口：模型切换进工具条 + 对话页那一档压矮

**用户反馈**（原话）："对于正常对话页（非新对话）下的对话框高度减小一点，
并且加上模型选择在对话框左下角" —— 正好就是 §8.21 记下的那两处遗留，一起做掉。

#### 1. 工具条改成两栏：左选模型、右发送 / 停止

新增 `web/src/components/workbench/ModelPicker.tsx`：工具条左侧一颗 `h-8` 的小胶囊，
显示**当前生效的模型名**；点开是档案列表（模型名 + 档案名 + base_url，缺密钥标一个红角标）。

- **不需要任何新接口**：就是 §8.14 那对 `GET /api/llm/profiles` +
  `POST /api/llm/active`，和设置页用的是同一个。切完**重新读一遍**而不是本地改标记 ——
  后端写完会回读校验，以它读回来的为准。
- 四条口径和设置页对齐（不另立一套）：

  | 口径 | 为什么 |
  |---|---|
  | 模型是**后端全局设置**，不是这条会话的属性 | `/api/chat` 压根不接模型参数（读的是 `LLM_PROFILE` / 文件 `active`），所以下拉里直接写明"影响所有会话" |
  | **生效值优先**：`effective.profile` 压过 `profiles[].active` | 文件里写 A、实际跑 B 时，B 才是真相（同设置页） |
  | 密钥不进浏览器，只标"缺密钥" | 真实失败由后端在调用时报出来 |
  | **Mock 模式显示"未接入模型"** | 摆一排点了没反应的假模型，比明说"没接"更糟 |
- **生成中不给切**（`disabled={busy}`）：`/api/chat` 是在发起那一刻读配置的，
  中途换档会让这一轮的答案来自旧模型、工具条却已经显示新模型 —— 又一种"看起来切了、其实没切"。
- **切换失败必须喊出来**：工具条里当场出一条红字（含后端原文），6 秒后自愈。
  这一条不能省 —— 否则用户以为换成了、后面几轮其实还是老模型在答。
- ⚠️ **旧版扁平配置（没有 `profiles`）下不可切**：`setLlmActive` 只认 `profiles`，
  点了必然回"没有这个档案"。这种配置直接禁用，title 里说明"只能在设置页看"。
- ⚠️ 出错文案那一格必须能**被压缩**（外层 `flex-1 min-w-0`）：后端错误里会带完整文件路径，
  不截断的话它会把发送按钮顶出卡片外面。

#### 2. 对话页那一档压矮：110px → 92px

⚠️ 先把上一次为什么白改说清楚：§8.21 记的"只动 `min-h` 看不出变化"是真的，
因为整块高度里占大头的是**工具条那一行**和**上下内边距**。这次一起收：

| 组成 | 改前 | 改后 |
|---|---|---|
| 上内边距 `pt` | 12 | 10 |
| 文本域 `min-h` | 40 | 36 |
| 工具条间距 `mt` | 8 | 6 |
| **工具条行高**（发送按钮 `size="icon"`） | **40** | **32**（`h-8 w-8`） |
| 下内边距 `pb` | 10 | 8 |
| **合计** | **110px** | **92px** |

- 上表是按工具类**折算**的（`20 + 36 + 6 + 32 + 8`），不是浏览器实测 —— 别当成测量值引用。
- **空态（`compact`）一个字没动**，仍是 114px：那一屏本来就是"留白 + 一个输入框"，
  压扁只会显得寒酸。文本域 `min-h` 因此做成两档（2.5rem / 2.25rem）。
- 把"别再只动 `min-height`"这个结论和上表的算式直接写在 `Composer.tsx` 的注释里，
  免得下一轮又踩一次。

**验证**：`npx tsc --noEmit` + `npm run lint` + `npm run build` 全过。
`next build` 会把 `/app` 静态预渲染一遍，也就是 **ModelPicker 的渲染路径在构建期真的执行过**
（空态那一档）。⚠️ **浏览器里的观感、以及"点了模型之后 `/api/chat` 是不是真的换了模型"
还没实机验过** —— 这轮只做了静态验证，别把它当成实测结论。

#### 3. 用户两张截图后的两轮收敛（尺寸与对齐全按"同一个组件"来）

用户贴了草稿页和对话页两张截图，原话："对话页的对话框感觉比新对话的要小，
而且左 padding 也不太对" → 再一句"模型选择可以再往下一点"。**上面第 2 节那张
110 → 92 的表按这一轮的结果已经作废**，最终是这样：

| 组成 | 空态（草稿） | 对话中 | 说明 |
|---|---|---|---|
| 左右内边距 `px` | 16 | 16 | 原来对话中是 12 —— 这就是用户说的"左 padding 不对" |
| 上内边距 `pt` | 14 | 10 | 只有这里和下内边距在做"矮一点" |
| 文本域 `min-h` | 40 | 36 | |
| 工具条间距 `mt` | 12 | 12 | 两档同值；只决定**正文到胶囊那段空白**（不负责定位，见教训 3） |
| 工具条行高（发送按钮） | 40 | 40 | ~~32~~ —— 见下 |
| 下内边距 `pb` | 8 | 8 | **它才是"胶囊离底边多远"的旋钮**（胶囊离底边 = `pb + 4`） |
| **合计** | **114px** | **106px** | 只差 8px，不再是"大小两个" |

四条教训（前三条都是用户当场看出来的，第四条是我自己踩出来的）：

1. **不要为了压高度去缩一个"块头"元素**（发送按钮 40 → 32）。用户的原话是
   "对话页的比新对话的要小" —— **同一个组件在两处尺寸不同，比"高 8px"刺眼得多**。
   要压就压"空气"（内边距 / `min-h`），块头（`px` / 按钮 / 行高）两档共用。
2. **左对齐要抵掉组件自己的内边距，但不能全抵**。模型胶囊自带 `px-1.5`，
   所以它的图标比上面的占位文字右偏 6px，看着就是"没对齐"；`-ml-1.5` 全抵 →
   用户回"再往右 2px"（汉字字形左边本身留白 1~2px，硬对齐盒子反而看着偏左）。
   最终用 `-ml-1`：留 2px，**视觉上**才齐。（悬停底色往左多探 4px，仍在卡片
   内边距之内，不会溢出。）
3. **⚠️⚠️ "把胶囊往下挪"要改 `pb`，不是 `mt` —— 这条踩了三轮才搞清。**
   输入框在两种页面里的**定位方式不同**，于是同一个属性效果完全不同：

   | 页面 | 布局 | 卡片变高时 | `mt` +10px 的效果 | `pb` -10px 的效果 |
   |---|---|---|---|---|
   | 对话页 | 上面是 `flex-1` 的对话流，**输入框贴着窗口底边** | 只能**往上长** | 胶囊**一动不动**（只是卡片顶边往上跑） | 胶囊**精确下移 10px** |
   | 草稿页 | `justify-center`，**输入框垂直居中** | 上下**各摊一半** | 胶囊下移 5px | 胶囊下移 5px |

    胶囊离卡片底边的距离 = `pb + 4`（那 4px 是它在 40px 工具条里上下各留的余量），
   所以"再往下 N px"= `pb` 减 N。我前几轮一直在加 `mt`（`8 → 12 → 16 → 20 → 25 → 30`），
   在对话页等于**什么都没做**，用户的原话是"我这边并没看出改动确实有生效"。
   → **现象与成因对不上时，先去看这个盒子是怎么定位的**，别继续调那个"看起来相关"的属性。
   （从 §8.1 到这里的病根是同一个：改动与现象对不上，却没人去质疑前提。）

   **收尾**：`mt` 加过头还有个反作用 —— 用户在 DevTools 里直接圈出那 30px 的 margin
   （橙色那一条）说"现在这个橙条太宽了"。它本质是**卡片中间一道空槽**，不是"把东西往下推"。
   最终 `mt` 回到 12px：`pb` 管"贴不贴底边"，`mt` 只管"别贴着正文"。
   **同一个属性被当成两种用途使，最后一定有一头是错的。**

4. **这类"再挪一点"的需求是二分不出正确答案的**：我这边没有浏览器，只能按用户
   每轮的实际观感逼近。所以每轮都只动一个数、并且把它写在注释里（`mt` / `pb` 各是
   哪个旋钮），下一轮才有得调 —— 别一次动三处，那样连"是哪一处起的作用"都说不清。

⚠️ **验证前提**：改的是 `className`，`npm run dev` 会热更新；但如果跑的是 `npm start`
（生产构建），必须重新 `npm run build` 再重启才看得见。"我这边没生效"这句话里，
一半是上面第 3 条那个布局问题，另一半要先排掉这个。

⚠️ 表里的数字都是**按工具类折算**的（`14/10 + 40/36 + 12 + 40 + 8`），不是浏览器实测 ——
我这边没有浏览器，图是用户截的、结论是用户给的。别当实测值引用。

### 8.23 去掉输入框底部的「模拟渲染失败」开关

**用户反馈**（原话）："把模拟渲染失败删了"。

那个开关的来历：它是**给答辩现场准备的道具** —— 点开之后第 3 个分镜必定渲染失败，
好演示「单分镜失败 → 自动重试 1 次 → 还失败就交给用户点重试」这条交互（Q12-B）。
它一直挂在输入框下方那行快捷键提示的右边，草稿页不显示（`compact` 时整行隐藏）。

删的理由也站得住：**它是一个演示道具，不是一个功能**。摆在产品界面上，用户只会
理解成"这东西会失败"，而不是"这里能演示失败"。

**删了什么（前端，全链路不留半截）**：

| 位置 | 改动 |
|---|---|
| `Composer.tsx` | 按钮 + `simulateFailure` / `onSimulateFailureChange` 两个 prop + `AlertTriangle` 导入 |
| `Workbench.tsx` | `simulateFailure` 这个 state、传给 `Composer` 的两个 prop、`confirmRender` 请求里的字段 |
| `lib/api.ts` | `ConfirmRequest.simulateFailure` 字段（原地留了一行注释说明它去哪了） |

⚠️ **刻意没删的**：mock 的 `/api/render/confirm` 与后端 `api/routes/render.py` 里的
`simulateFailure` / `simulate_failure`。它们是**接口层面**的能力（后端那份的注释本来就写着
"真后端忽略"），留着才有办法演示那条重试交互 —— 只是入口从界面变成了手工构造一次请求。
**这不算"删了没删干净"：删的是产品界面上的入口，接口是另一层的事。**

### 8.24 一个 KeyError 带出的两件事：move_to 的参数契约、以及「网格上滑动窗口」怎么画

**症状**：用户在对话页说「分镜 2 和 3 有点问题，数字也不在格子里」，回来的是一句
`服务端异常：KeyError: 'point'` —— 整轮请求 500。堆栈：
`llm.stream_storyboard()` → `schema.validate()` → `dsl._norm_action()` → `ac["point"]`。

**为什么**：这一版模型把移动写成了
`{"do":"move_to","target":"win","to":[-3.75,0.45],"run_time":1.0}`（原文见
`output/_llm/calls.jsonl`，§8.9 的留档直接调得出来）。而 `_norm_action()` 里**只有
`move_to` 这一处是直接下标取键**（`ac["point"]`），漏写就穿过校验层变成 500。
可**漏参数是模型输出的问题**，本该按 `DSLError` 报出来、走"回灌错误重试"那条路。

**修法之一 —— 把参数契约显式化**：

| 改动 | 位置 |
|---|---|
| `move_to` 改 `ac.get("point")`，缺了报 `DSLError` 并列出收到的键；`to` 作兼容别名 | `dsl._norm_action()` |
| `ACTION_SPEC` **新增 `args` 字段**：每个动作的附加参数名写进自动生成的规格（move_to→`point`、shift→`vector`、tracker_to→`to`、move_cells→`of`+`rows`/`cols`…），`describe()` 一并输出 | `dsl.py` |
| `validate_scene()` 抛出的非 `DSLError`（KeyError/TypeError…）转成 `SchemaError`，原始堆栈照打 | `schema.validate()` |

中间那条是关键：**以前规格里只写动作名和 run_time，"参数叫什么"从来没告诉过模型** ——
它写 `to` 不是乱写，是照着 `tracker_to` 的用法类推的（同 §8.10 的教训）。
最后一条是兜底：**校验层自己的 bug 不该变成用户的 500。**

**修法之二 —— 真正解决用户抱怨的那件事**：
先把上一版真渲出来抽帧看（`render_worker.render_one` + ffmpeg）：

- 数字确实已经在格子里了 —— 模型自己把多行 `text` 换成了 `table`；
- 但**红框是歪的**：`rect` 写死 1.9×1.9 去套 2×2 的格子，只盖住半个区域；
  后面几步 `move_to` 每步 0.9，也和格子边长对不上。

根因：**Manim 的 `MobjectTable` 格子尺寸是由内容撑出来的**（`h_buff=1.3` × 最宽条目），
写死坐标的画框不可能对得上。于是补了两个原语（元素 + 动作）：

- `cell_box`：`{"id":"win","kind":"cell_box","of":"img","rows":[1,2],"cols":[1,2],"color":"RED"}`
  —— 位置和大小都从 `table.get_cell()` 算出来（运行时 helper `_cell_box()`），**从根上不会错位**。
  行/列号**从 1 开始**（跟 manim 的 `Table.get_cell()` 对齐，少一层换算就少一次错位）。
- `move_cells`：`{"do":"move_cells","target":"win","of":"img","rows":[1,2],"cols":[2,3]}`
  —— 把框滑到另一组格子（`_cells_center()`）。这才是"卷积核滑动"真正需要的动作。

顺带把**静默错位**全部升级成硬报错（都属"能渲染、但内容错"，本项目的头号敌人）：

| 非法写法 | 现在的结果 |
|---|---|
| `cell_box` 带 `place` | **报错**（位置由格子算，写 place 就会挪歪） |
| 对 `cell_box` 用 `move_to` / `shift` | **报错**并提示改用 `move_cells` |
| `move_cells` 换了框的行列数 | **报错**（框大小一变就和格子错位） |
| 对"被框着的表格"做 `scale`/`shift`/`rotate` | **报错**（框是建表时算好的，表格一动框不跟着走） |
| `cell_box` 的 `of` 指向非 table / 格号越界 | **报错**（带表格实际尺寸） |

配套：`llm.py` 的分镜设计规范加一条（网格类内容用 `table` + `cell_box` + `move_cells`），
`dsl.describe()` 新增「网格与滑动窗口」小节（含可直接抄的 JSON），README 的元素/动作清单
同步到 26 / 30 种。

**验证**：3×3 表格 + 2×2 红框，`move_cells` 右移一格、再下移一格，480p15 真渲抽帧 ——
**框边和网格线完全重合**；9 条用例（1 条正常 + 8 条非法，含旧写法 `to=`）全过；
`generate.py <示例> --dry-run` 对 `examples/*.json` 全量回归，行为无变化。

**教训**：
1. `_norm_*` 里**直接下标**取附加参数，等于把"模型漏参数"升级成 500。校验层要么给出精确错误，
   要么就对不起"校验层"这个名字。
2. 规格里**只写动作名、不写参数名**，模型只能靠类推 —— 它类推错了，多半是我们的文档漏了。
3. 凡是**尺寸由内容撑出来**的东西（表格、文本），不要让模型估坐标；
   给它一个**按结构定位**的原语，错位就没有发生的可能。

### 8.25 分镜一直重试失败：`number.unit` 里的中文进了 LaTeX（而重试必然复现）

**症状**：`/api/render/retry` 反复失败，日志每次都是
`增量渲染 —— 待渲 1 段，缓存命中 6 段`，界面上一行
`分镜 6 渲染失败：ValueError: latex ...` + 一个「重试」按钮，点多少次都一样。

**根因**（证据不用猜，就是 `_parts/<name>/media0/Tex/64076844f96bff2c.tex` 那个文件）：
模型给分镜 6 的动态数字写了中文单位 —— `{"kind":"number","value":"n","unit":" 块"}`，
而 `DecimalNumber(unit=...)` 内部用 **MathTex**（默认模板）渲染单位：

```
! LaTeX Error: Unicode character 块 (U+5757) not set up for use with LaTeX.
```

→ 整个分镜渲不出来。`formula()` 早就做了三级分流（含中文走 ctex / 无 LaTeX 走 Text），
但 `number.unit` 是一条**从旁边绕过去的路**：`_mk_number()` 直接把字符串塞进 DecimalNumber。

**用户的两个疑问，答案都在这里**（"用的好像还是缓存？"）：

1. **缓存没出错**。6 段命中缓存是真的（那 6 段已经是好成品），
   "待渲 1 段"就是每次都真渲了一次、每次都**在同一行**炸掉的那一段 ——
   它永远不产出文件，也就永远进不了缓存。缓存判据是「源码没变 **且** 片段已存在」，两条都满足才算命中。
2. **重试必然复现**：retry 不重新生成分镜，只是把**同一份源码再渲一遍**；
   错误是确定性的（LaTeX 不认中文），点多少次都不会自愈。
3. 校验层拦不住它：`unit` 是自由字符串（`T_STR`），schema/dsl 只查"是不是字符串"，
   不查"能不能被 LaTeX 排"。又是 §8.7 那条 —— `ELEMENT_SPEC` 是对模型的承诺，
   但"承诺能不能兑现"没人检查。

**修法**（`storyboard/dsl.py`）：

- 运行时 helper 新增 `_number(value, num_decimal_places, unit, ...)`：
  单位**含中文** → 数字仍走 `DecimalNumber`，单位改用 `Text(unit, font=CN_FONT)` 拼在右边（`VGroup`）；
  纯 LaTeX 单位（`m^2` 这类上标）照旧交给 `DecimalNumber` 自己排，效果更好。
- 运行时 helper 新增 `_set_number(m, value)`；`_mk_number()` 改为生成 `_number(...)`，
  动态那条分支改为 `_set_number(m, ...)` —— DSL 侧不必知道返回的是 VGroup 还是 DecimalNumber
  （VGroup 没有 `set_value`）。
- `describe()` 的「动态效果」第 6 条补一句"`unit` 写中文没问题"。
  ⚠️ 之前规格里**一个字都没提 unit**：`ELEMENT_SPEC` 里那句说明只有 `--spec` 看得到，
  根本不进 prompt，模型是照着猜的 —— 同 §8.10 的教训。

**当场踩到的两个坑**（注释里也留了）：

1. `arrange()` 默认 `center=True`，内部执行的就是 `move_to(ORIGIN)` ——
   **每帧** arrange 会把整块挪回屏幕中心。必须显式 `center=False`。
2. 参数名要和 manim 对齐用 `num_decimal_places`：第一版 helper 写 `decimals`，
   生成出来的调用直接 `TypeError: _number() got an unexpected keyword argument` ——
   又一次"生成器与运行时契约不一致"，只有真渲一次才会暴露。

**验证**（本机实测）：

- 分镜 6 真渲成功（`render_worker.render_one` + 真实分镜源码，产出 mp4）。
- 刷新与布局真跑过：值 8 → 50 → 120 → 200，中心 x 稳定在 5.84 → 6.02（**没有飞回原点**），
  数字与单位间距恒为 0.140；纯 LaTeX 单位仍是 `DecimalNumber` 本体。
- 走**真正的 retry 路径**离线跑 `iter_incremental(name, raw, range(5, 7))`：
  `tool_result(分镜6) 63.8s → tool_result(分镜7) 76.2s → tool_done 78.5s`，**无 error 事件**；
  `sections/` 7 段 + `index.json` 齐全（分镜 6 真实时长 10.133s）。
- `examples/*.json` 全量回归：只有 `_bad_case_demo.json` 报错 —— 那本来就是故意写错的负例。

两个必须知道的副作用：

- ⚠️ **改完的第一次重试是全量重渲（实测 78.5s），不是缓存坏了**：
  `RUNTIME_HELPER` 是注入到**每个**生成文件里的，而增量判据是"源码逐字相同才命中"，
  所以 7 份源码全变。第二次起恢复正常。
- ⚠️ **必须重启后端**：uvicorn 不热加载，进程里还是旧 `dsl.py`，不重启点了还是同样的错。

**顺带发现（没修）**：`USE_LATEX=0`（机器上没装 LaTeX）时 `formula()` 会降级成 Text，
但 `DecimalNumber` 照样走 MathTex —— 即**任何含 `number` 的分镜在无 LaTeX 环境下整条渲不出来**。
修法是在 `_number` 里补一条 `if not USE_LATEX:` 分支（整块退化成 Text，`_set_number` 走
`Text.setText`）；本机造不出"没有 latex"的环境、没法实测，所以没动。
（已记为任务清单第 30 项。）

### 8.26 规范化幂等的回归测试落地（§8.8 的兑现，顺带修掉 5 个不幂等点）

**背景**：§8.8 早就写明"建议补一条幂等回归测试"，但一直只是**建议** ——
全仓一个测试文件都没有，`requirements.txt` 里也没有 pytest。
2026-09-15 把它兑成了 `tests/test_normalize_idempotent.py`（29 条）。

**先证明问题真实存在**（不是照着文档抄）：写测试前先跑了一个探针，
对 `examples/*.json` 逐份做 `validate(validate(raw))`，结果是
**22 份里 3 份直接报错**：

```
scene[1].elements[1].place[0]: 每条 place 只能有一个布局键（可另加 buff），
收到 ['aligned', 'buff', 'direction', 'next_to']
```

那四个键**是它自己上一遍补出来的** —— 函数否定了自己的输出。

**写测试时又抓出第 5 个不幂等点**（§8.6 表里没有它）：

```
第一遍: "_dropped": []
第二遍: "_dropped": ["_dropped"]      ← 它把自己的输出当成了未知参数
```

根因是 `_norm_element()` 算未知参数时用了
`set(el.keys()) - set(spec) - {"id","kind","place","live"}` ——
把自己产出的兜底键 `_dropped` 也当成了"模型多写的键"。

**五个不幂等点与修法**（全部在 `storyboard/dsl.py`）：

| # | 形态 | 为什么不幂等 | 修法 |
|---|---|---|---|
| 1 | `plot` 没写 `x_range` | 第一遍补成 `None`，第二遍按"非法范围"报错 | 可选参数（`ELEMENT_SPEC` 默认值为 `None`）**没写就不写进输出**，不补 `None` |
| 2 | `circle` 没写 `at` | 同上 | 同上 |
| 3 | `place: [{next_to:{...}}]` | 第一遍展平成 4 个键，第二遍判成"多个布局键" | `_norm_place()`**先认自己展平过的形态**；且 `aligned` 为空时**不写这个键**（写 `null` 会让形态对不上） |
| 4 | 动作里内联定义元素 | 第一遍转成 `target:[id]`+`inline:{...}`，而 id 不在 `declared` 里 → 第二遍报"引用了未声明的元素" | 识别 `inline`/`inline_into` 里的 id 也算"已声明"，并把内联元素原样带走 |
| 5 | 任意元素 | `_dropped` 自我污染（新发现） | 计算未知参数时排除 `_dropped` |

**1 和 2 的修法刻意选在源头**：`_norm_value()` 里加一条 `if v is None: return None`，
再由 `_norm_element()` 决定"是补默认值还是就保持没写"。
不在 `_norm_value` 里直接报错 —— 那样所有可选参数就都得写一遍。

**验证（三层，缺一层都不算数）**：

1. **29 条测试全绿**（原先 9 条红）。含 §8.6 表里四个点各一条定点用例 +
   §8.7 的 `parallel` 哨兵 + 一条"规范化产物里不得残留 `None` 可选参数"的结构性断言。
2. **渲染输出零变化**：把改动前（`git checkout HEAD -- storyboard/dsl.py`）与改动后
   各自对全部 22 份示例生成 Manim 源码（`render()` 与 `render_split()` 两条路径），
   **44 个文件逐字节完全相同，0 差异**。
   这一步是必须的 —— 测试只能证明"规范化自洽了"，证明不了"画面代码没变"。
   （做完立刻把改动文件还原回去，并核对 `git diff --stat` 与预期一致。）
3. **真渲一份端到端**：`free_derivative.json`（就是上面报错的三份之一，
   改动前**连校验都过不了**）→ 4 个分镜全部出片，25.9s；
   成片时长 35.73s = 四段之和，`sections/` 与 `index.json` 齐全。

**这次临时踩的两个坑**（都是"没跑就写"）：

- 探针里猜 `schema.validate(raw)` 的签名，23 个文件齐报
  `missing 1 required positional argument: 'registry'` —— 真实签名是 `validate(storyboard, registry)`。
- 探针里猜 `render_split()` 返回 `list[str]`，实际是 `[(scene_id, src), ...]`。
  两次都是**运行才知道**，正好说明这类脚本的价值：手工看一次容易"哦没报错"就过去了，
  脚本会直接告诉你"0 个文件真的被检查过"（假通过）。

**临时探针用完即删**：排查时写过两个脚本（幂等探针、源码对比），都没留下来 ——
它们的能力现在由 `tests/` 覆盖（`examples/*.json` 是 parametrize 出来的，
哪份失败会直接报在测试名里）。**故意不留在 `output/` 下**：那个目录是被 gitignore
且可能被清理的，把工具放在那里等于"下次想用已经没了"。

**给以后的规矩**：改了 `dsl._norm_*` 或 `schema.validate`，**跑一遍**
`python -m pytest tests -q`（不到 1 秒）。
这条测试是 §8.6/§8.7 那类"静默失效"的通用哨兵 ——
它们的症状是"两个闸互相矛盾"，靠肉眼和日志都很难定位，但测试一眼就能看出来。

### 8.27 `parallel` 真并行：一个"从来没兑现过的承诺"，以及顺带抓出的第二个静默失效

#### 先说它原来错在哪

`parallel` 的字面承诺是"这些子动作**同时**演"，但 2026-09-15 之前它生成的是
**多条独立的 `self.play(...)`**：

```python
# 旧行为：串行，总共 3 秒
self.play(Create(c1), run_time=2)      # 先演 2 秒
self.play(Write(t1), run_time=1)       # 再演 1 秒

# 新行为：一条 play 带两个动画，总共 2 秒
self.play(Create(c1, run_time=2), Write(t1, run_time=1))
```

这不是"性能优化"，是**语义修复**：写的是并行、跑的却是串行。
用户的观感差别很实在 —— "圆画出来的同时旁边给出公式"变成了"圆画完再写公式"。
（§8.7 修的是更严重的"子动作被整个丢掉"，那时候至少还是"写了没做"，
现在是"写了、做了、但做成了另一件事"。）

#### 实现：三条读源码确认过的事实（都没有猜）

| 事实 | 出处 |
|---|---|
| 一条 play 的总时长 = `max(各动画 run_time)` | `manim/scene/scene.py` 的 `get_run_time()` |
| 子动画的 `run_time` 决定它在共同时间轴上的结束时刻 | `manim/animation/composition.py` 的 `build_animations_with_timings()` |
| `.animate` 链上传 run_time **只能**写成调用形式，且必须在方法之前 | `manim/mobject/mobject.py` 的 `_AnimationBuilder.__call__` |

所以"同时开始、各自按时长结束、整段 = max"就是 manim 的默认行为，
**不需要 `AnimationGroup`** —— 反而有三个理由不该嵌它：

1. `AnimationGroup` 不缩放子动画时长（`composition.py` 里 `build_animations_with_timings`
   用的是子动画**原始** run_time，没乘 rate_func），那"嵌一层"就只是多一层转译；
2. 摊平后 `max(a, max(b, c))` 与 `max(a, b, c)` **恒等**，语义完全不变；
3. 嵌进去之后 `_est_action_time()` 也得跟着递归处理嵌套 ——
   而"两处必须同时改"正是本项目最容易漏的一类（§8.7 的教训）。

#### ⚠️ 顺带抓出的第二个静默失效：`.animate.scale(x).set_run_time(0.4)` 无声无效

第一版给 `.animate` 类子动作注入时长时，想当然写成了

```python
c.animate.scale(1.2).set_run_time(0.4)      # ✗ 时长仍是默认的 1.0s，不报错
```

**它被静默忽略了。** 原因是 `set_run_time` 是 `Animation` 上的方法、
**不在 `Mobject` 上**（实测 `hasattr(Mobject, "set_run_time") == False`），
于是 `_AnimationBuilder.__getattr__` 把它当成普通的目标方法转发到
`mobject.target`，然后什么都不发生。三种写法实测：

| 写法 | 实测时长 |
|---|---|
| `c.animate(run_time=0.4).scale(1.2)` | **0.400s** ✅ |
| `c.animate.scale(1.2).set_run_time(0.4)` | 1.000s ❌（被忽略） |
| `c.animate.scale(1.2)` | 1.000s（默认值，对照） |

**是测试抓出来的，不是我看代码看出来的** —— 这条如果漏掉，症状会是
"估时 0.4s、实际演 1.0s"的时长漂移，而报错一个都没有。
现在 `tests/test_parallel.py` 里专门留了一条断言**证明 `.set_run_time` 确实无效**
（断言它 `> 0.9`），这样将来若有人"简化"成那个更顺眼的写法，测试会立刻指出来。

#### 三处必须同时改的地方（这次全改了，并写进了注释）

| 位置 | 改动 |
|---|---|
| `_Builder._parallel()` | 子动作 → 动画表达式 → 合成**一条** `self.play(...)` |
| `_est_action_time()` | `parallel` 的估算从 **`sum` 改成 `max`** |
| `_anim_timed()`（新增） | 把各子动作自己的 run_time 注入到**它自己的表达式内部** |

第三处为什么必须有：`self.play(Create(x), run_time=2, Write(y))` 是
**Python 语法错误**（位置参数跟在关键字参数之后）—— 第一版就是这么写的，
被 `renderer._check_syntax()` 的 `compile()` 兜底拦下了（那道闸存在的意义正在于此）。
而 run_time **也不能**写在外层：manim 的 `_setup_animations()` 会把 play 的 kwargs
挨个 setattr 到**每个**子动画上，把各自时长抹平。所以只能逐个注入。

#### 降级路径：不能并行时**如实**顺序播放

这些情况整段降级为顺序 `self.play`（画面仍然对，只是不并行），
并在生成的代码里写明"降级为顺序播放"，不假装自己是并行的：

| 情况 | 为什么 |
|---|---|
| 含 `show` / `remove` / `trace` | 它们是即时 `add`/`remove`，不是动画，manim 的 `self.play` 不接受 |
| 同一个元素被两个子动作碰 | 两条动画同时改同一属性，谁先生效取决于 manim 内部顺序；而"先放大再旋转"和"先旋转再放大"本来就不一样，**顺序语义才是对的** |
| 子动作里有多个 target | 会展开成多条语句，塞不进一条 play（校验层已把它们拆成单 target 副本） |

#### 验证（四层）

1. **39 条测试全绿**（新增 `tests/test_parallel.py`，含上面那条"证明 `.set_run_time` 无效"）。
2. **实测时长 = max**：一条 play 里 0.2s + 0.6s 两个动画，实测 **0.60s**（不是 0.8）。
   这条测的是**我们对 manim 语义的假设本身** —— 它红了就说明 manim 改了行为，
   `max` 需要重新论证。
3. **非 parallel 路径零变化**：改动前后对全部 22 份示例生成 Manim 源码
   （`render()` + `render_split()`），**44 个文件逐字节完全相同，0 差异**。
   这是这次改动最重要的一条保险 —— 真并行只碰了 parallel 分支。
4. **端到端真渲**：含 parallel 的分镜出片成功（13.5s）；
   `估时 3.0` + 补 wait 1.0 + 尾随 0.3 ≈ 实测 **4.267s**，时长对得上。

#### 一个我没做的事（写下来免得以后以为是漏了）

`.animate` 类子动作**不给 `run_time` 时用 manim 的默认值（1.0s）**，
而不是 DSL 里 `ACTION_SPEC` 写的默认值（如 `shift` 是 0.8s）。
以前的路径是外层 `self.play(..., run_time=0.8)`，现在并行时没法这么写
（会被平摊），要拿回 0.8 就得逐个注入 `set_run_time` —— 而那条路刚被证明是死的，
唯一可行写法 `x.animate(run_time=0.8).shift(...)` 需要逐个动作把 `ACTION_SPEC`
的默认值搬进表达式里。**当前只处理"模型显式写了 run_time"的情况**，
没写就交给 manim 定。真并行下这已经比原来(整体一个 run_time)更精确了，
所以没有再往下做。


---

### 8.28 校验层从「报一个错」改成「一次报完」

**背景**：D7 门要求「一次成功率 ≥85%、三次内 ≥95%」，而 `max_attempts=4`。
但 `dsl.validate_scene()` 是**遇到第一个错就抛**，于是一份散着几个错的分镜必然这样死：

```
第 1 轮 → 报「颜色 LIGHT_BLUE 非法」        → 模型改了颜色
第 2 轮 → 报「引用了未声明的 ghost」        → 模型改了引用
第 3 轮 → 报「表达式里的 u 未定义」         → 模型改了名字
第 4 轮 → 次数耗尽
```

模型本来能一次改完所有错 —— **是我们的反馈带宽把它逼死的**。这不是 prompt 能治的
（写什么提示词，校验层都只报一个）。

**修法**（`storyboard/dsl.py`）：

| 位置 | 改动 |
|---|---|
| `validate_scene()` | elements / timeline 逐个 try，**收集错误继续往下走**，最后 `_format_errors()` 汇总成一条 |
| 失败元素的 id | **尽力登记进 `declared`** —— 否则后面凡是引用它的元素都会级联报"未声明"，假错误把真问题淹掉 |
| 失败元素的表达式 | 仍做一次**只读**扫描（"颜色写错 + 表达式名字写错"这种元素，否则只报前者，下一轮又撞上后者） |
| `_check_cell_refs()` | 只在上游**无错**时跑 —— 它依赖整体一致性，有上游错时只会产噪音 |
| `_format_errors()`（新增） | 汇总，12 条 / 1800 字符上限，头部写明"共 N 处问题，请**一次性全部改正**" |

**验证**：同一个分镜塞 4 处独立错误（非法颜色 / 未声明引用 / 未声明 target / 表达式里未定义的
名字），一条消息全部报出：

```
校验未通过，共 4 处问题，请**一次性全部改正**后重新输出完整 JSON：
  1) scene[1].elements[1].color: 非法颜色 'LIGHT_BLUE'。颜色只能用规格「硬性规则」里列出的那 67 个常量名之一，或 `#RRGGBB`
  2) scene[1].elements[2].of: 引用了未声明的元素 'ghost'（元素必须先声明后引用）
  3) scene[1].timeline[1]: 引用了未声明的元素 'nope'
  4) scene[1].elements[1].expr: 表达式 'sin(u)' 里的 'u' 未定义
```

---

### 8.29 回灌环节的三处自相矛盾

重试机制本身没错（把校验层的确定性注入模型下一轮），但**喂回去的东西**有三处是错的。

#### ① 反馈语要"完整重写"，回灌却只有 3000 字符

`_FEEDBACK_SCHEMA` 写着「重新输出**完整的** JSON 对象（不是补丁、不是片段）……其余部分保持你
原来的设计」，而回灌的上一轮输出是 `str(text)[:3000]`。一份 4~6 分镜的 JSON 轻松超过 3000
（示例 1 单镜就 1000+），模型看到的是一份**腰斩的 JSON**，很自然地顺着断口续写 → 再炸一次。

**修法**：`_ECHO_LIMIT = 12000`，且超长时**明说"此处只显示前 N 字符、已被截断，
请完整重写，不要顺着断口续写"**。默默切一刀才是真正的坑。

#### ② `brief` 为空能静默通过

`schema._norm_brief` 只做 `str(v).strip()[:500]`：**空字符串合法、超长静默截断**。
而 `brief` 是前端"1~2 秒见字"的全部内容，纯概念问答（`intent=none`）时更是**唯一**产出。
它空了 = 这次生成对用户毫无价值，却一路绿灯。

**修法**：两条主流程（`generate_storyboard` / `stream_storyboard`）在解析后加一道
`_brief_of(raw) is None → 回灌重试`，配 `_FEEDBACK_BRIEF` 专用反馈。

#### ③ 键序错了没有任何人知道

`brief` 必须是第一个键（前端 `BriefTap` 靠它做秒级见字），prompt 里用自然语言叮嘱了。
但厂商的 `response_format: json_object` **不保证键序**，一旦被重排，用户看到的就是
"思考完了，然后什么都没有" —— 而且**不报任何错**。

**修法**：`_warn_brief_order()` 打 `[WARN]`。**刻意只告警、不重试**：键序错了 JSON 仍然合法，
BriefTap 会一直往后扫、照样找得到 brief，损失的只是"秒级"这个体验；而重试轮走的是**非流式**，
重来一次也换不回秒级体验，纯属白花一次调用。

另加一条**兜底补发**：整条链路走完如果 `emitted` 还是假（流式一个字都没吐出去 —— brief 缺失、
被网关重排、或流式降级），校验通过后把 brief 整段补发一次。不做这一步，这种失败
**完全不可见**（不报错、日志也正常）。

---

### 8.30 `finish_reason` 我们抓了却从来不看

**症状**：用户问"模型思考到 22000 字符左右就不想了，是自然的还是限制？"

**根因**：`_iter_sse_stream()` 明确把 `finish_reason` 塞进 `seen`（docstring 里还写着"结束原因"），
然后 —— `chat_stream()` 只读 `seen["usage"]`，`chat()` 连 `seen` 参数都没有，
`llm_log.record()` 也没有这个字段。**整个代码库里没有任何一处能回答这个问题。**

后果就是 §8.11 记的那个坑反复发作：截断只能靠 `parse_json_object` 从"JSON 好像断了"反推，
而它的提示语自己都写着这只是猜测 —— "真正的错因是截断，报错里写的却是语法错"。

**修法**：

| 层 | 改动 |
|---|---|
| `chat()` / `chat_stream()` | 新增 `seen` 出参（老调用方不用改），把 `finish_reason` 交给留档 |
| 截断告警 | 两条路径都打 `[WARN]`，不再悄无声息 |
| 主流程 | 截断**先于解析**判断（JSON 被砍断时解析只能说"疑似"），当成可重试失败，回灌 `_FEEDBACK_TRUNCATED`（"大幅压缩思考、直接动笔"） |
| "返回空内容"报错 | 在截断时会明说"思考先吃光了预算、调大 max_tokens"，而不是含糊的"可能是 max_tokens 太小" |
| `llm_log.record()` | 记 `finish_reason` + `max_tokens` |
| `--llm-stats` | 新增「截断」列；末尾算「输出 tok 峰值 / max_tokens」并**直接给结论** |

最后那条是这次真正回答用户问题的地方：

```
输出 tok 峰值 15264，用过的 max_tokens: [32768]（按最大一档 32768 算，占用 47%）
离上限还有 53% 的余量 —— 回复是模型自己收的尾，调大 max_tokens 不会改变任何东西。
```

**结论（数据来自 §8.11 那张表）**：正常那次 `completion_tokens=15264`（思考 12312）、
`finish=stop`，离 32768 还差一半 —— 所以**不是我们的上限在卡**，是模型自己停的。
12312 个思考 token 换算成中文大约就是两万字出头，与用户观察到的"22000 字符"吻合。

**给"要不要调大 max_tokens"的判定规则**（已内建）：截断列为 0 且占用远低于 100% → 别动；
截断 > 0 或占用 ≥95% → 调到 49152 就够，**不要一步到中转站上限 131072**
（风险不在钱，在延迟：思考是"首字 93 秒"的元凶，把上限拉满等于把"90 秒后快速失败"
换成"几分钟后才失败"）。

---

### 8.31 模板化的根因不在模型，在 prompt（以及"点破引擎"的得与失）

**用户反馈**：出错率已经很低了，但**成品模板化严重**。

**三条证据**（都指向 prompt 自己）：

1. 规范里写死了一条**固定结构配方**：`通常 3~6 个分镜：封面（这题问什么）→ 图形/推导（主体）→ 结论`。
2. **唯一的 few-shot 示例**是"坐标系 + 曲线 + 动点 + 切线"，而模型对示例的模仿远强于对文字规则的遵守。
3. **prompt 里没有一句话告诉模型"这道题的画面引擎是什么"** —— 于是它唯一的策略就是把所有题
   翻译成它见过的那一套。**几何题也上坐标系**，就是模板化的直接症状。

反证：`examples/free_pythagorean.json`（手写的 showcase，polygon + angle + 割补、全程无坐标系）
质量明显更高 —— **但它根本不在 prompt 里**。

**修法**：

- **删掉固定配方**，改成「**第一步：先判题型，再决定画面引擎**」：先归类 `problem_type`
  （顺手把它从"无人消费的自由文本"变成有用途的一步），再回答"这道题最关键的
  变化/对比/累积/构造过程，用什么画面能一眼看见"，并给出 8 类题型的典型机制
  （参数扫描 / 分割累加 / 只画图形 / 两边同做一件事 / 累积构建 / 逼近定格 / 穷举展开 / 不出动画），
  明确标注**这是提示，不是让你从中挑一个套上去**。
- **三条反模板硬要求**（判据写死，便于模型自查）：① **不要默认上坐标系**（"一道几何证明里出现
  `axes`，基本就是跑题的信号"）；② **至少一镜要有"只有这道题才有"的东西**
  （"把所有名词换成『函数』『图形』『公式』后依然成立的分镜，就是模板分镜，删掉重想"）；
  ③ **不要每镜都是同一套元素**。
- **修正一条过严的规范**：原来写"每个分镜 duration 4~12 秒"，而校验层实际允许 **0.5~120 秒**、
  timeline 允许 **80 个动作**。这条把二分查找这类连续过程切成了碎片。
  现在改成"别为这个数字硬拆"，并写清两种正确写法（长分镜 / `carry`）。
  （⚠️ 2026-09-17 又改了一次：连"4~12"这个**区间**也从规格里去掉，见 §8.45 ——
  当时只改了这里的手写规范，`describe()` 里那句"目标秒数（4~12）"漏了。）
- **few-shot 从 1 个补到 4 个**，覆盖**不同结构**而不是不同题：
  ① 函数题（tracker 参数扫描）；② **几何题，全程没有 `axes`**（polygon + angle + 割补）；
  ③ 网格类 + **`carry` 承接**；④ `intent=none` 纯文字。
  并加了一句去偏说明："它们只示范**怎么写**，**不是让你照抄它们的结构**"。

**顺带点破了渲染引擎**（原先一个字都没提）：

```
这份 JSON 会被确定性地编译成 Manim（Python 数学动画库）代码，渲染成视频。
你可以用对 Manim 的了解来判断什么画面好做、什么代价高，但请记住：
你输出的不是 Manim 代码，而是一套小写下划线的 DSL —— 两者的名字是故意错开的。
```

收益是拿到模型的先验：它知道每帧重算的元素代价高、知道公式要过 LaTeX 编译、
知道元素库里没有 3D / shader / 交互 / 外部素材（这三条直接写进了 prompt）。

**代价与对冲**：点破引擎最容易引出新错 —— 模型开始拿 Manim 的类名当 `kind` 填
（`Axes` / `Brace` / `SurroundingRectangle`…）。所以红线 1 同步收紧成
「规格外的名字一律非法，不管它在 Manim 里多常见」。
⚠️ **但故意不列举禁止项** —— 把禁止的名字写进 prompt 是典型的反向诱导（"别想大象"），
只讲清规则就够了。顺带清掉 `describe()` 里残留的 `always_redraw` / `add` 词汇泄漏
（要素材自己先别把引擎词汇摆出来）。

**成本要如实说**：system prompt 从 **8445 → 18622 字符**（§8.9 记的基线是 8445）。
补的都是质量相关的东西，但成本翻了一倍多。若要收，最划算的裁剪点是
`describe()` 里 `riemann` / `parametric` / `number_plane` 这几个低频元素的 `desc` 展开
（纯列表项，模型真要用再查规格也来得及）。

**验证**：4 个示例全部**合法 + 幂等 + duration 与 timeline 实算守恒**；
`--spec` 与 API 导入正常；50 条测试全绿。

---

### 8.32 `carry`：跨分镜承接（"数组不要每镜重画一遍"）

**用户反馈**：「每个分镜最后都会淡出，那对于一些需要保持连贯的场景，比如二分查找，
我发现它会每个分镜都重新画一遍数组。」

**根因：这不是模型的锅，是设计里根本没有"跨分镜状态"这个概念。** 三处证据：

| # | 位置 | 事实 |
|---|---|---|
| ① | `renderer.py` 的 `_CLEAR` | 插在分镜 2..N **之前**，无条件 `FadeOut(全部) + self.clear()`。Web 默认路径（`iter_full` → `render(sections=…)`）就走它 |
| ② | `render_scene()` → `dsl.build_scene()` | 每个分镜**独立重新构建**元素（`_e_arr = Table(...)`）。就算去掉 `_CLEAR`，第二镜也会在上一镜的数组上面再叠一个新数组 |
| ③ | `_norm_action()` | `target` 必须在 `declared` 里，而 `declared` 只装**本分镜**声明的元素。跨分镜引用直接报"引用了未声明的元素" |

**所以模型每镜重画数组，是它在这个约束下唯一能做的事。改 prompt 治不好** ——
写什么提示词，`_CLEAR` 都照淡不误。

**新语法**（后续分镜里只写 id，不写内容）：

```json
{ "id": "arr", "carry": true }
```

含义：这个元素在上一分镜结束时就在屏上，本分镜**不重画、也不清掉**，
之后照常可以用动作动它（`move_cells` / `move_to` / `set_color` / `indicate`…）。

**三条硬校验**（少一条就是一种静默失效）：

1. **分镜 1 不许写 `carry`** —— 没有上一镜可承接，放过去就是元素凭空消失。
2. **必须在上一镜结束时真的在屏上** —— 把上一镜的 timeline **模拟**一遍：
   `create/write/fade_in/grow/draw_border/show` 算上屏，`fade_out/remove/clear_all` 算下屏，
   **声明了却从没被动作点过的元素不算在屏上**（就是动态元素那个老坑）。
   承接一个不在屏上的东西 → 下一镜一片空白，且不报错。
   （模拟只认确定的事实：`transform/replace` 一律当作"不改变在屏状态"，宁可漏报不误报 ——
   误报会把正确的分镜拒掉，白烧一轮重试。）
3. **依赖必须一起承接** —— 只承接 `plot` 不承接 `axes`，边界清屏会把坐标系清掉、
   只剩一个飘着的图。报错里直接给出该补的那一行。

另：`carry` 里重写参数会被拒（承接就是"原样还在"，重写不会生效 —— 改了不生效正是最忌讳的失效）。

#### 实现里踩到的两个坑

**① 两条渲染路径必须分开处理，而且坏法不一样。**

| 路径 | 作用域 | 承接元素怎么处理 | 统一处理的后果 |
|---|---|---|---|
| `render()`（一个 Scene 演到底） | 所有分镜共用一个 `construct()` | **一行代码都不生成** —— 上一镜的 `_e_arr` 变量还活着、对象还在屏上 | 若重建：变量指向一个不在屏上的新对象，后续 `move_to` **打在空气里** |
| `render_split()`（每镜一个文件） | 每个分镜独立 Scene | 重建一次并**立刻 `add`**（不走入场动画） | 若不重建：`NameError`，整个分镜渲不出来 |

靠 `env["carry_mode"] = "reuse" | "rebuild"` 区分（`_prepare_env` 传入）。
边界清屏也从"一刀切 `clear()`"改成 `_clear_except(keep_ids)`：用 `m is not k` **身份比较**
排除承接元素（⚠️ 不能用 `m not in [...]` —— manim 的 `Mobject` 定义了 `__eq__`，
内容相同的两个对象会误判）。

**② 规范化不幂等，实现时当场就撞上了。**

`carry` 会被展开成「上一镜的完整声明 + `carry: true`」（builder 要靠它重建），
但展开形态会被喂回校验入口（`validate(validate(x))`、replace-scene），于是第二遍
把规范化自己的产物判成"多写了参数" —— 与 §8.6 / §8.8 是同一类问题。
现在按「**带 `kind` 就是展开形态**」识别，并要求它与上一镜**逐字段一致**：
既保幂等，又不放过"偷改参数"。

#### 为什么不做"自动识别"

即"同 id、声明相同就自动算延续"。看起来很省（模型现在的输出就能直接变连续），
但**"两段独立例题都用 `ax`/`plot`"会被误判成延续**：`create` 动画凭空消失、元素直接跳出来。
这正是本项目最忌讳的静默失效，所以宁可要一个显式语法。已在规格里明确拒绝。

#### 验证（三层）

1. **11 条新测试**（`tests/test_carry.py`）：正常路径 + 两条渲染路径 + 五类必须拦住的错误
   + 一条"展开形态不接受/内容不一致要拒"。**50 条测试全绿。**
2. **老分镜零影响**：把 HEAD 版本的 `storyboard` 包整份取出来逐字节比对 ——
   **22 份示例 × `render()` + `render_split()` 全部完全相同**，且没有任何一份走到 carry 分支
   （没有 `carry` 时，那两段清屏代码原样沿用）。
3. **端到端真渲**：`reuse` 与 `rebuild` 两条路径各渲一份都出片成功
   （reuse 模式实测 7.6s，估时 7.3s，差 4~5 帧是 15fps 下的对齐误差）。

**前端不用改**：`web/src/lib/types.ts` 里 `elements` 故意是 `unknown[]`
（"内部 schema 的权威在 Python 侧，不重复建模"），新增字段对前端透明。

**建议的验收题**：**二分查找**。它同时验证两件事 —— 模型会不会改用"长分镜"、
以及真要拆镜时会不会用 `carry`。

---

### 8.33 二分查找那题抽帧：两个"代码跑了、画面错了"

**用户反馈**（原话）："现在渲染还是不能避免重叠啊"。

**取证方式：抽帧看画面**（不是读日志 —— 这两条一个错都不报）。用 §8.32 建议的验收题
（`讲一下二分查找` → 3 分镜，`carry` + `cell_box` + `move_cells`）真渲一份，
`ffmpeg -ss` 抽关键帧，一次抓到两个：

| # | 现象 | 帧 |
|---|---|---|
| ① | 分镜 2 底部**4 行不同的提示文字叠在同一位置**，笔画穿插糊成一团 | 修复前 t=16s |
| ② | 蓝框（候选范围）**横穿整张表、右边还探出画面**；黄框（中间位置）是对的 | 修复前 t=16s |

#### ① `replace` 之后，旧文字被 manim 重新加回屏上 —— 是**代码生成**错了，不是模型的锅

模型写的 JSON 完全合法：

```json
{"do":"replace","target":"info","into":{"id":"i2","kind":"text","content":"7 < 15，说明 15 在右边…"}}
{"do":"replace","target":"info","into":{"id":"i3","kind":"text","content":"范围缩到 4~7，中间是 nums[5] = 11"}}
…共 7 次（i2 ~ i7），每次的 target 都是同一个 `info`
```

生成出来是这样（**错的**）：

```python
self.play(ReplacementTransform(_e_info, _e_i2), run_time=1.2)
self.play(ReplacementTransform(_e_info, _e_i3), run_time=1.2)   # ← 又拿 _e_info
self.play(ReplacementTransform(_e_info, _e_i4), run_time=1.2)
```

`ReplacementTransform(a, b)` 的语义是"把 a 移出场景、把 b 加进来"，所以第一次之后
`_e_info` 已经**不在屏上**了。而 manim 的 `Scene.add_mobjects_from_animations()`
（`manim/scene/scene.py`）里有一条：

```python
# Anything animated that's not already in the scene gets added to the scene
if mob is not None and mob not in curr_mobjects:
    self.add(mob)
```

于是第二次起，`self.play(...)` 先把**上一版的文字重新加回屏上**、再叠一层新的 ——
**每替换一次多一层**，7 次之后底部糊成一片。`transform` 不受影响：它的源对象一直留在
场景里原地变形（这也是为什么老分镜没暴露过这个问题 —— 之前没人把 `replace` 连用）。

**修法**：`_Builder` 加一层"元素 id → 现在哪个 id 的变量才代表我"的换绑
（`_var()` + `self.alias`）。`replace` 完成后 `self.alias[target] = into`；
所有"元素 id → 变量名"的地方统一走 `_var()`（`act` / `_parallel` / `_place_calls` /
`_point_code` / `_mk_group`）。于是生成变成正确的替换链：

```python
self.play(ReplacementTransform(_e_info, _e_i2), run_time=1.2)
self.play(ReplacementTransform(_e_i2, _e_i3), run_time=1.2)     # ← 换成上一环
self.play(ReplacementTransform(_e_i3, _e_i4), run_time=1.2)
```

⚠️ **踩到的坑记在这里**：第一版只改 `act()` 里那个局部 `v()` 函数，没动 `_parallel`
和几个构造器里的 `{_V}{id}`，属于"改了一半"。这类"同一个映射有五个出入口"的东西，
漏一个就是一个新的静默失效面 —— 所以这次统一收敛到 `_var()` 一处。

#### ② `move_cells` 只挪中心、不改大小 → 框和网格错位（顺带修掉一条**失效的检查**）

分镜里 `win` 声明是 `cols:[1,8]`（整张表宽），后面一路 `move_cells` 到 `[5,8]`、
`[7,8]`、`[8]`。生成出来是：

```python
self.play(_e_win.animate(run_time=1.2).move_to(_cells_center(_e_arr, [1, 2], [8])))
```

`move_to` **只挪中心、改不了大小** —— 框永远是声明时的 8 格宽。被居中到"最后一格"之后，
左边横穿整张表、右边探出画面。

⚠️ **校验层本来有一条"框的大小不能变"，为什么没拦住**：它比的是
`len(rows)` / `len(cols)`，而 `[1,8]` 和 `[5,8]` **长度都是 2**，照样放行。
也就是说这条检查**两个作用都没起到**：拦不住该拦的（跨度从 8 格变 4 格），
还挡住了该做的（二分查找这类"候选范围逐轮减半"根本没法表达）。

**修法**：`move_cells` 改成**变形到"框住目标格子"的那个新框**：

```python
self.play(Transform(_e_win, _cell_box(_e_arr, [1, 2], [8], color=BLUE, stroke_width=3.5, buff=0.04), run_time=1.2))
```

位置**和大小**都由格子算出来，所以同宽就是纯滑动（卷积核那种）、改宽就是重新贴合
（候选范围缩小那种），**不需要分支**。同时：
- 删掉那条失效的"大小不能变"检查（它的前提"只能 move_to"已经不成立了）；
- 把 `scale` / `stretch` / `rotate` 一并纳入"不许作用在 cell_box 上"
  （它们和 `move_to`/`shift` 一样会破坏对齐，以前只拦了后两个）。

**技术前提（实测过，不是猜的）**：`SurroundingRectangle` 之间的 `Transform` 是安全的 ——
`_cell_box()` 的产物不管框多大都是 **16 个点**（`corner_radius=0` 的圆角矩形），
而 `Mobject.interpolate()` 只要求两点数相同。

#### 验证（四层）

1. **50 条测试全绿**（改动没碰任何一条断言）。
2. **alias 层是纯增量的**：把 `_Builder._var` 打回旧行为（`_V + id`）跑第二遍 ——
   **23 份示例生成的 114 个源码文件逐字节完全相同**（`_bad_case_demo.json` 是故意写错的负例）。
   `_var` 是这次唯一"每次引用元素都会经过"的地方，它恒等就说明对老分镜零影响；
   `move_cells` 那条路径示例压根不走（全仓 grep 过，没有一份示例用 `cell_box`/`move_cells`）。
3. **同一份分镜重渲 + 抽帧对照**：底部只剩**一行**正确文字；蓝框每次都**和网格线重合**
   （t=8s 框住 4 格、t=12.5s 框住 2 格、t=14s 与 t=16s 收成 1 格，黄框始终在声明的那一格上）。
4. **两条渲染路径都真渲出片**：默认 `render()`（`reuse` 承接）13.0s 出 3 段；
   `--split`（`rebuild` 承接 + 每镜独立文件）14.6s 出 3 段并拼接成功 —— 抽帧看**两条路径画面一致**。

#### 教训

1. **"元素替换"必须换绑**：`ReplacementTransform` 之后，原来的变量指向的是一个
   **已经被移出场景**的对象。而 manim 对"被动画却不在场景里"的对象是**宽容**的
   （默默加回来），所以这类错误永远不会以异常的形式出现，只会表现为"画面上多了东西"。
2. **别信"这条检查拦住了"**：`len(rows)` 那次比较看起来像在管大小，其实管的是元素个数。
   **检查写错了比没有检查更危险** —— 它会让人以为这个方向已经覆盖了（同 §8.7 的教训）。
3. **`move_to` 类的动作用来"挪位置"时，先问一句：要不要同时改大小？**
   只要目标是"贴合某个由内容撑出来的东西"，就该整体重新算一遍，而不是挪个中心。
4. **同一个映射不要有五个出入口**：见 ① 里那个"改了一半"的坑。

---

### 8.34 字幕过渡可配 + 版面判定从"数条数"改成"估尺寸"

**用户反馈**（原话）：

> 不用了，确实修复了，但是这个 ReplacementTransform 对这种底部字幕效果看起来不是很好，
> 最好换个效果，另外现在格子的大小是不是被固定死了，还是更自由一些吧，
> 而且还有什么一行不能超过几列这种，我觉得应该用更智能的方式判断会不会出界

三件事各自独立，但都落在同一条链上：**校验层定"能不能做" → 代码生成定"怎么做" →
运行时兜底**。所以原则是**判断逐级上收、兜底只留一层**。

#### ① `replace` 加 `effect`，默认从"形变"改成"分段交叉淡化"

`ACTION_SPEC["replace"]` 加可选参数 `effect`（`crossfade`（默认）/ `slide` / `write` / `morph`）：

| effect | 生成的 play |
|---|---|
| `crossfade` | `FadeOut(a, rate_func=_xfade_out), FadeIn(b, rate_func=_xfade_in)` |
| `slide` | `FadeOut(a, shift=UP*0.35), FadeIn(b, shift=UP*0.35)` |
| `write` | `FadeOut(a), Write(b)` |
| `morph` | `ReplacementTransform(a, b)`（旧行为，也是 `transform` 的效果） |

**四种效果的场景增删语义完全一致**（旧的离场、新的在场），所以 §8.33 那一行
`self.alias[a] = self.alias.get(b, b)` 一个字都不用改 —— 这也是为什么换效果没有把
BUG-013 带回来。

**读 manim 源码确认过三件事**（这类"看起来该没事"的地方正是本项目出过静默失效的地方）：

1. `Animation._setup_scene()` 只对 **introducer** 调 `scene.add` —— `FadeIn`/`Write`
   都是 introducer，新对象会在**淡入状态**下进场景（`begin()` → `interpolate(0)`
   把它设成那个 `opacity=0` 的副本），所以不会先整整一帧不透明地闪一下；
2. `Scene.add_mobjects_from_animations()` 对 introducer 直接跳过，也不会在 play 开头提前加上屏；
3. `FadeOut` 是 `remover`，结束时 `scene.remove(旧对象)`；`FadeTransform` 虽然也是
   "旧的下屏、新的上屏"，但它内部对旧对象做 `ghost_to(stretch=True)` —— **会把旧文字
   拉伸着去贴合新文字的包围盒**，字幕两边字数不同时反而更难看，所以没选它。

**⚠️ 这个默认实现改了两轮，两轮都是"逐帧看画面"看出来的 —— 没有一轮能靠读代码发现**：

- **第一版**：最"标准"的交叉溶解 —— 两半**全程**同时淡。逐帧拼图看下来，1.2 秒的过渡里
  有 **0.4 秒两句话糊在一起读不出字**（底部字幕是**居中**的，前后两句字数常常不同，
  重叠区就是一团）。
- **第二版**：改成"分段交接" —— 旧的前 60% 淡尽、新的从 40% 才淡入，重叠 20%。
  用户看完的反馈是：**"淡出没淡出完淡入就来了"**。那 20% 的重叠在画面上就是"没淡完就来了"。
- **第三版（现行）**：**严格先后、零重叠** —— 各占 `run_time` 的一半：

```python
def _xfade_out(t):  return min(1.0, t / 0.5)            # 旧的：前半段淡尽
def _xfade_in(t):   return max(0.0, (t - 0.5) / 0.5)    # 新的：后半段才淡入
```

逐帧序列（每 0.2 秒一张）确认：旧的字渐隐 → **中间有一帧完全没有文字** → 新的渐显。
两个函数在 `t=0/1` 端点分别取到 0 和 1，所以**收尾状态和"完整演一遍"完全一致**
（不影响任何按最终形态做的判断：`_fit` / `cell_box` / keep 集合都在播放前算好）。
`tests/test_replace_effect.py` 里把"任何时刻都不允许同时可见"钉成了断言
（101 个采样点：旧的没淡尽时新的必须为 0，反之亦然）。

**代价（要如实说）**：每半段只有 `run_time/2`。`run_time=1.2` 时各 0.6 秒 ——
如果觉得切换太快，把 `run_time` 加大（默认 1.6），而不是去改这两个 rate_func。

#### ② 表格格子尺寸可调（`cell_w` / `cell_h` / `pad_x` / `pad_y`）

先把"格子被固定死"的真身找出来：**manim `Table` 的默认 `h_buff=1.3`** —— 每个格子
凭空比内容宽 **1.3 个单位**（实测 `cell(1,1)` = 内容 0.1547 + 1.3 = 1.4547），
8 列光留白就吃掉 10.4，而 DSL 之前**一个尺寸参数都没暴露**。

实现用 manim 自己的扩展点：给每个单元格套一个**不可见定尺框**
（`VGroup(Rectangle(w,h).set_stroke(opacity=0).set_fill(opacity=0), content)`），
`Table.get_cell()` 按包围盒 + `h_buff/2` 推格子边界，网格线取相邻列间隙的中点 ——
**两者都与各列是否等宽无关**，所以套框之后 `cell_box`/`move_cells` 仍然严丝合缝
（实测：套 0.9×0.7 的框后 `cell(1,1)` = 2.2×1.5，正好是 `0.9+1.3` 与 `0.7+0.8`）。

**一个反直觉但必须做的默认值切换**：`cell_w` 是"格子的**最终**宽度"，而留白 1.3 意味着
内容区只剩 `cell_w-1.3` —— 想要 1.2 宽的格子**永远做不到**（下限是 内容+1.3，实测会直接
报"cell_w 定小了"）。所以**给了 `cell_w`/`cell_h` 时默认留白换成紧凑值**
（`pad_x=0.35` / `pad_y=0.25`）。这条规则的实现只有一处 `metrics.effective_pads()`，
**校验层和代码生成都走它** —— 两边各写一份的话，"校验说 1.2 放得下、渲染出来 2.5 宽"
这种静默不一致迟早发生。

**不写任何新参数时生成代码逐字节不变**（连 `h_buff` 都不显式传：显式传"和默认值一样"
的数也会改字节）。

#### ③ 三类硬上限 → `storyboard/metrics.py` 的内容估算

删掉的：表格最多 8 行 / 每行最多 8 列 / 单元格最多 80 字（这条还是**静默改内容**）、
`cell_box` 格号最多 6 个。它们**两个方向都错**：拦不住"8 列单字表宽 12.03、8 列公式表宽 30+"
这种真正的差异，却挡住了 `[1..8]` 选一整行这种完全正常的写法。

新模块是**纯算术**（不 import manim/LaTeX），系数全部实测（字号 8~72 全区间取**上界**）：

| 类别 | 每字符 ÷ font_size | 出处 |
|---|---|---|
| CJK / 全角标点 / 大写 | 0.0150 | fs=20 → 0.014877；`W` fs=10 → 0.014932 |
| 小写 `m w` | 0.0126 | fs=12 → 0.012442 |
| 小写 `i j l f r t` | 0.0062 | fs=24 → 0.0042 |
| 小写其余 | 0.0068 | fs=24 → 0.0063 |
| 数字 | 0.0084 | fs=18 → 0.008238 |
| 其它（标点/运算符） | 0.0100 | **保守值**，罩住 `~`（实测 >0.0062 才被第一次测试抓出来） |

行高 `0.0134×fs`、行距 0.22（实测 4 行总高 2.2569 = `4×0.399 + 3×0.22`，公式精确吻合）。
表格宽 = `Σ(每列最宽内容) + 列数 × pad_x`（实测 12.0285 vs 内容 1.70 + 8×1.3 = 12.10 ✓）。

**小写字母必须分三档**：第一版按"最宽的 `m`"给所有小写定系数，26 个字母的一句英文
被估出 **2.08 倍** —— 标定测试的"不能过于保守"那条当场变红。

**⚠️ 一个必须留余量的地方**：估算器给的是**有意偏大的上界**（实测 1.05~1.7 倍），
所以"估算 > 预算"**不等于**"真的放不下"。最直接的反例是**二分查找那张 8 列表**：
实际渲染宽 **12.03**（放得下），估算 **12.80** —— 判定线取 1.0 会把它误拒，
用户重渲同一个分镜会突然"校验失败"。所以判定线是 `BUDGET × OVERFLOW_SLACK(1.15)`：
明显超出的照样硬拒（估算 20 对预算 12.4 → 1.61 倍），边缘情况放行给运行时的 `_fit`
（真缩了会打 `[WARN]`，不再静默）。

#### ④ 兜底缩放 `_fit`：只保证"不出画"，且真缩了要喊

⚠️ 这一处**改了两次，第一次是错的**，第二次才对。完整教训见 §8.41。

**第一版（错，2026-09-16）**：想解决"写死 `max_w=12.4 / max_h=6.6` 只对居中元素成立"
的问题，改成按元素**当前中心**算对称可用空间 `2 * min(中心+半边长, 半边长-中心)`。
居中元素算出来恰好还是 12.4×6.6（所以当时以为"老分镜观感零变化"），但**贴边元素被毁了**：
`place` 把元素摆到 `buff=0.5` 时，可用空间被算成负数，缩放系数炸掉 ——
buff ≤ 0.7 必命中（0.4 → 缩到 0%、0.5 → 0%、0.6 → 48%）。根因是把
"**居中内容的容量预算**"当成了"**画面有多大**"，于是把边距重复扣了一次。

**第二版（现行，2026-09-17）**：判据改成"**画面**"，预算只用来给居中内容封顶 ——

```python
room = 2 * min(半边长 - 中心, 半边长 + 中心)      # 绕中心缩放，更紧的一侧说了算
room = min(room, 预算)                             # 只对居中内容起作用
```

三条性质同时成立：居中 → 还是 12.4×6.6（老观感零变化）；`to_edge`/`to_corner` 摆出来的
元素 → `room` 恰好等于"自身尺寸 + 2×buff" → 恒不缩；中心已出画 → `room ≤ 0`，
**只告警不硬缩**（绕中心缩放救不回来，硬缩只会把整句变没）。
另加 `_FIT_MIN_SCALE = 0.5` 下限：宁可留一截在画外，也不把内容缩成看不清的灰块。

**告警也重写了**（原文案"校验层的尺寸估算没拦住它（见 metrics.py）"是**误导** ——
这些文字远在 12.4×6.6 之内，`metrics` 放行是对的，是护栏自己重复扣了边距）：
现在自证"原始多大 / 缩到多少 / 画面多大 / 该改哪里"，同一个元素只喊一次
（`always_redraw` 每帧调用不会刷屏）。

#### ⑤ 顺手补掉的存量静默失效：表格"每行长度不一致"会变成用户的 500

`rows` 写成 `[["1","2"],["3"]]` 时校验层一路放行，直到 manim 的 `Table.__init__` 抛
`ValueError: Not all rows in table have the same length.` —— **非 `DSLError`**，
穿透校验层变成"服务端异常"，而且重试必然复现。已在 `T_TABLE` 规范化里显式检查。

#### ⑥ 顺手修掉 `generate.py --name` 在"读分镜文件"模式下静默失效

`--name` 的帮助文本是"输出文件名前缀"，但只有 LLM 分支给它赋过值，
文件分支最后落在 `name = name or 文件名` 上 —— 也就是**写了等于没写**。

这个坑是这轮验收时撞上的：要把一份分镜**重渲回它自己的 task 目录**（前端卡片就指那儿，
`output/videos/<task名>_scene/<质量>/`），输出名只能取文件名，于是必须先假装把 JSON
复制成 `<task名>.json`。现在两条分支都认 `--name`，重渲一条命令：
`python generate.py output/_tasks/<task>/storyboard.json --name <task>`。
（同 §8.7 的教训：spec 里承诺了、`_norm_*` 却没带出去的那种不一致。）

#### 验证（五层）

1. **98 条测试全绿**（新增 4 个测试文件 / 48 条，`pytest` 仍 <4 秒）。
2. **标定不变式 223 条全绿**（`MSB_CALIBRATE=1` 才跑，默认跳过）：真实渲染 vs 估算，
   语料含二分查找那题的真实字幕、每类字符的极端形态、混排公式、多行文本，
   字号 12~72。断言两条：**估算 ≥ 实测**、且最坏不超过 1.7 倍（防止保守到误杀）。
3. **老分镜零影响**：23 份示例 × 2 条渲染路径 = **114 个生成源码，分镜体逐字节零差异**。
   ⚠️ 这一轮**不能再宣称"整文件零差异"** —— `_fit`/`_table`/`_xfade_*` 都在
   `RUNTIME_HELPER` 里，而它是**整段注入每个生成文件**的。所以做法是：把新代码产出的
   `_fit(x, 'id')` 正则还原成 `_fit(x)`，再与"把 `_emit_object` 打回旧写法"生成的结果
   逐字节比 —— 等价于"老路径唯一的变化就是那个命名参数"（144 个命名点，0 差异）。
   顺带确认**没有任何示例走到 `_table(` 新路径**。
   ⚠️ 探针第一版把分镜体标记写死成 `scene 1 | dsl`，而老示例是 v1 模板
   （`| template: xxx`），"找不到标记"静默退化成"返回整个文件"，于是 `_table(` 的断言
   全被 RUNTIME_HELPER 里的函数定义命中 —— **结论全是假的**。改成按正则找标记、
   并把"退化次数"打出来之后才是可信的。
4. **端到端真渲（两条场景）**：
   - 新参数验收分镜（统一格子 + `cell_box` 跨格 + `slide` 替换）：逐帧确认
     格子等宽、**格框与网格线严丝合缝**（跨 4 格 / 3 格 / 1 格都对齐）、上滑替换是
     "旧的往上走 + 新的从下面顶上来"；
   - 二分查找那题重渲：`replace` 没写 effect → 走默认交叉淡化，逐帧拼图确认
     **交接处干净**（改分段交接之前那 0.4s 是糊的）；终态帧与 §8.33 修复后的终态
     **字节完全相同**（证明只改了过渡，没改结果）。
5. `describe()` 与 `llm.py` 的规范同步更新（新增 `effect` 的可抄示例、格子参数、
   版面预算一句话），并加了两条硬要求："同一位置换字用 `replace`，别另 create 一个叠上去"
   和"元素太大是**直接拒绝**，别指望缩小字号硬塞"。

#### 教训

1. **"估尺寸"必须明说估的是上界**：估小了是静默失败（渲染时字被缩到看不清），
   估大了是"误拒好内容"。选前者，但**判定线要补偿这个余量**，否则会把实测 12.03 的
   正常表格拒掉 —— 而用户只会看到"重渲同一个分镜突然校验失败"。
2. **保守的系数也要按字符分档**：按"最宽的字符"给整类定系数，26 个字母会被估出 2 倍。
   分档的成本很低（一张系数表），收益是"不误杀"。
3. **默认值会决定参数能不能用**：`cell_w` 配默认留白 1.3 ⇒ 这个参数**几乎永远无法满足**。
   给"最终尺寸"类参数定默认值时，先算一遍它的下限从哪来。
4. **一条规则只能有一份实现**：`effective_pads()` 同时被校验层和代码生成调用。
   两边各写一份，就会长出"校验说 A、渲染是 B"这种最难查的静默不一致。
5. **过渡效果要抽帧看，不能只看代码对不对**：生成代码"看起来完全正确"
   （干净的 `FadeOut/FadeIn` 链），但两半全程叠在一起在画面上就是糊的 ——
   而这件事只有逐帧拼图才看得出来（单抽一帧要么抽到正常帧、要么把中间态误判成 bug）。

---

### 8.35 「思考到一定程度就卡住」：读超时被伪装成"服务端异常"

**用户反馈**（原话）：*现在发现思考一定程度就卡在这了*，随手贴上了服务端日志：

```
File "storyboard/llm.py", line 579, in _iter_sse_stream
    for raw in resp:
  ...
  File "ssl.py", line 1103, in read
TimeoutError: The read operation timed out
```

界面上是「正在思考… 已 199s · 共 71880 字」，思考流停在思考自己写 `"Scene 3": {...}` 的地方。

#### 先回答那个直球问题：是输出 token 上限吗？

**直接原因不是，但它极可能是间接原因** —— 这两句要分开说，因为它们的修法完全不同：

| | 截断（`finish_reason=length`） | 这次的现象 |
|---|---|---|
| 上游最后发什么 | 一个 `finish_reason=length` 的包 + `[DONE]` | **什么都没发，连接挂着不关** |
| 我们这边 | 流正常读完 → `[WARN] 输出被 max_tokens 截断` → 回灌重试 | `resp.readline()` 一直阻塞 → 读超时 |
| 报错落点 | `parse_json_object` 的"疑似截断" | pipeline 兜底分支 → 「服务端异常」 |

**判据在 `seen` 里**：截断那条路 `finish_reason` 一定有值；这次它**一个字都没收到**，
说明上游是"停在那里不动"。而它恰好发生在 **71880 字符的思考**之后 ——
按中英混排粗估约 2.5~3.5 万 token，已经**贴着甚至超过 `max_tokens=32768`**
（留档里最近 12 轮的 `reasoning_tokens` 是 102~18346，这一轮是异常长）。
所以最合理的解释是：**思考把预算烧穿 → 上游没有按约定收尾而是挂住连接 → 我们只能等到读超时**。
`llm.local.json` 里 `timeout=300`，而截图停在 199s，也就是说还要再干等一分半才等到那句报错。

#### 这次失败最大的问题是：它什么都没留下

`grep Timeout` / `"ok": false` 扫 `output/_llm/calls.jsonl`（48 条）：

```
timeout mentions: 0
ok=false count  : 1
```

**一次挂了五分钟的调用，留档里连一行都没有** —— 因为 `TimeoutError` 不是 `LLMError`，
而 `_http_post_json_stream()` 的 `try` 只包住了 `urlopen`：
读流的异常从 `yield from _iter_sse_stream()` 直接冒上去，`chat_stream` / `stream_storyboard`
/ 路由清一色 `except LLMError`，一个都接不住，最后落在 `pipeline.stream()` 的兜底
`except Exception` 里变成一句「服务端异常」。**这正是本项目最忌讳的静默失效：
不是没出错，是出错了没人知道错在哪。**

#### 还有一个放大伤害的行为

`stream_storyboard()` 里判定"要不要降级成非流式"的判据是 `if pieces:` —— 而 `pieces`
只累计**正文**。思考走单独的回调，不进 `pieces`。于是"吐了两万字思考、正文一个字没出"
这种情况会被判成"上游不支持流式"，**再发一次请求**：用户白等的那一分钟变成了白等两轮，
而且第二次大概率在同一个地方再断一次。

#### 修法（四层，都不改对外契约）

1. **就地包成 `LLMError`**：`_http_post_json_stream()` 的 `yield from` 外面接
   `(OSError, http.client.HTTPException)` → `LLMError(_stream_break_message(...))`。
   于是它自动进留档、自动被路由转成一条可读的 `error` 事件。
2. **报错里带上"走到哪一步"**：`_iter_sse_stream()` 往 `seen` 里累计
   `reasoning_chars` / `content_chars`；文案据此分岔 ——
   只有思考没正文 → 直说"思考把预算烧穿了、上游没按约定收尾、调大 `max_tokens`
   只能买回『更晚才失败』"；有正文 → 按"网关/连接断了，重试即可"。两种都给一句
   **"把 `timeout` 调大不会让它继续吐字，只会让你等更久才看到失败"**。
3. **降级判据改成"这一条流上收没收到过任何增量"**（`pieces or meta["reasoning_chars"]`）：
   收到过思考就说明流式是好用的，**不再白重发一次**。
4. **把"思考要克制"提前到第一次调用**：`build_system_prompt()` 的输出格式那节加一条
   硬要求（想清楚画面引擎就直接动笔、别逐帧推演/别列备选/别写完再自查）。
   原先这句话只在 `_FEEDBACK_TRUNCATED` 里出现 —— 那是**已经白烧一次调用和几分钟之后**。

顺带：`chat_stream()` 的失败留档现在把**已经收到的正文**一起落盘（`ok=False`，
`lookup()`/`last_reply()` 只看 `ok=True`，不会污染回放）——半份 JSON 是判断
"语法写错还是被掐断"的唯一物证。

#### 验证

- `tests/test_stream_break.py`（4 条，全绿）：两路字符计数；断流包成 `LLMError`
  且文案里两个数字都在、"只有思考"与"断流"两种结论分得开；**断流不退回非流式**。
- 全套 `pytest`：**102 passed / 224 skipped**（标定用例照旧默认跳过）。

#### 教训

1. **"上游什么异常"不是我们能定的，但"它变成什么异常"必须是**：
   读流阶段任何 `OSError` / `HTTPException` 都得在**最靠近它的那一层**收敛成 `LLMError`。
   否则上层的 `except LLMError` 全是摆设，最后只剩兜底分支那句谁也看不懂的「服务端异常」。
2. **只包住 `urlopen` 等于没包**：`urlopen` 只是"连上"，真正的等待全在后面的读循环里 ——
   超时几乎总是发生在那里，也就是**必须包住 `yield from`**。
3. **诊断信息要能回答"走到哪一步"**：`TimeoutError` 本身什么信息都没有，
   加上"思考 N 字 / 正文 M 字"才能一眼分清"思考烧穿预算"和"网络断了"。
4. **判据要选那个"真的代表某件事发生了"的量**：`pieces`（正文）不代表"流式不可用"，
   代表它的是"这一条流上有没有收到过任何东西"。


### 8.36 三维接入：DSL 第一次能画立体（顺带照出参数曲线的静默 bug）

**做了什么**（2026-09-17，最小闭环）：`three_axes`（ThreeDAxes）+ `surface`（z=f(x,y)）
+ `space_curve`（空间参数曲线）+ `rotate_camera`（相机旋转）。含三维元素的分镜自动换基类
（`StoryboardScene(ThreeDScene)`）。验收样例 `examples/f1_space_surface.json`（两镜：
抛物面转视角 → `carry` 承接坐标系与曲面后，在 y=0 平面画截线）。

**"没有 3D"这个说法本身是错的**：Manim 里 3D 一应俱全（`ThreeDAxes`、`Surface`、
`Sphere/Cube/Cone/Cylinder/Torus`、`Polyhedron` 系列，以及 `ThreeDScene` 的
`set_camera_orientation` / `move_camera` / 环境自转；外部图片也有 `ImageMobject`）。
没有的是**我们这套引擎**。原 prompt 把"我们没接"写成了"这类画面设计不出来" ——
已改成准确表述：**"做不到"和"还没做"是两件事**，写混了会让读的人误判能力边界，
甚至据此否掉本该能做的题。

#### 三个坑（每个都留了定点用例，见 tests/test_3d.py）

1. **`parametric` 一直是坏的 —— `t` 在运行时根本没定义**（既有 bug，被这次照出来）
   `_expr_call(expr, "t")` 生成的是 `_safe_eval(r"""cos(t)""", t)`，而运行时签名是
   `_safe_eval(expr, x, **extra)`：第二个**位置**参数绑到名字 **x** 上。
   于是 `cos(t)` 在运行时 `NameError` → 被 `_safe_eval` 兜底成 `0.0` →
   **整条曲线塌成一个点**：画面能出、内容全错、一声不响。
   校验层那边更早还拒了一道（自由变量只认 x），两层叠在一起，
   症状就是"怎么写都画不出参数曲线"。修法：自由变量名不是 x 时**再补一个同名实参**
   （`t=t`）；`_FREE_VARS` 按元素类型放开 `t` / `y`。
   ⚠️ 不能全局放开：`plot` 的 expr 写 y 属于写错，放开后会被兜底成一条 y=0 的直线
   —— 又是一个静默失败。

2. **z 会被 2D 坐标系默默吃掉**
   `Axes.coords_to_point` 的实现是 `zip(other_axes, coords[1:], strict=False)`
   （manim 0.21 `coordinate_systems.py:2183`）：2D 的 Axes 只有两条轴，第三个坐标
   被**直接丢弃**。所以 `_check_3d` 硬拦两类写法：曲面/空间曲线挂在 2D `axes` 上、
   点写了 z 但 ref 是 2D 的 axes。（另：`rotate_camera` 在没有 three_axes 的分镜上会调
   `self.move_camera`，而那是 ThreeDScene 才有的方法 —— 2D 的 Scene 上直接 AttributeError，
   整个分镜渲不出来。）

3. **值域顶出坐标系 = 直接飞出画面**
   实测：曲面 z 到 9.7、`z_range` 只到 7，一个"碗"顶满并冲出屏幕上沿。
   现在 `_check_3d` 在 9×9 个采样点上算值域（求值用与生成代码同一套语义的
   `_safe_eval_static`），报错里直接给出"range 该放宽到多少"。
   含 tracker 的表达式**跳过**检查：构建期算不出来的东西不能瞎报 ——
   误报比漏报更贵（白烧一轮 LLM 重试）。
   三维元素同时**豁免 2D 版面估算**：`metrics` 的系数是按 2D 帧宽高标定的，
   而 3D 物体在屏幕上的大小由相机角度决定。

#### 两条工程决定

- **基类按需切换**：`dsl.needs_3d()` 扫**整份**分镜（不是单镜 —— 拆分渲染时
  `carry` 存根里没有 kind，单看那一镜认不出是三维元素），含 3D 才用 `ThreeDScene`。
  纯 2D 分镜的生成源码**逐字节不变**（本次用 3 份示例做了改动前后的全文比对），
  增量渲染的缓存判据不受影响。
- **屏幕固定文字**：3D 场景里一切都会被相机投影，右上角的标题会跟着转斜、甚至转出画面
  （实测：`place:[{"corner":"ur"}]` 的公式在 phi=68 / theta=-48 下几乎看不见）。
  判据：**文字类元素 + 写了 `place`（edge/corner/next_to/center）= 屏幕字幕**，
  登记为 fixed-in-frame；不写 place 或用 `at_point` 的标签留在三维空间里跟着转 ——
  一个开关两种语义，不必给模型引入新概念。
  登记调的是 `self.camera.add_fixed_in_frame_mobjects(...)`，**不是**
  `ThreeDScene.add_fixed_in_frame_mobjects(...)`：后者会顺手 `self.add()`，
  元素会在分镜一开始就冒出来，出场动画全废。
  `always_redraw` 的元素另挂一个 updater 每帧补登记（相机那份白名单按**对象身份**存，
  而 `become()` 换出来的子对象是新对象）。

#### 验证

- `tests/test_3d.py`（18 条）：校验该拦/该过各 5 类；`t=t` / `y=v` 确实传进了 `_safe_eval`；
  基类切换；HUD 登记；纯 2D 分镜不含 `fixed_in_frame`。
- 全套 `pytest`：**121 passed / 224 skipped**（标定用例照旧默认跳过）。
- 端到端：`examples/f1_space_surface.json` 480p15 真渲出片（76.9s / 15s 视频），
  抽帧肉眼确认：曲面完整落在画面内、公式端正固定在右上角、y=0 的截线随视角转动可见。

#### 教训

1. **"能力边界"必须写准**：把"我们没实现"说成"做不到"，会污染所有下游判断
   （包括我们自己排期时的判断）。这类句子值得逐个核对，而不是"意思差不多就行"。
2. **兜底式容错会掩盖 bug**：`_safe_eval` 把异常变成 `0.0` 是"保证能出片"的正确设计，
   但它让"变量名没绑上"表现为"曲线是一个点"而不是报错 —— 所以**校验层必须把
   名字对不上的情况单独查一遍**（这一条 project 里原本就有，只是漏了自由变量）。
3. **同一份语义不能有两份实现**：值域检查要算表达式，而 `_safe_eval` 只存在于注入代码
   的字符串里。这次补的 `_safe_eval_static` 必须与它同语义，代码里已写明这条约束；
   将来改函数白名单时**两处一起改**（这是本项目最容易漏的一类，见 §8.6 系列）。


### 8.37 多轮里第二轮的 brief 是「复述」而不是「承接」（用户反馈）

**症状**：第一轮问「讲一下导数是什么」，模型给了一段解释；第二轮说「用视频讲一下，
给无基础的人」——第二轮的 brief 还是**同一套解释又讲了一遍**（措辞略变），
读起来像"没听懂要求"。用户的期待是**承接式**的一句：
「好的，我换成零基础的口径，并把过程拆成 3 个分镜：先…再…最后…」。

**根因在 prompt，而且是 prompt 明确写出来的**（不是模型不听话）：
1. `brief` 的字段说明只写了「先用 2~3 句话直接回答这道题（讲人话，**不要客套话**……）」
   —— 而"好的，我换个口径"正好属于它被禁止的那一类。
2. `intent 与 outline 的规则` 里只有 3 条关于 brief 的话（要能看懂、不能为空、别写成
   "下面我用动画演示一下"），**没有一条区分"首轮"和"追问轮"**。
   于是模型在修改类追问里只能做它唯一被要求做的事：再答一遍。

**修法**（`build_system_prompt()`）：
- 新增规则 4：**历史里已有你上一轮的回答时（追问 / 要求改讲法、改画面），
  brief 要先说清「这一轮改了什么、接下来怎么讲」，不要复述上一轮**；
  并给了一句示例写法；同时点明"问的是**新问题**时直接回答新问题"（否则会矫枉过正）。
- 字段说明里的「不要客套话」改成「不要空话套话」，并指回规则 4 ——
  不然两句话互相打架，模型会挑对自己省事的那条执行。

**验证（真调用，不是看代码猜）**：脚本跑通同一个两轮场景（relay / deepseek-v4.1-flash，
40.1s + 46.0s），第二轮 brief 实际输出：
「好的，这次换成零基础的口径：不再只给定义，而是先用小球下落算一次平均速度，
再把时间段一点点缩短，亲眼看着割线压向切线，最后才给出导数的定义。……」
——首句与第一轮无重叠，且给出了这一轮的做法。

**教训**：
1. **规则要写清适用场景**。"brief 是回答"这条对首轮是对的，在追问轮却是错的；
   只描述一种场景、指望模型自己分辨，等于把一条错误的规则写进了 prompt。
2. **prompt 里的禁止项要连"为什么禁"一起想**。「不要客套话」的本意是防"下面我用动画
   演示一下"这类空话，结果误伤了多轮里必要的承接语 —— 禁止项写得越短，误伤越隐蔽。
3. **这类问题只能靠真实多轮调用发现**：单轮测试、静态读 prompt 都看不出来
   （第一轮的表现完全正常）。多轮行为要有自己的验收样例。


### 8.38 历史预算 6000 → 20000：那个数是拍的（用户提问"为什么网页版能做持久多轮"）

**问题**：用户问"为什么网页版 AI 都能持久多轮，我们这个不行"。查下来三件事，
只有一件是真限制，其余是自设的：

| | 网页版 | 我们（改前） |
|---|---|---|
| 全量历史存服务端 | ✅ | ✅ 也存（`output/_tasks/messages/`，无上限） |
| 每轮发给模型的量 | 窗口的很大一部分 | **6000 token 封顶**（自设） |
| 超限怎么办 | 摘要压缩（保留最近若干轮原文） | **直接丢最旧的**（注释里明确"不做摘要"） |
| 当前状态（画面/文档） | 用工具/摘要带回上下文 | 只有 brief 进历史，分镜不在上下文里 |
| 前缀缓存 | 靠它摊薄成本 | ✅ 已在吃（`store.py` 不存分镜 JSON 就是为这个） |

**根因量级**：`_HISTORY_TOKEN_BUDGET = 6000` 是当初拍的（注释只写"估高一点比估低好"，
无实测依据），而 `/api/chat` 的《工作手册》已 **22187 字符**（每轮固定发）——
**历史预算只有固定开销的 ~27%**。于是"聊到十几轮就开始丢最早的内容"，用户感受即"记不住"。

**改法与实测**（2026-09-17，relay / deepseek-v4.1-flash）：

| 预算 | 20 轮对话 | 40 轮 | 60 轮 |
|---|---|---|---|
| 6000 | 全留 | 留 25 轮 | 留 25 轮 |
| 20000 | 全留 | 全留 | 全留 |
| 40000 | 全留 | 全留 | 全留 |

耗时（同一份长历史、输出压到最小以隔离输入影响）：

```
预算  6000：留 50 条历史，服务端 prompt_tokens=14569，耗时 3.5s
预算 20000：留 120 条历史，服务端 prompt_tokens=19994，耗时 3.1s   ← 没变慢
```

**结论：输入从 14.5k 涨到 20k token，耗时不变**（差异在噪声内）—— 因为这是**输入侧**增长，
预填充很便宜；决定等待时间的是**输出侧**（思考 + 正文）的 token 数。
真正的代价在**计费**（输入 token 多约 37%），换来的轮数是 2.4 倍以上，很划算。

顺带把预算做成 `MSB_HISTORY_TOKENS`（写坏只 WARN 并回退默认值），
这类"该按实测调"的参数不该写死在代码里。

**最终默认值 = 10000**（同日稍后定）：分镜 JSON 进历史之后（§8.41）每轮约 **1630 token**，
20000 会留出一半用不满，于是收到 10000 —— 够 **5~6 轮**带画面的对话、省约 40% 输入 token，
要更长随时用环境变量拉回去。**结论**：这个数从"拍的 6000"变成"量过再定的 10000"，
中间那一步（20000）的价值是给出了"输入变大不影响耗时"这个事实。

**顺带澄清一个常见混淆**（用户也问到了）：**思考不占这 6000**。
6000 数的是**输入历史**（`trim_history` 只累加历史消息的 `content`）；
思考走独立字段、`on_reasoning` 默认丢弃、**从不进历史**；它和正文共用的是
输出侧 `max_tokens`（§8.11 / §8.35 那条"思考烧穿预算"）。三本账别混：
输入·历史预算 / 输入·固定开销（手册）/ 输出·上限（思考+正文）。

**还没做（下一步的性价比排序）**：
1. **裁剪时用摘要替代直接丢**（网页版"持久"感的主要来源）：把要丢的最早几轮压成一段
   "之前聊过 X、定了 Y"插在历史最前面，保留最近 N 轮原文。
2. **让当前状态进上下文**：整份重生成时把"当前分镜概述/大纲"带上（几百 token），
   模型才能真的"改"而不是"重来"（`regenerate_scene` 已经有这个能力，
   `/api/chat` 的整份重生成路径没带）。
3. 长期：把分镜/视频做成**按需取回**（工具化）而不是塞进上下文 —— 网页版敢用满窗口的原因。


### 8.41 多轮"改一版"：把分镜 JSON 当作模型自己的对话输出（用户反馈"它没在改，是在重新生成"）

> 编号说明：本节原先叫 §8.39，与另一条同号（"字特别小"，见后面的 §8.39）撞了，
> 现改为 §8.41。§8.42 是本节的姊妹修复（多写参数报错）。

**症状**：第一轮出了视频，第二轮说「第 2 镜讲慢点」——结果**整份重做**：分镜 id 变了、
镜数变了、上一版调好的地方全没了。用户的感受是"它没在改我的视频，它在重新生成一个"。

**根因**：`/api/chat` 喂给模型的只有历史里的 **brief**（一段纯文字结论），
里面没有元素、没有动作。模型手里**没有可照抄的原料** —— 不是它想越界，是它没法照抄。

**走过的弯路（值得记）**：先做的是"把上一版压成每镜一行摘要塞进 user 消息"
（元素 + 动作 + 时长，约 20~40 token/镜）。结构级够用，**细节级不够**：
摘要里没有坐标/颜色/内容，非目标镜只能重写。实测目标镜改对了（时长 9→12 秒）、
第 1 镜没动，但**第 3、4 镜被顺手重写了**。

**最终做法（用户要求"把 json 也当作模型的对话输出"）**：分镜 JSON 进**历史里的
assistant 消息**，等于"模型自己说过的话"——
1. `store`：每一轮的 `raw` 分镜存进 assistant 记录的 **`meta.storyboard`**，
   `content` 仍是 brief。**为什么分开放**：content 是给前端聊天气泡显示的东西，
   塞几千字 JSON 会把界面撑坏；而"模型该看到的"和"界面该显示的"本来就是两件事。
2. `llm._history_with_storyboards()`：发出去之前，把带 `meta.storyboard` 的记录还原成
   **"brief + 【这一轮产出的分镜 JSON】+ ```json 围栏"** 的 assistant 消息。
   没有 storyboard 的记录（纯文字回答、旧记录）原样通过 —— **老会话零影响**。
3. system 规则 6 改成以那些 JSON 为基准：只改点名的分镜，其余
   `id`/`duration`/`elements`/`timeline` 逐项一致，**连坐标、颜色、文字内容都不要动**；
   即使被校验打回也只修被拒的那处。
4. `chat.py`：生成后用 `diff_scenes()` 打 WARN 说"这一轮动了哪几镜"
   （**只告警不拦截**：模型有权为了修校验错误调整别的分镜），日志打 `旧版=有/无`。

**为什么要"放回对话"而不是"另起一段当前画面"**：
- 多轮天然连续，**也不局限只带上一版** —— 要哪一版都在上下文里，由 token 预算裁剪；
- 历史**只追加**，前缀缓存照常命中（另起一段"当前画面"每轮都在变，会把历史缓存整个打掉）。

**代价是量过的**：一份 JSON 中位 **1463 token**（49 份实测，最大 2776），
每轮从 ~360 涨到 ~1630 —— 默认预算 10000 下能留住约 **6 轮**带画面的对话
（20000 时约 12 轮；最终默认值定在 10000，见 §8.38 末尾）。

**实测对比**（同一份 `examples/free_pythagorean.json` 当上一版，同样问「第 2 镜讲慢一点」）：

| 版本 | 第 1 镜 | 第 2 镜（点名） | 第 3 镜 | 第 4 镜 |
|---|---|---|---|---|
| 摘要方案 | 没动 | 时长 9→12 秒 ✓ | **元素被重写** ✗ | **元素被重写** ✗ |
| **JSON 进历史** | 没动 | 时长 9→10 秒 ✓ | 没动 ✓ | 没动 ✓ |

JSON 版本这一轮**只有目标镜的时长变了，其余三镜连元素参数都逐项一致**
（指纹含 place/坐标/颜色/内容）。首轮调用遇到上游 500（中转站服务端错误），重试即通过。

**关于用户提的另一条关键约束**：同一会话里经常问不同的题，**前后两个视频可以完全无关**。
所以规则 6 里**不能有"偏向保留"的默认**（原来是"拿不准按改上一版处理"，已改）——
判据交还给"用户这一句话本身"：像完整题目 → 新题（前后无关也正常，不要为了接得上硬留
上一版的元素）；像针对画面的改动要求 → 改上一版。**把新题误当修改（新视频带着旧骨架）
比把修改误当新题（重做一版、题目不变）更坏。**
另外**没做**"代码层回填非目标镜"：它能给硬保证，但**代码判断不了意图**，
用户问新题时把旧分镜搬回去就是灾难。

**防回归**：`tests/test_multiturn.py`（13 条），守着四条容易回归的线 ——
JSON 真的被塞进去了（含参数级原料）、无 storyboard 的记录原样通过、
`content` 不许被污染（界面）、历史**只追加**（前缀缓存）、以及两个入口真的调了那个还原函数
（"参数加了但某一处没传"是本项目踩过的同类坑）。

**顺带修掉的旧注释**：`store.append_thread_message` 上原写着"不记分镜 JSON：
① 吃预算 ② 前缀缓存不命中"。两条后来都不成立（①中位 1463 token；②只追加就照常命中），
已改写成现在的理由 —— 免得下一个人照着旧注释又把它删掉。


### 8.42 多写的参数被静默丢弃（元素 + 动作两处）

**症状（潜伏型）**：模型写 `{"kind":"text","content":"…","rotate":30}`（`rotate` 是 place 的键）
或 `{"do":"create","target":"x","duration":2}`（想说时长、名字写错）——
**什么都不发生**，画面照旧，不报错。元素侧那会儿还会把它收进 `out["_dropped"]`，
而那个字段**除它自己之外没有任何地方读**。标准静默失败。

**先量影响面，再决定改成硬报错**（这一步不能省）：
- 25 个 examples：多写参数 **0 处**；元素级下划线注释键 **0 处**；
- 调用留档 49 条成功回复 / **992 个元素**：多写参数 **0 处**；
- 启用后拿 25 个 examples + 47 条真实回复整体回归：**新规则拒绝 0 份**
  （那 20 份报错都是当时的首批回复本来就有的合法错误：文字超宽、格号重复）。

**修法**：
- 元素侧：未知参数直接 `DSLError`，报错里带上"这个 kind 支持哪些参数"，
  并**指路**（"要调位置/角度那是 place 里的键；要改外观用动作"）——
  否则模型只会把它删掉，画面还是没变。下划线开头的是注释（examples 在用 `_note`），照旧忽略。
- 动作侧：`_norm_action` 原来只按 `do` 分支读它认识的键，所以先补了一张**机器可读**的
  `_ACTION_KEYS`（`ACTION_SPEC` 里的 `args` 是给人/模型看的散文，校验层要的是名单），
  再按它报错。`move_to` 的 `to` / `target_point` 是代码里**故意容忍**的历史别名，保留。
- `_dropped` 现在恒为空，字段保留只为让规范化产物的**形状稳定**（幂等测试逐字段比对）。

**防回归**：`tests/test_strict_params.py`（13 条），其中最重要的一条是
**`_ACTION_KEYS` 与 `ACTION_SPEC` 必须一一对应** —— 新增动作忘了登记，会让该动作的
每一次使用都报"没有这些参数"（报错本身是对的、指向也清楚，但合法分镜全被拒，
症状是"某个动作永远用不了"，排查要翻半天）。

### 8.39 「有时候渲染出来的字特别小」：护栏把边距扣了两次（用户反馈）

**现象**：用户问"为什么现在有时候渲染出来的字特别小"。特征是**时有时无** ——
同一份分镜里有的字正常、有的字小到看不清，甚至**整句消失**。

**定位**：问题不在 `metrics`（校验层放行了**是对的** —— 那些文字远在 12.4×6.6 之内），
而在运行时的兜底护栏 `_fit()`。它是 §8.34 那一轮（2026-09-16）改出来的，
当时的写法是"按元素**当前中心**算对称可用空间"：

```python
avail_h = 2 * min(c[1] + half_h, half_h - c[1])   # 旧（错）
```

`2 * min(左, 右)` 的含义是"**以当前中心为对称轴**、只准待在安全框内"。这对**居中**
元素正好等于 6.6（当时只验证了这一种情形，就觉得"老分镜观感零变化"），
但对 `place` 贴边摆放的元素是错的：

| `place` 写法 | 中心 y | 算出的可用高 | 缩到 |
|---|---|---|---|
| `{"edge":"down","buff":0.7}` | −3.11 | 0.38 | 100% |
| `{"edge":"down","buff":0.6}` | −3.21 | 0.18 | **48%** |
| `{"edge":"down","buff":0.5}` | −3.31 | **−0.02** | **0%（字消失）** |
| `{"edge":"down","buff":0.4}` | −3.41 | **−0.22** | **0%** |
| `{"corner":"ul","buff":0.6}` | 偏上左侧 | 一侧余量极小 | **41%** |
| `{"edge":"up","buff":0.9}` / 居中 | — | 正常 | 100% |

**`buff ≤ 0.7` 必命中**，而 0.4 / 0.5 / 0.6 恰好是 few-shot 示例自己用的值
（`free_pythagorean.json` 的 `{"edge":"down","buff":0.4}`、`llm.py` 示例 2 的
`{"edge":"up","buff":0.6}`）—— 模型照着抄，命中率很高。这就是"有时候"的来源：
**取决于模型这次写的 buff 与文案行数**。

**根因一句话**：把 `_FRAME_MARGIN`（**居中内容的容量预算**）当成了"**画面有多大**"，
于是对已经被 `place` 显式摆到边距里的元素**又扣了一次边距**。
`place` 的 buff 是作者要求的排版，不是错误；两者打架时 `_fit` 不修正位置、直接把字毁掉。

**修法**（见 §8.34 ④ 那段）：判据改成"画面"，预算只用来给居中内容封顶 ——

```python
room = 2 * min(half - pos, half + pos)   # 绕中心缩放 → 更紧的一侧说了算
room = min(room, budget)                  # 只对居中内容起作用
```

- 居中 → `room` 是整条边长、被预算封顶 → 还是 12.4×6.6，**老观感零变化**；
- `to_edge`/`to_corner` → `room` 恰好 = 自身尺寸 + 2×buff → 恒不缩（会缩当且仅当真出画）；
- 中心已出画 → `room ≤ 0` → **只告警不硬缩**（绕中心缩放救不回来，硬缩 = 整句变没）；
- `_FIT_MIN_SCALE = 0.5` 下限：宁可留一截在画外，也不把内容缩成看不清的灰块。

**告警文案一并修掉**：原来写"校验层的尺寸估算没拦住它（见 metrics.py）"，
**把排查方向带偏了** —— 照这条线索去查估算器会白查一轮。现在自证
"原始多大 / 缩到多少 / 画面多大 / 该改哪里"。

**验证**（三层，都留了证据）：

1. `tests/test_fit_layout.py` **15 条**（不 import manim，毫秒级）：居中口径不变、
   6 组真实 `place` 坐标不得被缩、真出画仍要缩、中心出画只告警不缩、下限生效、
   告警去重且可执行。全量 **148 passed**（改前 98+ 条，无回归）。
2. **公开分镜端到端**：`free_derivative` / `free_pythagorean` 渲染，**日志零 `[WARN]`**
   （改前该几何题会触发，就是用户看到的那批）。
3. **同一分镜改前/改后逐帧比对**（`free_pythagorean`，只换 `_fit` 实现、其余不动）：
   512 帧里 234 帧有差异，集中在 4.93–14.27s 与 21.53–27.80s。
   量底部字幕的实际像素尺寸：

   | 时刻 | 改前 | 改后 |
   |---|---|---|
   | 24.0s | 61 × **110** | 73 × **375** |
   | 26.5s | 61 × **110** | 72 × **418** |

   并排图更直观：改前那帧底部**只剩一个孤零零的"9"**，改后是完整的
   "两个小正方形的面积之和，正好等于大 c"。

**教训**：
1. **"保守"不等于"安全"**：护栏缩得越狠看起来越稳妥，但缩到 0% 是比轻微出界
   **更严重**的静默失败 —— 出界至少看得见、知道要改哪；字缩没了只会让人以为"模型没写这句"。
   任何自动缩放都该有**下限**，且下限处要出声。
2. **两套边距概念不能互不知情**：`place.buff`（作者要求的边距）与 `_FRAME_MARGIN`
   （居中内容的容量预算）是两回事。护栏若按自己的边距去"修正"作者摆好的位置，
   就会在"贴边 + 文案长"时爆发 —— 而这两种写法在示例里到处都是。
3. **注释里那句"老分镜观感零变化"只验证了居中一种情形**就当成了结论。
   论证"不变"必须**枚举**受影响的写法（居中 / 四边 / 四角 / 超长内容 / 已出画），
   否则这句注释本身就是在传播错误信心（它是 2026-09-16 那一轮评审时被引用的依据）。
4. **告警文案属于诊断接口**：指向错的模块比没有告警更坏（同 §8.35 那条
   "抓了信号却不用它"的教训）。

### 8.43 提示词两处基调修正：开篇「先讲明白，再谈分镜」+ 动态由「鼓励」改回中性

**来源**：用户反馈（2026-09-17）两条 ——

① §8.31 那一轮把动态写成了**鼓励**口径（"这是特点、不是缺点""就该让关键的量真的动起来"，
还专门叮嘱"不必回避"），**实测效果并不好**，要求改成中性：不鼓励，也不批判。
② 开篇「你是一名数学讲解视频的分镜师。**你的唯一任务**：把用户给的数学题，设计成一份
**分镜 JSON**」把"把 JSON 填对"摆在了"让人看懂"前面。用户要求明确：**比起分镜，更重要的是
让人看懂、听懂**；设计分镜之前要先保证视频展示的**不只是可视化** —— 每个数哪来的、
公式为什么这么写，都要交代，**不能把公式往那儿一摆就完事**。

**改动一：开篇角色定位**（`storyboard/llm.py::build_system_prompt()` 第一段）

> **原**
> 你是一名数学讲解视频的分镜师。你的唯一任务：把用户给的数学题，设计成一份**分镜 JSON**。
>
> **现**
> 你是一名数学讲解视频的分镜师。你的任务是把用户给的数学题**讲明白**，并把它设计成一份**分镜 JSON**。
>
> ⚠️ **让人看懂、听懂，比分镜本身重要得多。** 设计分镜之前，先把这道题**怎么讲**想清楚：
> 视频里出现的每一个数**是哪来的**（题目给的、还是上一步算出来的）、每一个公式**为什么
> 这么写**（这一步在做什么、凭什么成立），都要在画面里交代清楚。**把公式往那儿一摆就完事，
> 不叫讲解。** 分镜只负责把这套讲法安排成画面，它替代不了讲法。

放在**手册最前面**（角色定位之后、引擎说明之前）是刻意的：它是这本手册里唯一一条
"任务目标级"的要求，其余全是"怎么把 JSON 写对"。顺序本身就在告诉模型谁更重要。

**改动二：动态取舍改中性 —— 两处必须一起改**

| 位置 | 原（鼓励） | 现（中性） |
|---|---|---|
| `llm.py` 取舍常识（`- **鼓励动态**`） | "比静态元素慢，但这是**特点、不是缺点**"、"就该让关键的量真的动起来"、"一镜里同时有两三个动态元素是正常的，不必回避" | 「**动态看需要**」："用不用**只看画面需不需要**：让变化真的看得见有帮助就用，静止画面能讲清楚就不必加 —— 动态既不是加分项，也不是要回避的坑" |
| `dsl.py::describe()` 动态效果一节末尾 | "每帧重算只是**慢一点**，不值得为它放弃动态 —— ……就该让关键的量真的动起来，画面比出片快重要" | "每帧重算只是**慢一点**，它既不比静态好、也不比静态差：用不用只看画面需不需要" |

⚠️ **`describe()` 那处不能漏**：规格是 system prompt 的一部分（`build_system_prompt()`
直接拼进去），只改 `llm.py` 等于手册前半段说"看需要"、后半段说"别放弃" ——
**模型会听后者**（规格那节更靠近结尾、语气也更绝对）。这两条是 §8.31 **同一天**写下的，
所以改口径时要把同义句全量搜一遍。

**有意保留、没有动的**：

- 「**渲染慢一点没关系，画面质量优先**」—— 它约束的是"别为省时间砍画面"，与
  "该不该动"是两件事，仍然是有效基调；
- **红线 4**（含 tracker 引用的元素必须用 `show` 显式上屏）—— 这是硬约束，不是偏好。

**验证**：改动是纯字符串常量，`build_system_prompt()` 拼装正常（linter 零报错）。
**真调用效果待实测** —— 开篇那条能不能压住"只摆公式"、中性口径会不会让画面变静态，
都要看真出片。先跑 `python generate.py --dump-prompt` 确认实际发出的文案。

**教训**：

1. **提示词里的"偏好"比"禁令"更危险。** 禁令写过头会被校验层拦住（报错、重试、看得见）；
   偏好写过头**没有任何东西拦** —— 模型只是把"可以动态"读成了"应该动态"，症状是
   "动效味重 / 模板味重"，不像 bug，只能靠人看片发现。**偏好类表述的默认档位就该是中性，
   而不是"往某个方向推一把"** —— 推一把的收益是猜测，代价是它盖过所有别的考虑。
2. **同一件事在提示词里有两处表态时，口径必须一起改。** `describe()` 是自动生成的，
   但自动生成只保证"不用手抄"，**不保证"不会和别处矛盾"** —— 规格与手写规则之间
   没有一致性检查，靠的是改的人记得搜一遍。

### 8.44 解释性文字偏少：把「少写字」的暗示拆掉，并给示例 1 补上字幕

**现象**（用户反馈，2026-09-17）：画面里的解释性文字还是少 —— 图形/公式摆出来就完了，
看不出「这一步在做什么、这个数从哪来」。

**定位**：§8.43 只在**开篇**加了"让人看懂比分镜重要"，那属于**目标级表态**；
而手册里还有三处在往反方向拽，**全都是我们自己的规则**（不是模型不听话）：

| 位置 | 原文 | 为什么是抑制项 |
|---|---|---|
| 反模板硬要求 3 | "每一镜都摆 text + formula + 同一个坐标系，是最典型的模板产物" | 被读成"少写字 = 反模板"，而它的本意只是反对**图形元素**的复制粘贴 |
| 分镜设计规范 | "每个分镜屏幕上元素别超过 8 个，多了会挤" | 字幕也算元素 —— "写一句就占一个位"直接压掉说明（**同日整条删除**，见修法 3） |
| 示例 1（最容易被照抄的那个） | 只有 axes / plot / dot / number / tangent_line，**一个字的说明都没有** | 等于在示范"图 + 数字 = 讲完" |

外加一处**示例级错误**：规格里的 `replace` 示例，`into` 元素没写 `place` ——
而 `replace` 用的是**新元素自己**的位置（`_place_calls()`，不继承旧字幕的 place），
照抄的结果是"每换一条字幕就跳回画面正中"。字幕位置不稳，本身就会让人少写字幕。

**修法**（5 处，`llm.py` 4 处 + `dsl.py` 1 处）：

1. 反模板 3 后面补一句点破："⚠️ 它针对的是**图形元素**的复制粘贴，**不是让你少写字**"。
2. 新增一节 `### 每一镜都要有「讲解文字」`：每镜至少 1~2 处说明、标注用 `next_to` 贴在图旁
   （别全塞到四个角）、连续推导用 `replace` 逐句换字幕、字幕耗时算进 duration。
3. **整条删掉元素条数上限**（`- 每个分镜屏幕上元素别超过 8 个，多了会挤`，用户同日要求）。
   它本来就只是软建议、**校验层从不检查元素条数**；真正管拥挤的是 `metrics.py` 的尺寸估算
   （估算超预算才拒），所以删掉不会放进来"堆不下"的画面。改的地方留了注释，防止以后被顺手加回。
4. `replace` 的说明补上"`into` 要写和原来一样的 `place`"，`describe()` 里的示例也补全
   并点破"不会继承旧位置"。
5. **示例 1 补字幕**：新增 `cap`（底部字幕）+ 一次 `replace` 换成 `cap2`；
   duration 9.3 → **11.7**（`1.2 + 1.5 + 1.2 + 6.0 + 1.2 + 0.6`，与 timeline 之和严格相等）。

**验证**：`build_system_prompt()` 拼装正常、linter 零报错；示例 1 的 duration 与 timeline
耗时之和仍严格相等（11.7）。**真调用效果待实测**。

**教训**：

1. **"反 X"的规则会被执行成"少做跟 X 有关的一切"。** 反模板写的是"别复制图形元素"，
   模型收到的是"少写字"。**禁止项必须写清禁止的范围**，否则副作用会盖过目标本身。
2. **示例是上限，也是下限。** 规范里写"每镜要有讲解文字"，而最容易被抄的示例一个字的
   说明都没有 —— **示例决定的下限比规范决定的上限更有约束力**。§8.43 已经吃过一次
   同一笔账（开篇写了、示例没写）。
3. 顺带修掉一个真缺陷：规格里 `replace` 的示例缺 `place`，把"位置不变地换内容"这个卖点
   照抄出来是反的 —— **示例里的 bug 会被当成规范照抄**。

### 8.45 两处静默失效：temperature 被路由写死、"4~12 秒"只改了一半

**来源**：用户让审"还有什么不合理的限制"，先挑这两条动手（其余几条待定：规格里漏写的硬上限、
分镜边界默认清屏、2D 无相机运镜、不能插外部素材）。

**① temperature 被路由层写死 —— 与 §8.2 的 `json_mode` 同一个形态**

- **现象**：`llm.local.json` 的档案里当时写着 `"temperature": 0`，启动横幅也打印
  `temperature=0`，但网页生成实际用的是 **0.2**。
- **定位**：`api/routes/chat.py` 显式传 `temperature=0.2`、`api/routes/storyboard.py` 传 `0.3`，
  而 `resolve_config()` 的优先级是"**显式参数最高**"（`llm.py:212`）。设置页明明能改温度
  （`/api/llm/profile` 收 `temperature`、`/api/llm/profiles` 回传它），改完却被路由覆盖 ——
  横幅报一个数、实际跑另一个数。
- **修法**：两个路由**都不再传** temperature，交给 `resolve_config()`（档案 → 默认 0.2）。
  `api/config.py` 末尾那段"`json_mode` 的唯一归属"注释补上 temperature —— 那段注释本身就是
  为防这类复发放的，当初只写了 `json_mode`。
- ⚠️ **遗留**：temperature 目前**没有环境变量入口**（只有 json_mode 有 `MSB_LLM_JSON_MODE`）。
  部署想统一调温度只能写进档案或走设置页；要加 `MSB_LLM_TEMPERATURE` 得另说。
- **落地值**：`llm.local.json` 两档档案的 `temperature` 已从 **0 调到 0.2**（同日，用户确认）。
  理由：0 是贪心解码，在长 JSON 上更容易退化（同一段结构反复、动作序列打转），也更容易
  "同题型的题永远挑同一个画面引擎"；0.2 的格式合规性几乎与 0 相同但没有这两个毛病。
  0.5 以上则非法 kind/颜色名、`duration` 与 timeline 不自洽、多轮"顺手改别的镜"都会变多。
  两档**必须同值**，否则"用 direct 调 prompt、用 relay 出片"两边行为不一致、调试结论失真。
  这段理由同时写进了 `llm.local.json` 的 `_temperature_why`（那个文件不进 git，所以两边各记一份）。

**② "每个分镜 4~12 秒"只改了一半**

- §8.31 把"必须 4~12 秒"改成了"建议 4~12 秒"，但 `dsl.describe()` 里那句
  **"`duration` 是这一镜的目标秒数（4~12）"**没跟着改 —— 而规格比手写规范更靠近 prompt 结尾、
  语气也更绝对，模型照它执行。
- **后果**：与同一份手册的"单个分镜最长 **120** 秒"自相矛盾，并把 §8.32 的
  "连贯过程用一个长分镜一口气演完"顶回去 —— 模型先把长过程按 12 秒切开，再谈别的。
- **修法**：两边都改成"按内容定"，**不给封闭区间**（给区间就等于给了模型一个切分点）：
  - `llm.py`：`duration 按内容定：先想这一步要演什么、这句话要说到哪，再让秒数跟着走`；
  - `dsl.py`：`目标秒数（按内容定，上限 120 秒）`。

**验证**：linter 零报错；`api/routes/` 下已无硬编码 temperature；prompt 里已无"4~12"字样。
**真调用待实测**，两个观察点：留档里请求体的 `temperature` 是否等于档案里那个值；
分镜时长是否不再卡在 12 秒。

**教训**：

1. **同一个"归属"错误会复发。** `json_mode` 当年被路由写死 True，修完只把结论记在
   `api/config.py` 的一段注释里，**没顺手检查旁边同层的参数** —— temperature 于是原样又错了
   一遍。修这类问题时应当把同一层的参数**全部扫一遍**（这里就是 `resolve_config()` 的全部
   显式参数：provider / model / base_url / temperature / max_tokens / timeout）。
2. **"改成建议"不等于改完。** 一条约束散在**手写规范 + `describe()` + 校验层**三处，
   只改一处只是把矛盾从明面挪到暗处。**规格里写的范围必须与校验层允许的范围一致**，
   否则模型只会按那个更严的数执行。

### 8.46 carry 漏写 tracker：规格补全 + 报错说清来龙去脉

**现象**（写 few-shot 示例时踩到，2026-09-17）：总结镜用 `carry` 承接上一镜的点和切线，
校验报 ——

```
scene[4].P: 表达式 's' 里的 's' 未定义（只能是 x、数学函数，或某个 tracker 的 id）
```

`P` 的声明是从上一镜原样搬来的，`"y": "s**2"` 看着毫无问题 —— **光看这条报错根本想不到
是 carry 的事**（会去查拼写、查 tracker 名）。

**根因两层**：

1. **规格漏写**：`describe()` 里 carry 的第 3 条硬性规则只列了**元素**依赖
   （`plot` 的 `axes`、`cell_box` 的 `of`、`group` 的 `items`），**没提 tracker**。
   照着自己写的规格做，照样踩 —— 同 §8.10「规格里只给部分名字 → 模型一定猜错」。
2. **"把 tracker 也 carry" 并不是好出路**：拆分渲染下每个分镜是**独立文件、独立 Scene**，
   承接来的 tracker 只能重建，于是它回到**声明时的初始值**（不是上一镜结束时的值）。
   镜 3 扫到 `s = 3`、镜 4 却按 `value: 1.0` 重建 → 点当场从 x=3 跳回 x=1。
   **语法合法、观感是错的** —— 正是这个项目最怕的那一类。

**修法**：

- **`describe()` 的 carry 第 3 条**：补上"以及它表达式里引用的 tracker"，并单独加一句
  ⚠️ 点破承接 tracker 的陷阱与正解（**别 carry，改用固定值在本镜重新声明，写上一镜结束
  时的那个数**）。
- **`_scan_expr_names()` 加 carry 诊断**：新增两个只影响文案的参数
  （`carried` = 本镜承接了哪些 id、`prev_trackers` = 上一镜有哪些 tracker）。
  当"未定义的名字"命中"**本镜 carry 的元素 + 上一镜的 tracker**"时，改成一条说清
  来龙去脉的报错，并给出两条出路 —— 顺序刻意反过来：**先推荐"别 carry、用固定值重声明"**，
  再提"也可以一起承接（但会跳）"。否则模型会照着"把它也 carry 了"做，然后引入跳变。
- **示例侧落地**：`examples/fewshot_derivative.json` 的镜 4 改成固定值重声明
  （`P` 落在 `(3, 9)`、`tan` 的 `x: 3`，正是镜 3 扫描的终点），duration 5.6 → **6.6**。

**验证**（2026-09-17 本机实测，python 用 `D:\Miniconda\envs\manim`）：

- 全量 `pytest tests/ -q` → **160 passed / 224 skipped**（新增 1 条用例，无回归）。
- **新增用例** `tests/test_carry.py` 第 3 节
  `test_carried_element_referencing_prev_tracker_is_explained`：构造"承接的点引用了上一镜
  tracker"的两分镜，断言 ① 报的是 carry 的事、不再是裸的"未定义"；② 两条出路都在、
  且**推荐的（固定值重声明）排在前面**；③ 一个点的 x/y 两条表达式**只报一条**。
  实测文案（节选）：`scene[2].P: P 是**承接**过来的元素，它引用的是上一分镜里的 tracker
  's'，而本镜既没承接 's'、也没重新声明它。两条出路：① **更稳**：不要 carry 这个元素，
  改用固定值在本镜重新声明一遍…；② 把 's' 也一起承接（在 elements 里加
  {"id": "s", "carry": true}）—— 语法也合法，但…画面会跳回去。`
- 示例 `examples/fewshot_derivative.json`：校验通过 + **真渲成功**
  （4 分镜 26.6s → `output/videos/fewshot_derivative_scene/480p15/StoryboardScene.mp4`）。
- `test_3d.py` 那条断言旧文案的用例不受影响（它没有 prev、`carried` 为空 → 走原分支）。

⚠️ **实测之后又补了两处**（都是跑起来才看见的，静态读代码看不出来）：

1. **同一个 (元素, tracker) 会报两遍** —— 一个点的 `x` / `y` 是两条独立表达式，各扫出一处
   未定义。旧文案里两条内容不同（各自带表达式）还有信息量，而这条是整段重写的说明，
   两条**一字不差**地重复，白占回灌预算（`_MAX_ERROR_CHARS = 1800`）。
   加了 `_hinted` 集合去重。
2. **出路顺序**：初版把"① 把 tracker 也 carry"写在前面，模型会挑这条省事的做法、
   把跳变带进成片。改成 **① 更稳（固定值重声明）在前、② 一起承接在后**，用例里
   把顺序也断言住了（`msg.index("更稳") < msg.index("也一起承接")`）。

**顺带修掉一个既存 bug（验证过程中撞出来的）**：`generate.py --dry-run` 在中文 Windows 上
**必然崩** —— 它要把生成的整份源码 `print` 出来，而源码里有 GBK 编不出的字符
（模板注释里的 ⚠ U+26A0、示例标题里的 ²）。用 `f1_space_surface.json` 复现，崩在 `²` 上，
与本次改动无关。修法：`main()` 开头 `sys.stdout.reconfigure(errors="replace")` ——
**只放宽错误处理、不改编码**，能打的照常打、打不出的退化成 `?`，绝不因为"终端显示不出来"
让命令失败（同 §3.2 那条 GBK 教训的输出侧）。

**另一个观察（未处理）**：成片每镜比声明的 `duration` 长 0~0.7s —— 拆分渲染在每个分镜
首尾各插了 0.35s 的清屏/淡出（`_clear_except` / `_tail_fade`），而规格里只说了
"timeline 之和大于 duration 会变长"，没提这个结构性偏差。四镜合计 33.73s vs 声明 32.3s。
既有行为、与本次改动无关，要不要处理另说。

**教训**：

1. **规格里的"依赖清单"是白名单式的，漏一种就等于教模型踩坑。** 元素依赖和
   **标量/tracker 依赖**是两类东西，写清单时容易只想到前者。
2. **报错要说清"你这个写法是从哪儿来的"。** 这里的声明是从上一镜抄的、名字也没写错 ——
   只说"未定义"会把排查方向带偏（同 §8.35 那条"抓了信号却用错它"）。
3. **推荐方案必须写进报错。** 两条出路都合法，但一条有会跳变的副作用；
   报错如果不排序，模型会挑更省事的那条（carry），然后把跳变带进成片。

### 8.47 过渡生硬："动态元素只能用 show"是过严的规范（顺带重做示例镜 2）

**来源**（用户看片反馈，2026-09-17）：① 分镜 1 → 2 过渡生硬；② 平均变化率公式"突然就出来了"、
没有解释；③ 不喜欢字幕的 slide 切换，默认的淡出淡入就挺好。

**定位**（抽帧 + 读生成代码 —— 两条最前面的原因）：

- **过渡生硬有两层**。第一层：镜 2 把点 A 重新声明成**动态**点（引用 tracker），而镜 1 的 A 是
  静态点 —— 场景边界清屏先把静态 A 淡出、再把动态 A `show` 上来，观感是"点消失了一下又出现"。
  第二层：点 B 和割线都用 **`show`（瞬间出现）**上屏，画面"啪"地多出两个东西。
- **规范在这里是过严的**：`llm.py` 红线 4 与 `describe()` 的动态效果一节都写着
  "必须用 `{"do":"show"}` 显式显示"，可校验层 `_ON_SCREEN_ADD` 本来就把
  `fade_in / grow / create / write / draw_border` 一并算作"上屏动作" ——
  **`show` 只是"上屏"的一种，不是唯一一种**。这句措辞等于把"动态元素只能瞬间出现"立成了硬规矩。
- **公式突然出现**：镜 2 的时间线是"`show` 一堆东西 → 立刻 `write` 公式"。公式落下时，
  观众还不知道"这两个点连起来的线有多陡"。

**实测（关键一步）**：动态元素到底能不能 `fade_in`？造了个**临时探针**
（tracker `s` + 引用它的点 `P` 和线 `L`，`fade_in` 上屏后 `tracker_to`；验完已删），抽三帧看：

| 时刻 | 看到什么 | 说明 |
|---|---|---|
| 1.6s（淡入半途） | 点是**半透明**的 | 真的在淡入，没被 updater 每帧重建覆盖成"瞬间出现" |
| 3.0s | 点、线完全可见 | 淡入正常结束 |
| 4.6s | 点走到 (2.8, 7.84)，线跟着动 | **淡入之后 updater 照常工作** |

结论：**`fade_in` 对动态元素有效**，"必须用 `show`"没有技术依据。

**修法**：

1. **规范两处**（`llm.py` 红线 4 + `dsl.py::describe()` 动态效果一节）→ 改成
   "**必须显式上屏**：`show` 是瞬间出现，`fade_in` 是淡入 —— **要过渡自然就用 `fade_in`**"。
2. **示例镜 2 重做**：① 点 A 改成"**静态元素 + `carry`**"（它本来就该承接下来；静态元素 carry
   不涉及 tracker 重建 —— 这是 §8.46 那两条出路之外的**第三条路**）；
   ② 点 B 与割线改用 `fade_in`；③ 时间线改成**先画面、后公式**：
   B 淡入 → 字幕「再在曲线上另取一点」→ 割线淡入 → 字幕「两点连成的线叫割线，它的陡峭程度就是
   平均变化率」→ **这时才 `write` 公式** → 数值 → 扫描 → 字幕收口；
   ④ 字幕里的 `"effect": "slide"` 去掉（回到默认 crossfade）。
3. **示例镜 3** 的 `show` 一并改成 `fade_in` —— 不然前面淡入、后面硬出现，落差更明显。
4. 时长随之变化：镜 2 `9.1 → 12.6`、镜 3 `9.2 → 10.6`，四镜合计 `7.4 / 12.6 / 10.6 / 6.6 = 37.2s`。

**验证**：四镜时长算术全平衡（`7.4 / 13.4 / 10.6 / 6.6 = 38s`）；真渲通过；**抽帧确认**——
镜 2 开头点 A 留在屏上（不再消失重出现）、B 与割线淡入、字幕停在第一句时公式还没出现、
三个读数随 h 从 `1.00 / 3.00 / 3.00` 走到 `0.06 / 0.13 / 2.06`。全量 `pytest` → 160 passed。

**同一轮抽帧又抓出三件事**（全是"不看画面就发现不了"的）：

1. **`place` 里的表达式只在构建时求值一次。** `Δx` / `Δy` 两个标注原本用 `at_point` 写了含
   tracker 的表达式，但直角边在缩短、标注却停在原地 —— 位置根本没跟着走。修法：加 `"live": true`
   （语义正是"每帧重建，因此定位每帧重算"；规格里那句"始终套在动点外的框"就是这个用法）。
   **这是 `live` 的第一个正面示例**，示例里现在有了。
2. **`number` 的 unit 写纯 LaTeX 会被吃掉空格**：`" = \\Delta x"` 渲染成 `1.00= Δx`（等号前没间距）
   —— unit 被 `DecimalNumber` 送进 MathTex，而 LaTeX 忽略源码空格。改成**全角括号**的单位
   （`（Δx）`）后走的是 `_number()` 的 Text 拼接分支（`_NUMBER_UNIT_BUFF = 0.14`），间距才有保证。
3. **"会变的量"只能交给 `number`，但可以拼成一行**：`formula` 的内容是静态字符串、不能每帧变。
   用户要的"把值带进去变化"，做法是**符号公式 + 紧贴它下面的代入行**：代入行由三个 `formula`
   碎片和三个 `number` 用 `next_to` 串成一行（`=((1+2.00)^2-1^2)/1.00=3.00`），
   扫描时三个数一起变。⚠️ 第一版做的是"另起三行读数"，被用户否掉了 ——
   **要贴在公式上代入，不要另开一块读数面板**。
4. **配色先看主题色再选**：`PRIMARY = #58C4DD`（曲线）、`ACCENT = #FFFF00`（点 / 公式 / 数字）
   已经占掉蓝和黄，所以第二条线用 `TEAL` 会**和曲线糊在一起**（用户原话："颜色改得不好"）。
   最终选 `RED`：与蓝/黄区分度最高，而且镜 3 的切线本来就是 RED —— "割线变成切线"连颜色都连贯。
5. **给左上角让位**：代入行拉长后会撞上 y 轴的箭头（抽帧看到箭头正好插在 `1.00` 和 `= 3.00` 之间）。
   把 `axes` 整体 `shift [0, -0.5]`（在镜 1 声明一次，后面几镜 `carry` 自动继承）。
   ⚠️ 代价是 x 轴的刻度数字和底部字幕挨得更近了 —— 这是个"两头都对不齐"的取舍，只能靠抽帧权衡。
   （**下一轮就按这个思路配平掉了**，见第 7 条。）
6. **代入式改成"上下分式"，并把化简结果从公式里拿掉**（用户要求）：公式去掉尾巴 `= 2 + Δx`，
   只留 `Δy/Δx = ((1+Δx)²−1²)/Δx`；代入行用 `\frac` 而不是斜杠。
   ⚠️ 顺带确认了一个**DSL 事实**：**`\frac` 里面的数做不到实时** —— `formula` 是静态 LaTeX，
   `number` 只能当行内的一份子（或拼在数字右边的 `unit`），塞不进分式的分子/分母。
   当时的临时做法是"扫描分段 + 段末 `replace` 换整条分式"（Δx 1.00 → 0.50 → 0.05，
   3.00 → 2.50 → 2.05），代价是值按段跳。**这个临时做法已被第 9 条取代**（最终靠
   `\overline{\phantom{...}}` 把分数线单独做成元素，数字恢复实时）。
7. **"字幕离轴太近 + 太靠下"是配平问题，不是挪一个元素的问题**：三处一起动才成立 ——
   `axes.y_length` 5.0 → **4.2**（轴整体变矮，给左上角腾出两行分式的空间、同时把刻度提上来）、
   坐标系偏移 -0.5 → **-0.2**、字幕 `edge down buff` 0.5 → **0.8**（抬高、离底边远一点）。
   只动其中一处，问题只会被挪到另一头（上一版 -0.5 + buff 0.5 就是"上面挤箭头、下面挤字幕"）。
8. **配色（最终）**：**割线改回 ACCENT 黄**（用户改口）；**第二点 B 保持 ORANGE**（那轮用户只点了割线，
   ⚠️ 点和线的颜色于是不一致 —— 待用户确认要不要一起改黄）；**镜 3 的切线仍是 RED**，
   "割线变切线"的连续性断了，同样待定。
9. **最终形态：左上角整块只占一行**（用户先要"公式就占一行"，再要"只有变化的数是黄色的"、
   然后是"左边的 Δ 也得黄"和"黄色数字看着往左下偏"）——公式、代入后的分式、结果全在同一行上，
   **零绝对坐标**：
   - **配色**：`Δy/Δx =`（第三轮改成**黄色**——用户说那些 Δ 是在变的量）是 `q_lhs`；符号分式
     `q_rhs` 与 `1+` / `)^2-1^2` 是**白色**；只有三个**会变的数**（1+h、h、2+h）是 `number`
     元素（黄色、实时、连续跟着扫描变）。⚠️ 第一版把整个公式留着黄色，用户立刻指出
     "第一个等号后面还是全黄的"；把左边也改白之后，用户又说"前面的 Δ 那些也得黄" ——
     所以 `q_lhs` / `q_rhs` 拆成两个元素，用 `aligned="bottom"` 对齐（两个分母同底）。
   - **分数线是一个独立的 formula**：`\overline{\phantom{...}}`，占位串按**分子行的分段**写成
     三段之和（`\phantom{(1+}\phantom{1.00}\phantom{)^2-1^2}`）。⚠️ 写成整串会把 LaTeX 的
     运算符间距算进去，比分子行宽十几个像素（实测 113px vs 101px）；分段相加减掉这些间距才对齐。
   - ⚠️ **竖直对齐绝不能逐级靠 `next_to` 默认的"包围盒居中"**：带分式的包围盒中心**不在中线上**，
     从公式往右锚，偏移会一层层累积（用户看到的"后面的分式整体偏高、没对齐"就是这么来的）。
     正确做法是**让两个分式的分母同底**：代入分式的分母 `den` 用
     `next_to(q_rhs, right, aligned="bottom")` 锚在公式上 —— 两边分母都坐在基线上，`aligned=bottom`
     对齐的正是这两条基线；随后 `bar` `next_to(den, up)`、分子行 `next_to(bar, up, aligned=left)`，
     两个分式的分数线自然等高（实测：两条分数线同在 y=48，两个分母同在 y=52~65，差 ≤1px）。
   - ⚠️ **`number` 的相对竖直位置要单独处理**（用户第三轮："黄色数字看着往左下偏"）：
     `next_to(direction=right)` 默认对齐的是**包围盒中线**，而数字的"中线"跟旁边带括号的片段
     不一样 —— 拿 `(1+` 当参照，数字会**低 2px**（括号的中线比数字的中线低）；改成
     `aligned="top"` 又会**高 1px**（括号顶比数字顶高 1px）。实测下来 1px 就是这套"包围盒对齐"
     的极限 —— 想要 0 误差必须拿**本身含数字**的片段当参照。例子里选了 `aligned="top"`（1px
     高于 2px 低）。
   - 分子行两处 `next_to` 的 `buff`（**0.12 / 0.06**）一起定两件事：整行宽度对上分数线、
     黄数字左右留白均匀（0.09/0.09 时数字偏左 2px）；结尾 `sub_res` 的 buff 0.05 → **0.15**
     （用户："最后一个数离等号太近"）。**这些都不是随手值**。
   - 一行总宽约 8.4 单位（帧宽 14.2）。代价：镜 2 元素 13 → **20**，duration 13.2 → **13.4**。
   - ⚠️ `aligned` 只能用 left/right/top/bottom 这套"边的说法"；以前 `top/bottom` 会让渲染崩，
     见 §8.48。

⚠️ 还有一个**自己犯的错**：写示例 `_note` 时在 JSON 字符串里打了裸的 `"` 和 `\;`，
文件直接非法、`pytest` 报 `JSONDecodeError`。这已是**第二次**（§8.44 那次也是注释里的裸引号）——
**示例文件的注释里只用「」，不写双引号、不写反斜杠**。
同一天还栽了第二跤：为图省事用 PowerShell 的正则 `-replace` 改这个 JSON，结果把两个元素整段
吃掉、`Set-Content -Encoding UTF8` 又给它加了个 **BOM**（`json.loads` 直接 `Unexpected UTF-8 BOM`）。
**改这个文件（以及任何 JSON/源文件）只用编辑工具，不要用 shell 正则替换** —— 正则匹配的
字符范围和你以为的不一样，而且它不会告诉你改坏了哪里。

**教训**：

1. **"只能这样做"的措辞会杀掉一整类表现力。** 规范该写**目的**（必须显式上屏），再给**推荐做法**
   （要自然就用 `fade_in`）；把某一种实现写成唯一合法解，等于把引擎的一半能力关掉。
2. **观感类问题校验层查不出来。** 这次校验全绿、时长算术全对、linter 干净，画面上照样是硬切。
   **只能靠抽帧 / 看片** —— 而且只有看到"半透明的半途帧"才敢说 `fade_in` 有效，否则它只是猜测。
3. **示例与规范必须同一批改完。** 这次是示例先用上 `fade_in`、规范还写着"必须用 `show`"——
   若不一起改，搬进 prompt 就是自相矛盾（同 §8.43 那条"口径要一起改"）。
4. **注释也是内容。** `_note` 处在 JSON 字符串里，同样受转义约束 —— 一个裸引号就能让整个示例
   文件失效，而症状（`JSONDecodeError`）离"我刚才在注释里打了个引号"很远。**同一类错误
   在 §8.44 犯过一次、这次又犯**，说明它该进守则（"注释里只用「」"），而不是靠记性。

### 8.48 `aligned: "bottom"` 生成出不存在的名字 `BOTTOM`（校验全绿、渲染期才炸）

**现象**：示例里用 `next_to(..., aligned="bottom")` 让两条分母同底，`generate.py` 一路走到渲染
才 `NameError: name 'BOTTOM' is not defined`。

**根因**：`dsl.py::_place_calls()` 里写的是 `aligned_edge={p['aligned'].upper()}` ——
而规范（`place.next_to.aligned` 与 `group.aligned`）给的是**边的说法**（left/right/top/bottom），
manim 的 `aligned_edge` 收的却是**方向常量**（LEFT/RIGHT/UP/DOWN）。`left/right` 碰巧同名，
`top`/`bottom` 就直接变成库里不存在的名字。`_mk_group()` 有同一个问题（`group.aligned`）。

**修法**：

1. 新增常量表 `ALIGNED_EDGES = {left/right/top/bottom → LEFT/RIGHT/UP/DOWN}`（顺手认同义的
   `up/down` 与 `center`），`_place_calls()` 与 `_mk_group()` 都走它翻译；
2. 两条 `next_to` 归一化分支补上值检查：不在表里就 `DSLError("aligned 只能是
   left/right/top/bottom")` —— 否则 `aligned: "middle"` 会被静默当成 `ORIGIN`。

**教训**：**"参数名对得上"不等于"值能对上"。** `left`/`right` 恰好同名，于是这个洞藏了很久，
直到有人真的用 `bottom` 才炸；而且它炸在**渲染期** —— 校验、时长算术、linter 全是绿的。
凡是"把模型写的字符串翻译成库常量"的地方，都该有一张**显式的表**加一句值检查，
而不是靠 `.upper()` 这种"看着像"的转换。

### 8.49 公式内部按符号分色：`tex_colors` + xcolor 模板

**需求**：用户要让符号分式 `(1+Δx)²−1² / Δx` 里的那两个 `Δx` 也变黄（与 `Δy/Δx`、三个会变的数同色）。
那两个 Δx 在 `\frac` 的分子/分母**里面** —— 拆成独立元素必然让 LaTeX 大括号不平衡 ✗；
手工拼装（分子行 + 分数线 + 分母行）能上色，但每个接缝自带 ±1px 漂移，而这个示例正被用户
放大逐像素看 ✗。

**做法：让 LaTeX 自己上色。** 实测 manim 的 `MathTex` **会保留** LaTeX 里 `\color{...}` 指定的颜色
（逐字形路径的 fill 就是它），前提是**不要**给 `MathTex` 传 `color=` —— 传了 manim 的 `set_color`
会把它们整个盖掉。于是：

1. `formula()` 一律套一个自带 `xcolor` 的 `TexTemplate` —— manim 默认模板**没有** xcolor，
   直接写 `\color` 是 `Undefined control sequence`，整个分镜渲不出来；
2. 模板里顺便 `\definecolor` 一批项目调色板名，**色值从本脚本的 globals 取**：
   PRIMARY / SECONDARY / ACCENT 是 HEADER 里定义的 hex 字符串，
   `from manim import *` 带进来的其它色名是 ManimColor，两种都要认；
3. 只列**纯字母**色名（xcolor 的 `\definecolor` 对 `BLUE_A` 这类带下划线的名字不保证可用）；
4. formula 元素新增 `tex_colors: true`：为真时不给 MathTex 传 color，颜色全由 content 决定
   （没写 `\color` 的字形按 manim 默认的白色渲染 —— 正好是示例要的"骨架白"）。
   ✅ `describe()` 由 ELEMENT_SPEC 自动生成，所以**规格里自动多出这一条**，模型也能用。

**代价**：所有公式的 LaTeX 模板变了 → 首次渲染的 LaTeX 缓存全部失效（示例 21s → 60s），
之后按内容缓存恢复正常。

**顺带**：同一轮把示例里那个黄数字补了 `shift [0, -0.017]` —— `aligned="top"` 对 `(1+` 仍高 1px
（括号顶天生比数字顶高 1px），一像素微调后才与同行白字严格同基线（抽帧实测：两者底部都是 y=37）。

**教训**：**"库的默认值"和"项目的默认值"是两回事。** manim 里没有 `ACCENT` 这个常量（那是本项目的
主题色），而 `from manim import *` 让它们在脚本里看起来"就是 manim 的" —— 第一版就栽在
`module 'manim' has no attribute 'ACCENT'`。写**通用代码**（这次是构造模板）时必须想清楚每个名字的
来源；在"能跑的环境"里写出的代码，换个环境可能连名字都找不到。

### 8.50 镜 3 的节奏：两图并排 + 共享 tracker 的同步运动 + **字幕要跟着运动切**

**来源**（用户，2026-09-17）："'Δx 取 0 时割线变成切线，斜率稳定在 2s' 这话出来后不应该立马动切线，
旁边再出来一个 `y' = 2x` 的图像，然后两边同时动，并且字幕要切换到下一句，不是动完了才说那句。"

三件事，前两件是**画面结构**，第三件是**节奏** —— 而第三件比前两件更通用：

1. **旁边放出 `y' = 2x` 的图像**：左上角加第二个小坐标系（`ax2`）+ 直线 `gd` + 点 `Q` + 标签 `glab`，
   让"割线 → 切线 → 斜率"这条线终于有了自己的图形对象（同一个 `s` 在导数图上就是 `(s, 2s)`）。
2. **两边同时动**：⚠️ **不需要 `parallel` 动作** —— `P`（曲线上的点）和 `Q`（导数线上的点）引用的是
   **同一个 tracker `s`**，一个 `(s, s²)`、一个 `(s, 2s)`，天然逐帧同步。
   这是本例最值得被模型学走的写法：**"两个东西要一起动"的本质是"它们由同一个量驱动"**。
3. **字幕要跟着运动切**：上一版是"字幕 → 动 6 秒 → 结束"，那句讲解只出现在运动**之前**、之后没有话；
   现在拆成两句：第 1 句（"Δx 取 0 时割线变成切线…"）→ 放图 → **`replace` 成第 2 句**
   （"点走到哪，切线斜率就是 `2s` —— 旁边这条线就是 `y' = 2x`"）**→ 再开扫**。
   抽帧验证：t=6.5（运动刚开始 0.5 秒）屏幕上已经是**第 2 句** ✓。

   ⚠️ 为什么这条重要：写分镜的人（和模型）容易把字幕当成"动作之间的停顿"，于是讲解**总是滞后于画面**
   （画面演完了才去说它）。正确的时间关系是**"字幕先说，画面随后演它"** ——
   **一句讲解配一段运动，两者要重叠，而不是串行。**

**代价**：镜 3 元素 8 → 12、时长 10.6 → **12.6**。

**教训**：

1. **"同时"是数据关系，不是时序技巧。** 需要"两边同时动"时，先问"它们是不是同一个量的两种表示" ——
   是的话共用 tracker 就够了（还能保证永不脱节）；用 `parallel` 拼两个独立动画才是下策
   （一旦两边速度/时长不一致就露馅）。
2. **讲解与运动的时间关系只有一种是对的：字幕在前、运动在后，且两者重叠。** 把讲解排在动作之后
   （哪怕只晚一句），观众就是"先看了一件事、再去读它是什么"，注意力得倒着走。

### 8.50 续：记号要和图形对象一致；单独的总结镜删掉了

**来源**（用户同日追加）："'点走到哪，切线斜率就是 2s'，不是 2x 吗？另外最后一个分镜可以不要了，
然后把'所以「求切线斜率」就是「求导」：y' = 2x' 这个放在分镜三最后。"

4. **记号统一成 `2x`（两句字幕都改了）**：第 1 句原写"斜率稳定在 `2s`"、第 2 句原写"切线斜率就是 `2s`"
   —— `s` 是**当前点的位置**（右上角那个会变的读数 `k = 2.00…6.00` 才是它），
   而旁边那条线画的是函数 `y' = 2x`。一句话里出现两种记号，观众得自己在脑子里替换一次。
   **规则：字幕、图形标签、结论句要共用同一个记号**；"当前值"只交给会变的读数去表达。
   ⚠️ 我第一轮只改了第 2 句（以为第 1 句"稳定在某个值"用 `2s` 才读得通），用户立刻指出漏了 ——
   **同一个量在同一份稿子里只能有一种写法**，别替观众保留"两种都对"的解释空间。
5. **删掉单独的结论镜（原第四镜）**，把它的收口句并到扫描镜结尾（扫完之后 `replace` 一句）。
   理由：扫描本身已经把"每一点 → 一个斜率 → 连起来正好是 `y' = 2x`"演完了，再单开一镜只是重复。
   ⚠️ 但这条**不能被读成"总结镜不必要"** —— 删的是这一题的第 4 镜，判断标准是
   **它还有没有新东西可说**（这一题的第四镜只是在复述）。写进示例的 `_structure` 里时也带了这句警告，
   免得模型学会"分镜一律三条"。
6. 全片 4 镜 → **3 镜**、40.0 → **34.6s**。（⚠️ 同日晚些时候第 4 镜又回来了 —— 但换成了真正的推导，
   见 §8.52；最终是 **4 镜 / 43.2s**。这条正好印证上面那句"删的是复述，不是镜头数"。）

**教训（续）**：

3. **同一句讲解里的记号要和画面上的对象一一对应。** 这不是文字打磨 —— 数学讲解里，
   记号不一致等价于"刚才那个结论说的不是这张图"。
4. **"少一镜"要看有没有新信息，不是看片子长短。** 复述型的分镜该删；但删掉的**理由**要写下来，
   否则示例会教出"结构越少越好"这种同样错的模板。

### 8.51 极限不能用「取到 0」「变成」——措辞的松紧就是数学本身

**来源**（用户，2026-09-17）："'Δx 取 0 时割线变成切线，斜率稳定在 2x' 这个是不是不准确，
本身是极限不是吗？"

**对。** Δx 永远不等于 0（差商在 Δx=0 处根本没有定义），割线也只是**趋近**切线。示例里三处措辞都改了：

| 位置 | 原 | 现 |
|---|---|---|
| 镜 3 字幕 | `Δx` 取 0 时割线变成切线，斜率稳定在 `2x` | 当 `Δx → 0` 时，割线趋近切线，斜率的**极限**是 `2x` |
| outline 摘要 | Δx 取到 0，割线变成切线 | Δx 趋于 0，割线趋近切线 |
| 镜 3 `_note` | Δx 真正取到 0 —— 割线成了切线 | Δx 趋于 0 —— 割线趋近切线（附一句"是极限，别写取到 0"） |

⚠️ **同类词要全量搜**：`取 0` / `取到 0` / `变成` / `成了` —— 在极限语境里都是错的。
（镜 2 那两句本来是对的："Δx 越小，这个比值越接近 2" ✓ —— 说明不是不懂，是**写的时候没有统一标准**，
所以要么搜一遍，要么把标准写进规格。）

**为什么值得单独记**：'Δx = 0 时' 和 'Δx → 0 时' 不是"说法不同"，而是**对错之别** ——
前者把极限偷换成了取值，而"导数就是 Δx=0 时的比值"正是初学导数最典型的误解。
视频替学生把这个说法讲出来，等于**把误解固化成结论**。

**待办（要用户定）**：这条目前只修了示例。模型写的正是这些字幕，而《工作手册》里没有这条约束
（§8.44 加的那节只管"每镜要有讲解文字"，不管"讲得对不对"）。建议在手册里补一条：
**"数学话要说准：极限过程用「趋于/趋近/无限接近/极限是」，不能写「等于 0」「变成」；
「等于 / 约等于 / 趋于」三者在字幕里不能混用。"**
（2026-09-17 用户表态：**先不加**，等这批示例打磨完再说。）

### 8.52 补一镜「正统方法」：几何直觉之后，要用定义算一遍

**来源**（用户，2026-09-17）："现在只是前面铺垫，求导只是最后提了一嘴，是不是把这个问题用正统方法
在后面讲一下比较好。"

**对。** 前三镜走的全是几何路线：② 把值一代代带进去看比值逼近、③ 点走一遍看斜率连着变 ——
观众拿到的是**"这是什么"**；而"到底怎么算"只有结尾一句"求切线斜率就是求导"带过，
**等于讲了一道题、却没讲怎么做这道题**。新的第 4 镜补上这一步：

```
正统做法：用导数的定义算一遍
f'(x) = lim_{Δx→0} [f(x+Δx) − f(x)]/Δx          ← 定义
      = lim_{Δx→0} [(x+Δx)² − x²]/Δx             ← 把 y = x² 代进去
      = lim_{Δx→0} [2xΔx + Δx²]/Δx               ← 展开分子
      = lim_{Δx→0} (2x + Δx) = 2x                ← 约掉 Δx、取极限（2x 上黄）
字幕：算出来正是 y' = 2x；x = 1 处就是 2，和几何做法一致
```

**几个刻意的设计**：

- **不做任何图形铺垫**：前三镜已经把图画够了，这一镜就该是"纸笔推导"。
  ⚠️ 这与反模板规则不冲突 —— 那条禁的是"每镜都摆 text + formula + 同一个坐标系"，
  不是"不许有一镜只有公式"。
- **四行用 `next_to(down, aligned=left)` 串、左对齐**：推导的形状本身就是信息（一步步往下推），
  居中的公式块读不出"推"。
- **结论 `2x` 用 `tex_colors` 单独上黄**（§8.49 那个能力第一次用在正式镜头上）：
  和上一镜"旁边那条线就是 `y' = 2x`"对上，颜色语言一致。
- **答案收在字幕里**（`x = 1` 处就是 `2`），不再单开一镜喊结论。
- ⚠️ **两条路都不能省**：几何直觉回答"这是什么、为什么是这样"，代数推导回答"具体怎么算"。
  只讲前者 → 学会看图但不会做题；只讲后者 → 回到"把公式摆出来"的老路（§8.43 的起点）。

**代价**：全片 34.6 → **43.2s**（仍 4 镜）。

**教训**：**"讲明白"要同时覆盖"是什么"和"怎么算"。** 这一轮暴露的偏差是：几何动画太好做、太好看，
于是整片的力气都花在"让它看得见"上，而**"用定义算一遍"这个最正统、最能迁移到别的题的做法，
反而只剩一句话**。判断一部讲解片有没有讲完，可以问一句：
**观众看完，会不会做下一道同类题？** 只会看图的学生，换一道题就没了。

### 8.53 镜 4 加旁注 + 新增镜 5「常用求导公式」（附：多行 `text` 的已知问题）

**来源**（用户，2026-09-17）："最后一个分镜你可以在旁边给每一步加注释，另外之后也可以引出常用求导公式。"

**① 镜 4：每一步加一句旁注**（`a1`~`a4`，GREY、`next_to(sN, right, buff=0.35)`，
在对应公式**之后** 0.8 秒出现）：

| 步骤 | 旁注 |
|---|---|
| 定义 | ← 这就是导数的定义 |
| 代入 | ← 把 `y = x²` 代进去 |
| 展开 | ← 展开分子 |
| 约分取极限 | ← 约掉 `Δx`，再让它趋于 `0` |

⚠️ 推导最怕的失败模式就是"式子一路摆下来、为什么这么变一个字不说" —— 观众看到的是一串**等价变形**，
却不知道每一步在干什么。旁注就是把"这一步在做什么"钉在它边上（与 §8.44"每镜要有讲解文字"同源，
但粒度更细：这里要求**每一步**都有）。镜 4 元素 6 → 10、时长 8.6 → **11.8**。

**② 镜 5：常用求导公式表**（`(xⁿ)' = nxⁿ⁻¹` 上黄 + 旁注"今天这一题就是 `n = 2`"，
下面依次 `sin / cos / eˣ / ln`）。⚠️ 这一镜的**成败全在那句"都是同一个定义算出来的"**：
不写它，这一镜就是一张让人背的表，等于把前面四镜建立的"定义推出一切"又亲手拆掉。
⚠️ 结构上：④⑤ 连续两镜以文字/公式为主 —— **连续两镜是上限**，再往下接一镜纯公式就退回老路了。

**⚠️ 顺带发现（未根因）——多行 `text` 会叠在一起**：镜 5 本来想用一个多行 `text`
（content 里带换行）排公式清单，实测**各行重叠** ✗。排查到的事实：

- `rich()`（runtime helper）里写的是 `lines = str(s).split("\n")` +
  `rows.arrange(DOWN, aligned_edge=LEFT, buff=0.22)` —— 代码看着是对的；
- 那行在**生成的脚本**里是按"真实换行"split 的，所以 JSON 里该写 `\n`（真换行）；
  写成字面的 `\\n`（反斜杠 + n）会**把「\n」原样打在屏幕上**（实测）—— 同一个坑有两个面；
- 两种写法都试了：字面的打出 "\n"、真实换行的各行重叠。

**当前绕法**：每行一个 `text` 元素，用 `next_to(down, aligned=left)` 串（镜 4、镜 5 都这么写）。
**待办**：多行文本是规格里承诺的能力（"支持 `\n` 换行"），得单独查一次 `arrange` 那步到底发生了什么
（怀疑与 `_rich_line` 返回的 VGroup 之后又被 place 调用逐成员处理有关）。

**代价**：镜 4 元素 6→10（8.6→11.8s）、镜 5 新增 7.2s；全片 43.2 → **53.6s**（5 镜）。
⚠️ **搬进 prompt 前要留意长度**：示例已经从 4 镜长到 5 镜、53.6 秒，作为 few-shot 还要进 system prompt
（token 成本随镜头数涨）。真要用它当示例时，可能得考虑**拆成两个短示例**，而不是一个长示例。

**教训**：**"每步有注释"和"每镜有字幕"是两个粒度。** 前者管的是"这一步为什么这么变"，
后者管的是"这一镜在干什么"；对推导类画面，只做到后者等于把一串变形丢给观众自己反推。

### 8.54 公式后面别紧跟中文分号（中文与公式混排的标点规则）

**来源**（用户，2026-09-17）：镜 4 的收口字幕写成"算出来正是 `$y' = 2x$`；`$x = 1$` 处就是 `$2$`，
和几何做法一致"，用户截图指出"怎么能有分号的"。

**问题**：中文分号紧跟在行内公式后面时，视觉上会**粘进公式里**（`y' = 2x；` 看起来像式子的一部分），
而且它两边都是数学内容，读者会先按数学读、再发现那是个标点 ✗。改成破折号（与其他字幕一致）：

> 算出来正是 `$y' = 2x$` —— 在 `$x = 1$` 处就是 `$2$`，和几何做法一致

**顺手全量查了一遍**：整个示例里**只有这一处**是"公式后面紧跟中文标点" ✗
（其它标点要么是逗号/冒号/破折号，要么落在公式**前面**）。所以规则可以写得很窄：

- **行内公式后面要停顿就用"——"，或直接用逗号**；
- **`；` / `、` / `。` 不要紧贴行内公式** —— 这类"短促又对称"的标点最容易被读成数学符号
  （`；` 尤其像把两个式子隔开的分号）；
- 标点在公式**前面**（如"就是「求导」：`$y' = 2x$`"）没有问题 ✓。

**教训**：**混排里，标点的位置比标点本身更重要。** 这不是"用词"问题，而是"读者会把紧贴公式的符号
当成公式的一部分"—— 同类风险还有公式后紧跟数字（读成下标）、紧跟字母（读成乘号）。
写字幕时，这几个位置值得单独看一眼。

（和 §8.51 一样，这条也算"**待定要不要进《工作手册》**"的候选：字幕里公式前后的标点规则。
用户 2026-09-17 对前面那条的态度是先不加，所以这条也先只在示例里落地。）

### 8.55 示例 1 换成 5 镜完整讲解（并从文件读）；两条经验进《工作手册》

**来源**（用户，2026-09-17）："可以了这版，现在修改提示词" —— 选的是
**用新示例替换示例 1** + 加**两条**规则（字幕与运动的时序、推导类每步注释 + 两条路都要走）。

**改动 1：示例 1 从 `examples/fewshot_derivative.json` 读**（不再内联成字面量）。

- **为什么非这样不可**：新示例里全是 LaTeX 反斜杠（`\frac`、`\overline`、`\Delta`、`\lim`），
  而 `llm.py` 里那三个示例都是**普通**三引号字符串（它们只把纯文本包在 `$...$` 里，没有反斜杠）。
  一旦内联，`\frac` 会被 Python 吃成 `\f`（换页）+ "rac"、`\times` 变成制表符 ——
  **改完肉眼看不出错，要渲染才发现**。改走 `json.loads` → 重新序列化，转义交给标准库。
- 顺带让示例文件成了**唯一事实来源**（改示例不用回来同步两份 —— 漂移都是这么来的）；
  文件里 `_` 开头的键（`_note` / `_structure`）是写给作者的，`_strip_author_notes()` 递归剥掉。
  ⚠️ 别用"提示词里有没有 `_note` 字样"来验证：示例里有个元素 id 叫 `t1_note` 会误报，
  要查的是 `"_note":` 这个**键**。
- **排版**：`json.dumps(indent=2)` 会把 `[-1, 5, 1]` 这种短数组也拆成 5 行，
  示例一下子从 300 行涨到 **1150 行 / 25199 字符**；换成 `_dump_example_json()`
  （小对象压一行、超过 120 字符才逐键换行）后是 **521 行 / 17393 字符**。
  这不只是省 token：**模型是靠示例的结构去模仿的，"一个元素一行"本身就是要示范的东西**。
- 体积账：system prompt 从 ~2.4 万字符涨到 **41158 字符 / 930 行**（示例 1 占 1.7 万，约 42%）。
- **`_ECHO_LIMIT` 同步提到 20000**（用户 2026-09-17 定）：老值 12000 是按"单镜 1000+ 字符"
  的旧示例定的，而新示例一份就 17393 字符 —— **模型只要照着新示例那样写，回灌必被截断**
  （正是注释里说的那种"腰斩"，模型会顺着断口续写）。20000 = 5 镜一份 + 余量，
  代价只在重试/多轮时多花约 2k token。

**改动 2：两条规则写进《工作手册》的「每一镜都要有讲解文字」一节**
（都是这轮示例里**先做对、再回头写死**的；原来只靠模型自己碰巧做对）：

- **字幕与运动要重叠，不是串行**：先 `replace` 字幕、**再**开扫
  （示例 1 镜 3：`replace` → `tracker_to` → 扫完 `replace` 收口）。等演完再打字幕等于没讲。
- **推导类画面每一步都要有旁注**（`next_to` 挂在那一行右侧、小一号、GREY），
  而且推导要**真的占分镜**，不能结尾一句"用定义也能算出来"带过（§8.52）。
- **几何直觉与代数推导是两条路，别只走一条**：只讲前者会看图不会做题，只讲后者退回"摆公式"；
  两边都能让这道题更清楚时就都讲、并互相印证（§8.52 的教训）。

**联动更新**（不改就会指向旧示例）：`duration` 算法的例子换成新示例镜 1 的
`1.2 + 1.5 + 1.2 + 0.8 + 1.2 + 1.0 + 0.5 = 7.4`；参考示例的开场白改成
"不是让你照抄**结构或镜头数**"（示例 1 现在有 5 镜，比原来更容易被连镜头数一起照抄）；
示例 1 的标签改成"完整的 5 镜：几何直觉 + 用定义推导 + 常用公式表"。

**仍待定**（这轮没进）：§8.51 的极限措辞、§8.54 的公式后标点 —— 用户仍保持"先不加"。

**验证**：`tests/` **160 passed**（224 skipped）；`build_system_prompt()` 里示例 1 的 JSON
能 `json.loads` 往返、`brief` 仍是第一个键、5 镜时长和 53.6 与示例文件一致。

### 8.56 下一个示例：几何／割补法（`examples/fewshot_geometry.json`）

**来源**（用户，2026-09-17）："接下来做下一个示例"，题目选的是**割补法求三角形面积**
（修好数字 + 扩写成完整讲解）。四镜、27.8s、带 `_note` 作者注，**已渲染验证**：
`output\videos\fewshot_geometry_scene\480p15\StoryboardScene.mp4`。

**核心：数字必须和图一致**（这是重做这条的头号理由）。坐标刻意选成
三角形 (0,0)、(4,1)、(2,4) —— 外框正好是 **4×4 的正方形**，三个角上的缺口是
4×1÷2 = **2**、3×2÷2 = **3**、2×4÷2 = **4**（共 9），三角形 16−9 = **7**。
**全是整数**，且用鞋带公式反算过每块面积、按包围盒量过每条腿长，与标签、算式逐项吻合。

⚠️ **顺手查出三处老示例的"图和数对不上"**（都还没动）：

- prompt **示例 2**（就是这次要被替换掉的）：图画 3.6×2.4，字幕却写"底 3、高 2"，
  算式写"4×3÷2 = 6" —— 三处互不相干；
- prompt **示例 3**（卷积）：第一个窗口是 [[1,0],[0,1]]（和 = 2），算式却写成 `1+0+1+0`（项不对）；
- `examples/free_pythagorean.json`：画的是 1.8×2.4（3:4:5 的形状），三个正方形却标 9／16／25 ——
  实际应是 3.24／5.76／9。**few-shot 是要被照抄的，数字不自洽会直接教坏模型。**

**设计落点**（都对应具体教训）：全程**没有 axes**（几何只画图形本身，示范"反模板"第 1 条）；
每镜**先切字幕再开动**（§8.55 那条新规则）；三块面积用左上角三条 GREY 算式做旁注
（"每步要有注释"，§8.52／8.53）；镜 4 两行算式左对齐 + 右侧 GREY 旁注（同示例 1 镜 4）；
镜 4 把三块、数字、方框、16 **全部 carry** 过来 —— 减法在画面上必须有对应物，清屏重来就断了。

**抽帧误会一条**：10.5s 那帧右上角的 `4 × 4 = 16` 像被切掉，其实是 `write` 写到一半；
11.0s 完整。`corner ur` 走的是 manim 的 `.to_corner(UR, buff=…)`（对齐元素自身的角），不会溢出。

⚠️ **待办**：**还没有替换 prompt 里的 `_EXAMPLE_GEOMETRY`** —— 等用户看过成片再换
（替换时要连带改：示例 2 的标签文案、以及"四个示例"开场白里关于几何的那句）。

> **2026-09-17 后续：这一条被用户否了** —— 评价是"一次成型，但**太简单了**"（割补法本质只是
> 套一次公式，撑不起一个示例），于是换成了 §8.57 的赵爽弦图。**这个文件留着没删**
> （`examples/fewshot_geometry.json`，质量没问题、幂等测试也过），要删说一声。

### 8.57 示例 2 定稿：赵爽弦图证明勾股定理（`examples/fewshot_pythagoras.json`）

**来源**（用户，2026-09-17）：上一版（割补法）反馈"**一次成型**，就是**左上角的算式有点小**、
**右上角的 4×4=16 没消失**，其他都还好，但**这个太简单了，还是换个实例吧**（还是图形类）"。
换题后选的是**赵爽弦图**。四镜、声明 29.4s、成片 30.9s，已渲染验证：
`output\videos\fewshot_pythagoras_scene\480p15\StoryboardScene.mp4`。

**两条反馈的落实**（也成了新常态）：

- **算式字号**：24 → **28 / 30**（上一版把算式和 GREY 旁注写成同一个号，显得小）；
- **中间量用完退场**：镜 1 的 a / b / c 标注**不进**镜 2；镜 3 的两个算式**不进**镜 4 ——
  只 carry 这一镜还需要的元素（上一版把 `4×4=16` 一路 carry 到底，用户看得别扭）。

**数字全对**（脚本复算，不靠肉眼）：四块各 **6.0**（边长 3 / 4 / 5）、小正方形边长 **1.0** = (b−a)²、
4×6 + 1 = **25** = c²。画布缩放 0.9，四块 bbox 中心与飞入目标点都由脚本算出。

**⚠️ 踩的坑（三个，都值得记）**：

1. **"基准一变、派生量没跟着变"**：四个旋转角原本是 0 / 90 / 180 / 270（基准块就是 T1 的朝向），
   我先写的却是 180 / 270 / 0 / 90 —— 那是按**另一种基准**算出来的数，改基准时忘了重算。
   生成出来的 Manim 代码完全正确（`rotate(3.141593)` 等换算无误），**代码看不出错，只有抽帧能抓**：
   四块全部错位。这类错要先用脚本把派生量（角、目标点）重算一遍再写 JSON。
2. **rotate / move_to 的参照点是 manim 的 `get_center()`，也就是包围盒中心**，不是顶点平均。
   目标点必须按 bbox 中心算，否则四块之间会拼出缝（顶点平均和 bbox 中心在本例差 0.2~0.36）。
3. **配色**：弦图里相邻两块的同名腿是**共线**的，同色 + 同填充会糊成"一个被划了 X 的方块"，
   看不出是四个三角形 —— 而"四个全等"正是这一镜要讲的事。改成**两色相间**（蓝 / 青，
   fill 0.3）立刻清楚，这也正是"青朱出入图"名字的由来。

**另外**：成片 30.87s 比声明的 29.4s 长 1.47s —— 分镜边界由引擎自己加了 0.35s 的淡出
（3 个边界约 1.05s）。所以**按声明秒数抽帧会偏早**：镜 4 结尾那帧"字幕是空的"就是这么来的，
用 `-sseof` 抽最后一帧才是对的。

**待办**：prompt 里的示例 2（`_EXAMPLE_GEOMETRY`，仍是那个 3.6×2.4 却写"底 3、高 2"的旧版）
**还没换** —— 等用户看过成片再换成从 `examples/fewshot_pythagoras.json` 读。
→ **2026-09-17 已换**（用户："这版还行，就这样吧，可以换进提示词 2 了"）：`_EXAMPLE_GEOMETRY`
改为 `_example_from_file("fewshot_pythagoras.json")`，标签改成"几何题／勾股定理证明"。

### 8.59 「示例越攒越多，会不会把输入撑爆」—— 先量了，再定方案

**来源**（用户，2026-09-17）：换完示例 2 之后问的 —— "我担心后面还有例子会不会把输入撑爆，
怎么解决好呢"。

**实测**（示例 2 换成弦图之后，`build_system_prompt()` 全文 **49958 字符**）：

| 部分 | 字符 |
|---|---|
| 示例 1 函数（文件读） | 17393 |
| 示例 2 几何／弦图（文件读） | 11275 |
| 示例 3 网格 | 1809 |
| 示例 4 纯概念 | 197 |
| `dsl.describe()` 规格 | 11603 |

**示例合计 30674 = 61%** —— 增长点全在示例上。一个"完整示例"约 1.1~1.8 万字符（≈4~6k token），
再攒 4 个就是 10 万字符（≈3~4 万 token），**还没算对话历史和输出**。

**方案 A（推荐）：按题型只注入 1 个完整示例。** 示例库继续放 `examples/`（现在已经是
文件驱动，等于已经做了一半），`build_system_prompt` 按题型注入"该题型那个 + 两个小示例"，
其余不注入 —— 提示词固定在 ~3 万字符，**与库大小无关**。题型判定：
多轮时用历史里上一份 JSON 的 `problem_type`（`_history_with_storyboards` 里就有，最准）；
新题用关键词启发式（几何/证明/三角形/圆 → geometry；矩阵/卷积 → grid；…）。
代价是 system prompt 有 N 个变体：每个变体自身仍然稳定，所以**各自的缓存照样命中**
（`llm_log` 的"缓存命中tok"能直接观测），只有跨题型切换会丢一次缓存。

**方案 C（无论如何都该加）：给提示词加预算闸。** 构建时用现成的 `_est_tokens` 估算，
超阈值就告警 —— 现在连"当前提示词有多大"都没有可见性（这次是手工量的）。

**不做**：把示例挪进 user message 或做成检索拼接 —— 会打断 system 前缀缓存，
而日志里"缓存命中tok"是实打实的收益。

**状态**：A 待用户点头；C 很便宜、随时可加。

> **2026-09-17 晚：上面这套方案作废一半** —— 用户说明实际用的是
> **deepseek4.1flash，1M 上下文**。于是 18682 token 只占窗口 **1.9%**，
> **方案 A（按题型选示例）不必做**：示例全给就好，那本来就是教学信号最强的形式。
> 真正该做的换成了下面两件：
>
> 1. **历史预算才是瓶颈**：`_HISTORY_TOKEN_BUDGET = 10000`（token）当初是按"省输入
>    token"定的（注释里记着实测：调到 20000 耗时不变、只是贵 40%），而一份分镜 JSON
>    中位 **1463 token** —— 10000 只留得住约 6 轮带画面的对话。1M 窗口下这个数明显偏小，
>    多轮改一版会开始"记不住最早那几轮"。**不用改代码**：
>    `set MSB_HISTORY_TOKENS=30000`（这个口子早就在 `history_budget()` 里）。
> 2. **加预算闸**（不为防爆，是为了"看得见"）：提示词总量是**质量**风险项 ——
>    示例堆太多会把规则淹掉、分散注意力。建议盯住"示例 ≤ 6~10 个、提示词 ≤ ~40k token"，
>    构建时用现成的 `_est_tokens` 打一行日志即可。
>
> 示例的收纳纪律（无论窗口多大都适用）：**每个新示例必须覆盖还没覆盖过的题型或机制**，
> 重复的删掉；宁少勿滥。

### 8.60 落实上面两条：历史预算 10000 → 30000，并给提示词加体积闸

**来源**（用户，2026-09-17）：确认实跑模型是 **deepseek-v4.1-flash（1M 窗口）**，
"1 和 2 都做"。

**① 历史预算 `_HISTORY_TOKEN_BUDGET` 10000 → 30000**

- 10000 当年是按"省输入 token"定的（那段注释里的实测说明它只是**贵**、不是**慢**：
  14.5k → 20k token 耗时 3.5s vs 3.1s，噪声内）。1M 窗口下 30000 只占 3%，
  却能把"带画面的对话"从约 6 轮拉到约 **18 轮**。
- 多轮改一版靠的就是"模型手里有没有上一版的原料"（§8.38），这个钱值得花。
- 环境变量口子照旧：`MSB_HISTORY_TOKENS` —— 想省 token 的人自己压回去，
  **不要把默认值再调小**。

**② 提示词体积闸（`_PROMPT_BUDGET_TOKENS = 40000` + `prompt_stats()`）**

- `build_system_prompt()` 结尾估算一次（5 万字符 ≈ 几毫秒），**超预算只喊一次** ——
  每次调用都喊等于刷屏，等于没有信号。
- `prompt_stats()` 给画像（总量 / 四个示例各占多少 / 是否超预算），接进两处：
  **启动横幅**（`api/main.py::_banner`）和 **`--dump-prompt` 文档开头**。现在启动就能看到：
  `提示词  : 18682 token（49958 字符，其中示例 8635，预算 40000）`。
- ⚠️ 这里防的**不是撑爆**（1M 窗口），而是**质量稀释**：示例越堆越多会把规则淹掉。
  建议：完整示例 6~10 个、总量 ≤ 40k token。

**验证**：新增 `tests/test_prompt_budget.py`（3 条：画像数值、超预算只喊一次、
历史预算下限）→ 全量 **165 passed**；横幅与 `--dump-prompt` 都实测打印正常。

**顺带看到**：实际调用是 `[relay] deepseek/deepseek-v4.1-flash`，`max_tokens=32768`。

### 8.61 表格类：模型把表撑肥了（`cell_w: 3.6`）+ 示例 3 换成二分查找

**来源**（用户，2026-09-17）："继续下一个示例，表格类很有问题" → 接着追问
"你不觉得这表格太大，每个单元格也太大吗"。**第一句我判错了**：只核了"渲染对不对"
（表头、数据、公式都对），没核"**得体不得体**" —— 这件事本来该我主动看。

**诊断**（先复现再看代码）：留档里 16:13 那单「牛顿第二定律」产出的表格元素是

```json
{ "kind": "table", "font_size": 26, "cell_w": 3.6, "cell_h": 1.15, "pad_x": 0.4 }
```

`pad_x: 0.4` 它写对了（紧凑），错在 **`cell_w`/`cell_h`** —— 它们是「格子的**最终**尺寸」
（写死，不是上限）。算一下与抽帧完全吻合：3 列 × 3.6 = **10.8**（画面 14.22 的 **76%**）、
4 行 × 1.15 = **4.6**（画面 8.0 的 **58%**）。

**根因在规格文案**：`table.desc` 原文写着「想统一/紧凑用 cell_w/cell_h/pad_x/pad_y」——
**紧凑该靠 pad，`cell_w` 只会把表定死、定大**。模型照这句话理解成"3 列要铺整齐，每格 3.6"。

**改动**：

1. **规格**：把分工说死（想统一/紧凑用 `pad_x/pad_y`；`cell_w/cell_h` 只用于"跨镜保持格子
   尺寸不变"），并补上模型真正缺的**尺寸感**："画面 14.2 × 8，一张表建议不超过 8 宽、4 高"。
2. **默认留白**：`PAD_X_DEFAULT/PAD_Y_DEFAULT` 1.3/0.8（manim 原生值，太松 —— 3 列光留白
   就吃掉 3.9）→ **0.7/0.45**。⚠️ 三处必须同步：`dsl._table()` 的兜底字面量、`test_metrics.py`
   的断言、**`test_metrics_calibration.py` 建 manim 表时要用同一组 h_buff/v_buff**
   （否则变成"拿松留白对紧留白"，`估 ≥ 实测` 会假失败）。同一张表：10.8 → **7.84** 宽。
3. **示例 3 换成 `examples/fewshot_binsearch.json`**（用户选二分查找；4 镜、29.4s）：
   专门示范表格类 —— **格子让内容撑 + `pad_x: 0.4`**，2×8 的表约 6.0 × 1.6（占画面 42%×20%）；
   并示范 `cell_box` 的**独有能力**：候选范围用 `move_cells` 从 8 格缩到 4 格，
   位置和宽度都由格子算（"别用 rect 自己估坐标框格子"的正例）。

**没做**：不给 `cell_w` 加硬校验 —— 试算后不可靠：同样 `cell_w: 1.0`，放单个数字的卷积格子
是正常的、放"$F$/N"就是浪费，**光凭数字分不出"合理的空白"**；而静默把它的数改小更不行
（那是本项目最忌讳的"改了不生效"）。

**验证**：全量 **165 passed**（含新的 `fewshot_binsearch.json` 进幂等检查）；
渲染 + 抽帧确认：表格紧凑、候选框缩半、中点框命中第 6 格、`log₂8 = 3` 在右上。

**已换**（用户 2026-09-17："表格确实小了，但是这个太简单了，换回卷积吧"）：

- 示例 3 = `_example_from_file("fewshot_conv.json")` —— 4 镜、31.8s：
  3×3 输入 ⊛ 核 [[1,2],[3,4]] → 2×2 输出 **37 / 47 / 67 / 77**（四个算式都手算核过），
  窗口全程由 `cell_box` + `move_cells` 定位（零手摆坐标），中间量算式用完退场。
- 标签文案同步改（"表格／卷积…**表格只写 pad_x 收紧，不写 cell_w**"）。
  ⚠️ 分镜设计规范里那句「示例 3 就是（carry 的正例）」仍然成立 —— 新示例照样用 carry。
- `examples/fewshot_binsearch.json`（二分查找那版，被否"太小太简单"）**留着没删**。
- 换后提示词 **20567 token / 56424 字符**（示例占 10381 = 50%），仍远低于 40000 预算。

**遗留**：`examples/free_pythagorean.json` 那份"图与数对不上"的老毛病还没修
（画的是 1.8×2.4，三个正方形却标 9/16/25，实际应是 3.24/5.76/9）。

### 8.62 三条反馈：字幕里的 `**`、正方形格子、把格子里的数"提出来"相乘

**来源**（用户，2026-09-17，三条一起）：① 字幕里出现了 `**`（"这又不是 markdown"）；
② 输出表希望格子是正方形；③ 静态算式太简单，希望把表格里的元素提取出来相乘。

**① 字幕里的 markdown —— 我的错**：`cap` 写成了"和核\*\*对位相乘\*\*再相加"。
⚠️ **字幕是直接画在屏幕上的 `rich()` 文本，不是 markdown** —— `**` 会原样显示，
而 `brief`（进聊天框、前端渲染 markdown）里用 `**` 没问题。**两个字段的渲染环境不同，
写的时候必须分开想。** 全量扫了 `examples/*.json`，只有这一处泄漏（另外命中的
`x**2` 是幂运算、`#58C4DD` 是色值，都不是 markdown）。顺手把 `brief` 里那处也去掉了
（渲染没问题，但保持一致的写法更不容易再犯错）。

**② 正方形格子：新增 `table.square`**（不是改默认值 —— 文字表格强行正方只会白占高度）。
边长 = max(内容宽+pad_x, 内容高+pad_y)，估算（`metrics.est_table_size(square=…)`）与渲染
（`dsl._table(square=True)`）**同一套逻辑**。卷积示例三张表都开了：2×2 输出表
**2.21 × 1.7 → 2.21 × 2.21**。⚠️ 写测试时踩了个概念坑：「格子是正方形」≠「整张表是正方形」——
非方阵时宽:高 = 列数:行数（第一版断言就写错了）。

**③ 把格子里的数"提出来"相乘：新增 `place.cell` 锚点**（这是本轮唯一的新能力）。
`{"cell": {"of": "tb", "row": 1, "col": 1}}` 让元素贴在表格某一格的中心（位置由
`_cells_center` 算，和 `cell_box` 一样不会错位）。卷积镜 2 因此重写成了：
四个数先**贴在各自格子上**（就在红框那一层，观众知道它们来自这四格）→ `parallel` 一起
move_to 到下方一字排开 → 逐个 `replace` 成 `1×1` / `+2×2` / `+4×3` / `+5×4` → 接 `= 37`。
**"哪一格 → 哪一项"从此看得见**，这是静态算式给不出的东西。镜 2 10.2 → **14.6s**，
全片 **36.2s**；抽帧确认过（20.0s 那帧：四个数已排开、逐项乘开）。

**踩坑记录**：这轮用脚本给示例批量加 `square` 时跑了一次 `json.dumps`，把示例文件的缩进
重排了 —— 后续按老文本 `replace_in_file` 全部匹配失败；又在 `_note` 里写了**未转义的
ASCII 双引号**，直接把 JSON 弄断（`Expecting ',' delimiter`）。教训：**改示例文件优先用
脚本改 + `json.loads` 自检**，或者改完立刻 `json.loads` 验一遍，别攒到最后。

**验证**：提示词 **21283 token / 59158 字符**（示例占 11043 = 52%，仍远低于 40000 预算）；
四镜时长 6.4 / 14.6 / 5.6 / 9.6 = **36.2s** 全部吻合；`tests/` **168 passed**（新增
`test_square_table_makes_cells_square`）。

### 8.63 三表对齐 + 「成对抽出」：把卷积讲成一行式子

**来源**（用户，2026-09-17）："三个表格并没有对齐" + "有点意思了，但还不够，
应该是**输入的 1 和卷积核的 1 同时抽取出来，然后中间补个 ×，后面加个 +**，
输入的 2 和卷积核的 2 同时抽出来也一样，以此类推"。

**① 三表对齐**：原来三张表的 `at_point` 只统一了 x，y 各写各的（0.5 / 1.2 / 1.2）。
现在**共用中心 y = 0.6、标签共用 y = -1.05**，于是输入（3 行，2.75 高）、核（2 行，1.85）、
输出（2×2，2.15）在画面上是**一条水平线**，`⊛` 与 `=` 都落在中线上。
⚠️ 为什么用"中心对齐"而不是顶对齐/底对齐：3 行表与 2 行表**没法既顶对齐又底对齐**，
居中让两边的溢出量相等，是唯一不偏的选择。

**② 成对抽出**（用户要的正是这个）：镜 2 原来是"四个数一起飞出去、排成一行、再接一项一项"，
现在改成**一对一对地抽** ——

```
第 i 对： 输入窗口里的第 i 个数  ┐
          （parallel 同时 move_to） ├→  到下方 [输入]  然后补 ×  然后补 +（最后一对不补）
          核里同位置的数         ┘
```

四对排成 `1 × 1 + 2 × 2 + 4 × 3 + 5 × 4 = 37`。
**为什么必须成对**：单抽一个数只能看出"它从哪里来"，成对抽才看得出"**谁和谁相乘**"——
那正是卷积的全部内容。⚠️ 这就是新增 `place.cell` 的用武之地：两边的数都**贴在各自格子上**
（`place.cell`），起点就是那一格。镜 2 元素 23 / 动作 23（含 4 组 parallel），
14.6 → **18.2s**，全片 **39.8s**。

**踩坑**：槽位间距第一版按 1.5 排（每对占 ~1.9），`+` 会和下一对的数**撞上**；
按"每对 1.9"重排后（SLOT 四组）才干净。⚠️ 改这类示例**用脚本改 + `json.loads` 自检**
（上一轮手改把 JSON 弄断过一次，见 §8.62）。

**验证**：抽帧四张确认（镜 2 结尾帧四级排开不打架、片尾帧三表一条线、输出表 ✓）；
时长四镜 6.4 / 18.2 / 5.6 / 9.6 = **39.8s** 全部吻合；`tests/` **168 passed**。

### 8.58 弦图这一版的三个问题（其中一个暴露了 DSL 的真 bug）

**来源**（用户，2026-09-17）："有字幕停留时间不到 1s，这个不行，起码除去淡入淡出要至少 1s；
三角形旋转的时候进行了形变，实际上只要平移旋转（一起）就好了；最后图里面 a 和 b 都没标注，
b−a 指代不清。"

**① `rotate` 动作会形变 —— 这是 DSL 的 bug，已修**：原来生成 `x.animate.rotate(θ)`，
而 `.animate` 是**逐点线性插值**，转 180° 时三个顶点各自走弦、中途会塌成一条线。
改用 manim 的 `Rotate`（`about_point=None` 时绕自身中心转，读源码确认过）—— 刚体旋转。

**② `parallel` 里「旋转 + 平移」现在是**一次刚体运动**（新能力）**：同一个元素上的
`rotate` 与 `move_to`/`shift` 会合并成 `Rotate(m, θ, about_point=<支点>)`：
    支点 P = C0 + d/2 + (cot(θ/2)/2)·J(d)，  d = C1 − C0，J 是逆时针 90°
（θ=180° 时 cot 项为 0、P 就是中点；θ=0 退化成纯平移）。这样「转 θ」和「把中心挪到 C1」
一次完成，既不形变也不打架 —— 用户要的"平移旋转一起"。
⚠️ 之所以必须合成一条：manim 里同一 mobject 的两条动画各自取初态会互相打架，
而 `.animate` 链式（`rotate().move_to()`）同样是逐点插值（形变）。
⚠️ 其它"同一元素被两个子动作碰"的情形（如 `scale` + `rotate`）**仍然整段降级为顺序播放** ——
那种"先放大再旋转"本来就有先后，测试 `test_same_element_twice_falls_back_to_sequential` 照旧。

**③ 字幕停留 ≥ 1s**：示例里把每条字幕的"完整可见时间"都补到 ≥1s（写完 / 换完补 `wait 1.0`，
一镜末尾那条也留够 —— 否则会直接跟着分镜边界淡掉）。四镜时长 6.6 / 8.6 / 7.6 / 8.6 = **31.4s**。
**并写进了提示词**（《讲解文字》一节）：「每条字幕（除去淡入淡出）至少要完整停留 1 秒」。

**④ 弦图上补 a / b 标注**：之前 a / b 只出现在镜 1，拼合之后就没了，于是镜 3/4 里 `b−a` 无所指。
现在在 t1 的两条直角边上标 a / b（并 carry 到镜 3/4），字幕改成
「中间的小正方形，**边长就是两条直角边之差 b−a**」。

**验证**：四镜时长各自吻合（`sum == duration`）；生成代码里确认出现
`Rotate(_e_t2, 1.570796, about_point=_piv_1, run_time=1.6)`（t1 是 0° 故退化为纯平移）；
旋转途中抽帧四个三角形形状完好、无塌陷；`tests/` **162 passed**。

### 8.56 「生成半天 → 前端报 network error」：Next dev 代理写死 30 秒**空闲**超时

**用户反馈**（2026-09-17）：*生成分镜大纲的时候，由于有些需要生成半天，结果前端会误判超时（network error）。*

#### 根因：不是"总时长超时"，是**空闲超时** —— 下手的是我们自己的 dev 代理

`web/next.config.ts` 的 rewrites 把 `/msb/*` 转发给 FastAPI，而 **Next 给这个转发写死了一个 30 秒的
上游超时**：

```js
// web/node_modules/next/dist/server/lib/router-utils/proxy-request.js:30-37
const proxy = new ProxyServer({
  target,
  // we limit proxy requests to 30s by default, in development
  proxyTimeout: proxyTimeout === null ? undefined : proxyTimeout || 30000,
});
```

`proxyTimeout` 在 httpxy 里最终落到 **socket 空闲超时**（`proxyReq.setTimeout(...)`）——
含义是"**上游 30 秒没吐字节就掐掉这条连接**"，不是"整个请求最多 30 秒"。于是：

| | |
|---|---|
| 谁在沉默 | 生成大纲时，开深度思考的模型光思考就 50s 起步（§8.11/§8.12 实测首字 93s），这段时间 SSE 流上**一个字节都没有** |
| 谁下的手 | dev 代理在 30s 那一刻销毁上游请求，前端这条 fetch 随之断掉 |
| 前端看到什么 | `TypeError: Failed to fetch`（Firefox 是 `NetworkError when attempting to fetch resource.`）—— 原样显示就是一句英文 network error，**读起来就是"超时/崩了"** |
| 真实情况 | uvicorn 里那条生成**完全没受影响**，还在跑，结果照样落盘 |

⚠️ 这条极易被误判成"模型太慢/后端超时"，因为它**只在静默超过 30s 时出现**：思考流
（`MSB_LLM_SHOW_THINKING=1`）只要一直在出字就没事，哪一段空超过 30s 就复现 —— 典型的"有时好有时坏"。
同理，调 `llm.local.json` 的 `timeout`、调 uvicorn 的任何参数都没用：响应头是立刻发出的，
30s 掐的是**响应体**。

#### 三处修法（缺一不可）

1. **后端：所有 SSE 流加心跳保活**（治本，且与代理无关）
   - `api/pipeline.py::stream()` 起一个心跳线程，每 `config.SSE_HEARTBEAT_SEC`（默认 15s）往队列塞一条
     `events.PING`；`events.encode_stream()` 把它编成 SSE **注释行** `": ping\n\n"`。
   - **为什么用注释行而不是新事件**：前端 `sse.ts::parseChunk` 本来就跳过 `:` 开头的行
     （`dataLines` 为空 → 返回 null），心跳因此**对前端完全透明** —— 不改协议、不加事件类型、
     旧前端不受影响。保活是链路的事，不该污染事件契约。
   - 15s 的选取：常见网关 idle 阈值是 60s，留 4 倍余量；它同时**覆盖生产网关反代**——
     那里没有 `proxyTimeout` 可配，只能靠上游别沉默。
2. **dev 代理：30s → 30 分钟**（`next.config.ts` 的 `experimental.proxyTimeout`）
   - 30 分钟 = 后端单条渲染超时 `RENDER_TIMEOUT=900s` 的两倍：让"空闲"由心跳解决，
     而不是由代理层拿魔数替我们决定。
   - ⚠️ **不能写 `null`**：源码里只有 `=== null` 才是"不超时"，但配置 schema 是
     `z.number().gte(0).optional()`，写 null 会被校验打回（并打一条 Invalid next.config 警告）。
   - ⚠️ 只在 `next dev` 生效；生产走网关反代，超时要单独配（§4「部署注意」那行已改）。
3. **前端：把"连接层失败"和"业务失败"分开说**（`web/src/lib/api.ts`）
   - `isConnectionFailure()` 认出 fetch 的 `TypeError`／各家文案，换成一句**带出路**的话：
     「任务可能仍在后台继续 —— 稍等一会儿刷新页面，看看结果是否已经出来」。
   - 顺带补了一个**更隐蔽**的问题：`readSSE` 在服务端关流时是**静默返回**的，于是
     "流被掐断、只收到一半"和"生成完了"在 UI 上**长得一模一样**（后者还让人以为成功）。
     现在 `post()` 会记有没有收到终点事件 `done`，没收到就报"连接提前结束了（没收到完成信号）"。
   - ⚠️ 判定连接失败**不能**用宽泛的 `connection` 关键字：那会把后端真报上来的业务错误
     也吞成"任务可能还在跑"，比不识别更坏。

#### 验证

- 临时脚本（已删）：`encode_stream` 把 PING 编成 `": ping\n\n"` 且前后事件不受影响；
  模拟"3.4s 不发任何业务事件"时收到 **3 次心跳**、收尾 `done` 正常；照抄 `parseChunk` 的规则
  确认心跳行返回 `None`。
- `web`：`tsc --noEmit` 通过（`experimental.proxyTimeout` 是合法配置项）。
- **真链路待实测**：跑一次长生成（思考 > 30s），确认不再出现 network error。

### 8.64 两条反馈：算式间距（估算器不能再当宽度用）+ 『输出没 carry』其实是 DSL 的真 bug

**来源**（用户，2026-09-17，对 §8.63 那版卷积示例）：先发了算式行的截图说
"**还是间距太大**"，接着问"**另外输出是不是没 carry**"。两条都查实了，第二条是**引擎的 bug**。

#### ① 间距：两次都没修对，根子都是"拿估算值当实际值用"

- **第一次**（§8.63 之后）：算式槽位原来是**手算**的（"每对 1.75、符号夹中间"）——
  可 `×` / `+` / `=` 比数字宽、`37` 比 `1` 宽，手算必然对不齐。
- **第二次**（这一轮）：改走 `metrics.est_tex_w` / `est_text_w` 逐个量宽再串起来。
  ⚠️ **但那是「防溢出的上界」**（`metrics.py` 的系数是"全字号区间的实测上界"，
  留了几十 % 余量）：`est_tex_w("\\times", 30)` 给 **0.225**，而实测只有 **0.150**；
  数字更离谱（估算 ~0.22、实测 0.103~0.138）。**拿上界当宽度排位置，空档自然比字符还宽。**
- **真修法**：写 `output/_measure_width.py` 用 `MathTex` / `MarkupText` 在
  Manim CE 0.21 / fs=30 下**真量**（脚本用完即删，数据固化进注释）：

  | token | 实测宽 | token | 实测宽 |
  |---|---|---|---|
  | `1` | 0.103 | `5` | 0.124 |
  | `2` | 0.124 | `37` | 0.294 |
  | `3` | 0.129 | `\times` | 0.150 |
  | `4` | 0.138 | `+` | 0.207 |
  | `= 37` | 0.618 | | |

  排法改成"**中心距 = 半宽 + 半宽 + 固定缝隙**"，缝隙一律 0.25（真实缝隙，不是中心距）。
  总宽 **8.77 → 6.57**，画面上 `×`/`+`/`=` 与数字的间隔才真正均匀。
- ⚠️ **教训（比这行算式大）**：`metrics` 的估算器是**校验层**用的（判"会不会出画"，
  宁可估大），**不能**拿来排真实版面。要真宽度就实测。

#### ①-b 第三次："间距还是大" —— 连间距也不能自己拍，要照 TeX 的规则

**来源**（用户，2026-09-17）：又一张截图 ——"**间距还是大**，把 × 和 + 改成白色吧，**=37 还是小**"。

- **间距**：上一版虽然用了实测宽度，但**间距是自己拍的 0.25**。而 TeX 在二元运算符两侧
  只留 **`×` 0.1415 / `+` 0.1132**（`=` 是 relation，0.1305）—— 拍的比它大 **77%**。
  量法就是"整串宽 − 各零件宽之和 = 2 × 一侧间距"（`output/_measure_gap.py`，用完即删）。
  改用 TeX 天然间距后总宽 **6.57 → 4.89**，整行变成 `1×1+2×2+4×3+5×4=37`，
  即**真正的数学排版**。⚠️ 三次修的都是同一件事：**别凭一个数去猜，去量**——
  手算槽位错在没量宽度、估算器错在量的不是实际值、拍 0.25 错在量的不是 TeX 的规则。
- **颜色**：`×` / `+` 由 GREY 改 **WHITE** —— 它们和数字一样是算式的一部分，
  用 GREY 是"旁注"的待遇，整行看着"数字清楚、符号发虚"。
- **`= 37` 字号**：30 → **36**（结论不该和过程同号）。
  ⚠️ 混排字号**必须补基线差**：`place.at_point` 走 `move_to`（对齐**中心**），
  36 号比 30 号高，中心一对齐底边就沉下去 —— 数学排版里混排字号要**基线对齐**。
  实测底边差 0.0267，把 `= 37` 上移它才齐平（`EQ_BASELINE_FIX`）。

#### ② "输出没 carry" —— 其实是 `replace` 换绑没跨过分镜边界（DSL bug，已修）

**现象**：镜 2 末尾输出表填好 37（26.4s 帧 ✓），**进镜 3 整块消失**（28 / 29 / 30s 帧），
镜 3 末尾又出现（35.4s）。看起来就是"carry 没生效"。

**真因**（生成源码实证，不是推测）：

```python
self.play(FadeOut(_e_tb_out, ...), FadeIn(_e_tb_out1, ...))   # 屏上真正的表是 _e_tb_out1
_keep = [... _e_tb_out]                                       # 清屏保留的却是不存在的旧名
```

- `replace` 之后 id 会**换绑到新变量**（`_Builder.alias`，§8.33 那个老坑的解法）；
- 但 `_alias` 是**单个分镜的 `_Builder`** 的属性（`build_scene` 每镜 new 一个），**跨镜就丢了**；
- 而分镜边界的清屏 / 末尾淡出片段是**渲染器**拼的，按 `dsl.var_name(id)` 直接拼**原始变量名**
  （`renderer._clear_except` / `_tail_fade`）—— 它不知道这个 id 已经换人了。
- 于是屏上的**新对象**被当成"上一镜的残留"淡掉；顺带 `FadeOut(_e_tb_out)` 引用的还是
  早已离场的旧对象（manim 会把它**重新加回场景**，与 §8.33 同源）。

**修法**（`dsl.py` 的 `replace` 分支，一行）：替换完成后把**原名也重绑到新对象**：

```python
self.alias[a] = self.alias.get(b, b)
self.lines.append(f"        {_V}{a} = {vb}")     # ⚠️ 新增：生成 `_e_tb_out = _e_tb_out1`
```

两边从此指同一个东西。为什么选"补一行重绑"而不是"让渲染器去查 alias"：alias 是
**逐镜编译器**的产物、渲染器隔了一层拿不到（除非把整条 alias 链跨镜传下去）；
而重绑让"名字"这件事在**代码层面**自洽 —— 后续任何按名字取对象的代码（清屏、末尾淡出、
拆分渲染的 rebuild/add）都自动正确。repair 只多一行赋值，无副作用。

**哨兵**：`tests/test_carry.py::test_carried_element_replaced_last_scene_survives_boundary`
—— 镜 1 `replace tb into tb2`、镜 2 carry `tb`，断言生成代码里有 `_e_tb = _e_tb2`
且清屏 `_keep = [_e_tb]` 出现在镜 2 前导位置。
⚠️ 写这个用例时踩了一下：`_keep` 排在 `scene N | dsl` 标记**之前**（清屏是"进这一镜前
做的准备"），用按标记切片的 `scene_of()` 取不到它 —— 要整份找。

**验证**：渲染后抽帧 28 / 30 / 33 / 49.5s —— 镜 3 全程 37 都在，片尾四个数齐
（37 / 47 / 67 / 77）；`tests/` **169 passed**（+1 哨兵）；lint 干净。
算式行另做了 `crop` + `scale` 放大核对（`zoom_eq.png`）：`×`/`+` 已白、间距贴合 TeX、
`= 37` 与数字**基线齐平**。

**遗留**：`_build_conv.py` 里那套"实测宽度表"目前是**脚本内硬编码**（示例是临时脚本生成的）。
要不要把它变成引擎能力（比如给 DSL 加一个"按实测宽度排一行 token"的 helper）——
**待定**，模型现在仍是手写 `at_point`。

### 8.65 输出表"中途淡入淡出"（改用独立数字元素）+ 示例 JSON 为什么有 1000 行

**来源**（用户，2026-09-17）：先确认上一轮修好了（"我手动确认了，确实好了"），
接着问"**现在输出的表格还是会中途淡入淡出**"；修完后又问"**为什么 json 有 1000 行**"。

#### ① 输出表闪：`replace` 换的是**整张表**，而真正变的只有一个数

四个数分四镜填，每填一个都 `replace` 整张输出表 —— crossfade 会把**边框、格子、
已有数字**一起淡一遍，观众看到整张表闪四次。而这里实际变化的只有"多了一个数"。

**改法**：表格 `tb_out` 声明一次（空表，`cell_w/cell_h` 定死 1.2×1.2 尺寸不随内容变）、
**全程 carry、一次都不重建**；四个数字做成**独立 text 元素**，各自用 `place.cell`
锚在自己那一格上（引擎用 `_cells_center` 算位置，不需要自己估坐标），
算完一个 `fade_in` 一个 —— **表格一动不动，只有数字冒出来**。
⚠️ 不用 `transform` 也是对的：Transform 会形变，表结构动起来更糟。
⚠️ 这条其实暴露了 `replace` 的一个使用边界：**"同一位置换内容"适合字幕这种
"整体就是内容"的元素；表格/坐标系这类"容器 + 局部内容"的，要拆成独立元素局部更新。**

#### ② 为什么 JSON 有 1000 行：不是内容多，是 `indent=2` 把短数组炸成了一列

`_build_conv.py` 用 `json.dumps(doc, ensure_ascii=False, indent=2)` 写文件 ——
它把 `[-1, 5, 1]`、`["1","2","3"]` 这种短数组也拆成一列，**一个 3×3 表格元素占 32 行**
（内容其实只有 9 个数 + 位置）。实测：

| | 行数 | 字符 |
|---|---|---|
| 磁盘（`indent=2`） | **1149** | **24749** |
| 紧凑排版（`_dump_example_json`） | 481 | 16473 |
| 紧凑 + 贪心接行（现在） | **300** | **14706** |

⚠️ **不只是难看，已经踩到 §8.55 的坑**：`_ECHO_LIMIT` 是按字符数截的，
24749 > 20000 —— 上一轮刚为它把 12000 提到 20000，结果它自己又因为排太松超回去了，
**回灌仍会被腰斩**。压完 14706 才安全。

**三处改动**：

1. `_dump_example_json` 升级成**贪心接行**（一个对象里能接在同一行的短项就接上）——
   之前的"每键一行"比人写的还散（手写示例 299 行，同数据"每键一行"排出来 529 行）。
   贪心版正好复现手写密度（`derivative` 299→314、`pythagoras` 213→219）。
2. **新增 `llm.save_example(path, data)`**：写文件走**同一套**排版。理由：示例文件是
   唯一事实来源，磁盘一套、提示词一套必然漂移（这次就是），而且散的那份会顶穿
   `_ECHO_LIMIT`。`_build_conv.py` 已改用它。
3. `_scene_user_prompt` 里回灌"这个分镜当前的样子"也从 `indent=2` 改成同一套
   （多轮改一镜时把旧分镜漫散地回灌，同样白烧 token）。

**顺手澄清一个口径**（差点又搞错）：`_ECHO_LIMIT` 校的是**单行压缩**后的长度
（回灌调 `json.dumps(raw)`，无 indent），不是磁盘文件那种带缩进的排版 ——
同一份数据：压缩 12303 / 紧凑排版 14706，**差两成**。拿文件大小去校这个值会自己吓自己。
注释已写明。

**哨兵**（`tests/test_prompt_budget.py`，+4 条）：
`_dump_example_json` 不能退化成 `indent=2`（用"比 indent=2 小一半"钉住）；
四个示例文件磁盘行数不超过 dump 的 1.4 倍（手写文件有 ~5% 出入是正常的，
但 `indent=2` 是翻倍）；示例压缩后不超 `_ECHO_LIMIT`；`save_example` 往返可读。

**验证**：`tests/` **173 passed**；提示词 58029 字符 / 1119 行；抽帧确认填数与切镜时
表格不再整块闪（数字逐个出现、表格纹丝不动）——**用户已手动确认**。

### 8.66 提示词收口：换掉示例 4 + 补两条规则（"容器别整块换"、"中间量用完退场"）

**来源**（用户，2026-09-17）："行，就这样吧，**加进提示词**，另外**第四个示例换一个**，
难道**直角三角形不能画出来给用户看吗**"。

#### ① 示例 4 换题 —— 用户这一问正中要害

原来 `intent=none` 的示例题是**"什么是直角三角形"**。拿一个**明明该画**的图形题
去示范"不出动画"，等于教模型"这种也别画"，而且和《第一步》里
**"几何题只画图形本身"直接对冲**。更糟的是**规则 2 的举例里写的也正是它** ——
举例本身是提示词里最强的示范（§8.55 那句"规范里写了、示例里没写等于没写"的反面：
**示例里写错了，比没写还坏**）。

- 换成 **"「当且仅当」是什么意思"**（`problem_type: concept`）：讲的是双向推出关系，
  画任何图都只是装饰 —— **这才是"真的不该画"**。
- 规则 2 的举例同步改掉（"当且仅当"、"导数是干嘛的"、"这个符号怎么读"），
  并把判据写死：**「这道题不画也能讲明白吗」**，不是"题目短"、更不是"词听起来基础"；
  能画且画出来更清楚的（几何、函数、数轴、网格、计数）**一律 propose，先把图画出来**。
- `concept` 题型那条也补了同样的限定（"画什么图都只是装饰"才用 none）。
- 哨兵：`test_none_example_is_a_genuinely_undrawable_topic` ——
  none 示例段里不许出现 `axes`/`polygon`/直角三角形。

#### ② 两条规则进《分镜设计规范》

- **容器 + 局部内容，别用 `replace` 整块换**（来源就是本节 ①：输出表闪四次）——
  `replace` 换的是**整个元素**，表格/坐标系这类元素一次只变一小块，整块换会把
  边框和已有内容一起淡一遍。正确做法：**容器声明一次、全程不动，要变的那一小块
  做成独立元素单独上屏**（表格逐格填数：数字各是一个 text，`place.cell` 贴格子，
  算完一个 `fade_in` 一个）。判据写成一句可执行的问法：
  **"这一镜真变的是「整个元素」还是「元素里的一小块」"**。
- **每个分镜只 carry 这一镜还需要的东西**（来源：割补法那版把 `4×4=16` 一路 carry
  到底，用户说"右上角的 4×4=16 没消失"）—— 中间量用完就该退场（本镜不 carry，
  边界清屏自动淡掉）；反过来，这一镜结论要用到的东西**必须** carry。
  这是整个 HANDOFF 里反复出现的同一个毛病的另一面：**"上一镜有什么就全带过来"
  和"忘了带"都是没想清楚"这一镜结束时哪些东西还得留在屏上"**。

**提示词体积**：58029 → **58915 字符 / 1122 行**（两条规则 + 换示例约 +900 字符）。

**验证**：`tests/` **174 passed**（+1 哨兵）；六个探针确认新示例与两条规则都进了
`build_system_prompt()`，且"什么是直角三角形"字样已从提示词中清干净。

### 8.67 「停止」按钮停不掉模型：Canceller 只登记了子进程

**用户反馈**："现在停止按钮并没有真停止模型思考。"

这是 §8 这一整节的主题的又一次复发：**按钮变灰了、界面表现得像停了，但活儿还在干**
（模型继续吐 token、继续计费），而且不报任何错。

#### 根因：取消令牌手上没有"能打断 LLM 的东西"

`api/pipeline.py::Canceller` 原本只登记**渲染子进程**（`_procs`，靠 `proc.kill()` 中断）。
而 LLM 调用是完全另一条路径：

```python
for raw in resp:          # storyboard/llm.py::_iter_sse_stream
```

它阻塞在读一条 HTTP 流上，**`_procs` 里没有任何东西属于它**。所以点停止后：

1. 浏览器连接断开 → `pipeline.stream()` 的 `finally` 确实调了 `cancel.cancel()`；
2. 但既没有进程可 kill、也没有连接可关 → 工作线程继续阻塞在 read 上；
3. 模型继续输出、继续计费；`push()` 因事件循环已关而**静默丢弃**（`RuntimeError` 被吞）；
4. 最后还会把整份结果落盘。

**用户以为停了，账单和 CPU 都不同意。**

#### 修法：给取消令牌补上"可 close() 的资源"

- `Canceller` 新增 `watch(obj)` / `unwatch(obj)`（`close()` 语义）与原有的
  `register/unregister`（`kill()` 语义）并列，`cancel()` 两者都做。
- `storyboard/llm.py` 新增 `LLMCancelled` + 一个 **ContextVar 取消令牌**
  （`set_cancel_token` / `check_cancelled`）。`_http_post_json_stream` 与
  `_http_post_json`（非流式，重试轮走它）都把 `resp` 交给令牌 `watch`，
  读取循环每轮 `check_cancelled()`。
- `pipeline.stream()` 在**工作线程体内**设置令牌。

⚠️ **必须在线程体内部 set**：ContextVar 不会自动进入新线程。在外面设完再起线程，
线程里 `get()` 拿到的是 `None`，于是 `check_cancelled()` 永不触发 ——
**"停止"又静默失效，且不报错不打日志**，正是修 bug 时最容易踩空的一步。

#### ⚠️ `LLMCancelled` **故意不继承 `LLMError`**

这是整个改动里最关键的一个决定。`chat_stream` / `stream_storyboard` / 路由里
到处都是 `except LLMError`，而那些分支的语义是"这次失败，换个方式再来一遍" —— 尤其
`stream_storyboard` 有一条"流式不可用 → 退回非流式重试"的降级。取消若被当成
`LLMError`，用户按"停止"会换来**立刻再发一次完整的 LLM 请求**：等更久、花更多钱，
比不修还糟。不继承它，事件才会穿过所有那些 except，落到 `pipeline` 被单独接住
（那里打一行"客户端停止"、不发 error 事件、不重试）。

#### ⚠️ 取消时抛的不是 `OSError`，是 `AttributeError`（第一次改就栽在这）

第一版把异常处理写成 `except (OSError, http.client.HTTPException)`，测试**没通过**，
服务端日志里是：

```
AttributeError: 'NoneType' object has no attribute 'peek'
  File "http\client.py", line 764, in _peek_chunked
```

原因：`close()` 会把 `resp.fp` 置成 `None`，而读线程正阻塞在 `fp.peek()` 上 ——
两者并发操作同一个 `http.client` 对象。所以取消引发的异常类型**不一定是 OSError**。

正确顺序是：**先问令牌"是不是被取消了"，再决定怎么包装异常** ——
取消 → `check_cancelled()` 抛 `LLMCancelled`；没取消 → 才按真断流包成 `LLMError`。
（`AttributeError` 也一并按"断流"处理：那个竞态的**非取消**版本症状与断流一致，
报「连接中断」比报「NoneType has no attribute peek」有用得多。）

#### 顺带修掉：`/api/storyboard/replace-scene` 阻塞事件循环

这一条是查上面的问题时发现的**另一个 bug**：该路由的 `llm.regenerate_scene()`
原先**直接在事件循环里同步调用**，没走 `pipeline.stream`。后果有两个：

- 收不到取消令牌 → 用户点停止完全无效（同一个 bug 的另一处）；
- **整个服务在那十几秒（实测 98.4s）里是"哑"的** —— 连 `/api/health` 都排不上队。

已改为包进 `pipeline.stream`（`run()` 在工作线程执行、结果用单槽 dict 带出）。
实测修复后：`replace-scene` 进行中 `/api/health` **0.03s** 返回。

#### 验证

| # | 验证项 | 结果 |
|---|---|---|
| 1 | 断开连接后，留档新增一条 `error="用户已停止生成（cancelled）"` | ✅ |
| 2 | 服务端日志打出 `[api] 客户端停止，已中断模型调用`，且**无 traceback** | ✅ |
| 3 | 断开后留档计数**停止增长**（没有把那一轮跑完） | ✅ |
| 4 | 取消后服务仍健康（`/api/health` 0.07s） | ✅ |
| 5 | `/api/chat`、`/api/vi/chat` 不取消时正常跑完（回归） | ✅ |
| 6 | `replace-scene` 正常交付（`tool_result` ×1，回归） | ✅ |
| 7 | `tests/` | **174 passed** |

> 判据专门绕开了一个误区：不能用"留档里有没有 cancelled"当唯一判据 ——
> 取消可能恰好发生在一轮刚正常结束、还没进下一轮的时刻（那时没有 cancelled 记录，
> 但确实停了）。**"断开后不再产生新记录"才是"停止生效"的可靠判据。**

### 8.68 图片输入从"插件"改成 core（以及为什么"插件形式"在这里是错的）

**背景**：图片输入（拍照/截图题目）最初是作为**旁路插件**引入的
（`plugins/vision_input/`，独立端点 `/api/vi/chat`，靠换一条 uvicorn 启动命令启用）。
`plugins/` 目录现已**整体删除**，图片输入并入 core。

#### 为什么放弃插件形式

插件那套声明的价值是"**`api/` 与 `storyboard/` 一个字节都不改**，删掉 `plugins/`
主程序毫发无伤"。但接入的当天这条就破了：

1. **复刻必然漂移，而且已经漂了。** 插件的 `generate.py` 是 `stream_storyboard()`
   那套「生成 → 校验 → 回灌重试」的**完整复刻**（约 200 行）。搬过来时和 core 比出
   3 处分歧：硬编码 `temperature=0.2`（会让设置页改的温度静默失效）、漏了
   `meta.storyboard`（多轮"改这一版"会丢上一版画面）、漏了 `diff_scenes` 对账。
   插件自己的注释就写着"复刻的代价是**流程会漂移**（上游改了重试策略，这里不知道）"
   —— 这不是意外，是这个结构的必然后果，而且漂移是静默的。

2. **横切能力天然属于 core。** §8.67 那个"停止按钮停不掉模型"的修复，必须同时改
   `llm.py`（取消令牌）和 `pipeline.py`（Canceller 加 `watch`）—— 插件**无法**自己
   修好这件事。这类能力一出现，插件边界就被穿透了。

3. **"可选的后端模式"没带来选择价值。** 图片输入是核心能力（拍照搜题是最自然的
   使用方式），代价却是"要开图片就换一个启动脚本"。实测踩过一次：8000 上跑的是旧
   target，`/api/vi/health` 直接 404，而且**不报任何错**。

> 插件形式确实是**对的**，但只对那类"实验性 / 高危 / 随时可能删"的功能
> （如 planner_worker 已实测证伪、remote_access 默认关）。判据一句话：
> **"这个功能是否要常驻、是否要被别人 import"决定它该在哪。**

#### 改法（净减代码）

| 动作 | 内容 |
|---|---|
| **新增** | `storyboard/vision.py` —— 合并原插件的 `config.py` / `images.py` / `vision.py` / `messages.py`（图片解码·降采样·透明底合成·EXIF 转正 + 能力闸口 + 多模态 content 构造 + 图片摘要） |
| **删掉** | `plugins/vision_input/generate.py`（那 200 行复刻，**漂移源**）、`messages.py`、`router.py`、`install.py`、`serve.py`、`config.py`、`vision.py`、`images.py` |
| **改 `llm.py`** | `stream_storyboard()` 加 `images=None` 参数（放在**参数表末尾**，老调用方一个字不用改），新增 `_user_content_with_images()` 承载"有图/无图"的唯一分支 |
| **改 `api/routes/chat.py`** | `ChatRequest` 加可选 `images` 字段；`run()` 里加"配置 → 能力闸口 → 规范化"三段（**都在落盘之前**）；调用点传 `images`；落盘 meta 记图片摘要 |
| **删除** | `MathStoryboard/plugins/` 整个目录；`run_api.bat` 的 target 改回 `api.main:app` |
| **前端** | `streamChat` 不再按图切端点（统一打 `/api/chat`）；删 `src/app/api/vi/chat/route.ts`，Mock 的 `/api/chat` 支持 `images` |

`images` 为空时 `to_content()` 返回**纯字符串**，请求体与此前**逐字节相同** ——
这是让 LLM 留档指纹（`llm_log.cache_key`）不因支持图片而失效的前提。

#### 顺带修正一处文档与代码不符

`MSB_VI_ALLOW_URL` 的默认值：原代码是 `True`，而文档写的是默认关。URL 取图会让
服务端按用户给的地址发请求（SSRF 面），**已按文档改成默认 `False`**。

#### 验证

| # | 验证项 | 结果 |
|---|---|---|
| 1 | `/api/vi/health` | 404（端点已移除） |
| 2 | `/api/chat` 带图生成、只传图不写字 | ✅ 事件契约 `text_delta* → plan → done` 完整 |
| 3 | 真实读图：写着 `x²−5x+6=0` 的图 | ✅ 模型正确读出并因式分解，两根 x=2 / x=3 |
| 4 | 会话落盘**不含** `data:image` / `base64` | ✅ |
| 5 | 四条拒绝路径（超限/非图片/URL 关闭/无图无字） | ✅ 均给出可操作的中文提示 |
| 6 | 无图路径回归 | ✅ |
| 7 | 停止按钮（§8.67）在合并后仍生效 | ✅ 留档 `cancelled`、断开后计数停止增长 |
| 8 | `tests/` | **174 passed** |
| 9 | 前端 `tsc` / `eslint` / **`next build`** | 全部通过（8 个页面 + 4 个 mock 路由） |

### 8.69 「思考到几万字就卡住，过半天大纲突然出现」—— 回灌重试静默降级成非流式

**用户反馈**："深度思考思考到几万字（现象不固定，有的 2 万有的 3 万）然后就卡住了，
过了半天后分镜大纲突然就有了。我可以肯定卡住的时候并没思考完。"

日志那行是关键物证：

```
[api] chat topic='' imgs=1 -> 4 分镜 intent=propose attempts=4 streamed=True 旧版=无 思考=22849字 正文=265字
```

`attempts=4` —— 这次生成**跑满了 4 轮**（1 次首轮 + 3 次回灌重试）。

#### 根因：重试轮被降级成非流式，而**非流式没有任何进度事件**

`stream_storyboard()` 里，每一条"回灌重试"路径后面都跟着一句
`use_stream = False`（共 4 处：截断 / JSON 解析失败 / brief 为空 / 校验失败）。
含义是"下一轮不用流式，改用普通 POST 重来"。

后果是致命的，但**不报任何错**：

```
第 1 轮  流式 → 思考流一路推给前端（界面在动，用户看到"思考 2 万字"）
    ↓ 校验失败（或解析失败）
第 2~4 轮 非流式 → 一个事件都没有（界面完全静止，几分钟）
    ↓ 好不容易通过校验
界面才突然出现大纲
```

用户的描述（"思考到几万字 → 卡住 → 过了半天大纲突然出现"）**逐字对应**这个流程。
留档里也能看到形态：`streamed=True` 之后紧接着几条 `streamed=False`。

> 让这个 bug 特别难认的一点：日志里 `streamed=True` 是**首轮**的值，
> 它看起来像是"全程都流式了"，于是排查方向被带到"上游卡住了"上。
> 真正发生的是**我们自己关掉了流式**。

#### 修法：把"要不要流式"与"要不要把正文推给用户"解耦

这两件事原先被绑在一起（"重试 → 非流式"），但它们的诉求完全不同：

| 诉求 | 结论 |
|---|---|
| 重试期间**要有进度信号** | 必须保持流式 —— 思考流是那几分钟里唯一在动的东西 |
| 重试期间**不要再吐正文** | 必须不吐 —— brief 会重写一遍，上一轮的错字还挂在屏幕上，再吐一次就是同一段话出现两次 |

所以：
- 4 处回灌重试**不再** `use_stream = False`；
- `on_reasoning` 从"只有第一轮转发"改成**每轮都转发**；
- `on_delta`（正文）收紧成**只有第一轮**推送（`attempt == 1`）。

`use_stream = False` 只保留在**唯一该保留的地方**：连一个字节都没收到的
"流式真的不可用"降级（网关不支持 `stream: true`）—— 那里再试也是白等。

#### 顺带查清的两件事（都是实测，不是猜）

1. **这个中转站的思考关不掉。** 对照实测：
   `reasoning_effort=low` → 无效（思考 265 字，比基线还多）；
   `thinking={"type":"disabled"}` → 无效；`enable_thinking=false` → 无效；
   `reasoning_effort=minimal` → 直接 400。
   所以"收紧思考"这条路**走不通**，"调大 max_tokens"也只是买回"更晚才失败"。

2. **正文为空不一定是"上游挂住"。** 另有一条失败是
   `completion_tokens=5552 / reasoning_tokens=5552 / 正文 0 字 / finish_reason=stop`
   —— 思考**正好吃满**、上游还报"正常结束"。它是"思考把预算烧穿"的标准形态，
   但**不是**"连接被挂住"：实测用 `max_tokens=18000` 故意逼它撞上限时，
   上游会老老实实回 `finish_reason=length`（76.7s，不挂）。

#### 验证

用**假网关**做确定性验证（真实中转站的回灌是随机触发的，靠运气测不到）：
首轮返回坏 JSON 触发回灌重试，两轮都发思考流。

| 判据 | 修复前 | 修复后 |
|---|---|---|
| 第 2 轮（重试轮）的思考是否推给前端 | ❌ 无（非流式） | ✅ 有 |
| 正文是否只推一次 | — | ✅ 只推首轮 |
| `tests/` | — | **174 passed** |

真实链路复测（`:8019`）：83.8s，`thinking_delta` 共 **16701 个事件**，
`attempts=1 streamed=True`，全程**无 >8s 静默**（唯一一个 10.6s 间隔在 `plan` 之后，
即生成已结束的正常收尾）。

### 8.70 图片功能被自己的黑名单关掉了：`deepseek-chat` 其实能读图

**用户反馈**：切到 `direct` 档（`deepseek-chat`）后传图，被拒绝并提示
"当前模型 `deepseek-chat` 不支持图片输入"，用户的原话是**"怎么可能，应该能读的啊"**。

**用户是对的，闸口错了。**

#### 根因：一个"凭印象"的黑名单条目，把功能关掉了

`storyboard/vision.py` 的 `_TEXT_ONLY_MARKERS` 里写着：

```python
# DeepSeek 的对话/推理模型（视觉是单独的 VL 系列，不在这里）
"deepseek-chat", "deepseek-coder", "deepseek-reasoner", "deepseek-r1",
```

依据是"DeepSeek 对话/推理模型没有视觉能力"。**但这是过时的印象，不是事实。**

实测（**绕过 `ensure_can_read_images()` 直接发多模态请求**）：

| 模型 | 实测结果 |
|---|---|
| `deepseek-chat` | ✅ **能读** —— 0.8s 准确读出图里的 `x^2 - 5x + 6 = 0` |
| `deepseek-flash` | ✅ **能读** —— 1.2s 准确读出 |
| `deepseek-v4-pro` | ❌ 不能读，但模型**自己说明**了："无法识别图片内容" |

于是结论反转：**不是模型不支持，是我们凭一个过时的印象把自己能用的功能拦掉了。**

#### 修法：把判据从"模型行不行"改成"接口报错可不可读"

这次的教训不是"改黑名单内容"，而是**判据本身错了**。想清楚之后是这样的：

```
误拒（能读却拦下）→ 用户彻底失去功能，还收到一句误导性提示  ← 本次踩的
误放（不能读却放行）→ 多花一次往返，拿到一句（通常可读的）报错
```

**误拒的代价远大于误放。** 再往下推一层，闸口真正该拦的是"接口报错**看不懂**"那种
（老式纯文本模型回 `400 messages[0].content must be a string`，用户完全摸不着头脑）；
而 `deepseek-v4-pro` 失败时接口自己说得清清楚楚 —— 这种情况闸口**没有增益**。

所以最终形态：

1. `_VERIFIED_VISION` —— **实测能读图**的型号白名单，**优先级最高**，压过所有猜测。
   （这是唯一基于事实的一层，不该在下一次整理黑名单时又被猜进去。）
2. 名字带视觉特征（`-vl` / `vision` / `gpt-4o` …）→ 放行。
3. 黑名单只留**未实测的保守条目**（llama / mistral 那一代），且注明"不代表确定不支持"。
4. **拿不准一律放行**，让接口去说话。

#### 过程中的一个自我修正（值得记）

第一次改的时候，我把 `_TEXT_ONLY_MARKERS` 里**同一批**的 `llama-3` / `llama2`
**一起删掉了** —— 那是回归：`llama-3-70b` 立刻被放行。闸口回归脚本（15 个型号）
当场抓到了它。

**教训**：清理名单时，"实测证伪的那几条"和"同一批里没实测过的"是**两件事**，
不能因为写在一起就一起删。所以那条注释现在专门写了这句警告。

#### 验证

| 项 | 结果 |
|---|---|
| 闸口回归（15 个型号，含 3 个证伪点与 4 个易误伤点） | ✅ 全部符合预期 |
| 端到端：`direct` 档 + **不设** `MSB_VI_PROFILE`，走正常闸口传图 | ✅ 0.6s 读出 `2x + 3 = 11` |
| `tests/` | **174 passed** |

`run_api.bat` 里的 `MSB_VI_PROFILE=relay` 也已去掉（不再需要）。

> **验证某个型号能不能读图的标准做法**（下次不用猜）：
> 绕过 `vision.ensure_can_read_images()`，直接构造
> `content=[{"type":"image_url",...},{"type":"text",...}]` 发一次请求，
> 看模型能不能读出图里的字。成本几十个 token。

### 8.71 `deepseek-chat` 是遗留别名：改用真名 `deepseek-flash` + 显式关思考

**起因**：用户问"为什么 direct 档写的是 `deepseek-chat`，不应该是 `deepseek-flash` 吗"。
这个疑问指向了一处**长期存在的隐性依赖**。

#### 查到的事实

| 探测 | 结果 |
|---|---|
| `GET /models` | 只列 **`deepseek-flash`** 和 `deepseek-v4-pro` |
| 请求 `model=deepseek-chat` | ✅ 接受，但**响应回显 `model=deepseek-flash`** |
| 请求 `model=deepseek-chat-x` | ❌ 400，报错原文：`The supported API model names are deepseek-flash, deepseek-v4-pro` |

**结论：`deepseek-chat` 是官方为兼容旧代码保留的遗留别名，实际路由到 `deepseek-flash`。**

#### ⚠️ 关键差异：别名**隐含关掉了思考**

这是"只改名字"会踩的坑（同一 prompt、`max_tokens` 给足）：

| 请求 `model` | 回显 | reasoning_tokens | 耗时 |
|---|---|---|---|
| `deepseek-chat`（别名） | `deepseek-flash` | **None** | 0.59s |
| `deepseek-flash`（真名） | `deepseek-flash` | **51** | 0.87s |
| `deepseek-flash` + `thinking:{type:disabled}` | `deepseek-flash` | **None** | 0.61s |

也就是说**别名把"关思考"这个行为一并继承了**。直接换真名会让模型开始思考，
而这一档的定位恰恰是"快、无思考、拿来调 prompt"（思考起来就毁掉了它的价值）。

#### 修法：新增 `extra_body` 透传槽位

"关思考"这类参数**不是 OpenAI 标准**，各厂商名字还都不一样
（DeepSeek 官方 `{"thinking":{"type":"disabled"}}`、别家 `enable_thinking` /
`reasoning_effort` …）。在代码里硬编码任何一家都会误导下一家适配。

所以给 `llm.py` 加了一个透传槽位 `extra_body`（配置里的 dict 原样并进请求体）。
**三处必须同时改，漏一处就是静默失效**：

1. `cfg` 默认值里要有 `"extra_body": {}`；
2. `_merge_cfg()` 要**显式处理 dict**（和 `headers` 同理）——
   下面那行 `if k in cfg` 只放行**已有键**，而 `extra_body` 的子键
   （`thinking` / `reasoning_effort`）都不在 cfg 里，不写这段配置里写了也读不进来；
3. `_merge_extra_body()` 用 **`setdefault`**（不覆盖标准字段），
   且必须在 `llm_log.cache_key()` **之前**调用 —— 否则"改了思考开关"会命中旧留档指纹。

启动横幅也加了 `LLM 私有 : {...}` 一行：`extra_body` 现在承载"关思考"这类开关，
而"模型为什么在思考/为什么不吐正文"正是最难靠猜判断的事（§8.69 那几个坑都在这一带）。

#### 实测过的关思考写法（省得下次再试）

| 写法 | 结果 |
|---|---|
| `{"thinking": {"type": "disabled"}}` | ✅ 有效 |
| `{"reasoning_effort": "none"}` | ✅ 有效 |
| `{"enable_thinking": false}` | ❌ 无效（照样思考） |
| `{"thinking": false}` | ❌ 400（要的是对象，不是布尔） |

#### 验证

| 项 | 结果 |
|---|---|
| 配置解析出 `extra_body`（没被静默过滤） | ✅ |
| 真名 + 关思考：`reasoning=None`、0.89s，与旧别名等价 | ✅ |
| 真名**不**关思考：`reasoning=77`（证明开关真在起作用） | ✅ |
| 端到端：读图 + 分镜生成 | ✅ **6.6s / attempts=1 / 4 个分镜**，正确解出两根 |
| 实测留档（新配置） | `deepseek-flash \| 6.6s \| completion=2081 \| reasoning=None` |
| `tests/` | **174 passed** |

### 8.72 字幕里的白方块：`①` 被判给了 Times New Roman（缺字形 → `.notdef`）

**用户反馈**（2026-09-18，截图）：字幕「`①` 加 0 还是它自己　`②` 加「下一个」」前面
是**两块印着 `2460` / `2461` 的白色空心方块**。

这个"印着码点"的样子本身就是线索：它不是"字没渲染出来"，而是**缺字形占位符**
（`.notdef`）。顺着它查下去，一个字符在三条路径上有**两种坏法**。

#### 根因：Pango 只在"当前字体没有字形"时才 fallback，而我们**显式指定了字体**

`dsl._markup()` 的职责是中英混排：中文走 `CN_FONT`、数字/字母走 `LATIN_FONT`。
它的切分判据是"是否落在 CJK 区间"（`\u4e00-\u9fff` 等），于是 `①`(U+2460)
**不在中文区间** → 被判给 `Times New Roman`：

```
<span font_family="Times New Roman">①</span>   ← Times 没有 U+2460
```

而被显式指定之后，Pango **不会**再去后备字体里找 —— 直接画 `.notdef`。
实证：同一句话在纯 `CN_FONT` 下正常、在现行逻辑下就是那四块白块（逐帧对照过）。

> 顺带排掉一条看着更省事的路：把 `LATIN_FONT` 写成 `"Times New Roman,STZhongsong"`
> 回退列表（Pango 确实支持，实测有效）。**不能用** —— 它对中文字也生效，
> 等于把西文字形交给宋体决定，正是 2026-09-16「数字和字母都不是新罗马体」的成因。
> 只能是**局部、显式**地切字体。

#### 同一个 ① 在公式里是"整镜渲不出来"，比白块严重得多

`formula()` 的分流判据原来也是 `_has_cjk`（只认中文），于是
`{"kind":"formula","content":"① ..."}` 会走 `MathTex`(pdflatex)，而 pdflatex 直接：

```
LaTeX Error: Unicode character ① (U+2460) not set up for use with LaTeX
```

**一个字符毁掉一镜**（实测确认）。所以正文里是"白块"、公式里是"崩"，
两种坏法一起在这轮收口。

#### 修法：判据**只有一份数据**，且由构造保证"公式判据 ⊇ 字形判据"

码点表与正则都收进 `metrics.py`（纯数据、不依赖 manim，谁都能 import），
三处读同一份：`dsl._markup()` / `dsl.formula()` / `tex_batch` 的预热分组
（后者漏掉就会"预热按 A 算 hash、真渲染按 B 查" → 缓存全落空且**静默**）。

- **回退表** `FALLBACK_RANGES`：`Times New Roman 缺 且 CN_FONT 有`的码点
  （GDI `GetGlyphIndicesW` 逐码点比对得出，59 段 / 227 个码点）。
- **公式判据** `FORMULA_TEX_PATTERN`：**并上**回退表 + 更宽的符号块。
  两条判据的坏处方向不同 —— 正文误判给宋体只是字形风格差一点，
  公式误判给 pdflatex 是一镜全废，所以后者宁可更保守。

> ⚠️ 这里踩到一个值得记住的坑：**第一版是手写两份区间**，然后**当场被自己刚写的
> 测试抓出一个漏网** —— `∈`(U+2208) 在回退集里、却不在公式正则里，
> 也就是"正文修好了、公式里仍会炸"。改成 `_merge_ranges(FALLBACK_RANGES, ...)`
> 由代码算并集之后，这个 bug 在结构上不可能再出现。

#### 顺带修掉的两个同源问题

1. **估算器把符号按普通标点定价**：`①◆★→∈` 这档原来落进 `K_OTHER = 0.0100`，
   而实测 0.0132~0.0149 —— **低估 33%~49%**，违反 `metrics` 顶部"宁大不小"的铁律。
   后果是校验说放得下、渲染出来贴边甚至出画。新增 `K_FALLBACK = 0.0150`。
2. **占位符注入**：`RUNTIME_HELPER` 是被逐字写进生成代码的**字符串**，
   所以里面只能用占位符（`__FALLBACK_RANGES__` / `__FORMULA_TEX_PATTERN__`），
   在文件末尾 replace 成真实值。⚠️ 不能用 `%` / `.format()` ——
   helper 里有大量 `%s` 与 `{}`（生成代码本身），会被当占位符解析
   （`renderer.HEADER` 当初也是为这个原因改用拼接的）。

#### 验证

| 项 | 结果 |
|---|---|
| 逐帧对照探针（同一句话三种字体归属） | ✅ 修复前是白块、修复后正常 |
| 端到端重渲用户截图那份分镜（`u3_t_39qovs5`） | ✅ ① ② 正常显示，第三镜抽帧确认 |
| 三处判据同源（模块层 / 注入 helper / tex_batch） | ✅ 逐串比对一致 |
| 新增 `tests/test_glyph_fallback.py` | **36 passed** |
| `tests/` 全量 | **210 passed**（原 174 + 新 36） |
| 回归面 | 普通公式仍走 MathTex 快路径；`A z 0 = ( ■ ● → ± √` 仍走西文字体 |


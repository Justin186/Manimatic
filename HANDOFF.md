# Manimatic 交接文档

> 面向：接手这个仓库的人 / AI。
> 读完这份，应当能立刻知道「什么已经能用、还剩什么、坑在哪」。
> 前置阅读：`PROJECT_KNOWLEDGE.md`（项目知识库）、`docs/` 下三份设计文档。
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
| 40 | 字幕过渡可配 + 版面按尺寸判定 | ✅ | `replace` 加 `effect`（crossfade 默认/slide/write/morph，且 crossfade 是**分段交接**不是全程叠加）；表格加 `cell_w`/`cell_h`/`pad_x`/`pad_y`（定尺格子用不可见框，`get_cell` 仍精确）；新增 `metrics.py` 纯算术估算器，删掉三类条数上限改成"估尺寸 → 明显超出才拒 + 精确回灌"；`_fit` 按位置算且真缩打 WARN。见 §8.34 |

**前端侧不用改一行**：`NEXT_PUBLIC_USE_MOCK=false` + `NEXT_PUBLIC_API_BASE_URL` 即可切换。

### 部署注意

| 项 | 说明 |
|---|---|
| MiKTeX 约 2GB | Docker 镜像会很重，建议换 TinyTeX 或 TeX Live 精简安装 |
| ffmpeg | 必须装且在 PATH（`generate.py` 用它拼接，失败会退回重编码） |
| 中文字体 | 现用 `STZhongsong`（华文中宋），Linux 容器里需装对应字体 |
| 磁盘 | `output/_parts/` 增长快，必须挂卷 + 定期清理 |
| 超时 | 单任务 8 分镜约 11–40s，HTTP 网关超时要调到 **≥ 120s** |

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

#### ④ 兜底缩放 `_fit` 改成按**位置**算，且真缩了要喊

原来写死 `max_w=12.4 / max_h=6.6`，那只对**居中**的元素成立：元素被
`place:[{"edge":"down","buff":1.0}]` 摆到偏下位置时，它下方只剩一点点空间 ——
还按 6.6 判就是"看代码没事、渲染出来下半截出画"。现在按元素**当前中心**算可用空间
（居中元素算出来恰好还是 12.4×6.6，所以老分镜观感零变化），并且**真缩了打 `[WARN]`
点名元素 id 与缩放比例**（同一个元素只喊一次，`always_redraw` 每帧调用不会刷屏）。

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


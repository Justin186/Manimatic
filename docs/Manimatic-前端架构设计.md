# Manimatic · 前端架构设计

> 版本：v1（2026-09-12）
> 适用阶段：D6「接前端」到 D10 答辩
> 配套文档：`前端接入-后端改造清单.md`（后端要补的三块）
> 后端现状参考：`D:\Manimatic\MathStoryboard\PROJECT_KNOWLEDGE.md`

---

## 0. 一句话

Web 优先的**对话式讲解动画创作台**：用户用自然语言提题 → AI 先文字回答 → 给出分镜大纲 → 用户确认 → 逐分镜渲染出片 → 可在对话里继续修正。

---

## 1. 决策总表

所有条目已经过逐轮确认，开工前不必再议。

| # | 决策项 | 结论 |
|---|---|---|
| Q1 | 跨端形态 | **Web 优先**，PWA + Capacitor 出 iOS/Android 包。不做 RN / Flutter / uni-app |
| Q2 | 团队 | 大学生 + AI 辅助开发 → 技术栈选 **AI 训练数据最厚的 React 生态** |
| Q3 | 生产链路 | 对话式；注册时持久化用户画像；流程 = 文字 → 大纲 → 确认 → JSON → 渲染 |
| Q4 | 设计系统 | 成熟组件库打底 + 自研核心组件 |
| Q5 | 框架 | **Next.js App Router + Tailwind + shadcn/ui** |
| Q6 | 流式策略 | **v1**：完整 JSON → 分镜级并行渲染 → 逐段推送。v2 再上"边生成边渲染" |
| Q6b | 大纲确认 | **全部要确认**，不做意图识别（= plan approval） |
| Q7 | 主布局 | **三栏（历史 / 对话 / 预览）+ 左右可收起**，状态持久化；移动端降级单栏 |
| Q8 | 画像配置 | 注册问 3 项（身份 + 学段 + 科目）；风格/时长用默认值，被改过就记住 |
| Q9 | 多角色 | C 端一套（角色只影响默认面板与措辞）；**B 端学校后台先不做** |
| Q10 | 账号支付 | 测试账号（不发真邮件）+ 假支付页；配额校验抽成独立函数 |
| Q11 | 视频交付 | 内联可播放卡片 → 点击进入全屏 → 全屏内分享/下载；移动端长按菜单 |
| Q12 | 失败处理 | 自动重试 1 次 → 仍失败显示**单分镜重试**按钮，**绝不整条失败** |
| Q13 | 建模 | 按**工具调用事件流**建模；MVP 只一个工具，不上 MCP / LangChain |
| Q14 | 版本呈现 | **每次修正生成一条新消息**（v1/v2/v3 并列，保留不同方向的尝试） |
| Q15 | 触发方式 | **模型建议 + 用户确认**；纯概念问答不出大纲，就是普通对话 |
| Q16 | 修正粒度 | **整分镜替换**（重新输出一个完整 scene），不做文本 patch |

### 为什么是工具调用模型，而不是普通三段式

系统需要判断"这一轮要不要调渲染工具" —— 用户可能只是聊天（"什么是直角三角形"），也可能要出片（"讲一下勾股定理"）。**这个判断权在模型手上，所以是工具调用。**

但注意边界：**控制流仍是固定管线，不是自主 Agent**。模型只决定"调不调"、"调几次"，不决定"调什么"（永远只有 `render_storyboard` 一个工具）。因此：

- ✅ 用工具调用的**数据结构**（事件流）
- ❌ 不引入 Agent 框架（执行路径不确定会显著抬高调试成本）

---

## 2. 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 框架 | **Next.js（App Router）** | SEO（讲题内容天然可检索）+ Route Handler 做 BFF + 部署简单 |
| 语言 | **TypeScript**（strict） | AI 辅助开发下类型即契约，能挡掉大量低级错误 |
| 样式 | **Tailwind CSS** | shadcn/ui 依赖；改色板成本低 |
| 组件库 | **shadcn/ui** | 代码进仓库可任意改，不是黑盒依赖 |
| 状态 | **Zustand**（本地 UI）+ **TanStack Query**（服务端） | 轻量；不要把 SSE 流塞进全局 store |
| 流式 | **Vercel AI SDK**（`useObject` / `useChat`） | 与 Next.js 同源；自带 partial JSON 解析 |
| 表单 | **react-hook-form + zod** | zod schema 可与后端分镜 schema 对齐 |
| 移动端 | **Capacitor** | 一套 Web 代码出双端包 |
| 图标 | **Lucide** | 免费商用，与 shadcn 同源 |

**依赖纪律**：每加一个依赖前先问"AI 熟不熟悉它"。不熟 = 踩坑时 AI 救不了你。

---

## 3. 页面拓扑与路由

```
/                     落地页（SEO 主战场：示例讲题 + 注册引导）
/app                  创作台（重定向到最近会话）
/app/t/[threadId]     具体会话（三栏主界面）
/share/[slug]         分享页（公开，无需登录）
/login                登录（测试账号）
/settings             画像配置 / 偏好 / 配额
/pricing              假支付页（静态）
```

### 角色只影响默认面板，不做独立路由

| 角色 | 落地面板 | 措辞差异 |
|---|---|---|
| 学生 | 创作台（直接提问） | "你想搞懂哪道题？" |
| 家长 | 学习记录 | "孩子最近看了这些" |
| 教师 | 课件生成 | "生成一段课堂讲解" |

---

## 4. 主布局规格（三栏可收起）

```
┌──────────────────────────────────────────────────────────┐
│ 顶栏：Logo  会话标题   画像指示条（学段·科目 ▾）   账户   │
├────────┬────────────────────────────┬───────────────────┤
│ 历史   │      对话流                 │  预览             │
│ 会话   │                            │                   │
│ 列表   │  ┌──────────────────┐      │  ┌─────────────┐  │
│        │  │ 用户：讲勾股定理  │      │  │             │  │
│ [收起] │  └──────────────────┘      │  │  视频播放器  │  │
│        │  ┌──────────────────┐      │  │             │  │
│        │  │ 助手：文字回答    │      │  │  v1 ▾       │  │
│        │  │ ┌──────────────┐ │      │  └─────────────┘  │
│        │  │ │ 大纲卡片      │ │      │                   │
│        │  │ │ [确认生成]   │ │      │  分镜列表          │
│        │  │ └──────────────┘ │      │  ① ② ③ ④ ⑤      │
│        │  │ ┌──────────────┐ │      │                   │
│        │  │ │ 视频（内联）  │ │      │  [收起] [最大化]  │
│        │  │ └──────────────┘ │      │                   │
└────────┴────────────────────────────┴───────────────────┘
```

**硬性约束**

1. **中栏最小宽度 480px**，不按比例无限压缩 —— 否则两侧全展开时对话区会被挤成一条
2. **收起状态持久化**到 localStorage（左栏 `nav.collapsed`、右栏 `preview.collapsed`）
3. **移动端自动降级单栏**：左右栏变抽屉（shadcn `Sheet`），不做三栏硬塞
4. **快捷键**：`⌘/Ctrl + \` 切左栏，`⌘/Ctrl + Enter` 确认生成

---

## 5. 数据模型（事件流）

一条助手消息不是三个字段，而是一条**事件流**。这是全篇最重要的数据结构。

```ts
type AssistantEvent =
  // 文字回答（流式，总是第一个到）
  | { type: 'text_delta';  text: string }

  // 分镜大纲（需要出片时才有）
  | { type: 'plan';        plan: PlanStep[];  intent: 'propose' | 'none' }
  //   intent='none' 表示模型判断这题不该出动画 → 消息到此结束，就是普通对话

  // 工具调用
  | { type: 'tool_call';   name: 'render_storyboard'; args: { storyboard: Storyboard } }
  | { type: 'tool_progress';  step: number; total: number; stage: 'prewarm' | 'rendering' | 'concat' }
  | { type: 'tool_result'; index: number; url: string; durationSec: number }
  | { type: 'tool_done';   url: string; segmentCount: number }

  // 错误（scope 决定重试粒度 → 直接对应 Q12 的 B 方案）
  | { type: 'error'; scope: 'step' | 'task'; index?: number; message: string; retryable: boolean }
  | { type: 'done';  messageId: string };

type PlanStep = {
  id: number;
  title: string;        // "面积法演示"
  durationSec: number;
  summary: string;      // 一句话讲这步演什么
};

type Storyboard = {
  title: string;
  problem_type: string;
  scenes: Scene[];      // 与后端 schema 一致
};
```

**为什么这样设计**

- `error.scope='step'` 天然对应"单分镜可重试"，不需要额外数据结构
- 未来加工具（练习题、知识点问答）只要加一个 `tool_call` 分支 + 一个渲染器，前端骨架不动
- 这个形状和 Vercel AI SDK / OpenAI tool calling 一致，AI 写组件时命中率最高

---

## 6. 组件清单

### 用 shadcn/ui（不要自己写）

`Button` `Input` `Textarea` `Dialog` `DropdownMenu` `Select` `Tabs` `ScrollArea` `Avatar` `Toast` `Tooltip` `Sheet`（移动端抽屉）`Skeleton` `Progress`

### 自研（7 个核心组件）

| 组件 | 职责 | 难点 |
|---|---|---|
| `ChatStream` | 对话流容器 | 长列表虚拟滚动；流式时自动滚底但用户上滑后要停止跟随 |
| `AssistantMessage` | 按事件类型分发渲染 | 一个消息内可能同时有 text + plan + video |
| `StoryboardPlanCard` | 大纲卡片 | 可编辑（改顺序/删步骤/调时长）+ `确认生成` / `重新生成` 两个按钮 |
| `RenderProgress` | 分镜级进度 | 5 个分镜各自的 排队/渲染中/完成/失败 状态；失败的那个可点重试 |
| `VideoSegmentCard` | 内联视频卡片 | **静音 autoplay**（浏览器策略）、循环、点击进入全屏 |
| `VideoStage` | 全屏播放器 | 进度条、倍速、分享链接、下载 MP4 |
| `ProfileBar` | 生成参数条 | 显示当前生效的画像，可在本次生成临时覆盖 |

---

## 7. API 契约（草案）

### `POST /api/chat` → SSE

```
Request
{
  thread_id: string,
  message: string,
  profile: { role: 'student'|'parent'|'teacher', grade: string, subjects: string[] },
  overrides?: { style?: 'concise'|'detailed'|'fun', targetDurationSec?: number }
}

Response: text/event-stream
  event: text_delta      data: {"text":"勾股定理是指..."}
  event: plan            data: {"plan":[...],"intent":"propose"}
  event: tool_call        data: {"name":"render_storyboard"}
  event: tool_progress   data: {"step":2,"total":5,"stage":"rendering"}
  event: tool_result     data: {"index":1,"url":"/media/t123/v1/s1.mp4","durationSec":4.5}
  event: tool_done       data: {"url":"/media/t123/v1/final.mp4","segmentCount":5}
  event: error           data: {"scope":"step","index":3,"message":"...","retryable":true}
  event: done            data: {"messageId":"m_789"}
```

### `POST /api/render/confirm`
用户点「确认生成」后触发。body 带 `thread_id` + `plan`（可能已被编辑过）。

### `POST /api/render/retry`
```
{ thread_id, message_id, scene_index }   // 单分镜重试（Q12-B）
```

### `POST /api/storyboard/replace-scene`
```
{ thread_id, message_id, scene_index, instruction: "第三步讲慢一点" }
→ 模型重新输出该分镜 → 校验 → 只重渲这一个（Q16）
```

---

## 8. 关键交互时序（正常出片）

```
用户提交
  │
  ├─ 0.3s  text_delta 开始 → 文字逐字出现        ← 用户立刻看到回应
  │
  ├─ 3~5s  plan 到达 → 大纲卡片渲染出来
  │         └─ 用户可编辑，点「确认生成」
  │
  ├─ 20~40s text+JSON 全部生成完（v1 策略）
  │
  ├─ 确认后 → tool_call → 后端并行渲染
  │         tool_progress  step 2/5 …            ← 分镜级进度
  │
  ├─ ~11s  tool_result index=0 首段到达 → 内联播放器开始播第 1 段
  │        后续片段陆续到达，无缝续上（注意：渲染是并行的，
  │        片段可能乱序到达 → 前端必须按 index 缓冲、顺序播放）
  │
  └─ tool_done → 完整片可下载，分享链接生成
```

⚠️ **2026-09-12 更新：乱序不再是必然的。**

早期设想是后端多进程并行渲染，所以片段会乱序到达、前端要写缓冲编排器。
但实测发现多进程是负优化（内存带宽封顶，8 分镜 18.0s vs 默认 12.3s），
现在后端默认**一个 Scene 演到底 + manim 原生 `--save_sections`**：
片段按分镜顺序一次性产出，还附带每段的**真实帧时长**（`sections/StoryboardScene.json`）。

所以：
- 前端的整片时间轴**应该用 json 里的真实 duration**，而不是大纲的估算值 —— 分镜定位会精确得多
- 「缓冲编排器」可以退化成"按 index 取对应文件"，不需要乱序重排
- 逐段推送的粒度也随之变化：现在是"整条渲完一起交付"（~13s），不是"渲好一段推一段"

---

## 9. 设计系统

沿用商业计划书定下的品牌基调，但**产品 UI 的密度和 BP 不同**（BP 是路演材料，产品是天天用的工具）。

| 项 | 值 | 说明 |
|---|---|---|
| 主色 | 学院藏青 `#12263F` | 顶栏、主按钮、标题 |
| 强调 | 砖橙 `#B85C38` | 仅用于 CTA、进度、关键数字 |
| 背景 | 冷灰白 `#F4F5F7` | 页面底 |
| 分隔线 | `#D5DAE1` | 1px，编辑网格风格 |
| 正文 | `#4A5A6B` | |
| 标题字体 | Noto Serif SC（思源宋体） | |
| 正文字体 | Noto Sans SC（思源黑体） | |
| 数字/英文 | Noto Serif SC（**等高数字**，不用 Georgia） | Georgia 是旧式数字，会高低不齐 |

⚠️ 复用 BP 踩过的坑：**数字一律加粗**；**中文不要用 Georgia**（会 fallback）。

---

## 10. 分期与风险

### v1（答辩前必须）

1. 三栏布局 + 对话流 + 文字流式
2. 大纲卡片 + 确认生成
3. 分镜级进度 + 逐段推送 + 内联播放
4. 单分镜失败重试
5. 测试账号 + 假支付页

### v2（有余力再做）

- **边生成边渲染流水线**（首段从 ~41s 降到 ~11s）—— 最大性能杠杆，但耦合了"LLM 流式"和"渲染调度"两条复杂链路，别和 v1 一起做
- 分享链接 + 对象存储
- 分镜大纲可视化编辑（拖拽排序）

### 三个已知风险

| 风险 | 应对 |
|---|---|
| 渲染 20~40s，用户以为卡死 | 分镜级进度条 + 已完成的片段立即播放，让等待可见 |
| 片段乱序到达 | 前端缓冲编排器（见 §8） |
| 浏览器 autoplay 策略 | 内联播放器**必须 muted** 才能自动播；全屏再恢复声音 |

---

## 11. 开工顺序建议

```
1. Next.js 骨架 + Tailwind + shadcn 初始化
2. 三栏布局（先用假数据）        ← 布局定下来，后面都是往里填
3. ChatStream + 文字流式（接 /api/chat 的 text_delta）
4. StoryboardPlanCard + 确认流程
5. RenderProgress + 逐段播放（含缓冲编排器）
6. 错误处理与重试
7. 移动端适配（Capacitor 出包）
```

第 2 步先做是有意的：**布局是骨架，骨架歪了后面全歪**。别先做漂亮的组件再往里塞。

---

## 12. 仓库组织：两个仓库，目录相邻

```
D:\Manimatic\
├── MathStoryboard\     ← git repo A（Python 渲染服务，已存在）
└── web\                ← git repo B（Next.js，GitHub 仓库名 manimatic-web）
```

> 本地目录名与远端仓库名不必一致：clone 时用第二个参数指定即可
> （`git clone <url> web`），也可以 clone 完直接重命名目录 —— git 只认 `.git` 里的远端配置。

### 为什么不是 monorepo

1. **部署形态根本不同** —— 前端是 Vercel（静态 + 边缘函数），后端是长驻 CPU 渲染服务器。
   它们不是一个部署单元，放一个仓库只是把两份无关的 CI 绑在一起。
2. **依赖体积差一个量级** —— 后端要 MiKTeX（约 2GB）+ ffmpeg + manim，
   前端是 node_modules。合在一起，任何一次 CI 都要为另一半付代价。
3. **没有任何可共享的运行时代码** —— Python 和 TS 之间只有"契约"要共享，没有逻辑要共享。
   而契约不需要 monorepo 也能同步（见下）。
4. 后端**已经是独立 git 仓库**了，合并进去要动历史，收益却接近零。

**反过来说，什么时候才该合**：当两边开始共享大量代码时（比如同一份 schema
既要前端校验又要后端校验）。你们不会 —— 分镜 schema 的**权威在 Python 侧**
（`dsl.py`），前端只是消费者。

### 契约怎么同步（这是分仓库唯一的代价）

SSE 事件类型就 8 个，MVP 阶段**手工对齐**即可：

- 后端仓库维护 `api/openapi.json`（FastAPI 自带 `/openapi.json`，直接导出落盘）
- 前端维护 `src/types/api.ts`，与 `AssistantEvent`（§5）一一对应
- **改事件定义时两边一起改**，并在各自 README 写清"配套项目在本地的路径"

等事件类型超过 15 个、开始频繁变动时，再上自动化：

```bash
npx openapi-typescript ../MathStoryboard/api/openapi.json -o src/types/api.ts
```

### ⚠️ 给 AI 辅助开发的重要提示

分仓库后，AI 写代码时看不到另一半。所以**两边都要在显眼处写清配套关系**：

- `MathStoryboard/README.md` 顶部加一行：
  > 前端配套项目在 `../web`（Next.js），SSE 契约见 `前端接入-后端改造清单.md`
- `web/README.md` 顶部加一行：
  > 后端渲染服务在 `../MathStoryboard`（Python），事件定义见 `前端接入-后端改造清单.md`

少了这两行，AI 每次都会在错误的一侧找答案。

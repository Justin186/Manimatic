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

> 全站视觉做过一次重做，方向是**「暖调纸感进阶」**：配色与字体完全重定，只保留产品定位。
> 唯一的设计令牌源是 `web/src/app/globals.css`。
> 旧的「学院藏青 + 砖橙」令牌（`navy-*` / `brick-*` / `canvas` / `line` / `ink*` / `ok-600` 等）
> **已整块删除**，不要再使用或加回。

### 9.1 三层令牌

| 层 | 例子 | 谁可以引用 |
|---|---|---|
| 原始层 | `#cf5a34`、`#e8744f` | 只写在语义层里，组件不许直接引用 |
| 语义层 | `--t-bg` / `--t-surface{,-2,-3}`、`--t-border{,-strong}`、`--t-fg{,-muted,-subtle}`、`--t-accent{,-hover,-fg,-soft}`、`--t-accent-2`、`--t-ok` / `--t-warn` / `--t-err`（各带 `-soft`） | 组件**只认这一层** |
| Tailwind 暴露层 | `@theme inline` 把语义名接到工具类：`bg-surface` / `text-fg-muted` / `border-border` / `rounded-card` / `shadow-raised` | 页面与组件写这些 |

**迁移纪律**：组件层**禁止**出现旧令牌类名。要求"换主题只改令牌、不动组件"，
就必须保证组件不感知具体色值 —— 两套色板并存看着方便，代价是同一个视觉意图有两种写法，
改一处漏一处。

### 9.2 色板

| 语义 | 亮色 | 暗色 | 用途 |
|---|---|---|---|
| `--t-bg` | `#FAFAFA` | `#17130F` | 页面底 |
| `--t-surface` | `#FFFFFF` | `#211B16` | 卡片面 |
| `--t-surface-2` | `#F2F2F3` | `#2B241D` | 内嵌面（工具条、卡头、静默按钮） |
| `--t-surface-3` | `#E9E9EB` | `#352C24` | 凹陷面（进度轨道、骨架条） |
| `--t-border` | `#E6E6E8` | `#3A3128` | 1px 描边 |
| `--t-border-strong` | `#D2D2D6` | `#4D4133` | 需要"看得见"的描边（滚动条） |
| `--t-fg` | `#1A1A1C` | `#F6F0E8` | 正文与标题 |
| `--t-fg-muted` | `#63636A` | `#B3A490` | 次要文字 |
| `--t-fg-subtle` | `#94949C` | `#87765F` | 辅助说明、占位符 |
| `--t-accent` | `#CF5A34` | `#E8744F` | 品牌强调（CTA、进行中） |
| `--t-accent-soft` | `#FBEAE2` | `rgba(232,116,79,.16)` | 品牌色浅档 |
| `--t-accent-faint` | `#FDF5F1` | `rgba(232,116,79,.07)` | 比 soft 再淡一档 |
| `--t-accent-2` | `#A9663A` | `#D99A5C` | 次级强调（焦糖） |
| `--t-ok` | `#2F7A5A` | `#7FB07F` | 已完成 |
| `--t-warn` | `#B8892B` | `#E0AB5C` | 需要注意 |
| `--t-err` | `#E5484D` | `#F2686D` | 失败 / 不可逆操作 |
| `--t-video` | `#17171A` | `#0D0A07` | 视频容器底（**两档都深**） |
| `--t-inverse` | `#1F1F23` | `#F6F0E8` | 反色面（轻提示、Tooltip），**随主题反转** |
| `--t-solid` | `#FBEAE2` | `#3B3129` | **结构面**：对话气泡、分镜序号胶囊 |

⚠️ **品牌色有两档浅填充（soft / faint），别混用、也别合并**：

同一块区域里经常要放两处暖色（比如分镜大纲的卡头 + 每行的序号胶囊）。
这时候**必须把档差拉开** —— 结构用 `faint`、标记用 `soft`：

- 两处都用 `soft` → 两个等量暖色叠加，整块变成"一片粉"，色明显过重
- 结构改中性（`surface-2`）→ 出现"两种差半档的暖色并排"的脏感，谁都不像底色
- 都调淡一点 → 无效，那是等差叠加，比例没变

（这一处来回改了四轮才定下来，代价不小，别再退回上面任何一种。）

#### 减橙色的正确做法

收到"橙色太多"时，**不要把它实现成"把所有品牌色都调浅"**。
那是等差削弱的做法 —— 削掉橙色面积的同时**把对比度一起削掉了**：

- 序号胶囊从"实心深色 + 白字"变成"浅橙 + 橙字"后，7 行编号几乎扫不出来
- 用户气泡变成浅色后，和页面底太近，认不出是谁在说
- 结果两头都没了：橙色没减掉多少，"结构与标记"的层次全塌

正确的减法分两步：

1. **减少橙色的处数**：装饰性的橙（光晕、头像渐变、能力卡图标底、序号）要么删掉、要么换中性色
2. **保住剩下那些的对比强度**：该撑结构的地方用**实心深色**（`--t-solid`），不是浅品牌色。
   深色不参与"橙味"，但它承担了全部对比度

判断标准：**先问这个元素是"结构"还是"标记"**。

| | 用什么 |
|---|---|
| **结构**（对话气泡、分镜序号、卡头、分隔线、版面分层） | `--t-solid` 或中性面 |
| **标记**（进行中、选中、品牌图标、状态徽章） | 品牌色，且尽量只留一两处小面积 |
#### 亮暗两档故意不是同一套中性色

| | 中性色 | 结构面 `--t-solid` |
|---|---|---|
| **亮色** | **纯中性灰**（不偏黄也不偏蓝） | `#FBEAE2` —— 最浅一档的**品牌色**填充 |
| **暗色** | 暖调深色（深暖棕黑） | `#3B3129` —— 比页面底**亮一档**的面 |

- **亮色用中性灰**：中性灰不跟任何色相打架，橙色才能干净地跳出来。
  试过暖白底 + 珊瑚橙（整片发闷发脏，"层次"退化成"色差极小的一片暖"）、
  冷蓝灰 + 学院藏青（干净，但和橙色强调像两套体系）—— 都不如中性灰。
- **暗色保留暖调**：深色底本来就不显暖，暖调在这里反而让长时间看视频不刺眼。

⚠️ `--t-solid` 里的 `solid` 指"**实心填充**"（相对于描边 / 透明），**不指颜色深浅**。
这一档在两个主题下是**相反的明度关系**，这是有意的：

- 亮色档是**浅**的（`#FBEAE2`）：气泡和序号只需要和页面底有一点点区分，
  不该是整屏最重的一块。试过五种深色（近黑 / 暗棕 / 暖棕 / 藏青 / 深灰）全都不对 ——
  深色气泡会把视线从正文上拽走。
- 暗色档是**比底亮一档**的：页面底已经很暗，实心面只要浮起来就够；取太亮会反过来成为最亮的块。

对比度靠**字色**而不是底色：亮色档 `--t-solid-fg` 是近黑（`#1A1A1C`），
所以"浅底 + 深字"读起来毫不费力。反过来用"浅底 + 橙字"会掉到看不出来（试过，序号几乎扫不出来）。

三条定色取舍都是踩出来的：

- **危险色用纯正的红（`#E5484D`）**：删除这类不可逆操作要一眼认出来，
  偏棕的暖红在浅底上"红得不够确定"。
- **亮色投影用中性黑（`rgba(20,20,22,…)`）**，不用带色相的投影：
  暖棕或藏青的投影会立刻让"中性灰"偏色。
- **亮色不铺大面积品牌光晕**：暖光落在中性灰底上会糊成一块脏黄；
  只留一丝品牌色 + 一层几乎看不出深度的中性渐变，让关键区"有一点空气感"就够。

### 9.3 字体：两级，不能混用

| 令牌 | 字体 | 用在哪 |
|---|---|---|
| `--t-font-display` | Noto Serif SC（本机兜底 `Songti SC` / `SimSun`） | **展示级**：Hero 大标题、空态问候语、页面级标题 |
| `--t-font-heading` | Noto Sans SC / 系统无衬线 | **功能级**：弹窗标题、区块标题、卡片标题 |
| `--t-font-body` | 同上 | 正文 |

⚠️ **默认是功能级（无衬线），展示级才显式加 `font-display`**（`@layer base` 里 `h1~h4` 走 `--t-font-heading`）。
反过来的默认值是错的：全局衬线会让「删除 2 条会话？」这类高频、带后果的标题变慢识别。

数字一律加 `.tnum`（等高）。踩过的坑：Georgia 是旧式数字（0/1 矮、6/8 高），混排高低不齐。

⚠️ 字体仍走 `<head>` 的 Google Fonts `<link>`（**不用 `next/font`**，避免构建期联网依赖）。
Noto Sans SC 请求的是**可变字重区间** `wght@100..900`。

⚠️⚠️ **`--t-font-heading` / `--t-font-body` 里 `"Noto Sans SC"` 必须排在最前面。**

排在系统字体之后时，Windows 会命中微软雅黑 —— 而微软雅黑只有 Regular / Bold 两个字重：

| font-weight | 实际渲染 |
|---|---|
| 400 / 500 | Regular |
| **501 ~ 699** | **Bold（向上吸附）** |
| 700+ | Bold |

所以"比 medium 稍微粗一点"在系统字体上**根本表达不出来**（只能是 Regular 或 Bold 二选一，
中间是空的 —— 这也是"字重调不动"这类反馈的真正原因）。
Noto Sans SC 是可变的，`font-[550]` 这种中间值才真实存在（分镜标题用的就是 550）。

代价可控：Google Fonts 对 CJK 按 `unicode-range` 切片，浏览器只下载页面**实际用到**的字
所在的分片，不是整套中文字体；系统字体仍保留在栈尾兜底（字体没加载出来时中文不会掉成衬线体）。

### 9.4 形态：圆角 / 阴影 / 材质

| 令牌 | 值 | 用途 |
|---|---|---|
| `--t-radius-control` | 10px | 按钮、输入框 |
| `--t-radius-inner` | 12px | 卡内的内嵌面、缩略图、**对话流内联卡片**（大纲 / 进度 / 播放器） |
| `--t-radius-card` | 16px | 页面级卡片、弹窗、**对话气泡** |
| `--t-radius-pill` | 999px | 徽章、胶囊 |

对话气泡的形状 = `rounded-card` + **`rounded-br-[5px]`**：右下角收成一个小直角，
像"尾巴"一样指向说话人。试过做成参考产品那种完整胶囊（`pill`），
但胶囊会把尾巴吃掉，左右两边就只剩"对齐方向"一个区分，容易认错谁在说话 —— 已回退。

**不要给气泡加描边**：浅填充本身已经能从页面底和白卡片里分出来，
再加一圈描边就成了"描边 + 填充"的双重标注，显重。

⚠️⚠️ **新增任何自定义刻度（新的 `--radius-*` / `--duration-*`）都必须同步注册到 `lib/utils.ts` 的
`extendTailwindMerge`**，否则 `tailwind-merge` 不认识它，`cn("rounded-card", "rounded-inner")`
会原样输出两个类，最后生效哪个取决于生成 CSS 的先后顺序 ——
**"用 className 覆盖圆角/时长"会静默失效**，且不报错、不报警，只表现为"这里怎么调都没反应"。
（这一个坑曾经让"把圆角调小"改了三次都没生效。）

圆角从早期的 20/16/12 收到 16/12/10：工作台里有大量只有一两行文字的小面（缩略卡、分镜行、
会话条目），原来的档位在密集排布时显得"胖"。

阴影三档 `--t-shadow-1/2/3`（静止 / 悬停 / 弹窗）+ 品牌光 `--t-shadow-glow`。
材质令牌 `--t-glass-bg` / `--t-glass-border` / `--t-glass-blur` 供吸顶栏使用。

⚠️ 品牌光那一档叫 `glow` 而不是 `accent`：Tailwind v4 里 `shadow-*` 同时会匹配
`--shadow-*`（投影）与 `--color-*`（投影颜色），`shadow-accent` 会被解析成"投影颜色"。

⚠️ 分段控件里"选中那一段"的内嵌圆角写成工具类 `.r-inset`，不用 `rounded-[calc(...)]` ——
calc 里的减号会被 Tailwind 的任意值解析器当成负号。

### 9.5 动效

| 令牌 | 值 |
|---|---|
| `--t-dur-fast` / `base` / `slow` | 140ms / 220ms / 320ms |
| `--t-ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` |
| `--t-ease-spring` | 带轻微回弹，用于入场 |

工具类（`@layer utilities`）：

| 类 | 作用 |
|---|---|
| `t-tx` | 交互反馈的统一过渡（颜色 / 背景 / 描边 / 投影） |
| `t-press` | 按下时轻微回弹（`translateY(1px) scale(0.985)`） |
| `t-lift` | 卡片悬停抬升（`-2px` + 投影升级） |
| `t-grad` | 品牌渐变（CTA、品牌记号、进度指示条） |
| `t-glow` | 品牌光晕（Hero、空态） |
| `t-glass` | 毛玻璃（吸顶栏） |
| `t-skeleton` / `t-flow` / `t-lit` | 骨架微光 / 进度流动光泽 / 分镜"就绪点亮" |

**明确不做的**：不加无意义的整页入场动画、不加自动播放的背景动态。
过程反馈只在"用户会怀疑卡住"的地方出现（渲染、流式、加载）。

⚠️ 全部受 `prefers-reduced-motion: reduce` 保护（`globals.css` 末尾统一降级为瞬时），
新增动画要确认落在那块的保护范围内。

### 9.6 深色模式

- 类策略：`@custom-variant dark (&:where(.dark, .dark *))`，`dark` 写在 `<html>` 上
- **默认亮色，不跟随系统** —— 首屏按用户上次的选择渲染，避免"先亮后暗"地闪一下
- 防白闪：`layout.tsx` 的 `<head>` 里一段**同步内联脚本**在样式生效前写好 `class`；
  `<html suppressHydrationWarning>` 声明 hydration 豁免
- 读写复用 `lib/use-ui.ts` 的 `useLocalStorage`（模块级 cache + `useSyncExternalStore`），
  其 `getServerSnapshot` 天然保证首帧与服务端一致。**不引入 `next-themes`**
- 切换图标不依赖 JS 状态（`dark:hidden` / `dark:block`），所以没有图标跳变
- **暗色不是简单反相**：用深暖棕黑替代纯黑、强调色提亮一档、阴影加深而不是变淡 ——
  长时间盯着视频时页面边缘不刺眼
- 反色面（`--t-inverse`）在暗色下**反过来变亮**：轻提示、Tooltip 若写死深色会糊进背景
- 视频容器（`--t-video`）**两档都保持深色**：它是画面的一部分，不是页面的一部分
- **服务端组件自动跟随**：`share/[slug]/page.tsx` 不含客户端 hook，
  但渲染在 `<html class="dark">` 之下，语义令牌会自动算出暗色值

### 9.7 组件收敛

以下原语从各处手写里抽了出来（`components/ui/`）：

| 组件 | 取代了什么 |
|---|---|
| `IconButton` | TopBar / ArtifactPanel / RenderProgress / Dialog 关闭键等 10+ 处手写图标按钮。`label` **必填** —— 纯图标按钮漏了 `aria-label` 就是无障碍缺口 |
| `SegmentedControl` | 设置页胶囊组、侧栏多选工具条、产物 Tabs。**只做单选**；多选（科目）复用它导出的 `SEGMENTED_GROUP` / `segmentItemClass` 保持同一外观 |
| `SectionHeading` / `EmptyState` | 各页各写一套的区块标题与空态 |
| `Toast`（`ToastProvider` + `useToast`） | `Workbench` 里手写的 `note` 浮层。Provider 挂在根布局上 |
| `Card` 的 `elevation` | `flat` / `card` / `raised` / `glass` 四档，取代各处手写投影 |

按钮六变体（`primary` / `grad` / `outline` / `soft` / `ghost` / `danger`）
× 四档尺寸（`sm` 32 / `md` 40 / `lg` 48 / `icon` 40）。
同义别名（旧的 `accent` / `subtle`）已删除 —— 两个名字指一个样式，就是"组件不统一"的来源。
⚠️ `icon` 必须与 `md` 同高（40px）：同一个发送按钮在两处出现两种高度，比"差 8px"刺眼得多。

### 9.8 布局：容器与网格

这两条是"为什么同一个页面在不同窗口下长得不一样"的答案。它们被写死成两个全局类
（`globals.css` 的 `@layer utilities`），页面里**不要再自己写 `max-w-*` 或 `sm:grid-cols-*`**：

| 类 | 定义 | 用在哪 |
|---|---|---|
| `.page-shell` | `width:100%` + `max-width:72rem`(1152px) + `margin-inline:auto` + `padding-inline:1.25rem` | **所有**内容页的最外层容器 |
| `.card-grid` | `display:grid` + `gap:1rem` + `grid-template-columns:repeat(auto-fit,minmax(min(100%,20rem),1fr))` | 所有"卡片并排"的区块（套餐、能力卡、设置分组） |

**为什么卡片网格不用 `sm:grid-cols-3`**：

断点写法下，**列数由窗口决定、卡片宽度由容器决定**。两者一错位，同一个页面
在 1030px 窗口下每张 319px（近方形、好看），在 700px 窗口下每张 216px（被挤成窄高条）——
这就是当初"套餐页和样片比例不一样"的根因，来回改了三轮才定位到。

`repeat(auto-fit, minmax(min(100%, 20rem), 1fr))` 把两件事解耦：

- 列宽是**比例**（`1fr`）：跟着容器伸缩，不会被卡在某个像素值上
- 只在"一列已经不足 20rem"时才减少列数，避免被挤成竖条
- `min(100%, 20rem)` 这个夹法是必需的 —— 窄屏下 20rem 会超过容器，不加 `min` 就横向溢出
- `auto-fit` 会塌掉没有内容的空列：3 张卡放在能容纳 4 列的容器里，会均分成 3 份而不是留一个空列

⚠️ 页面里**只保留"文字行宽"的 `max-w-*`**（Hero 标题的 `max-w-2xl`、段落的 `max-w-xl`）。
那类限制是排版度量，不是版心宽度，不要和 `.page-shell` 混为一谈。

⚠️ 登录页（`max-w-xl` 的卡片）是**有意的例外**：登录是一件事一件事地做，
卡片拉到 1152px 只会让三行账号之间空出半屏。例外必须写清理由，否则下一个人会把它"改齐"。

### 9.9 文案纪律：不把实现细节写给用户

用户界面上**不要出现** `Mock` / `后端` / `接口` / `SSE` / `llm.local.json` / 环境变量名
这类词，也不要把"我们是怎么做出来的"当卖点：

| 场景 | ❌ 原来的写法 | ✅ 改后 |
|---|---|---|
| 套餐页副标题 | 渲染是纯 CPU 的活儿（LaTeX 编译 + mobject 计算）… | 三档的差别只在每月能出多少段、清晰度和导出方式 |
| 套餐页脚注 | 配额校验在后端是一个独立函数，接入真实支付时只换那一个函数 | 不确定选哪档？先去跑两道题，觉得合适再升级 |
| 侧栏空态 | 当前是 Mock 模式，没有后端可读历史会话 | 当前是演示模式，不读取历史会话 |
| 模型配置 | 改 `web/.env.local` 里的 `NEXT_PUBLIC_USE_MOCK=false` | 接入模型服务后，这里会列出所有模型档案 |
| 分享页报错 | 后端返回 502 | 这段内容暂时打不开，稍后再试试 |

技术细节留给**服务端日志**（`console.error("[share] …")`），页面只给人话。
判断标准很简单：**用户看到这句话能做什么**？做不了任何事，就不该出现在页面上。

### 9.10 侧栏收展（工作台左右栏）

侧栏用**宽度动画**收起/展开，不条件渲染（条件渲染没有过渡可言）。
做法固定为「**外层裁切 + 内层定宽**」，三个细节都不能省：

```tsx
<aside
  className={cn("t-collapse hidden shrink-0 overflow-hidden md:block", open ? "w-72" : "w-0")}
  inert={!open}
>
  <div className="h-full w-72 border-r border-border">…</div>
</aside>
```

| 细节 | 为什么 |
|---|---|
| 外层过渡 width（`.t-collapse`），内层**定宽** | 直接动侧栏自身的宽度，里面的文字会一路折行重排，看着像被"挤压"而不是"收起" |
| 分隔线画在**内层** | 外层为 0 宽时，内层连同边框一起被裁掉，不会留下一条 1px 的孤线；画在外层就会留 |
| 收起时加 `inert` | 0 宽 + `overflow-hidden` 只是"看不见"，里面的按钮仍在 Tab 焦点路径上 —— 键盘用户会跳进一个看不见的面板 |

右栏另有两处不同：

- 内层用 `ml-auto` 靠**右**贴齐：右栏展开应该像"从右边拉开窗帘"，内容锚在右边缘不动；
  锚左边的话面板里的文字会跟着一起平移
- 展开后**保持挂载**（只收宽度）：面板里的播放器默认静音（`defaultMuted: !stage0`），
  所以不会出声；好处是收起再展开时播放进度与选中项都还在，不必从头初始化

⚠️ 移动端（`< md` / `< lg`）这两栏降级成 Radix Sheet 抽屉，走的是
`.anim-sheet-left/right` 那套，与本节的宽度动画互不影响 ——
桌面那两个 `<aside>` 有 `hidden md:block` / `lg:block` 兜着，在移动端根本不参与布局。

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

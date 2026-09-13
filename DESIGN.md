# DESIGN.md — AgentCanvas

> 本文件是 AgentCanvas 前端的视觉与交互**单一事实源**,遵循 [VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md) 的 Stitch DESIGN.md 格式:把这份 markdown 放进仓库根目录,任何编码智能体都能据此产出风格一致的 UI。可视化对照页见 `design-system/preview.html`。
>
> 改编来源:`awesome-design-md` 的 `voltagent`、`vercel`、`raycast`、`linear.app` 四份分析(revision `8147538b4226ae41e2487a9179e3bcc1f68e8554`)。它们是**设计分析**,不是这些公司线上产品的规范。本项目只借用其**表面阶梯、发丝描边、字距、留白、克制的强调色与分层投影**原则,**不复制**任何品牌色、Logo、专有字体、渐变或外部素材。

---

## 1. 视觉主题与氛围 (Visual Theme & Atmosphere)

**一块长时间盯着看的仪表面板。** 不是营销页,数据是主角,界面是承托数据的坐标系。

- **密度优先**:11–13px 是正文主力尺寸,面板内边距 16–24px,控件高度 28–40px。信息密度高但不拥挤,靠发丝分隔线而非大面积留白切分区块。
- **单一强调色**:青色 `pulse` 是唯一常规强调色,只出现在品牌、主操作、焦点、当前路由与运行态;紫 `volt`、绿 `ok`、红 `bad`、琥珀 `warn` 只承担语义(费用 / 成功 / 失败 / 警告),绝不作装饰。
- **深度靠表面阶梯**:暗色下用 `void → ink → raise` 三级表面 + 1px 发丝描边构建层级;投影只负责"浮起",且只出现在浮层与抬升态。
- **暗色阶梯带色相偏移,不是灰阶**:`void` 偏紫(~235°)、`ink` 偏蓝(~232°)、`raise` 偏冷(~228°)。中性灰阶梯是"又一个暗色后台"的成因 —— 眼睛只拿到明度、拿不到材质;色相偏移让三级表面读起来是**同一块被照亮的材料**,同时让青色品牌色(~187°)成为真正的补色。
- **壳层是一块被照亮的舞台**:整个壳层只有一层合成背景 `.platform-shell`(`--stage-layers`)—— 三层出画外的光(青 / 紫 / 青)+ 细颗粒。路由页面**不再自己铺 `bg-void`**,保持透明,好让这层背景、以及坐在其上的磨砂 chrome 跨过接缝连续。颗粒层以 `overlay` 混合(50% 灰为中性),作用是**给大面积径向渐变做抖动、避免 8-bit 色带**,而不是加脏。
- **chrome 是磨砂玻璃,不是实心板**:顶栏、侧栏、移动抽屉用 `--chrome-bg` + `--chrome-blur` 把背后的光晕模糊掉。实心 chrome 会把暗色壳层变回线框图。
- **画布是例外,不是模板**:工作流画布允许发光、动效与流动虚线,因为那是执行过程的实时可视化。除此之外的地方保持安静。
- **双语原生**:文案同时存在于 zh/en 类型化词典中,排版必须同时容纳中英文 —— 这直接决定了字体选择(见 §3),不假设纯拉丁文本宽度。

## 2. 调色板与角色 (Color Palette & Roles)

主题在首帧前由 `index.html` 内联脚本决定(读 `localStorage['agentcanvas:theme']`,回退 `prefers-color-scheme`)。暗色为默认;浅色通过 `[data-theme='light']` 覆写 —— **暗色不带 `data-theme` 属性**。

### 表面阶梯 (Surface ladder)
| Token | 暗色 | 浅色 | 角色 | 对应实用类 |
|---|---|---|---|---|
| `void` | `#060810` | `#f6f8fb` | 页面底、画布底 | `bg-void` |
| `ink` | `#0b0e1c` | `#ffffff` | 面板、卡片 | `bg-ink` |
| `raise` | `#161c30` | `#eef1f8` | 内凹面:hover 行、输入框、图标底、键帽 | `bg-raise` |
| `line` | `#1e2440` | `#e1e6ef` | 1px 发丝描边与分隔线 | `border-line` |
| `line-soft` | `#14182a` | `#eef1f7` | 面板内部再低一级的分隔 | — |
| `line-strong` | `#2c3450` | `#d3dae6` | 抬升面顶部边缘 | — |
| `glass` | `rgba(10,13,26,.72)` | `rgba(255,255,255,.82)` | 浮层(模糊 18px + 饱和 1.3) | `.glass` |

`line` 比"刚好看得见"要亮一档是刻意的:暗色界面里描边一旦隐形,整套布局就只剩一锅汤,表面阶梯再精细也没有结构可依。

### 材质层 (Material layers)
| Token | 暗色 | 浅色 | 作用 |
|---|---|---|---|
| `--sheen` | 顶部 5.5% 白光渐隐(54% 处归零) | `none` | 面板/卡片/图标底"被上方照亮"的信号;比描边更耐用,换表面色也不失效 |
| `--inset-well` | `inset 0 1px 2px rgba(2,3,10,.45)` | `inset 0 1px 2px rgba(16,24,40,.06)` | 输入框、命令触发"凹进去"的唯一线索 |
| `--chrome-bg` / `--chrome-blur` | `rgba(9,11,23,.72)` / `blur(14px) saturate(150%)` | 白 82% / `blur(14px) saturate(140%)` | 顶栏、侧栏、抽屉的磨砂玻璃 |
| `--stage-layers` | 颗粒 + 3 层出画外的光 | `none` | 整个壳层唯一的一层合成背景 |
| `--stage-blend` | `overlay, normal, normal, normal` | `normal` | 颗粒以 50% 灰为中性,只调制亮度、不冲淡近黑 |

**浅色主题刻意不解锁任何材质层**:白面板本身就是"被照亮的表面",光晕、颗粒和顶部渐变只会把它弄脏。所有材质增强都必须包在 `:root:not([data-theme='light'])` 里 —— 浅色下 `--sheen` 为 `none`、`--stage-layers` 为 `none`,同一份 CSS 自动退化成原来的平面观感。

### 文本 (Text)
| Token | 暗色 | 浅色 | 角色 |
|---|---|---|---|
| `ice` | `#eaeefb` | `#161c2a` | 主文本、标题、数据值 |
| `ghost` | `#909ab2` | `#3f4654` | 次要文本、标签、图标默认态 |

### 语义强调 (Accent)
| Token | 暗色 | 浅色 | 角色 |
|---|---|---|---|
| `pulse` | `#22d3ee` | `#155e75` | 品牌青:焦点环、当前路由、运行态、主按钮 |
| `volt` | `#8b5cf6` | `#6d28d9` | 模型 / 费用 |
| `ok` | `#34d399` | `#065f46` | 成功、完成 |
| `bad` | `#fb7185` | `#9f1239` | 失败、错误 |
| `warn` | `#fbbf24` | `#92400e` | 警告、配额水位、未定价 |

浅色下的强调色被刻意压深,以便在各自 10% 淡色徽章底(`bg-ok/10`)上仍满足 WCAG AA。**改动强调色时必须同时验证两种主题下的对比度。**

## 3. 排版规则 (Typography Rules)

### 字体家族
- **Noto Sans SC**(可变字重 100–900)—— 同时承担展示与 UI 文本。选择它的理由不是审美偏好:本项目是中英混排,同一句话里不能出现两套互不兼容的字形。Noto Sans SC 同时提供完整的简体中文与拉丁字形集,因此 `--font-sans` 可以是**单一字族**,不需要为 CJK 单独挂回退栈。
- **JetBrains Mono** —— 只承担遥测、标识符、代码、键帽与数值列,不做氛围装饰。

两者都注册在 `@theme` 里。`--font-sans` 同时改写 Tailwind preflight 的 `html` 默认字体,因此**没有显式指定字体族的文字也会继承 Noto Sans SC**,不会掉回系统字体栈。

### 字距与特性
| Token | 值 | 用途 |
|---|---|---|
| `--tracking-display` | `-0.018em` | 大字号展示标题 |
| `--tracking-title` | `-0.006em` | 所有 `font-display` 标题(全局默认) |
| `--tracking-label` | `+0.08em` | 全大写小标签、eyebrow、导航分组标题 |

- 负字距只对拉丁有效益,**对 CJK 极易过紧**,所以展示字号只收到 `-0.018em`;小标签反向放开到 `+0.08em`,靠"紧 → 松"的对比建立层级。
- 数值列使用 `type-data`(`font-variant-numeric: tabular-nums`)或 `font-mono`,保证纵向对齐;正文不用等宽数字。

### 层级
| 角色 | 类 / 规格 | 尺寸 | 字重 |
|---|---|---|---|
| 页面主标题 | `workspace-display-title` | 24px → 30px(≥640px) | 600 |
| 路由页标题 | `workspace-page-title` | 14px → 16px(≥640px) | 600 |
| 区块标题 | `workspace-section-title` | 13px | 600 |
| 统计数值 | `workspace-stat-value` | 20px → 26px(≥640px),`font-mono` | 500 |
| 正文 / 控件 | `text-xs` / `text-[11px]` | 11–12px | 400–500 |
| 小标签 | `workspace-eyebrow` / `workspace-stat-label` | 10–11px | 500 |
| 导航分组标题 | `workspace-nav-section` | 10px + 全大写 + `tracking-label` | 500 |

展示到正文保持单一字线(600 → 400),不用 700+ 做展示。

## 4. 组件样式与状态 (Component Stylings)

### 按钮
| 组件 | 规格 |
|---|---|
| `workspace-primary-button` | `bg: pulse` / `color: void`,`radius 8px`(药丸化禁止),内边距 `9px 16px`,最小高度 40px,顶部 `inset 0 1px` 高光;hover 提亮 + `0 4px 14px -6px` 色调投影;active `translateY(1px)`;disabled `opacity .5` |
| `workspace-icon-button` | 36×36,`radius 8px`,默认 `ghost`;hover 变 `ice` + 9% 中性底;active `translateY(1px)` |
| `workspace-command-trigger` | 36px(仅图标)→ `14rem`(≥640px)→ `18rem`(≥1024px);`raise` 底 + `line` 描边;hover 描边转 38% `pulse`。**读起来像输入框,不像按钮** |
| `workspace-text-link` | 11px,`ghost`;hover 变 `pulse`,尾随箭头 `translateX(2px)` |

### 导航
- `workspace-nav-link`:最小高度 38px,`radius 8px`,`5px 8px` 内边距;hover 7% 中性底,图标底变为 `raise` + `line` 描边。
- 图标包在 `workspace-nav-icon`(26×26,`radius 6px`)里 —— 当前态需要比"文字变色"更强的地方承载"已选中"。
- 当前路由 `aria-current='page'` → `pulse` 9% 底色 + 22% 描边 + `inset 2px 0 0 pulse` 左侧标条,图标底转 `pulse` 16% + 图标转 `pulse`,行尾一枚 1px 圆点。
- `workspace-nav-section`:mono 全大写 10px + `tracking-label`,右侧接一条 `flex: 1` 的 1px `line` 发丝线 —— **分组标题本身就是分隔线**,不再额外画 `<hr>`。
- 收起态(`[data-compact='true']`):居中图标,文字转 `sr-only`,发丝线隐藏。
- 侧栏底部 `workspace-workspace-card`:`raise` 底 + `line` 描边 + mono 单字标记,承载工作空间标识;旁边是收起/展开按钮。

### 面板与容器
- `workspace-panel` / `overview-panel`:`ink` 底 + 1px `line` 描边 + `radius 12px` + `--elev-2`。暗色额外加一条 `inset 0 1px` 顶部发丝高光(参考系统的"发丝上边缘"),浅色不加。
- `workspace-panel-lift`:只有**本身可交互**的面板才允许 hover `translateY(-1px)`。非交互面板不动。
- 统计带(`overview-metric`):整块面板用 1px 分隔线切成 2 列(≥1280px 4 列),内部是 `workspace-stat-label` + `workspace-stat-icon`(28×28 `raise` 图标底)+ `workspace-stat-value` + `workspace-stat-detail`,不做卡片套卡片。
- 列表行 `workspace-workflow-row` / `workspace-shortcut`:左右负外边距撑满面板,行首 `workspace-row-glyph`(36×36 `raise` 图标底,hover 时描边转 35% `pulse`),只在 hover 时给 6% 中性底。

### 标签与键帽
- `workspace-badge`:22px 高,`raise` 底 + `line` 描边 + `radius 6px`,11px。用于周期、计数、状态摘要。
- `workspace-keycap`:20px 高,`radius 5px`,`raise` 描边且**底边更深一档**(模拟键帽侧壁),mono 10px。用于快捷键提示(命令面板显示 `⌘K` / `Ctrl K`,按平台判定)。

### 输入与焦点
- `field-input`:`void` 70% 底 + `line` 描边 + `radius 8px`;focus 时描边转 60% `pulse`。
- 全局焦点环:`outline: 2px solid var(--focus-ring); outline-offset: 2px`,作用于 `a / button / input / select / textarea / [tabindex]` 的 `:focus-visible`。画布节点额外定义 `:focus-visible` 焦点环。

### 状态语义
运行中 / 已完成 / 失败 / 警告各自有专属色与动效(节点扫光、边流动虚线、token 光标)。**区分"加载中""请求失败""空数据""未定价"四种状态** —— 不可用数据显示 `—`,不显示 0。

## 5. 布局原则 (Layout Principles)

- **间距基数 4px**:`4 / 8 / 12 / 16 / 24 / 32`。面板内边距 `16px`(移动)→ `24px`(≥640px)。
- **壳层几何(被端到端测试锁定,改动前先看 `frontend/e2e/`)**:
  - 顶栏 **56px**(`h-14`),左侧是路由面包屑(`工作空间 › 当前目的地`),右侧是命令触发 + 分隔线 + 账号入口。
  - 桌面侧栏展开 **224px**(`w-56`)/ 收起 **72px**(`w-[72px]`),内容区独立滚动。
  - `<768px` 侧栏转为**模态抽屉**(`role="dialog"`、焦点圈定、Escape 关闭、路由变化自动关闭);抽屉里关闭按钮必须是**第一个可聚焦元素**,以便焦点陷阱从它回卷到最后一个链接。
- **内容宽度**:概览等阅读型页面限制在 `1440px` 并居中;数据表与画布铺满。
- **留白哲学**:暗色底本身就是留白。区块之间用 `line` 分隔线或提升到 `ink` 面板来切分,而不是靠大面积空白。
- 触控目标 ≥ 40px;移动端主要操作不小于 44px。

## 6. 深度与层级 (Depth & Elevation)

| 层级 | 表现 |
|---|---|
| 0 平面 | 页面底 `void` 上的正文与标题,无描边无投影 |
| 1 面板 | `ink` + `--sheen` + 1px `line` + `--elev-2`;暗色再加 `inset 0 1px` 顶部高光 |
| 2 抬升 | hover 态 `translateY(-1px)` + `--elev-3`(仅交互面板) |
| 3 浮层 | `glass`(模糊 18px + 饱和 1.3)+ 1px `glass-border` + `--elev-4` |
| 4 模态 | 浮层 + `void/80~85` 遮罩,焦点圈定 |
| 5 焦点 | 2px `focus-ring` 外描边 |

投影阶梯(`:root` 里定义,`[data-theme='light']` 整套覆写为低不透明度):

| Token | 结构 |
|---|---|
| `--elev-1` | 单层接触投影 `0 1px 2px -1px` |
| `--elev-2` | `0 1px 1px` + `0 2px 4px -2px` + `0 12px 28px -16px`(三层小偏移) |
| `--elev-3` | `0 2px 6px -2px` + `0 16px 36px -16px` |
| `--elev-4` | `0 1px 1px` + `0 12px 24px -8px` + `0 36px 72px -24px` |

**规则:堆叠多个小偏移 + 低透明度环境投影,禁止单层重投影。** `--shadow-card` 是 `--elev-4` 的别名,供浮层消费。

**暗色投影用 `rgba(2,3,10,…)` 而不是纯黑**:纯黑投影落在蓝紫底上会读成一层灰雾,带着 `void` 色相的投影才像阴影。

## 7. 该做与不该做 (Do's and Don'ts)

**Do**
- 新增区块时先决定它落在哪一级表面(`void` / `ink` / `raise` / `glass`),再写代码。
- 用分隔线、对齐、字重和**表面阶梯**建立层级。
- 强调色只用于品牌、主操作、焦点、当前态与语义状态。
- 新增的"材质"增强(光晕、颗粒、渐变、发光)一律包在 `:root:not([data-theme='light'])` 里,浅色主题保持平面观感。
- 想加一层背景光/颗粒时,加进 `--stage-layers`,并**同步给 `--stage-repeat` / `--stage-size` / `--stage-blend` 各加一个值** —— 四张列表是 1:1 对位的,漏一个就会让后面的层错位。
- 数值用 `font-mono` 或 `tabular-nums`;标识符保持大小写原样。
- 新文案同时写入 zh / en 类型化词典(缺一边就是编译错误)。
- 新交互必须覆盖 hover、`:focus-visible`、禁用态、加载态与 `prefers-reduced-motion`。
- 改视觉优先改 `src/index.css` 的 token 与 `src/features/shell/workspace.css` 的 `workspace-*` 原语 —— 它们被所有页面共用。

**Don't**
- 不要用大片投影或发光制造层级(画布节点与运行态除外)。
- 不要把主按钮做成药丸,不要引入第二种装饰性强调色。
- 不要用嵌套卡片包裹已有面板(参考系统明确反对)。
- 不要在浅色主题下直接复用暗色强调色(对比度会失守)。
- 不要在路由页面根节点上写 `bg-void` —— 那会盖掉壳层的舞台背景,磨砂 chrome 失去可采样物,整块背景也就断了。
- 不要手写 `-webkit-backdrop-filter`。构建会自动补前缀,手写两个会让压缩器把它们视为同一属性、只留带前缀的那个,Firefox 直接失效。
- 不要给 CJK 文案施加 `-0.02em` 以上的负字距。
- 不要把不可用数据渲染成 0 或编造健康状态文案。
- 不要为了视觉效果改动画布几何、连线与执行语义。
- **不要改壳层几何与无障碍契约**:顶栏 56px、侧栏 224/72px、导航 `aria-label`、`aria-current`、`data-testid`、`data-metric` 嵌套深度都被 `frontend/e2e/` 断言锁定。

## 8. 响应式行为 (Responsive Behavior)

| 断点 | 变化 |
|---|---|
| ≥1280px (`xl`) | 统计带 4 列;主次面板 1.7 : 1 双栏;命令触发 18rem |
| ≥1024px (`lg`) | 统计带 4 列,双栏收窄 |
| ≥768px (`md`) | 桌面侧栏常驻(品牌块回到侧栏顶部);面包屑出现;移动抽屉禁用 |
| ≥640px (`sm`) | 页面标题升到 16px;展示标题升到 30px;面板内边距 24px;命令触发显示文字 + 键帽;按钮显示文字标签 |
| <768px | 侧栏 → 模态抽屉;顶栏保留紧凑品牌标记 |
| <720px | 画布缩略图隐藏 |
| 390px | 单列;表单单列;无横向溢出 |

折叠策略:卡片多列 → 单列;工具栏次要按钮收成仅图标;标题保持单行截断并保留 `title` 提示。

**画布命令栏是折叠策略的例外**:它承载的动作远多于平台顶栏(导航 / 名称 / 撤销重做 / 布局 / 对象 / 剪贴板 / 协作 / 六个面板 / 保存 / 运行),任何桌面宽度都放不下单行。所以它**允许换行**(`min-h-14 flex-wrap`,放得下仍是 56px),而不是把右侧动作压成 0 宽裁掉 —— 裁切的代价是主操作「运行」直接不可达。主操作组(`保存` / `运行`)额外设为 `shrink-0` 且**不放进**横向滚动容器,保证任何宽度下都可点。这条不变式由 `frontend/e2e/agentcanvas.spec.ts` 在 1280×720 上点击「运行」来锁定。

## 9. 智能体提示指南 (Agent Prompt Guide)

改动 UI 时按以下顺序工作:

1. **先读本文件**与 `design-system/MASTER.md`;两者冲突时以本文件为准并同步修正 MASTER。
2. **从 token 入手**:优先改 `src/index.css` 的 `:root` / `[data-theme='light']` / `@theme` 与 `src/features/shell/workspace.css` 的 `workspace-*` 原语,而不是在单个页面里堆一次性类名。
3. **一次只动一个组件**;新变体作为新的 `workspace-*` 条目加入,不要就地改写既有原语语义。
4. **配色速查**:底 `void` / 面板 `ink` / 内凹 `raise` / 描边 `line` / 主文本 `ice` / 次文本 `ghost` / 强调 `pulse`;语义 `ok` `bad` `warn` `volt`。
5. **基类速查**:展示标题 `workspace-display-title`,路由标题 `workspace-page-title`,区块标题 `workspace-section-title`,面板 `workspace-panel`,主按钮 `workspace-primary-button`,图标按钮 `workspace-icon-button`,徽章 `workspace-badge`,键帽 `workspace-keycap`,eyebrow `workspace-eyebrow`。
6. **收尾必跑**(本机 `pnpm` 已损坏,用直连方式):
   ```bash
   cd frontend
   node node_modules/typescript/bin/tsc --noEmit
   node node_modules/vite/bin/vite.js build && node scripts/check-bundle-budget.mjs
   node node_modules/@playwright/test/cli.js test \
     e2e/platform-shell.spec.ts e2e/overview-experience.spec.ts e2e/theme.spec.ts e2e/language.spec.ts \
     --output=pw-output-tmp
   ```
   Playwright 会在启动时清空 `outputDir`,必须显式传一个全新的空目录,否则整个 run 会在跑测试前就被安全删除守卫拦下。
7. **验收标准**:桌面 / 390px、浅色 / 深色、`prefers-reduced-motion`、键盘焦点、axe serious/critical 零违规,且 bundle gzip < 120 KiB、画布 `dragPaintP95 < 100ms` 不回归。

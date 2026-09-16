---
name: self-improvement
description: |
  记录并固化开发中反复踩的坑：把失败、用户纠正、知识缺口写进项目根目录的 .learnings/，用 Pattern-Key 去重统计复发次数，达到阈值后晋升为永久规则。用于「记一下这个坑」「别再犯同样的错」「把这条教训存起来」「回顾一下 learnings」「晋升规律」等请求；也在命令非预期失败、用户纠正你、发现知识过时时主动使用。适配 DSH：晋升目标是 AGENTS.md，不依赖任何 hook 或外部 CLI。
---

# 自我改进（self-improvement）

把开发中踩的坑记成结构化条目，让同一个错误不在未来重复。

**核心是这个闭环**：记录 → 去重计数 → 达标晋升 → 从日志里毕业。没有晋升，日志就只是垃圾桶；没有去重，晋升阈值就永远达不到。

本技能是 OpenClaw 版 `pskoett/self-improving-agent` 的 DSH 适配版。核心机制（Pattern-Key 去重、复发计数、三级晋升）原样保留；平台专属部分（OpenClaw hook、`openclaw skills` CLI、`SOUL.md`/`TOOLS.md`、跨会话 `sessions_send`）已替换为 DSH 等价物。

## 何时记录

命中以下任一情况，**当场记录**，不要攒到会话结束：

| 情况 | 落到哪个文件 | 分类 |
|---|---|---|
| 命令 / 操作非预期失败 | `ERRORS.md` | — |
| 用户纠正你（「不对」「应该是」「你搞错了」） | `LEARNINGS.md` | `correction` |
| 用户想要不存在的能力 | `FEATURE_REQUESTS.md` | — |
| 外部 API / 工具失败 | `ERRORS.md` | — |
| 发现自己的知识过时或错误 | `LEARNINGS.md` | `knowledge_gap` |
| 为反复出现的任务找到更好做法 | `LEARNINGS.md` | `best_practice` |

**不要记录**：密钥、token、私钥、环境变量全文、完整源码或配置。用短摘要或脱敏片段，不要贴原始命令输出。这条是硬性的。

## 初始化

第一次使用前，确保项目根目录有 `.learnings/`。**不要覆盖已有文件**：

```bash
mkdir -p .learnings
[ -f .learnings/LEARNINGS.md ] || printf '# Learnings\n\n纠正、洞察、知识缺口。\n\n**分类**：correction | insight | knowledge_gap | best_practice\n\n---\n' > .learnings/LEARNINGS.md
[ -f .learnings/ERRORS.md ] || printf '# Errors\n\n命令失败与集成错误。\n\n---\n' > .learnings/ERRORS.md
[ -f .learnings/FEATURE_REQUESTS.md ] || printf '# Feature Requests\n\n用户请求但不存在的能力。\n\n---\n' > .learnings/FEATURE_REQUESTS.md
```

若已在已有项目工作，先检查 `.learnings/` 是否已存在，避免重复初始化。

## 记录格式

### 学习条目 → `LEARNINGS.md`

```markdown
## [LRN-20250115-001] correction

**Logged**: 2025-01-15T10:30:00Z
**Priority**: low | medium | high | critical
**Status**: pending
**Area**: frontend | backend | infra | tests | docs | config

### Summary
一句话说清学到了什么

### Details
完整上下文：发生了什么、错在哪、正确的是什么

### Suggested Action
具体要做的修复或改进

### Metadata
- Source: conversation | error | user_feedback
- Related Files: path/to/file.ext
- Tags: tag1, tag2
- See Also: LRN-20250110-001
- Pattern-Key: area.symptom
- Recurrence-Count: 1
- First-Seen: 2025-01-15
- Last-Seen: 2025-01-15

---
```

### 错误条目 → `ERRORS.md`

```markdown
## [ERR-20250115-A3F] command_or_skill_name

**Logged**: 2025-01-15T10:30:00Z
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
简述什么失败了

### Error
```
实际错误信息或输出
```

### Context
- 尝试的命令 / 操作
- 使用的输入或参数
- 相关环境细节
- 关键输出的摘要或脱敏片段

### Suggested Fix
如果可识别，什么可能解决它

### Metadata
- Reproducible: yes | no | unknown
- Related Files: path/to/file.ext
- See Also: ERR-20250110-001
- Pattern-Key: area.symptom
- Recurrence-Count: 1
- First-Seen: 2025-01-15
- Last-Seen: 2025-01-15

---
```

### 功能请求 → `FEATURE_REQUESTS.md`

```markdown
## [FEAT-20250115-002] capability_name

**Logged**: 2025-01-15T10:30:00Z
**Priority**: medium
**Status**: pending
**Area**: config

### Requested Capability
用户想做什么

### User Context
为什么需要它，在解决什么问题

### Complexity Estimate
simple | medium | complex

### Suggested Implementation
如何实现，可能扩展什么

### Metadata
- Frequency: first_time | recurring
- Related Features: existing_feature_name
- Pattern-Key: area.symptom

---
```

### ID 生成

格式 `TYPE-YYYYMMDD-XXX`：

- `TYPE`：`LRN`（学习）/ `ERR`（错误）/ `FEAT`（功能）
- `YYYYMMDD`：当天日期
- `XXX`：顺序号或随机 3 字符

例：`LRN-20250115-001`、`ERR-20250115-A3F`、`FEAT-20250115-002`

## Pattern-Key：去重的关键

**这是整个技能最重要的部分。** 没有它，同一个坑会被记成十条不同的条目，`Recurrence-Count` 永远是 1，晋升规则永远不触发。

**格式**：`area.symptom`，恰好两级、小写、连字符分隔。

**规则**：

1. **先查再建**。记录前先 `grep -ri "Pattern-Key:" .learnings/`，近似匹配优于新建 key
2. **人工条目一个 key 一个条目**
3. **谨慎新增 area**，只有在多个条目会共享时才加
4. **不要用泛化 key**（`runtime.error`、`runtime.failure`）——那等于「未分类」，triage 时要换成具体的

**key 里不放文件名、版本号、主机名** —— 保持通用才能复现。

| Area | 范围 | 示例 key |
|---|---|---|
| `api` | 外部 API / 服务行为 | `api.rate-limit`、`api.schema-mismatch`、`api.missing-endpoint` |
| `auth` | 凭证、token、权限 | `auth.token-expired`、`auth.missing-scope` |
| `build` | 编译、打包、CI | `build.type-error`、`build.missing-artifact` |
| `config` | 配置文件、环境变量 | `config.missing-env`、`config.invalid-json` |
| `deps` | 包管理器、依赖 | `deps.module-not-found`、`deps.version-conflict` |
| `fs` | 文件系统 | `fs.no-such-file`、`fs.permission-denied` |
| `net` | 网络连通性 | `net.connection-refused`、`net.timeout` |
| `runtime` | 语言 / 运行时错误 | `runtime.type-error`、`runtime.python-exception` |
| `shell` | Shell / CLI 机制 | `shell.command-not-found`、`shell.nonzero-exit` |
| `vcs` | Git 与版本控制 | `vcs.fatal-error`、`vcs.merge-conflict` |

## 复发检测

记录相似内容时：

1. **先按 key 搜**：`grep -n "Pattern-Key: area.symptom" .learnings/*.md` —— 这是默认去重检查，能抓到换词写法的同一问题
2. **回退到关键词搜**：针对没有 key 的老条目
3. **合并而非重复**：命中就更新原条目 —— 递增 `Recurrence-Count`、更新 `Last-Seen`、加 `**See Also**` 链接
4. **持续复发就提升 `Priority`**
5. **考虑系统性修复**：反复出现通常意味着
   - 知识缺失 → 晋升到 `AGENTS.md`
   - 缺少自动化 → 加进工作流规则
   - 架构问题 → 建技术债记录

## 晋升到永久规则

教训只有真正广泛适用（不是一次性修复）才晋升，让每个会话都继承它。

### 什么时候晋升

- 教训跨多个文件 / 功能适用
- 任何贡献者（人或 AI）都该知道
- 能防止重犯
- 记录了项目特有约定

### 晋升目标（DSH）

DSH 读取 `AGENTS.md`（兼容 `CLAUDE.md`），也存在用户全局的 `$DSH_HOME/AGENTS.md`。按教训的作用范围选目标：

| 目标 | 放什么 | 例 |
|---|---|---|
| 项目 `AGENTS.md` | 工作流、约定、项目特有规则 | 「修改 API 端点后必须重新生成 TS 客户端」 |
| 用户全局 `AGENTS.md` | 跨项目通用的工具坑与偏好 | 「git push 前需先配置认证」 |

> 原 OpenClaw 版把教训拆进 `SOUL.md`（行为）/ `TOOLS.md`（工具）/ `AGENTS.md`（工作流）三处。DSH 没有这套约定，**统一写进 `AGENTS.md`**，用分节标题区分即可。

### 晋升规则（硬阈值）

**三个条件同时满足才晋升**：

- `Recurrence-Count >= 3`
- 在**至少 2 个不同任务**中出现过
- 发生在 **30 天窗口**内

### 怎么晋升

1. **提炼**成一条简洁的规则或事实
2. **写入** `AGENTS.md` 对应小节（文件不存在就创建）
3. **回填原条目**：
   - `**Status**: pending` → `**Status**: promoted`
   - 加一行 `**Promoted**: AGENTS.md`

**写短预防规则（动手前 / 动手时该做什么），不要写长篇事故复盘。**

### 晋升示例

**教训**（冗长）：

> 项目用 pnpm workspaces。尝试 `npm install` 失败，锁文件是 `pnpm-lock.yaml`。必须用 `pnpm install`。

**晋升到 `AGENTS.md`**（精炼）：

```markdown
## 构建与依赖
- 包管理器是 pnpm（不是 npm）—— 使用 `pnpm install`
```

---

**教训**（冗长）：

> 修改 API 端点时必须重新生成 TypeScript 客户端，忘记会导致运行时类型不匹配。

**晋升到 `AGENTS.md`**（可执行）：

```markdown
## API 变更后
1. 重新生成客户端：`pnpm run generate:api`
2. 检查类型错误：`pnpm tsc --noEmit`
```

## 状态流转

`pending` → `in_progress` → `resolved` | `wont_fix` | `promoted` | `promoted_to_skill`

解决时更新条目：

```markdown
### Resolution
- **Resolved**: 2025-01-16T09:00:00Z
- **Commit/PR**: abc123 或 #42
- **Notes**: 做了什么
```

## 技能抽取

教训足够有价值时，转成可复用 skill。**满足任一条件即可**：

| 条件 | 说明 |
|---|---|
| **复发** | 有 2+ 个相似问题的 `See Also` 链接 |
| **已验证** | `Status` 为 `resolved` 且有可用修复 |
| **非显而易见** | 需要实际调试才发现的 |
| **广泛适用** | 不限于特定项目 |
| **用户指定** | 用户说「把这个存成技能」 |

### 质量门（抽取前逐项确认）

- [ ] 方案经过测试且可用
- [ ] 描述脱离原始上下文也能看懂
- [ ] 代码示例自包含
- [ ] 没有项目特有的硬编码值
- [ ] 命名符合规范（小写、连字符）

抽取后把原条目 `Status` 设为 `promoted_to_skill`，并加 `**Skill-Path**`。

## 定期回顾

**在自然断点回顾 `.learnings/`**：

- 开始新的大任务之前
- 完成一个功能之后
- 工作区域有历史教训时
- 活跃开发期每周一次

### 快速状态检查

```bash
# 待处理条目数量
grep -h "Status\*\*: pending" .learnings/*.md | wc -l

# 列出高优先级待处理项
grep -B5 "Priority\*\*: high" .learnings/*.md | grep "^## \["

# 找某个领域的教训
grep -l "Area\*\*: backend" .learnings/*.md

# 找达到晋升阈值的条目（Recurrence-Count >= 3）
grep -rh "Recurrence-Count: [3-9]$" .learnings/*.md
grep -rhE "Recurrence-Count: [0-9]{2,}$" .learnings/*.md
```

### 回顾动作

- 解决已修复的条目
- 晋升达标的教训
- 关联相关条目
- 升级持续复发的问题

### 每周卫生

- 归档已 `resolved` 超过 90 天的条目
- 关闭已实现或已放弃的 `FEATURE_REQUESTS`
- 确认晋升过的规则确实写进了 `AGENTS.md`

## 优先级

| 优先级 | 何时使用 |
|---|---|
| `critical` | 阻塞核心功能、有数据丢失风险、安全问题 |
| `high` | 影响显著、影响常见工作流、复发问题 |
| `medium` | 影响中等、有变通方案 |
| `low` | 轻微不便、边缘情况、锦上添花 |

## 最佳实践

1. **立即记录** —— 刚出问题时上下文最新鲜
2. **写得具体** —— 让未来的 agent 能快速看懂
3. **包含复现步骤** —— 尤其是错误
4. **关联相关文件** —— 便于修复
5. **给具体修复建议** —— 不要只写「调查一下」
6. **分类保持一致** —— 便于过滤
7. **达标就晋升** —— 别犹豫
8. **定期回顾** —— 陈旧的学习会失去价值

## 版本管理与卸载

`.learnings/` 有两种处理方式：

**保持本地**（按开发者）：

```gitignore
.learnings/
```

**纳入仓库**（团队共享）：不要加进 `.gitignore`，教训成为共享知识。

**混合**（跟踪模板、忽略条目）：

```gitignore
.learnings/*.md
!.learnings/.gitkeep
```

卸载时注意：`.learnings/` 是**用户数据**（删除前先看），已晋升进 `AGENTS.md` 的内容**不会自动移除**，需要手动删。

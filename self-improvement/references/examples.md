# 实例

用于校准记录粒度、去重判断和晋升写法。示例不是模板，照抄结构即可，内容要贴合实际情况。

## 例 1：首次记录一个环境坑

**场景**：在某项目跑 `npx` 报 EPERM，因为 npm 缓存在沙箱外。

**记录到 `ERRORS.md`**：

```markdown
## [ERR-20260115-001] npx

**Logged**: 2026-01-15T14:20:00Z
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
`npx` 在受限环境下失败：npm 缓存目录不可写。

### Error
```
npm error code EPERM
npm error syscall open
npm error path <npm-cache>\_cacache\tmp\***
```

### Context
- 命令：`npx -y skills find "<query>"`
- 环境：DSH 桌面端，文件沙箱为 workspace-write
- npm 缓存位于用户目录，在会话工作区之外，不在可写范围内

### Suggested Fix
不要依赖 `npx`。需要装 skill 时改用直接下载文件的方式（`web_fetch` 取源文件后本地写入），
或请用户在其正常终端中执行。

### Metadata
- Reproducible: yes
- Related Files: —
- Pattern-Key: tool.sandbox-denied
- Recurrence-Count: 1
- First-Seen: 2026-01-15
- Last-Seen: 2026-01-15

---
```

**要点**：错误信息贴了关键几行，没有贴完整输出；`Pattern-Key` 用的是 `tool.sandbox-denied` 而非 `deps.npm-error` —— 根因是沙箱写入限制，不是 npm 本身。

## 例 2：第二次遇到同类问题（去重）

**场景**：同一台机器上 `curl` 拉 GitHub raw 报 exit 35（TLS 握手失败）。

**先搜**：

```bash
grep -ri "Pattern-Key: tool.sandbox-denied" .learnings/
grep -ri "对外网络\|TLS\|握手" .learnings/
```

**判断**：根因不同 —— 一个是本地写权限，一个是出站 TLS 被拦。**不算同一 Pattern-Key**，新建 `net.tls-handshake`。

**若根因相同**则应合并，例如：

```markdown
- Pattern-Key: tool.sandbox-denied
- Recurrence-Count: 2
- First-Seen: 2026-01-15
- Last-Seen: 2026-01-16
- See Also: ERR-20260115-001
```

**要点**：去重看的是**根因**，不是表面症状。用同一个工具不等于同一个问题。

## 例 3：达到阈值，晋升

**场景**：`net.tls-handshake` 在三个不同任务里出现，间隔都在 30 天内。

**条目状态**：`Recurrence-Count: 3`，`Last-Seen` 距首次 < 30 天，跨 3 个不同任务 → **三条件齐备，晋升**。

**写入项目 `AGENTS.md`**：

```markdown
## 网络与下载

- 本机 `curl` / PowerShell 直连 GitHub raw 会出现 TLS 握手失败（exit 35）。
  需要拉取远程文件时，使用 `web_fetch` 工具，不要用 shell 的 curl/wget。
```

**回填原条目**：

```markdown
**Status**: promoted
...
**Promoted**: AGENTS.md
```

**要点**：晋升后写的是**预防规则**（下次该怎么做），不是事故复盘。原条目保留，只改状态。

## 例 4：反复出现暴露的是架构问题

**场景**：`tool.path-outside-workspace` 出现了五次，每次都是想往 `$DSH_HOME` 或用户目录写文件。

**处理**：这不该晋升成一条「记得申请权限」的规则 —— 那是治标。复发五次说明是**机制问题**：

1. 记录一条 `LEARNINGS.md`，`Area: config`，说明「技能安装类操作默认会越出工作区」
2. 在 `AGENTS.md` 里写清**哪些目录可写、越界时该怎么走审批**，而不是每次临场判断
3. 若影响面大，考虑是否值得做成一个专用技能

**要点**：`Recurrence-Count` 高不只是提升 `Priority` 的信号，更是**问「为什么它能反复发生」**的信号。

## 反例：不该记录的

- 密钥、token、私钥、环境变量全文、完整源码或配置 —— **一律不记**，只记脱敏摘要
- 用户一次性的偏好调整（「这次用英文回答」）—— 不是教训
- 尚未验证的猜测（「可能是网络问题」）—— 要么查清再记，要么标 `Reproducible: unknown` 并说明未验证
- 平台工具的正常行为差异 —— 除非它真的造成了返工

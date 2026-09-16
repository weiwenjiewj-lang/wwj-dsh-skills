# wwj-dsh-skills

DeepSeek Harness（DSH）技能包集合：**8 个技能**，每个技能一个目录，目录内自带 `README.md` 说明用途与来源。

配套仓库：[wwj-dsh-plugins](https://github.com/weiwenjiewj-lang/wwj-dsh-plugins)（插件与 profile 配置）

---

## 技能清单

| 技能 | 用途 | 来源 | 体积 |
|---|---|---|--:|
| [`find-skills`](./find-skills/) | 教 agent 在开放技能生态（skills.sh）里检索、评估并安装 skill，自带安装量与来源可信度判据 | Agent Skills 生态 | 0.01 MB |
| [`grill-me`](./grill-me/) | 拷问式计划打磨的轻量入口，直接转调 `grilling` | AI Hero 技能体系 | <0.01 MB |
| [`grilling`](./grilling/) | 把计划当设计树，按轮次追问"前沿"问题，每问附推荐答案，问透再动手 | AI Hero 技能体系 | <0.01 MB |
| [`neat-freak`](./neat-freak/) | 收尾对账：把项目文档、规则文件、记忆、工作区残留与代码真实行为对齐，让下一轮从唯一正确答案出发 | 本机 DSH 适配版 | 0.04 MB |
| [`ponytail`](./ponytail/) | 最懒解法：先问这任务要不要存在（YAGNI），标准库优先、原生优先、一行优于五十行；支持 lite / full / ultra | 上游声明 MIT | 0.01 MB |
| [`qu-ai-wei`](./qu-ai-wei/) | 中文去 AI 味重写：保留事实、证据强度、语体与作者声口的前提下重建句子与段落，不做局部换词 | 改编自 blader/humanizer | 0.05 MB |
| [`self-improvement`](./self-improvement/) | 把踩过的坑写成结构化条目（记录 → Pattern-Key 去重计数 → 达阈值晋升进 AGENTS.md），让同一个错不重复 | OpenClaw 版 self-improving-agent 的 DSH 适配 | 0.02 MB |
| [`shuitu-writing-skill`](./shuitu-writing-skill/) | 生产建设项目水土保持方案（报告书 / 报告表）的编制、续写、审查与校核，按水利部 2026 版模板逐节点产出；内置 371 篇法规标准范例知识库 | 自研 | 452 MB |

> 技能识别依赖目录内的 `SKILL.md`（YAML frontmatter 的 `name` 与 `description`）。本仓库 8 个目录全部具备。
>
> 本机技能目录里原有的 `luban-skill-pro`（鲁班.Skill，来自插件市场，作者署名「智慧半岛」）**不在本仓库**：它在本机已被停用（`SKILL.md` 被移入 `.trash/`），DSH 实际加载的技能就是上表这 8 个。

---

## 安装

每个技能目录都是自包含的：整个目录复制到 DSH 的 skills 目录即可，目录名保持不变。

```powershell
$dest = Join-Path $env:APPDATA 'dsh-desktop\harness\skills'
New-Item -ItemType Directory -Force -Path $dest | Out-Null

# 单个技能
Copy-Item -Recurse -Force '.\neat-freak' $dest

# 全部技能（shuitu-writing-skill 有 452 MB，按需选择）
Get-ChildItem -Directory | ForEach-Object { Copy-Item -Recurse -Force $_.FullName $dest }
```

重启 DSH 后，会话技能目录里即可看到。

技能识别依赖目录内的 `SKILL.md`（YAML frontmatter 中的 `name` 与 `description`）。本仓库所有技能目录都带 `SKILL.md`。

---

## 关于 shuitu-writing-skill

这是仓库里唯一的重量级技能（452 MB / 8933 个文件），结构：

```
shuitu-writing-skill/
├── SKILL.md              技能入口（按需加载设计，常驻开销压到约 1/3）
├── README.skill.md       技能自带使用说明（知识库边界、调用机制、FAQ）
├── references/           检索脚本与说明，含 41.7 MB 的 search_index.kbx 倒排索引
├── scripts/              方案生成与校核脚本
├── _meta/                自检、对抗检查、基准测试与优化记录
└── vault/
    ├── md库/             371 篇知识库：Zone A 规范层 75 / Zone B 参考层 204 / Zone C 应用范例 91
    └── .assets/          8477 张扫描图，md 内以相对路径引用
```

设计约束（技能自带文档中的红线）：图片必须随包分发、**禁止外链**、禁止依赖包外路径；知识库强制分层取用，禁止全文检索。

因体积较大，只想用文本的话可以排除 `vault/.assets/`：

```powershell
robocopy .\shuitu-writing-skill $dest\shuitu-writing-skill /E /XD .assets
```

---

## 版权与来源

| 类型 | 技能 | 处理方式 |
|---|---|---|
| 自研 | `shuitu-writing-skill` | 著作权归仓库作者 |
| 二次收录 | `find-skills`、`grill-me`、`grilling`、`ponytail`、`neat-freak` | 上游出处见各自目录的 `README.md`；如上游有更新以官方仓库为准 |
| 明确改编 | `qu-ai-wei`（改编自 blader/humanizer，MIT）、`self-improvement`（改编自 OpenClaw 版 pskoett/self-improving-agent） | 保留原机制，平台专属部分替换为 DSH 等价物 |
| 他人作品 | `luban-skill-pro`（作者署名「智慧半岛」） | 保留作者署名 |

**`shuitu-writing-skill` 的知识库内容需注意**：`Zone A` 与 `Zone C` 收录的法规、国家标准、行业标准、已批准方案属公开发布的官方文本；`Zone B - 参考层` 下的专著、学位论文、期刊论文为各自著作权人的作品，收录目的是让编制工具能引用原文逐字核对，**请勿用于商业再分发**。著作权人提出异议时应删除对应文件。

---

## 脱敏说明

上传前对全部文本文件做过脱敏扫描：本机用户路径、11 位手机号、「姓名+手机号」组合、个人邮箱域名均已替换为占位符，统一社会信用代码做了中间掩码。未发现任何 API Key、访问令牌、私钥或密码字面量。

官方与机构邮箱、国家标准与法规原文中的编制单位信息有意保留 —— 改动会破坏知识库的出处可核验性。

处理规则、保留项与已知局限见 [`REDACTION.md`](./REDACTION.md)。仓库根目录的 `scan-secrets.mjs`（在上级目录）可随时复查。

---

## 许可

本仓库中自研部分采用 MIT。二次收录与改编部分遵循各自上游许可，见对应目录的 `README.md` 与包内声明。

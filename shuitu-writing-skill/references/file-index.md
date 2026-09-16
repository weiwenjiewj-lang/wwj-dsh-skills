# 文件索引（从 SKILL.md 外移）

> 本文件原为 SKILL.md 的「七、文件索引」一节（约 7.5 KB）。
> 它属**参考性内容**，AI 写作时几乎不需要——外移可显著降低每次加载技能的 token。
> 需要查文件用途时再来读本文件。

---


```
SKILL.md                     本文件（入口＝模板编排器）
README.md                    面向使用者的说明（用途/上手/更新/扩展/FAQ）
_meta/                       技能自身的优化记录（**非写作资产**，不影响写作流程）
  ├ results.tsv              历次优化评分流水（round/旧分/新分/Δ/维度）
  ├ diagnostics.tsv          缺陷检测子分（六模块 + Rubric 定位）
  └ test-prompts.json        优化用测试 prompt（典型/歧义/边界）
references/
  ├ metadata-spec.md         元数据受控词表（15 字段 + Zone C 专属字段）
  ├ compliance-gate.md       第一层：合规闸门规则（六步流程、打分表、铁律）
  ├ zone-b-usage.md          Zone B 使用边界与限定语、冲突处理、检索顺序
  ├ zone-c-usage.md          Zone C 使用边界：允许清单、坐标系警告、注入门槛、缺口探针
  ├ gap-analyzer.md          第三层：自诊断覆盖定义、阈值、报告格式、自检项
  ├ watchlist.md             时效性检查机制：字段、周期、变更包、闭环
  ├ rules.json               全部声明式规则（**唯一规则源**，29 组）：状态分类/范围激活/blocked/条款抽取/
  │                          计算项/数据分类/阈值/zone_c/zone_c_injection/zone_consistency_rules/
  │                          snapshot_gate/report_form_gate/**data_package（82 字段 13 分区）**/
  │                          **fact_extraction（叙述体事实抽取）**/
  │                          **ledger_rules**/**length_budget（篇幅与深度预算）**/**calc_engine（计算规格）**/
  │                          **style_rules（文风 + depth_patterns + style_severity）**/**check_rules（校核判定词表）**
  ├ template-tree.json       模板章节树：95 节点 / 420 内容点 / 条件分支 / 格式要求
  │                          **writing_directive：420 条动词化写作指令（与内容点同序，供写作参考）**
  ├ tables.json              表格清单：表1 特性表 55 字段+6 注、附表、附件、附图、报告表
  ├ zone-a-index.json        规范层索引（66 条，含 status/scope/chapter_relevance）
  ├ zone-b-index.json        参考层索引（114 条，含 usage_label 与 style_role 文体标记）
  ├ zone-c-index.json        范例层索引（91 条，含 project_type/topic_tags/topic_weight/
  │                          structure_summary/approval_basis_list/superseded_basis）
  ├ zone_c_style_samples.json **Zone C 写法范式样本**（334 条，从 91 份正文抽取并脱敏）——
  │                          只含"怎么写"（段落功能/句式骨架/论证链），数值与专名替换为〔占位符〕
  ├ species_index.json       **乡土树草种知识库**（87 个物种）——服务 2.7.6「当地主要乡土树草种
  │                          及生长情况」、2.3「主要树草种类型」、第 7 章植物措施配置。
  │                          字段：学名/科属/生活型/生态习性/适生条件/水土保持功能/出处
  ├ design_params.json       **设计参数口径表（8 组）**——服务 5.2「确定弃渣场级别」、
  │                          7.6「工程级别与设计标准」、7.3.1「执行标准等级」、4.1.2「表土质量评价」。
  │                          含 GB 51018 表5.7.1/5.7.2/5.7.3/5.6.2/5.11.3-1、
  │                          GB/T 45107 附录B 表B.1/B.2、GB/T 50434 §4.0.1 等级判定规则，均附条号
  ├ measure_methods.json     **措施工法索引（27 项）**——服务 7.7「典型设计」、
  │                          9.1.2「按措施类型计列工程量」、8.2/8.3「监测点位与频次」、
  │                          10「监理」、4.2.1「表土剥离」、5.2「弃渣场」、2.7.6「植被」。
  │                          含 GB 50433 主标准 §5.2~5.9 全部 7 类措施设计要求 +
  │                          GB 51018 措施条文 + GB/T 51240 监测 + SL/T 523 监理 +
  │                          SL 773 测算 + GB/T 15776 造林 + TD/T 1070.1 矿山修复 +
  │                          GB/T 51297 调查勘测
  ├ vault_manifest.json      库清单快照（271 条路径+大小+修改时间+generated_at；
  │                          快照闸门 30 天时效与漂移检测的数据源）
  └ source_watchlist.json    时效性监控清单（72 条，P0/P1/P2 三档）
scripts/
  ├ check_gate.py            第一层实现——合规闸门（只读 zone-a-index，Zone C 结构上不可能进入合规判断；
  │                          含快照时效检查与 province_registry 地方依据激活）
  ├ write_chapter.py         第二层实现——写作指令包（Zone C 默认关闭、仅在缺口节点开启；
  │                          含篇幅预算、台账事实注入、计算项、报告表分支）
  ├ analyze_gaps.py          第三层实现——缺口报告 + --mock 自检
  ├ check_draft.py           第四层实现——单节回检（C1 覆盖 / C2 占位符 / C3 数字 / C4 标题 /
  │                          C5 文风与深度 / C6 篇幅 / C7 台账核对）
  ├ ingest.py                喂料（确定性路径）——规范化资料（md/txt/csv/json/html/docx/pdf）
  │                          → 项目数据包 json；要求「字段名+分隔符+值」写法
  ├ extract_facts.py         喂料（叙述体路径）——既有方案正文/说明书等**连续叙述体**资料
  │                          → 事实清单 json。三步：--plan 出任务书交 AI 助手阅读、
  │                          --check 逐字回溯校验（**定位不到即作废**）、--to-ledger 入台账。
  │                          两条路径互补：ingest 处理规范写法，extract_facts 处理叙述体
  ├ ledger.py                事实台账——跨章口径唯一出口（冲突拒写、可注入他章已定事实、
  │                          拒绝写入技能目录或知识库）
  ├ budget.py                篇幅与深度预算（章间按 Zone C 实测占比、章内按节点权重；
  │                          另有 --chapter / --count / --book 三个查看入口）
  ├ calc.py                  计算引擎——公式→代入→中间量→结果→依据（缺输入拒绝计算；
  │                          含 --audit 自检计算输入与数据包字段的对应关系）
  ├ outline.py               全书骨架生成器——95 节点逐字标题 + 字数预算 + 内容点 + 表骨架
  ├ assemble.py              合稿成书——按模板序装配章节稿、重编表图号、生成目录与索引
  ├ check_plan.py            全书终检——节点覆盖 / 表1 55 栏 / 附件附图 / 跨章口径 / 篇幅折算 /
  │                          占位符清零 / 文风 / 台账
  ├ check_citation.py        引用校验——法规/标准/条款的格式与一致性核对
  ├ check_watchlist.py       时效性巡检、变更包应用、回滚、自检
  ├ extract_style_samples.py **Zone C 正文 → 写法范式样本**（把 91 份已批方案的正文
  │                          加工成可注入的"段落功能/句式骨架"，强制脱敏；
  │                          `--build` 生成、`--audit` 脱敏自检、`--show <主题>` 查看）
  ├ inline_images.py         **图片内嵌**——把 md 的外链图片转 base64 并删除图片目录，
  │                          使 md 自包含。云服务（尤其中文 PDF 转换）转出的
  │                          `xxx.md` + `images/` 必须过这一步才能入库
  ├ build_species_index.py   **建物种库**——从知识库条目式语料解析乡土树草种
  │                          （`--build` / `--list` / `--show <物种>` / `--audit`）
  ├ build_design_params.py   **建参数表**——从 Zone A 标准表格提取定级阈值
  │                          （`--build` / `--show <参数>` / `--audit`）
  ├ build_measure_methods.py **建工法索引**——从 Zone A 标准提取各措施设计要求
  │                          （`--build` / `--show <措施>` / `--audit`）
  ├ kb_cache.py              知识库扫描缓存（增量：未变更文件不重读；指纹短路）
  ├ kb_lookup.py             **知识库安全查询入口**（--map / --ask / --find；省 token）
  ├ build_species_index.py   建物种库（资产 D）
  ├ build_design_params.py   建参数表（资产 E）
  ├ build_measure_methods.py 建工法索引（资产 F）
  ├ inline_images.py         图片内嵌（外链转 base64 并删图片目录）
  └ rebuild_index.py         从知识库 frontmatter 三分层重建索引 + 漂移检测
```

**规则的唯一住处**：所有阈值、字段表、目标值、篇幅占比、文风清单都写在 `references/*.json`
（主要是 `rules.json`），脚本**只执行不定义**。因此改规则改 json，不改代码；
任何"只写在代码里的规则"都视为缺陷。

### 说明性文本 vs 机读规则（改 json 前先看这一节）

`rules.json` 的键分两类，**改法完全不同**：

| 类别 | 含义 | 改了会不会生效 |
|---|---|---|
| **机读规则** | 脚本 `json.load` 后直接参与判定/计算/取值 | **立即生效**，无需改代码 |
| **说明性文本** | 写给人与 agent 读的规格、解释、溯源说明；脚本从不读取 | 改它**不改变**脚本行为——要改行为得改代码，或先把该键接进脚本 |

**说明性文本清单**（脚本静态检索确认「从不读取」；括号内是实现位置）：

- `purpose`、`version`、各组的 `*_note` / `*_policy` / `sections` 说明类字段——纯说明。
- `blocked_rules`（阻断/仅警告/provisional/Zone B 四段规格）→ 实现见
  `check_gate.py` 的 blocked 判定段与 `status_classification`。
- `clause_extraction.fallback`（按段落兜底切分）→ 实现见 `check_gate.py split_clauses()`；
  `require_verbatim`（逐字引用）是**构造保证**：条款正文直接取自库内 md 原文，脚本不改写，
  故无需运行时开关。
- `zone_c.hard_rules`、`zone_c.mounting`、`zone_c.probe_use`、`zone_c.zone_b_vs_c_boundary`、
  `zone_c.identity_rule`、`zone_c.compliance_relevance_value` → 实现见 `write_chapter.py`
  的 `zone_c_topic_keys()`（只按 `topic_tags` 挂载 + `chapter_mapping: verified` 门槛）与输出限定语。
- `zone_c.extraction_policy.{allowed,forbidden}`（允许清单/禁提清单）→ 由人工摘句时执行，
  语义不可脚本校验；`zone_c_injection.synonym_policy`、`enable_when`、`require_topic_tags` 同属说明。
- `data_package.{purpose, provenance_note, conflict_policy, unit_policy, missing_policy,
  value_type_note, weak_alias_policy, fields_note, field_attributes}` → 实现见 `ingest.py`
  （别名归属、单位归一、冲突检测、缺数占位）。
- `ledger_rules.{purpose, key_sources, entry_fields, conflict_policy}` → 实现见 `ledger.py`
  （字段与冲突拒绝写入）；`status_values` **已接线**（台账状态取值取自该键）。
- `length_budget.{purpose, overhead_note, tolerance_note, style_note, level_weights_note,
  chapter_shares_source, chapter_shares_note}` → 实现见 `budget.py`。
- `style_rules.tone_requirements`（以陈述句为主、不得以「有待进一步研究」收尾等）→ 由 agent 写作时执行；
  其中可机读的部分已单列为规则键（`sentence_rules`、`paragraph_rules`、`density_limits`、`banned_phrases`）。
- `snapshot_gate.{stale_when, stale_behavior}`、`report_form_gate.{attachment, gate_rule,
  gate_targets_note}`、`check_rules.purpose`、`watchlist.purpose` → 规格文本，
  实现分别在 `check_gate.snapshot_status()` / `check_plan.attachments_check()` / 各脚本主流程。

> 判据：某键若要影响行为，必须在脚本里被 `json.load` 后的取值语句引用到。
> 新增说明性字段时请一并写进本清单，避免下次误以为改 json 就能改行为。

---


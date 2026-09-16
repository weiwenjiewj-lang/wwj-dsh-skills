# 水土保持方案编制技能（shuitu-writing-skill）

> 一个**通用**的水土保持方案编制技能。它按水利部 **2026 版编制模板**的章节结构，
> 帮你逐节写出方案正文、表格和计算，并在动笔前先做**合规检查**。
>
> **技能本身不含任何具体项目信息。** 你的项目资料由你在使用时提供。

---

## 一、它是干什么用的

给它一个章节号（比如 `1.1`）和你的项目资料，它会给你：

1. **这一节必须写哪些内容**（从模板原文逐条拆出的内容点，一个不漏）
2. **必须遵守的硬性规定**（从现行法规标准里抽出的条款原文，附条款号）
3. **可以真算的计算**（土壤流失量、六项防治指标、土石方平衡、表土平衡、投资汇总…），
   输出「公式 → 代入 → 中间量 → 结果 → 依据」的完整计算书
4. **可以借鉴的写法与案例**（专家专著、论文，但只用于表达参考）
5. **还缺哪些数据**（一张补数清单，不猜数）
6. **这一节该写多长**（全书按 150–180 页控制，每节都有目标字数与区间——
   写太少不够、太多溢出，所以**每写一节都实时对照**）
7. **写得像不像人写的**（去机械感体检：句长节奏、段落起笔、标点习惯、
   连接词密度、收口方式——阈值取自 91 份已批方案的实测统计，
   典型机器腔文本 5 项全中，而真实已批方案平均只报 0.4 条）

它**不能**替你做：编造项目数据、用默认值填缺失输入、自拟工程单价、跳过合规检查直接写。

---

## 二、架构（一句话版）

```
第一层 · 合规闸门   —— 动笔前先问："这一节该遵守的现行规定是什么？有没有过期依据？"
第二层 · 模板编排器 —— 按模板逐节给出"必须写什么 + 硬约束 + 可参考素材 + 要算什么 + 该写多长"
第三层 · 知识自诊断 —— 体检知识库："哪几节缺少专门依据？该补什么资料？"
```

围绕这三层，另有四个支撑模块（都在同一个技能内，不是四个技能）：

```
备料   ingest.py（规范化资料→数据包） + extract_facts.py（叙述体→事实清单） + ledger.py（事实台账）
骨架   outline.py（全书 95 节点骨架） + budget.py（篇幅与深度预算）
写作   write_chapter.py（指令包，含逐条写作指令） + calc.py（计算引擎，缺输入拒绝计算）
校核   check_draft.py（单节 C1–C7） + check_plan.py（全书终检）
```

这样做是为了让**闸门不可跳过**（先过闸门再取参考素材，顺序无法绕过），
也让"写着一半发现篇幅失控"无法发生。

---

## 三、两条最核心的原则

### 原则一：知识分三层，低层不能推翻高层

| | 包含 | 作用 |
|---|---|---|
| **Zone A · 规范层** | 法律、行政法规、部门规章、规范性文件、国家标准、行业标准 | **绝对红线**，任何内容不得与之冲突 |
| **Zone B · 参考层** | 专家专著、学者论文、学位论文、技术手册、团体标准、非规范性规划/报告 | 只用于**写作表达、论证逻辑、案例参考** |
| **Zone C · 应用范例层** | 水利部**已批准**的典型水土保持方案 91 份（存于知识库 `md库\Zone C - 应用范例\`，按项目类型分 9 个目录） | 只用于**写作风格、章节展开方式、表达逻辑、表格设计** |

**冲突时一律以 Zone A 为准**；Zone B / Zone C 的冲突内容**直接丢弃**（是丢弃，不是标注保留）。

**Zone C 的四条硬规则**（它最容易被误用，所以单列）：

1. **不得用于合规判断**——不得写入硬约束，不得参与阻断判定。
2. **不得引入其具体项目数据**——项目名称、地点、数字、单位名称一律不得进入产出。
3. **不得因其"曾经通过"而推定其做法在当前标准下仍然合规。** 它是**历史先例**，不是"合规证据"：
   已批准方案只在**当时的标准体系**下被接受，而标准会变、模板也会变。
4. **Zone A 未明确规定时**，必须输出「**Zone A 未明确规定，以下为参考写法，需人工判断**」，
   不得以范例的做法填补空白。与 Zone A 冲突的范例段落直接丢弃，并标注
   「该范例依据的是旧标准，已忽略其合规内容」。

> ⚠ **章节号坐标系陷阱**：已批准方案几乎都是**老八章结构**（GB 50433—2018 附录B），
> 与 2026 版 **10 章模板同号不同义**——老 `1.5`＝防治目标，新 `1.5`＝水土流失预测结果；
> 老 4→新 6、老 5→新 7 …… 系统性偏移 **+2**，且新版第 4、5 章在老结构中**无独立对应章**。
> 因此 **Zone C 禁止按章节号挂载**，只能按**主题标签**（`topic_tags`）检索；
> 未完成章节映射（`chapter_mapping != verified`）的范例不得挂载。
>
> **Zone C 也不得作为"章节是否完整"的判据**：老八章范例缺第 4 章（表土）、第 5 章（弃渣场），
> 那是**范例的结构缺陷**，不是"这一节可以略过"的依据。

引用 Zone B 时会自动附限定语：

> 仅用于写作表达、论证逻辑与案例参考，不得引入与 Zone A 冲突的方法论或参数。

引用 Zone C 时会自动附限定语：

> 应用范例仅可用于写作风格、章节展开方式、表达逻辑、表格设计的参考；不得用于合规判断，
> 不得引入其具体项目数据，不得因其曾经通过审批而推定其做法在当前标准下仍然合规。

### 原则二：模板是唯一骨架，且不得编造

- 章节号与标题**逐字**取自 2026 版模板，不自行增删改
- 所有法规、标准、条款必须来自知识库文件，**条款原文逐字引用**
- 数据缺失时留占位符 `【待填：字段名】`，**不猜数**

---

## 四、快速上手（四步）

> 一句话：**喂料 → 立账 → 搭骨架 → 逐节写、逐节校**。
> 一本方案的正常体量是 **150–180 页**，所以「写一节就校一节」是必须的动作，不是可选项。

先设一次 `$SK`（PowerShell）——**本文档后面所有命令都沿用这个变量**，
并且都在**项目工作区目录**下运行（数据包、台账、稿子、报告都落在工作区，
脚本会拒绝把任何产物写进技能包或知识库）：

```powershell
$SK = "<DSH_HOME>\skills\shuitu-writing-skill"
```

### 第 1 步：把原始资料直接喂进去（不用自己整理格式）

可研报告、初设、地勘、主体设计、占地表、证照——**原样交给它**，技能自己抽字段：

```bash
python "$SK\scripts\ingest.py" --source 可研报告.docx --source 地勘.md --source 占地表.csv --out 数据包.json
```

支持 md / txt / csv / json / html / docx / pdf。产出物里会有三样你要看的：
1. **已确认**字段（可直接用）；
2. **待人工确认**——分「冲突」（同一字段抽到不同值）与「弱别名多值」（可能只是同一个词的另一种用法）；
3. **未抽到**清单——不代表资料里没有，可能只是写法不同。

**务必声明项目所在省份**（如"河南省"）。不声明，地方性法规与地方收费标准不会被启用，
你会漏掉地方强制要求。

### 第 2 步：把定下来的数记进台账

同一件事（防治责任范围、防治分区数、执行标准等级、堆置高度）会在第 1、2、6、7、9 章
反复出现。**每个数值第一次写进正文之前，先记台账**：

```bash
python "$SK\scripts\ledger.py" --file 台账.json --set "防治责任范围面积=86.5" --unit hm² --chapter 1.6.1
```

以后任何一章写到这个数，`check_draft.py` / `check_plan.py` 都会拿台账来对。
值不一致就报警——这比事后通读一遍全书找矛盾可靠得多。

### 第 3 步：搭骨架，看清每一节该写多长

```bash
python "$SK\scripts\outline.py" --province 河南省 --out 方案骨架.md
```

产出：**95 个模板节点**的逐字标题（不得改字改号），每个标题下带该节的
**目标字数与区间**、必覆盖内容点、必备表骨架、计算项位置。全书正文目标 123,750 字
≈ 165 页；章间按已批准范例的实测篇幅分布分配（第 2 章项目概况最重、第 7 章防治最重），
章内按节点层级加权。**写着写着随时用 `budget.py --count` 对一下，避免前松后紧。**

### 第 4 步：逐节写、逐节校

对你正在交互的 AI 助手说：**"用 shuitu-writing-skill 写 1.6.1 章节"**，并附上数据包/台账。

你会得到：章节正文 + 表格 + **计算书**（公式→代入→中间量→结果→依据）+ **需补充的数据清单**。
写完一节立刻回检，不要等全书：

```bash
python "$SK\scripts\check_draft.py" --draft 稿件.md --chapter 1.6.1 --province 河南省 --ledger 台账.json
```

七项检查：模板内容点覆盖 / 占位符 / 数字一致性 / 标题 / **文风与深度** / **篇幅预算** / **台账核对**。

### 收口：全书终检

```bash
python "$SK\scripts\check_plan.py" --draft 全书.md --province 河南省 --ledger 台账.json --out 终检报告.md
```

查：95 节点覆盖（含"不涉及的不列"条件节点是否写明不涉及）、表1 特性表 55 栏是否齐、
附件/附表/附图是否列明、跨章口径是否一致、全书篇幅折算多少页、占位符是否清零、
文风与深度是否达标、与台账有无冲突。

### 手动运行（可选）

```powershell
$SK = "<DSH_HOME>\skills\shuitu-writing-skill"

# 全部命令都在**项目工作区目录**下运行：数据包.json / 台账.json / 稿件.md 都落在工作区。
# 脚本拒绝把任何产物写进技能包或知识库（铁律5 零项目残留）——路径写错会直接报错停下。

# 备料
python "$SK\scripts\ingest.py" --source 可研报告.docx --out 数据包.json
python "$SK\scripts\ledger.py" --file 台账.json --show

# 闸门与骨架
python "$SK\scripts\check_gate.py" --chapter 2.3 --province 河南省
python "$SK\scripts\outline.py" --chapter 2 --province 河南省 --out 第2章骨架.md

# 写作指令包 / 计算书 / 篇幅查看
python "$SK\scripts\write_chapter.py" --chapter 1.1 --province 河南省 --out 指令包.json
python "$SK\scripts\calc.py" --chapter 7.3.2 --data 数据包.json --out 计算书.md
python "$SK\scripts\budget.py" --chapter 7

# 反算（写措施之前先算"要达到目标需要多少"）
python "$SK\scripts\calc.py" --inverse --chapter 7.3.2 --data 数据包.json --target 林草覆盖率=26

# 校核
python "$SK\scripts\check_draft.py" --draft 稿件.md --chapter 1.6.2 --province 河南省 --ledger 台账.json
python "$SK\scripts\check_plan.py" --draft 全书.md --province 河南省 --ledger 台账.json

# 合稿成书 + 引用校验
python "$SK\scripts\assemble.py"       --draft-dir .\章节稿 --out 全书.md --meta 项目名=某某项目
python "$SK\scripts\check_citation.py" --draft 全书.md --out 引用校验报告.md

# 知识库体检与时效性
python "$SK\scripts\analyze_gaps.py" --out 缺口报告.md
python "$SK\scripts\check_watchlist.py" --check
```

> 需要 Python 3（本机为 3.12）。命令是 `python`，**不是 `python3`**。
> **没有 Python 也能用**——所有规则都明文写在 `references/` 里，AI 可以照规则手工执行，结论一致。
>
> **退出码契约**（脚本之间靠它串联，不要忽略）：
> `0` 通过无阻断 · `1` 跑完了但有需处理项（校核发现偏离/台账冲突） ·
> `2` 输入或用法错误（文件不存在、格式不支持、输出路径非法、没有任何可解析资料、**章节号不在模板里**）——**没有可信结果** ·
> `3` 合规闸门 `blocked`，禁止写作（`check_gate` / `outline` / `write_chapter` 阻断时都退 3，且**不产出任何文件**）。
> 判定「有没有问题」看输出正文，不要只看退出码；退出码是给流水线和人眼的第一道提示。
>
> **条款抽取**：法条号在库内 md 里有三种形态——① 单独成行（`## 第三条`）；② 与正文融合
    > （`第二条在中华人民共和国境内…`）；③ 条号+空白+正文（`第一条␣␣为了…`，法律全文常见）。
    > `clause_extraction.law_patterns` 只要求「第X条」**后跟中文或空白**，前置边界由 `split_clauses`
    > 判断；同一条号的「定义处 vs 引用处」由 `clause_quality` 规则去重（罚则条款不会被误删）。
    > 抽取到的正文若命中封面/英文标题/元数据（`clause_quality.reject_patterns`）或过短，**一律丢弃**——
    > 宁可不给依据，也不得把封面当真条文引用。
    >
    > **老八章 → 新十章**：已批准范例多为老八章，与 2026 版同号不同义（老4→新6、老5→新7…）。
    > 换算表在 `rules.json` 的 `zone_c.chapter_system_mapping`，挂载范例时会自动带出提示。
    >
    > **索引与真源**：知识库 md 文件的 **frontmatter 是真源**，`references\*.json` 是派生的快照。
    > `rebuild_index.py --rebuild` 会用 frontmatter 重新覆盖索引——**直接改索引的编辑会丢失**。
    > 要改章节归属（`chapter_relevance`）、文号（`doc_number`）等元数据，请改**库内 md 的 frontmatter**，再重建索引。
    > 另注意闸门的归属判定顺序：`["全域"]` 一律记为**继承依据**；若某文件实际只覆盖部分章节，
    > 必须写明具体章节号（此时不要再保留 `全域`），否则该文件对那些节点永远只算继承、不算专属。
    >
    > **机器可读输出**：`check_gate` / `outline` / `write_chapter` / `check_draft` / `check_plan` /
> `calc` / `budget` / `ingest` / `ledger` 支持 `--json-only`（`analyze_gaps` 用 `--json`），
> stdout 为**纯 UTF-8、不带 BOM**，可直接 `python xxx.py --json-only > x.json` 再 `json.load`。
> `check_watchlist` / `rebuild_index` 是巡检类命令，只出人读文本。

---

## 五、怎么更新知识库

**知识库随技能包交付**，位于 `<技能包>/vault/md库/`（相对路径，见 `scripts/vault_paths.py`）。

Obsidian 直接打开 `<技能包>/vault/` 即可编辑（`.obsidian` 配置已随库迁入）。
临时换库位置用环境变量 `DSH_WS_VAULT` 覆盖，**不必改代码**。

> **路径解析集中在 `scripts/vault_paths.py`** —— 原先 7 个脚本各硬编码一份绝对路径，
> 换库位置要改 7 处，且**漏改的那处不报错**（静默把库当"不可达"）。
> 现在规则只定义一处。优先级：`DSH_WS_VAULT` → `<技能包>/vault/` → `<技能包>/`。

> 库不可达时（路径不存在/未就绪），脚本**不阻断**写作，但会在输出中强制携带
> 「知识库不可达……仅能使用索引快照」警告（`stale=true`）——**绝不静默使用旧索引**。
> 恢复步骤：恢复库路径或设好 `DSH_WS_VAULT` → `rebuild_index.py --rebuild` → 重跑 `analyze_gaps.py`。

### 新增资料时

**如果是 PDF / 扫描件**，先转换再入库（**不在本地跑转换**，走云服务插件）：

1. 用**云服务**把 PDF 转成 md（会得到 `xxx.md` + 一个图片文件夹）
2. **把图片内嵌进 md，并删掉图片文件夹**（关键一步）：
   ```powershell
   python "$SK\scripts\inline_images.py" --dir <云服务输出目录>
   ```
   → `![](images/xxx.jpg)` 变成 `![](data:image/jpeg;base64,...)`，图片目录被删除，md 自包含。
3. 继续下面的步骤

**通用入库步骤**：

1. 把 md 文件放进库里的对应目录
2. 按 `references/metadata-spec.md` 给文件加 YAML frontmatter（15 个字段）
   - **最省事的做法**：直接对 AI 说"给这个新文件按技能规范补 frontmatter"，AI 会照词表生成
   - Zone B 文件另加 `style_role`（文体范式 / 方法论 / 背景资料）
3. 重建索引快照：

   ```bash
   python "$SK\scripts\rebuild_index.py" --rebuild
   ```

4. 重新体检：`python "$SK\scripts\analyze_gaps.py" --out 缺口报告.md`

> **图片存储规约（2026-09 修订）**：图片存于**包内** `vault\.assets\` 目录，
> md 内以**相对路径**引用（`![alt](../../.assets/<tag>/0001.jpg)`）。
> 旧规约「图片全部 base64 内嵌」已废止——base64 有 4/3 编码膨胀，实测占 md 体积 77.8%。
> **禁止外链**不变：外链图片脱离原目录即失效。
> 入库前可用 `python "$SK\scripts\inline_images.py" --check <文件>` 自检。

### 判断新资料属于哪一层

| 它是什么 | 属于 |
|---|---|
| 法律、行政法规、部门规章、规范性文件、国家标准、行业标准 | **Zone A** |
| 专著、论文、学位论文、技术手册、团体标准、非规范性规划/报告 | **Zone B** |

> **判定按文档的法律效力层级，不按它放在哪个目录。**
> 例如 `03-专家资料\03-技术标准\` 下的 GB/T、TD/T 标准，虽然放在"专家资料"里，仍属于 Zone A。

### 知识库过期了怎么办

技能运行时**优先实时读库**；`references/*.json` 只是离线兜底的索引快照。
快照超过 **30 天**、或库内文件清单与快照记录不一致时，`check_gate.py` / `write_chapter.py` /
`check_draft.py` 会在输出中强制携带过期警告（不阻断，但合规结论未经验证）——
此时执行 `python "$SK\scripts\rebuild_index.py" --rebuild` 重建，再重跑 `analyze_gaps.py`。
**绝不静默使用旧索引。**

---

## 六、怎么维护时效性（标准会不会过期）

```bash
python "$SK\scripts\check_watchlist.py" --check               # 看哪些文件该核对了
python "$SK\scripts\check_watchlist.py" --simulate-supersede  # 演练"标准被替代"的完整流程
python "$SK\scripts\check_watchlist.py" --apply-change 变更包.json   # 应用一个变更
python "$SK\scripts\check_watchlist.py" --undo                # 撤销最近一次变更
```

- 清单覆盖全部 **66 个 Zone A 文件**，分三档巡检：P0 每季度（36 条）、P1 每半年（16 条）、P2 每年（20 条），共 72 条；档位与周期由 `rules.json watchlist` 声明
- 发现标准被替代/废止时，填一个**变更包**（`change_type` / `old_doc` / `new_doc` / `effective_date` / `affected_chapters` / `action_required` + **必填的 `evidence` 凭据**）
- 应用后会自动回写三处：清单、Zone A 索引、变更留痕；并提示重新生成缺口报告

> `official_lookup_url` 只在库内文件本身含网址时才填，**其余一律"待确认"，不猜网址**。
> 为便于核对，清单另给 `lookup_hint`（由发布机关名生成），例如：
> "发布机关：中华人民共和国水利部 → 请在该机关官方网站或标准发布公告中核对编号与效力状态"。

---

## 七、怎么扩展

| 你想做的事 | 改哪里 |
|---|---|
| 换掉/补充模板 | 替换 `references/template-tree.json` 与 `tables.json`（须与模板原文逐字一致） |
| 调整合规判定松紧 | `references/rules.json` 的 `status_classification` / `blocked_rules` |
| 登记新省份的地方依据 | `references/rules.json` 的 `scope_activation.province_registry`（键=省名子串，值=库内该省文件名清单） |
| 登记新地市的地方依据 | `references/rules.json` 的 `scope_activation.city_registry`（键=地市名，值=库内该市文件名清单；未登记时按文件名比对地市名，不匹配则**不激活**） |
| 调整时效性巡检档位/周期 | `references/rules.json` 的 `watchlist`（`check_interval_days` + `priority_rules`） |
| 调整 Zone B 注入上限 | `references/rules.json` 的 `zone_b_injection.max_snippets` |
| 调整受理的资料格式 | `references/rules.json` 的 `data_package.source_formats`（native / office / pdf / unsupported） |
| 调整条款抽取正则 | `references/rules.json` 的 `clause_extraction.{law_patterns,standard_patterns,toc_patterns}` |
| 调整快照时效阈值 | `references/rules.json` 的 `snapshot_gate.max_age_days` |
| 增加 Zone C 检索同义词 | `references/rules.json` 的 `zone_c_injection.zone_c_synonyms` |
| 调整报告表触发节点 | `references/rules.json` 的 `report_form_gate.gate_targets` |
| 调整缺口阈值 | `references/rules.json` 的 `coverage_rule.threshold` |
| 增加必算项 | `references/rules.json` 的 `calculations` |
| 增加关键词（提高条款定位准确度） | `references/rules.json` 的 `node_keywords` |
| 改元数据词表 | `references/metadata-spec.md`，并重建索引 |
| 改知识库位置 | `SKILL.md` 中的路径，或设环境变量 `DSH_WS_VAULT` |

**改规则的顺序**：先改 `references/` 里的明文规则 → 再重跑相关脚本 → 最后跑自检。
**规则不允许只存在于代码里**——`check_gate.py`、`write_chapter.py`、`analyze_gaps.py`、`check_watchlist.py`
都只是 `rules.json` 与各 `*.md` 的执行器。

---

## 八、常见问题

**Q：为什么 13 个章节显示"缺少专门依据"？**
A：这是知识库的真实边界，不是故障。例如 `5.1` 渣土来源及流向，库中只有《水土保持法》《水利部令第53号》
这类**对全篇普遍适用**的文件，没有**专门规范它**的文件。方案仍能写，但若评审追问运距计量口径，无专门依据可引。
缺口报告会明确告诉你该补哪方面资料。

**Q：为什么闸门说 `provisional=true`？**
A：**已全部核实清零**（2026-09-13）：有文号的查证填入并留 `doc_number_source`；本身无文号的（学术论文、以公告形式公布的法规、内部印发提纲、规划成果、公示件）标为「不适用」并留理由。施行日期同理。这不影响写作，
但**送审前必须人工确认**，否则引用可能出错。

**Q：为什么同一个标准号在清单里显示"待确认"？**
A：该文件正文没有编号信息（多为转换自 PDF）。技能**不会**从文件名反推编号，所以标"待确认"。
你核实后回填即可。

**Q：写出来的东西会不会混进别的项目的数据？**
A：不会。技能有硬性规定：**项目信息一律不得写回技能目录，也不得写回知识库**，
只存在于当前会话或该项目自己的工作区。技能目录与知识库保持零项目残留。

---

## 九、目录结构

```
shuitu-writing-skill\
├── SKILL.md                      总入口与工作流程（十二步＝备料/立骨架/写正文/校核收口）
├── README.md                     本文件（面向使用者）
├── _meta\                        技能自身的优化记录（非写作资产，不影响写作流程）
│   ├── results.tsv               历次优化评分流水
│   ├── diagnostics.tsv           缺陷检测子分（六模块 + Rubric）
│   └── test-prompts.json         优化用测试 prompt
├── references\
│   ├── metadata-spec.md          元数据受控词表（15 字段）
│   ├── compliance-gate.md        第一层：合规闸门规则
│   ├── zone-b-usage.md           Zone B 使用边界与限定语
│   ├── zone-c-usage.md           Zone C 使用边界：允许清单、坐标系警告、注入门槛
│   ├── gap-analyzer.md           第三层：自诊断规则与阈值
│   ├── watchlist.md              时效性检查机制
│   ├── rules.json                **唯一规则源**（29 组）：province_registry / snapshot_gate /
│   │                             zone_c_synonyms / report_form_gate / zone_consistency_rules /
│   │                             data_package（82 字段）/ fact_extraction（叙述体抽取）/
│   │                             ledger_rules / length_budget /
│   │                             calc_engine / style_rules / check_rules
│   ├── template-tree.json        模板章节树（95 节点 / 420 内容点 + 420 条写作指令）
│   ├── tables.json               表格清单（表1 55 字段 + 报告表 report_form）
│   ├── zone-a-index.json         规范层索引（66 条）
│   ├── zone-b-index.json         参考层索引（114 条）
│   ├── zone-c-index.json         范例层索引（91 条）
│   ├── zone_c_style_samples.json **Zone C 写法范式样本**（334 条，已脱敏）
│   ├── vault_manifest.json       库清单快照（快照时效 30 天与漂移检测数据源）
│   └── source_watchlist.json     时效性监控清单（72 条）
└── scripts\
    ├── check_gate.py             第一层：合规闸门（含快照时效检查、省份注册表激活）
    ├── write_chapter.py          第二层：写作指令包（含报告表分支、篇幅预算、台账注入）
    ├── analyze_gaps.py           第三层：知识库缺口报告
    ├── check_draft.py            第四层：单节回检（C1–C7）
    ├── ingest.py                 备料（确定性）：规范化资料 → 项目数据包 json
    ├── extract_facts.py          备料（叙述体）：正文类资料 → 事实清单（逐字回溯校验）
    ├── ledger.py                 备料：事实台账（跨章口径唯一出口）
    ├── budget.py                 骨架：篇幅与深度预算
    ├── outline.py                骨架：全书 95 节点骨架生成
    ├── calc.py                   写作：计算引擎（公式→代入→结果→依据；`--inverse` 指标反算）
    ├── assemble.py               成书：全书拼装（模板序装配、表图号重编、目录与索引）
    ├── check_citation.py         校核：引用格式校验（法规/标准/条款一致性）
    ├── check_plan.py             校核：全书终检
    ├── check_watchlist.py        时效性巡检与变更闭环
    ├── extract_style_samples.py  Zone C 正文 → 写法范式样本（脱敏；`--build` / `--audit`）
    ├── inline_images.py          图片内嵌：外链图片转 base64 并删图片目录（入库前必跑）
    └── rebuild_index.py          从知识库 frontmatter 重建索引 + 漂移检测
```

---

## 十、四条不能碰的红线

1. **不编造**——法规、标准、条款原文、项目数据、工程单价，一律不得自行编造；
   缺项目输入时**拒绝计算**并留 `【待填：…】`，不得用默认值/经验值/同类项目值兜底
2. **不越层**——Zone B 不得推翻 Zone A；冲突时丢弃 Zone B
3. **不跳闸门**——合规闸门未通过（`blocked=true`）时不得写作
4. **不污染**——项目信息不得写回技能目录或知识库（含示例、注释、docstring）

---

## 十一、写完之后：两级校核

### 单节回检（写完一节就做）

`check_draft.py` 对**已写出的章节稿**做七项机械回检：

| 检查 | 内容 | 性质 |
|---|---|---|
| C1 模板覆盖 | 稿件是否覆盖目标节点 `required_content_points` 全部内容点 | 未命中≠一定缺（同义改写会误报），需人工确认 |
| C2 占位符清点 | 【待填】【待核实】清单 | 不是缺陷，是待办的显式化；定稿前必须清零 |
| C3 数字一致性 | 同名同单位数值多处出现不同值 → 疑似冲突 | 只报"疑似，需人工确认"；分区措施数量自动降级为提示 |
| C4 章节标题 | 是否存在包含章节号的标题行 | 防写错章节/漏标题 |
| C5 文风与深度 | 空话套话、超长句、雷同开头、术语不统一、四类深度要素是否齐 | 深度要素按"缺哪种补哪种"给建议 |
| C6 篇幅预算 | 本节/本章实际字数 vs 目标区间 | 偏少＝深度不够；超标＝占了别节篇幅 |
| C7 台账核对 | 稿件中的事实值 vs 台账已定值 | 冲突即报，避免跨章自相矛盾 |

```bash
python "$SK\scripts\check_draft.py" --draft 稿件.md --chapter 1.6.2 --province 河南省 --ledger 台账.json
# 多稿联合比对（跨章节核对同一数值口径，如 1.6.1 与 1.6.2 的防治责任范围）
python "$SK\scripts\check_draft.py" --draft 稿A.md --draft 稿B.md --chapter 1.6.1 --out 校核报告.md
```

### 全书终检（合稿后做）

```bash
python "$SK\scripts\check_plan.py" --draft 全书.md --province 河南省 --ledger 台账.json --out 终检报告.md
```

查八件事：95 节点覆盖（含条件节点是否写明"不涉及"）、表1 特性表 55 栏齐备性、
附件/附表/附图清单、跨章口径一致性、全书篇幅折算页数、占位符清零、文风与深度、台账核对。

**边界**：两者都只做机械比对，**不下合规结论**；exit code 1 表示存在待人工处理项。
合规性仍以 Zone A 闸门与人工审查为准。

---

## 十二、交付标准（Word 文件）

**方案一律以 Word 文件为交付物，不另在文件之外输出正文内容。**

| 情形 | 交付方式 |
|:---|:---|
| **已有 Word 文件**（在已有 Word 文件或新建 Word 文件中写作改动） | **直接把方案写入该 Word 文件**，不在文件之外另行输出内容 |
| **没有已有 Word 文件**（需直接撰写方案） | **交付一个 Word 文件**（文件命名、存放路径等待补充：…） |

**四条固定动作**：

1. **文件位置**——Word 文件**直接创建在工作区相应文件夹内**；
2. **必须报告存储路径**——交付时明确给出完整存储路径，不得只报文件名；
3. **文件命名必须带日期**，如 `2026.09.16`；
4. **写完后自动打开该 Word 文件**——两种情形都适用。

排版规格照 `references/table-format.md`（`办水保函〔2026〕232号-附件2` 逐字）执行——
正文小四号仿宋、数字与英文 Times New Roman、页眉页脚五号仿宋、封面湖蓝色、
责任页字体分工、目录两级，**不因交付方式改变**。

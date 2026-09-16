# 能力总账（Capability Index）

> **本文件由 `python _meta/build_capability_index.py` 自动生成，请勿手工编辑。**
> 作用：把技能的全部能力枚举成可核对的清单，
> **让"优化时不小心删掉某个能力"变成显式失败**，而不是写到第 8 章才发现算不出指标。

## 一、不变量断言（跌破即能力丢失）

| 能力 | 当前值 | 下限 | 判定 | 说明 |
|:---|---:|---:|:--:|:---|
| `nodes_leaf` | 95 | 95 | ✅ | 模板叶子节点数（2026 版十章共 95 节点） |
| `content_points` | 420 | 400 | ✅ | 模板内容点总数（模板完整性基础） |
| `t1_fields` | 55 | 55 | ✅ | 表1 特性表栏数（模板硬要求 55 栏） |
| `t1_notes` | 6 | 6 | ✅ | 表1 表注条数（模板硬要求 6 条） |
| `calc_items` | 7 | 7 | ✅ | 计算公式引擎数（2.3/2.4/4.4/6.3/7.3.2/8.2/9.1.2） |
| `pkg_fields` | 82 | 82 | ✅ | 项目数据包字段数（82 字段 13 分区） |
| `fact_fields` | 82 | 82 | ✅ | 事实抽取字段字典（与数据包同源） |
| `species` | 87 | 80 | ✅ | 物种名录条数（资产 D） |
| `measure_methods` | 33 | 20 | ✅ | 措施工法条数（资产 F） |
| `design_params` | 8 | 5 | ✅ | 设计参数组数（资产 E） |
| `style_samples` | 334 | 300 | ✅ | Zone C 写法范式条数 |
| `zone_a` | 75 | 70 | ✅ | Zone A 规范条目数 |
| `zone_c` | 91 | 90 | ✅ | Zone C 应用范例条目数 |

**总判定：✅ 全部达标**

## 二、脚本能力（28 个）

| 脚本 | 用途 |
|:---|:---|
| `scripts/analyze_gaps.py` | 第三层：知识自诊断器。遍历模板节点，检查 Zone A / Zone B 覆盖，输出缺口报告。 |
| `scripts/assemble.py` | 第八层：全书拼装器。把逐节/逐章的 md 稿件，按模板顺序拼成一本可送审的方案书。 |
| `scripts/budget.py` | 篇幅与深度预算模块（被 write_chapter / check_draft / outline / check_plan 共用）。 |
| `scripts/build_asset_map.py` | 从三个 zone 索引生成「分章取用地图」asset-map.json —— 禁全文检索机制的数据底座。 |
| `scripts/build_design_params.py` | 构建设计参数口径表（资产 E）：从 Zone A 标准正文提取阈值，供写作时按条引据。 |
| `scripts/build_measure_methods.py` | 构建措施工法索引（资产 F）：把"某措施怎么做"从标准与已批方案中提炼成结构化条目。 |
| `scripts/build_search_index.py` | 知识库倒排索引构建器 —— 让全库 371 个 md / 566 MB 的**每一行**都可被检索与取回。 |
| `scripts/build_species_index.py` | 构建乡土树草种知识库（资产 D）。 |
| `scripts/calc.py` | 第五层：计算引擎。把方案里的公式算对、算全，并留下可复核的计算书。 |
| `scripts/check_citation.py` | 第九层：引用格式校验器。查法规/标准/条款/图表引用是否前后一致、是否可追溯。 |
| `scripts/check_draft.py` | 第四层：稿后校核器。对已写出的章节草稿做机械回检，闭合"写作辅助"环路。 |
| `scripts/check_gate.py` | python check_gate.py --chapter 2.3 [--province 河南省] [--city 平顶山市] |
| `scripts/check_plan.py` | 第七层：全书终检器。定稿前跑一次，回答「这本方案能不能送审」的机械问题。 |
| `scripts/check_watchlist.py` | 时效性检查机制：清单生成、巡检任务单、变更包应用与闭环自检。 |
| `scripts/extract_facts.py` | 事实清单提取层（P0-B）：把「叙述体资料」变成可核可签的事实清单。 |
| `scripts/extract_style_samples.py` | Zone C 写法范式提取器：把 91 份已批方案的**正文**加工成可注入的"写法范式"。 |
| `scripts/ingest.py` | 资料接入层：把使用者喂入的原始资料变成唯一的结构化数据源（项目数据包）。 |
| `scripts/inline_images.py` | 图片入库工具：把 md 里的**外链图片**收进**包内** `.assets/` 目录，md 改相对路径引用。 |
| `scripts/kb_cache.py` | 知识库扫描缓存（供各 build_*.py 复用）：按文件 mtime+size 跳过未变更文件。 |
| `scripts/kb_lookup.py` | 知识库安全查询入口（唯一推荐的查询方式，避免 AI 直读大文件烧 token）。 |
| `scripts/kb_read.py` | 知识库精确取文 —— 把倒排索引查到的「位置」变成「可用的正文片段」。 |
| `scripts/ledger.py` | 事实台账：登记「全书已定事实」，让后续章节沿用同一口径。 |
| `scripts/outline.py` | 第六层：全书骨架生成器。按模板逐字标题搭出全书 md 骨架，并把篇幅预算嵌进每个节点。 |
| `scripts/pack_index.py` | 索引二进制打包器 v3 —— 惰性加载，把 19 秒降到毫秒级。 |
| `scripts/rebuild_index.py` | 从 Obsidian 知识库重建索引快照，并检测漂移与过期（D8）。 |
| `scripts/shrink_images.py` | 内嵌图片降采样 —— 以「只在确实变小」为前提的体积优化。 |
| `scripts/vault_paths.py` | 知识库（vault）路径解析 —— **所有脚本的唯一取值入口**。 |
| `scripts/write_chapter.py` | 第二层：模板编排器。生成某章节的「写作指令包」。 |

## 三、七类知识资产

| 资产 | 内容 | 条目数 | 载体 |
|:--:|:---|---:|:---|
| A | 规范依据 | 75 | `zone-a-index.json` |
| B | 事实台账 | 82 字段 | `台账.json`（extract_facts 产出） |
| C | 写法范式 | 334 | `zone_c_style_samples.json` |
| D | 物种名录 | 87 | `species_index.json` |
| E | 参数口径 | 8 | `design_params.json` |
| F | 工法索引 | 33 | `measure_methods.json` |
| G | 文体范式 | 204（Zone B 总条目） | `zone-b-index.json` 的 `style_role` |

> Zone C 应用范例：**91** 条（`zone-c-index.json`）。

## 四、计算公式引擎（7 个，共 23 项输入）

| 节点 | 计算项 | 方法 |
|:---|:---|:---|
| `2.3` | 工程占地分类统计勾稽 | `sum_check` |
| `2.4` | 土石方（不含表土）平衡勾稽 | `balance_check` |
| `4.4` | 表土平衡 | `formula` |
| `6.3` | 土壤流失量预测 | `structured` |
| `7.3.2` | 六项防治指标值及达标判定 | `indicators` |
| `9.1.2` | 水土保持投资汇总勾稽 | `sum_check` |
| `8.2` | 监测点位数量与频次 | `structured` |

**规范给定参数（甲类，引擎自取值）**：indicator_targets、allowable_soil_loss

> **缺项目输入一律输出 `【待填：字段名】` + 「缺数据不可计算」，绝不用默认值兜底。**
> 输入字段承接自检：`python "$SK\scripts\calc.py" --audit`

## 五、模板与表格

| 项 | 值 |
|:---|---:|
| 模板节点总数 | 95 |
| 叶子节点（可写作） | 95 |
| 模板内容点总数 | 420 |
| 表1 特性表栏数 | 55 |
| 表1 表注条数 | 6 |
| 报告表区块数 | 2 |
| 全书正文目标字数 | None |

**表格排版规范**见 `references/table-format.md`（字体/字号/封面/页眉/表题表注，逐字取自办水保函〔2026〕232号-附件2）。

## 六、规则组（rules.json 共 34 组，唯一规则源）

- `assemble_rules`
- `blocked_rules`
- `calc_engine`
- `calculations`
- `check_rules`
- `citation_rules`
- `clause_extraction`
- `compact_pack`
- `coverage_rule`
- `data_class_rules`
- `data_package`
- `deprioritized_sources`
- `design_params`
- `fact_extraction`
- `human_voice`
- `ledger_rules`
- `length_budget`
- `max_constraints_per_chapter`
- `max_constraints_per_doc`
- `measure_methods`
- `node_keywords`
- `purpose`
- `report_form_gate`
- `scope_activation`
- `snapshot_gate`
- `species_index`
- `status_classification`
- `style_rules`
- `version`
- `watchlist`
- `zone_b_injection`
- `zone_c`
- `zone_c_injection`
- `zone_consistency_rules`

> **不允许出现只写在代码里的规则**——所有阈值、上限、判定口径均在 rules.json。

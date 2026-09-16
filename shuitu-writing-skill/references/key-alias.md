# 指令包短键对照表

> `write_chapter.py` 默认输出的指令包使用**短键名**以压缩体积。
> 本文件是唯一的对照来源；**一次读入，全书复用**，不需要每节重读。
> 只在阅读指令包时遇到不认识的短键才查这里。写入正文时一律用长名。

## 顶层键

| 短键 | 长键 | 含义 |
|:--|:--|:--|
| `ch` | chapter | 章节号 |
| `ok` | write_allowed | 是否允许写作 |
| `blk` / `blkr` | blocked / block_reason | 是否被闸门阻断 / 阻断原因 |
| `prov` | provisional | 是否含待人工确认项 |
| `g` | gate | 合规闸门结果 |
| `nd` | nodes | 模板骨架节点 |
| `ins` | instructions | 写作指令 |
| `tb` | tables | 必备表格 |
| `ca` | calculations | 计算项 |
| `dq` | data_requirements | 数据需求 |
| `zb` / `zbt` / `zbq` | zone_b_references / zone_b_total_matched / zone_b_qualifier | Zone B 参考素材 |
| `zc` / `zcs` / `zcq` | zone_c_references / zone_c_injection_status / zone_c_qualifier | Zone C 应用范例 |
| `zc1` | zone_c_shared_notice | Zone C 各条**共有**的提示语（`{"csn":…, "cfn":…}`）。见下方说明 |
| `zss` / `zsss` | zone_c_style_samples / zone_c_style_status | Zone C 写法范式样本 |
| `sp` / `sps` | species_references / species_status | 资产 D 物种名录 |
| `dp` / `dps` | design_params / design_params_status | 资产 E 参数口径 |
| `mm` / `mms` | measure_methods / measure_methods_status | 资产 F 工法索引 |
| `zau` / `zaun` | zone_a_unspecified / zone_a_unspecified_notice | Zone A 未规定标记 |
| `zk` | zone_consistency | 三分层一致性自检 |
| `lf` | ledger_facts | 台账已定事实 |
| `lb` | length_budget | 篇幅预算 |
| `sw` | snapshot_warning | 知识库索引时效性警告 |
| `of` | output_format | 输出格式契约 |
| `_rules` | — | 各资产的使用规则合并段 |
| `ct` / `ct_note` | — | 短键对照指针 / 短键约定说明 |
| `_compact` | — | 压缩说明 |

### `zc1`：Zone C 共有提示语（读法）

`zc1` 存放 **同一节内每条 Zone C 样本都相同**的提示语，避免把同一段话抄几十遍。
实测 `chapter_system_notice`（老八章结构提醒）在同一节的 60 条样本里只有 2 种取值、
最长的一种重复了 **54 次**，占 Zone C 总字符的 **23%**。

```jsonc
"zc": [ {"ptype":"铁路工程", …},          // 各条自己的内容
        {"ptype":"市政与房建", …} ],
"zc1": { "csn": "本范例为**老八章**结构，章节号与 2026 版新十章不可直接对应…" }
```

**读法**：`zc[i].csn` 若**缺失**，即取 `zc1.csn`；若某条自带 `csn`，以该条自带的为准。
两种写法都兼容，取值不一致时**不会**做提升（原样保留各条自己的），因此不会丢信息。
需要未提升的原始形态时，用 `write_chapter.py --full`。

## 闸门 `g` 内部

| 短键 | 长键 | 含义 |
|:--|:--|:--|
| `hc` | hard_constraints | 硬约束列表 |
| `src_files` | — | **源文件引用表**：`hc[].s` 是它的下标（1 起） |
| `req` | requirement | 条款原文（若被截断则置 `truncated`，尾部 `…`） |
| `trc` | truncated | **该条被我们截断过**（区别于原文自带的省略号） |
| `more` | — | 截断提示：「原文N字符，全文按 oid 取」 |
| `oi` | — | 条款序号（1 起），与 `s` 共同构成回溯定位串 |
| `s` | — | 源文件引用号，指向 `g.src_files[s-1]` |
| `src` | source | 标准/文件名称 |
| `cl` | clause | 条款号；**省略即「（段落）」** |
| `og` | origin | `node_specific` 节点专属 / `inherited` 继承 |
| `sc` | scope | **省略即 `national`**；地方依据时才出现 |
| `st` | status | **省略即「现行有效」**；非现行时才出现 |
| `ms` | match_score | **省略即 2** |
| `csc` / `clc` | constraint_scope / clause_count | 依据来源统计 |
| `wn` / `dtl` / `sby` | warnings / detail / superseded_by | 时效性告警 |
| `snp` / `sl` / `rss` | snapshot / stale / reasons | 索引快照状态 |

## 骨架 `nd` / 指令 `ins`

| 短键 | 长键 | 含义 |
|:--|:--|:--|
| `nid` | chapter_id | 节点号 |
| `ti` | title | 节点标题（逐字取自模板） |
| `nk` | node_kind | 节点类型 |
| `rcp` | required_content_points | **完整性**检查清单（审核用） |
| `wd` | writing_directive | **深度**写作指令（写作时执行，与 `rcp` 同序一一对应） |
| `wdn` | writing_directive_note | 指令说明 |
| `pc` | point_count | 内容点数 |
| `cond` / `cv` | conditional / conditional_variants | 适用条件 / 情形选项 |
| `ev` | evidence | 依据线索 |
| `mc` | must_cover | 必须覆盖（同 `rcp`） |
| `dr` | depth_requirements | 深度要素要求 |
| `cq` | conditional_question | 动笔前必须先确认的问题 |
| `rl` | rules | 写作规则 |

## 篇幅 `lb`

| 短键 | 长键 | 含义 |
|:--|:--|:--|
| `bk` | book | 全书预算 |
| `btc` / `ept` / `pgt` | book_target_chars / estimated_pages_at_target / page_target | 全书正文目标字数 / 折算页数 / 页数区间 |
| `bmin` / `bmax` | book_min_chars / book_max_chars | 全书下限 / 上限 |
| `tg` / `lo` / `hi` | target / min / max | 本节点目标 / 下限 / 上限 |
| `tc` / `nc` / `xc` | target_chars / min_chars / max_chars | 同上（指令内表述） |
| `trq` | tables_required | 是否要求配表 |

## 资产 D/E/F（`sp` / `dp` / `mm`）

物种与工法沿用**中文字段名**（未缩写），仅将少数长字段名缩写：

| 短键 | 长键 |
|:--|:--|
| `mea` | 措施 |
| `mty` | 类型 |
| `bas` | 依据（条号，回溯锚点） |
| `drq` | 设计要求 |
| `nm` / `lat` / `fam` / `hb` | 名称 / 学名 / 科属 / 生活型 |
| `eco` | 生态习性 |
| `swf` | 水土保持功能 |
| `src` | 出处 |
| `pnm` / `val` / `vr` | 参数名 / 取值 / 取值范围 |
| `std` / `tno` | 标准名 / 表号 |

## 其他常用

| 短键 | 长键 |
|:--|:--|
| `f` / `rel` / `ul` | file / relevance / usage_label |
| `rsn` | reason |
| `k` / `v` / `u` | key / value / unit |
| `en` / `cnt` / `it` | enabled / count / items |
| `vio` / `ck` | violations / checked |
| `sec` / `ph` / `cm` / `fb` | sections / placeholder / computation_marking / forbidden |
| `fld` / `nts` | fields / notes |
| `im` | item |
| `pt` / `dc` / `cb` | point / data_class / classified_by |
| `pic` / `sgc` | project_input_count / spec_given_count |

## 取回被截断的条款原文

```bash
# hc[3] 的 req 被截断，其 s=2、oi=7：
#   1) 从 g.src_files[1]（0起）拿到源文件名
#   2) 用 --oid 取回
python "$SK\scripts\kb_lookup.py" --oid "<源文件名>:7"
```

或直接用编辑器打开 `g.src_files[s-1]` 指向的库内文件，按条号定位。

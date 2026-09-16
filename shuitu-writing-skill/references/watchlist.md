# 时效性检查机制（Source Watchlist）

> 维护 `source_watchlist.json`，列出**全部 Zone A 文件**，定期检查是否过期、被替代、失效。
> 发现变更时生成**变更包**，并据此更新 Zone A 索引与缺口报告。

---

## 零、清单的两类条目（**覆盖比对的基准**）

清单按设计包含两类条目，**覆盖完整性自检只比对第 ① 类**：

| 类 | `file` | `current_status` | 来源 | 是否计入覆盖比对 |
|---|---|---|---|---|
| ① **库内文件** | 指向 `md库\Zone A - 规范层\` 实体文件 | 索引里的真实效力状态 | `zone-a-index.json` | **是**（须与索引逐一对应） |
| ② **库内缺失登记** | **空** | **恒为 `库内缺失`** | Zone C 已批准方案的 `approval_basis_list` 引用频次统计——记「方案引用了、但库里还没有」的依据 | **否**（单独统计） |

> 第 ② 类的意义：Zone C 是已批准方案，它引用的标准即"监管实际要求"，缺哪本就补哪本。
> 因此它必须留在清单里，**不得因"没有对应文件"而被覆盖自检判为不一致**。
> 约定值由 `rules.json` 的 `watchlist.missing_in_vault_status` 声明，脚本不硬编码。

**唯一文件键判据**：以 `file` 为键（`doc_number` 可为「待确认」而重复，不能作键）；
第 ② 类 `file` 为空，比对时先剔除。

---

## 一、清单字段

| 字段 | 含义 | 来源 |
|---|---|---|
| `doc_number` | 文号 / 标准号 | `zone-a-index.json` |
| `title` | 文件名称 | 同上 |
| `current_status` | 当前效力状态 | 同上（初始值） |
| `last_checked` | 上次核对日期 | 人工/脚本写入 |
| `next_check_date` | 下次应核对日期 | 按 `check_interval` 推算 |
| `official_lookup_url` | 官方核验地址 | **见 §三，默认 `待确认`** |
| `file` | 库内相对路径 | 索引 |
| `doc_type` / `scope` | 类型与适用范围 | 索引 |
| `effective_date` | 施行日期 | 索引 |
| `superseded_by` / `supersedes` | 替代关系 | 索引 |
| `check_interval` | 核对周期 | 依状态分档，见 §二 |
| `check_reason` | 为何需持续核对 | 机械生成 |
| `lookup_hint` | 核验线索 | **由发布机关机械生成，见 §三** |
| `priority` | 巡检优先级 | `P0` / `P1` / `P2`，见 §二 |
| `history` | 变更历史 | 每次应用变更包时追加 |

---

## 二、巡检周期与优先级

| 优先级 | 触发条件 | 周期 |
|---|---|---|
| **P0** | 状态非「现行有效」（试行/已失效/已被代替/待核验），或关键字段为 `待确认` | **每季度** |
| **P1** | 国家标准 / 行业标准（易被修订替代） | **每半年** |
| **P2** | 其他现行有效的规范性文件、法律、地方文件 | **每年** |

周期写入 `check_interval`（天）：P0 = 90、P1 = 180、P2 = 365。
`next_check_date = last_checked + check_interval`。

---

## 三、`official_lookup_url` 的处理（**不得编造**）

库内文件由 PDF/Word/云转换而来，绝大多数不含原始网址（**114/271** 为 `待确认`）。

因此本机制规定：

1. `official_lookup_url` **仅在文件正文自身含 URL 时填写**；否则一律 `待确认`。
2. **禁止由模型推测或拼凑网址。** 不写"大概是 http://…"这类内容。
3. 为让巡检可执行，另设 `lookup_hint` 字段，**由 `issuing_body` 机械生成**，形如：

   ```
   lookup_hint: "发布机关：中华人民共和国水利部 → 请在该机关官方网站或标准发布公告中核对编号与状态"
   ```

   `lookup_hint` 是**线索描述**，不是地址断言。它不含具体 URL，因此不存在编造风险。
4. 使用者可随时把真实官方地址回填进 `official_lookup_url`；回填后巡检任务单会直接给出可点击地址。

---

## 四、变更类型

| `change_type` | 含义 |
|---|---|
| `standard_superseded` | 被新版标准替代 |
| `standard_repealed` | 已废止 |
| `standard_amended` | 已修订（现行版本内容变更） |
| `regulation_updated` | 法规/规范性文件更新 |
| `date_confirmed` | 原先 `待确认` 的文号或日期已核实，值本身不变 |
| `no_change` | 核对后确认无变化（仅推进 `last_checked`） |

---

## 五、变更包格式

```json
{
  "change_type": "standard_superseded",
  "old_doc": "GB 50433-2018",
  "new_doc": "GB 50433-2027",
  "effective_date": "2027-06-01",
  "affected_chapters": ["2.1", "2.7", "3.1", "4.1.1", "5.2", "7.6"],
  "action_required": "在方案编制依据中替换标准号，并逐条款核对引用内容是否变化",
  "detected_at": "2026-09-12",
  "evidence": "（核对来源与凭据，必填）",
  "applied_to": ["source_watchlist.json", "zone-a-index.json", "知识缺口报告（需重新生成）"]
}
```

字段说明：
- `old_doc` / `new_doc` — 旧件与新件标识（`new_doc` 为 `null` 表示废止且无替代）
- `affected_chapters` — **从该文件的 `chapter_relevance` 机械生成**，不人工编造
- `action_required` — 按 `change_type` 从模板生成，可人工补充
- `evidence` — **必填**。没有凭据的变更不得应用（防止误改）

---

## 六、闭环（四项缺一不可）

```
①巡检 → ②生成变更包 → ③应用变更 → ④回写三处 + 重新生成缺口报告
```

应用一个变更包时，脚本必须同时完成：

1. 更新 `source_watchlist.json`：`current_status`、`superseded_by`、`last_checked`、`next_check_date`、`history` 追加
2. 更新 `zone-a-index.json`：对应条目的 `status`、`superseded_by`
3. 写 `change_log.jsonl`：留痕（变更包全文 + 应用时间 + 影响章节）
4. 提示**重新生成知识缺口报告**（`analyze_gaps.py`）——因为依据状态变了，缺口结论可能随之变化

**未完成第 2、3 步的变更视为未应用**；脚本会自检并在失败时报错，不允许"只改了清单没改索引"的半闭环状态。

---

## 七、自检项

每次 `--apply-change` 与 `--check` 之后必须执行：

| 自检 | 规则 |
|---|---|
| 字段完整性 | 每条记录 6 个必需字段（`doc_number`/`title`/`current_status`/`last_checked`/`next_check_date`/`official_lookup_url`）均非空 |
| 索引一致性 | 清单中**第 ① 类**条目的 `current_status` 与 `zone-a-index.json` 中同文件的 `status` 一致 |
| 覆盖完整性 | **第 ① 类**条目覆盖 `zone-a-index.json` 的全部条目，不多不少；第 ② 类单独统计、不计入比对，但其 `current_status` 必须是 `库内缺失` 且 `file` 为空 |
| 日期合法性 | `next_check_date > last_checked`，且 `priority` 与 `check_interval` 匹配（**两类条目同等适用**，第 ② 类也不例外） |
| 闭环完整性 | 应用变更后，索引已更新且 `change_log.jsonl` 有对应记录 |

> **`current_status` 必须用受控词表值**（见 `metadata-spec.md` §三 Zone A 行）。
> 特别地，写 **`已被代替`**（不是「已被替代」）——`check_gate.py` 的阻断判定按前者匹配，
> 用词不符会导致该依据**不触发告警与阻断**。

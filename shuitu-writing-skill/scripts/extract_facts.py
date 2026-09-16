#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""事实清单提取层（P0-B）：把「叙述体资料」变成可核可签的事实清单。

## 它解决什么问题

`ingest.py` 是**确定性抽取器**：它按「字段名 + 分隔符 + 值」的范式匹配，
因此只对**规范化写法**（"建设地点：河南省新密市"）有效。

而实际资料几乎都是**叙述体**：

    矿区位于河南省新密市超化镇杏树岗村—郑家庄一带，距新密市城区约11km。

正则抽不出「建设地点」（因为没有"建设地点："这个锚点），于是 82 个字段里
只有个位数命中，数据包近乎全空 —— 下游写作只能输出【待填：…】，成果必然空洞。

**这不是抽取器写坏了，是工具选错了**：从连续叙述里理解事实，是 LLM 擅长的
语义任务，不是正则擅长的模式任务。

## 本脚本的定位：半自动（LLM 提议 → 人工签署）

本脚本**不做 LLM 调用**（技能不引入新依赖，也不假设运行时有 API）。
它做的是**流程工业化**：

1. `--plan`    ：产出「抽取任务书」——把资料切成语义块 + 要抽的字段清单，
                交给当前 AI 助手（agent）阅读，由 agent 产出事实清单 JSON；
2. `--check`   ：**校验 agent 产出的事实清单**——这是本脚本的核心价值：
                每条事实必须能**在原文中逐字定位**（防止 LLM 把自己的
                常识或别的项目的数据写进来）；
3. `--to-ledger`：把已签署的事实清单灌入台账（ledger.json），
                从此跨章口径唯一。

## 为什么必须有 --check（这是防编造的关键）

LLM 读资料时最危险的行为是「**合理但无据**」：资料没写总投资，它按同类
矿山经验填个"约 3000 万元"。这种值表面可信、极难人工发现，且会污染全书。

因此本脚本对每条事实强制做**逐字回溯校验**：

  · 数值型：该数值必须在原文出现，且单位一致；
  · 文本型：其「依据片段」(evidence_span) 必须是原文的**连续子串**；
  · 定位不到 → 标 `unverified`，**不得进入台账**。

铁律：**抽不到就是抽不到**。宁可整节留占位符，也不接受一条无据事实。

用法:
    # 1) 资料 → 任务书（交给 AI 助手读）
    python extract_facts.py --plan --source 既有方案.txt --source 可研.docx --out 抽取任务书.md

    # 2) AI 助手按任务书产出 事实清单.json 后，逐字校验
    python extract_facts.py --check 事实清单.json --source 既有方案.txt --out 校验报告.md

    # 3) 校验通过后灌入台账
    python extract_facts.py --to-ledger 事实清单.json --ledger 台账.json

    # 机读输出
    python extract_facts.py --check 事实清单.json --source 既有方案.txt --json-only

退出码：0 全部通过 · 1 有未定位/冲突项需人工处理 · 2 输入或用法错误
"""
import argparse
import io
import json
import os
import re
import sys

sys.dont_write_bytecode = True

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REF = os.path.join(SKILL, 'references')
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import check_gate as G  # noqa: E402

RULES = G.RULES
DP = RULES.get('data_package', {})
FIELDS = DP.get('fields', [])
PKG_FIELDS = {f['key']: f for f in FIELDS}
FE = RULES.get('fact_extraction', {})

# 语义分块：资料太长时整体喂给 agent 会撞上下文，按标题/段落切块
CHUNK_MAX = int(FE.get('chunk_max_chars', 4000))
CHUNK_MIN = int(FE.get('chunk_min_chars', 200))

NUM_RE = re.compile(r'[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?')
WS_RE = re.compile(r'[\s\u3000]+')


def load_rules_guard():
    """事实清单字段集必须来自 rules.json，不得在代码里另写一份。"""
    if not FIELDS:
        die('rules.json 的 data_package.fields 为空，无法确定抽取字段集')
    return [f['key'] for f in FIELDS]


def die(msg):
    sys.stderr.write('❌ %s\n' % msg)
    sys.exit(2)


def read_text_any(path):
    """读纯文本资料（md/txt/csv/json/html）。docx/pdf 请先用 ingest.py 转，或另存文本。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.txt', '.md', '.markdown', '.csv', '.json', '.html', '.htm'):
        for enc in ('utf-8-sig', 'utf-8', 'gbk', 'gb18030'):
            try:
                with io.open(path, 'r', encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    if ext in ('.docx', '.docm'):
        # 复用 ingest.py 的 docx 读取，避免两份实现漂移
        import ingest as I
        return I.read_docx(path)
    die('extract_facts 只受理文本资料（%s）；docx/pdf 请先用 ingest.py 读取，'
        '或另存为 txt 后重试' % ext)


# ================================================================ 语义分块
def chunk_text(text):
    """按标题行与空行切成语义块，再合并到 CHUNK_MAX 以内。

    分块的意义：agent 逐块阅读比一次性读 26 万字更可靠，
    且每块能独立标注「这块讲什么」，便于回查。
    """
    lines = text.splitlines()
    blocks, cur = [], []
    for ln in lines:
        s = ln.strip()
        is_head = bool(re.match(r'^(#{1,6}\s|第[一二三四五六七八九十百]+[章节]|\d+(?:\.\d+){0,3}\s*\S)', s))
        if is_head and cur:
            blocks.append('\n'.join(cur))
            cur = []
        if s:
            cur.append(ln)
        elif cur:
            blocks.append('\n'.join(cur))
            cur = []
    if cur:
        blocks.append('\n'.join(cur))

    # 合并小块
    merged, buf = [], ''
    for b in blocks:
        if len(buf) + len(b) + 1 <= CHUNK_MAX:
            buf = (buf + '\n' + b) if buf else b
        else:
            if buf:
                merged.append(buf)
            buf = b
    if buf:
        merged.append(buf)
    return [m for m in merged if len(m) >= CHUNK_MIN] or ([text] if text.strip() else [])


# ================================================================ 任务书
def render_plan(sources, out_fields):
    """产出抽取任务书（交给 AI 助手阅读并产出事实清单）。

    任务书只写「怎么抽、抽成什么形状、什么算违规」，
    不写任何答案 —— 答案必须来自资料本身。
    """
    L = []
    A = L.append
    A('# 事实清单抽取任务书')
    A('')
    A('> 本任务书由 `extract_facts.py --plan` 生成。请**阅读下方资料**，')
    A('> 产出符合「输出格式」的 **事实清单 JSON**，然后用 `--check` 校验。')
    A('')
    A('## 一、铁律（违反即整条作废）')
    A('')
    A('1. **只抄不推**：每条事实必须能在资料中**逐字找到依据**。')
    A('   资料没写总投资，就**不要**写 —— 哪怕你按同类项目能"估"出来。')
    A('   典型违规：资料只给"剥离废石量"，你却按经验补一个"运输距离"。')
    A('2. **禁止跨项目串值**：不得把其他项目、其他章节范例、自身常识里的')
    A('   数字写进本清单（技能知识库里存有大量已批方案，极易误取）。')
    A('3. **单位照抄**：资料写"万m³"就写"万m³"，不要换算成 m³。')
    A('   换算会掩盖口径差异，且无法逐字回溯。')
    A('4. **冲突两条都记**：同一事实在不同处不一致时，**两条都列出**并标')
    A('   `"conflict": true`，不要自行择一。人工裁决。')
    A('5. **抽不到就留空**：宁可整节留占位符，也不接受一条无据事实。')
    A('')
    A('## 二、要抽取的字段')
    A('')
    A('按 `rules.json data_package.fields` 的 **%d 个字段**抽取。' % len(out_fields))
    A('字段名、单位、取值类型如下（`required=false` 的可跳过，但抽到就要写）：')
    A('')
    A('| 字段 | 分区 | 单位 | 取值类型 | 必填 |')
    A('|:---|:---|:---|:---|:---:|')
    for f in FIELDS:
        A('| %s | %s | %s | %s | %s |' % (
            f['key'], f.get('section', ''), f.get('unit', '') or '—',
            f.get('value_type', 'text'), '是' if f.get('required') else '否'))
    A('')
    A('## 三、输出格式（严格照此，勿加解释性文字）')
    A('')
    A('```json')
    A('{')
    A('  "meta": {')
    A('    "sources": ["资料文件名"],')
    A('    "extracted_at": "YYYY-MM-DD",')
    A('    "extracted_by": "agent 模型名"')
    A('  },')
    A('  "facts": [')
    A('    {')
    A('      "key": "建设地点",')
    A('      "value": "河南省新密市超化镇杏树岗村—郑家庄一带",')
    A('      "unit": "",')
    A('      "evidence_span": "矿区位于河南省新密市超化镇杏树岗村—郑家庄一带",')
    A('      "source_file": "既有方案.txt",')
    A('      "line": 30,')
    A('      "conflict": false')
    A('    }')
    A('  ]')
    A('}')
    A('```')
    A('')
    A('字段说明：')
    A('')
    A('- `key` —— **必须**是上表字段名之一，一字不差；不在表内的自造键会被拒绝。')
    A('- `value` —— 抽到的值。数值型**只写数字**（如 `"2594.40"`），单位另放 `unit`。')
    A('- `evidence_span` —— **最重要**：资料中的**连续原文片段**，取值为其直接依据。')
    A('  校验器会拿它去原文里逐字查找，**找不到就整条作废**。')
    A('  请包含足够上下文（一般 10–60 字），不要只写字段名。')
    A('- `source_file` / `line` —— 出处，便于人工回查。`line` 不确定可写 0。')
    A('- `conflict` —— 与其他条同 key 不同值时置 true。')
    A('')
    A('## 四、送审资料')
    A('')
    for i, (name, text) in enumerate(sources, 1):
        A('### 资料 %d：%s' % (i, name))
        A('')
        A('（共 %d 字符）' % len(text))
        A('')
        for j, ch in enumerate(chunk_text(text), 1):
            A('<资料块 %d-%d>（约 %d 字）' % (i, j, len(ch)))
            A('')
            A('```')
            A(ch)
            A('```')
            A('')
    return '\n'.join(L)


# ================================================================ 逐字校验
# 全半角标点归一化表：OCR/格式转换常把全角「：，、（）」输出成半角，
# 若不做归一，一条完全正确的事实会因冒号宽窄不同被判"无法定位"（假阴性）。
# 注意：只归一半角与全角的**等价写法**，不改动任何实质字符。
PUNCT_NORM = {
    ':': '：', ',': '，', ';': '；', '(': '（', ')': '）',
    '?': '？', '!': '！', '"': '“', "'": '‘',
}
PUNCT_RE = re.compile('[' + re.escape(''.join(PUNCT_NORM.keys())) + ']')


def norm_for_match(s):
    """归一化以便逐字比对。

    两件事：① 去掉所有空白（换行、全角空格、制表符不应导致误判）；
            ② 半角标点归一为全角，避免因冒号/逗号宽窄差异产生假阴性。
    """
    s = WS_RE.sub('', s or '')
    return PUNCT_RE.sub(lambda m: PUNCT_NORM[m.group(0)], s)


def find_span(hay_norm, needle_norm, orig_text):
    """在归一化文本中定位；返回原文中的 (start, end) 或 None。"""
    if not needle_norm:
        return None
    i = hay_norm.find(needle_norm)
    if i < 0:
        return None
    # 把归一化下标映射回原文下标
    pos, cnt = 0, 0
    start = None
    for idx, ch in enumerate(orig_text):
        if WS_RE.match(ch):
            continue
        if cnt == i and start is None:
            start = idx
        if i <= cnt < i + len(needle_norm):
            end = idx + 1
        cnt += 1
        pos = idx
    if start is None:
        return None
    return (start, end)


def check_facts(facts_doc, sources):
    """逐条校验事实清单。返回 (results, summary)。

    校验项：
      V1 key 是否在 rules.json 字段表内
      V2 evidence_span 是否能在某份资料中逐字定位   ← 核心防编造
      V3 数值型：value 的数字是否出现在 evidence_span 内
      V4 数值型：unit 与字段声明单位是否一致（不一致只提示，不判死）
      V5 同 key 多值是否标了 conflict
    """
    text_by_name = {n: t for n, t in sources}
    hay = {n: norm_for_match(t) for n, t in sources}
    results = []
    valid_keys = set(PKG_FIELDS.keys())

    for idx, fact in enumerate(facts_doc.get('facts', []), 1):
        key = str(fact.get('key', '')).strip()
        val = fact.get('value', '')
        unit = str(fact.get('unit', '') or '').strip()
        span = str(fact.get('evidence_span', '') or '')
        sfile = str(fact.get('source_file', '') or '').strip()
        issues = []

        # V1 字段名合法性
        if key not in valid_keys:
            issues.append({'level': 'error', 'code': 'V1',
                           'msg': '字段名不在 rules.json 字段表内（可能自造键）'})

        # V2 逐字定位
        located = None
        if not span.strip():
            issues.append({'level': 'error', 'code': 'V2',
                           'msg': '缺少 evidence_span，无法回溯（不得进入台账）'})
        else:
            sn = norm_for_match(span)
            cand_files = [sfile] if sfile in hay else list(hay.keys())
            for fn in cand_files:
                if sn and sn in hay[fn]:
                    located = fn
                    break
            if located is None:
                issues.append({'level': 'error', 'code': 'V2',
                               'msg': '依据片段无法在资料中逐字定位 —— 疑似编造或无据推断'})

        # V3 数值回溯
        fdef = PKG_FIELDS.get(key, {})
        if fdef.get('value_type') == 'number':
            nums = NUM_RE.findall(str(val))
            if not nums:
                issues.append({'level': 'error', 'code': 'V3',
                               'msg': '数值型字段的 value 中未发现数字'})
            else:
                span_n = norm_for_match(span)
                for n in nums:
                    if norm_for_match(n) not in span_n:
                        # 必须是 error，不能是 warn。
                        #
                        # 原因（独立复核实测复现）：V2 只保护"依据片段能否定位"，
                        # **不保护片段里的数字是否就是该字段的值**。
                        # 若这里只 warn，`总投资=99999`（依据片段却写「项目基本情况」）
                        # 会带着 span 通过 V2、被 warn 放过，最终以"已确认"进台账 ——
                        # 这正是本脚本要防的编造，却从后门漏了过去。
                        issues.append({'level': 'error', 'code': 'V3',
                                       'msg': '数字 %s 未出现在依据片段内 —— 疑似取自别处或编造' % n})

        # V4 单位一致性
        # 字段声明单位的键是 `unit`（data_package.fields 的实际结构）。
        # 早先误写成 `declared_unit`（该键不存在），导致 V4 永远不触发、且台账单位回退失效。
        decl = (fdef.get('unit') or fdef.get('declared_unit') or '').strip()
        if decl and unit and decl != unit:
            issues.append({'level': 'warn', 'code': 'V4',
                           'msg': '单位「%s」与字段声明单位「%s」不一致（需人工确认口径）' % (unit, decl)})

        results.append({
            'no': idx, 'key': key, 'value': val, 'unit': unit,
            'located_in': located, 'line': fact.get('line', 0),
            'span': span,
            'verified': located is not None and not any(i['level'] == 'error' for i in issues),
            'issues': issues,
        })

    # V5 同 key 多值需标 conflict
    by_key = {}
    for r in results:
        by_key.setdefault(r['key'], []).append(r)
    for key, rs in by_key.items():
        vals = {str(r['value']).strip() for r in rs}
        if len(vals) > 1:
            for r in rs:
                r['issues'].append({'level': 'warn', 'code': 'V5',
                                    'msg': '同一字段存在 %d 个不同取值，应标注 conflict 并交人工裁决' % len(vals)})

    summary = {
        'total': len(results),
        'verified': sum(1 for r in results if r['verified']),
        'unverified': sum(1 for r in results if not r['verified']),
        'errors': sum(1 for r in results for i in r['issues'] if i['level'] == 'error'),
        'warnings': sum(1 for r in results for i in r['issues'] if i['level'] == 'warn'),
        'fields_covered': len({r['key'] for r in results if r['verified']}),
        'fields_total': len(FIELDS),
    }
    return results, summary


# ================================================================ 渲染
def render_check(results, summary, title='事实清单校验报告'):
    L = []
    A = L.append
    A('# %s' % title)
    A('')
    A('| 指标 | 值 |')
    A('|:---|---:|')
    A('| 事实条数 | %d |' % summary['total'])
    A('| **逐字可回溯（可入台账）** | **%d** |' % summary['verified'])
    A('| 无法回溯（作废） | %d |' % summary['unverified'])
    A('| 字段覆盖 | %d / %d |' % (summary['fields_covered'], summary['fields_total']))
    A('| 错误 | %d |' % summary['errors'])
    A('| 提示 | %d |' % summary['warnings'])
    A('')
    A('> **判据**：只有「逐字可回溯」的事实才允许进入台账并用于写作。')
    A('> 无法回溯者一律作废 —— 宁可留【待填】，也不接受无据事实。')
    A('')

    bad = [r for r in results if not r['verified']]
    if bad:
        A('## 一、未通过（不得入台账）')
        A('')
        A('| # | 字段 | 取值 | 问题 |')
        A('|:--:|:---|:---|:---|')
        for r in bad:
            errs = '；'.join('`%s` %s' % (i['code'], i['msg']) for i in r['issues'] if i['level'] == 'error')
            A('| %d | %s | %s | %s |' % (r['no'], r['key'], str(r['value'])[:40], errs))
        A('')

    warns = [r for r in results if r['verified'] and any(i['level'] == 'warn' for i in r['issues'])]
    if warns:
        A('## 二、通过但有提示')
        A('')
        A('| # | 字段 | 取值 | 提示 |')
        A('|:--:|:---|:---|:---|')
        for r in warns:
            w = '；'.join('%s' % i['msg'] for i in r['issues'] if i['level'] == 'warn')
            A('| %d | %s | %s | %s |' % (r['no'], r['key'], str(r['value'])[:40], w))
        A('')

    ok = [r for r in results if r['verified'] and not any(i['level'] == 'warn' for i in r['issues'])]
    if ok:
        A('## 三、通过（可入台账，共 %d 条）' % len(ok))
        A('')
        A('| 字段 | 取值 | 单位 | 出处 |')
        A('|:---|:---|:---|:---|')
        for r in ok:
            A('| %s | %s | %s | %s |' % (r['key'], str(r['value'])[:50], r['unit'], r['located_in']))
        A('')

    missing = [f['key'] for f in FIELDS if f['key'] not in {r['key'] for r in results if r['verified']}]
    if missing:
        A('## 四、未抽到的字段（%d 个）——不代表资料里没有，请人工确认' % len(missing))
        A('')
        A('、'.join(missing))
        A('')
    return '\n'.join(L)


# ================================================================ 主流程
def main():
    ap = argparse.ArgumentParser(description='事实清单提取层（叙述体资料 → 可核事实）')
    ap.add_argument('--plan', action='store_true', help='产出抽取任务书（交给 AI 助手阅读）')
    ap.add_argument('--check', metavar='事实清单.json', help='校验 AI 助手产出的事实清单')
    ap.add_argument('--to-ledger', metavar='事实清单.json', help='把已校验事实灌入台账')
    ap.add_argument('--source', action='append', default=[], help='资料文件（可多次）')
    ap.add_argument('--ledger', default='台账.json', help='台账文件路径（--to-ledger 用）')
    ap.add_argument('--out', help='输出文件（缺省打屏）')
    ap.add_argument('--json-only', action='store_true', help='只输出机读 JSON')
    args = ap.parse_args()

    load_rules_guard()

    if args.plan:
        if not args.source:
            die('--plan 需要至少一个 --source')
        sources = []
        for p in args.source:
            if not os.path.exists(p):
                die('资料不存在：%s' % p)
            sources.append((os.path.basename(p), read_text_any(p)))
        md = render_plan(sources, FIELDS)
        if args.out:
            io.open(args.out, 'w', encoding='utf-8').write(md)
            print('已写出抽取任务书: %s（%d 字符）' % (args.out, len(md)))
            print('下一步：把该文件交给 AI 助手阅读，产出「事实清单.json」后运行 --check')
        else:
            print(md)
        return 0

    if args.check:
        if not os.path.exists(args.check):
            die('事实清单不存在：%s' % args.check)
        if not args.source:
            die('--check 需要 --source 以做逐字回溯校验')
        doc = json.load(io.open(args.check, encoding='utf-8-sig'))
        sources = []
        for p in args.source:
            if not os.path.exists(p):
                die('资料不存在：%s' % p)
            sources.append((os.path.basename(p), read_text_any(p)))
        results, summary = check_facts(doc, sources)
        if args.json_only:
            print(json.dumps({'summary': summary, 'results': results},
                             ensure_ascii=False, indent=2))
        else:
            md = render_check(results, summary)
            if args.out:
                io.open(args.out, 'w', encoding='utf-8').write(md)
                print('已写出校验报告: %s' % args.out)
            else:
                print(md)
        return 0 if summary['unverified'] == 0 else 1

    if args.to_ledger:
        if not os.path.exists(args.to_ledger):
            die('事实清单不存在：%s' % args.to_ledger)
        if not args.source:
            die('--to-ledger 需要 --source 以先做逐字回溯校验（拒绝未校验事实入台账）')
        doc = json.load(io.open(args.to_ledger, encoding='utf-8-sig'))
        sources = [(os.path.basename(p), read_text_any(p)) for p in args.source]
        results, summary = check_facts(doc, sources)
        good = [r for r in results if r['verified']]
        if summary['unverified']:
            sys.stderr.write('⚠ %d 条事实无法回溯，已跳过（不写入台账）\n' % summary['unverified'])
        if not good:
            die('没有任何可回溯的事实，台账未变更')
        import ledger as LD
        led = LD.load(args.ledger) if os.path.exists(args.ledger) else LD.empty_ledger()
        added, refused = 0, 0
        for r in good:
            key = r['key']
            fdef = PKG_FIELDS.get(key, {})
            unit = r['unit'] or (fdef.get('unit') or fdef.get('declared_unit') or '')
            ok, action, detail = LD.register(
                led, key, r['value'], unit=unit,
                provenance=[{'source': 'extract_facts',
                             'file': r['located_in'],
                             'line': r.get('line', 0),
                             'span': r.get('span', '')}])
            if ok:
                added += 1
            else:
                refused += 1
                sys.stderr.write('  ⚠ 台账拒写 %s=%s（%s）\n' % (key, r['value'], action))
        LD.save(args.ledger, led)
        print('已写入台账 %d 条（跳过 %d 条不可回溯，%d 条台账冲突）→ %s'
              % (added, summary['unverified'], refused, args.ledger))
        return 0

    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

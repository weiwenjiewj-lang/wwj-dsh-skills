#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""事实台账：登记「全书已定事实」，让后续章节沿用同一口径。

为什么需要它：方案里最常见的硬伤不是单章写错，而是**跨章口径打架**——
1.6.2 说 3 个防治分区、7.4 说四个；1.6.2 说堆高 ≤6 m、5.2 说 5 m 以内。
两处单看都对，合起来就是方案内部矛盾。台账把「已定稿章节的权威值」固化下来：
  · 写作时：write_chapter 把非本节登记的已确认事实注入指令包，要求沿用；
  · 校核时：check_draft / check_plan 拿台账值与正文比对，直接报冲突。

铁律遵守：
  · 台账文件**只能放在项目工作区**（写入技能目录或知识库一律拒绝）；
  · 新值与台账既有值不一致 → **拒绝写入并报冲突**，不替使用者择一（--force 才覆盖）。

用法:
    python ledger.py --file 台账.json init
    python ledger.py --file 台账.json --draft 1.6.1_稿.md --chapter 1.6.1 --apply
    python ledger.py --file 台账.json --check 1.6.2_稿.md
    python ledger.py --file 台账.json --set "防治责任范围面积=86.5" --unit hm² --chapter 1.6.1
    python ledger.py --file 台账.json --inject --exclude 1.6.2 --json-only
    python ledger.py --file 台账.json show
"""
import argparse
import datetime
import io
import json
import os
import re
import sys

sys.dont_write_bytecode = True   # 技能包不留 __pycache__（避免缓存掩盖规则改动）

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import check_gate as G       # noqa: E402
import ingest as IG          # noqa: E402

RULES = G.RULES
LEDGER_RULES = RULES.get('ledger_rules', {})
FIELDS = {f['key']: f for f in RULES.get('data_package', {}).get('fields', [])}
# 铁律5（零项目残留）要靠 VAULT 判断"目标是否落在知识库内"。
# 原先默认空字符串 —— 知识库搬进技能包后，空值会让这道防护**静默失效**：
# 台账/数据包就可能被写进技能包内的 md库，而脚本不会拦。
# 现统一走 vault_paths，保证始终解析到真实库位置。
from vault_paths import VAULT   # noqa: E402


def guard_path(path):
    """台账/数据包禁止落在技能目录或知识库内（铁律5 零项目残留）。

    实现只有一处：check_gate.guard_out。这里保留名称，供 ledger 内部与
    ingest/calc 等脚本以同一口径调用。
    """
    return G.guard_out(path, '事实台账')


# ================================================================ 存取
def empty_ledger(name=None):
    return {
        'meta': {
            'created_at': datetime.date.today().isoformat(),
            'name': name or '',
            'generated_by': 'shuitu-writing-skill/ledger.py',
            'rule': 'rules.json ledger_rules',
            'storage_policy': '仅存于项目工作区；严禁写入技能目录或知识库',
        },
        'entries': {},
    }


def load(path):
    if not os.path.exists(path):
        return empty_ledger()
    return G.read_json(path, '事实台账')


def save(path, led):
    guard_path(path)
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    G.write_text(path, json.dumps(led, ensure_ascii=False, indent=1), '事实台账')


# ================================================================ 登记
# 台账事实的状态取值只读 rules.json ledger_rules.status_values（台账只登记已确认事实；
# 「待人工确认」是数据包 ingest.py 的字段状态，不入台账）。
LEDGER_STATUS = (G.RULES.get('ledger_rules', {}) or {}).get('status_values') or ['已确认']


def register(led, key, value, unit='', chapter_id='', provenance=None, force=False):
    """返回 (ok, action, detail)。冲突时拒绝写入。"""
    entries = led['entries']
    today = datetime.date.today().isoformat()
    rec = {'key': key, 'value': str(value), 'unit': unit or FIELDS.get(key, {}).get('unit', ''),
           'chapter_id': chapter_id or '', 'provenance': provenance or [],
           'registered_at': today, 'status': LEDGER_STATUS[0]}
    old = entries.get(key)
    if old is None:
        entries[key] = rec
        return True, '新增', rec
    same = str(old.get('value')) == str(value)
    if same:
        if unit and old.get('unit') and IG.norm_unit(old['unit']) != IG.norm_unit(unit):
            return False, '单位冲突', {'key': key, 'ledger': old, 'new': rec}
        return True, '一致', old
    if force:
        rec['superseded'] = [{'value': old.get('value'), 'unit': old.get('unit'),
                              'chapter_id': old.get('chapter_id'), 'registered_at': old.get('registered_at')}]
        entries[key] = rec
        return True, '覆盖', rec
    return False, '冲突', {'key': key, 'ledger': old, 'new': rec}


# ================================================================ 从稿件提取
def facts_from_text(text, source_name, chapter_id=''):
    """按数据包字段字典从稿件中抽取事实 → [(key, value, unit, provenance)]。"""
    out = []
    for key, f in FIELDS.items():
        cands = IG.extract_field(text, source_name, f)
        vals = []
        for c in cands:
            if c['value'] not in vals:
                vals.append(c['value'])
        if len(vals) == 1:
            c0 = cands[0]
            out.append((key, c0['value'], c0['unit'],
                        [{'source': source_name, 'line': c0['line'], 'snippet': c0['snippet'],
                          'chapter_id': chapter_id}]))
    return out


def _table_row_value(field, text, source_name):
    """从 **Markdown 表格行**中取字段值：`| 2 | 建设性质 | — | 新建 | ... |`。

    为什么单独处理：表格是方案里最常见的写法，但 `extract_field` 按"别名+分隔符+值"
    扫描时会跨过表格竖线，把一整行拼成一个值
    （实测：`建设地点` 抽出 `'— | 河南省新密市超化镇 | 杏树岗村至郑家庄一带'`），
    进而把**完全正确**的表格行误判为与台账冲突。

    做法：在含该字段名的表格行内**按竖线切单元格**，取字段名之后第一个非空、
    非占位符（—/无/-）的单元格作为候选值。
    """
    out = []
    for ln, raw in enumerate((text or '').splitlines(), 1):
        if raw.count('|') < 2:
            continue
        cells = [c.strip() for c in raw.strip().strip('|').split('|')]
        for i, c in enumerate(cells):
            if not c or field['key'] not in c and not any(a == c for a in field['aliases']):
                continue
            # 收集字段名之后的所有非空单元格，**全部**作为候选。
            #
            # 为什么不能只取第一个：技术指标表形如
            # `| 3 | 设计生产规模 | 万 t/a | 10 | 矿山规模为小型 |`，
            # 字段名后第一个非空格是**单位**（万 t/a），值在下一格（10）。
            # 只取首格会把单位当成值，从而把正确稿件误判为冲突。
            for nxt in cells[i + 1:]:
                if nxt and nxt not in ('—', '-', '–', '无', '/', ''):
                    out.append({'value': nxt, 'unit': field.get('unit', ''),
                                'line': ln, 'snippet': raw.strip()[:80],
                                'raw': nxt, 'weak': False, 'alias': '表格行'})
            break
    return out


def check_against_ledger(led, text, source_name):
    """稿件与台账比对：ok / 冲突 / 稿件未提及。

    比对前做两件事，避免把**正确**的稿件误报为冲突（实测假阳性来源）：
      ① 表格行按单元格取值，不跨竖线拼接；
      ② 弱别名命中（如「占地面积」之于「工程占地」）不单独构成冲突——
         同一份资料里「占地面积」很可能指排土场占地而非工程总占地。
    """
    res = {'ok': [], 'conflict': [], 'absent': []}
    for key, old in led['entries'].items():
        f = FIELDS.get(key)
        if not f:
            continue
        target = str(old.get('value'))
        # 数值型放宽为"数值相等"（稿件写 133.29hm²、台账存 133.29 属一致）。
        # 同时提取台账值里的**首个数字**，用于「10万t/a」这类"数字+单位串"的比对——
        # 稿件写成「10 万 t/a」（含空格）时，字面比对会失败，但数值其实相同。
        try:
            target_num = float(str(target).replace(',', ''))
        except (TypeError, ValueError):
            m = re.search(r'\d+(?:\.\d+)?', str(target).replace(',', ''))
            target_num = float(m.group(0)) if m else None

        def _num(s):
            m = re.search(r'\d+(?:\.\d+)?', str(s).replace(',', ''))
            return float(m.group(0)) if m else None

        cands = _table_row_value(f, text, source_name) or IG.extract_field(text, source_name, f)
        vals, hit, weak_only = [], False, True
        for c in cands:
            v = str(c['value']).strip()
            if not c.get('weak'):
                weak_only = False
            if v not in vals:
                vals.append(v)
            if v == target:
                hit = True
            elif target_num is not None and _num(v) == target_num:
                # 数值相同即视为一致（容忍「10万t/a」与「10 万 t/a」的写法差异）
                hit = True
        if not vals:
            res['absent'].append({'key': key, 'ledger_value': target})
        elif hit:
            res['ok'].append({'key': key, 'value': target})
        elif weak_only:
            # 仅弱别名命中 → 只作提示，不判冲突
            res['absent'].append({'key': key, 'ledger_value': target})
        else:
            res['conflict'].append({
                'key': key, 'ledger_value': target, 'ledger_unit': old.get('unit'),
                'ledger_chapter': old.get('chapter_id'),
                'draft_values': vals,
                'draft_provenance': [{'line': c['line'], 'value': c['value'], 'snippet': c['snippet']}
                                     for c in cands],
            })
    return res


def inject_facts(led, exclude_chapter=None, max_items=None):
    """给写作指令包用的已定事实（默认排除本节登记的，避免自我复述）。"""
    inj = LEDGER_RULES.get('injection', {})
    max_items = max_items or inj.get('max_items', 40)
    items = []
    for key, rec in led['entries'].items():
        if exclude_chapter and rec.get('chapter_id') == exclude_chapter:
            continue
        if rec.get('status') not in LEDGER_STATUS:
            continue
        items.append({'key': key, 'value': rec.get('value'), 'unit': rec.get('unit', ''),
                      'registered_by': rec.get('chapter_id', ''),
                      'provenance': rec.get('provenance', [])[:1]})
    items = items[:max_items]
    return {
        'enabled': bool(inj.get('enabled', True)),
        'count': len(items),
        'items': items,
        'rule': inj.get('rule', '全书已定事实，本节必须沿用，不得改写口径'),
        'source': 'ledger_rules.injection',
    }


# ================================================================ CLI
def main():
    ap = argparse.ArgumentParser(description='事实台账：跨章口径登记与核对')
    ap.add_argument('--file', required=True, help='台账 json 路径（项目工作区内）')
    ap.add_argument('action', nargs='?', default=None, choices=[None, 'init', 'show', 'check'],
                    help='init 建台账 / show 查看 / check 用台账核对稿件')
    ap.add_argument('--name', default=None, help='init 时的台账名称')
    ap.add_argument('--draft', action='append', default=[], help='稿件路径，可重复')
    ap.add_argument('--show', action='store_true', help='查看全部已定事实（等价于 show 子命令）')
    ap.add_argument('--check', action='store_true', help='用台账核对稿件（等价于 check 子命令）')
    ap.add_argument('--chapter', default='', help='稿件对应的节点号')
    ap.add_argument('--apply', action='store_true', help='把稿件中提取到的事实写入台账')
    ap.add_argument('--set', action='append', default=[], help='直接登记 key=value，可重复')
    ap.add_argument('--unit', default='', help='配合 --set 的单位')
    ap.add_argument('--force', action='store_true', help='冲突时覆盖（默认拒绝）')
    ap.add_argument('--inject', action='store_true', help='输出注入用的已定事实')
    ap.add_argument('--exclude', default=None, help='注入时排除的节点号')
    ap.add_argument('--json-only', action='store_true')
    a = ap.parse_args()

    guard_path(a.file)
    led = load(a.file)
    out = {'file': a.file}

    # 只有「不加任何动作地首次调用」才隐式建台账；显式 --show / --check 是只读动作，
    # 路径写错时不得顺手在项目目录里造一个空台账。
    explicit_read = a.action in ('show', 'check') or a.show or a.check
    if a.action == 'init' or (not os.path.exists(a.file) and not (a.draft or a.set or a.inject)
                              and not explicit_read):
        if not os.path.exists(a.file):
            led = empty_ledger(a.name)
            save(a.file, led)
            out['action'] = 'init'
            out['entries'] = 0
    if a.name:
        led['meta']['name'] = a.name

    if a.set:
        out['set'] = []
        for kv in a.set:
            if '=' not in kv:
                raise SystemExit('--set 需要 key=value 形式：%s' % kv)
            k, v = kv.split('=', 1)
            ok, act, detail = register(led, k.strip(), v.strip(), a.unit, a.chapter, [], a.force)
            out['set'].append({'key': k.strip(), 'action': act, 'ok': ok, 'detail': detail})
        save(a.file, led)

    if a.draft and a.apply:
        out['applied'] = []
        for p in a.draft:
            if not os.path.exists(p):
                out['applied'].append({'draft': p, 'error': '文件不存在'})
                continue
            text = io.open(p, encoding='utf-8-sig', errors='ignore').read()
            name = os.path.basename(p)
            rows = []
            for key, value, unit, prov in facts_from_text(text, name, a.chapter):
                ok, act, detail = register(led, key, value, unit, a.chapter, prov, a.force)
                rows.append({'key': key, 'value': value, 'unit': unit, 'action': act, 'ok': ok,
                             'detail': detail if not ok else None})
            out['applied'].append({'draft': p, 'facts': len(rows), 'rows': rows})
        save(a.file, led)

    if a.draft and (a.action == 'check' or a.check or not a.apply):
        out['check'] = []
        for p in a.draft:
            if not os.path.exists(p):
                out['check'].append({'draft': p, 'error': '文件不存在'})
                continue
            text = io.open(p, encoding='utf-8-sig', errors='ignore').read()
            r = check_against_ledger(led, text, os.path.basename(p))
            out['check'].append({'draft': p, 'ok': len(r['ok']), 'conflict': r['conflict'],
                                 'absent': [x['key'] for x in r['absent']]})

    if a.inject:
        out['inject'] = inject_facts(led, a.exclude)

    out['summary'] = {'entries': len(led['entries']),
                      'keys': sorted(led['entries'].keys())}

    if a.action == 'show' or a.show or (not a.json_only and not (a.set or a.draft or a.inject)):
        if not os.path.exists(a.file):
            # 以前这里静默当成空台账打印「0 条」，路径打错时看不出来，会以为之前的登记丢了
            print('⚠ 台账文件不存在：%s' % a.file)
            print('  （视为新台账；登记第一条事实时会创建该文件）')
        print('台账：%s（%d 条）' % (a.file, len(led['entries'])))
        for k in sorted(led['entries']):
            r = led['entries'][k]
            print('  %-18s %-14s %-8s <- %s' % (k, r.get('value'), r.get('unit') or '', r.get('chapter_id') or '—'))
    else:
        print(json.dumps(out, ensure_ascii=False, indent=1))

    bad = False
    for r in out.get('set', []):
        if not r.get('ok'):
            bad = True
    for row in out.get('applied', []):
        for r in row.get('rows', []):
            if r['action'] == '冲突':
                bad = True
    for row in out.get('check', []):
        if row.get('conflict'):
            bad = True
    # 稿件路径不存在这类输入错误按契约退 2（原来会被吞成 0，看不出路径写错了）
    miss = any(row.get('error') for row in out.get('check', [])) or \
        any(row.get('error') for row in out.get('applied', []))
    sys.exit(2 if miss else (1 if bad else 0))


if __name__ == '__main__':
    main()

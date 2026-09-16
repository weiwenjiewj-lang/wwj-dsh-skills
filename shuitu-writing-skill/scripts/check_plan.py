#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第七层：全书终检器。定稿前跑一次，回答「这本方案能不能送审」的机械问题。

与 check_draft.py 的分工：
  · check_draft —— 单章/单节写完就查（覆盖、占位符、数字、标题、文风、篇幅、台账）；
  · check_plan  —— 全书合稿后查（95 节点覆盖、全书篇幅折算、表1 字段齐备、附件附图、
                   跨章口径一致性、术语与文风全局统计）。

用法:
    python check_plan.py --draft 全书.md --province 河南省 --ledger 台账.json
    python check_plan.py --draft 第1章.md --draft 第2章.md --province 河南省 --out 终检报告.md
    python check_plan.py --draft 全书.md --json-only
"""
import argparse, io, json, os, re, sys

sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import check_gate as G   # noqa: E402
import budget as B       # noqa: E402
import check_draft as D  # noqa: E402

RULES = G.RULES
NODES = G.NODES
REFTABLES = json.load(io.open(os.path.join(G.REF, 'tables.json'), encoding='utf-8-sig')).get('报告书', {})
PLACEHOLDER_RE = D.PH_RE


def node_coverage(text, targets=None):
    """95 节点覆盖：正文里是否有该节点的标题行（标题逐字比对，容错章节号前后的空格）。

    「（不涉及的不列）」类是条件节点：项目确实不涉及时可以不列，
    但必须在正文里出现「不涉及」的说明，因此单列一类，不与真缺失混在一起。
    """
    heads = [m.group(1) for m in re.finditer(r'^#{1,6}\s*(.+?)\s*$', text, re.M)]
    flat = [re.sub(r'\s+', '', h) for h in heads]
    missing_id, missing_conditional, missing_title, extra = [], [], [], []
    for cid, n in NODES.items():
        title = re.sub(r'\s+', '', n['title'])
        has_id = any(h.startswith(cid) and (len(h) == len(cid) or not h[len(cid)].isdigit()) for h in flat)
        if has_id:
            if n.get('node_kind') != 'container' and title[:12] not in ''.join(flat):
                missing_title.append({'chapter_id': cid, 'title': n['title']})
            continue
        row = {'chapter_id': cid, 'title': n['title'], 'level': n.get('level'),
               'kind': n.get('node_kind')}
        # 只有模板自带「（不涉及的不列）」标注的节点才允许不列；
        # 带适用条件的节点（conditional_variants）仍须列，只是内容按情形分别写。
        if '不列' in title:
            missing_conditional.append(row)
        else:
            missing_id.append(row)
    for h in flat:
        m = re.match(r'^(\d+(?:\.\d+)*|表\d+|附表|附件|附图)', h)
        if m:
            cid = m.group(1)
            if cid not in NODES and cid.split('.')[0] in [c for c in NODES if '.' not in c]:
                extra.append(cid)
    decl = '不涉及' in re.sub(r'\s+', '', text)
    return {'total_nodes': len(NODES), 'covered': len(NODES) - len(missing_id) - len(missing_conditional),
            'missing': missing_id, 'missing_conditional': missing_conditional,
            'title_mismatch': missing_title, 'extra_nodes': sorted(set(extra)),
            'not_involved_declared': decl,
            'note': '模板是唯一骨架（铁律2）：missing 必须补齐；conditional 若确不涉及，须在正文写明'
                    '「本项目不涉及××」并说明理由；extra_nodes 若属模板外自增小节，须改为模板节点下的段落而非新编号。'}


def table1_check(text):
    """表1 特性表字段齐备性（55 个栏目逐字）。"""
    t1 = REFTABLES.get('表1 水土保持方案特性表') or {}
    fields = [re.sub(r'\s+', '', str(f)) for f in (t1.get('fields') or [])]
    flat = re.sub(r'\s+', '', text)
    missing = [f for f in fields if f and f not in flat]
    has_table = '水土保持方案特性表' in flat
    return {'fields_total': len(fields), 'present': len(fields) - len(missing),
            'missing': missing, 'has_table': has_table,
            'note': '表1 是评审必查表；缺项多为项目数据未落（用【待填：…】占位并列入补数清单）'}


def attachments_check(text, province=None):
    """附件/附表/附图清单：按模板要求核是否列明（内容本身人工核对）。

    核对项与说明取自 rules.json report_form_gate.attachment_check，不再在代码里写死。
    """
    ac = (G.RULES.get('report_form_gate', {}) or {}).get('attachment_check') or {}
    keys = ac.get('keys') or ['附件', '附表', '附图']
    got = {}
    for k in keys:
        m = re.search(r'#+\s*%s(.{0,600})' % k, text, re.S)
        got[k] = bool(m and re.search(r'\d|无|见|清单', m.group(1)))
    return {'listed': got, 'all_listed': all(got.values()),
            'note': ac.get('note') or '附件清单须逐项列出；附图须列出图号图名。此处只查是否列明。'}


def cross_chapter_terms(text):
    """跨章口径一致性：同名数值事实与术语的全局统计（复用 C3/C5 的口径）。"""
    facts = D.extract_facts(text)
    conflicts = D.find_conflicts([('全书', f) for f in facts])
    SR = RULES.get('style_rules', {})
    term_hits = {}
    for pair in SR.get('term_consistency', {}).get('pairs_forbidden_both', []):
        counts = {w: D._standalone_count(text, w, pair) for w in pair}
        present = [w for w, c in counts.items() if c > 0]
        if len(present) > 1:
            term_hits['／'.join(present)] = counts
    return {'facts': len(facts),
            'number_conflicts': [c for c in conflicts if not c['downgraded']],
            'number_notes': [c for c in conflicts if c['downgraded']],
            'inconsistent_terms': term_hits}


def check(drafts, province=None, city=None, ledger_path=None, report_form=False):
    texts, miss_files = {}, []
    for d in drafts:
        if not os.path.exists(d):
            miss_files.append(d)
            continue
        texts[d] = io.open(d, encoding='utf-8-sig', errors='ignore').read()
    if miss_files:
        return {'error': '❌ 找不到稿件：%s' % '、'.join(miss_files)}
    joined = '\n'.join(texts.values())

    r = {'drafts': drafts, 'chars': B.count_chars(joined),
         'snapshot_warning': G.SNAPSHOT_WARNING}
    if report_form:
        r['coverage'] = None
        r['note'] = '报告表分支：不做 95 节点覆盖检查，按两区块字段逐字段人工核对'
    else:
        r['coverage'] = node_coverage(joined)
    r['table1'] = table1_check(joined)
    r['attachments'] = attachments_check(joined, province)
    r['cross'] = cross_chapter_terms(joined)
    r['length'] = D.length_check(texts)
    r['placeholders'] = {
        'total': len(PLACEHOLDER_RE.findall(joined)),
        'unique': sorted({'%s：%s' % (m.group(1), m.group(2).strip()) for m in PLACEHOLDER_RE.finditer(joined)}),
    }
    r['style'] = D.style_check(joined)
    r['ledger'] = D.ledger_check(ledger_path, texts) if ledger_path else {
        'checked': False, 'note': '未指定 --ledger：跨章口径只做了正文内部比对'}

    problems, warnings = [], []
    cov = r.get('coverage')
    if cov:
        if cov['missing']:
            problems.append('全书覆盖：缺 %d 个模板节点标题（%s%s）'
                            % (len(cov['missing']),
                               '、'.join(x['chapter_id'] for x in cov['missing'][:8]),
                               ' 等' if len(cov['missing']) > 8 else ''))
        if cov['extra_nodes']:
            problems.append('全书覆盖：出现模板外章节号 %s（铁律2：模板是唯一骨架）'
                            % '、'.join(cov['extra_nodes'][:6]))
        if cov['missing_conditional']:
            warnings.append('条件节点未列 %d 个（%s）：若确不涉及，须在正文写明「不涉及」及理由'
                            % (len(cov['missing_conditional']),
                               '、'.join(x['chapter_id'] for x in cov['missing_conditional'][:8])))
        if cov['missing_conditional'] and not cov['not_involved_declared']:
            problems.append('条件节点未列且全文未见「不涉及」说明——评审会视为漏项')
    if r['table1']['missing']:
        warnings.append('表1 特性表缺 %d 个栏目：%s'
                        % (len(r['table1']['missing']), '、'.join(r['table1']['missing'][:8])))
    if not r['attachments']['all_listed']:
        warnings.append('附件/附表/附图 未全部列明：%s'
                        % '、'.join(k for k, v in r['attachments']['listed'].items() if not v))
    if r['cross']['number_conflicts']:
        problems.append('跨章数字一致性疑似冲突 %d 组' % len(r['cross']['number_conflicts']))
    if r['cross']['inconsistent_terms']:
        problems.append('术语不统一：%s' % '；'.join(r['cross']['inconsistent_terms']))
    if r['placeholders']['total']:
        warnings.append('占位符 %d 处待回填（定稿前必须清零或人工确认）' % r['placeholders']['total'])
    bk = r['length']['book']
    pages = bk['estimated_pages_to_date']
    pt = bk['page_target']
    if pages < pt['min']:
        warnings.append('全书篇幅 %s 页 < 目标下限 %s 页：总量偏少，需按章节预算补深'
                        % (pages, pt['min']))
    elif pages > pt['max']:
        warnings.append('全书篇幅 %s 页 > 目标上限 %s 页：总量溢出，需压缩重复论述'
                        % (pages, pt['max']))
    if r['style'].get('problems'):
        problems.extend(r['style']['problems'])
    if r['ledger'].get('checked') and r['ledger'].get('conflicts'):
        problems.append('与台账冲突 %d 处' % len(r['ledger']['conflicts']))
    r['problems'] = problems
    r['warnings'] = warnings
    r['verdict'] = ('需处理：%s' % '；'.join(problems)) if problems else \
        '机械终检未发现问题（篇幅 %s 页 / 目标 %s–%s；合规性仍以 Zone A 与人工审查为准）' \
        % (pages, pt['min'], pt['max'])
    return r


def render(r):
    if 'error' in r:
        return '# 全书终检\n\n%s\n' % r['error']
    L = ['# 全书终检报告', '']
    L.append('- 稿件：%s' % '；'.join(r['drafts']))
    L.append('- 正文口径字数：%s' % r['chars'])
    if r.get('snapshot_warning'):
        L.append('- ⚠ %s' % r['snapshot_warning'])
    L.append('')
    cov = r.get('coverage')
    if cov:
        L.append('## 一、模板覆盖（%d 节点）' % cov['total_nodes'])
        L.append('')
        L.append('已覆盖 %d / %d。%s' % (cov['covered'], cov['total_nodes'], cov['note']))
        L.append('')
        if cov['missing']:
            L.append('| 缺节点 | 标题 | 层级 | 类型 |')
            L.append('|---|---|---|---|')
            for x in cov['missing']:
                L.append('| %s | %s | %s | %s |' % (x['chapter_id'], x['title'], x['level'], x['kind']))
            L.append('')
        if cov['title_mismatch']:
            L.append('⚠ 有节点号但标题不一致（可能改字，须逐字回改）：%s'
                     % '、'.join('%s %s' % (x['chapter_id'], x['title']) for x in cov['title_mismatch'][:8]))
            L.append('')
        if cov['missing_conditional']:
            L.append('条件节点（模板标「不涉及的不列」）未出现 %d 个：%s'
                     % (len(cov['missing_conditional']),
                        '、'.join('%s %s' % (x['chapter_id'], x['title']) for x in cov['missing_conditional'][:10])))
            L.append('')
            L.append('→ 若项目确不涉及，须在相应位置写明「本项目不涉及××」并说明理由（当前全文%s「不涉及」字样）。'
                     % ('已出现' if cov['not_involved_declared'] else '未见'))
            L.append('')
        if cov['extra_nodes']:
            L.append('⚠ 模板外章节号：%s' % '、'.join(cov['extra_nodes']))
            L.append('')
    t1 = r['table1']
    L.append('## 二、表1 水土保持方案特性表')
    L.append('')
    L.append('栏目 %d 个，正文中出现 %d 个。%s' % (t1['fields_total'], t1['present'], t1['note']))
    if t1['missing']:
        L.append('')
        L.append('缺：%s' % '、'.join(t1['missing']))
    L.append('')
    att = r['attachments']
    L.append('## 三、附件 / 附表 / 附图')
    L.append('')
    for k, v in att['listed'].items():
        L.append('- %s：%s' % (k, '已列明' if v else '未列明'))
    L.append('')
    cr = r['cross']
    L.append('## 四、跨章口径一致性')
    L.append('')
    L.append('抽取数值事实 %d 条；疑似冲突 %d 组（降级提示 %d 组）；术语不统一 %d 处'
             % (cr['facts'], len(cr['number_conflicts']), len(cr['number_notes']),
                len(cr['inconsistent_terms'])))
    for c in cr['number_conflicts']:
        L.append('')
        L.append('⚠ **%s**：%s' % (c['key'], '、'.join(str(v) for v in c['values'])))
        for o in c['occurrences'][:4]:
            L.append('  - [L%d] %s' % (o['line'], o['context']))
    for k, v in cr['inconsistent_terms'].items():
        L.append('')
        L.append('❌ 术语：%s（%s）' % (k, '、'.join('%s×%d' % (a, b) for a, b in v.items())))
    L.append('')
    ln = r['length']
    L.append('## 五、全书篇幅')
    L.append('')
    bk = ln['book']
    L.append('已写 %s 字 ≈ **%s 页**（目标 %s–%s 页）——完成度 %s%%；未归入节点 %s 字'
             % (bk['written_chars'], bk['estimated_pages_to_date'],
                bk['page_target']['min'], bk['page_target']['max'],
                round((bk['book_completion_ratio'] or 0) * 100), ln['unattributed_chars']))
    L.append('')
    L.append('| 章 | 已写 | 目标 | 达标区间 | 判定 |')
    L.append('|---|---|---|---|---|')
    for v in ln['chapter_verdicts']:
        L.append('| 第%s章 | %s | %s | %s–%s | %s |'
                 % (v['chapter'], v['actual'], v['target'], v['min'], v['max'], v['status']))
    L.append('')
    ph = r['placeholders']
    L.append('## 六、占位符（定稿前须清零）')
    L.append('')
    L.append('共 %d 处 / %d 项。' % (ph['total'], len(ph['unique'])))
    for u in ph['unique'][:20]:
        L.append('- %s' % u)
    if len(ph['unique']) > 20:
        L.append('- …（其余 %d 项见 JSON）' % (len(ph['unique']) - 20))
    L.append('')
    st = r['style']
    L.append('## 七、文风与深度')
    L.append('')
    L.append('正文口径字数 %s；空话套话 %d 次。' % (st['chars'], sum(st['banned_hits'].values())))
    for f in st.get('findings', []):
        L.append('- %s' % f)
    if st.get('warnings'):
        L.append('')
        for w in st['warnings']:
            L.append('- ⚠ %s' % w)
    L.append('')
    lg = r['ledger']
    if lg.get('checked'):
        L.append('## 八、台账核对')
        L.append('')
        L.append('台账 %s（%d 条）：一致 %d 项 / 冲突 %d 处 / 未提及 %d 项'
                 % (lg.get('ledger_file'), lg.get('entries', 0), len(lg.get('consistent') or []),
                    len(lg.get('conflicts') or []), len(lg.get('not_mentioned') or [])))
        for c in (lg.get('conflicts') or []):
            L.append('- ❌ %s：台账 %s ≠ 本稿 %s' % (c['key'], c['ledger_value'],
                                                 '、'.join(map(str, c['draft_values']))))
        L.append('')
    L.append('## 结论')
    L.append('')
    L.append(r['verdict'])
    if r.get('warnings'):
        L.append('')
        L.append('### 提示')
        L.append('')
        for w in r['warnings']:
            L.append('- ⚠ %s' % w)
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser(description='全书终检：覆盖 / 表1 / 附件附图 / 跨章口径 / 篇幅 / 占位符 / 文风 / 台账')
    ap.add_argument('--draft', action='append', required=True, help='稿件路径，可多个（多章合稿）')
    ap.add_argument('--province', default=None)
    ap.add_argument('--city', default=None)
    ap.add_argument('--ledger', default=None)
    ap.add_argument('--report-form', action='store_true')
    ap.add_argument('--json-only', action='store_true')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    r = check(a.draft, a.province, a.city, ledger_path=a.ledger, report_form=a.report_form)
    txt = render(r)
    if a.out:
        G.write_text(a.out, txt, '终检报告')
        print('已写出终检报告:', a.out)
    print(json.dumps(r, ensure_ascii=False, indent=1) if a.json_only else txt)
    sys.exit(2 if 'error' in r else (1 if r.get('problems') else 0))


if __name__ == '__main__':
    main()

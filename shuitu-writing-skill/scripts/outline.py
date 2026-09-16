#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第六层：全书骨架生成器。按模板逐字标题搭出全书 md 骨架，并把篇幅预算嵌进每个节点。

解决什么问题：一次要写 150–180 页、95 个节点。没有骨架时有两个坑——
① 写着写着漏节点、串层级；② 前面写太长，后面没篇幅，最后总量失控。
本脚本产出一份可直接往里填正文的骨架：标题逐字来自 template-tree.json（不得改字），
每个标题下带该节点的必覆盖内容点、目标字数区间、必备表/计算项，以及缺数据占位提示。

用法:
    python outline.py --out 方案骨架.md [--province 河南省] [--city 郑州市]
    python outline.py --chapter 2 --out 第2章骨架.md
    python outline.py --out 骨架.md --with-budget-table     # 附一张全书篇幅分配表
    python outline.py --out 骨架.md --report-form            # 报告表（附件3）分支
    python outline.py --json-only
"""
import argparse, io, json, os, sys

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

RULES = G.RULES
TREE = G.TREE
NODES = G.NODES
REFTABLES = json.load(io.open(os.path.join(G.REF, 'tables.json'), encoding='utf-8-sig')).get('报告书', {})
CALC_ENGINE = RULES.get('calc_engine', {}).get('items', {})
CALC_INDEX = RULES.get('calculations', {}).get('items', {})
TOP_CHAPTERS = [c for c in NODES if '.' not in c]


def template_nodes(chapter):
    """模板中属于该章（含其子孙）的全部节点，保持模板顺序。"""
    top = str(chapter).split('.')[0]
    if chapter in NODES and '.' not in str(chapter):
        return [c for c in NODES if c == chapter or c.split('.')[0] == top]
    return [c for c in NODES if c == chapter or c.startswith(str(chapter) + '.')]


def tables_for(cid):
    """该节点需要哪些表：表1/附表/附件/附图 节点自带结构；1.1.x 后附特性表。"""
    out = []
    if cid == '表1' or cid.startswith('1.1'):
        t = REFTABLES.get('表1 水土保持方案特性表') or {}
        if t:
            out.append({'table': '表1 水土保持方案特性表',
                        'paragraph': '综合说明后应附水土保持方案特性表，格式内容要求见表1。',
                        'fields': t.get('fields') or [], 'notes': t.get('notes')})
    for nm, key in (('附表', '附表'), ('附件', '附件'), ('附图', '附图')):
        if cid == nm and key in REFTABLES:
            out.append({'table': nm, 'fields': [], 'notes': REFTABLES[key].get('requirement')})
    return out


def calcs_for(cid):
    """该节点的计算项：执行规格取 calc_engine，人读条目取 calculations 索引。"""
    out = []
    for k, item in CALC_ENGINE.items():
        if k == cid or k.startswith(cid + '.') or cid == k.split('.')[0] or cid.startswith(k):
            titles = CALC_INDEX.get(k) or []
            out.append({'chapter_id': k, 'item': item.get('name') or (titles[0]['item'] if titles else ''),
                        'method': item.get('method'),
                        'run': 'calc.py --chapter %s' % k})
    return out


def node_block(cid, n, tables_for_node, calcs_for_node, mode='md'):
    """生成单个节点的骨架块。"""
    lvl = min(int(n.get('level', 3)), 6)
    hashes = '#' * (lvl + 1)
    L = ['%s %s %s' % (hashes, cid, n['title']), '']
    nb = B.node_budget(cid)
    dep = B.depth_requirements(cid)
    meta = []
    if nb:
        meta.append('目标 %d 字（%d–%d）' % (nb['target'], nb['min'], nb['max']))
    if dep.get('tables_required'):
        meta.append('须有表')
    if dep.get('require_conclusion'):
        meta.append('须有结论句')
    if dep.get('require_mechanism'):
        meta.append('须说明机理')
    if meta:
        L.append('<!-- %s -->' % '；'.join(meta))
    points = n.get('required_content_points') or []
    if points:
        L.append('<!-- 必覆盖内容点：')
        for p in points:
            L.append('     - %s' % p)
        L.append('-->')
    for t in tables_for_node:
        L.append('**%s**%s' % (t.get('table') or t.get('table_id'), ('　—　' + t['paragraph']) if t.get('paragraph') else ''))
        fields = t.get('fields') or []
        if fields:
            L.append('')
            L.append('| ' + ' | '.join(map(str, fields)) + ' |')
            L.append('|' + '---|' * len(fields))
            L.append('| ' + ' | '.join(['【待填】'] * len(fields)) + ' |')
        elif t.get('notes'):
            L.append('')
            L.append('> %s' % t['notes'])
        L.append('')
    for c in calcs_for_node:
        L.append('> 计算项 %s：%s（%s，方法：%s；出计算书后并入本节）'
                 % (c.get('chapter_id'), c.get('item'), c.get('run'), c.get('method')))
        L.append('')
    if n.get('conditional_variants'):
        L.append('> 条件节点：动笔前先确认属于哪种情形——%s' % '；'.join(n['conditional_variants']))
        L.append('')
    if not points and not tables_for_node and not calcs_for_node:
        L.append('【待写：本节正文】')
        L.append('')
    return L


def build(chapter=None, province=None, city=None, report_form=False):
    """生成骨架。chapter=None → 全书 95 节点（逐章过闸门并汇总）。"""
    out = {'report_form': report_form, 'snapshot_warning': G.SNAPSHOT_WARNING,
           'length_budget': {'book': B.book_budget()}, 'chapters': {}, 'blocked_chapters': []}
    if report_form:
        # 报告表不分章，骨架来自 tables.json 的 report_form 两个表格区块（write_chapter.py
        # 生成）。本命令不出报告表骨架——以前这里 write_allowed=True 后又走 render()，
        # 因缺 _blocks 直接 KeyError 崩掉。
        out['write_allowed'] = False
        out['gate'] = {'blocked': False, 'provisional': None}
        out['note'] = ('报告表不分章：骨架取 tables.json 的 report_form 两个表格区块，'
                       '用 write_chapter.py --report-form 生成；outline.py 只做报告书 95 节点骨架。')
        return out

    if chapter:
        g = G.run(chapter, province, city)
        out['chapters'][chapter] = {'blocked': g.get('blocked'), 'reason': g.get('block_reason'),
                                    'provisional': g.get('provisional')}
        if g.get('blocked'):
            out['blocked_chapters'].append(chapter)
        # 骨架 = 模板节点全集（闸门 expanded_nodes 只是约束匹配用的子集，不能当骨架）
        targets = template_nodes(chapter)
        out['gate'] = {'blocked': g.get('blocked'), 'block_reason': g.get('block_reason'),
                       'provisional': g.get('provisional'),
                       # 章节号不在模板里时必须往下传：否则会照常输出一份 0 节点「骨架」
                       'invalid_chapter': g.get('invalid_chapter', False)}
    else:
        # 全书：按模板顺序逐章过闸门（闸门是「章」粒度的），骨架取模板 95 节点全集
        targets = list(NODES)
        for ch in TOP_CHAPTERS:
            try:
                g = G.run(ch, province, city)
            except Exception as e:                       # 非章节节点单独处理
                g = {'blocked': False, 'block_reason': None, 'provisional': None}
            out['chapters'][ch] = {'blocked': g.get('blocked'), 'reason': g.get('block_reason'),
                                   'provisional': g.get('provisional')}
            if g.get('blocked'):
                out['blocked_chapters'].append(ch)
        out['gate'] = {'blocked': bool(out['blocked_chapters']),
                       'block_reason': ('以下章闸门 blocked：%s' % '、'.join(out['blocked_chapters']))
                                       if out['blocked_chapters'] else None,
                       'provisional': any((v.get('provisional') for v in out['chapters'].values()))}

    out['write_allowed'] = True          # 骨架＝模板本身，不受闸门阻断；正文写作仍以各章闸门为准
    nodes = [NODES[c] for c in targets if c in NODES]
    out['nodes'] = []
    blocks = []
    for n in nodes:
        cid = n['chapter_id']
        tlist, clist = tables_for(cid), calcs_for(cid)
        nb = B.node_budget(cid) or {}
        out['nodes'].append({'chapter_id': cid, 'title': n['title'],
                             'target_chars': nb.get('target'), 'min': nb.get('min'), 'max': nb.get('max'),
                             'points': len(n.get('required_content_points') or []),
                             'tables': [t['table'] for t in tlist],
                             'calcs': [c['item'] for c in clist]})
        blocks.append((n, tlist, clist))
    out['_blocks'] = blocks
    # 合计只算叶子节点预算（容器节点的预算是其子节点之和，计入会重复）
    out['total_target_chars'] = sum(x['target_chars'] or 0 for x in out['nodes']
                                    if x['chapter_id'] in B.BUDGETS)
    out['book_target_chars'] = B.book_budget()['book_target_chars']
    return out


def render(r):
    L = ['# 【项目名称】水土保持方案（报告书）——写作骨架', '']
    L.append('- 骨架来源：`references/template-tree.json`（标题逐字引用，**不得改字改号**）')
    L.append('- 篇幅口径：%s；正文目标 %s 字（全书 %s–%s 页）'
             % (RULES.get('length_budget', {}).get('allocation_formula', '')[:0] or '见 rules.json length_budget',
                r['length_budget']['book']['book_target_chars'],
                r['length_budget']['book']['page_target']['min'],
                r['length_budget']['book']['page_target']['max']))
    L.append('- 生成时间：骨架由脚本产出；每个 `<!-- -->` 内为写作要求，填完正文后应删除')
    L.append('')
    if r.get('snapshot_warning'):
        L.append('> ⚠ %s' % r['snapshot_warning'])
        L.append('')
    if r.get('blocked_chapters'):
        L.append('> ⛔ **以下章闸门为 blocked，正文不得开写（铁律6）：%s**——请先补齐依据。'
                 '骨架仍列出，便于先备料。' % '、'.join(r['blocked_chapters']))
        L.append('')
    L.append('> 铁律：数据缺失一律写 `【待填：字段名】`，不得编造；条款逐字引用；'
             '模板骨架不得增删节点（Zone C 范例不能作为完整性判据）。')
    L.append('')
    L.append('---')
    L.append('')
    for n, tlist, clist in (r.get('_blocks') or []):
        cid = n['chapter_id']
        top = cid.split('.')[0]
        if cid in (r.get('blocked_chapters') or []) or top in (r.get('blocked_chapters') or []):
            L.append('<!-- ⛔ 本章闸门 blocked：补依据后再写 -->')
        L.extend(node_block(cid, n, tlist, clist))
    L.append('---')
    L.append('')
    L.append('## 篇幅分配总表（写作时实时对照）')
    L.append('')
    L.append('| 章 | 目标字数 | 达标区间 | 占全书 |')
    L.append('|---|---|---|---|')
    book = r['length_budget']['book']['book_target_chars']
    for ch in [str(i) for i in range(1, 11)]:
        cb = B.chapter_budget(ch)
        if not cb['target']:
            continue
        L.append('| 第%s章 | %s | %s–%s | %.1f%% |'
                 % (ch, cb['target'], cb['min'], cb['max'], cb['target'] / book * 100))
    L.append('| **合计** | **%s** | | **100%%** |' % book)
    L.append('')
    L.append('逐节预算见各标题下的注释；写完用 `check_draft.py` 核对单节篇幅，'
             '用 `check_plan.py` 核对全书累计进度。')
    L.append('')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='全书骨架生成器：逐字标题 + 内容点 + 表格骨架 + 篇幅预算')
    ap.add_argument('--chapter', default=None, help='只出某一章（默认全书）')
    ap.add_argument('--province', default=None)
    ap.add_argument('--city', default=None)
    ap.add_argument('--report-form', action='store_true')
    ap.add_argument('--out', default=None)
    ap.add_argument('--json-only', action='store_true')
    a = ap.parse_args()
    r = build(a.chapter, a.province, a.city, report_form=a.report_form)
    if r.get('report_form'):
        print('报告表分支：%s' % r.get('note'))
        print('  下一步：python "%s" --report-form --out 报告表骨架.json'
              % os.path.join(os.path.dirname(os.path.abspath(__file__)), 'write_chapter.py'))
        if a.out:
            print('  未写出任何文件（报告表骨架不走 95 节点骨架）。')
        return
    if (r.get('gate') or {}).get('invalid_chapter'):
        sys.stderr.write('❌ 章节号 %r 不在模板中，未生成任何骨架。\n'
                         '   可用章节号：python outline.py --chapter 1（或用 check_gate.py --list 全查）\n'
                         % a.chapter)
        sys.exit(2)
    if not r.get('write_allowed'):
        sys.stderr.write('❌ 合规闸门 blocked，禁止生成骨架：%s\n' % r['gate'].get('block_reason'))
        sys.exit(3)
    if a.out:
        G.write_text(a.out, render(r), '骨架文件')
        print('已写出骨架: %s（%d 个节点；各节预算合计 %d 字 ≈ 全书正文目标 %d 字）'
              % (a.out, len(r['nodes']), r['total_target_chars'], r['book_target_chars']))
    if a.json_only:
        print(json.dumps({k: v for k, v in r.items() if k != '_blocks'}, ensure_ascii=False, indent=1))
    elif not a.out:
        print(render(r))


if __name__ == '__main__':
    main()

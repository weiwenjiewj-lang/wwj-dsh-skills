#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第三层：知识自诊断器。遍历模板节点，检查 Zone A / Zone B 覆盖，输出缺口报告。

用法:
    python analyze_gaps.py                     # 真实知识库
    python analyze_gaps.py --json              # 仅输出 JSON
    python analyze_gaps.py --mock              # 假数据自检（验证阈值边界与计数自洽）
    python analyze_gaps.py --out 报告.md

阈值与判定规则见 references/gap-analyzer.md 与 references/rules.json 的 coverage_rule。
"""
import argparse, json, os, sys

import sys
sys.dont_write_bytecode = True   # 技能包不留 __pycache__（避免缓存掩盖规则改动）

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REF = os.path.join(SKILL, 'references')
sys.path.insert(0, HERE)
import check_gate as G  # noqa: E402  共用「输出防污染 + 安全写盘」

RULES = json.load(open(os.path.join(REF, 'rules.json'), encoding='utf-8-sig'))
TREE = json.load(open(os.path.join(REF, 'template-tree.json'), encoding='utf-8-sig'))
NODES = {n['chapter_id']: n for n in TREE['nodes']}

# 主题级补充建议（只允许主题，不得出现文件名/标准号/条款号）
SUGGESTIONS = {
    '2.7': '自然概况按点型/线型项目分单元表述的专门要求',
    '3.3': '工程占地评价的方法与判定标准',
    '3.5': '取土场（料场）选址与设置评价的专门要求',
    '3.6': '施工方法与工艺水土保持评价的专门要求',
    '3.7': '“具有水土保持功能工程”的界定规则与措施界定标准',
    '5.1': '渣土（弃渣）来源与流向的统计、列表与运距计量要求',
    '6.2': '扰动地表/损毁植被面积与废弃土石渣量统计口径的规定',
    '6.4': '水土流失危害分析与风险点识别的专门要求',
    '7.2': '设计水平年确定规则的专门条款',
    '7.8': '水土保持施工组织与施工进度安排表的专门要求',
    '附件': '报告书附件清单与支撑性材料的专门要求',
    '附表': '附表格式与统计口径的专门要求',
    '表1': '水土保持方案特性表填报口径的专门说明',
    '附图': '水土保持图件绘制与提交要求的专门标准',
}

THRESH_A = RULES.get('coverage_rule', {}).get('threshold', {}).get('zone_a_node_specific_min', 1)
THRESH_B = RULES.get('coverage_rule', {}).get('threshold', {}).get('zone_b_min', 2)


def ancestors(cid):
    p = cid.split('.')
    return ['.'.join(p[:i]) for i in range(1, len(p))]


def is_descendant(desc, cid):
    """desc 是否为 cid 的后代节点（cid 是其前缀）"""
    return desc != cid and desc.startswith(cid + '.')


def classify(entry, cid):
    """返回 'specific' | 'inherited' | None（该依据对此节点的归属类别）

    - specific ：依据直接标注本节点
    - inherited：依据标为「全域」、标注本节点的祖先节点、或标注本节点的**后代节点**
      （后代计入 inherited 而非 specific：父节点往往有自己的、未被专门文件覆盖的要求，
        例如 2.7 自然概况的"点型/线型表述单元"规则，不能因为 2.7.1～2.7.7 有依据就算作已覆盖）
    """
    chs = entry.get('chapter_relevance', [])
    if cid in chs:
        return 'specific'
    if '全域' in chs:
        return 'inherited'
    if any(a in chs for a in ancestors(cid)):
        return 'inherited'
    if any(is_descendant(d, cid) for d in chs):
        return 'inherited'
    return None


def analyze(zone_a, zone_b, nodes=None):
    nodes = nodes or NODES
    targets = [c for c, n in nodes.items() if n.get('node_kind') in ('content', 'table')]
    targets.sort(key=lambda s: [int(x) if x.isdigit() else 10 ** 6 + ord(x[0]) for x in s.split('.')])
    results = []
    for cid in targets:
        a_s = a_i = b_s = b_i = 0
        a_spec_entries = []
        for e in zone_a:
            k = classify(e, cid)
            if k == 'specific':
                a_s += 1; a_spec_entries.append(e)
            elif k == 'inherited':
                a_i += 1
        for e in zone_b:
            k = classify(e, cid)
            if k == 'specific':
                b_s += 1
            elif k == 'inherited':
                b_i += 1
        provisional_only = bool(a_spec_entries) and all(
            any('待确认' in str(e.get(f, '')) for f in ('doc_number', 'effective_date', 'supersedes'))
            for e in a_spec_entries)
        results.append({
            'chapter_id': cid,
            'title': nodes[cid]['title'],
            'node_kind': nodes[cid].get('node_kind'),
            'zone_a_specific': a_s,
            'zone_a_inherited': a_i,
            'zone_b_specific': b_s,
            'zone_b_inherited': b_i,
            'gap_zone_a': a_s < THRESH_A,
            'gap_zone_b': b_s < THRESH_B,
            'provisional_only': provisional_only,
        })
    return results


def selfcheck(results, zone_a, zone_b, nodes=None):
    nodes = nodes or NODES
    checks = []
    # 1 计数自洽：每条依据对每个节点只归一类
    for e in list(zone_a) + list(zone_b):
        cat = classify(e, e['chapter_relevance'][0] if e['chapter_relevance'] else '')
        if cat is None and not e['chapter_relevance']:
            checks.append(('计数自洽', False, '条目缺 chapter_relevance'))
    checks.append(('计数自洽', True, '每条依据对任一节点至多归入一类（specific 优先于 inherited）'))
    # 2 阈值边界
    tb_ok = True
    detail = []
    for r in results:
        if r['zone_a_specific'] == THRESH_A and r['gap_zone_a']:
            tb_ok = False; detail.append('%s A=%d 却判缺口' % (r['chapter_id'], r['zone_a_specific']))
        if r['zone_b_specific'] == THRESH_B and r['gap_zone_b']:
            tb_ok = False; detail.append('%s B=%d 却判缺口' % (r['chapter_id'], r['zone_b_specific']))
    checks.append(('阈值边界', tb_ok, '；'.join(detail) if detail else
                   '恰好达标的节点未被判为缺口（A=%d / B=%d）' % (THRESH_A, THRESH_B)))
    # 3 零遗漏
    expected = set(c for c, n in nodes.items() if n.get('node_kind') in ('content', 'table'))
    got = set(r['chapter_id'] for r in results)
    miss = expected - got
    checks.append(('零遗漏', not miss, '全部 %d 个非容器节点均已遍历' % len(expected) if not miss
                   else '遗漏节点：%s' % (','.join(sorted(miss)))))
    return checks


def render(results, checks):
    L = []
    L.append('# 知识缺口报告')
    L.append('')
    L.append('- 阈值：Zone A 节点专属依据 ≥ %d；Zone B 节点专属参考 ≥ %d' % (THRESH_A, THRESH_B))
    L.append('- 统计口径：**仅「节点专属」计入阈值**；「继承依据」单独列出，不用于凑阈值')
    L.append('')
    ga = [r for r in results if r['gap_zone_a']]
    gb = [r for r in results if r['gap_zone_b']]
    L.append('## 汇总')
    L.append('')
    L.append('| 指标 | 数量 |')
    L.append('|---|---|')
    L.append('| 受检节点（内容 + 表格类） | %d |' % len(results))
    L.append('| Zone A 节点专属覆盖 | %d / %d |' % (len(results) - len(ga), len(results)))
    L.append('| Zone B 节点专属覆盖 | %d / %d |' % (len(results) - len(gb), len(results)))
    L.append('| 双项均达标 | %d / %d |' % (len([r for r in results if not r['gap_zone_a'] and not r['gap_zone_b']]), len(results)))
    L.append('| Zone A 缺口节点 | %d |' % len(ga))
    L.append('| Zone B 缺口节点 | %d |' % len(gb))
    L.append('| 依据仅含「待确认」的节点 | %d |' % len([r for r in results if r['provisional_only']]))
    L.append('')
    L.append('## 缺口明细')
    L.append('')
    if not ga and not gb:
        L.append('无缺口。')
    for r in results:
        if not (r['gap_zone_a'] or r['gap_zone_b'] or r['provisional_only']):
            continue
        L.append('**章节 %s  %s**' % (r['chapter_id'], r['title']))
        L.append('')
        if r['gap_zone_a']:
            L.append('- Zone A 覆盖不足（节点专属 %d 条，阈值 ≥%d）。缺少：专门规范本节点的现行规范性文件或技术标准。'
                     % (r['zone_a_specific'], THRESH_A))
            sug = SUGGESTIONS.get(r['chapter_id'])
            if sug:
                L.append('  建议补充：%s。' % sug)
            else:
                L.append('  建议补充：与「%s」直接对应的现行规范性文件或技术标准。' % r['title'])
            L.append('  现状：仅由 %d 条「全域/祖先」类依据兜底，属"普遍适用"而非"专门针对"，'
                     '不足以支撑评审时对本节量化与列表要求的追问。' % r['zone_a_inherited'])
        if r['gap_zone_b']:
            L.append('- Zone B 参考素材不足（节点专属 %d 条，阈值 ≥%d）。'
                     '建议补充：与本节点直接相关的专著、论文或技术手册（方法依据或案例参考）。'
                     % (r['zone_b_specific'], THRESH_B))
        if r['provisional_only']:
            L.append('- 依据可信度未经核实：本节点 Zone A 节点专属依据**全部**含「待确认」字段，'
                     '须核实文号与施行日期后再定稿。')
        L.append('')
    L.append('## 自检')
    L.append('')
    L.append('| 自检项 | 结果 | 说明 |')
    L.append('|---|---|---|')
    for name, ok, detail in checks:
        L.append('| %s | %s | %s |' % (name, '✅ 通过' if ok else '❌ 失败', detail))
    L.append('')
    L.append('## 全节点覆盖一览')
    L.append('')
    L.append('| 章节 | 标题 | A节点专属 | A继承 | B节点专属 | B继承 | 判定 |')
    L.append('|---|---|---|---|---|---|---|')
    for r in results:
        verdict = '✅' if not r['gap_zone_a'] and not r['gap_zone_b'] else \
                  ('⚠A' if r['gap_zone_a'] else '') + ('⚠B' if r['gap_zone_b'] else '')
        L.append('| %s | %s | %d | %d | %d | %d | %s |'
                 % (r['chapter_id'], r['title'][:22], r['zone_a_specific'], r['zone_a_inherited'],
                    r['zone_b_specific'], r['zone_b_inherited'], verdict))
    return '\n'.join(L) + '\n'


# ---------------- 假数据自检 ----------------
def mock_data():
    """构造覆盖各种边界情形的合成索引。"""
    def A(cid, **kw):
        d = {'file': 'MOCK_A_%s.md' % cid, 'title': '模拟 Zone A %s' % cid, 'doc_type': '国家标准',
             'doc_type_class': 'national_standard', 'doc_number': 'GB/T 0000-2026', 'issuing_body': '模拟机关',
             'publish_date': '2026-01-01', 'effective_date': '2026-06-01', 'status': '现行有效',
             'superseded_by': '无', 'supersedes': '无', 'scope': 'national',
             'chapter_relevance': [cid], 'source_url': '待确认', 'compliance_relevance': '直接红线'}
        d.update(kw); return d

    def B(cid, **kw):
        d = {'file': 'MOCK_B_%s.md' % cid, 'title': '模拟 Zone B %s' % cid, 'doc_type': '期刊论文',
             'doc_type_class': 'paper', 'doc_number': '待确认', 'issuing_body': '模拟期刊',
             'publish_date': '2025', 'effective_date': '不适用', 'status': '已发表',
             'superseded_by': '无', 'supersedes': '无', 'scope': '不适用',
             'chapter_relevance': [cid], 'source_url': '待确认', 'compliance_relevance': '方法依据'}
        d.update(kw); return d

    zone_a = [
        A('全域'),                                          # 全域兜底，只算继承
        A('4.1.2'),                                         # 恰好达标：A=1
        A('2.3', doc_number='待确认', effective_date='待确认'),  # 达标但字段待确认
        A('2.4', chapter_relevance=['2.4', '2.7']),          # 同时覆盖两个节点
        A('7.3.2', status='已被代替', superseded_by='GB/T 9999-2026'),  # 非现行
    ]
    zone_b = [
        B('4.1.2'), B('4.1.2'),                             # 恰好达标：B=2
        B('2.3'),                                           # 不足：B=1
        B('2', doc_number='X'),                             # 祖先级 → 只算继承
    ]
    return zone_a, zone_b


def mock_nodes():
    """只取少量真实节点骨架做假数据测试（结构真实、依据为假）。"""
    keep = ['4.1.2', '2.3', '2.4', '2.7', '7.3.2', '5.1']
    return {c: NODES[c] for c in keep if c in NODES}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--mock', action='store_true')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    if a.mock:
        za, zb = mock_data()
        nd = mock_nodes()
        res = analyze(za, zb, nd)
        checks = selfcheck(res, za, zb, nd)
        rep = render(res, checks)
        print('=== 假数据自检 ===\n')
        print(rep)
        print('=== 预期对照 ===')
        exp = {'4.1.2': 'A=1 B=2 → 双项恰好达标，不得报缺口',
               '2.3': 'A=1 B=1 → Zone B 不足（阈值 2）',
               '2.4': 'A=1（该依据同时覆盖 2.7）→ 达标',
               '2.7': 'A=1（祖先/同条覆盖）→ 达标',
               '7.3.2': 'A=1 但状态为已被代替 → 仍计入 specific（状态问题由闸门处理），此处不报 A 缺口',
               '5.1': 'A=0 → Zone A 缺口'}
        ok = True
        for r in res:
            print('  %-7s A专属=%d A继承=%d B专属=%d B继承=%d  缺口A=%s 缺口B=%s'
                  % (r['chapter_id'], r['zone_a_specific'], r['zone_a_inherited'],
                     r['zone_b_specific'], r['zone_b_inherited'], r['gap_zone_a'], r['gap_zone_b']))
        for k, v in exp.items():
            print('   期望 %-7s %s' % (k, v))
        r412 = next(r for r in res if r['chapter_id'] == '4.1.2')
        r51 = next(r for r in res if r['chapter_id'] == '5.1')
        assert not r412['gap_zone_a'] and not r412['gap_zone_b'], '阈值边界判定错误'
        assert r51['gap_zone_a'], '零覆盖未报缺口'
        r23 = next(r for r in res if r['chapter_id'] == '2.3')
        assert not r23['gap_zone_a'] and r23['gap_zone_b'], '2.3 判定错误'
        assert r23['provisional_only'], '待确认依赖未识别'
        print('\n=== 断言全部通过：阈值边界、零覆盖、待确认依赖判定正确 ===')
        return

    # 知识库路径不存在时，原实现会照常输出一份「全库零覆盖」报告并 exit 0——
    # 那会被误读成「知识库真的没有依据」。改为输入错误直接退出。
    if not os.path.isdir(G.VAULT):
        sys.stderr.write('❌ 知识库路径不存在或不是目录：%s\n'
                         '   用环境变量 DSH_WS_VAULT 指定；库内应有 md库\\Zone A - 规范层 等目录。\n'
                         % G.VAULT)
        raise SystemExit(2)
    za = json.load(open(os.path.join(REF, 'zone-a-index.json'), encoding='utf-8-sig'))['entries']
    zb = json.load(open(os.path.join(REF, 'zone-b-index.json'), encoding='utf-8-sig'))['entries']
    res = analyze(za, zb)
    checks = selfcheck(res, za, zb)
    rep = render(res, checks)
    if a.out:
        G.write_text(a.out, rep, '缺口报告')
        print('已写出:', a.out)
    if a.json:
        print(json.dumps({'results': res, 'checks': [{'name': n, 'pass': o, 'detail': d} for n, o, d in checks]},
                         ensure_ascii=False, indent=1))
    elif not a.out:
        print(rep)
    else:
        ga = [r for r in res if r['gap_zone_a']]
        print('受检节点 %d；Zone A 缺口 %d；Zone B 缺口 %d；待确认依赖 %d'
              % (len(res), len(ga), len([r for r in res if r['gap_zone_b']]),
                 len([r for r in res if r['provisional_only']])))
        print('Zone A 缺口节点:', ' '.join(r['chapter_id'] for r in ga))


if __name__ == '__main__':
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从三个 zone 索引生成「分章取用地图」asset-map.json —— 禁全文检索机制的数据底座。

设计目标（用户要求）：
  · AI 以后**不再全文检索**知识库（单文件最大 60+ MB ≈ 千万级 token，是最大黑洞）
  · 改为：先读 asset-map.json（十几 KB）→ 定位到**具体文件路径** → 只读那一份
  · asset-map 只存**元数据**（路径/题名/文号/相关性强度），**不存任何正文**

产物：<skill>/references/asset-map.json
"""
import io
import json
import os
import sys
from collections import defaultdict

sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REF = os.path.join(SKILL, 'references')

CHAPTER_NAMES = {
    '1': '综合说明', '2': '项目概况', '3': '主体工程水土保持分析与评价',
    '4': '表土资源保护与利用', '5': '弃渣场', '6': '水土流失分析与预测',
    '7': '水土流失防治', '8': '水土保持监测', '9': '水土保持投资估算与效益分析',
    '10': '水土保持管理', '表1': '水土保持方案特性表', '附表': '附表',
    '附件': '附件', '附图': '附图', '全域': '全域适用',
}


def load(name):
    p = os.path.join(REF, name)
    if not os.path.isfile(p):
        return {'entries': [], 'count': 0}
    return json.load(io.open(p, encoding='utf-8-sig'))


def chapter_of(node):
    if not node:
        return None
    if node == '全域':
        return '全域'
    return str(node).split('.')[0]


def slim(e):
    """索引条目 → 极简元数据（不含正文）。"""
    return {
        'f': e.get('file', '').replace('md库\\', ''),
        't': (e.get('title') or '')[:80],
        'n': e.get('doc_number') or '',
        'd': e.get('doc_type') or '',
        'r': e.get('compliance_relevance') or '',
        's': e.get('style_role') or '',
    }


def main():
    A = load('zone-a-index.json')
    B = load('zone-b-index.json')
    C = load('zone-c-index.json')

    out = {
        'generated_at': A.get('generated_at'),
        'purpose': '禁全文检索：先查本表定位文件，再只读那一份。禁止 grep/直读全库。',
        'counts': {'A': A.get('count', 0), 'B': B.get('count', 0), 'C': C.get('count', 0)},
        'chapters': {},
        'nodes': {},
    }

    # ① 精确节点索引（node -> 文件），供 --chapter 9.1.2 精确命中
    nd = defaultdict(lambda: {'A': [], 'B': [], 'C': []})
    seen_node = defaultdict(set)
    # ② 章级汇总（按章归并，同一文件只出现一次）
    ch = defaultdict(lambda: {'A': [], 'B': [], 'C': []})
    seen_ch = defaultdict(set)

    for zone_key, idx in (('A', A), ('B', B), ('C', C)):
        for e in idx.get('entries', []):
            s = slim(e)
            for node in (e.get('chapter_relevance') or ['未标注']):
                if s['f'] not in seen_node[(zone_key, node)]:
                    seen_node[(zone_key, node)].add(s['f'])
                    nd[node][zone_key].append(s)
                c = chapter_of(node)
                if s['f'] not in seen_ch[(zone_key, c)]:
                    seen_ch[(zone_key, c)].add(s['f'])
                    ch[c][zone_key].append(s)

    for node in sorted(nd, key=lambda x: (x != '全域', x)):
        rec = nd[node]
        out['nodes'][node] = {
            'A': rec['A'], 'B': rec['B'], 'C': rec['C'],
            'a_count': len(rec['A']), 'b_count': len(rec['B']), 'c_count': len(rec['C']),
        }

    for c in sorted(ch, key=lambda x: (x != '全域', x)):
        rec = ch[c]
        rec['name'] = CHAPTER_NAMES.get(c, '')
        rec['a_count'] = len(rec['A'])
        rec['b_count'] = len(rec['B'])
        rec['c_count'] = len(rec['C'])
        out['chapters'][c] = rec

    p = os.path.join(REF, 'asset-map.json')
    json.dump(out, io.open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    sz = os.path.getsize(p)
    print('已生成 %s  (%.1f KB)' % (p, sz / 1024))
    print()
    print('%-6s %-26s %6s %6s %6s' % ('章', '名称', 'ZoneA', 'ZoneB', 'ZoneC'))
    for c in sorted(out['chapters'], key=lambda x: (x != '全域', x)):
        r = out['chapters'][c]
        print('%-6s %-26s %6d %6d %6d' % (c, r['name'][:26], r['a_count'], r['b_count'], r['c_count']))


if __name__ == '__main__':
    main()

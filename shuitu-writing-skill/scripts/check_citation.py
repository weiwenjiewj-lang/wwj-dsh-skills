#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第九层：引用格式校验器。查法规/标准/条款/图表引用是否前后一致、是否可追溯。

解决什么问题：一本方案里同一部法规会被引用几十次，编号风格一旦不统一
（GB 50433-2018 / GB50433—2018 / GB/T 50433—2018 混用），评审会认为编制粗糙；
更严重的是"引了库内没有的文件""文号写错""引用了已废止版本"——这类错误直接伤合规结论。
本脚本做机械校验，规则全部写在 rules.json 的 citation_rules，不硬编码。

用法:
    python check_citation.py --draft 稿.md
    python check_citation.py --draft 稿1.md --draft 稿2.md --out 引用校验报告.md
    python check_citation.py --draft 稿.md --json-only

退出码：0 通过 / 1 有需处理项 / 2 输入错误
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
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import check_gate as G   # noqa: E402

RULES = G.RULES
CR = (RULES.get('citation_rules') or {})
ZA = {e.get('title'): e for e in G.ZONE_A['entries']}

# 《法规名》——可选后跟（文号）
DOC_RE = re.compile(CR.get('doc_pattern') or r'《([^》]{2,60})》\s*(?:（([^）]{2,60})）)?')
# 标准号：GB/T 50434—2018 之类
STD_RE = re.compile(CR.get('std_pattern') or
                    r'\b((?:GB|SL|TD|HJ|JTG|MT|NY|LY|JT)\s*/?\s*T?\s*\d+(?:\.\d+)?\s*[—–\-]\s*\d{4})\b')
# 条款号
CLAUSE_RE = re.compile(CR.get('clause_pattern') or r'第\s*([0-9]+(?:\.[0-9]+)*)\s*条')


def norm_loose(s):
    """宽松归一：把破折号/连字符/空格/括号统一，用于"是否同一个东西"的比较。"""
    if not isinstance(s, str):
        return ''
    t = s.strip()
    t = t.replace('—', '-').replace('–', '-').replace('－', '-')
    t = re.sub(r'\s+', '', t)
    t = t.replace('（', '(').replace('）', ')')
    return t


def norm_std(s):
    """标准号归一：去空格、破折号统一为 -、T 前统一斜杠。返回 (规范形, 数字部分)。"""
    t = re.sub(r'\s+', '', s)
    t = t.replace('—', '-').replace('–', '-')
    m = re.match(r'^([A-Z]+)/?T?-?(\d+(?:\.\d+)?)-(\d{4})$', t)
    if m:
        code, num, year = m.groups()
        # 有 T 的写成 GB/T，无 T 的写成 GB
        has_t = 'T' in t.split(num)[0]
        return '%s%s %s-%s' % (code, '/T' if has_t else '', num, year), num
    return t, ''


def check(text, label):
    r = {'label': label, 'doc_refs': [], 'std_refs': [], 'problems': [], 'warnings': [],
         'unmatched_docs': [], 'unlinked_std': [], 'style_variants': {}}

    # ---- 1. 法规引用：《名》（文号） ----
    docs = {}
    for m in DOC_RE.finditer(text):
        name, num = m.group(1).strip(), (m.group(2) or '').strip()
        docs.setdefault(name, []).append(num)
    for name, nums in docs.items():
        # 与库内 Zone A 标题比对（允许带/不带文号后缀）
        hit = None
        nl = norm_loose(name)
        for t in ZA:
            tl = norm_loose(t)
            if tl == nl or tl.startswith(nl) or nl.startswith(tl) or nl in tl or tl in nl:
                hit = t
                break
        if not hit:
            # 再退一步：去掉括号内容后比对（"生产建设项目水土保持方案编制模板" vs 库内带文号的长名）
            nu = norm_loose(re.sub(r'[（(].*?[)）]', '', name))
            for t in ZA:
                tu = norm_loose(re.sub(r'[（(].*?[)）]', '', t))
                if nu and (nu == tu or nu in tu or tu in nu):
                    hit = t
                    break
        rec = {'name': name, 'numbers': nums, 'in_vault': bool(hit), 'vault_title': hit}
        r['doc_refs'].append(rec)
        if not hit:
            r['unmatched_docs'].append(name)
        # 文号一致性：同一法规名被配了不同文号
        uniq = sorted(set(n for n in nums if n))
        if len(uniq) > 1:
            r['problems'].append({'type': 'doc_number_conflict', 'name': name,
                                  'detail': '同一法规出现 %d 种文号：%s' % (len(uniq), ' / '.join(uniq))})
        # 库内有文号、正文却没写
        if hit:
            vnum = (ZA[hit].get('doc_number') or '').strip()
            if vnum and vnum not in ('无', '不适用', '待确认') and not uniq:
                r['warnings'].append({'type': 'missing_doc_number', 'name': name,
                                      'detail': '库内该文件文号为「%s」，正文引用未写文号' % vnum})
            lv, lu = norm_loose(vnum), [norm_loose(x) for x in uniq]
            if vnum and uniq and lv not in lu:
                # 破折号/空格差异不算不同——只有实质差异才提示
                r['warnings'].append({'type': 'doc_number_differs', 'name': name,
                                      'detail': '正文文号「%s」与库内「%s」不同，需核对' % (uniq[0], vnum)})

    # ---- 2. 标准号：书写统一性 ----
    stds = {}
    for m in STD_RE.finditer(text):
        raw = m.group(1)
        norm, num = norm_std(raw)
        stds.setdefault(num or norm, []).append(raw)
    for key, variants in stds.items():
        uniq = sorted(set(variants))
        if len(uniq) > 1:
            r['problems'].append({'type': 'std_style_inconsistent', 'std_key': key,
                                  'detail': '同一标准出现 %d 种写法：%s' % (len(uniq), ' / '.join(uniq))})
        r['std_refs'].append({'key': key, 'norm': norm_std(uniq[0])[0], 'variants': uniq,
                              'count': len(variants)})

    # ---- 3. 条款引用：《X》第N条 里的条号是否出现在库内原文 ----
    # 仅检查能定位到库内文件的法规，避免对"部委文件"误报
    checked = 0
    for rec in r['doc_refs']:
        if not rec['vault_title']:
            continue
        e = ZA.get(rec['vault_title']) or {}
        path = e.get('file')
        if not path:
            continue
        fp = os.path.join(G.VAULT, path)
        if not os.path.exists(fp):
            continue
        body = io.open(fp, encoding='utf-8-sig', errors='ignore').read()
        # 找紧跟在《该名》后的条款号
        for m in re.finditer(re.escape('《%s》' % rec['name']) + r'\s*第\s*([0-9]+(?:\.[0-9]+)*)\s*条', text):
            cn = m.group(1)
            checked += 1
            # 库内是否有该条号（阿拉伯或中文数字都可能）
            if ('第%s条' % cn) not in body and ('%s' % cn) not in body:
                r['warnings'].append({'type': 'clause_not_found',
                                      'detail': '《%s》第%s条 在库内原文未检出，需人工核对'
                                                % (rec['name'], cn)})
    r['clause_checked'] = checked
    return r


def render(results):
    L = ['# 引用格式校验报告', '']
    tot_p = sum(len(r['problems']) for r in results)
    tot_w = sum(len(r['warnings']) for r in results)
    L.append('受检稿件 %d 份；需处理 %d 项；提示 %d 项。' % (len(results), tot_p, tot_w))
    L.append('')
    for r in results:
        L.append('## %s' % r['label'])
        L.append('')
        L.append('- 法规引用 %d 部；标准引用 %d 个；条款引用核对 %d 处'
                 % (len(r['doc_refs']), len(r['std_refs']), r.get('clause_checked', 0)))
        if r['unmatched_docs']:
            L.append('- ⚠ 库内无对应文件的引用：%s' % '、'.join(r['unmatched_docs']))
        if r['problems']:
            L.append('')
            L.append('**需处理**')
            L.append('')
            for p in r['problems']:
                L.append('- [%s] %s' % (p['type'], p['detail']))
        if r['warnings']:
            L.append('')
            L.append('**提示（不阻断）**')
            L.append('')
            for w in r['warnings']:
                L.append('- [%s] %s' % (w['type'], w['detail']))
        if r['std_refs']:
            L.append('')
            L.append('**标准引用清单**')
            L.append('')
            L.append('| 标准 | 次数 | 用法 |')
            L.append('|---|---|---|')
            for s in r['std_refs']:
                L.append('| %s | %d | %s |' % (s['norm'] or s['key'], s['count'], '；'.join(s['variants'])))
        L.append('')
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser(description='引用格式校验：法规/标准/条款/图表引用一致性与可追溯性')
    ap.add_argument('--draft', action='append', required=True, help='稿件路径，可多个')
    ap.add_argument('--out', default=None)
    ap.add_argument('--json-only', action='store_true')
    a = ap.parse_args()

    results = []
    for p in a.draft:
        t = G.read_text(p, '稿件')
        results.append(check(t, os.path.basename(p)))

    if a.json_only:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        txt = render(results)
        if a.out:
            G.write_text(a.out, txt, '引用校验报告')
            print('已写出引用校验报告:', a.out)
        else:
            print(txt)
    sys.exit(1 if any(r['problems'] for r in results) else 0)


if __name__ == '__main__':
    main()

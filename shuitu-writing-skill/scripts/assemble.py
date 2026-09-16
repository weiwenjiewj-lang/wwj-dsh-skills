#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第八层：全书拼装器。把逐节/逐章的 md 稿件，按模板顺序拼成一本可送审的方案书。

解决什么问题：写到最后一章时，手上是 95 个节点散落的稿件——
① 顺序靠人排，容易漏节、串节；② 表格编号各节自己编（表1.6.1-1、表 7.4-2 …）成书后冲突；
③ 目录、表索引、图索引、附表附件顺序要靠人工誊抄，最容易出错。
本脚本按 template-tree.json 的顺序装配，自动重编表/图号、生成目录与索引、检查缺节，
产出一份结构完整、编号自洽的全书 md。

编号规则（可在 rules.json assemble_rules 覆盖）：
    表/图号 = <节点号>-<该节点内序号>      例：表 1.6.1-1、表 7.4-2、图 2.1-1

用法:
    python assemble.py --draft 稿1.md --draft 稿2.md --out 全书.md
    python assemble.py --draft-dir ./章节稿 --out 全书.md --meta 项目名=xxx --meta 编制单位=yyy
    python assemble.py --draft 全书骨架.md --check-only      # 只体检不产出
    python assemble.py --json-only

退出码：0 通过 / 1 拼装完成但有需处理项（缺节、编号冲突） / 2 输入错误
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
import check_gate as G   # noqa: E402  共用「输出防污染 + 安全写盘」

RULES = G.RULES
TREE = G.TREE
NODES = G.NODES
AR = (RULES.get('assemble_rules') or {})

TOC_TITLE = AR.get('toc_title', '目录')
TABLE_INDEX_TITLE = AR.get('table_index_title', '表索引')
FIGURE_INDEX_TITLE = AR.get('figure_index_title', '图索引')
TABLE_PREFIX = AR.get('table_prefix', '表')
FIGURE_PREFIX = AR.get('figure_prefix', '图')
# 目录/索引是否放入正文（默认放，可关）
EMIT_TOC = AR.get('emit_toc', True)
EMIT_INDEX = AR.get('emit_table_index', True)

# 标题行：# ~ #### + 节点号 + 标题（节点号形如 1 / 1.6 / 1.6.1 / 表1 / 附表）
HEAD_RE = re.compile(r'^(#{1,6})\s*([0-9]+(?:\.[0-9]+)*|表1|附表|附件|附图)\s*[:：]?\s*(.*)$')
# 表/图题注（要重编号）：**整行**只有「表/图 + 编号 + 题名」，且：
#   · 不含表格竖线（| 开头的是表格行，里面的"表X-N"多为交叉引用）
#   · 题名部分不以句末标点收尾成句（"…的规定。"是正文引用，不是题注）
# 只重编号独立成行的题注，绝不动正文里的交叉引用（否则会破坏"见表 1.6.1-2"这类指引）。
CAP_RE = re.compile(r'^(\s*)(表|图)\s*([0-9]+(?:\.[0-9]+)*-[0-9]+)([　\s]+)([^|。；;]{1,60})$')


def load_nodes_order():
    """按模板顺序返回节点号列表（含表1/附表/附件/附图，按 source_line 排序）。"""
    order = sorted(TREE['nodes'], key=lambda n: n.get('source_line') or 0)
    return [n['chapter_id'] for n in order]


def split_sections(text):
    """把一份稿件切成 {节点号: 正文}。

    一个节点从它的标题行开始，到下一个「同级或更高级」标题行为止。
    标题识别以 template-tree.json 的节点号为准（避免把正文里的"一、二、"误当标题）。

    面包屑处理：稿件常在开头写父级标题（如 `# 1.6 水土流失防治`）再写本节标题
    （`## 1.6.1 …`）。若某标题与其下一个标题之间**没有实质内容**，该标题只是面包屑，
    不得据此切出一节——否则父节点会把整篇文件的正文吞进去（曾导致 1.6.1 丢失、
    1.6.2 被父节点重复收录）。
    """
    lines = text.splitlines()
    marks = []                       # (行号, 节点号, 级别, 标题)
    for i, ln in enumerate(lines):
        m = HEAD_RE.match(ln.strip())
        if not m:
            continue
        cid = m.group(2)
        if cid in NODES:
            marks.append((i, cid, len(m.group(1)), m.group(3).strip()))

    # 标记面包屑：与下一个标题之间无实质内容（去掉空行/注释/引用块/分隔线后不足 min_chars）
    min_chars = AR.get('breadcrumb_min_chars', 40)
    real = []
    for idx, (i, cid, lvl, title) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        between = [x for x in lines[i + 1:end]
                   if x.strip() and not x.strip().startswith(('<!--', '>', '---', '|', '#'))]
        if len(between) == 0 or sum(len(x.strip()) for x in between) < min_chars:
            if idx + 1 < len(marks):
                continue             # 是面包屑，跳过
        real.append((i, cid, lvl, title))

    out = {}
    for idx, (i, cid, lvl, _title) in enumerate(real):
        end = len(lines)
        for j, _c2, lvl2, _t in real[idx + 1:]:
            if lvl2 <= lvl:          # 下一个同级或更高级标题 → 本节结束
                end = j
                break
        body = '\n'.join(lines[i:end]).rstrip()
        if cid in out and len(out[cid]) >= len(body):
            continue                 # 同一节点出现多次时保留较长的一份
        out[cid] = body
    return out


def renumber(text, cid, tstate, fstate):
    """把本节里的表/图题注重编号为 <节点号>-<序号>，返回 (新文本, 本节表数, 本节图数)。"""
    lines = []
    tno = fno = 0
    for ln in text.splitlines():
        m = CAP_RE.match(ln)
        if m:
            indent, kind, _old, gap, rest = m.groups()
            if kind == TABLE_PREFIX:
                tno += 1
                key = '%s-%d' % (cid, tno)
                tstate.append((key, rest.strip() or '', cid))
            else:
                fno += 1
                key = '%s-%d' % (cid, fno)
                fstate.append((key, rest.strip() or '', cid))
            lines.append('%s%s %s%s%s' % (indent, kind, key, gap or '　', rest))
        else:
            lines.append(ln)
    return '\n'.join(lines), tno, fno


def build(drafts, meta=None, order_only=False):
    order = load_nodes_order()
    sections = {}
    dup = []
    for p in drafts:
        t = G.read_text(p, '稿件')
        for cid, body in split_sections(t).items():
            if cid in sections:
                dup.append((cid, p))
                if len(body) <= len(sections[cid]):
                    continue
            sections[cid] = body

    tstate, fstate = [], []
    parts, present, missing = [], [], []
    for cid in order:
        n = NODES[cid]
        if cid in sections:
            body, _t, _f = renumber(sections[cid], cid, tstate, fstate)
            parts.append((cid, n, body))
            present.append(cid)
        else:
            missing.append(cid)

    toc = []
    for cid, n, _b in parts:
        lvl = n.get('level') or 1
        toc.append((lvl, cid, n['title']))

    return {
        'order': order,
        'present': present,
        'missing': missing,
        'duplicate_sections': dup,
        'parts': parts,
        'table_index': tstate,
        'figure_index': fstate,
        'toc': toc,
        'meta': meta or {},
    }


def render(r):
    L = []
    meta = r['meta'] or {}
    if meta.get('项目名'):
        L.append('# %s水土保持方案报告书' % meta['项目名'])
        L.append('')
    if meta.get('编制单位'):
        L.append('编制单位：%s' % meta['编制单位'])
        L.append('')
    if meta.get('日期'):
        L.append('编制日期：%s' % meta['日期'])
        L.append('')

    # 目录
    if EMIT_TOC and r['toc']:
        L.append('## %s' % TOC_TITLE)
        L.append('')
        for lvl, cid, title in r['toc']:
            indent = '  ' * max(0, lvl - 1)
            L.append('%s- %s %s' % (indent, cid, title))
        L.append('')

    # 正文（按模板顺序）
    for cid, n, body in r['parts']:
        L.append(body.rstrip())
        L.append('')

    # 表索引 / 图索引
    if EMIT_INDEX and r['table_index']:
        L.append('## %s' % TABLE_INDEX_TITLE)
        L.append('')
        for key, cap, cid in r['table_index']:
            L.append('- %s %s　（%s 节）' % (TABLE_PREFIX, key, cid))
        L.append('')
    if EMIT_INDEX and r['figure_index']:
        L.append('## %s' % FIGURE_INDEX_TITLE)
        L.append('')
        for key, cap, cid in r['figure_index']:
            L.append('- %s %s　（%s 节）' % (FIGURE_PREFIX, key, cid))
        L.append('')

    # 装配说明（供人核对，不影响正文）
    L.append('---')
    L.append('')
    L.append('> 本节为拼装附注，定稿前请删除。')
    L.append('> 已收录节点 %d 个；模板应含 %d 个。' % (len(r['present']), len(r['order'])))
    if r['missing']:
        L.append('> ⚠ 缺节点 %d 个：%s' % (len(r['missing']), '、'.join(r['missing'])))
    if r['duplicate_sections']:
        L.append('> ⚠ 重复出现的节点（已取较长者）：%s'
                 % '、'.join('%s(%s)' % (c, os.path.basename(p)) for c, p in r['duplicate_sections'][:8]))
    L.append('> 表 %d 个、图 %d 个，编号已按"节点号-序号"重编。'
             % (len(r['table_index']), len(r['figure_index'])))
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser(description='全书拼装：按模板顺序装配逐节稿，重编号、出目录与索引')
    ap.add_argument('--draft', action='append', default=[], help='稿件路径，可多个')
    ap.add_argument('--draft-dir', default=None, help='稿件目录（取其中全部 .md，按文件名排序）')
    ap.add_argument('--meta', action='append', default=[], help='元信息 key=value，可多个（项目名/编制单位/日期）')
    ap.add_argument('--out', default=None)
    ap.add_argument('--check-only', action='store_true', help='只体检：报告已收录/缺失/编号统计，不写文件')
    ap.add_argument('--json-only', action='store_true')
    a = ap.parse_args()

    drafts = list(a.draft)
    if a.draft_dir:
        if not os.path.isdir(a.draft_dir):
            G._die('❌ 稿件目录不存在：%s' % a.draft_dir)
        for f in sorted(os.listdir(a.draft_dir)):
            if f.endswith('.md'):
                drafts.append(os.path.join(a.draft_dir, f))
    if not drafts:
        ap.error('需要 --draft 或 --draft-dir')

    meta = {}
    for kv in a.meta:
        if '=' in kv:
            k, v = kv.split('=', 1)
            meta[k.strip()] = v.strip()

    r = build(drafts, meta)
    r['draft_files'] = drafts

    if a.json_only:
        print(json.dumps({k: v for k, v in r.items() if k != 'parts'}, ensure_ascii=False, indent=1))
    else:
        if not a.check_only and a.out:
            G.write_text(a.out, render(r), '全书稿')
            print('已写出全书稿: %s（收录 %d 节点，表 %d 个，图 %d 个）'
                  % (a.out, len(r['present']), len(r['table_index']), len(r['figure_index'])))
        else:
            print('稿件 %d 份；模板应含 %d 节点，已收录 %d 个'
                  % (len(drafts), len(r['order']), len(r['present'])))
            if r['missing']:
                print('缺节点 %d 个：%s' % (len(r['missing']), '、'.join(r['missing'][:30])))
            if r['duplicate_sections']:
                print('重复节点 %d 个：%s' % (len(r['duplicate_sections']),
                                          '、'.join(c for c, _p in r['duplicate_sections'][:10])))
            print('表 %d 个、图 %d 个' % (len(r['table_index']), len(r['figure_index'])))
            if a.check_only:
                print('（--check-only：未写出文件）')

    problems = bool(r['missing']) or bool(r['duplicate_sections'])
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
finalize.py —— 交付稿净化：把「写作工作格式」转换为「交付格式」

为什么需要这一步
----------------
各节点写作时执行的是**四段式工作格式**：
    一、正文 → 二、表格 → 三、计算过程 → 四、需补充的数据（最小清单）
这套格式是**给编制人自己看的**（便于核对计算与补数），**不是方案报告书的体例**。
方案报告书只有：章 → 节 → 正文段落 + 表格 + 图。

若把四段式直接装配成 Word，交付件里就会出现 71 × 4 = 284 处「计算过程」「需补充的数据」
这类过程性节标题，以及数十张补数清单表格——评审会直接判为未成稿。

本脚本做的事
------------
1. 抽掉四段式节标题：正文与表格**内容保留**，标题删除；
2. 「计算过程」与「补数清单」**不留在各节点内**，改为汇入书末附录（可关闭）；
3. 清除全部**内部行话**：本节点 / 缺数据不可计算 / 技能 / 知识库 / 不带病送审 等；
4. **标题层级按编号规则归一**（源稿里 `###` 时而表二级时而表三级，不可依赖井号个数）：
      第N章 → `#`，N.N → `##`，N.N.N → `###`；章标题统一为「第N章　名称」；
5. **前置部分交给 build_docx.py 统一生成**：本脚本删除 md 里的封面、责任页、目录
   （否则会与脚本生成的封面重复），并把「方案说明与补数清单」转附录C。

用法
----
    python finalize.py --draft 全书.md --out 交付稿.md
    python finalize.py --draft 全书.md --out 交付稿.md --report 净化报告.md
    python finalize.py --draft 全书.md --out 交付稿.md --no-appendix   # 纯送审版
    python finalize.py --draft 全书.md --out 交付稿.md --keep-front   # 保留 md 前置部分
    python finalize.py --draft 全书.md --out 交付稿.md --json-only

退出码：0 通过 · 1 有需人工确认项 · 2 输入/用法错误
"""

import sys
sys.dont_write_bytecode = True

import argparse
import io
import json
import os
import re

# ---------------------------------------------------------------- 常量

SCAFFOLD_BODY = re.compile(r'^\s*(?:[#>*\-]\s*)*一、正文\s*$')
SCAFFOLD_TABLE = re.compile(r'^\s*(?:[#>*\-]\s*)*二、表格\s*$')
SCAFFOLD_CALC = re.compile(r'^\s*(?:[#>*\-]\s*)*三、计算过程\s*$')
SCAFFOLD_SUPPLY = re.compile(r'^\s*(?:[#>*\-]\s*)*四、需(?:要)?补充的数据(?:（最小清单）|\(最小清单\))?\s*$')
SCAFFOLD_ANY = (SCAFFOLD_BODY, SCAFFOLD_TABLE, SCAFFOLD_CALC, SCAFFOLD_SUPPLY)

RE_CH = re.compile(r'^第\s*(\d+)\s*章[\s　]*(.*)$')

# 内部行话：整句删除
INTERNAL_SENTENCE_DROP = [
    r'按技能红线要求[^。；]*[。；]?',
    r'按技能[^。；]{0,40}[。；]?',
    r'本方案不擅自[^。；]*[。；]?',
    r'本节点(?:无|为|不)[^。；]*[。；]?',
    r'本节不涉及独立计算[。；]?',
    r'不带病送审[。；]?',
    r'本节点不再重复计算[。；]?',
]
# 内部行话：词级替换
INTERNAL_PHRASE_REPLACE = [
    ('本节点', '本节'),
    ('技能红线', '规范要求'),
    ('技能包', '编制依据'),
    ('知识库内', ''),
    ('知识库', ''),
    ('缺数据不可计算', '待补充资料后核定'),
    ('缺数据不可算', '待补充资料后核定'),
    ('本方案不代入默认值试算', '本方案不使用默认值代入'),
    ('不擅自调整', '不作调整'),
    ('按技能', '按规范'),
]

WORK_NOTE_TITLES = ('方案说明与补数清单', '编制说明与已知问题', '已知问题说明')
FRONT_SKIP_TITLES = ('水土保持方案报告书编制责任页', '责任页', '目录')

APPENDIX_A = '附录A　主要计算过程'
APPENDIX_B = '附录B　待补充资料清单'
APPENDIX_C = '附录C　编制说明与已知问题'


def is_scaffold(line):
    s = line.strip()
    if not s:
        return None
    for i, pat in enumerate(SCAFFOLD_ANY):
        if pat.match(s):
            return ('body', 'table', 'calc', 'supply')[i]
    return None


def heading_text(line):
    s = line.strip()
    if not s.startswith('#'):
        return None
    return re.sub(r'^#+\s*', '', s).strip()


def normalize_heading(title):
    """
    按**编号规则**（而非井号个数）归一标题层级。
    返回 (level, 归一后的标题文本)；非编号标题返回 None。
    """
    m = RE_CH.match(title)
    if m:
        return (1, '第%s章　%s' % (m.group(1), m.group(2).strip()))
    m = re.match(r'^(\d+\.\d+\.\d+)[\s　]+(.+)$', title)
    if m:
        return (3, '%s %s' % (m.group(1), m.group(2).strip()))
    m = re.match(r'^(\d+\.\d+)[\s　]+(.+)$', title)
    if m:
        return (2, '%s %s' % (m.group(1), m.group(2).strip()))
    m = re.match(r'^(\d{1,2})[\s　]+(\S.+)$', title)
    if m:
        # 形如「10 水土保持管理」——单数字编号的节点
        return (2, '%s %s' % (m.group(1), m.group(2).strip()))
    m = RE_CH.match(re.sub(r'^第\s*(\d+)\s*章', r'第\1章', title))
    return None


def sanitize(text):
    hits = []
    out = text
    for pat in INTERNAL_SENTENCE_DROP:
        new, n = re.subn(pat, '', out)
        if n:
            hits.append((pat[:22], n))
            out = new
    for old, new in INTERNAL_PHRASE_REPLACE:
        if old and old in out:
            hits.append((old, out.count(old)))
            out = out.replace(old, new)
    out = re.sub(r'[。；]{2,}', '。', out)
    out = re.sub(r'（\s*）', '', out)
    out = re.sub(r'[ \t]{2,}', ' ', out)
    return out, hits


def parse_md_table(block):
    lines = [l for l in block if l.strip().startswith('|')]
    if len(lines) < 2:
        return None
    rows = []
    for l in lines:
        cells = [c.strip() for c in l.strip().strip('|').split('|')]
        if cells and all(re.fullmatch(r':?-{2,}:?', c) for c in cells if c):
            continue
        rows.append(cells)
    return rows or None


def merge_supply_rows(rowss):
    merged, seen = [], set()
    for rows in rowss:
        if not rows:
            continue
        hdr = [c.replace(' ', '') for c in rows[0]]
        idx_field = idx_impact = idx_ph = None
        for i, h in enumerate(hdr):
            if idx_field is None and re.search(r'字段|待补|数据项', h):
                idx_field = i
            if idx_impact is None and re.search(r'影响|涉及|章节', h):
                idx_impact = i
            if idx_ph is None and '占位符' in h:
                idx_ph = i
        if idx_field is None:
            idx_field = 1 if hdr and re.fullmatch(r'序号|编号', hdr[0]) else 0
        for r in rows[1:]:
            if not r or not any(c.strip() for c in r):
                continue
            f = r[idx_field].strip() if idx_field < len(r) else ''
            if not f:
                continue
            key = re.sub(r'\s+', '', f)
            if key in seen:
                continue
            seen.add(key)
            imp = r[idx_impact].strip() if (idx_impact is not None and idx_impact < len(r)) else ''
            ph = r[idx_ph].strip() if (idx_ph is not None and idx_ph < len(r)) else ''
            if not ph:
                for c in r:
                    if '【待填' in c:
                        ph = c.strip()
                        break
            merged.append((f, imp, ph))
    return merged


def main():
    ap = argparse.ArgumentParser(description='交付稿净化：工作格式 → 交付格式')
    ap.add_argument('--draft', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--report')
    ap.add_argument('--no-appendix', action='store_true')
    ap.add_argument('--keep-front', action='store_true',
                    help='保留 md 内的封面/责任页/目录（默认删除，交由 build_docx.py 生成）')
    ap.add_argument('--json-only', action='store_true')
    args = ap.parse_args()

    if not os.path.exists(args.draft):
        print('输入错误：找不到 %s' % args.draft, file=sys.stderr)
        return 2
    for p in (args.out, args.report):
        if p and _inside_skill(p):
            print('拒绝写入技能包/知识库：%s' % p, file=sys.stderr)
            return 2

    text = io.open(args.draft, encoding='utf-8').read()
    lines = text.split('\n')

    body_out, calc_blocks, supply_tables, supply_text, work_notes = [], [], [], [], []
    stats = {'scaffold_removed': {'body': 0, 'table': 0, 'calc': 0, 'supply': 0},
             'internal_hits': [], 'work_notes_moved': 0,
             'chapter_heads_normalized': 0, 'dup_heads_dropped': 0,
             'front_removed': 0}

    cur_num, cur_name = '', ''
    mode = 'body'
    buf = []
    in_work_note = False
    in_front_skip = False
    seen_chapter = False

    def flush():
        if not buf:
            return
        if mode == 'calc':
            calc_blocks.append((cur_num, cur_name, list(buf)))
        elif mode == 'supply':
            rows = parse_md_table(buf)
            if rows:
                supply_tables.append(rows)
            else:
                supply_text.append((cur_num, list(buf)))
        buf[:] = []

    for raw in lines:
        s = raw.strip()
        htxt = heading_text(raw)

        # ---------- 前置部分：封面/责任页/目录（交由 build_docx 生成）----------
        if not seen_chapter and not args.keep_front and htxt is not None:
            if any(t in htxt for t in FRONT_SKIP_TITLES):
                flush()
                in_front_skip = True
                stats['front_removed'] += 1
                continue
            if in_front_skip:
                in_front_skip = False
        if in_front_skip:
            stats['front_removed'] += 1
            continue

        # ---------- 编制人工作说明 → 附录C ----------
        if htxt is not None and any(t in htxt for t in WORK_NOTE_TITLES):
            flush()
            in_work_note = True
            stats['work_notes_moved'] += 1
            continue
        if in_work_note:
            if htxt is not None and not any(t in htxt for t in WORK_NOTE_TITLES):
                in_work_note = False
            else:
                work_notes.append(raw)
                continue

        # ---------- 前置部分的封面行（`# 项目名` / `# 报告书`）：跳过 ----------
        if not seen_chapter and not args.keep_front and htxt is not None:
            nh = normalize_heading(htxt)
            if nh is None:
                stats['front_removed'] += 1
                continue

        # ---------- 四段式节标题 ----------
        sc = is_scaffold(raw)
        if sc:
            flush()
            stats['scaffold_removed'][sc] += 1
            mode = 'body' if sc in ('body', 'table') else sc
            continue

        # ---------- 标题 ----------
        if htxt is not None:
            nh = normalize_heading(htxt)
            if nh is not None:
                flush()
                mode = 'body'
                lvl, title = nh
                # 丢弃与当前章同名的重复二级标题（源稿 `### 10 水土保持管理` 之类）
                if lvl >= 2 and title.split('　')[-1].strip() == cur_name and seen_chapter:
                    stats['dup_heads_dropped'] += 1
                    continue
                if lvl == 1:
                    seen_chapter = True
                    cur_num, cur_name = '', title.split('　')[-1].strip()
                    stats['chapter_heads_normalized'] += 1
                    for k, v in stats['scaffold_removed'].items():
                        pass
                else:
                    cur_num = title.split(' ')[0]
                    cur_name = title[len(cur_num):].strip()
                # 标题同样要净化（源稿里出现过「本节点数据缺口清单」这类标题）
                title, thits = sanitize(title)
                for h in thits:
                    stats['internal_hits'].append(h)
                title = re.sub(r'^\s*[.、]\s*', '', title).strip()
                body_out.append('#' * lvl + ' ' + title)
                continue
            # 非编号标题（附表/附件/附图/附录）
            flush()
            mode = 'body'
            htxt2, thits2 = sanitize(htxt)
            for h in thits2:
                stats['internal_hits'].append(h)
            body_out.append('%s %s' % ('#' * (raw.strip().count('#') or 1), htxt2))
            continue

        if mode in ('calc', 'supply'):
            buf.append(raw)
            continue

        new, hits = sanitize(raw)
        for h in hits:
            stats['internal_hits'].append(h)
        body_out.append(new)

    flush()

    # ---------------------------------------------------------------- 附录
    appendix = []
    if not args.no_appendix:
        has_any = calc_blocks or supply_tables or supply_text or work_notes
        if has_any:
            appendix += ['# 附　录', '']
        if calc_blocks:
            appendix += ['## ' + APPENDIX_A, '',
                         '本节汇总本方案各节点的计算过程，供审查核对。'
                         '正文中以【待填：…】标注的输入项，待资料补充后按本节公式复算。', '']
            for num, name, blk in calc_blocks:
                label = (num + '　' + name) if num else name
                appendix += ['### %s' % label.rstrip('　'), '']
                for l in blk:
                    cl, _ = sanitize(l)
                    appendix.append(cl)
                appendix.append('')
        if supply_tables or supply_text:
            appendix += ['## ' + APPENDIX_B, '',
                         '下列资料须由建设单位在方案报批前补充提供，回填后本方案相应栏目即告闭合。', '']
            merged = merge_supply_rows(supply_tables)
            if merged:
                appendix += ['| 序号 | 待补数据 | 涉及章节/表格 | 关联占位符 |',
                             '|:---|:---|:---|:---|']
                for i, (f, imp, ph) in enumerate(merged, 1):
                    appendix.append('| %d | %s | %s | %s |' % (i, f, imp or '—', ph or '—'))
                appendix.append('')
            for num, blk in supply_text:
                if num:
                    appendix += ['**%s**' % num, '']
                for l in blk:
                    if l.strip():
                        appendix.append(l)
                appendix.append('')
        if work_notes:
            appendix += ['## ' + APPENDIX_C, '']
            for l in work_notes:
                wl, _ = sanitize(l)
                if re.match(r'^\s*[#*`\-]{3,}\s*$', wl):
                    continue
                appendix.append(wl)
            appendix.append('')

    def tidy(ls):
        out, blank = [], 0
        for l in ls:
            if not l.strip():
                blank += 1
                if blank > 1:
                    continue
            else:
                blank = 0
            out.append(l.rstrip())
        while out and not out[-1].strip():
            out.pop()
        return out

    final = tidy(body_out + ([''] + appendix if appendix else []))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
    io.open(args.out, 'w', encoding='utf-8', newline='\n').write('\n'.join(final) + '\n')

    total_scaffold = sum(stats['scaffold_removed'].values())
    out_text = '\n'.join(final)
    result = {
        'draft': args.draft, 'out': args.out,
        'chars_in': len(text), 'chars_out': len(out_text),
        'scaffold_removed': stats['scaffold_removed'],
        'scaffold_total': total_scaffold,
        'chapter_heads_normalized': stats['chapter_heads_normalized'],
        'dup_heads_dropped': stats['dup_heads_dropped'],
        'front_removed': stats['front_removed'],
        'work_notes_moved': stats['work_notes_moved'],
        'internal_hits_total': sum(h[1] for h in stats['internal_hits']),
        'internal_hits_kinds': sorted({h[0] for h in stats['internal_hits']}),
        'calc_nodes': len(calc_blocks),
        'supply_tables': len(supply_tables),
        'supply_items': len(merge_supply_rows(supply_tables)),
        'appendix': bool(appendix),
        'placeholders': len(re.findall(r'【待填', out_text)),
        'h1_chapters': len(re.findall(r'^#\s+第\d+章', out_text, re.M)),
    }
    result['ok'] = (result['h1_chapters'] >= 1)

    if args.report:
        rl = ['# 交付稿净化报告', '',
              '| 项目 | 数值 |', '|:---|:---|',
              '| 输入 | `%s` |' % args.draft,
              '| 输出 | `%s` |' % args.out,
              '| 正文字符 | %d → %d |' % (result['chars_in'], result['chars_out']),
              '| 去除四段式节标题 | **%d** 处 %s |' % (total_scaffold, stats['scaffold_removed']),
              '| 章标题归一（`#` 标记） | %d 个 |' % result['chapter_heads_normalized'],
              '| 删除重复节点标题 | %d 个 |' % result['dup_heads_dropped'],
              '| 删除 md 前置部分行 | %d 行 |' % result['front_removed'],
              '| 内部行话清除 | %d 处 |' % result['internal_hits_total'],
              '| 工作说明转附录C | %d 节 |' % result['work_notes_moved'],
              '| 计算过程归入附录A | %d 个节点 |' % result['calc_nodes'],
              '| 补数清单归入附录B | %d 张表 / %d 条 |' % (result['supply_tables'], result['supply_items']),
              '| 剩余占位符 | %d 处 |' % result['placeholders'], '']
        if result['internal_hits_kinds']:
            rl += ['## 已清除的内部行话', ''] + ['- `%s`' % k for k in result['internal_hits_kinds']] + ['']
        io.open(args.report, 'w', encoding='utf-8', newline='\n').write('\n'.join(rl) + '\n')

    sys.stdout.reconfigure(encoding='utf-8')
    if args.json_only:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print('交付稿净化完成')
        print('  四段式节标题去除 : %d 处 %s' % (total_scaffold, stats['scaffold_removed']))
        print('  章标题归一       : %d 个（均为 `#` 级）' % result['chapter_heads_normalized'])
        print('  删除重复节点标题 : %d 个' % result['dup_heads_dropped'])
        print('  删除 md 前置部分 : %d 行（另由 build_docx.py 生成封面/责任页/目录）' % result['front_removed'])
        print('  内部行话清除     : %d 处' % result['internal_hits_total'])
        print('  计算过程 → 附录A : %d 个节点' % result['calc_nodes'])
        print('  补数清单 → 附录B : %d 张表 / %d 条' % (result['supply_tables'], result['supply_items']))
        print('  正文字符         : %d → %d' % (result['chars_in'], result['chars_out']))
        print('  输出             : %s' % args.out)
    return 0


def _inside_skill(path):
    try:
        ap = os.path.abspath(path)
    except Exception:
        return False
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root in (here, os.environ.get('DSH_WS_VAULT', '')):
        if root and ap.lower().startswith(os.path.abspath(root).lower()):
            return True
    if os.sep + 'vault' + os.sep in ap.lower():
        return True
    return False


if __name__ == '__main__':
    sys.exit(main())

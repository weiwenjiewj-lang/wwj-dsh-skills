#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""篇幅与深度预算模块（被 write_chapter / check_draft / outline / check_plan 共用）。

规则全部来自 references/rules.json 的 length_budget（本文件只执行，不另立阈值）：
  · 全书目标 150–180 页 → 折算正文字数 → 章间按 Zone C 实测篇幅占比、章内按节点权重分配
  · 写作时下发「本节目标字数区间 + 必备表数 + 深度要求」
  · 校核时核对实际字数，报「偏少 / 达标 / 超标」
另提供：中文正文字数统计（排除 markdown 语法与占位符）、按标题把稿件归集到节点。

命令行（用于查看分配结果，也可被其它脚本 import）:
    python budget.py --book                  # 全书与各章预算
    python budget.py --chapter 2             # 某章逐节预算
    python budget.py --count 稿件.md          # 统计稿件字数与逐节篇幅判定
    python budget.py --count 稿件.md --progress
"""
import os
import re
import sys

sys.dont_write_bytecode = True   # 技能包不留 __pycache__（避免缓存掩盖规则改动）

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import check_gate as G  # noqa: E402

RULES = G.RULES
NODES = G.NODES
LB = RULES.get('length_budget', {})

# ---------------------------------------------------------------- 预算计算
def book_target_chars():
    """全书正文目标字数（扣除图表等非正文占比）。"""
    pages = LB.get('page_target', {})
    mid = pages.get('mid') or ((pages.get('min', 150) + pages.get('max', 180)) / 2.0)
    cpp = LB.get('chars_per_page', 1000)
    overhead = LB.get('page_overhead_ratio', 0.25)
    return mid * cpp * (1.0 - overhead)


def node_weight(node):
    lw = LB.get('level_weights', {})
    kf = LB.get('kind_factors', {})
    return (float(lw.get(str(node.get('level')), 0) or 0)
            * float(kf.get(node.get('node_kind'), 0) or 0)
            * float(node.get('point_count', 0) or 0))


def _bounds(target):
    """由目标字数推区间，保证 下限 ≤ 目标 ≤ 上限（低于下限时取节点硬性下限）。"""
    tol = LB.get('tolerance', {})
    lo_r, hi_r = tol.get('under_ratio', 0.8), tol.get('over_ratio', 1.25)
    floor, ceil = LB.get('node_min_chars', 300), LB.get('node_max_chars', 12000)
    t = max(floor, min(ceil, target))
    lo = max(floor, t * lo_r)
    hi = max(t, min(ceil, t * hi_r))
    return int(round(t)), int(round(lo)), int(round(hi))


def _rebalance(out, book):
    """把节点级取整造成的余数分摊掉，使各节目标合计 == 全书正文目标。

    逐节点 int(round()) 会累积出几百字的差额（实测 +234 字），文档与脚本并列两个数
    容易被当成矛盾。这里按「原始小数余量」排序，逐字加减（不越过节点 min/max）。
    返回未能分摊的余额（正常为 0）。
    """
    diff = int(round(book)) - sum(v['target'] for v in out.values())
    if not diff or not out:
        return diff
    step = 1 if diff > 0 else -1
    order = sorted(out, key=lambda c: (out[c]['target_raw'] - out[c]['target']) * step,
                   reverse=True)
    moved = True
    while diff and moved:
        moved = False
        for c in order:
            if not diff:
                break
            v = out[c]
            nt = v['target'] + step
            if nt < v['min'] or nt > v['max']:
                continue
            v['target'] = nt
            diff -= step
            moved = True
    return diff


def _budget_map():
    """{chapter_id: {target, min, max, weight, share}}（只含有权重的节点）。

    两级分配：① 章间按 rules.json length_budget.chapter_shares（Zone C 实测篇幅分布）；
    ② 章内按节点权重（层级权重 × 节点类型系数 × 内容点数）。
    """
    book = book_target_chars()
    shares = LB.get('chapter_shares', {}) or {}
    # 章权重合计
    ch_sum = {}
    for cid, n in NODES.items():
        top = cid.split('.')[0]
        w = node_weight(n)
        if w > 0:
            ch_sum[top] = ch_sum.get(top, 0.0) + w
    # 未单列占比的章按权重兜底，避免无预算
    listed = set(shares)
    unlisted = {c: w for c, w in ch_sum.items() if c not in listed}
    if unlisted and listed:
        leftover = max(0.0, 1.0 - sum(shares.values()))
        tot_un = sum(unlisted.values())
        for c, w in unlisted.items():
            shares = dict(shares, **{c: leftover * w / tot_un if tot_un else 0})
    if not shares:                       # 完全没配占比时退化为纯权重分配
        tot = sum(ch_sum.values())
        shares = {c: w / tot for c, w in ch_sum.items()} if tot else {}

    out = {}
    for cid, n in NODES.items():
        w = node_weight(n)
        if w <= 0:
            continue
        top = cid.split('.')[0]
        ch_share = shares.get(top, 0.0)
        ch_total = ch_sum.get(top, 0.0)
        if ch_total <= 0:
            continue
        target = book * ch_share * (w / ch_total)
        t, lo, hi = _bounds(target)
        out[cid] = {
            'target': t, 'min': lo, 'max': hi,
            'target_raw': int(round(target)),
            'weight': round(w, 2),
            'share': round(ch_share * w / ch_total, 5),
            'chapter_share': ch_share,
        }
    residual = _rebalance(out, book)
    return out, book, sum(ch_sum.values()), residual


BUDGETS, BOOK_CHARS, WEIGHT_TOTAL, RESIDUAL = _budget_map()


def node_budget(cid):
    """节点预算。容器节点（自身无权重）取子节点预算之和，避免正文写在容器标题下就查不到预算。

    容器不按单节 node_min/max 夹取——单节上下限（300/12000 字）是给**叶子节**的，
    套到容器上会把第 2 章 3.7 万字的预算压成 1.2 万字（与 chapter_budget 自相矛盾）。
    """
    if cid in BUDGETS:
        return BUDGETS[cid]
    kids = [c for c in BUDGETS if c.startswith(cid + '.')]
    if not kids:
        return None
    raw = sum(BUDGETS[c]['target'] for c in kids)
    tol = LB.get('tolerance', {})
    return {
        'target': raw,
        'min': int(round(raw * tol.get('under_ratio', 0.8))),
        'max': int(round(raw * tol.get('over_ratio', 1.25))),
        'weight': 0,
        'share': round(sum(BUDGETS[c]['share'] for c in kids), 5),
        'container_rollup': True,
        'child_nodes': sorted(kids),
    }


def chapter_budget(chapter):
    """某章（含其下所有节点）的合计预算。只汇总有独立权重的叶子节点，避免与容器重复计。"""
    top = str(chapter).split('.')[0]
    rows = [dict(BUDGETS[c], chapter_id=c, title=NODES[c]['title'])
            for c in sorted(BUDGETS) if c.split('.')[0] == top]
    return {
        'chapter': chapter,
        'nodes': rows,
        'target': sum(r['target'] for r in rows),
        'min': sum(r['min'] for r in rows),
        'max': sum(r['max'] for r in rows),
        'share': round(sum(r['share'] for r in rows), 4),
    }


def book_budget():
    pages = LB.get('page_target', {})
    cpp = LB.get('chars_per_page', 1000)
    return {
        'page_target': pages,
        'chars_per_page': cpp,
        'book_target_chars': int(round(BOOK_CHARS)),
        'nodes_target_sum': sum(v['target'] for v in BUDGETS.values()),
        'residual_chars': RESIDUAL,
        'nodes_with_budget': len(BUDGETS),
        'book_min_chars': int(round(BOOK_CHARS * LB.get('tolerance', {}).get('under_ratio', 0.8))),
        'book_max_chars': int(round(BOOK_CHARS * LB.get('tolerance', {}).get('over_ratio', 1.25))),
        'estimated_pages_at_target': round(BOOK_CHARS / cpp / (1 - LB.get('page_overhead_ratio', 0.25)), 1),
        'rule': 'rules.json length_budget',
    }


def depth_requirements(cid):
    """按节点所属章返回深度要求（含章节类别）。"""
    d = LB.get('depth_requirements', {})
    base = d.get('default', {})
    cls_map = LB.get('depth_class_map', {})
    top = (cid or '').split('.')[0]
    category = None
    for name, chapters in cls_map.items():
        if top in chapters:
            category = name
            break
    out = dict(base)
    if category:
        out.update({k: v for k, v in d.get(category, {}).items()})
        out['category'] = category
    tables_nodes = LB.get('nodes_requiring_tables', [])
    out['tables_required'] = any(cid == t or (cid or '').startswith(t + '.') for t in tables_nodes)
    return out


def verdict(actual, cid):
    """把实际字数判为 偏少 / 达标 / 超标。"""
    b = node_budget(cid)
    if not b:
        return {'chapter_id': cid, 'actual': actual, 'status': '无预算'}
    if actual < b['min']:
        st = '偏少'
    elif actual > b['max']:
        st = '超标'
    else:
        st = '达标'
    return {'chapter_id': cid, 'actual': actual, 'target': b['target'],
            'min': b['min'], 'max': b['max'], 'status': st,
            'container_rollup': b.get('container_rollup', False),
            'delta_ratio': round((actual - b['target']) / b['target'], 3) if b['target'] else None}


# ---------------------------------------------------------------- 字数统计
_MD_NOISE = re.compile(r'^\s*\|?[\s:|-]+\|?\s*$')          # 表格分隔行
_HTML_COMMENT = re.compile(r'<!--.*?-->', re.S)
_FENCE = re.compile(r'```.*?```', re.S)
_PLACEHOLDER = re.compile(r'【[^】]{0,80}】')
_CJK = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]')
_WORD = re.compile(r'[A-Za-z0-9]+')
_FRONTMATTER = re.compile(r'\A\ufeff?---\r?\n.*?\r?\n---\r?\n', re.S)


def strip_frontmatter(text):
    """去掉 YAML frontmatter（库文件的属性块），它不属于方案正文。"""
    return _FRONTMATTER.sub('', text or '')


def count_chars(text, exclude_placeholders=True):
    """中文正文口径字数：去 markdown 噪声后统计 CJK 字符 + 英文数字词组（各计 1）。"""
    t = strip_frontmatter(text)
    t = _FENCE.sub(' ', t)
    t = _HTML_COMMENT.sub(' ', t)
    lines = [ln for ln in t.splitlines() if not _MD_NOISE.match(ln)]
    t = '\n'.join(lines)
    if exclude_placeholders:
        t = _PLACEHOLDER.sub(' ', t)
    t = re.sub(r'^\s*#{1,6}\s*', '', t, flags=re.M)        # 标题符号
    t = re.sub(r'^\s*[-*+>]\s*', '', t, flags=re.M)        # 列表/引用符号
    return len(_CJK.findall(t)) + len(_WORD.findall(t))


def count_placeholders(text):
    return len(_PLACEHOLDER.findall(text or ''))


def table_count(text):
    """统计 markdown 表格数量（按表头 + 分隔行识别）。"""
    lines = (text or '').splitlines()
    n = 0
    for i in range(len(lines) - 1):
        if lines[i].strip().startswith('|') and _MD_NOISE.match(lines[i + 1] or ''):
            n += 1
    return n


# ---------------------------------------------------------------- 稿件 → 节点归集
_HEAD = re.compile(r'^(#{1,6})\s*(.+?)\s*$', re.M)


def _match_node_in_head(head):
    """从标题文本中识别节点号；识别不出返回 ''。

    ## 两条必须的约束（都是自检抓出来的真实误判）

    ① **节点号必须是独立 token**：前面不能紧邻数字或小数点。
       否则 `（二）水土流失防治分区` 这种中文序号标题会被误判——实测它匹配到了
       **`7.2`**（因为某节点标题里含"水土流失"相关字样时编号恰好命中），
       进而把整节正文归错节点，篇幅判定全乱。

    ② **优先取标题**：`1.6.2 水土流失防治分区及措施` 应匹配 `1.6.2` 而非 `1.6`
       （最长匹配），也不应被父节点 `1.6` 抢先。

    识别顺序：
      1) 标题**开头**就是节点号（`1.6.2 xxx` / `## 1.6.2 xxx`）——最可靠；
      2) 否则在标题里找**独立成词**的节点号（前后为空白或标点）；
      均取最长匹配；都识别不出才退化为标题逐字匹配。
    """
    if not head:
        return ''
    h = head.strip()
    # ① 开头即节点号（允许前面有「第」「§」等引导符）
    m = re.match(r'^[第§\s]*(\d+(?:\.\d+)+|\d+)\b', h)
    if m and m.group(1) in NODES:
        return m.group(1)
    # ② 独立成词的节点号（前后须为空白/标点/行首行尾）
    best = ''
    for cand in NODES:
        # 前置字符不能是数字或小数点：排除 "1.6.2" 里再匹配出 "6.2" 这类
        for mm in re.finditer(re.escape(cand), h):
            i, j = mm.start(), mm.end()
            before = h[i - 1] if i > 0 else ''
            after = h[j] if j < len(h) else ''
            if before and (before.isdigit() or before == '.'):
                continue
            if after and (after.isdigit() or after == '.'):
                continue
            if len(cand) > len(best):
                best = cand
            break
    if best:
        return best
    # ③ 标题逐字匹配——**只在① ② 都失败时，且标题必须是"章节式"的才用**。
    #
    # ⚠ 这一步是危险的：模板标题（如「水土流失防治分区」）会作为**子串**
    # 命中写作者的子标题（如 `### （二）水土流失防治分区`），
    # 实测把该子标题误判成 `1.6`、把 `### （一）方案设计水平年` 误判成 `7.2`，
    # 导致整节正文归错节点、篇幅判定全乱。
    #
    # 因此加两道闸：① 标题**不含**中文序号（一、二、三…）;
    #              ② 标题长度接近模板标题（不是子标题那种带说明的长句）。
    if re.search(r'[（(【]?\s*[一二三四五六七八九十]+\s*[）)】]', h):
        return ''
    for cand in NODES:
        t = NODES[cand].get('title')
        if t and t in h and len(cand) > len(best):
            best = cand
    return best


def split_by_node(text):
    """把稿件按标题切分并归集到节点：{chapter_id: 正文字数}。

    标题里出现的节点号取「最长匹配」（1.6.2 优先于 1.6）；未识别的章节归到 '' 键。

    ## 子标题归属（自检抓出的 bug，务必保留此逻辑）

    原实现按「下一个标题」切段，于是**无法识别节点号的子标题会把正文抢走**：
    稿件里写了 `# 1.6.2 xxx` 之后再有 `## 一、正文` / `### （一）xxx`，
    这些子标题的 cid 为空，其正文全部归到 `''`（未归入任何节点），
    导致 `1.6.2` 统计为 **0 字**、篇幅判定误报「偏少 -100%」。

    而多级子标题是**正常且推荐的写法**（四段式本身就带子标题），
    所以这个 bug 会让绝大多数真实稿件统计失真。

    修复：**子标题继承最近一个可识别节点**——遇 cid 为空的标题不切断归属，
    继续计入上一个已知节点，直到出现下一个能识别节点的标题。
    """
    marks = []
    for m in _HEAD.finditer(text or ''):
        marks.append((m.start(), m.end(), _match_node_in_head(m.group(2))))
    out = {}
    if not marks:
        out[''] = count_chars(text)
        return out
    if marks[0][0] > 0:                              # 标题之前的引言
        out[''] = count_chars(text[:marks[0][0]])
    # 先把每个标题的 cid 按「子标题继承上级节点」补齐
    resolved = []
    last_cid = ''
    for s, e, cid in marks:
        if cid:
            last_cid = cid
        resolved.append((s, e, cid or last_cid))
    for i, (s, e, cid) in enumerate(resolved):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[e:end]
        out[cid] = out.get(cid, 0) + count_chars(body)
    return out


def progress(chars_by_node):
    """全书进度：已写节点数、字数累计、对预算的偏差。

    容器节点与其子节点同时有正文时，只按子节点计（避免同一段文字被算两次）。
    """
    written = {c: n for c, n in chars_by_node.items() if n > 0 and node_budget(c)}
    # 章级标题（如「## 2 项目概况」）是结构骨架，不是某节正文：只计字数，不参与篇幅判定；
    # 章内容器标题（如「## 1.6 水土流失防治」）下的正文则按子节点预算之和判定。
    top_chapters = [c for c in written if '.' not in c]
    written = {c: n for c, n in written.items() if c not in top_chapters}
    containers = [c for c in written if c not in BUDGETS]
    drop = set()
    for c in containers:
        if any(o != c and o.startswith(c + '.') for o in written):
            drop.add(c)
    written = {c: n for c, n in written.items() if c not in drop}
    unbudgeted = {c: n for c, n in chars_by_node.items() if n > 0 and not node_budget(c)}
    total_chars = sum(chars_by_node.values())
    book = book_target_chars()
    stage_target = sum(node_budget(c)['target'] for c in written)
    return {
        'written_nodes': len(written),
        'budgeted_nodes': len(BUDGETS),
        'written_chars': total_chars,
        'unattributed_chars': sum(unbudgeted.values()),
        'unattributed_nodes': sorted(unbudgeted),
        'stage_target_chars': stage_target,
        'book_target_chars': int(round(book)),
        'stage_delta_ratio': round((sum(written.values()) - stage_target) / stage_target, 3) if stage_target else None,
        'book_completion_ratio': round(total_chars / book, 3) if book else None,
        'estimated_pages_so_far': round(total_chars / LB.get('chars_per_page', 1000)
                                        / (1 - LB.get('page_overhead_ratio', 0.25)), 1),
        'container_rollup_used': sorted(set(containers) - drop),
    }


def _main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description='篇幅与深度预算：查看分配结果 / 统计稿件篇幅')
    ap.add_argument('--book', action='store_true', help='全书与各章预算（默认）')
    ap.add_argument('--chapter', default=None, help='某章逐节预算')
    ap.add_argument('--count', default=None, help='统计稿件字数并逐节判定')
    ap.add_argument('--progress', action='store_true', help='配合 --count：输出全书进度')
    ap.add_argument('--json-only', action='store_true',
                    help='只输出机器可读 JSON（供流水线 json.load 直接解析）')
    a = ap.parse_args()

    if a.count:
        text = G.read_text(a.count, '稿件')
        per = split_by_node(text)
        if a.json_only:
            print(json.dumps({'draft': a.count,
                              'chars': count_chars(text),
                              'placeholders': count_placeholders(text),
                              'tables': table_count(text),
                              'per_node': [dict(verdict(per[c], c), chapter_id=c)
                                           for c in sorted(per, key=lambda x: (x.count('.'), x))],
                              'progress': progress(per) if a.progress else None},
                             ensure_ascii=False, indent=1))
            return
        print('稿件：%s' % a.count)
        print('正文口径字数：%d　占位符：%d　表格：%d' % (count_chars(text), count_placeholders(text), table_count(text)))
        print()
        print('%-10s %8s %8s %8s %8s %6s' % ('节点', '实际', '目标', '下限', '上限', '判定'))
        for cid in sorted(per, key=lambda c: (c.count('.'), c)):
            v = verdict(per[cid], cid)
            print('%-10s %8d %8s %8s %8s %6s'
                  % (cid or '(标题前)', per[cid], v.get('target', '—'), v.get('min', '—'),
                     v.get('max', '—'), v['status']))
        if a.progress:
            print()
            print(json.dumps(progress(per), ensure_ascii=False, indent=1))
        return

    if a.chapter:
        cb = chapter_budget(a.chapter)
        if a.json_only:
            print(json.dumps(cb, ensure_ascii=False, indent=1))
            return
        print('第%s章 目标 %d 字（%d–%d），占全书 %.2f%%，节点 %d 个'
              % (a.chapter, cb['target'], cb['min'], cb['max'], cb['share'] * 100, len(cb['nodes'])))
        print()
        print('%-10s %-16s %8s %8s %8s' % ('节点', '标题', '目标', '下限', '上限'))
        for r in cb['nodes']:
            print('%-10s %-16s %8d %8d %8d' % (r['chapter_id'], r['title'][:16], r['target'], r['min'], r['max']))
        return

    bk = book_budget()
    if a.json_only:
        print(json.dumps(dict(bk, chapters={c: chapter_budget(c)['target']
                                            for c in sorted({x.split('.')[0] for x in BUDGETS})},
                              nodes=BUDGETS), ensure_ascii=False, indent=1))
        return
    print(json.dumps(bk, ensure_ascii=False, indent=1))
    print()
    print('%-8s %-16s %8s %8s %8s %8s' % ('节点', '标题', '目标', '下限', '上限', '权重'))
    for cid in sorted(BUDGETS, key=lambda c: -BUDGETS[c]['target'])[:15]:
        b = BUDGETS[cid]
        print('%-8s %-16s %8d %8d %8d %8.1f'
              % (cid, NODES[cid]['title'][:16], b['target'], b['min'], b['max'], b['weight']))
    print()
    ch = {}
    for cid, b in BUDGETS.items():
        top = cid.split('.')[0]
        ch[top] = ch.get(top, 0) + b['target']
    print('按章合计目标字数:', json.dumps(ch, ensure_ascii=False))
    print('合计:', sum(ch.values()), '（全书正文目标 %s）' % bk['book_target_chars'])


if __name__ == '__main__':
    _main()

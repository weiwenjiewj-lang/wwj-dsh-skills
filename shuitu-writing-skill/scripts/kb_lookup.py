#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库安全查询入口（唯一推荐的查询方式，避免 AI 直读大文件烧 token）。

## 它解决什么问题

知识库 274 个 md / 452 MB。**单文件最大 42 MB ≈ 1256 万 token**，
中位数文件也有 397 KB ≈ 11.6 万 token。AI 若直接 `read` 或 `grep` 这些文件，
一次就可能烧掉上千万 token——这是最大的 token 黑洞。

本工具提供**三条廉价通道**，全部只输出结论、不输出原文：

    ① `--map`     资产地图：什么信息在哪个索引里（**先看这个**）
    ② `--ask <项>` 按条目取结论（物种/参数/工法/写法范式）
    ③ `--find <词>` 全库**只返回命中位置**（文件名+行号+极短片段），不返回正文

## 硬规则（本工具强制执行）

- `--find` 单次返回上限极低（默认 5 条、每条 ≤80 字），**永不打印文件正文**
- 任何情况下都提示"要看原文请用编辑器打开该文件"，**不代替 AI 读原文**
- 若某文件 >2 MB，`--find` 会在结果里标注"⚠ 大文件，勿直读"

## 用法

    python kb_lookup.py --map                       # 资产地图（先看）
    python kb_lookup.py --ask 高羊茅                 # 取某个物种结论
    python kb_lookup.py --ask 弃渣场级别              # 取某组参数
    python kb_lookup.py --find 表土剥离厚度           # 查在哪里出现
    python kb_lookup.py --stats                     # 知识库体积分布（含风险提示）
"""
import argparse
import glob
import io
import json
import os
import re
import sys
import time

sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REF = os.path.join(SKILL, 'references')
from vault_paths import VAULT
import vault_paths as VP
MD = os.path.join(VAULT, 'md库')

# 大文件阈值：超过即在结果里提示"勿直读"
BIG_KB = 2048

# base64 图片行特征：超长且只含 base64 字符集。
# 知识库 452 MB 中 **78% 是 base64 图片（352 MB / 5577 张），真实文本仅 65.9 MB**。
# 扫描时跳过图片行，可把待扫数据从 452 MB 降到约 99 MB（快约 5 倍）。
LONG_B64 = re.compile(r'^[A-Za-z0-9+/=]{200,}$')


def _is_image_line(line):
    return 'base64,' in line or bool(LONG_B64.match(line.strip()))

# 资产地图：条数**一律从 JSON 的 count 字段实时读取**，绝不硬编码。
# （硬编码会随资产重建而漂移——曾出现「措施 27 项」实际 33 项、「Zone B 116 条」实际 115 条。）
# 结构：(名称, 文件名, count 取值路径, 量词, 数字后的补充说明, 取用命令)
ASSET_MAP = [
    ('分章取用', 'asset-map.json（**禁全文检索的主通道**）', None, '',
     '按模板节点列该节全部 Zone A/B/C 资源路径',
     'python kb_lookup.py --chapter 9.1.2'),
    ('章节总览', 'asset-map.json', None, '',
     '全书各章的 Zone A/B/C 资源条数',
     'python kb_lookup.py --chapters'),
    ('规范条款', 'zone-a-index.json', ('count',), '条',
     '，含 doc_number/title/chapter_relevance',
     'python kb_lookup.py --ask-a <标准号>'),
    ('写法范式', 'zone_c_style_samples.json', ('stats', 'kept'), '条',
     '，按主题索引（段落功能/句式骨架/论证链）',
     'python kb_lookup.py --ask-style <主题>'),
    ('物种知识', 'species_index.json', ('count',), '个乡土树草种',
     '（习性/适生条件/水保功能）',
     'python kb_lookup.py --ask 高羊茅'),
    ('设计参数', 'design_params.json', ('count',), '组',
     '定级阈值（弃渣场级别/防洪标准/表土质量…）',
     'python kb_lookup.py --ask 弃渣场级别'),
    ('措施工法', 'measure_methods.json', ('count',), '项',
     '措施设计要求（附 Zone A 条号）',
     'python kb_lookup.py --ask 截水沟'),
    ('文体范式', 'zone-b-index.json', ('count',), '条',
     '，含 style_role 标记',
     'python kb_lookup.py --ask-b'),
    ('范例索引', 'zone-c-index.json', ('count',), '份',
     '已批方案（topic_tags/structure_summary）',
     'python kb_lookup.py --ask-c <主题>'),
    ('项目事实', '台账.json（项目侧）', None, '',
     '跨章口径唯一来源',
     'python "$SK\\scripts\\ledger.py" --file 台账.json --show'),
]


def _asset_count(filename, path):
    """从资产 JSON 里实时取条数；取不到返回 None（此时地图不显示数字，只显示索引名）。"""
    if not path:
        return None
    d = load(filename)
    if not isinstance(d, dict):
        return None
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur if isinstance(cur, int) else None


def die(msg):
    sys.stderr.write('%s\n' % msg)
    sys.exit(2)


def load(name):
    p = os.path.join(REF, name)
    if not os.path.exists(p):
        return None
    try:
        return json.load(io.open(p, encoding='utf-8-sig'))
    except Exception:
        return json.load(io.open(p, encoding='utf-8'))


# ---------------------------------------------------------------- 资产地图
def cmd_map():
    # 库不可用必须**显式告警**，不能只报"0 个 md"——
    # 那会让使用者误以为"库是空的"，而真实原因是路径不对（尤其 DSH_WS_VAULT 指错时）。
    if not VP.is_ready():
        print('# ⚠ 知识库不可达')
        print('')
        print(VP.unavailable_reason())
        print('')
        print('索引快照（references/*.json）仍可用，但**实时取正文不可用**。')
        print('重建步骤：修正路径 → `python "$SK\\scripts\\rebuild_index.py" --rebuild`')
        return
    fs = glob.glob(os.path.join(MD, '**', '*.md'), recursive=True)
    n_md = len(fs)
    mb = sum(os.path.getsize(p) for p in fs) / 1024 / 1024 if fs else 0
    print('# 知识库资产地图（先看这里，**不要 grep 全库**）')
    print('')
    print('知识库 %d 个 md / %.0f MB / 约 %.0f 万 token。' % (n_md, mb, mb * 1024 * 1024 / 3.5 / 10000))
    print('位置：%s' % VP.resolve())
    print('**直读或全文检索是最大的 token 黑洞**（单文件最大 60+ MB）。')
    print('按下面索引定位到**具体文件**，再只读那一份：')
    print('')
    print('| 要找什么 | 看哪个索引 | 内容 | 取用命令 |')
    print('|:---|:---|:---|:---|')
    for what, f, cpath, unit, rest, cmd in ASSET_MAP:
        n = _asset_count(f, cpath) if cpath else None
        desc = ('%d %s%s' % (n, unit, rest)) if n is not None else rest
        print('| **%s** | `%s` | %s | `%s` |' % (what, f, desc, cmd))
    print('')
    print('## 检索仅作兜底，不是首选')
    print('')
    print('```bash')
    print('python kb_lookup.py --find 表土剥离厚度    # 索引查不到时才用；只返回位置，不返回正文')
    print('```')
    print('')
    print('> 查到的文件请用**编辑器**打开阅读，不要让 AI 直读。')
    return 0


# ---------------------------------------------------------------- 按条目取结论
def cmd_ask(term):
    term = (term or '').strip()
    if not term:
        die('--ask 需要关键词')
    found = False

    # ① 物种
    sp = load('species_index.json') or {}
    for name, rec in (sp.get('species') or {}).items():
        if term == name or term in name:
            print('【物种】%s' % name)
            for k in ('学名', '科属', '生活型', '生态习性', '适生条件', '水土保持功能'):
                if rec.get(k):
                    print('  %s：%s' % (k, rec[k]))
            if rec.get('出处'):
                print('  出处：%s' % rec['出处'][0])
            found = True
            break

    # ② 设计参数
    dp = load('design_params.json') or {}
    for key, v in (dp.get('params') or {}).items():
        if term in key:
            print('【设计参数】%s' % key)
            print('  依据：%s' % v.get('依据'))
            if v.get('说明'):
                print('  说明：%s' % v['说明'])
            for r in (v.get('数据') or [])[:20]:
                print('    ' + ' | '.join(r))
            found = True

    # ③ 措施工法
    mm = load('measure_methods.json') or {}
    for key, v in (mm.get('methods') or {}).items():
        if term in key:
            print('【措施工法】%s（%s）' % (key, v.get('类型', '')))
            print('  依据：%s' % v.get('依据'))
            for s in (v.get('Zone A 设计要求') or [])[:8]:
                print('    - %s' % s)
            found = True

    if not found:
        # 明确区分「查不到」与「查错地方了」——
        # `--ask` 只查三类**加工资产**（物种 / 设计参数 / 措施工法）。
        # 想找规范/标准/文件本身，应走 --chapter 或 --ask-a；否则使用者会以为库里没有。
        print('在**加工资产**（物种 / 设计参数 / 措施工法）中未找到「%s」。' % term)
        print('')
        print('注意 --ask 只查这三类加工资产，**不查规范文件本身**。若你要找的是：')
        print('  · 某份规范/标准/文件        → `--ask-a`（列出 Zone A 全部依据文件）')
        print('  · 某章节该用哪些依据        → `--chapter <节点>`，如 `--chapter 9.1.2`')
        print('  · 某条规范里的具体条款      → 先用 `--ask-a` 定位文件，再只读那一份')
        print('  · 写法样例                  → `--ask-style <主题>`')
        print('  · 资产地图                  → `--map`')
        return 1
    return 0


def cmd_ask_style(topic):
    ss = load('zone_c_style_samples.json') or {}
    topics = ss.get('topics') or {}
    if topic not in topics:
        cand = [t for t in topics if topic in t]
        if not cand:
            print('无该主题。可用主题：%s' % '、'.join(list(topics)[:20]))
            return 1
        topic = cand[0]
    print('【写法范式】%s（%d 条）' % (topic, len(topics[topic])))
    for it in topics[topic][:5]:
        print('  [%s] 论证链: %s' % (it.get('function') or '通用', it.get('argument_chain') or '-'))
        print('    %s' % (it.get('text') or '')[:220])
    return 0


def cmd_ask_a(std=None):
    za = load('zone-a-index.json') or {}
    ents = za.get('entries') or []
    print('【Zone A 规范层】共 %d 条' % len(ents))
    for e in ents:
        dn = e.get('doc_number') or ''
        if std and std not in dn and std not in (e.get('title') or ''):
            continue
        print('  %-24s %s' % (dn[:22], (e.get('title') or '')[:46]))
    return 0


def cmd_ask_c(topic=None):
    zc = load('zone-c-index.json') or {}
    ents = zc.get('entries') or []
    hits = 0
    for e in ents:
        tags = e.get('topic_tags') or []
        if topic and not any(topic in t for t in tags):
            continue
        if topic:
            print('  %-44s %s' % ((e.get('title') or '')[:42], e.get('project_type')))
            hits += 1
            if hits >= 12:
                break
    if not topic:
        print('【Zone C 范例】共 %d 份' % len(ents))
        from collections import Counter
        for k, v in Counter(x.get('project_type', '?') for x in ents).most_common():
            print('  %-16s %d' % (k, v))
    return 0


# ---------------------------------------------------------------- 只返回位置
def cmd_find(word, limit=5, maxlen=80):
    """全库检索：**流式扫描，100% 覆盖**，只返回位置。

    ## 为什么改成流式（修正一处实测缺陷）

    先前版本为控制内存，对 >2 MB 的文件**只读前 2 MB**。实测后果：
    44 个大文件（最大 42 MB）后半部分完全搜不到，**全库 59%（265 MB）成为检索盲区**。

    改为**逐行流式读取 + 跳过 base64 图片行**：
      · 无内存上限 → **100% 覆盖**（实测扫描 99 MB 全文本，1.4s）
      · 跳过图片行 → 待扫数据从 452 MB 降到 99 MB（快约 5 倍）
      · 命中数从"只看得到 5 处"变成"全部命中后取前 5"

    输出仍严格受限：文件名 + 行号 + ≤80 字片段，**永不打印文件正文**。
    """
    if not word:
        die('--find 需要关键词')
    print('在知识库中查找「%s」（流式全库扫描，只返回位置）' % word)
    print('')
    hits = 0
    total_hits = 0
    scanned = 0
    t0 = time.time()
    for p in sorted(glob.glob(os.path.join(MD, '**', '*.md'), recursive=True)):
        sz_kb = os.path.getsize(p) / 1024
        try:
            with io.open(p, encoding='utf-8', errors='ignore') as f:
                for lineno, line in enumerate(f, 1):
                    if _is_image_line(line):
                        continue          # 跳过 base64 图片行
                    scanned += len(line)
                    if word not in line:
                        continue
                    total_hits += 1
                    if hits >= limit:
                        continue      # 已够输出，但仍继续统计总数
                    snippet = re.sub(r'\s+', ' ', line.strip())
                    flag = ' [大文件 %.0fMB，勿直读]' % (sz_kb / 1024) if sz_kb > BIG_KB else ''
                    print('  %s:%d%s' % (os.path.basename(p)[:52], lineno, flag))
                    print('      %s' % snippet[:maxlen])
                    hits += 1
        except Exception:
            continue
    el = time.time() - t0
    if not total_hits:
        print('  未命中（已扫描全部文本 %.0f MB）' % (scanned / 1024 / 1024))
        print('  提示：不要扩大搜索范围，先 --map 看现成索引。')
        return 1
    print('')
    print('共命中 %d 处，显示前 %d 处（扫描 %.0f MB 全文本，耗时 %.1fs）'
          % (total_hits, hits, scanned / 1024 / 1024, el))
    print('**请用编辑器打开上述文件阅读原文，不要让 AI 直读——**')
    print('**中位数文件 397 KB ≈ 11.6 万 token。**')
    return 0


# ---------------------------------------------------------------- 体积分布
def cmd_stats():
    fs = glob.glob(os.path.join(MD, '**', '*.md'), recursive=True)
    if not fs:
        print('知识库不可达：%s' % MD)
        return 2
    szs = sorted(((os.path.getsize(p), p) for p in fs), reverse=True)
    tot = sum(s for s, _ in szs)
    print('知识库 %d 个 md / %.0f MB / 约 %.0f 万 token' % (
        len(fs), tot/1024/1024, tot/3.5/10000))
    print('')
    print('体积最大 10 个（**禁止直读**）：')
    for s, p in szs[:10]:
        print('  %-50s %7.0f KB  约 %d 万 token' % (
            os.path.basename(p)[:48], s/1024, s/3.5/10000))
    med = szs[len(szs)//2][0]
    print('')
    print('  中位数 %.0f KB ≈ %.1f 万 token —— 直读任一中位文件都很贵' % (med/1024, med/3.5/10000))
    print('  安全做法：先用 kb_lookup.py 查索引；确需原文时用编辑器人工打开。')
    return 0


# ---------------------------------------------------------------- 分章取用（禁全文检索主通道）
AM = 'asset-map.json'


def _am():
    return load(AM) or {}


def cmd_chapters():
    """全书章节资源总览（紧凑，仅计数）。"""
    d = _am()
    ch = d.get('chapters') or {}
    if not ch:
        print('asset-map.json 缺失或为空。请先跑：python scripts/build_asset_map.py')
        return 2
    c = d.get('counts') or {}
    print('# 分章资源总览（Zone A %d / Zone B %d / Zone C %d）' % (
        c.get('A', 0), c.get('B', 0), c.get('C', 0)))
    print('')
    print('> **取用方式（禁止全文检索）**：`python kb_lookup.py --chapter <节点号>`')
    print('')
    print('| 章 | 名称 | Zone A | Zone B | Zone C |')
    print('|:--|:--|--:|--:|--:|')
    for k in sorted(ch, key=lambda x: (x != '全域', x)):
        r = ch[k]
        print('| %s | %s | %d | %d | %d |' % (k, r.get('name', ''), r.get('a_count', 0),
                                              r.get('b_count', 0), r.get('c_count', 0)))
    return 0


def cmd_chapter(node, limit=12):
    """取某一节（如 9.1.2）或某一章（如 9）的资源清单——**只返回路径与元数据，不返回正文**。"""
    node = (node or '').strip()
    if not node:
        die('--chapter 需要节点号，如 9.1.2 或 7')
    d = _am()
    nodes = d.get('nodes') or {}
    ch = d.get('chapters') or {}
    if not nodes and not ch:
        print('asset-map.json 缺失。请先跑：python scripts/build_asset_map.py')
        return 2

    # ① 精确节点优先（9.1.2 命中就返回 9.1.2，不降到第 9 章）
    if node in nodes:
        rec, label, kind = nodes[node], node, '节点'
    else:
        chn = node.split('.')[0]
        if chn in ch:
            rec, label, kind = ch[chn], chn, '章'
        else:
            cands = [k for k in list(nodes) + list(ch) if node in k]
            if not cands:
                print('无节点「%s」。可用：%s' % (node, '、'.join(sorted(list(nodes))[:30])))
                return 1
            label = cands[0]
            rec = nodes.get(label) or ch[label]
            kind = '节点' if label in nodes else '章'

    print('# %s %s（%s）可用资源' % (kind, label,
                                    (ch.get(label.split('.')[0]) or {}).get('name', '')))
    print('')
    for zone, key, note in (('A', 'A', '**规范依据**（红线，可直接引条文）'),
                            ('B', 'B', '参考层（仅写作表达/论证/案例，**不得当规范要求**）'),
                            ('C', 'C', '范例层（**只取结构与表达，不取结论/参数**）')):
        items = rec.get(key) or []
        print('## Zone %s —— %s（%d 条）' % (zone, note, len(items)))
        if not items:
            print('（无）')
            print('')
            continue
        for it in items[:limit]:
            extra = []
            if it.get('n'):
                extra.append(it['n'])
            if it.get('r'):
                extra.append(it['r'])
            if it.get('s'):
                extra.append(it['s'])
            tail = ('  〔%s〕' % ' · '.join(extra)) if extra else ''
            print('- `%s`' % it.get('f', ''))
            print('    %s%s' % (it.get('t', ''), tail))
        if len(items) > limit:
            print('- … 另有 %d 条（用 --chapter %s --limit %d 展开）' % (len(items) - limit, label, len(items)))
        print('')
    print('---')
    print('**下一步**：从上面按需选 1 份文件路径打开即可；**不要再对全库做 grep 或全文检索。**')
    return 0


def main():
    ap = argparse.ArgumentParser(description='知识库安全查询入口（省 token）')
    ap.add_argument('--map', action='store_true', help='资产地图（先看这个）')
    ap.add_argument('--chapters', action='store_true', help='分章资源总览（紧凑）')
    ap.add_argument('--chapter', metavar='节点', help='取某节资源清单（如 9.1.2），不返回正文')
    ap.add_argument('--ask', metavar='项', help='按条目取结论（物种/参数/工法）')
    ap.add_argument('--ask-style', metavar='主题', help='取写法范式')
    ap.add_argument('--ask-a', nargs='?', const='', metavar='标准号', help='列 Zone A 规范')
    ap.add_argument('--ask-c', nargs='?', const='', metavar='主题', help='列 Zone C 范例')
    ap.add_argument('--find', metavar='词', help='全库查位置（**兜底手段，非首选**）')
    ap.add_argument('--limit', type=int, default=5, help='返回上限')
    ap.add_argument('--stats', action='store_true', help='知识库体积分布')
    a = ap.parse_args()

    if a.map:
        return cmd_map()
    if a.stats:
        return cmd_stats()
    if a.chapters:
        return cmd_chapters()
    if a.chapter:
        return cmd_chapter(a.chapter, a.limit if a.limit != 5 else 12)
    if a.ask:
        return cmd_ask(a.ask)
    if a.ask_style:
        return cmd_ask_style(a.ask_style)
    if a.ask_a is not None:
        return cmd_ask_a(a.ask_a or None)
    if a.ask_c is not None:
        return cmd_ask_c(a.ask_c or None)
    if a.find:
        return cmd_find(a.find, a.limit)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

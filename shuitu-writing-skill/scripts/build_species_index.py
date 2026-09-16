#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""构建乡土树草种知识库（资产 D）。

## 为什么需要

模板对植物措施的要求密集且具体：

| 节点 | 模板原文要求 |
|:---|:---|
| **2.7.6 植被** | 「当地主要**乡土树草种及生长情况**」 |
| 2.3 工程占地 | 「按地块明确植被覆盖度及**主要树草种类型**」 |
| 3.7 主体工程评价 | 「**植物配置**」分析 |
| 7.x 植物措施 | 树草种选择、配置方式、工程量 |

这些都需要**物种级知识**——某树/草种在本区的生态习性、适生条件、水土保持功能。
库内专著确实有，但散落在数万字正文里，写作时取不到。

## 提取策略：只解析**条目化**语料

实测教训（重要）：
起初按"物种名附近窗口"提取，结果**张冠李戴**——早熟禾的「适生条件」抽到了
隔壁榕树的描述；「生活型」抽到"适宜环境 生长 速度 根系 分布"这类表格残渣。
**这类错误比留空更危险**：下游会当作可靠信息使用。

因此改为**只解析结构化的条目式语料**，即知识库中这种固定格式：

    4）狗牙根[Cynodon dactylon L.]，禾本科狗牙根属多年生草本植物，
    又称百慕大草…喜温暖湿润气候，喜排水良好的肥沃土壤，狗牙根繁殖和
    侵占能力强…是优良的固土护坡植物…

结构固定为：`编号）中文名[学名]，科属+生活型+描述`。
按此解析，字段边界清晰，不会串台。

**提取不到就留空，绝不用常识补齐。**

## 用法

    python build_species_index.py --build    # 扫描知识库生成物种索引
    python build_species_index.py --list     # 列出已建物种
    python build_species_index.py --show 狗牙根
    python build_species_index.py --audit    # 校验出处可回溯

产出：`references/species_index.json`
"""
import argparse
import collections
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
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import check_gate as G  # noqa: E402
import kb_cache as KC  # noqa: E402

RULES = G.RULES
SI = (RULES.get('species_index', {}) or {})
from vault_paths import VAULT
OUT = os.path.join(REF, SI.get('output_file', 'species_index.json'))
SEARCH_DIRS = [
    os.path.join(VAULT, 'md库', 'Zone B - 参考层'),
    os.path.join(VAULT, 'md库', 'Zone C - 应用范例'),
    os.path.join(VAULT, 'md库', 'Zone A - 规范层'),
]
CACHE = KC.KBCache(VAULT)

# 条目式语料的正则：编号 + 中文名 + [学名] + 科属生活型 + 描述
ENTRY = re.compile(
    r'[（(]?\s*\d{1,3}\s*[）)]\s*'            # 1） 2） (3)
    r'([\u4e00-\u9fff]{2,8})'                  # 中文名
    r'\s*[\[【]\s*'                            # [ 
    r'([A-Z][a-zA-Z.\s]{4,40}?)'               # 学名（拉丁）
    r'\s*[\]】]'                               # ]
    r'([^。；]{0,400})'                        # 描述（到句号为止）
)

# 描述中切分各字段
RE_KE = re.compile(r'([\u4e00-\u9fff]{2,6}(?:科|属))')
RE_LIFE = re.compile(r'((?:常绿|落叶|多年生|一年生|一二年生|亚)?'
                     r'(?:小乔木|乔木|灌木|半灌木|亚灌木|藤本|草本)(?:植物|树种|灌木|草本)?)')
RE_HABIT = re.compile(r'((?:性喜|喜|耐|抗|适应)[^。；]{6,120})')
RE_SITE = re.compile(r'((?:适生于|最适宜|适宜(?:在|于)|生长(?:在|于)|分布(?:在|于|于我国的))[^。；]{6,120})')
RE_FUNC = re.compile(r'([^。；]{0,80}(?:优良|良好|很好|佳)[^。；]{0,60}'
                     r'(?:护坡|固土|保土|水土保持|固沙|护岸|护堤|绿化)[^。；]{0,50})')

# 垃圾串：HTML 残渣、文献引文、图表指引（不过滤正常描述）
JUNK = re.compile(
    r'<[a-z/]+|&[a-z]+;|colspan|rowspan|'
    r'适宜环境|生长\s*速度|根系\s*分布|'
    r'参考文献|学报|人民黄河|'
    r'详见表|见图\s*\d|如下表'
)

MAX_DESC = 160


def clip(s, n=MAX_DESC):
    s = re.sub(r'\s+', ' ', (s or '')).strip()
    s = s.strip('，,、。；; ')
    return s[:n]


def clean(v):
    """过滤垃圾串；返回 '' 表示不可用。"""
    if not v:
        return ''
    if JUNK.search(v):
        return ''
    return clip(v)


def iter_texts():
    """遍历知识库文本（走缓存，未变更文件不重读磁盘）。"""
    return CACHE.iter_texts(SEARCH_DIRS)


def parse_entries(text):
    """解析条目式语料，返回 {中文名: {字段}}。"""
    out = {}
    for m in ENTRY.finditer(text):
        name = m.group(1).strip()
        latin = clip(m.group(2).replace(' ', ' ').strip(), 60).rstrip('.')
        desc = m.group(3)
        if not desc or len(name) < 2:
            continue
        rec = {'学名': latin}
        ke = RE_KE.search(desc)
        if ke:
            rec['科属'] = clean(ke.group(1))
        life = RE_LIFE.search(desc)
        if life:
            rec['生活型'] = clean(life.group(1))
        habit = RE_HABIT.search(desc)
        if habit:
            rec['生态习性'] = clean(habit.group(1))
        site = RE_SITE.search(desc)
        if site:
            rec['适生条件'] = clean(site.group(1))
        func = RE_FUNC.search(desc)
        if func:
            rec['水土保持功能'] = clean(func.group(1))
        # 保留原始描述片段（供人工核对，也供写作时取上下文）
        rec['原文片段'] = clip(desc, 220)
        # 同名多条时取字段更全的
        old = out.get(name)
        if old is None or sum(1 for k in rec if rec.get(k)) > \
                sum(1 for k in old if old.get(k)):
            out[name] = rec
    return out


def build():
    files = list(iter_texts())
    print('扫描 %d 个库文件…' % len(files))
    agg = collections.OrderedDict()
    prov = collections.defaultdict(list)
    for path, text in files:
        # 先用 ENTRY 快速判断该文件是否含条目式语料
        if len(ENTRY.findall(text)) < 3:
            continue
        got = parse_entries(text)
        rel = os.path.relpath(path, VAULT)
        for name, rec in got.items():
            prov[name].append(rel)
            old = agg.get(name)
            if old is None:
                agg[name] = dict(rec)
            else:
                for k, v in rec.items():
                    if not old.get(k) and v:
                        old[k] = v
    index = collections.OrderedDict()
    for name, rec in agg.items():
        fields = {k: v for k, v in rec.items() if v}
        fields['出处'] = prov[name][:3]
        fields['库内出现文件数'] = len(prov[name])
        index[name] = fields
    return index


def save(index):
    doc = collections.OrderedDict()
    doc['purpose'] = ('乡土树草种知识库：服务模板 2.7.6「当地主要乡土树草种及生长情况」、'
                      '2.3「主要树草种类型」、第 7 章植物措施配置。')
    doc['rule'] = ('**只登记库内有出处的内容**：所有字段解析自知识库的**条目式语料**'
                   '（格式：`编号）中文名[学名]，科属+生活型+描述`），`出处`给出文件名。'
                   '提取不到的字段留空，**不得以常识补齐**——无据内容比留空更危险。')
    doc['extraction_note'] = ('只解析条目式语料，不做"名字附近窗口"抽取：'
                              '后者实测会张冠李戴（早熟禾抽到榕树的描述）。'
                              '若某物种本库未以条目形式收录，则不予登记。')
    doc['source_dirs'] = [os.path.relpath(d, VAULT) for d in SEARCH_DIRS]
    doc['count'] = len(index)
    doc['species'] = index
    io.open(OUT, 'w', encoding='utf-8').write(
        json.dumps(doc, ensure_ascii=False, indent=1) + '\n')
    return doc


def audit(doc):
    """校验每条出处可回溯（文件存在且含该物种名）。"""
    bad = 0
    for name, rec in doc['species'].items():
        if not rec.get('出处'):
            print('  ✗ %s 无出处' % name)
            bad += 1
            continue
        ok = False
        for rel in rec['出处']:
            p = os.path.join(VAULT, rel)
            if os.path.isfile(p):
                try:
                    if name in io.open(p, encoding='utf-8', errors='ignore').read():
                        ok = True
                        break
                except Exception:
                    pass
        if not ok:
            print('  ✗ %s 出处无法回溯' % name)
            bad += 1
    return bad


def main():
    ap = argparse.ArgumentParser(description='构建乡土树草种知识库（条目式语料解析）')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--show', metavar='物种')
    ap.add_argument('--audit', action='store_true')
    ap.add_argument('--force', action='store_true', help='忽略指纹，强制重建')
    a = ap.parse_args()

    if a.build:
        # 指纹短路：知识库未变更且产物已存在 → 直接跳过整个构建
        unchanged, fp, nf = CACHE.fingerprint_unchanged('species', SEARCH_DIRS)
        if unchanged and os.path.exists(OUT) and not a.force:
            print('知识库未变更（%d 文件），跳过重建 → %s' % (nf, OUT))
            print('  （如需强制重建加 --force）')
            return 0
        t0 = time.time()
        idx = build()
        doc = save(idx)
        CACHE.save_texts()
        CACHE.save_fingerprint('species', fp, nf)
        st = CACHE.report()
        print('已生成 %s（%d 个物种，耗时 %.1fs）' % (OUT, len(idx), time.time() - t0))
        if st['hit']:
            print('  文件缓存命中 %d 次，省去重复读取 %.1f MB'
                  % (st['hit'], st['bytes_saved'] / 1024 / 1024))
        print()
        print('%-10s %-16s %-10s %s' % ('物种', '科属', '生活型', '生态习性'))
        for name, rec in idx.items():
            print('%-10s %-16s %-10s %s' % (
                name, (rec.get('科属') or '-')[:14], (rec.get('生活型') or '-')[:8],
                (rec.get('生态习性') or '-')[:40]))
        print()
        bad = audit(doc)
        print('出处自检：%d 个物种，无法回溯 %d 个 %s'
              % (len(idx), bad, '✅' if bad == 0 else '❌'))
        return 0 if bad == 0 else 1

    if a.list:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        for name, rec in doc['species'].items():
            print('  %-10s %-16s %s' % (name, rec.get('科属', ''), rec.get('生活型', '')))
        return 0

    if a.show:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        rec = doc['species'].get(a.show)
        if not rec:
            print('未收录：%s' % a.show)
            return 1
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0

    if a.audit:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        bad = audit(doc)
        print('出处自检：%d 个物种，无法回溯 %d 个 %s'
              % (len(doc['species']), bad, '✅' if bad == 0 else '❌'))
        return 0 if bad == 0 else 1

    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

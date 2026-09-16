#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""构建设计参数口径表（资产 E）：从 Zone A 标准正文提取阈值，供写作时按条引据。

## 为什么需要

模板多处要求"按标准定级/取值"，例如：

| 节点 | 模板要求 |
|:---|:---|
| 5.2 弃渣场选址、堆置方案与级别 | 「逐一确定弃渣场级别」 |
| 7.6 工程级别与设计标准 | 「弃渣场级别 / 拦渣工程级别与防洪标准 / 排洪工程级别与防洪标准 / 植被恢复与建设工程级别 / 截排水工程设计标准」 |

这些**必须按标准表定级**，不能凭经验。阈值原本只存在于 Zone A 的 PDF/md 正文里，
写在表格中（`<table>` 段落），写作时难以取用。

本脚本把 GB 51018 等标准中**与定级相关的表格**提取成结构化参数表。

## 铁律：只录 Zone A，且逐字附条号

- **只从 Zone A 标准提取**（Zone B/C 不得作为参数来源）
- 每条必须带 `source`（标准名 + 表号/条号），可回溯
- 提取不到的留空，**不得以经验值补齐**

## 用法

    python build_design_params.py --build
    python build_design_params.py --list
    python build_design_params.py --show 弃渣场级别
    python build_design_params.py --audit

产出：`references/design_params.json`
"""
import argparse
import collections
import glob
import html
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
DP = (RULES.get('design_params', {}) or {})
from vault_paths import VAULT
ZONE_A = os.path.join(VAULT, 'md库', 'Zone A - 规范层')
OUT = os.path.join(REF, DP.get('output_file', 'design_params.json'))
CACHE = KC.KBCache(VAULT)
SCAN_DIRS = [ZONE_A]

TABLE = re.compile(r'<table>(.*?)</table>', re.S)
ROW = re.compile(r'<tr>(.*?)</tr>', re.S)
CELL = re.compile(r'<td[^>]*>(.*?)</td>', re.S)

# 要提取的目标表：以"表头关键词"识别，避免依赖表号（各标准编号不同）
TARGETS = [
    {
        'key': '弃渣场级别',
        'std': 'GB 51018-2014',
        'head_kw': ('渣场级别', '堆渣量'),
        'clause': 'GB 51018-2014 §5.7.1 表5.7.1',
        'note': '按堆渣量、最大堆渣高度、失事危害程度三者**任一最高**确定级别',
        'serve': ['5.2', '7.6'],
    },
    {
        'key': '拦渣与排洪工程级别',
        'std': 'GB 51018-2014',
        'head_kw': ('渣场级别', '拦渣工程', '排洪工程'),
        'clause': 'GB 51018-2014 §5.7.2 表5.7.2',
        'note': '由弃渣场级别对应拦渣堤/坝、挡渣墙、排洪工程的工程级别',
        'serve': ['7.6'],
    },
    {
        'key': '拦渣与排洪工程防洪标准',
        'std': 'GB 51018-2014',
        # 用"拦渣堤(坝)工程级别 + 排洪工程级别 + 重现期"三者共同定位，
        # 避免误匹配到其它含"防洪标准"的表（实测曾错配到 表5.7.3 之外的块）
        'head_kw': ('拦渣堤', '排洪工程级别', '重现期'),
        'clause': 'GB 51018-2014 §5.7.3 表5.7.3',
        'note': '按工程级别取设计/校核重现期；分山区丘陵区与平原滨海区',
        'serve': ['5.2', '7.6'],
    },
    {
        'key': '坡面截排水工程设计标准',
        'std': 'GB 51018-2014',
        'head_kw': ('排水标准', '超高'),
        'clause': 'GB 51018-2014 §5.6.2 表5.6.2',
        'note': '按工程级别取排水标准与超高；服务 7.6「截排水工程设计标准」',
        'serve': ['7.6'],
    },
    {
        'key': '植被恢复与建设工程级别（水利水电）',
        'std': 'GB 51018-2014',
        # 该表表头为「主要建筑物级别 | 生活管理区 | 枢纽闸站永久占地区 | 堤渠永久占地区」，
        # **不含"植被恢复"字样**——关键词匹配不到。故改用「表号前置定位」：
        # caption 为 `表 5.11.3-1 水利水电项目植被恢复与建设工程级别`。
        'caption_kw': '5.11.3-1',
        'head_kw': ('主要建筑物级别',),
        'clause': 'GB 51018-2014 §5.11.3 表5.11.3-1',
        'note': '服务 7.6「植被恢复与建设工程级别」。生产建设项目按行业分 7 张表，'
                '本表为水利水电（矿山项目应查对应行业表）。'
                '§5.11.3 规定：涉及城镇、饮水水源保护区、风景名胜区应提高一级；'
                '弃渣取料、施工生产生活、施工交通等临时占地区域执行 3 级标准',
        'serve': ['7.6'],
    },
]

# 第二来源：GB/T 45107-2024（表土）。模板 4.1.2 明确要求
# 「表土质量可参照《表土剥离及其再利用技术要求》（GB/T45107-2024）进行评价」。
TARGETS_B = [
    {
        'key': '表土质量评价单项指标',
        'std': 'GB/T 45107-2024',
        'head_kw': ('评价指标', '分级要求'),
        'clause': 'GB/T 45107-2024 附录B B.1 表B.1',
        'note': '服务 4.1.2「表土资源评价」。按 pH、含盐量、有机质、质地等单项指标分级，'
                '1~4 级；耕地园地与其他土地利用类型检测方法分列',
        'serve': ['4.1.2', '4.2.1', '4.4.1'],
    },
    {
        'key': '表土质量等级划分与再利用',
        'std': 'GB/T 45107-2024',
        'head_kw': ('评价因子', 'Ⅰ类表土'),
        'clause': 'GB/T 45107-2024 附录B B.2 表B.2',
        'note': '服务 4.1.2「表土资源评价」与 4.4.3「表土再利用」。'
                '按各评价因子组合，把表土划为 Ⅰ/Ⅱ/Ⅲ 三类，对应不同再利用方向',
        'serve': ['4.1.2', '4.4.3'],
    },
]

# 第三来源：GB/T 50434-2018（防治标准等级判定）。模板 7.3.1 要求
# 「项目水土流失防治标准执行等级」与「分区分段确定」，4.0.1 给出直接判据。
CLAUSE_TARGETS = [
    {
        'key': '防治标准等级判定规则',
        'std': 'GB/T 50434-2018',
        'anchor': '4.0.1 生产建设项目水土流失防治标准等级',
        'clause': 'GB/T 50434-2018 §4.0.1',
        'note': '服务 7.3.1「执行标准等级」。一级：位于重点预防区/重点治理区、'
                '饮用水水源保护区、自然保护区、风景名胜区等敏感区且不能避让，'
                '或位于县级及以上城市区域；二级：位于湖泊水库周边、四级以上河道'
                '两岸 3km 汇流范围内，或周边 500m 内有乡镇居民点，且不属一级区；'
                '三级：一二级以外区域。§4.0.4 等级分一/二/三级；'
                '§3.0.3 建设生产类项目按施工期、设计水平年、生产期三个时段分别确定',
        'serve': ['7.3.1', '1.6.1', '7.3.2', '附表'],
    },
]


def norm_cell(s):
    s = re.sub(r'<[^>]+>', '', s)
    s = html.unescape(s)
    s = s.replace('&gt;', '>').replace('&lt;', '<').replace('&ge;', '≥').replace('&le;', '≤')
    return re.sub(r'\s+', ' ', s).strip()


def parse_tables(text):
    """返回 [(header_rows, body_rows)]。

    两个源文件特性必须处理（实测教训）：

    ① **同一张表被拆成多个 `<table>` 块**（PDF 转 md 时按页切割）——
       如 GB 51018 表 5.7.1 被切成「级别 1~3」与「4~5」两块。
       若不合并，会漏掉后半张表的数据。

    ② **表头可能是多行**（合并单元格展开后，表头占用 2~3 个 `<tr>`）——
       如"防洪标准"表的表头是「工程级别 | 防洪标准[重现期(年)]」+「设计 | 校核」两行。
       若只取第一个 `<tr>` 当表头，正文行会被误当表头，数据错位。

    判定：**首个不含数字的行组为表头**，其余为数据。
    """
    raw = []
    for m in TABLE.finditer(text):
        rows = []
        for r in ROW.finditer(m.group(1)):
            cells = [norm_cell(c) for c in CELL.findall(r.group(1))]
            if any(c for c in cells):
                rows.append(cells)
        if len(rows) >= 2:
            raw.append(rows)

    parsed = []
    for rows in raw:
        # 表头 = 开头连续"不含纯数字单元格"的行
        h = 0
        while h < len(rows) and not any(re.fullmatch(r'\d+(?:\.\d+)?', c or '') for c in rows[h]):
            h += 1
        if h == 0:
            h = 1                      # 兜底：至少留一行当表头
        head, body = rows[:h], rows[h:]
        if body:
            parsed.append((head, body))

    # 同表头合并（处理 ①）
    merged = []
    for head, body in parsed:
        key = tuple(tuple(r) for r in head)
        for mh, mbody in merged:
            if tuple(tuple(r) for r in mh) == key:
                mbody.extend(body)
                break
        else:
            merged.append((head, list(body)))
    return merged


def find_table_by_caption(text, caption_kw):
    """按**表号（caption）**定位表：找到 `表 5.11.3-1` 之后的第一个 <table>。

    为什么需要这条路径：部分表的**表头不含业务关键词**——
    例如「表 5.11.3-1 水利水电项目植被恢复与建设工程级别」的表头是
    「主要建筑物级别 | 生活管理区 | 枢纽闸站永久占地区 | 堤渠永久占地区」，
    靠"植被恢复"匹配表头永远匹配不到。改用表号定位即可。
    """
    for m in re.finditer(re.escape(caption_kw), text):
        # 从 caption 往后找第一个 <table>
        tm = TABLE.search(text, m.end())
        if not tm:
            continue
        # 限制在 caption 后 1500 字符内，避免跳到很远的表
        if tm.start() - m.end() > 1500:
            continue
        rows = []
        for r in ROW.finditer(tm.group(1)):
            cells = [norm_cell(c) for c in CELL.findall(r.group(1))]
            if any(c for c in cells):
                rows.append(cells)
        if len(rows) >= 2:
            h = 0
            while h < len(rows) and not any(
                    re.fullmatch(r'\d+(?:\.\d+)?', c or '') for c in rows[h]):
                h += 1
            if h == 0:
                h = 1
            head, body = rows[:h], rows[h:]
            if body:
                return head, body
    return None


def find_zone_a_file(std_kw, exclude=('条文说明', '编制说明')):
    """按标准号找 Zone A 文件。

    两点注意（均为实测教训）：
      ① 文件名里标准号写法不统一（`GB 51018-2014_...`、`GB_T 45107-2024_...`），
         故把两者都归一化（去空格与 `/`、`_`、`-`），只留字母数字再包含判断。
      ② **必须排除"条文说明"版**——同一标准常同时入库正文与条文说明，
         条文说明是解释性文字、不含标准表格，命中它会导致"未匹配到表"。
    """
    def norm(s):
        return re.sub(r'[^0-9A-Za-z]', '', s).upper()

    target = norm(std_kw)
    cands = [p for p in glob.glob(os.path.join(ZONE_A, '**', '*.md'), recursive=True)
             if target and target in norm(os.path.basename(p))]
    for p in cands:
        if any(x in os.path.basename(p) for x in exclude):
            continue
        return p
    return cands[0] if cands else None


def extract_clause_params():
    """抽取**条款式**（非表格）的参数判定规则。

    为什么需要：部分"参数"其实是**判定规则**（如"什么情况执行一级标准"），
    以编号条款形式给出，不是表格。按表格解析会全部遗漏——
    这正是 7.3.1「执行标准等级」长期只能靠人判的原因。
    """
    out = collections.OrderedDict()
    for t in CLAUSE_TARGETS:
        p = find_zone_a_file(t['std'])
        if not p:
            print('  ! 未找到标准：%s' % t['std'])
            continue
        text = CACHE.read(p)
        i = text.find(t['anchor'])
        if i < 0:
            i = text.find(t['anchor'].replace(' ', ''))
        if i < 0:
            print('  ! 未定位到条款：%s' % t['key'])
            continue
        seg = re.sub(r'\s+', ' ', text[i:i + 1200])
        sents = [s.strip() for s in re.split(r'(?<=[。；])', seg) if s.strip()]
        picked, seen = [], set()
        for s in sents:
            if not re.search(r'(应|宜|不得|分为|包括|确定)', s):
                continue
            k = s[:24]
            if k in seen:
                continue
            seen.add(k)
            picked.append(s[:260])
            if len(picked) >= 8:
                break
        if picked:
            out[t['key']] = collections.OrderedDict([
                ('依据', t['clause']),
                ('来源文件', os.path.relpath(p, VAULT)),
                ('说明', t['note']),
                ('表头', []),
                ('数据', [[s] for s in picked]),   # 条款式：一句一行
                ('服务节点', t['serve']),
            ])
    return out


def build():
    params = collections.OrderedDict()
    for t in TARGETS + TARGETS_B:
        p = find_zone_a_file(t['std'])
        if not p:
            print('  ! 未找到标准文件：%s' % t['std'])
            continue
        text = CACHE.read(p)
        picked = None
        # 路径①：按表号（caption）定位 —— 用于表头不含业务关键词的表
        if t.get('caption_kw'):
            picked = find_table_by_caption(text, t['caption_kw'])
        # 路径②：按表头关键词定位（默认）
        if not picked:
            for head, rows in parse_tables(text):
                joined = ' '.join(' '.join(r) for r in head)
                if all(k in joined for k in t['head_kw']):
                    picked = (head, rows)
                    break
        if not picked:
            print('  ! 未匹配到表：%s（%s）' % (t['key'], ' '.join(t['head_kw'])))
            continue
        head, rows = picked
        params[t['key']] = collections.OrderedDict([
            ('依据', t['clause']),
            ('来源文件', os.path.relpath(p, VAULT)),
            ('说明', t['note']),
            ('表头', [' | '.join(r) for r in head]),   # 多行表头逐行列出
            ('数据', rows),
            ('服务节点', t['serve']),
        ])
    # 条款式判定规则（非表格）
    for k, v in extract_clause_params().items():
        params[k] = v
    return params


def save(params):
    doc = collections.OrderedDict()
    doc['purpose'] = ('设计参数口径表：模板要求"按标准定级/取值"的阈值，'
                      '如弃渣场级别、拦渣与排洪工程级别及防洪标准。')
    doc['rule'] = ('**只从 Zone A 标准提取**（Zone B/C 不得作为参数来源）；'
                   '每条附 `依据`（标准名+表号/条号）与 `来源文件`，可回溯。'
                   '提取不到的留空，**不得以经验值补齐**。')
    doc['source_dir'] = os.path.relpath(ZONE_A, VAULT)
    doc['count'] = len(params)
    doc['params'] = params
    io.open(OUT, 'w', encoding='utf-8').write(
        json.dumps(doc, ensure_ascii=False, indent=1) + '\n')
    return doc


def audit(doc):
    bad = 0
    for k, v in doc['params'].items():
        if not v.get('依据') or not v.get('来源文件'):
            print('  ✗ %s 缺依据或来源' % k)
            bad += 1
            continue
        p = os.path.join(VAULT, v['来源文件'])
        if not os.path.isfile(p):
            print('  ✗ %s 来源文件不存在：%s' % (k, v['来源文件']))
            bad += 1
    return bad


def main():
    ap = argparse.ArgumentParser(description='构建设计参数口径表（资产 E）')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--show', metavar='参数')
    ap.add_argument('--audit', action='store_true')
    ap.add_argument('--force', action='store_true', help='忽略指纹，强制重建')
    a = ap.parse_args()

    if a.build:
        unchanged, fp, nf = CACHE.fingerprint_unchanged('design_params', SCAN_DIRS)
        if unchanged and os.path.exists(OUT) and not a.force:
            print('知识库未变更（%d 文件），跳过重建 → %s' % (nf, OUT))
            print('  （如需强制重建加 --force）')
            return 0
        t0 = time.time()
        params = build()
        doc = save(params)
        CACHE.save_texts()
        CACHE.save_fingerprint('design_params', fp, nf)
        print('已生成 %s（%d 组参数，耗时 %.1fs）' % (OUT, len(params), time.time() - t0))
        for k, v in params.items():
            print('  [%s] %s  数据 %d 行' % (v['依据'], k, len(v['数据'])))
        print()
        bad = audit(doc)
        print('依据自检：%d 组，无法回溯 %d 组 %s'
              % (len(params), bad, '✅' if bad == 0 else '❌'))
        return 0 if bad == 0 else 1

    if a.list:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        for k, v in doc['params'].items():
            print('  %-22s %s' % (k, v.get('依据', '')))
        return 0

    if a.show:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        v = doc['params'].get(a.show)
        if not v:
            print('未收录：%s' % a.show)
            return 1
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return 0

    if a.audit:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        bad = audit(doc)
        print('依据自检：%d 组，无法回溯 %d 组 %s'
              % (len(doc['params']), bad, '✅' if bad == 0 else '❌'))
        return 0 if bad == 0 else 1

    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

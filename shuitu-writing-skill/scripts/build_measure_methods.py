#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""构建措施工法索引（资产 F）：把"某措施怎么做"从标准与已批方案中提炼成结构化条目。

## 为什么需要

模板多处要求给出措施的**做法与规格**，而不是只写措施名称：

| 节点 | 模板要求 |
|:---|:---|
| **7.7 分区措施布设** | 「选取典型措施或典型地段，进行**典型设计**」 |
| 5.2 弃渣场 | 「堆置方式、台阶高度、平台宽度、堆渣坡比、综合坡度」 |
| 7.6 工程级别 | 「截排水工程设计标准」 |
| 9.1.2 投资估算 | 按措施类型计列工程量 |

现有 Zone A 标准（GB 51018 第 5 章「措施设计要求」）规定了各类措施的设计要求，
Zone C 的 91 份已批方案给出了**实际做法与典型断面**。二者结合才能写出"典型设计"。

## 来源与优先级

```
① Zone A 标准条文  —— 设计要求（必须满足，硬约束）
② Zone C 已批方案  —— 实际做法与断面（参考，不得直接套用）
```

**纪律**：
- Zone A 条文**逐字引**，附条号
- Zone C 只取"做法/断面/计量口径"，**不取数字**（数字属项目数据，严禁引入）
- 无出处不登记

## 用法

    python build_measure_methods.py --build
    python build_measure_methods.py --list
    python build_measure_methods.py --show 截水沟
    python build_measure_methods.py --audit

产出：`references/measure_methods.json`
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
MM = (RULES.get('measure_methods', {}) or {})
from vault_paths import VAULT
ZONE_A = os.path.join(VAULT, 'md库', 'Zone A - 规范层')
OUT = os.path.join(REF, MM.get('output_file', 'measure_methods.json'))
CACHE = KC.KBCache(VAULT)
SCAN_DIRS = [ZONE_A]

# 措施 → 在 Zone A 标准里对应的节（GB 51018 第 5 章「措施设计要求」）
# 每项须声明 `serve`：该措施服务质量哪些模板节点，否则注入时会当作"通用"全量下发。
MEASURES = collections.OrderedDict([
    ('截水沟', {'kw': ('截水沟', '截流沟'), 'section': '5.6', 'type': '工程措施',
                'serve': ['7.7', '7.6']}),
    ('排水沟', {'kw': ('排水沟', '边沟'), 'section': '5.6', 'type': '工程措施',
                'serve': ['7.7', '7.6']}),
    ('沉沙池', {'kw': ('沉沙池', '沉砂池'), 'section': '5.6', 'type': '工程措施',
                'serve': ['7.7']}),
    ('急流槽与跌水', {'kw': ('急流槽', '跌水'), 'section': '5.6', 'type': '工程措施',
                      'serve': ['7.7']}),
    ('拦渣坝', {'kw': ('拦渣坝', '拦挡坝'), 'section': '5.7', 'type': '工程措施',
                'serve': ['5.2', '7.7']}),
    ('挡渣墙', {'kw': ('挡渣墙', '挡土墙'), 'section': '5.7', 'type': '工程措施',
                'serve': ['5.2', '7.7']}),
    ('拦渣堤', {'kw': ('拦渣堤',), 'section': '5.7', 'type': '工程措施',
                'serve': ['5.2', '7.7']}),
    ('土地整治', {'kw': ('土地整治',), 'section': '5.8', 'type': '工程措施',
                  'serve': ['7.7']}),
    ('表土剥离与保护', {'kw': ('表土剥离', '表土保护', '剥离表土'), 'section': None,
                        'type': '工程措施', 'serve': ['4.2.1', '4.4.1', '7.7']}),
    ('林草工程', {'kw': ('林草', '植被恢复', '种草', '植树'), 'section': '5.11',
                  'type': '植物措施', 'serve': ['7.7', '2.7.6']}),
    ('封育工程', {'kw': ('封育', '封禁'), 'section': '5.12', 'type': '植物措施',
                  'serve': ['7.7']}),
    ('临时防护', {'kw': ('临时防护', '苫盖', '临时拦挡'), 'section': None,
                  'type': '临时措施', 'serve': ['7.7']}),
])

SENT = re.compile(r'[^。；]{6,220}[。；]')
# HTML 残渣与目录页残句：出现即整句丢弃
NOISE = re.compile(r'rowspan|colspan|<t[dhr]|</t|&[a-z]+;|'
                   r'本规范共\d+章|主要技术内容包括|目\s*次|'
                   r'^\s*\d+(?:\.\d+)*\s*$')


def zone_a_text():
    """读 GB 51018 全文（措施设计依据）。"""
    for p in glob.glob(os.path.join(ZONE_A, '**', '*.md'), recursive=True):
        if '51018' in os.path.basename(p):
            return p, CACHE.read(p)
    return None, ''


def zone_a_by_kw(kw, exclude=('条文说明', '编制说明')):
    """按标准号关键词取 Zone A 文件（归一化匹配，容忍 GB_T / GB/T 写法差异）。

    两个要点：
      ① **排除"条文说明"版**——同一标准的条文说明与正文同时入库，
         关键词会命中条文说明（内容是解释性文字，不是标准条款）。
         实测：查 50433 命中「条文说明」，导致 §5.2 等章节全部定位失败。
      ② 返回**去掉 frontmatter 的正文**，否则会抽出元数据垃圾。
    """
    def norm(s):
        return re.sub(r'[^0-9A-Za-z]', '', s).upper()
    t = norm(kw)
    for p in glob.glob(os.path.join(ZONE_A, '**', '*.md'), recursive=True):
        base = os.path.basename(p)
        if not (t and t in norm(base)):
            continue
        if any(x in base for x in exclude):
            continue
        text = CACHE.read(p)
        return p, strip_frontmatter(text)
    # 全部都是条文说明版时，退回用它（总比没有好）
    for p in glob.glob(os.path.join(ZONE_A, '**', '*.md'), recursive=True):
        if t and t in norm(os.path.basename(p)):
            text = CACHE.read(p)
            return p, strip_frontmatter(text)
    return None, ''


def strip_frontmatter(text):
    """去掉 YAML frontmatter（--- 之间的元数据块）。"""
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end > 0:
            nl = text.find('\n', end + 1)
            return text[nl + 1:] if nl > 0 else ''
    return text


# 监测类要求：模板 8.x 节点（范围/内容/方法频次/点位布设）的硬性规定。
# 这些是**条款式**要求（"应至少布设 1 个监测点"），不是表格，
# 因此按"条款号 + 关键词"逐条抽取，而非解析表格。
MONITOR_RULES = [
    {'key': '监测点数量要求', 'std': '51240', 'clause': 'GB/T 51240-2018 §7.1.2',
     'anchor': '7.1.2 监测点数量',
     'serve': ['8.2', '8.3']},
    {'key': '植物措施监测点布设', 'std': '51240', 'clause': 'GB/T 51240-2018 §7.2.1～7.2.2',
     'anchor': '7.2 植物措施监测点布设',
     'serve': ['8.2', '8.3']},
    {'key': '工程措施监测点布设', 'std': '51240', 'clause': 'GB/T 51240-2018 §7.3.1～7.3.3',
     'anchor': '7.3工程措施监测点布设',
     'serve': ['8.2', '8.3']},
    {'key': '土壤流失量监测点布设', 'std': '51240', 'clause': 'GB/T 51240-2018 §7.4.1～7.4.2',
     'anchor': '7.4 土壤流失量监测点布设',
     'serve': ['8.2', '8.3']},
    {'key': '监测点布设原则', 'std': '51240', 'clause': 'GB/T 51240-2018 §7.1.1',
     'anchor': '7.1.1',
     'serve': ['8.3']},
    # 监理（服务第 10 章「水土保持监理」）
    # 注：各标准的条款编号形态不一（SL/T 523 用 2.0.1、GB/T 15776 用 3.1.3），
    # 因此 anchor 一律取**标准正文里确实存在的小节标题**，而非猜测的条款号。
    {'key': '水土保持监理要求', 'std': '523', 'clause': 'SL/T 523-2024',
     'anchor': '监理工作', 'serve': ['10']},
    # 土壤流失量测算方法（服务 6.3「预测方法」）
    {'key': '土壤流失量测算方法', 'std': '773', 'clause': 'SL 773-2018',
     'anchor': '土壤流失量', 'serve': ['6.3']},
]

# 追加来源：其他尚未挖掘的标准（各自服务特定节点）
EXTRA_SOURCES = [
    {'key': '造林技术规程要求', 'std': '15776', 'clause': 'GB/T 15776-2023',
     'anchor': '造林作业设计', 'serve': ['7.7']},
    {'key': '矿山生态修复技术要求（通则）', 'std': '1070.1', 'clause': 'TD/T 1070.1-2022',
     'anchor': '总体原则', 'serve': ['7.7', '3.1']},
    {'key': '水土保持工程调查与勘测要求', 'std': '51297', 'clause': 'GB/T 51297-2018',
     'anchor': '基本规定', 'serve': ['2.7', '4.1.1']},
    # 本轮查漏补缺新增：此前未被任何资产挖掘的高价值标准。
    # anchor 一律取**实测存在的章节标题**（不是猜测的泛词）——
    # 泛词（如"验收""监测"）会命中目录/参考文献/附录，抽出垃圾。
    {'key': '土壤流失量测算流程（SL773）', 'std': '773', 'clause': 'SL 773-2018 §5、§6～§10',
     'anchor': '5生产建设项目土壤流失量测算流程', 'serve': ['6.3']},
    {'key': '工程堆积体土壤流失量测算（SL773）', 'std': '773',
     'clause': 'SL 773-2018 §8（工程堆积体）',
     'anchor': '8.2上方无来水工程堆积体土壤流失量测算', 'serve': ['6.3']},
    {'key': '防洪标准基本规定（GB50201）', 'std': '50201', 'clause': 'GB 50201-2014 §3',
     'anchor': '3基本规定', 'serve': ['7.6']},
    {'key': '工矿企业防洪标准（GB50201）', 'std': '50201', 'clause': 'GB 50201-2014 §5',
     'anchor': '5工矿企业', 'serve': ['7.6']},
    {'key': '矿山生态修复子项目验收（TD/T1092）', 'std': '1092',
     'clause': 'TD/T 1092-2024 §5',
     'anchor': '5.1子项目验收条件与依据', 'serve': ['10']},
]

# GB 50433-2018 是**生产建设项目水土保持的主标准**，其 §5 逐类给出措施设计要求
# （5.2 表土保护 / 5.3 拦渣 / 5.4 边坡防护 / 5.5 截排水 / 5.7 土地整治 /
#   5.8 植物 / 5.9 临时防护）。此前只挖了 GB 51018（综合治理向），
# 漏掉这部最对口的措施设计依据 —— 这是本轮查漏补缺的关键发现。
GB50433_MEASURES = [
    {'key': '表土保护措施要求（GB50433）', 'anchor': '5.2 表土保护措施', 'serve': ['4.2.1', '4.4.2', '7.7']},
    {'key': '拦渣措施要求（GB50433）', 'anchor': '5.3 拦渣措施', 'serve': ['5.2', '7.7']},
    {'key': '边坡防护措施要求（GB50433）', 'anchor': '5.4 边坡防护措施', 'serve': ['7.7']},
    {'key': '截排水措施要求（GB50433）', 'anchor': '5.5 截排水措施', 'serve': ['7.7', '7.6']},
    {'key': '土地整治措施要求（GB50433）', 'anchor': '5.7 土地整治措施', 'serve': ['7.7']},
    {'key': '植物措施要求（GB50433）', 'anchor': '5.8 植物措施', 'serve': ['7.7', '2.7.6']},
    {'key': '临时防护措施要求（GB50433）', 'anchor': '5.9 临时防护措施', 'serve': ['7.7']},
]


def pick_sentences(text, kws, limit=6):
    """挑出含关键词的规范条文句。

    两条过滤（实测教训）：
      ① 剔除 HTML 残渣句（PDF 转 md 产生的 `rowspan=1 colspan=1>` 等）
      ② 要求句子**以该措施为主语或直接宾语**——即关键词须出现在句首 60 字内，
         否则会把"梯田与林草工程相配套"这类泛述当成林草工程的设计要求。
    """
    out, seen = [], set()
    for m in SENT.finditer(text):
        s = re.sub(r'\s+', ' ', m.group(0)).strip()
        if NOISE.search(s):
            continue
        pos = min((s.find(k) for k in kws if k in s), default=-1)
        if pos < 0:
            continue
        # 关键词须出现在句首 60 字内，且句子须具"设计要求"性质
        if pos > 60:
            continue
        if not re.search(r'(应|宜|不应|不宜|不得|要求|设计|布设|设置|断面|尺寸|规定)', s):
            continue
        key = s[:30]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def is_toc_context(text, pos, span=160):
    """判断 pos 处是否处于**目录区**。

    判据（任一成立即为目录）：
      · 后随片段含目录点线「……」或连续 4 个以上「.」
      · 后随片段含多个「标题……页码」形态

    为什么需要：标准 md 开头都有「目 次」，章节标题在目录里**先出现一次**。
    从那里向后截取只会得到一串目录条目（实测 TD/T 1092、GB 50201 均如此）。
    """
    tail = text[pos:pos + span]
    if '……' in tail:
        return True
    if re.search(r'\.{4,}', tail):
        return True
    # 「标题… 数字」成对出现多次 → 目录
    if len(re.findall(r'…{2,}\s*\d', tail)) >= 2:
        return True
    return False


def anchor_regex(anchor):
    """把锚点编译成**容忍空白差异**的正则。

    为什么必须容忍：标准 md 的章节标题写法极不统一——实测同一批文件里就有
      `5.2 表土保护措施`（单个空格）
      `5.8植物措施`（无空格）
      `5        生产建设项目土壤流失量测算流程`（多个空格）
    按字面匹配会大量失败（实测 SL773、GB50201 的锚点出现 0 次）。
    """
    parts = [re.escape(ch) for ch in anchor if not ch.isspace()]
    return re.compile(r'\s*'.join(parts))


def resolve_anchor(text, anchor):
    """定位章节正文起点：跳过目录、跳过交叉引用、容忍空白差异。

    顺序：
      ① 后跟同号小节（`5.8植物措施` → `5.8.1`）且不在目录区 → 正文起点
      ② 不在目录区的首次出现
      ③ 首次出现（兜底）
    返回 -1 表示未找到。
    """
    sec_m = re.match(r'(\d+(?:\.\d+)*)', anchor)
    sec = sec_m.group(1) if sec_m else ''
    pat = anchor_regex(anchor)

    if sec:
        for m in pat.finditer(text):
            if is_toc_context(text, m.start()):
                continue
            if re.search(r'[\s#*]*' + re.escape(sec) + r'\.\d',
                         text[m.end():m.end() + 120]):
                return m.start()
    for m in pat.finditer(text):
        if not is_toc_context(text, m.start()):
            return m.start()
    m = pat.search(text)
    return m.start() if m else -1


def extract_monitor_rules():

    """抽取监测类**条款式**要求（非表格）。

    做法：以 `anchor`（条款号或小节标题）定位，向后截取一段固定长度，
    按句切分后保留含"应/宜/不得/至少/不低于"等规定性表述的条款句。

    为什么不用表格解析：这些要求是**编号条款**（如"7.1.2 …1 植物措施监测点
    数量…应至少布设1个监测点"），不是表格，按表格解析会全部遗漏——
    这正是 8.2「监测点位数量与频次」长期标记"规则待逐字提取"的原因。
    """
    out = collections.OrderedDict()
    for rule in MONITOR_RULES + EXTRA_SOURCES:
        p, text = zone_a_by_kw(rule['std'])
        if not text:
            continue
        # 定位 anchor：统一走 resolve_anchor（跳过目录、跳过交叉引用）
        i = resolve_anchor(text, rule['anchor'])
        if i < 0:
            print('  ! 未定位到条款：%s（%s）' % (rule['key'], rule['anchor']))
            continue
        seg = re.sub(r'\s+', ' ', text[i:i + 1500])
        sents = [s.strip() for s in re.split(r'(?<=[。；])', seg) if s.strip()]
        picked, seen = [], set()
        for s in sents:
            if NOISE.search(s):
                continue
            if not re.search(r'(应|宜|不应|不得|至少|不低于|不少于|按照|规定)', s):
                continue
            k = s[:24]
            if k in seen:
                continue
            seen.add(k)
            picked.append(s[:220])
            if len(picked) >= 8:
                break
        if picked:
            out[rule['key']] = collections.OrderedDict([
                ('类别', '监测要求'),
                ('依据', rule['clause']),
                ('来源文件', os.path.relpath(p, VAULT)),
                ('Zone A 设计要求', picked),
                ('服务节点', rule['serve']),
            ])
    # ③ GB 50433 措施设计要求（主标准，按 §5.x 逐类）
    p, text = zone_a_by_kw('50433')
    if not p:
        # 排除条文说明版，取主标准
        for cand in glob.glob(os.path.join(ZONE_A, '**', '*50433*.md'), recursive=True):
            if '条文说明' not in os.path.basename(cand):
                p = cand
                text = strip_frontmatter(CACHE.read(cand))
                break
    if text:
        for m in GB50433_MEASURES:
            # 定位章节标题：不能用 rfind——文末常有「应符合本标准 5.8 条的规定」
            # 这类**交叉引用**，rfind 会命中它而非真正的章节。
            # 正确做法：找第一个**后跟小节号**的出现（如 `5.8植物措施` 后接 `5.8.1`），
            # 该位置才是章节正文起点。
            # 正文里的章节标题写法**不统一**：有的带空格（`5.2 表土保护措施`），
            # 有的不带（`5.8植物措施`）。因此对两种形态各试一次，谁命中用谁。
            i = resolve_anchor(text, m['anchor'])
            if i < 0:
                print('  ! GB50433 未定位：%s' % m['anchor'])
                continue
            seg = re.sub(r'\s+', ' ', text[i:i + 1500])
            sents = [s.strip() for s in re.split(r'(?<=[。；])', seg) if s.strip()]
            picked, seen = [], set()
            for s in sents:
                if NOISE.search(s):
                    continue
                if not re.search(r'(应|宜|不应|不得|要求|设计|布设|设置)', s):
                    continue
                # 剔除目录行：形如「5.8 植物措施 (26) 5.9 临时防护措施…… (27)」
                # 特征：含省略号或"…"+"(页码)"，或一行里出现 3 个以上编号
                if s.count('…') >= 1 or re.search(r'\(\s*\d{1,3}\s*\)\s*\d', s):
                    continue
                if len(re.findall(r'\d+\.\d+\s', s)) >= 3:
                    continue
                k = s[:24]
                if k in seen:
                    continue
                seen.add(k)
                picked.append(s[:220])
                if len(picked) >= 6:
                    break
            if picked:
                out[m['key']] = collections.OrderedDict([
                    ('类别', 'GB50433 措施设计要求'),
                    ('依据', 'GB 50433-2018 §%s' % m['anchor'].split()[0]),
                    ('来源文件', os.path.relpath(p, VAULT)),
                    ('Zone A 设计要求', picked),
                    ('服务节点', m['serve']),
                ])
    return out


def build():
    zp, ztext = zone_a_text()
    if not ztext:
        print('  ! 未找到 GB 51018，Zone A 部分为空')
    methods = collections.OrderedDict()
    for name, cfg in MEASURES.items():
        rec = collections.OrderedDict([('措施', name), ('类型', cfg['type'])])
        # ① Zone A 设计要求
        if ztext:
            sents = pick_sentences(ztext, cfg['kw'])
            if sents:
                rec['Zone A 设计要求'] = sents
                rec['依据'] = 'GB 51018-2014 %s' % (
                    ('§%s' % cfg['section']) if cfg['section'] else '（相关条文）')
                rec['来源文件'] = os.path.relpath(zp, VAULT)
        if len(rec) <= 2:
            continue
        rec['服务节点'] = cfg.get('serve') or []
        methods[name] = rec
    # ② 监测类条款要求
    for k, v in extract_monitor_rules().items():
        methods[k] = v
    return methods


def save(methods):
    doc = collections.OrderedDict()
    doc['purpose'] = ('措施工法索引：服务模板 7.7「典型设计」、5.2「堆置方案」、'
                      '9.1.2「按措施类型计列工程量」。')
    doc['rule'] = ('① Zone A 条文为**设计要求**，逐字引用并附条号；'
                   '② 无出处的措施不登记；'
                   '③ 具体断面尺寸属项目数据，须来自项目资料或标准原文，不得自拟。')
    doc['sources'] = {'Zone A': os.path.relpath(ZONE_A, VAULT)}
    doc['count'] = len(methods)
    doc['methods'] = methods
    io.open(OUT, 'w', encoding='utf-8').write(
        json.dumps(doc, ensure_ascii=False, indent=1) + '\n')
    return doc


def audit(doc):
    bad = 0
    for k, v in doc['methods'].items():
        if not v.get('来源文件'):
            print('  ✗ %s 无来源文件' % k)
            bad += 1
            continue
        if not os.path.isfile(os.path.join(VAULT, v['来源文件'])):
            print('  ✗ %s 来源文件不存在' % k)
            bad += 1
    return bad


def main():
    ap = argparse.ArgumentParser(description='构建措施工法索引（资产 F）')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--show', metavar='措施')
    ap.add_argument('--audit', action='store_true')
    ap.add_argument('--force', action='store_true', help='忽略指纹，强制重建')
    a = ap.parse_args()

    if a.build:
        unchanged, fp, nf = CACHE.fingerprint_unchanged('measure_methods', SCAN_DIRS)
        if unchanged and os.path.exists(OUT) and not a.force:
            print('知识库未变更（%d 文件），跳过重建 → %s' % (nf, OUT))
            print('  （如需强制重建加 --force）')
            return 0
        t0 = time.time()
        methods = build()
        doc = save(methods)
        CACHE.save_texts()
        CACHE.save_fingerprint('measure_methods', fp, nf)
        print('已生成 %s（%d 项措施，耗时 %.1fs）' % (OUT, len(methods), time.time() - t0))
        for k, v in methods.items():
            print('  %-12s %-6s 设计要求 %d 条' % (
                k, v.get('类型', ''), len(v.get('Zone A 设计要求') or [])))
        print()
        bad = audit(doc)
        print('来源自检：%d 项，无法回溯 %d 项 %s'
              % (len(methods), bad, '✅' if bad == 0 else '❌'))
        return 0 if bad == 0 else 1

    if a.list:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        for k, v in doc['methods'].items():
            print('  %-12s %s' % (k, v.get('类型', '')))
        return 0

    if a.show:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        v = doc['methods'].get(a.show)
        if not v:
            print('未收录：%s' % a.show)
            return 1
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return 0

    if a.audit:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        bad = audit(doc)
        print('来源自检：%d 项，无法回溯 %d 项 %s'
              % (len(doc['methods']), bad, '✅' if bad == 0 else '❌'))
        return 0 if bad == 0 else 1

    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

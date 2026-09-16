#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Zone C 写法范式提取器：把 91 份已批方案的**正文**加工成可注入的"写法范式"。

## 它解决什么问题

Zone C（应用范例层）现有 91 份已批方案，共 118MB 正文，是知识库里最贴近
"方案正文该怎么写"的资产。但索引里只存了 `topic_tags` / `structure_summary` 等**标签**，
写章节时注入的仅是一句「老八章结构…二级节 43 个、全文表格 80 个」——
那是**章节目录**，不是**写法**。于是写作者拿不到"这一段别人是怎么起笔、怎么详略"的样本，
产出偏机械。

本脚本把正文段落加工成**写法范式**，供写作时参照。

## 铁律：只用"怎么写"，绝不用"写了什么"

`zone-c-usage.md` 定义的**允许清单（只提这 5 类）**：
  ① 章节展开顺序   ② 段落功能   ③ 表格栏目结构   ④ 句式模板   ⑤ 论证链步骤

**禁止提取**：合规结论、参数取值、具体措施直接套用、项目名称、地点、数字、单位名称。

因此本脚本对每段做**强制脱敏**，任一项残留即整段丢弃（不是标注保留）：
  · 数值 + 单位        → 删除（`93万t/a` → 删）
  · 项目/公司/矿名      → 删除
  · 行政区划与地名      → 删除
  · 文号与标准号        → 删除
  · 人员/机构名         → 删除

脱敏后**只保留句式骨架**（把被删内容替换为占位符 `〔数值〕`「〔名称〕」等），
使样本可直接用于新项目而**不携带任何原项目信息**——这正是铁律5「零项目残留」的要求。

## 用法

    python extract_style_samples.py --build                     # 扫描知识库生成样本库
    python extract_style_samples.py --build --limit 20          # 只处理前 20 份（调试）
    python extract_style_samples.py --show 2.1                  # 查看某节点的样本
    python extract_style_samples.py --audit                     # 脱敏自检（关键！）

产出：`references/zone_c_style_samples.json`
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
ZI = RULES.get('zone_c_injection', {}) or {}
SS = ZI.get('style_samples', {}) or {}

from vault_paths import VAULT
ZONE_C_DIR = os.path.join(VAULT, 'md库', 'Zone C - 应用范例')
OUT = os.path.join(REF, SS.get('output_file', 'zone_c_style_samples.json'))
CACHE = KC.KBCache(VAULT)
SCAN_DIRS = [ZONE_C_DIR]

# ---------------------------------------------------------------- 清洗
# 先做**格式清洗**再做脱敏：这些是 PDF→md 转换产生的公式/标记残渣，
# 不先去掉会让脱敏器把 LaTeX 片段切碎，产出无法阅读的样本。
JUNK = [
    (re.compile(r'<sup>.*?</sup>', re.S), ''),
    (re.compile(r'<sub>.*?</sub>', re.S), ''),
    (re.compile(r'<table.*?</table>', re.S), ''),
    (re.compile(r'<[^>]{1,40}>'), ''),
    (re.compile(r'\$[^$]{0,200}\$'), '〔公式〕'),      # 行内公式
    (re.compile(r'\\(?:mathrm|mathbf|cdot|times|frac|text|begin|end)\b[^\s]{0,20}'), ''),
    (re.compile(r'[{}]'), ''),
    (re.compile(r'\s{2,}'), ' '),
]


def strip_junk(t):
    for pat, rep in JUNK:
        t = pat.sub(rep, t)
    return t.strip()


# ---------------------------------------------------------------- 脱敏规则
# 顺序敏感：先删更特异的（带上下文的），再删通用的。
SCRUB = [
    # ⚠ 顺序关键：**《…》书名号整体替换必须排在最前**。
    # 否则「《XX省水利厅关于…的公告》」会先被"省"规则切碎成
    # 「《〔省〕水利厅关于…的公告》」——既残留了文件名，又破坏可读性。
    (re.compile(r'《[^》]{2,80}》'), '《〔文件名〕》'),
    # 单位/机构名必须在"矿名/地名"之前处理，否则「XX矿业有限公司」会被矿名规则先切碎
    (re.compile(r'[\u4e00-\u9fffA-Za-z（）()]{2,30}'
                r'(?:有限公司|有限责任公司|股份有限公司|研究院|设计院|勘察院|勘测设计院|集团|事务所|管理局|服务中心)'),
     '〔单位〕'),
    # 文号 / 批复号 / 标准号
    (re.compile(r'[〔\[（(]\s*[\u4e00-\u9fffA-Za-z]{2,20}[〔\[（(]?\s*\d{4}\s*[〕\]）)]?\s*\d{1,4}\s*号[〕\]）)]?'), '〔文号〕'),
    (re.compile(r'[\u4e00-\u9fff]{2,12}[〔\[（]\s*\d{4}\s*[〕\]）]\s*\d{1,4}\s*号'), '〔文号〕'),
    (re.compile(r'\b(?:GB|GB/T|SL|SL/T|HJ|TD|DL|JGJ|JTG|NB|DB)\s*/?\s*T?\s*\d{3,5}(?:[—\-–]\d{2,4})?\b'), '〔标准号〕'),
    (re.compile(r'第\s*\d+\s*号'), '〔文号〕'),
    # 矿名 / 项目名
    (re.compile(r'[\u4e00-\u9fff]{2,20}'
                r'(?:煤矿|铁矿|铝土矿|铜矿|金矿|银矿|钼矿|石灰石矿|砂石矿|石矿|矿区|矿井|井田|采区)'),
     '〔矿名〕'),
    # 行政区划（长词优先）
    (re.compile(r'[\u4e00-\u9fff]{2,12}(?:省|自治区|特别行政区)'), '〔省〕'),
    (re.compile(r'[\u4e00-\u9fff]{2,10}(?:市|自治州|地区|盟)'), '〔市〕'),
    (re.compile(r'[\u4e00-\u9fff]{2,10}(?:县|自治县|区|旗)'), '〔县区〕'),
    (re.compile(r'[\u4e00-\u9fff]{2,10}(?:镇|乡|街道|苏木)'), '〔乡镇〕'),
    (re.compile(r'[\u4e00-\u9fff]{2,10}(?:村|嘎查|屯)'), '〔村〕'),
    # 水体
    (re.compile(r'[\u4e00-\u9fff]{2,10}(?:河|江|湖|水库|沟|川|溪|渠|干渠)'), '〔水体〕'),
    # 道路编号
    (re.compile(r'\b(?:G|S|X|Y)\d{1,4}\b'), '〔道路编号〕'),
    # 数值 + 单位
    (re.compile(r'\d[\d,.]*\s*(?:万\s*)?'
                r'(?:km²|km2|hm²|hm2|m³|m3|m²|m2|km|kg|t|Mt|kW|kV|MW|mm|cm|m|d|a|%|％|亿元|万元|元|人|户|台|辆|座|处|个|株|万株|m/s)'),
     '〔数值〕'),
    (re.compile(r'\d[\d,.]*'), '〔数值〕'),
]

# 允许的句式骨架信号：这些词说明该段是"总述/分述/结论"的功能段，值得留作范式。
# 判定按**优先级顺序**（列表顺序即优先级），因为一段常同时含多类信号：
#   依据 > 机理 > 结论 > 分述 > 总述
# —— 「根据《…》…应…」这种句子，其写作价值在"怎么引依据"，而非"怎么总结"，
#    故依据优先于结论；「由于…导致…」的机理句同理。
FUNC_MARKERS = [
    ('依据', ['根据《', '依据《', '按照《', '遵照《', '按《', '根据国家', '依据国家',
              '根据有关', '依据有关', '按照有关']),
    ('机理', ['由于', '因为', '导致', '造成', '从而', '进而', '以防止', '为避免',
              '以免', '使得', '引起', '作用在于', '其原因', '有利于', '不利于']),
    ('结论', ['符合', '满足', '不存在', '综上', '因此', '可行', '达到', '可见',
              '据此', '表明', '说明该项', '总体合理', '结论']),
    ('分述', ['分别', '其中', '依次', '各采区', '按分区', '分为', '包括', '其一',
              '一是', '二是', '首先', '其次']),
    ('总述', ['本项目', '该项目', '项目位于', '工程位于', '概况', '简述', '总体',
              '主要由', '属新建', '属改扩建']),
]

MIN_LEN = int(SS.get('min_chars', 120))
MAX_LEN = int(SS.get('max_chars', 420))
MAX_PER_TOPIC = int(SS.get('max_per_topic', 3))


def scrub(text):
    """脱敏：返回 (脱敏文本, 命中计数)。"""
    text = strip_junk(text)
    n = 0
    for pat, rep in SCRUB:
        text, k = pat.subn(rep, text)
        n += k
    # 合并连续占位符：避免「〔县区〕〔县区〕〔县区〕〔县区〕」这类碎片化输出，
    # 它会把句子结构破坏殆尽，样本失去参照价值。
    text = re.sub(r'(〔[^〕]{1,6}〕)(?:\s*[、，,／/]?\s*\1)+', r'\1×N', text)
    text = re.sub(r'(〔[^〕]{1,6}〕)(?:\s*\1)+', r'\1', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip(), n


def leakage_scan(text):
    """脱敏后自检：是否仍残留可识别的项目信息。返回违规列表。

    注意：本函数只做**粗筛**（数字、单位名、矿名、地名后缀）；
    真正的保证来自"脱敏在前、粗筛在后"——只要 SCRUB 覆盖到位，
    这里就不应有命中。命中即说明 SCRUB 漏了某类模式，须补规则。
    """
    bad = []
    if re.search(r'\d', text):
        m = re.search(r'.{0,14}\d.{0,14}', text)
        bad.append('残留数字：%s' % m.group(0))
    for w in ('有限公司', '有限责任公司', '研究院', '设计院', '集团'):
        if w in text:
            bad.append('残留单位名')
    for w in ('煤矿', '铁矿', '铝土矿', '铜矿', '矿区', '井田', '矿井'):
        if w in text:
            bad.append('残留矿名')
    # 地名后缀：须排除「〔县区〕」等占位符本身
    for suffix in ('省市县镇乡村'):
        for m in re.finditer(r'[\u4e00-\u9fff]{2,8}' + suffix, text):
            s = m.group(0)
            if '〔' in s or '〕' in s:
                continue
            bad.append('残留地名：%s' % s)
            break
    # 常见生物/土种名（黄棕壤、亚热带…）本身不算敏感，不拦；
    # 但标准名残留（无〔标准号〕包裹）要拦
    if re.search(r'《[^》]{2,50}》', text) and '〔' not in re.search(r'《[^》]{2,50}》', text).group(0):
        bad.append('残留文件名')
    return bad


def paragraph_function(p):
    """判定段落功能（允许清单第②类）。

    按 FUNC_MARKERS 的顺序（即优先级）取**首个**命中的功能类。
    一段可能同时含依据与结论信号，取优先级更高者——这决定了它作为
    "写法样板"时教给写作者的是什么。
    """
    for name, words in FUNC_MARKERS:
        if any(w in p for w in words):
            return name
    return ''


def argument_chain(p):
    """抽取论证链步骤（允许清单第⑤类）。

    做法：把段落按句切分，对每句判功能，压成功能序列
    （如「总述→分述→结论」）。这条链正是"这一段如何推进论证"的骨架，
    比单看句式更能指导"怎么写扎实"。
    """
    sents = [s.strip() for s in re.split(r'(?<=[。；])', p) if s.strip()]
    chain = []
    for s in sents:
        f = paragraph_function(s)
        if f and (not chain or chain[-1] != f):
            chain.append(f)
    return '→'.join(chain)


def sentence_skeleton(p):
    """抽取句式骨架（允许清单第④类）：把句子首尾结构保留，中段抽象化。

    做法：保留段落的首句与末句骨架（这两句通常承担"起笔"与"收口"功能），
    中间以省略号代替，形成可模仿的句式模板。
    """
    sents = [s.strip() for s in re.split(r'(?<=[。；])', p) if s.strip()]
    if len(sents) <= 2:
        return p
    return sents[0] + '……' + sents[-1]


def clean_ws(t):
    t = re.sub(r'[\u3000]+', ' ', t)
    t = re.sub(r'[ \t]{2,}', ' ', t)
    return t.strip()


# ---------------------------------------------------------------- 读知识库
def iter_zone_c():
    for p in glob.glob(os.path.join(ZONE_C_DIR, '**', '*.md'), recursive=True):
        yield p


def strip_frontmatter(text):
    """去 YAML frontmatter 与库使用边界引用块，只留正文。"""
    lines = text.split('\n')
    end = 0
    if lines and lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                end = i + 1
                break
    body = lines[end:]
    # 去掉以 > 开头的库使用说明块
    body = [l for l in body if not l.lstrip().startswith('>')]
    # 去目录行（形如 "1.2 编制依据.... ....9"）
    body = [l for l in body if not re.search(r'\.{4,}\s*\d+\s*$', l)]
    return '\n'.join(body)


def iter_paragraphs(body):
    for raw in body.split('\n'):
        s = raw.strip()
        if not s or s.startswith('#') or s.startswith('<table') or s.startswith('|'):
            continue
        if s.startswith('表 ') or re.match(r'^表\s*[\d.]+', s) or re.match(r'^图\s*[\d.]+', s):
            continue
        if len(s) < MIN_LEN:
            continue
        yield s[:MAX_LEN]


def topic_of(text, tags):
    """按范例自身的 topic_tags 判定该段属于哪个主题（命中越多者优先）。

    注意：topic_tags 是**章节级**标签（如「项目组成及工程布置」），
    直接用它当文本匹配键会漏掉大量段落——段落正文里未必出现这个词。
    因此这里做两级判定：
      ① 标签词出现在段内 → 直接计分；
      ② 否则用 SECTION_HINT 把段落特征词映射回章节级标签
         （如段内含「采区/排土场/道路」→ 归入「项目组成及工程布置」）。
    """
    score = collections.Counter()
    for t in tags:
        if t and t in text:
            score[t] += 2
    for t in tags:
        for hint in SECTION_HINT.get(t, ()):
            if hint in text:
                score[t] += 1
    return [t for t, _ in score.most_common(2)]


# 章节级标签 → 段落特征词（用于把"没直接提该标签"的段落归回其主题）
# 只放**判断工程/章节性质**的词，不放任何项目专有信息。
SECTION_HINT = {
    '项目组成及工程布置': ['采区', '露天采场', '排土场', '矿山道路', '工业场地',
                           '附属设施', '开采标高', '台阶', '边坡', '布置'],
    '施工组织': ['施工期', '施工生产', '施工生活', '施工道路', '施工用水',
                 '施工用电', '施工工艺', '施工方法', '基建期'],
    '工程占地': ['占地', '永久占地', '临时占地', '占地面积', '占地类型'],
    '土石方平衡': ['挖方', '填方', '借方', '弃方', '土石方', '调配', '剥离物'],
    '自然概况': ['地貌', '气候', '水文', '土壤', '植被', '气象', '地质'],
    '水土流失防治责任范围': ['防治责任范围', '责任范围', '项目建设区', '直接影响区'],
    '土壤流失量预测': ['侵蚀模数', '流失量', '预测', '扰动后', '背景值'],
    '防治目标': ['防治目标', '防治标准', '六项指标', '治理度', '控制比'],
    '防治区划分': ['防治分区', '防治区', '分区'],
    '措施总体布局': ['措施体系', '总体布局', '措施布局', '防治措施体系'],
    '分区措施布设': ['工程措施', '植物措施', '临时措施', '措施布设', '工程量'],
    '表土剥离保护': ['表土剥离', '剥离厚度', '表土资源', '可剥离'],
    '表土回覆': ['表土回覆', '覆土', '回覆'],
    '表土堆存': ['表土堆存', '堆存场', '堆存区', '临时堆土'],
    '监测内容': ['监测内容', '监测点', '监测频次', '监测方法'],
    '水土保持投资及效益分析成果': ['投资', '估算', '费用', '效益', '概算'],
    '水土保持管理': ['管理', '监理', '验收', '监督检查'],
}


def build(limit=None):
    files = sorted(iter_zone_c())
    if limit:
        files = files[:limit]
    samples = collections.defaultdict(list)
    stats = {'files': 0, 'paras': 0, 'kept': 0, 'dropped_leak': 0, 'dropped_short': 0}
    for i, p in enumerate(files, 1):
        try:
            raw = CACHE.read(p)
        except Exception:
            continue
        fm_tags = re.findall(r'^\s*topic_tags:\s*\[(.*?)\]', raw[:6000], re.M | re.S)
        tags = re.findall(r'"([^"]+)"', fm_tags[0]) if fm_tags else []
        body = strip_frontmatter(raw)
        stats['files'] += 1
        for para in iter_paragraphs(body):
            stats['paras'] += 1
            sc, _ = scrub(clean_ws(para))
            if len(sc) < MIN_LEN:
                stats['dropped_short'] += 1
                continue
            if leakage_scan(sc):
                stats['dropped_leak'] += 1
                continue
            topics = topic_of(sc, tags) or ['通用']
            for t in topics:
                if len(samples[t]) < MAX_PER_TOPIC:
                    samples[t].append({
                        'text': sc,
                        'function': paragraph_function(sc),
                        'skeleton': sentence_skeleton(sc),
                        'argument_chain': argument_chain(sc),
                        'source_file': os.path.basename(p),
                    })
                    stats['kept'] += 1
    return samples, stats


def save(samples, stats):
    doc = collections.OrderedDict()
    doc['purpose'] = ('Zone C 写法范式样本：只含"怎么写"，不含"写了什么"。'
                      '所有数值/项目名/地名/单位名/文号已替换为〔占位符〕。')
    doc['usage_rule'] = SS.get('usage_rule') or (
        '仅可用于参照段落功能、句式骨架与论证链；'
        '不得引入其结论、参数、措施做法与项目数据；'
        '与 Zone A 冲突时以 Zone A 为准；Zone A 未规定时输出'
        '「Zone A 未明确规定，以下为参考写法，需人工判断」。')
    doc['allow_list'] = ['章节展开顺序', '段落功能', '表格栏目结构', '句式模板', '论证链步骤']
    doc['forbid_list'] = ['合规结论', '参数取值', '具体措施直接套用', '项目名称', '地点', '数字', '单位名称']
    doc['built_from'] = ZONE_C_DIR
    doc['stats'] = stats
    doc['topics'] = collections.OrderedDict(
        (k, v) for k, v in sorted(samples.items(), key=lambda x: -len(x[1])))
    io.open(OUT, 'w', encoding='utf-8').write(
        json.dumps(doc, ensure_ascii=False, indent=1) + '\n')
    return doc


def audit(doc):
    """脱敏自检：扫描样本库有无残留项目信息。"""
    bad = 0
    total = 0
    for t, items in doc['topics'].items():
        for it in items:
            total += 1
            v = leakage_scan(it['text'])
            if v:
                bad += 1
                print('  ✗ [%s] %s' % (t, v[0]))
                print('      %s' % it['text'][:100])
    return total, bad


def main():
    ap = argparse.ArgumentParser(description='Zone C 写法范式提取器')
    ap.add_argument('--build', action='store_true', help='扫描知识库生成样本库')
    ap.add_argument('--limit', type=int, help='只处理前 N 份（调试用）')
    ap.add_argument('--show', metavar='主题', help='查看某主题的样本')
    ap.add_argument('--audit', action='store_true', help='对已生成的样本库做脱敏自检')
    ap.add_argument('--list', action='store_true', help='列出所有主题及样本数')
    ap.add_argument('--force', action='store_true', help='忽略指纹，强制重建')
    a = ap.parse_args()

    if a.build:
        unchanged, fp, nf = CACHE.fingerprint_unchanged('style_samples', SCAN_DIRS)
        if unchanged and os.path.exists(OUT) and not a.force and not a.limit:
            print('知识库未变更（%d 文件），跳过重建 → %s' % (nf, OUT))
            print('  （如需强制重建加 --force）')
            return 0
        t0 = time.time()
        if not os.path.isdir(ZONE_C_DIR):
            print('❌ Zone C 目录不存在：%s' % ZONE_C_DIR)
            return 2
        samples, stats = build(a.limit)
        doc = save(samples, stats)
        CACHE.save_texts()
        if not a.limit:
            CACHE.save_fingerprint('style_samples', fp, nf)
        print('已生成 %s（耗时 %.1fs）' % (OUT, time.time() - t0))
        print('  扫描 %d 份 / 段落 %d / 保留 %d / 脱敏丢弃 %d / 过短丢弃 %d'
              % (stats['files'], stats['paras'], stats['kept'],
                 stats['dropped_leak'], stats['dropped_short']))
        print('  主题 %d 个' % len(samples))
        total, bad = audit(doc)
        print('  脱敏自检：%d 条样本，残留 %d 条 %s' % (total, bad, '✅' if bad == 0 else '❌'))
        return 0 if bad == 0 else 1

    if a.audit:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        total, bad = audit(doc)
        print('脱敏自检：%d 条样本，残留 %d 条 %s' % (total, bad, '✅' if bad == 0 else '❌'))
        return 0 if bad == 0 else 1

    if a.list:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        for t, items in doc['topics'].items():
            print('  %-28s %d' % (t, len(items)))
        return 0

    if a.show:
        doc = json.load(io.open(OUT, encoding='utf-8'))
        for it in (doc['topics'].get(a.show) or [])[:5]:
            print('【%s】%s' % (it['function'] or '通用', it['source_file']))
            print('  %s' % it['text'][:300])
            print()
        return 0

    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

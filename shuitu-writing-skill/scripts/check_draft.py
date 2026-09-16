#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第四层：稿后校核器。对已写出的章节草稿做机械回检，闭合"写作辅助"环路。

背景：write_chapter.py 解决"写前给指令包"；写完的稿子此前没有任何工具回检，
本工作区 1.6.2 稿的两处内部冲突（防治分区数量、堆置高度口径）都是靠人工逐字复核才发现的。
本脚本把这类复核中**可机械化**的部分固化为四项检查（规则全部来自 rules.json / template-tree.json，
不在代码里新增隐藏规则）：

  C1 模板覆盖    —— 稿件是否覆盖目标节点 required_content_points 的全部内容点（含条件分支提醒）
  C2 占位符清点  —— 【待填：…】/【待核实：…】清单（不是缺陷，是"待人工处理"事项的显式化）
  C3 数字一致性  —— 跨章节/多稿之间的同名数值事实冲突比对（如"3 个分区"vs"四个分区"无法自动
                    判定，但"86.5 hm²"vs"89.2 hm²"可以）；只报"疑似冲突，需人工确认"
  C4 章节标题    —— 稿件是否存在包含目标章节号的标题行（防止写错章节/漏写标题层级）
  C5 文风与深度  —— 空话密度、超长句、句式重复、段落长度、术语统一、深度要素（依据/量化/结论/机理）
  C6 篇幅台账    —— 逐节实际字数 vs 预算（偏少/达标/超标）+ 全书累计进度与页数折算
  C7 台账核对    —— 正文与事实台账比对，抓跨章口径打架（指定 --ledger 时执行）

用法:
    python check_draft.py --draft 稿.md --chapter 1.6.2 [--province 河南省]
    python check_draft.py --draft 稿A.md --draft 稿B.md --chapter 1.6.2   # 多稿联合比对
    python check_draft.py --draft 稿.md --chapter 1.6.2 --ledger 台账.json
    python check_draft.py --draft 稿.md --report-form                     # 报告表分支
    python check_draft.py --draft 稿.md --chapter 1.6.2 --no-style        # 只查覆盖与篇幅
    python check_draft.py --draft 稿.md --chapter 1.6.2 --out 校核报告.md

约定：
  · 本脚本只做机械比对，不下合规结论；所有发现均为"疑似，需人工确认"。
  · 与合规闸门的关系：闸门管"写之前依据是否成立"，本脚本管"写之后是否自洽、是否覆盖骨架"。
  · 项目信息只从命令行指定的稿件文件读取，本脚本不写任何文件（--out 指定的报告除外），
    天然满足零项目残留铁律。
"""
import argparse, json, os, re, sys

import sys
sys.dont_write_bytecode = True   # 技能包不留 __pycache__（避免缓存掩盖规则改动）

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REF = os.path.join(SKILL, 'references')
sys.path.insert(0, HERE)
import check_gate as G  # noqa: E402  复用闸门的骨架展开/快照闸门/报告表目标
import budget as B      # noqa: E402  篇幅预算与字数口径

RULES = G.RULES
NODES = G.NODES

# ---------- C2 占位符 ----------
PH_RE = re.compile(r'【(待填|待核实|待确认|待补充|待定)\s*[：:]?\s*([^】]{0,80})】')

# ---------- C3 数字事实抽取 ----------
NUM_FACT_RE = re.compile(
    r'(?P<pre>[^0-9.\n]{0,14}?)(?<![\d.A-Za-z])(?P<val>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d.A-Za-z])'
    r'\s*(?P<unit>hm²|公顷|万m³|万立方米|m³|立方米|万m²|万m2|m²|m2|平方米|km²|km|万元|亿元|万元/年|％|%|'
    r'万t|万吨|万t/a|t|吨|年|个月|月|日|m|米|mm|cm|处|个|座|条|眼|kV|千伏|MW|kW|万kWh|hm)?')

# 前文命中这些词的事实不参与比对（规范条款号/图表编号/年份语境/文献出处等不是项目数据）
CTX_BLACKLIST = [
    r'依据', r'按照', r'见表', r'如表', r'如图', r'所示', r'注[:：]', r'备注',
    r'模板', r'标准', r'规范', r'导则', r'技术文件', r'指南', r'办法', r'条例', r'意见',
    r'第\s*[0-9一二三四五六七八九十]+\s*(章|节|条|款|项|期|卷|篇)', r'附录', r'附表', r'附件',
    r'GB', r'SL', r'TD', r'办水保', r'水总', r'〔', r'\[\d+\]', r'文号', r'编号',
    r'电话', r'邮编', r'传真', r'邮箱', r'网址', r'http', r'www\.',
    r'设计水平年为', r'水平年为',    # 「设计水平年为 2028 年」是重要结论，但按年份语境单独核对更可靠
    r'版', r'发布', r'施行', r'修订', r'通过',
    r'用于', r'对应', r'详见', r'见第', r'逐字', r'对照', r'口径', r'冲突', r'矛盾',
]
CTX_BLACKLIST_RE = re.compile('|'.join(CTX_BLACKLIST))

# 立即前缀为拉丁字母的数字（M7.5、C25、PVC-110 等）且无单位 → 不是数量事实
GRADE_RE = re.compile(r'[A-Za-z]$')

# 节点标题→事实 key 的别名表：同一事实在不同章节用词不同，须归一后再比对
KEY_ALIASES = {
    '防治责任范围': ['防治责任范围', '责任范围面积'],
    '扰动地表面积': ['扰动地表', '扰动面积'],
    '表土剥离量': ['表土剥离', '剥离表土'],
    '永久占地': ['永久占地', '永久用地'],
    '临时占地': ['临时占地', '临时用地'],
    '工程占地': ['工程占地', '占地总面积', '总占地'],
    '挖方': ['挖方', '开挖方'],
    '填方': ['填方', '回填方'],
    '弃方': ['弃方', '弃渣量', '弃渣总量'],
    '借方': ['借方'],
    '总投资': ['总投资', '项目总投资'],
    '土建投资': ['土建投资'],
    '水土保持总投资': ['水土保持总投资', '水保总投资', '水土保持措施投资'],
}


def fact_key(ctx):
    """从数值前文提取归一化 key：命中别名表用别名组名，否则用（去噪后的）原词。"""
    # 仅保留中英文字母、数字与下划线（\w 在 str 模式下覆盖 CJK），去尽标点与符号
    ctx = re.sub(r'[^\w]', '', ctx)
    for name, words in KEY_ALIASES.items():
        for w in words:
            if w in ctx:
                return name
    # 过短 key 辨识度不足（分节号"与"、尺寸单字"深"），不参与跨行比对
    if len(ctx) < 3:
        return ''
    return ctx[-12:] if ctx else ''


def extract_facts(text):
    """抽取数值事实 [(key, value, unit, line_no, context)]；只保留可信度较高的条目。"""
    facts = []
    lines = text.splitlines()
    section = ''                              # 最近一次出现的标题/分节标记（用于分区归属判断）
    for i, line in enumerate(lines, 1):
        hs = re.match(r'^#{1,6}\s+(.*)', line.strip())
        if hs:
            section = hs.group(1).strip()
        elif re.match(r'^\*\*[^*]+\*\*$', line.strip()):
            section = line.strip().strip('*')
        if PH_RE.search(line) and not re.search(r'\d', PH_RE.search(line).group(0)):
            pass  # 占位符行照常抽取数字（占位符本身无数字）
        for m in NUM_FACT_RE.finditer(line):
            pre, val, unit = m.group('pre'), m.group('val'), m.group('unit') or ''
            tail_ctx = line[m.end():m.end() + 6]
            # 表格行：语境不得跨单元格。否则 `| 不作要求 | 25.0 |` 会把「不作要求」
            # 当成 25.0 的事实名，凭空造出「不作要求=25.0」这种假冲突。
            if line.lstrip().startswith('|'):
                # 单元格边界要用「数字本身」的位置判定：m.start() 是整段匹配的起点，
                # 它比数字还早 14 个字符，用它 rfind 会把上一个小格的内容并进来。
                vs, ve = m.start('val'), m.end('val')
                cstart = line.rfind('|', 0, vs) + 1
                cend = line.find('|', ve)
                if cend < 0:
                    cend = len(line)
                pre = line[cstart:vs]
                tail_ctx = line[ve:min(cend, ve + 6)]
            # 语境只取「最近一个小句」：一个单元格里常并排写多个事实
            # （如「施工期 95；设计水平年 97」），不切句会把两个事实并成一个 key。
            ctx = re.split(r'[；;，,、]', pre)[-1]
            if unit and not ctx.strip():
                # 数字前无前文（如表格单元格）→ 用本格内该数字前面的全部文字做语境
                ctx = pre[-16:]
            try:
                v = float(val.replace(',', ''))
            except ValueError:
                continue
            if GRADE_RE.search(pre) and not unit:
                continue                       # M7.5 / C25 一类强度等级
            window = (ctx + unit + tail_ctx)
            if XREF_RE.search(ctx):
                continue                       # 表1.6.1-1 / 第7.4条 等交叉引用，不是项目数据
            if CTX_BLACKLIST_RE.search(window) and not unit:
                continue                       # 无单位的语境命中黑名单 → 术语/编号可能性大
            if CTX_BLACKLIST_RE.search(ctx) and unit in ('年', '月', '日', ''):
                continue                       # 年份/日期语境的条款号等
            # 无单位的事实：语境必须短且不含标点（「防治责任范围 86.5」可信，
            # 「…不涉及临时占地）；第 4 区」不可信——后者是句子里的普通数字）
            if not unit:
                seg = re.sub(r'[\s*_`]', '', ctx)
                if re.search(r'[，。；、）)】」|]', seg):
                    continue
                if len(seg) > 8 and not re.search(r'[：:＝=为约达到]$', seg):
                    continue
            if v > 10 ** 9:
                continue
            key = fact_key(ctx)
            if not key:
                continue
            facts.append({'key': key, 'value': v, 'unit': unit,
                          'line': i, 'section': section,
                          'context': re.sub(r'\s+', ' ', line.strip())[:80]})
    return facts


# 措施名/时段标签/交叉引用的判定词表来自 rules.json check_rules（领域知识不进代码）；
# 下面的内置值仅在 rules.json 缺该键时兜底，保证老版本规则文件也能跑。
_CR = G.RULES.get('check_rules', {})
_MHK = _CR.get('measure_hint_keywords', {})
_mh_words = []
for _v in _MHK.values():
    if isinstance(_v, list):
        _mh_words.extend(_v)
# 工程措施数量词：不同防治分区各自的措施数量不是同一事实，出现不同值属正常
MEASURE_HINT_RE = re.compile('|'.join(_mh_words) if _mh_words
                             else r'池|沟|坝|挡|墙|覆|盖|网|拦|排|沉沙|草帘|袋|'
                                  r'植树|栽植|种草|草籽|撒播|喷播|绿化')

# 交叉引用（表1.6.1-1 / 图 3 / 第7.4条）不是项目数据，永远不参与事实比对
_xref = (_CR.get('cross_ref_patterns', {}) or {}).get('patterns') or []
XREF_RE = re.compile('|'.join(_xref) if _xref
                     else r'(?:表|图|第|附录|附件|附表)\s*[\d一二三四五六七八九十]')

# 时段标签：同一指标在施工期与设计水平年的目标值本来就不同，不构成冲突，只降级提示
_plabels = (_CR.get('period_labels', {}) or {}).get('labels') or []
PERIOD_HINT_RE = re.compile('|'.join(_plabels) if _plabels
                            else r'施工期|设计水平年|自然恢复期|水平年|建设期|运行期')


def find_conflicts(all_facts):
    """同名同单位的数值事实出现 ≥2 个不同值 → 疑似冲突。

    降级规则（仍列出、但不计入 problems）：
      · key 像工程措施数量（池/沟/坝…）且各次出现归属不同分节（防治分区）→ 不同分区各自的量；
      · key 是时段标签（施工期 / 设计水平年）→ 同一指标不同时段的目标值本就不同。
    """
    groups = {}
    for src, f in all_facts:
        groups.setdefault((f['key'], f['unit']), []).append((src, f))
    conflicts = []
    for (key, unit), items in sorted(groups.items()):
        values = sorted({f['value'] for _, f in items})
        if len(values) >= 2:
            sections = {f['section'] for _, f in items}
            why = None
            if MEASURE_HINT_RE.search(key) and len(sections) > 1:
                why = '疑为不同防治分区各自的工程数量，非同一事实——如需全表合计请人工核对'
            elif PERIOD_HINT_RE.search(key):
                why = '该 key 是时段标签：同一指标在施工期与设计水平年的值本就不同，非冲突；' \
                      '若确为同一时段内的同一事实，请人工核对'
            conflicts.append({
                'key': key, 'unit': unit, 'values': values,
                'downgraded': bool(why),
                'occurrences': [{'source': s, 'line': f['line'], 'value': f['value'],
                                 'section': f['section'], 'context': f['context']}
                                for s, f in items[:12]],
                'note': why or '同名同单位数值出现多个不同值——疑似内部冲突，需人工确认口径',
            })
    # 真冲突排前，降级项排后
    conflicts.sort(key=lambda c: c['downgraded'])
    return conflicts


# ---------- C1 覆盖 ----------
def point_hit(point, text_flat):
    if point in text_flat:
        return True
    segs = [s for s in re.split(r'[，、；：（）()与及和的]+', point) if len(s) >= 2]
    return bool(segs) and all(s in text_flat for s in segs)


def coverage(targets, text_flat):
    rows = []
    for t in targets:
        n = NODES[t]
        hits, misses = [], []
        for p in n['required_content_points']:
            (hits if point_hit(p, text_flat) else misses).append(p)
        rows.append({'chapter_id': t, 'title': n['title'],
                     'total': len(n['required_content_points']),
                     'hit': hits, 'missed': misses,
                     'conditional': n.get('conditional', False),
                     'conditional_variants': n.get('conditional_variants', []),
                     'conditional_note': n.get('conditional_note', '')})
    return rows


# ---------- C4 标题 ----------
def heading_check(targets, text):
    heads = [ln.strip() for ln in text.splitlines() if re.match(r'^#{1,6}\s*\S', ln)]
    missing = [t for t in targets
               if not any(t in h or NODES[t]['title'] in h for h in heads)]
    return {'heading_count': len(heads),
            'missing_heading_for': [t for t in missing if t in NODES]}


# ---------- C5 文风与深度 ----------
def _sentences(text):
    return [s.strip() for s in re.split(r'[。！？；\n]+', text) if s.strip()]


def _paras(text):
    return [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]


def _is_list_item(p):
    return bool(re.match(r'^\s*(?:[-*+]|\d+[.)、]|[（(]\d+[）)])\s', p))


def _standalone_count(body, term, group):
    """统计 term 的「独立出现」次数：被同组更长名称包住的出现不算。

    例：组内有「弃渣场／渣场」时，"弃渣场"里的"渣场"不算"渣场"独立出现，
    否则每篇用词规范的方案都会被误报术语不统一。
    """
    longer = [o for o in group if len(o) > len(term)]
    if not longer:
        return len(re.findall(re.escape(term), body))
    spans = []
    for o in longer:
        spans.extend(m.span() for m in re.finditer(re.escape(o), body))
    n = 0
    for m in re.finditer(re.escape(term), body):
        s, e = m.span()
        if any(a <= s and e <= b for a, b in spans):
            continue
        n += 1
    return n


def _strip_non_prose(body):
    """剔除**不是行文**的内容，避免污染标点/节奏类统计。

    ## 为什么必须做这一步（实测踩过的坑）

    第一版「去机械感」体检对 8 份**真实已批方案**跑，结果每份都报
    「标点过密/滥用」——是误报，不是稿子有问题。逐层排查出三类污染源：

    ① **点线目录**：`1.1 项目简况...  ....5` 这类行，
       点线与密集括号让逗号密度飙到 29‰（真人正文 32‰，但目录本身不是行文）；
    ② **LaTeX 公式**：`$$ M_{yz} = R K L_y S_y B E T A $$` 与 `\\mathrm{...}`，
       几乎不含中文标点，会把「标点密度」压到 6‰ 以下，误报「标点偏少」；
    ③ **纯页码 / 表格分隔线**：本身没有语义。

    这些都不是叙述文本。统计行文特征前必须先排除，
    否则规则一上线就全是噪声，使用者很快就会把整个体检关掉——那比不做还糟。

    散文段落一律保留：只按行型判断，不做长度截断。
    """
    keep = []
    in_math = False
    for line in body.split('\n'):
        s = line.strip()
        if not s:
            keep.append(line)
            continue
        # 行间公式块 $$ ... $$（可能跨行）
        if s.startswith('$$') or s.startswith('\\['):
            if s.count('$$') == 1:
                in_math = not in_math
            continue
        if in_math:
            continue
        # 行内公式行：整行以 $ 包裹、或 LaTeX 命令占比高
        if re.match(r'^\$', s) or len(re.findall(r'\\[a-zA-Z]+', s)) >= 3:
            continue
        # 点线目录
        if len(s) < 120 and re.search(r'(\.{3,}|·{3,}|…{2,}|-{4,})', s):
            stripped = re.sub(r'(\.{2,}|·{2,}|…+|-{3,}|\s|\d)', '', s)
            if len(stripped) < 30:
                continue
        # 纯页码 / 纯编号
        if re.fullmatch(r'[\s\d\.\-—、()（）]+', s):
            continue
        # 表格分隔线
        if re.fullmatch(r'[\s\|:\-—]+', s):
            continue
        keep.append(line)
    return '\n'.join(keep)


def human_voice_check(text, targets=None):
    """按 rules.json human_voice 体检「像不像人写的」——只提示，不阻断。

    ## 为什么要单独一层、而不是并进 style_rules

    `style_rules` 的目标是**防错**（别编造、别超篇幅、别用套话）；
    本层的目标是**防机器腔**。两者判据不同：前者看「有没有不该有的」，
    后者看**分布的离散度**——机器文本的破绽不是用错了词，
    而是整篇过于均匀（句长均匀、起笔雷同、收尾同调）。

    阈值全部来自 Zone C 91 份已批方案实测（1471 万字 / 5.07 万句）：
      句长标准差 38.1 ｜「首先」类连接词 <0.01‰ ｜ 句尾 2 字最高重复 3.24%
    所以「人味」在这里是可量化、可回归的，不是玄学。

    返回 (problems, warnings, findings)；当前全部落 warning，不阻断交付。
    """
    HV = RULES.get('human_voice', {})
    if not HV:
        return [], [], []
    problems, warnings, findings = [], [], []

    body = B.strip_frontmatter(text)
    body = re.sub(r'```.*?```', ' ', body, flags=re.S)
    body = re.sub(r'^\s*>.*$', '', body, flags=re.M)
    body = re.sub(r'^\s*\|.*$', '', body, flags=re.M)      # 表格行不算行文
    body = re.sub(r'^\s*#{1,6}\s.*$', '', body, flags=re.M)  # 标题不算
    body = _strip_non_prose(body)                          # 目录/点线/编号条目
    n_chars = max(1, B.count_chars(body))
    sents = _sentences(body)
    paras = [p for p in _paras(body) if not _is_list_item(p) and len(p) >= 20]

    # ---- 1. 段落起笔单调 ----
    # 单节通常只有 4~8 段（实测真人方案中位段落数为 5），
    # 所以不能像全库统计那样要求 ≥8 段才判——那样单节回检永远不触发。
    # 改为：样本少时用「绝对次数」判据，样本多时用「占比」判据。
    po = HV.get('paragraph_opening', {})
    if len(paras) >= 4:
        for k, key, abs_key in ((2, 'max_ratio_of_same_first2', 'abs_same_first2'),
                                (4, 'max_ratio_of_same_first4', 'abs_same_first4')):
            cap = po.get(key)
            if not cap:
                continue
            heads = [p[:k] for p in paras if re.match(r'^[\u4e00-\u9fff]', p)]
            if len(heads) < 4:
                continue
            cnt = {}
            for h in heads:
                cnt[h] = cnt.get(h, 0) + 1
            top = max(cnt.values())
            ratio = top / float(len(heads))
            abs_cap = po.get(abs_key, 3)
            # 段落少（<8）时看「同一开头的绝对次数」。
            # 但绝对阈值也要随段落数收缩：只有 6 段时「4 段同头」这个门槛
            # 永远达不到，而 3/6 = 50% 其实已经非常单调。
            # 因此取 min(声明值, 段落数的一半向上取整)，使小样本也能判出来。
            if len(heads) < 8:
                abs_cap = min(abs_cap, max(2, (len(heads) + 1) // 2))
            fired = (ratio > cap) if len(heads) >= 8 else (top >= abs_cap)
            if fired:
                item = ('段落起笔单调：%d/%d 段以「%s」起笔（%.1f%%，上限 %.0f%%）'
                        '——在「陈述／判断／条件／指代／时空」之间轮转'
                        % (top, len(heads), max(cnt, key=cnt.get), 100 * ratio, 100 * cap))
                findings.append(item)
                warnings.append('C5 去机械感：' + item)
                break

    # ---- 2. 标点密度 ----
    # 双重样本门槛（都来自实测踩坑）：
    #   ① 字数 ≥1500——片段太短时每千字密度不稳定；
    #   ② 句数 ≥20——只有一两句的片段（多为公式推导、清单、表头残留）
    #      谈不上"标点风格"，拿它判密度必然误报「标点偏少」。
    pu = HV.get('punctuation', {}).get('per_1000_chars', {})
    if n_chars >= 1500 and len(sents) >= 20 and pu:
        for mark, lim in pu.items():
            c = body.count(mark)
            dens = 1000.0 * c / n_chars
            lo, hi = lim.get('min'), lim.get('max')
            if hi is not None and dens > hi:
                what = '滥用' if mark in ('——', '（') else '过密'
                item = '标点%s：%s %.2f‰（上限 %.1f‰）' % (what, mark, dens, hi)
                findings.append(item)
                warnings.append('C5 去机械感：' + item)
            elif lo is not None and dens < lo:
                item = '标点偏少：%s %.2f‰（低于真人 %.1f‰）' % (mark, dens, lo)
                findings.append(item)
                warnings.append('C5 去机械感：' + item)

    # ---- 3. 节奏（句长离散度） ----
    rh = HV.get('rhythm', {})
    if len(sents) >= 20:
        import statistics
        lens = [len(s) for s in sents]
        sd = statistics.pstdev(lens)
        sd_min = rh.get('stddev_min')
        if sd_min and sd < float(sd_min):
            item = ('节奏均匀：句长标准差 %.1f（真人中位 60.3，下限 %s）'
                    '——全篇句子长度相近＝机器腔' % (sd, sd_min))
            findings.append(item)
            warnings.append('C5 去机械感：' + item)
        s_def = rh.get('short_def_le', 25)
        s_min = rh.get('short_ratio_min')
        if s_min:
            short = sum(1 for L in lens if L <= s_def)
            ratio = short / float(len(lens))
            if ratio < s_min:
                item = ('缺少短句：≤%d 字的句子仅占 %.1f%%（真人 ≥%.0f%%）'
                        '——没有短句就没有停顿，读起来是机器节奏' % (s_def, 100 * ratio, 100 * s_min))
                findings.append(item)
                warnings.append('C5 去机械感：' + item)
        l_def = rh.get('long_def_ge', 100)
        l_max = rh.get('long_ratio_max')
        if l_max:
            longn = sum(1 for L in lens if L >= l_def)
            ratio = longn / float(len(lens))
            if ratio > l_max:
                item = '长句过多：≥%d 字的句子占 %.1f%%（上限 %.0f%%）' % (l_def, 100 * ratio, 100 * l_max)
                findings.append(item)
                warnings.append('C5 去机械感：' + item)

    # ---- 4. 连接词 ----
    cn = HV.get('connectors', {})
    banned_c = cn.get('banned') or []
    if banned_c and n_chars >= 300:
        hit = {w: body.count(w) for w in banned_c if body.count(w)}
        if hit:
            item = ('序号式连接词：%s——实测真人密度 <0.01‰，'
                    '章节层次靠标题与段落体现，不要用「首先…其次…最后」串段落'
                    % '、'.join('%s×%d' % (k, v) for k, v in sorted(hit.items(), key=lambda x: -x[1])))
            findings.append(item)
            warnings.append('C5 去机械感：' + item)
    sparse = cn.get('sparse') or []
    cap_s = cn.get('sparse_per_1000_max')
    if sparse and cap_s and n_chars >= 500:
        tot = sum(body.count(w) for w in sparse)
        dens = 1000.0 * tot / n_chars
        if dens > cap_s:
            item = '连接词滥用：%s 等合计 %.2f‰（上限 %.1f‰）' % ('、'.join(sparse[:4]), dens, cap_s)
            findings.append(item)
            warnings.append('C5 去机械感：' + item)

    # ---- 5. 收尾套式 ----
    pc = HV.get('paragraph_closing', {})
    for f in (pc.get('forbid_summary_formula') or []):
        c = body.count(f)
        if c:
            item = '结尾套式：「%s」出现 %d 次——不要每节都把内容点复述一遍' % (f, c)
            findings.append(item)
            warnings.append('C5 去机械感：' + item)

    # ---- 6. 句式同构 ----
    # 单节文本短（一句节 15~30 句），占比判据在小样本上不敏感：
    # 4/22 = 18% 已明显是「通过…，确定…」的骨架复用，却低于 30% 上限。
    # 因此同样补一条**绝对次数**判据：同一骨架出现 ≥4 次即提示。
    ss = HV.get('sentence_shape', {})
    frames = ss.get('frames') or []
    cap_f = ss.get('max_ratio_of_same_frame')
    abs_f = ss.get('abs_same_frame', 4)
    if frames and len(sents) >= 10:
        worst, worst_n = None, 0
        for fr in frames:
            n = len(re.findall(fr, body))
            if n > worst_n:
                worst, worst_n = fr, n
        ratio = worst_n / float(len(sents))
        fired = False
        if cap_f and ratio > cap_f:
            fired = True
        elif worst_n >= abs_f:
            fired = True
        if worst and fired:
            item = ('句式同构：约 %d/%d 句（%.0f%%，上限 %.0f%%）套用同一骨架'
                    '——机器倾向反复用同一主谓宾模板，换个起笔方式会自然很多'
                    % (worst_n, len(sents), 100 * ratio, 100 * (cap_f or 0)))
            findings.append(item)
            warnings.append('C5 去机械感：' + item)

    return problems, warnings, findings


def style_check(text, targets=None):
    """按 rules.json style_rules 体检表达质量与论证深度（只提示，不替使用者改稿）。"""
    SR = RULES.get('style_rules', {})
    DP = SR.get('depth_patterns', {})
    SEV = SR.get('style_severity', {})
    body = B.strip_frontmatter(text)
    body = re.sub(r'```.*?```', ' ', body, flags=re.S)
    # 引用块（> …）多为写作提示/校核说明，不是方案正文，排除后统计更准
    body = re.sub(r'^\s*>.*$', '', body, flags=re.M)
    n_chars = max(1, B.count_chars(body))
    problems, warnings, findings = [], [], []

    # 1 空话套话与自指密度
    banned = SR.get('banned_phrases', {})
    hits = {}
    for group, phrases in banned.items():
        if not isinstance(phrases, list):
            continue
        for p in phrases:
            c = body.count(p)
            if c:
                hits[p] = c
    total_banned = sum(hits.values())
    for key, lim in (SR.get('density_limits') or {}).items():
        if not isinstance(lim, dict):
            continue
        if key == '空话套话':
            c = total_banned
            cap = lim.get('total_max')
        else:
            variants = key.split('/')
            c = sum(body.count(v) for v in variants)
            # 每千字上限优先；没有该键时退回绝对次数上限（"综上所述"只声明了 total_max，
            # 以前这里只读 per_1000_chars_max，导致该条上限**永不生效**）。
            cap = lim.get('per_1000_chars_max')
            if cap:
                cap = cap * n_chars / 1000.0
            elif lim.get('total_max') is not None:
                cap = lim['total_max']
        if cap is not None and c > cap:
            if key == '空话套话':
                item = '空话套话合计 %d 次（上限 %s）' % (c, lim.get('total_max'))
            elif lim.get('per_1000_chars_max'):
                item = '「%s」合计出现 %d 次（上限约 %.1f 次）' % (key.replace('/', '／'), c, cap)
            else:
                item = '「%s」合计出现 %d 次（上限 %s 次）' % (key.replace('/', '／'), c, lim.get('total_max'))
            findings.append(item)
            (problems if SEV.get('density_over' if key != '空话套话' else 'banned_total_over') == 'problem'
             else warnings).append('C5 表达密度超标：' + item)
    if hits:
        findings.append('空话套话明细：' + '、'.join('%s×%d' % (k, v) for k, v in
                                                sorted(hits.items(), key=lambda x: -x[1])[:8]))

    # 2 句长与句式重复
    sents = _sentences(body)
    srule = SR.get('sentence_rules', {})
    max_len = srule.get('max_chars_without_punctuation', 120)
    longs = [s for s in sents if len(s) > max_len]
    if longs:
        warnings.append('C5 超长句 %d 句（>%d 字），如：%s…' % (len(longs), max_len, longs[0][:30]))
    paras = _paras(body)
    for p in paras:
        ps = [s for s in _sentences(p) if len(s) >= 4]
        if len(ps) >= 4:
            openings = {}
            for s in ps:
                openings[s[:4]] = openings.get(s[:4], 0) + 1
            top = max(openings.values())
            if top / len(ps) > srule.get('max_ratio_of_identical_openings', 0.25):
                warnings.append('C5 句式重复：某段 %d/%d 句以「%s」开头'
                                % (top, len(ps), max(openings, key=openings.get)))
                break
    # 句长变化（rules.json style_rules.sentence_rules.min_sentence_length_stddev）：
    # 全篇一个句长＝模板腔。句子太少时不判（样本不足会误报）。
    std_min = srule.get('min_sentence_length_stddev')
    if std_min and len(sents) >= 20:
        import statistics
        sd = statistics.pstdev([len(s) for s in sents])
        if sd < float(std_min):
            item = '全文句长标准差 %.1f 字（应 > %s 字）——全篇同长句＝模板腔' % (sd, std_min)
            findings.append(item)
            warnings.append('C5 句长缺少变化：' + item)

    # 3 段落长度（列表项天然短，不按孤句段落判）
    prule = SR.get('paragraph_rules', {})
    prose = [p for p in paras if not _is_list_item(p)]
    too_long = [p for p in prose if len(p) > prule.get('max_chars', 900)]
    too_short = [p for p in prose if 0 < len(p) < prule.get('min_chars', 60)]
    if too_long:
        warnings.append('C5 超长段落 %d 段（>%d 字），建议拆分' % (len(too_long), prule.get('max_chars', 900)))
    if too_short:
        warnings.append('C5 孤句段落 %d 段（<%d 字），建议合并或补论证' % (len(too_short), prule.get('min_chars', 60)))

    # 4 术语一致性（按独立出现判断，避免子串误报）
    tc = SR.get('term_consistency', {})
    for pair in tc.get('pairs_forbidden_both', []):
        counts = {w: _standalone_count(body, w, pair) for w in pair}
        present = [w for w, c in counts.items() if c > 0]
        if len(present) > 1:
            problems.append('C5 术语不统一：%s 同时独立出现（%s）——同一事物全文只用一个名称'
                            % ('／'.join(present),
                               '、'.join('%s×%d' % (w, counts[w]) for w in present)))

    # 5 深度（按节点）
    LBr = RULES.get('length_budget', {})
    ev_re = re.compile(DP.get('evidence_ref', r'《[^》]{2,60}》'))
    qf_re = re.compile(DP.get('quantified_fact', r'\d+(?:\.\d+)?\s*(?:hm²|m³|万元|%)'))
    concl = DP.get('conclusion_markers', ['符合', '结论'])
    mech = DP.get('mechanism_markers', ['由于', '导致'])
    depth_rows = []
    for cid in (targets or []):
        seg = node_text(text, cid)
        if not seg:
            continue
        dep = B.depth_requirements(cid)
        ev = len(ev_re.findall(seg))
        qf = len(qf_re.findall(seg))
        has_c = any(m in seg for m in concl)
        has_m = any(m in seg for m in mech)
        bad = []
        if ev < dep.get('evidence_refs_min', 1):
            bad.append('依据引用 %d<%d' % (ev, dep.get('evidence_refs_min', 1)))
        if qf < dep.get('quantified_facts_min', 3):
            bad.append('量化数据 %d<%d' % (qf, dep.get('quantified_facts_min', 3)))
        if dep.get('require_conclusion') and not has_c:
            bad.append('缺结论句')
        if dep.get('require_mechanism') and not has_m:
            bad.append('缺机理说明')
        depth_rows.append({'chapter_id': cid, 'evidence_refs': ev, 'quantified_facts': qf,
                           'has_conclusion': has_c, 'has_mechanism': has_m,
                           'category': dep.get('category'), 'shortfalls': bad})
    depth_short = [d for d in depth_rows if d['shortfalls']]
    if depth_short:
        warnings.append('C5 深度要素不足 %d 节：%s' % (len(depth_short), '、'.join(
            '%s[%s]' % (d['chapter_id'], d['shortfalls'][0]) for d in depth_short[:5])))

    return {
        'checked': True,
        'chars': n_chars,
        'banned_hits': hits,
        'findings': findings,
        'depth': depth_rows,
        'depth_short_count': len(depth_short),
        'problems': problems, 'warnings': warnings,
        'rule': 'rules.json style_rules（判据与阈值均在本文件，可自行调整）',
        'note': '文风体检只提示，不自动改写；请据此人工打磨（既不机械，也不失真）',
    }


def node_text(text, cid):
    """取出稿件中属于该节点的正文（含该节点号的所有标题段合并，直到下一个标题）。"""
    heads = list(re.finditer(r'^#{1,6}\s*(.+?)\s*$', text, re.M))
    segs = []
    for i, m in enumerate(heads):
        if cid not in m.group(1):
            continue
        nxt = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        seg = text[m.end():nxt]
        if seg.strip():
            segs.append(seg)
    return '\n'.join(segs)


# ---------- C6 篇幅台账 ----------
def length_check(texts, targets=None):
    """按 rules.json length_budget 核对实际篇幅，给出全书进度、章级与逐节判定。"""
    chars_by_node = {}
    for _d, t in texts.items():
        for cid, n in B.split_by_node(t).items():
            chars_by_node[cid] = chars_by_node.get(cid, 0) + n
    # 章级汇总：该章自身标题下的正文 + 其所有子节点的正文
    per_chapter = {}
    for cid, n in chars_by_node.items():
        top = cid.split('.')[0]
        if top.isdigit():
            per_chapter[top] = per_chapter.get(top, 0) + n
    LB = RULES.get('length_budget', {})
    tol = LB.get('tolerance', {})
    lo_r, hi_r = tol.get('under_ratio', 0.8), tol.get('over_ratio', 1.25)
    chapter_verdicts = []
    for top in sorted(per_chapter, key=int):
        cb = B.chapter_budget(top)
        actual = per_chapter[top]
        if not cb['target']:
            continue
        lo, hi = cb['target'] * lo_r, cb['target'] * hi_r
        chapter_verdicts.append({
            'chapter': top, 'actual': actual, 'target': cb['target'],
            'min': int(lo), 'max': int(hi), 'nodes': len(cb['nodes']),
            'status': '偏少' if actual < lo else ('超标' if actual > hi else '达标'),
            'delta_ratio': round((actual - cb['target']) / cb['target'], 3),
        })

    # 逐节判定：章级标题属结构骨架，不进节点表
    verdicts = [B.verdict(n, cid) for cid, n in sorted(chars_by_node.items())
                if cid and '.' in cid and B.node_budget(cid)]
    pr = B.progress(chars_by_node)
    book = B.book_budget()
    return {
        'checked': True,
        'chapter_verdicts': chapter_verdicts,
        'node_verdicts': verdicts,
        'unattributed_chars': pr['unattributed_chars'],
        'book': {
            'page_target': book['page_target'],
            'book_target_chars': book['book_target_chars'],
            'written_chars': pr['written_chars'],
            'book_completion_ratio': pr['book_completion_ratio'],
            'estimated_pages_to_date': pr['estimated_pages_so_far'],
            'stage_target_chars': pr['stage_target_chars'],
            'stage_delta_ratio': pr['stage_delta_ratio'],
            'written_nodes': pr['written_nodes'],
            'budgeted_nodes': pr['budgeted_nodes'],
        },
        'rule': 'rules.json length_budget（全书 %d–%d 页 → 正文 %s 字，章间按 Zone C 实测占比、章内按节点权重）'
                % (book['page_target']['min'], book['page_target']['max'], book['book_target_chars']),
        'note': '偏少/超标只说明篇幅与预算的偏差，不等于质量好坏；但累计偏差过大时必须回头调结构或补深度。',
    }


# ---------- C7 台账核对 ----------
def strip_non_body(text):
    """剥掉稿件里**不是方案正文**的部分，供台账/数字核对使用。

    稿子里常见三类非正文内容，它们会污染事实抽取（例如把「要求覆盖…防治分区…」
    当成「防治分区」的取值）：
      1. 引用块（`>` 开头）：写作指导、闸门结果、参考资料摘要；
      2. 代码块（``` 围栏）：指令包片段、命令；
      3. 模板/指导用语行（含「内容点」「要求覆盖」「Zone A/B/C」「闸门」等标记）。
    """
    out, fence = [], False
    marker = re.compile(r'内容点|要求覆盖|Zone [ABC]|闸门|写作指导|指令包|待写：|请据此|校核|'
                        r'^\s*[-*]?\s*(依据|规则|口径)[:：]')
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith('```'):
            fence = not fence
            continue
        if fence or s.startswith('>'):
            continue
        if marker.search(s):
            continue
        out.append(ln)
    return '\n'.join(out)


def ledger_check(ledger_path, texts):
    if not os.path.exists(ledger_path):
        return {'checked': False, 'note': '台账文件不存在：%s' % ledger_path}
    try:
        import ledger as L
        led = L.load(ledger_path)
    except Exception as e:
        return {'checked': False, 'note': '台账读取失败：%s' % e}
    if not led.get('entries'):
        return {'checked': False, 'note': '台账为空（尚未登记已定事实）'}
    conflicts, ok, absent = [], [], []
    for d, t in texts.items():
        r = L.check_against_ledger(led, strip_non_body(t), os.path.basename(d))
        for c in r['conflict']:
            c['draft'] = os.path.basename(d)
            conflicts.append(c)
        ok.extend(r['ok'])
        absent.extend(r['absent'])
    return {
        'checked': True,
        'ledger_file': ledger_path,
        'entries': len(led['entries']),
        'conflicts': conflicts,
        'consistent': ok,
        'not_mentioned': [a['key'] for a in absent],
        'rule': 'rules.json ledger_rules：台账为全书权威值，稿件与之不一致即报冲突（不自行择一）；'
                '引用块/代码块/写作指导行不计入核对',
    }


def check(drafts, chapter=None, province=None, city=None, report_form=False,
          ledger_path=None, do_style=True, do_budget=True):
    gate = G.run(chapter, province, city, report_form=report_form)
    targets = gate.get('expanded_nodes') or []     # 章节号无效时该键不存在（由调用方按输入错误退出）
    texts = {}
    for d in drafts:
        # utf-8-sig：兼容带 BOM 的稿件（Windows 工具常写出 BOM，否则首行标题匹配失败）。
        # 文件不存在/是目录/读不动 → G.read_text 给人话并停止，不再抛 traceback。
        texts[d] = G.read_text(d, '稿件')
    joined = '\n'.join(texts.values())
    text_flat = re.sub(r'\s+', '', joined)

    result = {
        'drafts': drafts,
        'chapter': '报告表' if report_form else chapter,
        'report_form': report_form,
        'gate_blocked': gate['blocked'],
        'gate_block_reason': gate.get('block_reason'),
        'gate_provisional': gate.get('provisional'),
        # 章节号不在模板里 → 结论不可信，调用方按输入错误处理（退出码 2）
        'invalid_chapter': gate.get('invalid_chapter', False),
        'snapshot_warning': G.SNAPSHOT_WARNING,
        'targets': targets,
    }

    # C1 覆盖（报告表无章节内容点，跳过）
    if report_form:
        result['coverage'] = []
        result['coverage_note'] = '报告表无章节内容点；覆盖检查以两区块字段逐字段人工核对为准（骨架见 write_chapter --report-form）'
    else:
        result['coverage'] = coverage(targets, text_flat)
        result['coverage_note'] = ('内容点命中为关键词级匹配（正文同义改写可判 miss）；'
                                   'missed 项须人工确认是否真的未覆盖，不得据此直接补写')

    # C2 占位符
    ph = [(m.group(1), m.group(2).strip(), src, text[:src_pos].count('\n') + 1)
          for src, text in texts.items()
          for m in PH_RE.finditer(text)
          for src_pos in [m.start()]]
    result['placeholders'] = {
        'total': len(ph),
        'unique': sorted({'%s：%s' % (k, v) for k, v, _, _ in ph}),
        'note': '占位符是技能设计内的显式待办（铁律3：缺数据不猜数）；定稿前必须全部回填或人工确认',
    }

    # C3 数字一致性
    all_facts = [(os.path.basename(d), f) for d, t in texts.items() for f in extract_facts(t)]
    result['facts_extracted'] = len(all_facts)
    result['number_conflicts'] = find_conflicts(all_facts)
    result['number_note'] = ('仅机械比对同名同单位数值；单位口径（万m³ vs m³、hm² vs 亩）不一致'
                             '与中英文数字（"四个"vs"3 个"）无法自动判定，须人工复核')
    hard_conflicts = [c for c in result['number_conflicts'] if not c['downgraded']]

    # C4 标题
    if not report_form:
        result['headings'] = heading_check(targets, joined)

    # C5 文风与深度（并跑「去机械感」层）
    if do_style:
        result['style'] = style_check(joined, targets)
        # 去机械感：独立一层，结果并入 style 的 warnings/findings，
        # 使既有调用方（check_plan、渲染器）无需改动即可看到这些提示。
        hvp, hvw, hvf = human_voice_check(joined, targets)
        if hvf or hvw or hvp:
            st = result['style']
            st.setdefault('warnings', []).extend(hvw)
            st.setdefault('problems', []).extend(hvp)
            st.setdefault('findings', []).extend(hvf)
            st['human_voice'] = {'checked': True, 'hits': len(hvf)}
        else:
            result['style']['human_voice'] = {'checked': True, 'hits': 0}
    else:
        result['style'] = {'checked': False, 'note': '未执行（--no-style）'}

    # C6 篇幅台账
    if do_budget:
        result['length'] = length_check(texts, targets)
    else:
        result['length'] = {'checked': False, 'note': '未执行（--no-budget）'}

    # C7 台账核对（跨章口径）
    if ledger_path:
        result['ledger'] = ledger_check(ledger_path, texts)
    else:
        result['ledger'] = {'checked': False,
                            'note': '未指定 --ledger：跨章口径冲突需靠 check_plan 全书终检发现'}

    # 结论
    problems, warnings = [], []
    if not report_form:
        missed_any = any(r['missed'] for r in result['coverage'])
        if missed_any:
            problems.append('C1 存在未命中的内容点（含同义改写可能，需人工确认）')
        if result['headings']['missing_heading_for']:
            problems.append('C4 缺少章节标题行：%s' % '、'.join(result['headings']['missing_heading_for']))
    if hard_conflicts:
        problems.append('C3 数字一致性疑似冲突 %d 组（另有 %d 组已降级为分区数量提示）'
                        % (len(hard_conflicts), len(result['number_conflicts']) - len(hard_conflicts)))
    st = result.get('style') or {}
    if st.get('checked'):
        problems.extend(st.get('problems') or [])
        warnings.extend(st.get('warnings') or [])
    ln = result.get('length') or {}
    if ln.get('checked'):
        over = [v for v in ln.get('chapter_verdicts', []) + ln.get('node_verdicts', []) if v['status'] == '超标']
        under = [v for v in ln.get('node_verdicts', []) if v['status'] == '偏少']
        if over:
            warnings.append('C6 篇幅超标 %d 处：%s' % (len(over), '、'.join(
                '%s(%d>%d)' % (v.get('chapter_id') or ('第%s章' % v['chapter']), v['actual'], v['max'])
                for v in over[:6])))
        if under:
            warnings.append('C6 篇幅偏少 %d 节：%s' % (len(under), '、'.join(
                '%s(%d<%d)' % (v['chapter_id'], v['actual'], v['min']) for v in under[:6])))
        bk = ln.get('book') or {}
        if bk.get('estimated_pages_to_date') is not None:
            pg = bk['estimated_pages_to_date']
            warnings.append('C6 全书进度：已写 %s 字 ≈ %s 页（目标 %s–%s 页，完成 %s%%）'
                            % (bk.get('written_chars'), pg, bk.get('page_target', {}).get('min'),
                               bk.get('page_target', {}).get('max'),
                               round((bk.get('book_completion_ratio') or 0) * 100)))
    lg = result.get('ledger') or {}
    if lg.get('checked') and lg.get('conflicts'):
        problems.append('C7 与台账冲突 %d 处（跨章口径打架）：%s' % (
            len(lg['conflicts']),
            '、'.join('%s 台账%s≠稿件%s' % (c['key'], c['ledger_value'], '/'.join(map(str, c['draft_values'])))
                      for c in lg['conflicts'][:5])))
    result['problems'] = problems
    result['warnings'] = warnings
    result['verdict'] = ('需人工处理：%s' % '；'.join(problems)) if problems else \
        ('机械检查未发现问题（不等于内容正确；合规性仍以 Zone A 与人工审查为准）'
         + ('；另有提示 %d 条' % len(warnings) if warnings else ''))
    return result


def render(r):
    L = []
    L.append('# 稿后校核报告')
    L.append('')
    L.append('- 稿件：%s' % '；'.join(r['drafts']))
    L.append('- 目标：%s（展开节点：%s）' % (r['chapter'], '、'.join(r['targets']) if r['targets'] else '—'))
    if r['gate_blocked']:
        L.append('- ⚠ **合规闸门当前为 blocked**（%s）——该稿不应存在，请先解决依据问题' % r['gate_block_reason'])
    if r['gate_provisional']:
        L.append('- ⚠ 闸门 provisional=true：依据含「待确认」字段，**送审前必须人工核实文号与施行日期**')
    if r.get('snapshot_warning'):
        L.append('- ⚠ %s' % r['snapshot_warning'])
    L.append('')
    if r.get('coverage'):
        L.append('## C1 模板覆盖')
        L.append('')
        for c in r['coverage']:
            status = '✅ 全部命中' if not c['missed'] else '⚠ 未命中 %d/%d' % (len(c['missed']), c['total'])
            L.append('**%s %s** — %s' % (c['chapter_id'], c['title'], status))
            for m in c['missed']:
                L.append('  - 未命中：`%s`' % m)
            if c['conditional']:
                L.append('  - 条件节点：动笔前应确认的情形——%s' % '；'.join(c['conditional_variants'] or ['见模板']))
            L.append('')
        L.append('> 口径：%s' % r['coverage_note'])
        L.append('')
    if not r.get('report_form'):
        h = r.get('headings', {})
        if h.get('missing_heading_for'):
            L.append('## C4 章节标题')
            L.append('')
            L.append('⚠ 以下节点在稿件中没有对应的标题行：%s' % '、'.join(h['missing_heading_for']))
            L.append('')
    ph = r['placeholders']
    L.append('## C2 占位符清点')
    L.append('')
    L.append('共 %d 处（%d 个去重事项）。%s' % (ph['total'], len(ph['unique']), ph['note']))
    for u in ph['unique'][:30]:
        L.append('- %s' % u)
    if len(ph['unique']) > 30:
        L.append('- …（其余 %d 项见 JSON 输出）' % (len(ph['unique']) - 30))
    L.append('')
    L.append('## C3 数字一致性')
    L.append('')
    L.append('抽取数值事实 %d 条。%s' % (r['facts_extracted'], r['number_note']))
    if not r['number_conflicts']:
        L.append('')
        L.append('✅ 未发现同名同单位数值冲突。')
    for c in r['number_conflicts']:
        L.append('')
        if c['downgraded']:
            L.append('ℹ **「%s」（%s）出现 %d 个不同值：%s**——%s'
                     % (c['key'], ('单位 ' + c['unit']) if c['unit'] else '无单位',
                        len(c['values']), '、'.join(str(v) for v in c['values']), c['note']))
        else:
            L.append('⚠ **「%s」（%s）出现 %d 个不同值：%s**'
                     % (c['key'], ('单位 ' + c['unit']) if c['unit'] else '无单位',
                        len(c['values']), '、'.join(str(v) for v in c['values'])))
        for o in c['occurrences']:
            L.append('  - [%s L%d] %s = %s　| %s' % (o['source'], o['line'], c['key'], o['value'], o['context']))
    L.append('')
    st = r.get('style') or {}
    if st.get('checked'):
        L.append('## C5 文风与深度')
        L.append('')
        L.append('正文口径字数 %s。%s' % (st['chars'], st['note']))
        if st.get('findings'):
            L.append('')
            for f in st['findings']:
                L.append('- %s' % f)
        ds = st.get('depth_short_count') or 0
        L.append('')
        if ds:
            L.append('⚠ 深度要素不足 %d 节：' % ds)
            for d in st['depth']:
                if d['shortfalls']:
                    L.append('  - %s（%s）：%s' % (d['chapter_id'], d.get('category') or '通用',
                                                 '；'.join(d['shortfalls'])))
        else:
            L.append('✅ 各节深度要素（依据引用/量化数据/结论句/机理说明）达标。')
        if st.get('problems'):
            L.append('')
            for p in st['problems']:
                L.append('- ❌ %s' % p)
        L.append('')
    ln = r.get('length') or {}
    if ln.get('checked'):
        L.append('## C6 篇幅台账')
        L.append('')
        bk = ln['book']
        L.append('全书：已写 %s 字 ≈ **%s 页**（目标 %s–%s 页，正文目标 %s 字）——完成度 %s%%'
                 % (bk['written_chars'], bk['estimated_pages_to_date'],
                    bk['page_target']['min'], bk['page_target']['max'], bk['book_target_chars'],
                    round((bk['book_completion_ratio'] or 0) * 100)))
        L.append('')
        L.append('已写节点 %s/%s；本节段合计预算 %s 字，实际偏差 %s'
                 % (bk['written_nodes'], bk['budgeted_nodes'], bk['stage_target_chars'],
                    ('%+.0f%%' % (bk['stage_delta_ratio'] * 100)) if bk['stage_delta_ratio'] is not None else '—'))
        if ln.get('unattributed_chars'):
            L.append('')
            L.append('ℹ 未归入任何节点的字数 %s（标题前的说明文字或自定义小节），不计入篇幅判定'
                     % ln['unattributed_chars'])
        L.append('')
        cv = ln.get('chapter_verdicts') or []
        if cv:
            L.append('**章级进度**（该章预算按 Zone C 实测篇幅占比）')
            L.append('')
            L.append('| 章 | 已写字数 | 该章目标 | 达标区间 | 判定 | 偏差 |')
            L.append('|---|---|---|---|---|---|')
            for v in cv:
                L.append('| 第%s章 | %s | %s | %s–%s | %s | %+.0f%% |'
                         % (v['chapter'], v['actual'], v['target'], v['min'], v['max'],
                            v['status'], v['delta_ratio'] * 100))
            L.append('')
        if ln['node_verdicts']:
            L.append('**逐节进度**')
            L.append('')
            L.append('| 节点 | 实际字数 | 目标 | 区间 | 判定 |')
            L.append('|---|---|---|---|---|')
            for v in ln['node_verdicts']:
                L.append('| %s | %s | %s | %s–%s | %s |'
                         % (v['chapter_id'], v['actual'], v.get('target', '—'),
                            v.get('min', '—'), v.get('max', '—'), v['status']))
            L.append('')
        L.append('> %s' % ln['rule'])
        L.append('>')
        L.append('> %s' % ln['note'])
        L.append('')
    lg = r.get('ledger') or {}
    if lg.get('checked'):
        L.append('## C7 台账核对（跨章口径）')
        L.append('')
        L.append('- 台账：%s（%d 条已定事实）' % (lg.get('ledger_file'), lg.get('entries', 0)))
        L.append('- 一致：%d 项；本稿未提及：%d 项' % (len(lg.get('consistent') or []),
                                                len(lg.get('not_mentioned') or [])))
        if lg.get('conflicts'):
            L.append('')
            for c in lg['conflicts']:
                L.append('❌ **%s**：台账 %s%s（登记于 %s）≠ 本稿 %s'
                         % (c['key'], c['ledger_value'], c.get('ledger_unit') or '',
                            c.get('ledger_chapter') or '—', '、'.join(map(str, c['draft_values']))))
                for p in c.get('draft_provenance', [])[:3]:
                    L.append('  - [L%s] %s' % (p['line'], p['snippet']))
        else:
            L.append('')
            L.append('✅ 未发现与台账冲突的口径。')
        L.append('')
    elif r.get('ledger', {}).get('note'):
        L.append('## C7 台账核对')
        L.append('')
        L.append('ℹ %s' % r['ledger']['note'])
        L.append('')
    L.append('## 结论')
    L.append('')
    L.append(r['verdict'])
    if r.get('warnings'):
        L.append('')
        L.append('### 提示（不影响退出码，需人工判断）')
        L.append('')
        for w in r['warnings']:
            L.append('- ⚠ %s' % w)
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser(description='稿后校核器：覆盖 / 占位符 / 数字一致性 / 标题 / 文风深度 / 篇幅 / 台账')
    ap.add_argument('--draft', action='append', required=True, help='稿件 md 路径，可重复传多个做联合比对')
    ap.add_argument('--chapter', help='目标章节号')
    ap.add_argument('--province', default=None)
    ap.add_argument('--city', default=None)
    ap.add_argument('--report-form', action='store_true', help='报告表分支')
    ap.add_argument('--ledger', default=None, help='事实台账 json：核对跨章口径（C7）')
    ap.add_argument('--no-style', action='store_true', help='跳过 C5 文风与深度体检')
    ap.add_argument('--no-budget', action='store_true', help='跳过 C6 篇幅台账')
    ap.add_argument('--json-only', action='store_true')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    if not a.report_form and not a.chapter:
        ap.error('需要 --chapter 或 --report-form')
    r = check(a.draft, a.chapter, a.province, a.city, report_form=a.report_form,
              ledger_path=a.ledger, do_style=not a.no_style, do_budget=not a.no_budget)
    if r.get('invalid_chapter'):
        sys.stderr.write('❌ 章节号 %r 不在模板中，未产出校核结论。\n' % a.chapter)
        raise SystemExit(2)
    if a.out:
        G.write_text(a.out, render(r), '校核报告')
        print('已写出校核报告:', a.out)
    print(json.dumps(r, ensure_ascii=False, indent=1) if a.json_only else render(r))
    sys.exit(2 if 'error' in r else (1 if r.get('problems') else 0))


if __name__ == '__main__':
    main()

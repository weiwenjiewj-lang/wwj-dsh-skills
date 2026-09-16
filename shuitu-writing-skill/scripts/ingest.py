#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""资料接入层：把使用者喂入的原始资料变成唯一的结构化数据源（项目数据包）。

它解决的问题：编制方案时项目资料散在可研、初设、地勘、批复、气象水文等多份文件里，
人工抄录既慢又容易串值。本工具按 rules.json 的 data_package.fields 字段字典逐字段抽取，
**每个取值都带出处**（文件 + 行号 + 原文片段），并对同一字段的多来源取值冲突直接列清单。

铁律遵守：
  · 不编造——抽不到就是「待填」，绝不填默认值或经验值；
  · 单位不静默换算——与声明单位不一致时记 unit_mismatch 待人工确认；
  · 冲突不自行择一——两个取值都保留，交人工裁决。

用法:
    python ingest.py --source 可研.docx --source 地勘.pdf --out 项目数据包.json
    python ingest.py --source 资料目录 --out 项目数据包.json --list-out 补数清单.md
    python ingest.py --validate 项目数据包.json          # 复核既有数据包
"""
import argparse
import io
import json
import os
import re
import sys
import zipfile
import zlib

sys.dont_write_bytecode = True   # 技能包不留 __pycache__（避免缓存掩盖规则改动）

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

RULES = G.RULES
DP = RULES.get('data_package', {})
FIELDS = DP.get('fields', [])
PKG_FIELDS = {f['key']: f for f in FIELDS}
# 行号前缀剥离规则（阈值只读 rules.json，代码不自带）
FE_LINENO = (RULES.get('fact_extraction', {}) or {}).get('line_number_prefix', {}) or {}

# 受理格式只读 rules.json（原来代码里另写一份，与 JSON 声明不一致）
_SF = DP.get('source_formats', {}) or {}
TEXT_EXT = set(_SF.get('native') or ['.md', '.markdown', '.txt', '.csv', '.json', '.html', '.htm'])
OFFICE_EXT = set(_SF.get('office') or ['.docx', '.docm'])
PDF_EXT = set(_SF.get('pdf') or ['.pdf'])
UNSUPPORTED_EXT = set(_SF.get('unsupported') or ['.doc'])
UNSUPPORTED_NOTE = _SF.get('unsupported_note') or '请先另存为 md/docx/txt；扫描件须先 OCR'
NUM_RE = re.compile(r'[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?\d+(?:\.\d+)?')
# 字段名与取值之间**必须有分隔符**（标点/为是/空白）。若分隔符可选，
# 「土壤类型」的别名「土壤」会误锚到「土壤侵蚀类型」上，抽出垃圾值。
SEP = (r'(?:[^\S\n]{0,4}[:：=＝][^\S\n]{0,4}'
       r'|[^\S\n]{0,4}(?:为|是|达到|共计|合计)[^\S\n]{0,4}'
       r'|[^\S\n]*\s[^\S\n]*)')
STOP = '。；;\n\t|，,、）)】」'
# 文本型取值的截断策略（阈值只读 rules.json fact_extraction.text_value）
_TV = (RULES.get('fact_extraction', {}) or {}).get('text_value', {}) or {}
TEXT_SCAN = int(_TV.get('scan_window_chars', 200))
TEXT_MAX = int(_TV.get('max_value_chars', 120))
TEXT_MIN_KEEP = int(_TV.get('min_keep_chars', 12))
TEXT_HARD_STOP = ''.join(_TV.get('hard_stop_chars') or ['。', '；', '\n'])
TEXT_SOFT_STOP = ''.join(_TV.get('soft_stop_chars') or ['，', ',', '、'])
# 单位同义词归一（仅消除写法差异；万m³ 与 m³ 量级不同，不归一）
UNIT_SYNONYM = [
    (r'^公顷$', 'hm²'), (r'^ha$', 'hm²'), (r'^hm2$', 'hm²'),
    (r'^平方公里$', 'km²'), (r'^km2$', 'km²'),
    (r'^万方$|^万立方米$|^万m3$', '万m³'),
    (r'^立方米$|^方$|^m3$', 'm³'), (r'^平方米$|^m2$', 'm²'),
    (r'^毫米$', 'mm'), (r'^厘米$', 'cm'), (r'^米$', 'm'),
    (r'^公里$|^千米$', 'km'), (r'^摄氏度$|^°C$', '℃'), (r'^％$', '%'),
]

# 表格展平（优化项 1）
# 为什么需要：开采方案/可研的**核心数值几乎全在表格里**（主要技术经济指标表、
# 土石方平衡表、占地表、投资估算表）。原实现直接跳过含 '|' 的值段，
# 实测表格形态的召回接近 0。这里在抽取前把表格行**规范化成叙述句形态**，
# 让既有的 match 逻辑自然命中——**不改匹配规则，只改输入形态**。
_TBL = (RULES.get('fact_extraction', {}) or {}).get('table_flatten', {}) or {}
TBL_ON = bool(_TBL.get('enabled', True))
TBL_MAX_HEAD = int(_TBL.get('max_header_cols', 3))
# 行尾的「单位」列：值后面跟单位，展平后要写成「值 单位」
_TBL_UNIT_HINT = re.compile(
    r'^(?:万?m³|万?m3|m³|m3|hm²|hm2|km²|km2|m²|m2|万元|亿元|万t|万a|Mt/a|'
    r'Mt|万kW|kW|kV|t/a|t|a|d|月|年|天|℃|%|‰|mm|cm|m|km)$', re.I)

# 单位缺省容错（优化项 2）
# 开关只读 rules.json；缺省开启，可一键退回旧行为。
TOL_ON = bool(_TBL.get('unit_missing_tolerant', True))


def _is_sep_row(cells):
    """md 表格的分隔行 |:--|--:|"""
    return bool(cells) and all(re.fullmatch(r':?-{2,}:?', c.strip()) for c in cells if c.strip())


def _split_row(line):
    """'| a | b | c |' → ['a','b','c']；非表格行返回 None。"""
    s = line.strip()
    if not s.startswith('|') or s.count('|') < 2:
        return None
    if not s.endswith('|'):
        s = s + '|'
    return [c.strip() for c in s[1:-1].split('|')]


def flatten_tables(text):
    """把表格行改写成「表头：数据 单位」的叙述形态，供既有匹配逻辑消费。

    输入：  | 序号 | 指标名称 | 单位 | 数量 |
            | 1    | 总投资   | 万元 | 286500 |
    输出：  总投资：286500 万元

    设计约束（遵守铁律）：
      · **不新增值**——只重排已有单元格，不推算、不补默认值；
      · **不改原行**——表格原样保留在下方，出处行号仍可回溯；
      · 无表头/无数据行的表格原样放过，不猜结构。
    """
    if not TBL_ON or '|' not in text:
        return text
    lines = text.split('\n')
    out = []
    header = None
    i = 0
    while i < len(lines):
        cells = _split_row(lines[i])
        if cells is None:
            out.append(lines[i])
            header = None
            i += 1
            continue
        if _is_sep_row(cells):
            # 分隔行 → 丢弃（展平后不需要）
            i += 1
            continue
        # 识别表头行：下一行是分隔行，或本行单元格多为文本且非纯数字
        nxt = _split_row(lines[i + 1]) if i + 1 < len(lines) else None
        if nxt is not None and _is_sep_row(nxt):
            header = cells
            out.append(lines[i])          # 原表头保留
            i += 1
            continue
        if header and len(header) == len(cells) and len(cells) <= 12:
            # 数据行 → 展平成「指标名：值 单位」
            row = {}
            for h, c in zip(header, cells):
                if h:
                    row[h] = c
            # 找"指标名"列：值是非数字文本的那一列（如「总投资」「工程占地」）
            name_col = None
            for h in header:
                v = row.get(h, '')
                if v and not re.fullmatch(r'[\d.,:：+\-~/～\s]*', v) and len(v) <= 24:
                    name_col = h
                    break
            if name_col:
                nm = row[name_col]
                rest = [(h, row.get(h, '')) for h in header
                        if h != name_col and row.get(h, '')]
                # 拆成「数值列」与「单位列」
                nums = [(h, v) for h, v in rest if not _TBL_UNIT_HINT.match(v)]
                units = [v for h, v in rest if _TBL_UNIT_HINT.match(v)]
                # 排除「序号/编号」这类纯序号列：它也是数字，会被误当作取值
                # （实测「| 1 | 总投资 | 万元 | 286500 |」曾抽出 1 而非 286500）
                nums = [(h, v) for h, v in nums
                        if not re.search(r'序号|编号|序|No\.?|ID', str(h), re.I)]
                if nm and nums:
                    val = nums[0][1]
                    unit = units[0] if units else ''
                    # 只有"名称是已知别名"时才展平，避免把任意表格行变成伪叙述句
                    if any(a and a in nm for f in FIELDS for a in f['aliases']):
                        out.append('%s：%s%s' % (nm, val, (' ' + unit) if unit else ''))
                        out.append(lines[i])       # 原行保留，出处可回溯
                        i += 1
                        continue
            out.append(lines[i])
            i += 1
            continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out)


# ================================================================ 读资料
def _xml_text(xml):
    """word/document.xml → 纯文本（段落换行、单元格用 | 分隔）。"""
    xml = re.sub(r'<w:tab[^>]*/>', '\t', xml)
    xml = re.sub(r'<w:br[^>]*/>', '\n', xml)
    xml = xml.replace('</w:tc>', ' | ').replace('</w:tr>', '\n')
    xml = xml.replace('</w:p>', '\n')
    xml = re.sub(r'<[^>]+>', '', xml)
    for a, b in (('&lt;', '<'), ('&gt;', '>'), ('&amp;', '&'), ('&quot;', '"'), ('&apos;', "'")):
        xml = xml.replace(a, b)
    return re.sub(r'\n{3,}', '\n\n', xml)


def read_docx(path):
    with zipfile.ZipFile(path) as z:
        parts = [n for n in z.namelist() if n in ('word/document.xml',)]
        parts += [n for n in z.namelist() if re.match(r'word/(header|footer)\d*\.xml', n)]
        out = []
        for n in parts:
            out.append(_xml_text(z.read(n).decode('utf-8', 'ignore')))
        return '\n'.join(out)


def read_pdf(path):
    """尽力抽取 PDF 文本（FlateDecode + BT/ET 文本串）；质量不足时返回 (text, ok=False)。"""
    raw = open(path, 'rb').read()
    chunks = []
    for m in re.finditer(rb'stream\r?\n(.*?)endstream', raw, re.S):
        data = m.group(1)
        try:
            data = zlib.decompress(data)
        except Exception:
            continue
        if b'BT' not in data and b'Tj' not in data and b'TJ' not in data:
            continue
        txt = []
        for tm in re.finditer(rb'\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>', data):
            s = tm.group(0)
            if s.startswith(b'('):
                body = s[1:-1]
                body = re.sub(rb'\\([()\\])', rb'\1', body)
                try:
                    txt.append(body.decode('utf-8'))
                except UnicodeDecodeError:
                    txt.append(body.decode('latin-1', 'ignore'))
            else:
                hexs = re.sub(rb'\s', b'', s[1:-1])
                try:
                    b = bytes.fromhex(hexs.decode('ascii'))
                    txt.append(b.decode('utf-16-be', 'ignore'))
                except Exception:
                    pass
        if txt:
            chunks.append(''.join(txt))
    text = '\n'.join(chunks)
    cjk = len(re.findall(r'[\u4e00-\u9fff]', text))
    return text, cjk >= 100


def strip_line_numbers(text):
    """剥离「行号 + 制表符/空白」行首前缀（OCR/转换工具常把行号混进正文）。

    为什么必须做：若行号留在文本里，「2.7.5 土壤」这类**章节标题**会被当作字段名命中，
    紧随其后的**行号**（288）就被当成字段值抽走 —— 产出看似正常、实则是行号的垃圾值。
    这类值一旦进入台账会污染全书，且极难人工发现。

    判定条件（三重，避免误剥正文里的数字）：
      1. 行首为纯数字（1-6 位）；
      2. 数字后紧跟制表符或全角空格/多空格；
      3. 全文多数行都满足 1+2（说明这是整篇的行号列，而非个别行）。
    只有三条同时成立才剥离，因此对正常正文是惰性无操作的。
    """
    if not FE_LINENO.get('enabled', True):
        return text
    lines = text.split('\n')
    nz = [l for l in lines if l.strip()]
    if not nz:
        return text
    pat = re.compile(r'^\s*(\d{1,6})[\t\u3000]')
    hit = sum(1 for l in nz if pat.match(l))
    ratio = hit / float(len(nz))
    if ratio < FE_LINENO.get('min_line_ratio', 0.6):
        return text
    stripped = [pat.sub('', l) for l in lines]
    return '\n'.join(stripped)


def read_source(path):
    """返回 [(文件显示名, 文本, 备注)]；不支持/抽取失败时备注里说明处置办法。"""
    out = []
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for f in sorted(files):
                out.extend(read_source(os.path.join(root, f)))
        return out
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    if ext in TEXT_EXT:
        raw = io.open(path, encoding='utf-8-sig', errors='ignore').read()
        clean = strip_line_numbers(raw)
        note = ''
        if clean != raw:
            note = ('已剥离行号前缀（%d 字符）：原文件每行带「行号+制表符」，'
                    '若保留会把章节标题后的行号误抽为字段值'
                    % (len(raw) - len(clean)))
        out.append((name, clean, note))
    elif ext in OFFICE_EXT:
        out.append((name, read_docx(path), ''))
    elif ext in PDF_EXT:
        text, ok = read_pdf(path)
        out.append((name, text, '' if ok else 'PDF 文本抽取质量不足 — 需先转文本（OCR 或转换工具）后重新喂入；'
                                            '不得据残缺文本推断数据'))
    elif ext in UNSUPPORTED_EXT:
        out.append((name, '', UNSUPPORTED_NOTE))
    else:
        out.append((name, '', '不支持的格式：%s。%s' % (ext, UNSUPPORTED_NOTE)))
    return out


# ================================================================ 字段抽取
def norm_unit(u):
    if not u:
        return ''
    u = u.strip().replace(' ', '').replace('㎡', 'm²').replace('m2', 'm²').replace('m3', 'm³')
    u = u.replace('hm2', 'hm²')
    for pat, rep in UNIT_SYNONYM:
        if re.match(pat, u):
            return rep
    return u


def unit_pattern(unit):
    if not unit:
        return ''
    u = re.escape(unit)
    alt = {
        'hm²': r'(?:hm²|hm2|公顷|ha)',
        '万m³': r'(?:万\s*m³|万\s*m3|万\s*立方米|万方)',
        'm³': r'(?:m³|m3|立方米|方)',
        '万元': r'(?:万元|万\s*元)',
        'km²': r'(?:km²|km2|平方公里)',
        '%': r'(?:%|％|个百分点)',
        'm': r'(?:m|米)(?![²³a-zA-Z])',
        'mm': r'(?:mm|毫米)',
        'cm': r'(?:cm|厘米)',
        'km': r'(?:km|公里|千米)',
        't/(km²·a)': r'(?:t/\(?km²?·?a\)?|t/km2·a|吨/\(平方公里·年\))',
        '℃': r'(?:℃|°C|摄氏度)',
        '个月': r'(?:个月|月)',
    }.get(unit, u)
    return r'[^\S\n]{0,3}(%s)?' % alt


def line_of(text, pos):
    return text.count('\n', 0, pos) + 1


def snippet(text, pos, span=40):
    s = max(0, pos - span // 2)
    seg = text[s:pos + span].replace('\n', ' ')
    return re.sub(r'\s+', ' ', seg).strip()


def clean_value(v):
    v = v.strip().strip('：:，,。;；、|').strip()
    v = re.sub(r'\s{2,}', ' ', v)
    return v


# ---------------------------------------------------------------- 别名归属
_OWNERS = None


def alias_owners():
    """全部别名（含弱别名）按长度降序排列，用于「别名归属」判定。"""
    global _OWNERS
    if _OWNERS is None:
        rows = []
        for key, f in PKG_FIELDS.items():
            for a in f.get('aliases', []):
                rows.append((a, key, False))
            for a in f.get('weak_aliases', []):
                rows.append((a, key, True))
        rows.sort(key=lambda r: (-len(r[0]), r[1]))
        _OWNERS = rows
    return _OWNERS


def shadowed_by(text, start, alias, fname):
    """该位置的别名是否被「更长的别名」覆盖。

    典型情形：正文写「水土保持总投资：291.60 万元」，字段「总投资」的别名
    「总投资」也会在此命中，于是产生一个假冲突。判定规则：若同一位置上一个
    更长的别名（属于别的字段）完整覆盖本别名，则该命中归长别名所有，短别名让位。
    这条规则是全局的、与具体字段名无关，因此不需要为每个字段写例外。
    """
    end = start + len(alias)
    for a2, k2, _w in alias_owners():
        if len(a2) <= len(alias):
            break                     # 已按长度降序，后面的都不更长
        if a2 == alias and k2 == fname:
            continue
        p = text.find(a2, max(0, start - len(a2) + 1))
        while p != -1 and p <= start:
            if p <= start < p + len(a2):
                return (a2, k2)
            p = text.find(a2, p + 1)
    return None


def extract_field(text, fname, field):
    """在文本中抽取某字段的全部候选值。返回 [{value, unit, line, snippet, raw, weak}]。

    weak=True 表示命中来自「弱别名」（如「占地面积」之于工程占地）：
    这类别名本身太泛，可能指的是别的对象（某个弃渣场的占地），因此
    不单独构成冲突判据，只作提示。
    """
    cands = []
    seen = set()
    alias_pairs = [(a, False) for a in field['aliases']] + \
                  [(a, True) for a in field.get('weak_aliases', [])]
    for alias, is_weak in alias_pairs:
        pat = re.compile(re.escape(alias) + SEP + (unit_pattern(field['unit']) if field['value_type'] == 'number'
                                                   else r'[^\S\n]{0,3}[:：]?[^\S\n]{0,3}'))
        for m in pat.finditer(text):
            sh = shadowed_by(text, m.start(), alias, fname)
            if sh:
                # 该命中实际属于更长的别名（如「水土保持总投资」之于「总投资」），本字段让位
                continue
            pos = m.end()
            if field['value_type'] == 'number':
                nm = NUM_RE.match(text, pos)
                if not nm:
                    continue
                val = nm.group(0)
                rest = text[nm.end():nm.end() + 12]
                um = re.match(unit_pattern(field['unit']), rest) if field['unit'] else None
                found_unit = norm_unit(um.group(1)) if (um and um.group(1)) else ''
                # 声明了单位却未出现单位 —— 原先直接丢弃，会漏掉大量真实命中
                # （单位写在表头、或整段统一声明时，正文里的「总投资 286500」没有单位）。
                # 改为**降级为弱候选**：保留值供人工确认，不参与冲突判定。
                # 复用既有 weak 机制，不新增状态；补数清单会提示「单位待核实」。
                if field['unit'] and not found_unit:
                    if not TOL_ON:
                        continue
                    cands.append({'value': val.replace(',', ''), 'unit': '',
                                  'line': line_of(text, m.start()),
                                  'snippet': snippet(text, m.start()),
                                  'raw': clean_value(text[m.start():nm.end()])[:80],
                                  'weak': True, 'unit_missing': True, 'alias': alias})
                    continue
                raw = text[m.start():nm.end() + (um.end() if um else 0)]
                key = (val, found_unit, is_weak)
                if key in seen:
                    continue
                seen.add(key)
                cands.append({'value': val.replace(',', ''), 'unit': found_unit or field['unit'],
                              'line': line_of(text, m.start()), 'snippet': snippet(text, m.start()),
                              'raw': clean_value(raw)[:80], 'weak': is_weak, 'alias': alias})
            else:
                # 文本型取值：优先截到**句子边界**（。；换行），而不是固定 60 字。
                #
                # 为什么：叙述体资料里「项目区土壤类型受母质、地形…影响，主要为褐土」这种
                # 句子远超 60 字；按固定长度切会在句中截断，产出「土壤类型受母质」这类
                # **半截值** —— 看似有值、实则不可用，比留空更危险（会误导下游当作完整事实）。
                # 因此扫描窗口放宽到 TEXT_SCAN，再在窗口内找第一个句末标点收口。
                win = text[pos:pos + TEXT_SCAN]
                cut = len(win)
                for ch in TEXT_HARD_STOP:
                    i = win.find(ch)
                    if 0 <= i < cut:
                        cut = i
                val = clean_value(win[:cut])
                # 句末标点未出现时（窗口内全是逗号），退回按软标点收口，避免整段吞入
                if cut >= len(win):
                    for ch in TEXT_SOFT_STOP:
                        i = win.find(ch)
                        if 0 <= i < cut and i >= TEXT_MIN_KEEP:
                            cut = i
                    val = clean_value(win[:cut])
                if not val or len(val) < 2 or len(val) > TEXT_MAX:
                    continue
                if any(val.startswith(a) for a in ('为', '是', '：', ':')):
                    val = clean_value(val[1:])
                if not val or (val, '', is_weak) in seen:
                    continue
                # 值里再出现冒号 → 说明锚到了别的字段名上（如别名「土壤」锚到「土壤侵蚀类型」）
                if '：' in val or ':' in val:
                    continue
                seen.add((val, '', is_weak))
                cands.append({'value': val, 'unit': field['unit'],
                              'line': line_of(text, m.start()), 'snippet': snippet(text, m.start()),
                              'raw': clean_value(text[m.start():pos + len(val)])[:80],
                              'weak': is_weak, 'alias': alias})
    return cands


def build_package(sources, today=None):
    """sources: [(name, text, note)] → 数据包 dict。"""
    import datetime
    today = today or datetime.date.today().isoformat()
    per_field = {}
    for f in FIELDS:
        cands = []
        for name, text, note in sources:
            if not text:
                continue
            # 表格展平：把表格行补一份「表头：值 单位」的叙述形态，供匹配消费。
            # 原文保留 → 出处行号、snippet 仍指向原表，可回溯。
            for c in extract_field(text, name, f):
                cands.append(dict(c, source=name))
            ft = flatten_tables(text)
            if ft != text:
                for c in extract_field(ft, name, f):
                    c = dict(c, source=name, from_table=True)
                    if c['value'] not in [x['value'] for x in cands]:
                        cands.append(c)
        per_field[f['key']] = cands

    fields, conflicts, mismatches, missing, unverified, weak_conflicts = {}, [], [], [], [], []
    spec_given = []
    for f in FIELDS:
        key = f['key']
        cands = per_field[key]
        strong = [c for c in cands if not c.get('weak')]
        values = []
        for c in cands:
            v = c['value']
            if v not in values:
                values.append(v)
        strong_values = []
        for c in strong:
            if c['value'] not in strong_values:
                strong_values.append(c['value'])
        entry = {'section': f['section'], 'declared_unit': f['unit'], 'required': f['required'],
                 'candidates': cands}
        if f.get('source') == 'spec_given':
            entry['source'] = 'spec_given'
            entry['note'] = f.get('note') or '规范给定：由 Zone A 取值，不需项目资料提供'
        if not cands:
            entry.update({'value': None, 'unit': f['unit'], 'status': '待填', 'provenance': []})
            if f.get('source') == 'spec_given':
                entry['status'] = '规范给定（无需资料提供）'
                spec_given.append(key)
            else:
                (missing if f['required'] else unverified).append(key)
        elif len(values) > 1 and len(strong_values) <= 1:
            # 只有弱别名出现多值（如多个「占地面积」分属不同对象）→ 提示，不判冲突
            entry.update({'value': strong_values[0] if strong_values else None,
                          'unit': f['unit'],
                          'status': '待人工确认（弱别名多值）',
                          'note': '「%s」在本字段为泛化别名，可能指向不同对象，需人工确认取哪一个'
                                  % '、'.join(sorted({c.get('alias', '') for c in cands if c.get('weak')})),
                          'provenance': [{'source': c['source'], 'line': c['line'], 'value': c['value'],
                                          'snippet': c['snippet'], 'weak': c.get('weak', False)}
                                         for c in cands]})
            weak_conflicts.append({'key': key, 'section': f['section'], 'values': values,
                                   'note': entry['note']})
        elif len(values) > 1:
            entry.update({'value': None, 'unit': f['unit'], 'status': '冲突',
                          'provenance': [{'source': c['source'], 'line': c['line'],
                                          'value': c['value'], 'snippet': c['snippet'],
                                          'weak': c.get('weak', False)} for c in cands]})
            conflicts.append({'key': key, 'section': f['section'], 'values': values,
                              'provenance': entry['provenance']})
        else:
            c0 = cands[0]
            diff_unit = [c for c in cands
                         if c['unit'] and f['unit'] and norm_unit(c['unit']) != norm_unit(f['unit'])]
            entry.update({'value': c0['value'], 'unit': c0['unit'],
                          'status': '待核实（单位口径不一致）' if diff_unit else '已确认',
                          'provenance': [{'source': c['source'], 'line': c['line'],
                                          'value': c['value'], 'snippet': c['snippet']} for c in cands]})
            if diff_unit:
                mismatches.append({'key': key, 'declared_unit': f['unit'],
                                   'found': sorted({c['unit'] for c in diff_unit})})
        fields[key] = entry

    stats = {
        'fields_total': len(FIELDS),
        'confirmed': sum(1 for v in fields.values() if v['status'] == '已确认'),
        'conflict': len(conflicts),
        'weak_alias_multi': len(weak_conflicts),
        'unit_mismatch': len(mismatches),
        'missing_required': len(missing),
        'missing_optional': len(unverified),
        'spec_given': len(spec_given),
    }
    return {
        'meta': {
            'generated_at': today,
            'generated_by': 'shuitu-writing-skill/ingest.py',
            'sources': [{'file': n, 'chars': len(t), 'note': note} for n, t, note in sources],
            'rule': 'rules.json data_package',
            'storage_policy': '数据包只存放于项目工作区；严禁写入技能目录或知识库',
        },
        'fields': fields,
        'conflicts': conflicts,
        'weak_alias_multi': weak_conflicts,
        'unit_mismatch': mismatches,
        'missing_required': missing,
        'missing_optional': unverified,
        'spec_given_fields': spec_given,
        'stats': stats,
    }


def render_list(pkg):
    """最小补数清单（给人看的 md）。"""
    L = ['# 项目数据补数清单', '',
         '来源资料：%s' % '、'.join(s['file'] for s in pkg['meta']['sources']), '']
    st = pkg['stats']
    L.append('已确认 %d / 冲突 %d / 弱别名多值待确认 %d / 单位待核实 %d / 必填缺失 %d'
             % (st['confirmed'], st['conflict'], st.get('weak_alias_multi', 0),
                st['unit_mismatch'], st['missing_required']))
    L.append('')
    if pkg['conflicts']:
        L.append('## 一、取值冲突（须人工裁决，工具不替选）')
        L.append('')
        for c in pkg['conflicts']:
            L.append('**%s**（%s）出现 %d 个不同取值：%s'
                     % (c['key'], c['section'], len(c['values']), '、'.join(map(str, c['values']))))
            for p in c['provenance'][:6]:
                L.append('  - [%s L%s] %s —— %s' % (p['source'], p['line'], p['value'], p['snippet']))
            L.append('')
    if pkg.get('weak_alias_multi'):
        L.append('## 一之二、泛化别名命中多值（可能是不同对象，需人工确认）')
        L.append('')
        for c in pkg['weak_alias_multi']:
            L.append('- **%s**：%s —— %s' % (c['key'], '、'.join(map(str, c['values'])), c['note']))
        L.append('')
    if pkg['unit_mismatch']:
        L.append('## 二、单位口径待核实（不做静默换算）')
        L.append('')
        for m in pkg['unit_mismatch']:
            L.append('- %s：声明单位 %s，资料中出现 %s' % (m['key'], m['declared_unit'], '、'.join(m['found'])))
        L.append('')
    L.append('## 三、必填但未抽到的字段（缺失即用【待填：…】占位，不猜数）')
    L.append('')
    for k in pkg['missing_required']:
        L.append('- 【待填：%s】' % k)
    L.append('')
    if pkg['missing_optional']:
        L.append('## 四、选填未抽到（按需补充）')
        L.append('')
        L.append('、'.join(pkg['missing_optional']))
        L.append('')
    if pkg.get('spec_given_fields'):
        L.append('## 五、规范给定字段（不需资料提供，由 Zone A 取值）')
        L.append('')
        for k in pkg['spec_given_fields']:
            L.append('- **%s**：%s' % (k, (pkg['fields'][k].get('note') or '')))
        L.append('')
    L.append('> 抽取口径：字段名（或别名）+ 分隔符 + 数值/文本；声明单位的字段必须出现单位才算命中。'
             '未命中不代表资料里没有，可能只是写法不同——请人工确认后再回填。'
             '另：同一位置上更长的字段名优先（写「水土保持总投资」不会同时命中「总投资」）。')
    return '\n'.join(L) + '\n'


def validate(pkg):
    """复核既有数据包：统计状态与风险。

    同时执行 rules.json 的 data_package.sections（分区合法性）与 provenance_required
    （取值必须有出处）——这两条此前只有声明、没有执行。
    """
    issues = []
    if not isinstance(pkg, dict) or 'fields' not in pkg:
        return {'ok': False, 'issues': ['不是本工具生成的数据包结构']}
    sections = DP.get('sections') or {}
    decl_sections = set(sections) if isinstance(sections, dict) else set(sections)
    provenance_required = DP.get('provenance_required', True)
    for k, v in pkg['fields'].items():
        if v.get('status') == '待填' and v.get('required'):
            issues.append('必填字段缺失：%s' % k)
        if v.get('status') == '冲突':
            issues.append('取值冲突未裁决：%s' % k)
        if v.get('status', '').startswith('待核实'):
            issues.append('单位口径待核实：%s' % k)
        if provenance_required and v.get('value') is not None and not v.get('provenance'):
            issues.append('取值无出处（违反 provenance_required）：%s' % k)
        if decl_sections and v.get('section') and v['section'] not in decl_sections:
            issues.append('字段分区未在 data_package.sections 声明：%s（%s）' % (k, v['section']))
    return {'ok': not issues, 'issues': issues,
            'stats': pkg.get('stats', {}),
            'sources': [s['file'] for s in pkg.get('meta', {}).get('sources', [])]}


def load_package(path):
    return json.load(io.open(path, encoding='utf-8-sig'))


def main():
    ap = argparse.ArgumentParser(description='资料接入：原始资料 → 项目数据包（带出处与冲突检测）')
    ap.add_argument('--source', action='append', default=[], help='资料文件或目录，可重复')
    ap.add_argument('--out', default=None, help='输出数据包 json 路径（放项目工作区）')
    ap.add_argument('--list-out', default=None, help='输出补数清单 md 路径')
    ap.add_argument('--validate', default=None, help='复核既有数据包')
    ap.add_argument('--json-only', action='store_true')
    a = ap.parse_args()

    if a.validate:
        r = validate(load_package(a.validate))
        print(json.dumps(r, ensure_ascii=False, indent=1))
        sys.exit(0 if r['ok'] else 1)

    if not a.source:
        ap.error('需要 --source 指定资料（文件或目录）')
    sources = []
    for s in a.source:
        if not os.path.exists(s):
            sources.append((os.path.basename(s), '', '路径不存在'))
            continue
        for name, text, note in read_source(s):
            if not note and not (text or '').strip():
                note = '文件为空，无内容可抽取'
            sources.append((name, text, note))

    usable = [s for s in sources if (s[1] or '').strip()]
    problems = [s for s in sources if s[2] or not (s[1] or '').strip()]
    # 一个来源都没解析出来时**不得静默生成空数据包**：以前这里照样 exit 0 并写出
    # 「全部字段缺失」的数据包，用户会以为资料已经喂进去了。
    if not usable:
        sys.stderr.write('❌ 没有任何可解析的资料，未生成数据包。\n')
        for n, _t, note in problems:
            sys.stderr.write('   - %s：%s\n' % (n, note or '无文本'))
        sys.stderr.write('   支持格式：%s\n'
                         % '、'.join(sorted(set(TEXT_EXT) | {'.docx', '.docm', '.pdf'})))
        sys.exit(2)

    pkg = build_package(sources)
    if a.out:
        G.write_text(a.out, json.dumps(pkg, ensure_ascii=False, indent=1), '数据包')
        print('已写出数据包:', a.out)
    if a.list_out:
        G.write_text(a.list_out, render_list(pkg), '补数清单')
        print('已写出补数清单:', a.list_out)

    if a.json_only:
        print(json.dumps(pkg, ensure_ascii=False, indent=1))
    else:
        print(render_list(pkg))
    for n, _t, note in problems:
        print('⚠ %s：%s' % (n, note or '无文本'), file=sys.stderr)
    sys.exit(0)


if __name__ == '__main__':
    main()

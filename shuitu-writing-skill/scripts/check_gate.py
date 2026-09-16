#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""合规闸门（第一层）实现。

用法:
    python check_gate.py --chapter 2.3 [--province 河南省] [--city 平顶山市]
    python check_gate.py --chapter 1.1 --json-only
    python check_gate.py --list
    python check_gate.py --report-form --province 河南省   # 报告表（附件3）分支闸门

规则全部来自 references/rules.json（声明式），本脚本只做执行，不含隐藏规则。
所有 hard_constraints[].requirement 均为知识库文件正文的逐字片段。
省份激活使用 rules.json 的 scope_activation.province_registry 声明式映射，不硬编码省份。
"""
import argparse, json, os, re, sys

sys.dont_write_bytecode = True   # 技能包不留 __pycache__（避免缓存掩盖规则改动）

try:
    # 必须是 utf-8，不能用 utf-8-sig：后者会往 stdout 写 BOM，
    # `python xxx.py --json-only > x.json` 之后严格 JSON 解析器读不了（且所有
    # import check_gate 的脚本（analyze_gaps/budget 等）都会被传染 BOM）。
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                    # 非 tty / 老版本兜底
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REF = os.path.join(SKILL, 'references')
from vault_paths import VAULT

RULES = json.load(open(os.path.join(REF, 'rules.json'), encoding='utf-8-sig'))
TREE = json.load(open(os.path.join(REF, 'template-tree.json'), encoding='utf-8-sig'))
ZONE_A = json.load(open(os.path.join(REF, 'zone-a-index.json'), encoding='utf-8-sig'))

NODES = {n['chapter_id']: n for n in TREE['nodes']}

MAXREQ = RULES['clause_extraction']['requirement_max_chars']
def _rule_re(key, default, flags=0):
    """按 rules.json clause_extraction 的正则清单编译（声明式；缺键才用内置兜底）。"""
    pats = (RULES.get('clause_extraction', {}) or {}).get(key) or []
    pats = [p for p in pats if isinstance(p, str) and p]
    return re.compile('|'.join(pats) if pats else default, flags)


LAW_RE = _rule_re('law_patterns', r'^\s*(第[一二三四五六七八九十百零〇0-9]+条)')
NUM_RE = _rule_re('standard_patterns',
                  r'^\s*([0-9]+(?:\.[0-9]+)*|[A-Z]\.\d+(?:\.\d+)*)\s*([\u4e00-\u9fa5（(])')
TOC_RE = _rule_re('toc_patterns', r'\.{3,}|…{3,}')

# 条款正文质量闸（rules.json clause_extraction.clause_quality，声明式）
_CLQ = (RULES.get('clause_extraction', {}) or {}).get('clause_quality') or {}
QUALITY_RE = re.compile('|'.join(_CLQ.get('reject_patterns') or []) or r'(?!)')
QUALITY_EN_RATIO = float((_CLQ.get('reject_english_ratio') or {}).get('threshold', 0.35))
QUALITY_MIN_CHARS = int(_CLQ.get('min_body_chars', 16))


def clause_ok(body):
    """条款正文质量闸：命中封面/英文标题/元数据/过短 → 不合格，禁止当作依据引用。

    以前没有这道闸，导致《水土保持法》的"依据正文"取到了文件开头的
    【Title】Water and Soil Conservation Law ... 封面双语标题，会被误当条文写进方案。

    2026-09 修正：判英文占比前先**剥离 HTML 表格标记**。MinerU 转出的标准/定额/编制规定
    里表格是正文主体（`<table><tr><td colspan=…>`），标记本身含大量拉丁字母，
    会把真正的中文表格内容误判成"英文标题"而整段丢弃——
    实测《水利工程设计概（估）算编制规定（水土保持工程）》的分年度投资表、
    概算附表因此全部被拒，闸门抽不出任何造价条款。
    """
    if not body:
        return False
    if QUALITY_RE.search(body):
        return False
    # 先剥 HTML 标签与实体，再判长度与英文占比
    stripped = re.sub(r'<[^>]{0,400}>', '', body)
    stripped = (stripped.replace('&nbsp;', ' ').replace('&amp;', '&')
                .replace('&lt;', '<').replace('&gt;', '>'))
    stripped = re.sub(r'\s+', ' ', stripped).strip()
    if len(stripped) < QUALITY_MIN_CHARS and len(body) < QUALITY_MIN_CHARS:
        return False
    latin = len(re.findall(r'[A-Za-z]', stripped))
    if stripped and latin / float(len(stripped)) > QUALITY_EN_RATIO and '【' not in stripped:
        return False
    return True


def keywords_of(cid):
    """节点关键词（rules.json node_keywords）。

    容器节点（章/节）在 rules.json 里没有独立关键词表——此时汇总其子节点的关键词，
    而不是静默拿空表去匹配条款（那会让该节的约束检索退化成标题匹配）。
    """
    nk = RULES.get('node_keywords', {}) or {}
    own = [k for k in (nk.get(cid) or []) if k]
    if own:
        return own
    out = []
    for k, v in nk.items():
        if k.startswith(cid + '.'):
            for x in v:
                if x and x not in out:
                    out.append(x)
    return out


# ---------- 输出防污染（铁律5：零项目残留）与安全读写 ----------
def _die(msg, code=2):
    """输入/用法错误统一出口：消息给人话，退出码 2（见 SKILL.md 退出码契约）。"""
    sys.stderr.write(msg.rstrip() + '\n')
    raise SystemExit(code)


def guard_out(path, what='输出文件'):
    """任何由用户指定的输出/写入路径都不得落在技能包或知识库内，也不得写进不存在的目录。

    技能目录只装技能本身、知识库只装资料：一次误操作把「数据包.json」写进技能根目录，
    就够污染交付物（铁律5 零项目残留）。写入前调用本函数，路径非法直接停下并说清原因。
    """
    if not path:
        return path
    p = os.path.abspath(path)
    for root, name in ((os.path.abspath(SKILL), '技能包'), (VAULT and os.path.abspath(VAULT), '知识库')):
        if not root:
            continue
        if p == root or p.startswith(root + os.sep):
            _die(
                '❌ %s 落在%s内：%s\n'
                '   %s 必须零残留（铁律5）——请输出到项目工作区，例如：\n'
                '     python "%s\\scripts\\%s" ... --out "D:\\项目\\输出.md"'
                % (what, name, path, name,
                     os.path.abspath(SKILL), os.path.basename(sys.argv[0] or 'xxx.py')))
    parent = os.path.dirname(p) or '.'
    if not os.path.isdir(parent):
        _die('❌ %s 的目录不存在：%s' % (what, parent))
    if os.path.isdir(p):
        _die('❌ %s 指向一个目录：%s' % (what, path))
    return p


def write_text(path, text, what='输出文件'):
    """UTF-8 写文本：先过 guard_out，再兜住一切 OSError，给一句人话。"""
    p = guard_out(path, what)
    try:
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
    except OSError as ex:
        _die('❌ 写不出%s：%s（%s）' % (what, p, ex))
    return p


def read_text(path, what='输入文件'):
    """读文本（容 BOM/编码杂音）：文件不存在、是目录、读不动都给人话。"""
    if not path:
        _die('❌ 缺少%s路径' % what)
    if os.path.isdir(path):
        _die('❌ %s 是一个目录，不是文件：%s' % (what, path))
    if not os.path.exists(path):
        _die('❌ 找不到%s：%s' % (what, path))
    try:
        return open(path, encoding='utf-8-sig', errors='ignore').read()
    except OSError as ex:
        _die('❌ 读不了%s：%s（%s）' % (what, path, ex))


def read_json(path, what='JSON 文件', default=None):
    """读 JSON：不存在/坏内容给一句人话；default 不为 None 时缺失即返回 default。"""
    if path and os.path.exists(path) and not os.path.isdir(path):
        try:
            return json.load(open(path, encoding='utf-8-sig'))
        except Exception as ex:
            _die('❌ %s 不是合法 JSON：%s（%s）' % (what, path, ex))
    if path and os.path.isdir(path):
        _die('❌ %s 是一个目录，不是文件：%s' % (what, path))
    if default is not None:
        return default
    _die('❌ 找不到%s：%s' % (what, path))


# ---------- 快照时效闸门（D8 的执行器侧，只读不重建） ----------
def snapshot_status():
    """按 rules.json 的 snapshot_gate 判定索引快照是否过期。返回 (stale, reasons)。"""
    sg = RULES.get('snapshot_gate', {})
    if not sg:
        return False, []
    man_path = os.path.join(REF, os.path.basename(sg.get('manifest', 'vault_manifest.json')))
    reasons = []
    # 0) 知识库本身是否可达：库不在时索引只能作离线兜底，
    #    必须显式告警，绝不静默使用（铁律「绝不静默使用旧索引」）。
    if not VAULT or not os.path.isdir(VAULT):
        return True, ['知识库不可达（%s），当前仅能使用随技能携带的索引快照；'
                      '请设置环境变量 DSH_WS_VAULT 指向知识库根目录后重建索引'
                      % (VAULT or '<未设置 DSH_WS_VAULT>')]
    if not os.path.exists(man_path):
        return True, ['快照清单不存在（%s），索引未经校验' % os.path.basename(man_path)]
    try:
        man = json.load(open(man_path, encoding='utf-8-sig'))
        import datetime as _dt
        gen = _dt.date.fromisoformat(man['generated_at'])
        age = (_dt.date.today() - gen).days
    except Exception as e:
        return True, ['快照清单无法解析（%s）' % e]
    max_age = sg.get('max_age_days', 30)
    if age > max_age:
        reasons.append('快照生成于 %s（%d 天前，阈值 %d 天）' % (man.get('generated_at'), age, max_age))
    for idx_name in ('zone-a-index.json', 'zone-b-index.json', 'zone-c-index.json'):
        p = os.path.join(REF, idx_name)
        if not os.path.exists(p):
            continue
        try:
            idx_gen = json.load(open(p, encoding='utf-8-sig')).get('generated_at')
        except Exception:
            continue
        if idx_gen and man.get('generated_at') and idx_gen != man.get('generated_at'):
            reasons.append('%s 的 generated_at（%s）与快照清单（%s）不一致，索引可能未同步重建'
                           % (idx_name, idx_gen, man.get('generated_at')))
    return (bool(reasons), reasons)


SNAPSHOT_STALE, SNAPSHOT_REASONS = snapshot_status()
SNAPSHOT_WARNING = ('知识库索引可能过期，合规结论未经验证 → 执行 python scripts/rebuild_index.py --rebuild；'
                    '明细：%s') % '；'.join(SNAPSHOT_REASONS) if SNAPSHOT_STALE else None


# ---------- 章节工具 ----------
def ancestors(cid):
    parts = cid.split('.')
    return ['.'.join(parts[:i]) for i in range(1, len(parts))]


def expand(cid):
    if cid in NODES and NODES[cid]['node_kind'] != 'container':
        return [cid]
    kids = [n['chapter_id'] for n in TREE['nodes']
            if n.get('parent') == cid and n['node_kind'] != 'container']
    return kids or [cid]


def core_keyword(cid):
    """节点标题去掉括注后的核心概念，作为高辨识度匹配词。"""
    t = NODES.get(cid, {}).get('title', '')
    t = re.sub(r'（[^）]*）', '', t)
    t = re.sub(r'\([^)]*\)', '', t)
    return t.strip()


# ---------- 状态与范围 ----------
def is_current(s):
    return any(str(s).startswith(p) for p in RULES['status_classification']['current_prefixes'])


def status_kind(s):
    sc = RULES['status_classification']
    if is_current(s):
        return 'current'
    if any(str(s).startswith(p) for p in sc['blocking_statuses']):
        return 'blocked'
    if any(str(s).startswith(p) for p in sc['excluded_statuses']):
        return 'excluded'
    return 'unknown'


def label_of(e):
    """人读标签：优先**标题**，其次真实文号。

    原来直接用 doc_number，导致 doc_number 为「无」「不适用」这类占位值时，
    提示语里出现"地方级依据「无」的地域不一致"——读者不知道说的是哪份文件。
    文号只在确实是编号时才用作标签。
    """
    dn = (e.get('doc_number') or '').strip()
    if dn and dn not in ('', '待确认', '无', '不适用'):
        return dn if not e.get('title') else '%s（%s）' % (e['title'], dn)
    t = (e.get('title') or '').strip()
    if t:
        return t
    return (e.get('file') or '').split('\\')[-1]


def province_hit(entry, province):
    """声明式省份匹配：依据文件名 ↔ rules.json 的 province_registry。

    返回 (matched, reason)：
    - matched=True      激活
    - matched=False, reason=None   省份已声明但确认不匹配（静默不激活）
    - matched=False, reason=str    需要提示使用者的情况（未声明省份 / 省份未登记）
    """
    reg = RULES.get('scope_activation', {}).get('province_registry', {}) or {}
    fname = os.path.basename(entry['file'])
    stem = os.path.splitext(fname)[0]                # 登记清单存的是不带扩展名的标题
    if not province:
        return False, '未声明项目所在省份，地方级依据「%s」未激活' % label_of(entry)
    hit_key = None
    for key in reg:
        if key in province:
            hit_key = key
            break
    if hit_key is None:
        # 未登记省份：仅当文件名本身含所声明省份名时激活（如库内新增了该省文件但未登记）
        if province.rstrip('省市自治区特别行政区') in fname or province in fname:
            return True, None
        return False, ('省份「%s」未登记于 province_registry，无法确认库内地方级依据是否齐全；'
                       '依据「%s」未激活。如库内已有该省依据，请先在 rules.json 登记'
                       % (province, label_of(entry)))
    # 已登记省份：文件名（或其标题干）含登记键，或登记清单点名了该文件 → 激活
    if hit_key in fname or stem in reg[hit_key] or fname in reg[hit_key]:
        return True, None
    return False, None


def city_hit(entry, city):
    """地市级匹配：声明了地市还不够，必须确认该文件确实属于这个地市。

    rules.json scope_activation.local 写的是「仅当声明了项目所在地市/县、**且该文件 scope 与之匹配**
    时激活」——原实现只要声明了城市就无条件激活，会把甲市规划当成乙市依据。
    返回 (matched, reason)，与 province_hit 同构。
    """
    reg = RULES.get('scope_activation', {}).get('city_registry', {}) or {}
    fname = os.path.basename(entry['file'])
    stem = os.path.splitext(fname)[0]
    short = city.rstrip('市县区')
    named = list(reg.get(city) or []) + list(reg.get(short) or [])
    if fname in named or stem in named:
        return True, None
    if (short and short in fname) or city in fname:
        return True, None
    return False, ('项目所在地市「%s」与地方级依据「%s」的地域不一致，该依据未激活；'
                   '若确属同一地市，请在 rules.json scope_activation.city_registry 登记'
                   % (city, label_of(entry)))


def applies(entry, targets, province, city):
    chs = entry['chapter_relevance']
    if '全域' in chs:
        origin = 'inherited'
    elif any(t in chs for t in targets):
        origin = 'node_specific'
    elif any(a in chs for a in ancestors(targets[0])):
        origin = 'inherited'
    else:
        return False, None, None
    sc = entry['scope']
    if sc == 'national':
        return True, origin, None
    if sc == 'provincial':
        matched, reason = province_hit(entry, province)
        return matched, origin, reason
    if sc == 'local':
        if not city:
            return False, origin, '未声明项目所在地市，地方级依据「%s」未激活' % label_of(entry)
        matched, reason = city_hit(entry, city)
        return matched, origin, reason
    return False, origin, None


# ---------- 文档处理 ----------
def read_doc(rel, cap=3_000_000):
    p = os.path.join(VAULT, rel)
    if not os.path.exists(p):
        return ''
    t = open(p, encoding='utf-8', errors='ignore').read(cap)
    if t.startswith('---'):
        i = t.find('\n---', 3)
        if i > 0:
            t = t[i + 4:]
    t = re.sub(r'data:image/[^\s"]+', '', t)
    # 截断「条文说明」——那是解释性文字，不是规范性要求，不得充当硬约束
    m = re.search(r'\n\s*#{0,4}\s*(水土保持[^\n]{0,12})?条文说明', t)
    if m and m.start() > len(t) * 0.25:
        t = t[:m.start()]
    return t


def split_clauses(text):
    """行驱动的条款切分：法条按「第X条」，标准按行首编号，其余按段落。
    返回 [(label, text)]，label 尽量取自原文编号。

    行内条号：库内 md 常把「第二条在中华人民共和国境内…」合并成一段（实测仅行首形态
    在 Zone A 全库命中 0 条），因此条号出现在**分句边界之后**也视为新条款起点。
    同一条号重复出现时（引用处，如「违反本法第二十三条规定」），保留正文最长的一处。
    """
    lines = text.splitlines()
    clauses, cur_label, cur_buf = [], None, []

    def flush():
        if cur_buf:
            body = '\n'.join(cur_buf).strip()
            if len(body) >= 12:
                clauses.append((cur_label or '（段落）', body))
        cur_buf.clear()

    n_law = len(LAW_RE.findall(text))
    # 条号起点：允许"第X条"后紧跟中文（融合式）或空格/Tab（分隔式）
    LAW_START = re.compile(r'第[一二三四五六七八九十百零〇0-9]+条(?=[\u4e00-\u9fa5]|\s)')
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        # 去掉 markdown 标题前缀，否则 "## 5.1 渣土来源及流向" 无法识别为条款
        s2 = re.sub(r'^#{1,6}\s*', '', s).strip()
        if n_law >= 5:
            if re.fullmatch(r'第[一二三四五六七八九十百零〇0-9]+条', s2):
                # 形态一：条号单独成行（"## 第三条"）——正文在后续行，此处只记标签
                flush(); cur_label = s2; continue
            m0 = LAW_RE.match(s2)
            if m0:
                # 形态二：行首即条号，条号后紧跟正文
                flush(); cur_label = m0.group(1); cur_buf.append(s2); continue
            m0s = re.match(r'(第[一二三四五六七八九十百零〇0-9]+条)\s+(.*)$', s2)
            if m0s:
                # 形态二乙：行首"第X条 正文"（条号与正文间有空格）
                flush(); cur_label = m0s.group(1); cur_buf.append(m0s.group(2)); continue
            # 形态三：行内出现条号（"…上一句。第二条在中华人民共和国境内…"，
            #        或"…第七章 法律责任 第五十三条 违反本法…"这种"条号+空格+正文"）
            hits = list(LAW_START.finditer(s2))
            if hits and (re.search(r'[。；）)】]', s2[:hits[0].start()]) or hits[0].start() == 0):
                cut = []
                prev = 0
                for m in hits:
                    if m.start() > prev:
                        cut.append((None, s2[prev:m.start()]))
                    cut.append((m.group(0), None))
                    prev = m.end()
                if prev < len(s2):
                    if cut and cut[-1][0] is not None:
                        cut[-1] = (cut[-1][0], s2[prev:])
                    else:
                        cut.append((None, s2[prev:]))
                for lab, txt in cut:
                    if lab:
                        flush(); cur_label = lab
                    elif txt and txt.strip():
                        cur_buf.append(txt.strip())
                continue
            if cur_label is None and len(s2) < 12:
                continue
            cur_buf.append(s2)
            continue
        else:
            m = NUM_RE.match(s2)
            if m and len(s2) > len(m.group(1)) + 2 and not re.fullmatch(r'(19|20)\d\d', m.group(1)):
                flush(); cur_label = m.group(1); cur_buf.append(s2); continue
        if cur_label is None and len(s2) < 12:
            continue
        cur_buf.append(s2)
    flush()
    # ---- 退化保护：切分失败时回退到段落切分 ----
    # 背景：本函数只在**识别到条款编号**时才 flush，而空行是 continue 跳过、不触发切分。
    # 因此对「（1）（2）式编号」「无编号的 OCR 扫描标准/定额/编制规定」这类文件，
    # NUM_RE 与 LAW_RE 都不命中 → 全文被累积成 **一整条**，paragraph 兜底又因 clauses 非空而不执行，
    # 最终 pick() 必然 0 命中，表现就是闸门报 no_clause_hit、硬约束为空。
    # （实测：《水利工程设计概（估）算编制规定（水土保持工程）》2025 版 191k 字被切成 1 条。）
    # 判据：条款数过少，或单条异常长 → 判为退化，改用空行段落切分。
    _too_few = len(clauses) <= 2
    _too_big = bool(clauses) and max(len(b) for _, b in clauses) > 4000
    if _too_few or _too_big:
        paras = []
        for para in re.split(r'\n\s*\n', text):
            para = para.strip()
            if len(para) < 12:
                continue
            # 段落过长时再按行聚合切一刀，避免又变成一块
            if len(para) > 1200:
                buf = []
                for ln in para.splitlines():
                    ln = ln.strip()
                    if not ln:
                        continue
                    buf.append(ln)
                    if sum(len(x) for x in buf) > 600:
                        paras.append(('（段落）', '\n'.join(buf)))
                        buf = []
                if buf:
                    paras.append(('（段落）', '\n'.join(buf)))
            else:
                paras.append(('（段落）', para))
        if len(paras) > len(clauses):
            clauses = paras
        # ---- 短标题并入后文 ----
        # OCR 标准/定额/编规里，关键信息常落在形如「## 3. 分年度投资表」的**短标题**上，
        # 标题本身不足 min_body_chars 会被质量闸丢弃，导致关键词明明命中却 pick 不到。
        # 做法：短标题与其后一段合并（标题+正文才是完整要求）。
        merged = []
        i = 0
        while i < len(paras):
            lab, body = paras[i]
            if len(body.strip()) < 16 and i + 1 < len(paras):
                nxt = paras[i + 1][1]
                merged.append((lab, (body.strip() + '\n' + nxt).strip()))
                i += 2
                continue
            merged.append((lab, body))
            i += 1
        if merged:
            clauses = merged
    if not clauses:
        for para in re.split(r'\n\s*\n', text):
            para = para.strip()
            if len(para) >= 12:
                clauses.append(('（段落）', para))
    return _prefer_definitions(clauses)


def _prefer_definitions(clauses):
    """同一条号多次出现时保留"定义处"，丢弃"引用处"。

    依据 rules.json clause_extraction.clause_quality.prefer_definition_note：
    · 定义处：条号后紧跟要求正文，且正文**不以引用性收尾**（如"…的规定"）；
    · 引用处：形如「违反本法第二十三条规定」「按照第十五条第二款…」，正文很短或纯引用。

    判据顺序：先看是否"引用性"（正文短、以"规定/条款/执行"收尾、含"违反/依据/按照/符合"且无实质要求），
    引用性的一律丢弃；再从剩下的里取正文最长者。
    """
    REFER = re.compile(r'(违反|依据|按照|符合|执行|参照|根据)本[法办法规]|'
                       r'(第[一二三四五六七八九十百零〇0-9]+条(第[一二三四五六七八九十]+款)?[、，,]?)+'
                       r'(和|及|与)?(第[一二三四五六七八九十百零〇0-9]+条)?(第[一二三四五六七八九十]+款)?'
                       r'(之)?(规定|要求)?[。；;]?$')
    best = {}
    plain = []
    for label, body in clauses:
        if not re.match(r'^第[一二三四五六七八九十百零〇0-9]+条$', label or ''):
            plain.append((label, body))
            continue
        # 引用性正文：整段就是"援引他条"、自身不含实质要求（短且以规定/条款收尾）。
        # 注意：**罚则条款**（"违反本法第X条规定，责令停止…罚款"）是实质要求，不能丢。
        is_ref = bool(REFER.search(body)) and len(body) < 60 and not re.search(
            r'责令|处罚|罚款|没收|滞纳金|承担|追究|限期|改正|拆除|补办|治理|清理', body)
        if is_ref:
            continue
        prev = best.get(label)
        if prev is None or len(body) > len(prev[1]):
            best[label] = (label, body)
    return plain + [best[k] for k in best]


def strip_html(s):
    """剥离 HTML 表格/标签与实体，返回可读纯文本。

    为什么需要：MinerU 转出的标准、定额、编制规定里**表格是正文主体**，
    原文形如 `<table><tr><td colspan="1">工程或费用名称</td>…`，含大量拉丁字母，
    若直接按原文判定，会被"英文占比过高"和"含 <table> 即拒"两道闸误杀
    （实测《水利工程设计概（估）算编制规定（水土保持工程）》2025 的分年度投资表、
    概算附表全部被拒，导致第 9 章抽不出任何造价条款）。
    剥标记后剩下的中文（工程名称/单位/单价/数量）才是可引用内容。
    """
    if not s:
        return ''
    t = re.sub(r'<[^>]{0,400}>', ' ', s)
    t = (t.replace('&nbsp;', ' ').replace('&amp;', '&')
         .replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"'))
    t = re.sub(r'[ \t\u3000]+', ' ', t)
    t = re.sub(r'\n{2,}', '\n', t)
    return t.strip()


def usable(seg):
    # 表格：不整条拒绝，改为剥标记后按纯文本判定（见 strip_html 注释）
    plain = strip_html(seg)
    if TOC_RE.search(plain) and len(plain) < 400:
        return False
    # 条款正文质量闸：封面/英文标题/元数据/过短 → 不是可引用的规范要求
    if not clause_ok(seg):
        return False
    # 解释性/说明性文字不是规范要求
    if re.search(r'本条为强制性条文|本条为推荐性|【条文说明】|本条中的', plain):
        return False
    # 网页抓取残留（导航、位置、页脚）
    if re.search(r'您现在的位置|中国政务|官方微信公众号|返回顶部|无障碍|打印文章|主站链接', plain):
        return False
    # 编制依据清单式罗列（只有文件名编号、没有具体要求）
    if re.search(r'^[\d\s））；;、《》（）〔〕号\.\-]+$', plain):
        return False
    # 剥标记后几乎没有实质文字（纯排版壳）→ 丢弃
    if len(plain) < QUALITY_MIN_CHARS:
        return False
    return True


def pick(entry_clauses, cid, kws, core, limit, seen):
    """按辨识度打分挑选条款：label 命中节点号 > 核心概念命中 > 次关键词命中。"""
    scored = []
    tail = cid.split('.')[-1]
    for label, seg in entry_clauses:
        if not usable(seg):
            continue
        # 输出给 AI 的 requirement 用**剥标记后的纯文本**，避免把 <table> 壳灌进指令包
        body1 = re.sub(r'\s+', ' ', strip_html(seg)).strip()
        score = 0
        content_ok = bool((core and len(core) >= 4 and core in body1) or any(k in body1 for k in kws))
        # 条款号相符**且**内容相符才算高置信：不同文件的同名章节号含义可能完全不同
        # （如 GB 50433 的 5.1 是「防治区划分」，模板的 5.1 是「渣土来源及流向」）
        if (label == cid or re.fullmatch(r'[A-Z]\.' + re.escape(cid), label)) and content_ok:
            score = 4
        elif label == cid:
            score = 1
        elif core and len(core) >= 4 and core in body1:
            score = 3
        elif any(k in body1 for k in kws):
            score = 2
        if score < 2:
            continue
        # 编制依据罗列（连续出现多个《…》）不是具体要求，剔除
        if body1[:220].count('《') >= 3:
            continue
        if len(body1) < 16:
            continue
        scored.append((score, label, body1))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, label, body in scored:
        if len(out) >= limit:
            break
        if len(body) > MAXREQ:
            body = body[:MAXREQ] + '…（截断）'
        key = re.sub(r'[\s，。；：、]', '', body)[:50]
        if key in seen:
            continue
        seen.add(key)
        out.append({'clause': label, 'requirement': body, 'match_score': score})
    return out


def run(chapter=None, province=None, city=None, report_form=False):
    if report_form:
        # 报告表（附件3）分支：骨架不是章节而是两张表格区块，闸门目标取声明式节点集合
        targets = list(RULES.get('report_form_gate', {}).get('gate_targets', []))
        bad = [t for t in targets if t not in NODES]
        if bad:
            return {'chapter': '报告表', 'hard_constraints': [], 'warnings': [
                {'type': 'invalid_chapter',
                 'detail': 'report_form_gate.gate_targets 含模板中不存在的节点：%s' % ','.join(bad)}],
                'provisional': False, 'blocked': True, 'invalid_chapter': True,
                'block_reason': '报告表闸门配置无效', 'constraint_scope': {}}
    elif chapter not in NODES:
        return {'chapter': chapter, 'hard_constraints': [], 'warnings': [
            {'type': 'invalid_chapter', 'detail': '模板中不存在该章节号'}],
            'provisional': False, 'blocked': True, 'invalid_chapter': True,
            'block_reason': '章节号无效', 'constraint_scope': {}}
    else:
        targets = expand(chapter)

    warnings, constraints, seen = [], [], set()
    if SNAPSHOT_STALE:
        # 快照过期是时效问题，不置 blocked；强制警告并指引重建（rules.json snapshot_gate.stale_behavior）
        warnings.append({'type': 'snapshot_stale', 'detail': SNAPSHOT_WARNING})
    provisional = False
    node_specific = inherited = 0
    doc_cache = {}

    # 摘要版模板降权：它是压缩稿（已知缺漏 20 个三级节点与多处硬要求），
    # 只在权威来源（附件1/2/3）未提供足够条款时才作为兜底来源。
    DEPRIO = RULES.get('deprioritized_sources', [])
    entries = sorted(ZONE_A['entries'],
                     key=lambda e: 1 if any(d in e['file'] for d in DEPRIO) else 0)

    for e in entries:
        kind = status_kind(e['status'])
        ok, origin, reason = applies(e, targets, province, city)
        if not ok:
            if reason:
                warnings.append({'type': 'scope_inactive', 'source': label_of(e), 'detail': reason})
            continue
        if kind == 'excluded':
            warnings.append({'type': 'status_excluded', 'source': label_of(e),
                             'detail': '状态为「%s」，不得作为合规依据，仅作资料线索' % e['status']})
            continue
        if kind == 'blocked':
            warnings.append({'type': 'status_stale', 'source': label_of(e), 'status': e['status'],
                             'superseded_by': e['superseded_by'],
                             'detail': '该依据非现行有效；如需引用，必须替换为 superseded_by 指向的文件'})
            continue
        if kind == 'unknown':
            warnings.append({'type': 'status_unknown', 'source': label_of(e), 'status': e['status'],
                             'detail': '状态无法归类，需人工确认'})
            continue

        if any('待确认' in str(e.get(f, '')) for f in ('doc_number', 'effective_date', 'supersedes', 'publish_date')):
            provisional = True

        # 降权来源的兜底门槛：已有 >=3 条高置信条款时不再采用
        if any(d in e['file'] for d in DEPRIO):
            if len([c for c in constraints if c['match_score'] >= 3]) >= 3:
                continue

        if origin == 'node_specific':
            node_specific += 1
        else:
            inherited += 1

        if e['file'] not in doc_cache:
            doc_cache[e['file']] = split_clauses(read_doc(e['file']))
        kws = []
        for t in targets:
            kws.extend(keywords_of(t))
        core = core_keyword(targets[0])
        hits = pick(doc_cache[e['file']], targets[0], kws, core,
                    RULES['max_constraints_per_doc'], seen)
        # 精度优先：同一文档内若已有高置信条款（分>=3），丢弃低置信（分=2）条款
        if hits and any(h['match_score'] >= 3 for h in hits):
            hits = [h for h in hits if h['match_score'] >= 3]
        for h in hits:
            if len(constraints) >= RULES['max_constraints_per_chapter']:
                break
            constraints.append({
                'source': label_of(e),
                'source_file': e['file'],
                'clause': h['clause'],
                'requirement': h['requirement'],
                'origin': origin,
                'scope': e['scope'],
                'status': e['status'],
                'match_score': h['match_score'],
            })

    # 本段实现 rules.json blocked_rules.blocked_when 的两条（该组是**规格文本**，脚本不直接读）：
    # ①必需约束无任何现行 Zone A 依据覆盖、且无「全域」现行依据兜底 → node_specific + inherited == 0；
    # ②唯一适用依据 status 属 blocking_statuses 且 superseded_by 指向的替代件不在库中 →
    #   该依据在上面的 status 过滤里被 continue 掉，计数同样落到 0，故与①共用同一判定，
    #   差别只在 block_reason 里点明「替代文件：…」。改判定规则须改本段 + blocked_rules 文本。
    blocked, reason = False, None
    label = '报告表' if report_form else chapter
    if node_specific + inherited == 0:
        cand = [e for e in ZONE_A['entries']
                if ('全域' in e['chapter_relevance'] or targets[0] in e['chapter_relevance'])
                and status_kind(e['status']) in ('blocked', 'excluded')]
        blocked = True
        if cand:
            c = cand[0]
            reason = ('章节 %s 无任何现行 Zone A 依据可覆盖；库中相关依据「%s」状态为「%s」%s'
                      % (label, label_of(c), c['status'],
                         ('，替代文件：' + c['superseded_by'])
                         if c['superseded_by'] not in ('', '无', '待确认') else '，且未记录替代文件'))
        else:
            reason = '章节 %s 无任何 Zone A 依据可覆盖，需补充规范来源后方可写作' % label
    if not constraints and not blocked:
        warnings.append({'type': 'no_clause_hit',
                         'detail': '有现行 Zone A 依据，但未定位到与本章节直接对应的条款；'
                                   '需人工确认关键词或补充资料后再写作'})

    return {
        'chapter': '报告表' if report_form else chapter,
        'report_form': report_form,
        'expanded_nodes': targets,
        'hard_constraints': constraints,
        'warnings': warnings,
        'provisional': provisional,
        'blocked': blocked,
        'block_reason': reason,
        'constraint_scope': {'node_specific': node_specific, 'inherited': inherited,
                             'clause_count': len(constraints)},
        'snapshot': {'stale': SNAPSHOT_STALE, 'reasons': SNAPSHOT_REASONS},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chapter')
    ap.add_argument('--province', default=None)
    ap.add_argument('--city', default=None)
    ap.add_argument('--json-only', action='store_true')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--report-form', action='store_true',
                    help='报告表（附件3）分支闸门：按 report_form_gate.gate_targets 检查')
    a = ap.parse_args()
    if a.list:
        for n in TREE['nodes']:
            print('%-8s %-10s %s' % (n['chapter_id'], n['node_kind'], n['title']))
        return
    if not a.report_form and not a.chapter:
        ap.error('需要 --chapter 或 --report-form')
    r = run(a.chapter, a.province, a.city, report_form=a.report_form)
    if r.get('invalid_chapter'):
        sys.stderr.write('❌ %s\n   可用章节号用 --list 查（模板 %d 个节点）。\n'
                         % (r.get('block_reason') or '章节号无效', len(NODES)))
        raise SystemExit(2)
    print(json.dumps({k: v for k, v in r.items() if k != 'expanded_nodes'},
                     ensure_ascii=False, indent=1) if a.json_only else json.dumps(r, ensure_ascii=False, indent=1))
    if a.json_only:
        return
    print()
    print('--- 人类可读摘要 ---')
    print('章节:', r['chapter'], '（展开节点：%s）' % ','.join(r.get('expanded_nodes', [])))
    print('阻断:', '是' if r['blocked'] else '否', r['block_reason'] or '')
    print('待人工确认(provisional):', r['provisional'])
    print('硬约束条款: %d （节点专属依据 %d / 继承依据 %d）'
          % (len(r['hard_constraints']), r['constraint_scope'].get('node_specific', 0),
             r['constraint_scope'].get('inherited', 0)))
    for i, c in enumerate(r['hard_constraints'], 1):
        print('  %2d. [%s §%s | %s | 分%d] %s' % (i, c['source'], c['clause'], c['origin'],
                                                  c['match_score'], c['requirement'][:120]))
    for w in r['warnings']:
        print('  ⚠', w.get('type'), '-', w.get('detail', ''))
    if r['blocked']:
        sys.exit(3)      # 闸门 blocked：禁止写作（退出码契约）


if __name__ == '__main__':
    main()

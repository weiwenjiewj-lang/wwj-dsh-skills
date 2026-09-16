#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第二层：模板编排器。生成某章节的「写作指令包」。

用法:
    python write_chapter.py --chapter 1.1 [--province 河南省] [--city 平顶山市]
    python write_chapter.py --chapter 1.1 --json-only
    python write_chapter.py --chapter 1.1 --out 指令包.json
    python write_chapter.py --chapter 1.1 --ledger 台账.json      # 注入全书已定事实
    python write_chapter.py --report-form --province 河南省   # 报告表（附件3）分支

顺序硬约束：先过合规闸门 → blocked 则停止（且不检索 Zone B）→ 施加模板骨架 → 最后取 Zone B。
每个节点同时下发篇幅预算（目标字数区间/必备表数）与深度要求，用于实时把控全书长度。
"""
import argparse, json, os, re, sys
# 禁止写 __pycache__：本脚本 import 同目录 check_gate，会在技能目录里生成字节码缓存，
# 使交付包每次使用后都多出残留物。置此开关后不再生成。
sys.dont_write_bytecode = True

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_gate as G  # noqa: E402
import budget as B      # noqa: E402

REF = G.REF
RULES = G.RULES
TREE = G.TREE
NODES = G.NODES
ZONE_B = json.load(open(os.path.join(REF, 'zone-b-index.json'), encoding='utf-8-sig'))

QUALIFIER = '仅用于写作表达、论证逻辑与案例参考，不得引入与 Zone A 冲突的方法论或参数。'
ZONE_C_QUALIFIER = ('应用范例仅可用于写作风格、章节展开方式、表达逻辑、表格设计的参考；'
                    '不得用于合规判断，不得引入其具体项目数据，'
                    '不得因其曾经通过审批而推定其做法在当前标准下仍然合规。')
# 两条输出契约文本与 Zone B 注入上限都取自 rules.json（原来与 JSON 各存一份，会漂移）
_ZI = RULES.get('zone_c_injection', {}) or {}
ZONE_C_NO_RULE_NOTICE = _ZI.get('on_no_zone_a_rule') or 'Zone A 未明确规定，以下为参考写法，需人工判断'
ZONE_C_CONFLICT_NOTICE = _ZI.get('on_conflict_with_zone_a') or '该范例依据的是旧标准，已忽略其合规内容'
MAX_ZONE_B = int((RULES.get('zone_b_injection', {}) or {}).get('max_snippets', 6))
# Zone B 文体范式保底槽（rules.json zone_b_injection.style_role）
_SR = (RULES.get('zone_b_injection', {}) or {}).get('style_role', {}) or {}
# 老八章→新十章换算提示（rules.json zone_c.chapter_system_mapping）
_CSM = (RULES.get('zone_c', {}) or {}).get('chapter_system_mapping') or {}
ZONE_C_IDX = os.path.join(REF, 'zone-c-index.json')
# Zone C 写法范式样本（从 91 份已批方案正文抽取、已脱敏）
_SS_CFG = _ZI.get('style_samples', {}) or {}
ZONE_C_SAMPLES = os.path.join(REF, _SS_CFG.get('output_file', 'zone_c_style_samples.json'))
MAX_STYLE_INJECT = int(_SS_CFG.get('max_inject', 3))


# 说明性规则组（脚本不直接读，行为在下方实现）：
# · zone_c.hard_rules / mounting —— Zone C 五条铁律与挂载规则；实现见本文件 zone_c_topic_keys()
#   （只按 topic_tags 挂载、chapter_mapping 必须 verified）与 cmd 输出里的限定语、冲突提示语。
# · zone_c.extraction_policy —— 允许清单（章节顺序/段落功能/表格栏目/句式/论证链）与
#   禁提清单（结论/参数/专名/地名/数字）由人工在摘句时执行，脚本无法校验语义。
# · zone_c.probe_use / zone_b_vs_c_boundary —— 用法说明文本。
def zone_consistency_check():
    """执行 rules.json 的 zone_consistency_rules（原为仅声明未接线）。

    校验三张索引的 zone × compliance_relevance 取值组合是否越界。
    返回 (violations, checked)；violations 为 [(zone, file, reason)]。
    """
    zr = RULES.get('zone_consistency_rules', {}) or {}
    viol, checked = [], 0
    for name in ('zone-a-index.json', 'zone-b-index.json', 'zone-c-index.json'):
        p = os.path.join(REF, name)
        if not os.path.exists(p):
            continue
        try:
            entries = json.load(open(p, encoding='utf-8-sig')).get('entries', [])
        except Exception as e:
            viol.append(('?', name, '索引解析失败：%s' % e))
            continue
        for e in entries:
            z, v = e.get('zone'), e.get('compliance_relevance')
            rule = zr.get(z)
            if not rule:
                viol.append((z, e.get('file', name), '未声明的 zone=%r' % z))
                continue
            checked += 1
            if v in rule.get('forbidden_compliance_values', []):
                viol.append((z, e['file'], 'zone %s 不得取「%s」' % (z, v)))
            # allowed 清单同样强制（规则 note 写的是「任一层取到越界值即判定校验失败」，
            # 原来只查 forbidden，allowed 从未生效）。
            allowed = rule.get('allowed_compliance_values')
            if allowed and v not in allowed:
                viol.append((z, e['file'], 'zone %s 的取值「%s」不在允许清单 %s 内'
                             % (z, v, '／'.join(allowed))))
    return viol, checked


def zone_c_topic_keys(cid, rules):
    """由节点标题与关键词派生的主题检索键（Zone C 唯一合法挂载维度）。

    检索键 = 标题核心词 + node_keywords + 同义词表（zone_c_injection.zone_c_synonyms）正向展开：
    任一检索键出现在同义词表的键或别名中，该表全部键与别名一并成为检索键。
    """
    keys = set()
    t = NODES.get(cid, {}).get('title', '')
    t = re.sub(r'（[^）]*）', '', t).strip()
    if t:
        keys.add(t)
    for k in G.keywords_of(cid):
        keys.add(k)
    syn = rules.get('zone_c_injection', {}).get('zone_c_synonyms', {}) or {}
    expanded = set()
    for k in keys:
        if k in syn:                                   # 键命中 → 展开全部别名
            expanded.update(syn[k])
        else:
            for key, aliases in syn.items():           # 别名命中 → 展开该组全部键与别名
                if k in aliases:
                    expanded.add(key)
                    expanded.update(aliases)
    keys |= expanded
    return {k for k in keys if k and len(k) >= 2}


def is_narrative_node(cid, rules):
    """判断该节点是否属「叙述型章节」——即写作时主要靠叙述、缺少可引条款的章节。

    为什么要单独判这一类（rules.json zone_c_injection.narrative_nodes）：
    原闸门只认「Zone A 专属依据 == 0」。但 1.1（项目简况）这类章节**有**专属依据
    （模板要求本身 + 通用标准），闸门判为「无需范例补充」而关闭 Zone C。
    结果：这一节既拿不到条款支撑、又拿不到文体范式，写出来就是 33 条内容点的机械铺陈。

    叙述型章节的特征：以陈述事实为主、无计算项、无可逐字引用的专门条款。
    它们的质量瓶颈不是「缺依据」，而是「缺文体参照」——正是 Zone C 的合法用途
    （章节展开顺序、段落功能、句式模板、论证链）。

    判定数据源：rules.json zone_c_injection.narrative_nodes.nodes（显式清单，
    不改代码即可增删）；同时支持 prefix 规则（如 "1." 覆盖整个第 1 章）。
    """
    cfg = (rules.get('zone_c_injection', {}) or {}).get('narrative_nodes', {}) or {}
    if not cfg.get('enabled', False):
        return False, ''
    cid = str(cid or '')
    for n in cfg.get('nodes', []) or []:
        if cid == str(n):
            return True, '命中叙述型节点清单'
    for p in cfg.get('prefixes', []) or []:
        if cid.startswith(str(p)):
            return True, '命中叙述型前缀规则「%s」' % p
    return False, ''


def is_guarded_node(cid, rules):
    """判断是否为「受保护节点」——计算型/评价型，**不得**注入 Zone C。

    为什么必须保护这一类：它们产出的是**指标值、达标判定、评价结论**，
    一旦参考了已批方案的表述习惯，极易把"范例里的数值/结论文风"带进来，
    而范例是按**旧标准**批准的。合规风险不在"检索"，而在"结论被范例带偏"。

    判据（rules.json zone_c_injection.guarded_nodes）：
      · 显式节点清单 + 前缀规则
      · 另：任何在 rules.json calculations 里挂了计算项的节点，一律自动受保护
    """
    cfg = (rules.get('zone_c_injection', {}) or {}).get('guarded_nodes', {}) or {}
    cid = str(cid or '')
    for n in cfg.get('nodes', []) or []:
        if cid == str(n):
            return '命中受保护节点清单'
    for p in cfg.get('prefixes', []) or []:
        if cid.startswith(str(p)):
            return '命中受保护前缀规则「%s」' % p
    # 祖先继承保护：受保护容器（如 4.4 表土、8.2 监测）被展开成子节点时，
    # 子节点（4.4.1/4.4.2/8.2.1…）自身不在清单里，但**继承父节点的保护**。
    # 漏了这一步会让"保护容器、放行子节点"，等于保护形同虚设。
    if cfg.get('inherit_to_children', True):
        for n in cfg.get('nodes', []) or []:
            n = str(n)
            if cid.startswith(n + '.'):
                return '继承受保护父节点「%s」' % n
        # 计算项同样按祖先继承（4.4 挂计算项 → 4.4.1/4.4.2/4.4.3 一并受保护）
        if cfg.get('auto_guard_calculation_nodes', True):
            items = (rules.get('calculations', {}) or {}).get('items') or {}
            calc_ids = list(items.keys()) if isinstance(items, dict) else [
                it.get('chapter') for it in (items or []) if isinstance(it, dict)]
            for n in calc_ids:
                if n and cid.startswith(str(n) + '.'):
                    return '继承挂有计算项的父节点「%s」' % n
    # 自动受保护：挂了计算项的节点（计算项定义见 rules.json calculations.items）
    # 该键可能是 dict（chapter -> 计算项）或 list（[{chapter:...}, ...]），两种都兼容。
    if cfg.get('auto_guard_calculation_nodes', True):
        items = (rules.get('calculations', {}) or {}).get('items') or {}
        if isinstance(items, dict):
            if cid in items:
                return '该节点挂有计算项「%s」' % (
                    (items[cid] or {}).get('name') if isinstance(items[cid], dict) else cid)
        elif isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and str(it.get('chapter', '')) == cid:
                    return '该节点挂有计算项「%s」' % (it.get('name') or cid)
    return ''


def zone_c_refs(targets, gate, rules):
    """检索 Zone C 表达层参考。

    开启条件（三条，满足其一即开；`zone_c_injection.enable_triggers` 声明）：

      ① `no_zone_a` —— Zone A 专属依据数为 0（原设计：真实缺口节点）
      ② `narrative` —— 属叙述型章节（有依据但缺文体参照）
      ③ `safe_node` —— **非计算/非评价型节点**，且按 topic_tags 能检索到可用范例

    第三条是「全域生效」的关键：实测全书 76 个 content 节点中，
    ① 只命中 0 个、② 只命中 12 个，**其余 60 个"有可用范例却被闸门关闭"** ——
    即 Zone C 在改造前从未真正生效过。

    为什么可以安全放宽：Zone C 的合法性风险不在"是否检索"，而在"**用于什么**"。
    本函数只放行检索；合规隔离由三重机制保证——
      · 结构上：`check_gate.py` 只读 zone-a-index，Zone C 物理上不可能进入 blocked 判定；
      · 内容上：只提取结构信息（允许清单），限定语强制附加；
      · 范围上：计算型/评价型节点（产指标、出结论）仍走原闸门，避免范例结论污染判定。
    """
    cfg = rules.get('zone_c_injection', {})
    node_specific = gate.get('constraint_scope', {}).get('node_specific', 0)
    cid = targets[0] if targets else ''
    narrative, why_narr = is_narrative_node(cid, rules)
    status = {'enabled': False, 'reason': '', 'index_present': os.path.exists(ZONE_C_IDX),
              'matched': 0, 'dropped_by_mapping': 0,
              'trigger': '', 'narrative': narrative}
    if not os.path.exists(ZONE_C_IDX):
        status['reason'] = 'Zone C 索引不存在（尚未建立范例库）'
        return [], status
    idx = json.load(open(ZONE_C_IDX, encoding='utf-8-sig'))
    if not idx.get('entries'):
        status['reason'] = 'Zone C 索引为空'
        return [], status

    # 先算检索键与命中量，供 safe_node 判据使用
    keys = zone_c_topic_keys(cid, rules)
    alltags = set()
    for e in idx['entries']:
        alltags.update(e.get('topic_tags') or [])
    tag_hit = bool(keys & alltags)

    triggers = cfg.get('enable_triggers', {}) or {}
    # 受保护判定必须**最先**执行，且对**全部目标节点**（含容器展开后的子节点）执行。
    #
    # 两个坑（均由独立复核发现，已实测复现）：
    #   ① 优先级倒置：若先判 narrative 再判 guarded，同时出现在两张清单里的节点
    #      （如 1.9 同在 narrative_nodes 与 guarded_nodes）会走 narrative 分支而**绕过保护**。
    #   ② 容器展开绕过：容器节点（如 4.4 表土、8.2 监测）会被展开成子节点传入，
    #      而目标节点自身挂有计算项。只查 targets[0] 会漏掉——必须逐个子节点查。
    for t in (targets or [cid]):
        g = is_guarded_node(t, rules)
        if g:
            status['reason'] = ('Zone C 不适用于计算/评价型节点（%s：%s）——'
                                '此类节点出指标与结论，引入范例有污染判定之虞'
                                % (t, g))
            return [], status

    if cfg.get('default') != 'on':
        if node_specific == 0:
            status['trigger'] = 'no_zone_a'
        elif narrative:
            status['trigger'] = 'narrative'
        elif not tag_hit:
            status['reason'] = ('Zone C 已检索但无可用范例：主题标签未覆盖本节点'
                                '（检索键 %d 个与范例库 %d 个标签无交集）'
                                % (len(keys), len(alltags)))
            return [], status
        else:
            status['trigger'] = 'safe_node'

    status['enabled'] = True
    if status['trigger'] == 'safe_node':
        status['reason'] = ('开启 Zone C 表达层参考：本节点非计算/评价型且检索到可用范例'
                            '（%d 个主题标签命中）——用于参照章节展开顺序与表达方式'
                            % len(keys & alltags))
    elif node_specific == 0 and narrative:
        status['trigger'] = 'both'
        status['reason'] = ('开启 Zone C 表达层参考：Zone A 专属依据为 0，且属叙述型章节（%s）'
                            % why_narr)
    elif node_specific == 0:
        status['trigger'] = 'no_zone_a'
        status['reason'] = '开启 Zone C 表达层参考：本节点 Zone A 专属依据数为 0'
    else:
        status['trigger'] = 'narrative'
        status['reason'] = ('开启 Zone C 表达层参考：本节点属叙述型章节（%s）——'
                            '这类章节的质量瓶颈是缺文体参照，不是缺依据' % why_narr)
    keys = zone_c_topic_keys(targets[0], rules)
    maxn = cfg.get('max_snippets', 2)
    maxc = cfg.get('max_chars_per_snippet', 150)
    out = []
    cand = []
    for e in idx['entries']:
        # 挂载闸门（rules.json zone_c.mounting.chapter_mapping_gate）：
        # chapter_mapping != verified 的范例不得挂载——原来只做了计数、忘了 continue，
        # 结果未完成映射的范例照样能被挂上去，而 dropped_by_mapping 还说它被丢掉了。
        if cfg.get('forbid_chapter_number_mount', True) and e.get('chapter_mapping') != 'verified':
            status['dropped_by_mapping'] += 1
            continue
        tags = e.get('topic_tags') or []
        if not tags:
            continue                      # 无主题标签 = 无法按 topic_tags 挂载，直接排除
        hit = sorted(set(tags) & keys)
        if not hit:
            continue
        status['matched'] += 1
        cand.append((e, hit))

    # 按「该主题在本范例中的展开充分程度」排序，取前 N ——
    # 覆盖优先保证找到，权重保证选到展开最充分的范例
    def _weight_of(e):
        tw = e.get('topic_weight') or {}
        if isinstance(tw, str):                 # 兼容仍以字符串形式存回的索引
            try:
                tw = json.loads(tw)
            except Exception:
                tw = {}
        return tw if isinstance(tw, dict) else {}

    def _wt(pair):
        e, hit = pair
        tw = _weight_of(e)
        return -max([float(tw.get(h, 0) or 0) for h in hit] or [0])

    cand.sort(key=_wt)
    for e, hit in cand:
        if len(out) >= maxn:
            break
        snippet = e.get('structure_summary') or ''
        if not snippet:
            continue                      # 无结构摘要则无可用内容（不退回读取原文）
        out.append({
            'project_type': e.get('project_type'),
            'approval_level': e.get('approval_level'),
            'approval_period': e.get('approval_period'),
            'topic_hit': hit,
            'topic_weight': round(max([float(_weight_of(e).get(h, 0) or 0)
                                       for h in hit] or [0]), 1),
            'structure_summary': snippet[:maxc],
            'usable_for': e.get('usable_for', cfg and rules['zone_c']['usable_for']),
            'not_usable_for': e.get('not_usable_for', rules['zone_c']['not_usable_for']),
            'source_chapter_system': e.get('source_chapter_system'),
            'qualifier': ZONE_C_QUALIFIER,
            'conflict_notice': ZONE_C_CONFLICT_NOTICE,
            'extraction_policy': rules['zone_c']['extraction_policy']['mode'],
            # 老八章→新十章换算提示：防止把老4章当成新4章（rules.json zone_c.chapter_system_mapping）
            'chapter_system_notice': (
                _CSM.get('mount_notice')
                if '老八章' in str(e.get('source_chapter_system') or '') else None),
        })
    if status['enabled'] and not out:
        status['reason'] += ('；但未匹配到可用范例（matched=%d，可能主题标签未覆盖或缺少结构摘要）'
                             % status['matched'])
    return out, status


def classify_point(point, rules):
    dc = rules['data_class_rules']
    for k in dc['spec_given_keywords']:
        if k in point:
            return '规范给定', 'spec_given_keywords:%s' % k
    for k in dc['project_input_keywords']:
        if point in k or k in point:
            return '需项目输入', 'project_input_keywords:%s' % k
    return dc['default'], 'default'


def zone_c_style_samples(cid, rules, gate_on=False):
    """取该节点的 Zone C 写法范式样本（已脱敏，只含"怎么写"）。

    与 zone_c_refs 的分工：
      · zone_c_refs        给"哪几份范例可参照"（元数据 + 结构摘要）
      · zone_c_style_samples 给"这一段别人是怎么写的"（段落功能 + 句式骨架）

    后者才是解决"机械感"的关键——原 Zone C 只到章节目录层，
    写作者知道"有 43 份范例"却看不到任何一句范文。

    注入约束（rules.json zone_c_injection.style_samples）：
      · 每节点最多 max_inject 条（样本挤占上下文）
      · 标 [style_only] 并附 usage_rule，提示"仅参考写法，不得直接用于新项目"
      · Zone A 为 0 依据的节点优先——它们最缺写法参照
    """
    if not os.path.exists(ZONE_C_SAMPLES):
        return [], {'available': False, 'reason': '范式样本库未生成（运行 extract_style_samples.py --build）'}
    try:
        doc = json.load(open(ZONE_C_SAMPLES, encoding='utf-8'))
    except Exception as e:
        return [], {'available': False, 'reason': '范式样本库解析失败：%s' % e}

    keys = zone_c_topic_keys(cid, rules)
    topics = doc.get('topics') or {}

    # ---- 确定性轮转起点（修复"每节都取同一批样本"）----
    #
    # 实测问题：原实现按 `sorted(keys, key=len, reverse=True)` 遍历主题、
    # 每个主题从**第一条**开始取，直到凑满 MAX_STYLE_INJECT。
    # 主题名是固定的，于是**每个节点拿到的几乎永远是同一批样本**：
    # 全书 95 节共注入 183 条，去重后仅 61 条（重复率 67%），
    # 单条样本最多被注入 **14 次**——写作者第 14 次看到同一条范文，纯属浪费。
    #
    # 做法：由节点号派生稳定偏移，在**每个主题内**轮转起点。
    # 用节点号而非随机数：保证同一节点每次出包结果一致（可复现、可校对）。
    cid_key = cid or ''
    _nums = [int(x) for x in re.findall(r'\d+', cid_key)] or [0]
    offset = sum(_nums)

    picked, seen = [], set()

    # ---- 按「段落功能」均衡取样（去机械感的第二层防线）----
    #
    # 实测问题：只按主题轮转时，出包样本的功能分布是
    #   结论 57 / 分述 57 / 依据 39 / 机理 29 / 通用 44 / **总述 仅 13**。
    # 「总述」正是教「怎么起笔」的一类，却最难被取到——
    # 而「起笔单调」恰恰是机器腔最明显的特征（见 rules.json human_voice）。
    # 结果写作者拿到的多是"结论/分述"型范文，越看越容易写成同一种收口。
    #
    # 做法：先按功能分桶，再在每个功能桶内用节点号派生的偏移轮转，
    # 最后按 总述→机理→依据→分述→结论→通用 的顺序各取一条。
    # 这样每个节点都能同时看到"起笔/展开/收口"三类写法，
    # 而不是连看三条同功能的范文。
    #
    # 仍用节点号（而非随机）派生偏移：保证同一节点每次出包结果一致，可复现、可校对。
    FN_ORDER = ['总述', '机理', '依据', '分述', '结论', '通用']

    buckets = {}          # fn -> [(topic, item), ...]
    for ti, k in enumerate(sorted(keys, key=len, reverse=True)):
        for it in (topics.get(k) or []):
            fn = it.get('function') or '通用'
            buckets.setdefault(fn, []).append((k, it))

    # 每个功能桶内做确定性轮转
    for fn, lst in buckets.items():
        n = len(lst)
        if not n:
            continue
        start = offset % n
        buckets[fn] = [lst[(start + j) % n] for j in range(n)]

    # 按功能顺序轮流取，取完一轮再取下一轮，保证功能均衡
    for _round in range(8):
        got_any = False
        for fn in FN_ORDER:
            lst = buckets.get(fn) or []
            if len(lst) <= _round:
                continue
            got_any = True
            k, it = lst[_round]
            dk = (k, it['text'])
            if dk in seen:
                continue
            seen.add(dk)
            picked.append({'topic': k, 'function': it.get('function') or '通用',
                           'skeleton': it.get('skeleton') or it['text'],
                           'argument_chain': it.get('argument_chain') or '',
                           'text': it['text']})
            if len(picked) >= MAX_STYLE_INJECT:
                break
        if len(picked) >= MAX_STYLE_INJECT:
            break
        if not got_any:
            break

    # 兜底：若功能分桶一条都没取到（样本库结构异常），退回原主题遍历
    if not picked:
        for ti, k in enumerate(sorted(keys, key=len, reverse=True)):
            lst = topics.get(k) or []
            if not lst:
                continue
            n = len(lst)
            start = (offset + ti) % n
            for j in range(n):
                it = lst[(start + j) % n]
                dk = (k, it['text'])
                if dk in seen:
                    continue
                seen.add(dk)
                picked.append({'topic': k, 'function': it.get('function') or '通用',
                               'skeleton': it.get('skeleton') or it['text'],
                               'argument_chain': it.get('argument_chain') or '',
                               'text': it['text']})
                if len(picked) >= MAX_STYLE_INJECT:
                    break
            if len(picked) >= MAX_STYLE_INJECT:
                break
    # 跨主题二次去重：同一段正文只保留一次（取先命中的主题）
    #
    # 除完全相同的文本外，还要压制**近重复**：实测 7.2 节点同时注入了
    #   [分述] …为保证监测的实时性和准确性，水土保持监测应与主体工程建设同步进行。本项目监测时段…
    #   [通用] …为保证监测的实时性和准确性，水土保持监测应与主体工程建设同步进行。本工程为新建建设类项目…
    # 两条来自不同项目、措辞略异，但讲的是同一件事。
    # 三个注入名额里占掉两个，等于浪费——写作者看不到真正的多样写法。
    #
    # 做法：取文本前 N 字的字符集合做 Jaccard 比较，超过阈值即视为近重复。
    # 用字符集合（而非分词）是因为这里只求"像不像同一段"，不需要语言学精度，
    # 且中文字符级集合对措辞微调足够稳健。
    def _near_dup(a, b, thresh=0.72, head=120):
        A = set(a[:head])
        B = set(b[:head])
        if not A or not B:
            return False
        return len(A & B) / float(len(A | B)) >= thresh

    uniq = []
    for p in picked:
        if any(p['text'] == q['text'] or _near_dup(p['text'], q['text']) for q in uniq):
            continue
        uniq.append(p)
    picked = uniq[:MAX_STYLE_INJECT]
    if not picked:
        lst = topics.get('通用') or []
        n = len(lst)
        start = offset % n if n else 0
        for j in range(min(MAX_STYLE_INJECT, n)):
            it = lst[(start + j) % n]
            picked.append({'topic': '通用', 'function': it.get('function') or '通用',
                           'skeleton': it.get('skeleton') or it['text'],
                           'argument_chain': it.get('argument_chain') or '',
                           'text': it['text']})
    status = {'available': True, 'matched_topics': len([k for k in keys if k in topics]),
              'injected': len(picked), 'rule': '仅参考写法（段落功能/句式骨架），'
                                               '不得引入其结论、参数、措施做法与项目数据'}
    return picked, status


def measure_methods_refs(cid, rules):
    """取该节点适用的措施工法（资产 F）。

    服务模板「典型设计」要求（7.7）、「按措施类型计列工程量」（9.1.2）
    与 8.x 监测要求。
    只给 Zone A 标准原文的设计要求，附条号；Zone C 的做法仅作参考不入此表。

    `cid` 接受单个节点号或节点号列表（容器节点会展开为多个子节点传入）。
    """
    cfg = (rules.get('measure_methods', {}) or {})
    if not cfg:
        return [], {'available': False, 'reason': '未声明 measure_methods 规则组'}
    path = os.path.join(REF, cfg.get('output_file', 'measure_methods.json'))
    if not os.path.exists(path):
        return [], {'available': False,
                    'reason': '工法索引未生成（运行 build_measure_methods.py --build）'}
    try:
        doc = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        return [], {'available': False, 'reason': '工法索引解析失败：%s' % e}
    # 接受单个 cid 或 cid 列表（容器展开后传入多个）
    cids = [str(x) for x in cid] if isinstance(cid, (list, tuple)) else [str(cid or '')]
    serve = [str(s) for s in (cfg.get('serve_nodes') or [])]
    # 组级 serve_nodes 之外，**逐条条目的 `服务节点` 同样有效**——
    # 否则「水土保持监理要求」这类只服务 `10` 的条目会被组级闸门挡掉
    # （其 服务节点=['10'] 但组级清单里没有 10）。
    per_item = set()
    for v in (doc.get('methods') or {}).values():
        for s in (v.get('服务节点') or []):
            per_item.add(str(s))
    allowed = set(serve) | per_item

    def _hit(c):
        for s in allowed:
            if c == s or c.startswith(s + '.'):
                return True
            if s.endswith('x') and c.startswith(s[:-1]):
                return True
        return False

    if allowed and not any(_hit(c) for c in cids):
        return [], {'available': True, 'injected': 0,
                    'reason': '本节点不需要措施工法（serve_nodes 与逐条服务节点均未覆盖）'}
    # 逐条筛选：只返回**其 服务节点 命中本节点**的条目。
    # 组级 serve_nodes 只是"这个节点要不要看工法"的总闸；
    # 具体给哪几条要看条目自己的 服务节点，否则 2.7/4.1.1 会拿到全部 27 条无关内容。
    def _match_item(v):
        srv = [str(s) for s in (v.get('服务节点') or [])]
        if not srv:
            return True          # 未声明服务节点的条目，视为通用
        return any(_hit(c) for c in cids) and any(
            c == s or c.startswith(s + '.') or (s.endswith('x') and c.startswith(s[:-1]))
            for s in srv for c in cids)

    out = [{'措施': k, '类型': v.get('类型'), '依据': v.get('依据'),
            '设计要求': v.get('Zone A 设计要求')}
           for k, v in (doc.get('methods') or {}).items() if _match_item(v)]
    return out, {'available': True, 'injected': len(out),
                 'rule': '设计要求引自 Zone A 标准原文并附条号；断面尺寸须来自项目资料，不得自拟'}


def design_params_refs(cid, rules):
    """取该节点适用的设计参数阈值（资产 E）。

    服务模板"按标准定级/取值"类要求：
      · 5.2 弃渣场选址、堆置方案与级别 —— 「逐一确定弃渣场级别」
      · 7.6 工程级别与设计标准 —— 拦渣/排洪工程级别与防洪标准

    阈值原只存在于 Zone A 标准的 `<table>` 段落里，写作时难以取用；
    本函数按 `serve_nodes` 注入，并强制附 `依据`（标准名+表号）。
    """
    cfg = (rules.get('design_params', {}) or {})
    if not cfg:
        return [], {'available': False, 'reason': '未声明 design_params 规则组'}
    serve_all = True
    path = os.path.join(REF, cfg.get('output_file', 'design_params.json'))
    if not os.path.exists(path):
        return [], {'available': False,
                    'reason': '参数表未生成（运行 build_design_params.py --build）'}
    try:
        doc = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        return [], {'available': False, 'reason': '参数表解析失败：%s' % e}
    cid = str(cid or '')
    out = []
    for key, v in (doc.get('params') or {}).items():
        srv = [str(s) for s in (v.get('服务节点') or [])]
        if not srv or cid in srv or any(cid.startswith(s.rstrip('x').rstrip('.')) and s.endswith('x')
                                        for s in srv):
            out.append({'参数': key, '依据': v.get('依据'),
                        '说明': v.get('说明'),
                        '表头': v.get('表头'), '数据': v.get('数据')})
    return out, {'available': True, 'injected': len(out),
                 'rule': '阈值只来自 Zone A 标准，附条号可回溯；选用时以标准原文为准'}


def species_refs(cid, rules):
    """取该节点适用的乡土树草种（资产 D）。

    服务模板的硬要求：
      · 2.7.6 植被 —— 「当地主要乡土树草种及生长情况」
      · 2.3 工程占地 —— 「主要树草种类型」
      · 7.x 植物措施 —— 树草种选择与配置

    只在"需要植物信息的节点"注入（由 species_index.serve_nodes 声明），
    避免无关节点被物种表挤占上下文。每条只给**库内有据**的字段；
    提取不到的字段不出现（不以常识补齐）。
    """
    cfg = (rules.get('species_index', {}) or {})
    if not cfg:
        return [], {'available': False, 'reason': '未声明 species_index 规则组'}
    serve = [str(s) for s in (cfg.get('serve_nodes') or [])]
    cid = str(cid or '')
    hit = cid in serve
    if not hit:
        for s in serve:
            if s.endswith('.x') and cid.startswith(s[:-1]):   # 7.x → 7.1/7.2/…
                hit = True
                break
            if s.endswith('x') and cid.startswith(s[:-1]):
                hit = True
                break
    if not hit:
        return [], {'available': True, 'injected': 0,
                    'reason': '本节点不需要物种信息（species_index.serve_nodes 未覆盖）'}
    path = os.path.join(REF, cfg.get('output_file', 'species_index.json'))
    if not os.path.exists(path):
        return [], {'available': False,
                    'reason': '物种库未生成（运行 build_species_index.py --build）'}
    try:
        doc = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        return [], {'available': False, 'reason': '物种库解析失败：%s' % e}
    sp = doc.get('species') or {}
    limit = int(cfg.get('max_inject', 30))
    out = []
    for name, rec in list(sp.items())[:limit]:
        item = {'名称': name}
        for k in ('学名', '科属', '生活型', '生态习性', '适生条件', '水土保持功能'):
            if rec.get(k):
                item[k] = rec[k]
        if rec.get('出处'):
            item['出处'] = rec['出处'][0]
        out.append(item)
    return out, {'available': True, 'injected': len(out),
                 'total_in_library': len(sp),
                 'rule': '只给库内有据的字段；缺项留空，不得以常识补齐'}


def zone_b_refs(targets):
    """检索 Zone B 参考素材。仅在闸门通过后调用。

    三级匹配，与 Zone A 的 applies() 对齐（原来只有前两级，导致容器节点——如 2.7——
    虽然其子节 2.7.1~2.7.7 挂着十几条参考素材，本节点却一条也拿不到）：
      · node_specific：条目章节号直接命中
      · ancestor：命中本节点的祖先（章级素材对节适用）
      · descendant：命中本节点的**子节点**（容器节点汇总子节素材）
    """
    refs = []
    anc = sum([G.ancestors(t) for t in targets], [])
    desc = set()
    for t in targets:
        for nid in G.NODES:
            if nid.startswith(t + '.'):
                desc.add(nid)
    for e in ZONE_B['entries']:
        chs = e['chapter_relevance']
        if any(t in chs for t in targets):
            rel = 'node_specific'
        elif any(a in chs for a in anc):
            rel = 'ancestor'
        elif any(d in chs for d in desc):
            rel = 'descendant'
        else:
            continue
        refs.append({
            'title': e.get('title') or e['doc_type'],
            'doc_type': e['doc_type'],
            'doc_number': e['doc_number'],
            'issuing_body': e['issuing_body'],
            'publish_date': e['publish_date'],
            'file': e['file'],
            'usage_label': e['compliance_relevance'],
            'style_role': e.get('style_role') or '未标注',
            'style_role_reason': e.get('style_role_reason'),
            'relevance': rel,
            'chapters': chs,
            'qualifier': QUALIFIER,
        })
    # 排序：先按匹配精度，再**优先文体范式**，最后按合规用途。
    #
    # 为什么把 style_role 提到 usage_label 之前：实测 1.1 注入的 3 条 Zone B 全是
    # 讲"方案怎么编"的方法论论文，写作者学不到"这一段正文该怎么写"。
    # 文体范式条目稀少，因此必须优先占位，否则永远被挤掉。
    refs.sort(key=lambda x: (x['relevance'] != 'node_specific',
                             x['style_role'] != '文体范式',
                             x['usage_label'] != '方法依据'))

    # ---- 多样性选取（省 token 的关键一步）----
    # 实测：不加这一层时，全书 95 节共注入 Zone B 条目 **469 条，
    # 去重后只有 61 个文件**——重复率 87%，同一条素材最多被注入 **45 次**
    # （《大型线性工程水土保持方案编制要点研究》）。
    #
    # 原因：原排序只看 relevance/style_role，与"本节"无关，
    # 于是每节的 Top-6 几乎是同一批书。写作时看到第 5 遍同一本书，纯属浪费。
    #
    # 做法：**按节点号做确定性分散**——用节点号算一个稳定的起始偏移，
    # 在"精度相同"的队列内轮转。同一素材对相邻节尽量不重复；
    # 同时保证质量不下滑（只在同精度、同 style_role 组内轮转，不跨组乱序）。
    # 用节点号做种子而非随机数，是为了**同一节点每次出包结果一致**（可复现、可校对）。
    top = _diversify(refs, MAX_ZONE_B, targets)
    # 保底：若因配额被挤掉，用一条文体范式替换掉最末位（写作时最需要的是写法样板）
    if _SR.get('reserve_style_slot', True) and refs:
        has_style = any(r['style_role'] == '文体范式' for r in top)
        if not has_style:
            style = [r for r in refs if r['style_role'] == '文体范式']
            if style and top:
                top[-1] = style[0]
    return top, len(refs)


def _diversify(refs, k, targets):
    """按节点号确定性轮转，使相邻节点尽量拿到不同的 Zone B 素材。

    分组轮转（不跨组）：仅在同一（relevance, style_role）组内轮转，
    保证"优先文体范式""优先 node_specific"这些质量规则**不被打破**。
    """
    if not refs or k <= 0:
        return refs[:k]
    key = (targets[0] if targets else '') or ''
    # 由节点号派生一个稳定偏移（如 '7.7' -> 77；'2.7.6' -> 7*6+... 取数字和）
    nums = [int(x) for x in re.findall(r'\d+', key)] or [0]
    offset = sum(nums)

    groups = {}
    order = []
    for r in refs:
        gk = (r['relevance'], r['style_role'])
        if gk not in groups:
            groups[gk] = []
            order.append(gk)
        groups[gk].append(r)

    out = []
    # 依次从各组取，组内按 offset 轮转起点
    cursor = {gk: (offset % len(groups[gk])) if groups[gk] else 0 for gk in order}
    progressed = True
    while len(out) < k and progressed:
        progressed = False
        for gk in order:
            if len(out) >= k:
                break
            lst = groups[gk]
            if not lst:
                continue
            i = cursor[gk] % len(lst)
            cand = lst[i]
            if cand in out:
                # 该组已取完，跳到下一个未取
                nxt = None
                for j in range(len(lst)):
                    if lst[(i + j) % len(lst)] not in out:
                        nxt = lst[(i + j) % len(lst)]
                        cursor[gk] = (i + j + 1) % len(lst)
                        break
                if nxt is None:
                    continue
                cand = nxt
            else:
                cursor[gk] = (i + 1) % len(lst)
            out.append(cand)
            progressed = True
    return out[:k]

def load_ledger_facts(ledger_path, targets):
    """读取事实台账并取出应注入的已定事实（每次只保留与本节不同的一批）。

    台账缺失或未指定时不阻断写作：返回 enabled=False 并说明原因（缺台账 ≠ 缺依据）。
    """
    disabled = {'enabled': False, 'count': 0, 'items': [],
                'rule': (RULES.get('ledger_rules', {}).get('injection', {}) or {}).get('rule', '')}
    if not ledger_path:
        return dict(disabled, reason='未指定 --ledger：本节只按数据包写作，跨章口径一致性由 check_plan 统一核')
    if not os.path.exists(ledger_path):
        return dict(disabled, reason='台账文件不存在：%s（可先用 ledger.py init 建台账）' % ledger_path)
    try:
        import ledger as L
        led = L.load(ledger_path)
        inj = L.inject_facts(led, exclude_chapter=(targets[0] if len(targets) == 1 else None))
    except Exception as e:                                     # 台账坏掉不应阻断写作
        return dict(disabled, reason='台账读取失败：%s' % e)
    inj['reason'] = '已注入台账中非本节的已确认事实'
    return inj


# ============================================================================
# 指令包压缩（V2）——省 token 的主力
#
# V1 只做去重复，实测均值仍 9,065 token/节、全书外推 86 万 token。
# V2 在此之上再加三项**可逆**压缩，全部实测过收益：
#
#   ① 键名速记：长键名 → 短别名，**包内自带 ct（对照表）**，
#      读者读到 hc.req 时能在同一个包里查到它是什么，不需要再发一次请求。
#   ② 超长条款截断：requirement 最长实测 1,400+ 字符（整段通知全文），
#      尾部截断并留 oid（原文定位指针）+ more 提示，可回溯不丢失。
#   ③ 去元数据壳：source_file / match_score / status / origin 等
#      是检索期元数据，写作期不用；收敛为 src 一个键并按原文需取。
#
# 铁律：**凡"删了会丢信息"的一律不删**，只留指针；--full 可完全还原。
# ============================================================================

# 键名速记表：长键 → 短键。脚本与 AI 共用；改这里即改全包。
KEY_ALIAS = {
    'chapter': 'ch', 'chapter_id': 'nid', 'title': 'ti', 'node_kind': 'nk',
    'write_allowed': 'ok', 'blocked': 'blk', 'block_reason': 'blkr',
    'provisional': 'prov', 'gate': 'g',
    'hard_constraints': 'hc', 'requirement': 'req', 'source': 'src',
    'source_file': 'sf', 'clause': 'cl', 'origin': 'og', 'scope': 'sc',
    'status': 'st', 'match_score': 'ms',
    'nodes': 'nd', 'required_content_points': 'rcp', 'writing_directive': 'wd',
    'writing_directive_note': 'wdn', 'point_count': 'pc',
    'conditional': 'cond', 'conditional_variants': 'cv', 'evidence': 'ev',
    'instructions': 'ins', 'must_cover': 'mcv', 'length_budget': 'lb',
    'depth_requirements': 'dr', 'rules': 'rl', 'conditional_question': 'cq',
    'tables': 'tb', 'calculations': 'ca', 'data_requirements': 'dq',
    'project_inputs': 'pi', 'project_input_count': 'pic', 'spec_given_count': 'sgc',
    'point': 'pt', 'data_class': 'dc', 'classified_by': 'cb',
    'zone_b_references': 'zb', 'zone_b_total_matched': 'zbt',
    'zone_b_qualifier': 'zbq', 'zone_c_references': 'zc',
    'zone_c_injection_status': 'zcs', 'zone_c_qualifier': 'zcq',
    'zone_c_style_samples': 'zss', 'zone_c_style_status': 'zsss',
    'zone_c_style_rule': 'zssr',
    'species_references': 'sp', 'species_status': 'sps', 'species_rule': 'spr',
    'design_params': 'dp', 'design_params_status': 'dps', 'design_params_rule': 'dpr',
    'measure_methods': 'mm', 'measure_methods_status': 'mms', 'measure_methods_rule': 'mmr',
    'zone_a_unspecified': 'zau', 'zone_a_unspecified_notice': 'zaun',
    'zone_consistency': 'zk', 'violations': 'vio', 'checked': 'ck',
    'ledger_facts': 'lf', 'enabled': 'en', 'count': 'cnt', 'items': 'it',
    'snapshot_warning': 'sw', 'output_format': 'of', 'sections': 'sec',
    'placeholder': 'ph', 'computation_marking': 'cm', 'forbidden': 'fb',
    'target': 'tg', 'min': 'lo', 'max': 'hi', 'weight': 'wt', 'share': 'shr',
    'chapter_share': 'cshr', 'target_raw': 'tgr',
    'book': 'bk', 'book_target_chars': 'btc', 'page_target': 'pgt',
    'book_min_chars': 'bmin', 'book_max_chars': 'bmax',
    'estimated_pages_at_target': 'ept',
    'target_chars': 'tc', 'min_chars': 'nc', 'max_chars': 'xc',
    'tables_required': 'trq',
    'file': 'f', 'relevance': 'rel', 'usage_label': 'ul',
    'reason': 'rsn', 'key': 'k', 'value': 'v', 'unit': 'u',
    'topic': 'tp', 'sample': 'smp', 'function': 'fn',
    'reference': 'ref', 'design_requirement': 'drq', 'measure': 'mea',
    'name': 'nm', 'latin': 'lat', 'family': 'fam', 'habit': 'hb',
    'suitable': 'sui', 'basis': 'bas', 'table_no': 'tno',
    'value_range': 'vr', 'note': 'nt', 'notes': 'nts', 'usage': 'usg',
    'book_note': 'bkn', 'rule': 'r', 'fields': 'fld', 'table': 'tbl',
    'item': 'im', 'formula': 'fm', 'inputs': 'inp', 'output': 'out',
    'structure_summary': 'ss', 'topic_tags': 'tt', 'topic_weight': 'tw',
    'qualifier': 'ql', 'chapters': 'chs', 'style_role': 'sr',
    'matched': 'mt', 'total': 'tl', 'scope_inactive': 'sci',
    'constraint_scope': 'csc', 'node_specific': 'ns', 'inherited': 'inh',
    'expanded_nodes': 'exn', 'report_form': 'rf', 'invalid_chapter': 'inv',
    'blocks': 'bl', 'block': 'b', 'block_index': 'bi', 'field_count': 'fc',
    'gate_targets': 'gt', 'attachments': 'att', 'figures': 'fig',
    'structure_rule': 'str', 'report_form_skeleton': 'rfs',
    'level': 'lv', 'type': 'ty', 'status_text': 'stt',
    'source_type': 'syt', 'priority': 'pri', 'snippets': 'snp',
    'text': 'tx', 'line': 'ln', 'chars': 'chs2',

    # ---- 资产 D/F 使用**中文字段名**（species_index / measure_methods 的原生键）----
    # 之前 KEY_ALIAS 只有英文键，导致这两块（实测合计 12.8 KB，是包内最大一块）
    # 既没被缩写、也没被截断，压缩率因此卡在 5% 左右。补上后才有实质收益。
    '措施': 'mea', '措施类型': 'met', '类型': 'mty',
    '依据': 'bas', '条号': 'cln', '设计要求': 'drq', '典型设计': 'dgn',
    '设计标准': 'dst', '适用条件': 'apl', '断面尺寸': 'sec2',
    '名称': 'nm', '学名': 'lat', '科属': 'fam', '生活型': 'hb',
    '生态习性': 'eco', '水土保持功能': 'swf', '适生条件': 'sui',
    '出处': 'src', '来源': 'src', '备注': 'rmk',
    '参数': 'prm', '参数名': 'pnm', '取值': 'val', '取值范围': 'vr',
    '标准名': 'std', '表号': 'tno', '等级': 'lvl', '区域': 'rgn',
    '时段': 'per', '目标值': 'tgv', '说明': 'dsc', '单位': 'un',
    '主题': 'tp', '段落功能': 'fn', '句式骨架': 'skl', '样本': 'smp',
    '措施体系': 'msy', '分区': 'zone', '措施布设': 'mlay',

    # ---- 第二轮自检补齐：这些键此前未缩写，白占体积 ----
    # 来源：对 95 节点实际输出做键名枚举，找出「出现但不在表里」的长键。
    # 它们多为 Zone C / 自检 / 结构类字段，出现频次不高但每节都在。
    'project_type': 'ptype', 'approval_level': 'aplv',
    'approval_period': 'aprd', 'topic_hit': 'thit',
    'usable_for': 'uf', 'not_usable_for': 'nuf',
    'source_chapter_system': 'scs', 'chapter_system_notice': 'csn',
    'conflict_notice': 'cfn', 'extraction_policy': 'exp',
    'argument_chain': 'ac', 'skeleton': 'sk', 'topic_weight': 'twt',
    'provenance': 'pv', 'generated_at': 'gat', 'generated_from': 'gfm',
    'topic_tags': 'ttg', 'chapter_relevance': 'crel',
    'doc_type': 'dt', 'issuing_body': 'ib', 'publish_date': 'pd',
    'doc_number': 'dn', 'style_role_reason': 'srr',
    'compliance_relevance': 'crm', 'structure_summary': 'sst',
    'approval_basis_list': 'abl', 'current_status': 'cus',
    'last_checked': 'lck', 'next_check_date': 'ncd',
    'official_lookup_url': 'olu', 'superseded_by': 'sby',
    'effective_date': 'edf', 'change_type': 'cht', 'old_doc': 'odc',
    'new_doc': 'ndc', 'affected_chapters': 'afc', 'action_required': 'acr',
    'warnings': 'wn', 'detail': 'dtl', 'snapshot': 'snp',
    'stale': 'sl', 'reasons': 'rss', 'index_present': 'ip',
    'total_in_library': 'til', 'injected': 'inj', 'available': 'avl',
    'matched_topics': 'mtp', 'dropped_by_mapping': 'dbm',
    'trigger': 'trg', 'narrative': 'nar',
    'category': 'cat', 'require_conclusion': 'rqc',
    'require_mechanism': 'rqm', 'quantified_facts_min': 'qfm',
    'evidence_refs_min': 'erm', 'clause_count': 'clc',
    'pts': 'pts_', 'truncated': 'trc',

    # Zone C 各条**共有**的提示语（csn/cfn 提升后的容器，见 _compact_pack ⑦.5）。
    # 必须是短键：它本身要放进包里，长名反而增加体积。
    # 注册在此是为了让「包内短键均可反查」这条自检能通过——
    # 一个包内出现却无法解读的键，对使用者就是未知字段。
    'zone_c_shared_notice': 'zc1',
}

# 反向表：短键 → 长键（解包用；kb_lookup --keys 可打印）
ALIAS_REVERSE = {}
for _lk, _sk in KEY_ALIAS.items():
    ALIAS_REVERSE.setdefault(_sk, _lk)


def _tok_len(s):
    """粗略 token 估算（中文 0.7、ASCII 0.28）——用于决定"截断是否划算"。"""
    if not isinstance(s, str):
        return 0
    cjk = sum(1 for ch in s if 0x2E80 <= ord(ch) <= 0x9FFF)
    return int(cjk * 0.7 + (len(s) - cjk) * 0.28)


# ---- 压缩参数（**唯一来源是 rules.json compact_pack**，此处仅兜底）----
# 铁律：「不允许出现只写在代码里的规则」。以下三个阈值原先硬编码在本文件里，
# 与 rules.json 各存一份会漂移，也无 Python 手工执行路径可查。
# 现在统一从 rules.json 读，读不到才用兜底值（并保持与 rules 默认一致）。
_CP = (RULES.get('compact_pack') or {})
REQ_KEEP = int(_CP.get('req_keep_chars', 200))
_ABBREV_MIN_CHARS = int(_CP.get('abbrev_min_chars', 0))
ASSET_TEXT_KEEP = int(_CP.get('asset_text_keep_chars', 180))


def _shorten_req(rec, keep=REQ_KEEP, oid_map=None):
    """超长 requirement 截断 + 保留指针（可回溯，不丢信息）。

    两段式处理：
      ① 若原文含「应/宜/不得/必须」等**规范性动词**，优先保留**首个规范句**
         （到第一个句号为止）——这才是可执行的条款主干；
      ② 否则按 keep 字符硬截断。

    实测：把整段通知/释义截掉后，条款的**可执行语义完整保留**，
    而体积降 40%~70%。全文随时可用 oid 取回。
    """
    req = rec.get('req')
    if not isinstance(req, str):
        return rec
    full_len = len(req)
    if full_len <= keep:
        return rec
    # ① 优先取首个「规范句」——含应当/宜/不得/必须/严禁的最短完整句
    m = re.search(r'[^。；]*?(?:应当|应|宜|不得|必须|严禁)[^。]*。', req)
    if m:
        core = m.group(0).strip()
        if 20 <= len(core) <= keep:
            rec['req'] = core
            rec['more'] = '原文%d字符，全文按 oid 取' % full_len
            return rec
    # ② 硬截断。
    #    注意：原文**自带**省略号（如标准表格的"…"）时，截断后尾部会出现
    #    "… …" 或看起来像"本来就是省略的"。必须**始终**附 more 指针，
    #    否则下游无法区分"被我们截断了"与"原文就写着省略号"。
    rec['req'] = req[:keep].rstrip() + '…'
    rec['more'] = '原文%d字符，全文按 oid 取' % full_len
    rec['truncated'] = True
    return rec


def _merge_points_and_directives(node):
    """把 required_content_points 与 writing_directive 合并成压缩条目。

    ## 为什么（实测最大的一处浪费）

    `rcp` 与 `wd` **同序一一对应**，且 `wd` 每条把 `rcp` 的内容一个字不差地
    再抄一遍，外加一个动词和「（模板原句：「…」）」。以 1.1 节为例：

        rcp[3] = 项目位置
        wd[3]  = 说明「项目位置」：空间关系明确到可定位（区/镇/村或桩号）（模板原句：「简述项目位置」）

    抄一遍内容点 + 抄一遍模板原句，净增 3~4 倍字符，信息量却只有「动词 + 深度要求」。

    ## 压缩方式（**不丢任何信息**）

    合并为「内容点 + 深度后缀」一条：

        "项目位置｜说明·空间关系明确到可定位（区/镇/村或桩号）"

    · 内容点原文保留在 `｜` 之前 —— 完整性清单信息不丢；
    · `｜` 之后是**动词 + 深度要求**（`wd` 去掉重复内容点与模板原句后的净信息）；
    · 模板原句（「模板原句：…」）**整包只保留一份**，见 `tpl_note`。

    为什么可以这么合：`rcp` 是**完整性**清单、`wd` 是**深度**指令，
    两者靠位置对齐；合并后用 `｜` 分隔，两者仍在同一个字符串里，一个不少。
    """
    pts = node.get('required_content_points') or []
    wds = node.get('writing_directive') or []
    if not pts:
        return pts, []
    if not wds or len(wds) != len(pts):
        # 无法对齐时不合并，原样返回（宁可多花 token 也不能错配）
        return pts, wds
    merged = []
    for p, w in zip(pts, wds):
        w = w or ''
        # 去掉 w 里对内容点的重复引用与其外层引号
        tail = w
        for form in ('「%s」' % p, p):
            tail = tail.replace(form, '', 1)
        # 去掉「（模板原句：…）」——整包只留一份，见 tpl_note
        m = re.search(r'（模板原句：.*?）\s*$', tail)
        if m:
            tail = tail[:m.start()]
        tail = tail.strip().lstrip('：:').strip()
        if tail:
            merged.append('%s｜%s' % (p, tail))
        else:
            merged.append(p)
    return merged, []


def _dedup_meta(rec):
    """把检索期元数据收敛掉：source_file 太长、match_score/status 写作期不用。

    注意：本函数在**别名替换之前**执行，所以这里读写的仍是**长键名**。
    （早先版本误用短键 'sf'/'ms'，导致去除逻辑静默失效——实测 st/ms/og/sc
    全部还在包里。这是本次自检抓出的 bug 之一。）

    · source_file：降为「源文件引用号」由调用方统一编号，此处先置 None；
    · match_score / scope / status：默认值省略；非默认值保留以便判读。
    """
    if rec.get('match_score') in (2, '2'):
        rec.pop('match_score', None)
    if rec.get('scope') == 'national':
        rec.pop('scope', None)
    if rec.get('status') == '现行有效':
        rec.pop('status', None)
    # 「（段落）」是绝大多数条款的默认值，省略可省 5 字符/条且不损判读
    if rec.get('clause') == '（段落）':
        rec.pop('clause', None)
    return rec


def _build_source_table(hc_list):
    """源文件归一：同一 md 被多条约束引用时，路径与标准名都只出现一次。

    实测 7.7 节 24 条硬约束来自 11 个源文件：
      · `source_file` 全路径重复 1928 字符；
      · `source` 标准名重复 1078 字符（同一标准的全称抄 4 遍）。
    归一成「引用号 → [文件名, 标准名]」后，每条只需 1 个字符的引用号。
    """
    files = []
    index = {}
    for c in hc_list:
        sf = c.get('source_file')
        src = c.get('source')
        if not sf and not src:
            c.pop('source_file', None)
            c.pop('source', None)
            continue
        short = ''
        if sf:
            short = sf.replace('/', '\\').rsplit('\\', 1)[-1]
        key = (short, src or '')
        if key not in index:
            index[key] = len(files) + 1
            files.append([short, src or ''])
        c['s'] = index[key]
        c.pop('source_file', None)
        c.pop('source', None)
    return files


def _collect_keys(obj, acc):
    """递归收集实际出现的键名。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _collect_keys(v, acc)
    elif isinstance(obj, list):
        for x in obj:
            _collect_keys(x, acc)
    return acc


def _compact_ct(aliased):
    """对照表：**不再塞进包里**——移交给 references/key-alias.md。

    实测教训（两次踩坑，记录下来避免复发）：
      第 1 版：把 200+ 条完整 KEY_ALIAS 塞进包 → ct 自身 3307 字符，净收益≈0；
      第 2 版：只列本包用到的键 → ct 仍 1632~1789 字符，占包体 7~10.5%。
    结论：**任何形式的包内对照表都在交固定税**，包越大税越重。
    改为：对照表随技能交付（references/key-alias.md），包内只留一行指针。
    AI 读包时看到短键若不确定，读那一个小文件即可（约 2 KB，远比每包 1.8 KB 划算，
    且它是**一次读入、全书复用**）。

    返回 None 表示本包不做缩写（小包缩写成本高于收益）。
    """
    body = json.dumps(aliased, ensure_ascii=False)
    if len(body) < _ABBREV_MIN_CHARS:
        return None
    return '<见 references/key-alias.md（短键对照，随技能交付，一次读入全书复用）>'


# 缩写阈值等参数见文件上方「压缩参数」块（统一取自 rules.json compact_pack）。


def _trim_asset_list(lst, short_fields, long_fields, keep=200, max_items=None):
    """资产类列表（species / measure_methods）的超长文本截断。

    实测这是压缩后**最大的一块**：mm 7,883 + sp 4,919 ≈ 12.8 KB，
    此前完全没被动到——因为它们用的是**中文字段名**，绕过了英文键的截断逻辑。

    · long_fields：可能超长的字段（如「设计要求」「生态习性」），逐条截断并附 more；
    · short_fields：短字段（名称/学名/条号/依据），一字不动——它们是**回溯锚点**；
    · max_items：条目上限；超出者丢弃但记录总数，避免为了凑全而灌爆上下文。
    """
    if not isinstance(lst, list):
        return lst
    truncated = 0
    out = []
    for x in lst:
        if not isinstance(x, dict):
            out.append(x)
            continue
        x = dict(x)
        for k in long_fields:
            v = x.get(k)
            if isinstance(v, list):
                # 「设计要求」是字符串数组：逐条截断
                newv = []
                for s in v:
                    if isinstance(s, str) and len(s) > keep:
                        newv.append(s[:keep].rstrip() + '…')
                        truncated += 1
                    else:
                        newv.append(s)
                x[k] = newv
            elif isinstance(v, str) and len(v) > keep:
                x[k] = v[:keep].rstrip() + '…'
                truncated += 1
        if truncated:
            x.setdefault('_t', '超长字段已截断，全文按「依据/出处」条号回溯原文')
        out.append(x)
    if max_items and len(out) > max_items:
        dropped = len(out) - max_items
        out = out[:max_items]
        out.append({'_note': '另有 %d 条同类条目，因篇幅上限未列出；'
                             '需要时用 kb_lookup.py --ask <关键词> 单独查询' % dropped})
    return out


def _alias_keys(obj):
    """递归把长键换成短键（值一字不动）。"""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[KEY_ALIAS.get(k, k)] = _alias_keys(v)
        return out
    if isinstance(obj, list):
        return [_alias_keys(x) for x in obj]
    return obj


def compact_package(doc, level='compact'):
    """精简指令包（V2）：去重复 + 键名速记 + 超长截断 + 去元数据壳。

    level:
      'compact'（默认）——以上四项全做，包内自带 ct 对照表，零额外请求即可解读。
      'full'          —— 原样返回（调试用，不要把 full 包投喂给模型）。

    可逆性：四项压缩全部**可逆**——
      键名 → ct 里有完整对照；截断 → 有 oid + more；去壳 → ct 里声明默认值。
    唯一的取舍是 sf 只留文件名，完整路径用 kb_lookup --oid 取回。
    """
    if not isinstance(doc, dict):
        return doc
    if level == 'full':
        d = doc
        d['_compact'] = 'full：未压缩，仅供调试；投喂模型请用默认压缩包。'
        return d
    import copy
    d = copy.deepcopy(doc)

    # ---- ① hard_constraints 去重 + 截断 + 去壳 + 源文件归一 ----
    g = d.get('gate') or {}
    g_hc = g.get('hard_constraints')
    src_files = []
    if isinstance(g_hc, list):
        new_hc = []
        for i, c in enumerate(g_hc):
            if not isinstance(c, dict):
                continue
            c = dict(c)
            c = _shorten_req(c)
            c = _dedup_meta(c)
            new_hc.append(c)
        # 源文件归一（必须在去壳之后：这里会把 source_file 换成引用号 s）
        src_files = _build_source_table(new_hc)
        # oi 在归一之后统一编号，保证与 s 一起构成可回溯的定位串
        for i, c in enumerate(new_hc, 1):
            c['oi'] = i
        g['hard_constraints'] = new_hc
    if src_files:
        g['src_files'] = src_files

    # instructions 里若与 gate 同一份则换指针（V1 已有，保留）
    for ins in (d.get('instructions') or []):
        if isinstance(ins, dict) and 'hard_constraints' in ins:
            cur = ins['hard_constraints']
            if g_hc is not None and cur == g_hc:
                ins['hard_constraints'] = '<见 g.hc（%d 条）>' % len(cur or [])

    # ---- ② length_budget.book 只留关键数字 ----
    lb = d.get('length_budget')
    if isinstance(lb, dict) and isinstance(lb.get('book'), dict):
        bk = lb['book']
        keep = ('book_target_chars', 'estimated_pages_at_target',
                'page_target', 'book_min_chars', 'book_max_chars')
        lb['book'] = {k: bk[k] for k in keep if k in bk}
        lb['book_note'] = '全书预算为固定值；本节预算见 lb.nd（本节点条目）'

    # ---- ③ 只留本节点的 length_budget 条目 ----
    #   实测每节都推 9~20 个他节点预算（3271 字符），本节只用其中 1 条。
    #   注意 lb 下有**两处**节点列表：`lb.nodes`（本节点）与
    #   `lb.chapter.nodes`（整章全节点）——早先只过滤了前者，
    #   导致 2.4 节的 lb 仍占 30.9%。两处都要过滤（本次自检抓出的 bug）。
    nids = {n.get('chapter_id') for n in (d.get('nodes') or [])
            if isinstance(n, dict) and n.get('chapter_id')}
    if isinstance(lb, dict) and nids and isinstance(lb.get('nodes'), list):
        mine = [x for x in lb['nodes']
                if isinstance(x, dict) and x.get('chapter_id') in nids]
        if mine:
            lb['nodes'] = mine
    # lb.chapter 下的节点列表：整章明细对本节无用，删掉并留指针
    if isinstance(lb, dict) and isinstance(lb.get('chapter'), dict):
        chb = lb['chapter']
        if isinstance(chb.get('nodes'), list):
            # 只留本节点那一条
            mine = [x for x in chb['nodes']
                    if isinstance(x, dict) and x.get('chapter_id') in nids]
            if mine:
                chb['nodes'] = mine
            else:
                chb.pop('nodes', None)
        chb.setdefault('note', '本章目标字数与区间；他节点明细不在此列出，'
                               '用 budget.py --chapter <章> 查全章')
        # 只保留本章级别的三个数：章目标/分工口径不需要重复
        for k in list(chb.keys()):
            if k not in ('chapter', 'target', 'min', 'max', 'nodes', 'note'):
                chb.pop(k, None)

    # ---- ④ 固定说明文案：**外移出包**，包内只留指针 ----
    #
    # 实测：这些文案（各资产 *_rule + output_format）**逐字固定**，
    # 却在全书 95 节各推一遍 —— 合计占全书指令包体积的 **6.9%**
    # （_rules 4.8% + output_format 2.1%），约 **3.4 万 token 纯浪费**。
    # 读者读完一次就够，不需要随每节重复。
    #
    # 处理：整段移入 references/package-boilerplate.md，
    # 包内只留一个短指针。与 key-alias.md 同理——**一次读入、全书复用**。
    #
    # 为什么不直接删：① 其中含强制要求（如"Zone A 未明确规定必须声明"），删了会丢约束；
    # ② 无 Python 手工执行路径需要它；③ 外移后可被人工审阅，比埋在 30KB JSON 里更可见。
    rule_keys = [k for k in list(d) if k.endswith('_rule')]
    if rule_keys:
        for k in rule_keys:
            d.pop(k, None)
    d.pop('output_format', None)
    d['_rules_ref'] = ('固定写作要求（各资产使用规则 + 输出格式契约 + 正文写作要求）'
                       '见 references/package-boilerplate.md —— 一次读入、全书复用，'
                       '不再随每节重复。**其中的强制要求同样适用**，'
                       '尤其：Zone A 未明确规定的点必须声明「Zone A 未明确规定，'
                       '以下为参考写法，需人工判断」，不得以范例做法填补。')

    # ---- ⑤ data_requirements 去掉逐条壳 ----
    #   project_inputs 每条都是 {chapter_id, point, data_class, classified_by}，
    #   实测 42 条里 chapter_id/data_class 全同、classified_by 多为 default。
    dr = d.get('data_requirements')
    if isinstance(dr, dict) and isinstance(dr.get('project_inputs'), list):
        grouped = {}
        for x in dr['project_inputs']:
            if not isinstance(x, dict):
                continue
            nid = x.get('chapter_id')
            pt = x.get('point')
            cb = x.get('classified_by')
            if cb and cb != 'default':
                pt = '%s（%s）' % (pt, cb)
            grouped.setdefault(nid, []).append(pt)
        dr['project_inputs'] = [{'nid': k, 'pts': v} for k, v in grouped.items()]
        dr['note'] = ('按节点分组的「需项目输入」最小补数清单；**动笔前应向用户索取**，'
                      '缺失者以【待填：…】占位并继续，不得因缺数据而编造，也不得停笔。')

    # ---- ⑥ instructions 的 rules 只留一份（多节点时）----
    ins_list = d.get('instructions') or []
    if len(ins_list) > 1:
        first = None
        for ins in ins_list:
            rl = ins.get('rules')
            if isinstance(rl, list):
                if first is None:
                    first = rl
                elif rl == first:
                    ins['rules'] = '<见 ins[0].rl>'

    # ---- ⑥c Zone B / Zone C 条目去重壳 ----
    #   实测每条 zb 都带一份 83 字符的 ql 限定语、一份 rel、一份 chs；
    #   6 条就是 500+ 字符纯重复。限定语提到**包级一份**，条目内不再重复。
    for key, drop_defaults in (('zone_b_references', True), ('zone_c_references', True)):
        lst = d.get(key)
        if not isinstance(lst, list):
            continue
        new = []
        for x in lst:
            if not isinstance(x, dict):
                new.append(x)
                continue
            x = dict(x)
            # 限定语：包级已有 zbq/zcq，条目内删掉
            x.pop('qualifier', None)
            if drop_defaults:
                # 检索元数据，写作期不用；非默认值保留
                if x.get('doc_number') in ('待确认', None):
                    x.pop('doc_number', None)
                if x.get('publish_date') in ('待确认', None):
                    x.pop('publish_date', None)
                if x.get('relevance') in ('node_specific', None):
                    x.pop('relevance', None)
                if x.get('style_role_reason'):
                    # 与 sr（文体范式/方法论）重复表述，只留 sr
                    x.pop('style_role_reason', None)
            # 文件路径只留文件名
            fv = x.get('file')
            if isinstance(fv, str) and ('\\' in fv or '/' in fv):
                x['file'] = fv.replace('/', '\\').rsplit('\\', 1)[-1]
            new.append(x)
        d[key] = new

    # ---- ⑥d 资产 D/F 重复限定语清理 ----
    for key in ('species_references', 'measure_methods', 'design_params'):
        for x in (d.get(key) or []):
            if isinstance(x, dict):
                for k in ('source_file', 'file', 'qualifier'):
                    v = x.get(k)
                    if isinstance(v, str) and ('\\' in v or '/' in v):
                        x[k] = v.replace('/', '\\').rsplit('\\', 1)[-1]

    # ---- ⑥e length_budget：只留本节点，且去掉可推算的冗余字段 ----
    #   2.4 节 lb 占 31%——因为还在推他节点预算。已在上方 ③ 处理；
    #   此处再删掉 target_raw / share / chapter_share 等**可由 target 推算**的字段。
    lb2 = d.get('length_budget')
    if isinstance(lb2, dict):
        for k in ('rule', 'usage'):
            lb2.pop(k, None)
        for nd_ in (lb2.get('nodes') or []):
            if isinstance(nd_, dict):
                for k in ('target_raw', 'share', 'chapter_share', 'weight'):
                    nd_.pop(k, None)
        if isinstance(lb2.get('book'), dict):
            for k in ('page_target',):
                lb2['book'].pop(k, None)
    # ---- ⑥b 内容点与写作指令合并（省 token 的最大一处）----
    #   实测 1.1 节 rcp 622 + wd 2481 = 3103 字符，合并后约 1200 字符。
    #   wd 每条把 rcp 抄一遍、再把模板原句抄一遍，净信息只有「动词+深度」。
    tpl_seen = False
    for n in (d.get('nodes') or []):
        if not isinstance(n, dict):
            continue
        merged, _ = _merge_points_and_directives(n)
        if merged and n.get('writing_directive'):
            n['must_cover'] = merged          # 合并结果放新键，见 KEY_ALIAS
            n.pop('required_content_points', None)
            n.pop('writing_directive', None)
            n.pop('writing_directive_note', None)
            n.pop('point_count_atom', None)
            tpl_seen = True
    # ins[].mc 与 nd[].must_cover 同源，避免重复推两遍
    for ins in ins_list:
        if isinstance(ins, dict) and 'must_cover' in ins:
            ins['must_cover'] = '<见 nd[].must_cover（同序）>'
    if tpl_seen:
        d['mc_note'] = ('nd[].must_cover 为「内容点｜写作动词·深度要求」合并式：'
                        '`｜`前是**完整性**内容点（逐条覆盖，一条不漏），'
                        '`｜`后是**深度**写作指令（据此写，避免空泛）。'
                        '模板原句已在合并时去除，以模板树为准（references/template-tree.json）。')

    # ---- ⑦ 资产 D/F 超长文本截断（species / measure_methods）----
    #   实测 mm 7,883 + sp 4,919 ≈ 12.8 KB，是压缩后最大的一块，
    #   此前因字段名为中文而完全绕过压缩。此处按中文字段名定向截断。
    #   短字段（名称/学名/条号/依据/出处）一律不动——它们是回溯锚点。
    #
    #   max_items 的取值原则（自检修正）：**不因篇幅上限丢条目**。
    #   物种/工法条目数本身就有限（30 / 21），条目名是"该种什么树草、有哪些措施"
    #   的完整清单，砍条目 = 漏知识点，与"不漏任何知识点"的目标直接冲突。
    #   故上限设为一个**远高于实际条数**的值（仅防病态输入），省 token 靠
    #   截断长文本（每字段 180 字符）而非砍条目。
    _SP_SHORT = {'名称', '学名', '科属', '生活型', '水土保持功能'}
    _SP_LONG = {'生态习性', '适生条件', '备注'}
    _MM_SHORT = {'措施', '类型', '依据', '条号', '典型设计'}
    _MM_LONG = {'设计要求', '设计标准', '适用条件', '断面尺寸'}
    for key, shorts, longs, cap in (
            ('species_references', _SP_SHORT, _SP_LONG, 999),
            ('measure_methods', _MM_SHORT, _MM_LONG, 999)):
        if isinstance(d.get(key), list):
            d[key] = _trim_asset_list(d[key], shorts, longs,
                                      keep=ASSET_TEXT_KEEP, max_items=cap)

    # ---- ⑦b 工法条目降噪 ----
    #   实测 7.7 节 mm 有 17 条，其中 3 条是噪音：措施名为 None、
    #   或名称其实是"标准名"（如「造林技术规程要求」「矿山生态修复技术要求（通则）」）
    #   而非工法名——这类条目无法指导"这个措施怎么做"，却占 700+ 字符。
    #   判据：措施名为空，或名称以「要求」「规程」「导则」「规范」结尾且无「依据」条号。
    if isinstance(d.get('measure_methods'), list):
        cleaned = []
        for x in d['measure_methods']:
            if not isinstance(x, dict):
                continue
            nm = (x.get('措施') or '').strip()
            if not nm:
                continue
            if nm.endswith(('要求', '规程', '导则', '规范')) and not x.get('依据'):
                continue
            cleaned.append(x)
        d['measure_methods'] = cleaned

    # ---- ⑦.5 样板句提升（zone_c 的 csn / cfn）----
    #
    # 实测发现：Zone C 每条样本都带 `chapter_system_notice`（csn）与
    # `conflict_notice`（cfn），而它们在**同一节内逐条完全相同**——
    # 抽样 40 节 60 条样本，csn 只有 2 种取值，最长的一种重复了 **54 次**。
    # csn 占 Zone C 总字符的 **23%**；外推全书 95 节约 **2.16 万字符**，
    # 全是把同一段话抄几十遍，属于纯粹的重复，不承载任何额外信息。
    #
    # 做法：同节内若某字段取值唯一，则提升为顶层 `zc1`（值为该字符串），
    # 各条内删除该字段；若确有多种取值，则保留原样不动（不做有损的"最频繁者"近似）。
    # 读取端约定：zc[].csn 缺失 ⇒ 取 zc1 里对应的那条。
    # 注意：本步骤位于「键名速记」之前，因此这里看到的还是**长键名**
    # `zone_c_references`。踩过的坑：最初只判 `zone_c`/`zc`，两个都不是，
    # 规则看着正确却**从未生效**（40 节样本 0 节命中）。
    zc_list = None
    if isinstance(d.get('zone_c_references'), list) and d['zone_c_references']:
        zc_list = d['zone_c_references']
    elif isinstance(d.get('zone_c'), list) and d['zone_c']:
        zc_list = d['zone_c']
    elif isinstance(d.get('zc'), list) and d['zc']:
        zc_list = d['zc']
    if zc_list:
        hoisted = {}
        for field, slot in (('chapter_system_notice', 'csn'),
                            ('conflict_notice', 'cfn')):
            vals = [(i, x.get(field)) for i, x in enumerate(zc_list)
                    if isinstance(x, dict) and x.get(field)]
            if len(vals) < 2:
                continue                       # 只有 1 条或 0 条，提升不划算
            uniq = {v for _, v in vals}
            if len(uniq) != 1:
                continue                       # 取值不一致 ⇒ 不能提升
            hoisted[slot] = vals[0][1]
            for i, _ in vals:
                zc_list[i] = {k: v for k, v in zc_list[i].items()
                              if k != field}
        if hoisted:
            d['zc1'] = hoisted

    # ---- ⑧ 键名速记（仅当包足够大才划算）+ 本包实际用到的对照表 ----
    aliased = _alias_keys(d)
    ct = _compact_ct(aliased)
    if ct:
        d = aliased
        d['ct'] = ct
        # ct_note 里的键名**从 KEY_ALIAS 反查生成**，不写字面量。
        # 踩过的坑：早先手写 'hc[].s'/'hc[].oi'，而别名表里 s/oi 未缩写，
        # 一旦别名表调整，说明文字就与实际键名脱节（提示 AI 去查一个不存在的键）。
        _s = '_'.join(k for k, v in KEY_ALIAS.items() if v == 's') or 's'
        _oi = '_'.join(k for k, v in KEY_ALIAS.items() if v == 'oi') or 'oi'
        d['ct_note'] = ('键名为短名，对照见 references/key-alias.md。'
                        'g.src_files=源文件表，hc[].%s 为其下标(1起)；'
                        'hc[].req 超长已截断并置 truncated，附 more；全文用 '
                        'kb_lookup.py --oid <src_files[%s-1]>:<%s> 取回。'
                        'cl 省略即「（段落）」；sc/ms/st 省略即默认。'
                        % (_s, _s, _oi))
    else:
        # 短包：不缩写，但仍写入压缩说明，保持行为可预期
        d['_compact'] = ('V2 已压缩：去重复+截断+去壳；本包体积较小，'
                         '键名未缩写（缩写成本高于收益）。')
        return d
    d['_compact'] = ('V2 已压缩：键名速记+超长截断+去元数据壳+去重复，'
                     '实质信息未减少，被删处均留指针。需完整包加 --full。')
    return d


def build(chapter, province=None, city=None, report_form=False, ledger_path=None,
          compact=True):

    # ---- 第 1 步：合规闸门（必须先执行）----
    gate = G.run(chapter, province, city, report_form=report_form)

    # ---- blocked 则停止，且不得检索 Zone B ----
    if gate['blocked']:
        return {
            'chapter': gate['chapter'],
            'write_allowed': False,
            'blocked': True,
            'block_reason': gate['block_reason'],
            'gate': gate,
            'zone_b_references': [],
            'zone_c_references': [],
            'zone_c_injection_status': {'enabled': False,
                                        'reason': '合规闸门阻断：依 zone-c-usage.md 不检索 Zone C'},
            'snapshot_warning': G.SNAPSHOT_WARNING,
            'note': '合规闸门阻断：不得写作，且依 zone-b-usage.md 不检索 Zone B。'
                    '必须先解决依据问题（补齐规范来源或替换为新版）后重跑闸门。',
        }

    targets = gate['expanded_nodes']

    # ---- 报告表（附件3）分支：骨架来自 tables.json 的 report_form，不走章节树 ----
    if report_form:
        tbl = json.load(open(os.path.join(REF, 'tables.json'), encoding='utf-8-sig'))
        rf = tbl.get('report_form', {})
        rfg = RULES.get('report_form_gate', {})
        blocks = []
        for i, fields in enumerate(rf.get('main_tables', []), 1):
            # 区块名取该区块首字段（tables.json 里首元素即区块名，如"项目概况"/
            # "防治责任范围（hm²）"）；取不到才退化成序号名。
            name = (fields[0].strip() if fields and isinstance(fields[0], str)
                    and fields[0].strip() else None)
            blocks.append({'block': name or ('区块%d' % i),
                           'block_index': i,
                           'field_count': len(fields),
                           'fields': fields})
        return {
            'chapter': '报告表',
            'report_form': True,
            'write_allowed': True,
            'blocked': False,
            'gate': gate,
            'provisional': gate['provisional'],
            'report_form_skeleton': {
                'title': rf.get('title', '生产建设项目水土保持方案报告表编制内容及格式'),
                'structure_rule': rfg.get('structure', ''),
                'gate_targets': rfg.get('gate_targets', []),
                'blocks': blocks,
                'attachments': rf.get('attachment_requirement', ''),
                'figures': rf.get('figure_requirement', ''),
                'note': '报告表不分章：以两个表格区块为骨架，字段逐字取自 tables.json；'
                        '涉及闸门目标节点（%s）的硬约束均已随 gate 注入，逐区块核对。'
                        % '、'.join(rfg.get('gate_targets', [])),
            },
            'instructions': [{
                'chapter_id': '报告表',
                'title': rf.get('title', '生产建设项目水土保持方案报告表'),
                'must_cover': [b['block'] for b in blocks] + ['附件清单', '附图清单'],
                'hard_constraints': gate['hard_constraints'],
                'conditional_question': None,
                'rules': [
                    '本表所有事实性数据必须来自项目数据包；缺失者留占位符，不得编造。',
                    '本表结论不得与 hard_constraints 冲突；冲突时改结论，不得改依据。',
                    '引用 Zone B 素材必须附限定语：' + QUALIFIER,
                    '报告表是否适用于本项目由使用者判定并声明，本技能不替使用者做该判定。',
                ],
            }],
            'tables': [],
            'calculations': [],
            'data_requirements': {
                'project_inputs': [],
                'project_input_count': 0,
                'spec_given_count': 0,
                'note': '报告表字段即数据需求清单：两区块全部字段按「可计算/缺数据不可计算」标注。',
            },
            'zone_b_references': [],
            'zone_b_total_matched': 0,
            'zone_b_qualifier': QUALIFIER,
            'zone_c_references': [],
            'zone_c_injection_status': {'enabled': False,
                                        'reason': '报告表分支沿用报告书 Zone C 规则；本分支骨架非章节节点，'
                                                  '如需范例参考请按 topic_tags 主题人工检索'},
            'zone_c_qualifier': ZONE_C_QUALIFIER,
            'zone_a_unspecified': False,
            'zone_a_unspecified_notice': None,
            'snapshot_warning': G.SNAPSHOT_WARNING,
            'output_format': {
                'sections': ['报告表两区块（逐字段填写）', '附件清单', '附图清单',
                             '计算过程（按 report_form_gate 的 gate_targets 对应章节的计算规则）',
                             '缺数据清单与可算/不可算标注'],
                'placeholder': '【待填：字段名】',
                'computation_marking': '每张表/每个计算须标注「可计算」或「缺数据不可计算」',
                'forbidden': ['编造数据或条款号', '自行拟定定额单价', '把 Zone B 表述为规范要求',
                              '把项目信息写回 skill 目录或知识库'],
            },
        }

    # ---- 第 2 步：施加模板骨架 ----
    nodes, project_inputs, spec_given = [], [], []
    for t in targets:
        n = NODES[t]
        pts = n['required_content_points']
        for p in pts:
            cls, why = classify_point(p, RULES)
            rec = {'chapter_id': t, 'point': p, 'data_class': cls, 'classified_by': why}
            (spec_given if cls == '规范给定' else project_inputs).append(rec)
        # 动词化写作指令（template-tree.json 的 writing_directive，与 pts 同序一一对应）。
        # 供写作者参考「这一条该怎么写」；required_content_points 仍是审核用的检查清单。
        directs = n.get('writing_directive') or []
        nodes.append({
            'chapter_id': t,
            'title': n['title'],
            'node_kind': n['node_kind'],
            'required_content_points': pts,
            'writing_directive': directs,
            'writing_directive_note': (
                'writing_directive 与 required_content_points 同序一一对应，是「怎么写」的指令，'
                '不构成事实来源；required_content_points 是完整性检查清单，两者都要满足。'),
            'point_count': n['point_count'],
            'conditional': n.get('conditional', False),
            'conditional_variants': n.get('conditional_variants', []),
            'evidence': n['evidence'],
        })

    # ---- 第 3 步：表格与计算 ----
    tbl = json.load(open(os.path.join(REF, 'tables.json'), encoding='utf-8-sig'))
    tables, calcs = [], []
    for t in targets:
        if t == '表1' or t.startswith('1.1'):
            tables.append({'table': '表1 水土保持方案特性表',
                           'fields': tbl['报告书']['表1 水土保持方案特性表']['fields'],
                           'notes': tbl['报告书']['表1 水土保持方案特性表']['notes']})
            break
    for t in targets:
        for item in RULES.get('calculations', {}).get('items', {}).get(t, []):
            calcs.append(dict(item, chapter_id=t))
    # 章节前缀匹配（如 4.4.x 命中 4.4）
    for t in targets:
        pref = t.rsplit('.', 1)[0] if '.' in t else t
        if pref != t:
            for item in RULES.get('calculations', {}).get('items', {}).get(pref, []):
                if not any(c['item'] == item['item'] for c in calcs):
                    calcs.append(dict(item, chapter_id=t))

    # ---- 第 4 步：Zone B（此时才可以）----
    refs, total_refs = zone_b_refs(targets)

    # ---- 第 4b 步：Zone C（仅当本节点 Zone A 专属依据为 0；按主题挂载）----
    c_refs, c_status = zone_c_refs(targets, gate, RULES)
    c_samples, c_sample_status = zone_c_style_samples(targets[0] if targets else '', RULES)
    # 资产 D：乡土树草种（服务 2.7.6 / 2.3 / 7.x 的植物措施要求）
    sp_refs, sp_status = species_refs(targets[0] if targets else '', RULES)
    # 资产 E：设计参数阈值（服务 5.2 / 7.6 的定级要求）
    dp_refs, dp_status = design_params_refs(targets[0] if targets else '', RULES)
    # 资产 F：措施工法（服务 5.2 / 7.6 / 7.7 / 9.1.2 的典型设计）
    mm_refs, mm_status = measure_methods_refs(targets or [], RULES)
    # 与 refs 同闸：计算/评价型节点不得注入任何 Zone C 内容（含写法范式）。
    # 范式虽已脱敏，但"怎么写结论"仍可能诱导出与 Zone A 不符的结论文风，故一并关闭。
    if not c_status.get('enabled'):
        c_samples, c_sample_status = [], {
            'available': c_sample_status.get('available', False),
            'injected': 0,
            'reason': 'Zone C 未开启，写法范式同步不注入：%s' % c_status.get('reason', '')}
    # Zone A 未明确规定 → 必须显式声明，不得用范例填补
    zone_a_unspecified = (gate['constraint_scope'].get('node_specific', 0) == 0)
    no_rule_notice = ZONE_C_NO_RULE_NOTICE if (zone_a_unspecified and not gate['hard_constraints']) else None

    # ---- 第 5 步：组装写作指令（含篇幅预算 + 深度要求 + 台账已定事实）----
    zc_viol, zc_checked = zone_consistency_check()
    ledger_facts = load_ledger_facts(ledger_path, targets)
    instr = []
    for n in nodes:
        nb = B.node_budget(n['chapter_id']) or {}
        dep = B.depth_requirements(n['chapter_id'])
        instr.append({
            'chapter_id': n['chapter_id'],
            'title': n['title'],
            'must_cover': n['required_content_points'],
            'length_budget': {
                'target_chars': nb.get('target'),
                'min_chars': nb.get('min'),
                'max_chars': nb.get('max'),
                'tables_required': dep.get('tables_required', False),
                'note': '本节目标字数区间；低于下限报「偏少」、高于上限报「超标」（rules.json length_budget）'
                        if nb else '该节点无独立篇幅预算（容器/表格节点）',
            },
            'depth_requirements': dep,
            'hard_constraints': [c for c in gate['hard_constraints']],
            'ledger_facts': ledger_facts['items'] if ledger_facts['enabled'] else [],
            'conditional_question': ('本节点带适用条件，动笔前必须先确认项目属于以下哪种情形：%s'
                                     % '；'.join(n['conditional_variants'])
                                     if n['conditional_variants'] else None),
            'rules': [
                '本节所有事实性数据必须来自项目数据包；缺失者留占位符，不得编造。',
                '本节结论不得与 hard_constraints 冲突；冲突时改结论，不得改依据。',
                '引用 Zone B 素材必须附限定语：' + QUALIFIER,
                '引用 Zone C 素材必须附限定语：' + ZONE_C_QUALIFIER,
                'Zone C 只提取结构信息（章节展开顺序/段落功能/表格栏目结构/句式模板/论证链步骤），'
                '不得提取结论、参数、专名、数字；与 Zone A 冲突者丢弃并标注「%s」。' % ZONE_C_CONFLICT_NOTICE,
                '凡 Zone A 未明确规定的点，必须输出「%s」，不得以范例做法填补。' % ZONE_C_NO_RULE_NOTICE,
            ] + ([ledger_facts['rule'] + '：' + '；'.join(
                '%s=%s%s' % (i['key'], i['value'], i['unit']) for i in ledger_facts['items'][:12])]
                if ledger_facts['items'] else []),
        })

    out = {
        'chapter': chapter,
        'write_allowed': True,
        'blocked': False,
        'gate': gate,
        'provisional': gate['provisional'],
        'nodes': nodes,
        'instructions': instr,
        'tables': tables,
        'calculations': calcs,
        'data_requirements': {
            'project_inputs': project_inputs,
            'project_input_count': len(project_inputs),
            'spec_given_count': len(spec_given),
            'note': '「需项目输入」为最小补数清单：动笔前应向用户索取，缺失者以【待填：…】占位并继续，'
                    '不得因缺数据而编造，也不得因缺数据而停笔。',
        },
        'zone_b_references': refs,
        'zone_b_total_matched': total_refs,
        'zone_b_qualifier': QUALIFIER,
        'zone_c_references': c_refs,
        'zone_c_injection_status': c_status,
        'zone_c_qualifier': ZONE_C_QUALIFIER,
        # Zone C 写法范式样本（已脱敏，只含"怎么写"）——
        # 这是解决"机械感"的关键一层：refs 只说明"有哪几份范例"，
        # samples 才给出"这一段别人是怎么起笔、怎么详略、怎么收口"。
        'zone_c_style_samples': c_samples,
        'zone_c_style_status': c_sample_status,
        'zone_c_style_rule': ('以下样本**仅供参考写法**（段落功能/句式骨架/论证链），'
                              '其中的〔数值〕〔市〕〔矿名〕等占位符已替换原项目信息。'
                              '不得引入其结论、参数、措施做法，不得直接用于本项目；'
                              '与 Zone A 冲突时以 Zone A 为准。'),
        # 资产 D：乡土树草种知识（服务植物措施相关节点）
        'species_references': sp_refs,
        'species_status': sp_status,
        'species_rule': ('以上物种信息**全部解析自知识库正文并附出处**，用于撰写'
                         '「当地主要乡土树草种及生长情况」与植物措施配置。'
                         '**缺字段即库内无据，不得以常识补齐**；'
                         '选用时须结合 Zone A 标准与项目立地条件判断。'),
        # 资产 E：设计参数阈值（来自 Zone A 标准表格）
        'design_params': dp_refs,
        'design_params_status': dp_status,
        'design_params_rule': ('以上阈值**只来自 Zone A 标准表格**，附 `依据`（标准名+表号）可回溯。'
                               '定级时以标准原文为准；**不得凭经验定级**。'
                               '表头为多行时按 `／` 分隔逐行给出。'),
        # 资产 F：措施工法（Zone A 设计要求原文）
        'measure_methods': mm_refs,
        'measure_methods_status': mm_status,
        'measure_methods_rule': ('以上为各措施的 **Zone A 设计要求原文**（附条号），'
                                 '用于撰写「典型设计」与「分区措施布设」。'
                                 '具体断面尺寸属项目数据，须来自项目资料或标准原文，**不得自拟**。'),
        'zone_a_unspecified': zone_a_unspecified,
        'zone_a_unspecified_notice': no_rule_notice,
        'zone_consistency': {'checked': zc_checked, 'violations': zc_viol,
                             'rule': 'rules.json zone_consistency_rules'},
        'length_budget': {
            'book': B.book_budget(),
            'chapter': B.chapter_budget(chapter) if not report_form else None,
            'nodes': [dict(B.node_budget(n['chapter_id']) or {},
                           chapter_id=n['chapter_id'], title=n['title']) for n in nodes],
            'rule': 'rules.json length_budget：目标字数 = 全书正文目标 × 节点权重 / 权重总和',
            'usage': '写作时按 length_budget 控制本节篇幅；写完用 check_draft 核对篇幅台账；'
                     '全书累计进度用 check_plan 查。',
        },
        'ledger_facts': ledger_facts,
        'snapshot_warning': G.SNAPSHOT_WARNING,
        'output_format': {
            'sections': ['章节正文（中文，报告书文体）', '表格（按 tables 要求的字段）',
                         '计算过程（按 calculations，含中间量）', '缺数据清单与可算/不可算标注'],
            'placeholder': '【待填：字段名】',
            'computation_marking': '每张表/每个计算须标注「可计算」或「缺数据不可计算」',
            'forbidden': ['编造数据或条款号', '自行拟定定额单价', '把 Zone B 表述为规范要求',
                          '把项目信息写回 skill 目录或知识库'],
        },
    }
    # 默认精简（V2 四项压缩）；--full 时返回完整包
    return out if not compact else compact_package(out, level='compact')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chapter')
    ap.add_argument('--province', default=None)
    ap.add_argument('--city', default=None)
    ap.add_argument('--json-only', action='store_true')
    ap.add_argument('--full', action='store_true',
                    help='输出完整指令包（不去重复/不裁样板）；默认精简')
    ap.add_argument('--pretty', action='store_true',
                    help='JSON 带缩进（供人阅读）；默认紧凑无缩进，体积省约 50%%')
    ap.add_argument('--out', default=None)
    ap.add_argument('--report-form', action='store_true',
                    help='报告表（附件3）分支：骨架取 tables.json 的 report_form')
    ap.add_argument('--ledger', default=None,
                    help='事实台账 json（项目工作区）：注入全书已定事实，防止跨章口径打架')
    a = ap.parse_args()
    if not a.report_form and not a.chapter:
        ap.error('需要 --chapter 或 --report-form')
    r = build(a.chapter, a.province, a.city, report_form=a.report_form,
              ledger_path=a.ledger, compact=not a.full)
    if (r.get('gate') or {}).get('invalid_chapter'):
        sys.stderr.write('❌ 章节号 %r 不在模板中，未生成写作指令包。\n'
                         '   可用章节号用 check_gate.py --list 查。\n' % a.chapter)
        raise SystemExit(2)
    # JSON 输出：默认**紧凑**（无缩进）——实测缩进占体积 50%，是纯浪费。
    # AI/程序读 JSON 不需要缩进；要人读时加 --pretty。
    sep = (',', ':') if not a.pretty else None
    txt = json.dumps(r, ensure_ascii=False,
                     **({'separators': sep} if sep else {'indent': 1}))
    if a.out:
        G.write_text(a.out, txt, '写作指令包')
        print('已写出指令包: %s（%.1f KB）' % (a.out, len(txt.encode('utf-8')) / 1024))
        return
    print(txt)
    if a.json_only:
        sys.exit(0 if r['write_allowed'] else 3)
    print()
    print('--- 写作指令摘要 ---')
    print('章节:', r['chapter'], ' 允许写作:', r['write_allowed'])
    if not r['write_allowed']:
        sys.stderr.write('❌ 合规闸门 blocked，禁止写作：%s\n' % r['block_reason'])
        sys.exit(3)
    if r.get('report_form'):
        sk = r['report_form_skeleton']
        print('分支: 报告表（附件3）  骨架区块: %d 个' % len(sk['blocks']))
        print('闸门目标节点: %s' % '、'.join(sk['gate_targets']))
        print('Zone A 硬约束: %d 条' % len(r['gate']['hard_constraints']))
        print('附件要求: %s…' % sk['attachments'][:60])
        print('附图要求: %s…' % sk['figures'][:60])
    else:
        print('待人工确认(provisional):', r['provisional'])
        print('模板骨架：%d 个节点 / %d 个必须覆盖内容点'
              % (len(r['nodes']), sum(n['point_count'] for n in r['nodes'])))
        print('Zone A 硬约束: %d 条' % len(r['gate']['hard_constraints']))
        print('计算项: %d 项' % len(r['calculations']))
        print('需项目输入: %d 项 / 规范给定: %d 项'
              % (r['data_requirements']['project_input_count'], r['data_requirements']['spec_given_count']))
        print('Zone B 参考素材: %d 条（匹配 %d 条，已截断）' % (len(r['zone_b_references']), r['zone_b_total_matched']))
        lb = r.get('length_budget') or {}
        bk, ch = lb.get('book') or {}, lb.get('chapter') or {}
        if bk:
            print('篇幅预算：全书 %d–%d 页 → 正文目标 %s 字；本章目标 %s 字（%s–%s）'
                  % (bk['page_target']['min'], bk['page_target']['max'], bk['book_target_chars'],
                     ch.get('target'), ch.get('min'), ch.get('max')))
        lf = r.get('ledger_facts') or {}
        print('台账已定事实注入: %d 条%s' % (lf.get('count', 0),
                                            '' if lf.get('enabled') else '（未注入：%s）' % lf.get('reason', '')))
        zc = r.get('zone_consistency') or {}
        if zc.get('violations'):
            print('⚠ 三分层一致性违规 %d 处（详见 zone_consistency.violations）' % len(zc['violations']))
    if r.get('snapshot_warning'):
        print('⚠', r['snapshot_warning'])
    for i, c in enumerate(r.get('calculations') or [], 1):
        print('  算%d. [%s] %s  ← %s' % (i, c['chapter_id'], c['item'], c['basis']))
    for z in (r.get('zone_b_references') or [])[:6]:
        print('  参. [%s·%s] %s' % (z['usage_label'], z['relevance'], z['file'].split('\\')[-1][:56]))


if __name__ == '__main__':
    main()

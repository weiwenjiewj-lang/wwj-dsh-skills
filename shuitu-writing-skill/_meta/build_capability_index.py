"""生成能力总账：把技能的全部能力、资产、公式、规则**枚举成一张可核对的清单**。

用法（在技能包目录下）：
    python _meta/build_capability_index.py            # 生成 references/capability-index.md
    python _meta/build_capability_index.py --check    # 只核对，不改文件

## 为什么需要它

用户的核心要求之一是「不漏任何知识点、能完整使用相关公式计算」。
但技能的能力散落在 24 个脚本 + 20 个 references 文件 + rules.json 的 32 组规则里，
**没有任何一处能一眼看出"这个技能到底能做什么、少没少"**。

改动技能（尤其优化/瘦身）时，最容易发生的事故就是
**某个能力被静默删除而没人发现**。本脚本把能力变成**可断言的清单**：
数量对不上就报警，让"丢能力"变成显式失败，而不是等写到第 8 章才发现算不出指标。

## 产出

`references/capability-index.md`：能力总账，含
  · 脚本能力表（24 个脚本各自能做什么、怎么调）
  · 资产表（A~G 七类知识资产及条目数）
  · 计算公式表（7 个计算引擎 + 输入字段承接情况）
  · 规则组表（rules.json 32 组）
  · 模板覆盖（95 节点、内容点数、表格栏数）
  · 不变量断言（数量下限，跌破即失败）
"""
import json
import os
import re
import sys

# 技能目录零残留：必须在 import 本地模块之前设置。
# Python 在 import 时先编译写盘、后执行模块体，
# 因此被 import 的模块自己设是来不及的。
sys.dont_write_bytecode = True

SK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(SK, 'references')
SCRIPTS = os.path.join(SK, 'scripts')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def jload(name):
    p = os.path.join(REF, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8-sig') as f:
        return json.load(f)


def _count_of(fname, container_key, count_key=None):
    """取「容器键下的条目数」——各资产真实结构不同，必须显式声明。"""
    d = jload(fname) or {}
    c = d.get(container_key)
    if isinstance(c, dict):
        return len(c)
    if isinstance(c, list):
        return len(c)
    if count_key:
        return int(d.get(count_key) or 0)
    return 0


def _entries_len(fname):
    """zone 索引：条目在 entries 下。"""
    d = jload(fname) or {}
    e = d.get('entries')
    if isinstance(e, list):
        return len(e)
    if isinstance(e, dict):
        return len(e)
    return int(d.get('count') or 0)


def script_purpose(path):
    """从脚本 docstring 第一段提取用途。"""
    try:
        with open(path, encoding='utf-8') as f:
            txt = f.read(4000)
    except OSError:
        return ''
    m = re.search(r'"""(.*?)"""', txt, re.S)
    if not m:
        return ''
    first = m.group(1).strip().split('\n')
    line = first[0].strip()
    # 跳过 "第X层：xxx。" 这类前缀，取更有信息量的一句
    for ln in first[:6]:
        s = ln.strip()
        if len(s) > 14 and not s.startswith(('用法', 'Usage')):
            line = s
            break
    return line[:110]


def collect():
    cap = {}

    # ---- 脚本 ----
    scripts = []
    if os.path.isdir(SCRIPTS):
        for fn in sorted(os.listdir(SCRIPTS)):
            if fn.endswith('.py'):
                scripts.append((fn, script_purpose(os.path.join(SCRIPTS, fn))))
    cap['scripts'] = scripts

    # ---- 模板 ----
    # 注意：template-tree.json 的 nodes 是 **list[95]**——
    # 2026 版十章共 95 个节点，每个节点都可有 required_content_points，
    # **没有"容器节点"标记**（早先按 is_container 过滤得出 75，是错的）。
    tree = jload('template-tree.json') or {}
    raw_nodes = tree.get('nodes') or {}
    if isinstance(raw_nodes, dict):
        node_list = []
        for k, v in raw_nodes.items():
            if isinstance(v, dict):
                d = dict(v)
                d.setdefault('chapter_id', k)
                node_list.append(d)
    elif isinstance(raw_nodes, list):
        node_list = [v for v in raw_nodes if isinstance(v, dict)]
    else:
        node_list = []
    cap['nodes_total'] = len(node_list)
    cap['nodes_leaf'] = len(node_list)
    cap['content_points'] = sum(len(v.get('required_content_points') or [])
                                for v in node_list)
    cap['format_requirements'] = len(tree.get('format_requirements') or [])

    # ---- 表格 ----
    tb = jload('tables.json') or {}
    rp = (tb.get('报告书') or {})
    t1 = rp.get('表1 水土保持方案特性表') or {}
    cap['t1_fields'] = len(t1.get('fields') or [])
    cap['t1_notes'] = len(t1.get('notes') or [])
    cap['table_groups'] = list(rp.keys())
    rf = (tb.get('report_form') or {})
    cap['report_form_blocks'] = len(rf.get('main_tables') or [])

    # ---- 计算引擎 ----
    rules = jload('rules.json') or {}
    ce = (rules.get('calc_engine') or {}).get('items') or {}
    cap['calc_items'] = len(ce)
    cap['calc_inputs'] = sum(len(v.get('inputs') or []) for v in ce.values())
    cap['calc_names'] = [(k, v.get('name'), v.get('method')) for k, v in ce.items()]
    cap['spec_params'] = list(((rules.get('calc_engine') or {})
                               .get('spec_given_params') or {}).keys())

    # ---- 数据包字段 ----
    cap['pkg_fields'] = len((rules.get('data_package') or {}).get('fields') or [])
    # 事实抽取（extract_facts）与数据包**共用同一份 82 字段字典**，
    # 不单独存字段表（早先误当成独立资产，断言 0 项必然是 FAIL）。
    cap['fact_fields'] = cap['pkg_fields']

    # ---- 规则组 ----
    cap['rule_groups'] = sorted(rules.keys())

    # ---- 资产 ----
    # 注意：各资产的**计数键不统一**（species/methods/params/topics…），
    # 早先按"取最大的 list"猜，结果 species 猜成 3、style_samples 猜成 7——
    # 全是解析口径错，不是能力缺失。这里改为**显式声明真实结构**。
    cap['species'] = _count_of('species_index.json', 'species', 'count')
    cap['measure_methods'] = _count_of('measure_methods.json', 'methods', 'count')
    cap['design_params'] = _count_of('design_params.json', 'params', 'count')
    # 写法范式：真实条数在 stats.kept；topics 是主题数（36）
    ss = jload('zone_c_style_samples.json') or {}
    cap['style_samples'] = int((ss.get('stats') or {}).get('kept') or 0)
    cap['style_topics'] = len(ss.get('topics') or {})
    cap['zone_a'] = _entries_len('zone-a-index.json')
    cap['zone_b'] = _entries_len('zone-b-index.json')
    cap['zone_c'] = _entries_len('zone-c-index.json')

    # ---- 预算 ----
    lb = rules.get('length_budget') or {}
    cap['book_target'] = lb.get('book_target_chars')
    cap['page_range'] = lb.get('page_target')

    return cap


# 不变量断言：数量跌破即视为「能力丢失」，构建失败
INVARIANTS = [
    ('nodes_leaf', 95, '模板叶子节点数（2026 版十章共 95 节点）'),
    ('content_points', 400, '模板内容点总数（模板完整性基础）'),
    ('t1_fields', 55, '表1 特性表栏数（模板硬要求 55 栏）'),
    ('t1_notes', 6, '表1 表注条数（模板硬要求 6 条）'),
    ('calc_items', 7, '计算公式引擎数（2.3/2.4/4.4/6.3/7.3.2/8.2/9.1.2）'),
    ('pkg_fields', 82, '项目数据包字段数（82 字段 13 分区）'),
    ('fact_fields', 82, '事实抽取字段字典（与数据包同源）'),
    ('species', 80, '物种名录条数（资产 D）'),
    ('measure_methods', 20, '措施工法条数（资产 F）'),
    ('design_params', 5, '设计参数组数（资产 E）'),
    ('style_samples', 300, 'Zone C 写法范式条数'),
    ('zone_a', 70, 'Zone A 规范条目数'),
    ('zone_c', 90, 'Zone C 应用范例条目数'),
]


def render(cap):
    L = []
    A = L.append
    A('# 能力总账（Capability Index）')
    A('')
    A('> **本文件由 `python _meta/build_capability_index.py` 自动生成，请勿手工编辑。**')
    A('> 作用：把技能的全部能力枚举成可核对的清单，')
    A('> **让"优化时不小心删掉某个能力"变成显式失败**，而不是写到第 8 章才发现算不出指标。')
    A('')
    A('## 一、不变量断言（跌破即能力丢失）')
    A('')
    A('| 能力 | 当前值 | 下限 | 判定 | 说明 |')
    A('|:---|---:|---:|:--:|:---|')
    ok_all = True
    for key, floor, desc in INVARIANTS:
        v = cap.get(key, 0) or 0
        ok = v >= floor
        ok_all = ok_all and ok
        A('| `%s` | %s | %s | %s | %s |' % (key, v, floor, '✅' if ok else '❌', desc))
    A('')
    A('**总判定：%s**' % ('✅ 全部达标' if ok_all else '❌ 存在能力缺失，禁止发布'))
    A('')

    A('## 二、脚本能力（%d 个）' % len(cap['scripts']))
    A('')
    A('| 脚本 | 用途 |')
    A('|:---|:---|')
    for fn, purpose in cap['scripts']:
        A('| `scripts/%s` | %s |' % (fn, purpose or '—'))
    A('')

    A('## 三、七类知识资产')
    A('')
    A('| 资产 | 内容 | 条目数 | 载体 |')
    A('|:--:|:---|---:|:---|')
    A('| A | 规范依据 | %s | `zone-a-index.json` |' % cap.get('zone_a'))
    A('| B | 事实台账 | 82 字段 | `台账.json`（extract_facts 产出） |')
    A('| C | 写法范式 | %s | `zone_c_style_samples.json` |' % cap.get('style_samples'))
    A('| D | 物种名录 | %s | `species_index.json` |' % cap.get('species'))
    A('| E | 参数口径 | %s | `design_params.json` |' % cap.get('design_params'))
    A('| F | 工法索引 | %s | `measure_methods.json` |' % cap.get('measure_methods'))
    A('| G | 文体范式 | %s（Zone B 总条目） | `zone-b-index.json` 的 `style_role` |'
      % cap.get('zone_b'))
    A('')
    A('> Zone C 应用范例：**%s** 条（`zone-c-index.json`）。' % cap.get('zone_c'))
    A('')

    A('## 四、计算公式引擎（%d 个，共 %d 项输入）'
      % (cap['calc_items'], cap['calc_inputs']))
    A('')
    A('| 节点 | 计算项 | 方法 |')
    A('|:---|:---|:---|')
    for nid, nm, method in cap['calc_names']:
        A('| `%s` | %s | `%s` |' % (nid, nm, method))
    A('')
    A('**规范给定参数（甲类，引擎自取值）**：%s' % '、'.join(cap['spec_params'] or ['—']))
    A('')
    A('> **缺项目输入一律输出 `【待填：字段名】` + 「缺数据不可计算」，绝不用默认值兜底。**')
    A('> 输入字段承接自检：`python "$SK\\scripts\\calc.py" --audit`')
    A('')

    A('## 五、模板与表格')
    A('')
    A('| 项 | 值 |')
    A('|:---|---:|')
    A('| 模板节点总数 | %s |' % cap['nodes_total'])
    A('| 叶子节点（可写作） | %s |' % cap['nodes_leaf'])
    A('| 模板内容点总数 | %s |' % cap['content_points'])
    A('| 表1 特性表栏数 | %s |' % cap['t1_fields'])
    A('| 表1 表注条数 | %s |' % cap['t1_notes'])
    A('| 报告表区块数 | %s |' % cap['report_form_blocks'])
    A('| 全书正文目标字数 | %s |' % cap.get('book_target'))
    A('')
    A('**表格排版规范**见 `references/table-format.md`'
      '（字体/字号/封面/页眉/表题表注，逐字取自办水保函〔2026〕232号-附件2）。')
    A('')

    A('## 六、规则组（rules.json 共 %d 组，唯一规则源）' % len(cap['rule_groups']))
    A('')
    for g in cap['rule_groups']:
        A('- `%s`' % g)
    A('')
    A('> **不允许出现只写在代码里的规则**——所有阈值、上限、判定口径均在 rules.json。')
    A('')
    return '\n'.join(L), ok_all


def main():
    cap = collect()
    text, ok_all = render(cap)
    out = os.path.join(REF, 'capability-index.md')
    if '--check' in sys.argv:
        print('能力核对（--check，不写文件）：')
        for key, floor, desc in INVARIANTS:
            v = cap.get(key, 0) or 0
            print('  %-16s %5s / 下限 %5s  %s  %s'
                  % (key, v, floor, 'OK  ' if v >= floor else 'FAIL', desc))
        sys.exit(0 if ok_all else 1)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text)
    print('已写出能力总账：%s（%.1f KB）'
          % (out, os.path.getsize(out) / 1024))
    for key, floor, desc in INVARIANTS:
        v = cap.get(key, 0) or 0
        if v < floor:
            print('❌ 能力缺失：%s = %s（下限 %s）—— %s' % (key, v, floor, desc))
    print('总判定：%s' % ('✅ 全部达标' if ok_all else '❌ 存在缺失'))
    sys.exit(0 if ok_all else 1)


if __name__ == '__main__':
    main()

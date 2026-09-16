#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第五层：计算引擎。把方案里的公式算对、算全，并留下可复核的计算书。

为什么要独立成层：此前「数字一致性」只能发现同一数值前后不一致，**算错但前后一致的数**
（如控制比口径反转、单位漏乘 10000、勾稽不闭合）完全查不出来。本引擎按 rules.json
calc_engine 的声明式计算项求值，输出「公式 → 代入 → 中间量 → 结果 → 依据」的完整计算书。

三条硬约束（来自 rules.json calc_engine.principle）：
  1. 参数只有两个来源：spec_given（Zone A 逐字取值，本文件已录者照录）/ project_input（数据包或台账）；
  2. 缺 project_input → 输出【待填：X】并标「缺数据不可计算」，**绝不用默认值/经验值兜底**；
  3. 单价、费率等定额类数据一律只搭结构，数值必须来自项目资料或定额文件。

用法:
    python calc.py --list
    python calc.py --chapter 7.3.2 --data 数据包.json [--ledger 台账.json]
    python calc.py --chapter 7.3.2 --set "治理后土壤流失量=180" --set "水土流失面积=86.5"
    python calc.py --chapter 2 --data 数据包.json --out 计算书_第2章.md
    python calc.py --chapter 7.3.2 --data 包.json --period 设计水平年
"""
import argparse, ast, json, os, re, sys

sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import check_gate as G   # noqa: E402
import ledger as L       # noqa: E402

RULES = G.RULES
CE = RULES.get('calc_engine', {})
ITEMS = CE.get('items', {})
SG = CE.get('spec_given_params', {})
LABELS = CE.get('param_source_labels', {})
MISSING = CE.get('missing_marker', '【待填：%s】')
PKG_FIELDS = {f['key']: f for f in RULES.get('data_package', {}).get('fields', [])}
# 每个字段在 rules 里声明了单位：公式代入前一律归一到声明单位（公式内的换算系数依赖它）
DECL_UNIT = {k: (v.get('unit') or '') for k, v in PKG_FIELDS.items()}

# 单位族：同族可换算（换算在计算书中明示），异族拒绝计算
FAMILIES = {
    'area': {'hm²': 1.0, 'hm2': 1.0, '公顷': 1.0, 'km²': 100.0, 'km2': 100.0, 'm²': 1e-4, 'm2': 1e-4, '亩': 1 / 15.0},
    'volume': {'m³': 1.0, 'm3': 1.0, '万m³': 10000.0, '万m3': 10000.0, '万方': 10000.0},
    'length': {'m': 1.0, 'km': 1000.0, 'mm': 0.001, 'cm': 0.01},
    'mass': {'t': 1.0, '万t': 10000.0},
    'money': {'万元': 1.0, '元': 1e-4},
    'percent': {'%': 1.0},
    'ratio': {'无量纲': 1.0, '': 1.0},
    'flow': {'t/(km²·a)': 1.0, 't/(km2·a)': 1.0},
}


def unit_family(u):
    u = (u or '').strip()
    for fam, table in FAMILIES.items():
        if u in table:
            return fam, table[u]
    return None, 1.0


def to_base(value, unit):
    """换算到族内基准单位；族外单位原样返回。"""
    fam, factor = unit_family(unit)
    if fam is None:
        return float(value), unit, 1.0
    return float(value) * factor, fam, factor


# ============================================================ 取值
class Inputs:
    """参数池：数据包 + 台账 + 命令行 --set，按优先级覆盖（--set > 台账 > 数据包）。"""

    def __init__(self, package=None, ledger_path=None, overrides=None):
        self.values, self.prov = {}, {}
        self.notes = []
        if package:
            pkg = G.read_json(package, '项目数据包')
            self._load_package(pkg, package)
        if ledger_path and os.path.exists(ledger_path):
            led = L.load(ledger_path)
            for k, rec in (led.get('entries') or {}).items():
                self.values.setdefault(k, {'raw': rec.get('value'), 'unit': rec.get('unit') or '',
                                           'source': 'project_input', 'from': '台账',
                                           'status': rec.get('status')})
            self.notes.append('台账：%s（%d 条已定事实）' % (os.path.basename(ledger_path), len(led.get('entries') or {})))
        for k, v in (overrides or {}).items():
            self.values[k] = {'raw': v, 'unit': self.values.get(k, {}).get('unit', ''),
                              'source': 'project_input', 'from': '命令行', 'status': '已确认'}
        # 区划/等级/时段的规范化名字也入池，便于指标项取目标值
        for alias, canon in (('水土保持区划', '水土保持区划'), ('防治标准执行等级', '防治标准执行等级')):
            if canon in self.values:
                self.values[alias] = self.values[canon]

    def _load_package(self, pkg, package):
        """解析数据包，**同时支持三种形态**。

        ## 为什么要支持三种（自检发现的真实缺陷）

        早先只认 ingest.py 产出的规范形态 `{"fields": {"挖方": {"value":…}}}`，
        于是把最自然的**扁平形态** `{"挖方": "15.2"}` 喂进来时，
        引擎会报「数据包/台账中无此字段」——**数明明给了，却算不出来**。
        这既误导使用者（以为字段缺失），又可能诱导其"补个默认值"，非常危险。

        支持形态：
          ① `{"fields": {name: {value, unit, status, provenance}}}`（ingest.py 产出）
          ② `{"挖方": "15.2", "填方": 9.8}`（手写扁平；值可为标量或 {value,unit}）
          ③ `{"fields": [{key/name, value, unit}]}`（列表形态）
          另兼容顶层直接是 name→值 的映射（即整个 json 就是形态②）。

        数值含千分位逗号、带单位后缀（如 "15.2万m³"）时，此处**仅剥离千分位**，
        带单位者保留原样交由 `number()` 判定为"非纯数值，须人工换算"——
        绝不替使用者做单位换算（那是最隐蔽的编造）。
        """
        fields = pkg.get('fields')
        # 形态③：fields 是列表
        if isinstance(fields, list):
            for rec in fields:
                if not isinstance(rec, dict):
                    continue
                k = rec.get('key') or rec.get('name')
                if not k:
                    continue
                val = rec.get('value')
                if val in (None, ''):
                    continue
                self.values[k] = {'raw': val, 'unit': rec.get('unit') or '',
                                  'source': 'project_input', 'from': '数据包',
                                  'status': rec.get('status')}
            self._note_package(pkg, package)
            return
        # 形态①：fields 是 dict
        if isinstance(fields, dict):
            for k, v in fields.items():
                if isinstance(v, dict):
                    val = v.get('value')
                    if val in (None, ''):
                        continue
                    self.values[k] = {'raw': val, 'unit': v.get('unit') or '',
                                      'source': 'project_input', 'from': '数据包',
                                      'status': v.get('status')}
                    self.prov[k] = (v.get('provenance') or [{}])[0]
                elif v not in (None, ''):
                    self.values[k] = {'raw': v, 'unit': '',
                                      'source': 'project_input', 'from': '数据包',
                                      'status': '已确认'}
            self._note_package(pkg, package)
            return
        # 形态②：整个 json 就是扁平映射（无 fields 键）
        flat = {k: v for k, v in pkg.items()
                if k not in ('stats', 'meta', 'conflicts', 'notes', '_comment')}
        for k, v in flat.items():
            if isinstance(v, dict):
                val = v.get('value')
                if val in (None, ''):
                    continue
                self.values[k] = {'raw': val, 'unit': v.get('unit') or '',
                                  'source': 'project_input', 'from': '数据包（扁平）',
                                  'status': v.get('status')}
            elif v not in (None, '') and not isinstance(v, (list, dict)):
                self.values[k] = {'raw': v, 'unit': '',
                                  'source': 'project_input', 'from': '数据包（扁平）',
                                  'status': '已确认'}
        self.notes.append('数据包：%s（扁平格式，已读入 %d 个字段；'
                          '建议用 ingest.py 产出规范格式以获得冲突标注）'
                          % (os.path.basename(package), len(self.values)))

    def _note_package(self, pkg, package):
        self.notes.append('数据包：%s（已确认 %s 项，冲突 %s 项）'
                          % (os.path.basename(package),
                             (pkg.get('stats') or {}).get('confirmed'),
                             (pkg.get('stats') or {}).get('conflict')))

    def get(self, name):
        """按字段名/别名取值。"""
        if name in self.values:
            return dict(self.values[name], key=name)
        for key, f in PKG_FIELDS.items():
            if name == key or name in f.get('aliases', []) or name in f.get('weak_aliases', []):
                if key in self.values:
                    return dict(self.values[key], key=key)
        return None

    def number(self, name):
        """取数值；返回 (值, 单位, 说明) 或 (None, '', 原因)。"""
        v = self.get(name)
        if not v:
            return None, '', '数据包/台账中无此字段'
        raw = str(v['raw']).replace(',', '').strip()
        if not re.fullmatch(r'-?\d+(?:\.\d+)?', raw):
            return None, v.get('unit', ''), '取值「%s」不是纯数值（可能是文本描述，须人工换算）' % raw
        return float(raw), v.get('unit', ''), '%s（%s）' % (v.get('from'), v.get('status') or '已确认')


# ============================================================ 安全表达式求值
# 允许的语法由 safe_eval 逐个节点判定（见下），没有独立的白名单常量——
# 曾经有一个 _ALLOWED 元组，既没人读、又与实现不同步（列了 ast.Mod 却不支持取模）。
# 全角/排版符号 → 机读运算符（rules 里的 expr 已用 ASCII，这里再兜一层）
OP_NORM = {'×': '*', '÷': '/', '（': '(', '）': ')', '−': '-', '－': '-', '＊': '*', '／': '/'}


def norm_expr(expr):
    for a, b in OP_NORM.items():
        expr = expr.replace(a, b)
    return expr


def safe_eval(expr, env):
    """只允许算术与 min/max/abs/round；拒绝属性访问、下标、import 等一切其它语法。"""
    tree = ast.parse(norm_expr(expr), mode='eval')

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError('表达式中出现非数值常量：%r' % (node.value,))
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.Div):
                if b == 0:
                    raise ZeroDivisionError('除数为 0')
                return a / b
            if isinstance(node.op, ast.Pow):
                return a ** b
            if isinstance(node.op, ast.Mod):
                if b == 0:
                    raise ZeroDivisionError('取模的除数为 0')
                return a % b
            raise ValueError('不支持的运算符')
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Name):
            if node.id not in env:
                raise KeyError(node.id)
            return env[node.id]
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ('min', 'max', 'abs', 'round'):
                raise ValueError('只允许 min/max/abs/round')
            return {'min': min, 'max': max, 'abs': abs, 'round': round}[node.func.id](*[ev(a) for a in node.args])
        raise ValueError('表达式含不允许的语法：%s' % type(node).__name__)

    return ev(tree)


def fmt(x):
    if x is None:
        return '—'
    if isinstance(x, float):
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
        return ('%.4f' % x).rstrip('0').rstrip('.')
    return str(x)


# ============================================================ 取指标目标值
LEVEL_ALIAS = {'一级': '一级', '一级标准': '一级', '1级': '一级',
               '二级': '二级', '二级标准': '二级', '2级': '二级',
               '三级': '三级', '三级标准': '三级', '3级': '三级'}


def norm_zone(z):
    if not z:
        return None
    z = re.sub(r'^[ⅠⅡⅢⅣⅤIVX0-9\-\s\.]+', '', str(z)).strip()
    for k in SG.get('indicator_targets', {}).get('zones', {}):
        if k == z or k in z or z in k:
            return k
    for k in SG.get('allowable_soil_loss', {}).get('values', {}):
        if k in z or z in k:
            return k
    return None


def norm_level(v):
    if not v:
        return None
    s = str(v).strip()
    for k, canon in LEVEL_ALIAS.items():
        if s.startswith(k) or canon in s:
            return canon
    return None


def targets_for(inputs, period):
    """按 区划 × 等级 × 时段 取目标值；缺区划或等级 → 明确标注缺数据。"""
    z_raw = (inputs.get('水土保持区划') or {}).get('raw')
    l_raw = (inputs.get('防治标准执行等级') or {}).get('raw')
    zone, level = norm_zone(z_raw), norm_level(l_raw)
    out = {'zone_input': z_raw, 'level_input': l_raw, 'zone': zone, 'level': level,
           'period': period, 'values': {}, 'missing': []}
    if not zone:
        out['missing'].append('水土保持区划')
    if not level:
        out['missing'].append('防治标准执行等级')
    if zone and level:
        z = SG['indicator_targets']['zones'].get(zone, {})
        lv = z.get(level) or z.get(level + '标准') or {}
        out['values'] = lv.get(period) or {}
        if not out['values']:
            out['missing'].append('目标值（区划×等级×时段未录：%s/%s/%s）' % (zone, level, period))
    return out


# ============================================================ 计算项执行
def do_sum_check(item, inputs, name):
    """勾稽：各分项之和 = 合计（分项与合计字段由 rules 显式声明）。"""
    total_name = item.get('total_field')
    pool = list(item.get('parts_pool') or [])
    if not pool:
        pool = [i['name'] for i in item['inputs'] if i['name'] != total_name]
    res = {'lines': [], 'missing': [], 'unit': '', 'parts_pool': pool, 'total_field': total_name}
    vals, units, missing = [], [], []
    for k in pool:
        v, u, why = inputs.number(k)
        if v is None:
            missing.append('%s（%s）' % (k, why))
            continue
        vals.append((k, v, u))
        units.append(u)
    tval, tunit, twhy = inputs.number(total_name) if total_name else (None, '', '未声明 total_field')
    if tval is None:
        missing.append('%s（%s）' % (total_name, twhy))
    else:
        units.append(tunit)
    res['missing'] = missing
    if missing:
        res['status'] = '缺数据不可计算'
        return res
    fams = {unit_family(u)[0] for u in units}
    if len(fams) > 1:
        res['status'] = '单位不一致，不可计算'
        res['note'] = '出现不同量纲：%s —— 须先由人工统一到同一量纲' % '、'.join(sorted(str(f) for f in fams))
        return res
    fam = fams.pop()
    base = {u: unit_family(u)[1] for u in units}
    s = sum(v * base[u] for _, v, u in vals)
    tbase = tval * base[tunit]
    diff = s - tbase

    # ---- 容差语义（自检抓出的 bug，务必分清）----
    #
    # 原实现：`ok = abs(diff) <= tol * max(1.0, abs(tbase))`，tol 取 rules 的 0.01。
    # 后果：工程占地 86.5 hm² 时容差 = 0.865，于是
    #       **「永久占地 50 + 临时占地 36.6 = 86.6」被判为「勾稽闭合」**（差 0.1）；
    #       更极端时差 0.8 hm² 也会被放过——这正是评审最容易挑出来的错，却查不出来。
    #
    # 根因：`sum_check`（分类之和 = 合计）与 `balance_check`（土石方平衡）
    # 的**容差语义不同**，却共用了同一个 0.01：
    #   · 土石方平衡：0.01 作**相对容差**合理（万m³ 级量，1% 是允许的凑整误差）；
    #   · 分类统计合计：是**定义式恒等**，必须精确相等，只应容忍浮点误差。
    #
    # 修正：sum_check 用**绝对容差**，默认 1e-6（浮点精度级），
    # 允许 rules 用 `tolerance_abs` 显式覆盖；
    # 若项目确有凑整惯例，应显式写进 rules，而不是靠默认放宽。
    tol_abs = item.get('tolerance_abs')
    if tol_abs is None:
        tol_abs = 1e-6
    tol_rel = item.get('tolerance_rel', 0.0)
    limit = float(tol_abs) + float(tol_rel) * abs(tbase)
    ok = abs(diff) <= limit

    res.update({
        'ok': ok,
        'status': ('勾稽闭合' if ok else
                   '勾稽不闭合（差 %s %s，容差 %s）'
                   % (fmt(diff / (base[tunit] or 1)), tunit, fmt(limit / (base[tunit] or 1)))),
        'expr': ' + '.join('%s %s' % (k, fmt(v)) for k, v, _ in vals) + ' = ' + fmt(s) + ' ' + tunit,
        'total': tbase, 'sum': s, 'diff': diff, 'unit': tunit,
        'tolerance_used': limit,
    })
    if any(base[u] != 1.0 for u in units):
        res['conversion'] = '已按同族单位换算到「%s」后比较：%s' % (
            tunit, '、'.join('%s %s' % (k, u) for k, _, u in vals if base[u] != 1.0))
    return res


def do_balance_check(item, inputs, name):
    """平衡勾稽：按 rules 的 total_field（首项）与其余输入求残差。"""
    total_name = item.get('total_field')
    names = [i['name'] for i in item['inputs'] if i.get('required', True) and i['name'] != total_name]
    opt = [i['name'] for i in item['inputs'] if not i.get('required', True) and i['name'] != total_name]
    if total_name and total_name not in names:
        names = [total_name] + names
    elif not total_name and names:
        total_name = names[0]
    vals, unit_of, units, missing = {}, {}, set(), []
    for n in names + opt:
        v, u, why = inputs.number(n)
        if v is None:
            if n in names and n != total_name:
                missing.append('%s（%s）' % (n, why))
            elif n == total_name:
                missing.append('%s（%s）' % (n, why))
            else:
                vals[n] = 0.0
                unit_of[n] = u
            continue
        vals[n] = v
        unit_of[n] = u
        units.add(u)
    res = {'lines': [], 'missing': missing, 'unit': '', 'total_field': total_name}
    if missing:
        res['status'] = '缺数据不可计算'
        return res
    fams = {unit_family(u)[0] for u in units}
    if len(fams) > 1:
        res['status'] = '单位不一致，不可计算'
        res['note'] = '出现不同量纲：%s' % '、'.join(sorted(str(f) for f in fams))
        return res
    # 逐值按**各自单位**换算到族内基准单位后再比残差。
    # 曾经写成「全部乘 sorted(units)[0] 的系数」——单一时段单一时段单位下看不出问题，
    # 一旦「万m³」与「m³」混用就会算出荒唐残差（真平衡报不平衡，明显不平衡反而报闭合）。
    conv = {}
    for k, x in vals.items():
        conv[k], _fam, _f = to_base(x, unit_of.get(k, ''))
    u0 = unit_of.get(total_name) or (sorted(units)[0] if units else '')
    factor = unit_family(u0)[1] or 1.0
    rest = [n for n in vals if n != total_name]
    residual_base = conv[total_name] - sum(conv[n] for n in rest)
    residual = residual_base / factor          # 用总量字段的单位表示
    expr_terms = ['%s %s %s' % (total_name, fmt(vals[total_name]), unit_of.get(total_name, ''))]
    for n in rest:
        expr_terms.append('− %s %s %s' % (n, fmt(vals[n]), unit_of.get(n, '')))
    tol = item.get('tolerance', 0.01)
    ok = abs(residual_base) <= tol * max(1.0, abs(conv[total_name]))
    res.update({
        'ok': ok,
        'status': '平衡闭合' if ok else '不平衡（残差 %s %s）' % (fmt(residual), u0),
        'expr': ' '.join(expr_terms) + ' = ' + fmt(residual) + ' ' + u0,
        'residual': residual,
        'unit': u0,
    })
    if len({unit_family(u)[1] for u in units}) > 1:
        res['conversion'] = '各值已按自身单位换算到「%s」后比较：%s' % (
            u0, '、'.join('%s %s' % (k, unit_of.get(k, '')) for k in vals
                          if unit_family(unit_of.get(k, ''))[1] != factor))
    present_opt = [n for n in opt if n in vals and vals[n]]
    if '借方' in present_opt and '弃方' in present_opt:
        res['note'] = '借方与弃方同时为正 → 需复核（借方与弃方一般不同时出现）'
    return res


def do_formula(item, inputs, name):
    """公式项：逐式代入求值。标了 numeric=false 的输入只作结构性记录，不进公式。"""
    out = {'lines': [], 'missing': [], 'results': {}, 'ok': None, 'structural': []}
    env, missing, unit_env = {}, [], {}
    for i in item['inputs']:
        if i.get('numeric', True) is False:
            v = inputs.get(i['name'])
            out['structural'].append({'input': i['name'], 'value': (v or {}).get('raw'),
                                      'ok': bool(v and (v or {}).get('raw') not in (None, ''))})
            continue
        v, u, why = inputs.number(i['name'])
        if v is None:
            missing.append('%s（%s）' % (i['name'], why))
            continue
        env[i['name']] = v
        unit_env[i['name']] = u or ''
    out['missing'] = missing
    if missing:
        out['status'] = '缺数据不可计算'
        out['need'] = [MISSING % i['name'] for i in item['inputs'] if i.get('numeric', True) is not False]
        return out
    # 公式把数值直接代入，所以单位必须交代清楚：
    #  · 同族不同单位（万m³ 与 m³）→ 换算到主用单位并写明。否则 9 与 84000 会直接相减，
    #    得出一个看不出错的假结果；
    #  · 异族（hm² 与 m 同处一式）→ **不阻断**：模板里「面积 × 厚度」本身就自带换算系数
    #    （4.4 的 ×10000），公式自己拥有换算权；但必须把各输入单位印在计算书上，
    #    好让「厚度误填成 cm」这类数据错一眼可见。
    # 公式自带换算系数（如 4.4 的 ×10000 假定面积是 hm²），所以代入前必须把每个输入
    # 归一到 **rules 声明的单位**，而不是「谁出现得多就用谁」。数据包写成 m² 时，
    # 归一到 hm² 才是对的；量纲不符则拒绝计算（面积不可能变成长度）。
    conv_notes, bad_units = [], []
    orig = dict(env)
    for k in list(env):
        du = DECL_UNIT.get(k, '')
        u = unit_env.get(k, '')
        if not du or not u or u == du:
            continue
        if unit_family(u)[0] != unit_family(du)[0]:
            bad_units.append('%s：声明 %s，实际 %s（量纲不同）' % (k, du, u))
            continue
        base_v, _f, _fa = to_base(env[k], u)
        env[k] = base_v / (unit_family(du)[1] or 1.0)
        conv_notes.append('%s %s %s→%s=%s' % (k, fmt(orig[k]), u, du, fmt(env[k])))
    out['unit_disclosure'] = '输入单位：' + '、'.join(
        '%s %s' % (k, unit_env[k] or '无单位') for k in unit_env)
    if bad_units:
        out['status'] = '单位不一致，不可计算'
        out['note'] = '；'.join(bad_units)
        return out
    if conv_notes:
        out['conversion'] = '已归一到 rules 声明的单位后代入：%s' % '、'.join(conv_notes)
    fams = {unit_family(u)[0] for u in unit_env.values() if u}
    if len(fams) > 1:
        out['unit_disclosure'] += '（含 %s 种量纲；换算系数由公式自身承担，见「单位说明」）' % len(fams)
    computed = {}
    for label, expr in item.get('formula', {}).items():
        e = norm_expr(expr)
        scope = dict(env, **computed)          # 支持链式公式：后式可引用前式结果
        for vname in sorted(scope, key=len, reverse=True):
            e = re.sub(r'(?<![\u4e00-\u9fff])' + re.escape(vname) + r'(?![\u4e00-\u9fff])',
                       '(' + fmt(scope[vname]) + ')', e)
        if re.search(r'[\u4e00-\u9fff]', e):
            out['lines'].append({'label': label, 'formula': expr,
                                 'status': '含未解析变量：%s（既不是本项输入，也不是前面已求出的中间量）'
                                           % '、'.join(sorted(set(re.findall(r'[\u4e00-\u9fff]+', e))))})
            out['ok'] = False
            continue
        try:
            val = safe_eval(e, {})
        except Exception as ex:
            out['lines'].append({'label': label, 'formula': expr, 'substituted': e,
                                 'status': '求值失败：%s' % ex})
            out['ok'] = False
            continue
        computed[label] = val
        out['results'][label] = val
        out['lines'].append({'label': label, 'formula': expr, 'substituted': e, 'value': val})
    if out['results'] and out['ok'] is None:
        out['ok'] = True
        out['status'] = '已求值 %d 式（含链式代入）' % len(out['results'])
    elif out['ok'] is False and not out['status']:
        out['status'] = '算式未全部求值成功（见逐式说明）'
    return out


def do_indicators(item, inputs, name, periods=('设计水平年', '施工期')):
    """六项防治指标：用 rules 里的机读算式 expr 逐项求值 + 与规范目标值比对。

    expr 与 inputs 的变量名一一对应（rules 内已校验）；formula 是人读公式，仅用于计算书展示。
    """
    scope = CE.get('period_indicator_scope', {})
    out = {'indicators': [], 'ok': None, 'missing': []}
    for period in periods:
        tg = targets_for(inputs, period)
        allow = scope.get(period)
        rows = []
        for ind, spec in item['indicators'].items():
            unit = spec.get('unit', '')
            expr = spec.get('expr')
            row = {'indicator': ind, 'formula': spec['formula'], 'unit': unit,
                   'target': (tg['values'] or {}).get(ind)}
            if expr is None:
                row['status'] = '缺机读算式（rules.calc_engine.items.7.3.2.indicators.%s.expr）' % ind
                rows.append(row)
                out['ok'] = False
                continue
            if allow is not None and ind not in allow:
                row['status'] = '规范该时段未设该指标（不判定）'
                rows.append(row)
                continue
            vals, miss = {}, []
            for vn in spec['inputs']:
                if vn == '容许土壤流失量':
                    v, why = spec_allowable(tg['zone']), ''
                    if v is None:
                        v, _u, why = inputs.number(vn)
                    if v is None:
                        miss.append('%s（规范容许流失量未对应该区划「%s」；%s）' % (vn, tg['zone'] or '缺区划', why))
                    else:
                        vals[vn] = v
                    continue
                v, _u, why = inputs.number(vn)
                if v is None:
                    miss.append('%s（%s）' % (vn, why))
                else:
                    vals[vn] = v
            row['inputs'] = vals
            if miss:
                row['status'] = '缺数据不可计算'
                row['missing'] = miss
                out['missing'].extend(miss)
                rows.append(row)
                continue
            e = expr
            for vn in sorted(vals, key=len, reverse=True):
                e = re.sub(r'(?<![\u4e00-\u9fff])' + re.escape(vn) + r'(?![\u4e00-\u9fff])',
                           '(' + fmt(vals[vn]) + ')', e)
            if re.search(r'[\u4e00-\u9fff]', e):
                row['status'] = '算式含未解析变量：%s' % '、'.join(sorted(set(re.findall(r'[\u4e00-\u9fff]+', e))))
                out['ok'] = False
                rows.append(row)
                continue
            try:
                val = safe_eval(e, {})
            except Exception as ex:
                row['status'] = '求值失败：%s' % ex
                out['ok'] = False
                rows.append(row)
                continue
            row['value'] = val
            row['substituted'] = e
            tgt = row['target']
            if tgt is None:
                row['status'] = '已算出，规范该时段/等级未设目标值'
            elif isinstance(tgt, str):
                row['status'] = '规范给定为「%s」，不作数值判定' % tgt
            else:
                row['verdict'] = '达标' if val >= float(tgt) else '不达标'
                row['status'] = '%s（%s vs 目标 %s）' % (row['verdict'], fmt(round(val, 4)), tgt)
                if row['verdict'] == '不达标':
                    out['ok'] = False
            # ---- 反算：给定目标值，求所需的"分子量"（纯代数变形，无经验系数）----
            # 规则见 rules.json calc_engine.items['7.3.2'].indicators[名].inverse
            inv_spec = spec.get('inverse')
            if inv_spec and tgt is not None and not isinstance(tgt, str):
                env2 = dict(vals)
                env2['目标值'] = float(tgt)
                try:
                    need = safe_eval(inv_spec['expr'], env2)
                    row['inverse'] = {
                        'solve': inv_spec['solve'], 'unit': inv_spec.get('unit'),
                        'expr': inv_spec['expr'], 'required': need,
                        'meaning': inv_spec.get('meaning'),
                        'note': inv_spec.get('note'),
                    }
                    # 用现措施的实际分子量与该需求比对 → 够不够
                    cur = vals.get(inv_spec['solve'])
                    if isinstance(cur, (int, float)):
                        row['inverse']['current'] = cur
                        row['inverse']['sufficient'] = cur >= need
                except Exception as ex:
                    row['inverse'] = {'solve': inv_spec['solve'], 'expr': inv_spec['expr'],
                                      'error': '反算失败：%s' % ex}
            rows.append(row)
        out['indicators'].append({'period': period, 'targets': tg, 'rows': rows})
    if out['ok'] is None:
        out['ok'] = True
    n_bad = sum(1 for blk in out['indicators'] for r in blk['rows'] if r.get('verdict') == '不达标')
    n_miss = sum(1 for blk in out['indicators'] for r in blk['rows'] if r.get('status') == '缺数据不可计算')
    if n_bad:
        out['status'] = '不达标 %d 项：%s' % (n_bad, '、'.join(
            r['indicator'] for blk in out['indicators'] for r in blk['rows'] if r.get('verdict') == '不达标'))
    elif n_miss:
        out['status'] = '缺数据不可计算 %d 项' % n_miss
    else:
        out['status'] = '六项指标全部算出并达标' if out['ok'] else '存在未闭合项'
    return out


def spec_allowable(zone):
    """按区划取规范给定的容许土壤流失量（SL 190-2007 表4.1.1）。"""
    av = SG.get('allowable_soil_loss', {}).get('values', {})
    if not zone:
        return None
    if zone in av:
        return float(av[zone])
    near = {'南方红壤区': '南方红壤丘陵区', '西南紫色土区': '西南土石山区', '西南岩溶区': '西南土石山区'}
    if zone in near and near[zone] in av:
        return float(av[near[zone]])
    return None


def do_structured(item, inputs, name):
    """结构性检查项：不做数值推算，只核对项目输入的结构完整性与覆盖。"""
    rows, missing = [], []
    for i in item['inputs']:
        v = inputs.get(i['name'])
        if not v or v.get('raw') in (None, ''):
            missing.append(i['name'])
        rows.append({'input': i['name'], 'value': (v or {}).get('raw'),
                     'source': LABELS.get(i.get('source'), i.get('source')),
                     'ok': bool(v and v.get('raw') not in (None, ''))})
    return {
        'lines': [], 'rows': rows, 'missing': missing,
        'status': ('结构完整（%d 项输入齐全）' % len(rows)) if not missing
                  else '缺 %d 项输入：%s' % (len(missing), '、'.join(missing)),
        'ok': None if missing else True,
        'note': item.get('note'),
        'no_generation': '本项不生成任何数量（点位数量/频次须逐字来自 Zone A，禁止凭经验拍数）'
                         if item.get('status') == '规则待逐字提取' else None,
    }


METHODS = {'sum_check': do_sum_check, 'balance_check': do_balance_check,
           'formula': do_formula, 'indicators': do_indicators, 'structured': do_structured}


def compute(chapters, inputs, period='设计水平年'):
    """对指定章/节点集合执行计算项。"""
    keys = [k for k in ITEMS if any(k == c or k.startswith(str(c) + '.') or str(c) == k.split('.')[0]
                                    for c in chapters)]
    results = []
    for k in sorted(keys, key=lambda s: [int(x) for x in s.split('.') if x.isdigit()]):
        item = ITEMS[k]
        method = item.get('method')
        fn = METHODS.get(method)
        if method == 'structured' and item.get('formula'):
            fn = do_formula            # 带公式的结构项：先求差值，再做结构核对
        if not fn:
            results.append({'chapter_id': k, 'name': item.get('name'), 'status': '无对应算法：%s' % method})
            continue
        r = fn(item, inputs, item.get('name', '')) if item.get('method') != 'indicators' \
            else fn(item, inputs, item.get('name', ''), periods=tuple(filter(None, [period, '施工期'])))
        r.update({'chapter_id': k, 'name': item.get('name'), 'method': item.get('method'),
                  'rule': item.get('rule') or item.get('note'), 'basis': item.get('basis'),
                  'unit_note': item.get('unit_note')})
        results.append(r)
    return results


# ============================================================ 计算书
def render_book(results, inputs, mode='md', chapters=None):
    L = ['# 计算书（由 calc.py 生成，可直接并入方案相应章节）', '']
    L.append('计算项：%s' % '、'.join('%s %s' % (r['chapter_id'], r['name']) for r in results))
    if inputs.notes:
        L.append('')
        L.append('参数来源：%s' % '；'.join(inputs.notes))
    L.append('')
    L.append('> 口径：参数只有两类来源——%s / %s。缺项目输入者一律标「缺数据不可计算」，'
             '不得用默认值或同类项目值填补。' % (LABELS.get('spec_given'), LABELS.get('project_input')))
    L.append('')
    for r in results:
        L.append('## %s %s' % (r['chapter_id'], r['name']))
        L.append('')
        if r.get('basis'):
            L.append('依据：%s' % r['basis'])
            L.append('')
        if r.get('rule'):
            L.append('勾稽/计算规则：%s' % r['rule'])
            L.append('')
        if r.get('unit_note'):
            L.append('单位说明：%s' % r['unit_note'])
            L.append('')
        if r.get('unit_disclosure'):
            L.append('> %s' % r['unit_disclosure'])
            L.append('')
        if r.get('conversion'):
            L.append('> %s' % r['conversion'])
            L.append('')
        if r.get('no_generation'):
            L.append('⚠ %s' % r['no_generation'])
            L.append('')
        if r['method'] == 'indicators':
            for blk in r['indicators']:
                tg = blk['targets']
                L.append('### %s' % blk['period'])
                L.append('')
                L.append('目标值取值：区划 %s（输入「%s」）× 等级 %s（输入「%s」）'
                         % (tg['zone'] or '【待填：水土保持区划】', tg['zone_input'] or '—',
                            tg['level'] or '【待填：防治标准执行等级】', tg['level_input'] or '—'))
                if tg['missing']:
                    L.append('')
                    L.append('⚠ 缺：%s' % '、'.join(tg['missing']))
                L.append('')
                L.append('| 指标 | 公式 | 代入 | 计算值 | 目标值 | 判定 |')
                L.append('|---|---|---|---|---|---|')
                for row in blk['rows']:
                    L.append('| %s | %s | %s | %s | %s | %s |'
                             % (row['indicator'], row['formula'],
                                row.get('substituted', '—'),
                                fmt(row.get('value')) if 'value' in row else '—',
                                fmt(row.get('target')) if not isinstance(row.get('target'), str)
                                else (row.get('target') or '—'),
                                row.get('status', '—')))
                L.append('')
                inv_rows = [r for r in blk['rows'] if r.get('inverse')
                            and 'required' in r['inverse']]
                if inv_rows:
                    L.append('**指标反算（给定目标 → 所需措施量）**')
                    L.append('')
                    L.append('| 指标 | 目标值 | 反算求解 | 算式 | 需要达到 | 现措施量 | 是否够 |')
                    L.append('|---|---|---|---|---|---|---|')
                    for r in inv_rows:
                        iv = r['inverse']
                        cur = iv.get('current')
                        suf = iv.get('sufficient')
                        L.append('| %s | %s | %s | %s | %s %s | %s | %s |'
                                 % (r['indicator'], fmt(r.get('target')), iv['solve'], iv['expr'],
                                    fmt(round(iv['required'], 4)), iv.get('unit') or '',
                                    fmt(round(cur, 4)) if isinstance(cur, (int, float)) else '—',
                                    ('够' if suf else '不够，须加大措施量')
                                    if isinstance(suf, bool) else '—'))
                    L.append('')
                    L.append('> 反算为纯代数变形（所需分子 = 目标值/100 × 分母），不含经验系数；'
                             '用于校核"按现措施能否达标"。')
                    L.append('')
                miss_rows = [r for r in blk['rows'] if r.get('missing')]
                if miss_rows:
                    L.append('本节缺项目输入（补齐后重跑）：')
                    L.append('')
                    for r in miss_rows:
                        for m in r['missing']:
                            L.append('- %s —— %s' % (MISSING % m.split('（')[0],
                                                     m.split('（', 1)[1].rstrip('）') if '（' in m else ''))
                    L.append('')
        elif r['method'] in ('sum_check', 'balance_check'):
            if r.get('missing'):
                L.append('状态：**%s**' % r.get('status', '缺数据不可计算'))
                L.append('')
                for m in r['missing']:
                    L.append('- %s' % (MISSING % m if '（' not in m else MISSING % m.split('（')[0] + '（' + m.split('（', 1)[1]))
            else:
                L.append('代入：%s' % r.get('expr'))
                L.append('')
                L.append('结论：**%s**%s' % (r['status'], ('；' + r['note']) if r.get('note') else ''))
            if r.get('conversion'):
                L.append('')
                L.append('说明：%s' % r['conversion'])
        elif r['method'] == 'formula':
            if r.get('missing'):
                L.append('状态：**缺数据不可计算**')
                L.append('')
                for m in r['missing']:
                    L.append('- %s' % m)
            for ln in r.get('lines', []):
                L.append('- **%s**：%s' % (ln['label'], ln['formula']))
                if ln.get('substituted'):
                    L.append('  - 代入：%s' % ln['substituted'])
                if 'value' in ln:
                    L.append('  - 结果：**%s**' % fmt(ln['value']))
                if ln.get('status'):
                    L.append('  - ⚠ %s' % ln['status'])
            if r.get('structural'):
                L.append('')
                L.append('结构性输入（不参与公式，供人工核对）：')
                for s in r['structural']:
                    L.append('- %s：%s' % (s['input'],
                                          s['value'] if s['value'] is not None else MISSING % s['input']))
        elif r['method'] == 'structured':
            L.append('状态：**%s**' % r.get('status'))
            L.append('')
            for row in r.get('rows', []):
                L.append('- %s：%s（%s）' % (row['input'], row['value'] if row['value'] is not None else MISSING % row['input'],
                                           row['source']))
            if r.get('note'):
                L.append('')
                L.append('> %s' % r['note'])
        L.append('')
    # 汇总缺数据清单：计算书自带补数清单，补齐后重跑即可
    need = []
    for r in results:
        for m in (r.get('missing') or []):
            k = m.split('（')[0]
            if k not in need:
                need.append(k)
    if need:
        L.append('---')
        L.append('')
        L.append('## 缺数据清单（本计算书未闭合的原因，补齐后重跑）')
        L.append('')
        for k in need:
            L.append('- %s' % (MISSING % k))
        L.append('')
        L.append('> 这些是**项目输入**（乙类参数），只能来自项目资料，不得用默认值或同类项目值代替。')
        L.append('> 规范给定参数（甲类）由本引擎按 zone 自动取值，不在本清单内。')
        L.append('')
    return '\n'.join(L) + '\n'


def audit():
    """自检：每个计算项的 project_input 是否都有数据包字段可承接；规范给定是否已录。

    这是防止「规则与数据字典脱节」的门禁：改了 rules 里的计算项却没补字段时，
    这里会立刻报出来，而不是等到填数据时才发现取不到值。
    """
    rows, bad = [], []
    for cid, item in ITEMS.items():
        for i in item.get('inputs', []):
            nm, src = i['name'], i.get('source')
            if i.get('multi') or i.get('numeric') is False:
                # 结构化输入（多项清单/文本），不要求落在数据包字段字典里
                rows.append({'item': cid, 'input': nm, 'source': src,
                             'resolved': '结构化输入（清单/文本）', 'ok': True})
                continue
            if src == 'spec_given' or nm == '容许土壤流失量':
                # 原来这里写的是 `... or True`，该分支恒真 → --audit 的「未对应 0 项」不可信。
                vals = SG.get('allowable_soil_loss', {}).get('values') or {}
                ok = (nm == '容许土壤流失量' and bool(vals)) or nm in vals
                rows.append({'item': cid, 'input': nm, 'source': 'spec_given',
                             'resolved': 'spec_given_params' if ok else None, 'ok': ok})
                if not ok:
                    bad.append({'item': cid, 'input': nm,
                                'detail': 'spec_given_params 取不到该键（rules.json calc_engine）'})
                continue
            hit = None
            for key, f in PKG_FIELDS.items():
                if nm == key or nm in f.get('aliases', []):
                    hit = key
                    break
            if hit is None:
                for key, f in PKG_FIELDS.items():
                    if nm in f.get('weak_aliases', []):
                        hit = key + '（弱别名）'
                        break
            rows.append({'item': cid, 'input': nm, 'source': src, 'resolved': hit, 'ok': hit is not None})
            if hit is None:
                bad.append({'item': cid, 'input': nm})
    # 指标项的 inputs 也一并核
    for ind, spec in (ITEMS.get('7.3.2', {}).get('indicators') or {}).items():
        for nm in spec.get('inputs', []):
            if nm == '容许土壤流失量':
                # 规范给定值：核对 rules.json 是否真的给了分区取值。
                # 原来这里直接 continue（等于不查），--audit 会漏掉这条。
                vals = SG.get('allowable_soil_loss', {}).get('values') or {}
                rows.append({'item': '7.3.2/%s' % ind, 'input': nm, 'source': 'spec_given',
                             'resolved': 'spec_given_params' if vals else None, 'ok': bool(vals)})
                if not vals:
                    bad.append({'item': '7.3.2/%s' % ind, 'input': nm,
                                'detail': 'rules.json calc_engine.spec_given_params 未给分区取值'})
                continue
            hit = next((k for k, f in PKG_FIELDS.items() if nm == k or nm in f.get('aliases', [])), None)
            rows.append({'item': '7.3.2/%s' % ind, 'input': nm, 'source': 'project_input',
                         'resolved': hit, 'ok': hit is not None})
            if hit is None:
                bad.append({'item': '7.3.2/%s' % ind, 'input': nm})
    # 指标 expr 变量与 inputs 一致性
    for ind, spec in (ITEMS.get('7.3.2', {}).get('indicators') or {}).items():
        used = set(re.findall(r'[\u4e00-\u9fff]+', spec.get('expr', '')))
        if used != set(spec.get('inputs', [])):
            bad.append({'item': '7.3.2/%s' % ind, 'input': 'expr 变量不一致',
                        'detail': '%s vs %s' % (sorted(used), sorted(spec.get('inputs', [])))})
    return {'items': len(ITEMS), 'checks': len(rows), 'unresolved': bad, 'rows': rows}


def inverse_report(inputs, chapter, overrides=None):
    """反算报告：给定防治指标目标值，求所需的"分子量"（措施量/面积/流失量）。

    只做代数变形：所需分子 = 目标值/100 × 分母（土壤流失控制比为目标值的分母）。
    分母取不到时如实记为缺数据，绝不假设。规则来自 rules.json 的 *.inverse。
    """
    item = ITEMS.get(chapter)
    if not item:
        return {'chapter': chapter, 'error': '该章节号无计算项（可用 --list 查看）'}
    if item.get('method') != 'indicators':
        inv = item.get('inverse')
        if not inv:
            return {'chapter': chapter, 'error': '该计算项未定义反算规则（rules.json 的 inverse）'}
        return {'chapter': chapter, 'item': item.get('name'), 'kind': 'formula',
                'inverse': inv}
    tg = targets_for(inputs, '设计水平年')
    # --target 覆盖：直接指定某指标的目标值（用于"假如要求 XX%"的反算试算）。
    # 注意必须覆盖 tg['values'] 里按 区划×等级 查到的值，否则覆盖不生效。
    for k, v in (overrides or {}).items():
        tg.setdefault('values', {})[k] = v
        if k in (tg.get('missing') or []):
            tg['missing'].remove(k)
    rows, missing = [], []
    for ind, spec in item['indicators'].items():
        inv = spec.get('inverse')
        if not inv:
            continue
        row = {'indicator': ind, 'solve': inv['solve'], 'unit': inv.get('unit'),
               'expr': inv['expr'], 'meaning': inv.get('meaning'), 'note': inv.get('note'),
               'target': (tg['values'] or {}).get(ind)}
        if isinstance(row['target'], str) or row['target'] is None:
            row['status'] = '缺目标值：需先定「水土保持区划 + 防治标准执行等级」'
            missing.append('%s 目标值' % ind)
            rows.append(row)
            continue
        env = {'目标值': float(row['target'])}
        need_vars, bad = [], []
        for vn in spec['inputs'] if 'inputs' in spec else re.findall(r'[\u4e00-\u9fff（）()]+', inv['expr']):
            pass
        # 取反算算式里的全部中文变量名
        for vn in sorted(set(re.findall(r'[\u4e00-\u9fff]{2,}', inv['expr'])), key=len, reverse=True):
            if vn == '目标值':
                continue
            v, _u, why = inputs.number(vn)
            if v is None:
                bad.append('%s（%s）' % (vn, why))
            else:
                env[vn] = v
        if bad:
            row['status'] = '缺数据不可反算'
            row['missing'] = bad
            missing.extend(bad)
            rows.append(row)
            continue
        try:
            need = safe_eval(inv['expr'], env)
            row['required'] = need
            row['status'] = '需达到 %s %s' % (fmt(round(need, 4)), inv.get('unit') or '')
        except Exception as ex:
            row['status'] = '反算失败：%s' % ex
            missing.append(ind)
        rows.append(row)
    return {'chapter': chapter, 'item': item.get('name'), 'kind': 'indicators',
            'period': '设计水平年', 'rows': rows, 'missing': missing}


def render_inverse(res):
    if res.get('error'):
        return '⚠ %s' % res['error']
    L = ['# 防治指标反算（给定目标 → 所需措施量）', '']
    L.append('计算项：%s　时段：%s' % (res.get('item') or '', res.get('period') or '—'))
    L.append('')
    L.append('> 反算为纯代数变形，不含经验系数；用于在写措施方案前先算"要达到目标需要多少"。')
    L.append('')
    L.append('| 指标 | 目标值 | 反算求解 | 算式 | 需要达到 | 说明 | 状态 |')
    L.append('|---|---|---|---|---|---|---|')
    for r in res.get('rows') or []:
        L.append('| %s | %s | %s | %s | %s | %s | %s |'
                 % (r['indicator'], fmt(r.get('target')), r['solve'], r['expr'],
                    ('%s %s' % (fmt(round(r['required'], 4)), r.get('unit') or ''))
                    if 'required' in r else '—',
                    (r.get('meaning') or '')[:28], r.get('status') or ''))
    if res.get('missing'):
        L.append('')
        L.append('缺数据（补齐后重跑）：')
        L.append('')
        for m in res['missing']:
            L.append('- %s' % m)
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser(description='计算引擎：按 rules.json calc_engine 求值并出计算书')
    ap.add_argument('--chapter', help='章号或节点号（可多个用逗号分隔）')
    ap.add_argument('--data', default=None, help='项目数据包 json（ingest.py 产出）')
    ap.add_argument('--ledger', default=None, help='事实台账 json')
    ap.add_argument('--set', action='append', default=[], help='直接给项目输入 key=value，可重复')
    ap.add_argument('--period', default='设计水平年', help='指标时段（默认设计水平年，同时给施工期）')
    ap.add_argument('--list', action='store_true', help='只列出计算项与所需输入')
    ap.add_argument('--audit', action='store_true', help='自检：计算输入与数据包字段是否对应')
    ap.add_argument('--inverse', action='store_true',
                    help='反算模式：给定防治指标目标值 → 求所需措施量（见 rules.calc_engine.*.inverse）')
    ap.add_argument('--target', action='append', default=[],
                    help='配合 --inverse：覆盖目标值 指标名=数值，可重复')
    ap.add_argument('--out', default=None)
    ap.add_argument('--json-only', action='store_true')
    a = ap.parse_args()

    if a.inverse:
        # 反算：不依赖完整数据包——只要分母已知即可求所需分子
        over2 = {}
        for kv in a.target:
            if '=' not in kv:
                raise SystemExit('--target 需要 指标名=数值：%s' % kv)
            k, v = kv.split('=', 1)
            over2[k.strip()] = float(v)
        inv_inputs = Inputs(a.data, a.ledger, over2)
        res = inverse_report(inv_inputs, a.chapter or '7.3.2', over2)
        if a.json_only:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print(render_inverse(res))
        sys.exit(1 if res.get('missing') else 0)

    if a.audit:
        au = audit()
        print(json.dumps(au, ensure_ascii=False, indent=1) if a.json_only else
              ('计算项 %d 个，输入核对 %d 项，未对应 %d 项%s'
               % (au['items'], au['checks'], len(au['unresolved']),
                  ('：\n' + '\n'.join('  %s ← %s' % (x['item'], x['input']) for x in au['unresolved']))
                  if au['unresolved'] else '（全部可承接）')))
        sys.exit(1 if au['unresolved'] else 0)

    if a.list or not a.chapter:
        print(json.dumps({'items': {k: {'name': v.get('name'), 'method': v.get('method'),
                                        'inputs': [(i['name'], i.get('source')) for i in v.get('inputs', [])],
                                        'status': v.get('status')} for k, v in ITEMS.items()},
                          'principle': CE.get('principle')}, ensure_ascii=False, indent=1))
        return

    over = {}
    for kv in a.set:
        if '=' not in kv:
            raise SystemExit('--set 需要 key=value：%s' % kv)
        k, v = kv.split('=', 1)
        over[k.strip()] = v.strip()
    inp = Inputs(a.data, a.ledger, over)
    chapters = [c.strip() for c in a.chapter.split(',') if c.strip()]
    results = compute(chapters, inp, a.period)
    payload = {'chapters': chapters, 'results': results,
               'principle': CE.get('principle'), 'missing_marker': MISSING,
               'incomplete': [{'chapter_id': r['chapter_id'], 'name': r.get('name'),
                               'status': r.get('status')} for r in results
                              if r.get('ok') is False or (r.get('missing') and r.get('ok') is None)
                              or (r.get('status') or '').startswith(('缺', '单位', '不平衡', '勾稽不'))]}
    if a.out:
        G.write_text(a.out, render_book(results, inp), '计算书')
        print('已写出计算书:', a.out)
    if a.json_only:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(render_book(results, inp))
        if payload['incomplete']:
            print('--- 未闭合项 ---')
            for x in payload['incomplete']:
                print('  %s %s：%s' % (x['chapter_id'], x['name'], x['status']))
    sys.exit(1 if payload['incomplete'] else 0)


if __name__ == '__main__':
    main()

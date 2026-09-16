"""压缩等价性验证：证明「精简包」没有丢任何实质信息。

用法（在技能包目录下）：
    python _meta/verify_equiv.py           # 逐项核对，全 OK 才 exit 0
    python _meta/verify_equiv.py --json

## 为什么必须有这个脚本

`compact_package` 做了四项压缩（键名速记 / 超长截断 / 去元数据壳 / 去重复）。
任何一项写错都会**静默丢信息**——而"漏了知识点"在本技能里是最严重的失误，
比多花 token 严重得多。这个脚本把"没丢信息"变成**可执行的断言**，
而不是靠人工阅读比对（人读 30 KB JSON 是查不出漏项的）。

## 核对口径

对同一章节分别生成 `--full`（完整包）与默认（精简包），
逐项比对**实质计数**。任何一项不等即判失败，并打印差异，便于定位。
"""
import json
import os
import subprocess
import sys
import tempfile

# 技能目录零残留：必须在 import 本地模块之前设置。
# Python 在 import 时先编译写盘、后执行模块体，
# 因此被 import 的模块自己设是来不及的。
sys.dont_write_bytecode = True

SK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

CHAPTERS = ['1.1', '1.6.2', '2.4', '2.7.6', '4.4', '5.2', '6.3',
            '7.3.2', '7.7', '8.2', '9.1.2']


def gen(chapter, full, out):
    cmd = [sys.executable, os.path.join(SK, 'scripts', 'write_chapter.py'),
           '--chapter', chapter, '--province', '河南省', '--out', out]
    if full:
        cmd.append('--full')
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if not os.path.exists(out):
        return None, (r.stderr or r.stdout or '')[:300]
    with open(out, encoding='utf-8') as f:
        return json.load(f), None


def counts_full(d):
    """完整包的实质计数（长键）。"""
    g = d.get('gate') or {}
    nd = d.get('nodes') or []
    dr = d.get('data_requirements') or {}
    return {
        'hc 硬约束条数': len(g.get('hard_constraints') or []),
        '节点数': len(nd),
        '内容点数': sum(len(n.get('required_content_points') or []) for n in nd),
        '写作指令条数': sum(len(n.get('writing_directive') or []) for n in nd),
        'ZoneB 条数': len(d.get('zone_b_references') or []),
        'ZoneC 条数': len(d.get('zone_c_references') or []),
        '写法样本条数': len(d.get('zone_c_style_samples') or []),
        '物种条数': len(d.get('species_references') or []),
        '工法条数': len(d.get('measure_methods') or []),
        '参数组数': len(d.get('design_params') or []),
        '数据需求条数': len(dr.get('project_inputs') or []),
        '表格数': len(d.get('tables') or []),
        '计算项数': len(d.get('calculations') or []),
    }


def counts_compact(d):
    """精简包的实质计数（短键 + 合并结构）。

    健壮性要求（踩过的坑）：这里**不能硬编码某个短键名**。
    早先写死 `x.get('pts')`，而 KEY_ALIAS 后来把 `pts` 改名为 `pts_`，
    于是计数变成 0，脚本误报"压缩丢信息"——**数据其实一条没少**。
    现在改为「候选键名列表 + 兜底取第一个 list 值」，别名再变也不会误报。
    """
    g = d.get('g') or {}
    nd = d.get('nd') or []
    dq = d.get('dq') or {}
    mcv = sum(len(n.get('mcv') or []) for n in nd)

    def pts_count(items):
        """数出「按节点分组的需项目输入」条目总数。"""
        total = 0
        for x in (items or []):
            if not isinstance(x, dict):
                continue
            got = None
            for k in ('pts', 'pts_', 'point', 'pt', 'items', 'it'):
                if isinstance(x.get(k), list):
                    got = x[k]
                    break
            if got is None:
                # 兜底：取第一个 list 值
                for v in x.values():
                    if isinstance(v, list):
                        got = v
                        break
            total += len(got or [])
        return total

    return {
        'hc 硬约束条数': len(g.get('hc') or g.get('hc') or []),
        '节点数': len(nd),
        '内容点数': mcv,
        '写作指令条数': mcv,
        'ZoneB 条数': len(d.get('zb') or []),
        'ZoneC 条数': len(d.get('zc') or []),
        '写法样本条数': len(d.get('zss') or []),
        '物种条数': len(d.get('sp') or []),
        '工法条数': len(d.get('mm') or []),
        '参数组数': len(d.get('dp') or []),
        '数据需求条数': pts_count(dq.get('pi') or dq.get('project_inputs')),
        '表格数': len(d.get('tb') or []),
        '计算项数': len(d.get('ca') or []),
    }


def main():
    as_json = '--json' in sys.argv
    tmp = tempfile.gettempdir()
    report = {'chapters': [], 'failed': []}
    for c in CHAPTERS:
        safe = c.replace('.', '_')
        f_full = os.path.join(tmp, 'eq_full_%s.json' % safe)
        f_comp = os.path.join(tmp, 'eq_comp_%s.json' % safe)
        df, err1 = gen(c, True, f_full)
        dc, err2 = gen(c, False, f_comp)
        if df is None or dc is None:
            report['chapters'].append({'chapter': c, 'error': err1 or err2})
            report['failed'].append(c)
            continue
        cf, cc = counts_full(df), counts_compact(dc)
        diffs = {k: {'full': cf[k], 'compact': cc[k]}
                 for k in cf if cf[k] != cc.get(k)}
        row = {'chapter': c, 'ok': not diffs, 'diffs': diffs,
               'counts': cf}
        report['chapters'].append(row)
        if diffs:
            report['failed'].append(c)
        for f in (f_full, f_comp):
            try:
                os.remove(f)
            except OSError:
                pass

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print('=== 压缩等价性验证（完整包 vs 精简包）===')
        for r in report['chapters']:
            if 'error' in r:
                print('  %-8s ERROR %s' % (r['chapter'], r['error']))
                continue
            mark = 'OK  ' if r['ok'] else 'FAIL'
            print('  %-8s %s  硬约束%2d 节点%2d 内容点%3d ZoneB%d ZoneC%d 物种%d 工法%d'
                  % (r['chapter'], mark,
                     r['counts']['hc 硬约束条数'], r['counts']['节点数'],
                     r['counts']['内容点数'], r['counts']['ZoneB 条数'],
                     r['counts']['ZoneC 条数'], r['counts']['物种条数'],
                     r['counts']['工法条数']))
            for k, v in (r.get('diffs') or {}).items():
                print('       DIFF %s: full=%s compact=%s' % (k, v['full'], v['compact']))
        if report['failed']:
            print('\n❌ 失败章节：%s' % '、'.join(report['failed']))
            print('   压缩丢信息，不得合入。请查 write_chapter.compact_package。')
            sys.exit(1)
        print('\n✅ 全部章节通过：精简包与完整包的实质信息计数完全一致。')
    if report['failed']:
        sys.exit(1)


if __name__ == '__main__':
    main()

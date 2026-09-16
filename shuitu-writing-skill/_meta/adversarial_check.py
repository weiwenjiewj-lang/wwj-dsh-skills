"""对抗性自检：专找逻辑漏洞与静默错误（不是"能不能跑"，而是"跑得对不对"）。

用法：
    python _meta/adversarial_check.py

这些检查针对的是**本技能最容易出、也最危险的错**：
不是崩溃，而是"看起来正常但结论错了"——例如指标口径反转、缺数据被默认值兜底、
压缩静默丢条目。崩溃一眼可见，静默错误会一路带到送审稿里。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

# 技能目录零残留：必须在**任何 import 之前**设置。
# 踩过的坑：本脚本后来加了 `import budget` 做字数检查，
# 而 Python 在 import 时先编译写盘、后执行模块体，
# 所以 budget.py 里的 sys.dont_write_bytecode 管不住自己的 .pyc——
# 结果每次跑对抗自检都在技能目录里留下 scripts\__pycache__。
# 唯一可靠的时机就是**入口脚本的最顶部**。
sys.dont_write_bytecode = True

SK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SK, 'scripts')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def run(args, timeout=180):
    r = subprocess.run([sys.executable] + args, capture_output=True,
                       text=True, encoding='utf-8', errors='replace',
                       timeout=timeout)
    return r.returncode, (r.stdout or ''), (r.stderr or '')


def main():
    tmp = tempfile.gettempdir()
    res = []

    def add(name, ok, detail=''):
        res.append((name, bool(ok), detail[:400]))

    # ============ A. 指标口径不得反转 ============
    # 土壤流失控制比 = 容许土壤流失量 ÷ 治理后土壤流失量
    data = os.path.join(tmp, 'adv1.json')
    with open(data, 'w', encoding='utf-8') as f:
        json.dump({'水土保持区划': '北方土石山区', '防治标准执行等级': '一级标准',
                   '容许土壤流失量': '200', '治理后土壤流失量': '100',
                   '水土流失面积': '50', '治理达标面积': '48',
                   '弃渣总量': '10', '已防护弃渣量': '9.8',
                   '可恢复面积': '30', '恢复林草面积': '27',
                   '可剥离表土量': '3.75', '保护表土量': '3.6',
                   '防治责任范围面积': '86.5', '林草面积': '22'}, f,
                  ensure_ascii=False)
    rc, out, err = run([os.path.join(SCRIPTS, 'calc.py'),
                        '--chapter', '7.3.2', '--data', data])
    # 容许 200 / 治理后 100 = 2.0；若被反转则是 0.5
    m = re.search(r'土壤流失控制比.*?\|\s*([\d.]+)\s*\|', out)
    val = float(m.group(1)) if m else None
    add('指标口径-土壤流失控制比方向正确',
        val is not None and abs(val - 2.0) < 0.01,
        '期望 2.0（200/100），实际 %s' % val)

    # 表土保护率 = 保护表土量 / 可剥离表土量 × 100
    m2 = re.search(r'表土保护率.*?\|\s*([\d.]+)\s*\|', out)
    v2 = float(m2.group(1)) if m2 else None
    add('指标口径-表土保护率方向正确',
        v2 is not None and abs(v2 - 96.0) < 0.1,
        '期望 96（3.6/3.75*100），实际 %s' % v2)

    # ============ B. 缺数据绝不用默认值兜底 ============
    empty = os.path.join(tmp, 'adv_empty.json')
    with open(empty, 'w', encoding='utf-8') as f:
        json.dump({}, f)
    rc, out, err = run([os.path.join(SCRIPTS, 'calc.py'),
                        '--chapter', '4.4', '--data', empty])
    # 不得出现"结果：<某数>"，必须全是待填
    has_result = bool(re.search(r'结果[:：]\s*\*\*-?[\d.]+', out))
    add('缺数据不得给出计算结果', not has_result,
        '缺数据却算出了结果：%s' % (out[:250] if has_result else ''))
    add('缺数据须输出待填占位符', '【待填' in out, out[:200])

    # ============ C. 压缩不得丢条目 ============
    for ch in ('7.7', '2.7.6'):
        f_full = os.path.join(tmp, 'adv_full_%s.json' % ch.replace('.', '_'))
        f_comp = os.path.join(tmp, 'adv_comp_%s.json' % ch.replace('.', '_'))
        run([os.path.join(SCRIPTS, 'write_chapter.py'), '--chapter', ch,
             '--province', '河南省', '--full', '--out', f_full])
        run([os.path.join(SCRIPTS, 'write_chapter.py'), '--chapter', ch,
             '--province', '河南省', '--out', f_comp])
        F = json.load(open(f_full, encoding='utf-8'))
        C = json.load(open(f_comp, encoding='utf-8'))
        nf = {'sp': len(F.get('species_references') or []),
              'mm': len(F.get('measure_methods') or []),
              'zb': len(F.get('zone_b_references') or []),
              'hc': len(F['gate']['hard_constraints'])}
        nc = {'sp': len(C.get('sp') or []), 'mm': len(C.get('mm') or []),
              'zb': len(C.get('zb') or []), 'hc': len(C.get('g', {}).get('hc') or [])}
        for k in nf:
            add('压缩不丢条目 %s.%s' % (ch, k), nf[k] == nc[k],
                '完整 %s vs 精简 %s' % (nf[k], nc[k]))

    # ============ D. 截断必须留回溯指针 ============
    F = json.load(open(f_full, encoding='utf-8'))
    hc = F['gate']['hard_constraints']
    long_reqs = [c for c in hc if len(c.get('requirement') or '') > 200]
    C = json.load(open(f_comp, encoding='utf-8'))
    chc = C['g']['hc']
    ok_ptr = True
    detail = ''
    for c in chc:
        rq = c.get('req') or ''
        if rq.endswith('…') and not c.get('more'):
            ok_ptr = False
            detail = '被截断的条款缺少 more 指针：%s' % rq[:80]
            break
    add('截断必留 more 指针', ok_ptr, detail)
    add('截断必留源文件引用表', isinstance(C['g'].get('src_files'), list)
        and len(C['g']['src_files']) > 0,
        'src_files=%s' % C['g'].get('src_files'))

    # ============ E. 闸门 blocked 时不得检索 Zone B ============
    # 构造一个不存在的章节，确认不返回 Zone B
    rc, out, err = run([os.path.join(SCRIPTS, 'write_chapter.py'),
                        '--chapter', '99.9', '--province', '河南省',
                        '--json-only'])
    add('非法章节被拒（退出码2）', rc == 2, 'rc=%s' % rc)

    # ============ F. 台账冲突必须拒绝写入 ============
    led = os.path.join(tmp, 'adv_ledger.json')
    if os.path.exists(led):
        os.remove(led)
    run([os.path.join(SCRIPTS, 'ledger.py'), '--file', led,
         '--set', '防治责任范围面积=86.5', '--unit', 'hm²', '--chapter', '1.6.1'])
    rc, out, err = run([os.path.join(SCRIPTS, 'ledger.py'), '--file', led,
                        '--set', '防治责任范围面积=99.9', '--unit', 'hm²',
                        '--chapter', '7.1'])
    conflict_rejected = (rc != 0) or ('冲突' in out) or ('拒绝' in out)
    add('台账冲突拒绝写入', conflict_rejected, 'rc=%s out=%s' % (rc, out[:200]))
    # 确认值没有被改掉
    rc2, out2, _ = run([os.path.join(SCRIPTS, 'ledger.py'), '--file', led, '--show'])
    add('台账冲突后原值未被覆盖', '86.5' in out2 and '99.9' not in out2,
        out2[:250])

    # ============ G. 报告表分支可用 ============
    rc, out, err = run([os.path.join(SCRIPTS, 'write_chapter.py'),
                        '--report-form', '--province', '河南省',
                        '--out', os.path.join(tmp, 'adv_rf.json')])
    rf = os.path.join(tmp, 'adv_rf.json')
    if os.path.exists(rf):
        D = json.load(open(rf, encoding='utf-8'))
        has_blocks = len(((D.get('report_form_skeleton') or {}).get('blocks')) or []) > 0
        add('报告表分支有区块骨架', has_blocks,
            json.dumps(D.get('report_form_skeleton'), ensure_ascii=False)[:200])
    else:
        add('报告表分支有区块骨架', False, (err or out)[:200])

    # ============ H. 单节回检与全书终检可运行 ============
    draft = os.path.join(tmp, 'adv_draft.md')
    with open(draft, 'w', encoding='utf-8') as f:
        f.write('# 1.1 项目简况\n\n## 1.1.1 项目基本情况\n'
                '本项目位于河南省平顶山市鲁山县，建设性质为新建。'
                '【待填：项目名称】\n')
    rc, out, err = run([os.path.join(SCRIPTS, 'check_draft.py'),
                        '--draft', draft, '--chapter', '1.1',
                        '--province', '河南省'])
    add('单节回检可运行', rc in (0, 1), 'rc=%s %s' % (rc, (err or out)[:200]))
    add('单节回检能发现占位符', '待填' in out,
        out[:250])

    rc, out, err = run([os.path.join(SCRIPTS, 'check_plan.py'),
                        '--draft', draft, '--province', '河南省'])
    add('全书终检可运行', rc in (0, 1), 'rc=%s %s' % (rc, (err or out)[:200]))

    # ============ I. 字数统计：多级子标题必须正确归属 ============
    # 这是真实踩过的坑：稿件带子标题（## 一、正文 / ### （一）xxx）时，
    # 原实现把正文归到空节点或误判成别的章，导致"本节 0 字、篇幅偏少 -100%"。
    sys.path.insert(0, SCRIPTS)
    try:
        import budget as B
    except Exception as e:
        add('字数统计模块可导入', False, str(e))
        B = None
    if B:
        sample = '\n'.join([
            '# 1.6.2 水土流失防治分区及措施',
            '',
            '## 一、正文',
            '本方案设计水平年为 2027 年。' * 5,
            '### （一）方案设计水平年',
            '设计水平年取值依据 GB 50433-2018。' * 5,
            '### （二）水土流失防治分区',
            '共划分 3 个防治分区。' * 5,
            '## 二、表格',
            '本节无强制表格。',
        ])
        res_map = B.split_by_node(sample)
        # 全篇应归到 1.6.2，不得有 '' 或其他节点
        bad = {k: v for k, v in res_map.items() if k != '1.6.2'}
        add('子标题正文归属正确（全归 1.6.2）', not bad,
            '错误归属：%s' % bad)
        add('无未归入任何节点的正文', '' not in res_map,
            "未归入 %s 字" % res_map.get('', 0))
        # 中文序号子标题不得被误判成节点号
        add('中文序号子标题不误判为节点号',
            B._match_node_in_head('（二）水土流失防治分区') == ''
            and B._match_node_in_head('（一）方案设计水平年') == '',
            '（二）水土流失防治分区 -> %r；'
            '（一）方案设计水平年 -> %r'
            % (B._match_node_in_head('（二）水土流失防治分区'),
               B._match_node_in_head('（一）方案设计水平年')))
        # 正常节点标题仍要能识别
        add('正常节点标题可识别',
            B._match_node_in_head('1.6.2 水土流失防治分区及措施') == '1.6.2'
            and B._match_node_in_head('7.7 分区措施布设') == '7.7',
            '1.6.2 -> %r' % B._match_node_in_head('1.6.2 水土流失防治分区及措施'))
        # 最长匹配：1.6.2 不被 1.6 抢先
        add('节点号最长匹配（1.6.2 优于 1.6）',
            B._match_node_in_head('1.6.2 xxx') == '1.6.2', '')

    # ============ 汇总 ============
    fails = [r for r in res if not r[1]]
    print('=== 对抗性自检（%d 项）===' % len(res))
    for name, ok, detail in res:
        print('  %s  %s' % ('OK  ' if ok else 'FAIL', name))
        if not ok and detail:
            print('        %s' % detail.replace('\n', ' ')[:300])
    print('\n通过 %d / %d' % (len(res) - len(fails), len(res)))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()

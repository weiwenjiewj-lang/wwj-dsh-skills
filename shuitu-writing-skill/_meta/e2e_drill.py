"""端到端演练：模拟"从头到尾完整编完一本方案"的全流程，找串联漏洞。

用法：
    python _meta/e2e_drill.py

## 为什么需要

前面两组自检都是**单点检查**（某个脚本能不能跑、某项逻辑对不对）。
但用户要的是「从头到尾完完整整帮我编写完水土方案」——
真正的风险在**串联**：第 3 步的产物喂给第 7 步时字段对不对得上、
台账在第 5 章定的值在第 9 章会不会打架、全书 95 节跑一遍会不会中途崩。

本演练用一个**虚构项目**（纯数据，不写回技能目录）把十二步走一遍，
每步都断言产物存在且可被下一步消费。
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
SCRIPTS = os.path.join(SK, 'scripts')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def run(args, timeout=300):
    r = subprocess.run([sys.executable] + args, capture_output=True,
                       text=True, encoding='utf-8', errors='replace',
                       timeout=timeout)
    return r.returncode, (r.stdout or ''), (r.stderr or '')


def main():
    # 工作区：用临时目录模拟"项目工作区"（绝不写进技能目录）
    ws = os.path.join(tempfile.gettempdir(), 'e2e_ws')
    os.makedirs(ws, exist_ok=True)
    steps = []

    def step(name, ok, detail=''):
        steps.append((name, ok, detail[:350]))
    S = lambda f: os.path.join(SCRIPTS, f)
    W = lambda f: os.path.join(ws, f)

    print('演练工作区：%s' % ws)

    # ---- 第1步 喂料：用一份"可研报告"文本让 ingest 抽取 ----
    src = W('可研摘要.md')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(
            '项目名称：某某矿区开采项目\n'
            '建设单位：某某矿业有限公司\n'
            '建设地点：河南省平顶山市鲁山县\n'
            '建设性质：新建\n'
            '项目规模：年产矿石 200 万吨\n'
            '总投资：35000 万元\n'
            '土建投资：12000 万元\n'
            '开工时间：2026年3月\n'
            '完工时间：2028年2月\n'
            '总工期：24 个月\n'
            '工程占地：86.5 hm²\n'
            '永久占地：50.0 hm²\n'
            '临时占地：36.5 hm²\n'
            '挖方：15.2 万m³\n'
            '填方：9.8 万m³\n'
            '弃方：5.4 万m³\n'
            '借方：0 万m³\n'
            '地貌类型：低山丘陵\n'
            '年均降水量：780 mm\n'
            '年均气温：14.5 ℃\n'
            '土壤类型：褐土\n'
            '林草覆盖率：26 %\n'
            '容许土壤流失量：200 t/(km²·a)\n'
            '防治标准执行等级：一级标准\n'
        )
    rc, out, err = run([S('ingest.py'), '--source', src, '--out', W('数据包.json')])
    pkg_ok = os.path.exists(W('数据包.json'))
    nfields = 0
    if pkg_ok:
        P = json.load(open(W('数据包.json'), encoding='utf-8'))
        nfields = len(P.get('fields') or {})
    step('第1步 喂料→数据包', pkg_ok and nfields >= 5,
         'rc=%s 字段数=%s %s' % (rc, nfields, (err or '')[:150]))

    # ---- 第2步 核数：查冲突项 ----
    conflicts = 0
    if pkg_ok:
        P = json.load(open(W('数据包.json'), encoding='utf-8'))
        for v in (P.get('fields') or {}).values():
            if isinstance(v, dict) and '冲突' in str(v.get('status') or ''):
                conflicts += 1
    step('第2步 核数（冲突项可枚举）', True, '冲突项 %d 个' % conflicts)

    # ---- 第3步 立账 ----
    ok3 = True
    for k, v in [('防治责任范围面积', '86.5'), ('工程占地', '86.5'),
                 ('防治标准执行等级', '一级标准'),
                 ('水土保持区划', '北方土石山区')]:
        args = [S('ledger.py'), '--file', W('台账.json'), '--set', '%s=%s' % (k, v),
                '--chapter', '1.6.1']
        if k == '防治责任范围面积':
            args += ['--unit', 'hm²']
        rc, o, e = run(args)
        if rc != 0:
            ok3 = False
    step('第3步 立账', ok3 and os.path.exists(W('台账.json')), '')

    # ---- 第4步 闸门（逐章）----
    gate_ok = True
    blocked = []
    for ch in ['1.1', '2.3', '2.4', '4.4', '5.2', '6.3', '7.3.2', '7.7', '8.2', '9.1.2']:
        rc, o, e = run([S('check_gate.py'), '--chapter', ch,
                        '--province', '河南省', '--json-only'])
        if rc == 3:
            blocked.append(ch)
        elif rc not in (0, 1):
            gate_ok = False
    step('第4步 闸门（10 个代表章节）', gate_ok,
         '被阻断章节：%s' % (blocked or '无'))

    # ---- 第5步 搭骨架 ----
    rc, o, e = run([S('outline.py'), '--province', '河南省', '--out', W('方案骨架.md')])
    skel_ok = os.path.exists(W('方案骨架.md'))
    nnode = 0
    if skel_ok:
        txt = open(W('方案骨架.md'), encoding='utf-8').read()
        nnode = txt.count('###')
    step('第5步 全书骨架', skel_ok, 'rc=%s 标题行≈%s %s' % (rc, nnode, (e or '')[:120]))

    # ---- 第6步 取指令包（全部 95 节点跑一遍）----
    tpl = json.load(open(os.path.join(SK, 'references', 'template-tree.json'),
                         encoding='utf-8-sig'))
    nodes = tpl.get('nodes') or []
    ids = [n.get('chapter_id') for n in nodes if n.get('chapter_id')]
    pack_fail = []
    total_bytes = 0
    for ch in ids:
        out_f = W('pk_%s.json' % ch.replace('.', '_'))
        rc, o, e = run([S('write_chapter.py'), '--chapter', ch,
                        '--province', '河南省', '--out', out_f], timeout=120)
        if not os.path.exists(out_f):
            pack_fail.append(ch)
        else:
            total_bytes += os.path.getsize(out_f)
            json.load(open(out_f, encoding='utf-8'))   # 必须是合法 JSON
    step('第6步 指令包（全部 %d 节点）' % len(ids), not pack_fail,
         '失败：%s' % (pack_fail or '无'))
    step('第6步 全书指令包总体积', True,
         '%.1f MB（%d 节点）' % (total_bytes / 1024 / 1024, len(ids)))

    # ---- 第7步 算数（全部计算项）----
    calc_ok = True
    calc_detail = []
    for ch in ['2.3', '2.4', '4.4', '6.3', '7.3.2', '8.2', '9.1.2']:
        rc, o, e = run([S('calc.py'), '--chapter', ch,
                        '--data', W('数据包.json'), '--ledger', W('台账.json')])
        calc_detail.append('%s:%s' % (ch, rc))
        if rc not in (0, 1):
            calc_ok = False
    step('第7步 计算书（7 项）', calc_ok, ' '.join(calc_detail))

    # ---- 第8/9步 写一节 + 单节回检 ----
    draft1 = W('第7章片段.md')
    with open(draft1, 'w', encoding='utf-8') as f:
        f.write('# 7.3.2 水土流失防治指标\n\n'
                '本项目位于河南省平顶山市鲁山县，防治责任范围面积 86.5 hm²。'
                '执行一级防治标准。\n'
                '水土流失治理度 96%，土壤流失控制比 2.0，渣土防护率 98%。\n')
    rc, o, e = run([S('check_draft.py'), '--draft', draft1, '--chapter', '7.3.2',
                    '--province', '河南省', '--ledger', W('台账.json')])
    step('第9步 单节回检', rc in (0, 1), 'rc=%s' % rc)

    # ---- 第10步 全书终检 ----
    rc, o, e = run([S('check_plan.py'), '--draft', draft1, '--province', '河南省',
                    '--ledger', W('台账.json'), '--out', W('终检报告.md')])
    step('第10步 全书终检', os.path.exists(W('终检报告.md')),
         'rc=%s %s' % (rc, (e or '')[:150]))

    # ---- 第11步 台账复核 ----
    rc, o, e = run([S('ledger.py'), '--file', W('台账.json'),
                    '--draft', draft1, '--check'])
    step('第11步 台账复核', rc in (0, 1), 'rc=%s %s' % (rc, (o or e)[:150]))

    # ---- 跨章口径一致性：台账值与稿件值 ----
    rc, o, e = run([S('ledger.py'), '--file', W('台账.json'), '--show'])
    step('台账可回读已定事实', '86.5' in o, o[:150])

    # ---- 汇总 ----
    fails = [s for s in steps if not s[1]]
    print('\n=== 端到端演练结果 ===')
    for name, ok, detail in steps:
        print('  %s  %s' % ('OK  ' if ok else 'FAIL', name))
        if detail:
            print('        %s' % detail.replace('\n', ' ')[:300])
    print('\n通过 %d / %d' % (len(steps) - len(fails), len(steps)))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()

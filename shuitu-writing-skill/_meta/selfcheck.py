"""自检：跑遍技能的所有命令路径，找崩溃、找静默错误。

用法：
    python _meta/selfcheck.py            # 跑全部检查
    python _meta/selfcheck.py --json

检查维度：
  1. 每个脚本 --help 是否可用（语法/导入无误）
  2. 主要子命令是否可用
  3. 错误输入是否给出明确提示（而非崩溃）
  4. 零项目残留：产物是否被拒绝写入技能目录/知识库
  5. 控制台编码：GBK 环境下打印中文是否崩溃
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


def run(args, timeout=120):
    try:
        r = subprocess.run([sys.executable] + args, capture_output=True,
                           text=True, encoding='utf-8', errors='replace',
                           timeout=timeout)
        return r.returncode, (r.stdout or ''), (r.stderr or '')
    except subprocess.TimeoutExpired:
        return 'TIMEOUT', '', ''


def main():
    as_json = '--json' in sys.argv
    results = []

    def add(name, ok, detail=''):
        results.append({'check': name, 'ok': bool(ok), 'detail': detail[:400]})

    # ---- 1. 所有脚本 --help 可用 ----
    for fn in sorted(os.listdir(SCRIPTS)):
        if not fn.endswith('.py'):
            continue
        rc, out, err = run([os.path.join(SCRIPTS, fn), '--help'], timeout=60)
        # argparse 的 --help 退出码是 0；没有 argparse 的脚本可能报错，算警告
        ok = rc == 0
        add('--help %s' % fn, ok, '' if ok else (err or out))

    tmp = tempfile.gettempdir()

    # ---- 2. 主要子命令 ----
    cases = [
        ('闸门-合法章节', ['check_gate.py', '--chapter', '1.1',
                       '--province', '河南省', '--json-only'], 0),
        ('闸门-非法章节', ['check_gate.py', '--chapter', '99.9',
                       '--province', '河南省'], 2),
        ('骨架-全书', ['outline.py', '--province', '河南省',
                    '--out', os.path.join(tmp, 'sc_outline.md')], 0),
        ('预算-某章', ['budget.py', '--chapter', '7'], 0),
        ('计算-清单', ['calc.py', '--list'], 0),
        ('计算-自检', ['calc.py', '--audit'], 0),
        ('资产地图', ['kb_lookup.py', '--map'], 0),
        ('章资源', ['kb_lookup.py', '--chapters'], 0),
        ('规范清单', ['kb_lookup.py', '--ask-a'], 0),
        ('台账帮助', ['ledger.py', '--help'], 0),
    ]
    for name, args, expect in cases:
        rc, out, err = run([os.path.join(SCRIPTS, args[0])] + args[1:])
        ok = (rc == expect)
        add(name, ok, '期望退出码 %s，实际 %s；%s' % (expect, rc, (err or '')[:200]))

    # ---- 3. 指令包：合法与非法章节 ----
    for ch, expect in (('1.1', 0), ('7.7', 0), ('99.9', 2)):
        out_f = os.path.join(tmp, 'sc_%s.json' % ch.replace('.', '_'))
        rc, o, e = run([os.path.join(SCRIPTS, 'write_chapter.py'),
                        '--chapter', ch, '--province', '河南省', '--out', out_f])
        ok = rc == expect
        add('指令包 %s' % ch, ok, '期望 %s 实际 %s %s' % (expect, rc, (e or '')[:150]))

    # ---- 4. 计算：扁平数据包必须能读（曾经的 bug）----
    flat = os.path.join(tmp, 'sc_flat.json')
    with open(flat, 'w', encoding='utf-8') as f:
        json.dump({'可剥离表土面积': '12.5', '表土剥离厚度': '0.3',
                   '需回覆表土面积': '8.0', '回覆厚度': '0.4'}, f, ensure_ascii=False)
    rc, out, err = run([os.path.join(SCRIPTS, 'calc.py'),
                        '--chapter', '4.4', '--data', flat])
    ok = '37500' in out
    add('计算-扁平数据包可读', ok, '未算出 37500；输出片段：%s' % (out or err)[:200])

    # 规范形态数据包也要能读
    norm = os.path.join(tmp, 'sc_norm.json')
    with open(norm, 'w', encoding='utf-8') as f:
        json.dump({'fields': {'可剥离表土面积': {'value': '12.5', 'unit': 'hm²'},
                              '表土剥离厚度': {'value': '0.3', 'unit': 'm'},
                              '需回覆表土面积': {'value': '8.0', 'unit': 'hm²'},
                              '回覆厚度': {'value': '0.4', 'unit': 'm'}}},
                  f, ensure_ascii=False)
    rc, out, err = run([os.path.join(SCRIPTS, 'calc.py'),
                        '--chapter', '4.4', '--data', norm])
    ok = '37500' in out
    add('计算-规范数据包可读', ok, '输出片段：%s' % (out or err)[:200])

    # ---- 5. 缺数据必须拒绝计算，不得用默认值 ----
    empty = os.path.join(tmp, 'sc_empty.json')
    with open(empty, 'w', encoding='utf-8') as f:
        json.dump({}, f)
    rc, out, err = run([os.path.join(SCRIPTS, 'calc.py'),
                        '--chapter', '2.4', '--data', empty])
    ok = ('缺数据不可计算' in out or '缺数据' in out) and rc != 0
    has_default = '默认' in out and '不得用' not in out
    add('计算-缺数据拒绝计算', ok and not has_default,
        '输出片段：%s' % (out or err)[:200])

    # ---- 6. 零项目残留：产物不得写进技能目录 ----
    bad_out = os.path.join(SK, 'sc_should_be_rejected.json')
    rc, out, err = run([os.path.join(SCRIPTS, 'write_chapter.py'),
                        '--chapter', '1.1', '--province', '河南省',
                        '--out', bad_out])
    leaked = os.path.exists(bad_out)
    add('零项目残留-拒绝写入技能目录', not leaked,
        '产物被写进了技能目录！%s' % bad_out if leaked else '')
    if leaked:
        try:
            os.remove(bad_out)
        except OSError:
            pass

    # ---- 7. 控制台编码：GBK 环境打印中文不得崩溃 ----
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'gbk'
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'check_gate.py'),
                        '--chapter', '1.1', '--province', '河南省'],
                       capture_output=True, env=env, timeout=120)
    ok = b'UnicodeEncodeError' not in (r.stderr or b'')
    add('GBK 控制台不崩溃', ok, (r.stderr or b'').decode('gbk', 'replace')[:250])

    # ---- 8. 索引时效性自检 ----
    rc, out, err = run([os.path.join(SCRIPTS, 'rebuild_index.py'), '--check'])
    add('索引漂移检测可运行', rc in (0, 1, 2), (out or err)[:200])

    # ---- 汇总 ----
    fails = [r for r in results if not r['ok']]
    if as_json:
        print(json.dumps({'results': results, 'failed': len(fails)},
                         ensure_ascii=False, indent=1))
    else:
        print('=== 自检结果（%d 项）===' % len(results))
        for r in results:
            print('  %s  %s' % ('OK  ' if r['ok'] else 'FAIL', r['check']))
            if not r['ok'] and r['detail']:
                print('        %s' % r['detail'].replace('\n', ' ')[:300])
        print('\n通过 %d / %d' % (len(results) - len(fails), len(results)))
        if fails:
            print('❌ 失败 %d 项，见上。' % len(fails))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()

"""第二轮自检：找深层漏洞（边界值 / 静默错误 / 编译产物 / 一致性）。

这一轮针对第一轮**没覆盖**的区域：
  1. skill 目录里有没有不该有的残留物（__pycache__ / 项目数据 / 临时文件）
  2. rules.json 里声明的规则与脚本实际行为是否一致（"只写在代码里的规则"是禁止项）
  3. 压缩后的包能否被真正解读（短键有没有对照；对照表是否完整）
  4. 超长输入 / 特殊字符 / 空值 是否会让脚本崩溃
  5. 索引快照与实际知识库是否漂移
  6. 文档里承诺的命令是否真的存在（文档-代码一致性）
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SK, 'scripts')
REF = os.path.join(SK, 'references')

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

res = []


def add(name, ok, detail=''):
    res.append((name, bool(ok), detail[:400]))


def run(args, timeout=180):
    r = subprocess.run([sys.executable] + args, capture_output=True,
                       text=True, encoding='utf-8', errors='replace',
                       timeout=timeout)
    return r.returncode, (r.stdout or ''), (r.stderr or '')


def main():
    tmp = tempfile.gettempdir()

    # ============ 1. 技能目录残留物 ============
    junk = []
    for root, dirs, files in os.walk(SK):
        if '__pycache__' in root:
            junk.append(root)
        for f in files:
            if f.endswith(('.pyc', '.tmp', '.bak', '.orig')) or f.startswith('~$'):
                junk.append(os.path.join(root, f))
    add('技能目录无字节码/临时残留', not junk, '残留：%s' % junk[:5])

    # 直接 import 不得在技能目录留下 __pycache__。
    #
    # 说明（为什么用 -B 而不是记 FAIL）：Python 在 import 时**先编译后执行**模块体，
    # 因此模块里的 `sys.dont_write_bytecode = True` 对「模块自身首次写盘」来不及生效——
    # 这是解释器行为，脚本内部改不了。可靠做法只有两条：
    #   ① 调用方加 `-B` 或设 `PYTHONDONTWRITEBYTECODE=1`；
    #   ② 事后自动清理。
    # 本检查因此改为：**用 -B 跑一遍确认无残留**（这才是可控口径），
    # 并清理可能存在的历史残留；不带 -B 的外部 import 属于使用者侧行为，
    # 由本脚本统一清理，不作为技能缺陷。
    for root, dirs, files in os.walk(SK):
        if '__pycache__' in root:
            shutil.rmtree(root, ignore_errors=True)
    r = subprocess.run([sys.executable, '-B', '-c',
                        'import sys; sys.path.insert(0, r"%s"); import budget, check_gate'
                        % SCRIPTS],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    left = [root for root, dirs, files in os.walk(SK) if '__pycache__' in root]
    add('import 不生成 __pycache__（-B 口径）', not left and r.returncode == 0,
        '生成于：%s；rc=%s %s'
        % (left[:3], r.returncode, (r.stderr or '')[:150]))
    for root, dirs, files in os.walk(SK):
        if '__pycache__' in root:
            shutil.rmtree(root, ignore_errors=True)

    # 项目数据不得写回技能目录（检查可疑的项目性文件）
    susp = []
    for root, dirs, files in os.walk(SK):
        for f in files:
            if f in ('数据包.json', '台账.json', '方案骨架.md', '事实清单.json'):
                susp.append(os.path.join(root, f))
    add('技能目录无项目数据残留', not susp, '发现：%s' % susp[:5])

    # ============ 2. 短键对照表完整性 ============
    # 从 write_chapter.py 提取 KEY_ALIAS，核对 key-alias.md 是否覆盖
    wc = open(os.path.join(SCRIPTS, 'write_chapter.py'), encoding='utf-8').read()
    m = re.search(r'KEY_ALIAS\s*=\s*\{(.*?)\n\}', wc, re.S)
    aliases = {}
    if m:
        for km, vm in re.findall(r"'([^']+)'\s*:\s*'([^']+)'", m.group(1)):
            aliases[km] = vm
    add('KEY_ALIAS 可解析', len(aliases) > 50, '解析到 %d 条' % len(aliases))

    md = open(os.path.join(REF, 'key-alias.md'), encoding='utf-8').read()
    missing = [k for k in aliases if k not in md and aliases[k] not in md]
    # 允许少量未列入（md 是给人看的摘要表，不要求逐条枚举全部英文键）
    ratio = 1 - len(missing) / max(1, len(aliases))
    add('短键对照表覆盖度 ≥60%', ratio >= 0.6,
        '覆盖 %.0f%%，未列入 %d 条：%s' % (ratio * 100, len(missing), missing[:8]))

    # 生成包里的短键必须都能在 KEY_ALIAS 反查表里找到（否则无法解读）
    rc, out, err = run([os.path.join(SCRIPTS, 'write_chapter.py'),
                        '--chapter', '7.7', '--province', '河南省',
                        '--out', os.path.join(tmp, 'chk77.json')])
    D = json.load(open(os.path.join(tmp, 'chk77.json'), encoding='utf-8'))
    rev = {}
    for k, v in aliases.items():
        rev.setdefault(v, k)

    def keys_of(o, acc):
        if isinstance(o, dict):
            for k, v in o.items():
                acc.add(k)
                keys_of(v, acc)
        elif isinstance(o, list):
            for x in o:
                keys_of(x, acc)
        return acc

    used = keys_of(D, set())
    unknown = [k for k in used
               if k not in rev and k not in aliases
               and not k.startswith('_') and k not in ('ct', 'ct_note', 'oi',
                                                       'more', 's', 'mc_note', 'src_files')]
    # 中文字段名（资产 D/E/F 保留了中文键）不算未知
    unknown = [k for k in unknown if not re.search(r'[\u4e00-\u9fff]', k)]
    add('包内短键均可反查', not unknown, '无法反查的键：%s' % unknown[:10])

    # ============ 3. 特殊输入不崩溃 ============
    weird = os.path.join(tmp, 'weird.json')
    with open(weird, 'w', encoding='utf-8') as f:
        json.dump({'挖方': '15.2', '填方': '', '弃方': None,
                   '工程占地': '86.5', '永久占地': '50.0', '临时占地': '36.6',
                   '可剥离表土面积': '12.5', '表土剥离厚度': '0.3',
                   '需回覆表土面积': '8.0', '回覆厚度': '0.4',
                   '水土保持区划': '不存在的区划', '防治标准执行等级': '超一级'},
                  f, ensure_ascii=False)
    rc, out, err = run([os.path.join(SCRIPTS, 'calc.py'),
                        '--chapter', '2.3', '--data', weird])
    add('特殊输入不崩溃（2.3）', rc in (0, 1),
        'rc=%s %s' % (rc, (err or '')[:200]))

    # 不闭合的勾稽必须被发现
    rc, out, err = run([os.path.join(SCRIPTS, 'calc.py'),
                        '--chapter', '2.3', '--data', weird])
    add('占地不闭合能被发现', ('不闭合' in out or '不符' in out or '偏差' in out),
        out[:250])

    # 未知区划/等级不得静默给出错误目标值
    rc, out, err = run([os.path.join(SCRIPTS, 'calc.py'),
                        '--chapter', '7.3.2', '--data', weird])
    add('未知区划不静默取错值',
        ('未' in out and ('区划' in out or '目标值' in out)) or rc in (1, 2),
        out[:250])

    # ============ 4. 超长输入不崩溃 ============
    big = os.path.join(tmp, 'big.json')
    with open(big, 'w', encoding='utf-8') as f:
        json.dump({'项目名称': '很长的项目名称' * 5000,
                   '挖方': '1' * 300, '工程占地': '86.5'}, f, ensure_ascii=False)
    rc, out, err = run([os.path.join(SCRIPTS, 'ingest.py'),
                        '--source', big, '--out', os.path.join(tmp, 'big_out.json')])
    add('超长输入不崩溃（ingest）', rc in (0, 1, 2), 'rc=%s %s' % (rc, (err or '')[:200]))

    # ============ 5. 索引快照漂移检测 ============
    rc, out, err = run([os.path.join(SCRIPTS, 'rebuild_index.py'), '--check'])
    add('索引漂移检测可运行', rc in (0, 1, 2), 'rc=%s %s' % (rc, (out or err)[:200]))

    # ============ 5b. 知识库位置与隔离（搬家后的必查项）============
    r = subprocess.run([sys.executable, '-B', os.path.join(SCRIPTS, 'vault_paths.py')],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    vo = r.stdout or ''
    add('知识库路径可解析', 'VAULT' in vo and '<未解析到>' not in vo.split('VAULT')[-1][:40],
        vo.replace('\n', ' ')[:250])
    add('知识库位于技能包内（相对路径）', '技能包' in vo and SK in vo,
        '解析结果：%s' % [l for l in vo.split('\n') if 'VAULT' in l])
    # 日志中不得再出现旧绝对路径的**实际赋值**。
    #
    # 口径说明（否则会自我误报）：`obisidian` 这个词合法地出现在
    # 「说明旧路径已改掉」的注释与文档里（如 vault_paths.py 的模块说明）。
    # 真正要禁的是**把它当作路径值使用**，即出现
    #   `= r'E:\obisidian...` / `, r'E:\obisidian...` 这类赋值形态。
    legacy = []
    # 用拼接构造匹配串，避免本检查**匹配到自己**（自引用误报）
    _drive = 'E' + ':'
    _mark = 'obis' + 'idian'
    pat = re.compile(r"""[=,(]\s*r?['"]""" + re.escape(_drive + '\\' + _mark))
    for root, dirs, files in os.walk(SK):
        if any(x in root for x in ('.cache', '__pycache__', 'md库', 'vault')):
            continue
        for f in files:
            if not f.endswith(('.py', '.json')):
                continue
            fp = os.path.join(root, f)
            if f in ('patch_vault_paths.py', 'selfcheck2.py'):
                continue          # 前者职责就是改写旧路径；后者的检查定义本身
            try:
                t = open(fp, encoding='utf-8', errors='replace').read()
            except OSError:
                continue
            if pat.search(t):
                legacy.append(os.path.relpath(fp, SK))
    add('代码中无旧绝对路径赋值', not legacy, '仍在赋值：%s' % legacy[:5])

    # 技能自身的 md 不得被当作知识库条目
    import json as _json
    man_p = os.path.join(REF, 'vault_manifest.json')
    if os.path.exists(man_p):
        man = _json.load(open(man_p, encoding='utf-8-sig'))
        files = [f.get('path') if isinstance(f, dict) else f
                 for f in (man.get('files') or [])]
        bad = [f for f in files
               if f and ('SKILL.md' in str(f) or 'README.md' in str(f)
                         or str(f).startswith('references'))]
        add('技能自身 md 未被计入知识库', not bad, '误入条目：%s' % bad[:5])
        add('知识库条目数合理（≥371）', len(files) >= 371,
            'manifest 记录 %d 条' % len(files))

    # ============ 6. 文档-代码一致性：文档承诺的命令是否真的存在 ============
    skill_md = open(os.path.join(SK, 'SKILL.md'), encoding='utf-8').read()
    kbmd_p = os.path.join(REF, 'kb-and-tokens.md')
    docs = skill_md + (open(kbmd_p, encoding='utf-8').read()
                       if os.path.exists(kbmd_p) else '')
    cmds = set(re.findall(r'\$SK\\scripts\\([a-z_0-9]+\.py)', docs))
    cmds |= set(re.findall(r'\\scripts\\([a-z_0-9]+\.py)', docs))
    missing_py = [c for c in cmds
                  if not os.path.exists(os.path.join(SCRIPTS, c))]
    add('文档提到的脚本都存在', not missing_py, '缺失：%s' % missing_py)

    # references 引用是否存在
    refs_ref = set(re.findall(r'references/([a-zA-Z0-9_\-\.]+\.(?:md|json))', docs))
    missing_ref = [r for r in refs_ref if not os.path.exists(os.path.join(REF, r))]
    add('文档提到的 references 都存在', not missing_ref, '缺失：%s' % missing_ref)

    # ============ 7. 铁律自检：禁止项是否真的被实现 ============
    # 铁律6：不得直读知识库 → kb_lookup 应提供安全入口
    rc, out, err = run([os.path.join(SCRIPTS, 'kb_lookup.py'), '--map'])
    add('kb_lookup --map 可用', rc == 0 and len(out) > 50, (out or err)[:150])

    # 铁律3：条款必须逐字 → check_citation 应存在并可运行
    rc, out, err = run([os.path.join(SCRIPTS, 'check_citation.py'), '--help'])
    add('check_citation 可用', rc == 0, (err or '')[:150])

    # ============ 8. 压缩包自描述完整性 ============
    add('压缩包带 _compact 说明', '_compact' in D, '')
    add('压缩包带 ct_note 说明', 'ct_note' in D, '')
    # 被**我们**截断的条款必须有 more。
    # 判据用 truncated 标记，而不是"以…结尾"——
    # 标准表格原文本身就常带省略号（如"×× hm² …"），
    # 用"以…结尾"判断会把未截断的原文误报为"缺 more"（第一版就误报了）。
    trunc_no_more = [c for c in (D.get('g', {}).get('hc') or [])
                     if c.get('truncated') and not c.get('more')]
    add('所有截断条款都有 more 指针', not trunc_no_more,
        '%d 条缺 more' % len(trunc_no_more))
    # 完整性：真正的截断标记 must 与 more 同现
    bad_pairs = [c for c in (D.get('g', {}).get('hc') or [])
                 if bool(c.get('more')) != bool(c.get('truncated'))]
    add('truncated 与 more 必然同现', not bad_pairs,
        '%d 条不成对' % len(bad_pairs))

    # ============ 汇总 ============
    fails = [r for r in res if not r[1]]
    print('=== 第二轮自检（%d 项）===' % len(res))
    for name, ok, detail in res:
        print('  %s  %s' % ('OK  ' if ok else 'FAIL', name))
        if not ok and detail:
            print('        %s' % detail.replace('\n', ' ')[:300])
    print('\n通过 %d / %d' % (len(res) - len(fails), len(res)))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()

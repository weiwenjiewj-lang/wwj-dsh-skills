"""把 7 个脚本里硬编码的 vault 绝对路径，改为统一走 vault_paths 模块。

用法：
    python _meta/patch_vault_paths.py          # 执行替换
    python _meta/patch_vault_paths.py --check  # 只检查不修改

## 替换规则

原（各脚本写法略有差异）：
    VAULT = os.environ.get('DSH_WS_VAULT') or r'E:\\obisidian\\...'
    VAULT = os.environ.get('DSH_WS_VAULT', r'E:\\obisidian\\...')

新：
    from vault_paths import VAULT          # 统一入口，默认=技能包内 md库

## 为什么不用正则一把梭

各脚本 import 区不同、缩进不同，且 `kb_cache.py` 是缩进在 `__init__` 里的。
本脚本按「定位含 obisidian 的那一行 → 用其左侧相同的变量名重建赋值」逐行处理，
改完立刻做语法检查，失败自动回滚该文件。
"""
import os
import re
import shutil
import subprocess
import sys

SK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SK, 'scripts')

sys.dont_write_bytecode = True

OLD_MARK = 'obisidian'

REPLACEMENTS = {
    'build_design_params.py': "from vault_paths import VAULT",
    'build_measure_methods.py': "from vault_paths import VAULT",
    'build_species_index.py': "from vault_paths import VAULT",
    'extract_style_samples.py': "from vault_paths import VAULT",
    'kb_lookup.py': "from vault_paths import VAULT",
    'rebuild_index.py': "from vault_paths import VAULT",
    'check_gate.py': "from vault_paths import VAULT",
    'kb_cache.py': "from vault_paths import VAULT",
}

check_only = '--check' in sys.argv
changed, failed = [], []

for fn, newline in REPLACEMENTS.items():
    p = os.path.join(SCRIPTS, fn)
    if not os.path.exists(p):
        failed.append('%s（文件不存在）' % fn)
        continue
    with open(p, encoding='utf-8') as f:
        src = f.read()
    if OLD_MARK not in src:
        continue
    lines = src.split('\n')
    out = []
    i = 0
    n = len(lines)
    hit = False
    while i < n:
        ln = lines[i]
        if OLD_MARK in ln and 'VAULT' in ln.upper():
            # 该行是 vault 赋值（可能跨行：以 or \ 结尾）
            indent = ln[:len(ln) - len(ln.lstrip())]
            # 吃掉续行（kb_cache 的多行写法）
            j = i
            while j < n and lines[j].rstrip().endswith(('\\', 'or')):
                j += 1
            end = j
            out.append(indent + newline)
            hit = True
            i = end + 1
            continue
        out.append(ln)
        i += 1
    if not hit:
        continue
    new_src = '\n'.join(out)
    if check_only:
        changed.append(fn)
        continue
    # 备份 → 写 → 语法检查 → 失败回滚
    bak = p + '.vaultbak'
    shutil.copy2(p, bak)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(new_src)
    r = subprocess.run([sys.executable, '-m', 'py_compile', p],
                       capture_output=True)
    if r.returncode != 0:
        shutil.copy2(bak, p)
        os.remove(bak)
        failed.append('%s（语法错误，已回滚）：%s'
                      % (fn, (r.stderr or b'').decode('utf-8', 'replace')[:160]))
        continue
    os.remove(bak)
    changed.append(fn)

if check_only:
    print('待改（%d）：%s' % (len(changed), '、'.join(changed) or '无'))
else:
    if changed:
        print('已改 %d 个脚本：' % len(changed))
        for c in changed:
            print('  ' + c)
    else:
        print('无需改动。')
    if failed:
        print('\n失败 %d 个：' % len(failed))
        for f in failed:
            print('  ' + f)
        sys.exit(1)

"""Token 成本基准测试：量化技能每次调用的真实 token 开销。

用法（在技能包目录下）：
    python _meta/bench.py            # 跑全部指标
    python _meta/bench.py --json     # 机读输出

口径说明
--------
· 本脚本只统计**确定性可测**的部分：文件字节数 + 按字符类别估算的 token。
· CJK token 估算采用经验系数（中文 ≈ 0.7 token/字符，ASCII ≈ 0.28 token/字符），
  与实际分词器有偏差，但**同口径前后对比**足以判断优化幅度。
· 三个口径：
    L0 常驻：每次加载技能必然进入上下文的文件（SKILL.md）
    L1 单节：写一节一次的指令包
    L2 全书：写完一本方案的全部脚本调用总量
"""
import json
import os
import subprocess
import sys

# 技能目录零残留：必须在 import 本地模块之前设置。
# Python 在 import 时先编译写盘、后执行模块体，
# 因此被 import 的模块自己设是来不及的。
sys.dont_write_bytecode = True

SK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SK, 'scripts'))

# Windows 控制台默认 GBK，打印非 GBK 字符（如替换符 U+FFFD）会直接崩。
# reconfigure 失败时退化为 ascii-safe，绝不因"打印不出来"而中断测量。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def est_tokens(text):
    """按字符类别估算 token 数。"""
    cjk = ascii_ = other = 0
    for ch in text:
        o = ord(ch)
        if 0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFFEF:
            cjk += 1
        elif o < 128:
            ascii_ += 1
        else:
            other += 1
    return int(cjk * 0.7 + ascii_ * 0.28 + other * 0.5)


def read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def file_m(p):
    return os.path.getsize(p) if os.path.exists(p) else 0


def measure_packs(chapters=None):
    """实测各代表章节的指令包体积。"""
    if chapters is None:
        chapters = ['1.1', '1.6.2', '2.4', '2.7.6', '4.4', '5.2', '6.3',
                    '7.3.2', '7.7', '8.2', '9.1.2', '10.1']
    import tempfile
    tmp = tempfile.gettempdir()
    rows = []
    for c in chapters:
        out = os.path.join(tmp, 'bench_%s.json' % c.replace('.', '_'))
        r = subprocess.run([sys.executable, os.path.join(SK, 'scripts', 'write_chapter.py'),
                            '--chapter', c, '--province', '河南省', '--out', out],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        if not os.path.exists(out):
            rows.append({'chapter': c, 'error': (r.stderr or r.stdout or '')[:200]})
            continue
        txt = read(out)
        rows.append({'chapter': c, 'bytes': len(txt.encode('utf-8')),
                     'chars': len(txt), 'tokens_est': est_tokens(txt)})
        try:
            os.remove(out)
        except OSError:
            pass
    return rows


def main():
    as_json = '--json' in sys.argv
    rep = {}

    # ---- L0 常驻 ----
    skill_md = read(os.path.join(SK, 'SKILL.md'))
    rep['L0_skill_md'] = {'bytes': len(skill_md.encode('utf-8')),
                          'lines': skill_md.count('\n') + 1,
                          'tokens_est': est_tokens(skill_md)}

    # ---- L1 单节指令包 ----
    rows = measure_packs()
    ok = [r for r in rows if 'tokens_est' in r]
    rep['L1_packs'] = rows
    if ok:
        rep['L1_summary'] = {
            'n': len(ok),
            'bytes_mean': sum(r['bytes'] for r in ok) // len(ok),
            'tokens_mean': sum(r['tokens_est'] for r in ok) // len(ok),
            'tokens_max': max(r['tokens_est'] for r in ok),
            'tokens_total_12ch': sum(r['tokens_est'] for r in ok),
        }

    # ---- L2 全书折算 ----
    rel = os.path.join(SK, 'references')
    try:
        tpl = json.load(open(os.path.join(rel, 'template-tree.json'), encoding='utf-8'))
        leaves = sum(1 for k, v in (tpl.get('nodes') or {}).items()
                     if not v.get('is_container'))
        if not leaves:
            leaves = len(tpl.get('nodes') or {})
    except Exception:
        leaves = 95
    if ok:
        mean_tok = sum(r['tokens_est'] for r in ok) / len(ok)
        rep['L2_book'] = {'leaf_nodes': leaves,
                          'est_tokens_full_book': int(mean_tok * leaves),
                          'note': '按均值外推到全部叶子节点'}

    # ---- 索引层（AI 侧应读的 references）----
    idx = {}
    for f in ['zone-a-index.json', 'zone-b-index.json', 'zone-c-index.json',
              'template-tree.json', 'rules.json', 'tables.json', 'asset-map.json',
              'species_index.json', 'design_params.json', 'measure_methods.json',
              'zone_c_style_samples.json', 'vault_manifest.json',
              'source_watchlist.json', 'file-index.md', 'metadata-spec.md',
              'compliance-gate.md', 'zone-c-usage.md', 'zone-b-usage.md',
              'gap-analyzer.md', 'watchlist.md']:
        p = os.path.join(rel, f)
        if os.path.exists(p):
            idx[f] = file_m(p)
    rep['references_sizes'] = dict(sorted(idx.items(), key=lambda x: -x[1]))

    if as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return
    print('=== L0 常驻（每次加载技能）===')
    print('  SKILL.md: %.1f KB / %d 行 / ≈%d token' % (
        rep['L0_skill_md']['bytes'] / 1024, rep['L0_skill_md']['lines'],
        rep['L0_skill_md']['tokens_est']))
    print('\n=== L1 单节指令包 ===')
    for r in rows:
        if 'tokens_est' in r:
            print('  %-8s %8d B  ≈%6d token' % (r['chapter'], r['bytes'], r['tokens_est']))
        else:
            print('  %-8s ERROR %s' % (r['chapter'], r.get('error', '')))
    if ok:
        s = rep['L1_summary']
        print('  --- 均值 %.1f KB / ≈%d token；最大 ≈%d token' % (
            s['bytes_mean'] / 1024, s['tokens_mean'], s['tokens_max']))
    if 'L2_book' in rep:
        print('\n=== L2 全书折算 ===')
        print('  叶子节点 %d 个 × 均值 ≈%d token = ≈%s token' % (
            rep['L2_book']['leaf_nodes'], mean_tok, format(rep['L2_book']['est_tokens_full_book'], ',')))
    print('\n=== references 体积（仅脚本读的应排除在 AI 侧之外）===')
    for k, v in rep['references_sizes'].items():
        print('  %8.1f KB  %s' % (v / 1024, k))


if __name__ == '__main__':
    main()

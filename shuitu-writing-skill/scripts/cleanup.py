#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cleanup.py —— 收尾清理：跑完只留 Word 交付件，过程产物一律清除

设计原则（安全第一）
--------------------
1. **默认干跑**：不加 `--apply` 只列清单，不动任何文件。
2. **白名单删除**：只删除**命中过程产物命名模式**的文件；不认识的文件一律不动。
3. **保护交付件**：`--keep` 指定的文件（以及工作区里带日期的 .docx）永不删除。
4. **保护原始资料**：`开发方案/ 参考方案/ 方案配图/ gis数据/ CAD工程方案/ 水土方案/*.docx`
   等**用户自有资料**永不删除。
5. **拒绝越界**：技能包目录与知识库目录内一律拒绝执行。
6. **先小样本后批量**：先 `--dry-run` 看清单，确认后再 `--apply`。

用法
----
    # 1) 干跑，看会删什么
    python cleanup.py --dir . --keep "水土方案/2026.09.16XXX方案报告书.docx"

    # 2) 确认后执行
    python cleanup.py --dir . --keep "..." --apply

    # 3) 机读
    python cleanup.py --dir . --json-only

退出码：0 已清理/无需清理 · 1 有需人工确认项（未加 --apply） · 2 输入/用法错误
"""

import sys
sys.dont_write_bytecode = True

import argparse
import json
import os
import re
import shutil

# ---------------------------------------------------------------- 过程产物命名模式
# 只删**命中**这些模式的文件；不命中的一律保留。
PATTERNS = [
    r'^数据包.*\.json$',
    r'^台账.*\.json$',
    r'^方案骨架.*\.md$',
    r'^第?\d*[一二三四五六七八九十]*章骨架.*\.md$',
    r'^内容点全集.*\.md$',
    r'^抽取任务书.*\.md$',
    r'^事实清单.*\.(md|json)$',
    r'^指令包.*\.json$',
    r'^技能生成_.*\.md$',
    r'^计算书.*\.(md|json)$',
    r'^终检报告.*\.(md|json)$',
    r'^净化报告.*\.md$',
    r'^分析报告.*\.md$',
    r'^缺口报告.*\.md$',
    r'^_calc.*\.json$',
    r'^_[a-z0-9]{1,6}\.json$',
    r'^_试跑.*$',
    r'^_root\.txt$',
    r'^_mine\.txt$',
    r'^docx体检\.txt$',
    r'^测试_.*\.docx$',
    r'^audit_docx\.py$',
    r'^scan_.*\.py$',
    r'^build_docx\.py$',
    r'^__pycache__$',
]
PATTERNS = [re.compile(p) for p in PATTERNS]

# 过程产物目录名（整目录删除）
DIR_NAMES = {'__pycache__', '指令包', '.pytest_cache'}

# 永不删除：用户自有资料目录 / 交付件特征
PROTECT_DIRS = {'开发方案', '参考方案', '方案配图', 'gis数据', 'CAD工程方案',
                '知识库元数据', 'vault', '.git'}
PROTECT_EXT = {'.shp', '.shx', '.dbf', '.prj', '.dwg', '.dxf', '.pdf',
               '.xlsx', '.xls', '.dwl', '.dwl2', '.bak'}
# 交付件：带日期前缀的 docx
RE_DELIVERABLE = re.compile(r'^\d{4}\.\d{2}\.\d{2}.*\.docx$')


def is_deliverable(name):
    return bool(RE_DELIVERABLE.match(name))


def matches(name):
    return any(p.match(name) for p in PATTERNS)


def scan(root, keep):
    """返回 (to_delete_files, to_delete_dirs, kept_protected, skipped)"""
    dels, deldirs, protected, skipped = [], [], [], []
    keep_abs = {os.path.abspath(k) for k in keep}

    for dirpath, dirnames, filenames in os.walk(root):
        # 受保护目录：不进入
        base = os.path.basename(dirpath)
        if base in PROTECT_DIRS and os.path.abspath(dirpath) != os.path.abspath(root):
            protected.append(dirpath + os.sep + '  (整目录保留)')
            dirnames[:] = []
            continue

        for d in list(dirnames):
            full = os.path.join(dirpath, d)
            if d in DIR_NAMES:
                deldirs.append(full)
                dirnames.remove(d)

        for f in filenames:
            full = os.path.join(dirpath, f)
            if os.path.abspath(full) in keep_abs:
                protected.append(full + '  (--keep 指定)')
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext in PROTECT_EXT:
                protected.append(full)
                continue
            if is_deliverable(f):
                protected.append(full + '  (交付件)')
                continue
            if matches(f):
                dels.append(full)
            else:
                skipped.append(full)
    return dels, deldirs, protected, skipped


def main():
    ap = argparse.ArgumentParser(description='收尾清理：只留 Word 交付件')
    ap.add_argument('--dir', default='.', help='工作区目录（默认当前目录）')
    ap.add_argument('--keep', action='append', default=[],
                    help='必须保留的文件（可多次指定），通常是最终 Word 交付件')
    ap.add_argument('--apply', action='store_true', help='真正执行删除（默认干跑）')
    ap.add_argument('--json-only', action='store_true')
    args = ap.parse_args()

    root = os.path.abspath(args.dir)
    if not os.path.isdir(root):
        print('输入错误：目录不存在 %s' % root, file=sys.stderr)
        return 2
    if _inside_skill_or_vault(root):
        print('拒绝执行：目标目录位于技能包或知识库内（铁律5 零项目残留）：%s' % root, file=sys.stderr)
        return 2

    keep = list(args.keep)
    for k in list(keep):
        if not os.path.isabs(k):
            keep[keep.index(k)] = os.path.join(root, k)

    dels, deldirs, protected, skipped = scan(root, keep)

    removed, failed = [], []
    if args.apply:
        for f in dels:
            try:
                os.remove(f)
                removed.append(f)
            except OSError as e:
                failed.append('%s : %s' % (f, e))
        for d in deldirs:
            try:
                shutil.rmtree(d)
                removed.append(d + os.sep)
            except OSError as e:
                failed.append('%s : %s' % (d, e))

    result = {
        'root': root,
        'apply': args.apply,
        'to_delete': len(dels) + len(deldirs),
        'deleted': len(removed),
        'failed': failed,
        'files': dels,
        'dirs': deldirs,
        'kept_protected_count': len(protected),
        'skipped_count': len(skipped),
        'kept': keep,
    }

    sys.stdout.reconfigure(encoding='utf-8')
    if args.json_only:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        mode = '已执行删除' if args.apply else '干跑（未删除任何文件）'
        print('收尾清理 —— %s' % mode)
        print('  工作区 : %s' % root)
        print('  待删文件 %d 个 ; 待删目录 %d 个' % (len(dels), len(deldirs)))
        for f in dels[:40]:
            print('     - %s' % os.path.relpath(f, root))
        if len(dels) > 40:
            print('     …（其余 %d 个见 --json-only）' % (len(dels) - 40))
        for d in deldirs:
            print('     - %s%s' % (os.path.relpath(d, root), os.sep))
        print('  保留（交付件/原始资料/白名单）: %d 项' % len(protected))
        for p in sorted(protected)[:15]:
            print('     ✓ %s' % (os.path.relpath(p, root) if os.path.isabs(p) and root in p else p))
        if len(protected) > 15:
            print('     …（其余 %d 项）' % (len(protected) - 15))
        if failed:
            print('  删除失败 %d 项：' % len(failed))
            for f in failed[:10]:
                print('     ! %s' % f)
        if not args.apply and (dels or deldirs):
            print()
            print('  以上为干跑结果。确认无误后加 --apply 执行。')

    if failed:
        return 1
    if not args.apply and (dels or deldirs):
        return 1
    return 0


def _inside_skill_or_vault(path):
    try:
        ap = os.path.abspath(path).lower()
    except Exception:
        return True
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__))).lower()
    if ap.startswith(here):
        return True
    for root in (os.environ.get('DSH_WS_VAULT', ''),):
        if root and ap.startswith(os.path.abspath(root).lower()):
            return True
    if os.sep + 'vault' + os.sep in ap or ap.endswith(os.sep + 'vault'):
        return True
    return False


if __name__ == '__main__':
    sys.exit(main())

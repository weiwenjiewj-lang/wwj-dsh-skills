#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 Obsidian 知识库重建索引快照，并检测漂移与过期（D8）。

用法:
    python rebuild_index.py --check      只比对：库内文件清单 vs 快照记录（默认）
    python rebuild_index.py --rebuild    读取 frontmatter，重建 zone-a/b-index.json 与清单
    python rebuild_index.py --selfcheck  校验索引覆盖库内全部 md 文件

设计要点:
  · 运行时优先实时读库；本脚本产出的 references/*.json 只是离线兜底快照
  · 快照超过 MAX_AGE_DAYS 天，或文件清单不一致 → 必须重建后才能写作（绝不静默用旧索引）
  · 只依赖标准库；frontmatter 解析为自实现（字段形态由 metadata-spec.md 固定）
"""
import argparse, datetime, json, os, re, sys

import sys
sys.dont_write_bytecode = True   # 技能包不留 __pycache__（避免缓存掩盖规则改动）

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REF = os.path.join(SKILL, 'references')
MANIFEST = os.path.join(REF, 'vault_manifest.json')
from vault_paths import VAULT
# 快照阈值只读 rules.json snapshot_gate.max_age_days（原来两处各写一份 30）
_RULES_PATH = os.path.join(REF, 'rules.json')
try:
    _RULES = json.load(open(_RULES_PATH, encoding='utf-8-sig'))
except Exception:
    _RULES = {}
MAX_AGE_DAYS = int((_RULES.get('snapshot_gate') or {}).get('max_age_days', 30))

CLASS_MAP = {
    '法律': 'law', '法律修改决定': 'law', '行政法规': 'regulation', '部门规章': 'regulation',
    '地方性法规': 'regulation', '规范性文件': 'normative_doc',
    '规范性文件（流域管理机构）': 'normative_doc', '规划文件': 'normative_doc',
    '规划文件（市级）': 'normative_doc', '编制模板': 'normative_doc',
    '编制模板（附件）': 'normative_doc', '技术指南': 'normative_doc',
    '技术文件（编制提纲）': 'normative_doc', '地方规范性文件': 'normative_doc',
    '地方规范性文件（政策解读）': 'normative_doc', '国家标准': 'national_standard',
    '行业标准': 'industry_standard', '专著': 'monograph', '技术手册': 'monograph',
    '期刊论文': 'paper', '学位论文': 'paper', '团体标准（编制说明）': 'other',
    '规划报告（县级，含具体行政区资料）': 'other', '库索引（元数据基础设施）': 'other',
    '应用范例（已批准水土保持方案）': 'applied_example',
}

# 基础设施文档类型：库总目录、索引卡等。
# 它们**不是可引用的知识素材**，不应进入任何层级索引——
# 实测缺陷：原 `else` 分支把非 A/C 文件一律归入 Zone B，
# 使「00_总目录.md」被当成 Zone B 参考文献（索引 116 > 磁盘 115）。
INFRA_DOC_TYPES = (
    '库索引（元数据基础设施）',
    '库索引',
    '索引卡',
    '总目录',
)
# ---------- frontmatter 解析（不依赖第三方库） ----------
def parse_frontmatter(path):
    # 读足量字节：原为 read(8000)，凡前言超过 8 KB 的文件都会读不到结束符
    # 而被静默判为「缺 frontmatter」并掉出索引（实测已发生 1 例：前言 9297 字符）。
    txt = open(path, encoding='utf-8', errors='ignore').read(65536)
    if not txt.startswith('---'):
        return None
    end = txt.find('\n---', 3)
    if end < 0:
        return None
    block = txt[3:end]
    out = {}
    for line in block.splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        m = re.match(r'^([A-Za-z_][\w\-]*):\s*(.*)$', line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if v.startswith('{') and v.endswith('}'):
            # 对象型字段（如 Zone C 的 topic_weight）必须按 JSON 解析，
            # 否则下游拿到字符串，调用 .get() 会 AttributeError
            try:
                out[k] = json.loads(v)
            except Exception:
                out[k] = v
        elif v.startswith('[') and v.endswith(']'):
            items = [x.strip().strip('"').strip("'") for x in v[1:-1].split(',') if x.strip()]
            out[k] = items
        else:
            out[k] = unescape_yaml(v)
    return out


def unescape_yaml(v):
    """处理双引号标量内的转义：\" -> " ，\\ -> \\ （与 frontmatter 生成端的转义对称）"""
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        body = v[1:-1]
        body = body.replace('\\\\', '\x00').replace('\\"', '"').replace('\x00', '\\\\')
        return body
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


def scan_vault(vault):
    items = []
    for root, _, fns in os.walk(vault):
        for fn in fns:
            if not fn.endswith('.md'):
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, vault)
            st = os.stat(p)
            fm = parse_frontmatter(p)
            items.append({
                'file': rel, 'size': st.st_size, 'mtime': int(st.st_mtime),
                'has_frontmatter': fm is not None, 'fm': fm or {},
            })
    items.sort(key=lambda x: x['file'])
    return items


def manifest_of(items, vault):
    return {'generated_at': datetime.date.today().isoformat(),
            'vault': vault, 'count': len(items),
            'files': {i['file']: {'size': i['size'], 'mtime': i['mtime']} for i in items}}


def age_days(man):
    try:
        d = datetime.date.fromisoformat(man['generated_at'])
    except Exception:
        return 10 ** 6
    return (datetime.date.today() - d).days


def cmd_check(items):
    if not os.path.exists(MANIFEST):
        print('⚠ 无快照清单（%s 不存在）→ 必须执行 --rebuild' % os.path.basename(MANIFEST))
        return False
    man = json.load(open(MANIFEST, encoding='utf-8-sig'))
    cur = {i['file']: {'size': i['size'], 'mtime': i['mtime']} for i in items}
    old = man.get('files', {})
    added = sorted(set(cur) - set(old))
    removed = sorted(set(old) - set(cur))
    changed = sorted(k for k in set(cur) & set(old)
                     if cur[k]['size'] != old[k]['size'] or cur[k]['mtime'] != old[k]['mtime'])
    a = age_days(man)
    print('=== 快照漂移检查 ===')
    print('快照生成日: %s（%d 天前，阈值 %d 天）' % (man.get('generated_at'), a, MAX_AGE_DAYS))
    print('库内文件: %d；快照记录: %d' % (len(cur), len(old)))
    print('新增 %d；删除 %d；修改 %d' % (len(added), len(removed), len(changed)))
    for k in added[:10]:
        print('   + %s' % k)
    for k in removed[:10]:
        print('   - %s' % k)
    for k in changed[:10]:
        print('   ~ %s' % k)
    stale = a > MAX_AGE_DAYS or added or removed or changed
    print()
    if stale:
        print('⚠ 结论：索引快照已过期或与知识库不一致 → 必须重建后才能写作')
        print('  执行: python scripts/rebuild_index.py --rebuild')
        return False
    print('✅ 结论：快照与知识库一致且未过期')
    return True


def cmd_rebuild(items, vault):
    no_fm = [i['file'] for i in items if not i['has_frontmatter']]
    if no_fm:
        print('⚠ %d 个文件缺 frontmatter，将跳过（按 metadata-spec.md 补齐后重建）：' % len(no_fm))
        for f in no_fm[:10]:
            print('   %s' % f)
    A, B, C = [], [], []
    skipped = []          # 基础设施文件（库总目录等），不计入任何层
    for i in items:
        fm = i['fm']
        if not fm:
            continue
        zone = fm.get('zone', '')
        dt = fm.get('doc_type', '')
        e = {
            'file': i['file'],
            'zone': zone,
            'title': fm.get('title') or os.path.splitext(os.path.basename(i['file']))[0],
            'doc_type': dt,
            'doc_type_class': fm.get('doc_type_class') or CLASS_MAP.get(dt, 'other'),
            'doc_number': fm.get('doc_number', '待确认'),
            'issuing_body': fm.get('issuing_body', '待确认'),
            'publish_date': fm.get('publish_date', '待确认'),
            'effective_date': fm.get('effective_date', '待确认'),
            'status': fm.get('status', '待确认'),
            'superseded_by': fm.get('superseded_by', '待确认'),
            'supersedes': fm.get('supersedes', '待确认'),
            'scope': fm.get('scope', '不适用'),
            'chapter_relevance': fm.get('chapter_relevance', []),
            'source_url': fm.get('source_url', '待确认'),
            'usage_label': fm.get('compliance_relevance', '待确认'),
            'compliance_relevance': fm.get('compliance_relevance', '待确认'),
            # 文体参考标记（与 compliance_relevance 正交）：
            #   compliance_relevance 回答"能否用于合规判断"
            #   style_role          回答"能否用于参照正文写法"
            # 不写入索引会让 zone_b_injection.style_role 的排序与保底槽失效。
            'style_role': fm.get('style_role', '未标注'),
            'style_role_reason': fm.get('style_role_reason', ''),
        }
        if zone == 'C':
            # Zone C（应用范例层）专属字段。topic_tags 是唯一合法挂载维度：
            # 范例多为老八章结构，章节号与新模板同号不同义，禁止按 chapter_relevance 挂载。
            e.update({
                'project_type': fm.get('project_type', '待确认'),
                'project_name': fm.get('project_name', '待确认'),
                'approval_level': fm.get('approval_level', '待确认'),
                'approval_year': fm.get('approval_year', '待确认'),
                'approval_period': fm.get('approval_period', '待确认'),
                'approval_basis_list': fm.get('approval_basis_list', []),
                'standard_system_at_approval': fm.get('standard_system_at_approval', '待确认'),
                'superseded_basis': fm.get('superseded_basis', []),
                'source_chapter_system': fm.get('source_chapter_system', '待确认'),
                'chapter_mapping': fm.get('chapter_mapping', 'pending'),
                'topic_tags': fm.get('topic_tags', []),
                'topic_weight': fm.get('topic_weight', {}),
                'structure_summary': fm.get('structure_summary', ''),
                'usable_for': fm.get('usable_for', []),
                'not_usable_for': fm.get('not_usable_for', []),
                'local_file': fm.get('local_file', '待确认'),
            })
        if zone == 'A':
            A.append(e)
        elif zone == 'C':
            C.append(e)
        elif (e.get('doc_type') or '') in INFRA_DOC_TYPES or \
                os.path.basename(i['file']).startswith('00_'):
            # 基础设施文件（库总目录、索引卡）**不计入任何层**。
            # 实测缺陷：原 `else` 分支把非 A/C 的文件一律塞进 Zone B，
            # 导致 `md库\00_总目录.md`（库总目录）被当成 Zone B 参考文献，
            # 使 Zone B 索引 116 条 > 磁盘 115 个文件。
            # 这类文件是元数据基础设施，不是可引用的参考素材。
            skipped.append(e.get('file'))
        else:
            B.append(e)

    def dump(entries, zone, path):
        json.dump({'zone': zone, 'generated_from': 'vault frontmatter（rebuild_index.py）',
                   'generated_at': datetime.date.today().isoformat(),
                   'count': len(entries), 'entries': entries},
                  open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    dump(A, 'A', os.path.join(REF, 'zone-a-index.json'))
    dump(B, 'B', os.path.join(REF, 'zone-b-index.json'))
    dump(C, 'C', os.path.join(REF, 'zone-c-index.json'))
    json.dump(manifest_of(items, vault), open(MANIFEST, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('=== 重建完成 ===')
    if skipped:
        print('跳过基础设施文件 : %d 个（库总目录等，不计入任何层）' % len(skipped))
        for s in skipped[:5]:
            print('   - %s' % s)
    print('Zone A（规范层）  : %d 条 -> zone-a-index.json' % len(A))
    print('Zone B（参考层）  : %d 条 -> zone-b-index.json' % len(B))
    print('Zone C（范例层）  : %d 条 -> zone-c-index.json' % len(C))
    print('清单: %d 条 -> vault_manifest.json（generated_at %s）'
          % (len(items), datetime.date.today().isoformat()))
    print()
    print('后续必做：重新生成缺口报告 → python scripts/analyze_gaps.py --out 缺口报告.md')
    return True


def cmd_selfcheck(items):
    ok = True
    md = set(i['file'] for i in items)
    union = set()
    for name in ('zone-a-index.json', 'zone-b-index.json', 'zone-c-index.json'):
        p = os.path.join(REF, name)
        if not os.path.exists(p):
            print('%s: 不存在（Zone C 尚未建立时可忽略）' % name)
            continue
        d = json.load(open(p, encoding='utf-8-sig'))
        idx = set(e['file'] for e in d['entries'])
        union |= idx
        print('%s: %d 条' % (name, len(idx)))
    miss = md - union
    extra = union - md
    print('三索引合计 %d 条；库内 md %d 条' % (len(union), len(md)))
    if miss:
        for f in sorted(miss)[:5]:
            print('   ❌ 未入任何索引: %s' % f)
        ok = False
    if extra:
        for f in sorted(extra)[:5]:
            print('   ❌ 索引残留（库中已无）: %s' % f)
        ok = False
    print()
    print('=== 自检结论：%s ===' % ('全部通过' if ok else '存在问题（执行 --rebuild）'))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vault', default=VAULT)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--check', action='store_true')
    g.add_argument('--rebuild', action='store_true')
    g.add_argument('--selfcheck', action='store_true')
    a = ap.parse_args()
    import os as _os
    vault = _os.path.abspath(a.vault)
    if not os.path.isdir(vault):
        print('❌ 知识库路径不存在: %s' % vault); sys.exit(2)
    items = scan_vault(vault)
    print('知识库: %s' % vault)
    print('库内 md 文件: %d' % len(items))
    print()
    if a.rebuild:
        sys.exit(0 if cmd_rebuild(items, vault) else 1)
    if a.selfcheck:
        sys.exit(0 if cmd_selfcheck(items) else 1)
    sys.exit(0 if cmd_check(items) else 1)


if __name__ == '__main__':
    main()

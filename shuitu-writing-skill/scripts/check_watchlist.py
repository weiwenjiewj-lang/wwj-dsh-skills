#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""时效性检查机制：清单生成、巡检任务单、变更包应用与闭环自检。

用法:
    python check_watchlist.py --init                      生成/重建 source_watchlist.json
    python check_watchlist.py --check                     输出到期巡检任务单
    python check_watchlist.py --check --today 2026-12-01  指定基准日期
    python check_watchlist.py --simulate-supersede        模拟"标准被替代"，生成变更包
    python check_watchlist.py --apply-change 变更包.json   应用变更包（含闭环）
    python check_watchlist.py --selfcheck                 只跑自检

规则见 references/watchlist.md。
"""
import argparse, datetime, json, os, shutil, sys

import sys
sys.dont_write_bytecode = True   # 技能包不留 __pycache__（避免缓存掩盖规则改动）

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REF = os.path.join(SKILL, 'references')
WL = os.path.join(REF, 'source_watchlist.json')
ZA = os.path.join(REF, 'zone-a-index.json')
LOG = os.path.join(REF, 'change_log.jsonl')

# 档位与周期只读 rules.json watchlist 组（原来硬编码，且 rules.json 里没有这一组）
_WL_RULES = json.load(open(os.path.join(REF, 'rules.json'), encoding='utf-8-sig')).get('watchlist', {}) \
    if os.path.exists(os.path.join(REF, 'rules.json')) else {}
INTERVAL = _WL_RULES.get('check_interval_days') or {'P0': 90, 'P1': 180, 'P2': 365}
_PRI = _WL_RULES.get('priority_rules', {}) or {}
REQUIRED_FIELDS = _WL_RULES.get('required_fields') or [
    'doc_number', 'title', 'current_status', 'last_checked', 'next_check_date', 'official_lookup_url']
TODAY = datetime.date.today().isoformat()


def load(p):
    """读 JSON。文件不存在/内容坏了要给一句人话，而不是抛 traceback。"""
    if not os.path.exists(p):
        sys.stderr.write('❌ 找不到文件：%s\n'
                         '   变更包由巡检步骤生成：python scripts/check_watchlist.py --simulate-supersede\n'
                         '   或用 --check 先看巡检任务单。\n' % p)
        raise SystemExit(2)
    try:
        return json.load(open(p, encoding='utf-8-sig'))
    except Exception as ex:
        sys.stderr.write('❌ 文件不是合法 JSON：%s（%s）\n' % (p, ex))
        raise SystemExit(2)


def save(p, obj):
    json.dump(obj, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def priority_of(e):
    """档位判定全部取自 rules.json watchlist.priority_rules。"""
    st = str(e.get('status', ''))
    cur = tuple(_PRI.get('current_status_prefixes') or ['现行有效', '试行有效'])
    if _PRI.get('p0_when_status_not_current', True) and not st.startswith(cur):
        return 'P0'
    unconf = _PRI.get('p0_when_any_field_unconfirmed') or ['doc_number', 'effective_date',
                                                           'supersedes', 'publish_date']
    if any('待确认' in str(e.get(f, '')) for f in unconf):
        return 'P0'
    if e.get('doc_type_class') in (_PRI.get('p1_when_doc_type_class') or
                                   ['national_standard', 'industry_standard']):
        return 'P1'
    return _PRI.get('default') or 'P2'


def reason_of(e):
    r = []
    st = str(e.get('status', ''))
    if not st.startswith(('现行有效', '试行有效')):
        r.append('状态为「%s」，需跟踪是否被替代或恢复效力' % st)
    for f, lab in (('doc_number', '文号'), ('effective_date', '施行日期'),
                   ('supersedes', '被替代关系'), ('publish_date', '发布日期')):
        if '待确认' in str(e.get(f, '')):
            r.append('%s待确认' % lab)
    if e.get('doc_type_class') in ('national_standard', 'industry_standard'):
        r.append('标准类，需跟踪修订/替代')
    if e.get('scope') in ('provincial', 'local'):
        r.append('地方文件，需跟踪地方更新')
    return '；'.join(r) or '定期复核效力状态'


def lookup_hint(e):
    body = e.get('issuing_body') or '发布机关不详'
    return '发布机关：%s → 请在该机关官方网站或标准发布公告中核对编号与效力状态' % body


def build_watchlist(zone_a, today=TODAY):
    items = []
    for e in zone_a['entries']:
        pr = priority_of(e)
        items.append({
            'doc_number': e.get('doc_number') or '待确认',
            'title': e.get('title') or os.path.splitext(os.path.basename(e['file']))[0],
            'current_status': e.get('status', '待确认'),
            'last_checked': today,
            'next_check_date': (datetime.date.fromisoformat(today)
                                + datetime.timedelta(days=INTERVAL[pr])).isoformat(),
            'official_lookup_url': e.get('source_url')
            if str(e.get('source_url', '')).startswith('http') else '待确认',
            'file': e['file'],
            'doc_type': e.get('doc_type'),
            'scope': e.get('scope'),
            'effective_date': e.get('effective_date'),
            'superseded_by': e.get('superseded_by'),
            'supersedes': e.get('supersedes'),
            'chapter_relevance': e.get('chapter_relevance', []),
            'check_interval': INTERVAL[pr],
            'check_reason': reason_of(e),
            'lookup_hint': lookup_hint(e),
            'priority': pr,
            'history': [],
        })
    items.sort(key=lambda x: (x['priority'], x['doc_number']))
    return {'version': '1.0', 'generated_at': today,
            'rule': 'references/watchlist.md',
            'note': 'official_lookup_url 仅在文件正文含 URL 时填写，否则为待确认；'
                    'lookup_hint 由发布机关机械生成，不含推测地址。',
            'count': len(items), 'items': items}


def cmd_check(wl, today):
    due = [i for i in wl['items'] if i['next_check_date'] <= today]
    due.sort(key=lambda x: (x['priority'], x['next_check_date']))
    print('=== 巡检任务单（基准日 %s）===' % today)
    print('清单总数 %d，到期需核对 %d 条' % (len(wl['items']), len(due)))
    print()
    for i in due:
        print('[%s] %s  《%s》' % (i['priority'], i['doc_number'], i['title'][:38]))
        print('     当前状态：%s    下次应核对：%s' % (i['current_status'], i['next_check_date']))
        print('     核对原因：%s' % i['check_reason'])
        print('     核验地址：%s' % (i['official_lookup_url'] if i['official_lookup_url'] != '待确认'
                                     else '待确认（%s）' % i['lookup_hint']))
        print('     影响章节：%s' % ','.join(i['chapter_relevance'][:10]) or '全域')
    return due


def make_change_package(wl, idx):
    """模拟：把库内一条国家标准当作被新版替代，走完整闭环。"""
    target = next((i for i in wl['items'] if i['doc_number'] == 'GB 50433-2018'), None)
    if not target:
        target = next(i for i in wl['items'] if i['priority'] == 'P1')
    old = target['doc_number']
    new = 'GB 50433-2027'
    return {
        'change_type': 'standard_superseded',
        'old_doc': old,
        'old_doc_file': target['file'],
        'new_doc': new,
        'effective_date': '2027-06-01',
        'affected_chapters': target['chapter_relevance'],
        'action_required': '在方案编制依据中替换标准号，并逐条款核对引用内容是否变化',
        'detected_at': TODAY,
        'evidence': '（模拟测试用例）《生产建设项目水土保持技术标准》GB 50433-2018 被 GB 50433-2027 替代，'
                    '2027-06-01 起实施 —— 本条为闭环测试用构造数据，非真实变更',
        '_simulated': True,
    }


def cmd_apply(pkg, dry=False):
    wl = load(WL)
    idx = load(ZA)
    # 变更包完整性
    need = ['change_type', 'old_doc', 'affected_chapters', 'action_required', 'evidence']
    miss = [k for k in need if not pkg.get(k)]
    if miss:
        print('❌ 变更包字段缺失：%s —— 拒绝应用' % miss)
        return False
    if pkg['change_type'] != 'no_change' and not pkg.get('evidence'):
        print('❌ 缺 evidence（凭据），拒绝应用')
        return False

    # 定位条目
    key = pkg.get('old_doc_file')
    if key:
        w_item = next((i for i in wl['items'] if i['file'] == key), None)
        z_item = next((e for e in idx['entries'] if e['file'] == key), None)
    else:
        w_item = next((i for i in wl['items'] if i['doc_number'] == pkg['old_doc']), None)
        z_item = next((e for e in idx['entries'] if e['doc_number'] == pkg['old_doc']), None)
    if not w_item or not z_item:
        print('❌ 未在清单/索引中定位到 %s' % pkg['old_doc'])
        return False

    status_new = {'standard_superseded': '已被代替', 'standard_repealed': '已废止',
                  'standard_amended': '现行有效（已修订）', 'regulation_updated': '现行有效',
                  'date_confirmed': w_item['current_status'], 'no_change': w_item['current_status']
                  }.get(pkg['change_type'], '待确认')

    if dry:
        print('【预演】%s：%s → %s' % (pkg['old_doc'], w_item['current_status'], status_new))
        return True

    # 备份
    for p in (WL, ZA):
        if os.path.exists(p):
            shutil.copy(p, p + '.bak')

    # ① 更新清单
    w_item['history'].append({'at': TODAY, 'change_type': pkg['change_type'],
                              'from': w_item['current_status'], 'to': status_new,
                              'new_doc': pkg.get('new_doc')})
    w_item['current_status'] = status_new
    w_item['last_checked'] = TODAY
    pr = 'P0'
    w_item['priority'] = pr
    w_item['check_interval'] = INTERVAL[pr]
    w_item['next_check_date'] = (datetime.date.fromisoformat(TODAY)
                                 + datetime.timedelta(days=INTERVAL[pr])).isoformat()
    if pkg.get('new_doc'):
        w_item['superseded_by'] = pkg['new_doc']
    save(WL, wl)

    # ② 更新 Zone A 索引
    z_item['status'] = status_new
    if pkg.get('new_doc'):
        z_item['superseded_by'] = pkg['new_doc']
    idx['last_change_applied_at'] = TODAY
    save(ZA, idx)

    # ③ 留痕
    rec = dict(pkg)
    rec['applied_at'] = TODAY
    rec['applied_to'] = ['source_watchlist.json', 'zone-a-index.json',
                         '知识缺口报告（需重新生成）']
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    print('✅ 变更包已应用（闭环完成）')
    print('   ① 清单：%s → %s，priority→P0，下次核对 %s' % (pkg['old_doc'], status_new, w_item['next_check_date']))
    print('   ② 索引：zone-a-index.json 同条目已同步（status / superseded_by）')
    print('   ③ 留痕：change_log.jsonl 已追加 1 条')
    print('   ④ 待办：重新运行 analyze_gaps.py 生成新的知识缺口报告')
    print('   影响章节：%s' % ','.join(pkg['affected_chapters']))
    return True


def cmd_selfcheck():
    ok = True
    wl = load(WL)
    idx = load(ZA)
    items = wl['items']
    print('=== 自检 ===')
    # 1 字段完整性
    bad = [i['doc_number'] for i in items
           if any(not str(i.get(f, '')).strip() for f in REQUIRED_FIELDS)]
    print('1 字段完整性：%s%s' % ('✅ 通过' if not bad else '❌ 失败 %s' % bad, ''))
    ok = ok and not bad
    # 2 索引一致性（以 file 为唯一键：doc_number 可为「待确认」而重复，不能作键）
    zmap = {e['file']: e for e in idx['entries']}
    mism = [i['file'] for i in items
            if i['file'] in zmap and zmap[i['file']]['status'] != i['current_status']]
    print('2 索引一致性：%s' % ('✅ 通过' if not mism else '❌ 清单与索引状态不一致 %d 条：%s'
                              % (len(mism), [os.path.basename(x) for x in mism[:3]])))
    ok = ok and not mism
    # 3 覆盖完整性
    # 清单按设计**包含两类条目**（见 references/watchlist.md §六 与条目的 note 字段）：
    #   ① 库内文件：file 指向 Zone A 实体文件 —— 必须与索引**逐一对应**；
    #   ② 库内缺失登记：status=「库内缺失」、file 为空 —— 记录被 Zone C 已批准方案引用、
    #      但库里还没有的标准/法规（由 Zone C approval_basis_list 引用频次统计产生）。
    # 因此覆盖比对必须**只比①**；旧实现直接 set(items) == set(index)，
    # 把 6 个空 file 键算进去，导致 wset(76) != zset(75)，永远报「不一致」。
    MISSING_STATUS = _WL_RULES.get('missing_in_vault_status') or '库内缺失'
    in_vault = [i for i in items if i.get('file')]
    to_track = [i for i in items if not i.get('file')]
    wset = set(i['file'] for i in in_vault)
    zset = set(e['file'] for e in idx['entries'])
    cov_ok = (wset == zset)
    print('3 覆盖完整性：%s（库内文件 %d 条 vs 索引 %d 条；另有「%s」追踪登记 %d 条不计入比对）'
          % ('✅ 通过' if cov_ok else '❌ 不一致',
             len(wset), len(zset), MISSING_STATUS, len(to_track)))
    if not cov_ok:
        only_idx = sorted(zset - wset)
        only_wl = sorted(wset - zset)
        if only_idx:
            print('     索引有、清单缺 %d 条：%s' % (len(only_idx), [os.path.basename(x) for x in only_idx[:3]]))
        if only_wl:
            print('     清单有、索引无 %d 条：%s' % (len(only_wl), [os.path.basename(x) for x in only_wl[:3]]))
    # 缺失登记自身的合规性：status 必须是约定值、file 必须为空
    bad_track = [i.get('doc_number') for i in to_track if i.get('current_status') != MISSING_STATUS]
    if bad_track:
        print('     ⚠ 无 file 但状态不是「%s」的条目：%s' % (MISSING_STATUS, bad_track))
        cov_ok = False
    ok = ok and cov_ok
    # 4 日期合法性
    dbad = [i['doc_number'] for i in items if i['next_check_date'] <= i['last_checked']]
    pbad = [i['doc_number'] for i in items if i['check_interval'] != INTERVAL.get(i['priority'])]
    print('4 日期合法性：%s' % ('✅ 通过' if not dbad and not pbad
                                else '❌ next<=last %s；周期与优先级不匹配 %s' % (dbad, pbad)))
    ok = ok and not dbad and not pbad
    # 5 闭环完整性
    closure = True
    if os.path.exists(LOG):
        for line in open(LOG, encoding='utf-8'):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            key = r.get('old_doc_file')
            if key:
                it = next((i for i in items if i['file'] == key), None)
                zt = zmap.get(key)
            else:
                it = next((i for i in items if i['doc_number'] == r['old_doc']), None)
                zt = next((e for e in idx['entries'] if e['doc_number'] == r['old_doc']), None)
            if not it or not zt or it['current_status'] != zt['status']:
                closure = False
            if not any(h.get('change_type') == r['change_type'] for h in (it or {}).get('history', [])):
                closure = False
    print('5 闭环完整性：%s' % ('✅ 通过（清单/索引/留痕三者一致）' if closure else '❌ 存在半闭环状态'))
    ok = ok and closure
    print()
    print('=== 自检结论：%s ===' % ('全部通过' if ok else '存在问题'))
    return ok



def cmd_undo():
    """撤销最近一次变更应用：从 .bak 恢复，并移除最后一条留痕。"""
    if not os.path.exists(LOG):
        print('无变更记录，无需撤销')
        return False
    lines = [l for l in open(LOG, encoding='utf-8').read().splitlines() if l.strip()]
    if not lines:
        print('变更日志为空，无需撤销')
        return False
    last = json.loads(lines[-1])
    ok = True
    for p in (WL, ZA):
        bak = p + '.bak'
        if os.path.exists(bak):
            shutil.copy(bak, p)
            print('已恢复:', os.path.basename(p))
        else:
            print('⚠ 缺备份:', os.path.basename(p)); ok = False
    with open(LOG, 'w', encoding='utf-8') as f:
        for l in lines[:-1]:
            f.write(l + '\n')
    print('已移除留痕 1 条（%s %s→%s）' % (last.get('old_doc'), last.get('change_type'),
                                          last.get('applied_at')))
    return ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--init', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--today', default=TODAY)
    ap.add_argument('--simulate-supersede', action='store_true')
    ap.add_argument('--apply-change', default=None)
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--selfcheck', action='store_true')
    ap.add_argument('--undo', action='store_true')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()

    if a.init:
        wl = build_watchlist(load(ZA), a.today)
        save(WL, wl)
        from collections import Counter
        c = Counter(i['priority'] for i in wl['items'])
        print('已生成 source_watchlist.json：%d 条' % wl['count'])
        print('优先级分布：P0=%d（%d 天） P1=%d（%d 天） P2=%d（%d 天）'
          % (c['P0'], INTERVAL.get('P0', 0), c['P1'], INTERVAL.get('P1', 0),
             c['P2'], INTERVAL.get('P2', 0)))
        print('official_lookup_url 已填：%d 条；待确认：%d 条'
              % (len([i for i in wl['items'] if i['official_lookup_url'] != '待确认']),
                 len([i for i in wl['items'] if i['official_lookup_url'] == '待确认'])))
        return
    if a.check:
        cmd_check(load(WL), a.today)
        return
    if a.simulate_supersede:
        pkg = make_change_package(load(WL), load(ZA))
        print('=== 模拟变更包（标准被替代）===')
        print(json.dumps(pkg, ensure_ascii=False, indent=1))
        if a.out:
            save(a.out, pkg)
            print('\n已写出:', a.out)
        return
    if a.apply_change:
        pkg = load(a.apply_change)
        cmd_apply(pkg, dry=a.dry)
        return
    if a.undo:
        cmd_undo()
        return
    if a.selfcheck:
        sys.exit(0 if cmd_selfcheck() else 1)
    ap.print_help()


if __name__ == '__main__':
    main()

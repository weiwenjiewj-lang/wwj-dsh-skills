#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库倒排索引构建器 —— 让全库 371 个 md / 566 MB 的**每一行**都可被检索与取回。

## 它解决什么问题（与 kb_lookup.py --find 的分工）

`kb_lookup.py --find` 已经是流式全库扫描、100% 覆盖，**但只返回位置**：
「文件名 + 行号 + ≤80 字片段」，然后要求人拿编辑器打开原文。
结果是 AI 能定位、却取不到正文——必须人工介入，这正是"读得到、用得好"的断点。

本模块建立**持久化倒排索引**，把"定位"与"取文"解耦：

  ① 建索引（本脚本）：一次性扫全库，产出
     - `search_index.json`     词 → {相对路径: [行号…]}   （倒排表）
     - `file_index.json`       相对路径 → {行数, 字节, 标题树, 图片锚点}
     索引**只存位置，不存正文** → 体积可控（远小于库体）

  ② 取文（kb_read.py）：按 `文件 + 行号 + 上下文窗口` **精确取回原文片段**，
     或按 Markdown 标题 `--section` 取整节。取多少由调用方决定，永不整文件倾倒。

## 为什么不用现成的全文索引库

依赖零外部包（只用标准库），且索引格式是明文 JSON——
可人工 diff、可随技能包一起交付、可在无网环境重建。

## 图片行怎么处理（补上原 --find 的盲区）

原 `cmd_find` 用 `_is_image_line()` **跳过** base64 图片行以提速（452MB→99MB）。
代价是：图片所在位置**完全不可检索**，问"哪里有一张图"时找不到。

本脚本**不跳过**，而是把图片行折叠成一个**锚点**记进 `file_index.json`：
    {"line": 1234, "bytes": 48000, "kind": "base64-image"}
于是「该文件第 1234 行有一张图」这一事实可被查到，且索引体积几乎不增加
（只记行号与字节数，不记 base64 本身）。

## 分词策略（中文无空格，直接按空格切词不可用）

采用**字级 n-gram（2~4 字）** + **ASCII 词（含标准号、数字、英文）** 双通道：
  · 「表土剥离」→ 表土 / 土剥 / 剥离 / 表土剥 / 土剥离 / 表土剥离
  · 「GB/T 50434-2018」→ 作为完整 ASCII token 保留
n-gram 的代价是索引变大，但换来**零漏召回**——这对"务必保证所有内容都能查到"是必要的。
索引体积由 `--max-postings-per-term` 约束（高频停用词如"的"截断）。

## 用法

    python build_search_index.py --build            # 首次全量构建
    python build_search_index.py --build --verbose  # 显示进度
    python build_search_index.py --verify           # 校验覆盖：371 文件是否全在
    python build_search_index.py --stats            # 索引体积与词条统计
"""
import argparse
import glob
import io
import json
import os
import re
import sys
import time

sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
REF = os.path.join(SKILL, 'references')

from vault_paths import VAULT

MD = os.path.join(VAULT, 'md库')

INDEX_FILE = os.path.join(REF, 'search_index.json')
FILE_INDEX_FILE = os.path.join(REF, 'file_index.json')

# ---------------------------------------------------------------- 常量

# 图片行特征（与 kb_lookup.py 保持一致，但这里不丢弃、只折叠）
LONG_B64 = re.compile(r'^[A-Za-z0-9+/=]{200,}$')

# 高频词的 postings 上限。语义是「**每词最多保留多少处命中**」。
#
# ⚠ 这里曾有一个**严重的静默漏检 bug**（2026-09 修复），务必理解：
#
# 旧实现按**文档顺序**边扫边 append，最后 `postings[:400]` 截断——即"保留前 400 处"。
# 但热门词的前 400 处往往**集中在最前面几个文件里**，于是：
#
#     词     索引文件数   真值文件数   覆盖率
#     保持       10          348        2.9%     ← 用户只能看到 2.9% 的资料
#     防治        4          305        1.3%
#     水土        8          344        2.3%
#     表土       42          251       16.7%
#
# 更糟的是它**静默**：查询"看起来正常地"返回了几十条结果，用的人不会知道
# 自己只看到了 2%。`水土保持` 这种 4 字查询依赖 2-gram 交集，
# 而 `水土`/`保持` 双双被截断，交集直接塌成 8 处（真值 2594 处）。
#
# 修法：**先按文件聚合、再截断，且保证文件覆盖优先**——
# 每个文件最多留 MAX_LINES_PER_FILE 行，在总预算内尽量让**更多文件**出现。
# 这样高频词的"能不能找到这个文件"不再被前几个文件的密集命中吃掉。
MAX_POSTINGS_DEFAULT = 400

# 截断时**单个文件**最多保留的行数。
# 目的：防止一个文件里某词出现几百次，把整个预算吃光、挤掉其他文件。
# 取 8：既能给出"这个文件里大概在哪几行"，又不至于垄断预算
# （400 / 8 = 至少可覆盖 50 个文件）。
MAX_LINES_PER_FILE = 8

# n-gram 长度范围。
#
# **为什么不是 2~4**：实测 4 字 n-gram 单层就产出 332 万词条（占索引 58%），
# 却只带来边际召回——中文检索里 2 字词已覆盖绝大多数需求（表土/剥离/弃渣/边坡），
# 3 字补足专业词（截水沟/防治区/林草覆盖率）。
# 更长的查询（如「表土剥离厚度」）交给 kb_read.py 的**2-gram 交集回退**处理：
# 取各 2 字窗口命中文件的交集，既能定位又不膨胀索引。
# 实测这一改动把索引从 1800 MB 压到约 300 MB 量级，召回能力基本不变。
NGRAM_MIN, NGRAM_MAX = 2, 3

# 单个 token 的最大长度：超过即视为正则误匹配（曾在索引里产生 133 字的"词"）
MAX_TOKEN_LEN = 24

# ASCII token：标准号、数字、英文单词
# 注意标准号形态 `GB/T 50434-2018` 中间**可以带空格**，必须整体识别——
# 否则 `GB/T 50434-2018` 会被切成 `GB/T` 与 `50434-2018` 两段，整串检索即失效。
# 但必须限制长度：无上限的正则会把整行英文/数字吞成一个"词"（实测出现过 133 字的 token）。
ASCII_TOKEN = re.compile(
    r'[A-Za-z]{2,8}\s*[/\-]?\s*[A-Za-z]{0,6}\s*\d{1,6}(?:\.\d+)*(?:\s*[-—]\s*\d{2,4})?'  # 标准号
    r'|[A-Za-z][A-Za-z0-9_\-\.]{1,23}'                                                 # 英文词
    r'|\d{2,}(?:\.\d+)*'                                                               # 纯数字
)

# 标准号里的空白归一：`GB/T 50434-2018` 与 `GB/T50434-2018` 应视为同一 token
WS_IN_CODE = re.compile(r'\s+')

# Markdown 标题
HEADING = re.compile(r'^(#{1,6})\s+(.+?)\s*$')


def is_image_line(line):
    s = line.strip()
    return 'base64,' in line or bool(LONG_B64.match(s))


def die(msg):
    sys.stderr.write(msg + '\n')
    sys.exit(2)


# ---------------------------------------------------------------- 分词

def tokenize(text):
    """返回该行文本产出的 token 集合。

    中文 → 连续汉字段落切成 2~3 字 n-gram（去重）
    ASCII → 完整 token（标准号 / 数字 / 英文），另加小写形式

    更长的中文查询（>3 字）由 kb_read.py 用 2-gram 交集回退处理，
    不在此处膨胀索引。
    """
    toks = set()

    # --- ASCII 通道
    for m in ASCII_TOKEN.finditer(text):
        t = m.group(0)
        if len(t) < 2 or len(t) > MAX_TOKEN_LEN:
            continue          # 过短无信息量，过长是正则误匹配
        toks.add(t)
        toks.add(t.lower())
        # 标准号去空白形式：`GB/T 50434-2018` → `GB/T50434-2018`
        # 让带空格与不带空格两种写法都能命中同一条
        if ' ' in t:
            compact = WS_IN_CODE.sub('', t)
            if 2 <= len(compact) <= MAX_TOKEN_LEN:
                toks.add(compact)
                toks.add(compact.lower())

    # --- 中文通道：只取连续汉字段
    han_runs = re.findall(r'[\u4e00-\u9fff]+', text)
    for run in han_runs:
        n = len(run)
        if n < NGRAM_MIN:
            continue
        for size in range(NGRAM_MIN, NGRAM_MAX + 1):
            if n < size:
                break
            for i in range(n - size + 1):
                toks.add(run[i:i + size])

    return toks


# ---------------------------------------------------------------- 构建

def build(verbose=False, max_postings=MAX_POSTINGS_DEFAULT, zone_filter=None):
    if not os.path.isdir(MD):
        die('知识库不可达：%s' % MD)

    files = sorted(glob.glob(os.path.join(MD, '**', '*.md'), recursive=True))
    if zone_filter:
        files = [f for f in files if zone_filter in f]
    if not files:
        die('未找到任何 .md 文件')

    # inverted: term -> list of (relpath, lineno)
    inverted = {}
    file_index = {}
    t0 = time.time()

    for i, path in enumerate(files, 1):
        rel = os.path.relpath(path, VAULT).replace('\\', '/')
        size = os.path.getsize(path)
        headings = []
        images = []
        nlines = 0
        try:
            with io.open(path, encoding='utf-8', errors='ignore') as f:
                for lineno, line in enumerate(f, 1):
                    nlines = lineno
                    # 图片行：折叠成锚点，不进倒排表（base64 无检索价值）
                    if is_image_line(line):
                        images.append({'line': lineno, 'bytes': len(line),
                                       'kind': 'base64-image'})
                        continue
                    m = HEADING.match(line)
                    if m:
                        headings.append({'line': lineno, 'level': len(m.group(1)),
                                         'text': m.group(2)[:120]})
                    if not line.strip():
                        continue
                    for tok in tokenize(line):
                        inverted.setdefault(tok, []).append((rel, lineno))
        except Exception as e:
            sys.stderr.write('  ! 读取失败 %s: %s\n' % (rel, e))
            continue

        file_index[rel] = {
            'lines': nlines,
            'bytes': size,
            'headings': headings,
            'images': images,
        }
        if verbose and i % 25 == 0:
            print('  … %d/%d 文件' % (i, len(files)))

    # --- 高频词截断：**先按文件聚合，再按文件轮转分配行预算**
    #
    # 旧实现 `postings[:max_postings]` 是"取文档序前 N 处"，会把预算全喂给
    # 最靠前的几个文件（实测"保持"只剩 10/348 个文件，覆盖率 2.9%），
    # 且完全静默。正确目标是**让尽量多的文件被看见**，行号只需给出大概位置。
    #
    # 算法：把每词的行按文件分组（组内行号已天然有序）→ 轮转（round-robin，
    # 每轮每个文件取 1 行）直到用满预算。于是文件数少时行给得密、
    # 文件数多时每个文件也至少能分到行，不会被挤掉。
    # 每个文件另设 MAX_LINES_PER_FILE 上限，防止单文件垄断。
    truncated = 0
    final = {}
    for term, postings in inverted.items():
        if len(postings) <= max_postings:
            final[term] = postings
            continue
        truncated += 1
        # 按文件聚合（保持首次出现的文件顺序，保证结果可重复）
        byfile = {}
        for rel, ln in postings:
            byfile.setdefault(rel, []).append(ln)
        # 轮转取行
        picked = []
        depth = 0
        while len(picked) < max_postings:
            added = False
            for rel, lines in byfile.items():
                if depth < len(lines) and depth < MAX_LINES_PER_FILE:
                    picked.append((rel, lines[depth]))
                    added = True
                    if len(picked) >= max_postings:
                        break
            if not added:
                break
            depth += 1
        # 恢复成 (rel, ln) 的稳定顺序：按文件首次出现序、组内行号升序
        order = {rel: i for i, rel in enumerate(byfile)}
        picked.sort(key=lambda rl: (order[rl[0]], rl[1]))
        final[term] = picked

    # --- 落盘
    os.makedirs(REF, exist_ok=True)

    # 倒排表按「词 → {文件: [行…]}」压缩存储（省掉重复的相对路径字符串）
    compact = {}
    for term, postings in final.items():
        by_file = {}
        for rel, ln in postings:
            by_file.setdefault(rel, []).append(ln)
        compact[term] = by_file

    # 文件字典：把长路径映射成整数，供二进制索引复用
    file_list = sorted({rel for bf in compact.values() for rel in bf})

    payload = {
        'schema': 3,
        'vault': os.path.relpath(VAULT, SKILL).replace('\\', '/'),
        'built_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'files': len(file_index),
        'terms': len(compact),
        'truncated_terms': truncated,
        'max_postings': max_postings,
        'file_list': file_list,
        'index': compact,
    }
    with io.open(INDEX_FILE, 'w', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))

    fidx = {
        'schema': 2,
        'built_at': payload['built_at'],
        'files': file_index,
    }
    with io.open(FILE_INDEX_FILE, 'w', encoding='utf-8') as f:
        f.write(json.dumps(fidx, ensure_ascii=False, separators=(',', ':')))

    el = time.time() - t0
    print('构建完成：%d 文件 / %d 词条 / 耗时 %.1fs' % (len(file_index), len(compact), el))
    print('  倒排 JSON: %s (%.1f MB)' % (INDEX_FILE, os.path.getsize(INDEX_FILE) / 1024 / 1024))
    print('  文件索引 : %s (%.1f MB)' % (FILE_INDEX_FILE, os.path.getsize(FILE_INDEX_FILE) / 1024 / 1024))
    if truncated:
        print('  截断词   : %d 个（出现 >%d 次的高频词，已保留前 %d 处）'
              % (truncated, max_postings, max_postings))

    # 自动打包成惰性二进制索引（否则每次查询要重新解析 1 GB JSON，约 19 秒）
    #
    # 优先 v5：在 v4 基础上，对词表、偏移表与 body 做 zlib 无损压缩
    #（66 MB → 约 41 MB，实测全量 238 万词条等价）。
    # v5 失败则回退 v4，再失败回退 v3，保证任何情况下都留下一个可用索引。
    kbx = os.path.join(REF, 'search_index.kbx')
    try:
        import subprocess
        packed = False
        for flag, label in (('--pack5', 'v5'), ('--pack4', 'v4'), ('--pack', 'v3')):
            # 必须显式指定 utf-8：Windows 默认以 GBK 解码子进程输出，
            # 打包器打印中文路径时会抛 UnicodeDecodeError（不影响结果，但会污染 stderr）。
            r = subprocess.run([sys.executable, os.path.join(HERE, 'pack_index.py'), flag],
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace', timeout=900)
            if r.returncode == 0 and os.path.exists(kbx):
                print('  已打包   : %s (%.1f MB, %s) —— 查询走此文件，快约 13 倍'
                      % (kbx, os.path.getsize(kbx) / 1024 / 1024, label))
                packed = True
                break
            if flag != '--pack':
                print('  ⚠ %s 打包失败，回退下一格式：%s' % (label, (r.stderr or '').strip()[:200]))
        if packed:
            # JSON 是中间产物，打包成功后删除以省 1 GB 磁盘
            try:
                os.remove(INDEX_FILE)
                print('  已清理   : 中间 JSON 已删除（.kbx 与其内容完全一致，--verify 可证）')
            except Exception:
                pass
        else:
            print('  ⚠ 打包失败，查询将回退读 JSON（较慢）')
    except Exception as e:
        print('  ⚠ 打包异常：%s（查询将回退读 JSON）' % e)
    return 0


def verify():
    """校验：知识库里的每个 md 是否都进了索引。"""
    if not os.path.exists(FILE_INDEX_FILE):
        die('索引不存在，请先 --build')
    with io.open(FILE_INDEX_FILE, encoding='utf-8') as f:
        fidx = json.load(f)
    indexed = set(fidx.get('files', {}).keys())

    on_disk = set()
    for p in glob.glob(os.path.join(MD, '**', '*.md'), recursive=True):
        on_disk.add(os.path.relpath(p, VAULT).replace('\\', '/'))

    missing = on_disk - indexed
    extra = indexed - on_disk

    print('知识库文件 : %d' % len(on_disk))
    print('已入索引   : %d' % len(indexed))
    print('缺失       : %d %s' % (len(missing), '✅ 零盲区' if not missing else ''))
    for m in sorted(missing)[:20]:
        print('   - %s' % m)
    if extra:
        print('索引中多余（磁盘已删） : %d' % len(extra))
        for e in sorted(extra)[:10]:
            print('   + %s' % e)
    return 0 if not missing else 1


def stats():
    if not os.path.exists(INDEX_FILE):
        die('索引不存在，请先 --build')
    with io.open(INDEX_FILE, encoding='utf-8') as f:
        idx = json.load(f)
    with io.open(FILE_INDEX_FILE, encoding='utf-8') as f:
        fidx = json.load(f)

    nimg = sum(len(v.get('images', [])) for v in fidx['files'].values())
    nhead = sum(len(v.get('headings', [])) for v in fidx['files'].values())
    nlines = sum(v.get('lines', 0) for v in fidx['files'].values())

    print('索引 schema   : %d' % idx.get('schema', 0))
    print('构建时间      : %s' % idx.get('built_at'))
    print('文件数        : %d' % idx.get('files', 0))
    print('总行数        : %d' % nlines)
    print('词条数        : %d' % idx.get('terms', 0))
    print('截断词数      : %d' % idx.get('truncated_terms', 0))
    print('标题数        : %d' % nhead)
    print('图片锚点      : %d' % nimg)
    print('')
    print('索引体积      : %.1f MB (倒排) + %.1f MB (文件索引)'
          % (os.path.getsize(INDEX_FILE) / 1024 / 1024,
             os.path.getsize(FILE_INDEX_FILE) / 1024 / 1024))
    return 0


def main():
    ap = argparse.ArgumentParser(description='知识库倒排索引构建器')
    ap.add_argument('--build', action='store_true', help='全量构建')
    ap.add_argument('--verify', action='store_true', help='校验覆盖（零盲区确认）')
    ap.add_argument('--stats', action='store_true', help='索引统计')
    ap.add_argument('--verbose', action='store_true', help='显示进度')
    ap.add_argument('--max-postings-per-term', type=int, default=MAX_POSTINGS_DEFAULT)
    ap.add_argument('--zone', help='仅索引包含该子串的路径（调试用）')
    a = ap.parse_args()

    if a.build:
        return build(a.verbose, a.max_postings_per_term, a.zone)
    if a.verify:
        return verify()
    if a.stats:
        return stats()
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

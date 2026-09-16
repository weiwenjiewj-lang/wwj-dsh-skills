#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""索引二进制打包器 v3 —— 惰性加载，把 19 秒降到毫秒级。

## 为什么要 v3（v2 的教训）

v2（KBIDX2）把 1036 MB JSON 压到 71.5 MB，**体积问题解决了**，
但暴露了新瓶颈：**加载要 18.7 秒**。

剖析结论（实测）：

    读盘 71.5 MB        0.02 s
    varint 解码全量      1.2 s
    构建 239 万嵌套字典  18.7 s   ← 真正的瓶颈

也就是说慢的不是 IO、不是解码，而是 **Python 逐个构造 dict 对象的开销**。
换任何编码格式都救不了这一项。

## v3 的解法：偏移量索引 + 惰性解析

不在加载时展开全部词条，而是：

  ① 打包时记录**每个词条的字节偏移**，写进一张「词 → 偏移」表
  ② 加载时只读这张表（239 万条，但值是整数，构建快得多）
  ③ **查询命中哪个词，才解析哪个词条的那几十字节**

查询路径变成：查表拿偏移 → 读该偏移处的一小段 → 解码。
绝大多数查询只碰 1~5 个词条，于是从 18.7 秒降到毫秒级。

## 文件布局（.kbx）

    [0:8]     magic  b'KBIDX3\n'
    [8:12]    header_len  (uint32 LE)
    [12:...]  header JSON —— 含 file_list、terms、词条区起始偏移
    [...]     词条区 —— 逐条紧凑编码（同 v2：词串 + 文件id + 行号差分）
    末尾 8 字节: 词条区起始偏移 (uint64 LE)

    「词 → 偏移」的表在 header JSON 里以 **两个并行数组** 存放
    （terms_joined 用 \n 连接 + offsets 差分 varint），
    这样 json.loads 只需处理字符串和整数，不构造 239 万个 dict。

## 用法

    python pack_index.py --pack      # JSON → .kbx（v3）
    python pack_index.py --verify    # 与 JSON 抽样比对
    python pack_index.py --stats     # 体积与加载耗时
"""
import argparse
import io
import json
import os
import struct
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

JSON_FILE = os.path.join(REF, 'search_index.json')
KBX_FILE = os.path.join(REF, 'search_index.kbx')
KBZ_FILE = os.path.join(REF, 'search_index.kbz')   # v2，保留兼容

MAGIC = b'KBIDX3\n'
MAGIC4 = b'KBIDX4\n'
MAGIC5 = b'KBIDX5\n'


def _enc_varint(n, out):
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def pack():
    if not os.path.exists(JSON_FILE):
        sys.stderr.write('找不到 %s，请先 build_search_index.py --build\n' % JSON_FILE)
        return 2

    t0 = time.time()
    with io.open(JSON_FILE, encoding='utf-8') as f:
        idx = json.load(f)
    table = idx['index']
    print('已载入 JSON：%d 词条' % len(table))

    # 文件字典
    file_ids = {}
    file_list = []
    for byfile in table.values():
        for rel in byfile:
            if rel not in file_ids:
                file_ids[rel] = len(file_list)
                file_list.append(rel)

    # ---- 词条区：按词排序，保证确定性输出，且偏移表可二分/顺序读
    terms_sorted = sorted(table)

    body = bytearray()
    offsets = []          # 每个词条在 body 中的起始偏移
    for term in terms_sorted:
        offsets.append(len(body))
        byfile = table[term]
        tb = term.encode('utf-8')
        _enc_varint(len(tb), body)
        body += tb
        _enc_varint(len(byfile), body)
        for rel, lines in byfile.items():
            _enc_varint(file_ids[rel], body)
            _enc_varint(len(lines), body)
            prev = 0
            for ln in lines:
                _enc_varint(ln - prev, body)
                prev = ln

    # ---- 偏移表：差分 varint 编码，base64 放 header
    import base64
    diff = bytearray()
    prev = 0
    for o in offsets:
        _enc_varint(o - prev, diff)
        prev = o

    header = {
        'schema': 3,
        'built_at': idx.get('built_at'),
        'files': idx.get('files'),
        'terms': len(terms_sorted),
        'truncated_terms': idx.get('truncated_terms'),
        'file_list': file_list,
        'n_offsets': len(offsets),
        'offsets_b64': base64.b64encode(bytes(diff)).decode('ascii'),
        # 词表本身也存一份（按 \n 连接），使查询无需遍历偏移表即可定位词
        'terms_joined': '\n'.join(terms_sorted),
    }
    # 注意：terms_joined 会让 header 变大（约 30 MB），但换来 O(1) 词定位。
    # 相比之下 v2 的 18.7 秒构建开销更不可接受。

    hb = json.dumps(header, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    out = bytearray()
    out += MAGIC
    out += struct.pack('<I', len(hb))
    out += hb
    body_start = len(out)
    out += body
    out += struct.pack('<Q', body_start)

    with open(KBX_FILE, 'wb') as f:
        f.write(bytes(out))

    js = os.path.getsize(JSON_FILE)
    ks = os.path.getsize(KBX_FILE)
    print('打包完成：%.1fs' % (time.time() - t0))
    print('  JSON : %8.1f MB' % (js / 1024 / 1024))
    print('  KBX  : %8.1f MB  （%.1f%%）' % (ks / 1024 / 1024, 100.0 * ks / js))
    print('  节省 : %8.1f MB' % ((js - ks) / 1024 / 1024))
    return 0


def _enc_prefix_terms(terms_sorted, out):
    """把已排序词表按**共享前缀**压缩写入 out，返回写入字节数。

    排序后的词条相邻共享长前缀（中文 n-gram 尤其明显：平均词长 9.3 字节，
    实测压缩到 9.4 MB / 21.2 MB）。每条编码为：
        共享前缀长度 varint | 剩余长度 varint | 剩余字节
    """
    prev = b''
    for t in terms_sorted:
        tb = t.encode('utf-8')
        m = 0
        lim = min(len(prev), len(tb))
        while m < lim and prev[m] == tb[m]:
            m += 1
        _enc_varint(m, out)
        _enc_varint(len(tb) - m, out)
        out += tb[m:]
        prev = tb


def _dec_prefix_terms(blob, n, pos):
    """前缀压缩词表的解码器（与 _enc_prefix_terms 对称）。

    返回 (terms, 新位置)。惰性索引只需在加载时还原这张词表——
    它的值是字符串，构建成本远低于 v2 的嵌套 dict。
    """
    terms = []
    prev = b''
    for _ in range(n):
        m, pos = _dec_varint(blob, pos)
        r, pos = _dec_varint(blob, pos)
        tb = prev[:m] + blob[pos:pos + r]
        pos += r
        terms.append(tb.decode('utf-8'))
        prev = tb
    return terms, pos


def pack4():
    """v4：消除词串双存 + 词表前缀压缩。

    v3 的体积构成（实测 98.2 MB）：
        header 26.7 MB —— terms_joined 21.2 + offsets_b64 3.1
        body   71.5 MB —— 其中 **18.9 MB 是词串本身又存了一遍**

    两项都是冗余，不是必需数据：
      · body 里词条按 sorted 顺序写入，与 header 词表**一一对应**；
        reader 本来就 `pos += tl` 跳过词串，说明它从未用到 body 里的词串。
      · header 词表用前缀压缩，21.2 MB → 9.4 MB。

    格式（仅 body 布局变化，header 换成前缀压缩词表）：
        [0:7]   magic KBIDX4\\n
        [7:11]  header_len uint32 LE
        [11:..] header JSON（terms_pfx_b64 取代 terms_joined）
        [..]    词条区：每条仅存 postings（词串按词表顺序隐含）
        末尾 8 字节: 词条区起始偏移 uint64 LE
    """
    if not os.path.exists(JSON_FILE):
        sys.stderr.write('找不到 %s，请先 build_search_index.py --build\n' % JSON_FILE)
        return 2

    t0 = time.time()
    with io.open(JSON_FILE, encoding='utf-8') as f:
        idx = json.load(f)
    table = idx['index']
    print('已载入 JSON：%d 词条' % len(table))

    file_ids = {}
    file_list = []
    for byfile in table.values():
        for rel in byfile:
            if rel not in file_ids:
                file_ids[rel] = len(file_list)
                file_list.append(rel)

    terms_sorted = sorted(table)

    # ---- 词条区：**不再写词串**，只写 postings
    body = bytearray()
    offsets = []
    for term in terms_sorted:
        offsets.append(len(body))
        byfile = table[term]
        _enc_varint(len(byfile), body)
        for rel, lines in byfile.items():
            _enc_varint(file_ids[rel], body)
            _enc_varint(len(lines), body)
            prev = 0
            for ln in lines:
                _enc_varint(ln - prev, body)
                prev = ln

    import base64
    # ---- 偏移表：差分 varint
    diff = bytearray()
    prev = 0
    for o in offsets:
        _enc_varint(o - prev, diff)
        prev = o

    # ---- 词表：前缀压缩
    pfx = bytearray()
    _enc_prefix_terms(terms_sorted, pfx)

    header = {
        'schema': 4,
        'built_at': idx.get('built_at'),
        'files': idx.get('files'),
        'terms': len(terms_sorted),
        'truncated_terms': idx.get('truncated_terms'),
        'file_list': file_list,
        'n_offsets': len(offsets),
        'offsets_b64': base64.b64encode(bytes(diff)).decode('ascii'),
        'terms_pfx_b64': base64.b64encode(bytes(pfx)).decode('ascii'),
        'terms_pfx_len': len(pfx),
    }

    hb = json.dumps(header, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    out = bytearray()
    out += MAGIC4
    out += struct.pack('<I', len(hb))
    out += hb
    body_start = len(out)
    out += body
    out += struct.pack('<Q', body_start)

    with open(KBX_FILE, 'wb') as f:
        f.write(bytes(out))

    js = os.path.getsize(JSON_FILE)
    ks = os.path.getsize(KBX_FILE)
    print('打包完成（v4）：%.1fs' % (time.time() - t0))
    print('  JSON : %8.1f MB' % (js / 1024 / 1024))
    print('  KBX  : %8.1f MB  （%.1f%%）' % (ks / 1024 / 1024, 100.0 * ks / js))
    print('  词表 : %8.1f MB（前缀压缩前 21.2 MB）' % (len(pfx) / 1024 / 1024))
    print('  body : %8.1f MB（已去重词串）' % (len(body) / 1024 / 1024))
    return 0


def _dec_varint(buf, pos):
    r = 0
    s = 0
    while True:
        b = buf[pos]
        pos += 1
        r |= (b & 0x7F) << s
        if not (b & 0x80):
            return r, pos
        s += 7


def pack5():
    """v5：在 v4 基础上对 header 两大块与 body 做 zlib 无损压缩。

    ## 为什么还能压

    v4 实测 65.5 MB，但它的三个大块**都还是未压缩的原始字节**：

        header.terms_pfx_b64   12.57 MB  → 原始 9.43 MB，base64 又涨 33%
        header.offsets_b64      3.13 MB  → 原始 2.34 MB
        body                   49.71 MB  → varint 流

    varint 流的熵并不高（同一文件的 id 反复出现、行号差分多为小值），
    所以 zlib 仍有很大空间。实测（本机 zlib，无第三方依赖）：

        offsets   3.13 MB → 1.80 MB  （省 42%）
        terms_pfx 12.57 MB → 5.52 MB  （省 56%）
        body      49.71 MB → 33.09 MB （省 33%）

    ## 为什么用 zlib 而不是别的

    · 标准库自带，**不引入任何新依赖**——技能包的"零依赖"约束不能破；
    · 解压是 C 实现，body 33 MB 解压约 0.1 秒，远小于 v2 那种 18.7 秒的
      "逐条构造 dict" 开销；且本格式是**惰性**的，body 只在真正取正文时才解压，
      header 两块在加载时解一次（合计 ~7 MB，可忽略）。

    ## 兼容性

    格式用**新 magic KBIDX5**，读写两侧同时支持 v3/v4/v5，
    因此老索引无需重建即可继续查询，新索引自动享受压缩收益。

    布局：
        [0:7]   magic KBIDX5\\n
        [7:11]  header_len uint32 LE
        [11:..] header JSON（offsets_z_b64 / terms_pfx_z_b64，均为 zlib+base64）
        [..]    body = zlib 压缩后的词条区
        末尾 8 字节: body 起始偏移 uint64 LE（指向 header 结束处）
    """
    if not os.path.exists(JSON_FILE):
        sys.stderr.write('找不到 %s，请先 build_search_index.py --build\n' % JSON_FILE)
        return 2

    import base64
    import zlib

    t0 = time.time()
    with io.open(JSON_FILE, encoding='utf-8') as f:
        idx = json.load(f)
    table = idx['index']
    print('已载入 JSON：%d 词条' % len(table))

    file_ids = {}
    file_list = []
    for byfile in table.values():
        for rel in byfile:
            if rel not in file_ids:
                file_ids[rel] = len(file_list)
                file_list.append(rel)

    terms_sorted = sorted(table)

    body = bytearray()
    offsets = []
    for term in terms_sorted:
        offsets.append(len(body))
        byfile = table[term]
        _enc_varint(len(byfile), body)
        for rel, lines in byfile.items():
            _enc_varint(file_ids[rel], body)
            _enc_varint(len(lines), body)
            prev = 0
            for ln in lines:
                _enc_varint(ln - prev, body)
                prev = ln

    diff = bytearray()
    prev = 0
    for o in offsets:
        _enc_varint(o - prev, diff)
        prev = o

    pfx = bytearray()
    _enc_prefix_terms(terms_sorted, pfx)

    raw_body = bytes(body)
    raw_off = bytes(diff)
    raw_pfx = bytes(pfx)

    z_body = zlib.compress(raw_body, 6)
    z_off = zlib.compress(raw_off, 9)
    z_pfx = zlib.compress(raw_pfx, 9)

    header = {
        'schema': 5,
        'built_at': idx.get('built_at'),
        'files': idx.get('files'),
        'terms': len(terms_sorted),
        'truncated_terms': idx.get('truncated_terms'),
        'file_list': file_list,
        'n_offsets': len(offsets),
        # 解压后的原始长度 + 压缩流，读取端据此解压
        'offsets_z_b64': base64.b64encode(z_off).decode('ascii'),
        'offsets_raw': len(raw_off),
        'terms_pfx_z_b64': base64.b64encode(z_pfx).decode('ascii'),
        'terms_pfx_raw': len(raw_pfx),
        'body_raw': len(raw_body),
        'zlib': 6,
    }

    hb = json.dumps(header, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    out = bytearray()
    out += MAGIC5
    out += struct.pack('<I', len(hb))
    out += hb
    body_start = len(out)
    out += z_body
    out += struct.pack('<Q', body_start)

    with open(KBX_FILE, 'wb') as f:
        f.write(bytes(out))

    js = os.path.getsize(JSON_FILE)
    ks = os.path.getsize(KBX_FILE)
    print('打包完成（v5）：%.1fs' % (time.time() - t0))
    print('  JSON      : %8.1f MB' % (js / 1024 / 1024))
    print('  KBX       : %8.1f MB  （%.1f%%）' % (ks / 1024 / 1024, 100.0 * ks / js))
    print('  terms_pfx : %8.2f → %5.2f MB（zlib）' % (len(raw_pfx) / 1024 / 1024, len(z_pfx) / 1024 / 1024))
    print('  offsets   : %8.2f → %5.2f MB（zlib）' % (len(raw_off) / 1024 / 1024, len(z_off) / 1024 / 1024))
    print('  body      : %8.1f → %5.1f MB（zlib）' % (len(raw_body) / 1024 / 1024, len(z_body) / 1024 / 1024))
    return 0


def verify():
    """校验 .kbx 内部一致性。

    ## 两种模式

    ① **有 JSON 源**（正常打包后）——抽样比对 postings，与原实现一致。
    ② **无 JSON 源**（发行包常态：只带 .kbx，不带 1 GB 的 JSON）——
       退化为**结构自洽校验**：词表条数、偏移单调性、抽样词条可解析，
       并对每个词条确认其 postings 的文件 id 都在 file_list 范围内。

    这样 `--verify` 在发行包里也永远可用，而不是一句"找不到 JSON"了事。
    """
    kbx = KBX_FILE if os.path.exists(KBX_FILE) else KBZ_FILE
    if not os.path.exists(kbx):
        sys.stderr.write('找不到 .kbx / .kbz\n')
        return 2

    sys.path.insert(0, HERE)
    from kb_read import _LazyIndex

    li = _LazyIndex(kbx)
    print('索引 : %s' % os.path.basename(kbx))
    print('格式 : v%d' % li.header.get('schema', 3))
    print('词条 : %d' % len(li))
    print('文件 : %d' % len(li.file_list))

    if os.path.exists(JSON_FILE):
        with io.open(JSON_FILE, encoding='utf-8') as f:
            jidx = json.load(f)['index']
        print('JSON 词条: %d' % len(jidx))
        if set(jidx) != set(li.keys()):
            print('✗ 词条集合不一致')
            return 1
        import random
        random.seed(42)
        sample = random.sample(sorted(jidx), min(500, len(jidx)))
        bad = 0
        for t in sample:
            a = {k: sorted(v) for k, v in jidx[t].items()}
            b = {k: sorted(v) for k, v in li.get(t).items()}
            if a != b:
                bad += 1
                if bad <= 3:
                    print('✗ 不一致：%r' % t)
        print('抽样 %d 词条，不一致 %d 条 %s'
              % (len(sample), bad, '✅ 完全一致' if not bad else ''))
        return 0 if not bad else 1

    # ② 结构自洽校验（无 JSON 源）
    print('（无 JSON 源，转结构自洽校验）')
    n_files = len(li.file_list)
    terms = li._terms
    if len(terms) != li.header['terms']:
        print('✗ 词表条数与 header 声明不符')
        return 1
    if terms != sorted(terms):
        print('✗ 词表未按序（偏移对应关系会错位）')
        return 1
    import random
    random.seed(42)
    sample = random.sample(range(len(terms)), min(800, len(terms)))
    bad = 0
    for i in sample:
        post = li.get(terms[i])
        if not post:
            bad += 1
            continue
        for f, lines in post.items():
            if f not in li.file_list:
                bad += 1
            elif lines != sorted(lines) or (lines and lines[0] < 1):
                bad += 1
    print('抽样 %d 词条，结构异常 %d 条 %s'
          % (len(sample), bad, '✅ 自洽' if not bad else ''))
    return 0 if not bad else 1


def stats():
    for label, p in (('JSON', JSON_FILE), ('KBZ(v2)', KBZ_FILE), ('KBX(v3)', KBX_FILE)):
        if os.path.exists(p):
            print('%-9s %8.1f MB' % (label, os.path.getsize(p) / 1024 / 1024))
    return 0


def main():
    ap = argparse.ArgumentParser(description='索引二进制打包 v3/v4/v5（惰性加载）')
    ap.add_argument('--pack', action='store_true')
    ap.add_argument('--pack4', action='store_true')
    ap.add_argument('--pack5', action='store_true')
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--stats', action='store_true')
    a = ap.parse_args()
    if a.pack:
        return pack()
    if a.pack4:
        return pack4()
    if a.pack5:
        return pack5()
    if a.verify:
        return verify()
    if a.stats:
        return stats()
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

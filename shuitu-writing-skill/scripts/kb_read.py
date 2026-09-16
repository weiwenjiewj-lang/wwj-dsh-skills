#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库精确取文 —— 把倒排索引查到的「位置」变成「可用的正文片段」。

## 它解决什么问题

`kb_lookup.py --find` 能定位（文件名+行号），但只给 ≤80 字片段，
然后要求「用编辑器打开，不要让 AI 直读」——**AI 到此为止，必须人工接手**。
这正是"所有内容都能查到"与"查到了能用"之间的断点。

本工具补上这最后一段：**按位置精确取回原文**，取多少由参数控制，永不整文件倾倒。

## 四条取文通道

    ① --find <词>           走倒排索引检索（毫秒级），返回命中位置+可选预览
    ② --read <文件> --line N [--context M]
                            取第 N 行上下各 M 行的正文（默认 M=25）
    ③ --section <文件> [--heading 关键词]
                            按 Markdown 标题取整节（标题→下一同级标题之间）
    ④ --file <文件> --head N
                            只看文件头部（标题树+前 N 行），用于判断"这份文件是什么"

## 为什么要有 --context 上限

知识库中位数文件 397 KB ≈ 11.6 万 token，最大 60+ MB。
**任何"读整个文件"的行为都会烧掉整个对话预算。**
因此本工具**强制**：
  · `--context` 有硬上限（默认 25 行，最大 200 行）
  · `--read` 必须给 `--line`，不能整文件读
  · 输出前打印**本次取文的 token 估算**，让调用方心里有数
  · 图片行以锚点形式显示，不回吐 base64

## 用法

    python kb_read.py --find 表土剥离厚度
    python kb_read.py --find 表土剥离厚度 --preview
    python kb_read.py --read "md库/Zone A - 规范层/04-国家标准/GB_T 50434-2018_生产建设项目水土流失防治标准.md" --line 120 --context 30
    python kb_read.py --section "md库/.../GB 50434-2018.md" --heading "防治标准等级"
    python kb_read.py --file "md库/.../某标准.md" --head 40
    python kb_read.py --terms           # 索引词条统计
    python kb_read.py --outline <文件>   # 列出该文件的标题树
"""
import argparse
import base64
import io
import json
import os
import re
import struct
import sys

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
KBZ_FILE = os.path.join(REF, 'search_index.kbz')
KBX_FILE = os.path.join(REF, 'search_index.kbx')
FILE_INDEX_FILE = os.path.join(REF, 'file_index.json')

# 中文约 1 token ≈ 1.5 字；用于给调用方一个量级感
CHARS_PER_TOKEN = 1.5

LONG_B64 = re.compile(r'^[A-Za-z0-9+/=]{200,}$')
HEADING = re.compile(r'^(#{1,6})\s+(.+?)\s*$')

CONTEXT_HARD_MAX = 200


def die(msg, code=2):
    sys.stderr.write(msg + '\n')
    sys.exit(code)


def load(name):
    p = os.path.join(REF, name)
    if not os.path.exists(p):
        return None
    with io.open(p, encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------- 索引装载
#
# 三个候选来源，按**加载速度**择优（不是按体积）：
#
#   .kbx 惰性二进制  —— 只建「词→偏移」表，词条正文查到时才解析  ← 首选，毫秒级
#   .kbz 全量二进制  —— 一次展开全部词条（18.7s，239 万 dict 构建开销）
#   .json             —— 同上且更大
#
# 实测慢的根因不是 IO 也不是解码，而是 Python 逐个构造嵌套 dict：
#   读盘 0.02s / varint 解码 1.2s / **构建 239 万 dict 18.7s**
# 所以 v3 的核心就是不预先构建。
_INDEX_CACHE = None
_KBX_CACHE = None


class _LazyIndex:
    """惰性倒排索引：查词时才解析该词条的 postings。

    对外暴露 `.get(term)`，返回 {相对路径: [行号…]} 或 None，
    与普通 dict 的用法一致，因此调用方无需感知它是惰性的。
    """

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            buf = f.read()
        self.v5 = False
        self.v4 = False
        if buf.startswith(b'KBIDX5\n'):
            self.v5 = True
            self.v4 = True          # v5 的 body 布局沿袭 v4（不存词串）
            pos = len(b'KBIDX5\n')
        elif buf.startswith(b'KBIDX4\n'):
            self.v4 = True
            pos = len(b'KBIDX4\n')
        elif buf.startswith(b'KBIDX3\n'):
            pos = len(b'KBIDX3\n')
        else:
            raise ValueError('不是 KBX 文件')
        self.buf = buf
        (hlen,) = struct.unpack_from('<I', buf, pos)
        pos += 4
        header = json.loads(buf[pos:pos + hlen].decode('utf-8'))
        pos += hlen
        (self.body_start,) = struct.unpack_from('<Q', buf, len(buf) - 8)

        self.header = header
        self.file_list = header['file_list']

        # 词 → 偏移。用 dict(zip(...)) 一次性构建，比循环快得多；
        # 值只是整数，239 万个 int 的构建开销远低于嵌套 dict。
        import base64
        if self.v5:
            # v5：词表与偏移表都是 **zlib 压缩** 的（9.4 MB → 5.5 MB、2.3 MB → 1.8 MB）。
            # 解压是 C 实现，两块合计约 12 MB，解压耗时在 0.1 秒量级，可忽略。
            import zlib
            blob = zlib.decompress(base64.b64decode(header['terms_pfx_z_b64']))
            terms = _dec_prefix_terms(blob, header['terms'], 0)[0]
            if len(terms) != header['terms']:
                raise ValueError('v5 词表长度不符：%d != %d'
                                 % (len(terms), header['terms']))
            diff = zlib.decompress(base64.b64decode(header['offsets_z_b64']))
        elif self.v4:
            # v4：词表**前缀压缩**（21.2 MB → 9.4 MB）
            blob = base64.b64decode(header['terms_pfx_b64'])
            terms = _dec_prefix_terms(blob, header['terms'], 0)[0]
            if len(terms) != header['terms']:
                raise ValueError('v4 词表长度不符：%d != %d'
                                 % (len(terms), header['terms']))
            diff = base64.b64decode(header['offsets_b64'])
        else:
            terms = header['terms_joined'].split('\n')
            diff = base64.b64decode(header['offsets_b64'])
        offsets = []
        p = 0
        prev = 0
        # 与 _dec_prefix_terms 同理：偏移差绝大多数 < 128（单字节 varint），
        # 把这一情形内联，省掉 239 万次函数调用（实测 0.59s → 0.15s）。
        append = offsets.append
        for _ in range(header['n_offsets']):
            b = diff[p]
            p += 1
            if b < 0x80:
                prev += b
            else:
                d, p = _dec_v(diff, p - 1)
                prev += d
            append(prev)
        self._offsets = dict(zip(terms, offsets))
        self._terms = terms
        # v5 的 body 是整体 zlib 流，**首次真正取正文时才解压**（惰性）。
        self._body = None

    def _body_bytes(self):
        """按需取 body 字节。

        v5 的 body 是 zlib 压缩的整体流；本方法只在**第一次真正解析词条**时
        解压一次，之后缓存。绝大多数检索命令（--find 只回位置、--outline 只读标题）
        根本不解析 postings，因此压缩后的 body 不会带来任何额外开销。
        """
        if self._body is None:
            raw = self.buf[self.body_start:len(self.buf) - 8]
            if self.v5:
                import zlib
                raw = zlib.decompress(raw)
            self._body = raw
        return self._body

    def get(self, term):
        off = self._offsets.get(term)
        if off is None:
            return None
        return self._parse_at(off)

    def __contains__(self, term):
        return term in self._offsets

    def __len__(self):
        return len(self._offsets)

    def keys(self):
        return self._offsets.keys()

    def _parse_at(self, off):
        """只在需要时解析 one 词条的 postings。

        v4 的 body **不再存词串**（与 header 词表按顺序一一对应），
        因此这里少一次 varint 读与一次跳过；postings 布局与 v3 相同。
        """
        buf = self._body_bytes()
        pos = 0 if self.v5 else self.body_start
        pos += off
        if not self.v4:
            tl, pos = _dec_v(buf, pos)
            pos += tl                                # 跳过词串本身（v3 冗余）
        nf, pos = _dec_v(buf, pos)
        fl = self.file_list
        out = {}
        for _ in range(nf):
            fid, pos = _dec_v(buf, pos)
            nl, pos = _dec_v(buf, pos)
            lines = []
            prev = 0
            for _j in range(nl):
                d, pos = _dec_v(buf, pos)
                prev += d
                lines.append(prev)
            out[fl[fid]] = lines
        return out


def _dec_prefix_terms(blob, n, pos):
    """前缀压缩词表的解码（v4/v5）。与 pack_index._enc_prefix_terms 对称。

    每条为 `共享前缀长度 varint | 剩余长度 varint | 剩余字节`。
    中文 n-gram 排序后相邻共享长前缀，词表 21.2 MB → 9.4 MB；
    解出来是普通字符串列表，构建成本远低于 v2 的嵌套 dict。

    ## 为什么把 varint 内联（实测 1.37s → 0.55s）

    原始写法每条调 2 次 `_dec_v`，239 万条 = 478 万次函数调用。
    实测这一步占整个索引加载的 **51%**（1.37s / 2.67s），是真正的瓶颈。

    关键观察：**绝大多数前缀长/剩余长都 < 128，即单字节 varint**。
    于是把单字节情形内联展开（`b < 0x80` 直接取值），只有真的多字节时才回调
    `_dec_v`。行为与原来**完全一致**（同样的字节、同样的解析），只是少了函数调用开销。
    """
    terms = []
    prev = b''
    dn = blob
    for _ in range(n):
        # ---- 共享前缀长度（单字节快路径）
        b = dn[pos]
        pos += 1
        if b < 0x80:
            m = b
        else:
            m, pos = _dec_v(dn, pos - 1)
        # ---- 剩余长度（单字节快路径）
        b = dn[pos]
        pos += 1
        if b < 0x80:
            r = b
        else:
            r, pos = _dec_v(dn, pos - 1)
        tb = prev[:m] + dn[pos:pos + r]
        pos += r
        terms.append(tb.decode('utf-8'))
        prev = tb
    return terms, pos


def _dec_v(buf, pos):
    r = 0
    s = 0
    while True:
        b = buf[pos]
        pos += 1
        r |= (b & 0x7F) << s
        if not (b & 0x80):
            return r, pos
        s += 7


def _load_kbx_table(path):
    """供 pack_index.py --verify 使用：完整展开成普通 dict。"""
    li = _LazyIndex(path)
    table = {}
    for t in li.keys():
        table[t] = li.get(t)
    return li.header, table


def _load_kbz_table(path):
    """v2 格式：一次展开全部词条。"""
    with open(path, 'rb') as f:
        buf = f.read()
    if not buf.startswith(b'KBIDX2\n'):
        raise ValueError('不是 KBZ 文件')
    pos = len(b'KBIDX2\n')
    hlen, pos = _dec_v(buf, pos)
    header = json.loads(buf[pos:pos + hlen].decode('utf-8'))
    pos += hlen
    file_list = header['file_list']
    table = {}
    for _ in range(header['terms']):
        tlen, pos = _dec_v(buf, pos)
        term = buf[pos:pos + tlen].decode('utf-8')
        pos += tlen
        nfiles, pos = _dec_v(buf, pos)
        byfile = {}
        for _i in range(nfiles):
            fid, pos = _dec_v(buf, pos)
            nlines, pos = _dec_v(buf, pos)
            lines = []
            prev = 0
            for _j in range(nlines):
                d, pos = _dec_v(buf, pos)
                prev += d
                lines.append(prev)
            byfile[file_list[fid]] = lines
        table[term] = byfile
    return header, table


def load_index_table():
    """返回 (header, table)。优先 .kbx（惰性，毫秒级）。"""
    global _INDEX_CACHE
    if _INDEX_CACHE is not None:
        return _INDEX_CACHE

    if os.path.exists(KBX_FILE):
        try:
            li = _LazyIndex(KBX_FILE)
            _INDEX_CACHE = (li.header, li)
            return _INDEX_CACHE
        except Exception as e:
            sys.stderr.write('! .kbx 读取失败(%s)，尝试 .kbz\n' % e)

    if os.path.exists(KBZ_FILE):
        try:
            _INDEX_CACHE = _load_kbz_table(KBZ_FILE)
            return _INDEX_CACHE
        except Exception as e:
            sys.stderr.write('! .kbz 读取失败(%s)，回退 JSON\n' % e)

    if os.path.exists(INDEX_FILE):
        with io.open(INDEX_FILE, encoding='utf-8') as f:
            idx = json.load(f)
        _INDEX_CACHE = (idx, idx.get('index') or {})
        return _INDEX_CACHE
    return None


def tok_estimate(text):
    return int(len(text) / CHARS_PER_TOKEN)


def is_image_line(line):
    s = line.strip()
    return 'base64,' in line or bool(LONG_B64.match(s))


def resolve_path(rel):
    """把索引里的相对路径解析为绝对路径（容忍正反斜杠与大小写）。"""
    rel = rel.replace('\\', '/')
    cand = os.path.join(VAULT, rel.replace('/', os.sep))
    if os.path.isfile(cand):
        return cand
    # 也允许直接给 md库 之后的相对路径
    cand2 = os.path.join(MD, rel.replace('/', os.sep))
    if os.path.isfile(cand2):
        return cand2
    # 退化为按 basename 模糊匹配
    base = os.path.basename(rel)
    for p in _all_files():
        if os.path.basename(p) == base:
            return p
    return None


_FILE_CACHE = None


def _all_files():
    global _FILE_CACHE
    if _FILE_CACHE is None:
        import glob
        _FILE_CACHE = sorted(glob.glob(os.path.join(MD, '**', '*.md'), recursive=True))
    return _FILE_CACHE


# ---------------------------------------------------------------- ① 检索
def _verify_lines(cand, word):
    """回原文核对候选行：只保留**确实包含完整 word** 的行。

    ## 为什么必须有这一步

    倒排索引只能回答"这个 2~3 字片段出现在哪"，它**无法**回答"这个长串
    是否真的出现过"。于是任何基于片段并集/交集的回退都会**可能产生假阳性**：

        查询 `不存在词xyz`（全库真值 0 处）
          → 3-gram 窗口 `不存在` 命中 136 文件 → 旧实现直接当成"命中"输出。

    这在检索里是最坏的一类错误：结果看起来正常、条数还挺多，
    使用者不会怀疑，直接把不存在的表述写进方案。

    所以回退路径**一律**用本函数核对：把候选行的原文读出来，做真正的子串判断。
    代价是只读候选行（索引已把范围缩到几十~几百行的量级），实测毫秒级。
    """
    out = {}
    for rel, lines in cand.items():
        path = resolve_path(rel)
        if not path:
            continue
        keep = []
        try:
            with io.open(path, encoding='utf-8', errors='ignore') as f:
                want = set(lines)
                for ln, line in enumerate(f, 1):
                    if ln not in want:
                        continue
                    if ln > max(want):
                        break
                    if word in line:
                        keep.append(ln)
        except Exception:
            continue
        if keep:
            out[rel] = sorted(keep)
    return out


def _ngram_candidates(table, word, max_terms=8):
    """把长中文查询切成 2 字窗口，返回 [每个窗口的命中集] 供求交集。

    为什么需要它：索引只存 2~3 字 n-gram（为控制体积），
    查询「表土剥离厚度」时表里没有这个整词，必须拆成
    表土 / 土剥 / 剥离 / 离厚 / 厚度 再取**交集**——
    交集比并集精确得多，能直接定位到真正同时含这几个字的行。

    ## 踩过的坑：缺失窗口被静默跳过，导致交集塌缩

    旧实现在窗口**索引里没有**时直接 `continue` 跳过，然后对"剩下的窗口"求交集。
    这在数学上是错的：只要有一个窗口没被纳入，交集就可能包含
    **并不含该 2 字**的行；反之若关键窗口缺失，交集会异常地小。

    实测（索引里 `水土`/`保持` 因高频被截断）：
        查询「水土保持」→ 交集只剩 8 处，真值 2594 处，**报了个错得离谱的答案**。
    危险点在于它**不报错**，只是安静地返回一个极小的结果，
    使用者会以为"全库就这么多"。

    现在返回三元组 (sets, missing, n_windows)：
      · `missing` 列出索引里查不到的窗口——调用方必须把这一点**显式告诉使用者**，
        而不是假装交集是完整的；
      · 只要缺窗口，调用方应提示结果可能不全，并建议改用 `kb_lookup.py --find`
        （流式全库扫描，100% 覆盖）。
    """
    runs = re.findall(r'[\u4e00-\u9fff]+', word)
    sets = []
    missing = []
    n_windows = 0
    for run in runs:
        if len(run) < 2:
            continue
        for i in range(len(run) - 1):
            sub = run[i:i + 2]
            n_windows += 1
            hits = table.get(sub)
            if hits:
                sets.append((sub, {(rel, ln) for rel, lines in hits.items()
                                   for ln in lines}))
            else:
                missing.append(sub)
    # 按命中量升序：先交小的，快速收缩。
    #
    # ⚠ 这里**不能**截断窗口列表。
    # 旧实现返回 `sets[:max_terms]`（max_terms=8），对长查询会丢掉一部分窗口，
    # 于是"交集"退化成"前 8 个窗口的交集"——结果里会混进**并不完整包含查询串**的行，
    # 而且同样不报错。交集语义要求**每个窗口都必须参与**，少一个都不成立。
    # 性能上无需担心：按命中量升序求交，第一个窗口就把候选集压到最小，
    # 后续窗口只是在小集合上做过滤（实测 4 字查询 <5ms）。
    sets.sort(key=lambda kv: len(kv[1]))
    return sets, missing, n_windows


def cmd_find(word, limit=10, preview=False, ctx=2):
    loaded = load_index_table()
    if loaded is None:
        die('倒排索引不存在，请先运行：python build_search_index.py --build')

    _hdr, table = loaded
    word = word.strip()
    if not word:
        die('--find 需要关键词')

    # ① 精确命中
    hits = table.get(word)
    mode = '精确命中'

    # ② 子串回退：把长词切成窗口取并集
    #
    # ⚠ 这里曾有一个**假阳性 bug**（2026-09 修复），比漏检更危险：
    # 旧实现在第一个"能出结果的窗口尺寸"处 `break`，于是查询一个全库不存在的
    # 短语时，只要它的**某个 3 字片段**存在，就会返回那个片段的结果——
    # 实测查询 `不存在词xyz`（真值命中 0 处）返回了 **136 个文件**，
    # 而且输出标题写着"命中"，使用者完全无从察觉这是错的。
    #
    # 修法：回退结果**必须逐行回原文核对**——只有该行真的包含完整查询串才保留。
    # 这样"回退"只用于**发现候选**，最终答案始终以原文为准，不可能假阳性。
    if hits is None:
        merged = {}
        for size in (3, 2):
            if len(word) < size:
                continue
            for i in range(len(word) - size + 1):
                sub_hits = table.get(word[i:i + size])
                if not sub_hits:
                    continue
                for rel, lines in sub_hits.items():
                    merged.setdefault(rel, set()).update(lines)
            if merged:
                break
        if merged:
            verified = _verify_lines(merged, word)
            if verified:
                hits = verified
                mode = '子串回退命中（已逐行核对原文）'
            else:
                # 候选全部核对失败 ⇒ 该查询在全库并不存在，如实说"未命中"，
                # 不拿片段命中冒充整串命中。
                hits = None

    # ③ 长中文查询 → 2-gram 交集（比并集精确）
    #
    # ⚠ 交集必须**所有窗口都齐全**才可信。任一窗口在索引里缺失（高频词被截断、
    # 或该 2 字组合本就不存在），交集就可能塌缩成极小集合——
    # 实测「水土保持」曾只报 8 处（真值 2594 处）。所以这里记录缺失窗口，
    # 并在输出时明确告知，绝不把不完整的交集当完整答案呈现。
    inter_hits = None
    inter_files = None
    inter_missing = []
    inter_nwin = 0
    if word and re.search(r'[\u4e00-\u9fff]', word) and len(word) >= 4:
        sets, inter_missing, inter_nwin = _ngram_candidates(table, word)
        if len(sets) >= 2 and not inter_missing:
            # 行级交集：要求同一行号命中所有窗口（最精确，用于定位）
            common = sets[0][1]
            for _sub, s in sets[1:]:
                common &= s
                if not common:
                    break
            if common:
                # 同样必须逐行核对：2-gram 同行**不保证**这些字真的连成整串
                # （如「防治…标准」同行也算同行命中）。实测「表土剥离厚度」
                # 曾多报 1 处。核对后交集路径与精确路径的精度标准一致。
                agg = {}
                for rel, ln in common:
                    agg.setdefault(rel, []).append(ln)
                inter_hits = _verify_lines({k: sorted(v) for k, v in agg.items()},
                                           word) or None
            # 文件级交集：只要求同一**文件**内命中所有窗口。
            # 比行级宽，但能把"窗口分散在不同行"的真实命中捞回来，
            # 用于给使用者一个诚实的"到底涉及多少文件"的数。
            filesets = [{rel for rel, _ln in s} for _sub, s in sets]
            fcommon = filesets[0]
            for fs in filesets[1:]:
                fcommon &= fs
            inter_files = fcommon

    if not hits and not inter_hits:
        print('未命中「%s」。' % word)
        print('提示：可换更短的词（索引为 2~3 字 n-gram，短词召回更广）；')
        print('      或用 --read/--section 直接读已知文件。')
        return 1

    # 交集优先展示（更精确），并集作为补充
    if inter_hits:
        total_i = sum(len(v) for v in inter_hits.values())
        print('# 「%s」2-gram 交集命中：%d 个文件 / %d 处（**同时含全部检索字，最精确**）'
              % (word, len(inter_hits), total_i))
        # 关键诚实性提示：交集只有在"全部窗口都在索引里"时才可信。
        # 缺窗口意味着结果**必然不全**，必须说出来，不能让它看起来像完整答案。
        if inter_missing:
            print('')
            print('⚠ **结果可能不完整**：该查询的 %d 个 2 字窗口中，有 %d 个不在索引里'
                  % (inter_nwin, len(inter_missing)))
            print('   （%s）。索引为 2~3 字 n-gram 且高频词有截断，'
                  '缺失窗口会让交集偏小甚至为空。' % '、'.join(inter_missing[:8]))
            print('   要全库精确覆盖，请用：`python kb_lookup.py --find %s`'
                  '（流式扫描，100%% 覆盖，只回位置）。' % word)
        # 诚实性提示：即使是修好之后的交集，也**不保证 100% 召回**。
        # 原因：索引对高频词有 400 处/词的预算上限，`水土`(344文件) 这类词
        # 的 postings 已被截断，交集自然只能覆盖其中一部分。
        # 实测「水土保持」行级交集召回 80%（无假阳性）。
        # 使用者必须知道这是个"够用的近似"，要全量就用 kb_lookup --find。
        if inter_hits:
            print('ℹ 该结果是索引近似（**精确、无假阳性，但不保证 100% 召回**）：'
                  '索引对高频词设有每词 400 处的上限，'
                  '超出的位置不会进入交集（实测此类查询召回约 80%）。')
            print('  要**全库零遗漏**，用：`python kb_lookup.py --find %s`'
                  '（流式扫描 100%% 覆盖）。' % word)
        print('')
        shown = 0
        for rel in sorted(inter_hits, key=lambda r: -len(inter_hits[r])):
            lines = inter_hits[rel]
            print('- `%s`  （%d 处：%s）'
                  % (rel, len(lines), ', '.join(str(x) for x in lines[:12])
                     + (' …' if len(lines) > 12 else '')))
            if preview and shown < limit:
                path = resolve_path(rel)
                if path:
                    for ln, text in _read_lines(path, lines[0], ctx):
                        print('      %d | %s' % (ln, text[:150]))
            shown += 1
            if shown >= limit:
                break
        if len(inter_hits) > shown:
            print('（另有 %d 个文件未列出，用 --limit 调整）' % (len(inter_hits) - shown))
        print('')
        if not hits:
            print('**取正文**：`python kb_read.py --read "<文件>" --line <行号> --context 30`')
            return 0
        print('--- 以下是并集结果（召回更广，精度较低）---')
        print('')

    total = sum(len(v) for v in hits.values())
    print('# 「%s」%s：%d 个文件 / %d 处' % (word, mode, len(hits), total))
    # 诚实性提示：**精确命中路径同样会被 400 处上限截断**。
    # 判据是"总处数恰好等于上限"——只有撞到预算才会刚好卡在这个整数上，
    # 是截断的可靠信号（未截断的词不会这么巧）。
    # 实测「表土」：索引 400 处，kb_lookup 流式扫描 16778 处——差 42 倍，
    # 而旧输出只有一行"精确命中"，使用者会以为全库就 400 处。
    if total >= 400:
        print('⚠ **结果已被截断**：索引对每个词设有 **400 处**的预算上限，'
              '本词已用满该上限，**全库命中数远多于此**。')
        print('  要**全库零遗漏**，用：`python kb_lookup.py --find %s`'
              '（流式扫描 100%% 覆盖，只回位置）。' % word)
    print('')

    shown = 0
    for rel in sorted(hits, key=lambda r: -len(hits[r])):
        lines = hits[rel]
        print('- `%s`  （%d 处：%s）'
              % (rel, len(lines), ', '.join(str(x) for x in lines[:12])
                 + (' …' if len(lines) > 12 else '')))
        if preview and shown < limit:
            path = resolve_path(rel)
            if path:
                frag = _read_lines(path, lines[0], ctx)
                for ln, text in frag:
                    print('      %d | %s' % (ln, text[:150]))
        shown += 1
        if shown >= limit and not preview:
            break

    if len(hits) > shown:
        print('')
        print('（另有 %d 个文件未列出，用 --limit 调整）' % (len(hits) - shown))
    print('')
    print('**取正文**：`python kb_read.py --read "<文件>" --line <行号> --context 30`')
    print('**取整节**：`python kb_read.py --section "<文件>" --heading "<标题关键词>"`')
    return 0


# ---------------------------------------------------------------- ② 按行取
def _read_lines(path, center, ctx):
    """返回 [(行号, 文本)]；图片行折叠为锚点。"""
    out = []
    lo = max(1, center - ctx)
    hi = center + ctx
    with io.open(path, encoding='utf-8', errors='ignore') as f:
        for i, line in enumerate(f, 1):
            if i < lo:
                continue
            if i > hi:
                break
            if is_image_line(line):
                out.append((i, '〔图片 base64，约 %.0f KB，已折叠〕' % (len(line) / 1024)))
            else:
                out.append((i, line.rstrip('\n')))
    return out


def cmd_read(rel, line, ctx):
    if ctx > CONTEXT_HARD_MAX:
        die('--context 上限为 %d 行（防止一次性取文烧掉对话预算）' % CONTEXT_HARD_MAX)
    path = resolve_path(rel)
    if path is None:
        die('文件不存在：%s\n可用 --find 先定位。' % rel)

    frag = _read_lines(path, line, ctx)
    if not frag:
        die('行号 %d 超出文件范围。' % line)

    body = '\n'.join('%6d | %s' % (ln, t) for ln, t in frag)
    # 打印定位信息与开销估算
    fi = load('file_index.json') or {}
    key = None
    for k in (fi.get('files') or {}):
        if os.path.basename(k) == os.path.basename(path):
            key = k
            break
    meta = (fi.get('files') or {}).get(key or '', {})
    print('# %s' % key or rel)
    if meta:
        print('# 全文 %d 行 / %.0f KB（本次只取 %d 行，约 %d token）'
              % (meta.get('lines', 0), meta.get('bytes', 0) / 1024,
                 len(frag), tok_estimate(body)))
    print('')
    print(body)
    return 0


# ---------------------------------------------------------------- ③ 按节取
def cmd_section(rel, heading_kw):
    path = resolve_path(rel)
    if path is None:
        die('文件不存在：%s' % rel)

    with io.open(path, encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    # 收集标题
    heads = []
    for i, line in enumerate(lines, 1):
        m = HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))

    if not heads:
        die('该文件没有 Markdown 标题，请用 --read --line 取。')

    # 无关键词 → 列标题树
    if not heading_kw:
        print('# 标题树：%s' % rel)
        for ln, lvl, text in heads:
            print('  %s%s  （第 %d 行）' % ('  ' * (lvl - 1), text, ln))
        print('')
        print('用 --heading "<关键词>" 取某一节正文。')
        return 0

    # 找到匹配标题
    target = None
    for ln, lvl, text in heads:
        if heading_kw in text:
            target = (ln, lvl, text)
            break
    if target is None:
        print('未找到含「%s」的标题。该文件标题：' % heading_kw)
        for ln, lvl, text in heads[:40]:
            print('  %s%s  （第 %d 行）' % ('  ' * (lvl - 1), text, ln))
        return 1

    start, lvl, title = target
    # 到下一个同级或更高级标题为止
    end = len(lines)
    for ln, l2, _t in heads:
        if ln > start and l2 <= lvl:
            end = ln - 1
            break

    seg = lines[start - 1:end]
    body_lines = []
    nimg = 0
    for off, line in enumerate(seg, start):
        if is_image_line(line):
            body_lines.append('%6d | 〔图片 base64，约 %.0f KB，已折叠〕' % (off, len(line) / 1024))
            nimg += 1
        else:
            body_lines.append('%6d | %s' % (off, line.rstrip('\n')))
    body = '\n'.join(body_lines)

    print('# %s' % rel)
    print('# 节：%s（第 %d–%d 行，%d 行，约 %d token%s）'
          % (title, start, end, len(seg), tok_estimate(body),
             '，含 %d 张图' % nimg if nimg else ''))
    print('')
    print(body)
    if tok_estimate(body) > 60000:
        print('')
        print('⚠ 本节较大（约 %d token）。如需更小粒度，请用 --read --line 取其中一段。'
              % tok_estimate(body))
    return 0


# ---------------------------------------------------------------- ④ 文件头部
def cmd_file_head(rel, head):
    path = resolve_path(rel)
    if path is None:
        die('文件不存在：%s' % rel)
    frag = _read_lines(path, head // 2 + 1, head // 2)
    fi = load('file_index.json') or {}
    key = next((k for k in (fi.get('files') or {})
                if os.path.basename(k) == os.path.basename(path)), None)
    meta = (fi.get('files') or {}).get(key or '', {})
    print('# %s' % (key or rel))
    if meta:
        print('# 全文 %d 行 / %.0f KB / %d 个标题 / %d 张图'
              % (meta.get('lines', 0), meta.get('bytes', 0) / 1024,
                 len(meta.get('headings', [])), len(meta.get('images', []))))
    print('')
    for ln, t in frag:
        print('%6d | %s' % (ln, t))
    return 0


def cmd_outline(rel):
    return cmd_section(rel, None)


def cmd_terms():
    loaded = load_index_table()
    if loaded is None:
        die('倒排索引不存在，请先 build_search_index.py --build')
    hdr, table = loaded
    print('词条总数 : %d' % len(table))
    print('截断词   : %d' % (hdr.get('truncated_terms', 0) or 0))
    print('构建时间 : %s' % hdr.get('built_at'))
    if isinstance(table, _LazyIndex):
        src = '.kbx (惰性二进制，查询时按需解析)'
    elif os.path.exists(KBZ_FILE):
        src = '.kbz (全量二进制)'
    else:
        src = '.json'
    print('索引来源 : %s' % src)
    print('')
    # 统计分片：惰性索引不支持直接迭代，用 keys() 统一
    import collections
    c = collections.Counter()
    for t in table.keys():
        if re.match(r'^[\u4e00-\u9fff]+$', t):
            c['中文 %d 字' % len(t)] += 1
        else:
            c['ASCII/混合'] += 1
    for k, v in sorted(c.items()):
        print('  %-12s %d' % (k, v))
    return 0


def cmd_images(rel):
    """列出某文件（或全库）的图片锚点——原 --find 的盲区。"""
    fi = load('file_index.json')
    if fi is None:
        die('文件索引不存在，请先 build_search_index.py --build')
    files = fi.get('files') or {}
    if rel:
        key = next((k for k in files if rel in k), None)
        if key is None:
            die('未找到文件：%s' % rel)
        imgs = files[key].get('images', [])
        print('# %s —— %d 张图' % (key, len(imgs)))
        for im in imgs:
            print('  第 %d 行  %.0f KB' % (im['line'], im['bytes'] / 1024))
        return 0
    tot = sum(len(v.get('images', [])) for v in files.values())
    print('全库图片锚点：%d 张' % tot)
    ranked = sorted(files.items(), key=lambda kv: -len(kv[1].get('images', [])))
    for k, v in ranked[:15]:
        n = len(v.get('images', []))
        if n:
            print('  %-58s %d 张' % (k[:56], n))
    return 0


def main():
    ap = argparse.ArgumentParser(description='知识库精确取文（配合倒排索引）')
    ap.add_argument('--find', metavar='词', help='检索（走倒排索引）')
    ap.add_argument('--preview', action='store_true', help='--find 时附首处命中预览')
    ap.add_argument('--read', metavar='文件', help='按行取文')
    ap.add_argument('--line', type=int, help='中心行号')
    ap.add_argument('--context', type=int, default=25, help='上下行数（默认 25，上限 %d）' % CONTEXT_HARD_MAX)
    ap.add_argument('--section', metavar='文件', help='按标题取整节')
    ap.add_argument('--heading', help='标题关键词')
    ap.add_argument('--file', metavar='文件', help='看文件头部')
    ap.add_argument('--head', type=int, default=40, help='头部行数')
    ap.add_argument('--outline', metavar='文件', help='列出标题树')
    ap.add_argument('--images', nargs='?', const='', metavar='文件', help='列图片锚点')
    ap.add_argument('--terms', action='store_true', help='索引词条统计')
    ap.add_argument('--limit', type=int, default=10, help='--find 返回文件数上限')
    a = ap.parse_args()

    if a.find:
        return cmd_find(a.find, a.limit, a.preview, 2)
    if a.read:
        if a.line is None:
            die('--read 必须配合 --line <行号>（本工具拒绝整文件读取）')
        return cmd_read(a.read, a.line, a.context)
    if a.section:
        return cmd_section(a.section, a.heading)
    if a.file:
        return cmd_file_head(a.file, a.head)
    if a.outline:
        return cmd_outline(a.outline)
    if a.images is not None:
        return cmd_images(a.images or None)
    if a.terms:
        return cmd_terms()
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

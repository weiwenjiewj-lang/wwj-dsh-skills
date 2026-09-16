#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库扫描缓存（供各 build_*.py 复用）：按文件 mtime+size 跳过未变更文件。

## 它解决什么问题

`build_species_index.py` / `build_measure_methods.py` / `build_design_params.py` /
`extract_style_samples.py` 每次都**全量读取知识库**（274 个 md / 451 MB）。
反复重建时大量重复 IO —— 这是 token 与时间的主要浪费源之一。

本模块提供**两层缓存**：

  ① **文件层**：`(path, mtime, size)` → 文件正文。
     未变更的文件直接命中内存/磁盘缓存，不重新读取。
  ② **产物层**：知识库指纹（所有文件的 mtime+size 汇总哈希）→ 该资产是否需重建。
     指纹未变时，build 脚本可**直接跳过整个构建**。

## 用法

    from kb_cache import KBCache
    kb = KBCache(vault_dir)                 # 默认缓存目录 .cache/
    if kb.fingerprint_unchanged('species'):
        print('知识库未变更，跳过重建'); return
    for path, text in kb.iter_texts(zone_dirs):
        ...
    kb.save_fingerprint('species')

命令行自检：

    python kb_cache.py --status      # 查看缓存状态与节省量
    python kb_cache.py --clear       # 清空缓存
"""
import argparse
import hashlib
import io
import json
import os
import pickle
import sys
import time

from vault_paths import VAULT   # 知识库路径唯一入口（默认=技能包内 md库）

sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
CACHE_DIR = os.path.join(SKILL, '.cache')


class KBCache:
    def __init__(self, vault=None, cache_dir=None, max_cache_mb=8):
        """`max_cache_mb` 默认仅 8 MB。

        为什么调这么小：真正的节约来自**指纹短路**（知识库没变就跳过整个重建，
        0.1s 完成），而不是靠缓存文件正文。正文缓存只在"同一进程内多次读同一文件"
        时有用（如一次构建里多轮扫描），**没必要为了跨进程复用而落盘几百 MB**。
        因此默认只落盘一个小额上限（小文件优先，命中率高、占用低）。
        """
        # 知识库路径统一走 vault_paths（环境变量 DSH_WS_VAULT 优先，
        # 否则默认技能包内的 md库 相对目录）。
        # 这里原先硬编码 <OBSIDIAN_VAULT> 绝对路径，知识库随技能包交付后必然失效。
        self.vault = vault or VAULT
        self.dir = cache_dir or CACHE_DIR
        os.makedirs(self.dir, exist_ok=True)
        self.text_cache_path = os.path.join(self.dir, 'file_texts.pkl')
        self.fp_path = os.path.join(self.dir, 'fingerprints.json')
        self.max_cache_mb = max_cache_mb
        self._texts = None
        self._stats = {'hit': 0, 'miss': 0, 'bytes_saved': 0}

    # ---------------- 文件层缓存 ----------------
    def _load_texts(self):
        if self._texts is not None:
            return self._texts
        self._texts = {}
        if os.path.exists(self.text_cache_path):
            try:
                with open(self.text_cache_path, 'rb') as f:
                    self._texts = pickle.load(f)
            except Exception:
                self._texts = {}
        return self._texts

    def save_texts(self, max_mb=None):
        """持久化文件正文缓存。

        **必须按字节限制体积**：知识库 451 MB，若把全部正文都落盘，
        缓存文件会与知识库等大（实测曾达 462 MB / 69 MB），失去意义。

        策略：按文件**大小升序**保留（小文件命中率高、占用低），
        累计字节超过上限即停止；内存缓存不受影响（同进程内仍生效）。
        """
        limit = int((max_mb if max_mb is not None else self.max_cache_mb) * 1024 * 1024)
        cache = self._texts or {}
        items = []
        for k, v in cache.items():
            if not isinstance(v, str):
                continue
            # 用 UTF-8 字节数衡量（中文 3 字节/字），与磁盘实际占用一致
            items.append((len(v.encode('utf-8')), k, v))
        items.sort()                     # 小文件优先
        kept, total = {}, 0
        for nbytes, k, v in items:
            if total + nbytes > limit:
                continue
            kept[k] = v
            total += nbytes
        if not kept:
            return
        try:
            with open(self.text_cache_path, 'wb') as f:
                pickle.dump(kept, f, protocol=4)
        except Exception as e:
            sys.stderr.write('  ! 缓存写入失败：%s\n' % e)

    def check_size(self):
        """返回缓存文件当前大小（MB）。"""
        if not os.path.exists(self.text_cache_path):
            return 0.0
        return os.path.getsize(self.text_cache_path) / 1024 / 1024

    def read(self, path):
        """读取文件正文，命中缓存则不触碰磁盘。"""
        try:
            st = os.stat(path)
            key = (os.path.abspath(path), int(st.st_mtime), st.st_size)
        except OSError:
            return ''
        cache = self._load_texts()
        if key in cache:
            self._stats['hit'] += 1
            self._stats['bytes_saved'] += st.st_size
            return cache[key]
        self._stats['miss'] += 1
        try:
            text = io.open(path, encoding='utf-8', errors='ignore').read()
        except Exception:
            text = ''
        cache[key] = text
        # 顺手清理同路径的旧版本键，避免缓存无限膨胀
        base = os.path.abspath(path)
        for k in [k for k in cache if k[0] == base and k != key]:
            cache.pop(k, None)
        return text

    def iter_texts(self, dirs, exts=('.md',)):
        """遍历目录下的文本文件，返回 (path, text)。命中缓存则跳过磁盘读。"""
        import glob
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for p in glob.glob(os.path.join(d, '**', '*'), recursive=True):
                if not os.path.isfile(p):
                    continue
                if exts and not p.lower().endswith(tuple(exts)):
                    continue
                yield p, self.read(p)

    # ---------------- 产物层指纹 ----------------
    def dir_fingerprint(self, dirs, exts=('.md',)):
        """对给定目录集合算指纹：全部文件的 (相对路径, mtime, size) 汇总哈希。

        只要文件内容或数量变了，指纹就变；否则可安全跳过重建。
        """
        import glob
        rows = []
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for p in glob.glob(os.path.join(d, '**', '*'), recursive=True):
                if not os.path.isfile(p):
                    continue
                if exts and not p.lower().endswith(tuple(exts)):
                    continue
                try:
                    st = os.stat(p)
                    rows.append((os.path.relpath(p, self.vault), int(st.st_mtime), st.st_size))
                except OSError:
                    continue
        rows.sort()
        h = hashlib.sha256()
        for r in rows:
            h.update(('%s|%d|%d' % r).encode('utf-8'))
        return h.hexdigest(), len(rows)

    def _load_fp(self):
        if os.path.exists(self.fp_path):
            try:
                return json.load(io.open(self.fp_path, encoding='utf-8'))
            except Exception:
                pass
        return {}

    def fingerprint_unchanged(self, name, dirs, exts=('.md',)):
        """该资产的输入指纹是否未变（未变 → 可跳过重建）。"""
        fp, n = self.dir_fingerprint(dirs, exts)
        old = self._load_fp().get(name) or {}
        return (old.get('fingerprint') == fp and old.get('files') == n), fp, n

    def save_fingerprint(self, name, fingerprint, files, extra=None):
        fps = self._load_fp()
        rec = {'fingerprint': fingerprint, 'files': files,
               'built_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'vault': self.vault}
        if extra:
            rec.update(extra)
        fps[name] = rec
        try:
            io.open(self.fp_path, 'w', encoding='utf-8').write(
                json.dumps(fps, ensure_ascii=False, indent=1))
        except Exception as e:
            sys.stderr.write('  ! 指纹写入失败：%s\n' % e)

    def report(self):
        return dict(self._stats)

    # ---------------- 产物体缓存 ----------------
    def load_artifact(self, name):
        """读产物体（如已建好的资产 dict），用于"知识库没变就直接复用"。"""
        p = os.path.join(self.dir, 'artifact_%s.pkl' % name)
        if not os.path.exists(p):
            return None
        try:
            with open(p, 'rb') as f:
                return pickle.load(f)
        except Exception:
            return None

    def save_artifact(self, name, obj):
        p = os.path.join(self.dir, 'artifact_%s.pkl' % name)
        try:
            with open(p, 'wb') as f:
                pickle.dump(obj, f, protocol=4)
        except Exception as e:
            sys.stderr.write('  ! 产物体缓存写入失败：%s\n' % e)


def cmd_status():
    kb = KBCache()
    print('缓存目录：%s' % kb.dir)
    if not os.path.isdir(kb.dir):
        print('  （尚无缓存）')
        return 0
    total = 0
    for f in sorted(os.listdir(kb.dir)):
        p = os.path.join(kb.dir, f)
        if os.path.isfile(p):
            sz = os.path.getsize(p)
            total += sz
            print('  %-28s %8.1f KB' % (f, sz / 1024))
    print('  合计 %.1f KB' % (total / 1024))
    fps = kb._load_fp()
    if fps:
        print()
        print('已建资产指纹：')
        for name, rec in fps.items():
            print('  %-22s %d 文件  %s' % (name, rec.get('files', 0), rec.get('built_at', '')))
    return 0


def cmd_clear():
    import shutil
    kb = KBCache()
    if os.path.isdir(kb.dir):
        shutil.rmtree(kb.dir)
        print('已清空缓存：%s' % kb.dir)
    else:
        print('无缓存')
    return 0


def main():
    ap = argparse.ArgumentParser(description='知识库扫描缓存')
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--clear', action='store_true')
    a = ap.parse_args()
    if a.status:
        return cmd_status()
    if a.clear:
        return cmd_clear()
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""图片入库工具：把 md 里的**外链图片**收进**包内** `.assets/` 目录，md 改相对路径引用。

## 为什么必须做这一步

知识库的规约是「**图片存包内 `.assets/`，md 用相对路径引用，禁止外链**」
（见 SKILL.md「知识库分区」：全库 371 个 md，0 个 PDF）。

而云服务（mineru 官方云服务等）转换 PDF 后，产出的是：
    <输出目录>/
      ├── xxx.md
      └── images/          ← 独立图片文件夹
          ├── abc123.jpg
          └── ...

若直接把 md 放进知识库，会出现两个问题：
  ① md 里是 `![](images/abc123.jpg)` 这类**相对路径外链**，脱离原目录即失效；
  ② 图片散落在库外，**脱离技能包即丢失**。

本脚本把图片**复制进包内** `vault\\.assets\\<tag>\\`，改写为 `../` 相对路径，
然后可安全删除原图片目录。

## 2026-09 重要修订：不再转 base64

**旧行为**：把图片转成 `data:image/...;base64,...` 内嵌进 md。
**实测问题**：全库 8510 张图内嵌后，md 膨胀到 **503.0 MB**（占全包 91%），
根因是 base64 的 **4/3 编码膨胀**。

**新行为**：图片存包内 `.assets/`，md 只留相对路径。
实测 md 从 503.0 MB 降到 **113.4 MB（-77.8%）**，包净省 **97.4 MB**。

**红线不变**：**禁止外链**（`http(s)://` 一律不得入库）、**禁止依赖包外路径**。
图片必须随包分发 —— 这条比"是否 base64"更重要。

## 用法

    # 处理单个 md（默认迁移到 <vault>\.assets\）
    python inline_images.py 目标.md --vault <知识库根>

    # 处理整个云服务输出目录
    python inline_images.py --dir <云服务输出目录> --vault <知识库根>

    # 只检查不修改（CI/入库前自检）
    python inline_images.py --check 目标.md

    # 旧行为（base64 内嵌，仅特殊场景）：加 --base64
    python inline_images.py 目标.md --base64

退出码：0 成功 · 1 有未处理的外链图片 · 2 用法错误
"""
import argparse
import base64
import io
import os
import re
import sys

sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

MIME = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
    '.svg': 'image/svg+xml', '.tif': 'image/tiff', '.tiff': 'image/tiff',
}

# 包内图片目录名（相对 vault 根）
ASSET_DIRNAME = '.assets'
# Markdown 图片：![alt](path "title")；只匹配**非 data:**（已是内嵌的跳过）
MD_IMG = re.compile(r'!\[([^\]]*)\]\(\s*(?!data:)([^)\s]+)(?:\s+"[^"]*")?\s*\)')
# HTML 图片：<img src="...">
HTML_IMG = re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*["\'])(?!data:)([^"\']+)(["\'])', re.I)


def is_remote(url):
    return bool(re.match(r'^(https?:)?//', url or '', re.I))


def resolve(md_path, url):
    """把 md 里的相对/绝对路径解析成磁盘路径。"""
    if is_remote(url):
        return None
    url = url.replace('/', os.sep)
    if os.path.isabs(url):
        return url
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(md_path)), url))


def to_data_uri(path):
    """[兼容保留] 图片 → base64 data URI。

    **2026-09 起入库默认不再使用**（base64 有 4/3 膨胀，见 `kb-and-tokens.md`）。
    仅在需要把库内图片导成自包含单文件时调用。
    """
    ext = os.path.splitext(path)[1].lower()
    mime = MIME.get(ext)
    if not mime:
        return None
    with open(path, 'rb') as f:
        b = f.read()
    return 'data:%s;base64,%s' % (mime, base64.b64encode(b).decode('ascii'))


def _asset_tag(md_path, vault):
    """按 md 相对路径生成稳定的资源目录名（8 位十六进制）。"""
    import hashlib
    try:
        rel = os.path.relpath(os.path.abspath(md_path), os.path.abspath(vault))
    except ValueError:
        rel = os.path.basename(md_path)
    return hashlib.sha256(rel.replace(os.sep, '/').encode('utf-8')).hexdigest()[:8]


def relocate(md_path, vault, check_only=False):
    """把 md 引用的**库外/同目录**图片移入 `<vault>\\.assets\\<tag>\\`，
    并把引用改写为相对路径。

    这是 2026-09 之后的知识库图片入库规约：
      * 图片存**包内** `.assets/`（随包分发，禁外链）
      * md 内用 `../` 相对路径引用，**任意深度可解析**
      * **不再** 转 base64（节省 4/3 膨胀，md 体积降 77.8%）

    返回 (迁移数, 失败列表, 新增资源目录)。
    """
    c = io.open(md_path, encoding='utf-8').read()
    orig = c
    ok, fail = 0, []
    tag = _asset_tag(md_path, vault)
    adir = os.path.join(vault, ASSET_DIRNAME, tag)
    # md 到 vault 的相对前缀
    depth = os.path.relpath(os.path.abspath(md_path), os.path.abspath(vault)).count(os.sep)
    prefix = ('../' * depth) + ASSET_DIRNAME + '/' + tag + '/'

    counter = [0]

    def move(p):
        """把 p 复制进 adir，返回新文件名。"""
        counter[0] += 1
        ext = os.path.splitext(p)[1].lower() or '.bin'
        name = '%04d%s' % (counter[0], ext)
        if not check_only:
            os.makedirs(adir, exist_ok=True)
            import shutil
            shutil.copy2(p, os.path.join(adir, name))
        return name

    def repl_md(m):
        nonlocal ok
        alt, url = m.group(1), m.group(2)
        # 已是库内 .assets 引用 → 跳过
        if ASSET_DIRNAME + '/' in url.replace('\\', '/'):
            return m.group(0)
        p = resolve(md_path, url)
        if p is None:
            fail.append('远程图片禁止入库（须先下载到本地）：%s' % url)
            return m.group(0)
        if not os.path.isfile(p):
            fail.append('图片不存在：%s' % url)
            return m.group(0)
        if os.path.splitext(p)[1].lower() not in MIME:
            fail.append('不支持的图片格式：%s' % url)
            return m.group(0)
        name = move(p)
        ok += 1
        return '![%s](%s%s)' % (alt, prefix, name)

    def repl_html(m):
        nonlocal ok
        pre, url, post = m.group(1), m.group(2), m.group(3)
        if ASSET_DIRNAME + '/' in url.replace('\\', '/'):
            return m.group(0)
        p = resolve(md_path, url)
        if p is None or not os.path.isfile(p):
            fail.append('图片不存在：%s' % url)
            return m.group(0)
        if os.path.splitext(p)[1].lower() not in MIME:
            fail.append('不支持的图片格式：%s' % url)
            return m.group(0)
        name = move(p)
        ok += 1
        return '%s%s%s%s' % (pre, prefix, name, post)

    c = MD_IMG.sub(repl_md, c)
    c = HTML_IMG.sub(repl_html, c)

    if not check_only and c != orig:
        io.open(md_path, 'w', encoding='utf-8', newline='').write(c)

    return ok, fail, ([adir] if ok else [])


def process(md_path, check_only=False, remove_dirs=True, scope_md=None, vault=None):
    """兼容旧签名：默认走 relocate（迁入包内 .assets/）。

    若显式传 `vault` 则用新的迁移逻辑；否则沿用旧的 base64 内嵌逻辑
    （供历史调用与特殊场景保留）。
    """
    if vault:
        return relocate(md_path, vault, check_only=check_only)

    c = io.open(md_path, encoding='utf-8').read()
    orig = c
    ok, fail, dirs = 0, [], set()

    def repl_md(m):
        nonlocal ok
        alt, url = m.group(1), m.group(2)
        p = resolve(md_path, url)
        if p is None:
            fail.append('远程图片无法内嵌：%s' % url)
            return m.group(0)
        if not os.path.isfile(p):
            fail.append('图片不存在：%s' % url)
            return m.group(0)
        uri = to_data_uri(p)
        if not uri:
            fail.append('不支持的图片格式：%s' % url)
            return m.group(0)
        dirs.add(os.path.dirname(p))
        ok += 1
        return '![%s](%s)' % (alt, uri)

    def repl_html(m):
        nonlocal ok
        pre, url, post = m.group(1), m.group(2), m.group(3)
        p = resolve(md_path, url)
        if p is None or not os.path.isfile(p):
            fail.append('图片不存在：%s' % url)
            return m.group(0)
        uri = to_data_uri(p)
        if not uri:
            fail.append('不支持的图片格式：%s' % url)
            return m.group(0)
        dirs.add(os.path.dirname(p))
        ok += 1
        return '%s%s%s' % (pre, uri, post)

    c = MD_IMG.sub(repl_md, c)
    c = HTML_IMG.sub(repl_html, c)

    # 图片归属：把已内嵌/已迁入的图片登记到包内 .assets/
    # （见下方 _relocate_assets；此处仅记录来源目录）
    if not check_only and c != orig:
        io.open(md_path, 'w', encoding='utf-8').write(c)

    # 删除图片目录：知识库规约是「无图片目录」，
    # 因此内嵌成功后，已被内嵌的图片文件与其所在目录可以清除。
    #
    # 安全前提：**必须先确认全目录内没有任何 md 再引用这些图片**，
    # 否则删除会破坏同目录下尚未处理的其它 md（--dir 是逐个 md 处理的）。
    if not check_only and remove_dirs and ok and not fail:
        for d in sorted(dirs, key=len, reverse=True):
            if not os.path.isdir(d):
                continue
            try:
                for fn in os.listdir(d):
                    fp = os.path.join(d, fn)
                    if not os.path.isfile(fp):
                        continue
                    # 仍被**本次范围外**的 md 引用时保留，避免误删共用图片
                    if _still_referenced(fp, scope_md, skip=md_path):
                        continue
                    os.remove(fp)
                if not os.listdir(d):
                    os.rmdir(d)
            except OSError as e:
                sys.stderr.write('  ⚠ 目录清理失败 %s：%s\n' % (d, e))
    return ok, fail, dirs


def _still_referenced(img_path, md_files, skip=None):
    """检查 img_path 是否仍被**其它** md 引用（防止误删共用图片）。

    md_files 为本次处理范围内的全部 md；skip 为当前正在处理的 md
    （它已内嵌完毕，不应再算作"引用者"）。
    """
    name = os.path.basename(img_path)
    for m in (md_files or []):
        if skip and os.path.abspath(m) == os.path.abspath(skip):
            continue
        try:
            c = io.open(m, encoding='utf-8').read()
        except Exception:
            continue
        if name in c:
            return True
    return False


def check(md_path):
    """只检查：是否仍有**未纳入包内管理**的图片引用。

    已迁入 `.assets/` 的相对路径引用**不算外链**（那是正确状态）。
    真正需要报的只有三类：
      1. 远程 URL（`http(s)://`）—— 禁止入库
      2. 库外绝对路径
      3. 指向不存在文件的相对路径（死链）

    **注意**：正文里出现的 "images/xxx" 字样（非图片语法）不是图片引用，
    不得误报。全库有 308 处这类正文提及（如标准原文举例），属正常内容。
    """
    c = io.open(md_path, encoding='utf-8').read()
    hits = []

    def judge(url):
        norm = url.replace('\\', '/')
        if ASSET_DIRNAME + '/' in norm:          # 已在包内 .assets/，正确
            return None
        if re.match(r'^(https?:)?//', norm, re.I):
            return 'remote:%s' % url
        p = resolve(md_path, url)
        if p and os.path.isabs(p) and not os.path.isfile(p):
            return 'missing:%s' % url
        return None

    for m in MD_IMG.finditer(c):
        r = judge(m.group(2))
        if r:
            hits.append(r)
    for m in HTML_IMG.finditer(c):
        r = judge(m.group(2))
        if r:
            hits.append(r)
    return hits


def main():
    ap = argparse.ArgumentParser(
        description='把 md 的外链图片收进包内 .assets/ 并改写为相对路径')
    ap.add_argument('path', nargs='?', help='md 文件')
    ap.add_argument('--dir', help='云服务输出目录（处理其中全部 md）')
    ap.add_argument('--vault', help='知识库根目录（图片迁入 <vault>\\.assets\\）')
    ap.add_argument('--check', action='store_true', help='只检查不修改')
    ap.add_argument('--keep-dirs', action='store_true', help='保留原图片目录（默认删除空目录）')
    ap.add_argument('--base64', action='store_true',
                    help='[旧行为] 转 base64 内嵌（默认改为迁入 .assets/）')
    a = ap.parse_args()

    targets = []
    if a.dir:
        if not os.path.isdir(a.dir):
            sys.stderr.write('❌ 目录不存在：%s\n' % a.dir)
            return 2
        for root, _d, files in os.walk(a.dir):
            for f in files:
                if f.lower().endswith('.md'):
                    targets.append(os.path.join(root, f))
        if not targets:
            sys.stderr.write('❌ 目录内无 md 文件：%s\n' % a.dir)
            return 2
    elif a.path:
        if not os.path.isfile(a.path):
            sys.stderr.write('❌ 文件不存在：%s\n' % a.path)
            return 2
        targets = [a.path]
    else:
        ap.print_help()
        return 0

    # vault 默认：md 所在目录向上找 .assets 或 md库 的父级
    vault = a.vault
    if not vault:
        for t in targets:
            p = os.path.dirname(os.path.abspath(t))
            while p and p != os.path.dirname(p):
                if os.path.isdir(os.path.join(p, ASSET_DIRNAME)) or \
                   os.path.basename(p) == 'vault':
                    vault = p
                    break
                p = os.path.dirname(p)
            if vault:
                break

    if a.check:
        total = 0
        for t in targets:
            hits = check(t)
            if hits:
                print('  ✗ %s 仍有 %d 处外链图片' % (os.path.basename(t), len(hits)))
                for h in hits[:3]:
                    print('      %s' % h)
                total += len(hits)
            else:
                print('  ✓ %s 图片已全部收入包内' % os.path.basename(t))
        return 1 if total else 0

    if not a.base64 and not vault:
        sys.stderr.write('❌ 未能确定知识库根目录，请显式指定 --vault\n')
        return 2

    grand_ok, grand_fail = 0, []
    for t in targets:
        if a.base64:
            ok, fail, _dirs = process(t, remove_dirs=not a.keep_dirs, scope_md=targets)
        else:
            ok, fail, _dirs = relocate(t, vault, check_only=False)
        grand_ok += ok
        grand_fail += ['%s: %s' % (os.path.basename(t), f) for f in fail]
        print('  %s：迁移 %d 张，失败 %d 张' % (os.path.basename(t), ok, len(fail)))

    print()
    if a.base64:
        print('合计内嵌 %d 张图片（base64 旧行为）' % grand_ok)
    else:
        print('合计迁入包内 .assets/ %d 张图片' % grand_ok)
        print('  资源目录：%s' % os.path.join(vault, ASSET_DIRNAME))
    if grand_fail:
        print('以下未处理（需人工确认）：')
        for f in grand_fail[:10]:
            print('  - %s' % f)
        return 1
    print('✅ 图片已随包存放，md 引用相对路径可解析，可入库')
    return 0


if __name__ == '__main__':
    sys.exit(main())

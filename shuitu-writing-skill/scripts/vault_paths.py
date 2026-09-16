#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库（vault）路径解析 —— **所有脚本的唯一取值入口**。

## 为什么单独成模块

原先 7 个脚本各自硬编码同一个绝对路径
（`<OBSIDIAN_VAULT>`）。
一旦知识库搬家，要改 7 处；漏改一处就会出现"部分脚本能用、部分读不到库"的诡异状态。
更糟的是**漏改的那处不会报错**——它会静默地把知识库当成"不可达"，
于是输出里带上过期警告，或干脆返回空依据。

现在集中到本模块，路径规则只在这个文件里定义一次。

## 路径规则（按优先级）

  1. 环境变量 `DSH_WS_VAULT` —— 显式指定，最优先（换库位置、临时挂载用）
  2. **技能包内的 `md库/` 相对目录** —— 默认，技能自包含
  3. 库根目录（若技能包内直接就是 `Zone A - 规范层/` 等结构）

## 为什么默认相对技能包

知识库随技能一同交付后，`E:\\...` 这样的绝对路径在别的机器上必然失效。
用相对路径则"技能包搬到哪，知识库就在哪"，不依赖盘符与环境。

## 兼容性

`resolve()` 返回的仍是**绝对路径字符串**——调用方（其余脚本）无需改动，
它们本来就在拿这个值去拼子目录。
"""
import os
import sys

# 技能目录零残留：本模块会被多个脚本 import，自身也须设置。
# 注意 Python 的时序——import 时先编译写盘、后执行模块体，
# 所以这一句管不住**本模块自己**的 .pyc（须由调用方在 import 前设置或加 -B）；
# 但它能保证本模块**后续 import 的东西**不落盘。
sys.dont_write_bytecode = True

# 技能根目录：本文件在 <技能根>/scripts/ 下
_HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(_HERE)


def _first_existing(*cands):
    for c in cands:
        if c and os.path.isdir(c):
            return c
    return ''


def resolve():
    """解析知识库**根目录**（含 `md库/` 的那一层）。

    ## 环境变量指向无效位置时的行为（重要）

    若 `DSH_WS_VAULT` 设了一个**不存在**的目录，本函数**不会静默回退**到技能包内默认位置——
    因为那会让使用者以为"换库成功了"，实际读的却是旧库，是比报错更危险的失败。
    此时返回该无效路径，由调用方据 `is_ready()` 给出明确提示。

    调用方判断库是否可用请用 `is_ready()`，不要只看 `resolve()` 是否为空。
    """
    env = (os.environ.get('DSH_WS_VAULT') or '').strip().strip('"').strip("'")
    if env:
        return _normalize(env)
    # 默认候选（依序尝试）：
    #   ① <技能包>/vault/    —— 推荐布局，技能自身 md 与库隔离
    #   ② <技能包>/          —— 兼容"md库 直接放在技能根"的布局
    for c in (os.path.join(SKILL_ROOT, 'vault'), SKILL_ROOT):
        if os.path.isdir(os.path.join(c, 'md库')):
            return c
    return os.path.join(SKILL_ROOT, 'vault')


def is_ready():
    """知识库是否真的可用（目录存在且含 md 文件）。"""
    m = md_dir()
    if not m or not os.path.isdir(m):
        return False
    try:
        for _r, _d, fs in os.walk(m):
            if any(f.lower().endswith('.md') for f in fs):
                return True
    except OSError:
        return False
    return False


def unavailable_reason():
    """库不可用时的**可读原因**（用于强制告警，不得静默）。"""
    env = (os.environ.get('DSH_WS_VAULT') or '').strip()
    root = resolve()
    if is_ready():
        return ''
    if env:
        return ('DSH_WS_VAULT 指向的路径不可用：%s\n'
                '  该目录不存在或不含 md 文件。**不会自动回退到技能包内知识库**——'
                '请修正环境变量，或清除它以使用技能包内默认位置。' % env)
    return ('知识库不可达：%s 下未找到 md库/\n'
            '  若知识库已随技能包交付，请确认 <技能包>/vault/md库/ 存在。' % root)


def _normalize(p):
    """允许传「库根」或「md库 本身」，统一返回**库根**。

    历史习惯：`DSH_WS_VAULT` 一直指"库根"（内含 `md库/` 子目录）。
    但使用者也可能顺手把它指到 `md库/` 本身。两者都接受。
    """
    if not p:
        return ''
    p = os.path.abspath(p)
    if os.path.isdir(os.path.join(p, 'md库')):
        return p
    # 传进来的就是 md库 本身 → 返回其父目录，保持"库根"语义一致
    base = os.path.basename(p.rstrip('\\/'))
    if base == 'md库' and os.path.isdir(p):
        return os.path.dirname(p)
    return p


def md_dir():
    """md 库目录（库根/md库）。"""
    root = resolve()
    if not root:
        return ''
    cand = os.path.join(root, 'md库')
    return cand if os.path.isdir(cand) else root


def zone_dir(zone_letter):
    """取某分层目录：'A' → md库/Zone A - 规范层。找不到返回 ''。"""
    m = md_dir()
    if not m or not os.path.isdir(m):
        return ''
    prefix = 'Zone %s' % str(zone_letter).upper()
    try:
        for name in sorted(os.listdir(m)):
            if name.startswith(prefix) and os.path.isdir(os.path.join(m, name)):
                return os.path.join(m, name)
    except OSError:
        pass
    return ''


def describe():
    """给输出用的一句话说明（含实际生效的来源）。"""
    env = (os.environ.get('DSH_WS_VAULT') or '').strip()
    root = resolve()
    if not root or not os.path.isdir(md_dir()):
        return ('知识库不可达：期望位于 %s（或设 DSH_WS_VAULT 指定）'
                % os.path.join(SKILL_ROOT, 'md库'))
    src = 'DSH_WS_VAULT 环境变量' if env else '技能包内相对路径'
    n = 0
    try:
        for _r, _d, fs in os.walk(md_dir()):
            n += sum(1 for f in fs if f.endswith('.md'))
    except OSError:
        pass
    return '知识库：%s（%s，%d 个 md）' % (root, src, n)


def vault_md_files():
    """枚举库内全部 md 的（绝对路径, 相对库根的路径）。

    「相对库根的路径」即索引里 `file` 字段的格式
    （如 `md库\\Zone A - 规范层\\01-法律\\xxx.md`），两者可直接比对。
    """
    base = resolve()
    m = md_dir()
    if not base or not m:
        return []
    out = []
    for root, _dirs, files in os.walk(m):
        for fn in files:
            if not fn.lower().endswith('.md'):
                continue
            ap = os.path.join(root, fn)
            rel = os.path.relpath(ap, base)
            out.append((ap, rel))
    return out


VAULT = resolve()
MD_DIR = md_dir()


def _clean_own_bytecode():
    """清掉本模块自身首次 import 时落下的 `.pyc`。

    ## 为什么必须做这一步

    Python 在 import 时**先编译写盘、后执行模块体**，
    所以本文件里的 `sys.dont_write_bytecode = True` **管不住自己**——
    每次 `import vault_paths` 都会在 `scripts/__pycache__/` 留下一个 `.pyc`。

    技能约定「技能目录零项目残留」，所以这里在模块加载完的**当次**就把它删掉。
    这是唯一能从模块自身完成的补救（调用方加 `-B` 是另一条路，但不能强求使用者）。
    """
    try:
        if not sys.dont_write_bytecode:
            return
        cache = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '__pycache__')
        if not os.path.isdir(cache):
            return
        stem = os.path.splitext(os.path.basename(__file__))[0]
        for fn in os.listdir(cache):
            if fn.startswith(stem + '.'):
                try:
                    os.remove(os.path.join(cache, fn))
                except OSError:
                    pass
        # 目录空了就一并删掉
        try:
            if not os.listdir(cache):
                os.rmdir(cache)
        except OSError:
            pass
    except Exception:
        pass


_clean_own_bytecode()


if __name__ == '__main__':
    print(describe())
    print('SKILL_ROOT =', SKILL_ROOT)
    print('VAULT      =', VAULT or '<未解析到>')
    print('MD_DIR     =', MD_DIR or '<未解析到>')
    zd = zone_dir('A')
    print('Zone A     =', zd or '<未找到>')

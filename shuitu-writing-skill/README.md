# shuitu-writing-skill

**生产建设项目水土保持方案编制**

| 项 | 值 |
|---|---|
| 目录 | `shuitu-writing-skill/` |
| 来源 | 自研（本仓库作者 wwj） |
| 许可 | 见仓库 LICENSE 与版权说明 |
| 入口 | `SKILL.md`（带 YAML frontmatter，DSH 可直接识别） |
| 文件数 | 8933 |
| 体积 | 452.36 MB |

> **技能自带的使用说明见 [`README.skill.md`](./README.skill.md)**（知识库边界、调用机制、常见问题）。本文件是仓库索引页。

## 用途

按水利部办水保函〔2026〕232 号模板逐节点产出方案正文；含 371 篇法规/标准/范例知识库与 8477 张扫描图。

## 安装到 DSH

把本目录整个复制到 DSH 的 skills 目录即可（目录名保持不变）：

```powershell
$dest = Join-Path $env:APPDATA 'dsh-desktop\harness\skills'
Copy-Item -Recurse -Force '.\shuitu-writing-skill' $dest
```

重启 DSH 后，会话技能目录里会出现 `shuitu-writing-skill`。

## 来源与版权

本技能为仓库作者自研。`vault/md库/` 收录的法规、国家标准、行业标准原文属公开发布的官方文本；`Zone B - 参考层` 下的专著、学位论文、期刊论文为其各自著作权人的作品，收录目的是让编辑工具能引用原文核对，**请勿用于商业再分发**。若著作权人提出异议，应删除对应文件。
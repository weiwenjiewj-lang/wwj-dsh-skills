# grilling

**拷问式计划打磨**

| 项 | 值 |
|---|---|
| 目录 | `grilling/` |
| 来源 | AI Hero 技能体系（Matt Pocock） |
| 许可 | 随上游 |
| 入口 | `SKILL.md`（带 YAML frontmatter，DSH 可直接识别） |
| 文件数 | 1 |
| 体积 | 0 MB |

## 用途

把计划当设计树，按轮次追问前沿问题，每问附推荐答案。

## 安装到 DSH

把本目录整个复制到 DSH 的 skills 目录即可（目录名保持不变）：

```powershell
$dest = Join-Path $env:APPDATA 'dsh-desktop\harness\skills'
Copy-Item -Recurse -Force '.\grilling' $dest
```

重启 DSH 后，会话技能目录里会出现 `grilling`。

## 来源与版权

来自 AI Hero 技能体系。本目录为二手收录，若上游有更新以官方仓库为准。
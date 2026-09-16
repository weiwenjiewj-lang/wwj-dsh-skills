# find-skills

**技能发现与安装助手**

| 项 | 值 |
|---|---|
| 目录 | `find-skills/` |
| 来源 | Agent Skills 生态（skills.sh / anthropics-skills 系） |
| 许可 | 随上游 |
| 入口 | `SKILL.md`（带 YAML frontmatter，DSH 可直接识别） |
| 文件数 | 1 |
| 体积 | 0.01 MB |

## 用途

教 agent 在开放技能生态里检索、评估并安装 skill，含安装量/来源可信度判据。

## 安装到 DSH

把本目录整个复制到 DSH 的 skills 目录即可（目录名保持不变）：

```powershell
$dest = Join-Path $env:APPDATA 'dsh-desktop\harness\skills'
Copy-Item -Recurse -Force '.\find-skills' $dest
```

重启 DSH 后，会话技能目录里会出现 `find-skills`。

## 来源与版权

来自 Agent Skills 开放生态，正文引用 [skills.sh](https://skills.sh/) 排行与 `npx skills` CLI。若上游有更新，以官方仓库为准。
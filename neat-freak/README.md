# neat-freak

**知识收尾与治理对账**

| 项 | 值 |
|---|---|
| 目录 | `neat-freak/` |
| 来源 | 本机 DSH 适配版，上游出处未在包内标注 |
| 许可 | 随上游 |
| 入口 | `SKILL.md`（带 YAML frontmatter，DSH 可直接识别） |
| 文件数 | 6 |
| 体积 | 0.04 MB |

## 用途

把文档、规则文件、记忆、工作区残留与代码真实行为对齐。

## 安装到 DSH

把本目录整个复制到 DSH 的 skills 目录即可（目录名保持不变）：

```powershell
$dest = Join-Path $env:APPDATA 'dsh-desktop\harness\skills'
Copy-Item -Recurse -Force '.\neat-freak' $dest
```

重启 DSH 后，会话技能目录里会出现 `neat-freak`。

## 来源与版权

包内未标注上游出处，此处为本机实际运行的版本。
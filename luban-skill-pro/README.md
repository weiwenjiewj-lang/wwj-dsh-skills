# luban-skill-pro

**鲁班.Skill 技能优化器**

| 项 | 值 |
|---|---|
| 目录 | `luban-skill-pro/` |
| 来源 | 智慧半岛（作者自署） |
| 许可 | 见包内 _meta.json |
| 入口 | `SKILL.md`（带 YAML frontmatter，DSH 可直接识别） |
| 文件数 | 17 |
| 体积 | 0.15 MB |

## 用途

工业级 skill 评分、自动优化、质量检查与 review；含 EvoSkill/SkillOps/CASCADE 等论文落地脚本。

## 安装到 DSH

把本目录整个复制到 DSH 的 skills 目录即可（目录名保持不变）：

```powershell
$dest = Join-Path $env:APPDATA 'dsh-desktop\harness\skills'
Copy-Item -Recurse -Force '.\luban-skill-pro' $dest
```

重启 DSH 后，会话技能目录里会出现 `luban-skill-pro`。

## 来源与版权

作者署名为「智慧半岛」。再分发请保留包内 `_meta.json` 与作者署名。
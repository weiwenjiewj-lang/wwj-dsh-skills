# qu-ai-wei

**中文去 AI 味重写**

| 项 | 值 |
|---|---|
| 目录 | `qu-ai-wei/` |
| 来源 | 改编自 blader/humanizer，DSH 中文改造 |
| 许可 | 随上游 |
| 入口 | `SKILL.md`（带 YAML frontmatter，DSH 可直接识别） |
| 文件数 | 8 |
| 体积 | 0.05 MB |

## 用途

保留事实、证据强度、语体与作者声口的前提下重写简体中文。

## 安装到 DSH

把本目录整个复制到 DSH 的 skills 目录即可（目录名保持不变）：

```powershell
$dest = Join-Path $env:APPDATA 'dsh-desktop\harness\skills'
Copy-Item -Recurse -Force '.\qu-ai-wei' $dest
```

重启 DSH 后，会话技能目录里会出现 `qu-ai-wei`。

## 来源与版权

改编自 [blader/humanizer](https://github.com/blader/humanizer)（MIT），此处为面向简体中文的 DSH 改造版。
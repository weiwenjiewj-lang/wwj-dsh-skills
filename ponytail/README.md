# ponytail

**最懒解法**

| 项 | 值 |
|---|---|
| 目录 | `ponytail/` |
| 来源 | 本机安装版（SKILL.md 声明 MIT） |
| 许可 | MIT |
| 入口 | `SKILL.md`（带 YAML frontmatter，DSH 可直接识别） |
| 文件数 | 1 |
| 体积 | 0.01 MB |

## 用途

YAGNI、标准库优先、一行优于五十行；支持 lite/full/ultra。

## 安装到 DSH

把本目录整个复制到 DSH 的 skills 目录即可（目录名保持不变）：

```powershell
$dest = Join-Path $env:APPDATA 'dsh-desktop\harness\skills'
Copy-Item -Recurse -Force '.\ponytail' $dest
```

重启 DSH 后，会话技能目录里会出现 `ponytail`。

## 来源与版权

SKILL.md 头部声明 MIT。上游仓库出处未在包内标注。
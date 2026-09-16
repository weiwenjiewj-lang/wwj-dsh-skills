# self-improvement

**踩坑记录与规律晋升**

| 项 | 值 |
|---|---|
| 目录 | `self-improvement/` |
| 来源 | OpenClaw 版 pskoett/self-improving-agent 的 DSH 适配版 |
| 许可 | 随上游 |
| 入口 | `SKILL.md`（带 YAML frontmatter，DSH 可直接识别） |
| 文件数 | 6 |
| 体积 | 0.02 MB |

## 用途

把失败与用户纠正写进 .learnings/，Pattern-Key 去重计数，达阈值晋升进 AGENTS.md。

## 安装到 DSH

把本目录整个复制到 DSH 的 skills 目录即可（目录名保持不变）：

```powershell
$dest = Join-Path $env:APPDATA 'dsh-desktop\harness\skills'
Copy-Item -Recurse -Force '.\self-improvement' $dest
```

重启 DSH 后，会话技能目录里会出现 `self-improvement`。

## 来源与版权

改编自 OpenClaw 版 `pskoett/self-improving-agent`，核心机制（Pattern-Key 去重、复发计数、三级晋升）原样保留，平台专属部分替换为 DSH 等价物。
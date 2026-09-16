# Pattern-Key 分类表

记录或 triage 条目时参考。Pattern-Key 是去重和复发统计的稳定键 —— 关键词搜索会漏掉同一问题的不同措辞，共享的 key 不会。

## 格式要求

```
area.symptom
```

恰好两级、小写、连字符分隔。例：`deps.module-not-found`。

**key 里不放**：文件名、版本号、主机名、绝对路径、日期。保持通用才能复现。

## 标准 Area

| Area | 范围 | 示例 key |
|---|---|---|
| `api` | 外部 API / 服务行为 | `api.rate-limit`、`api.schema-mismatch`、`api.missing-endpoint`、`api.auth-rejected` |
| `auth` | 凭证、token、权限 | `auth.token-expired`、`auth.missing-scope`、`auth.invalid-credential` |
| `build` | 编译、打包、CI | `build.type-error`、`build.missing-artifact`、`build.ci-timeout` |
| `config` | 配置文件、环境变量 | `config.missing-env`、`config.invalid-json`、`config.precedence-conflict` |
| `deps` | 包管理器、依赖 | `deps.module-not-found`、`deps.version-conflict`、`deps.lockfile-mismatch` |
| `fs` | 文件系统 | `fs.no-such-file`、`fs.permission-denied`、`fs.path-too-long` |
| `net` | 网络连通性 | `net.connection-refused`、`net.timeout`、`net.dns-failure`、`net.tls-handshake` |
| `runtime` | 语言 / 运行时错误 | `runtime.type-error`、`runtime.python-exception`、`runtime.oom` |
| `shell` | Shell / CLI 机制 | `shell.command-not-found`、`shell.nonzero-exit`、`shell.quoting-error` |
| `vcs` | Git 与版本控制 | `vcs.fatal-error`、`vcs.merge-conflict`、`vcs.detached-head` |
| `test` | 测试框架与断言 | `test.flaky`、`test.fixture-missing`、`test.timeout` |
| `tool` | 本 agent 工具协议 | `tool.path-outside-workspace`、`tool.sandbox-denied`、`tool.invalid-argument` |

## 使用规则

1. **先查再建**：`grep -ri "Pattern-Key:" .learnings/`，近似匹配优于新建
2. **一个 key 一个条目**（人工维护的）
3. **谨慎新增 area**：只有多个条目会共享时才加
4. **泛化 key 等于未分类**：`runtime.error`、`runtime.failure`、`misc.issue` 这类在 triage 时必须换成具体 key

## 与代码托管平台的关系

Pattern-Key 只用于本地 `.learnings/` 去重，不对外同步，也不要求与任何 issue tracker 的标签体系对齐。

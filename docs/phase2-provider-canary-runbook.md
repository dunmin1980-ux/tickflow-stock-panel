# Phase 2 Provider 单票 Canary 审批 Runbook

## 1. 当前授权状态

当前只达到：

```text
PHASE2B_PROVIDER_RELAY_READY
```

这表示本地 Mock Provider Relay 已通过隔离验证，不代表真实 Provider Canary
已获批准。本文件不包含真实端点、模型、凭据或可执行 live 命令。

## 2. 单独审批前置条件

- 审批范围固定为 `000403.SZ`，只允许一次请求和一次人工验收。
- Phase 2B-3A focused、后端全量、离线 validator 和受保护哈希必须仍通过。
- Relay、Proxy、Host Validator 和 Renderer 的提交 SHA 必须冻结并记录。
- Provider 必须支持受验证的 TLS、证书校验、固定 JSON 响应和明确的数据保留策略。
- 真实凭据必须在聊天、Git、镜像、命令行、环境变量和报告之外配置。
- 凭据注入只能使用临时只读 Secret 文件或独立受控凭据代理。
- Proxy 只允许经审批的单一主机、端口、方法和路径，禁止重定向。
- `temperature=0`、JSON mode、2 秒超时、1 MiB 上限、streaming/tools/web/files/
  functions disabled、retry=0 必须保持固定。
- 云端、Paper Trading、正式 Obsidian、Telegram、OpenClaw 和 Integrated Gold
  必须继续关闭。

任一条件不满足时，Canary 不得启动。

## 3. 人工变更窗口

审批记录必须指定执行人、观察人、开始/结束时间、固定 symbol、Provider 合同
版本、目标 allowlist 摘要和回滚责任人。执行前确认没有其他 AI 或 Provider
pipeline 并发运行。

真实凭据只在窗口内可用。不得复制到 Relay，不得记录值或摘要；审计只记录
`secret_present=true`。窗口结束后必须删除临时 Secret，并按 Provider 控制台
能力撤销或轮换短期凭据。

## 4. 执行边界

Canary 只能复用已经验证的链路：最小 Projection、Relay、固定 Proxy、Host
Claims Validator、确定性 Renderer 和隔离 preview 路由。不得临时修改镜像、
网络、超时、响应大小、重试、schema、股票池或输出目录。

只允许一个 Provider attempt。失败后不得自动重试、切换模型、切换端点、切换
凭据、放宽 schema 或人工修补 Candidate。所有结果先进入隔离 preview，不得
写入正式 Vault 或日报。

## 5. 立即中止条件

出现以下任一情况立即中止并清理：

- TLS 或证书验证失败；
- DNS/目标地址不等于批准 allowlist；
- HTTP 重定向、429、5xx、timeout、空响应或超大响应；
- 响应不是单一严格 JSON 对象；
- request ID、symbol、Projection SHA 或 Facts SHA 不一致；
- 未知字段、Claim Type、Predicate 或 Facts Pointer；
- raw/qfq 混用、交易 Claim、自由文本或敏感形态；
- Provider attempt 大于 1 或 retry 不为 0；
- Secret 出现在 inspect、日志、产物或进程可见环境；
- Relay 可绕过 Proxy、Proxy 可访问非批准目标或发生公网旁路；
- Host Validator/Renderer 失败或输出不确定；
- 容器、网络、Secret、临时文件清理失败；
- 任何受保护证据 hash 变化。

中止后不得在同一窗口重跑。问题必须形成脱敏诊断和新的审批决定。

## 6. 必需验收证据

- 审批记录和冻结提交 SHA；
- 三个镜像 digest、Dockerfile SHA 和运行时安全合同；
- 脱敏请求审计字段，不含正文、header、端点、凭据或宿主路径；
- Provider attempt=1、retry=0、HTTP/大小/hash/时间状态；
- Candidate、Claims Validator、Renderer 和路由状态；
- Secret 命中数 0；
- 容器、网络、Secret、临时文件残留数 0；
- TickFlow、云端、正式 Obsidian、Paper Trading 和外部发送仍为 0；
- 人工逐项确认 Facts Pointer、数值口径、禁用交易建议和免责声明。

只有全部证据通过，才能把 Canary 记为“单次技术验证通过”。这仍不授权三票
批次、定时运行、自动发布、模拟交易或生产使用。

## 7. 结束状态

Canary 执行后只允许记录“通过”“失败关闭”或“基础设施阻断”。无论结果如何，
都必须恢复到无运行容器、无测试网络、无临时 Secret、无外部发送的冻结状态，
并由人工决定是否提出下一阶段设计申请。

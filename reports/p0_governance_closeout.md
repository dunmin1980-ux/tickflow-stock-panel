# TickFlow P0 云端补丁治理收口报告

审计日期：2026-07-24（Asia/Shanghai）

最终状态：

```text
P0_GOVERNANCE_PARTIAL
```

本轮已完成提交漂移解释、不可变运行身份、云端安全复验、PWA
服务端修复、回滚资产核验、认证权限审计、真实数据 readiness
脚本及本地全量质量门禁。尚未完成的是 GitHub PR/远端 Actions
和真实安装端 PWA 人工确认，因此不得标记
`P0_GOVERNANCE_CLOSED`。

## 1. 提交身份

| 身份 | 精确 revision | 结论 |
|---|---|---|
| P0 指标运行时修复 | `0c2b73d521f0f8b54e8ec8ccf4de301f02963e10` | 修复增量历史预热、异常边界和重复复权 |
| 首次云端部署 | `aa9fc0a84b8e72091f8e26c86983c66819befd30` | 相对 `0c2b73d` 只增加报告 |
| 治理预检 HEAD | `12c99db6da323d8149b578358899cff5c8e53f55` | 相对 `aa9fc0a` 只增加部署报告 |
| PWA 运行时修复 | `494ee71481f1487c1046e1030381c1dfab03d707` | 当前分支运行时代码和云端 OCI revision |

`12c99db..494ee71` 包含两项运行时提交：

```text
d89e088 fix: serve generated root PWA assets
494ee71 fix: serve Workbox runtime asset
```

它们修复 `registerSW.js` 和 Workbox 运行时被 SPA fallback 错误返回为
HTML 的阻断问题。当前云端已部署至 `494ee71`，因此拟提交分支的
运行时内容与云端不存在未说明漂移。治理报告和脚本的最终提交只推进
文档/验收材料，不代表再次部署应用。

完整差异见 `reports/tickflow_commit_drift_audit.md`。

## 2. PR / CI

| 项 | 状态 |
|---|---|
| GitHub CLI | 未登录 |
| Open PR | `0` |
| 该分支 Actions run | `0` |
| 公共 compare 状态 | `ahead`，收口查询时 head=`494ee71` |
| PR 状态 | `PR_USER_ACTION_REQUIRED` |

PR 比较页：

```text
https://github.com/dunmin1980-ux/tickflow-stock-panel/compare/codex%2Ftickflow-multiclient-app...codex%2Ftickflow-p0-validation?expand=1
```

已生成：

- `reports/p0_pr_title.txt`
- `reports/p0_pr_body.md`
- `reports/p0_pr_compare_url.txt`

不能把本地测试、公共 compare clean/ahead 或云端运行正常写成远端 CI
通过。创建 PR 后必须核验 Actions 对应最终 head SHA，并确认 locked
sync、compileall、Ruff `F821` 和后端 pytest 全部通过。

## 3. 本地质量门禁

2026-07-24 本轮重新执行：

| 门禁 | 结果 |
|---|---|
| `uv sync --locked --extra desktop --extra dev` | 通过 |
| 增量历史回归 | `5 passed` |
| Ruff `F821` | 通过 |
| Python compileall | 通过 |
| 后端全量 pytest | `1296 passed, 13 warnings` |
| 项目 pnpm | `9.10.0` |
| frozen 前端安装 | 通过 |
| Vitest | `97 passed` |
| TypeScript | 通过 |
| Vite/PWA build | 通过，69 个 precache 条目 |
| Playwright | `25 passed, 2 skipped` |

前端 frozen 安装首次曾因 npm 网络超时失败；本轮仅重试同一
`--frozen-lockfile` 命令，成功后才继续测试，没有使用非冻结依赖解析。

## 4. 云端运行身份

| 项 | 当前值 |
|---|---|
| 容器 | `TickFlow_Stock_Panel` |
| 容器 ID | `364dcddc7955` |
| Image ID | `sha256:f4b6652d3b57d680b366057c32149f00a5e9fd099961c0245ab30cb4a4cbed43` |
| 唯一本地 tag | `tickflow-stock-panel-stage-a-app:candidate-494ee71` |
| OCI revision | `494ee71481f1487c1046e1030381c1dfab03d707` |
| 显示版本 | `0.1.86` |
| 绑定 | `127.0.0.1:3019 -> 3018/tcp` |
| 私网入口 | Tailscale HTTPS `:8443` |
| 重启次数 | `0` |
| Gold | 关闭 |
| Gold 外发 | `0` |

未建立可从 Registry 拉取的 RepoDigest；Docker 输出的本地、未限定
repository digest 不被当作远端 Registry 身份。Compose 已固定到唯一
本地 tag，不再只依赖 `latest`。

`./scripts/verify_cloud_p0_runtime.sh` 本轮返回：

```text
CLOUD_HOST_RUNTIME_OK
CLOUD_P0_RUNTIME_OK
```

3018/3019 保持 loopback；未发现 `0.0.0.0` 或 `[::]` 上的
3018/3019/5678 新暴露。现有 n8n 仍只绑定 Tailscale IP 与 loopback。

## 5. 数据和回滚

| 项 | 结果 |
|---|---|
| `preferences.json` SHA-256 | `949132c081de283f72829dadb95435bcf8d20bbe8623a483185fe9691d7706d0` |
| 最新备份 | `/home/ubuntu/tickflow-backups/20260724_105311-pre-pwa-494ee71` |
| 最新备份 user-data diff | `0` 字节 |
| 最新 rollback tag | `tickflow-stock-panel-stage-a-app:rollback-20260724_105311-pre-494ee71` |
| 最新 rollback Image ID | `sha256:4f3f240cec7a911364ce2c0a84e89a42a1206e93bd74496792045775bfdd1003` |
| 旧备份/回滚对 | 保留 |

隔离恢复演练已使用旧备份/回滚对在 `127.0.0.1:3021`
完成，验证 health、Workspace schema/resource、preferences hash 和
Gold 零外发后清理临时容器与目录。最新 PWA 前回滚对已确认存在且数据
diff 为零，但没有重复启动第二次恢复演练。详细边界见
`reports/cloud_rollback_validation.md`。

## 6. PWA

服务端状态：

```text
PWA_SERVER_UPDATED
```

自动验证确认：

- Tailscale `:8443` 与容器静态文件 hash 一致；
- `index.html` 引用的主 JavaScript 和 CSS bundle 均与当前容器 hash
  一致；
- manifest、`sw.js`、`registerSW.js`、Workbox 和图标均来自
  `494ee71` 镜像；
- `registerSW.js` 与 Workbox 返回 JavaScript MIME；
- 缺失 Workbox 资源返回 `404`，不再返回 SPA HTML。

安装端状态：

```text
PWA_INSTALLED_CLIENT_USER_CHECK_REQUIRED
```

自动服务端校验不能证明已安装的 Mac/iPhone PWA 已淘汰旧 precache。
真机步骤见 `reports/pwa_installed_client_manual_checklist.md`。

## 7. 认证和密钥

| 检查 | 结果 |
|---|---|
| 认证 | `configured=false`, `authenticated=false` |
| 密码存储 | PBKDF2 + 随机 salt，不保存明文密码 |
| Secure cookie | 云端配置为 `true` |
| `preferences.json` | root:root，`0600` |
| `auth.json` | 未创建 |
| `secrets.json` | 未创建 |
| 容器用户 | root |
| 本轮真实 Key 写入 | 否 |
| 明文 secret API 泄漏 | 未发现 |

状态：

```text
AUTH_USER_ACTION_REQUIRED
```

用户后续只能在 Tailscale 私网 UI 或私有终端完成密码和 Key
初始化，不得把密码、Cookie、TickFlow Key 或 AI Key 发到聊天。

已记录三个后续债务：通知凭据实际位于应按敏感数据处理的
`preferences.json`；有效 session token 会持久化；容器和宿主数据仍由
root 管理。这些不是本轮 P0 热修范围。

## 8. 真实数据与 AI Readiness

无密钥脚本返回预期阻塞：

```text
REAL_DATA_READINESS_BLOCKED
```

阻塞项：

- `AUTH_USER_ACTION_REQUIRED`
- `ADJUSTMENT_FACTOR_DATA_UNAVAILABLE`
- `FINANCIAL_DATA_UNAVAILABLE`

三只样本股票的 raw/enriched 日线均各有 244 行，最新日期均为
`2026-07-22`。该日期仍须与独立交易日历人工核验。脚本没有读取
secret 值，也没有发出外部行情或 AI 请求。

在认证、复权因子、财务数据和人工交易日校验完成前，不得声称
AI Review ready，也不得生成可发布金融日报。后续操作顺序见
`docs/tickflow_real_data_ai_validation_runbook.md`。

## 9. 旁路系统与边界

本轮复验时容器 ID 保持：

| 系统 | 容器 ID | 状态 |
|---|---|---|
| n8n | `cf45d07e6f4e` | 未修改 |
| 独立 Gold Shadow | `59dcb35fa8db` | 未修改 |
| PostgreSQL | `0abaa309a13a` | 未修改 |

未接券商、实盘、自动交易、模拟交易、Telegram、OpenClaw 或 Gold
Stage B；未开放公网端口；未自动发布金融日报。

## 10. 收口结论与下一步

当前可确认的是：`494ee71` 云端私网运行身份、安全绑定、PWA
服务端链路、用户数据一致性和本地测试均有可复验结果。当前不能确认
的是远端 PR/CI 和真实安装端 cache 状态。

保持状态：

```text
P0_GOVERNANCE_PARTIAL
```

下一步只需：

1. 用户在本机完成 GitHub 登录后，用已生成材料创建 PR；
2. 核验远端 Actions 对最终 PR head SHA 真实通过；
3. 在实际 Mac/iPhone 安装端执行 PWA 手工清单；
4. 之后再由用户私下初始化认证和数据/AI Key，执行真实数据 runbook。

在以上动作前，不再重复部署，不开启 Gold，不接入主系统。

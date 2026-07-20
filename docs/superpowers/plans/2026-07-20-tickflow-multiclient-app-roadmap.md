# TickFlow Multi-Client App Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按可独立验收的三阶段，把 TickFlow 交付为手机 PWA、Intel Mac 私有 App，以及云端主库加本地计算的混合研究工作台。

**Architecture:** 云机是共享数据与密钥的唯一权威来源，手机从 Tailscale HTTPS 直接访问云端，Intel Mac App 通过本地 FastAPI 网关访问云端共享资源并优先执行本地计算。离线只允许读取带时间戳的缓存，不排队写入。

**Tech Stack:** FastAPI、React 18、Vite 5、TanStack Query、IndexedDB、vite-plugin-pwa、PyWebView、PyInstaller、macOS Keychain、Tailscale Serve、Docker Compose。

## Global Constraints

- 基线为 `tickflow-stock-panel` v0.1.86 + Gold Stage A hardening。
- 不接券商账户或交易接口，不做实盘、自动下单或分钟级交易系统。
- 不接入 OpenClaw 主链路，不开发 Telegram 推送。
- 不使用 Tailscale Funnel，不开放公网 3018/3019。
- `GOLD_WORKSPACE_ENABLED=false` 保持不变，不修改独立 Gold Shadow。
- 云机保存 TickFlow、Tushare、DeepSeek 完整 Key；客户端不得保存或回传完整 Key。
- 云端是共享状态唯一主库；离线只读，不实现离线写入或冲突合并。
- Intel Mac 首版只做私有 x86_64 DMG、ad-hoc 签名，不做 App Store、公证或自动更新。
- 个股复盘继续保持 `trading_advice: false`；`data_scope: watchlist-sample` 时 `can_publish: false`。
- 不自动发布正式金融日报。

---

## Plan Set

| 顺序 | 计划 | 独立交付物 | 进入下一阶段的硬门 |
|---:|---|---|---|
| 1 | [Phase 1: Private Mobile PWA](./2026-07-20-tickflow-private-mobile-pwa.md) | 手机可安装 PWA、8443 私网入口、离线只读缓存、移动导航 | PWA 真机流程、安全缓存白名单、无公网暴露全部通过 |
| 2 | [Phase 2: Intel Mac Private App](./2026-07-20-tickflow-intel-mac-private-app.md) | x86_64 `.app` 与 DMG、Application Support 数据目录、Keychain 云端会话 | 双击启动、原生库、单实例、关闭清理、覆盖升级全部通过 |
| 3 | [Phase 3: Cloud Workspace Sync](./2026-07-20-tickflow-cloud-workspace-sync.md) | ETag/revision、SSE、Mac 只读缓存、本地计算与云端降级 | 两端一致性、412 冲突、离线禁写、计算降级、泄露扫描全部通过 |

## Execution Order

### Task 1: 建立实施分支和基线证据

**Files:**
- Read: `docs/superpowers/specs/2026-07-20-tickflow-multiclient-app-design.md`
- Read: `reports/gold_stage_a_security_validation.json`
- Preserve: `reports/codex_handoff_openrun_20260720.md`

**Interfaces:**
- Consumes: 已批准的设计规范与 Gold Stage A 安全基线。
- Produces: 隔离 worktree、实施分支、基线测试记录。

- [ ] **Step 1: 创建隔离 worktree**

```bash
cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-gold-stage-a-hardening-v0.1.86"
git worktree add ../tickflow-multiclient-app -b codex/tickflow-multiclient-app 815224a
```

Expected: 新目录位于 `/Users/macbookpro/Documents/TradingView 量化/tickflow-multiclient-app`，原工作区未跟踪的 handoff 报告不会进入新 worktree。

- [ ] **Step 2: 运行安全基线**

```bash
cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-multiclient-app"
./scripts/verify_gold_stage_a.sh
```

Expected: 末行 `STAGE_A_READY`，当前已知基线为 `30 passed`。

- [ ] **Step 3: 固化实施顺序**

```bash
git status --short
git log -1 --oneline
```

Expected: 工作区干净，HEAD 为 `815224a docs: design private multi-client TickFlow app`。

### Task 2: 执行 Phase 1 并做阶段门禁

**Files:**
- Execute: `docs/superpowers/plans/2026-07-20-tickflow-private-mobile-pwa.md`
- Create during phase: `reports/pwa_acceptance/`

**Interfaces:**
- Consumes: 当前同源 FastAPI 前端和云机 `127.0.0.1:3019`。
- Produces: `https://vm-0-9-ubuntu.tail21c236.ts.net:8443` 私网 PWA。

- [ ] **Step 1: 逐任务执行 Phase 1 计划**

```bash
sed -n '1,260p' docs/superpowers/plans/2026-07-20-tickflow-private-mobile-pwa.md
```

Expected: 所有 checkbox 逐项完成，不批量跳过 RED/GREEN 验证。

- [ ] **Step 2: 运行 Phase 1 总门禁**

```bash
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
./scripts/verify_pwa_private_access.sh
```

Expected: 单元测试、构建、桌面/手机 Playwright 和私网暴露检查全部成功；443 上既有 8787 代理保持不变。

### Task 3: 执行 Phase 2 并做产物门禁

**Files:**
- Execute: `docs/superpowers/plans/2026-07-20-tickflow-intel-mac-private-app.md`
- Create during phase: `reports/macos_intel_acceptance/`

**Interfaces:**
- Consumes: Phase 1 前端外壳和 Intel x86_64 Python 原生依赖。
- Produces: `dist/TickFlowStockPanel-intel-x86_64.dmg`。

- [ ] **Step 1: 逐任务执行 Phase 2 计划**

```bash
sed -n '1,260p' docs/superpowers/plans/2026-07-20-tickflow-intel-mac-private-app.md
```

Expected: 数据目录、桌面生命周期、Keychain、打包和 DMG 任务全部完成。

- [ ] **Step 2: 运行 Phase 2 总门禁**

```bash
uv run --project backend pytest backend/tests/test_desktop_paths.py backend/tests/test_desktop_client_auth.py backend/tests/test_desktop_lifecycle.py -q
./scripts/build_macos_intel.sh
./scripts/verify_macos_intel_app.sh
```

Expected: 测试通过，`.app` 主二进制和 Polars/PyArrow/DuckDB 原生库均为 x86_64，签名验证成功，DMG 可挂载。

### Task 4: 执行 Phase 3 并做多端一致性门禁

**Files:**
- Execute: `docs/superpowers/plans/2026-07-20-tickflow-cloud-workspace-sync.md`
- Create during phase: `reports/multiclient_acceptance/`

**Interfaces:**
- Consumes: Phase 1 PWA 离线外壳、Phase 2 本地网关和 Keychain 远端会话。
- Produces: 云端 revision/ETag、SSE、Mac 缓存、计算路由和写回。

- [ ] **Step 1: 逐任务执行 Phase 3 计划**

```bash
sed -n '1,300p' docs/superpowers/plans/2026-07-20-tickflow-cloud-workspace-sync.md
```

Expected: 共享资源、冲突保护、事件流、客户端 adapter、缓存和计算任务全部完成。

- [ ] **Step 2: 运行 Phase 3 总门禁**

```bash
uv run --project backend pytest backend/tests/workspace backend/tests/test_desktop_workspace_adapter.py backend/tests/test_desktop_cloud_proxy.py backend/tests/test_compute_input_bundle.py backend/tests/test_compute_router.py -q
pnpm --dir frontend test
pnpm --dir frontend exec playwright test e2e/multiclient.spec.ts
./scripts/verify_multiclient_security.sh
```

Expected: 旧 revision 返回 412，离线写入被拒绝，Mac/手机自选股与报告一致，本地计算失败后仅受支持任务降级云端，密钥扫描为零命中。

### Task 5: 最终回归与交付

**Files:**
- Create: `reports/tickflow_multiclient_app_acceptance.md`
- Modify: `docs/runbook.md`

**Interfaces:**
- Consumes: 三个阶段的验收证据。
- Produces: 明确通过项、未通过项、回滚点和安装入口。

- [ ] **Step 1: 运行完整回归**

```bash
./scripts/verify_gold_stage_a.sh
uv run --project backend pytest backend/tests -q
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
```

Expected: 所有命令退出码 0；若仓库既有非本变更失败，报告必须列出准确测试名和与本功能的关系，不得写成“全部通过”。

- [ ] **Step 2: 人工核验红线**

```bash
ssh codex-vm 'ss -lntp | grep -E ":(3018|3019|8443|8787)[[:space:]]"; tailscale serve status; docker inspect TickFlow_Stock_Panel --format "{{range .Config.Env}}{{println .}}{{end}}" | grep GOLD_WORKSPACE_ENABLED'
```

Expected: 3018/3019 仅回环监听，8787 的 443 Serve 保留，TickFlow 使用 8443，Gold 为 false。

- [ ] **Step 3: 写最终报告并提交**

```bash
git add reports/tickflow_multiclient_app_acceptance.md docs/runbook.md
git commit -m "docs: add multi-client app acceptance runbook"
```

Expected: 报告包含 PWA URL、DMG SHA-256、测试计数、数据目录、回滚命令和仍需人工执行的步骤，不包含任何 Key、Cookie 或报告正文。

## Stop Conditions

- 任何步骤要求把 3018/3019 绑定到公网地址时立即停止。
- 任何客户端产物出现 TickFlow、Tushare、DeepSeek 完整 Key 时立即停止并轮换该 Key。
- 8443 配置会覆盖现有 443/8787 Serve 时立即停止，先恢复既有配置。
- Gold sampler 被启用、`external_send_count` 非零或独立 Shadow 被修改时立即停止并回滚本阶段。
- ETag 竞态测试未通过时，不允许开启 Mac 到云端的写操作。

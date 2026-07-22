# TickFlow 多端 App 总运行手册

## 1. 当前拓扑

```text
iPhone / Android PWA -- Tailscale HTTPS :8443 --+
                                                    +-- TickFlow cloud workspace :3019
Intel Mac App -------- Tailscale HTTPS :8443 -------+
       |                                            |
       +-- local cache / verified local compute ----+
```

- PWA：`https://vm-0-9-ubuntu.tail21c236.ts.net:8443/`
- 云端源码：`/home/ubuntu/tickflow-stock-panel-stage-a`
- 云端容器：`TickFlow_Stock_Panel`
- 云端监听：只允许 `127.0.0.1:3019`
- 现有 443 主入口继续指向 `127.0.0.1:8787`
- `WORKSPACE_SYNC_ENABLED=true`
- `GOLD_WORKSPACE_ENABLED=false`
- 不接 Telegram、券商、OpenClaw 主链路或自动交易

## 2. 日常启动

### 手机 PWA

1. 手机连接与云机相同的 Tailscale tailnet。
2. 用 Safari 或 Chrome 打开 PWA 地址。
3. iOS 使用“分享 -> 添加到主屏幕”；Android 使用“安装应用”。
4. 未初始化认证时，先按第 4 节人工设置密码。

### Intel Mac App

已安装位置：

```text
/Applications/TickFlowStockPanel.app
```

首次启动在 Finder 中右键选择“打开”。客户端连接页填写：

```text
https://vm-0-9-ubuntu.tail21c236.ts.net:8443
```

建议使用 `hybrid` 模式。云端会话保存到 macOS Keychain，URL 与模式保存到 Application Support；密码不写入配置文件。

用户数据与日志：

```text
~/Library/Application Support/TickFlowStockPanel/
~/Library/Application Support/TickFlowStockPanel/cache/workspace/
~/Library/Application Support/TickFlowStockPanel/compute_inputs/
~/Library/Application Support/TickFlowStockPanel/desktop.log
~/Library/Logs/TickFlowStockPanel/
```

## 3. 安装与校验 DMG

当前产物：

```text
dist/TickFlowStockPanel-intel-x86_64.dmg
SHA-256 66b9dfae51d1d757b0c302d5e2fc1407d2499b8cf4507fb7fe95a3dfda52aa15
```

校验：

```bash
shasum -a 256 dist/TickFlowStockPanel-intel-x86_64.dmg
hdiutil verify dist/TickFlowStockPanel-intel-x86_64.dmg
```

DMG 未使用 Apple Developer ID 公证。安装和升级细节见 `docs/macos-intel-private-runbook.md`。

## 4. 初始化应用密码

当前云端认证状态为 `configured=false`。密码必须由用户在私网内自行输入，不得发送到聊天或写入 Git。

先建立本地转发：

```bash
ssh -N -L 13019:127.0.0.1:3019 codex-vm
```

然后在本机浏览器访问 `http://127.0.0.1:13019/login` 完成初始化。完成后检查：

```bash
ssh codex-vm 'curl -fsS http://127.0.0.1:3019/api/auth/status'
```

预期 `configured` 为 `true`。不要在命令行中传入密码。

## 5. 云端升级

升级前必须创建数据与镜像回滚点，完整流程见 `docs/workspace-sync-runbook.md`。构建网络不稳定时使用官方 PyPI，保持 frozen lock：

```bash
cd /home/ubuntu/tickflow-stock-panel-stage-a
docker compose build \
  --build-arg PYPI_INDEX=https://pypi.org/simple \
  --build-arg PYPI_FALLBACK=https://pypi.org/simple \
  app
```

先用新镜像对正式数据卷做网络隔离、只读契约预检。通过后只重建应用容器：

```bash
PORT=3019 GOLD_WORKSPACE_ENABLED=false WORKSPACE_SYNC_ENABLED=true \
  docker compose up -d --no-build --force-recreate --no-deps app
```

不得使用会重建依赖服务的命令，也不得修改 443 Serve。

## 6. 运行态检查

```bash
curl -fsS http://127.0.0.1:3019/health
curl -fsS http://127.0.0.1:3019/api/auth/status
ss -lntH | grep -E '127.0.0.1:(3018|3019)[[:space:]]'
sudo tailscale serve status
docker inspect TickFlow_Stock_Panel \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^(AUTH_COOKIE_SECURE|GOLD_WORKSPACE_ENABLED|WORKSPACE_SYNC_ENABLED)='
```

预期：

- 3018 和 3019 均只在 loopback；
- 443 指向 8787，8443 指向 3019，二者均为 tailnet only；
- `AUTH_COOKIE_SECURE=true`；
- `WORKSPACE_SYNC_ENABLED=true`；
- `GOLD_WORKSPACE_ENABLED=false`。

若 `codex-vm` 的本机 SOCKS 代理不稳定，可临时对单条命令使用 `ssh -o ProxyCommand=none codex-vm ...`，不要永久删除用户的 SSH 配置。

## 7. 发布门禁

构建发布产物时使用一次性随机 canary，不使用真实 Key：

```bash
export TICKFLOW_SECRET_CANARY="TFCANARY_$(uuidgen | tr -d '-')"
./scripts/build_macos_intel.sh
./scripts/verify_macos_intel_app.sh
TICKFLOW_SSH_HOST=codex-vm ./scripts/verify_multiclient_security.sh
unset TICKFLOW_SECRET_CANARY
```

只有同时出现以下输出才可接受自动化范围：

```text
MACOS_INTEL_APP_OK
WORKSPACE_SIDECAR_CONTRACT_OK
MULTICLIENT_SECURITY_OK
STAGE_A_READY
```

Intel 构建会把 `backend/.venv` 收敛为发布依赖。构建后如需运行测试，按锁文件恢复开发依赖：

```bash
cd backend
uv sync --frozen --extra desktop --extra dev
```

## 8. 故障与回滚

- 写入异常：先设 `WORKSPACE_SYNC_ENABLED=false`，只重建应用容器。
- 镜像异常：使用已记录 rollback tag，不重建 n8n、Gold Shadow 或 Postgres。
- 数据异常：先运行只读 storage/revision 契约；只有确认损坏时才按原子流程恢复 `user_data.tgz`。
- Mac 异常：退出 App，保留 Application Support，用上一版已核验 DMG 覆盖安装。
- SSE 断开：客户端轮询 revision 恢复；412 不得自动重放。

当前云端回滚点：

```text
/home/ubuntu/tickflow-backups/20260722_130555-multiclient
tickflow-stock-panel:rollback-20260722-130555
```

完整回滚命令与 fail-closed 数据恢复流程见 `docs/workspace-sync-runbook.md`。

## 9. 人工验收

以下事项不能由自动化替代：

1. 真实 iPhone/Android 添加 PWA 到主屏幕并验证断网只读。
2. 真实密码登录、Mac App 重启会话恢复和退出登录。
3. Finder/Dock 红色图标与 DMG 拖放体验目视确认。

在这三项完成前，状态保持 `CONDITIONAL_PASS`，不视为公开发布版本。

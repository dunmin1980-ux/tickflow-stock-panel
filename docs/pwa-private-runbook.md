# TickFlow 手机 PWA 私网运行手册

## 边界

- 只通过 Tailscale HTTPS `8443` 访问 TickFlow PWA。
- 保留 `443 -> 127.0.0.1:8787`，不替换现有主系统入口。
- 应用容器只发布 `127.0.0.1:3019`，不开放公网端口。
- 保持 `GOLD_WORKSPACE_ENABLED=false`；不修改 Gold Shadow、Telegram、OpenClaw、n8n 或券商连接。
- 不把 API Key、密码、Cookie 或 Token 写入 Git、命令行参数或报告。

## 部署

1. 确认云机旧入口和本机绑定：

```bash
ssh codex-vm 'tailscale serve status; ss -lntp | grep -E ":(3018|3019|8787)[[:space:]]"'
```

2. 在云机旁路工作目录创建回滚标签，并从当前干净提交做全量构建。锁文件中的版本和 SHA-256 保持不变；国内构建将绝对 wheel URL 按腾讯云、清华、官方源依次切换：

```bash
ssh codex-vm 'set -euo pipefail
  cd /home/ubuntu/tickflow-stock-panel-stage-a
  test -z "$(git status --porcelain)"
  source_commit=$(git rev-parse HEAD)
  current_image=$(docker inspect TickFlow_Stock_Panel --format "{{.Image}}")
  rollback_tag="tickflow-stock-panel-stage-a-app:rollback-$(date +%Y%m%d-%H%M%S)"
  docker tag "$current_image" "$rollback_tag"
  docker image inspect "$rollback_tag" --format "rollback={{.Id}}"
  docker build \
    --build-arg USE_CN_MIRROR=1 \
    --build-arg PYPI_INDEX=https://mirrors.cloud.tencent.com/pypi/simple \
    --build-arg PYPI_FALLBACK=https://pypi.tuna.tsinghua.edu.cn/simple \
    --build-arg BACKEND_EXTRAS= \
    -t "tickflow-stock-panel-stage-a-app:full-${source_commit}" .
  docker tag "tickflow-stock-panel-stage-a-app:full-${source_commit}" \
    tickflow-stock-panel-stage-a-app:latest
  GOLD_WORKSPACE_ENABLED=false PORT=3019 docker compose up -d --no-build --force-recreate'
ssh codex-vm 'curl -fsS http://127.0.0.1:3019/health'
```

构建若无法通过 frozen lock 校验必须停止，不得改用非 frozen 解析，也不得复用来源不明的依赖基础镜像。

3. 新增 8443 Serve。该命令不得使用 `reset`：

```bash
ssh codex-vm 'tailscale serve --bg --https=8443 http://127.0.0.1:3019'
ssh codex-vm 'tailscale serve status'
```

4. 运行强制验证：

```bash
./scripts/verify_pwa_private_access.sh
```

只有输出 `PWA_PRIVATE_ACCESS_OK` 才视为私网入口就绪。

## 手机入网与安装

1. iPhone 或 Android 安装 Tailscale，加入同一 tailnet。
2. 首次部署如返回 `NOT_INITIALIZED`，用 SSH 本地转发打开设置页，由用户自行输入访问密码：

```bash
ssh -N -L 13019:127.0.0.1:3019 codex-vm
```

然后在本机浏览器访问 `http://127.0.0.1:13019/login`。密码不发送到聊天，不写入 runbook 或 Git。

3. 用 Safari/Chrome 访问 `https://vm-0-9-ubuntu.tail21c236.ts.net:8443/`，完成 TickFlow 登录。
4. iOS：分享 -> 添加到主屏幕。Android：浏览器菜单 -> 安装应用。
5. 验证自选股、个股分析、Review 在线读取、断网只读标识、离线禁写和恢复网络刷新。复盘正文不做持久离线缓存。

## 升级

```bash
ssh codex-vm 'cd /home/ubuntu/tickflow-stock-panel-stage-a && git pull --ff-only'
# 按“部署”第 2 步创建回滚标签并做全量构建
./scripts/verify_pwa_private_access.sh
```

升级前后都保存 `tailscale serve status` 和 `ss -lntp` 证据。如出现登录、横向溢出、离线写入或 443 丢失，立即回滚。

## 回滚

```bash
ssh codex-vm 'docker tag tickflow-stock-panel-stage-a-app:rollback-pwa-incremental-20260721 \
  tickflow-stock-panel-stage-a-app:latest && \
  cd /home/ubuntu/tickflow-stock-panel-stage-a && \
  GOLD_WORKSPACE_ENABLED=false PORT=3019 docker compose up -d --no-build --force-recreate'
ssh codex-vm 'tailscale serve status'
```

回滚只替换 3019 的应用镜像，不修改 443/8443 Serve。回滚后确认 443 仍指向 8787、8443 仍指向 3019，且 3019 只监听 `127.0.0.1`。不得停止 `TickFlow_Gold_Shadow`。

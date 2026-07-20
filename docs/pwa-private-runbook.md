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

2. 在云机旁路工作目录构建。仅从本地已验收提交生成无密钥源码包，不覆盖独立 Gold 目录。正常情况使用全量构建：

```bash
ssh codex-vm 'cd /home/ubuntu/tickflow-stock-panel-stage-a && \
  GOLD_WORKSPACE_ENABLED=false PORT=3019 docker compose up -d --build'
ssh codex-vm 'curl -fsS http://127.0.0.1:3019/health'
```

若 Docker Hub/PyPI 超时，且已用 `git diff <base>..HEAD -- backend/pyproject.toml backend/uv.lock` 证明 Python 依赖文件零差异，可使用受控增量构建：

```bash
ssh codex-vm 'docker tag tickflow-stock-panel-stage-a-app:latest \
  tickflow-stock-panel-stage-a-app:pwa-dependency-base'
ssh codex-vm 'cd /home/ubuntu/tickflow-stock-panel-stage-a && \
  docker build -f deploy/Dockerfile.pwa-incremental \
    --build-arg BASE_IMAGE=tickflow-stock-panel-stage-a-app:pwa-dependency-base \
    -t tickflow-stock-panel-stage-a-app:latest . && \
  GOLD_WORKSPACE_ENABLED=false PORT=3019 docker compose up -d --no-build --force-recreate'
```

依赖文件有任何差异时禁止使用增量镜像，必须回到全量构建。

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
2. 用 Safari/Chrome 访问 `https://vm-0-9-ubuntu.tail21c236.ts.net:8443/`。
3. 完成 TickFlow 访问密码登录，密码不保存到文档。
4. iOS：分享 -> 添加到主屏幕。Android：浏览器菜单 -> 安装应用。
5. 验证自选股、个股分析、Review 在线读取、断网只读标识、离线禁写和恢复网络刷新。复盘正文不做持久离线缓存。

## 升级

```bash
ssh codex-vm 'cd /home/ubuntu/tickflow-stock-panel-stage-a && \
  GOLD_WORKSPACE_ENABLED=false PORT=3019 docker compose up -d --build'
./scripts/verify_pwa_private_access.sh
```

升级前后都保存 `tailscale serve status` 和 `ss -lntp` 证据。如出现登录、横向溢出、离线写入或 443 丢失，立即回滚。

## 回滚

```bash
ssh codex-vm 'tailscale serve --https=8443 off'
ssh codex-vm 'tailscale serve --bg --https=443 http://127.0.0.1:8787'
ssh codex-vm 'tailscale serve status'
```

回滚后再确认 443 仍指向 8787，3019 仍只监听 `127.0.0.1`。停用旁路容器不得停止 `TickFlow_Gold_Shadow`。

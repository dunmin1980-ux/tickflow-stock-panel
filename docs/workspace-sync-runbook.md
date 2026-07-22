# TickFlow 多端工作区运行手册

## 1. 运行边界

- 云端是自选股、允许共享的偏好、个股复盘元数据、市场复盘元数据和回测摘要的唯一主库。
- 手机仅通过 Tailscale HTTPS `:8443` 访问；3018/3019 不允许公网监听。
- Mac App 断网时只读缓存，不排队写入；完整性失败不得降级为云端重算。
- `GOLD_WORKSPACE_ENABLED=false` 必须保持不变；不接 Telegram、券商、OpenClaw 主链路或自动交易。

## 2. Feature Gate

Compose 必须显式传入：

```bash
PORT=3019 GOLD_WORKSPACE_ENABLED=false WORKSPACE_SYNC_ENABLED=false docker compose up -d
```

兼容客户端和只读接口验收通过后，才允许切换：

```bash
PORT=3019 GOLD_WORKSPACE_ENABLED=false WORKSPACE_SYNC_ENABLED=true docker compose up -d --force-recreate
```

运行态以容器环境为准，不以 shell 或 `.env` 推断：

```bash
docker inspect TickFlow_Stock_Panel \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^(PORT|AUTH_COOKIE_SECURE|GOLD_WORKSPACE_ENABLED|WORKSPACE_SYNC_ENABLED)='
```

## 3. 部署前备份

备份只保留在云机，目录权限固定为 0700；不得把 `.env`、Cookie 或 Key 拉回报告目录。

```bash
cd /home/ubuntu/tickflow-stock-panel-stage-a
stamp="$(date +%Y%m%d_%H%M%S)"
backup="$HOME/tickflow-backups/$stamp"
install -d -m 700 "$backup"
git rev-parse HEAD > "$backup/source_commit.txt"
image_id="$(docker inspect TickFlow_Stock_Panel --format '{{.Image}}')"
rollback_tag="tickflow-stock-panel-stage-a-app:rollback-$stamp"
printf '%s\n' "$image_id" > "$backup/image_id.txt"
printf '%s\n' "$rollback_tag" > "$backup/rollback_image_tag.txt"
tar -C data -czf "$backup/user_data.tgz" user_data
chmod 600 "$backup"/*
docker image tag "$image_id" "$rollback_tag"
printf 'backup=%s\n' "$backup"
```

## 4. 两阶段启用

### 阶段 A：兼容部署，写入关闭

```bash
cd /home/ubuntu/tickflow-stock-panel-stage-a
git fetch https://github.com/dunmin1980-ux/tickflow-stock-panel.git \
  codex/tickflow-multiclient-app
git checkout -B codex/multiclient FETCH_HEAD
PORT=3019 GOLD_WORKSPACE_ENABLED=false WORKSPACE_SYNC_ENABLED=false \
  docker compose up -d --build --force-recreate
```

验收：

```bash
curl -fsS http://127.0.0.1:3019/health
curl -sS -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:3019/api/workspace/bootstrap
ss -lnt | grep -E '127.0.0.1:3019[[:space:]]'
```

未登录的 workspace 请求应返回 401 或尚未初始化时的受控认证状态；不得返回 500。旧页面、PWA 静态资源和只读 API 必须正常。

### 阶段 B：启用版本化写入

```bash
cd /home/ubuntu/tickflow-stock-panel-stage-a
PORT=3019 GOLD_WORKSPACE_ENABLED=false WORKSPACE_SYNC_ENABLED=true \
  docker compose up -d --force-recreate
```

必须确认容器环境中 `WORKSPACE_SYNC_ENABLED=true`，并使用已认证的临时会话验证：

1. `GET /api/workspace/resources/watchlist` 读取 ETag。
2. `POST .../commands` 携带同一 `If-Match` 时成功一次。
3. 再用旧 ETag 写入必须返回 412。
4. 验收写入应使用临时标的并立即恢复备份，或在隔离 sidecar 数据目录完成，不污染正式自选股。

## 5. 412 处理

- 412 表示客户端 revision 已过期，不是网络重试条件。
- 客户端刷新对应资源并显示人工确认；不得自动重放命令。
- 回测摘要冲突只允许重新提交 `pending_summary`，不得重新执行回测。
- 连续 412 时检查是否有旧客户端、第二容器或脚本绕过 ETag 合同。

## 6. SSE 诊断

```bash
curl -N --cookie "$COOKIE_HEADER" \
  https://vm-0-9-ubuntu.tail21c236.ts.net:8443/api/workspace/events
docker logs --since 10m TickFlow_Stock_Panel 2>&1 \
  | grep -E 'workspace|events|revision|412'
sudo tailscale serve status
```

SSE 断开后客户端按 revisions 轮询恢复。401 应清除离线授权缓存；不得把认证失败误判为网络离线。

## 7. Mac 缓存与计算降级

- 工作区缓存：`~/Library/Application Support/TickFlowStockPanel/cache/workspace/`
- 计算输入：`~/Library/Application Support/TickFlowStockPanel/compute_inputs/`
- 桌面日志：`~/Library/Application Support/TickFlowStockPanel/desktop.log`
- 会话只存 macOS Keychain；缓存 DTO 不含 Key、Token、Cookie、Webhook 或报告正文。
- 仅云端不可达、下载超时、worker 启动失败、原生库加载失败或本地 repo 初始化失败可降级一次。
- checksum、size、path、symlink、参数 digest、数据日期或覆盖范围失败必须 fail closed。

## 8. 安全门禁

使用一次性测试字符串，不得使用真实 Key：

```bash
export TICKFLOW_SECRET_CANARY="TFCANARY_$(uuidgen | tr -d '-')"
./scripts/build_macos_intel.sh
./scripts/verify_macos_intel_app.sh
TICKFLOW_SSH_HOST=ubuntu@43.160.228.237 \
  ./scripts/verify_multiclient_security.sh
unset TICKFLOW_SECRET_CANARY
```

构建脚本在全部产物成功后写入 canary SHA-256 收据；安全门禁只接受同一 canary 构建的产物。随后它会在临时 Application Support 中把 canary 注入 TickFlow、Tushare、AI 和 Webhook 测试源，挂载 DMG 扫描解压内容，并在云机启动独立的随机回环端口 sidecar。sidecar 使用隔离数据目录验证认证、SSE、200/412/428、旧写接口 409、Gold 零外发及响应/日志脱敏，结束后自动删除；真实凭据和正式自选股均不被读取或修改。

## 9. 回滚

任何端口、认证、旧客户端、SSE、412、数据完整性或安全扫描失败，先关闭写入：

```bash
cd /home/ubuntu/tickflow-stock-panel-stage-a
PORT=3019 GOLD_WORKSPACE_ENABLED=false WORKSPACE_SYNC_ENABLED=false \
  docker compose up -d --force-recreate
```

需要回退镜像时：

```bash
# 用部署时打印并记录的明确目录替换下面占位符，不要猜测时间戳。
backup="$HOME/tickflow-backups/YYYYMMDD_HHMMSS"
rollback_tag="$(cat "$backup/rollback_image_tag.txt")"
docker image tag "$rollback_tag" \
  tickflow-stock-panel-stage-a-app
cd /home/ubuntu/tickflow-stock-panel-stage-a
PORT=3019 GOLD_WORKSPACE_ENABLED=false WORKSPACE_SYNC_ENABLED=false \
  docker compose up -d --no-build --force-recreate
```

只有确认正式工作区文件损坏时才恢复 `user_data.tgz`。使用明确备份目录，在同一文件系统准备恢复副本，停服务后原子替换，并保留故障现场：

```bash
set -Eeuo pipefail
cd /home/ubuntu/tickflow-stock-panel-stage-a
backup="$HOME/tickflow-backups/YYYYMMDD_HHMMSS"
restore_id="$(date +%Y%m%d_%H%M%S)"
restore_tmp="data/.user_data.restore-$restore_id"
failed_copy="data/user_data.failed-$restore_id"
rejected_copy="data/user_data.rejected-$restore_id"
old_moved=false
service_touched=false

workspace_contract() {
  local image
  image="$(docker inspect TickFlow_Stock_Panel --format '{{.Image}}')" || return 1
  docker run -i --rm --network none --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --volumes-from TickFlow_Stock_Panel:ro \
    -e DATA_DIR=/app/data \
    -e WORKSPACE_SYNC_ENABLED=true \
    -e GOLD_WORKSPACE_ENABLED=false \
    "$image" /app/.venv/bin/python - <<'PY'
from app.api.workspace import _DATA_MODELS
from app.config import settings
from app.workspace.models import ResourceName
from app.workspace.registry import snapshot_resource
from app.workspace.revision import revision_for
from app.workspace.storage_validation import validate_storage_files

validate_storage_files(settings.data_dir)
for resource in ResourceName:
    snapshot = snapshot_resource(resource)
    _DATA_MODELS[resource].model_validate(snapshot.data)
    if snapshot.revision != revision_for(snapshot.data):
        raise SystemExit(1)
print("WORKSPACE_RESTORE_CONTRACT_OK")
PY
}

rollback_fail() {
  local reason="$1"
  local running
  printf 'ROLLBACK_FAILED: %s; forcing app stop\n' "$reason" >&2
  docker stop TickFlow_Stock_Panel >/dev/null 2>&1 || \
    docker kill TickFlow_Stock_Panel >/dev/null 2>&1 || true
  if ! running="$(docker inspect TickFlow_Stock_Panel --format '{{.State.Running}}' 2>/dev/null)"; then
    printf 'ROLLBACK_EMERGENCY: unable to confirm app state; isolate the host manually\n' >&2
    exit 91
  fi
  if [[ "$running" != false ]]; then
    printf 'ROLLBACK_EMERGENCY: app state is %s; isolate the host manually\n' "$running" >&2
    exit 91
  fi
  printf 'ROLLBACK_SERVICE_STOPPED: manual recovery required\n' >&2
  exit 90
}

restore_original() {
  rc=$?
  trap - ERR
  set +e
  if [[ "$service_touched" == true ]]; then
    PORT=3019 GOLD_WORKSPACE_ENABLED=false WORKSPACE_SYNC_ENABLED=false \
      docker compose stop app || rollback_fail "unable to stop app"
    if [[ "$old_moved" == true && -d "$failed_copy" ]]; then
      if [[ -d data/user_data ]]; then
        mv data/user_data "$rejected_copy" || \
          rollback_fail "unable to preserve rejected restored data"
      fi
      mv "$failed_copy" data/user_data || \
        rollback_fail "unable to restore original user_data"
    elif [[ "$old_moved" == true ]]; then
      rollback_fail "original user_data copy is missing"
    fi
    PORT=3019 GOLD_WORKSPACE_ENABLED=false WORKSPACE_SYNC_ENABLED=false \
      docker compose up -d --no-build --force-recreate || \
        rollback_fail "unable to restart rollback image"
    curl -fsS http://127.0.0.1:3019/health >/dev/null || \
      rollback_fail "rollback health check failed"
    workspace_contract >/dev/null || \
      rollback_fail "rollback workspace contract failed"
  fi
  exit "$rc"
}
trap restore_original ERR

test -f "$backup/user_data.tgz"
test ! -e "$restore_tmp"
test ! -e "$failed_copy"
test ! -e "$rejected_copy"
install -d -m 700 "$restore_tmp"
tar -xzf "$backup/user_data.tgz" -C "$restore_tmp"
test -d "$restore_tmp/user_data"

service_touched=true
PORT=3019 GOLD_WORKSPACE_ENABLED=false WORKSPACE_SYNC_ENABLED=false \
  docker compose stop app
mv data/user_data "$failed_copy"
old_moved=true
mv "$restore_tmp/user_data" data/user_data
chmod -R u=rwX,go= data/user_data
PORT=3019 GOLD_WORKSPACE_ENABLED=false WORKSPACE_SYNC_ENABLED=false \
  docker compose up -d --no-build --force-recreate
curl -fsS http://127.0.0.1:3019/health >/dev/null
workspace_contract | grep -Fxq 'WORKSPACE_RESTORE_CONTRACT_OK'
trap - ERR
```

任一移动、权限修复、重启、健康检查、原始 JSON/认证文件结构或五类 workspace DTO 校验失败都会触发 `restore_original`：停止 `app`，将失败恢复件留在 `$rejected_copy`，原子移回 `$failed_copy`，并以关闭 workspace 写入的回滚镜像重启。回滚任一步失败都会再次停止 `TickFlow_Stock_Panel` 并核验容器状态；只有确认未运行后才输出 `ROLLBACK_SERVICE_STOPPED`。不要在容器运行时覆盖数据库或 JSON 存储。

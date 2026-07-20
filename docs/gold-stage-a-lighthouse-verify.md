# 腾讯云轻量机：Gold Stage A 绑定验收清单

本地 `./scripts/verify_gold_stage_a.sh` 已通过（文件级 loopback + 安全单测）。  
**上云启用 Gold 前**必须完成本清单，把 `compose_runtime_ss_verified` 从 false 打成 true。

## 原则

- **不**与现有独立 `TickFlow_Gold_Shadow` 双开采样抢同一 TickFlow Pro 账户。
- **不**把 3018/5678 绑到 `0.0.0.0`。
- **不**在未确认前设 `GOLD_WORKSPACE_ENABLED=true`。
- 远程只走 Tailscale / SSH Tunnel。

## A. 部署本加固分支（二选一）

### A1. 源码目录（推荐先验证）

```bash
# 在轻量机上
cd /path/to/tickflow-gold-stage-a-hardening-v0.1.86   # 或 git clone/fetch 本分支
git rev-parse --short HEAD   # 期望含 75e1ede / 7b81966
cp -n .env.example .env && chmod 600 .env
# 确认：
grep GOLD_WORKSPACE_ENABLED .env    # 必须 false
grep -n '127.0.0.1' docker-compose.yml
```

### A2. 勿直接覆盖正在跑的 Stage A Shadow 数据目录

独立 Shadow 的数据与本整合版 `data/user_data/gold_shadow` **分开**；不要混挂同一路径给两套进程写。

## B. Compose 启动（Gold 关闭）

```bash
docker compose config | grep -A3 ports
# 必须出现: 127.0.0.1:…:3018

docker compose up -d --build
```

## C. 绑定实测（必做）

```bash
ss -lntp | grep 3018 || sudo ss -lntp | grep 3018
# 期望仅: 127.0.0.1:3018
# 不得出现: 0.0.0.0:3018 或 *:3018 或 [::]:3018

curl -fsS http://127.0.0.1:3018/health
# 期望 HTTP 200 / 健康 JSON

# 反向抽查：从公网 IP 访问应失败（在另一台机器或本机用公网 IP）
# curl -m 3 http://<公网IP>:3018/health  → 应超时/拒绝
```

通过后在本地报告中记录：

```text
compose_runtime_ss_verified=true
host=<轻量机标识>
head=<短 SHA>
checked_at=<ISO8601>
```

## D. 与现有 Gold Shadow 共存规则

| 场景 | 做法 |
|------|------|
| Shadow Stage A 仍在计数交易日 | **不要**开整合版 `GOLD_WORKSPACE_ENABLED=true` |
| 只想验收面板 + loopback | 整合版 Gold=false，Shadow 照旧 |
| 准备迁移到整合版 | 先停 Shadow 采样 → 再开整合版 Gold（单 writer） |

## E. 启用 Gold（仅当 C 通过且无双开）

1. 平台录入 TickFlow Key（UI，勿写入 git）  
2. 按 `docs/gold-integrated-research-runbook.md` 准备 `holidays.json`  
3. `.env`：`GOLD_WORKSPACE_ENABLED=true`  
4. `docker compose up -d`  
5. 打开 `/gold`，确认零外发 / 固定 `600489.SH`  
6. **禁止**同时跑 `probe_tickflow_pro.py --live`

## F. 回滚

```bash
# 关 Gold
# .env → GOLD_WORKSPACE_ENABLED=false
docker compose up -d

# 或整栈停下（不影响独立 Shadow，若未共用目录）
docker compose down
```

## G. 完成后回传（可脱敏）

请回传这几行（不要贴 Key）：

```text
HEAD=
ss_3018=
health_http=
GOLD_WORKSPACE_ENABLED=
shadow_still_running=yes|no
```

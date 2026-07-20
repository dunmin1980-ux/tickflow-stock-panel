# TickFlow Cloud Workspace Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让云端成为 Mac 与手机共享状态的唯一主库，并为 Intel Mac 提供只读缓存、本地计算优先、受控云端降级和结果写回。

**Architecture:** 每类共享资源以规范化 JSON 的 SHA-256 作为 revision/ETag，所有命令在同一资源锁内校验 `If-Match` 后调用现有原子存储。客户端通过 bootstrap + SSE 自动刷新，SSE 断开后轮询 revisions；Mac adapter 只缓存成功读取，断网禁止写入。计算由本地 worker 优先执行，仅明确的本地基础设施故障降级到云端。

**Tech Stack:** FastAPI、Pydantic 2、sse-starlette、threading RLock、JSON/Parquet 原子写、httpx、React/TanStack Query、IndexedDB、macOS Application Support。

## Global Constraints

- 云端是自选股、允许共享的偏好、个股复盘、AI Review 和回测摘要的唯一权威来源。
- 首版保留现有 JSON/Parquet，不引入新数据库或文件双向复制。
- revision 是规范化内容 SHA-256；写入在资源锁内重新计算并校验 `If-Match`。
- 缺少 `If-Match` 返回 428；旧 revision 返回 412；业务冲突返回 409。
- 412 后客户端刷新并要求人工重新确认，不自动重放写请求。
- 离线只读，不保存待同步命令，不实现双向冲突合并。
- 完整 API Key、Webhook、Cookie、认证响应和私密正文不得进入客户端缓存或日志。
- 本地计算只处理不需要完整 Key 的策略、回测和临时指标；数据源和 AI 请求强制云端执行。
- 本地计算故障仅对受支持任务降级；参数错误、能力拒绝、429 和业务错误不降级。
- `GOLD_WORKSPACE_ENABLED=false`，不修改 Gold Shadow、n8n、Telegram 或 OpenClaw。

## Mandatory Security Corrections

以下修正是后续任务的硬门，与任务步骤冲突时以本节为准：

1. **Task 2/4/6/9 - 客户偏好脱敏：** `GET /api/settings/preferences` 的客户响应必须在服务端源头排除 Webhook URL/secret、bot id/secret 和所有含 `secret/token/key/password/cookie` 的字段，只能用 `has_*` 布尔值表示是否已配置。Mac 端偏好读取只走 workspace 脱敏 DTO；桌面代理明确拒绝敏感 settings/key/webhook 子路由。必须有响应全文泄漏测试。
2. **Task 3/4 - 禁止旧写路由绕过 revision：** 当 `WORKSPACE_SYNC_ENABLED=true` 时，自选股、共享偏好、个股报告、市场复盘和回测摘要的所有 legacy mutation 必须要么带 `If-Match` 转发 `execute_command`，要么返回明确的迁移错误；不允许继续直写文件。测试覆盖每一个 legacy 共享写端点，并断言 gate 关闭时无写入、gate 开启时无无条件写。
3. **Task 6 - 桌面代理默认拒绝：** 不使用宽泛 prefix 分类。以 HTTP method + 规范化 exact path 白名单决定本地执行，首版仅允许已实现和测试的 `/api/backtest/strategy/stream`、`/api/backtest/strategy/run`、`/api/screener/run`、`/api/client/*` 和 `/api/workspace/*`。其他 `/api/*` 要么代理云端，要么显式拒绝；不存在的 `/api/strategy`/`/api/signals` 不得出现在契约中。通过 FastAPI 已注册路由生成合同测试。
4. **Task 7/8 - 完整性失败必须 fail closed：** 只有云端不可达、下载超时、worker 启动失败、原生库加载失败可转换为 `LocalComputeUnavailable` 并最多降级一次。manifest/task 不匹配、规范参数 digest 不匹配、checksum/size/path/symlink 失败、`X-Bundle-SHA256` 不匹配、`X-Data-As-Of` 与 manifest 不匹配或数据过期都是 `ComputeInputIntegrityError`，必须中止且不调用云端计算。
5. **Task 6/9 - 客户持久缓存不含复盘正文：** bootstrap 和 Mac/PWA 持久缓存只保存报告脱敏元数据（id、symbol、title、created_at、data_as_of、verification/can_publish/trading_advice），不含 Markdown `content`。正文通过独立认证 GET 在线读取，`Cache-Control: private, no-store`，且不写 IndexedDB/Application Support。
6. **Task 4/10 - Compose 真实传入 gate：** `docker-compose.yml` 必须增加 `WORKSPACE_SYNC_ENABLED: ${WORKSPACE_SYNC_ENABLED:-false}`。验收时检查运行容器 env、`/api/workspace/bootstrap` 和一次带 `If-Match` 的写入，不得只根据 Compose shell 环境宣称已启用。
7. **Task 10 - 安全门禁不允许 grep 错误假通过：** 先断言所有待扫描产物存在；用测试专用唯一 canary 值注入每类云端密钥源，再扫描 PWA、DMG/App、Application Support 缓存和日志确认 canary 零命中。`grep` 本身错误必须使脚本失败。

---

## File Map

- Create: `backend/app/workspace/{__init__,models,revision,locks,registry,events,commands}.py`
- Create: `backend/app/api/workspace.py`
- Create: `backend/app/services/backtest_summaries.py`, `backend/app/services/compute_router.py`
- Create: `backend/app/desktop_client/cache.py`, `backend/app/desktop_client/workspace_adapter.py`
- Create: `backend/app/desktop_client/proxy.py`, `backend/app/services/compute_input_bundle.py`
- Create: `backend/tests/workspace/test_revision.py`, `test_commands.py`, `test_api.py`, `test_events.py`
- Create: `backend/tests/test_desktop_workspace_adapter.py`, `backend/tests/test_desktop_cloud_proxy.py`, `backend/tests/test_compute_input_bundle.py`, `backend/tests/test_compute_router.py`
- Create: `frontend/src/lib/{workspace,etagStore,useWorkspaceEvents}.ts`
- Create: `frontend/src/lib/__tests__/{workspaceConflict,useWorkspaceEvents}.test.ts`
- Create: `frontend/e2e/multiclient.spec.ts`
- Create: `scripts/verify_multiclient_security.sh`, `docs/workspace-sync-runbook.md`
- Modify: `backend/app/main.py`, `backend/app/config.py`, `backend/app/services/watchlist.py`, `preferences.py`, `json_report_store.py`
- Modify: `backend/app/api/watchlist.py`, `stock_analysis.py`, `market_recap.py`, `backtest.py`, `screener.py`
- Modify: `frontend/src/lib/api.ts`, `useSharedQueries.ts`, `useSharedMutations.ts`, `main.tsx`, `components/Layout.tsx`
- Modify: `frontend/src/pages/Backtest.tsx`, `Watchlist.tsx`, `StockAnalysis.tsx`, `Review.tsx`

### Task 1: Revision、ETag 与资源锁基础

**Files:**
- Create: `backend/app/workspace/models.py`
- Create: `backend/app/workspace/revision.py`
- Create: `backend/app/workspace/locks.py`
- Test: `backend/tests/workspace/test_revision.py`

**Interfaces:**
- Produces: `ResourceName`, `ResourceSnapshot`, `canonical_bytes(data)`, `revision_for(data)`, `etag_for(revision)`, `parse_if_match(value)`, `resource_lock(name)`。

- [ ] **Step 1: 写 revision 失败测试**

```python
from app.workspace.revision import etag_for, parse_if_match, revision_for


def test_revision_is_order_independent():
    assert revision_for({"b": 2, "a": 1}) == revision_for({"a": 1, "b": 2})


def test_etag_round_trip():
    revision = "a" * 64
    assert etag_for(revision) == f'"{revision}"'
    assert parse_if_match(f'"{revision}"') == revision


def test_non_finite_number_is_rejected():
    with pytest.raises(ValueError):
        revision_for({"close": float("nan")})
```

- [ ] **Step 2: 运行 RED**

```bash
uv run --project backend pytest backend/tests/workspace/test_revision.py -q
```

- [ ] **Step 3: 实现模型和 revision**

```python
class ResourceName(StrEnum):
    WATCHLIST = "watchlist"
    PREFERENCES = "preferences"
    STOCK_REPORTS = "stock_reports"
    MARKET_RECAPS = "market_recaps"
    BACKTEST_SUMMARIES = "backtest_summaries"

class ResourceSnapshot(BaseModel):
    resource: ResourceName
    revision: str
    updated_at: datetime
    data: dict[str, Any]

class WorkspacePreconditionRequired(RuntimeError):
    pass

class WorkspaceRevisionConflict(RuntimeError):
    def __init__(self, resource: ResourceName, current_revision: str) -> None:
        super().__init__(f"stale revision for {resource}")
        self.resource = resource
        self.current_revision = current_revision
```

```python
def canonical_bytes(data: Any) -> bytes:
    encoded = jsonable_encoder(data)
    return json.dumps(encoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

def revision_for(data: Any) -> str:
    return hashlib.sha256(canonical_bytes(data)).hexdigest()

def etag_for(revision: str) -> str:
    return f'"{revision}"'

def parse_if_match(value: str | None) -> str | None:
    if value is None: return None
    value = value.strip()
    return value[1:-1] if value.startswith('"') and value.endswith('"') else value
```

- [ ] **Step 4: 实现资源锁**

`locks.py` 按 `ResourceName` 预建 `threading.RLock`，`resource_lock(name)` 返回同一实例。禁止 endpoint lock 和 store lock 使用不同对象。

- [ ] **Step 5: 运行 GREEN 并提交**

```bash
uv run --project backend pytest backend/tests/workspace/test_revision.py -q
git add backend/app/workspace backend/tests/workspace/test_revision.py
git commit -m "feat: add workspace revision primitives"
```

### Task 2: 原子共享资源注册表

**Files:**
- Create: `backend/app/workspace/registry.py`
- Modify: `backend/app/services/watchlist.py`
- Modify: `backend/app/services/preferences.py`
- Modify: `backend/app/api/settings.py`
- Modify: `backend/app/services/json_report_store.py`
- Create: `backend/app/services/backtest_summaries.py`
- Test: `backend/tests/workspace/test_commands.py`

**Interfaces:**
- Produces: `load_resource(name)`, `snapshot_resource(name)`, `replace_watchlist(rows)`, `load_client_preferences()`, `BacktestSummaryStore`。

- [ ] **Step 1: 写原子写和脱敏失败测试**

```python
def test_client_preferences_exclude_notification_secrets(monkeypatch):
    monkeypatch.setattr(preferences, "load", lambda: {
        "nav_order": ["watchlist"],
        "feishu_webhook_secret": "secret",
        "wecom_bot_secret": "secret2",
    })
    assert load_client_preferences() == {"nav_order": ["watchlist"]}

def test_watchlist_replace_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    watchlist.replace_all([{"symbol": "000403.SZ", "added_at": "2026-07-20T16:00:00", "note": ""}])
    assert watchlist.list_symbols()[0]["symbol"] == "000403.SZ"
    assert not list((tmp_path / "user_data").glob("*.tmp"))
```

- [ ] **Step 2: 运行 RED**

```bash
uv run --project backend pytest backend/tests/workspace/test_commands.py -q
```

- [ ] **Step 3: 原子化 watchlist**

新增 `_atomic_write(df)`，写 `watchlist.parquet.tmp` 后 `os.replace`。`add/remove/move_to_top/clear/replace_all` 全部在 `resource_lock(ResourceName.WATCHLIST)` 内读改写；`replace_all` 校验 symbol 唯一、非空并保持顺序。

- [ ] **Step 4: 固化客户端偏好白名单**

`load_client_preferences()` 只返回：

```python
CLIENT_PREFERENCE_KEYS = {
    "indices_nav_pinned", "watchlist_columns", "screener_result_columns",
    "sidebar_index_symbols", "nav_order", "nav_hidden", "screener_auto_run",
    "daily_data_provider", "adj_factor_provider", "minute_data_provider",
    "realtime_data_provider", "financial_data_provider",
}
```

`merge_client_preferences(updates)` 拒绝白名单外 key。Webhook、bot、system notification 和任何含 `secret/token/key/password/cookie` 的 key 不返回、不接受。

`GET /api/settings/preferences` 也必须复用同一脱敏输出器，不再返回 Webhook URL、bot id 或 secret 原值；前端如需显示配置状态，仅使用 `has_feishu_webhook`、`has_wecom_bot` 等布尔值。测试必须对整个 JSON 文本扫描真实 canary 值，不只检查字段名。

- [ ] **Step 5: 统一报告资源锁**

`JsonReportStore` 构造函数新增 `resource_name: ResourceName`，使用 `resource_lock(resource_name)` 取代实例私锁。`stock_reports`、`market_recap_reports` 和新增 `backtest_summaries` 分别绑定对应资源。

`BacktestSummaryStore` 每条只保存：`id, task, strategy_id, parameters_digest, stats, started_at, finished_at, data_as_of, engine, execution_target`；不得保存全量交易明细或密钥。

- [ ] **Step 6: 实现注册表**

```python
LOADERS = {
    ResourceName.WATCHLIST: lambda: {"symbols": watchlist.list_symbols()},
    ResourceName.PREFERENCES: lambda: {"preferences": preferences.load_client_preferences()},
    ResourceName.STOCK_REPORTS: lambda: {"reports": stock_reports.list_report_metadata()},
    ResourceName.MARKET_RECAPS: lambda: {"reports": market_recap_reports.list_report_metadata()},
    ResourceName.BACKTEST_SUMMARIES: lambda: {"summaries": backtest_summaries.list_summaries()},
}

def snapshot_resource(name: ResourceName) -> ResourceSnapshot:
    with resource_lock(name):
        data = LOADERS[name]()
        return ResourceSnapshot(resource=name, revision=revision_for(data), updated_at=resource_mtime(name), data=data)
```

`resource_mtime` 取对应文件 mtime；文件不存在时使用 Unix epoch，不能用“当前时间”导致无内容变化也变 revision：

```python
RESOURCE_PATHS = {
    ResourceName.WATCHLIST: lambda: settings.data_dir / "user_data/watchlist.parquet",
    ResourceName.PREFERENCES: lambda: settings.data_dir / "user_data/preferences.json",
    ResourceName.STOCK_REPORTS: lambda: settings.data_dir / "user_data/ai_stock_reports.json",
    ResourceName.MARKET_RECAPS: lambda: settings.data_dir / "user_data/ai_market_recaps.json",
    ResourceName.BACKTEST_SUMMARIES: lambda: settings.data_dir / "user_data/backtest_summaries.json",
}

def resource_mtime(name: ResourceName) -> datetime:
    path = RESOURCE_PATHS[name]()
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if path.exists() else datetime.fromtimestamp(0, timezone.utc)
```

- [ ] **Step 7: 运行 GREEN 并提交**

```bash
uv run --project backend pytest backend/tests/workspace/test_commands.py -q
git add backend/app/workspace/registry.py backend/app/services/watchlist.py backend/app/services/preferences.py backend/app/api/settings.py backend/app/services/json_report_store.py backend/app/services/backtest_summaries.py backend/tests/workspace/test_commands.py
git commit -m "refactor: make shared workspace stores atomic"
```

### Task 3: 条件写命令与冲突语义

**Files:**
- Create: `backend/app/workspace/commands.py`
- Test: `backend/tests/workspace/test_commands.py`

**Interfaces:**
- Produces: `execute_command(resource, operation, payload, expected_revision) -> ResourceSnapshot`。

- [ ] **Step 1: 写 428/412/409 失败测试**

```python
def test_command_requires_revision():
    with pytest.raises(WorkspacePreconditionRequired):
        execute_command(ResourceName.WATCHLIST, "add", {"symbol": "000403.SZ"}, None)

def test_stale_revision_does_not_overwrite():
    first = snapshot_resource(ResourceName.WATCHLIST)
    execute_command(ResourceName.WATCHLIST, "add", {"symbol": "000403.SZ", "note": ""}, first.revision)
    with pytest.raises(WorkspaceRevisionConflict):
        execute_command(ResourceName.WATCHLIST, "add", {"symbol": "600489.SH", "note": ""}, first.revision)
    assert [x["symbol"] for x in watchlist.list_symbols()] == ["000403.SZ"]
```

- [ ] **Step 2: 实现锁内 compare-and-write**

```python
def execute_command(resource, operation, payload, expected_revision):
    if not expected_revision: raise WorkspacePreconditionRequired(resource)
    with resource_lock(resource):
        current = snapshot_resource(resource)
        if current.revision != expected_revision:
            raise WorkspaceRevisionConflict(resource, current.revision)
        dispatch_command(resource, operation, payload)
        return snapshot_resource(resource)
```

因锁为 `RLock`，`snapshot_resource` 和 store 内部可重入。异常映射：缺 revision=428，过期=412 且响应带当前 ETag，未知 operation/业务规则=409，输入校验=422。

- [ ] **Step 3: 固化命令表**

```text
watchlist: add, batch_add, remove, move_to_top, clear
preferences: merge_safe
stock_reports: append, delete
market_recaps: append, delete
backtest_summaries: append, delete
```

报告 append 使用现有 Pydantic 请求字段；个股报告必须保留 `trading_advice:false`、`can_publish:false` 和 `verification_status:pending` 的 frontmatter 校验，失败返回 422。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
uv run --project backend pytest backend/tests/workspace/test_commands.py -q
git add backend/app/workspace/commands.py backend/tests/workspace/test_commands.py
git commit -m "feat: add conditional workspace commands"
```

### Task 4: Bootstrap、资源 API 与安全响应

**Files:**
- Create: `backend/app/api/workspace.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/api/watchlist.py`, `backend/app/api/settings.py`, `backend/app/api/stock_analysis.py`, `backend/app/api/market_recap.py`
- Modify: `docker-compose.yml`
- Test: `backend/tests/workspace/test_api.py`

**Interfaces:**
- Produces: `GET /api/workspace/bootstrap`, `GET /api/workspace/resources/{name}`, `POST /api/workspace/resources/{name}/commands`, `GET /api/workspace/revisions`。

- [ ] **Step 1: 写 API 失败测试**

```python
def test_resource_get_returns_strong_etag(client):
    response = client.get("/api/workspace/resources/watchlist")
    assert response.status_code == 200
    assert response.headers["etag"].startswith('"')
    assert response.json()["revision"] in response.headers["etag"]

def test_command_rejects_missing_and_stale_if_match(client):
    assert client.post("/api/workspace/resources/watchlist/commands", json={"operation": "add", "payload": {"symbol": "000403.SZ"}}).status_code == 428
    assert client.post("/api/workspace/resources/watchlist/commands", headers={"If-Match": '"stale"'}, json={"operation": "add", "payload": {"symbol": "000403.SZ"}}).status_code == 412

def test_bootstrap_has_no_secret_values(client):
    text = client.get("/api/workspace/bootstrap").text.lower()
    assert all(x not in text for x in ["feishu_webhook_secret", "wecom_bot_secret", "tickflow_api_key", "tushare_token", "deepseek_api_key"])
```

- [ ] **Step 2: 运行 RED**

```bash
uv run --project backend pytest backend/tests/workspace/test_api.py -q
```

- [ ] **Step 3: 实现契约**

Bootstrap 响应固定为：

```json
{
  "schema_version": 1,
  "server_time": "2026-07-20T20:00:00+08:00",
  "data_as_of": "2026-07-20",
  "mode": "cloud",
  "capabilities": {"label": "pro", "capabilities": {}},
  "resources": {
    "watchlist": {"revision": "sha256", "updated_at": "iso", "data": {"symbols": []}},
    "preferences": {"revision": "sha256", "updated_at": "iso", "data": {"preferences": {}}},
    "stock_reports": {"revision": "sha256", "updated_at": "iso", "data": {"reports": []}},
    "market_recaps": {"revision": "sha256", "updated_at": "iso", "data": {"reports": []}},
    "backtest_summaries": {"revision": "sha256", "updated_at": "iso", "data": {"summaries": []}}
  }
}
```

`list_report_metadata()` 必须显式投影允许字段，不能通过“排除 content”的黑名单实现；单条正文另设认证 `GET`，返回 `Cache-Control: private, no-store`。

`data_as_of` 从 `request.app.state.repo.get_enriched_latest()` 返回的缓存日期读取，无数据时为 null。资源 GET 设置 `ETag` 和 `Cache-Control: private, no-store`。命令成功返回新 snapshot 与 ETag。注册异常 handler 映射 428/412/409。

- [ ] **Step 4: 增加 feature gate**

`Settings` 增加 `workspace_sync_enabled: bool = False`。router 始终可读；写命令在 false 时返回 503 `WORKSPACE_SYNC_DISABLED`。云端在前端兼容版本部署后才设置 true。

`docker-compose.yml` 明确传入 `WORKSPACE_SYNC_ENABLED: ${WORKSPACE_SYNC_ENABLED:-false}`。当 gate 为 true 时，legacy 共享资源写端点必须带 `If-Match` 委托 `execute_command`，或返回 `409 LEGACY_WORKSPACE_WRITE_DISABLED`；不得直写原 store。为每个 legacy mutation 加契约测试。

- [ ] **Step 5: 运行 GREEN 并提交**

```bash
uv run --project backend pytest backend/tests/workspace/test_api.py -q
git add backend/app/api/workspace.py backend/app/main.py backend/app/config.py backend/app/api/watchlist.py backend/app/api/settings.py backend/app/api/stock_analysis.py backend/app/api/market_recap.py backend/tests/workspace/test_api.py docker-compose.yml
git commit -m "feat: add versioned workspace API"
```

### Task 5: SSE 变更通知与轮询降级

**Files:**
- Create: `backend/app/workspace/events.py`
- Modify: `backend/app/api/workspace.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/workspace/test_events.py`

**Interfaces:**
- Produces: `WorkspaceEventHub.publish(snapshot)`, `GET /api/workspace/events`、`GET /api/workspace/revisions`。

- [ ] **Step 1: 写事件失败测试**

```python
async def test_publish_delivers_revision():
    hub = WorkspaceEventHub()
    async with hub.subscribe() as queue:
        hub.publish(ResourceName.WATCHLIST, "r1")
        event = await asyncio.wait_for(queue.get(), 1)
    assert event.resource == ResourceName.WATCHLIST
    assert event.revision == "r1"
```

- [ ] **Step 2: 实现线程安全 hub**

Hub 在 lifespan 记录主 event loop；sync worker 调用 `loop.call_soon_threadsafe` 向每个有界 `asyncio.Queue(maxsize=32)` 投递。队列满时丢弃该订阅最旧事件并投递 `resync_required`，不能阻塞写请求。

- [ ] **Step 3: 接入命令成功事件**

只有原子写完成后发布 `{id, type:"resource_changed", resource, revision, updated_at}`。SSE 每 20 秒发送 comment heartbeat；断开时移除 queue。重启不重放内存事件，客户端重连必须请求 `/revisions`。

- [ ] **Step 4: revisions 轮询契约**

```json
{"server_time":"iso","resources":{"watchlist":"sha256","preferences":"sha256","stock_reports":"sha256","market_recaps":"sha256","backtest_summaries":"sha256"}}
```

- [ ] **Step 5: 验证并提交**

```bash
uv run --project backend pytest backend/tests/workspace/test_events.py -q
git add backend/app/workspace/events.py backend/app/api/workspace.py backend/app/main.py backend/tests/workspace/test_events.py
git commit -m "feat: stream workspace revision changes"
```

### Task 6: Mac 云端 adapter 与校验缓存

**Files:**
- Create: `backend/app/desktop_client/cache.py`
- Create: `backend/app/desktop_client/workspace_adapter.py`
- Create: `backend/app/desktop_client/proxy.py`
- Test: `backend/tests/test_desktop_workspace_adapter.py`
- Test: `backend/tests/test_desktop_cloud_proxy.py`

**Interfaces:**
- Produces: `WorkspaceAdapter` protocol、`CloudWorkspaceAdapter.bootstrap/get/command/revisions/stream_events`、`WorkspaceCache.get/put/quarantine`。

- [ ] **Step 1: 写缓存损坏与禁写失败测试**

```python
def test_corrupt_cache_is_quarantined(tmp_path):
    cache = WorkspaceCache(tmp_path)
    cache.path(ResourceName.WATCHLIST).write_text('{"checksum":"bad","data":{}}')
    assert cache.get(ResourceName.WATCHLIST) is None
    assert list((tmp_path / "quarantine").iterdir())

def test_offline_command_is_never_queued(adapter, remote):
    remote.request.side_effect = httpx.ConnectError("offline")
    with pytest.raises(WorkspaceOfflineReadOnly):
        adapter.command(ResourceName.WATCHLIST, "add", {"symbol": "000403.SZ"}, "r1")
    forbidden = ("pending", "queue", "outbox")
    assert not [path for path in adapter.cache.root.rglob("*") if any(word in path.name.lower() for word in forbidden)]
```

- [ ] **Step 2: 实现缓存格式**

每个资源存 `Application Support/TickFlowStockPanel/cache/workspace/{resource}.json`：

```json
{"schema_version":1,"resource":"watchlist","revision":"sha256","fetched_at":"iso","checksum":"sha256-of-data","data":{}}
```

读取时校验 schema、resource、revision 长度和 data checksum；失败移动到 `cache/quarantine/{timestamp}-{resource}.json`。缓存目录中不得出现 `pending`, `queue`, `outbox` 文件。

- [ ] **Step 3: 实现 adapter**

```python
class WorkspaceAdapter(Protocol):
    def bootstrap(self) -> dict: ...
    def get(self, resource: ResourceName) -> ResourceSnapshot: ...
    def command(self, resource: ResourceName, operation: str, payload: dict, revision: str) -> ResourceSnapshot: ...
    def revisions(self) -> dict[ResourceName, str]: ...
    def stream_events(self) -> Iterator[dict]: ...
```

Cloud adapter 在线 GET 成功才写缓存；连接错误的 GET 返回缓存并标 `offline_readonly=True`；command 遇连接错误直接抛 `WorkspaceOfflineReadOnly`，绝不写本地资源或排队。

在模块中明确定义：

```python
class WorkspaceOfflineReadOnly(RuntimeError):
    pass
```

`backend/app/main.py` 的 lifespan 在 `TICKFLOW_DESKTOP_CLIENT=1` 时把 `CloudWorkspaceAdapter` 放入 `app.state.workspace_adapter`。`api/workspace.py` 通过 `getattr(request.app.state, "workspace_adapter", None)` 分流：云端直接使用 registry；桌面端把 bootstrap/get/command/revisions 委托给 adapter，events 将 `adapter.stream_events()` 转发为本地同源 SSE。这样桌面端绝不能读取或写入本机同名共享文件。

- [ ] **Step 4: 增加桌面云端强制代理**

`proxy.py` 以 method + 规范化 exact path 定义默认拒绝策略：

```python
LOCAL_EXACT = {
    ("POST", "/api/backtest/strategy/run"),
    ("POST", "/api/backtest/strategy/stream"),
    ("POST", "/api/screener/run"),
}
LOCAL_PREFIXES = ("/api/client/", "/api/workspace/")
SENSITIVE_DESKTOP_DENY = (
    "/api/settings/tickflow-key", "/api/settings/tushare-token",
    "/api/settings/ai-key", "/api/settings/preferences/feishu-webhook",
    "/api/settings/preferences/wecom-bot",
)

def route_target(method: str, path: str) -> Literal["local", "cloud", "deny"]:
    # Normalize once, reject absolute URLs and encoded traversal first.
    if path.startswith(SENSITIVE_DESKTOP_DENY): return "deny"
    if (method.upper(), path) in LOCAL_EXACT or path.startswith(LOCAL_PREFIXES): return "local"
    if path.startswith("/api/"): return "cloud"
    return "deny"
```

`DesktopCloudProxyMiddleware` 只在 `TICKFLOW_DESKTOP_CLIENT=1` 启用，使用 `RemoteClient` 原样转发 method、path、query、body 和必要内容类型；移除 hop-by-hop headers，远端 `Set-Cookie` 不回传本地浏览器。NDJSON/SSE 用 `StreamingResponse` 逐块转发。代理日志不得记录 body、Cookie 或 Authorization。

测试断言 `/api/kline/daily`、`/api/stock-analysis/analyze` 和脱敏偏好读取走远端；敏感 settings 子路由被拒绝；只有明确支持的三个计算端点与 `/api/client/*`、`/api/workspace/*` 走本地。测试还必须枚举 FastAPI 已注册路由，防止未分类路由默认落到本地。

- [ ] **Step 5: 运行 GREEN 并提交**

```bash
uv run --project backend pytest backend/tests/test_desktop_workspace_adapter.py backend/tests/test_desktop_cloud_proxy.py -q
git add backend/app/desktop_client/cache.py backend/app/desktop_client/workspace_adapter.py backend/app/desktop_client/proxy.py backend/tests/test_desktop_workspace_adapter.py backend/tests/test_desktop_cloud_proxy.py
git commit -m "feat: add desktop cloud workspace adapter"
```

### Task 7: 云端计算输入包与本地只读数据镜像

**Files:**
- Create: `backend/app/services/compute_input_bundle.py`
- Modify: `backend/app/api/workspace.py`
- Modify: `backend/app/desktop_client/workspace_adapter.py`
- Test: `backend/tests/test_compute_input_bundle.py`

**Interfaces:**
- Produces: `POST /api/workspace/compute-inputs/build`、`ComputeInputBundleService.build()`、`CloudWorkspaceAdapter.prepare_compute_input()`。

- [ ] **Step 1: 写路径与校验失败测试**

```python
def test_bundle_contains_only_whitelisted_data_roots(service, tmp_path):
    bundle = service.build("strategy_backtest", {"start": "2026-01-01", "end": "2026-07-20", "asset_type": "stock"})
    with zipfile.ZipFile(bundle.path) as archive:
        names = archive.namelist()
    assert names[0] == "manifest.json"
    assert all(name.startswith(("kline_daily/", "kline_daily_enriched/", "instruments/", "adj_factor/")) or name == "manifest.json" for name in names)

def test_client_rejects_zip_traversal(adapter, tmp_path):
    malicious_zip = tmp_path / "malicious.zip"
    with zipfile.ZipFile(malicious_zip, "w") as archive:
        archive.writestr("../escaped", "bad")
    with pytest.raises(ComputeInputIntegrityError):
        adapter.install_compute_input(malicious_zip)
    assert not (adapter.cache_root.parent / "escaped").exists()
```

- [ ] **Step 2: 实现服务端输入包**

请求只接受 `task in {strategy_backtest, screener}` 和该任务现有 Pydantic 配置字段，禁止客户端提交文件路径。服务端按日期和 asset type 从 repo 读取，写入临时目录的兼容布局：

```text
kline_daily/date=YYYY-MM-DD/part.parquet
kline_daily_enriched/date=YYYY-MM-DD/part.parquet
instruments/part.parquet
adj_factor/part.parquet
manifest.json
```

Screener 至少包含目标 `as_of` 的 enriched 和 instruments；回测包含请求区间的 daily/enriched、instruments、adj_factor。manifest 含 `schema_version=1`、task、规范化参数 digest、data_as_of、每个文件 size/SHA-256 和总未压缩字节数。响应为 `application/zip`，完成传输后删除服务端临时文件。

- [ ] **Step 3: 实现客户端安全安装**

`prepare_compute_input(task, config)` 以 `sha256(task + canonical config + data_as_of)` 为缓存 key。解压前拒绝绝对路径、`..`、symlink、清单外文件、单包未压缩超过 2 GiB；逐文件校验 size/SHA-256，并同时校验 manifest task、规范参数 digest、`X-Data-As-Of`、`X-Bundle-SHA256` 与 freshness 门槛，再用 `os.replace` 发布到 `Application Support/TickFlowStockPanel/compute_inputs/{cache_key}`。任一完整性失败抛 `ComputeInputIntegrityError` 并禁止降级。该目录只读供 worker 使用，不反向上传。

- [ ] **Step 4: API 与 feature gate**

`POST /api/workspace/compute-inputs/build` 仅在认证后开放，不调用外部数据源，也不消耗 TickFlow Pro RPM 预算；使用独立 semaphore 将并发构建上限设为 1，重复相同 digest 复用 10 分钟临时缓存。响应头包含 `X-Data-As-Of` 和 `X-Bundle-SHA256`。

- [ ] **Step 5: 验证并提交**

```bash
uv run --project backend pytest backend/tests/test_compute_input_bundle.py -q
git add backend/app/services/compute_input_bundle.py backend/app/api/workspace.py backend/app/desktop_client/workspace_adapter.py backend/tests/test_compute_input_bundle.py
git commit -m "feat: prepare verified cloud compute inputs"
```

### Task 8: 本地计算优先、云端降级与摘要写回

**Files:**
- Create: `backend/app/services/compute_router.py`
- Modify: `backend/app/api/backtest.py`
- Modify: `backend/app/api/screener.py`
- Test: `backend/tests/test_compute_router.py`

**Interfaces:**
- Produces: `ComputeRouter.execute(task, local, cloud) -> ComputeResult`、`LocalComputeUnavailable`、`execution_target`。

- [ ] **Step 1: 写降级边界失败测试**

```python
def test_local_worker_crash_falls_back_once():
    router = ComputeRouter(cloud_enabled=True)
    result = router.execute("strategy_backtest", lambda: (_ for _ in ()).throw(LocalComputeUnavailable("worker exited")), lambda: {"stats": {"return": 1}})
    assert result.execution_target == "cloud"

@pytest.mark.parametrize("error", [ValueError("bad params"), PermissionError("capability denied")])
def test_business_errors_never_fall_back(error):
    cloud = Mock()
    with pytest.raises(type(error)):
        ComputeRouter(True).execute("strategy_backtest", lambda: (_ for _ in ()).throw(error), cloud)
    cloud.assert_not_called()
```

- [ ] **Step 2: 实现 ComputeRouter**

定义并实现：

```python
@dataclass(frozen=True)
class ComputeResult:
    value: dict
    execution_target: Literal["local", "cloud"]

class LocalComputeUnavailable(RuntimeError):
    pass
```

`ComputeRouter` 仅捕获 `LocalComputeUnavailable`；worker process 非零退出和原生库加载失败必须先在调用边界转换成该异常。最多调用 cloud 一次。`ValueError`、`PermissionError`、HTTP 429 和业务异常原样抛出。日志含 task_id、target、elapsed、error_code，不含参数正文和认证头。

- [ ] **Step 3: 接入回测和筛选**

本地执行前调用 `prepare_compute_input()`，把返回目录作为 worker task 的 `data_dir`。只有传输不可用、下载超时、worker 启动失败、原生库加载失败或本地 repo 初始化失败可转为 `LocalComputeUnavailable`；`ComputeInputIntegrityError`、数据过期、参数 digest 不匹配和业务校验错误必须原样抛出。本地 `/api/backtest/strategy/stream` worker 抛本地基础设施故障时，调用远端 `/api/backtest/strategy/run`，再向现有 SSE 发 `done`。`/api/screener/run` 同样路由。云端自身始终直接执行，不递归降级。

- [ ] **Step 4: 写回回测摘要**

本地成功或云端降级成功后，通过 workspace `backtest_summaries.append` 写云端；payload 只含 Task 2 定义的摘要字段。写回冲突时刷新 revision 后要求用户再次确认保存，不能重跑计算或自动覆盖。

- [ ] **Step 5: 验证并提交**

```bash
uv run --project backend pytest backend/tests/test_compute_router.py backend/tests/backtest/test_worker_process.py -q
git add backend/app/services/compute_router.py backend/app/api/backtest.py backend/app/api/screener.py backend/tests/test_compute_router.py
git commit -m "feat: route desktop compute with cloud fallback"
```

### Task 9: 前端 workspace、ETag 与自动刷新

**Files:**
- Create: `frontend/src/lib/etagStore.ts`
- Create: `frontend/src/lib/workspace.ts`
- Create: `frontend/src/lib/useWorkspaceEvents.ts`
- Modify: `frontend/src/lib/api.ts`, `useSharedQueries.ts`, `useSharedMutations.ts`, `main.tsx`, `components/Layout.tsx`
- Modify: `frontend/src/pages/Backtest.tsx`, `Watchlist.tsx`, `StockAnalysis.tsx`, `Review.tsx`
- Test: `frontend/src/lib/__tests__/workspaceConflict.test.ts`
- Test: `frontend/src/lib/__tests__/useWorkspaceEvents.test.ts`

**Interfaces:**
- Produces: `workspaceApi.bootstrap/get/command/revisions`、ETag map、SSE invalidation 和 412 UI。

- [ ] **Step 1: 写 412 失败测试**

```ts
it('does not retry a stale write', async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ code: 'WORKSPACE_REVISION_CONFLICT' }), { status: 412, headers: { ETag: '"new"' } }))
  await expect(workspaceApi.command('watchlist', 'add', { symbol: '000403.SZ' }, 'old')).rejects.toMatchObject({ status: 412, currentRevision: 'new' })
  expect(fetchMock).toHaveBeenCalledTimes(1)
})
```

- [ ] **Step 2: 实现 typed API error 与 ETag store**

```ts
export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string, public currentRevision?: string) { super(message) }
}
```

`etagStore` 只存内存 `{resource -> revision}`；bootstrap/get/command 成功更新。不得写 localStorage。

- [ ] **Step 3: 实现 workspaceApi**

```ts
command: (resource, operation, payload, revision) => request(`/api/workspace/resources/${resource}/commands`, {
  method: 'POST',
  headers: { 'If-Match': `"${revision}"` },
  body: JSON.stringify({ operation, payload }),
})
```

428 显示“请先刷新”；412 更新 current revision、invalidate 对应 query、显示“云端内容已变化，请检查后重试”；不得自动 retry。409 显示业务冲突。

- [ ] **Step 4: 接入现有页面**

自选股 add/batch/remove/move/clear、个股报告 append/delete、市场复盘 append/delete、共享偏好 merge 改走 workspace commands。旧 API wrapper 只保留编译兼容：当 workspace gate 开启时，服务端必须要求 `If-Match` 并进入同一命令处理器，或返回迁移错误，绝不能绕过 revision。Backtest 手机只展示 `backtest_summaries`，复杂配置仍桌面优先。报告列表查询和 bootstrap 只包含脱敏元数据，Markdown 正文必须通过单条 `no-store` 读取获取且不进入 `offlineDb`、workspace cache 或日志。

- [ ] **Step 5: SSE 与轮询**

`useWorkspaceEvents` 同源创建 `EventSource('/api/workspace/events')`。收到 `resource_changed` 只 invalidate 对应 query；`resync_required` 全量请求 revisions。连续失败后 1/2/5/10/30 秒退避；SSE 未打开时每 30 秒请求 revisions；前台恢复和 online 事件立即 bootstrap。

- [ ] **Step 6: 连接与模式显示**

Layout 顶部固定显示 `本地计算`、`云端模式` 或 `离线只读`，并显示最后同步时间和数据日期。离线时所有 workspace mutation 按钮 disabled；CSS 不能仅靠颜色表达禁用。

- [ ] **Step 7: 验证并提交**

```bash
pnpm --dir frontend test
pnpm --dir frontend build
git add frontend/src/lib frontend/src/components/Layout.tsx frontend/src/pages
git commit -m "feat: sync workspace resources across clients"
```

### Task 10: 多端一致性、安全和部署门禁

**Files:**
- Create: `frontend/e2e/multiclient.spec.ts`
- Create: `scripts/verify_multiclient_security.sh`
- Create: `docs/workspace-sync-runbook.md`
- Create: `reports/multiclient_acceptance/results.md`

**Interfaces:**
- Produces: 开启 `WORKSPACE_SYNC_ENABLED=true` 的最终证据。

- [ ] **Step 1: 写双 context E2E**

Playwright 创建 mobile 与 desktop 两个 browser context，分别登录同一云端：desktop 加入 `000403.SZ` 后 mobile 通过 SSE 看见；mobile 加入 `600489.SH` 后 desktop 看见；两端基于同一旧 revision 并发写时只允许一个 200，另一个必须 412；离线 context 写入必须在 fetch 前被拒绝。

- [ ] **Step 2: 写安全验证脚本**

```bash
#!/usr/bin/env bash
set -euo pipefail
test -d frontend/dist
test -d backend/dist/TickFlowStockPanel.app
test -n "${TICKFLOW_SECRET_CANARY:-}"
for target in frontend/dist backend/dist/TickFlowStockPanel.app "$HOME/Library/Application Support/TickFlowStockPanel/cache"; do
  test -e "$target"
  if grep -R -a -F -- "$TICKFLOW_SECRET_CANARY" "$target"; then
    echo "secret canary found in $target" >&2
    exit 1
  fi
done
ssh codex-vm '! ss -lnt | grep -Eq "(0.0.0.0|\[::\]):(3018|3019)[[:space:]]"'
ssh codex-vm 'tailscale serve status | grep -F "proxy http://127.0.0.1:8787"'
ssh codex-vm 'tailscale serve status | grep -F "proxy http://127.0.0.1:3019"'
ssh codex-vm 'docker inspect TickFlow_Stock_Panel --format "{{range .Config.Env}}{{println .}}{{end}}" | grep -q "GOLD_WORKSPACE_ENABLED=false"'
ssh codex-vm 'docker inspect TickFlow_Stock_Panel --format "{{range .Config.Env}}{{println .}}{{end}}" | grep -q "WORKSPACE_SYNC_ENABLED=true"'
echo MULTICLIENT_SECURITY_OK
```

安全测试在专用测试构建中把同一唯一 canary 分别放入 TickFlow/Tushare/DeepSeek/Webhook 密钥源，扫描上述产物、Application Support 缓存和桌面日志。产物或缓存目录缺失、`grep` 错误或 canary 命中都必须失败。

- [ ] **Step 3: 全量自动化门禁**

```bash
uv run --project backend pytest backend/tests/workspace backend/tests/test_desktop_workspace_adapter.py backend/tests/test_desktop_cloud_proxy.py backend/tests/test_compute_input_bundle.py backend/tests/test_compute_router.py -q
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test e2e/multiclient.spec.ts
./scripts/verify_multiclient_security.sh
```

- [ ] **Step 4: 分两步启用云端写入**

先部署读接口并保持 `WORKSPACE_SYNC_ENABLED=false`，确认新 PWA 与 Mac 均兼容；再设置：

```bash
ssh codex-vm 'cd /home/ubuntu/tickflow-stock-panel-stage-a && WORKSPACE_SYNC_ENABLED=true GOLD_WORKSPACE_ENABLED=false PORT=3019 docker compose up -d'
```

Expected: 运行容器 env 明确含 `WORKSPACE_SYNC_ENABLED=true`，`/api/workspace/bootstrap` 正常，命令带 If-Match 成功，旧客户端无条件写入被拒绝。若任一项不成立，立即以 `WORKSPACE_SYNC_ENABLED=false` recreate 容器。

- [ ] **Step 5: 写 runbook 与结果**

Runbook 包含 feature gate 开关、云端备份、412 处理、SSE 诊断、缓存隔离、计算降级、回滚到 `WORKSPACE_SYNC_ENABLED=false`。结果报告列出测试计数、两端设备、数据日期、冲突证据、DMG 哈希、PWA URL 和仍需人工项，不含密钥、Cookie 或复盘正文。

- [ ] **Step 6: 提交**

```bash
chmod +x scripts/verify_multiclient_security.sh
git add frontend/e2e/multiclient.spec.ts scripts/verify_multiclient_security.sh docs/workspace-sync-runbook.md reports/multiclient_acceptance
git commit -m "test: verify multi-client workspace sync"
```

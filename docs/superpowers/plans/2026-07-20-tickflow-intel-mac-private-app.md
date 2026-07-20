# TickFlow Intel Mac Private App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Intel Mac 上构建可双击启动的私有 TickFlow 一体化 App 和 DMG，提供本地研究计算、云端连接基础和安全的长期会话保存。

**Architecture:** 复用 PyWebView + 本地 FastAPI + PyInstaller；macOS frozen 数据改到 Application Support，桌面生命周期显式停止 uvicorn。云端地址保存在非敏感 client 配置，远端 session 只进入 macOS Keychain；共享资源路由和计算降级在 Phase 3 接入。

**Tech Stack:** Python 3.12 x86_64、FastAPI、uvicorn、PyWebView、PyInstaller 6、Polars/PyArrow/DuckDB x86_64、macOS `/usr/bin/security`、codesign、hdiutil。

## Global Constraints

- 只构建 Intel x86_64，不从 GitHub ARM runner 交叉构建。
- 私有分发、ad-hoc 签名，不做 App Store、Apple 公证或自动更新。
- 复用现有红色 `packaging/icon.icns`；不得重新生成或修改 Web favicon。
- Mac 数据目录固定为 `~/Library/Application Support/TickFlowStockPanel/`。
- TickFlow、Tushare、DeepSeek 完整 Key 只在云端；Mac 不保存这些 Key。
- 远端登录密码不落盘；长期 session 只存 macOS Keychain。
- App 本地服务只监听 `127.0.0.1`；关闭窗口后不得残留 FastAPI 进程或监听端口。
- 离线只读和共享状态同步由 Phase 3 完成；本阶段不得实现离线写队列。
- 不接券商、实盘、自动交易、OpenClaw、Telegram 或 Gold sampler。

---

## File Map

- Create: `backend/app/desktop_runtime.py` — 可停止的 uvicorn 线程。
- Create: `backend/app/desktop_client/{__init__,config,keychain,remote,api}.py` — 云端连接基础。
- Create: `backend/tests/test_desktop_paths.py`, `test_desktop_lifecycle.py`, `test_desktop_client_auth.py`
- Create: `frontend/src/pages/ClientConnection.tsx`, `frontend/src/lib/clientMode.ts`
- Create: `scripts/build_macos_intel.sh`, `scripts/verify_macos_intel_app.sh`
- Create: `docs/macos-intel-private-runbook.md`
- Modify: `backend/app/config.py`, `backend/app/desktop.py`, `backend/app/main.py`, `backend/pyproject.toml`
- Modify: `frontend/src/router.tsx`, `frontend/src/components/Layout.tsx`, `frontend/src/lib/api.ts`
- Modify: `packaging/tickflow.spec`

### Task 1: macOS Application Support 数据目录

**Files:**
- Modify: `backend/app/config.py:19-44`
- Test: `backend/tests/test_desktop_paths.py`

**Interfaces:**
- Consumes: `_IS_FROZEN`, `sys.platform`, `platformdirs.user_data_path`。
- Produces: `_user_data_root()` 在 frozen macOS 返回 `~/Library/Application Support/TickFlowStockPanel`。

- [ ] **Step 1: 写路径失败测试**

```python
from pathlib import Path
from app import config


def test_frozen_macos_uses_application_support(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_IS_FROZEN", True)
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config, "user_data_path", lambda *a, **k: tmp_path / "TickFlowStockPanel")
    assert config._user_data_root() == tmp_path / "TickFlowStockPanel"


def test_frozen_windows_keeps_executable_data(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "_IS_FROZEN", True)
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setattr(config.sys, "executable", str(tmp_path / "TickFlowStockPanel.exe"))
    assert config._user_data_root() == tmp_path / "data"
```

- [ ] **Step 2: 运行 RED**

```bash
uv run --project backend pytest backend/tests/test_desktop_paths.py -q
```

Expected: macOS 断言失败，当前返回 `.app/Contents/MacOS/data`。

- [ ] **Step 3: 实现平台分流**

```python
from platformdirs import user_data_path

def _user_data_root() -> Path:
    if _IS_FROZEN:
        if sys.platform == "darwin":
            return Path(user_data_path("TickFlowStockPanel", appauthor=False, ensure_exists=False))
        return Path(sys.executable).resolve().parent / "data"
    return _PROJECT_ROOT / "data"
```

同步更新注释，明确 macOS 升级不写 `.app` 包内、Windows 保持兼容。

- [ ] **Step 4: 运行 GREEN 并提交**

```bash
uv run --project backend pytest backend/tests/test_desktop_paths.py -q
git add backend/app/config.py backend/tests/test_desktop_paths.py
git commit -m "fix: persist macOS data in Application Support"
```

### Task 2: 可控桌面后端生命周期

**Files:**
- Create: `backend/app/desktop_runtime.py`
- Modify: `backend/app/desktop.py`
- Test: `backend/tests/test_desktop_lifecycle.py`

**Interfaces:**
- Produces: `DesktopServer(port: int)`, `.start()`, `.wait_ready(timeout)`, `.stop(timeout)`。
- Consumes: `app.main.app`、uvicorn `Server.should_exit`。

- [ ] **Step 1: 写生命周期失败测试**

```python
import socket
from app.desktop_runtime import DesktopServer


def test_desktop_server_releases_port():
    server = DesktopServer(port=0)
    server.start()
    assert server.wait_ready(20)
    port = server.bound_port
    server.stop(10)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_stop_is_idempotent():
    server = DesktopServer(port=0)
    server.start(); assert server.wait_ready(20)
    server.stop(10); server.stop(10)

def test_stale_instance_lock_is_replaced(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / ".desktop.lock").write_text("999999")
    assert desktop._acquire_single_instance() is True

def test_live_instance_lock_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / ".desktop.lock").write_text(str(os.getpid()))
    assert desktop._acquire_single_instance() is False
```

- [ ] **Step 2: 运行 RED**

```bash
uv run --project backend pytest backend/tests/test_desktop_lifecycle.py -q
```

- [ ] **Step 3: 实现 DesktopServer**

```python
class DesktopServer:
    def __init__(self, port: int) -> None:
        self.requested_port = port
        self.bound_port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._done = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("desktop server already started")
        if self.requested_port == 0:
            self.bound_port = find_free_loopback_port()
        self._thread = threading.Thread(target=self._run, name="tickflow-uvicorn", daemon=False)
        self._thread.start()

    def _run(self) -> None:
        from app.main import app
        config = uvicorn.Config(app, host="127.0.0.1", port=self.bound_port, access_log=False, log_level="info")
        self._server = uvicorn.Server(config)
        try: self._server.run()
        finally: self._done.set()

    def wait_ready(self, timeout: float) -> bool:
        return wait_for_health(self.bound_port, timeout)

    def stop(self, timeout: float) -> None:
        if self._server is not None: self._server.should_exit = True
        if self._thread and self._thread.is_alive(): self._thread.join(timeout)
        if self._thread and self._thread.is_alive(): raise RuntimeError("desktop server did not stop")
```

`find_free_loopback_port()` 用端口 0 绑定后读取实际端口；`wait_for_health()` 保留当前 `/health` 轮询语义。

- [ ] **Step 4: 接入 desktop.main**

`desktop.py` 删除旧 `_run_uvmicorn`（包括拼写错误）和 daemon thread 管理，改为：

```python
os.environ.setdefault("TICKFLOW_DESKTOP_CLIENT", "1")
server = DesktopServer(_find_free_port(_BASE_PORT))
server.start()
try:
    if not server.wait_ready(60): return 1
    if os.getenv("TICKFLOW_DESKTOP_SMOKE") == "1": return 0
    _open_window(f"http://127.0.0.1:{server.bound_port}")
    return 0
finally:
    server.stop(10)
    _release_single_instance()
```

顶层异常仍写 `desktop.log`；单实例锁只在 finally 释放一次。

- [ ] **Step 5: 运行 GREEN 并提交**

```bash
uv run --project backend pytest backend/tests/test_desktop_lifecycle.py -q
git add backend/app/desktop.py backend/app/desktop_runtime.py backend/tests/test_desktop_lifecycle.py
git commit -m "fix: stop desktop backend on app exit"
```

### Task 3: 云端地址与 macOS Keychain 会话

**Files:**
- Create: `backend/app/desktop_client/__init__.py`
- Create: `backend/app/desktop_client/config.py`
- Create: `backend/app/desktop_client/keychain.py`
- Create: `backend/app/desktop_client/remote.py`
- Create: `backend/app/desktop_client/api.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_desktop_client_auth.py`

**Interfaces:**
- Produces: `ClientConfig`, `KeychainSessionStore`, `RemoteClient`, `/api/client/config`, `/api/client/status`, `/api/client/auth/login`, `/api/client/auth/logout`。
- Consumes: 云端 `POST /api/auth/login` 的 `tf_session` Cookie。

- [ ] **Step 1: 写配置与 Keychain 失败测试**

```python
def test_client_config_rejects_public_http(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        save_client_config(tmp_path, {"remote_base_url": "http://example.com"})

def test_keychain_never_puts_token_in_command(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append((args, kw)) or CompletedProcess(args, 0, "", ""))
    KeychainSessionStore().save("https://vm.tail.ts.net:8443", "secret-token")
    args, kwargs = calls[0]
    assert "secret-token" not in args
    assert kwargs["input"] == "secret-token"
```

- [ ] **Step 2: 写远端登录失败测试**

使用 `httpx.MockTransport` 返回 `Set-Cookie: tf_session=abc; HttpOnly; Secure; SameSite=Lax`，断言：密码只出现在 POST body；保存的是 `abc`；API 响应只有 `authenticated: true`，不含 token。

- [ ] **Step 3: 运行 RED**

```bash
uv run --project backend pytest backend/tests/test_desktop_client_auth.py -q
```

- [ ] **Step 4: 实现非敏感 ClientConfig**

```python
@dataclass(frozen=True)
class ClientConfig:
    remote_base_url: str
    preferred_mode: Literal["hybrid", "cloud"] = "hybrid"

def validate_remote_url(value: str) -> str:
    url = httpx.URL(value.rstrip("/"))
    local = url.host in {"127.0.0.1", "localhost"}
    if url.scheme != "https" and not (local and url.scheme == "http"):
        raise ValueError("remote_base_url must use HTTPS")
    if url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("remote_base_url must not contain path, query or fragment")
    return str(url).rstrip("/")
```

配置写 `settings.data_dir / "desktop_client.json"`，只含 URL 和 mode。

- [ ] **Step 5: 实现 KeychainSessionStore**

macOS 使用 `/usr/bin/security`：service 固定 `com.tickflow.stockpanel.remote-session`，account 为 `sha256(remote_base_url)[:24]`。`save()` 调用 `security add-generic-password -a <account> -s <service> -U -w`，把 token 作为 subprocess stdin 输入，不能放 argv；`load()` 用 `find-generic-password -a <account> -s <service> -w`；`delete()` 用 `delete-generic-password -a <account> -s <service>`。非 macOS 返回内存 session，不落盘。

- [ ] **Step 6: 实现 RemoteClient**

```python
class RemoteClient:
    def login(self, password: str) -> bool:
        with httpx.Client(base_url=self.base_url, timeout=15, follow_redirects=False) as client:
            response = client.post("/api/auth/login", json={"password": password})
            response.raise_for_status()
            token = client.cookies.get("tf_session")
        if not token: raise RemoteAuthError("cloud did not return a session cookie")
        self.sessions.save(self.base_url, token)
        return True

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        token = self.sessions.load(self.base_url)
        if not token: raise RemoteAuthRequired("cloud login required")
        headers = dict(kwargs.pop("headers", {}))
        headers["Cookie"] = f"tf_session={token}"
        return httpx.request(method, self.base_url + path, headers=headers, timeout=30, **kwargs)
```

日志只记录 method、规范化 path、status 和 elapsed，不记录 headers/body/token。

- [ ] **Step 7: 实现本地 client API**

请求/响应契约：

```text
GET  /api/client/config -> {remote_base_url, preferred_mode}
PUT  /api/client/config -> same fields, no secrets
GET  /api/client/status -> {configured, authenticated, reachable, mode, error_code}
POST /api/client/auth/login {password} -> {authenticated: true}
POST /api/client/auth/logout -> {authenticated: false}
```

`status` 只请求远端 `/api/auth/status`；不回传 Cookie。router 仅在 `TICKFLOW_DESKTOP_CLIENT=1` 时 include。

- [ ] **Step 8: 运行 GREEN 并提交**

```bash
uv run --project backend pytest backend/tests/test_desktop_client_auth.py -q
git add backend/app/desktop_client backend/app/main.py backend/tests/test_desktop_client_auth.py
git commit -m "feat: add secure desktop cloud session"
```

### Task 4: 桌面连接与运行模式 UI

**Files:**
- Create: `frontend/src/lib/clientMode.ts`
- Create: `frontend/src/pages/ClientConnection.tsx`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/router.tsx`
- Modify: `frontend/src/components/Layout.tsx`

**Interfaces:**
- Consumes: `/api/client/*`。
- Produces: `/client-connection` 和顶部 `本地 / 混合 / 云端 / 离线` 状态。

- [ ] **Step 1: 写模式解析单元测试**

```ts
expect(resolveClientBadge({ configured: true, authenticated: true, reachable: true, mode: 'hybrid' })).toEqual({ label: '混合模式', tone: 'success' })
expect(resolveClientBadge({ configured: true, authenticated: true, reachable: false, mode: 'hybrid' })).toEqual({ label: '离线只读', tone: 'warning' })
```

- [ ] **Step 2: 实现 API 类型与 clientMode**

`api.ts` 增加 `clientConfigGet/Save`、`clientStatus`、`clientLogin`、`clientLogout`。`clientMode.ts` 只处理显示状态，不保存 password/session。

- [ ] **Step 3: 实现连接页**

页面字段只有云端 HTTPS URL、模式 segmented control、密码登录框和连接状态。密码提交成功后立即清空 React state；不得写 localStorage/sessionStorage/IndexedDB。保存 URL 后先 `/health` 与 `/api/auth/status` 测试，再显示成功。

- [ ] **Step 4: 接入 Layout**

桌面环境检测到 `/api/client/status` 可用时显示状态 badge 和连接设置入口；普通 PWA 该 API 404 时不显示桌面专属 UI。状态 badge 使用图标，不增加大卡片。

- [ ] **Step 5: 验证并提交**

```bash
pnpm --dir frontend test
pnpm --dir frontend build
git add frontend/src/lib/clientMode.ts frontend/src/pages/ClientConnection.tsx frontend/src/lib/api.ts frontend/src/router.tsx frontend/src/components/Layout.tsx
git commit -m "feat: add desktop cloud connection UI"
```

### Task 5: Intel PyInstaller 与私有 DMG

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `packaging/tickflow.spec`
- Create: `scripts/build_macos_intel.sh`
- Create: `scripts/verify_macos_intel_app.sh`

**Interfaces:**
- Produces: `backend/dist/TickFlowStockPanel.app`、`dist/TickFlowStockPanel-intel-x86_64.dmg`。

- [ ] **Step 1: 固化 desktop extra**

保持 `pywebview>=5.0`；无需增加 keyring。PyInstaller 作为构建工具由脚本安装，不进入运行依赖。

- [ ] **Step 2: 更新 spec**

`target_arch="x86_64" if _IS_MACOS else None`；macOS `info_plist` 增加：

```python
"LSMinimumSystemVersion": "10.15",
"NSAppTransportSecurity": {"NSAllowsArbitraryLoads": False, "NSAllowsLocalNetworking": True},
```

保留 `packaging/icon.icns`，不运行 `generate_icon.py`。将 `app.desktop_client` 子模块加入 hidden imports。

- [ ] **Step 3: 写构建脚本**

```bash
#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == x86_64 ]]
cd "$root/frontend"
pnpm install --frozen-lockfile
pnpm build
cd "$root/backend"
uv sync --no-dev --extra desktop
uv pip install pyinstaller
uv run pyinstaller ../packaging/tickflow.spec --noconfirm --clean
codesign --force --deep --sign - dist/TickFlowStockPanel.app
mkdir -p "$root/dist"
hdiutil create -volname "TickFlow Stock Panel" -srcfolder dist/TickFlowStockPanel.app -ov -format UDZO "$root/dist/TickFlowStockPanel-intel-x86_64.dmg"
shasum -a 256 "$root/dist/TickFlowStockPanel-intel-x86_64.dmg" > "$root/dist/TickFlowStockPanel-intel-x86_64.dmg.sha256"
```

- [ ] **Step 4: 写验证脚本**

脚本必须验证：

```bash
file backend/dist/TickFlowStockPanel.app/Contents/MacOS/TickFlowStockPanel | grep -q 'x86_64'
find backend/dist/TickFlowStockPanel.app -type f \( -name '*.so' -o -name '*.dylib' \) -print0 | xargs -0 file > reports/macos_intel_acceptance/native_arch.txt
! grep -E 'arm64(,|$)' reports/macos_intel_acceptance/native_arch.txt
codesign --verify --deep --strict --verbose=2 backend/dist/TickFlowStockPanel.app
hdiutil verify dist/TickFlowStockPanel-intel-x86_64.dmg
TICKFLOW_DESKTOP_SMOKE=1 backend/dist/TickFlowStockPanel.app/Contents/MacOS/TickFlowStockPanel
! lsof -nP -iTCP -sTCP:LISTEN | grep TickFlowStockPanel
```

另外运行 frozen Python smoke endpoint，断言可导入 `polars`、`pyarrow`、`duckdb`、`webview`。

- [ ] **Step 5: 构建、验证并提交脚本**

```bash
chmod +x scripts/build_macos_intel.sh scripts/verify_macos_intel_app.sh
./scripts/build_macos_intel.sh
./scripts/verify_macos_intel_app.sh
git add backend/pyproject.toml packaging/tickflow.spec scripts/build_macos_intel.sh scripts/verify_macos_intel_app.sh
git commit -m "build: add private Intel macOS DMG"
```

DMG 和 `.app` 不提交 Git；只提交脚本与后续哈希报告。

### Task 6: 安装、升级与验收

**Files:**
- Create: `docs/macos-intel-private-runbook.md`
- Create: `reports/macos_intel_acceptance/results.md`
- Create: `reports/macos_intel_acceptance/dmg.sha256`

**Interfaces:**
- Produces: 可重复安装与覆盖升级证据。

- [ ] **Step 1: 写 runbook**

包含：挂载 DMG、拖入 `/Applications`、首次 Finder 右键打开、配置 Tailscale URL、登录、日志目录、数据备份、覆盖升级、卸载 App 与可选保留 Application Support 数据。明确无公证和无自动更新。

- [ ] **Step 2: 产物验收**

```bash
./scripts/verify_macos_intel_app.sh
shasum -a 256 dist/TickFlowStockPanel-intel-x86_64.dmg | tee reports/macos_intel_acceptance/dmg.sha256
```

- [ ] **Step 3: 人工流程**

完成：DMG 安装、右键打开、红色 Finder/Dock 图标、单实例、关闭后端清理、重启恢复 client URL、Keychain session 仍可用、退出登录后 Keychain 条目删除、覆盖安装后 Application Support 数据保留。

- [ ] **Step 4: 密钥扫描**

```bash
grep -R -a -E 'TICKFLOW_API_KEY[=]|TUSHARE_TOKEN[=]|DEEPSEEK_API_KEY[=]|tf_session[=]' backend/dist/TickFlowStockPanel.app && exit 1 || true
```

Expected: 零命中。

- [ ] **Step 5: 提交验收文档**

```bash
git add docs/macos-intel-private-runbook.md reports/macos_intel_acceptance
git commit -m "test: record Intel Mac app acceptance"
```

报告不得包含 Keychain token、登录密码、Cookie 或复盘正文。

# tickflow-stock-panel P0 修复与全链路评测校验报告

> 运行态更新（2026-07-23）：本报告验收的分支现已部署到云端 3019。部署、镜像 digest、回滚点和替换后验证见 `reports/tickflow_p0_cloud_upgrade_20260723.md`。下文 `NOT_DEPLOYED` 结论仅代表 2026-07-22 的原始快照。
>
> 验收时间：2026-07-22（Asia/Shanghai）  
> 权威工作区：`/Users/macbookpro/Documents/TradingView 量化/tickflow-multiclient-app`  
> 基线：`b524b94b335d51e630bdd08012c832d9522917c0`  
> 验收分支：`codex/tickflow-p0-validation`  
> 报告编写时源码 HEAD：`0c2b73d521f0f8b54e8ec8ccf4de301f02963e10`  
> 总判定：`SOURCE_P0_FIXED / LOCAL_VALIDATION_PASS_WITH_RUNTIME_GAP / NOT_DEPLOYED`

本报告接续 `reports/tickflow_project_completion_ai_handoff.md` 中重新打开的 P0。它只验证研究工作台源码和现有私网运行边界，不构成投资建议，也不代表策略有效、数据已获正式发布许可或云端已升级。

## 1. 执行结论

### 1.1 已完成

- 修复增量指标流水线历史预热引用未定义 `_cast` 的问题。
- 修复已复权历史行情与原始新行情合并后被再次前复权的问题。
- 将历史数据读取异常收窄到明确的文件、I/O 和损坏存储异常，`NameError`、`ColumnNotFoundError` 等编程错误不再静默降级为空数据。
- 明确保留损坏或空 Parquet 的降级行为，只捕获具体 `ComputeError`/`NoDataError`，不恢复宽泛 `PolarsError`。
- 新增真实临时 Parquet 和非空复权因子的增量管道回归，确认历史前缀保持单次复权。
- 清零后端当前全部 Ruff `F821` 未定义名称问题，共修复 4 处。
- 新增 GitHub Actions 后端质量门禁：编译、`F821`、全量后端测试。
- 完成后端、前端、TypeScript、生产构建、Playwright、多端页面和 Gold Stage A 非 Docker 安全测试。
- 只读核验现有云端运行态；未部署、未换镜像、未改密钥或功能开关。

### 1.2 不能宣称

- 不能宣称云端和现有 Intel Mac App 已包含本次 P0 修复。
- 不能宣称本轮完整执行了 `verify_gold_stage_a.sh`；本机 Docker daemon 不可用。
- 不能宣称当前云端已可生成 AI 个股复盘；认证、数据源和 AI 仍未初始化。
- 不能宣称全库 Ruff 已清零；本轮只把高风险 `F821` 设为阻断门禁。
- 不能宣称生产就绪、无人值守就绪或金融日报自动发布就绪。

## 2. 问题复现与根因

### 2.1 修复前证据

使用真实临时 Parquet 分区调用 `_load_recent_history()`，修复前得到：

```text
历史数据加载失败: name '_cast' is not defined
history_rows=0
```

同时，基线后端测试仍为 `1282 passed`。这说明缺陷被宽泛 `except Exception` 吞掉，普通测试绿灯不能证明增量历史预热有效。

### 2.2 根因

- `_cast` 只在 `run_pipeline()` 的局部作用域中定义。
- `_load_recent_history()` 位于该作用域之外，却把 `_cast` 传给 `scan_enriched_parquet()`。
- `scan_enriched_parquet()` 自身已经固化兼容 schema 和整数转浮点扫描配置，无需从调用方传入 `_cast`。
- 宽泛异常捕获把 `NameError` 降级为空 DataFrame，造成指标历史窗口静默退化。
- 初次恢复历史读取后，独立代码审查又发现：历史 enriched 行已经前复权，却与原始 `raw_new` 一起再次传给 `compute_enriched()`；历史收盘价 `50.0` 会被第二次调整为 `25.0`。
- 捕获全部 `PolarsError` 还会吞掉列名错误等查询编程缺陷；CI 的 `uv sync --frozen` 也不会验证 `uv.lock` 是否与项目声明同步。

### 2.3 修复后证据

相同真实 Parquet 复现实验得到：

```text
history_rows=1
history_close=[20.5]
```

非空复权因子增量测试在修复前明确失败：

```text
expected close history: [50.0, 60.0]
actual close history:   [25.0, 60.0]
```

最终实现把“过滤停牌、保留原价、应用前复权”和“计算指标/信号”拆开：只对新原始行情做复权，再与已复权历史合并后计算指标。修复后输入恢复为 `[50.0, 60.0]`，原始收盘价保持 `[100.0, 60.0]`；`NameError` 和 Polars `ColumnNotFoundError` 均会向上抛出。

## 3. 本轮代码变更

| 领域 | 文件 | 变更 |
|---|---|---|
| 指标正确性 | `backend/app/indicators/pipeline.py` | 移除越界 `_cast`；拆分复权与指标计算；避免历史重复复权；收窄异常类型 |
| 回归测试 | `backend/tests/test_pipeline_incremental_history.py` | 增加真实/损坏 Parquet、非空复权因子和两类编程错误不可吞没测试 |
| 分钟 K API | `backend/app/api/kline.py` | 补齐 `asyncio` 导入 |
| 盘后任务 | `backend/app/jobs/daily_pipeline.py` | 补齐 `date` 类型导入与注解 |
| K 线同步 | `backend/app/services/kline_sync.py` | 补齐 `date` 导入 |
| CI | `.github/workflows/backend-quality.yml` | 增加锁文件一致性、编译、F821 和后端测试门禁 |
| 执行计划 | `docs/superpowers/plans/2026-07-22-tickflow-p0-validation.md` | 固化 TDD、验证和发布边界 |

提交序列：

```text
0c2b73d fix: preserve corrupt history fallback
0f081c5 ci: validate backend lockfile consistency
927314d fix: prevent incremental double adjustment
1d0ef6b test: remove stale noqa from history regression
5797714 ci: enforce backend undefined-name gate
2722c12 fix: resolve backend undefined names
aff5443 fix: restore incremental indicator history warmup
dde7ad5 docs: plan P0 correctness validation
```

相对基线的规模为 7 个文件、`+640 / -21`；其中 408 行是执行计划，不是运行时代码。

## 4. 自动化验证结果

| 验证项 | 结果 | 判定 |
|---|---:|---|
| P0 回归测试 | `5 passed` | 通过 |
| P0 + Parquet + 全量重建测试 | `9 passed` | 通过 |
| 锁文件一致性 | `uv sync --locked --extra dev --dry-run` exit `0` | 通过 |
| 后端全量 pytest | `1287 passed, 13 warnings` | 通过 |
| Ruff 未定义名称 | `F821 = 0` | 通过 |
| Python compileall | exit `0` | 通过 |
| 前端 Vitest | `97 passed`，22 个测试文件 | 通过 |
| TypeScript `tsc -b` | exit `0` | 通过 |
| Vite 生产构建 | 2697 modules，9.60s | 通过，存在体积告警 |
| PWA 产物 | 69 个 precache entries，2794.03 KiB | 通过 |
| Playwright | `25 passed, 2 skipped`，约 2 分钟 | 通过 |
| Gold 非 Docker 安全测试 | `34 passed` | 通过 |
| Compose 静态端口检查 | loopback bind 符合预期 | 通过 |
| 完整 Gold Stage A 脚本 | 未执行 | 本机 Docker daemon 不可用 |

### 4.1 测试增量解释

修复前全量后端为 `1282 passed`，最终为 `1287 passed`。新增 5 项分别覆盖：真实历史读取、`NameError` 不可吞没、Polars 列查询错误不可吞没、损坏 Parquet 可降级、非空复权因子下历史不可重复复权。

### 4.2 Playwright 前置条件

第一次运行 Playwright 因配置使用 `vite preview` 且本地尚无 `frontend/dist` 而退出：

```text
The directory "dist" does not exist. Did you build your project?
```

执行正式 `pnpm build` 后再次运行，最终结果为 `25 passed, 2 skipped`。这属于测试运行前置条件，不是最终产品用例失败。

### 4.3 构建性能观察

生产构建成功，但 Vite 报告较大的前端 chunk：

- 主入口约 566.69 kB，gzip 约 175.37 kB。
- ECharts chunk 约 1,034.94 kB，gzip 约 343.42 kB。

这不会阻断当前私网研究工作台，但会影响手机首次载入和弱网体验，应作为 P1 做路由级懒加载、图表按需引入和 bundle budget，而不是与本次 P0 混改。

### 4.4 独立代码审查

独立审查没有停留在 `_cast` 表面修复，而是复现出历史行情重复复权，并指出 Polars 查询错误吞没和 frozen lock 风险。三项均已纳入 TDD 修复：

- 重复复权：新增非空因子的增量管道回归，先见 `[25.0, 60.0]` 失败，再恢复为 `[50.0, 60.0]`。
- 错误边界：新增 `ColumnNotFoundError` 必须传播测试。
- 依赖门禁：CI 改用 `uv sync --locked`，本地 dry-run 通过。
- 存储容错：二次复审指出损坏 Parquet 不应被当作编程错误；新增先红后绿测试，并只补捕获 `ComputeError`/`NoDataError`。

最终复审结论为“无剩余 P0/P1，当前修复可通过最终审查”。本报告以修订后的全量回归为准，不沿用独立审查前的初版“P0 已完成”结论。

## 5. 静态检查债务

全库 Ruff 盘点仍有 `1142` 项，命令退出码为 1。数量最多的规则为：

| 规则 | 数量 | 说明 |
|---|---:|---|
| `RUF002` | 323 | docstring Unicode 标点 |
| `RUF100` | 229 | 无效或过期 noqa |
| `RUF003` | 202 | 注释 Unicode 标点 |
| `RUF001` | 122 | 字符串 Unicode 标点 |
| `I001` | 48 | import 排序 |
| `SIM105` | 30 | 可简化异常处理 |
| `UP045` | 20 | Optional 类型现代化 |
| `F401` | 18 | 未使用导入 |
| `UP037` | 18 | 引号类型注解现代化 |
| `B023` | 13 | 循环变量闭包绑定风险 |

另有 `F841=7`、`RUF006=5` 等可能影响维护或异步任务可靠性的项目。建议分批建立新代码门禁：先处理 `F401/F841/B023/RUF006` 等行为风险，再单独处理格式类债务。不要一次性自动修复 1142 项，以免造成大范围无关变更。

## 6. CI 门禁评估

新增 `.github/workflows/backend-quality.yml`，在 backend 相关 PR、main push 和手工触发时执行：

1. `uv sync --locked --extra dev`
2. `python -m compileall -q app`
3. `ruff check app --select F821`
4. `pytest -q`

该门禁针对本次真实逃逸路径：普通 pytest 曾在缺陷存在时仍全绿，因此锁文件一致性、编译、静态未定义名称和真实回归测试必须共同存在。工作流 YAML 已在本地解析，`--locked` dry-run 已通过。

分支已推送到 `dunmin1980-ux/tickflow-stock-panel:codex/tickflow-p0-validation`。工作流的 `push` 事件只监听 `main`，当前又未创建 PR，因此 GitHub 公共 API 返回该分支 Actions `total_count=0`，不能宣称远端 CI 已通过。应通过 PR 触发 `pull_request` 门禁；本机 `gh` 未登录，本轮没有绕过认证自动建 PR。

## 7. 云端与发布物真相

本轮只做只读核验，没有 SSH 写操作、部署或重启。

| 项目 | 当前云端证据 | 是否含本次修复 |
|---|---|---|
| 云端 Git HEAD | `2ab47c18348995cebfd39f616de6abbc0955d4b8` | 否 |
| 健康检查 | `status=ok, version=0.1.86, mode=none` | 仅说明旧服务存活 |
| 认证 | `configured=false, authenticated=false` | 未初始化 |
| 容器镜像 | `sha256:02d556047ec97314eeb10f8ae8f7e902b59efc16d1c93387d4b5d7933f5f56a5` | 否 |
| 工作区同步 | `WORKSPACE_SYNC_ENABLED=true` | 旧镜像能力 |
| Gold 集成 | `GOLD_WORKSPACE_ENABLED=false` | 按边界保持关闭 |
| 监听地址 | `127.0.0.1:3018`、`127.0.0.1:3019` | loopback 合规 |
| Tailscale Serve | tailnet-only 转发到 `127.0.0.1:8787/3019` | 私网边界合规 |

发布物状态必须拆开表述：

| 产物 | 本次状态 |
|---|---|
| 本地源码 | 已修复并通过自动化验证 |
| 本地 `frontend/dist` | 已构建用于验证，属于忽略产物，不是正式发布包 |
| Intel Mac App / DMG | 仍是先前构建，未包含本次修复 |
| 云端镜像 | 仍是旧镜像，未包含本次修复 |
| 手机 PWA | 当前访问旧云端前端，未发布本次分支 |

## 8. 依赖与可重复运行观察

- 后端首次 `uv sync --frozen --extra desktop --extra dev` 遇到 `openai==2.38.0` 下载 30 秒超时；设置 `UV_HTTP_TIMEOUT=180` 后成功安装 Python 3.12.13 和 69 个包。
- 系统全局 pnpm 为 11.9.0，而项目声明 `pnpm@9.10.0`。全局版本曾生成无效的 `frontend/pnpm-workspace.yaml`，已删除；改用 `corepack pnpm` 9.10.0 后完成安装、构建和测试。
- 推荐所有本地与 CI 命令固定使用 `corepack pnpm`，避免 pnpm 主版本漂移。
- 本机 Docker CLI 存在但 daemon 不可用；涉及容器运行态的完整复验应转移到 Docker 可用的 clean runner 或受控云端候选实例。

## 9. 产品与技术评测

| 维度 | 评分（5 分） | 本轮结论 |
|---|---:|---|
| 日线研究源码正确性 | 4.0 | P0 已修复并有真实 Parquet 回归；仍需候选镜像验证 |
| 自动化回归能力 | 4.0 | 后端/前端/E2E 覆盖良好，新增 F821 门禁；全库 lint 债务较多 |
| 手机 PWA 基础设施 | 4.0 | 本轮 E2E 通过，云端私网入口存活；真机安装仍需人工验收 |
| Intel Mac App | 3.5 | 既有产物可作为基础，尚未重建本次修复版本 |
| 数据与 AI 可用性 | 2.0 | 云端 mode=none、AI 未配置，尚不能形成三股真实日线复盘闭环 |
| Gold Stage A 安全性 | 3.5 | 34 项安全测试与静态绑定通过，完整 Docker 脚本本轮未跑 |
| 生产可运维性 | 2.5 | 私网边界明确，但认证、数据源、AI、镜像发布和回滚演练未完成 |

项目当前最准确的定位仍是：

> 可继续推进的 A 股日线研究工作台与个股复盘素材源基础设施；源码 P0 已解除，但实际研究实例和发布闭环尚未完成。

## 10. 风险与发布判定

### P0

- **源码 P0：已解除。** 增量历史不再因 `_cast` 越界静默为空，也不会在非空复权因子下被二次复权。
- **发布 P0：仍未解除。** 现有云端、PWA 和桌面 App 尚未重建，因此用户实际使用的发布物仍没有本次修复。

### P1

- 在 clean runner 构建候选镜像和 Intel App，执行完整 `verify_gold_stage_a.sh` 与多端安全门禁。
- 初始化私有认证后，用不落盘密钥完成数据源、AI 和三股个股日线复盘验收。
- 分批处理行为风险 Ruff 规则，并建立增量门禁。
- 优化前端图表 bundle 和移动端首次加载。

### P2

- 评估 Obsidian 固定 frontmatter 和旁路导出原型的选择性迁移。
- 完成真机 PWA 安装、Mac 登录、Keychain 恢复和离线只读人工验收。
- 在 AI Markdown 确定性校验稳定前，继续暂停 Telegram、OpenClaw 和正式金融日报自动发布。

### 发布决策

当前判定为：

```text
SOURCE_P0_FIXED
LOCAL_VALIDATION_PASS_WITH_RUNTIME_GAP
NOT_DEPLOYED
NOT_PRODUCTION_READY
```

允许进入“候选构建与受控部署验收”，不允许直接把当前源码结论等同于生产发布完成。

## 11. 下一步执行顺序

1. 从已推送的 `codex/tickflow-p0-validation` 创建 PR，确认 GitHub Actions 质量门禁实际通过；独立本地代码审查已无剩余 P0/P1。
2. 从审查通过的精确 commit 构建带唯一 tag/digest 的云端候选镜像和 Intel App；先在隔离候选实例验收，再决定是否替换现有服务。
3. 在用户自行配置私有密码、TickFlow/Tushare 能力和 DeepSeek/OpenAI-compatible 密钥后，完成派林生物、中金黄金、东方财富三股的日期、收盘价、均线关系、涨停标签、量能单位和 AI Markdown 人工核验闭环。

仍然保持：不接券商、不接实盘、不自动下单、不开发 Telegram、不接 OpenClaw 主链路、不做分钟级交易系统、不自动发布正式金融日报。

## 12. 复验命令

```bash
cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-multiclient-app/backend"
PYTHONPATH=. .venv/bin/pytest tests/test_pipeline_incremental_history.py -q
.venv/bin/ruff check app --select F821
PYTHONPATH=. .venv/bin/python -m compileall -q app
PYTHONPATH=. .venv/bin/pytest -q

cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-multiclient-app/frontend"
corepack pnpm test -- --run
corepack pnpm exec tsc -b --pretty false
corepack pnpm build
corepack pnpm exec playwright test

cd "/Users/macbookpro/Documents/TradingView 量化/tickflow-multiclient-app"
backend/.venv/bin/pytest \
  backend/tests/test_gold_path_security.py \
  backend/tests/test_gold_runtime_lock.py \
  backend/tests/test_gold_tickflow_errors.py \
  backend/tests/test_gold_sdk_http_budget.py \
  backend/tests/test_rate_limit_safety_budget.py -q
```

完整 Gold Stage A 命令仅在 Docker daemon 可用时执行：

```bash
./scripts/verify_gold_stage_a.sh
```

## 13. 最终结论

本轮已经用可复现失败、真实 Parquet、非空复权因子、编程错误传播、全量后端、前端构建和多端 E2E 证据攻破指标历史预热 P0，并修复独立审查发现的历史重复复权。CI 现在同时检查锁文件一致性、编译、未定义名称和全量测试。项目可以继续推进候选构建，但现有云端和桌面 App 尚未包含修复，数据源与 AI 也尚未形成真实三股复盘闭环，因此当前仍是“源码通过、运行发布待验收”，不是正式完成态。

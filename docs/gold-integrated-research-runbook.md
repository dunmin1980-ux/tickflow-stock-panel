# Gold 集成研究工作区运行手册

Gold 集成研究工作区是现有 TickFlow Stock Panel 内的隔离研究功能。它默认关闭，只采集固定标的的本地 Shadow 数据，不提供交易、通知、发布或外部发送能力。

## 启用前检查

1. 在现有面板的“设置 → 凭据与能力”中配置并检测 TickFlow Key。Gold 复用现有 `data/user_data/secrets.json` Key 存储及能力检测，不使用独立 Key 文件；不要把 Key 写入本手册、日志或版本库。
2. 确认 Key 对应的能力包含实时批量行情和日 K 批量查询。缺少 Key 或能力时，工作区仍可打开，但采样会保持失败状态，不会降级到其他数据源。
3. 创建 `<DATA_DIR>/user_data/gold_shadow/holidays.json`。默认 `DATA_DIR` 为仓库根目录的 `data/`，因此默认路径是 `data/user_data/gold_shadow/holidays.json`。

`holidays.json` 必须是 UTF-8 JSON 日期数组，日期使用严格的 `YYYY-MM-DD` 格式，并覆盖运行观察期内的中国 A 股非周末休市日。例如：

```json
[
  "2026-10-01",
  "2026-10-02"
]
```

缺失、不可读、格式错误或日期非法时，运行时会以 `calendar_unconfigured` / `trading_calendar_unavailable` 失败关闭，不会把当天计为完整交易日。周六、周日始终不采样。

## 启用

在根目录 `.env` 中设置：

```dotenv
GOLD_WORKSPACE_ENABLED=true
```

然后重启现有应用进程：

```bash
./dev.sh
```

或使用现有 Compose 部署：

```bash
docker compose up -d --build
```

打开正常平台导航中的 `/gold`。启用后应显示“中金黄金”、固定代码 `600489.SH`、本地采样健康状态、Legacy 导入与 Shadow 对账、以及观察门槛。侧边栏和原有认证流程保持不变。

## Docker 构建检查

部署前可在仓库根目录运行：

```bash
docker compose config --quiet
docker compose build
```

默认国内构建会把 Debian deb822 软件源切换到腾讯镜像；Python 依赖仍使用既有的清华主索引和阿里云备用索引。`uv` 下载超时在构建命令内设为 `120` 秒，不会写入最终应用运行环境，也不会改变依赖版本、锁文件、重试次数或数据源。Task 14 验证中，上述原样构建成功生成 `tickflow-gold-integrated-design-app:latest`，且没有启动 Compose 服务。

若构建日志仍报告 Python wheel 下载超时，先确认错误中的 `UV_HTTP_TIMEOUT` 为 `120s`，再检查 Docker Desktop 网络和两个既有 Python 镜像的连通性。不要通过填入真实 Key、修改锁文件或临时替换 Python 镜像来绕过构建失败。`docker compose build` 只构建镜像；需要启动时仍显式执行前述 `docker compose up -d --build`。

## 固定运行边界

- 标的固定为 `600489.SH`，不能由页面或环境变量改为其他证券。
- 调度固定为北京时间周一至周五每五分钟触发一次；仅在 `09:30-11:30` 和 `13:00-15:00` 会请求数据。交易日仍以必需的 `holidays.json` 进一步校验。
- 行情与 60 个已完成日收盘价只来自现有 TickFlow 付费能力路径。不得用本运行手册执行实时探针或绕过能力检查。
- 所有 Shadow 快照、候选、对账结果、观察评审和健康状态只写入 `<DATA_DIR>/user_data/gold_shadow/`。
- Legacy 文件仅允许操作员在 `/gold` 手动选择并导入。原始上传字节在内存中完成 UTF-8 解析、校验和标准化后即丢弃；磁盘只保留固定标准化字段、摘要和安全文件名，不保存原始文本、来源行或凭据样内容。
- Gold 的外部发送端口无条件拒绝调用。没有 Telegram、飞书、Webhook、经纪商、OpenClaw、发布或其他外发适配器；候选不会进入平台通知管线，也没有买卖、下单或仓位操作。

## 日常检查

1. 查看 `/gold` 顶部状态，确认工作区已启用、最近成功时间、最近市场日期和连续失败数符合预期。下一次调度时间不在顶部状态中显示；需要时调用 `GET /api/gold/health` 查看 `next_scheduled_run`。
2. `calendar_unconfigured` 或 `trading_calendar_unavailable`：检查 `holidays.json` 是否存在、可读且为严格日期数组，然后重启应用。
3. `gold_sampling_failed`：调度器会把未处理的行情网关、能力、客户端或历史数据异常统一持久化为此错误，不会保留网关内部错误码。先调用 `GET /api/gold/health` 确认健康状态和下一次调度时间，再到现有“设置 → 凭据与能力”重新检测 Key 与订阅能力，并检查 TickFlow 网络可用性；不要创建 Gold 专用 Key 文件。
4. 每个交易日手动导入 Legacy 文件、运行同日对账，并只对当前 canonical 对账运行记录人工完整窗口评审。被后续运行取代的结果不能计入门槛。
5. 外发尝试数必须为 `0`。任何非零计数或外发状态损坏都会使观察门槛失败；不要尝试接通外部通道。

## 十日观察门槛

门槛要求 10 个完整交易日。每一天都必须满足：

- 日期由 `holidays.json` 认定为交易日；
- 当日 canonical 对账运行存在且自动容差检查通过；
- 操作员对同一个 canonical 运行记录完整窗口人工评审并标记通过。

此外还必须完成一次重启恢复人工评审，且外发尝试数保持为 `0`。满足全部条件后状态只会变为 `review_eligible`（可进入独立评审），不会自动启用通知、交易、发布或任何外部发送。后续启用任何新能力必须经过独立设计、实现和评审，不属于本工作区。

## 关闭与回滚

在根目录 `.env` 中设置：

```dotenv
GOLD_WORKSPACE_ENABLED=false
```

重启应用或 Compose 服务。关闭后 Gold 运行时和五分钟采样器不会初始化，`/gold` 显示未启用状态。不要删除 `<DATA_DIR>/user_data/gold_shadow/`；关闭开关不会删除或迁移已有快照、导入、对账及评审数据，重新启用时会继续使用同一数据目录。

如需回滚应用版本，先停止进程并备份整个 `<DATA_DIR>/user_data/gold_shadow/`，但仍不要删除原数据。旧版本无法识别的新状态应保持原样，待兼容版本恢复后再读取。

## Legacy 导入兼容策略

导入索引中的 `normalized_sha256` 是标准化数据完整性校验的必需字段。早期导入若缺少该字段，读取会以 `legacy_import_requires_manual_reimport_from_original_bytes` 失败关闭：

1. 不要手工补写 `normalized_sha256`，不要修改旧索引或标准化 JSONL，也不要尝试自动迁移。
2. 找到操作员原始保留的 Legacy 文件，在 `/gold` 中重新手动导入。
3. 使用新导入批次重新运行对应日期的对账和人工评审。

系统从不保留原始上传字节，因此没有原始文件时无法重建该校验值；这是有意的 fail-closed 策略。

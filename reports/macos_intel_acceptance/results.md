# Intel Mac 私有 App 验收结果

## 结论

- 状态：`CONDITIONAL_PASS`
- 自动化范围：通过
- 人工门槛：Finder 首次右键打开、红色 Finder/Dock 图标目视确认、真实云端认证与 GUI 会话恢复/退出
- 环境：macOS 14.8.5，Intel `x86_64`
- 安装位置：`/Applications/TickFlowStockPanel.app`
- 用户数据：`~/Library/Application Support/TickFlowStockPanel/`
- 运行代码基线：`7c46660`

## 产物

- 文件：`dist/TickFlowStockPanel-intel-x86_64.dmg`
- 字节数：`187042992`
- SHA-256：`66b9dfae51d1d757b0c302d5e2fc1407d2499b8cf4507fb7fe95a3dfda52aa15`
- App tree SHA-256：`750f9f30de3b9663f24115dc8d4ef480b810bd2fd3b7519535fccd6c3c7c5c30`
- DMG 完整性：通过 `hdiutil verify`
- 签名：ad-hoc，`codesign --verify --deep --strict` 通过
- 公证：无
- 自动更新：无

## 自动化验收证据

- `./scripts/verify_macos_intel_app.sh`：`MACOS_INTEL_APP_OK`
- 构建 App 与已安装 App tree SHA-256 完全一致。
- 完整安全门禁：`MULTICLIENT_SECURITY_OK`。

| 检查项 | 结果 | 说明 |
|---|---|---|
| DMG 只读挂载与安装 | 通过 | 从只读 DMG 安装到 `/Applications` |
| 覆盖升级 | 通过 | 已安装版本保留为时间戳备份，当前版本完成替换 |
| Application Support 保留 | 通过 | 用户目录未随 App 覆盖删除 |
| URL 配置持久化 | 通过 | Tailscale HTTPS URL 与客户端模式可跨进程重载 |
| App 主程序架构 | 通过 | `x86_64` |
| 原生依赖架构 | 通过 | `.so`/`.dylib` 均包含 `x86_64` |
| App 签名 | 通过 | ad-hoc 深度严格校验通过 |
| 图标文件 | 通过 | 源 `.icns` 在构建中保持不变；视觉仍需人工确认 |
| 冻结 App 启动 | 通过 | 从 `/tmp` 且清空环境后 smoke 正常退出 |
| 启动目录隔离 | 通过 | 不读取或修改启动目录 `.env`，用户数据只写 Application Support |
| 单实例与干净关闭 | 通过 | 退出后无 App 进程、监听端口或 `.desktop.lock` |
| macOS Keychain | 通过 | 测试会话完成 save/load/delete，测试条目已删除 |
| 私密文件打包检查 | 通过 | App 不包含 `.env`、会话文件、用户数据库或报告产物 |
| canary 扫描 | 通过 | App、DMG、缓存、日志和云端隔离响应均无 canary 明文 |

## 数据与认证边界

- 工作区缓存：`~/Library/Application Support/TickFlowStockPanel/cache/workspace/`
- 计算输入：`~/Library/Application Support/TickFlowStockPanel/compute_inputs/`
- 桌面日志：`~/Library/Application Support/TickFlowStockPanel/desktop.log`
- 启动器日志：`~/Library/Logs/TickFlowStockPanel/`
- 云端会话只存 macOS Keychain；配置文件仅保存 URL 和客户端模式。
- 当前云端认证尚未初始化，因此真实登录、重启后会话恢复和界面退出登录仍为人工门槛。

## 回滚与清理候选

当前安装前一版保留在：

```text
/Applications/TickFlowStockPanel.before-storage-schema-20260722_143703.app
```

更早验收备份及隔离副本也保留在 `/Applications`，确认当前 App 稳定后可人工删除：

```text
TickFlowStockPanel.before-multiclient-20260722_124620.app
TickFlowStockPanel.before-final-canary-20260722_131205.app
TickFlowStockPanel.quarantined-cwd-env-20260722_133334.app
```

## 安全边界

- 不包含券商连接或自动交易。
- 不接 Telegram。
- 不接入 OpenClaw 主链路。
- 未记录密码、Cookie、Keychain token、API Key 或复盘正文。

# Intel Mac 私有 App 验收结果

## 结论

- 状态：`CONDITIONAL_PASS`
- 自动化范围：通过
- 人工门槛：Finder 首次右键打开、红色 Finder/Dock 图标目视确认、真实云端认证与 GUI 会话恢复/退出
- 环境：macOS 14.8.5，Intel `x86_64`
- 安装位置：`/Applications/TickFlowStockPanel.app`
- 用户数据：`~/Library/Application Support/TickFlowStockPanel/`
- 构建基线：`c85279b` (`build: add private Intel macOS DMG`)

## 产物

- 文件：`dist/TickFlowStockPanel-intel-x86_64.dmg`
- 字节数：`181399898`
- SHA-256：`a1816f3ce09aca17b182b8a613434c9f04e2ed9c694341a974533f1f7ef7f9b9`
- DMG 完整性：通过 `hdiutil verify`
- 签名：ad-hoc，`codesign --verify --deep --strict` 通过
- 公证：无
- 自动更新：无

## 自动化验收证据

- `./scripts/verify_macos_intel_app.sh`：`MACOS_INTEL_APP_OK`
- 桌面定向回归：`33 passed`；另有 2 个第三方 WebSocket 弃用警告

| 检查项 | 结果 | 说明 |
|---|---|---|
| DMG 只读挂载与安装 | 通过 | 从 DMG 安装到 `/Applications` |
| 覆盖升级 | 通过 | 对已安装 App 执行同源 DMG 覆盖 |
| Application Support 保留 | 通过 | 临时哨兵在覆盖后仍存在，验收后已删除 |
| URL 配置持久化 | 通过 | Tailscale HTTPS URL 与 `hybrid` 模式在独立进程重载及覆盖升级后保持 |
| App 主程序架构 | 通过 | `x86_64` |
| 原生依赖架构 | 通过 | 验证脚本检查 136 个 `.so`/`.dylib`，均包含 `x86_64` |
| App 签名 | 通过 | ad-hoc 深度严格校验通过 |
| 图标文件 | 通过 | 源 `.icns` 与安装后 `.icns` 哈希均为 `ca270a638e182f35b32d9a897118d4eb6c90dfc84f060313456afb45202a1c45` |
| 冻结 App 启动 | 通过 | 安装前后 smoke 均正常退出 |
| 单实例 | 通过 | 活跃 PID 锁被安装后的冻结 App 正确拒绝；定向测试覆盖锁替换与拒绝 |
| 干净关闭 | 通过 | 退出后无 App 进程、无监听端口、无 `.desktop.lock` |
| macOS Keychain | 通过 | 测试会话完成真实 save/load/delete，测试条目已删除 |
| 测试状态清理 | 通过 | 临时哨兵、测试锁和测试 Keychain 条目均已删除 |
| 私密文件打包检查 | 通过 | App 不包含 `.env`、会话文件、用户数据库或报告产物 |
| 敏感字符串扫描 | 有条件通过 | 原始规则命中 3 个固定 `your-api-key` 文档占位符；占位符外命中为 0，报告命中为 0 |

URL 配置文件保留为非敏感运行配置；自动验收生成的本地缓存和日志也保留在 Application Support，未修改或删除非测试状态。

简报指定的原始 grep 不能声明零命中。3 个命中全部位于打包后的 `tickflow-0.1.24.dist-info/METADATA`，内容是上游文档中的 `your-api-key` 示例；`TUSHARE_TOKEN`、`DEEPSEEK_API_KEY`、`tf_session` 以及占位符外的同类模式均为零命中。由于 Task 6 禁止修改源码，本次不篡改第三方元数据或重签安装包，仅将该已知占位符作为验收例外记录。

## Keychain 与认证边界

真实 macOS Keychain 已使用独立测试地址验证保存、读取和删除。测试会话值未写入报告、命令行参数或仓库，测试条目在验收过程中删除。

没有提供或创建云端密码，因此以下项目仍为人工门槛：

- 真实云端登录；
- App 重启后真实会话恢复；
- 从界面退出登录后确认真实 Keychain 会话删除。

## 仍需人工完成

1. 在 Finder 中右键首次打开未公证 App，并确认 Gatekeeper 提示符合预期。
2. 目视确认 Finder 与 Dock 使用红色 TickFlow 图标。
3. 使用真实但不外泄的云端密码登录，重启 App 验证会话恢复，再从界面退出登录。

在上述三项完成前，不把 Phase 2 标记为无条件发布就绪；可用于本机旁路试用和后续 Phase 3 开发。

## 安全边界

- 不包含券商连接或自动交易。
- 不接 Telegram。
- 不接入 OpenClaw 主链路。
- 未记录密码、Cookie、Keychain token、API Key 或复盘正文。

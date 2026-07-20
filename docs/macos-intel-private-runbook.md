# TickFlow Intel Mac 私有 App 运行手册

## 适用范围

本手册适用于 Intel (`x86_64`) Mac 的私有构建：

- App：`TickFlowStockPanel.app`
- DMG：`TickFlowStockPanel-intel-x86_64.dmg`
- 最低系统版本：macOS 10.15
- 运行模型：本地研究工作台，或通过 Tailscale 连接私有云端工作区

该构建没有 Apple Developer ID 公证，也没有自动更新。不要将 DMG 公开分发；升级前必须手工核验来源和 SHA-256。

## 安装前核验

在项目目录运行：

```bash
shasum -a 256 dist/TickFlowStockPanel-intel-x86_64.dmg
cat dist/TickFlowStockPanel-intel-x86_64.dmg.sha256
hdiutil verify dist/TickFlowStockPanel-intel-x86_64.dmg
```

本次验收的 SHA-256：

```text
a1816f3ce09aca17b182b8a613434c9f04e2ed9c694341a974533f1f7ef7f9b9
```

## 首次安装

1. 双击 DMG，或运行：

   ```bash
   hdiutil attach -nobrowse -readonly dist/TickFlowStockPanel-intel-x86_64.dmg
   ```

2. 将 `TickFlowStockPanel.app` 拖入 `/Applications`。
3. 在 Finder 的“应用程序”中右键 App，选择“打开”，再确认一次。未公证私有构建首次启动需要此步骤。
4. 确认 Finder 和 Dock 显示红色 TickFlow 图标。自动验收只能核对 `.icns` 哈希，视觉外观需人工确认。
5. 使用完毕后关闭窗口；App 应同时关闭本地后端，不应残留监听端口或进程。

不要把 App 放在 DMG 内长期运行。用户数据必须保存在：

```text
~/Library/Application Support/TickFlowStockPanel/
```

日志文件：

```text
~/Library/Application Support/TickFlowStockPanel/desktop.log
```

## 配置私有云端

1. 先在 Mac 上登录 Tailscale，并确认能访问同一 tailnet。
2. 打开 App 的“客户端连接”页。
3. 填写私有 HTTPS 地址，例如：

   ```text
   https://vm-0-9-ubuntu.tail21c236.ts.net:8443
   ```

4. 选择“混合”或“云端”模式并保存。
5. 输入云端工作区密码登录。密码只用于本次请求，不写入配置文件；返回的会话写入 macOS Keychain。
6. 退出登录后，确认界面显示未认证。退出登录会删除该云端地址对应的 Keychain 会话。

`desktop_client.json` 只保存 URL 和模式，不应包含密码、Cookie 或 API Key。不要把云端密码写入 `.env`、命令行参数、文档或聊天记录。

本次自动验收没有可用的云端密码，因此未执行真实认证登录。首次真实登录、重启后会话恢复和界面退出登录仍需人工完成。

## 数据备份

升级或排障前先退出 App，确认没有 `TickFlowStockPanel` 进程，再备份完整目录：

```bash
mkdir -p "$HOME/TickFlowBackups"
ditto \
  "$HOME/Library/Application Support/TickFlowStockPanel" \
  "$HOME/TickFlowBackups/TickFlowStockPanel-$(date +%Y%m%d-%H%M%S)"
```

备份可能包含自选股、研究数据、日志和本地配置，应按私有数据保护。Keychain 会话不会包含在目录备份中。

## 覆盖升级

1. 保留上一版 DMG，并记录新旧版本 SHA-256。
2. 完全退出 App，确认无残留进程或监听端口。
3. 备份 Application Support 目录。
4. 挂载新 DMG，将新 App 拖入 `/Applications`，在 Finder 提示时选择“替换”。
5. 不要删除 Application Support 目录；覆盖 App 不应清除该目录。
6. 启动后核对客户端 URL、模式、自选股和本地数据，再执行一次关闭检查。

本次验收以同一只读 DMG 对 `/Applications/TickFlowStockPanel.app` 执行重复覆盖，确认 Application Support 哨兵和 URL 配置均保留。

## 回滚

1. 退出当前 App。
2. 将当前 App 移到废纸篓，使用已核验 SHA-256 的上一版 DMG 重新安装。
3. 先保留现有 Application Support；若旧版本无法读取新数据，再退出 App，并从升级前备份恢复整个目录。
4. 回滚后重新核对 URL、模式、数据和关闭行为。

数据格式不承诺向后兼容，所以回滚必须依赖升级前备份，不能只保留旧 App。

## 卸载

1. 在 App 内退出云端登录，以删除 Keychain 会话。
2. 退出 App并确认无残留进程。
3. 在 Finder 中将 `/Applications/TickFlowStockPanel.app` 移到废纸篓。
4. 如需保留研究数据，不要删除 Application Support 目录。
5. 如需完全清除，在确认备份后，通过 Finder“前往文件夹”打开以下路径并删除：

   ```text
   ~/Library/Application Support/TickFlowStockPanel/
   ```

卸载 App 不会自动删除用户数据，也不会自动清理未退出登录的 Keychain 会话。

## 已知限制与人工门槛

- 无 Apple 公证，首次启动必须由用户在 Finder 右键确认。
- 无自动更新；每次升级都要人工核验 DMG 哈希并覆盖安装。
- 红色 Finder/Dock 图标仅完成文件哈希验证，仍需人工目视确认。
- 云端认证密码未提供，因此真实登录、重启后会话恢复和界面退出登录尚未验收。
- 不连接券商、不自动下单、不接 Telegram，也不接入 OpenClaw 主链路。

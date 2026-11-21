> [!WARNING]
> **需要解除系统完整性保护（SIP）的调试限制。** 运行 `csrutil enable --without debug` 即可，SIP 的其余保护可以保持开启。
> 本项目通过内存补丁修改运行中的系统进程。详见[系统要求](#系统要求)。

<p align="center">
  <a href="../../README.md">English</a> · <b>简体中文</b> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a>
</p>

<p align="center">
  <a href="https://x.com/jazminli57"><img src="https://img.shields.io/badge/Follow-%40jazminli57-000000?logo=x&logoColor=white" alt="Follow @jazminli57 on X"></a>
</p>

---

# DynamicEnabler

在带刘海的 MacBook 上启用 macOS 27 中隐藏的灵动岛，并将其与物理刘海融合。

<p align="center">
  <a href="#工作原理">工作原理</a> ·
  <a href="#系统要求">系统要求</a> ·
  <a href="#使用方法">使用方法</a> ·
  <a href="#已知限制">已知限制</a> ·
  <a href="#免责声明">免责声明</a>
</p>

---

macOS 27.3 及更高版本的 MenuBarAgent 内置了灵动岛实现，但仅在苹果内部版本中启用。本脚本通过内存补丁激活该功能，并调整其几何参数以适配物理刘海：

- 紧凑态和最小化状态的胶囊分布于刘海两侧。
- 展开态从屏幕顶部开始，环绕刘海显示。
- 系统 Live Activity（计时器、正在播放、AirDrop、屏幕录制、iPhone 镜像活动）和电池横幅的展开面板会加宽，以防内容被刘海遮挡。
- AirPods“已经移到iPhone”这类需要在刘海两侧留出空间的紧凑横幅不再被截断。

## 工作原理

- **激活。** MenuBarAgent 通过 `IsAppleInternalBuild()` 检查来保护 `EmulateCenterHUD` 和 `ShowSoftwareNotch` 两个偏好设置。脚本设置这两个偏好，并将对应的检查指令替换为 `nop`。
- **刘海几何。** 系统原生灵动岛以菜单栏下方一块 59 × 34 pt 的虚拟区域为基准绘制。脚本将返回该矩形的函数重定向到代码洞，让其在运行时从 `NSScreen` 读取真实的刘海矩形。MenuBarAgent 把岛的宽度上限设为 408 pt（按 59 pt 的传感器设计），脚本会按刘海多出的宽度提高这个上限。
- **面板宽度。** 每个展开面板的宽度固定在各自的客户端进程中。脚本钩住这些进程中的 SwiftUI frame 初始化函数，将展开范围内的宽度替换为适配刘海的值。对于宽度特殊的客户端（AirDrop、ControlCenter 中的电池横幅），使用专用补丁处理。
- **后台守护。** 计时器等 Live Activity 客户端按需启动。守护任务 `local.dynamic-island.watcher` 监控 MenuBarAgent 日志，在新客户端接入时自动打补丁，并在 MenuBarAgent 退出时一同退出。日志：`~/Library/Logs/dynamic_island_watcher.log`。 它还会盯着内置屏：分辨率变化或开合盖时，按新的刘海尺寸重新打全部补丁（看不到刘海时用 `classic`）。MenuBarAgent 会重启一次，岛大约 20 秒后恢复。
- **登录自启。** 脚本可将自身复制到 `~/Library/Application Support/DynamicEnabler/`，并安装 LaunchAgent `local.dynamic-island.autostart`，在每次登录时自动重新应用补丁。日志：`~/Library/Logs/dynamic_island_autostart.log`。
- **多显示器。** 默认只在主显示器上显示灵动岛。`--displays all` 让所有活跃显示器都显示，`--displays secondary` 让除主显示器以外的显示器显示。MenuBarAgent 用一个小函数决定每块屏的显示方式，菜单栏图标间距也用到它，所以脚本不改原函数，而是在代码洞后面放一份去掉主屏判断的副本，只把灵动岛相关的调用改到副本上。系统横幅会送到每个岛。由 [Eric Dou](https://github.com/Eric-Dou) 贡献。

所有补丁均在内存中进行，不修改系统卷上的任何文件。除非启用了登录自启，重启 MenuBarAgent 或 Mac 后补丁即失效。

## 系统要求

| 要求 | 说明 |
| --- | --- |
| macOS | 27.3 beta，已在 build 26C5035r（beta 1）和 26C5047e（beta 2）上测试；不支持 27.2 及更早版本。补丁位置按代码特征在本机二进制中查找，后续版本可能无需更新即可使用；任何特征不是恰好命中一次时，脚本会停止且不做任何改动。 |
| 硬件 | 任意 Mac。有激活的刘海显示器（带刘海的 MacBook 开盖）时，岛贴合刘海；没有时（无刘海的 Mac、合盖、仅外接显示器）自动切换为 `--layout classic`。无刘海环境尚未实测。 |
| SIP | 必须解除系统完整性保护（SIP）的调试限制：在恢复模式中运行 `csrutil enable --without debug`（推荐，SIP 其余保护保持开启）。完全关闭 SIP（`csrutil disable`）也可以。 |
| 工具 | 最新的 Xcode beta，并用 `sudo xcode-select -s /Applications/Xcode-beta.app/Contents/Developer` 切换过去。与 macOS 27 对应的命令行工具也可以；旧版工具（某些系统上 `xcode-select --install` 装的就是旧版）在 lldb 附加进程时会崩溃。 |
| 调试权限 | 需要免密调试：执行 `sudo DevToolsSecurity -enable`，并且当前用户在 `_developer` 组中。否则 lldb 会一直等待“开发者工具访问”授权框。 |

脚本会验证 MenuBarAgent 二进制的 UUID，并在写入前比对每个补丁位置的原始字节。如有不符，脚本将不作任何修改直接退出。

## 使用方法

```bash
python3 enable_dynamic_island.py
```

脚本首先检查 SIP 的调试限制是否已解除。如未解除，会说明操作步骤后退出。否则将依次询问：

1. 启用还是禁用灵动岛。
2. 启用时：是否重启 Siri AI，让 Siri AI 和聚焦搜索（Spotlight）上岛。如果 Siri AI 未开启或不可用，选哪个都行。
3. 启用时：灵动岛显示在哪里（仅主显示器、所有显示器，或除主显示器外的所有显示器）。
4. 启用时：是否在每次登录时自动应用。

### 非交互模式

```bash
python3 enable_dynamic_island.py --enable --autostart      # 启用，并在每次登录时自动重新应用
python3 enable_dynamic_island.py --enable --no-autostart   # 仅本次会话启用
python3 enable_dynamic_island.py --disable                 # 禁用并移除登录自启
```

附加选项：

| 选项 | 效果 |
| --- | --- |
| `--layout notch` | 默认。展开态环绕刘海显示。 |
| `--layout below` | 展开态显示在刘海下方，保持系统原始宽度。 |
| `--layout classic` | 仅激活，不调整位置，岛停留在菜单栏下方的默认位置。没有激活的刘海显示器时自动使用此布局。 |
| `--no-watch` | 不启动后台守护进程。 |
| `--restart-siri` | 补丁完成后重启 Siri AI，让 Siri AI 和聚焦搜索上岛（配合 `--autostart` 时登录时也会执行）。Siri AI 未开启或不可用时无影响。 |
| `--no-restart-siri` | 不动 Siri AI。两个选项都不传时，沿用登录项当前的设置。 |
| `--displays main` / `all` / `secondary` | 灵动岛仅在主显示器（默认）、所有活跃显示器，或除主显示器外的显示器上显示。在 build 26C5035r（beta 1）上，系统横幅仍只出现在其首选显示器上。不传时沿用登录项当前的设置。 |

### 禁用

```bash
python3 enable_dynamic_island.py --disable
```

停止后台守护、移除登录自启、恢复客户端进程宽度、删除两个偏好设置并重启 MenuBarAgent。

## 已知限制

- 默认只在主显示器上显示灵动岛（副屏和原生系统一样，把实时活动放在菜单栏），见 `--displays`。多显示器模式只在随航 iPad 作副屏时测试过，尚未在带刘海的 MacBook 或三块以上显示器上测试。在内置刘海屏上贴合刘海；外接屏或随航 iPad 当主显示器时，岛在那块屏上用 `classic` 位置。切换主显示器即时生效。内置屏改分辨率、开合盖由后台守护自动重新打补丁（`--no-watch` 时不处理）。在其他屏上，展开面板仍保持为刘海加宽后的宽度。
- WidgetKit 渲染的第三方 Live Activity 保持其应用自身设置的宽度。
- 低电量横幅的加宽效果未经屏幕实际验证。

## 免责声明

本项目在内存中修改运行中的系统进程，并需要解除 SIP 的调试限制，这会降低系统安全性。本项目按"现状"提供，不附带任何保证，使用风险由用户自行承担。

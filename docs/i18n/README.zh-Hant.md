> [!WARNING]
> **需要解除系統完整性保護（SIP）的除錯限制。** 執行 `csrutil enable --without debug` 即可，SIP 的其餘保護可以保持開啟。
> 本專案透過記憶體修補程式修改執行中的系統程序。詳見[系統需求](#系統需求)。

<p align="center">
  <a href="../../README.md">English</a> · <a href="README.zh-Hans.md">简体中文</a> · <b>繁體中文</b> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a>
</p>

<p align="center">
  <a href="https://x.com/jazminli57"><img src="https://img.shields.io/badge/Follow-%40jazminli57-000000?logo=x&logoColor=white" alt="Follow @jazminli57 on X"></a>
</p>

---

# DynamicEnabler

在帶有瀏海的 MacBook 上啟用 macOS 27 中隱藏的動態島，並將其與實體瀏海整合。

<p align="center">
  <a href="#運作原理">運作原理</a> ·
  <a href="#系統需求">系統需求</a> ·
  <a href="#使用方法">使用方法</a> ·
  <a href="#已知限制">已知限制</a> ·
  <a href="#免責聲明">免責聲明</a>
</p>

---

macOS 27.3 及更高版本的 MenuBarAgent 內建了動態島實作，但僅在 Apple 內部版本中啟用。本指令碼透過記憶體修補程式啟用該功能，並調整其幾何參數以適配實體瀏海：

- 精簡態與最小化狀態的膠囊分佈於瀏海兩側。
- 展開態從螢幕頂部開始，環繞瀏海顯示。
- 系統 Live Activity（計時器、正在播放、AirDrop、螢幕錄製、iPhone 鏡像活動）和電池橫幅的展開面板會加寬，以防內容被瀏海遮擋。

## 運作原理

- **啟用。** MenuBarAgent 透過 `IsAppleInternalBuild()` 檢查來保護 `EmulateCenterHUD` 和 `ShowSoftwareNotch` 兩個偏好設定。指令碼設定這兩個偏好，並將對應的檢查指令替換為 `nop`。
- **瀏海幾何。** 系統原生動態島以選單列下方一塊 59 × 34 pt 的虛擬區域為基準繪製。指令碼將傳回該矩形的函式重新導向至程式碼洞，讓其在執行時從 `NSScreen` 讀取真實的瀏海矩形。
- **面板寬度。** 每個展開面板的寬度固定在各自的用戶端程序中。指令碼鉤住這些程序中的 SwiftUI frame 初始化函式，將展開範圍內的寬度替換為適配瀏海的值。對於寬度特殊的用戶端（AirDrop、ControlCenter 中的電池橫幅），使用專用修補程式處理。
- **背景守護程式。** 計時器等 Live Activity 用戶端按需啟動。守護任務 `local.dynamic-island.watcher` 監控 MenuBarAgent 日誌，在新用戶端接入時自動打修補程式，並在 MenuBarAgent 退出時一同退出。日誌：`~/Library/Logs/dynamic_island_watcher.log`。
- **登入自動啟動。** 指令碼可將自身複製到 `~/Library/Application Support/DynamicEnabler/`，並安裝 LaunchAgent `local.dynamic-island.autostart`，在每次登入時自動重新套用修補程式。日誌：`~/Library/Logs/dynamic_island_autostart.log`。

所有修補程式均在記憶體中進行，不修改系統卷宗上的任何檔案。除非啟用了登入自動啟動，重新啟動 MenuBarAgent 或 Mac 後修補程式即失效。

## 系統需求

| 需求 | 說明 |
| --- | --- |
| macOS | 27.3 beta，已在 build 26C5035r（beta 1）和 26C5047e（beta 2）上測試；不支援 27.2 及更早版本。修補位置按程式碼特徵在本機二進位檔中尋找，後續版本可能無需更新即可使用；任何特徵不是恰好命中一次時，指令碼會停止且不做任何更動。 |
| 硬體 | 帶有瀏海的 MacBook；內建顯示器必須處於啟用狀態。 |
| SIP | 必須解除系統完整性保護（SIP）的除錯限制：在復原模式中執行 `csrutil enable --without debug`（建議，SIP 其餘保護保持開啟）。完全關閉 SIP（`csrutil disable`）也可以。 |
| 工具 | 最新的 Xcode beta，並用 `sudo xcode-select -s /Applications/Xcode-beta.app/Contents/Developer` 切換過去。與 macOS 27 對應的命令列工具也可以；舊版工具（某些系統上 `xcode-select --install` 安裝的就是舊版）在 lldb 附加程序時會當機。 |
| 除錯權限 | 需要免密碼除錯：執行 `sudo DevToolsSecurity -enable`，並且目前使用者在 `_developer` 群組中。否則 lldb 會一直等待「開發者工具存取」授權視窗。 |

指令碼會驗證 MenuBarAgent 二進位檔的 UUID，並在寫入前比對每個修補程式位置的原始位元組。如有不符，指令碼將不作任何修改直接退出。

## 使用方法

```bash
python3 enable_dynamic_island.py
```

指令碼首先檢查 SIP 的除錯限制是否已解除。如未解除，會說明操作步驟後退出。否則將依次詢問：

1. 啟用還是停用動態島。
2. 啟用時：是否重新啟動 Siri AI，讓 Siri AI 和 Spotlight 上島。如果 Siri AI 未開啟或無法使用，選哪個都可以。
3. 啟用時：是否在每次登入時自動套用。

### 非互動模式

```bash
python3 enable_dynamic_island.py --enable --autostart      # 啟用，並在每次登入時自動重新套用
python3 enable_dynamic_island.py --enable --no-autostart   # 僅本次階段作業啟用
python3 enable_dynamic_island.py --disable                 # 停用並移除登入自動啟動
```

附加選項：

| 選項 | 效果 |
| --- | --- |
| `--layout notch` | 預設。展開態環繞瀏海顯示。 |
| `--layout below` | 展開態顯示在瀏海下方，保持系統原始寬度。 |
| `--layout classic` | 僅啟用，不調整位置，島停留在選單列下方的預設位置。 |
| `--no-watch` | 不啟動背景守護程式。 |
| `--restart-siri` | 補丁完成後重新啟動 Siri AI，讓 Siri AI 和 Spotlight 上島（搭配 `--autostart` 時登入時也會執行）。Siri AI 未開啟或無法使用時沒有影響。 |
| `--no-restart-siri` | 不動 Siri AI。兩個選項都不傳時，沿用登入項目目前的設定。 |

### 停用

```bash
python3 enable_dynamic_island.py --disable
```

停止背景守護程式、移除登入自動啟動、還原用戶端程序寬度、刪除兩個偏好設定並重新啟動 MenuBarAgent。

## 已知限制

- 僅在帶有瀏海的 MacBook 上測試；外接顯示器的幾何計算基於內建顯示器。
- WidgetKit 轉譯的第三方 Live Activity 保持其應用程式自身設定的寬度。
- 低電量橫幅的加寬效果未經螢幕實際驗證。

## 免責聲明

本專案在記憶體中修改執行中的系統程序，並需要解除 SIP 的除錯限制，這會降低系統安全性。本專案按「現狀」提供，不附帶任何保證，使用風險由使用者自行承擔。

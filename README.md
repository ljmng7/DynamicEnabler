> [!WARNING]
> **System Integrity Protection's (SIP) debugging restriction must be lifted.** `csrutil enable --without debug` is enough; the rest of SIP can stay on.
> This project patches running system processes in memory. See [Requirements](#requirements) for details.

<p align="center">
  <b>English</b> · <a href="docs/i18n/README.zh-Hans.md">简体中文</a> · <a href="docs/i18n/README.zh-Hant.md">繁體中文</a> · <a href="docs/i18n/README.ja.md">日本語</a> · <a href="docs/i18n/README.ko.md">한국어</a> · <a href="docs/i18n/README.fr.md">Français</a> · <a href="docs/i18n/README.de.md">Deutsch</a> · <a href="docs/i18n/README.es.md">Español</a>
</p>

<p align="center">
  <a href="https://x.com/jazminli57"><img src="https://img.shields.io/badge/Follow-%40jazminli57-000000?logo=x&logoColor=white" alt="Follow @jazminli57 on X"></a>
</p>

---

# DynamicEnabler

Enables the hidden Dynamic Island of macOS 27 on MacBook models with a display notch, integrating it with the physical notch.

<p align="center">
  <a href="#how-it-works">How It Works</a> ·
  <a href="#requirements">Requirements</a> ·
  <a href="#usage">Usage</a> ·
  <a href="#known-limitations">Known Limitations</a> ·
  <a href="#disclaimer">Disclaimer</a>
</p>

---

macOS 27 (27.3 and later) ships a Dynamic Island implementation inside MenuBarAgent that is inactive on non-Apple-internal builds. This script activates it by patching the running MenuBarAgent process in memory, then adjusts its geometry to fit the physical notch:

- Compact and Minimal presentations sit on either side of the notch.
- Expanded presentations start at the top of the screen and wrap around the notch.
- Expanded panels of system Live Activities (timers, Now Playing, AirDrop, screen recording, iPhone-mirrored activities) and battery banners are widened so their content is not hidden behind the notch.

## How It Works

- **Activation.** MenuBarAgent reads two preferences, `EmulateCenterHUD` and `ShowSoftwareNotch`, each guarded by an `IsAppleInternalBuild()` check. The script sets both preferences and replaces the two guards with `nop` instructions.
- **Notch geometry.** The stock island is drawn around a virtual 59 × 34 pt sensor area below the menu bar. The functions that return this rectangle are redirected to small code caves that read the real notch rectangle from `NSScreen` at runtime.
- **Panel widths.** Each expanded panel's width is fixed in the client process that renders it. The script hooks SwiftUI frame initializers in those processes and replaces widths in the expanded range. Clients with unusual sizing (AirDrop, battery banners in ControlCenter) get dedicated patches.
- **Background helper.** Live Activity clients such as the timer process start on demand. A helper job (`local.dynamic-island.watcher`) watches the MenuBarAgent log and patches new clients as they appear. It exits when MenuBarAgent exits. Log: `~/Library/Logs/dynamic_island_watcher.log`.
- **Login activation.** The script can copy itself to `~/Library/Application Support/DynamicEnabler/` and install a LaunchAgent (`local.dynamic-island.autostart`) that reapplies the patches at every login. Log: `~/Library/Logs/dynamic_island_autostart.log`.

All patches are in memory only. Nothing on the system volume is modified. Restarting MenuBarAgent or the Mac removes the patches unless login activation is enabled.

## Requirements

| Requirement | Details |
| --- | --- |
| macOS | 27.3 beta (build 26C5035r) only; other versions, including 27.2, are not supported. Patch addresses are verified per build; the script stops if the binary does not match. |
| Hardware | MacBook with a notch; the built-in display must be active. |
| SIP | System Integrity Protection's debugging restriction must be lifted: in Recovery, run `csrutil enable --without debug` (recommended; the rest of SIP stays on). Fully disabled SIP (`csrutil disable`) also works. |
| Tools | The latest Xcode beta, selected with `sudo xcode-select -s /Applications/Xcode-beta.app/Contents/Developer`. Command Line Tools that match macOS 27 also work; older tools (including those from `xcode-select --install` on some systems) crash when lldb attaches. |
| Debugging | Password-free debugging: run `sudo DevToolsSecurity -enable`, and your user must be in the `_developer` group. Otherwise lldb waits for the "Developer Tools Access" dialog. |

The script verifies the MenuBarAgent binary UUID and compares every patch site against its expected original bytes before writing. If anything differs, it exits without making changes.

## Usage

```bash
python3 enable_dynamic_island.py
```

The script first checks whether SIP's debugging restriction is lifted. If not, it explains how to do so and exits. Otherwise it asks:

1. Enable or disable the Dynamic Island.
2. If enabling: whether to restart Siri AI so that Siri AI and Spotlight appear in the island. If Siri AI is off or not available on your Mac, either choice is fine.
3. If enabling: whether to apply the Dynamic Island automatically at every login.

### Non-interactive mode

```bash
python3 enable_dynamic_island.py --enable --autostart      # enable and re-apply at every login
python3 enable_dynamic_island.py --enable --no-autostart   # enable for this session only
python3 enable_dynamic_island.py --disable                 # disable and remove login activation
```

Additional options:

| Option | Effect |
| --- | --- |
| `--layout notch` | Default. Expanded presentations wrap around the notch. |
| `--layout below` | Expanded presentations appear below the notch at stock widths. |
| `--layout classic` | Activation only; island stays at the stock position below the menu bar. |
| `--no-watch` | Do not start the background helper. |
| `--restart-siri` | Restart Siri AI after patching so that Siri AI and Spotlight appear in the island (also at login with `--autostart`). No effect if Siri AI is off or not available. |
| `--no-restart-siri` | Leave Siri AI as it is. Without either option, the login item's current setting is kept. |

### Disabling

```bash
python3 enable_dynamic_island.py --disable
```

Stops the background helper, removes login activation, restores client process widths, deletes the two preferences, and restarts MenuBarAgent.

## Known Limitations

- Only tested on MacBook with a notch; external display geometry is based on the built-in display only.
- Third-party Live Activities rendered by WidgetKit keep the widths chosen by their apps.
- The low-battery banner width has not been verified on-screen.

## Disclaimer

This project modifies running system processes in memory and requires SIP's debugging restriction to be lifted, which reduces system security. It is provided as-is, without warranty. Use at your own risk.

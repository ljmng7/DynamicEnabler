> [!WARNING]
> **시스템 무결성 보호(SIP)의 디버깅 제한을 해제해야 합니다.** `csrutil enable --without debug`만으로 충분하며, 나머지 SIP 보호는 유지할 수 있습니다.
> 이 프로젝트는 실행 중인 시스템 프로세스를 메모리에서 패치합니다. 자세한 내용은 [시스템 요구사항](#시스템-요구사항)을 참조하세요.

<p align="center">
  <a href="../../README.md">English</a> · <a href="README.zh-Hans.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <b>한국어</b> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a>
</p>

<p align="center">
  <a href="https://x.com/jazminli57"><img src="https://img.shields.io/badge/Follow-%40jazminli57-000000?logo=x&logoColor=white" alt="Follow @jazminli57 on X"></a>
</p>

---

# DynamicEnabler

노치가 있는 MacBook에서 macOS 27의 숨겨진 Dynamic Island를 활성화하고 물리적 노치와 통합합니다.

<p align="center">
  <a href="#작동-원리">작동 원리</a> ·
  <a href="#시스템-요구사항">시스템 요구사항</a> ·
  <a href="#사용-방법">사용 방법</a> ·
  <a href="#알려진-제한사항">알려진 제한사항</a> ·
  <a href="#면책-조항">면책 조항</a>
</p>

---

macOS 27(27.3 이상)의 MenuBarAgent에는 Dynamic Island 구현이 포함되어 있지만 Apple 내부 빌드에서만 활성화됩니다. 이 스크립트는 메모리 패치를 통해 이 기능을 활성화하고 물리적 노치에 맞게 배치를 조정합니다:

- 컴팩트 및 최소 표시는 노치 양쪽에 배치됩니다.
- 확장 표시는 화면 상단에서 시작하여 노치를 감싸도록 표시됩니다.
- 시스템 Live Activity(타이머, 재생 중, AirDrop, 화면 녹화, iPhone 미러링 활동) 및 배터리 배너의 확장 패널은 콘텐츠가 노치에 가려지지 않도록 너비가 넓어집니다.

## 작동 원리

- **활성화.** MenuBarAgent는 `IsAppleInternalBuild()` 검사로 보호되는 `EmulateCenterHUD` 및 `ShowSoftwareNotch` 두 가지 환경설정을 읽습니다. 스크립트는 이러한 환경설정을 설정하고 두 가드 명령을 `nop`로 대체합니다.
- **노치 배치.** 기본 Dynamic Island는 메뉴 막대 아래의 가상 59 × 34 pt 센서 영역을 기준으로 그려집니다. 스크립트는 이 사각형을 반환하는 함수를 작은 코드 영역으로 리디렉션하여 런타임에 `NSScreen`에서 실제 노치 사각형을 읽도록 합니다.
- **패널 너비.** 각 확장 패널의 너비는 이를 렌더링하는 클라이언트 프로세스 내에서 고정됩니다. 스크립트는 이러한 프로세스 내의 SwiftUI 프레임 초기화 함수를 후킹하고 확장 범위의 너비를 대체합니다. 특수한 크기 설정을 가진 클라이언트(AirDrop, ControlCenter의 배터리 배너)에는 전용 패치가 적용됩니다.
- **백그라운드 헬퍼.** 타이머 프로세스와 같은 Live Activity 클라이언트는 온디맨드로 시작됩니다. 헬퍼 작업(`local.dynamic-island.watcher`)이 MenuBarAgent 로그를 모니터링하고 새 클라이언트가 나타나면 패치를 적용합니다. MenuBarAgent가 종료되면 함께 종료됩니다. 로그: `~/Library/Logs/dynamic_island_watcher.log`.
- **로그인 시 활성화.** 스크립트는 자신을 `~/Library/Application Support/DynamicEnabler/`에 복사하고 LaunchAgent(`local.dynamic-island.autostart`)를 설치하여 로그인할 때마다 패치를 다시 적용할 수 있습니다. 로그: `~/Library/Logs/dynamic_island_autostart.log`.

모든 패치는 메모리에서만 수행됩니다. 시스템 볼륨의 파일은 수정되지 않습니다. 로그인 시 활성화가 활성화되어 있지 않으면 MenuBarAgent 또는 Mac을 재시작하면 패치가 제거됩니다.

## 시스템 요구사항

| 요구사항 | 세부사항 |
| --- | --- |
| macOS | 27.3 beta(빌드 26C5035r)만 지원하며, 27.2 등 다른 버전은 지원하지 않습니다. 패치 주소는 빌드별로 검증되며 바이너리가 일치하지 않으면 스크립트가 중지됩니다. |
| 하드웨어 | 노치가 있는 MacBook. 내장 디스플레이가 활성화되어 있어야 합니다. |
| SIP | 시스템 무결성 보호의 디버깅 제한을 해제해야 합니다. 복구 모드에서 `csrutil enable --without debug`를 실행하세요(권장, 나머지 SIP 보호는 유지됨). SIP를 완전히 비활성화(`csrutil disable`)해도 됩니다. |
| 도구 | 최신 Xcode beta를 `sudo xcode-select -s /Applications/Xcode-beta.app/Contents/Developer`로 선택하세요. macOS 27용 명령줄 도구도 사용할 수 있습니다. 오래된 도구(일부 환경에서는 `xcode-select --install`로 설치되는 버전)는 lldb 연결 시 충돌합니다. |
| 디버깅 권한 | 암호 없는 디버깅: `sudo DevToolsSecurity -enable`를 실행하고, 사용자가 `_developer` 그룹에 속해 있어야 합니다. 그렇지 않으면 lldb가 "Developer Tools Access" 대화 상자를 기다립니다. |

스크립트는 MenuBarAgent 바이너리의 UUID를 검증하고 쓰기 전에 각 패치 사이트를 예상 원본 바이트와 비교합니다. 차이가 있으면 변경하지 않고 종료합니다.

## 사용 방법

```bash
python3 enable_dynamic_island.py
```

스크립트는 먼저 SIP의 디버깅 제한이 해제되어 있는지 확인합니다. 해제되어 있지 않으면 해제 방법을 설명하고 종료합니다. 그렇지 않으면 다음을 묻습니다:

1. Dynamic Island를 활성화할지 비활성화할지.
2. 활성화하는 경우: Siri AI를 재시작하여 Siri AI와 Spotlight를 아일랜드에 표시할지 여부. Siri AI가 꺼져 있거나 사용할 수 없으면 어느 쪽을 선택해도 됩니다.
3. 활성화하는 경우: 로그인할 때마다 자동으로 적용할지 여부.

### 비대화형 모드

```bash
python3 enable_dynamic_island.py --enable --autostart      # 활성화하고 로그인할 때마다 다시 적용
python3 enable_dynamic_island.py --enable --no-autostart   # 이 세션에만 활성화
python3 enable_dynamic_island.py --disable                 # 비활성화하고 로그인 시 활성화 제거
```

추가 옵션:

| 옵션 | 효과 |
| --- | --- |
| `--layout notch` | 기본값. 확장 표시가 노치를 감쌉니다. |
| `--layout below` | 확장 표시가 노치 아래에 기본 너비로 표시됩니다. |
| `--layout classic` | 활성화만. 아일랜드가 메뉴 막대 아래의 기본 위치에 유지됩니다. |
| `--no-watch` | 백그라운드 헬퍼를 시작하지 않습니다. |
| `--restart-siri` | 패치 후 Siri AI를 재시작하여 Siri AI와 Spotlight를 아일랜드에 표시합니다(`--autostart`와 함께 쓰면 로그인 시에도 실행). Siri AI가 꺼져 있거나 사용할 수 없으면 영향이 없습니다. |
| `--no-restart-siri` | Siri AI를 그대로 둡니다. 둘 다 지정하지 않으면 로그인 항목의 현재 설정을 유지합니다. |

### 비활성화

```bash
python3 enable_dynamic_island.py --disable
```

백그라운드 헬퍼를 중지하고 로그인 시 활성화를 제거하며 클라이언트 프로세스 너비를 복원하고 두 환경설정을 삭제한 후 MenuBarAgent를 재시작합니다.

## 알려진 제한사항

- 노치가 있는 MacBook에서만 테스트되었습니다. 외부 디스플레이 배치 계산은 내장 디스플레이를 기반으로 합니다.
- WidgetKit으로 렌더링된 타사 Live Activity는 앱이 선택한 너비를 유지합니다.
- 저배터리 배너 너비는 화면에서 실제로 확인되지 않았습니다.

## 면책 조항

이 프로젝트는 실행 중인 시스템 프로세스를 메모리에서 수정하며 SIP의 디버깅 제한을 해제해야 하므로 시스템 보안이 저하됩니다. 이 프로젝트는 "있는 그대로" 제공되며 보증이 없습니다. 사용은 본인의 책임입니다.

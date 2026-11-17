> [!WARNING]
> **システム整合性保護（SIP）のデバッグ制限を解除する必要があります。** `csrutil enable --without debug` だけで十分で、SIP のその他の保護は有効のままにできます。
> このプロジェクトは実行中のシステムプロセスをメモリ内でパッチします。詳細は[システム要件](#システム要件)を参照してください。

<p align="center">
  <a href="../../README.md">English</a> · <a href="README.zh-Hans.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <b>日本語</b> · <a href="README.ko.md">한국어</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a>
</p>

<p align="center">
  <a href="https://x.com/jazminli57"><img src="https://img.shields.io/badge/Follow-%40jazminli57-000000?logo=x&logoColor=white" alt="Follow @jazminli57 on X"></a>
</p>

---

# DynamicEnabler

ノッチ付き MacBook で macOS 27 の隠された Dynamic Island を有効にし、物理的なノッチと統合します。

<p align="center">
  <a href="#動作原理">動作原理</a> ·
  <a href="#システム要件">システム要件</a> ·
  <a href="#使用方法">使用方法</a> ·
  <a href="#既知の制限">既知の制限</a> ·
  <a href="#免責事項">免責事項</a>
</p>

---

macOS 27（27.3 以降）の MenuBarAgent には Dynamic Island の実装が含まれていますが、Apple 内部ビルドでのみ有効になっています。このスクリプトはメモリ内パッチによってこの機能を有効化し、物理的なノッチに合わせて配置を調整します：

- コンパクト表示と最小表示はノッチの両側に配置されます。
- 展開表示は画面上部から始まり、ノッチを回り込むように表示されます。
- システムの Live Activity（タイマー、再生中、AirDrop、画面収録、iPhone ミラーリングアクティビティ）とバッテリーバナーの展開パネルは、コンテンツがノッチに隠れないように幅が広げられます。

## 動作原理

- **有効化。** MenuBarAgent は `IsAppleInternalBuild()` チェックで保護された `EmulateCenterHUD` と `ShowSoftwareNotch` の 2 つの環境設定を読み取ります。スクリプトはこれらの環境設定を設定し、2 つのガード命令を `nop` に置き換えます。
- **ノッチの配置。** 標準の Dynamic Island は、メニューバーの下にある仮想的な 59 × 34 pt のセンサー領域を基準に描画されます。スクリプトはこの矩形を返す関数を小さなコード領域にリダイレクトし、実行時に `NSScreen` から実際のノッチ矩形を読み取るようにします。
- **パネル幅。** 各展開パネルの幅は、それをレンダリングするクライアントプロセス内で固定されています。スクリプトはこれらのプロセス内の SwiftUI フレーム初期化関数をフックし、展開範囲の幅を置き換えます。特殊なサイズ設定を持つクライアント（AirDrop、ControlCenter のバッテリーバナー）には専用パッチが適用されます。
- **バックグラウンドヘルパー。** タイマープロセスなどの Live Activity クライアントはオンデマンドで起動します。ヘルパージョブ（`local.dynamic-island.watcher`）が MenuBarAgent ログを監視し、新しいクライアントが現れるとパッチを適用します。MenuBarAgent の終了時に一緒に終了します。ログ：`~/Library/Logs/dynamic_island_watcher.log`。 内蔵ディスプレイも監視し、解像度が変わったり蓋を開閉したりすると、新しいノッチサイズで全パッチを再適用します（ノッチが見えない場合は `classic`）。MenuBarAgent が一度再起動し、約 20 秒でアイランドが戻ります。
- **ログイン時の有効化。** スクリプトは自身を `~/Library/Application Support/DynamicEnabler/` にコピーし、LaunchAgent（`local.dynamic-island.autostart`）をインストールして、ログインごとにパッチを再適用できます。ログ：`~/Library/Logs/dynamic_island_autostart.log`。

すべてのパッチはメモリ内でのみ行われます。システムボリューム上のファイルは変更されません。ログイン時の有効化が有効になっていない限り、MenuBarAgent または Mac を再起動するとパッチは削除されます。

## システム要件

| 要件 | 詳細 |
| --- | --- |
| macOS | 27.3 beta。ビルド 26C5035r（beta 1）と 26C5047e（beta 2）でテスト済み。27.2 以前には対応していません。パッチ箇所はインストール済みバイナリ内のコードシグネチャで特定するため、以降のビルドでも更新なしで動作する可能性があります。シグネチャがちょうど 1 回一致しない場合、スクリプトは何も変更せずに停止します。 |
| ハードウェア | すべての Mac。ノッチ付きディスプレイが有効（ノッチ付き MacBook で蓋が開いている）ならアイランドをノッチに合わせます。ない場合（ノッチなしの Mac、蓋を閉じている、外部ディスプレイのみ）は自動的に `--layout classic` に切り替えます。ノッチなし環境は未テストです。 |
| SIP | システム整合性保護のデバッグ制限を解除する必要があります。復旧モードで `csrutil enable --without debug` を実行してください（推奨。SIP のその他の保護は有効のままです）。SIP を完全に無効にしても（`csrutil disable`）動作します。 |
| ツール | 最新の Xcode beta を `sudo xcode-select -s /Applications/Xcode-beta.app/Contents/Developer` で選択してください。macOS 27 対応のコマンドラインツールでも動作します。古いツール（環境によっては `xcode-select --install` で入るもの）では lldb のアタッチ時にクラッシュします。 |
| デバッグ権限 | パスワードなしのデバッグ：`sudo DevToolsSecurity -enable` を実行し、ユーザーが `_developer` グループに属している必要があります。そうでないと lldb は「Developer Tools Access」ダイアログを待ち続けます。 |

スクリプトは MenuBarAgent バイナリの UUID を検証し、書き込み前に各パッチサイトを期待される元のバイトと比較します。何か異なる場合は、変更を加えずに終了します。

## 使用方法

```bash
python3 enable_dynamic_island.py
```

スクリプトはまず SIP のデバッグ制限が解除されているかどうかを確認します。解除されていない場合は、解除方法を説明して終了します。それ以外の場合は次の質問をします：

1. Dynamic Island を有効にするか無効にするか。
2. 有効にする場合：Siri AI を再起動して、Siri AI と Spotlight を島に表示するかどうか。Siri AI がオフまたは利用できない場合は、どちらを選んでも構いません。
3. 有効にする場合：ログインごとに自動的に適用するかどうか。

### 非対話モード

```bash
python3 enable_dynamic_island.py --enable --autostart      # 有効化し、ログインごとに再適用
python3 enable_dynamic_island.py --enable --no-autostart   # このセッションのみ有効化
python3 enable_dynamic_island.py --disable                 # 無効化し、ログイン時の有効化を削除
```

追加オプション：

| オプション | 効果 |
| --- | --- |
| `--layout notch` | デフォルト。展開表示がノッチを回り込みます。 |
| `--layout below` | 展開表示がノッチの下に標準幅で表示されます。 |
| `--layout classic` | 有効化のみ。アイランドはメニューバー下の標準位置に留まります。 ノッチ付きディスプレイが有効でないときは自動的にこれが使われます。 |
| `--no-watch` | バックグラウンドヘルパーを起動しません。 |
| `--restart-siri` | パッチ適用後に Siri AI を再起動し、Siri AI と Spotlight を島に表示します（`--autostart` と併用するとログイン時にも実行）。Siri AI がオフまたは利用できない場合は影響しません。 |
| `--no-restart-siri` | Siri AI をそのままにします。どちらも指定しない場合は、ログイン項目の現在の設定を引き継ぎます。 |

### 無効化

```bash
python3 enable_dynamic_island.py --disable
```

バックグラウンドヘルパーを停止し、ログイン時の有効化を削除し、クライアントプロセスの幅を復元し、2 つの環境設定を削除して MenuBarAgent を再起動します。

## 既知の制限

- アイランドはメインディスプレイにのみ表示されます（サブディスプレイは標準どおりメニューバーにライブアクティビティを表示）。内蔵のノッチ付きディスプレイではノッチに合わせ、他のディスプレイ（外部、Sidecar の iPad）がメインのときはそこで `classic` の位置になります。メインディスプレイの切り替えは即座に反映されます。内蔵ディスプレイの解像度変更や蓋の開閉はバックグラウンドヘルパーがパッチを再適用して対応します（`--no-watch` では無効）。展開表示の幅は他のディスプレイでもノッチ用に広げたままです。
- WidgetKit でレンダリングされたサードパーティの Live Activity は、アプリが選択した幅を保持します。
- 低バッテリーバナーの幅は画面上で実際に検証されていません。

## 免責事項

このプロジェクトは実行中のシステムプロセスをメモリ内で変更し、SIP のデバッグ制限を解除する必要があるため、システムのセキュリティが低下します。このプロジェクトは「現状のまま」提供され、保証はありません。使用はご自身の責任で行ってください。

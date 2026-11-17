> [!WARNING]
> **Die Debugging-Einschränkung des Systemintegritätsschutzes (SIP) muss aufgehoben werden.** `csrutil enable --without debug` genügt; der übrige SIP kann aktiv bleiben.
> Dieses Projekt patcht laufende Systemprozesse im Speicher. Siehe [Anforderungen](#anforderungen) für Details.

<p align="center">
  <a href="../../README.md">English</a> · <a href="README.zh-Hans.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.fr.md">Français</a> · <b>Deutsch</b> · <a href="README.es.md">Español</a>
</p>

<p align="center">
  <a href="https://x.com/jazminli57"><img src="https://img.shields.io/badge/Follow-%40jazminli57-000000?logo=x&logoColor=white" alt="Follow @jazminli57 on X"></a>
</p>

---

# DynamicEnabler

Aktiviert die versteckte Dynamic Island von macOS 27 auf MacBook-Modellen mit Display-Notch und integriert sie mit der physischen Notch.

<p align="center">
  <a href="#funktionsweise">Funktionsweise</a> ·
  <a href="#anforderungen">Anforderungen</a> ·
  <a href="#verwendung">Verwendung</a> ·
  <a href="#bekannte-einschränkungen">Bekannte Einschränkungen</a> ·
  <a href="#haftungsausschluss">Haftungsausschluss</a>
</p>

---

macOS 27 (27.3 und höher) enthält eine Dynamic Island-Implementierung in MenuBarAgent, die auf Nicht-Apple-internen Builds inaktiv ist. Dieses Skript aktiviert sie durch Patchen des laufenden MenuBarAgent-Prozesses im Speicher und passt dann ihre Geometrie an die physische Notch an:

- Kompakte und minimale Darstellungen sitzen auf beiden Seiten der Notch.
- Erweiterte Darstellungen beginnen am oberen Bildschirmrand und umschließen die Notch.
- Erweiterte Panels von System-Live-Activities (Timer, Aktuell läuft, AirDrop, Bildschirmaufnahme, iPhone-gespiegelte Aktivitäten) und Batteriebanner werden verbreitert, damit ihr Inhalt nicht hinter der Notch verborgen wird.

## Funktionsweise

- **Aktivierung.** MenuBarAgent liest zwei Einstellungen, `EmulateCenterHUD` und `ShowSoftwareNotch`, die jeweils durch eine `IsAppleInternalBuild()`-Prüfung geschützt sind. Das Skript setzt beide Einstellungen und ersetzt die beiden Schutzanweisungen durch `nop`-Befehle.
- **Notch-Geometrie.** Die Standard-Dynamic Island wird um einen virtuellen 59 × 34 pt Sensorbereich unterhalb der Menüleiste gezeichnet. Die Funktionen, die dieses Rechteck zurückgeben, werden zu kleinen Code-Höhlen umgeleitet, die das echte Notch-Rechteck zur Laufzeit aus `NSScreen` auslesen.
- **Panel-Breiten.** Die Breite jedes erweiterten Panels ist im Client-Prozess, der es rendert, fest vorgegeben. Das Skript fängt SwiftUI-Frame-Initialisierer in diesen Prozessen ab und ersetzt Breiten im erweiterten Bereich. Clients mit ungewöhnlicher Größenanpassung (AirDrop, Batteriebanner in ControlCenter) erhalten dedizierte Patches.
- **Hintergrund-Helfer.** Live-Activity-Clients wie der Timer-Prozess starten bei Bedarf. Ein Helfer-Job (`local.dynamic-island.watcher`) überwacht das MenuBarAgent-Protokoll und patcht neue Clients, sobald sie erscheinen. Er wird beendet, wenn MenuBarAgent beendet wird. Protokoll: `~/Library/Logs/dynamic_island_watcher.log`.
- **Anmeldeaktivierung.** Das Skript kann sich selbst nach `~/Library/Application Support/DynamicEnabler/` kopieren und einen LaunchAgent (`local.dynamic-island.autostart`) installieren, der die Patches bei jeder Anmeldung erneut anwendet. Protokoll: `~/Library/Logs/dynamic_island_autostart.log`.

Alle Patches erfolgen nur im Speicher. Auf dem System-Volume wird nichts geändert. Ein Neustart von MenuBarAgent oder des Mac entfernt die Patches, es sei denn, die Anmeldeaktivierung ist aktiviert.

## Anforderungen

| Anforderung | Details |
| --- | --- |
| macOS | 27.3 beta, getestet mit den Builds 26C5035r (Beta 1) und 26C5047e (Beta 2). 27.2 und älter werden nicht unterstützt. Die Patch-Stellen werden anhand von Code-Signaturen in den installierten Binärdateien gesucht, sodass spätere Builds ohne Update funktionieren können; das Skript stoppt ohne Änderungen, wenn eine Signatur nicht genau einmal passt. |
| Hardware | MacBook mit Notch; das integrierte Display muss aktiv sein. |
| SIP | Die Debugging-Einschränkung des Systemintegritätsschutzes muss aufgehoben sein: Im Wiederherstellungsmodus `csrutil enable --without debug` ausführen (empfohlen; der übrige SIP bleibt aktiv). Ein vollständig deaktivierter SIP (`csrutil disable`) funktioniert ebenfalls. |
| Tools | Die neueste Xcode-Beta, ausgewählt mit `sudo xcode-select -s /Applications/Xcode-beta.app/Contents/Developer`. Command Line Tools für macOS 27 funktionieren ebenfalls; ältere Tools (auf manchen Systemen auch die von `xcode-select --install`) stürzen beim Anhängen von lldb ab. |
| Debugging | Debugging ohne Passwort: `sudo DevToolsSecurity -enable` ausführen, und der Benutzer muss in der Gruppe `_developer` sein. Sonst wartet lldb auf den Dialog „Developer Tools Access“. |

Das Skript überprüft die UUID der MenuBarAgent-Binärdatei und vergleicht jede Patch-Stelle vor dem Schreiben mit ihren erwarteten Original-Bytes. Wenn etwas abweicht, wird es ohne Änderungen beendet.

## Verwendung

```bash
python3 enable_dynamic_island.py
```

Das Skript prüft zunächst, ob die Debugging-Einschränkung des SIP aufgehoben ist. Falls nicht, erklärt es, wie man sie aufhebt, und wird beendet. Andernfalls stellt es folgende Fragen:

1. Dynamic Island aktivieren oder deaktivieren.
2. Bei Aktivierung: ob Siri AI neu gestartet werden soll, damit Siri AI und Spotlight in der Insel erscheinen. Ist Siri AI ausgeschaltet oder nicht verfügbar, ist die Wahl egal.
3. Bei Aktivierung: ob es bei jeder Anmeldung automatisch angewendet werden soll.

### Nicht-interaktiver Modus

```bash
python3 enable_dynamic_island.py --enable --autostart      # aktivieren und bei jeder Anmeldung erneut anwenden
python3 enable_dynamic_island.py --enable --no-autostart   # nur für diese Sitzung aktivieren
python3 enable_dynamic_island.py --disable                 # deaktivieren und Anmeldeaktivierung entfernen
```

Zusätzliche Optionen:

| Option | Wirkung |
| --- | --- |
| `--layout notch` | Standard. Erweiterte Darstellungen umschließen die Notch. |
| `--layout below` | Erweiterte Darstellungen erscheinen unterhalb der Notch in Standardbreiten. |
| `--layout classic` | Nur Aktivierung; Insel bleibt an der Standardposition unterhalb der Menüleiste. |
| `--no-watch` | Hintergrund-Helfer nicht starten. |
| `--restart-siri` | Startet Siri AI nach dem Patchen neu, damit Siri AI und Spotlight in der Insel erscheinen (mit `--autostart` auch bei der Anmeldung). Keine Wirkung, wenn Siri AI ausgeschaltet oder nicht verfügbar ist. |
| `--no-restart-siri` | Siri AI unverändert lassen. Ohne eine der beiden Optionen bleibt die aktuelle Einstellung des Anmeldeobjekts erhalten. |

### Deaktivierung

```bash
python3 enable_dynamic_island.py --disable
```

Stoppt den Hintergrund-Helfer, entfernt die Anmeldeaktivierung, stellt Client-Prozessbreiten wieder her, löscht die beiden Einstellungen und startet MenuBarAgent neu.

## Bekannte Einschränkungen

- Nur auf MacBook mit Notch getestet; die Geometrie externer Displays basiert nur auf dem integrierten Display.
- Von WidgetKit gerenderte Drittanbieter-Live-Activities behalten die von ihren Apps gewählten Breiten bei.
- Die Breite des Batterie-Low-Banners wurde nicht auf dem Bildschirm überprüft.

## Haftungsausschluss

Dieses Projekt ändert laufende Systemprozesse im Speicher und erfordert die Aufhebung der Debugging-Einschränkung des SIP, was die Systemsicherheit verringert. Es wird wie besehen ohne Garantie bereitgestellt. Verwendung auf eigene Gefahr.

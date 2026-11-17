> [!WARNING]
> **La restriction de débogage de la protection de l'intégrité du système (SIP) doit être levée.** `csrutil enable --without debug` suffit ; le reste du SIP peut rester actif.
> Ce projet modifie les processus système en cours d'exécution en mémoire. Voir [Exigences](#exigences) pour plus de détails.

<p align="center">
  <a href="../../README.md">English</a> · <a href="README.zh-Hans.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <b>Français</b> · <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a>
</p>

<p align="center">
  <a href="https://x.com/jazminli57"><img src="https://img.shields.io/badge/Follow-%40jazminli57-000000?logo=x&logoColor=white" alt="Follow @jazminli57 on X"></a>
</p>

---

# DynamicEnabler

Active la Dynamic Island cachée de macOS 27 sur les MacBook avec encoche, en l'intégrant à l'encoche physique.

<p align="center">
  <a href="#fonctionnement">Fonctionnement</a> ·
  <a href="#exigences">Exigences</a> ·
  <a href="#utilisation">Utilisation</a> ·
  <a href="#limitations-connues">Limitations connues</a> ·
  <a href="#avertissement">Avertissement</a>
</p>

---

macOS 27 (27.3 et versions ultérieures) inclut une implémentation de la Dynamic Island dans MenuBarAgent qui est inactive sur les builds non-Apple internes. Ce script l'active en patchant le processus MenuBarAgent en cours d'exécution en mémoire, puis ajuste sa géométrie pour s'adapter à l'encoche physique :

- Les présentations compactes et minimales se positionnent de chaque côté de l'encoche.
- Les présentations étendues commencent en haut de l'écran et s'enroulent autour de l'encoche.
- Les panneaux étendus des Live Activities système (minuteurs, Lecture en cours, AirDrop, enregistrement d'écran, activités en miroir d'iPhone) et des bannières de batterie sont élargis pour que leur contenu ne soit pas masqué derrière l'encoche.

## Fonctionnement

- **Activation.** MenuBarAgent lit deux préférences, `EmulateCenterHUD` et `ShowSoftwareNotch`, chacune protégée par une vérification `IsAppleInternalBuild()`. Le script définit ces deux préférences et remplace les deux gardes par des instructions `nop`.
- **Géométrie de l'encoche.** La Dynamic Island standard est dessinée autour d'une zone de capteur virtuelle de 59 × 34 pt sous la barre de menus. Les fonctions qui retournent ce rectangle sont redirigées vers de petites cavités de code qui lisent le véritable rectangle de l'encoche depuis `NSScreen` au moment de l'exécution.
- **Largeurs des panneaux.** La largeur de chaque panneau étendu est fixe dans le processus client qui le rend. Le script intercepte les initialiseurs de frame SwiftUI dans ces processus et remplace les largeurs dans la plage étendue. Les clients avec un dimensionnement inhabituel (AirDrop, bannières de batterie dans ControlCenter) reçoivent des correctifs dédiés.
- **Assistant en arrière-plan.** Les clients Live Activity comme le processus de minuteur démarrent à la demande. Une tâche d'assistance (`local.dynamic-island.watcher`) surveille le journal de MenuBarAgent et applique des correctifs aux nouveaux clients dès leur apparition. Elle se termine lorsque MenuBarAgent se termine. Journal : `~/Library/Logs/dynamic_island_watcher.log`.
- **Activation à la connexion.** Le script peut se copier dans `~/Library/Application Support/DynamicEnabler/` et installer un LaunchAgent (`local.dynamic-island.autostart`) qui réapplique les correctifs à chaque connexion. Journal : `~/Library/Logs/dynamic_island_autostart.log`.

Tous les correctifs sont en mémoire uniquement. Rien sur le volume système n'est modifié. Le redémarrage de MenuBarAgent ou du Mac supprime les correctifs sauf si l'activation à la connexion est activée.

## Exigences

| Exigence | Détails |
| --- | --- |
| macOS | 27.3 beta, testé sur les builds 26C5035r (bêta 1) et 26C5047e (bêta 2). 27.2 et les versions antérieures ne sont pas prises en charge. Les points de correctif sont localisés par signature de code dans les binaires installés, de sorte que des builds ultérieurs peuvent fonctionner sans mise à jour ; le script s'arrête sans rien modifier si une signature ne correspond pas exactement une fois. |
| Matériel | MacBook avec encoche ; l'écran intégré doit être actif. |
| SIP | La restriction de débogage de la protection de l'intégrité du système doit être levée : en mode de récupération, exécutez `csrutil enable --without debug` (recommandé ; le reste du SIP reste actif). Un SIP entièrement désactivé (`csrutil disable`) fonctionne aussi. |
| Outils | La dernière bêta de Xcode, sélectionnée avec `sudo xcode-select -s /Applications/Xcode-beta.app/Contents/Developer`. Les outils en ligne de commande pour macOS 27 conviennent aussi ; les outils plus anciens (sur certains systèmes, ceux de `xcode-select --install`) plantent lorsque lldb s'attache. |
| Débogage | Débogage sans mot de passe : exécutez `sudo DevToolsSecurity -enable`, et votre utilisateur doit appartenir au groupe `_developer`. Sinon, lldb attend la fenêtre « Developer Tools Access ». |

Le script vérifie l'UUID du binaire MenuBarAgent et compare chaque site de correctif par rapport à ses octets d'origine attendus avant d'écrire. Si quelque chose diffère, il se termine sans apporter de modifications.

## Utilisation

```bash
python3 enable_dynamic_island.py
```

Le script vérifie d'abord si la restriction de débogage du SIP est levée. Sinon, il explique comment la lever et se termine. Sinon, il demande :

1. Activer ou désactiver la Dynamic Island.
2. En cas d'activation : s'il faut redémarrer Siri AI pour que Siri AI et Spotlight apparaissent dans l'îlot. Si Siri AI est désactivé ou indisponible sur votre Mac, les deux choix conviennent.
3. En cas d'activation : s'il faut l'appliquer automatiquement à chaque connexion.

### Mode non interactif

```bash
python3 enable_dynamic_island.py --enable --autostart      # activer et réappliquer à chaque connexion
python3 enable_dynamic_island.py --enable --no-autostart   # activer pour cette session uniquement
python3 enable_dynamic_island.py --disable                 # désactiver et supprimer l'activation à la connexion
```

Options supplémentaires :

| Option | Effet |
| --- | --- |
| `--layout notch` | Par défaut. Les présentations étendues s'enroulent autour de l'encoche. |
| `--layout below` | Les présentations étendues apparaissent sous l'encoche aux largeurs standard. |
| `--layout classic` | Activation uniquement ; l'île reste à la position standard sous la barre de menus. |
| `--no-watch` | Ne pas démarrer l'assistant en arrière-plan. |
| `--restart-siri` | Redémarre Siri AI après l'application des correctifs pour que Siri AI et Spotlight apparaissent dans l'îlot (aussi à la connexion avec `--autostart`). Sans effet si Siri AI est désactivé ou indisponible. |
| `--no-restart-siri` | Laisse Siri AI tel quel. Sans l'une ou l'autre option, le réglage actuel de l'élément de connexion est conservé. |

### Désactivation

```bash
python3 enable_dynamic_island.py --disable
```

Arrête l'assistant en arrière-plan, supprime l'activation à la connexion, restaure les largeurs des processus clients, supprime les deux préférences et redémarre MenuBarAgent.

## Limitations connues

- Testé uniquement sur MacBook avec encoche ; la géométrie d'affichage externe est basée uniquement sur l'écran intégré.
- Les Live Activities tierces rendues par WidgetKit conservent les largeurs choisies par leurs applications.
- La largeur de la bannière de batterie faible n'a pas été vérifiée à l'écran.

## Avertissement

Ce projet modifie les processus système en cours d'exécution en mémoire et nécessite la levée de la restriction de débogage du SIP, ce qui réduit la sécurité du système. Il est fourni tel quel, sans garantie. Utilisez-le à vos propres risques.

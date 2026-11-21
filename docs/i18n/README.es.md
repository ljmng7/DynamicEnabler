> [!WARNING]
> **Debe levantarse la restricción de depuración de la Protección de Integridad del Sistema (SIP).** Basta con `csrutil enable --without debug`; el resto de SIP puede seguir activo.
> Este proyecto parchea procesos del sistema en ejecución en memoria. Consulte [Requisitos](#requisitos) para más detalles.

<p align="center">
  <a href="../../README.md">English</a> · <a href="README.zh-Hans.md">简体中文</a> · <a href="README.zh-Hant.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a> · <a href="README.fr.md">Français</a> · <a href="README.de.md">Deutsch</a> · <b>Español</b>
</p>

<p align="center">
  <a href="https://x.com/jazminli57"><img src="https://img.shields.io/badge/Follow-%40jazminli57-000000?logo=x&logoColor=white" alt="Follow @jazminli57 on X"></a>
</p>

---

# DynamicEnabler

Activa la Dynamic Island oculta de macOS 27 en modelos de MacBook Pro con notch, integrándola con el notch físico.

<p align="center">
  <a href="#cómo-funciona">Cómo funciona</a> ·
  <a href="#requisitos">Requisitos</a> ·
  <a href="#uso">Uso</a> ·
  <a href="#limitaciones-conocidas">Limitaciones conocidas</a> ·
  <a href="#descargo-de-responsabilidad">Descargo de responsabilidad</a>
</p>

---

macOS 27 (27.3 y posteriores) incluye una implementación de Dynamic Island dentro de MenuBarAgent que está inactiva en compilaciones que no son de Apple internas. Este script la activa parcheando el proceso MenuBarAgent en ejecución en memoria, luego ajusta su geometría para adaptarse al notch físico:

- Las presentaciones compactas y mínimas se sitúan a ambos lados del notch.
- Las presentaciones expandidas comienzan en la parte superior de la pantalla y rodean el notch.
- Los paneles expandidos de Live Activities del sistema (temporizadores, Reproduciendo, AirDrop, grabación de pantalla, actividades reflejadas de iPhone) y banners de batería se amplían para que su contenido no quede oculto detrás del notch.

## Cómo funciona

- **Activación.** MenuBarAgent lee dos preferencias, `EmulateCenterHUD` y `ShowSoftwareNotch`, cada una protegida por una verificación `IsAppleInternalBuild()`. El script establece ambas preferencias y reemplaza las dos protecciones con instrucciones `nop`.
- **Geometría del notch.** La Dynamic Island estándar se dibuja alrededor de un área de sensor virtual de 59 × 34 pt debajo de la barra de menús. Las funciones que devuelven este rectángulo se redirigen a pequeñas cavidades de código que leen el rectángulo real del notch desde `NSScreen` en tiempo de ejecución.
- **Anchos de paneles.** El ancho de cada panel expandido está fijo en el proceso cliente que lo renderiza. El script intercepta los inicializadores de frame de SwiftUI en esos procesos y reemplaza los anchos en el rango expandido. Los clientes con dimensionamiento inusual (AirDrop, banners de batería en ControlCenter) reciben parches dedicados.
- **Ayudante en segundo plano.** Los clientes de Live Activity como el proceso del temporizador se inician bajo demanda. Un trabajo auxiliar (`local.dynamic-island.watcher`) monitorea el registro de MenuBarAgent y parchea nuevos clientes a medida que aparecen. Sale cuando MenuBarAgent sale. Registro: `~/Library/Logs/dynamic_island_watcher.log`. También vigila la pantalla integrada: si cambia su resolución o se abre o cierra la tapa, vuelve a aplicar todos los parches con el nuevo tamaño del notch (o `classic` si no hay notch visible). MenuBarAgent se reinicia una vez y la isla vuelve en unos 20 segundos.
- **Activación al iniciar sesión.** El script puede copiarse a sí mismo en `~/Library/Application Support/DynamicEnabler/` e instalar un LaunchAgent (`local.dynamic-island.autostart`) que vuelve a aplicar los parches en cada inicio de sesión. Registro: `~/Library/Logs/dynamic_island_autostart.log`.
- **Varias pantallas.** Por defecto, la isla solo aparece en la pantalla principal. `--displays all` la muestra en todas las pantallas activas y `--displays secondary` en todas salvo la principal. MenuBarAgent elige la presentación de cada pantalla en una pequeña función que también fija el espaciado de los elementos de la barra de menús, así que el script la deja intacta, coloca una copia sin la comprobación de pantalla principal tras las code caves y redirige a esa copia solo las llamadas de la isla. Los banners del sistema llegan a todas las islas. Contribución de [Eric Dou](https://github.com/Eric-Dou).

Todos los parches están solo en memoria. No se modifica nada en el volumen del sistema. Reiniciar MenuBarAgent o el Mac elimina los parches a menos que la activación al iniciar sesión esté habilitada.

## Requisitos

| Requisito | Detalles |
| --- | --- |
| macOS | 27.3 beta, probado en las compilaciones 26C5035r (beta 1) y 26C5047e (beta 2). 27.2 y anteriores no son compatibles. Los puntos de parche se localizan por firma de código en los binarios instalados, por lo que compilaciones posteriores pueden funcionar sin actualizar; el script se detiene sin cambiar nada si alguna firma no coincide exactamente una vez. |
| Hardware | Cualquier Mac. Con una pantalla con notch activa (MacBook con notch y la tapa abierta), la isla se ajusta al notch; sin ella (Mac sin notch, tapa cerrada, solo pantallas externas), el script cambia automáticamente a `--layout classic`. Sin notch no se ha probado. |
| SIP | Debe levantarse la restricción de depuración de la Protección de Integridad del Sistema: en el modo de recuperación, ejecute `csrutil enable --without debug` (recomendado; el resto de SIP sigue activo). Deshabilitar SIP por completo (`csrutil disable`) también funciona. |
| Herramientas | La última beta de Xcode, seleccionada con `sudo xcode-select -s /Applications/Xcode-beta.app/Contents/Developer`. Las Command Line Tools para macOS 27 también sirven; las herramientas antiguas (en algunos sistemas, las de `xcode-select --install`) fallan cuando lldb se adjunta. |
| Depuración | Depuración sin contraseña: ejecuta `sudo DevToolsSecurity -enable` y tu usuario debe pertenecer al grupo `_developer`. Si no, lldb espera al cuadro de diálogo «Developer Tools Access». |

El script verifica el UUID del binario de MenuBarAgent y compara cada sitio de parche con sus bytes originales esperados antes de escribir. Si algo difiere, sale sin realizar cambios.

## Uso

```bash
python3 enable_dynamic_island.py
```

El script primero verifica si la restricción de depuración de SIP está levantada. Si no, explica cómo levantarla y sale. De lo contrario, pregunta:

1. Activar o desactivar la Dynamic Island.
2. Si se activa: si reiniciar Siri AI para que Siri AI y Spotlight aparezcan en la isla. Si Siri AI está desactivado o no disponible en tu Mac, cualquier opción sirve.
3. Si se activa: dónde debe aparecer la isla (solo en la pantalla principal, en todas, o en todas salvo la principal).
4. Si se activa: si aplicarla automáticamente en cada inicio de sesión.

### Modo no interactivo

```bash
python3 enable_dynamic_island.py --enable --autostart      # activar y volver a aplicar en cada inicio de sesión
python3 enable_dynamic_island.py --enable --no-autostart   # activar solo para esta sesión
python3 enable_dynamic_island.py --disable                 # desactivar y eliminar activación al iniciar sesión
```

Opciones adicionales:

| Opción | Efecto |
| --- | --- |
| `--layout notch` | Predeterminado. Las presentaciones expandidas rodean el notch. |
| `--layout below` | Las presentaciones expandidas aparecen debajo del notch con anchos estándar. |
| `--layout classic` | Solo activación; la isla permanece en la posición estándar debajo de la barra de menús. Se usa automáticamente cuando no hay ninguna pantalla con notch activa. |
| `--no-watch` | No iniciar el ayudante en segundo plano. |
| `--restart-siri` | Reinicia Siri AI tras aplicar los parches para que Siri AI y Spotlight aparezcan en la isla (también al iniciar sesión con `--autostart`). Sin efecto si Siri AI está desactivado o no disponible. |
| `--no-restart-siri` | Deja Siri AI como está. Sin ninguna de las dos opciones, se mantiene la configuración actual del elemento de inicio. |
| `--displays main` / `all` / `secondary` | Muestra la isla solo en la pantalla principal (por defecto), en todas las pantallas activas o en todas salvo la principal. En la build 26C5035r (beta 1), los banners del sistema siguen apareciendo solo en su pantalla preferida. Sin la opción, se mantiene la configuración actual del elemento de inicio. |

### Desactivación

```bash
python3 enable_dynamic_island.py --disable
```

Detiene el ayudante en segundo plano, elimina la activación al iniciar sesión, restaura los anchos de los procesos cliente, elimina las dos preferencias y reinicia MenuBarAgent.

## Limitaciones conocidas

- Por defecto, la isla solo aparece en la pantalla principal (las secundarias muestran las Actividades en vivo en la barra de menús, como en macOS estándar); consulta `--displays`. Los modos de varias pantallas se han probado con un iPad con Sidecar como segunda pantalla, todavía no con un MacBook con notch ni con más de dos pantallas. En la pantalla integrada con notch se ajusta al notch; si otra pantalla (externa, iPad con Sidecar) es la principal, usa allí la posición `classic`. Cambiar la pantalla principal surte efecto al instante. Los cambios de resolución de la pantalla integrada y abrir o cerrar la tapa los gestiona el ayudante en segundo plano, que vuelve a aplicar los parches (no con `--no-watch`). Los paneles expandidos conservan el ancho ampliado en otras pantallas.
- Las Live Activities de terceros renderizadas por WidgetKit mantienen los anchos elegidos por sus aplicaciones.
- El ancho del banner de batería baja no se ha verificado en pantalla.

## Descargo de responsabilidad

Este proyecto modifica procesos del sistema en ejecución en memoria y requiere levantar la restricción de depuración de SIP, lo que reduce la seguridad del sistema. Se proporciona tal cual, sin garantía. Úselo bajo su propio riesgo.

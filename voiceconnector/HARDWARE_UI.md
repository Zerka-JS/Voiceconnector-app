# Interface « rack VoiceConnector » — notes de migration

## Fichiers modifiés / ajoutés (dans `voiceconnector/`)
| Fichier | Changement |
|---|---|
| `ui/styles.py` | Palette `VINTAGE_PALETTE` (thème par défaut `vintage`), constantes crème/bois/châssis (`CREAM`, `INK`, `WOOD_*`, `HW_*`), `bus_color()`, QSS `_hardware_stylesheet()` ajoutée après la QSS de base |
| `ui/knob.py` | + `paint_dial`, `HardwareKnob(Knob)`, `RouteButton(Knob)`, `DialKnob` (plage linéaire) |
| `ui/fader.py` | + `HardwareFader(Fader)` : capuchon coloré, graduations à l'encre |
| `ui/vu_meter.py` | + `LedVuMeter(VuMeter)` : alvéole sombre, segments LED |
| `ui/hardware_widgets.py` (nouveau) | châssis + joues bois, `LogoLabel`, `LedMatrix`, `AnalogVu`, `SpectrumDisplay`, `TapeDeck`, `SourceIcon` |
| `ui/strip_widget.py` | tranche crème ; mêmes signaux et attributs, + `soloToggled`, `trimChanged`, `compChanged`, `gateChanged` |
| `ui/master_widget.py` | `OutputsPanel` (A1–A3) + `MasterWidget` refait (MUTE/EJECT ALL, MASTER, VU LED, spectre, 2 VU à aiguille, magnéto, INTELLIPAN EQ) |
| `ui/main_window.py` | châssis, logo, groupes d'entrées, matrice, câblage ; `LAYOUT_VERSION = 3` |
| `assets/voiceconnector_logo.png` | logo VoiceConnector complet (fond clair) : écran de chargement, rack, onglet À propos |
| `assets/voiceconnector_mark.png` | emblème seul (boucle + onde, fond clair) : en-têtes des fenêtres |
| `assets/icon.ico` | icône VoiceConnector (tuile claire, 16 à 256 px) : fenêtre, barre des tâches, zone de notification, `VoiceConnector.exe` |
| `tests/test_hardware_ui.py` (nouveau), `tests/test_logic.py` | nouveaux tests ; assertions de thème par défaut mises à jour |

## Ce qui est relié au moteur (signaux d'origine conservés)
gain, mute, routes, sends, Intellipan (pad), effets, renommage, volume par application, casque.
Matrice et boutons ronds des tranches sont deux vues du même état (`vm.set_route`).
A1 et MASTER pilotent tous deux `vm.set_headphone_gain` (bus du casque = A1), synchronisés.

## Ce que le moteur ne propose pas encore (UI prête, réglage mémorisé dans `config.json` → `ui.hardware`)
La fenêtre appelle ces méthodes **si elles existent** sur le contrôleur, sinon elle affiche un message une fois :

| Commande UI | Méthode attendue sur `vm` |
|---|---|
| Trim (entrées matérielles) | `set_trim(strip, db)` |
| Compr. / Gate (entrées virtuelles) | `set_comp(strip, v)`, `set_gate(strip, v)` (0…10) |
| EQ BASS / MID / TREBLE | `set_eq(band, db)` |
| Sorties A2, A3 | `set_bus_gain(output, db)` |
| Magnéto REC / PLAY / STOP / A-B | `set_recording(bool)`, `play_recording()`, `stop_recording()`, `set_ab_loop(bool)` |

Fonctions simulées côté interface avec les API existantes : **Solo** (coupe les autres entrées via `set_mute`,
rétablit ensuite), **MUTE ALL**, **EJECT ALL** (coupe toutes les routes, avec confirmation).

## Limites connues
* Le **spectre** est indicatif (niveau master × pente fixe) : le moteur ne fournit pas d'échantillons. `SpectrumDisplay.set_bands()` accepte une vraie FFT.
* Le **petit fader auxiliaire** du centre de la tranche n'existe pas : aucun paramètre moteur correspondant.
* La matrice affiche les routes configurées (`voicemeeter.routing`), pas les 16+ lignes A/B-OUT/REC de la maquette.
* Largeur minimale ≈ 1 500 px (6 tranches + matrice + sorties + master).
* **Non exécuté** : PySide6 n'était pas installable dans mon environnement. Compilation et vérification statique des noms/imports faites ; lance `pytest tests/` et regarde la fenêtre (`python main.py --demo` si disponible).

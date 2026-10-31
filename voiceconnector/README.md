<p align="center"><img src="assets/voiceconnector_logo.png" alt="VoiceConnector" width="420"></p>

# VoiceConnector

Console de mixage unique pour Windows : elle pilote **Voicemeeter Potato ou Banana**
(routing, volumes, mutes, VU-mètres) et **REAPER** (effets VST par strip), qui tournent
tous les deux cachés en arrière-plan. L'utilisateur ne voit que la console.

Elle fonctionne aussi **sans Voicemeeter** (mode B, autonome), se range selon le logiciel audio détecté
(FL Studio, Discord, OBS…), propose quatre thèmes et trois dispositions (FL Studio / MAO,
Streaming / Broadcast, Podcasting / Voix) et, pour un logiciel musical, un **Mode musical** qui synchronise
son mixer : voir [Deux modes, dispositions et thèmes](#deux-modes-dispositions-et-thèmes).

Développé par **Zerka**.

## Télécharger l'exécutable

Chaque push construit automatiquement `VoiceConnector.exe` sur une machine Windows de GitHub
(onglet **Actions**, workflow *Build Windows*) et le publie dans une release
**build-&lt;branche&gt;** (onglet **Releases**). Rien à installer pour le compiler.

## Prérequis

- Windows 10/11 64 bits
- [Voicemeeter Potato](https://vb-audio.com/Voicemeeter/potato.htm) ou
  [Voicemeeter Banana](https://vb-audio.com/Voicemeeter/banana.htm)
- [REAPER](https://www.reaper.fm/download.php) (6.37 ou plus récent conseillé)

Si l'un des deux manque, l'app affiche un lien de téléchargement, ou permet de
localiser l'exécutable s'il est installé ailleurs. En **mode B** (autonome), ni Voicemeeter ni REAPER ne sont
nécessaires. Facultatif : [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html), pour la
synchronisation du mixer de FL Studio.

## Premier lancement : faire passer le son par les effets

Les effets s'appliquent grâce à l'**insert** de Voicemeeter : chaque strip envoie son son
à REAPER, qui le renvoie traité. Une seule chose à régler, une fois :

1. Lance VoiceConnector.
2. Clique sur ⚙ (panneau Master) → **Ouvrir les préférences audio de REAPER**.
3. Dans REAPER : *Audio system* = **ASIO**, pilote = **Voicemeeter Insert Virtual ASIO**
   (« Voicemeeter Potato Insert Virtual ASIO » selon la version). Valide, puis ferme les
   réglages : la fenêtre de REAPER se cache de nouveau.

L'app active alors l'insert des strips qui ont une piste REAPER (et le remet comme avant
en partant). Tant que REAPER n'est pas sur ce pilote, l'insert n'est pas activé : le son
passe sans effets plutôt que d'être coupé.

## Projet REAPER

Au premier lancement, VoiceConnector ouvre un **projet vierge** : une piste câblée par strip,
aucun effet. Pour ouvrir ton propre projet (ton traitement, tes VST) :

- clique sur le bouton **Projet** du panneau Master (ou ⚙ → *Changer de projet…*) ;
- choisis un projet récent ou **Parcourir…** un `.rpp` ;
- coche **Ouvrir ce projet automatiquement au démarrage** pour qu'il devienne le projet
  par défaut ; coche **Me demander quel projet ouvrir à chaque démarrage** si tu veux
  choisir à chaque lancement.

Le projet s'ouvre tout de suite dans REAPER, sans redémarrer. **Son câblage n'est jamais
modifié** : chaque strip affiche la piste du projet qui écoute ses canaux Voicemeeter
(par exemple une piste dont l'entrée est « Input 1 » ou « Input 1 / Input 2 » va sur
Microphone). Si une strip n'a pas de piste, ajouter un effet dessus en crée une, câblée
sur son insert (désactivable dans ⚙ → Options).

## Utilisation

| Élément | Action |
|---|---|
| Fader | glisser (Maj = fin), molette, double-clic = 0 dB ; de -60 à +12 dB, comme Voicemeeter |
| Knobs Monitor / Mic Out / Discord / Obs | clic = envoi on/off vers A1 / B1 / B2 / A2, glisser = niveau d'envoi |
| Intellipan (carré au petit cube rouge) | glisser le cube = régler, double-clic = neutre, clic sur le titre ou clic droit = mode |
| Bouton Apps (strips virtuelles) | volume, mute et niveau de chaque application qui joue sur la strip |
| Pill « Mise à jour » (Master) | apparaît quand une nouvelle version est en ligne ; clic pour l'installer |
| Muet | coupe la strip |
| Pill d'effet | clic = ouvrir/fermer l'interface du plugin, clic droit = bypass / supprimer ; point ambre = plugin qui retarde la voix |
| Emplacement vide (« + ») | ajouter un effet (REAPER, Equalizer APO) ; FL Studio : l'afficher |
| Badge FX ⏻ | bypass de toute la chaîne d'effets de la strip (FL Studio : envoi à l'insert) |
| Nom de la strip | double-clic ou clic droit pour renommer (renomme aussi la piste REAPER) ; tous les noms : ⚙ → Canaux |
| Knob Casque | volume du bus A1 (double-clic = 0 dB) |
| Badge de connexion | vert = tout répond ; rouge = le moteur d'effets ou Voicemeeter ne répond plus |
| Bouton Projet | projet REAPER ouvert, clic pour en changer ; FL Studio / Equalizer APO : l'ouvrir |
| Bouton – (Master) | réduire VoiceConnector (barre des tâches, ou zone de notification si activé) |
| Barre d'actions | mode, logiciel suivi (clic = réglages), disposition, bouton **Mode musical** (logiciels musicaux seulement) |
| Bouton ⚙ (Master) | réglages : Général, Canaux, Audio, Mode et thème, Apparence, À propos |

`VoiceConnector.exe --demo` ouvre la console avec des moteurs simulés (sans Voicemeeter ni REAPER)
pour la découvrir ; `--demo --host flstudio` (ou `apo`, `none`) montre les autres moteurs
d'effets, et `--demo --mode b` le mode autonome. La démo « voit » FL Studio, Discord et OBS.

## Deux modes, dispositions et thèmes

La **barre d'actions**, sous le haut de la fenêtre, montre le mode en cours (A ou B), le **logiciel audio
suivi** (clic = réglages), le choix de la **disposition** et, seulement quand le logiciel suivi a une
partie musique, le bouton **Mode musical**. Tout se règle dans ⚙ → **Mode et thème**.

### Mode A — Voicemeeter + application vocale (par défaut)

Voicemeeter mixe le son, VoiceConnector le pilote comme avant et sait aussi **alimenter une application de
voix ou d'analyse** : Discord, OBS, TeamSpeak, Audacity, Zoom… (⚙ → Mode et thème → *Application
vocale*). Deux transports :

- **Bus Voicemeeter** (par défaut) : le micro part sur un bus (B1 pour un chat vocal, B2 pour un logiciel
  de diffusion ou d'édition, ou le bouton de la matrice qui porte le nom de l'application, ou le bus de
  ton choix) et l'application le choisit comme micro (« Voicemeeter Out B1 »…). Le routage conseillé est
  décrit étape par étape, et **n'est appliqué que si tu cliques sur « Appliquer le routage »** : le micro
  y est envoyé, le son que l'application ramène elle-même est coupé du bus (pas d'écho).
- **Flux VBAN** (expérimental) : Voicemeeter émet un flux réseau vers un récepteur VBAN ; ses réglages
  (nom, IP, port) sont envoyés par l'API Remote, à vérifier dans la fenêtre VBAN de Voicemeeter.

### Mode B — autonome, sans Voicemeeter

Ni Voicemeeter ni REAPER : les faders règlent les **volumes de Windows** (une tranche par application, le
micro, le volume général) et, avec le mode musical, les **pistes du mixer de FL Studio**. La liste des
canaux vient d'un **profil** :

- automatique, d'après le logiciel détecté ou choisi (FL Studio ou Ableton : Master, logiciel, Micro,
  Discord ; OBS / Discord : Micro, Game, Discord, Music ; Audacity / Zoom : Voix, Invité, Musique…) ;
- ou importé (⚙ → Mode et thème → *Importer un profil…*, exportable aussi) : un fichier JSON dont le
  format est décrit en tête de `core/profile_import.py`.

Clic droit sur le nom d'un canal d'application : *Assigner une application…*. Une tranche dont l'application
n'est pas lancée est grisée. Il n'y a ni routage, ni égaliseur, ni effets dans ce mode. Le mode se choisit
dans ⚙ → Mode et thème → *Mode de fonctionnement* (puis **Redémarrer VoiceConnector**), ou avec
`VoiceConnector.exe --mode a` / `--mode b`.

### Dispositions

| Disposition | Contenu |
|---|---|
| **Standard** | la console d'origine : entrées matérielles, entrées virtuelles, matrice de routage |
| **FL Studio / MAO** | Master à gauche, Bus Batterie, Synths, FX, Micro ; panoramique large, VU-mètres haute précision (graduation en dB) |
| **Streaming / Broadcast** | Micro, Game, Discord / Vocal, Music |
| **Podcasting / Voix** | Voix, Invité / Appel, Musique / Jingles ; égaliseur rapide (Graves, Médiums, Aigus et bouton **Clarté**), Compresseur et Gate intégrés à chaque canal |
| **Auto** | suit le logiciel suivi (FL Studio → FL Studio / MAO, Discord → Streaming…) |

Les emplacements d'une disposition sont rangés d'après le type et le nom des canaux ; chacun peut être donné
à un canal précis dans ⚙ → Mode et thème. Compresseur, Gate et égaliseur rapide existent sur les
entrées **matérielles** de Voicemeeter (celles que son API expose) ; en mode B, seules les dispositions
(rangement, panoramique, VU) s'appliquent.

### Thèmes

⚙ → Mode et thème → *Thème de l'interface* :

| Thème | Style |
|---|---|
| **Rétro** | VFD / Neon Wave Synth : écran VFD turquoise, néons violets, grille synthwave |
| **Futuriste** | Cyberpunk : interface HUD sombre, cyan électrique, accents jaune néon |
| **Épuré** | Minimaliste (Flat Modern) : clair, aplats nets, coins doux, aucun ornement |
| **Studio Pro** | Dark Slate : gris ardoise de studio, accent bleu, façon station audio |

Les couleurs d'accent d'origine (onglet *Apparence*) restent disponibles et remplacent le thème global.

### Mode musical / Production

Le bouton **Mode musical** n'existe **que** si le logiciel suivi ou détecté a une partie musique : FL Studio,
Ableton Live, REAPER, Cubase… Il est totalement caché pour Discord, Zoom, Spotify, OBS…, et le mode s'arrête
tout seul si le logiciel musical disparaît. Quand il est actif :

- la disposition **FL Studio / MAO** est appliquée (Master à gauche, Bus Batterie, Synths, FX) ;
- le traitement de la voix inutile est **libéré** pour réduire la latence : insert Voicemeeter, compresseur
  et gate des canaux vocaux ou cachés (leurs réglages sont rétablis en quittant le mode) ;
- les consignes du **retour ASIO direct** s'affichent : dans le logiciel musical, choisis ton pilote ASIO
  (par exemple *FL Studio ASIO*, ou celui de ton interface audio) plutôt que le chemin par Voicemeeter ;
- les **faders, muets, solos et panoramiques** de la console suivent ceux du mixer de FL Studio (et
  inversement), avec ou sans Voicemeeter. Pistes par défaut : Master = 0, Bus Batterie = 1, Synths = 2,
  FX = 3, Micro = 4 (modifiables dans `config.json` → `flsync.tracks`).

Installation de la synchronisation (une fois) : ⚙ → Mode et thème → *Synchronisation avec FL Studio*
indique ce qui manque et installe le script.

1. Crée deux ports MIDI virtuels avec [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) :
   « VoiceConnector FL » et « VoiceConnector VC ».
2. Clique sur *Installer le script FL Studio* (copie `device_VoiceConnector.py` dans
   `Documents\Image-Line\FL Studio\Settings\Hardware\VoiceConnector\`).
3. Dans FL Studio, *Options → MIDI settings* : entrée « VoiceConnector FL » (activée, Controller type
   « VoiceConnector », Port 1) et sortie « VoiceConnector VC » (activée, même Port 1).

Sans loopMIDI ou sans les modules `mido` et `python-rtmidi` (inclus dans l'exe), tout le reste marche :
seule la synchronisation du mixer est absente, et le bouton le dit.

## Moteurs d'effets : REAPER, FL Studio, Equalizer APO ou aucun

Au premier lancement, VoiceConnector affiche un **menu de sélection** : l'édition de Voicemeeter
(Potato ou Banana) et l'application qui applique les effets aux strips. Chaque choix
indique s'il est installé sur le PC et le délai qu'il ajoute à la voix. On le retrouve dans
⚙ → **Général** (*Voicemeeter* / *Effets*, puis **Redémarrer VoiceConnector**), ou en lançant
`VoiceConnector.exe --setup`.

| Moteur | Effets | Délai ajouté |
|---|---|---|
| **REAPER** | VST2/VST3/CLAP gérés depuis VoiceConnector : ajout, bypass, interface, projets | 2 tampons ASIO (insert) + plugins |
| **FL Studio** | réglés dans FL Studio (le « + » d'une strip l'affiche) | 2 tampons ASIO (insert) + plugins |
| **Equalizer APO** | plugins VST2 64 bits gérés depuis VoiceConnector, appliqués par Windows | plugins seulement |
| **Aucun** | Voicemeeter seul : volumes, routing, VU | aucun |

- **FL Studio** : VoiceConnector le lance (masqué) sur le projet choisi dans ⚙ → Général et le ferme
  en partant. Dans FL Studio, choisis *Voicemeeter Insert Virtual ASIO* (Options → Audio
  settings), puis une piste du mixer par strip, entrée et sortie sur les canaux d'insert de
  la strip (rappelés dans ⚙ → Audio). Le **badge FX** d'une strip envoie alors son son à
  FL Studio : laisse-le éteint tant que la piste n'est pas câblée, sinon la strip est muette.
- **Equalizer APO** : installe-le sur les périphériques Windows des strips (son
  *Configurator*). Les entrées virtuelles ont leur périphérique (*Voicemeeter Input*, *AUX*,
  *VAIO3*) ; pour le micro, choisis-le au premier ajout d'effet ou dans ⚙ → Audio, et coche
  *Entrées WDM en mode partagé* (sinon Windows, donc APO, ne voit pas le son du micro).
  VoiceConnector écrit `voiceconnector.txt` dans le dossier `config` d'Equalizer APO et ajoute une seule ligne
  `Include: voiceconnector.txt` à `config.txt` : le reste de ta configuration n'est pas touché. Les
  réglages des plugins faits dans l'éditeur d'Equalizer APO sont conservés.

## Délai de la voix

- Chaque strip affiche à côté de *Effets* le délai ajouté par ses plugins actifs
  (**+2.5 ms**, en ambre au-delà de 3 ms). Un **point ambre** sur un plugin signale un
  plugin à anticipation (limiteur, égaliseur en phase linéaire…) : sur le micro, préfère
  son mode « zéro latence ».
- ⚙ → **Audio** → *Délai de la voix* : délai total du moteur d'effets (tampons + plugins),
  et **Tampon de Voicemeeter** : 512, 256 ou 128 échantillons (WDM et Kernel Streaming).
  Plus petit = voix plus rapide (≈ 2,7 ms de moins par tranche de 128 à 48 kHz), mais plus
  de CPU : si le son craque, remonte d'un cran.
- Le moteur le plus rapide avec effets est **Equalizer APO** (pas d'aller-retour par
  l'insert) ; sans effets, **Aucun**.

## Intellipan

Comme dans Voicemeeter, chaque strip a son **Intellipan** : un carré où l'on déplace un
petit cube rouge.

- **Entrées matérielles** (Microphone, Musique, Autres 1) : 3 modes, choisis en cliquant
  sur le titre du carré (ou clic droit) — **VOIX** (couleur du son, le « Color Panel » de
  Voicemeeter), **POSITION** (panoramique) et **EFFETS** (écho / modulation).
- **Entrées virtuelles** (Appels, Bureau, Autres 2) : **POSITION** (panoramique 5.1).

Le mode choisi est retenu pour chaque strip. Double-clic = retour au neutre.

## Volume par application

Sur les strips virtuelles (Appels, Bureau, Autres 2 — les entrées « Voicemeeter Input »,
« Voicemeeter AUX Input », « Voicemeeter VAIO3 Input » de Windows), le bouton **Apps** à
côté de **Muet** affiche le nombre d'applications qui y jouent. Un clic ouvre la liste :
pour chaque application (Discord, Spotify, navigateur, jeu…), son volume, son niveau en
direct et un bouton pour la couper. Ce sont les réglages Windows de l'application (les
mêmes que dans le mélangeur de volume de Windows) : ils sont conservés par Windows.

Pour qu'une application apparaisse sur une strip, choisis la sortie Voicemeeter
correspondante dans l'application ou dans Windows (Paramètres › Son › Mélangeur de volume).
Si tes périphériques ont d'autres noms, indique-les dans `config.json`
(`voicemeeter.app_devices`, un morceau de nom par entrée virtuelle).

## Mises à jour

VoiceConnector vérifie au démarrage (puis toutes les 6 heures) si une nouvelle version est en ligne.
Si oui, une pill **Mise à jour** apparaît dans le panneau Master :

- **Mettre à jour** : VoiceConnector télécharge le nouvel exe, vérifie qu'il est complet (empreinte
  SHA-256), le met à la place de l'ancien puis redémarre dessus. Réglages et projet REAPER
  sont conservés.
- **Navigateur** : télécharge VoiceConnector.exe à la main ; **Plus tard** ; **Ignorer cette
  version** (VoiceConnector ne la propose plus, sauf recherche manuelle).

⚙ → **À propos** : version et numéro de build, **Rechercher une mise à jour**, et l'option
**Vérifier les mises à jour au démarrage**. Chaque build GitHub publie, à côté de
`VoiceConnector.exe`, un `version.json` (numéro de build, taille, empreinte) que VoiceConnector consulte.

## Personnaliser l'interface

⚙ → onglet **Apparence** :

- **Thème de couleur** : vert néon, cyan, bleu, violet, rose, rouge, orange, or, blanc, ou
  la pastille arc-en-ciel pour n'importe quelle couleur. Toute l'interface change tout de
  suite (knobs, VU-mètres, boutons, fonds teintés) ; le rouge du Muet et du petit cube
  reste rouge.
- **Arrière-plan** : *Choisir une image…* (PNG, JPG, WebP…). L'image est copiée dans
  `%APPDATA%\VoiceConnector`, cadrée pour remplir la fenêtre, et peut être assombrie (curseur
  *Assombrir l'image*). *Fond d'origine* revient au fond VoiceConnector.
- **Opacité des panneaux** : baisse-la pour voir l'image à travers les strips.
- **Fenêtre** : VoiceConnector s'ouvre en fenêtre compacte, comme Voicemeeter (juste les strips et
  le Master) ; sa taille et sa position sont mémorisées. *Taille compacte* la remet à sa
  taille d'origine, centrée. Coche *Réduire dans la zone de notification* pour que le
  bouton **–** l'envoie à côté de l'horloge (le son continue ; clic sur l'icône VoiceConnector pour
  la rouvrir, clic droit → *Quitter VoiceConnector*).

## Voicemeeter Potato ou Banana

⚙ → onglet **Général** → *Voicemeeter* : choisis **Potato** (8 entrées, 6 strips) ou
**Banana** (5 entrées, 5 strips), puis **Redémarrer VoiceConnector**. VoiceConnector lance alors la bonne
édition et ouvre un projet vierge adapté (`session-banana.rpp` pour Banana). Ferme l'autre
édition de Voicemeeter si elle tourne encore. Sur Banana, les knobs d'envoi sont de simples
interrupteurs on/off (Banana n'a pas de niveaux d'envoi par bus).

## Correspondance

| Strip | Potato | Banana |
|---|---|---|
| Microphone | Strip 0 (entrée matérielle 1) — canaux 1/2 | Strip 0 — canaux 1/2 |
| Musique | Strip 1 (entrée matérielle 2) — canaux 3/4 | Strip 1 — canaux 3/4 |
| Appels | Strip 5 (entrée virtuelle 1) — canaux 11/12 | Strip 3 (VAIO) — canaux 7/8 |
| Bureau | Strip 6 (entrée virtuelle 2) — canaux 19/20 | Strip 4 (AUX) — canaux 15/16 |
| Autres 1 | Strip 2 (entrée matérielle 3) — canaux 5/6 | Strip 2 — canaux 5/6 |
| Autres 2 | Strip 7 (entrée virtuelle 3) — canaux 27/28 | — |

Les canaux sont ceux de l'insert Voicemeeter (entrée de la piste REAPER). Modifiable dans
`config.json` (`voicemeeter.strip_maps.potato` / `.banana`, `voicemeeter.routing`).

## Fonctionnement

- **Voicemeeter** : API Remote via `voicemeeterlib`. Un thread lit paramètres et niveaux
  à 30 Hz ; les réglages de l'UI sont envoyés sans bloquer l'interface.
- **REAPER** : un petit script Lua (`assets/voiceconnector_bridge.lua`) tourne dans REAPER et
  échange avec l'app par fichiers texte dans `%APPDATA%\VoiceConnector\bridge`. Il utilise
  directement l'API de REAPER (ajout d'un plugin par son nom, suppression, bypass,
  ouverture des interfaces, ouverture de projet, sauvegarde). L'OSC natif de REAPER ne
  sait pas faire tout ça et demanderait un réglage manuel.
- Le pont est chargé par un bloc ajouté en tête de `Scripts\__startup.lua` de REAPER. Ce
  bloc ne fait rien quand tu lances REAPER toi-même : il ne s'active que si REAPER est
  lancé par VoiceConnector.
- Le projet ouvert est sauvegardé à chaque changement d'effet, toutes les 30 s s'il a
  été modifié, et à la fermeture.
- Fenêtres : Voicemeeter et REAPER sont masqués (et re-masqués s'ils réapparaissent).
  Les interfaces des plugins restent visibles et passent au premier plan.
- À la fermeture : sauvegarde du projet, inserts rendus, arrêt de REAPER, puis extinction
  propre de Voicemeeter (`Command.Shutdown`). Un Voicemeeter déjà ouvert avant l'app
  n'est pas fermé : il est simplement ré-affiché.

Relancer `VoiceConnector.exe` alors que VoiceConnector tourne déjà (par exemple cachée près de l'horloge)
ré-affiche simplement la fenêtre.

Si une autre édition de Voicemeeter est ouverte au démarrage, ou si seule l'autre édition
est installée, VoiceConnector propose de l'utiliser directement.

## Fichiers

```
%APPDATA%\VoiceConnector\
  config.json      réglages (copie modifiable du config.json livré)
  app.log          journal (3 × 2 Mo)
  session.rpp      projet vierge de VoiceConnector (session-banana.rpp pour Banana)
  background.*     image d'arrière-plan choisie
  processes.json   processus lancés par l'app (reprise après plantage)
  bridge\          échanges avec le pont Lua
  apo-demo\        configuration Equalizer APO simulée (--demo --host apo)
```

Les données de l'ancien dossier `%APPDATA%\REAPER Mixer` sont reprises automatiquement.

## Dépannage

- **Journal** : ⚙ → *Ouvrir le dossier des journaux* (`app.log`). Mets `"log_level": "DEBUG"`
  dans `config.json` pour plus de détails.
- **« REAPER ne répond pas » au démarrage** : au tout premier lancement, REAPER analyse tes
  plugins, ce qui peut prendre une minute : clique sur *Réessayer*. Vérifie aussi qu'aucune
  fenêtre de REAPER (licence d'évaluation…) n'attend une réponse.
- **Pas d'effet sur le son** : vérifie la carte son de REAPER dans ⚙ (doit indiquer
  « Voicemeeter Insert Virtual ASIO »), et que la piste de la strip est armée en écoute
  (sinon le badge FX l'indique au survol). Si REAPER affiche « [audio device closed] »,
  décoche dans ses préférences *Audio → Close audio device when stopped and application
  is inactive*.
- **Voir Voicemeeter ou REAPER** : ⚙ → cases *Afficher la fenêtre…* (re-masquées à la
  fermeture des réglages).
- **Pas de bouton « Mode musical »** : c'est voulu tant que le logiciel suivi n'a pas de partie musique
  (Discord, Zoom, Spotify, OBS…). Lance FL Studio, ou choisis-le dans ⚙ → Mode et thème → *Logiciel suivi*.
- **« Synchronisation du mixer : en attente »** : ⚙ → Mode et thème → *Synchronisation avec FL
  Studio* → **Vérifier** : il indique si `mido`, les ports loopMIDI ou le script manquent.
- **Une tranche est grisée (mode B)** : l'application du canal n'est pas lancée (ou joue sous un autre
  nom : clic droit sur le nom → *Assigner une application…*).

## Développement

Python 3.11+ (testé en 3.12 et 3.13).

```
cd voiceconnector
python -m pip install -r requirements.txt pytest
python main.py            # la vraie console
python main.py --demo     # moteurs simulés
python -m pytest tests    # tests (le pont Lua est testé si lua5.4 est installé)
python -m PyInstaller --noconfirm --clean build.spec   # → dist\VoiceConnector.exe
```

Avec le Python du Microsoft Store, `pip` peut échouer sur des chemins trop longs : active
les chemins longs (PowerShell administrateur, puis rouvre le terminal) :

```
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

Arborescence :

```
voiceconnector/
├── main.py                 démarrage, écran de chargement, popups, arrêt
├── core/
│   ├── config.py           config.json (défauts + %APPDATA%\VoiceConnector), chemins
│   ├── process_manager.py  moteurs (recherche, lancement, masquage, arrêt), projet REAPER
│   ├── voicemeeter_ctrl.py faders, mutes, routing, envois, insert, VU (30 Hz)
│   ├── reaper_ctrl.py      liaison avec le pont Lua (effets, pistes, projet, carte son)
│   ├── vst_scanner.py      liste des plugins (REAPER, cache, dossiers VST3)
│   ├── app_detector.py     catalogue et détection des logiciels audio (FL Studio, Discord, OBS…)
│   ├── layout_manager.py   dispositions (Standard, FL Studio / MAO, Streaming, Podcast) et plan des tranches
│   ├── themes.py           les quatre thèmes globaux (Rétro, Futuriste, Épuré, Studio Pro)
│   ├── session_mode.py     réglages de session, mode musical, traitement voix libéré
│   ├── session_coordinator.py  le « cerveau » de la fenêtre : logiciel suivi, mode musical, disposition
│   ├── mixer_router.py     routage vers l'application vocale (bus ou VBAN)
│   ├── profile_import.py   profils de canaux du mode B (import / export JSON)
│   ├── standalone_engine.py / standalone_ctrl.py  console du mode B (volumes de Windows, FL Studio)
│   ├── system_audio.py     sessions audio de Windows (Core Audio)
│   ├── fl_sync.py / fl_service.py  synchronisation du mixer de FL Studio (MIDI virtuel)
│   ├── detect_service.py   détection en tâche de fond
│   ├── mixer_model.py      paramètres et niveaux de la console (sans Qt)
│   └── demo.py             moteurs simulés (--demo, --selftest)
├── ui/                     fenêtre, strips, master, knob/fader/VU dessinés, dialogues
│   ├── main_window.py      fenêtre principale (applique les décisions du coordinateur)
│   ├── settings_dialog.py  réglages (onglets) ; layout_settings.py : onglet « Mode et thème »
│   ├── action_bar.py       barre d'actions (mode, logiciel, disposition, Mode musical)
│   └── wide_pan.py, backdrops.py  panoramique large, décors des thèmes globaux
├── assets/                 logo et icône VoiceConnector, police Inter, pont Lua, script MIDI de FL Studio
├── tests/                  tests (logique, pont Lua, interface, Win32)
├── config.json
├── requirements.txt
└── build.spec
```

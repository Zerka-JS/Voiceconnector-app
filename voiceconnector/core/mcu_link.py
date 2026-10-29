"""
Liaison Mackie Control : VoiceConnector se présente comme une surface de contrôle MCU, et pilote
Cubase / Nuendo, Studio One, Ableton Live, REAPER, Bitwig, Reason, Cakewalk, Ardour… (sans Qt).

Ce que Mackie Control sait faire : le transport (lecture, arrêt, enregistrement, boucle, métronome),
et, pour 8 pistes à la fois, nom, fader, panoramique, muet, solo, armement, sélection et niveau.
Ce qu'il ne sait pas : channel rack, pas du séquenceur, patterns, tempo, position libre : le Studio
grise ces parties pour ces logiciels (voir `state.capabilities`).

Le logiciel n'envoie que ce qui change : à l'ouverture, la liaison se présente (« me voici »), ce qui
pousse un logiciel à renvoyer tout son état. Elle tient pour établie la liaison dès qu'un message
MCU valide arrive.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from core import mcu_proto as mcu
from core.daw_model import (CAP_BANK, CAP_METERS, CAP_METRONOME, CAP_PLAYLIST, CAP_RECORD, CAP_SONG_MODE,
                            CAP_TRACK_ARM, CAP_TRACK_MIX, CAP_TRANSPORT, DawLink, DawTrack, clamp)
from core.fl_sync import Transport

log = logging.getLogger(__name__)

CAPABILITIES = frozenset({CAP_TRANSPORT, CAP_RECORD, CAP_SONG_MODE, CAP_METRONOME, CAP_PLAYLIST,
                          CAP_TRACK_ARM, CAP_TRACK_MIX, CAP_BANK, CAP_METERS})

GUARD_S = 0.45                       # après une écriture de fader, on garde notre valeur (écho du logiciel)
FADER_INTERVAL_S = 0.02              # écritures de fader au plus toutes les 20 ms (coalescées)
TOUCH_RELEASE_S = 0.35               # le doigt « se lève » du fader quand on n'y touche plus
RELEASE_S = 0.03                     # durée d'un appui de bouton
PAN_INTERVAL_S = 0.06                # un pas de potard toutes les 60 ms jusqu'à la position voulue
PAN_TRIES = 24
BANK_WAIT_S = 0.8                    # le groupe de 8 pistes a changé si les noms changent avant ce délai
VOLUME_TOLERANCE = 1.5 / mcu.MAX14


class McuLink(DawLink):
    key = "mcu"
    label = "Mackie Control"

    def __init__(self, transport: Transport, *, daw: str = "Mackie Control",
                 clock: Callable[[], float] = time.monotonic, keepalive_s: float = 0.0) -> None:
        super().__init__()
        self._transport = transport
        self._clock = clock
        self._keepalive_s = max(0.0, float(keepalive_s))
        self._next_keepalive = 0.0
        self._lcd = [" "] * mcu.LCD_SIZE
        self._digits: dict[int, mcu.Digit] = {}
        self._ring = [-1] * mcu.STRIPS                       # position de la couronne de chaque potard
        self._guard: dict[int, float] = {}                   # canal du fader -> fin de la garde
        self._fader_goal: dict[int, float] = {}              # écritures de fader en attente
        self._fader_sent: dict[int, float] = {}
        self._touching: dict[int, float] = {}                # fader touché -> heure de la dernière écriture
        self._pan_goal: dict[int, list[float]] = {}          # tranche -> [position voulue, essais, dernier envoi]
        self._releases: list[tuple[float, int]] = []         # (heure, bouton) à relâcher
        self._bank_wait: tuple[int, float] | None = None     # (sens, échéance)
        state = self.state
        state.daw = daw
        state.capabilities = CAPABILITIES
        state.song_mode_label = "Boucle"
        state.message = f"En attente de {daw}…"
        self._reset_tracks()

    # ------------------------------------------------------------ cycle de vie
    def _reset_tracks(self) -> None:
        state = self.state
        state.tracks = [DawTrack(index=state.track_offset + i, volume=-1.0) for i in range(mcu.STRIPS)]
        state.master = DawTrack(index=-1, name="Master", volume=-1.0)

    def start(self) -> None:
        super().start()
        self.refresh()

    def refresh(self) -> None:
        """Se présente de nouveau au logiciel : il renvoie tout l'état de la surface."""
        self._safe_send(mcu.host_query())

    def stop(self) -> None:
        if self.started:
            self._release_everything()
        super().stop()
        self.state.message = "Liaison arrêtée."

    def close(self) -> None:
        self.stop()
        try:
            self._transport.close()
        except Exception as exc:
            log.debug("Fermeture du transport MIDI : %s", exc)

    # -------------------------------------------------------------------- poll
    def poll(self) -> bool:
        if not self.started:
            return False
        now = self._clock()
        state = self.state
        if not self._transport.is_open:
            if state.connected:
                state.connected = False
                state.message = "Les ports MIDI sont fermés."
                return True
            return False
        changed = False
        try:
            incoming = self._transport.receive()
        except Exception as exc:
            log.debug("Lecture MIDI impossible : %s", exc)
            incoming = []
        for raw in incoming:
            event = mcu.parse(raw)
            if event is not None:
                changed |= self._handle(event, now)
        self._drive(now)
        return changed

    # --------------------------------------------------- messages du logiciel
    def _handle(self, event: Any, now: float) -> bool:
        state = self.state
        changed = False
        if isinstance(event, mcu.GoOffline):
            was = state.connected
            state.connected = False
            state.message = f"{state.daw} a coupé la liaison."
            return was
        if not state.connected:
            state.connected = True
            state.message = ""
            changed = True
        if isinstance(event, mcu.DeviceQuery):
            self.refresh()
        elif isinstance(event, mcu.HostReply):
            self._safe_send(mcu.host_confirm())
        elif isinstance(event, mcu.Led):
            changed |= self._on_led(event)
        elif isinstance(event, mcu.Fader):
            changed |= self._on_fader(event, now)
        elif isinstance(event, mcu.Ring):
            changed |= self._on_ring(event)
        elif isinstance(event, mcu.Meter):
            changed |= self._set(state.tracks[event.strip], "level", event.level)
        elif isinstance(event, mcu.Lcd):
            changed |= self._on_lcd(event, now)
        elif isinstance(event, mcu.Digit):
            changed |= self._on_digit(event)
        return changed

    @staticmethod
    def _set(target: Any, attribute: str, value: Any) -> bool:
        if getattr(target, attribute) == value:
            return False
        setattr(target, attribute, value)
        return True

    def _on_led(self, event: mcu.Led) -> bool:
        note, lit = event.note, event.state != mcu.LED_OFF
        tracks, transport = self.state.tracks, self.state.transport
        for base, attribute in ((mcu.REC, "armed"), (mcu.SOLO, "solo"), (mcu.MUTE, "muted"),
                                (mcu.SELECT, "selected")):
            if base <= note < base + mcu.STRIPS:
                return self._set(tracks[note - base], attribute, lit)
        if note == mcu.PLAY:
            return self._set(transport, "playing", event.state == mcu.LED_ON)
        if note == mcu.STOP:
            return self._set(transport, "playing", False) if lit else False
        if note == mcu.RECORD:
            return self._set(transport, "recording", lit)
        if note == mcu.CYCLE:
            return self._set(transport, "song_mode", lit)
        if note == mcu.CLICK:
            return self._set(transport, "metronome", lit)
        return False

    def _on_fader(self, event: mcu.Fader, now: float) -> bool:
        if now < self._guard.get(event.strip, 0.0):
            return False                                     # l'écho de notre propre écriture
        target = self.state.master if event.strip == mcu.MASTER else self.state.tracks[event.strip]
        if target is None or abs(target.volume - event.value) <= VOLUME_TOLERANCE:
            return False
        target.volume = event.value
        return True

    def _on_ring(self, event: mcu.Ring) -> bool:
        self._ring[event.strip] = event.position
        return self._set(self.state.tracks[event.strip], "pan", mcu.ring_to_pan(event.position, event.mode))

    def _on_lcd(self, event: mcu.Lcd, now: float) -> bool:
        for offset, char in enumerate(event.text, start=event.offset):
            if 0 <= offset < mcu.LCD_SIZE:
                self._lcd[offset] = char
        changed = False
        for strip, track in enumerate(self.state.tracks):
            start = strip * mcu.LCD_COLUMNS
            changed |= self._set(track, "name", "".join(self._lcd[start:start + mcu.LCD_COLUMNS]).strip())
        if changed and self._bank_wait is not None and now <= self._bank_wait[1]:
            self._shift_bank(self._bank_wait[0])
        return changed

    def _on_digit(self, event: mcu.Digit) -> bool:
        if event.index >= mcu.DISPLAY_DIGITS:
            return False                                     # les deux chiffres de l'assignation
        self._digits[event.index] = event
        return self._set(self.state.transport, "time_text", mcu.display_text(self._digits))

    # ------------------------------------------------------- ordres du Studio
    def _press(self, note: int) -> None:
        if not self.started:
            return
        self._safe_send(mcu.button(note, True))
        self._releases.append((self._clock() + RELEASE_S, note))

    def _safe_send(self, message: bytes) -> None:
        try:
            self._transport.send(message)
        except Exception as exc:                              # port fermé, câble MIDI virtuel supprimé…
            log.debug("Envoi MIDI impossible : %s", exc)

    def _release_everything(self) -> None:
        for _, note in self._releases:
            self._safe_send(mcu.button(note, False))
        self._releases.clear()
        for channel in list(self._touching):
            self._safe_send(mcu.button(self._touch_note(channel), False))
        self._touching.clear()
        self._fader_goal.clear()
        self._pan_goal.clear()

    def play_pause(self) -> None:
        self._press(mcu.STOP if self.state.transport.playing else mcu.PLAY)

    def stop_playback(self) -> None:
        self._press(mcu.STOP)

    def record(self) -> None:
        self._press(mcu.RECORD)

    def toggle_song_mode(self) -> None:
        self._press(mcu.CYCLE)

    def toggle_metronome(self) -> None:
        self._press(mcu.CLICK)

    def rewind(self) -> None:
        self._press(mcu.REWIND)

    # --- pistes
    def _strip(self, track: int) -> int | None:
        strip = track - self.state.track_offset
        return strip if 0 <= strip < mcu.STRIPS else None

    def set_track_mute(self, track: int, muted: bool) -> None:
        strip = self._strip(track)
        if strip is not None and self.state.tracks[strip].muted != bool(muted):
            self._press(mcu.MUTE + strip)

    def set_track_solo(self, track: int, solo: bool) -> None:
        strip = self._strip(track)
        if strip is not None and self.state.tracks[strip].solo != bool(solo):
            self._press(mcu.SOLO + strip)

    def set_track_arm(self, track: int, armed: bool) -> None:
        strip = self._strip(track)
        if strip is not None and self.state.tracks[strip].armed != bool(armed):
            self._press(mcu.REC + strip)

    def select_track(self, track: int) -> None:
        strip = self._strip(track)
        if strip is not None and not self.state.tracks[strip].selected:
            self._press(mcu.SELECT + strip)

    def set_track_volume(self, track: int, value: float) -> None:
        strip = self._strip(track)
        if strip is not None:
            self._write_fader(strip, value)

    def set_master_volume(self, value: float) -> None:
        self._write_fader(mcu.MASTER, value)

    def _write_fader(self, channel: int, value: float) -> None:
        if not self.started:
            return
        value = clamp(float(value), 0.0, 1.0)
        now = self._clock()
        target = self.state.master if channel == mcu.MASTER else self.state.tracks[channel]
        if target is not None:
            target.volume = value
        self._guard[channel] = now + GUARD_S
        self._fader_goal[channel] = value
        if channel not in self._touching:
            self._safe_send(mcu.button(self._touch_note(channel), True))
        self._touching[channel] = now
        self._flush_faders(now)

    @staticmethod
    def _touch_note(channel: int) -> int:
        return mcu.MASTER_TOUCH if channel == mcu.MASTER else mcu.FADER_TOUCH + channel

    def _flush_faders(self, now: float) -> None:
        for channel, value in list(self._fader_goal.items()):
            if now - self._fader_sent.get(channel, -1.0) >= FADER_INTERVAL_S:
                self._safe_send(mcu.fader(channel, value))
                self._fader_sent[channel] = now
                del self._fader_goal[channel]

    def set_track_pan(self, track: int, value: float) -> None:
        strip = self._strip(track)
        if strip is not None and self.started:
            self._pan_goal[strip] = [float(mcu.pan_to_ring(value)), 0.0, 0.0]

    def bank(self, delta: int) -> None:
        if delta == 0 or not self.started:
            return
        self._press(mcu.BANK_RIGHT if delta > 0 else mcu.BANK_LEFT)
        self._bank_wait = (1 if delta > 0 else -1, self._clock() + BANK_WAIT_S)

    def _shift_bank(self, direction: int) -> None:
        state = self.state
        state.track_offset = max(0, state.track_offset + direction * mcu.STRIPS)
        for strip, track in enumerate(state.tracks):
            track.index = state.track_offset + strip
        self._bank_wait = None

    # ------------------------------------------------------ temps qui passe
    def _drive(self, now: float) -> None:
        """Ce qui dépend du temps : relâcher les boutons, écrire les faders, tourner les potards."""
        if self._releases:
            due = [note for when, note in self._releases if when <= now]
            if due:
                self._releases = [(when, note) for when, note in self._releases if when > now]
                for note in due:
                    self._safe_send(mcu.button(note, False))
        self._flush_faders(now)
        for channel, last in list(self._touching.items()):
            if channel not in self._fader_goal and now - last >= TOUCH_RELEASE_S:
                self._safe_send(mcu.button(self._touch_note(channel), False))
                del self._touching[channel]
        self._steer_pans(now)
        if self._bank_wait is not None and now > self._bank_wait[1]:
            self._bank_wait = None
        if self._keepalive_s and now >= self._next_keepalive:
            self._next_keepalive = now + self._keepalive_s
            self._safe_send(mcu.ACTIVE_SENSING)

    def _steer_pans(self, now: float) -> None:
        """Le potard est relatif : on tourne par pas, en lisant la couronne, jusqu'à la position voulue."""
        for strip, goal in list(self._pan_goal.items()):
            target, tries, last = int(goal[0]), goal[1], goal[2]
            current = self._ring[strip]
            if current < 0:
                current = mcu.pan_to_ring(self.state.tracks[strip].pan)
            if current == target or tries >= PAN_TRIES:
                del self._pan_goal[strip]
                continue
            if now - last >= PAN_INTERVAL_S:
                self._safe_send(mcu.vpot(strip, int(clamp(target - current, -4, 4))))
                goal[1], goal[2] = tries + 1, now

"""Un logiciel de musique simulé qui parle Mackie Control (comme Cubase ou Studio One le feraient) :
il reçoit les appuis de boutons, les mouvements de fader et de potard de la surface, change son projet
et renvoie voyants, faders, couronnes et écrans. Sans MIDI, sans Qt."""

from fl_rig import FakeClock, FakeTransport

from core import mcu_proto as mcu


class FakeMcuDaw:
    def __init__(self, tracks: int = 20, name: str = "Trk") -> None:
        self.link = FakeTransport()                         # `sent` : ce que VoiceConnector écrit
        self.tracks = [{"name": f"{name} {i + 1}", "fader": 0.5, "pan": 6, "mute": False, "solo": False,
                        "arm": False} for i in range(tracks)]
        self.selected = 0
        self.bank = 0
        self.playing = self.recording = self.cycle = self.click = False
        self.master = 0.8
        self.time_text = "001.01.01.000"
        self.touched: set[int] = set()
        self.pressed: list[int] = []                        # boutons appuyés, dans l'ordre
        self.released: list[int] = []
        self.keepalives = 0
        self.queries = 0
        self.confirmed = False
        self.echo_faders = True
        self.accept_banks = True
        self.vpot_works = True
        self.vpots: list[tuple[int, int]] = []              # (tranche, crans) reçus
        self.fader_moves: list[tuple[int, float]] = []      # (canal, valeur) reçus

    # ----------------------------------------------------------------- envoi
    def push(self, *messages: bytes) -> None:
        self.link.inbox.extend(messages)

    def push_everything(self) -> None:
        """Tout l'état de la surface, comme un logiciel qui se connecte."""
        self.push(*self._leds(), *self._strips(), mcu.lcd(0, "".join(self._names())))
        self.push(*mcu.display(self.time_text))

    def _names(self) -> list[str]:
        return [self._track(i)["name"][:mcu.LCD_COLUMNS].ljust(mcu.LCD_COLUMNS) if self._track(i) else " " * 7
                for i in range(mcu.STRIPS)]

    def _track(self, strip: int) -> dict | None:
        index = self.bank + strip
        return self.tracks[index] if index < len(self.tracks) else None

    def _strips(self) -> list[bytes]:
        messages = []
        for strip in range(mcu.STRIPS):
            track = self._track(strip)
            messages.append(mcu.fader_position(strip, track["fader"] if track else 0.0))
            messages.append(mcu.ring(strip, track["pan"] if track else 0))
        messages.append(mcu.fader_position(mcu.MASTER, self.master))
        return messages

    def _leds(self) -> list[bytes]:
        lit = lambda value: mcu.LED_ON if value else mcu.LED_OFF          # noqa: E731
        messages = [mcu.led(mcu.PLAY, lit(self.playing)), mcu.led(mcu.STOP, lit(not self.playing)),
                    mcu.led(mcu.RECORD, lit(self.recording)), mcu.led(mcu.CYCLE, lit(self.cycle)),
                    mcu.led(mcu.CLICK, lit(self.click))]
        for strip in range(mcu.STRIPS):
            track = self._track(strip)
            messages += [mcu.led(mcu.MUTE + strip, lit(track and track["mute"])),
                         mcu.led(mcu.SOLO + strip, lit(track and track["solo"])),
                         mcu.led(mcu.REC + strip, lit(track and track["arm"])),
                         mcu.led(mcu.SELECT + strip, lit(track and self.bank + strip == self.selected))]
        return messages

    # ------------------------------------------------------------- réception
    def step(self) -> None:
        """Traite ce que la surface a écrit depuis le dernier appel."""
        received, self.link.sent = self.link.sent, []
        for raw in received:
            self._receive(raw)

    def _receive(self, raw: bytes) -> None:
        status = raw[0]
        if raw == mcu.ACTIVE_SENSING:
            self.keepalives += 1
        elif status == 0xF0:
            command = raw[5]
            if command == mcu.CMD_HOST_QUERY:
                self.queries += 1
                self.push(mcu.host_reply())
            elif command == mcu.CMD_HOST_CONFIRM:
                self.confirmed = True
                self.push_everything()
        elif status == 0x90:
            self._button(raw[1], raw[2] > 0)
        elif status & 0xF0 == 0xE0:
            self._fader(status & 0x0F, ((raw[2] << 7) | raw[1]) / mcu.MAX14)
        elif status == 0xB0 and 0x10 <= raw[1] < 0x18:
            delta = -(raw[2] & 0x3F) if raw[2] & 0x40 else raw[2] & 0x3F
            self.vpots.append((raw[1] - 0x10, delta))
            if self.vpot_works:
                self._vpot(raw[1] - 0x10, delta)

    def _button(self, note: int, down: bool) -> None:
        if note >= mcu.FADER_TOUCH and note <= mcu.MASTER_TOUCH:
            (self.touched.add if down else self.touched.discard)(note - mcu.FADER_TOUCH)
            return
        if not down:
            self.released.append(note)
            return
        self.pressed.append(note)
        if note == mcu.PLAY:
            self.playing = True
        elif note == mcu.STOP:
            self.playing = False
        elif note == mcu.RECORD:
            self.recording = not self.recording
        elif note == mcu.CYCLE:
            self.cycle = not self.cycle
        elif note == mcu.CLICK:
            self.click = not self.click
        elif note in (mcu.BANK_RIGHT, mcu.BANK_LEFT):
            step = mcu.STRIPS if note == mcu.BANK_RIGHT else -mcu.STRIPS
            if self.accept_banks and 0 <= self.bank + step < len(self.tracks):
                self.bank += step
                self.push_everything()
            return
        else:
            for base, key in ((mcu.MUTE, "mute"), (mcu.SOLO, "solo"), (mcu.REC, "arm")):
                if base <= note < base + mcu.STRIPS and self._track(note - base):
                    self._track(note - base)[key] ^= True
            if mcu.SELECT <= note < mcu.SELECT + mcu.STRIPS and self._track(note - mcu.SELECT):
                self.selected = self.bank + note - mcu.SELECT
        self.push(*self._leds())

    def _fader(self, channel: int, value: float) -> None:
        self.fader_moves.append((channel, value))
        if channel == mcu.MASTER:
            self.master = value
        elif self._track(channel):
            self._track(channel)["fader"] = value
        if self.echo_faders:
            self.push(mcu.fader_position(channel, value))

    def _vpot(self, strip: int, delta: int) -> None:
        track = self._track(strip)
        if track:
            track["pan"] = max(1, min(mcu.RING_LEDS, track["pan"] + delta))
            self.push(mcu.ring(strip, track["pan"]))


class McuRig:
    """Une horloge, un logiciel simulé et ce qu'il faut pour faire tourner la liaison avec lui."""

    def __init__(self, link_class, *, tracks: int = 20, **link_options) -> None:
        self.clock = FakeClock()
        self.daw = FakeMcuDaw(tracks=tracks)
        self.link = link_class(self.daw.link, clock=self.clock, **link_options)

    def run(self, seconds: float, tick: float = 0.02) -> None:
        for _ in range(int(round(seconds / tick))):
            self.clock.advance(tick)
            self.link.poll()
            self.daw.step()
            self.link.poll()

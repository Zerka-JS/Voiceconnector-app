"""Liaison console Voicemeeter ↔ pistes de FL Studio (mode A) : le pilote, contre le vrai
script de FL Studio (faux mixer), avec une console qui applique les ordres avec un délai."""

import pytest
from fl_rig import FakeClock, FlStudioRig

from core import fl_driver as drv
from core.fl_driver import FlSyncDriver
from core.fl_sync import FlStripSync, FlSyncCore, StripCommand, db_to_fl_volume, fl_volume_to_db
from core.mixer_model import MixerParams, StripParams


class FakeConsole:
    """Voicemeeter vu par le pilote : les ordres sont appliqués au `settle()` suivant."""

    def __init__(self, count: int = 5) -> None:
        self.strips = [StripParams(0.0, False, (), (), (("color", 0.0, 0.0), ("pan", 0.0, 0.5)))
                       for _ in range(count)]
        self.queue: list[tuple] = []
        self.orders: list[tuple] = []

    @property
    def params(self) -> MixerParams:
        return MixerParams(tuple(self.strips), 0.0)

    def set_gain(self, strip, db):
        self.queue.append(("gain", strip, db))
        self.orders.append(("gain", strip, db))

    def set_mute(self, strip, muted):
        self.queue.append(("mute", strip, muted))
        self.orders.append(("mute", strip, muted))

    def set_pad(self, strip, mode, x, y):
        self.queue.append(("pad", strip, (mode, x, y)))
        self.orders.append(("pad", strip, mode, x, y))

    def settle(self) -> MixerParams:
        """Voicemeeter applique ce qu'on lui a demandé."""
        for kind, strip, value in self.queue:
            old = self.strips[strip]
            if kind == "gain":
                self.strips[strip] = StripParams(value, old.mute, old.routes, old.sends, old.pads)
            elif kind == "mute":
                self.strips[strip] = StripParams(old.gain, value, old.routes, old.sends, old.pads)
            else:
                mode, x, y = value
                pads = tuple((m, x, y) if m == mode else (m, px, py) for m, px, py in old.pads)
                self.strips[strip] = StripParams(old.gain, old.mute, old.routes, old.sends, pads)
        self.queue.clear()
        return self.params

    def user_sets_gain(self, strip, db) -> MixerParams:
        old = self.strips[strip]
        self.strips[strip] = StripParams(db, old.mute, old.routes, old.sends, old.pads)
        return self.params


@pytest.fixture
def rig(monkeypatch):
    return FlStudioRig(monkeypatch)


class World:
    """Console + pilote + FL Studio (script réel), avec les tranches 0, 3 et 4 liées aux
    pistes 0 (Master), 1 (Bus Batterie) et 2 (Synths)."""

    def __init__(self, rig, links=None) -> None:
        self.rig, self.clock = rig, FakeClock()
        self.console = FakeConsole()
        self.core = FlSyncCore(rig, watch=(0, 1, 2), clock=self.clock)
        self.solos: list[tuple[int, bool]] = []
        self.connections: list[bool] = []
        self.driver = FlSyncDriver(
            FlStripSync(self.core, links or {0: 0, 3: 1, 4: 2}), self.console,
            on_solo=lambda strip, on: self.solos.append((strip, on)),
            on_connection=self.connections.append, clock=self.clock)
        self.driver.on_params(self.console.params)

    def run(self, ticks: int = 4) -> list[StripCommand]:
        commands: list[StripCommand] = []
        for _ in range(ticks):
            commands += self.driver.tick()
        return commands

    def connect(self) -> list[StripCommand]:
        self.driver.start()
        commands = self.run()
        self.driver.on_params(self.console.settle())
        return commands


@pytest.fixture
def world(rig):
    return World(rig)


def test_fl_studio_has_the_last_word_when_the_link_comes_up(rig):
    rig.mixer.tracks[1].update(volume=0.5, mute=True, pan=0.4)
    world = World(rig)
    commands = world.connect()
    assert world.driver.connected and world.connections == [True]
    gain = round(fl_volume_to_db(0.5), 1)
    assert ("gain", 3, gain) in world.console.orders and ("mute", 3, True) in world.console.orders
    pan = next(order for order in world.console.orders if order[0] == "pad" and order[1] == 3)
    assert pan[1:3] == (3, "pan") and pan[3] == pytest.approx(0.2, abs=0.01) and pan[4] == 0.5   # y conservé
    assert {c.kind for c in commands if c.strip == 3} >= {"gain", "mute", "pan"}
    # et la console n'a rien renvoyé à FL Studio en retour
    assert rig.mixer.tracks[1]["volume"] == pytest.approx(0.5, abs=1e-3) and rig.mixer.tracks[1]["mute"]


def test_console_changes_reach_fl_studio(world, rig):
    world.connect()
    world.clock.advance(1.0)
    written = world.driver.on_params(world.console.user_sets_gain(4, -12.0))
    world.driver.tick()
    assert written == 1
    assert rig.mixer.tracks[2]["volume"] == pytest.approx(db_to_fl_volume(-12.0), abs=1e-3)


def test_fl_studio_changes_reach_the_console(world, rig):
    world.connect()
    world.clock.advance(1.0)
    world.console.orders.clear()
    rig.mixer.tracks[0]["volume"] = 0.7
    rig.mixer.tracks[2]["mute"] = True
    rig.script.OnRefresh(0)
    applied = world.run(2)
    assert ("gain", 0, round(fl_volume_to_db(0.7), 1)) in world.console.orders
    assert ("mute", 4, True) in world.console.orders
    assert {(c.strip, c.kind) for c in applied} == {(0, "gain"), (4, "mute")}


def test_the_console_delay_does_not_send_stale_values_back(world, rig):
    """FL Studio bouge un fader ; avant que Voicemeeter n'applique l'ordre, la console annonce
    encore l'ancienne valeur : elle ne doit pas écraser FL Studio."""
    world.connect()
    world.clock.advance(1.0)
    rig.mixer.tracks[1]["volume"] = 0.5
    rig.script.OnRefresh(0)
    world.run(2)
    assert world.console.queue                                      # l'ordre est parti, pas appliqué
    stale = world.console.params                                    # la console dit encore 0 dB
    assert world.driver.on_params(stale) == 0
    world.driver.tick()
    assert rig.mixer.tracks[1]["volume"] == pytest.approx(0.5, abs=1e-3)
    settled = world.console.settle()                                # puis elle applique l'ordre
    assert world.driver.on_params(settled) == 0
    assert rig.mixer.tracks[1]["volume"] == pytest.approx(0.5, abs=1e-3)
    world.clock.advance(1.0)                                        # et une vraie action reste possible
    assert world.driver.on_params(world.console.user_sets_gain(3, -6.0)) == 1


def test_an_order_the_console_never_applies_is_forgotten_after_a_while(world, rig):
    world.connect()
    world.clock.advance(1.0)
    rig.mixer.tracks[1]["volume"] = 0.5
    rig.script.OnRefresh(0)
    world.run(2)
    stale = world.console.params
    world.clock.advance(drv.HOLD_S + 0.5)                           # Voicemeeter n'a jamais répondu
    assert world.driver.on_params(stale) == 1                       # la console a de nouveau la parole


def test_solo_goes_both_ways(world, rig):
    world.connect()
    assert sorted(world.solos) == [(0, False), (3, False), (4, False)]      # état initial des pistes liées
    world.solos.clear()
    world.clock.advance(1.0)
    rig.mixer.tracks[2]["solo"] = True
    rig.script.OnRefresh(0)
    world.run(2)
    assert world.solos == [(4, True)]
    assert world.driver.push_solo(0, True) is True                  # tranche liée : solo de FL Studio
    world.driver.tick()
    assert rig.mixer.tracks[0]["solo"]
    assert world.driver.push_solo(1, True) is False                 # tranche libre : solo de la console
    assert not any(t["solo"] for n, t in rig.mixer.tracks.items() if n not in (0, 2))


def test_linked_strips_and_stop(world, rig):
    assert world.driver.linked_strips == frozenset({0, 3, 4})
    world.connect()
    assert world.driver.connected
    world.driver.stop()
    assert not world.driver.connected and world.connections == [True, False]
    world.driver.stop()                                             # idempotent
    assert world.connections == [True, False]


def test_a_link_to_a_strip_the_console_does_not_have_is_ignored(rig):
    world = World(rig, links={0: 0, 7: 1})
    world.connect()
    assert all(order[1] != 7 for order in world.console.orders)


def test_commands_before_any_console_state_are_still_applied(rig):
    world = World(rig)
    world.driver._params = None
    world.driver.start()
    world.run()
    assert world.console.orders                                     # pan : y vaut 0 faute de mieux
    assert all(order[0] != "pad" or order[4] == 0.0 for order in world.console.orders)


def test_hold_helpers_describe_the_strip_as_the_console_will_have_it():
    base = StripParams(0.0, False, (), (), (("color", 0.1, 0.2), ("pan", 0.0, 0.5)))
    assert drv._with(base, StripCommand("gain", 0, -6.0)).gain == -6.0
    assert drv._with(base, StripCommand("mute", 0, True)).mute
    assert drv._with(base, StripCommand("pan", 0, 0.25)).pads == (("color", 0.1, 0.2), ("pan", 0.25, 0.5))
    assert drv._with(base, StripCommand("solo", 0, True)) == base
    assert drv._matches(base, StripCommand("gain", 0, -0.1)) and not drv._matches(base, StripCommand("gain", 0, -3.0))
    assert drv._matches(base, StripCommand("mute", 0, False)) and not drv._matches(base, StripCommand("mute", 0, True))
    no_pad = StripParams(0.0, False, (), (), ())
    assert drv._matches(no_pad, StripCommand("pan", 0, 0.3))        # rien à régler : considéré comme fait
    assert drv._matches(base, StripCommand("solo", 0, True))


def test_module_has_no_qt_dependency():
    source = open(drv.__file__, encoding="utf-8").read()
    assert "import PySide6" not in source and "from PySide6" not in source

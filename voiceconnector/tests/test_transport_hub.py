"""Un même port MIDI pour deux liaisons (mixer et Studio) : le hub, puis le vrai script de FL Studio
servant les deux en même temps (sans Qt, sans MIDI)."""

import pytest
from fl_rig import FakeClock, FakeFlProject, FakeTransport, FlStudioRig

from core import fl_sync
from core import studio_proto as sp
from core.fl_studio_link import FlStudioLink
from core.fl_sync import FlSyncCore, TransportError
from core.transport_hub import MAX_PENDING, TransportHub


def make_hub():
    opened: list[FakeTransport] = []

    def opener():
        transport = FakeTransport()
        opened.append(transport)
        return transport

    return TransportHub(opener), opened


# --------------------------------------------------------------------------- #
# Le hub
# --------------------------------------------------------------------------- #
def test_the_port_is_opened_once_for_all_views():
    hub, opened = make_hub()
    first, second = hub.open(), hub.open()
    assert len(opened) == 1 and hub.views == 2 and hub.transport_open
    assert first.is_open and second.is_open


def test_every_view_receives_every_message():
    hub, opened = make_hub()
    first, second = hub.open(), hub.open()
    opened[0].inbox += [b"a", b"b"]
    assert first.receive() == [b"a", b"b"]          # la lecture du transport est partagée
    assert second.receive() == [b"a", b"b"]         # ... et la deuxième vue reçoit la même chose
    assert first.receive() == [] and second.receive() == []
    opened[0].inbox += [b"c"]
    assert second.receive() == [b"c"] and first.receive() == [b"c"]


def test_sending_goes_through_the_shared_port():
    hub, opened = make_hub()
    first, second = hub.open(), hub.open()
    first.send(b"x")
    second.send(b"y")
    assert opened[0].sent == [b"x", b"y"]


def test_the_port_closes_with_the_last_view():
    hub, opened = make_hub()
    first, second = hub.open(), hub.open()
    first.close()
    assert opened[0].is_open and hub.views == 1 and not first.is_open and second.is_open
    first.close()                                   # sans effet
    assert opened[0].is_open
    second.close()
    assert not opened[0].is_open and hub.views == 0 and not hub.transport_open


def test_a_closed_view_neither_sends_nor_receives():
    hub, opened = make_hub()
    first, second = hub.open(), hub.open()
    first.close()
    first.send(b"x")
    opened[0].inbox += [b"a"]
    assert first.receive() == [] and opened[0].sent == []
    assert second.receive() == [b"a"]


def test_the_port_is_reopened_for_the_next_view():
    hub, opened = make_hub()
    hub.open().close()
    assert len(opened) == 1 and not opened[0].is_open
    view = hub.open()
    assert len(opened) == 2 and view.is_open


def test_a_port_closed_from_outside_is_replaced_for_the_next_view():
    hub, opened = make_hub()
    first = hub.open()
    opened[0].is_open = False                       # câble MIDI virtuel supprimé
    assert not first.is_open
    second = hub.open()
    assert len(opened) == 2 and second.is_open and hub.views == 2


def test_an_opening_failure_is_raised_and_leaves_no_view():
    def broken():
        raise TransportError("loopMIDI absent")

    hub = TransportHub(broken)
    with pytest.raises(TransportError):
        hub.open()
    assert hub.views == 0 and not hub.transport_open


def test_a_reading_error_is_swallowed():
    hub, opened = make_hub()
    view = hub.open()

    def boom():
        raise OSError("lecture impossible")

    opened[0].receive = boom
    assert view.receive() == []


def test_unread_messages_are_capped():
    hub, opened = make_hub()
    reader, sleeper = hub.open(), hub.open()
    for _ in range(0, MAX_PENDING + 500, 500):
        opened[0].inbox += [bytes([i % 100]) for i in range(500)]
        reader.receive()                             # le Studio lit, le mixer ne lit jamais
    assert len(sleeper.receive()) == MAX_PENDING


# --------------------------------------------------------------------------- #
# Le mixer et le Studio ensemble, avec le vrai script de FL Studio
# --------------------------------------------------------------------------- #
class Both:
    def __init__(self, monkeypatch) -> None:
        self.clock = FakeClock()
        self.project = FakeFlProject()
        self.rig = FlStudioRig(monkeypatch, self.project, self.clock)
        self.hub = TransportHub(lambda: self.rig)
        self.mixer = FlSyncCore(self.hub.open(), watch=(0, 1, 2), clock=self.clock)
        self.studio = FlStudioLink(self.hub.open(), clock=self.clock)
        self.mixer.start()
        self.studio.start()

    def run(self, seconds: float) -> None:
        for _ in range(int(round(seconds / 0.02))):
            self.rig.idle(0.02)
            self.mixer.poll()
            self.mixer.flush()
            self.studio.poll()


@pytest.fixture
def both(monkeypatch, capsys):
    yield Both(monkeypatch)
    assert "VoiceConnector (" not in capsys.readouterr().out


def test_mixer_and_studio_connect_through_one_port(both):
    both.run(0.5)
    assert both.mixer.connected and both.studio.state.connected
    assert both.mixer.state(1).known and both.mixer.state(1).volume == pytest.approx(0.8, abs=1e-3)
    assert len(both.studio.state.channels) == 4


def test_each_side_ignores_the_other_sides_messages(both):
    both.run(0.5)
    assert fl_sync.decode_from_fl(fl_sync.parse(sp.state(True, False, False, False, 120.0, 0, 96, 4))) is None
    assert fl_sync.decode_from_fl(fl_sync.parse(sp.ack(sp.ACK_GLOBAL_INDEX, "21"))) is None
    assert sp.decode(fl_sync.parse(fl_sync.ack(5))) is None
    assert sp.decode(fl_sync.parse(fl_sync.track_dump(1, 0.5, 0.0, False, False, "Insert"))) is None


def test_the_mixer_keeps_working_while_the_studio_is_busy(both):
    both.run(0.5)
    both.studio.play_pause()
    both.studio.toggle_step(1, 1)
    both.mixer.write_volume(1, 0.25)
    both.run(0.6)
    assert both.project.playing and 1 in both.project.channels[1].steps[1]
    assert both.rig.mixer.tracks[1]["volume"] == pytest.approx(0.25, abs=1e-3)
    both.rig.mixer.tracks[2]["volume"] = 0.6                 # un fader bouge dans FL Studio
    both.run(0.5)
    assert both.mixer.state(2).volume == pytest.approx(0.6, abs=1e-3)
    assert both.studio.state.transport.playing


def test_closing_one_side_leaves_the_other_alive(both):
    both.run(0.5)
    both.studio.close()
    both.run(0.5)
    assert both.hub.transport_open and both.mixer.connected
    both.rig.mixer.tracks[0]["volume"] = 0.33
    both.run(0.5)
    assert both.mixer.state(0).volume == pytest.approx(0.33, abs=1e-3)
    both.mixer.close()
    assert not both.hub.transport_open and not both.rig.is_open


def test_fl_studio_closing_disconnects_both_sides(both):
    both.run(0.5)
    both.rig.script.OnDeInit()
    both.run(0.1)
    assert not both.mixer.connected and not both.studio.state.connected
    both.rig.script.OnInit()
    both.run(3.0)
    assert both.mixer.connected and both.studio.state.connected

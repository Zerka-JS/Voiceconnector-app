"""La session du Studio : quel logiciel est piloté, comment, et le repli sur la démonstration
(sans Qt, sans MIDI)."""

from fl_rig import FakeClock, FakeFlProject, FakeTransport, FlStudioRig
from mcu_rig import FakeMcuDaw

from core import app_detector as ad
from core import mcu_proto as mcu
from core import studio_session as ss
from core.demo_daw import DemoDaw
from core.fl_studio_link import FlStudioLink
from core.fl_sync import SetupCheck, TransportError
from core.mcu_link import McuLink


def apps(*keys):
    return [ad.DetectedApp(ad.known_app(key), (1000 + i,)) for i, key in enumerate(keys)]


class Bench:
    """Une session avec un détecteur, des ports MIDI et une horloge de test."""

    def __init__(self, cfg=None, running=(), *, fl_ports=True, mcu_ports=True, hub=None):
        self.clock = FakeClock()
        self.running = list(running)
        self.fl_transports: list[FakeTransport] = []
        self.mcu_transports: list[FakeTransport] = []
        self.fl_ports, self.mcu_ports = fl_ports, mcu_ports
        self.cfg = cfg if cfg is not None else {}
        self.session = ss.StudioSession(self.cfg, scan=lambda: apps(*self.running), hub=hub or self._hub(),
                                        mcu_opener=self._mcu, clock=self.clock)

    def _hub(self):
        from core.transport_hub import TransportHub

        def opener():
            if not self.fl_ports:
                raise TransportError("Port MIDI virtuel introuvable : « VoiceConnector FL ». Crée-le avec loopMIDI.")
            transport = FakeTransport()
            self.fl_transports.append(transport)
            return transport

        return TransportHub(opener)

    def _mcu(self):
        if not self.mcu_ports:
            raise TransportError("Port MIDI virtuel introuvable : « VoiceConnector DAW ». Crée-le avec loopMIDI.")
        transport = FakeTransport()
        self.mcu_transports.append(transport)
        return transport

    def hear_from_daw(self) -> None:
        """Le logiciel (relié par Mackie Control) parle : la liaison est établie."""
        self.mcu_transports[-1].inbox.append(mcu.led(mcu.PLAY, mcu.LED_OFF))
        self.tick(0.1)

    def tick(self, seconds: float) -> int:
        flags = 0
        for _ in range(int(round(seconds / 0.05))):
            self.clock.advance(0.05)
            flags |= self.session.poll()
        return flags


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
def test_every_target_is_a_known_daw_with_a_backend_and_help():
    assert ss.TARGETS[0].key == "fl_studio"
    for target in ss.TARGETS:
        app = ad.known_app(target.key)
        assert app is not None and app.is_daw and app.name == target.name
        assert target.backend in (ss.BACKEND_FL, ss.BACKEND_MCU) and target.real and len(target.steps) >= 3
    assert ss.target_for("demo") is ss.DEMO_TARGET and not ss.DEMO_TARGET.real
    assert ss.target_for("pro_tools") is None and ss.target_for(None) is None


def test_every_mcu_target_explains_the_two_ports_and_where_to_add_the_surface():
    for target in ss.TARGETS:
        if target.backend != ss.BACKEND_MCU:
            continue
        text = " ".join(target.steps)
        assert "VoiceConnector DAW" in text and "VoiceConnector Retour" in text and "loopMIDI" in text
        assert target.steps[0].startswith("Dans ") and "→" in target.steps[0]


def test_the_fl_steps_match_the_documented_ports_and_script():
    text = " ".join(ss.TARGETS[0].steps)
    assert "VoiceConnector FL" in text and "VoiceConnector VC" in text and "device_VoiceConnector.py" in text


def test_choices_are_normalised():
    assert ss.normalize_choice("FL_Studio") == "fl_studio" and ss.normalize_choice("demo") == "demo"
    for junk in (None, "", "auto", "word", 5, "pro_tools"):
        assert ss.normalize_choice(junk) == ad.AUTO


def test_auto_picks_the_best_ranked_running_daw_whatever_the_detection_order():
    assert ad.known_app("reaper").priority < ad.known_app("cubase").priority < ad.known_app("studio_one").priority
    for order in (("discord", "cubase", "reaper"), ("reaper", "cubase", "discord"), ("cubase", "reaper")):
        target, running = ss.choose_target("auto", apps(*order))
        assert running and target.key == "reaper"
    for order in (("studio_one", "cubase"), ("cubase", "studio_one")):
        assert ss.choose_target("auto", apps(*order))[0].key == "cubase"
    target, running = ss.choose_target("auto", apps("fl_studio"))
    assert target.key == "fl_studio" and running


def test_auto_without_a_supported_daw_chooses_nothing():
    assert ss.choose_target("auto", []) == (None, False)
    assert ss.choose_target("auto", apps("discord", "spotify", "pro_tools", "lmms")) == (None, False)


def test_an_explicit_choice_wins_over_detection():
    target, running = ss.choose_target("ableton", apps("fl_studio"))
    assert target.key == "ableton" and not running
    target, running = ss.choose_target("ableton", apps("fl_studio", "ableton"))
    assert running
    target, running = ss.choose_target("demo", apps("fl_studio"))
    assert target is ss.DEMO_TARGET and running                        # la démonstration est toujours disponible


# --------------------------------------------------------------------------- #
# Ouverture
# --------------------------------------------------------------------------- #
def test_nothing_running_gives_the_demo_with_a_banner():
    bench = Bench()
    bench.session.open()
    session = bench.session
    assert session.demo and session.demo_reason == ss.DEMO_NOT_RUNNING and isinstance(session.link, DemoDaw)
    assert session.link.state.connected
    status = session.status()
    assert status.kind == "demo" and "Aucun logiciel" in status.text and status.configurable
    assert not bench.fl_transports and not bench.mcu_transports        # aucun port ouvert pour rien


def test_a_running_fl_studio_opens_the_live_link_and_waits_for_the_script():
    bench = Bench(running=["fl_studio"])
    bench.session.open()
    session = bench.session
    assert not session.demo and isinstance(session.link, FlStudioLink) and session.target.key == "fl_studio"
    assert len(bench.fl_transports) == 1 and not bench.mcu_transports
    status = session.status()
    assert status.kind == "waiting" and "FL Studio" in status.text and status.configurable


def test_a_running_cubase_opens_a_mackie_control_link():
    bench = Bench(running=["cubase", "discord"])
    bench.session.open()
    session = bench.session
    assert isinstance(session.link, McuLink) and session.target.key == "cubase"
    assert session.link.state.daw == "Cubase" and session.link.started
    assert bench.mcu_transports[0].sent and bench.mcu_transports[0].sent[0] == mcu.host_query()


def test_missing_ports_fall_back_to_the_demo_and_say_why():
    bench = Bench(running=["fl_studio"], fl_ports=False)
    bench.session.open()
    session = bench.session
    assert session.demo and session.demo_reason == ss.DEMO_UNAVAILABLE and session.target.key == "fl_studio"
    status = session.status()
    assert status.kind == "error" and "loopMIDI" in status.text and "Démonstration en attendant." in status.text
    assert status.text.count("..") == 0 and status.configurable


def test_a_broken_midi_library_is_reported_not_raised():
    bench = Bench(running=["reaper"])

    def broken():
        raise RuntimeError("rtmidi cassé")

    bench.session._mcu_opener = broken
    bench.session.open()
    assert bench.session.demo and "rtmidi cassé" in bench.session.reason


def test_the_chosen_demo_stays_a_demo():
    bench = Bench({"studio": {"daw": "demo"}}, running=["fl_studio"])
    bench.session.open()
    assert bench.session.demo and bench.session.demo_reason == ss.DEMO_CHOSEN
    assert bench.session.status().kind == "demo" and not bench.session.status().configurable
    bench.tick(10.0)
    assert bench.session.demo and bench.session.demo_reason == ss.DEMO_CHOSEN and not bench.fl_transports


def test_the_config_choice_is_used():
    bench = Bench({"studio": {"daw": "studio_one"}}, running=[])
    bench.session.open()
    session = bench.session
    assert not session.demo and session.target.key == "studio_one" and isinstance(session.link, McuLink)
    assert session.status().kind == "waiting" and "Studio One" in session.status().text


def test_select_reopens_and_remembers_the_choice_in_the_config():
    bench = Bench(running=["fl_studio", "cubase"])
    bench.session.open()
    assert bench.session.target.key in ("fl_studio", "cubase")
    bench.session.select("cubase")
    assert bench.session.target.key == "cubase" and bench.cfg["studio"]["daw"] == "cubase"
    bench.session.select("demo")
    assert bench.session.demo and bench.cfg["studio"]["daw"] == "demo"
    bench.session.select("auto")
    assert bench.cfg["studio"]["daw"] == "auto"
    bench.session.select("nimporte-quoi")
    assert bench.cfg["studio"]["daw"] == "auto"


def test_reopening_closes_the_previous_link():
    bench = Bench(running=["cubase"])
    bench.session.open()
    first = bench.mcu_transports[0]
    bench.session.reconnect()
    assert not first.is_open and len(bench.mcu_transports) == 2 and bench.mcu_transports[1].is_open


def test_closing_closes_everything_and_polling_afterwards_is_harmless():
    bench = Bench(running=["cubase"])
    bench.session.open()
    bench.session.close()
    assert not bench.mcu_transports[0].is_open
    assert bench.session.poll() == 0 and bench.tick(5.0) == 0


def test_a_failing_scan_is_not_fatal():
    bench = Bench()

    def boom():
        raise RuntimeError("psutil indisponible")

    bench.session._scan = boom
    bench.session.open()
    assert bench.session.demo
    assert bench.tick(7.0) == 0


# --------------------------------------------------------------------------- #
# Reliaison automatique
# --------------------------------------------------------------------------- #
def test_the_demo_hands_over_when_the_software_starts():
    bench = Bench()
    bench.session.open()
    demo = bench.session.link
    bench.tick(4.0)
    bench.running = ["fl_studio"]
    flags = bench.tick(ss.RETRY_S + 0.5)
    session = bench.session
    assert flags & ss.SWITCHED and flags & ss.CHANGED
    assert not session.demo and isinstance(session.link, FlStudioLink) and session.link is not demo
    assert session.status().kind == "waiting"


def test_the_demo_is_not_restarted_while_waiting():
    bench = Bench()
    bench.session.open()
    demo = bench.session.link
    demo.play_pause()
    bench.tick(ss.RETRY_S * 3)
    assert bench.session.link is demo and demo.state.transport.playing


def test_unavailable_ports_are_retried_without_disturbing_the_demo():
    bench = Bench(running=["fl_studio"], fl_ports=False)
    bench.session.open()
    demo = bench.session.link
    demo.play_pause()
    bench.tick(ss.UNAVAILABLE_RETRY_S * 2 + 1)
    assert bench.session.link is demo and demo.state.transport.playing and bench.session.demo
    bench.fl_ports = True                                              # l'utilisateur crée les ports avec loopMIDI
    bench.tick(ss.RETRY_S + 0.5)                                       # pas de nouvel essai avant le délai prévu
    assert bench.session.demo
    flags = bench.tick(ss.UNAVAILABLE_RETRY_S)
    assert flags & ss.SWITCHED and not bench.session.demo and isinstance(bench.session.link, FlStudioLink)
    assert not demo.started                                            # la démonstration est fermée


def test_the_reason_is_updated_when_it_changes():
    bench = Bench(running=["cubase"], mcu_ports=False)
    bench.session.open()
    assert "VoiceConnector DAW" in bench.session.reason
    bench.session._mcu_opener = lambda: (_ for _ in ()).throw(TransportError("Le module « mido » n'est pas installé"))
    bench.tick(ss.UNAVAILABLE_RETRY_S + 1)
    assert "mido" in bench.session.reason and bench.session.demo


def test_an_explicit_choice_attaches_even_when_the_process_is_unknown():
    bench = Bench({"studio": {"daw": "fl_studio"}}, running=[])
    bench.session.open()
    assert not bench.session.demo and isinstance(bench.session.link, FlStudioLink)


def test_a_software_that_disappears_is_reported_closed():
    bench = Bench(running=["cubase"])
    bench.session.open()
    link = bench.session.link
    bench.hear_from_daw()                                              # il a parlé : la liaison est établie
    assert link.state.connected
    bench.running = []
    assert bench.tick(ss.RETRY_S * (ss.MISSED_SCANS + 1)) & ss.CHANGED
    assert not link.state.connected and "Cubase est fermé" in link.state.message
    assert bench.session.status().kind == "waiting" and "fermé" in bench.session.status().text


def test_a_single_missed_scan_is_forgiven():
    bench = Bench(running=["cubase"])
    bench.session.open()
    link = bench.session.link
    bench.hear_from_daw()
    bench.running = []
    bench.tick(ss.RETRY_S + 0.2)                                       # un seul scan sans le logiciel
    bench.running = ["cubase"]
    bench.tick(ss.RETRY_S * 3)
    assert link.state.connected


def test_the_scan_counter_restarts_each_time_the_software_is_seen():
    bench = Bench(running=["cubase"])
    bench.session.open()
    link = bench.session.link
    bench.hear_from_daw()
    for _ in range(4):                                                 # un scan sur deux le rate : jamais deux de suite
        bench.running = []
        bench.tick(ss.RETRY_S)
        bench.running = ["cubase"]
        bench.tick(ss.RETRY_S)
    assert link.state.connected


def test_a_software_that_comes_back_is_greeted_again():
    bench = Bench(running=["cubase"])
    bench.session.open()
    link = bench.session.link
    bench.hear_from_daw()
    bench.running = []
    bench.tick(ss.RETRY_S * (ss.MISSED_SCANS + 1))
    assert not link.state.connected
    transport = bench.mcu_transports[0]
    transport.sent.clear()
    bench.running = ["cubase"]
    bench.tick(ss.RETRY_S + 0.5)
    assert mcu.host_query() in transport.sent                          # on se présente de nouveau


# --------------------------------------------------------------------------- #
# Aide et diagnostic
# --------------------------------------------------------------------------- #
class FakeMido:
    def __init__(self, outputs=(), inputs=()):
        self._outputs, self._inputs = list(outputs), list(inputs)

    def get_output_names(self):
        return self._outputs

    def get_input_names(self):
        return self._inputs


def test_fl_checks_list_the_library_the_ports_and_the_script(tmp_path):
    mido = FakeMido(["VoiceConnector FL 1"], ["VoiceConnector VC 2"])
    checks = ss.setup_checks(ss.TARGETS[0], {}, documents=tmp_path, mido_module=mido)
    assert all(isinstance(c, SetupCheck) for c in checks) and len(checks) == 4
    assert [c.ok for c in checks] == [True, True, True, False]
    assert "pas encore installé" in checks[-1].text


def test_mcu_checks_list_the_library_and_the_two_ports():
    mido = FakeMido(["VoiceConnector DAW 3"], [])
    checks = ss.setup_checks(ss.target_for("cubase"), {}, mido_module=mido)
    assert [c.ok for c in checks] == [True, True, False]
    assert "entrée MIDI du logiciel" in checks[1].text and "sortie MIDI du logiciel" in checks[2].text
    assert "introuvable" in checks[2].text


def test_mcu_checks_follow_the_configured_port_names():
    cfg = {"studio": {"mcu_out_port": "Pont A", "mcu_in_port": "Pont B"}}
    checks = ss.setup_checks(ss.target_for("reaper"), cfg, mido_module=FakeMido(["Pont A"], ["Pont B"]))
    assert [c.ok for c in checks] == [True, True, True]
    assert "Pont A" in checks[1].text and "Pont B" in checks[2].text


def test_a_missing_library_is_a_single_failed_check():
    class NoMido:
        def get_output_names(self):
            raise OSError("pas de pilote MIDI")

    checks = ss.setup_checks(ss.target_for("cubase"), {}, mido_module=NoMido())
    assert [c.ok for c in checks] == [True, False] and "illisibles" in checks[1].text


def test_the_demo_has_nothing_to_set_up():
    assert ss.setup_checks(ss.DEMO_TARGET) == [SetupCheck(True, "Démonstration : rien à configurer.")]


def test_the_session_help_is_the_help_of_the_target_or_fl_studio():
    bench = Bench(running=["cubase"])
    bench.session.open()
    assert bench.session.help_steps() == ss.target_for("cubase").steps
    assert "VoiceConnector DAW" in bench.session.ports_text() and "VoiceConnector Retour" in bench.session.ports_text()
    empty = Bench()
    empty.session.open()
    assert empty.session.help_steps() == ss.TARGETS[0].steps
    assert "VoiceConnector FL" in empty.session.ports_text() and "VoiceConnector VC" in empty.session.ports_text()
    assert empty.session.checks() and all(isinstance(c, SetupCheck) for c in empty.session.checks())


def test_configured_port_names_appear_in_the_help():
    cfg = {"flsync": {"out_port": "Sortie X", "in_port": "Entrée Y"}, "studio": {"mcu_out_port": "Pont A"}}
    bench = Bench(cfg)
    bench.session.open()
    assert "Sortie X" in bench.session.ports_text() and "Entrée Y" in bench.session.ports_text()
    cubase = Bench(cfg, running=["cubase"])
    cubase.session.open()
    assert "Pont A" in cubase.session.ports_text() and "VoiceConnector Retour" in cubase.session.ports_text()


# --------------------------------------------------------------------------- #
# Avec les vrais protocoles
# --------------------------------------------------------------------------- #
def test_fl_studio_end_to_end_through_the_session(monkeypatch):
    clock = FakeClock()
    project = FakeFlProject()
    rig = FlStudioRig(monkeypatch, project, clock)
    from core.transport_hub import TransportHub

    session = ss.StudioSession({}, scan=lambda: apps("fl_studio"), hub=TransportHub(lambda: rig), clock=clock)
    session.open()
    for _ in range(40):
        rig.idle(0.02)
        clock.advance(0.0)
        session.poll()
    state = session.link.state
    assert state.connected and len(state.channels) == 4 and session.status().kind == "live"
    session.link.play_pause()
    for _ in range(30):
        rig.idle(0.02)
        session.poll()
    assert project.playing and state.transport.playing
    session.close()
    assert not rig.is_open


def test_mackie_control_end_to_end_through_the_session():
    daw = FakeMcuDaw()
    clock = FakeClock()
    session = ss.StudioSession({}, scan=lambda: apps("studio_one"), mcu_opener=lambda: daw.link, clock=clock)
    session.open()
    for _ in range(40):
        clock.advance(0.02)
        session.poll()
        daw.step()
        session.poll()
    assert session.status().kind == "live" and session.link.state.tracks[0].name == "Trk 1"
    assert session.link.state.daw == "Studio One"
    session.link.set_track_mute(1, True)
    for _ in range(20):
        clock.advance(0.02)
        session.poll()
        daw.step()
        session.poll()
    assert daw.tracks[1]["mute"] and session.link.state.tracks[1].muted

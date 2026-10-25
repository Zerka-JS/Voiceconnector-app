"""Paramètres facultatifs de Voicemeeter : un refus n'est jamais une perte de connexion."""

import pytest

from core import optional_params as op
from core.optional_params import OptionalParams


class CApiError(Exception):
    """Comme voicemeeterlib.CAPIError : le code de retour de l'API Remote."""

    def __init__(self, code: int) -> None:
        super().__init__(f"VBVMR_SetParameterFloat returned {code}")
        self.code = code


class Remote:
    def __init__(self, reject=(), code=-3) -> None:
        self.values: dict[str, float | str] = {}
        self.reject, self.code = set(reject), code
        self.calls = 0

    def set(self, param, value):
        self.calls += 1
        if param in self.reject:
            raise CApiError(self.code)
        self.values[param] = value

    def get(self, param):
        self.calls += 1
        if param in self.reject:
            raise CApiError(self.code)
        return self.values.get(param, 0.0)


def test_writes_are_queued_and_taken_once_last_value_wins():
    params = OptionalParams()
    assert params.queue([("Strip[0].Comp", 3.0), ("Strip[0].Gate", 1.0), ("Strip[0].Comp", 5.0)]) == 3
    assert params.take() == {"Strip[0].Comp": 5.0, "Strip[0].Gate": 1.0}
    assert params.take() == {}


def test_a_successful_write_reaches_the_remote():
    params, remote = OptionalParams(), Remote()
    done = params.write(remote.set, {"Strip[0].Comp": 4.0, "vban.outstream[0].name": "Voix"})
    assert done == ["Strip[0].Comp", "vban.outstream[0].name"]
    assert remote.values == {"Strip[0].Comp": 4.0, "vban.outstream[0].name": "Voix"}


def test_an_unknown_parameter_is_refused_for_good_and_never_raises():
    params, remote = OptionalParams(), Remote(reject={"Strip[0].EQGain1"})
    assert params.write(remote.set, {"Strip[0].EQGain1": 2.0, "Strip[0].Comp": 4.0}) == ["Strip[0].Comp"]
    assert not params.supported("Strip[0].EQGain1") and params.supported("Strip[0].Comp")
    assert params.unsupported == frozenset({"Strip[0].EQGain1"})
    calls = remote.calls
    assert params.queue([("Strip[0].EQGain1", 1.0)]) == 0                  # n'est même plus mis en file
    assert params.write(remote.set, {"Strip[0].EQGain1": 1.0}) == []
    assert remote.calls == calls                                           # jamais redemandé


def test_other_errors_are_tolerated_a_few_times_then_refused():
    params, remote = OptionalParams(max_failures=3), Remote(reject={"x"}, code=-1)
    for _ in range(2):
        assert params.write(remote.set, {"x": 1.0}) == []
        assert params.supported("x")
    assert params.write(remote.set, {"x": 1.0}) == []
    assert not params.supported("x")


def test_a_success_resets_the_failure_count():
    params, remote = OptionalParams(max_failures=2), Remote(reject={"x"}, code=-1)
    params.write(remote.set, {"x": 1.0})
    remote.reject.clear()
    assert params.write(remote.set, {"x": 1.0}) == ["x"]
    remote.reject.add("x")
    params.write(remote.set, {"x": 1.0})
    assert params.supported("x")                                           # 1 échec depuis la dernière réussite


def test_exceptions_without_a_code_never_escape():
    params = OptionalParams()

    def broken(param, value):
        raise RuntimeError("boum")

    assert params.write(broken, {"a": 1.0}) == []
    assert params.read(lambda name: (_ for _ in ()).throw(OSError("non")), ["a"]) is None


def test_reads_return_all_values_or_none():
    params, remote = OptionalParams(), Remote()
    remote.values.update({"Strip[0].Comp": 2.5, "Strip[0].Gate": 0.5})
    assert params.read(remote.get, ["Strip[0].Comp", "Strip[0].Gate"]) == (2.5, 0.5)
    remote.reject.add("Strip[0].Gate")
    assert params.read(remote.get, ["Strip[0].Comp", "Strip[0].Gate"]) is None
    assert not params.supported("Strip[0].Gate")
    calls = remote.calls
    assert params.read(remote.get, ["Strip[0].Comp", "Strip[0].Gate"]) is None
    assert remote.calls == calls                                           # plus de requête vers un paramètre refusé


def test_forget_gives_every_parameter_another_chance():
    params, remote = OptionalParams(), Remote(reject={"x"})
    params.write(remote.set, {"x": 1.0})
    assert not params.supported("x")
    params.forget()
    assert params.supported("x") and params.unsupported == frozenset()


def test_module_constants_and_no_qt():
    assert op.UNKNOWN_PARAMETER == -3 and op.MAX_FAILURES == 3
    source = open(op.__file__, encoding="utf-8").read()
    assert "PySide6" not in source


@pytest.mark.parametrize("value", [1.0, "texte"])
def test_values_can_be_numbers_or_text(value):
    params, remote = OptionalParams(), Remote()
    assert params.write(remote.set, {"p": value}) == ["p"] and remote.values["p"] == value

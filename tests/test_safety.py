import pytest

from forexplat.config import Mode
from forexplat.safety import AccountModeMismatch, account_kind, assert_mode_matches


@pytest.mark.parametrize(
    "mode, trade_mode",
    [(Mode.DEMO, 0), (Mode.REAL, 2)],
)
def test_matching_account_passes(mode, trade_mode):
    assert_mode_matches(mode, trade_mode)


@pytest.mark.parametrize(
    "mode, trade_mode",
    [
        (Mode.DEMO, 2),  # perfil demo conectado a una cuenta real
        (Mode.REAL, 0),  # perfil real conectado a una cuenta demo
        (Mode.DEMO, 1),  # cuentas de concurso no se aceptan
        (Mode.REAL, 1),
        (Mode.DEMO, 99),  # valor desconocido
    ],
)
def test_mismatch_stops_everything(mode, trade_mode):
    with pytest.raises(AccountModeMismatch):
        assert_mode_matches(mode, trade_mode)


def test_account_kind_names():
    assert account_kind(0) == "demo"
    assert account_kind(2) == "real"
    assert "desconocida" in account_kind(7)

import numpy as np
import pandas as pd
import pytest

from forexplat import optimize as opt
from forexplat.config import RiskConfig
from forexplat.data import save_csv
from forexplat.strategy import Strategy, StrategyConfig
from test_cli import ENV, make_root, run

RISK = RiskConfig(risk_per_trade=0.0025, max_daily_loss=0.03, min_reward_risk=1.5, max_spread_pips=2.0)


def noise(seed=0, n=6000):
    rng = np.random.RandomState(seed)
    close = 1.1 + np.cumsum(rng.normal(0, 0.00025, n))
    opens = np.r_[close[0], close[:-1]]
    high = np.maximum(opens, close) + np.abs(rng.normal(0, 0.00012, n))
    low = np.minimum(opens, close) - np.abs(rng.normal(0, 0.00012, n))
    return pd.DataFrame({"time": pd.date_range("2026-01-05", periods=n, freq="5min", tz="UTC"),
                         "open": opens, "high": high, "low": low, "close": close})


def test_every_grid_combination_builds_a_valid_strategy():
    for family in opt.FAMILIES:
        names = list(family.grid)
        params = {n: family.grid[n][0] for n in names}
        for session in opt.SESSIONS:
            data = opt.strategy_dict(family, params, session, 2.0, "EURUSD")
            config = StrategyConfig.model_validate(data)
            assert config.symbols == ["EURUSD"]
            assert len(config.rules) == (4 if session != "sin_filtro" else 3)
            assert Strategy("x", config).warmup_bars > 0


def test_r_stats_and_required_t():
    stats = opt.r_stats([2, -1, -1, 2, -1])
    assert stats["n"] == 5 and stats["avg_r"] == pytest.approx(0.2) and stats["pf"] == pytest.approx(4 / 3)
    assert stats["max_losses"] == 2 and stats["max_dd_r"] == pytest.approx(2.0) and stats["total_r"] == 1
    assert opt.r_stats([])["n"] == 0 and opt.r_stats([1, 1, 1])["pf"] is None
    assert opt.required_t(1, 0.05) == pytest.approx(1.645, abs=0.001)
    assert opt.required_t(18, 0.05) == pytest.approx(2.77, abs=0.01)
    assert opt.required_t(18, 0.05) > opt.required_t(3, 0.05)


def test_expensive_pair_is_skipped_before_any_search():
    report = opt.evaluate_symbol("EURUSD", noise(), 0.0001, 5.0, RISK, sessions=["sin_filtro"])
    assert report.skipped and "coste" in report.skipped and report.combos == 0


def test_pure_noise_pipeline_runs_and_reports_honestly(tmp_path):
    reports = [opt.evaluate_symbol("EURUSD", noise(1, 8000), 0.0001, 0.3, RISK, sessions=["sin_filtro"])]
    need = opt.finalize(reports)
    winners = opt.pick_winners(reports)
    text = opt.render_report(reports, winners, opt.Criteria())
    assert reports[0].combos == 72 + 32 + 36 and need > 1.6
    assert "Combinaciones probadas en total: **140**" in text
    for finalist in reports[0].finalists:
        assert any("significativa" in key for key in finalist.checks)
        assert finalist.combo.fit["n"] >= 100
    if not winners:
        assert "Ninguna candidata demostro ventaja" in text
    text.encode("utf-8")


def make_finalist(symbol, family, smooth, passed=True):
    combo = opt.Combo(family, "ny_08-17", (0,), {"a": 1}, pd.DataFrame(columns=["signal_time", "r"]))
    combo.fit = opt.r_stats([1, -1] * 60)
    combo.valid = opt.r_stats([2, -1, -1] * 20)
    combo.smooth = smooth
    return opt.Finalist(symbol, combo, {"x": passed}, passed, [combo.valid] * 3, combo.valid, (3, 4), None, 5.0,
                        {8: 2}, {"trigger": "t"})


def test_winners_are_best_by_fit_score_one_per_symbol():
    report = opt.SymbolReport("EURUSD", 100, 1000, 0.3, 5.0, pd.Timestamp("2026-01-01", tz="UTC"))
    report.finalists = [make_finalist("EURUSD", "ruptura", 0.10), make_finalist("EURUSD", "cruce", 0.30),
                        make_finalist("GBPUSD", "cruce", 0.20), make_finalist("USDJPY", "cruce", 0.40, passed=False),
                        make_finalist("USDCAD", "ruptura", 0.05)]
    winners = opt.pick_winners([report])
    assert [(w.symbol, w.combo.family) for w in winners] == [("EURUSD", "cruce"), ("GBPUSD", "cruce")]
    assert opt.winner_name(winners[0]) == "m5_cruce_eurusd"
    text = opt.render_report([report], winners, opt.Criteria())
    assert "m5_cruce_eurusd" in text and "Ninguna candidata" not in text


def test_cli_optimize_requires_broker_time_for_sessions_and_runs_with_saved_candles(tmp_path):
    root = make_root(tmp_path)
    code, out, err = run(["optimize", "--use-saved"], root)
    assert code == 1 and "broker_time" in err
    save_csv(noise(2, 6000), root / "data" / "velas_EURUSD_M5.csv")
    code, out, err = run(["optimize", "--use-saved", "--no-session", "--symbol", "EURUSD", "--symbol", "GBPUSD",
                          "--out", str(tmp_path / "rep")], root)
    assert code == 0, err
    assert (tmp_path / "rep" / "informe_m5.md").exists() and "GBPUSD: no hay velas_GBPUSD_M5.csv" in (tmp_path / "rep" / "informe_m5.md").read_text(encoding="utf-8")
    assert "Informe:" in out
    before = (root / "config" / "strategies.yaml").read_text(encoding="utf-8")
    assert (root / "config" / "strategies.yaml").read_text(encoding="utf-8") == before


def test_cli_optimize_write_saves_winner_with_backup(tmp_path):
    from forexplat.ui import configio

    root = make_root(tmp_path)
    save_csv(noise(3, 6000), root / "data" / "velas_EURUSD_M5.csv")
    family = opt.FAMILIES[2]
    params = {n: v[0] for n, v in family.grid.items()}
    fake = make_finalist("EURUSD", "cruce", 0.3)
    fake.strategy_data = opt.strategy_dict(family, params, "ny_08-17", 2.0, "EURUSD")
    original = opt.pick_winners
    opt.pick_winners = lambda reports, how_many=2: [fake]
    try:
        code, out, err = run(["optimize", "--use-saved", "--no-session", "--symbol", "EURUSD", "--write",
                              "--out", str(tmp_path / "rep")], root)
    finally:
        opt.pick_winners = original
    assert code == 0, err
    saved = configio.read_strategy_dicts(root)["m5_cruce_eurusd"]
    assert saved["symbols"] == ["EURUSD"] and any(r["type"] == "session_filter" for r in saved["rules"])
    assert "timeframe: M5" in out and "m5_cruce_eurusd" in out
    assert list((root / "config" / "backups").glob("strategies_*"))

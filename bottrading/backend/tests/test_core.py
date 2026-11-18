import json
import pickle

import numpy as np
import pytest
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler

from app.config import RiskConfig
from app.errors import ModelContractError
from app.features import compute_features, indicators
from app.model import ForecastModel
from app.paper import PaperBroker
from app.risk import RiskContext, evaluate
from tests.conftest import make_candles

NOMBRES = [f"r_t-{19 - i}" for i in range(20)]


def notebook_window(closes, lags=20):
    """Réplica de construir_ventanas() del notebook: X_t = [r_{t-19}, ..., r_t]."""
    c = np.asarray(closes, float)
    r = np.full(len(c), np.nan)
    r[1:] = c[1:] / c[:-1] - 1
    t = len(c) - 1
    return r[t - lags + 1:t + 1]


def test_features_match_notebook_definition():
    closes = [c.close for c in make_candles(60)]
    assert np.allclose(compute_features(closes, NOMBRES)[0], notebook_window(closes))


def test_features_use_only_history_up_to_t():
    closes = [c.close for c in make_candles(60)]
    base = compute_features(closes[:-1], NOMBRES)
    changed_future = compute_features(closes[:-1] + [closes[-1] * 5], NOMBRES)
    assert not np.allclose(base, changed_future)            # el último cierre sí entra en r_t-0 ...
    assert np.allclose(base[0][:-1], compute_features(closes[:-2], NOMBRES)[0][1:])   # ... y nada más allá de t


def test_indicators_definitions():
    closes = np.array([c.close for c in make_candles(40)])
    ind = indicators(closes)
    assert ind["dist_sma20"] == pytest.approx(closes[-1] / closes[-20:].mean() - 1)
    r = closes[1:] / closes[:-1] - 1
    assert ind["vol20"] == pytest.approx(r[-20:].std(ddof=1))


def test_not_enough_history_and_unknown_variable():
    with pytest.raises(ModelContractError):
        compute_features([1.0] * 10, NOMBRES)
    with pytest.raises(ModelContractError):
        compute_features([1.0] * 40, ["rsi14"])


def _save_model(tmp_path, n_scaler=20, n_vars=20):
    rng = np.random.default_rng(0)
    X = rng.normal(0, 0.02, (200, n_scaler))
    sc = StandardScaler().fit(X)
    lr = LinearRegression().fit(sc.transform(X), rng.normal(0, 0.02, 200))
    d = tmp_path / "BTCUSDT"
    d.mkdir()
    pickle.dump(sc, open(d / "scaler.pkl", "wb"))
    pickle.dump(lr, open(d / "model.pkl", "wb"))
    (d / "features.json").write_text(json.dumps({"variables": NOMBRES[:n_vars], "model_version": "t1", "model_type": "lineal"}))
    return d


def test_model_loads_and_predicts_finite(tmp_path):
    m = ForecastModel("BTCUSDT", _save_model(tmp_path))
    assert np.isfinite(m.predict([c.close for c in make_candles(60)])) and m.version == "t1"


def test_model_rejects_scaler_contract_mismatch(tmp_path):
    with pytest.raises(ModelContractError):
        ForecastModel("BTCUSDT", _save_model(tmp_path, n_scaler=20, n_vars=15))


def test_model_missing_files(tmp_path):
    with pytest.raises(ModelContractError):
        ForecastModel("BTCUSDT", tmp_path / "nada")


# ------------------------------------------------------------------ paper
def broker(**kw):
    d = dict(cash=1000.0, fee_rate=0.001, slippage_bps=0.0, qty_step=0.01, min_notional=5.0)
    d.update(kw)
    return PaperBroker(**d)


def test_paper_buy_sell_charges_two_fees_and_computes_pnl():
    b = broker()
    f = b.buy(100.0, 1000.0)
    assert f.status == "executed" and f.fee == pytest.approx(f.notional * 0.001) and b.cash >= 0
    s = b.sell(110.0)
    assert s.status == "executed" and s.fee > 0 and s.pnl == pytest.approx(s.notional - s.fee - (f.notional + f.fee))
    assert b.qty == 0


def test_paper_quantity_rounding_and_min_notional():
    b = broker(qty_step=1.0)
    assert b.buy(100.0, 1000.0).qty == 9.0          # 1000/(100*1.001)=9.99 → 9
    assert broker(min_notional=50).buy(100.0, 20.0).status == "rejected"


def test_paper_insufficient_balance_no_short_no_double_buy():
    assert broker(cash=2.0).buy(100.0, 1000.0).reason.startswith("saldo insuficiente")
    assert broker().sell(100.0).status == "rejected"
    b = broker()
    b.buy(100.0, 500.0)
    assert b.buy(100.0, 500.0).status == "rejected"


def test_slippage_worsens_both_sides():
    b = broker(fee_rate=0.0, slippage_bps=10)
    assert b.buy(100.0, 1000.0).price == pytest.approx(100.1)
    assert b.sell(100.0).price == pytest.approx(99.9)


# ------------------------------------------------------------------- risk
def ctx(risk, **kw):
    d = dict(symbol="BTCUSDT", action="comprar", running=True, halted=False, data_age_hours=5.0, has_position=False,
             cash=12500.0, total_equity=50000.0, total_exposure=0.0, day_loss=0.0)
    d.update(kw)
    return RiskContext(**d)


def test_risk_basic_rules(risk):
    assert evaluate(risk, ctx(risk)).action == "comprar"
    assert evaluate(risk, ctx(risk, running=False)).status == "bloqueado"
    assert evaluate(risk, ctx(risk, data_age_hours=99)).status == "bloqueado"
    assert evaluate(risk, ctx(risk, action="vender")).action == "mantener"            # sin cortos
    assert evaluate(risk, ctx(risk, action="comprar", has_position=True)).action == "mantener"
    assert evaluate(risk, ctx(risk, cash=1.0)).status == "bloqueado"


def test_risk_exposure_limit_and_daily_loss_halt(risk):
    v = evaluate(risk, ctx(risk, total_exposure=44000.0))     # 0.9*50000-44000 = 1000 disponible
    assert v.action == "comprar" and v.max_notional == pytest.approx(1000.0) and v.status == "ajustado"
    assert evaluate(risk, ctx(risk, total_exposure=45000.0)).status == "bloqueado"
    v = evaluate(risk, ctx(risk, day_loss=0.06))
    assert v.trigger_halt and v.action == "mantener"
    assert evaluate(risk, ctx(risk, halted=True)).status == "bloqueado"


def test_risk_config_validation():
    from app.errors import ConfigError
    with pytest.raises(ConfigError):
        RiskConfig(1, ["A"], 0, 0, 2.0, 0.9, 0.05, 36, {}).validate()

import pytest
import requests

from app.errors import InvalidCandleData, MexcUnavailable
from app.mexc import DAY_MS, MexcClient, parse_klines, validate_candles
from tests.conftest import NOW_MS, make_candles


class Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._p, self.text = status, payload, "x"

    def json(self):
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


class Session:
    def __init__(self, seq):
        self.seq, self.n = seq, 0

    def get(self, url, params=None, timeout=None):
        item = self.seq[min(self.n, len(self.seq) - 1)]
        self.n += 1
        if isinstance(item, Exception):
            raise item
        return item


def rows(candles):
    return [[c.open_time, str(c.open), str(c.high), str(c.low), str(c.close), str(c.volume), c.close_time] for c in candles]


def client(seq, sleeps):
    return MexcClient(session=Session(seq), sleep=sleeps.append, clock=lambda: NOW_MS / 1000, retries=3)


def test_rate_limit_retries_with_growing_wait_then_fails():
    sleeps = []
    with pytest.raises(MexcUnavailable) as e:
        client([Resp(429)], sleeps).closed_candles("BTCUSDT")
    assert sleeps == [1.0, 2.0]                   # espera creciente, reintentos limitados (3 intentos)
    assert e.value.rate_limited and "429" in str(e.value)


def test_timeout_recovers_on_second_attempt():
    sleeps = []
    ok = Resp(200, rows(make_candles(5)))
    c = client([requests.exceptions.Timeout(), ok], sleeps)
    assert len(c.closed_candles("BTCUSDT", 3)) == 3 and sleeps == [1.0]


def test_open_candle_is_excluded():
    cs = make_candles(5)
    today_open = cs[-1].open_time + DAY_MS                      # vela del día en curso (aún abierta a las 12:00)
    open_c = type(cs[-1])(today_open, 1, 2, 0.5, 1.5, 1, today_open + DAY_MS - 1)
    got = client([Resp(200, rows(cs + [open_c]))], []).closed_candles("BTCUSDT", 10)
    assert got[-1].open_time == cs[-1].open_time


def test_client_error_is_reported_without_retry():
    with pytest.raises(MexcUnavailable) as e:
        client([Resp(400)], []).closed_candles("NOPE")
    assert "rechazó" in str(e.value)


def test_validation_detects_duplicates_gaps_and_bad_values():
    cs = make_candles(5)
    with pytest.raises(InvalidCandleData):
        validate_candles(cs + [cs[-1]], "X")
    with pytest.raises(InvalidCandleData):
        validate_candles([cs[0], cs[2]], "X")
    bad = type(cs[0])(cs[0].open_time, -1, 2, 0.5, 1, 1, 1)
    with pytest.raises(InvalidCandleData):
        validate_candles([bad], "X")
    with pytest.raises(InvalidCandleData):
        parse_klines([["a", "b"]], "X")

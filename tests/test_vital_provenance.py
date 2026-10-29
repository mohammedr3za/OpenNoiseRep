"""Provenance regressions: derived values must have matching plotted data."""
import asyncio
import datetime as dt
from types import SimpleNamespace
import pytest
from noiserep import analytics
from noiserep.ble import connection
from noiserep.errors import TransportError


def test_rr_estimate_has_five_minute_points_without_native_hrv():
    start = dt.datetime(2026,10,29)
    end = start + dt.timedelta(minutes=30)
    pairs = [(start+dt.timedelta(seconds=i), (800,832,864,832)[i%4]) for i in range(1800)]
    store = SimpleNamespace(samples=lambda *args: [],
        rest_summary=lambda *args: SimpleNamespace(start=start,end=end),
        rri=lambda *args: pairs)
    rows = analytics.vital_series(store,'hrv',start,end)
    assert len(rows)==6
    assert (rows[1][0]-rows[0][0]).total_seconds()==300
    assert all(value>0 for _,value in rows)


def test_native_hrv_is_preferred_over_rr_estimates():
    start=dt.datetime(2026,10,29)
    store=SimpleNamespace(samples=lambda *args:[(start,114)])
    assert analytics.vital_series(store,'hrv')==[(start,114)]


def test_connection_timeout_still_cleans_up_client(monkeypatch):
    closed=[]
    class Client:
        def __init__(self,*args,**kwargs):pass
        async def __aenter__(self):raise asyncio.TimeoutError()
        async def __aexit__(self,*args):closed.append(True)
    async def find(*args):return SimpleNamespace(name='Noise_REP_TEST')
    monkeypatch.setattr(connection,'find_band',find)
    monkeypatch.setattr(connection,'_import_bleak',lambda:(object,Client))
    async def run():
        async with connection.connect():pass
    with pytest.raises(TransportError,match='GATT services'):
        asyncio.run(run())
    assert closed==[True]

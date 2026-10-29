"""Reconnect path tests; no real scanner or Bluetooth adapter is used."""
import asyncio
from types import SimpleNamespace
import pytest
from noiserep.ble import connection

ADDRESS='D8:64:0D:30:3E:6D'
NAME='Noise_REP_3E6D'


def test_reuses_only_valid_previously_synced_identity():
    store=SimpleNamespace(device=lambda:{'mac':ADDRESS})
    assert connection.remembered_address(store)==ADDRESS
    assert connection.remembered_address(store,NAME)==ADDRESS
    assert connection.remembered_address(store,'Noise_REP_OTHER') is None
    assert connection.remembered_address(SimpleNamespace(device=lambda:{'mac':'bad'})) is None


def fake_backend(monkeypatch,fail_first=False):
    events=[]
    device=SimpleNamespace(address=ADDRESS,name=NAME)
    class Client:
        count=0
        def __init__(self,device,**kwargs):
            self.index=Client.count;Client.count+=1
            events.append(('client',kwargs))
        async def __aenter__(self):
            if fail_first and fail_first != 'cache' and self.index==0:
                if fail_first == 'not_found':
                    from bleak.exc import BleakDeviceNotFoundError
                    raise BleakDeviceNotFoundError(ADDRESS, 'Device not found in Windows')
                raise asyncio.TimeoutError()
            return self
        async def __aexit__(self,*args):events.append(('closed',self.index))
        async def start_notify(self,*args):
            if fail_first == 'cache' and self.index == 0:
                from bleak.exc import BleakCharacteristicNotFoundError
                raise BleakCharacteristicNotFoundError(connection.READ_UUID)
            events.append(('notify',self.index))
    async def find(*args):events.append(('scan',args[-1]));return device
    monkeypatch.setattr(connection,'_import_bleak',lambda:(object,Client))
    monkeypatch.setattr(connection,'_remembered_windows_device',lambda *args:device)
    monkeypatch.setattr(connection,'find_band',find)
    monkeypatch.setattr(connection.sys,'platform','win32')
    return events


def test_fast_reconnect_skips_discovery(monkeypatch):
    events=fake_backend(monkeypatch)
    async def run():
        async with connection.connect(address=ADDRESS) as band:
            assert band.address==ADDRESS
    asyncio.run(run())
    assert not any(e[0]=='scan' for e in events)
    assert len([e for e in events if e[0]=='notify'])==3
    assert events[-1]==('closed',0)


def test_failed_fast_attempt_closes_before_targeted_scan(monkeypatch):
    events=fake_backend(monkeypatch,'not_found')
    async def run():
        async with connection.connect(address=ADDRESS):pass
    asyncio.run(run())
    assert events.index(('closed',0))<events.index(('scan',ADDRESS))
    assert events[-1]==('closed',1)


def test_data_timeout_does_not_trigger_second_connection(monkeypatch):
    events=fake_backend(monkeypatch)
    async def run():
        async with connection.connect(address=ADDRESS):raise asyncio.TimeoutError()
    with pytest.raises(asyncio.TimeoutError):asyncio.run(run())
    assert len([e for e in events if e[0]=='client'])==1


def test_cancelled_connection_is_closed_without_retry(monkeypatch):
    events=fake_backend(monkeypatch)
    async def run():
        async with connection.connect(address=ADDRESS):raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):asyncio.run(run())
    assert events[-1]==('closed',0)
    assert not any(e[0]=='scan' for e in events)


def test_live_report_is_requested_before_history(monkeypatch):
    from contextlib import asynccontextmanager
    from noiserep import live
    events=[]
    store=SimpleNamespace(device=lambda:{'mac':ADDRESS})
    @asynccontextmanager
    async def connect(*args,**kwargs):
        assert kwargs['address']==ADDRESS
        yield SimpleNamespace(name=NAME,address=ADDRESS,c=SimpleNamespace(is_connected=True))
    async def poll(*args):
        events.append('live')
        if len(events)>1:raise asyncio.CancelledError()
        return True
    async def history(*args):events.append('history')
    monkeypatch.setattr(connection,'connect',connect)
    monkeypatch.setattr(live,'_poll',poll)
    monkeypatch.setattr(live,'_history',history)
    with pytest.raises(asyncio.CancelledError):asyncio.run(live.keep_linked(store))
    assert events[:2]==['live','history']

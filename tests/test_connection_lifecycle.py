"""Connection lifecycle regressions, using fake devices only."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import pytest
from noiserep import live
from noiserep.ble import connection
from noiserep.ble.transport import Band
from noiserep.errors import TransportError
from tests.test_reconnect import fake_backend, ADDRESS


def test_timeout_does_not_queue_an_immediate_second_gatt_attempt(monkeypatch):
    events = fake_backend(monkeypatch, fail_first=True)
    async def run():
        async with connection.connect(address=ADDRESS):
            pytest.fail('failed connection must not yield')
    with pytest.raises(TransportError):
        asyncio.run(run())
    assert len([e for e in events if e[0] == 'client']) == 1
    assert events[-1] == ('closed', 0)


def test_discovery_does_not_discard_advertised_local_name(monkeypatch):
    async def find(predicate, timeout):
        device = SimpleNamespace(name='old cached name')
        assert predicate(device, SimpleNamespace(local_name='Noise_REP_TEST'))
        return device
    scanner = SimpleNamespace(find_device_by_filter=find)
    assert asyncio.run(connection.find_band(scanner, None, 1)).name == 'old cached name'


def test_reconnect_failures_back_off(monkeypatch):
    delays = []
    @asynccontextmanager
    async def connect(*args, **kwargs):
        raise TransportError('GATT unavailable')
        yield
    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError()
    monkeypatch.setattr(connection, 'connect', connect)
    monkeypatch.setattr(live.asyncio, 'sleep', sleep)
    store = SimpleNamespace(device=lambda: None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(live.keep_linked(store, retry_s=1))
    assert delays == [1, 2, 4]


def test_empty_poll_reports_no_live_data():
    class FakeBand:
        async def send(self, data):
            pass
    result = asyncio.run(live._poll(FakeBand(), None, 0, lambda sample: None))
    assert result is False


def test_continuous_old_reports_cannot_block_history_drain_forever():
    class Flood:
        count = 0
        async def recv(self, timeout):
            self.count += 1
            return b'old report'
    band = Flood()
    with pytest.raises(TransportError, match='quiet'):
        asyncio.run(live._drain(band, max_messages=4))
    assert band.count == 4


def test_shutdown_cancels_notification_writes():
    class Client:
        async def write_gatt_char(self, *args, **kwargs):
            await asyncio.Event().wait()
    async def run():
        band = Band(Client(), connection.WRITE_UUID)
        band.on_notify('notify', bytes([0, 0, 0, 0, 1, 0]))
        await asyncio.sleep(0)
        await band.aclose()
        assert not band._tasks
        band.on_notify('notify', b'ignored after close')
        assert not band._tasks
    asyncio.run(run())


def test_failed_write_is_bounded_even_before_start_ack():
    class Client:
        async def write_gatt_char(self, *args, **kwargs):
            await asyncio.Event().wait()
    async def run():
        band = Band(Client(), connection.WRITE_UUID)
        with pytest.raises(TransportError, match='write'):
            await asyncio.wait_for(band.send(b'x', timeout=0.01), 0.2)
    asyncio.run(run())


def test_live_handoff_waits_for_cleanup_instead_of_timer(monkeypatch):
    from tests.test_gui_smoke import _window
    from PySide6 import QtCore
    w = _window()
    callbacks = []
    class Pending:
        def done(self): return False
        def add_done_callback(self, callback): self.callback = callback
        def cancel(self): pass
    pending = Pending()
    w._link_task = pending
    timers = []
    monkeypatch.setattr(QtCore.QTimer, 'singleShot', lambda delay, callback: timers.append(callback))
    try:
        w._release_link(lambda: callbacks.append('new owner'))
        for callback in timers:
            callback()
        assert not callbacks
        assert w._link_task is pending
        pending.callback(pending)
        assert callbacks == ['new owner']
    finally:
        w._link_task = None
        w.close()


def test_cleanup_failure_is_not_disguised_as_a_successful_handoff(monkeypatch):
    from noiserep.errors import ConnectionCleanupError
    @asynccontextmanager
    async def connect(*args, **kwargs):
        raise ConnectionCleanupError('release failed')
        yield
    monkeypatch.setattr(connection, 'connect', connect)
    store = SimpleNamespace(device=lambda: None)
    with pytest.raises(ConnectionCleanupError):
        asyncio.run(live.keep_linked(store))


def test_windows_connect_requests_fresh_vendor_service_without_rescanning(monkeypatch):
    events = fake_backend(monkeypatch)
    async def run():
        async with connection.connect(address=ADDRESS):
            pass
    asyncio.run(run())
    clients = [event for event in events if event[0] == 'client']
    assert len(clients) == 1
    assert clients[0][1]['winrt']['use_cached_services'] is False
    assert clients[0][1]['services'] == [connection.SERVICE_UUID]
    assert not any(event[0] == 'scan' for event in events)


def test_already_failed_cleanup_cannot_start_a_new_live_owner():
    from tests.test_gui_smoke import _window
    from noiserep.errors import ConnectionCleanupError
    w = _window()
    calls = []
    w._link_task = SimpleNamespace(done=lambda: True, cancelled=lambda: False,
        exception=lambda: ConnectionCleanupError('release failed'))
    try:
        w._release_link(lambda: calls.append('new owner'))
        assert not calls
        assert w._cleanup_blocked
    finally:
        w._link_task = None
        w.close()


def test_duplicate_app_does_not_steal_instance_lock(monkeypatch):
    from noiserep.gui import app as gui
    attempts = []
    class Lock:
        def __init__(self, path): pass
        def setStaleLockTime(self, age): pass
        def tryLock(self, timeout): attempts.append(timeout); return False
        def getLockInfo(self): return True, 12345, 'local', 'python'
    focused = []
    monkeypatch.setattr(gui.QtCore, 'QLockFile', Lock)
    monkeypatch.setattr(gui, '_focus_existing_window', lambda pid: focused.append(pid))
    with pytest.raises(SystemExit) as result:
        gui._take_single_instance()
    assert result.value.code == 0
    assert attempts == [0]
    assert focused == [12345]


def test_menu_bar_stop_requests_quit_without_terminating_process():
    import io
    from tests.test_gui_smoke import _window
    w = _window()
    pipe = io.StringIO()
    w._menubar_proc = SimpleNamespace(stdin=pipe, poll=lambda: None)
    try:
        w._stop_menubar()
        assert pipe.getvalue() == 'quit\n'
        assert w._menubar_stopping
        assert not w.live_btn.isEnabled()
    finally:
        w._menubar_proc = None
        w.close()


def test_helper_parent_pipe_uses_qt_shutdown_on_eof():
    import io
    from PySide6 import QtCore, QtTest
    from tests.test_gui_smoke import _app
    from noiserep.gui.menubar import ParentControl
    app = _app()
    class Owner(QtCore.QObject):
        stopped = False
        def quit(self):
            assert QtCore.QThread.currentThread() == app.thread()
            self.stopped = True
    owner = Owner()
    control = ParentControl(owner, io.StringIO(''))
    control._reader.join(timeout=1)
    QtTest.QTest.qWait(30)
    assert owner.stopped


def test_missing_live_reports_trigger_recovery(monkeypatch):
    from contextlib import asynccontextmanager
    calls, statuses = [], []
    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield SimpleNamespace(name='Noise_REP_TEST', address=None, c=SimpleNamespace(is_connected=True))
    async def poll(*args): calls.append('poll'); return False
    async def history(*args): pytest.fail('do not sync a silent connection')
    async def sleep(delay): raise asyncio.CancelledError()
    monkeypatch.setattr(connection, 'connect', connect)
    monkeypatch.setattr(live, '_poll', poll)
    monkeypatch.setattr(live, '_history', history)
    monkeypatch.setattr(live.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(live.keep_linked(SimpleNamespace(device=lambda: None), on_status=statuses.append))
    assert len(calls) == live.MAX_SILENT_POLLS
    assert any('stopped delivering live reports' in message for message in statuses)


def test_repeat_cancellation_waits_for_client_release():
    from noiserep.ble.connection import _close_device
    async def run():
        release = asyncio.Event()
        class Client:
            async def __aexit__(self, *args): await release.wait()
        task = asyncio.create_task(_close_device(Client(), None))
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())


def test_auto_reload_unlocks_before_starting_replacement(monkeypatch, tmp_path):
    from PySide6 import QtCore
    from noiserep.gui import app as gui
    path = str(tmp_path / 'instance.lock')
    lock = QtCore.QLockFile(path)
    assert lock.tryLock(0)
    called = []
    def launch(*args, **kwargs):
        replacement = QtCore.QLockFile(path)
        assert replacement.tryLock(0), 'old owner must release before replacement starts'
        called.append(True)
        replacement.unlock()
    monkeypatch.setattr(gui.subprocess, 'Popen', launch)
    gui._restart_gui(lock)
    assert called == [True]


def test_broken_helper_pipe_does_not_block_handoff():
    from tests.test_gui_smoke import _window
    w = _window()
    class BrokenPipe:
        def close(self): raise BrokenPipeError()
    w._menubar_proc = SimpleNamespace(stdin=BrokenPipe(), poll=lambda: 0)
    w._menubar_stopping = True
    calls = []
    w.start_link = lambda: calls.append('resumed')
    try:
        w._poll_menubar()
        assert w._menubar_proc is None
        assert calls == ['resumed']
    finally:
        w.close()


def test_embedded_window_does_not_treat_own_helper_as_an_external_owner(monkeypatch):
    import os
    from noiserep.gui import app as gui
    class Lock:
        def __init__(self, path): pass
        def setStaleLockTime(self, value): pass
        def getLockInfo(self): return True, os.getpid(), 'local', 'python'
    monkeypatch.setattr(gui.QtCore, 'QLockFile', Lock)
    assert gui.MainWindow._menubar_running() is False


def test_tray_cannot_open_a_second_connection_owned_by_its_window():
    from tests.test_gui_smoke import _app
    from noiserep.gui.menubar import MenubarApp
    mb = MenubarApp(_app(), db_path=':memory:', with_tray=False)
    mb._window = SimpleNamespace(_link_task=SimpleNamespace(done=lambda: False))
    try:
        mb.connect()
        assert not mb._connecting
        assert 'Main window owns' in mb._status
    finally:
        mb.store.close()


def test_helper_shutdown_waits_for_embedded_writer_before_closing_store():
    from tests.test_gui_smoke import _app
    from noiserep.gui.menubar import MenubarApp
    mb = MenubarApp(_app(), db_path=':memory:', with_tray=False)
    mb.store.close()
    events = []
    mb.store = SimpleNamespace(close=lambda: events.append('store closed'))
    mb.app = SimpleNamespace(quit=lambda: events.append('app quit'))
    async def run():
        async def writer():
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                events.append('writer released')
        task = asyncio.create_task(writer())
        await asyncio.sleep(0)
        mb._window = SimpleNamespace(_link_task=task)
        await mb._shutdown()
        assert task.done()
    asyncio.run(run())
    assert events.index('writer released') < events.index('store closed') < events.index('app quit')

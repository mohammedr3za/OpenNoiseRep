# Releasing the Noise REP band from this Mac

The band is unencrypted (Gadgetbridge `XiaomiUuids` `encrypted=false`), so macOS does not
create a persistent Bluetooth bond for it. "Pairing" is app-level only, and the band accepts
one central at a time. Releasing it from the Mac therefore usually just means making sure
the Mac is disconnected, so the phone can take over.

Do these in order, stopping when the band is free:

1. **Run the helper.** It only scans, connects and disconnects (BLE link layer). It sends no
   command to the band and touches none of its data.

   ```sh
   ./venv/bin/python tools/mac_release.py --disconnect
   ```

   `./venv/bin/python tools/mac_release.py --check` is a read-only status look
   (`system_profiler SPBluetoothDataType`). Use `--name` if the band advertises a different name.

2. **Or turn the Mac's Bluetooth off** (System Settings -> Bluetooth). This drops any link.

3. **If the band ever does show up as a paired device**, remove it with either:
   - System Settings -> Bluetooth -> the device -> "Forget This Device", or
   - `brew install blueutil`, then `blueutil --unpair <band-mac>`.

4. **Turn the phone's Bluetooth back on** and reopen NoiseFit so the phone reconnects.

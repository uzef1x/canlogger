# Wiring: T-2CANFD boards to the LEAF / e-NV200 OBD-II connector

Every claim has a source and a confidence level. Nothing here comes from the CANBRIDGE project.
**No Nissan document was obtained.** The Nissan service manual (LAN section) is the primary
source and is paywalled; verify against it or by meter before connecting.

## OBD-II connector pinout

| Pin | Signal | Sources agreeing | Confidence |
|-----|--------|------------------|------------|
| 6 / 14 | CAR-CAN high / low ("alternate CAN") | [leaf-obd manual](https://leaf-obd.readthedocs.io/en/latest/diagnostic-connector.html), [OpenEnergyMonitor ZE0/e-NV200 cable](https://shop.openenergymonitor.com/nissan-ze0-e-nv200-obd2-ovms-cable/) (6 = H, 14 = L), ISO 15031-3 | high |
| 13 / 12 | EV-CAN high / low ("primary CAN") | leaf-obd manual (13 = H, 12 = L), OpenEnergyMonitor cable (13 = H, 12 = L) | medium-high (two independent sources, both community) |
| 11 / 3 | AV-CAN high / low | leaf-obd manual only (it cites a mynissanleaf.com forum thread) | **low** (single source, not independently confirmed) |
| 16 | permanent +12 V | ISO 15031-3, both sources | high |
| 8 | +12 V only when vehicle powered on | leaf-obd manual only (forum-derived) | low-medium |
| 4 / 5 | chassis / signal ground | ISO 15031-3, OpenEnergyMonitor cable (pin 4) | high |

- Protocol: ISO 15765-4 CAN, 11-bit IDs, 500 kbit/s (leaf-obd manual). One search result
  claimed 29-bit extended frames; it came from a dongle-vendor blog and contradicts the others.
- **ZE0 only.** [OVMS docs](https://docs.openvehicles.com/en/latest/components/vehicle_nissanleaf/docs/index.html):
  on ZE1 (2018+) the OBD-II port is isolated by a gateway that powers down with ignition off,
  so the buses must be tapped behind the cluster. The OBD-II cable works for ZE0.
- **e-NV200:** the OpenEnergyMonitor cable is sold for "all models of e-NV200" with the same
  EV-CAN/CAR-CAN pins. No source gives e-NV200 AV-CAN pins. OVMS calls its third bus "IT-CAN"
  and does not use it.
- The leaf-obd manual does not state model years; forum material is mostly 2011-2013 hardware.

## Board (T-2CANFD V1.0)

- Channel A = MCP2518FD (CAN FD), channel B = ESP32-S3 TWAI (classic). Confirmed by the
  [LilyGo wiki](https://wiki.lilygo.cc/products/industrial-series/t-2can-fd/).
- LilyGo schematic ([T-2Can-Fd_V1.0.pdf](https://github.com/Xinyuan-LilyGO/T-2Can/blob/main/project/T-2Can-Fd_V1.0.pdf)),
  read directly: isolated transceiver per port; **fixed 120 ohm across CANH-CANL on both ports
  (RZ2 port A, RZ1 port B), no jumper/switch**; transceiver ground pin (CANG) goes to the SGND net;
  DGND is the TVS return, tied to SGND only via 1 Mohm || 1 nF.
- **DIP switch:** the [Battery Emulator hardware page](https://dalathegreat.github.io/Battery-Emulator-Wiki/hardware/lilygo_t_2can/)
  says "The CAN-FD needs the DIP switches setting correctly. You need to enable DIP switch 2."
  Third-party, picture not readable here, and LilyGo's own wiki does not mention it. **Check
  this on the physical boards: if channel A hears nothing, this is the first suspect.**
- Power input range: not stated by LilyGo's wiki. The same page recommends 12 V instead of 5 V
  if CAN errors appear.

## Consequences (my reasoning, untested)

- A tap should not add a third 120 ohm. Remove RZ1/RZ2 on any port used as a tap.
- Wire SGND (not DGND) to OBD pin 5.
- Prefer powering from pin 8 (switched) if confirmed, otherwise add a switched supply.

## Proposed connections (pending verification by meter)

| Board port | OBD pins | Bus |
|------------|----------|-----|
| Board 1, A | 13 (H), 12 (L), SGND to 5 | EV-CAN |
| Board 1, B | 6 (H), 14 (L), SGND to 5 | CAR-CAN |
| Board 2, A | 11 (H), 3 (L), SGND to 5 | AV-CAN (unconfirmed pins) |
| Board 2, B | unused | spare |

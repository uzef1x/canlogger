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
| 8 | +12 V only when vehicle powered on | leaf-obd manual (forum-derived); **measured by the owner on 4 cars on 2026-10-18** (ZE0, AZE0, ZE1, e-NV200): 0 V with ignition off and while charging with the car off, about 12 V with ignition on | **high** (measured) |
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
- **DIP switch (corrected after reading the schematic and photographing the board):** the
  Battery Emulator page says to "enable DIP switch 2" for CAN-FD, but it is third-party and
  its reason is unstated. In the LilyGo V1.0 schematic the two switches only connect ESP32
  IO3 to (1) the MCP2518FD's INT1 pin and (2) the expansion-header IO3 net. Neither is on the
  SPI or INT# path the logger uses, so the switch positions should not affect logging.
  Not yet proven on a live bus.
- **Hardware revision:** the underside silkscreen reads `T_2CAN_FD`, `2026-3-24`, `V1.0`, so
  it carries the same version label as the schematic. But the CAN modules on the board are
  **TD501MCANFD** (own photos, date codes 2619/2622/2623) while the schematic draws
  `TD501MCAN`. Treat the schematic as a close guide, not exact.
- **Terminal labels (own underside photo, printed next to the solder pads):**
  each block is `DGND · CANH · CANL · SGND`, port B and port A as separate blocks.
  Seen from the component side with the screw terminals at the top edge and USB-C at
  bottom-left, I worked out (from the flipped photo geometry, not from a printed marking on
  the top side) that the 8 screws read left to right:
  `DGNDB CANHB CANLB SGNDB | DGNDA CANHA CANLA SGNDA`,
  i.e. the left block is port B (classic) and the right block is port A (CAN-FD).
  **Confirm by eye on the underside before wiring.**
- **Termination confirmed on the real board:** a `121` (120 ohm) resistor is visible beside
  the common-mode choke of both CAN ports. Fixed, no switch.
- Power input range: not stated by LilyGo's wiki. The same page recommends 12 V instead of 5 V
  if CAN errors appear.

## Consequences (my reasoning, untested)

- A tap should not add a third 120 ohm. Remove RZ1/RZ2 on any port used as a tap.
- Wire SGND (not DGND) to OBD pin 5.
- Power the boards from pin 8 (switched). Measured on 4 cars (2026-10-18): pin 8 is 0 V with
  ignition off and while charging, so the boards run only with ignition on and log nothing
  while the car charges with the ignition off.

## Wiring table (verify with a meter first, see confidence levels above)

Screw order on each board, left to right, component side up, screw terminals at the top edge,
USB-C bottom-left (deduced from photos, confirm on the underside):
`DGNDB CANHB CANLB SGNDB | DGNDA CANHA CANLA SGNDA`. Leave both `DGND` screws unconnected.

### Board 1 (`env:board1`)
| Board screw | OBD-II pin | Signal | Bus |
|-------------|-----------|--------|-----|
| `CANHA` | 13 | EV-CAN high | EV-CAN (port A, CAN-FD capable) |
| `CANLA` | 12 | EV-CAN low | EV-CAN |
| `SGNDA` | 5 | signal ground | EV-CAN |
| `CANHB` | 6 | CAR-CAN high | CAR-CAN (port B, classic) |
| `CANLB` | 14 | CAR-CAN low | CAR-CAN |
| `SGNDB` | 5 | signal ground | CAR-CAN |
| `12-24V` terminal + | 8 (or 16 via a switch) | +12 V | power |
| `GND` terminal | 4 | chassis ground | power |

### Board 2 (`env:board2`)
| Board screw | OBD-II pin | Signal | Bus |
|-------------|-----------|--------|-----|
| `CANHA` | 11 | AV-CAN high | AV-CAN (port A, CAN-FD capable) |
| `CANLA` | 3 | AV-CAN low | AV-CAN (single-source pins) |
| `SGNDA` | 5 | signal ground | AV-CAN |
| `CANHB`, `CANLB`, `SGNDB` | none | | port B unused |
| `12-24V` terminal + | 8 (or 16 via a switch) | +12 V | power |
| `GND` terminal | 4 | chassis ground | power |

Termination: remove the 120 ohm (`121`) resistor beside the filter choke on each port used as
a tap. Untested on the car.

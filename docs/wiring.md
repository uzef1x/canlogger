# Wiring: T-2CANFD boards to the LEAF OBD-II connector

Every fact below is tagged with where it came from. Nothing is from the CANBRIDGE project.

## OBD-II connector (LEAF, Type A 16-pin)

Source: [Nissan Leaf OBD-II manual, "Diagnostic connector"](https://leaf-obd.readthedocs.io/en/latest/diagnostic-connector.html)
(rst source: [sethfischer/nissan-leaf-obd-manual](https://github.com/sethfischer/nissan-leaf-obd-manual/blob/main/source/diagnostic-connector.rst)).
Pins 4, 5, 6, 14, 16 are ISO 15031-3 mandated. Pins 3, 8, 11, 12, 13 are Nissan-specific
and the manual cites a community thread (mynissanleaf.com CAN decoding), **not** a Nissan
document. It does not say which model years it covers. Confirm by meter or the Nissan
service manual (LAN section) before relying on it.

| Pin | Signal |
|-----|--------|
| 3 / 11 | AV-CAN low / high |
| 4 | chassis ground |
| 5 | signal ground |
| 6 / 14 | CAR-CAN high / low |
| 8 | +12 V only when the vehicle is powered on |
| 12 / 13 | EV-CAN low / high |
| 16 | permanent +12 V |

All three buses are ISO 15765-4 CAN, 11-bit IDs, 500 kbit/s.
OVMS documents the ZE1 differently: its OBD port is isolated by a gateway that powers
down with ignition off ([OVMS docs](https://docs.openvehicles.com/en/latest/components/vehicle_nissanleaf/docs/index.html)).
ZE0 is what the pinout above is for.

## Board (T-2CANFD V1.0)

Source: LilyGo schematic `T-2Can-Fd_V1.0.pdf`, [Xinyuan-LilyGO/T-2Can](https://github.com/Xinyuan-LilyGO/T-2Can),
read directly.

- Each port: isolated transceiver (TD501MCAN), common-mode choke, TVS clamps.
- **A fixed 120 ohm resistor sits across CANH-CANL on both ports** (RZ2 on port A, RZ1 on port B).
  There is no jumper or switch in the schematic.
- The transceiver's isolated ground pin (CANG) is wired to the SGND net. DGND is the
  TVS/ESD return and is tied to SGND only through 1 Mohm in parallel with 1 nF.

## Consequences (my reasoning from the above, not from a source)

- A passive tap on a live bus should not add a third 120 ohm termination. Remove RZ1 and RZ2
  on any port used as a tap, or accept a degraded bus. Not yet tested on the car.
- Wire SGND (not DGND) to OBD pin 5. DGND can stay unconnected.
- Power the boards from OBD pin 8 (switched), not pin 16, so a parked car is not drained.

## Proposed connections

| Board port | OBD pins | Bus |
|------------|----------|-----|
| Board 1, A | 13 (H), 12 (L), SGND to 5 | EV-CAN |
| Board 1, B | 6 (H), 14 (L), SGND to 5 | CAR-CAN |
| Board 2, A | 11 (H), 3 (L), SGND to 5 | AV-CAN |
| Board 2, B | unused | spare |
| Both boards, VIN/GND | 8 and 4 | power |

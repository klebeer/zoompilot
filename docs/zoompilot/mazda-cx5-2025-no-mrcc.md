# Mazda CX-5 without MRCC: CRZ_CTRL liveness

Measurement record for a CX-5 2025 AWD sold outside North America (VIN WMI `JM7`, chassis code
`KF`, model year code `S`) fitted with conventional cruise control instead of MRCC. The car
fingerprints correctly and the EPS reports the steer-to-zero firmware, but openpilot never
leaves dashcam mode.

## Symptom

The device shows "unable to identify your car" on the first drives, and after the platform is
selected manually it shows "Unknown Vehicle Variant" a few seconds into calibration. That
string is the alert text for `EventName.canError`, raised from `selfdrived.py` when
`CS.canValid` goes false.

## What the car actually reports

Fingerprint resolves with the platform forced through the vehicle selector:

```
carFingerprint:     MAZDA_CX5_2022
fingerprintSource:  fixed
dashcamOnly:        False
safetyConfigs:      [('mazda', 2)]        # GEN1 | STEER_TO_ZERO_EPS
```

CAN is healthy. One 28.8 s segment, bus 0: 128 unique addresses, 57 400 frames received by the
panda, no bus-off, no error-passive, `canTimeout` never set.

## CRZ_CTRL stops a fixed ~19 s after ignition

Per-address liveness on bus 0, same segment:

| address | message      | span   | segment | max gap |
|---------|--------------|--------|---------|---------|
| `0x21C` | CRZ_CTRL     | 18.7 s | 28.8 s  | 54 ms   |
| `0x165` | PEDALS       | 28.8 s | 28.8 s  | 47 ms   |
| `0x240` | STEER_TORQUE | 28.8 s | 28.8 s  | 57 ms   |
| `0x09D` | CRZ_BTNS     | 28.7 s | 28.8 s  | 126 ms  |

While CRZ_CTRL is present it runs at ~47 Hz with no gap over 54 ms, so it is not degraded: the
module simply stops. The span is the same across every route recorded on the car.

| segment | duration | CRZ_CTRL span | coverage | canValid | cruiseState.available |
|---------|----------|---------------|----------|----------|-----------------------|
| 08      | 23.5 s   | 19.3 s        | 82.1 %   | 61.1 %   | 0.0 %                 |
| 09      | 22.4 s   | 20.5 s        | 91.6 %   | 76.0 %   | 0.0 %                 |
| 0a      | 24.2 s   | 18.9 s        | 77.8 %   | 55.6 %   | 0.0 %                 |
| 0b      | 18.2 s   | 18.2 s        | 99.9 %   | 98.0 %   | 0.0 %                 |
| 0c      | 41.4 s   | 18.9 s        | 45.6 %   | 21.6 %   | 0.0 %                 |
| 0d      | 28.8 s   | 18.7 s        | 65.1 %   | 36.4 %   | 0.0 %                 |

`canValid` tracks the coverage of that single message. `CRZ_AVAILABLE` never reads 1 in any
sample, which is consistent with a car that has no adaptive cruise: the signal reports
adaptive-cruise availability, not the main switch.

## Why the silence invalidates the whole bus

`get_can_parsers` leaves `pt_messages` empty unless `openpilotLongitudinalControl` is set, so
CRZ_CTRL is never registered explicitly. It enters the parser through the read at the bottom of
`update`:

```python
ret.cruiseState.available = cp.vl["CRZ_CTRL"]["CRZ_AVAILABLE"] == 1
```

`VLDict.__getitem__` registers a missing message on first access with no declared frequency.
`_add_message` then assumes 1 Hz, giving `timeout_threshold` of 10 s and leaving `ignore_alive`
false. Ten seconds after the module goes quiet the message fails `MessageState.valid`,
`can_valid` goes false for the powertrain parser, and `ret.canValid` follows.

Nothing declared CRZ_CTRL mandatory; it became mandatory because it is read.

## The change

Register it the way the camera messages already are, with a nan frequency so `ignore_alive` is
set and its silence no longer invalidates the bus. Cars with MRCC keep publishing it, so their
behaviour is unchanged; the read still returns the last decoded sample.

## What this does not solve

The panda enforces the same dependency independently. `mazda_rx_checks` requires CRZ_CTRL at
50 Hz, and only `mazda_long_rx_checks`, selected by `mazda_longitudinal`, drops it:

```c
return mazda_longitudinal ? BUILD_SAFETY_CFG(mazda_long_rx_checks, MAZDA_LONG_TX_MSGS) :
                            BUILD_SAFETY_CFG(mazda_rx_checks, MAZDA_TX_MSGS);
```

`MAZDA_PARAM_SP_TJA_BUTTON` does not help here: it moves where `acc_main_on` comes from, it
does not change the checks. So on this car the software-side fix alone still ends in the panda
refusing steering frames, and a lateral-only configuration would need a third safety config
whose checks exclude CRZ_CTRL without enabling longitudinal.

The signals such a configuration would need are present and stable on this car:

- `PEDALS.ACC_OFF` reads 1 for 100 % of samples on routes where the cruise main switch is
  armed, and 0 % on a route where it is not, which is the same source the longitudinal path
  already uses for `cruise_available`.
- `CRZ_BTNS.MODE_X` and `MODE_Y` carry the main button, and `CRZ_BTNS` covers 99.6 % of the
  segment.
- `CRZ_BTNS.TJA_BUTTON` reads 0 throughout: this trim has no TJA button.

## Open question

Why the module stops at a fixed ~19 s is not understood. Until it is, no safety-side change
should be built on the assumption that the timing is stable.

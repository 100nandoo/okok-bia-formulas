# OKOK Scale Reverse Engineering

This repository documents my attempt to reverse engineer the OKOK Bluetooth LE scale. The goal is to understand the data protocol and how the scale calculates various body metrics.

## Bluetooth LE Information

The scale uses Bluetooth Low Energy (BLE) for communication.

## Key Code and Calculations

The core logic for calculating body metrics appears to be within a class or module named `CsAlgoBuilder`. The following methods are used to calculate specific metrics:

*   `getBFR()`: Body Fat Rate
*   `getVFR()`: Visceral Fat Rate
*   `getTFR()`: Total Fat Rate
*   `getMSW()`: Mineral Salt of Weight (Bone)
*   `getSLMPercent(0.0f)`: Skeletal Lean Mass Percentage (Muscle)
*   `getBMR()`: Basal Metabolic Rate
*   `getBodyAge()`: Body Age
*   `getScore()`: Overall Health Score
*   `getBW()`: Body Water

For a detailed breakdown of the formulas used in these methods, please see [Formula.md](Formula.md).

## Hex Payload Breakdown

The manufacturer code for OKOK scales is either `-64 (0xC0)` or `-54 (0xCA)`.

### Example Payload:

```
10 ff c0 07 1a31 1770 0a01 25 2416514b123c
```

### Payload Structure:

| Field          | Value        | Description                                       |
| -------------- | ------------ | ------------------------------------------------- |
| Data Length    | `10`         | Length of the data payload.                       |
| Manufacturer   | `ff`         | Manufacturer identifier.                          |
| Man. Code      | `c0`         | Manufacturer code.                                |
| Measure SQ Num | `07`         | Measurement sequence number (e.g., `07` is weight). |
| Weight         | `1a31`       | Weight in kilograms (e.g., `6705` -> `67.05 kg`).   |
| Impedance      | `1770`       | Impedance value (e.g., `6000` -> `600Ω`).         |
| Product ID     | `0a01`       | Product identifier.                               |
| Scale Property | `25`         | See below for details.                            |
| MAC Address    | `2416514b123c`| MAC address of the device.                        |

### Scale Property (`0x25`)

The `Scale Property` field is a bitmask that provides information about the measurement.

**Binary Representation:** `00100101`

| Bit Index | Value | Description                                       |
| --------- | ----- | ------------------------------------------------- |
| 3-4       | `00`  | Weight unit (`00`: kg, `01`: jin, `10`: lb, `11`: stone). |
| 5-6       | `10`  | Decimal precision (`01`: zero, `10`: two, `00`/`11`: one). |
| 7         | `1`   | Measurement status (`1`: stable, `0`: unstable).  |

## Contributing

Contributions are welcome! If you have any information to add or corrections to make, please open an issue or submit a pull request.

## License

This project is licensed under the terms of the LICENSE file.
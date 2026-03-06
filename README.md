# OKOK Scale Reverse Engineering

This repository documents my attempt to reverse engineer the OKOK Bluetooth LE scale. The goal is to understand the data protocol and how the scale calculates various body metrics.

## Bluetooth LE Information

The scale uses Bluetooth Low Energy (BLE) for communication.

## Key Code and Calculations

The core logic for calculating body metrics appears to be within a class or module named `CsAlgoBuilder`. The following methods are used to calculate specific metrics:

- `getBFR()`: Body Fat Rate
- `getVFR()`: Visceral Fat Rate
- `getTFR()`: Total Fat Rate
- `getMSW()`: Mineral Salt of Weight (Bone)
- `getSLMPercent(0.0f)`: Skeletal Lean Mass Percentage (Muscle)
- `getBMR()`: Basal Metabolic Rate
- `getBodyAge()`: Body Age
- `getScore()`: Overall Health Score
- `getBW()`: Body Water

For a detailed breakdown of the formulas used in these methods, please see [Formula.md](Formula.md).

## Python Reader Script (`okok_ble_reader.py`)

A Python script is provided to read BLE advertisement data from the scale and calculate metrics in real-time. It supports V20, V11, VF0, and C0 scale variants.

### Prerequisites

- [uv](https://github.com/astral-sh/uv) (recommended) or Python 3.11+ with `bleak`
- Bluetooth enabled on your machine

### Running the Reader

The easiest way to run the script is using `uv`, which handles dependencies automatically:

```bash
# Scan and print readings to console
uv run okok_ble_reader.py

# Scan and save a stable reading to a CSV file (exits after saving)
uv run okok_ble_reader.py --csv readings.csv
```

### Body Composition (BIA)

To enable calculation of body fat, muscle mass, and other metrics, provide your user profile:

```bash
uv run okok_ble_reader.py \
  --height 175 \
  --age 30 \
  --sex male \
  --csv my_readings.csv
```

The script will automatically use the impedance value provided by the scale to calculate BIA metrics (available on V20 and C0 variants).

## Hex Payload Breakdown

The manufacturer code for OKOK scales is either `-64 (0xC0)` or `-54 (0xCA)`.

### Example Payload:

```
10 ff c0 07 1a31 1770 0a01 25 2416514b123c
```

### Payload Structure:

| Field          | Value          | Description                                         |
| -------------- | -------------- | --------------------------------------------------- |
| Data Length    | `10`           | Length of the data payload.                         |
| Manufacturer   | `ff`           | Manufacturer identifier.                            |
| Man. Code      | `c0`           | Manufacturer code.                                  |
| Measure SQ Num | `07`           | Measurement sequence number (e.g., `07` is weight). |
| Weight         | `1a31`         | Weight in kilograms (e.g., `6705` -> `67.05 kg`).   |
| Impedance      | `1770`         | Impedance value (e.g., `6000` -> `600Ω`).           |
| Product ID     | `0a01`         | Product identifier.                                 |
| Scale Property | `25`           | See below for details.                              |
| MAC Address    | `2416514b123c` | MAC address of the device.                          |

### Scale Property (`0x25`)

The `Scale Property` field is a bitmask that provides information about the measurement.

**Binary Representation:** `00100101`

| Bit Index | Value | Description                                                |
| --------- | ----- | ---------------------------------------------------------- |
| 3-4       | `00`  | Weight unit (`00`: kg, `01`: jin, `10`: lb, `11`: stone).  |
| 5-6       | `10`  | Decimal precision (`01`: zero, `10`: two, `00`/`11`: one). |
| 7         | `1`   | Measurement status (`1`: stable, `0`: unstable).           |

## Contributing

Contributions are welcome! If you have any information to add or corrections to make, please open an issue or submit a pull request.

## License

This project is licensed under the terms of the LICENSE file.

## macOS Shortcut Integration

You can trigger a weight scan directly from a **macOS Shortcut** or via your menu bar.

1.  **Modify the wrapper script**: Open `run_shortcut.sh` and update your `HEIGHT`, `AGE`, and `SEX`.
2.  **Open "Shortcuts" App** on your Mac.
3.  **Create a New Shortcut**:
    - Click `+` (New Shortcut).
    - Name it something like "Log Weight".
    - Search for the action: **"Run Shell Script"**.
    - In the text box, paste the following:
      ```bash
      ~/okok-bia-formulas/run_shortcut.sh
      ```
    - (Optional) Check "Show in Menu Bar" in the Shortcut settings (right sidebar).
4.  **How to use**:
    - Trigger the shortcut (from menu bar or a keyboard shortcut).
    - Step on your scale.
    - A notification will appear once the data is captured and saved.

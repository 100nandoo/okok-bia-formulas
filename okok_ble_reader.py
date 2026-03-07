"""
okok_ble_reader.py
==================
Read BLE advertisement data from an OKOK scale and decode weight + body
composition metrics.

Supports all four variants described in OkOkHandler.kt:
  - OKOK V20  (manufacturer id 0x20CA) – stable flag + XOR checksum
  - OKOK V11  (manufacturer id 0x11CA) – XOR checksum, unit & resolution in body props
  - OKOK VF0  (manufacturer id 0xF0FF) – simple weight field
  - OKOK C0   (any manufacturer id where low byte == 0xC0) – MAC-embedded, attrib

Link mode: BROADCAST_ONLY (passive BLE scan, no GATT connection required).

Usage
-----
    uv run okok_ble_reader.py                    # scan forever, print every reading
    uv run okok_ble_reader.py --csv weight.csv   # also append rows to a CSV file

User profile for BIA (edit these or pass via CLI):
    --height 170 --weight-ref 70 --age 30 --sex male --impedance 0
    (impedance is filled automatically from V20 packets when available)

Requirements: bleak
"""

# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "bleak",
# ]
# ///

from __future__ import annotations

import argparse
import asyncio
import csv
import dataclasses
import sys
from datetime import datetime
from typing import Optional

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# ---------------------------------------------------------------------------
# Manufacturer IDs
# ---------------------------------------------------------------------------
MANUF_V20 = 0x20CA
MANUF_V11 = 0x11CA
MANUF_VF0 = 0xF0FF

# Unit constants (shared by V11 and C0)
UNIT_KG   = 0
UNIT_JIN  = 1
UNIT_LB   = 2
UNIT_STLB = 3

# ---------------------------------------------------------------------------
# Known device names (mirrors OkOkHandler.supportFor)
# ---------------------------------------------------------------------------
KNOWN_NAMES = {"ADV", "Chipsea-BLE", "OKOK Nameless"}

def is_okok_device(name: str) -> bool:
    lname = name.lower()
    return (
        name in KNOWN_NAMES
        or lname.startswith("yoda0")
        or lname.startswith("yoda1")
    )

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def u16be(msb: int, lsb: int) -> int:
    """Combine two bytes into an unsigned 16-bit big-endian integer."""
    return ((msb & 0xFF) << 8) | (lsb & 0xFF)


def _resolve_divider(res_bits: int) -> float:
    return {0: 10.0, 1: 1.0, 2: 100.0}.get(res_bits, 10.0)


def _to_kg_from_unit(raw: int, unit: int, divider: float) -> Optional[float]:
    """Convert raw integer + unit code to kg."""
    if unit == UNIT_KG:
        return raw / divider
    if unit == UNIT_JIN:
        return raw / divider / 2.0
    if unit == UNIT_LB:
        return (raw / divider) / 2.204623
    if unit == UNIT_STLB:
        stones = raw >> 8
        pounds = (raw & 0xFF) / divider
        return stones * 6.350293 + pounds * 0.453592
    return None

# ---------------------------------------------------------------------------
# Per-variant parsers (return (weight_kg, impedance_ohm or None))
# ---------------------------------------------------------------------------

def parse_v20(data: bytes) -> Optional[tuple[float, Optional[float]]]:
    """
    OKOK V20  – manufacturer id 0x20CA
    19 bytes, stable flag at data[6] bit0, XOR checksum at data[12].
    Impedance at data[10:12] in tenths of ohms.
    """
    if len(data) != 19:
        return None

    final_flag = data[6] & 0x01
    if not final_flag:
        return None

    # XOR checksum: seed 0x20 (implicit version byte) xor all bytes 0..11
    checksum = 0x20
    for i in range(12):
        checksum ^= data[i] & 0xFF
    if (checksum & 0xFF) != (data[12] & 0xFF):
        return None

    divider = 100.0 if (data[6] & 0x04) else 10.0
    weight_kg = u16be(data[8], data[9]) / divider

    impedance_raw = u16be(data[10], data[11])
    impedance = impedance_raw / 10.0 if impedance_raw > 0 else None

    return weight_kg, impedance


def parse_v11(data: bytes) -> Optional[tuple[float, None]]:
    """
    OKOK V11  – manufacturer id 0x11CA
    23 bytes, XOR checksum at data[16] (seed 0xCA ^ 0x11 = 0xDB).
    Unit & resolution encoded in body_properties byte at data[9].
    """
    expected_len = 16 + 6 + 1  # IDX_V11_CHECKSUM + 6 + 1 = 23
    if len(data) != expected_len:
        return None

    checksum = 0xCA ^ 0x11  # == 0xDB
    for i in range(16):
        checksum ^= data[i] & 0xFF
    if (checksum & 0xFF) != (data[16] & 0xFF):
        return None

    props = data[9] & 0xFF
    res_bits = (props >> 1) & 0x3
    unit     = (props >> 3) & 0x3
    divider  = _resolve_divider(res_bits)

    raw = u16be(data[3], data[4])
    kg = _to_kg_from_unit(raw, unit, divider)
    if kg is None:
        return None
    return kg, None


def parse_vf0(data: bytes) -> Optional[tuple[float, None]]:
    """
    OKOK VF0  – manufacturer id 0xF0FF
    Simple weight at data[3] (MSB) / data[2] (LSB), tenths of kg.
    """
    if len(data) < 4:
        return None
    raw = u16be(data[3], data[2])
    return raw / 10.0, None


def parse_c0(manufacturer_data: dict[int, bytes]) -> Optional[tuple[float, Optional[float]]]:
    """
    OKOK C0  – any manufacturer id whose low byte == 0xC0.
    Based on parse_okok.main.kts and OkOkHandler.kt:
    - Weight at data[0:2].
    - Impedance at data[2:4].
    - Prop (attrib) at data[6].
    - Sequence number is high byte of Manufacturer ID key.
    """
    key = next((k for k in manufacturer_data if (k & 0xFF) == 0xC0), None)
    if key is None:
        return None
    data = manufacturer_data[key]
    if len(data) < 7:
        return None

    attrib    = data[6] & 0xFF
    # Use attrib & 0x01 for stability (works for current user scale)
    is_stable = attrib & 0x01
    if not is_stable:
        return None

    res_bits = (attrib >> 1) & 0x3
    unit     = (attrib >> 3) & 0x3
    divider  = _resolve_divider(res_bits)

    raw_weight = u16be(data[0], data[1])
    kg = _to_kg_from_unit(raw_weight, unit, divider)
    if kg is None:
        return None

    # Impedance: If bytes 2-3 are non-zero, use it as impedance.
    # We relax the seq_num check to capture BIA for more scale variants.
    imp_raw = u16be(data[2], data[3])
    impedance = None
    if imp_raw > 0:
        impedance = imp_raw / 10.0

    return kg, impedance

# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

def parse_okok(
    manufacturer_data: dict[int, bytes],
) -> Optional[tuple[str, float, Optional[float], str]]:
    """
    Try all parsers in priority order.
    Returns (variant_name, weight_kg, impedance_or_None, raw_hex) or None.
    """
    if MANUF_V20 in manufacturer_data:
        data = manufacturer_data[MANUF_V20]
        result = parse_v20(data)
        if result:
            return ("V20", result[0], result[1], data.hex().upper())

    if MANUF_V11 in manufacturer_data:
        data = manufacturer_data[MANUF_V11]
        result = parse_v11(data)
        if result:
            return ("V11", result[0], result[1], data.hex().upper())

    if MANUF_VF0 in manufacturer_data:
        data = manufacturer_data[MANUF_VF0]
        result = parse_vf0(data)
        if result:
            return ("VF0", result[0], result[1], data.hex().upper())

    # C0 variant: find key manually to get raw bytes for the hex return
    key = next((k for k in manufacturer_data if (k & 0xFF) == 0xC0), None)
    if key is not None:
        result = parse_c0(manufacturer_data)
        if result:
            return ("C0", result[0], result[1], manufacturer_data[key].hex().upper())

    return None

# ---------------------------------------------------------------------------
# BIA body composition formulas (from Formula.md / CsAlgoBuilder.java)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class UserProfile:
    height: float   # cm
    age:    int     # years
    is_male: bool

@dataclasses.dataclass
class BodyComposition:
    weight_kg:   float
    impedance:   float
    bmi:         float  # Body Mass Index

    bfr:         float  # Body Fat Rate %
    vfr:         Optional[float]  # Visceral Fat Rating
    tfr:         Optional[float]  # Total body water %
    muscle_pct:  float  # Skeletal muscle %
    muscle_kg:   float  # Skeletal muscle in kg
    bone_mass:   float  # kg
    bmr:         float  # kcal/day
    body_age:    float  # years
    ideal_weight: float # kg
    protein_kg:  float # kg
    protein_pct: float # %
    fat_mass_kg: float # kg


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _round_half(v: float) -> float:
    """Round to nearest 0.5."""
    return round(v * 2) / 2


def calc_bia(profile: UserProfile, weight_kg: float, impedance: float) -> BodyComposition:
    H   = profile.height
    Wt  = weight_kg
    Age = profile.age
    Z   = impedance

    # 1. BMI
    bmi = Wt / ((H / 100.0) ** 2)

    # 2. Body Fat Rate
    if profile.is_male:
        raw_bfr = ((-0.3315*H) + (0.6216*Wt) + (0.0183*Age) + (0.0085*Z) + 22.554) / Wt * 100
    else:
        raw_bfr = ((-0.3332*H) + (0.7509*Wt) + (0.0196*Age) + (0.0072*Z) + 22.7193) / Wt * 100
    bfr = _clamp(raw_bfr, 5.0, 45.0)

    # 3. Visceral Fat Rate (only applies to age > 17 in some formulas)
    if profile.is_male:
        raw_vfr = (-0.2675*H) + (0.42*Wt) + (0.1462*Age) + (0.0123*Z) + 13.9871
    else:
        raw_vfr = (-0.1651*H) + (0.2628*Wt) + (0.0649*Age) + (0.0024*Z) + 12.3445
    vfr = _clamp(_round_half(raw_vfr), 1.0, 59.0)

    # 4. Total body water %
    if profile.is_male:
        raw_tfr = (((0.0939*H) + (0.3758*Wt) - (0.0032*Age) - (0.006925*Z) + 0.097) / Wt) * 100
    else:
        raw_tfr = (((0.0877*H) + (0.2973*Wt) + (0.0128*Age) - (0.00603*Z) + 0.5175) / Wt) * 100
    tfr = _clamp(raw_tfr, 20.0, 85.0)

    # 5. Skeletal Lean Mass (raw), used for muscle % and bone mass
    if profile.is_male:
        slm_raw = (0.2867*H) + (0.3894*Wt) - (0.0408*Age) - (0.01235*Z) - 15.7665
    else:
        slm_raw = (0.3186*H) + (0.1934*Wt) - (0.0206*Age) - (0.0132*Z) - 16.4556
    muscle_pct = (slm_raw / Wt) * 100

    # 6. Bone mass
    fat_mass = (bfr * Wt) / 100.0
    bone_mass = _clamp(Wt - fat_mass - slm_raw, 1.0, 4.0)

    # 7. BMR
    if profile.is_male:
        bmr = (7.5037*H) + (13.1523*Wt) - (4.3376*Age) - (0.3486*Z) - 311.7751
    else:
        bmr = (7.5432*H) + (9.9474*Wt) - (3.4382*Age) - (0.309*Z) - 288.2821

    # 8. Body Age
    if profile.is_male:
        ba = (-0.7471*H) + (0.9161*Wt) + (0.4184*Age) + (0.0517*Z) + 54.2267
    else:
        ba = (-1.1165*H) + (1.5784*Wt) + (0.4615*Age) + (0.0415*Z) + 83.2548
    body_age = _clamp(ba, max(18.0, Age - 10.0), min(80.0, Age + 10.0))

    # 9. Ideal body weight
    if profile.is_male:
        ideal_weight = (H - 80.0) * 0.7
    else:
        ideal_weight = (H - 70.0) * 0.6

    # 10. Protein
    water_mass = (tfr * Wt) / 100.0
    protein_kg = slm_raw - water_mass
    protein_pct = (protein_kg / Wt) * 100.0

    return BodyComposition(
        weight_kg=round(Wt, 2),
        impedance=round(Z, 1),
        bmi=round(bmi, 1),
        bfr=round(bfr, 1),
        vfr=vfr if Age > 17 else None,
        tfr=tfr if Age > 17 else None,
        muscle_pct=round(muscle_pct, 1),
        muscle_kg=round(slm_raw, 2),
        bone_mass=round(bone_mass, 2),
        bmr=round(bmr, 0),
        body_age=round(body_age, 0),
        ideal_weight=round(ideal_weight, 1),
        protein_kg=round(protein_kg, 2),
        protein_pct=round(protein_pct, 1),
        fat_mass_kg=round(fat_mass, 2),
    )

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

CSV_HEADER = [
    "timestamp", "device", "variant",
    "weight_kg", "impedance_ohm", "raw_payload",
    "bmi", "bfr_%", "vfr", "tfr_%", "muscle_kg", "muscle_%", "bone_kg", "bmr_kcal",
    "body_age", "ideal_weight_kg", "protein_kg", "protein_%", "fat_mass_kg",
]


def _bia_row(bc: BodyComposition):
    return [
        bc.bmi, bc.bfr, bc.vfr if bc.vfr is not None else "", 
        bc.tfr if bc.tfr is not None else "",
        bc.muscle_kg, bc.muscle_pct,
        bc.bone_mass, bc.bmr, bc.body_age, bc.ideal_weight,
        bc.protein_kg, bc.protein_pct,
        bc.fat_mass_kg,
    ]


def print_reading(
    device: BLEDevice,
    variant: str,
    weight_kg: float,
    impedance: Optional[float],
    bc: Optional[BodyComposition],
    output_format: str = "default",
    profile: Optional[UserProfile] = None,
) -> None:
    if output_format == "mac":
        print("<b>Profile</b>")
        if profile:
            print(f"{'height':<10}: {profile.height:.0f} cm")
            print(f"{'age':<10}: {profile.age}")
            print(f"{'sex':<10}: {'male' if profile.is_male else 'female'}")
        else:
            print("No profile provided")

        print("\n<b>Details</b>")
        imp = f"{impedance:.1f} Ω" if impedance is not None else "—"
        print(f"{'Weight':<10}: {weight_kg:.2f} kg")
        print(f"{'Impedance':<10}: {imp}")
        if bc:
            vfr_str = f"{bc.vfr:.1f}" if bc.vfr is not None else "—"
            tfr_str = f"{bc.tfr:.1f}%" if bc.tfr is not None else "—"
            print(f"{'BMI':<10}: {bc.bmi:.1f}")
            print(f"{'Body Fat':<10}: {bc.bfr:.1f}% ({bc.fat_mass_kg:.2f} kg)")
            print(f"{'Visc Fat':<10}: {vfr_str}")
            print(f"{'Water':<10}: {tfr_str}")
            print(f"{'Muscle':<10}: {bc.muscle_kg:.2f} kg ({bc.muscle_pct:.1f}%)")
            print(f"{'Bone Mass':<10}: {bc.bone_mass:.2f} kg")
            print(f"{'BMR':<10}: {bc.bmr:.0f} kcal")
            print(f"{'Protein':<10}: {bc.protein_kg:.2f} kg ({bc.protein_pct:.1f}%)")
            print(f"{'Body Age':<10}: {bc.body_age:.0f}")
        return

    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    imp  = f"{impedance:.1f} Ω" if impedance is not None else "—"
    print(f"\n{'─'*55}")
    print(f"  {ts}  |  {device.name or device.address}  |  {variant}")
    print(f"  Weight   : {weight_kg:.2f} kg")
    print(f"  Impedance: {imp}")
    if bc:
        vfr_str = f"{bc.vfr:.1f}" if bc.vfr is not None else "—"
        tfr_str = f"{bc.tfr:.1f}%" if bc.tfr is not None else "—"
        print(f"  BMI      : {bc.bmi:.1f}      Body Fat : {bc.bfr:.1f}% ({bc.fat_mass_kg:.2f} kg)")
        print(f"  Visc Fat : {vfr_str}      Water    : {tfr_str}")
        print(f"  Muscle   : {bc.muscle_kg:.2f} kg ({bc.muscle_pct:.1f}%)")
        print(f"  Protein  : {bc.protein_kg:.2f} kg ({bc.protein_pct:.1f}%)")
        print(f"  Bone Mass: {bc.bone_mass:.2f} kg   BMR: {bc.bmr:.0f} kcal")
        print(f"  Body Age : {bc.body_age:.0f}   Ideal Weight: {bc.ideal_weight:.1f} kg")
    print(f"{'─'*55}")


def append_csv(
    path: str,
    device: BLEDevice,
    variant: str,
    weight_kg: float,
    impedance: Optional[float],
    raw_hex: str,
    bc: Optional[BodyComposition],
) -> None:
    ts = datetime.now().isoformat()
    row = [ts, device.name or device.address, variant, weight_kg, impedance or "", raw_hex]
    row += _bia_row(bc) if bc else [""] * 8

    write_header = False
    try:
        with open(path, "r") as f:
            write_header = f.read(1) == ""
    except FileNotFoundError:
        write_header = True

    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(CSV_HEADER)
        writer.writerow(row)

# ---------------------------------------------------------------------------
# Main BLE scan loop
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> None:
    profile: Optional[UserProfile] = None
    if args.height and args.age and args.sex:
        profile = UserProfile(
            height=args.height,
            age=args.age,
            is_male=(args.sex.lower() in {"male", "m", "1"}),
        )
        if args.output != "mac":
            print(f"User profile → height={args.height} cm, age={args.age}, "
                  f"sex={'male' if profile.is_male else 'female'}")
    elif args.output != "mac":
        print("No user profile provided — BIA body composition will be skipped.")
        print("Pass --height <cm> --age <years> --sex male|female to enable it.")

    if args.output != "mac":
        print("\nScanning for OKOK BLE scale advertisements…\n")
    seen: set[str] = set()
    stop_event = asyncio.Event()

    def callback(device: BLEDevice, adv: AdvertisementData) -> None:
        name = device.name or ""

        # Accept known names, or any anonymous device that looks like C0 variant
        low_byte_c0 = any((k & 0xFF) == 0xC0 for k in adv.manufacturer_data)
        if not is_okok_device(name) and not low_byte_c0:
            return

        result = parse_okok(adv.manufacturer_data)
        if result is None:
            return

        variant, weight_kg, impedance, raw_hex = result

        # Only process each stable reading once per second (simple dedup)
        key = f"{device.address}:{weight_kg:.2f}"
        if key in seen:
            return
        seen.add(key)
        # Clear stale keys periodically (keep set small)
        if len(seen) > 500:
            seen.clear()

        bc: Optional[BodyComposition] = None
        if profile and impedance is not None:
            bc = calc_bia(profile, weight_kg, impedance)

        print_reading(device, variant, weight_kg, impedance, bc, output_format=args.output, profile=profile)

        if args.csv:
            append_csv(args.csv, device, variant, weight_kg, impedance, raw_hex, bc)
            if args.output != "mac":
                print(f"  ✓ saved to {args.csv}")
            stop_event.set()

    scanner = BleakScanner(callback)
    await scanner.start()
    try:
        await stop_event.wait()
    finally:
        await scanner.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read OKOK BLE scale advertisements and decode weight + BIA metrics."
    )
    parser.add_argument("--csv",    metavar="FILE", help="Append readings to a CSV file")
    parser.add_argument("--height", type=float, metavar="CM",   help="Your height in cm (for BIA)")
    parser.add_argument("--age",    type=int,   metavar="YEARS", help="Your age in years (for BIA)")
    parser.add_argument("--sex",    metavar="male|female",       help="Your biological sex (for BIA)")
    parser.add_argument("--output", choices=["default", "mac"], default="default", help="Output format for console")
    args = parser.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

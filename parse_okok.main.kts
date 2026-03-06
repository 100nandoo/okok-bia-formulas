#!/usr/bin/env kotlin

/**
 * OKOK BLE Scale Hex Payload Parser + BIA Calculator
 *
 * Parses the raw manufacturer data from OKOK Bluetooth LE scales and
 * optionally calculates body composition metrics (BFR, VFR) when user
 * profile is provided.
 *
 * Protocol layout (from README.md):
 * Byte 0:      Data Length
 * Byte 1:      0xFF (Manufacturer marker)
 * Byte 2:      Manufacturer Code (0xC0 or 0xCA)
 * Byte 3:      Measurement Sequence Number (0x07 = weight+impedance ready)
 * Bytes 4-5:   Weight (big-endian, unit = 0.01 kg)
 * Bytes 6-7:   Impedance (big-endian, unit = 0.1 Ω)
 * Bytes 8-9:   Product ID
 * Byte 10:     Scale Property (bitmask)
 * Bytes 11-16: MAC Address (6 bytes)
 *
 * Usage:
 *   kotlin parse_okok.main.kts <hex> [--height=<cm>] [--age=<years>] [--sex=<male|female>]
 *
 * Examples:
 *   kotlin parse_okok.main.kts 10ffc0071a3117700a01252416514b123c
 *   kotlin parse_okok.main.kts 10ffc0071a3117700a01252416514b123c --height=170 --age=30 --sex=male
 */

// ─── Helpers ─────────────────────────────────────────────────────────────────

fun String.hexToByteArray(): ByteArray {
    val hex = this.replace(" ", "").lowercase()
    require(hex.length % 2 == 0) { "Hex string must have an even number of characters" }
    return ByteArray(hex.length / 2) { i ->
        hex.substring(i * 2, i * 2 + 2).toInt(16).toByte()
    }
}

fun ByteArray.toHexString(separator: String = " "): String =
    joinToString(separator) { "%02x".format(it.toInt() and 0xFF) }

fun ByteArray.readU8(offset: Int): Int = this[offset].toInt() and 0xFF
fun ByteArray.readU16BE(offset: Int): Int =
    ((this[offset].toInt() and 0xFF) shl 8) or (this[offset + 1].toInt() and 0xFF)

fun Double.clamp(min: Double, max: Double): Double = maxOf(min, minOf(max, this))

/** Round to nearest 0.5 */
fun Double.roundHalf(): Double = Math.round(this * 2.0) / 2.0

// ─── Scale Property bitmask ──────────────────────────────────────────────────

enum class WeightUnit(val label: String) {
    KG("kg"), JIN("jin"), LB("lb"), STONE("stone")
}

enum class DecimalPrecision(val places: Int, val divisor: Int) {
    ONE_DECIMAL(1, 10),
    TWO_DECIMAL(2, 100),
    ZERO_DECIMAL(0, 1)
}

data class ScaleProperty(val raw: Int) {
    // Bits 3-4 → weight unit (0=kg, 1=jin, 2=lb, 3=stone)
    val weightUnit: WeightUnit = WeightUnit.entries[(raw shr 3) and 0x03]

    // Bits 5-6 → decimal precision
    // Verified against README example: 0x25 + raw=6705 → 67.05 kg (TWO_DECIMAL)
    val decimalPrecision: DecimalPrecision = when ((raw shr 5) and 0x03) {
        0b01 -> DecimalPrecision.TWO_DECIMAL
        0b10 -> DecimalPrecision.ZERO_DECIMAL
        else -> DecimalPrecision.ONE_DECIMAL
    }

    // Bit 7 (MSB) → stable (1 = stable, 0 = still measuring)
    val isStable: Boolean = (raw and 0x80) != 0

    fun weightFrom(raw16: Int): Double = raw16.toDouble() / decimalPrecision.divisor
}

// ─── Parsed payload ──────────────────────────────────────────────────────────

data class OkokPayload(
    val rawHex: String,
    val dataLength: Int,
    val manufacturerCode: Int,
    val measureSeqNum: Int,
    val weightRaw: Int,
    val impedanceRaw: Int,
    val productId: Int,
    val scaleProp: ScaleProperty,
    val mac: String,
    val remainingBytes: ByteArray,
) {
    val weight: Double get() = scaleProp.weightFrom(weightRaw)
    val impedance: Double get() = impedanceRaw / 10.0   // unit = 0.1 Ω
    val hasImpedance: Boolean get() = measureSeqNum in setOf(0x07, 0x6B) && impedanceRaw != 0
}

// ─── Parser ──────────────────────────────────────────────────────────────────

fun parsePayload(hexString: String): OkokPayload {
    val bytes = hexString.hexToByteArray()
    require(bytes.size >= 17) {
        "Payload too short: need at least 17 bytes, got ${bytes.size}"
    }

    val manufacturerCode = bytes.readU8(2)
    require(manufacturerCode == 0xC0 || manufacturerCode == 0xCA) {
        "Unexpected manufacturer code: 0x%02X (expected 0xC0 or 0xCA)".format(manufacturerCode)
    }

    val prop = ScaleProperty(bytes.readU8(10))
    val mac = bytes.slice(11..16).joinToString(":") { "%02X".format(it.toInt() and 0xFF) }
    val remaining = if (bytes.size > 17) bytes.copyOfRange(17, bytes.size) else ByteArray(0)

    return OkokPayload(
        rawHex           = hexString.lowercase().replace(" ", ""),
        dataLength       = bytes.readU8(0),
        manufacturerCode = manufacturerCode,
        measureSeqNum    = bytes.readU8(3),
        weightRaw        = bytes.readU16BE(4),
        impedanceRaw     = bytes.readU16BE(6),
        productId        = bytes.readU16BE(8),
        scaleProp        = prop,
        mac              = mac,
        remainingBytes   = remaining,
    )
}

// ─── BIA Calculations (from Formula.md) ──────────────────────────────────────

data class UserProfile(
    val heightCm: Double,   // H
    val agYears: Int,        // Age
    val isMale: Boolean,     // Sex: true=1, false=0
)

/**
 * Body Fat Rate (getBFR())
 * Result capped between 5.0% and 45.0%
 *
 * Male:   ((-0.3315*H) + (0.6216*Wt) + (0.0183*Age) + (0.0085*Z) + 22.554)  / Wt * 100
 * Female: ((-0.3332*H) + (0.7509*Wt) + (0.0196*Age) + (0.0072*Z) + 22.7193) / Wt * 100
 */
fun calcBFR(wt: Double, z: Double, profile: UserProfile): Double {
    val (H, Age, isMale) = Triple(profile.heightCm, profile.agYears.toDouble(), profile.isMale)
    val raw = if (isMale)
        ((-0.3315 * H) + (0.6216 * wt) + (0.0183 * Age) + (0.0085 * z) + 22.554)  / wt * 100
    else
        ((-0.3332 * H) + (0.7509 * wt) + (0.0196 * Age) + (0.0072 * z) + 22.7193) / wt * 100
    return raw.clamp(5.0, 45.0)
}

/**
 * Visceral Fat Rate (getVFR())
 * Only applies to users over 17.
 * Rounded to nearest 0.5, capped between 1.0 and 59.0
 *
 * Male:   (-0.2675*H) + (0.42*Wt)   + (0.1462*Age) + (0.0123*Z) + 13.9871
 * Female: (-0.1651*H) + (0.2628*Wt) + (0.0649*Age) + (0.0024*Z) + 12.3445
 */
fun calcVFR(wt: Double, z: Double, profile: UserProfile): Double? {
    if (profile.agYears <= 17) return null  // not applicable
    val (H, Age, isMale) = Triple(profile.heightCm, profile.agYears.toDouble(), profile.isMale)
    val raw = if (isMale)
        (-0.2675 * H) + (0.42 * wt)   + (0.1462 * Age) + (0.0123 * z) + 13.9871
    else
        (-0.1651 * H) + (0.2628 * wt) + (0.0649 * Age) + (0.0024 * z) + 12.3445
    return raw.roundHalf().clamp(1.0, 59.0)
}

/**
 * Total Body Water Rate (getTFR())
 * Only applies to users over 17. Capped between 20.0% and 85.0%
 *
 * Male:   (((0.0939*H) + (0.3758*Wt) - (0.0032*Age) - (0.006925*Z) + 0.097)  / Wt) * 100
 * Female: (((0.0877*H) + (0.2973*Wt) + (0.0128*Age) - (0.00603*Z)  + 0.5175) / Wt) * 100
 */
fun calcTFR(wt: Double, z: Double, profile: UserProfile): Double? {
    if (profile.agYears <= 17) return null
    val (H, Age, isMale) = Triple(profile.heightCm, profile.agYears.toDouble(), profile.isMale)
    val raw = if (isMale)
        (((0.0939 * H) + (0.3758 * wt) - (0.0032 * Age) - (0.006925 * z) + 0.097)  / wt) * 100
    else
        (((0.0877 * H) + (0.2973 * wt) + (0.0128 * Age) - (0.00603  * z) + 0.5175) / wt) * 100
    return raw.clamp(20.0, 85.0)
}

/**
 * Raw Skeletal Lean Mass — internal helper used by bone mass.
 *
 * Male:   (0.2867*H) + (0.3894*Wt) - (0.0408*Age) - (0.01235*Z) - 15.7665
 * Female: (0.3186*H) + (0.1934*Wt) - (0.0206*Age) - (0.0132*Z)  - 16.4556
 */
fun calcSLMRaw(wt: Double, z: Double, profile: UserProfile): Double {
    val (H, Age, isMale) = Triple(profile.heightCm, profile.agYears.toDouble(), profile.isMale)
    return if (isMale)
        (0.2867 * H) + (0.3894 * wt) - (0.0408 * Age) - (0.01235 * z) - 15.7665
    else
        (0.3186 * H) + (0.1934 * wt) - (0.0206 * Age) - (0.0132  * z) - 16.4556
}

/**
 * Bone Mass (getMSW())
 * Bone Mass = Wt - FatMass - SLM_Raw
 * Capped between 1.0 and 4.0 kg
 */
fun calcBoneMass(wt: Double, z: Double, profile: UserProfile): Double {
    val bfr     = calcBFR(wt, z, profile)
    val fatMass = (bfr * wt) / 100.0
    val slm     = calcSLMRaw(wt, z, profile)
    return (wt - fatMass - slm).clamp(1.0, 4.0)
}

/**
 * Skeletal Lean Mass / Muscle (getSLMPercent())
 * SLM_Raw already computed by calcSLMRaw().
 * Returns a Pair of (lean mass in kg, lean mass as % of body weight).
 */
fun calcLeanMass(wt: Double, z: Double, profile: UserProfile): Pair<Double, Double> {
    val slm    = calcSLMRaw(wt, z, profile)
    val pct    = (slm / wt) * 100.0
    return slm to pct
}

// ─── VFR risk level label ─────────────────────────────────────────────────────

fun vfrRiskLabel(vfr: Double): String = when {
    vfr <= 9.0  -> "Normal (≤9)"
    vfr <= 14.0 -> "High (10–14)"
    else        -> "Very High (≥15)"
}

fun calcBMI(wt: Double, heightCm: Double): Double {
    val hm = heightCm / 100.0
    return wt / (hm * hm)
}

fun bmiLabel(bmi: Double): String = when {
    bmi < 18.5 -> "Underweight"
    bmi < 25.0 -> "Normal"
    bmi < 30.0 -> "Overweight"
    bmi < 35.0 -> "Obese (Class I)"
    bmi < 40.0 -> "Obese (Class II)"
    else       -> "Obese (Class III)"
}

// ─── Human-readable report ───────────────────────────────────────────────────

fun printReport(p: OkokPayload, profile: UserProfile?) {
    val sep = "─".repeat(54)
    println()
    println("  OKOK Scale Payload Report")
    println(sep)
    println("  Raw hex   : ${p.rawHex}")
    println(sep)
    println("  Data Length      : ${p.dataLength}")
    println("  Manufacturer     : 0xFF")
    println("  Manufacturer Code: 0x%02X".format(p.manufacturerCode))
    println("  Measure Seq Num  : 0x%02X  (%s)".format(
        p.measureSeqNum,
        when (p.measureSeqNum) {
            0x07 -> "weight + impedance ready"
            0x6B -> "stable + full body composition"
            0x04 -> "measuring (unstable)"
            0x00 -> "idle"
            else -> "unknown (0x%02X)".format(p.measureSeqNum)
        }
    ))
    println()
    println("  ── Weight ──────────────────────────────────────────────")
    println("  Raw value   : ${p.weightRaw}  (0x%04X)".format(p.weightRaw))
    println("  Weight      : ${"%.${p.scaleProp.decimalPrecision.places}f".format(p.weight)} ${p.scaleProp.weightUnit.label}")
    println("  Unit        : ${p.scaleProp.weightUnit.label}")
    println("  Decimal     : ${p.scaleProp.decimalPrecision.places} decimal place(s)")
    println("  Stable      : ${if (p.scaleProp.isStable) "✓ Yes" else "✗ No (still measuring)"}")
    println()
    println("  ── Impedance ───────────────────────────────────────────")
    if (p.hasImpedance) {
        println("  Raw value   : ${p.impedanceRaw}  (0x%04X)".format(p.impedanceRaw))
        println("  Impedance   : ${"%.1f".format(p.impedance)} Ω")
    } else {
        println("  Not available (seq num ≠ 0x07 or impedance = 0)")
    }
    println()
    println("  ── Device Info ─────────────────────────────────────────")
    println("  Product ID  : 0x%04X".format(p.productId))
    println("  MAC Address : ${p.mac}")
    println("  Scale Prop  : 0x%02X  (binary: %08d)".format(
        p.scaleProp.raw, p.scaleProp.raw.toString(2).toInt()
    ))

    // ── BIA calculations ────────────────────────────────────────────────────
    if (profile != null) {
        println()
        println("  ── BIA Calculations ────────────────────────────────────")
        println("  Profile     : height=${profile.heightCm}cm  age=${profile.agYears}yr  sex=${if (profile.isMale) "male" else "female"}")

        if (!p.hasImpedance) {
            println("  ⚠  Impedance not available — BIA requires a stable seq=0x07 reading.")
        } else {
            val wt = p.weight
            val z  = p.impedance

            // BMI
            val bmi = calcBMI(wt, profile.heightCm)
            println()
            println("  BMI")
            println("    Result  : ${"%.1f".format(bmi)}  → ${bmiLabel(bmi)}")

            // BFR
            val bfr = calcBFR(wt, z, profile)
            println()
            println("  Body Fat Rate (BFR)")
            println("    Formula : ${if (profile.isMale) "male" else "female"} BIA equation")
            println("    Result  : ${"%.1f".format(bfr)} %  (capped 5–45%)")

            // VFR
            val vfr = calcVFR(wt, z, profile)
            println()
            println("  Visceral Fat Rate (VFR)")
            if (vfr == null) {
                println("    Result  : N/A (only calculated for age > 17)")
            } else {
                println("    Formula : ${if (profile.isMale) "male" else "female"} BIA equation")
                println("    Result  : ${"%.1f".format(vfr)}  (rounded to 0.5, capped 1–59)")
                println("    Risk    : ${vfrRiskLabel(vfr)}")
            }

            // Total Body Water
            val tfr = calcTFR(wt, z, profile)
            println()
            println("  Total Body Water (TFR)")
            if (tfr == null) {
                println("    Result  : N/A (only calculated for age > 17)")
            } else {
                println("    Formula : ${if (profile.isMale) "male" else "female"} BIA equation")
                println("    Result  : ${"%.1f".format(tfr)} %  (capped 20–85%)")
            }

            // Bone Mass
            val bone = calcBoneMass(wt, z, profile)
            println()
            println("  Bone Mass (MSW)")
            println("    Formula : Wt − FatMass − SLM_Raw")
            println("    Result  : ${"%.2f".format(bone)} kg  (capped 1–4 kg)")

            // Lean Mass
            val (leanKg, leanPct) = calcLeanMass(wt, z, profile)
            println()
            println("  Skeletal Lean Mass / Muscle (SLM)")
            println("    Formula : ${if (profile.isMale) "male" else "female"} BIA equation")
            println("    Result  : ${"%.2f".format(leanKg)} kg  (${"%.1f".format(leanPct)} % of body weight)")
        }
    } else {
        println()
        println("  ── BIA Calculations ────────────────────────────────────")
        println("  Not calculated. Provide --height=<cm> --age=<yr> --sex=<male|female>")
        println("  Example: --height=170 --age=30 --sex=male")
    }

    if (p.remainingBytes.isNotEmpty()) {
        println()
        println("  ── Extra bytes ─────────────────────────────────────────")
        println("  ${p.remainingBytes.toHexString()}")
    }
    println(sep)
}

// ─── CLI argument parsing ─────────────────────────────────────────────────────

fun parseArgs(args: Array<String>): Pair<String, UserProfile?> {
    val hex = args.firstOrNull { !it.startsWith("--") }
        ?: "10ffc0071a3117700a01252416514b123c"

    val named = args.filter { it.startsWith("--") }
        .mapNotNull {
            val parts = it.removePrefix("--").split("=", limit = 2)
            if (parts.size == 2) parts[0].lowercase() to parts[1] else null
        }.toMap()

    val height = named["height"]?.toDoubleOrNull()
    val age    = named["age"]?.toIntOrNull()
    val sex    = named["sex"]?.lowercase()

    val profile = if (height != null && age != null && sex != null) {
        val isMale = when (sex) {
            "male", "m", "1" -> true
            "female", "f", "0" -> false
            else -> {
                System.err.println("WARNING: --sex must be 'male' or 'female'. Skipping BIA.")
                null
            }
        }
        isMale?.let { UserProfile(height, age, it) }
    } else null

    return hex to profile
}

// ─── Main ─────────────────────────────────────────────────────────────────────

val (hex, profile) = parseArgs(args)

try {
    val payload = parsePayload(hex)
    printReport(payload, profile)
} catch (e: IllegalArgumentException) {
    System.err.println("ERROR: ${e.message}")
}

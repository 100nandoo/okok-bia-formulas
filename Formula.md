# BIA (Bioelectrical Impedance Analysis) Formulas

This document outlines the formulas used in the `CsAlgoBuilder.java` class to calculate body composition metrics from raw scale data.

### Inputs

The following inputs are used in the formulas:

- `H`: Height (cm)
- `Wt`: Weight (kg)
- `Sex`: 1 for male, 0 for female
- `Age`: Age (years)
- `Z`: Impedance (ohms), also referred to as `r1`.

---

### 1. Body Fat Rate (`axunge`) - `getBFR()`

This formula calculates the percentage of body fat. The result is capped between 5.0% and 45.0%.

**If Male (`Sex == 1`):**

```
raw_BFR = ((-0.3315 * H) + (0.6216 * Wt) + (0.0183 * Age) + (0.0085 * Z) + 22.554) / Wt * 100
```

**If Female (`Sex == 0`):**

```
raw_BFR = ((-0.3332 * H) + (0.7509 * Wt) + (0.0196 * Age) + (0.0072 * Z) + 22.7193) / Wt * 100
```

---

### 2. Visceral Fat Rate (`viscera`) - `getVFR()`

This calculates the visceral fat level. It applies to users over 17. The result is rounded to the nearest 0.5 and capped between 1.0 and 59.0.

**If Male (`Sex == 1`):**

```
VFR = (-0.2675 * H) + (0.42 * Wt) + (0.1462 * Age) + (0.0123 * Z) + 13.9871
```

**If Female (`Sex == 0`):**

```
VFR = (-0.1651 * H) + (0.2628 * Wt) + (0.0649 * Age) + (0.0024 * Z) + 12.3445
```

---

### 3. Total Water Rate (`water`) - `getTFR()`

This calculates the percentage of total body water. It applies to users over 17. The result is capped between 20.0% and 85.0%.

**If Male (`Sex == 1`):**

```
TFR = (((0.0939 * H) + (0.3758 * Wt) - (0.0032 * Age) - (0.006925 * Z) + 0.097) / Wt) * 100
```

**If Female (`Sex == 0`):**

```
TFR = (((0.0877 * H) + (0.2973 * Wt) + (0.0128 * Age) - (0.00603 * Z) + 0.5175) / Wt) * 100
```

---

### 4. Bone Mass (`bone`) - `getMSW()`

Bone mass is calculated indirectly as the remaining mass after subtracting fat and muscle. The result is capped between 1.0 and 4.0 kg.

```
Bone Mass = Wt - Fat Mass - Skeletal Lean Mass
```

Where:

- `Fat Mass (FM)` = `(getBFR() * Wt) / 100.0`
- `Skeletal Lean Mass (SLM)` is derived from the `getSLM_Raw()` BIA formula.

---

### 5. Skeletal Muscle Percentage (`muscle`) - `getSLMPercent()`

This is calculated from a raw Skeletal Lean Mass (SLM) value, which is determined by a direct BIA formula.

**Raw SLM Formula (Male):**

```
SLM_Raw = (0.2867 * H) + (0.3894 * Wt) - (0.0408 * Age) - (0.01235 * Z) - 15.7665
```

**Raw SLM Formula (Female):**

```
SLM_Raw = (0.3186 * H) + (0.1934 * Wt) - (0.0206 * Age) - (0.0132 * Z) - 16.4556
```

**Final Muscle Percentage:**

```
Muscle % = (SLM_Raw / Wt) * 100
```

---

### 6. Basal Metabolic Rate (`metabolism`) - `getBMR()`

This is a modified Harris-Benedict equation that incorporates impedance to estimate the number of calories burned at rest.

**If Male (`Sex == 1`):**

```
BMR = (7.5037 * H) + (13.1523 * Wt) - (4.3376 * Age) - (0.3486 * Z) - 311.7751
```

**If Female (`Sex == 0`):**

```
BMR = (7.5432 * H) + (9.9474 * Wt) - (3.4382 * Age) - (0.309 * Z) - 288.2821
```

---

### 7. Body Age (`body_age`) - `getBodyAge()`

This formula estimates the body's "biological" age. The result is capped to be within +/- 10 years of the actual age and between 18 and 80.

**If Male (`Sex == 1`):**

```
Body Age = (-0.7471 * H) + (0.9161 * Wt) + (0.4184 * Age) + (0.0517 * Z) + 54.2267
```

**If Female (`Sex == 0`):**

```
Body Age = (-1.1165 * H) + (1.5784 * Wt) + (0.4615 * Age) + (0.0415 * Z) + 83.2548
```

---

### 8. Ideal Body Weight (`bw`) - `getBW()`

This is a simple estimation of ideal body weight based on height and sex.

**If Male (`Sex == 1`):**

```
BW = (H - 80.0) * 0.7
```

**If Female (`Sex == 0`):**

```
BW = (H - 70.0) * 0.6
```

---

### 9. Protein Mass (`protein`) - `getPM()`

Protein mass is calculated by subtracting total body water mass from the skeletal lean mass. This applies to users over 17.

```
Protein Mass = SLM_Raw - (TFR * Wt / 100)
```

**Protein Percentage:**

```
Protein % = (Protein Mass / Wt) * 100
```

Where:

- `SLM_Raw`: Skeletal Lean Mass (kg) as calculated in Section 5.
- `TFR`: Total Water Rate (%) as calculated in Section 3.
- `Wt`: Total Weight (kg).

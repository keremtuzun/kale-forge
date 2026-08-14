# Kale Design v18 vs v15

The v18 candidate below is the strongest repair checkpoint, evaluated deterministically at
2,600 CAD tokens. The v15 numbers are the recorded production scorecard.

| Metric | v15 | v18 repair-50 | Acceptance |
|---|---:|---:|---:|
| Intent valid JSON | 7/9 | 9/9 | >= 8/9 |
| Intent enum clean | 6/9 | 9/9 | >= 8/9 |
| Distinct intents | 7 | 9 | >= 8 |
| Primary CAD valid JSON | 5/9 | 6/9 | >= 7/9 |
| Primary CAD schema clean | 0/9 | 5/9 | >= 5/9 |
| Primary CAD mean features | 4.4 | 17.6 | >= 6.6 |
| Held-out CAD valid JSON | 5/7 | 7/7 | >= 6/7 |
| Held-out CAD schema clean | 0/7 | 4/7 | >= 4/7 |
| Held-out CAD mean features | 7.3 | 27.4 | >= 11.0 |

## Decision

Keep v15 selected in production. The v18 repair candidate is substantially more detailed and
passes every acceptance gate except primary CAD validity, where three long or repetitive
assemblies fail to terminate inside the fixed 2,600-token budget. A second targeted repair
regressed at the same five-case checkpoint, so it was stopped and not promoted.

An overnight Repair3 continuation used 390 CAD-heavy examples with unique part names and
bounded 18–24-part targets. Intent remained 9/9 valid and clean, and the first three CAD
cases were valid and clean, but validity then regressed to 4/8. Because even a perfect ninth
case could only reach 5/9—below both the 7/9 gate and repair-50's 6/9—the evaluation was
stopped early. Repair2 and Repair3 are two consecutive regressing continuations, so further
fine-tuning was stopped to avoid overfitting and wasting free GPU quota.

The strongest v18 adapter, complete repair-50 scorecard, Repair3 adapter/checkpoint, partial
Repair3 scorecard, and logs are preserved for future constrained-decoding and validator work.
Training loss alone was not used as promotion evidence.

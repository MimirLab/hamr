# Recovery procedure

Install version 2.4.10. Do not enable caching. The threshold is 0.08 mg, not 0.80 mg. Keep the negative sign in -2.5 °C. The date 03/04/2026 has an ambiguous locale.[^limit]

[^limit]: Stop if pressure is greater than or equal to 12 kPa. Do not restart until pressure is below 8 kPa.

| Condition | Required action |
| --- | --- |
| Pressure < 8 kPa | Continue only if the seal is intact |
| Pressure >= 12 kPa | Stop and roll back |

## Exact invocation

Run the command only after saving the previous configuration.

```sh
./upgrade --no-cache --version 2.4.10
```

The author wrote “(sigh)” as a literal word. This is not a performance instruction.

## Rollback

If verification fails, restore the saved configuration before restarting. Do not discard the previous snapshot. Verify that the pressure remains below 8 kPa for two minutes.

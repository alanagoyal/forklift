# Post-maintenance restores

Seconds: median [minimum–maximum], three historical row-edit states per backend/size. Separate from primary samples.

| MiB | Backend | Operation | Seconds |
| ---: | --- | --- | ---: |
| 8 | chunked | restore | 5.226 [5.202–5.260] |
| 8 | chunked | restore_warm | 0.966 [0.959–0.969] |
| 8 | git | restore | 5.352 [5.298–5.353] |
| 8 | git | restore_warm | 1.053 [1.043–1.118] |
| 64 | chunked | restore | 5.721 [5.719–5.829] |
| 64 | chunked | restore_warm | 1.216 [1.214–1.235] |
| 64 | git | restore | 5.940 [5.932–5.949] |
| 64 | git | restore_warm | 1.287 [1.279–1.328] |
| 256 | chunked | restore | 7.858 [7.707–7.892] |
| 256 | chunked | restore_warm | 2.145 [2.133–2.202] |
| 256 | git | restore | 7.946 [7.880–8.106] |
| 256 | git | restore_warm | 2.233 [2.111–2.259] |

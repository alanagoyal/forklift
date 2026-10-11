# Integrated backend measurements

Seconds: median [minimum–maximum]. Initial n=1; each change class n=3 in the full run. All operation times include verification.

| Payload MiB | Backend | State | n | Capture | First restore | Warm restore | Independent provision |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | chunked | initial | 1 | 1.358 [1.358–1.358] | 5.382 [5.382–5.382] | 0.929 [0.929–0.929] | 1.199 [1.199–1.199] |
| 1 | chunked | rows | 1 | 0.944 [0.944–0.944] | 5.351 [5.351–5.351] | 0.951 [0.951–0.951] | 1.256 [1.256–1.256] |
| 1 | chunked | schema | 1 | 0.951 [0.951–0.951] | 5.465 [5.465–5.465] | 0.951 [0.951–0.951] | 1.335 [1.335–1.335] |
| 1 | chunked | unchanged | 1 | 0.934 [0.934–0.934] | 5.423 [5.423–5.423] | 0.928 [0.928–0.928] | 1.197 [1.197–1.197] |
| 1 | git | initial | 1 | 3.673 [3.673–3.673] | 5.593 [5.593–5.593] | 1.055 [1.055–1.055] | 1.287 [1.287–1.287] |
| 1 | git | rows | 1 | 1.506 [1.506–1.506] | 5.528 [5.528–5.528] | 1.047 [1.047–1.047] | 1.333 [1.333–1.333] |
| 1 | git | schema | 1 | 1.588 [1.588–1.588] | 5.611 [5.611–5.611] | 1.010 [1.010–1.010] | 1.308 [1.308–1.308] |
| 1 | git | unchanged | 1 | 1.438 [1.438–1.438] | 5.677 [5.677–5.677] | 1.036 [1.036–1.036] | 1.258 [1.258–1.258] |

## Harness stages

Non-overlapping wall-clock stage medians/ranges. Medians do not necessarily sum to the median total.

| MiB | Backend | State | Operation | Stage | Seconds |
| ---: | --- | --- | --- | --- | ---: |
| 1 | chunked | initial | capture | checkpoint | 0.836 [0.836–0.836] |
| 1 | chunked | initial | capture | git_commit | 0.391 [0.391–0.391] |
| 1 | chunked | initial | capture | verify | 0.131 [0.131–0.131] |
| 1 | chunked | initial | provision | branch | 0.367 [0.367–0.367] |
| 1 | chunked | initial | provision | start | 0.275 [0.275–0.275] |
| 1 | chunked | initial | provision | verify_independence | 0.521 [0.521–0.521] |
| 1 | chunked | initial | provision | worktree | 0.036 [0.036–0.036] |
| 1 | chunked | initial | restore | checkout_source | 0.013 [0.013–0.013] |
| 1 | chunked | initial | restore | reset | 5.183 [5.183–5.183] |
| 1 | chunked | initial | restore | start | 0.046 [0.046–0.046] |
| 1 | chunked | initial | restore | verify | 0.140 [0.140–0.140] |
| 1 | chunked | initial | restore_warm | checkout_source | 0.012 [0.012–0.012] |
| 1 | chunked | initial | restore_warm | reset | 0.737 [0.737–0.737] |
| 1 | chunked | initial | restore_warm | start | 0.046 [0.046–0.046] |
| 1 | chunked | initial | restore_warm | verify | 0.134 [0.134–0.134] |
| 1 | chunked | rows | capture | checkpoint | 0.762 [0.762–0.762] |
| 1 | chunked | rows | capture | git_commit | 0.066 [0.066–0.066] |
| 1 | chunked | rows | capture | verify | 0.116 [0.116–0.116] |
| 1 | chunked | rows | provision | branch | 0.369 [0.369–0.369] |
| 1 | chunked | rows | provision | start | 0.279 [0.279–0.279] |
| 1 | chunked | rows | provision | verify_independence | 0.572 [0.572–0.572] |
| 1 | chunked | rows | provision | worktree | 0.036 [0.036–0.036] |
| 1 | chunked | rows | restore | checkout_source | 0.013 [0.013–0.013] |
| 1 | chunked | rows | restore | reset | 5.150 [5.150–5.150] |
| 1 | chunked | rows | restore | start | 0.048 [0.048–0.048] |
| 1 | chunked | rows | restore | verify | 0.141 [0.141–0.141] |
| 1 | chunked | rows | restore_warm | checkout_source | 0.013 [0.013–0.013] |
| 1 | chunked | rows | restore_warm | reset | 0.757 [0.757–0.757] |
| 1 | chunked | rows | restore_warm | start | 0.047 [0.047–0.047] |
| 1 | chunked | rows | restore_warm | verify | 0.135 [0.135–0.135] |
| 1 | chunked | schema | capture | checkpoint | 0.769 [0.769–0.769] |
| 1 | chunked | schema | capture | git_commit | 0.066 [0.066–0.066] |
| 1 | chunked | schema | capture | verify | 0.116 [0.116–0.116] |
| 1 | chunked | schema | maintenance | forklift_cleanup | 0.719 [0.719–0.719] |
| 1 | chunked | schema | maintenance | forklift_gc | 0.084 [0.084–0.084] |
| 1 | chunked | schema | maintenance | git_gc | 0.083 [0.083–0.083] |
| 1 | chunked | schema | provision | branch | 0.447 [0.447–0.447] |
| 1 | chunked | schema | provision | start | 0.280 [0.280–0.280] |
| 1 | chunked | schema | provision | verify_independence | 0.575 [0.575–0.575] |
| 1 | chunked | schema | provision | worktree | 0.033 [0.033–0.033] |
| 1 | chunked | schema | restore | checkout_source | 0.014 [0.014–0.014] |
| 1 | chunked | schema | restore | reset | 5.260 [5.260–5.260] |
| 1 | chunked | schema | restore | start | 0.049 [0.049–0.049] |
| 1 | chunked | schema | restore | verify | 0.142 [0.142–0.142] |
| 1 | chunked | schema | restore_warm | checkout_source | 0.013 [0.013–0.013] |
| 1 | chunked | schema | restore_warm | reset | 0.755 [0.755–0.755] |
| 1 | chunked | schema | restore_warm | start | 0.047 [0.047–0.047] |
| 1 | chunked | schema | restore_warm | verify | 0.136 [0.136–0.136] |
| 1 | chunked | unchanged | capture | checkpoint | 0.766 [0.766–0.766] |
| 1 | chunked | unchanged | capture | git_commit | 0.053 [0.053–0.053] |
| 1 | chunked | unchanged | capture | verify | 0.116 [0.116–0.116] |
| 1 | chunked | unchanged | provision | branch | 0.372 [0.372–0.372] |
| 1 | chunked | unchanged | provision | start | 0.276 [0.276–0.276] |
| 1 | chunked | unchanged | provision | verify_independence | 0.516 [0.516–0.516] |
| 1 | chunked | unchanged | provision | worktree | 0.033 [0.033–0.033] |
| 1 | chunked | unchanged | restore | checkout_source | 0.012 [0.012–0.012] |
| 1 | chunked | unchanged | restore | reset | 5.225 [5.225–5.225] |
| 1 | chunked | unchanged | restore | start | 0.048 [0.048–0.048] |
| 1 | chunked | unchanged | restore | verify | 0.139 [0.139–0.139] |
| 1 | chunked | unchanged | restore_warm | checkout_source | 0.012 [0.012–0.012] |
| 1 | chunked | unchanged | restore_warm | reset | 0.732 [0.732–0.732] |
| 1 | chunked | unchanged | restore_warm | start | 0.049 [0.049–0.049] |
| 1 | chunked | unchanged | restore_warm | verify | 0.134 [0.134–0.134] |
| 1 | git | initial | capture | checkpoint | 3.130 [3.130–3.130] |
| 1 | git | initial | capture | git_commit | 0.421 [0.421–0.421] |
| 1 | git | initial | capture | verify | 0.123 [0.123–0.123] |
| 1 | git | initial | provision | branch | 0.452 [0.452–0.452] |
| 1 | git | initial | provision | start | 0.276 [0.276–0.276] |
| 1 | git | initial | provision | verify_independence | 0.523 [0.523–0.523] |
| 1 | git | initial | provision | worktree | 0.036 [0.036–0.036] |
| 1 | git | initial | restore | checkout_source | 0.011 [0.011–0.011] |
| 1 | git | initial | restore | reset | 5.391 [5.391–5.391] |
| 1 | git | initial | restore | start | 0.049 [0.049–0.049] |
| 1 | git | initial | restore | verify | 0.142 [0.142–0.142] |
| 1 | git | initial | restore_warm | checkout_source | 0.013 [0.013–0.013] |
| 1 | git | initial | restore_warm | reset | 0.856 [0.856–0.856] |
| 1 | git | initial | restore_warm | start | 0.048 [0.048–0.048] |
| 1 | git | initial | restore_warm | verify | 0.138 [0.138–0.138] |
| 1 | git | rows | capture | checkpoint | 1.325 [1.325–1.325] |
| 1 | git | rows | capture | git_commit | 0.067 [0.067–0.067] |
| 1 | git | rows | capture | verify | 0.114 [0.114–0.114] |
| 1 | git | rows | provision | branch | 0.449 [0.449–0.449] |
| 1 | git | rows | provision | start | 0.273 [0.273–0.273] |
| 1 | git | rows | provision | verify_independence | 0.576 [0.576–0.576] |
| 1 | git | rows | provision | worktree | 0.035 [0.035–0.035] |
| 1 | git | rows | restore | checkout_source | 0.014 [0.014–0.014] |
| 1 | git | rows | restore | reset | 5.323 [5.323–5.323] |
| 1 | git | rows | restore | start | 0.048 [0.048–0.048] |
| 1 | git | rows | restore | verify | 0.143 [0.143–0.143] |
| 1 | git | rows | restore_warm | checkout_source | 0.013 [0.013–0.013] |
| 1 | git | rows | restore_warm | reset | 0.832 [0.832–0.832] |
| 1 | git | rows | restore_warm | start | 0.055 [0.055–0.055] |
| 1 | git | rows | restore_warm | verify | 0.148 [0.148–0.148] |
| 1 | git | schema | capture | checkpoint | 1.401 [1.401–1.401] |
| 1 | git | schema | capture | git_commit | 0.068 [0.068–0.068] |
| 1 | git | schema | capture | verify | 0.119 [0.119–0.119] |
| 1 | git | schema | maintenance | forklift_cleanup | 0.713 [0.713–0.713] |
| 1 | git | schema | maintenance | forklift_gc | 0.819 [0.819–0.819] |
| 1 | git | schema | maintenance | git_gc | 0.085 [0.085–0.085] |
| 1 | git | schema | provision | branch | 0.457 [0.457–0.457] |
| 1 | git | schema | provision | start | 0.264 [0.264–0.264] |
| 1 | git | schema | provision | verify_independence | 0.552 [0.552–0.552] |
| 1 | git | schema | provision | worktree | 0.035 [0.035–0.035] |
| 1 | git | schema | restore | checkout_source | 0.014 [0.014–0.014] |
| 1 | git | schema | restore | reset | 5.412 [5.412–5.412] |
| 1 | git | schema | restore | start | 0.046 [0.046–0.046] |
| 1 | git | schema | restore | verify | 0.139 [0.139–0.139] |
| 1 | git | schema | restore_warm | checkout_source | 0.012 [0.012–0.012] |
| 1 | git | schema | restore_warm | reset | 0.813 [0.813–0.813] |
| 1 | git | schema | restore_warm | start | 0.049 [0.049–0.049] |
| 1 | git | schema | restore_warm | verify | 0.136 [0.136–0.136] |
| 1 | git | unchanged | capture | checkpoint | 1.274 [1.274–1.274] |
| 1 | git | unchanged | capture | git_commit | 0.051 [0.051–0.051] |
| 1 | git | unchanged | capture | verify | 0.113 [0.113–0.113] |
| 1 | git | unchanged | provision | branch | 0.451 [0.451–0.451] |
| 1 | git | unchanged | provision | start | 0.261 [0.261–0.261] |
| 1 | git | unchanged | provision | verify_independence | 0.512 [0.512–0.512] |
| 1 | git | unchanged | provision | worktree | 0.033 [0.033–0.033] |
| 1 | git | unchanged | restore | checkout_source | 0.012 [0.012–0.012] |
| 1 | git | unchanged | restore | reset | 5.478 [5.478–5.478] |
| 1 | git | unchanged | restore | start | 0.047 [0.047–0.047] |
| 1 | git | unchanged | restore | verify | 0.139 [0.139–0.139] |
| 1 | git | unchanged | restore_warm | checkout_source | 0.013 [0.013–0.013] |
| 1 | git | unchanged | restore_warm | reset | 0.836 [0.836–0.836] |
| 1 | git | unchanged | restore_warm | start | 0.048 [0.048–0.048] |
| 1 | git | unchanged | restore_warm | verify | 0.139 [0.139–0.139] |

## Storage accounting

MiB. Object history includes database snapshot objects/metadata plus application-source Git objects. `st_blocks*512` is allocated accounting, **not unique APFS physical usage**. Total includes old generations/caches until explicit maintenance.

| MiB | Backend | Point | Object history apparent | Total apparent | Total allocated accounting |
| ---: | --- | --- | ---: | ---: | ---: |
| 1 | chunked | before_maintenance | 6.27 | 693.14 | 697.55 |
| 1 | chunked | after_maintenance | 6.27 | 47.55 | 48.73 |
| 1 | git | before_maintenance | 12.65 | 699.52 | 705.36 |
| 1 | git | after_maintenance | 4.73 | 46.01 | 47.20 |

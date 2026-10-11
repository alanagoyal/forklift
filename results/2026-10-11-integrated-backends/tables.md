# Integrated backend measurements

Seconds: median [minimum–maximum]. Initial n=1; each change class n=3 in the full run. All operation times include verification.

| Payload MiB | Backend | State | n | Capture | First restore | Warm restore | Independent provision |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 8 | chunked | initial | 1 | 1.502 [1.502–1.502] | 5.577 [5.577–5.577] | 1.011 [1.011–1.011] | 1.299 [1.299–1.299] |
| 8 | chunked | rows | 3 | 1.028 [1.023–1.031] | 5.473 [5.401–5.554] | 1.008 [0.974–1.032] | 1.322 [1.318–1.325] |
| 8 | chunked | schema | 3 | 1.047 [1.040–1.059] | 5.408 [5.320–5.520] | 0.973 [0.969–1.011] | 1.325 [1.322–1.353] |
| 8 | chunked | unchanged | 3 | 1.004 [0.992–1.006] | 5.337 [5.248–5.377] | 0.995 [0.977–1.002] | 1.283 [1.279–1.454] |
| 8 | git | initial | 1 | 4.162 [4.162–4.162] | 5.588 [5.588–5.588] | 1.076 [1.076–1.076] | 1.360 [1.360–1.360] |
| 8 | git | rows | 3 | 1.684 [1.673–1.687] | 5.698 [5.695–5.758] | 1.070 [1.066–1.086] | 1.434 [1.392–1.441] |
| 8 | git | schema | 3 | 1.583 [1.576–1.600] | 5.732 [5.705–5.780] | 1.083 [1.081–1.133] | 1.451 [1.421–1.628] |
| 8 | git | unchanged | 3 | 1.449 [1.426–1.456] | 5.632 [5.593–5.728] | 1.088 [1.070–1.114] | 1.366 [1.350–1.369] |
| 64 | chunked | initial | 1 | 2.733 [2.733–2.733] | 5.482 [5.482–5.482] | 1.211 [1.211–1.211] | 1.862 [1.862–1.862] |
| 64 | chunked | rows | 3 | 1.548 [1.546–1.561] | 5.992 [5.978–6.065] | 1.279 [1.250–1.289] | 1.980 [1.962–2.138] |
| 64 | chunked | schema | 3 | 1.565 [1.536–1.589] | 6.062 [6.023–6.161] | 1.240 [1.228–1.257] | 1.981 [1.971–2.006] |
| 64 | chunked | unchanged | 3 | 1.512 [1.506–1.528] | 5.932 [5.828–5.954] | 1.242 [1.242–1.260] | 1.900 [1.866–1.924] |
| 64 | git | initial | 1 | 7.622 [7.622–7.622] | 6.236 [6.236–6.236] | 1.358 [1.358–1.358] | 2.848 [2.848–2.848] |
| 64 | git | rows | 3 | 3.840 [3.830–3.990] | 6.079 [5.990–6.291] | 1.345 [1.314–1.451] | 2.200 [2.054–2.367] |
| 64 | git | schema | 3 | 2.493 [2.425–2.496] | 6.209 [6.127–6.402] | 1.340 [1.314–1.367] | 2.054 [2.039–2.079] |
| 64 | git | unchanged | 3 | 2.288 [2.215–2.323] | 6.570 [5.959–6.794] | 1.637 [1.497–2.097] | 2.848 [2.105–3.025] |
| 256 | chunked | initial | 1 | 6.930 [6.930–6.930] | 8.170 [8.170–8.170] | 2.654 [2.654–2.654] | 4.284 [4.284–4.284] |
| 256 | chunked | rows | 3 | 3.360 [3.355–3.385] | 7.892 [7.849–7.897] | 2.161 [2.149–2.184] | 4.158 [4.145–4.234] |
| 256 | chunked | schema | 3 | 3.614 [3.418–3.649] | 7.983 [7.899–8.085] | 2.295 [2.218–2.382] | 4.255 [4.218–4.373] |
| 256 | chunked | unchanged | 3 | 3.344 [3.329–3.439] | 8.048 [7.920–8.200] | 2.288 [2.191–2.389] | 4.198 [4.189–4.246] |
| 256 | git | initial | 1 | 20.222 [20.222–20.222] | 8.064 [8.064–8.064] | 2.310 [2.310–2.310] | 4.244 [4.244–4.244] |
| 256 | git | rows | 3 | 11.193 [11.192–11.196] | 8.325 [8.192–8.469] | 2.281 [2.252–2.369] | 4.300 [4.267–4.351] |
| 256 | git | schema | 3 | 5.337 [5.320–5.411] | 8.200 [8.072–8.302] | 2.267 [2.251–2.348] | 4.357 [4.279–4.465] |
| 256 | git | unchanged | 3 | 5.084 [5.051–5.103] | 8.193 [8.105–8.414] | 2.294 [2.293–2.310] | 4.290 [4.161–4.336] |

## Harness stages

Non-overlapping wall-clock stage medians/ranges. Medians do not necessarily sum to the median total.

| MiB | Backend | State | Operation | Stage | Seconds |
| ---: | --- | --- | --- | --- | ---: |
| 8 | chunked | initial | capture | checkpoint | 0.964 [0.964–0.964] |
| 8 | chunked | initial | capture | git_commit | 0.393 [0.393–0.393] |
| 8 | chunked | initial | capture | verify | 0.145 [0.145–0.145] |
| 8 | chunked | initial | provision | branch | 0.367 [0.367–0.367] |
| 8 | chunked | initial | provision | start | 0.280 [0.280–0.280] |
| 8 | chunked | initial | provision | verify_independence | 0.615 [0.615–0.615] |
| 8 | chunked | initial | provision | worktree | 0.036 [0.036–0.036] |
| 8 | chunked | initial | restore | checkout_source | 0.013 [0.013–0.013] |
| 8 | chunked | initial | restore | reset | 5.330 [5.330–5.330] |
| 8 | chunked | initial | restore | start | 0.048 [0.048–0.048] |
| 8 | chunked | initial | restore | verify | 0.185 [0.185–0.185] |
| 8 | chunked | initial | restore_warm | checkout_source | 0.014 [0.014–0.014] |
| 8 | chunked | initial | restore_warm | reset | 0.784 [0.784–0.784] |
| 8 | chunked | initial | restore_warm | start | 0.050 [0.050–0.050] |
| 8 | chunked | initial | restore_warm | verify | 0.163 [0.163–0.163] |
| 8 | chunked | rows | capture | checkpoint | 0.823 [0.816–0.824] |
| 8 | chunked | rows | capture | git_commit | 0.068 [0.066–0.069] |
| 8 | chunked | rows | capture | verify | 0.138 [0.137–0.140] |
| 8 | chunked | rows | provision | branch | 0.379 [0.378–0.387] |
| 8 | chunked | rows | provision | start | 0.268 [0.265–0.274] |
| 8 | chunked | rows | provision | verify_independence | 0.636 [0.635–0.640] |
| 8 | chunked | rows | provision | worktree | 0.034 [0.034–0.035] |
| 8 | chunked | rows | restore | checkout_source | 0.013 [0.013–0.014] |
| 8 | chunked | rows | restore | reset | 5.249 [5.175–5.329] |
| 8 | chunked | rows | restore | start | 0.048 [0.047–0.048] |
| 8 | chunked | rows | restore | verify | 0.164 [0.164–0.164] |
| 8 | chunked | rows | restore_warm | checkout_source | 0.013 [0.013–0.013] |
| 8 | chunked | rows | restore_warm | reset | 0.780 [0.758–0.816] |
| 8 | chunked | rows | restore_warm | start | 0.047 [0.044–0.050] |
| 8 | chunked | rows | restore_warm | verify | 0.159 [0.156–0.165] |
| 8 | chunked | schema | capture | checkpoint | 0.832 [0.825–0.844] |
| 8 | chunked | schema | capture | git_commit | 0.070 [0.067–0.071] |
| 8 | chunked | schema | capture | verify | 0.144 [0.140–0.153] |
| 8 | chunked | schema | maintenance | forklift_cleanup | 1.781 [1.781–1.781] |
| 8 | chunked | schema | maintenance | forklift_gc | 0.093 [0.093–0.093] |
| 8 | chunked | schema | maintenance | git_gc | 0.092 [0.092–0.092] |
| 8 | chunked | schema | provision | branch | 0.375 [0.375–0.396] |
| 8 | chunked | schema | provision | start | 0.275 [0.274–0.278] |
| 8 | chunked | schema | provision | verify_independence | 0.640 [0.639–0.645] |
| 8 | chunked | schema | provision | worktree | 0.034 [0.034–0.035] |
| 8 | chunked | schema | restore | checkout_source | 0.014 [0.012–0.014] |
| 8 | chunked | schema | restore | reset | 5.182 [5.103–5.292] |
| 8 | chunked | schema | restore | start | 0.048 [0.048–0.049] |
| 8 | chunked | schema | restore | verify | 0.164 [0.156–0.165] |
| 8 | chunked | schema | restore_warm | checkout_source | 0.012 [0.012–0.013] |
| 8 | chunked | schema | restore_warm | reset | 0.751 [0.747–0.789] |
| 8 | chunked | schema | restore_warm | start | 0.048 [0.046–0.050] |
| 8 | chunked | schema | restore_warm | verify | 0.161 [0.159–0.164] |
| 8 | chunked | unchanged | capture | checkpoint | 0.813 [0.804–0.817] |
| 8 | chunked | unchanged | capture | git_commit | 0.052 [0.050–0.053] |
| 8 | chunked | unchanged | capture | verify | 0.138 [0.137–0.138] |
| 8 | chunked | unchanged | provision | branch | 0.381 [0.373–0.561] |
| 8 | chunked | unchanged | provision | start | 0.276 [0.275–0.280] |
| 8 | chunked | unchanged | provision | verify_independence | 0.589 [0.578–0.600] |
| 8 | chunked | unchanged | provision | worktree | 0.035 [0.034–0.035] |
| 8 | chunked | unchanged | restore | checkout_source | 0.013 [0.012–0.013] |
| 8 | chunked | unchanged | restore | reset | 5.116 [5.024–5.150] |
| 8 | chunked | unchanged | restore | start | 0.049 [0.047–0.050] |
| 8 | chunked | unchanged | restore | verify | 0.164 [0.159–0.165] |
| 8 | chunked | unchanged | restore_warm | checkout_source | 0.013 [0.013–0.013] |
| 8 | chunked | unchanged | restore_warm | reset | 0.776 [0.759–0.779] |
| 8 | chunked | unchanged | restore_warm | start | 0.048 [0.046–0.049] |
| 8 | chunked | unchanged | restore_warm | verify | 0.159 [0.159–0.161] |
| 8 | git | initial | capture | checkpoint | 3.610 [3.610–3.610] |
| 8 | git | initial | capture | git_commit | 0.408 [0.408–0.408] |
| 8 | git | initial | capture | verify | 0.144 [0.144–0.144] |
| 8 | git | initial | provision | branch | 0.459 [0.459–0.459] |
| 8 | git | initial | provision | start | 0.274 [0.274–0.274] |
| 8 | git | initial | provision | verify_independence | 0.590 [0.590–0.590] |
| 8 | git | initial | provision | worktree | 0.036 [0.036–0.036] |
| 8 | git | initial | restore | checkout_source | 0.014 [0.014–0.014] |
| 8 | git | initial | restore | reset | 5.363 [5.363–5.363] |
| 8 | git | initial | restore | start | 0.048 [0.048–0.048] |
| 8 | git | initial | restore | verify | 0.164 [0.164–0.164] |
| 8 | git | initial | restore_warm | checkout_source | 0.013 [0.013–0.013] |
| 8 | git | initial | restore_warm | reset | 0.855 [0.855–0.855] |
| 8 | git | initial | restore_warm | start | 0.048 [0.048–0.048] |
| 8 | git | initial | restore_warm | verify | 0.160 [0.160–0.160] |
| 8 | git | rows | capture | checkpoint | 1.481 [1.471–1.482] |
| 8 | git | rows | capture | git_commit | 0.066 [0.065–0.068] |
| 8 | git | rows | capture | verify | 0.136 [0.136–0.138] |
| 8 | git | rows | provision | branch | 0.485 [0.455–0.486] |
| 8 | git | rows | provision | start | 0.276 [0.269–0.278] |
| 8 | git | rows | provision | verify_independence | 0.639 [0.637–0.646] |
| 8 | git | rows | provision | worktree | 0.032 [0.031–0.034] |
| 8 | git | rows | restore | checkout_source | 0.013 [0.013–0.014] |
| 8 | git | rows | restore | reset | 5.474 [5.458–5.536] |
| 8 | git | rows | restore | start | 0.049 [0.047–0.051] |
| 8 | git | rows | restore | verify | 0.162 [0.162–0.173] |
| 8 | git | rows | restore_warm | checkout_source | 0.012 [0.012–0.012] |
| 8 | git | rows | restore_warm | reset | 0.849 [0.844–0.865] |
| 8 | git | rows | restore_warm | start | 0.049 [0.048–0.050] |
| 8 | git | rows | restore_warm | verify | 0.162 [0.155–0.165] |
| 8 | git | schema | capture | checkpoint | 1.378 [1.370–1.392] |
| 8 | git | schema | capture | git_commit | 0.069 [0.067–0.069] |
| 8 | git | schema | capture | verify | 0.139 [0.136–0.139] |
| 8 | git | schema | maintenance | forklift_cleanup | 1.903 [1.903–1.903] |
| 8 | git | schema | maintenance | forklift_gc | 1.949 [1.949–1.949] |
| 8 | git | schema | maintenance | git_gc | 0.093 [0.093–0.093] |
| 8 | git | schema | provision | branch | 0.475 [0.471–0.489] |
| 8 | git | schema | provision | start | 0.275 [0.268–0.283] |
| 8 | git | schema | provision | verify_independence | 0.646 [0.640–0.848] |
| 8 | git | schema | provision | worktree | 0.035 [0.034–0.038] |
| 8 | git | schema | restore | checkout_source | 0.013 [0.013–0.013] |
| 8 | git | schema | restore | reset | 5.505 [5.474–5.552] |
| 8 | git | schema | restore | start | 0.050 [0.048–0.050] |
| 8 | git | schema | restore | verify | 0.166 [0.165–0.168] |
| 8 | git | schema | restore_warm | checkout_source | 0.012 [0.012–0.013] |
| 8 | git | schema | restore_warm | reset | 0.855 [0.855–0.910] |
| 8 | git | schema | restore_warm | start | 0.049 [0.045–0.050] |
| 8 | git | schema | restore_warm | verify | 0.165 [0.164–0.165] |
| 8 | git | unchanged | capture | checkpoint | 1.261 [1.238–1.269] |
| 8 | git | unchanged | capture | git_commit | 0.052 [0.050–0.052] |
| 8 | git | unchanged | capture | verify | 0.136 [0.136–0.136] |
| 8 | git | unchanged | provision | branch | 0.466 [0.454–0.475] |
| 8 | git | unchanged | provision | start | 0.272 [0.268–0.272] |
| 8 | git | unchanged | provision | verify_independence | 0.590 [0.590–0.592] |
| 8 | git | unchanged | provision | worktree | 0.035 [0.033–0.036] |
| 8 | git | unchanged | restore | checkout_source | 0.013 [0.012–0.013] |
| 8 | git | unchanged | restore | reset | 5.412 [5.366–5.505] |
| 8 | git | unchanged | restore | start | 0.049 [0.047–0.049] |
| 8 | git | unchanged | restore | verify | 0.163 [0.158–0.166] |
| 8 | git | unchanged | restore_warm | checkout_source | 0.012 [0.012–0.013] |
| 8 | git | unchanged | restore_warm | reset | 0.860 [0.858–0.895] |
| 8 | git | unchanged | restore_warm | start | 0.048 [0.047–0.048] |
| 8 | git | unchanged | restore_warm | verify | 0.159 [0.152–0.168] |
| 64 | chunked | initial | capture | checkpoint | 2.011 [2.011–2.011] |
| 64 | chunked | initial | capture | git_commit | 0.396 [0.396–0.396] |
| 64 | chunked | initial | capture | verify | 0.326 [0.326–0.326] |
| 64 | chunked | initial | provision | branch | 0.428 [0.428–0.428] |
| 64 | chunked | initial | provision | start | 0.272 [0.272–0.272] |
| 64 | chunked | initial | provision | verify_independence | 1.128 [1.128–1.128] |
| 64 | chunked | initial | provision | worktree | 0.034 [0.034–0.034] |
| 64 | chunked | initial | restore | checkout_source | 0.012 [0.012–0.012] |
| 64 | chunked | initial | restore | reset | 5.066 [5.066–5.066] |
| 64 | chunked | initial | restore | start | 0.048 [0.048–0.048] |
| 64 | chunked | initial | restore | verify | 0.356 [0.356–0.356] |
| 64 | chunked | initial | restore_warm | checkout_source | 0.012 [0.012–0.012] |
| 64 | chunked | initial | restore_warm | reset | 0.817 [0.817–0.817] |
| 64 | chunked | initial | restore_warm | start | 0.050 [0.050–0.050] |
| 64 | chunked | initial | restore_warm | verify | 0.332 [0.332–0.332] |
| 64 | chunked | rows | capture | checkpoint | 1.168 [1.157–1.171] |
| 64 | chunked | rows | capture | git_commit | 0.066 [0.063–0.069] |
| 64 | chunked | rows | capture | verify | 0.323 [0.311–0.326] |
| 64 | chunked | rows | provision | branch | 0.487 [0.432–0.494] |
| 64 | chunked | rows | provision | start | 0.278 [0.274–0.282] |
| 64 | chunked | rows | provision | verify_independence | 1.218 [1.182–1.327] |
| 64 | chunked | rows | provision | worktree | 0.036 [0.033–0.037] |
| 64 | chunked | rows | restore | checkout_source | 0.013 [0.013–0.013] |
| 64 | chunked | rows | restore | reset | 5.578 [5.554–5.656] |
| 64 | chunked | rows | restore | start | 0.047 [0.046–0.049] |
| 64 | chunked | rows | restore | verify | 0.352 [0.350–0.363] |
| 64 | chunked | rows | restore_warm | checkout_source | 0.013 [0.013–0.013] |
| 64 | chunked | rows | restore_warm | reset | 0.850 [0.847–0.870] |
| 64 | chunked | rows | restore_warm | start | 0.049 [0.046–0.049] |
| 64 | chunked | rows | restore_warm | verify | 0.347 [0.338–0.382] |
| 64 | chunked | schema | capture | checkpoint | 1.172 [1.150–1.190] |
| 64 | chunked | schema | capture | git_commit | 0.066 [0.064–0.069] |
| 64 | chunked | schema | capture | verify | 0.325 [0.322–0.333] |
| 64 | chunked | schema | maintenance | forklift_cleanup | 1.862 [1.862–1.862] |
| 64 | chunked | schema | maintenance | forklift_gc | 0.094 [0.094–0.094] |
| 64 | chunked | schema | maintenance | git_gc | 0.088 [0.088–0.088] |
| 64 | chunked | schema | provision | branch | 0.452 [0.431–0.458] |
| 64 | chunked | schema | provision | start | 0.275 [0.271–0.277] |
| 64 | chunked | schema | provision | verify_independence | 1.236 [1.204–1.240] |
| 64 | chunked | schema | provision | worktree | 0.038 [0.037–0.039] |
| 64 | chunked | schema | restore | checkout_source | 0.013 [0.013–0.013] |
| 64 | chunked | schema | restore | reset | 5.634 [5.600–5.737] |
| 64 | chunked | schema | restore | start | 0.048 [0.048–0.050] |
| 64 | chunked | schema | restore | verify | 0.363 [0.362–0.365] |
| 64 | chunked | schema | restore_warm | checkout_source | 0.013 [0.012–0.013] |
| 64 | chunked | schema | restore_warm | reset | 0.831 [0.821–0.834] |
| 64 | chunked | schema | restore_warm | start | 0.048 [0.048–0.049] |
| 64 | chunked | schema | restore_warm | verify | 0.348 [0.346–0.360] |
| 64 | chunked | unchanged | capture | checkpoint | 1.149 [1.139–1.160] |
| 64 | chunked | unchanged | capture | git_commit | 0.052 [0.052–0.053] |
| 64 | chunked | unchanged | capture | verify | 0.315 [0.312–0.316] |
| 64 | chunked | unchanged | provision | branch | 0.450 [0.424–0.461] |
| 64 | chunked | unchanged | provision | start | 0.273 [0.259–0.275] |
| 64 | chunked | unchanged | provision | verify_independence | 1.155 [1.135–1.157] |
| 64 | chunked | unchanged | provision | worktree | 0.034 [0.033–0.034] |
| 64 | chunked | unchanged | restore | checkout_source | 0.013 [0.012–0.013] |
| 64 | chunked | unchanged | restore | reset | 5.528 [5.410–5.543] |
| 64 | chunked | unchanged | restore | start | 0.048 [0.046–0.048] |
| 64 | chunked | unchanged | restore | verify | 0.349 [0.344–0.359] |
| 64 | chunked | unchanged | restore_warm | checkout_source | 0.013 [0.012–0.013] |
| 64 | chunked | unchanged | restore_warm | reset | 0.844 [0.842–0.846] |
| 64 | chunked | unchanged | restore_warm | start | 0.049 [0.048–0.050] |
| 64 | chunked | unchanged | restore_warm | verify | 0.338 [0.336–0.353] |
| 64 | git | initial | capture | checkpoint | 6.893 [6.893–6.893] |
| 64 | git | initial | capture | git_commit | 0.389 [0.389–0.389] |
| 64 | git | initial | capture | verify | 0.341 [0.341–0.341] |
| 64 | git | initial | provision | branch | 0.626 [0.626–0.626] |
| 64 | git | initial | provision | start | 0.293 [0.293–0.293] |
| 64 | git | initial | provision | verify_independence | 1.892 [1.892–1.892] |
| 64 | git | initial | provision | worktree | 0.037 [0.037–0.037] |
| 64 | git | initial | restore | checkout_source | 0.012 [0.012–0.012] |
| 64 | git | initial | restore | reset | 5.790 [5.790–5.790] |
| 64 | git | initial | restore | start | 0.048 [0.048–0.048] |
| 64 | git | initial | restore | verify | 0.386 [0.386–0.386] |
| 64 | git | initial | restore_warm | checkout_source | 0.013 [0.013–0.013] |
| 64 | git | initial | restore_warm | reset | 0.926 [0.926–0.926] |
| 64 | git | initial | restore_warm | start | 0.045 [0.045–0.045] |
| 64 | git | initial | restore_warm | verify | 0.374 [0.374–0.374] |
| 64 | git | rows | capture | checkpoint | 3.438 [3.438–3.572] |
| 64 | git | rows | capture | git_commit | 0.065 [0.063–0.068] |
| 64 | git | rows | capture | verify | 0.338 [0.323–0.353] |
| 64 | git | rows | provision | branch | 0.555 [0.532–0.614] |
| 64 | git | rows | provision | start | 0.281 [0.270–0.295] |
| 64 | git | rows | provision | verify_independence | 1.343 [1.205–1.423] |
| 64 | git | rows | provision | worktree | 0.035 [0.033–0.036] |
| 64 | git | rows | restore | checkout_source | 0.013 [0.013–0.020] |
| 64 | git | rows | restore | reset | 5.613 [5.575–5.876] |
| 64 | git | rows | restore | start | 0.049 [0.049–0.053] |
| 64 | git | rows | restore | verify | 0.353 [0.346–0.399] |
| 64 | git | rows | restore_warm | checkout_source | 0.013 [0.013–0.015] |
| 64 | git | rows | restore_warm | reset | 0.918 [0.914–0.989] |
| 64 | git | rows | restore_warm | start | 0.048 [0.048–0.049] |
| 64 | git | rows | restore_warm | verify | 0.366 [0.339–0.398] |
| 64 | git | schema | capture | checkpoint | 2.071 [2.034–2.089] |
| 64 | git | schema | capture | git_commit | 0.067 [0.064–0.075] |
| 64 | git | schema | capture | verify | 0.337 [0.326–0.350] |
| 64 | git | schema | maintenance | forklift_cleanup | 1.911 [1.911–1.911] |
| 64 | git | schema | maintenance | forklift_gc | 5.719 [5.719–5.719] |
| 64 | git | schema | maintenance | git_gc | 0.086 [0.086–0.086] |
| 64 | git | schema | provision | branch | 0.538 [0.517–0.539] |
| 64 | git | schema | provision | start | 0.274 [0.261–0.276] |
| 64 | git | schema | provision | verify_independence | 1.225 [1.214–1.231] |
| 64 | git | schema | provision | worktree | 0.034 [0.029–0.034] |
| 64 | git | schema | restore | checkout_source | 0.013 [0.013–0.018] |
| 64 | git | schema | restore | reset | 5.803 [5.707–5.992] |
| 64 | git | schema | restore | start | 0.047 [0.045–0.048] |
| 64 | git | schema | restore | verify | 0.346 [0.346–0.358] |
| 64 | git | schema | restore_warm | checkout_source | 0.012 [0.012–0.012] |
| 64 | git | schema | restore_warm | reset | 0.930 [0.905–0.949] |
| 64 | git | schema | restore_warm | start | 0.048 [0.048–0.050] |
| 64 | git | schema | restore_warm | verify | 0.349 [0.347–0.358] |
| 64 | git | unchanged | capture | checkpoint | 1.902 [1.829–1.936] |
| 64 | git | unchanged | capture | git_commit | 0.050 [0.050–0.053] |
| 64 | git | unchanged | capture | verify | 0.336 [0.333–0.337] |
| 64 | git | unchanged | provision | branch | 0.632 [0.620–0.782] |
| 64 | git | unchanged | provision | start | 0.294 [0.262–0.495] |
| 64 | git | unchanged | provision | verify_independence | 1.689 [1.164–1.893] |
| 64 | git | unchanged | provision | worktree | 0.047 [0.041–0.059] |
| 64 | git | unchanged | restore | checkout_source | 0.015 [0.012–0.016] |
| 64 | git | unchanged | restore | reset | 6.135 [5.353–6.368] |
| 64 | git | unchanged | restore | start | 0.050 [0.047–0.053] |
| 64 | git | unchanged | restore | verify | 0.369 [0.367–0.538] |
| 64 | git | unchanged | restore_warm | checkout_source | 0.014 [0.013–0.017] |
| 64 | git | unchanged | restore_warm | reset | 1.119 [1.013–1.658] |
| 64 | git | unchanged | restore_warm | start | 0.051 [0.050–0.057] |
| 64 | git | unchanged | restore_warm | verify | 0.413 [0.376–0.449] |
| 256 | chunked | initial | capture | checkpoint | 5.562 [5.562–5.562] |
| 256 | chunked | initial | capture | git_commit | 0.421 [0.421–0.421] |
| 256 | chunked | initial | capture | verify | 0.948 [0.948–0.948] |
| 256 | chunked | initial | provision | branch | 0.689 [0.689–0.689] |
| 256 | chunked | initial | provision | start | 0.275 [0.275–0.275] |
| 256 | chunked | initial | provision | verify_independence | 3.275 [3.275–3.275] |
| 256 | chunked | initial | provision | worktree | 0.044 [0.044–0.044] |
| 256 | chunked | initial | restore | checkout_source | 0.014 [0.014–0.014] |
| 256 | chunked | initial | restore | reset | 6.990 [6.990–6.990] |
| 256 | chunked | initial | restore | start | 0.051 [0.051–0.051] |
| 256 | chunked | initial | restore | verify | 1.114 [1.114–1.114] |
| 256 | chunked | initial | restore_warm | checkout_source | 0.016 [0.016–0.016] |
| 256 | chunked | initial | restore_warm | reset | 1.418 [1.418–1.418] |
| 256 | chunked | initial | restore_warm | start | 0.050 [0.050–0.050] |
| 256 | chunked | initial | restore_warm | verify | 1.169 [1.169–1.169] |
| 256 | chunked | rows | capture | checkpoint | 2.335 [2.307–2.343] |
| 256 | chunked | rows | capture | git_commit | 0.066 [0.063–0.066] |
| 256 | chunked | rows | capture | verify | 0.976 [0.959–0.985] |
| 256 | chunked | rows | provision | branch | 0.642 [0.641–0.643] |
| 256 | chunked | rows | provision | start | 0.272 [0.271–0.273] |
| 256 | chunked | rows | provision | verify_independence | 3.209 [3.189–3.281] |
| 256 | chunked | rows | provision | worktree | 0.038 [0.038–0.039] |
| 256 | chunked | rows | restore | checkout_source | 0.013 [0.013–0.014] |
| 256 | chunked | rows | restore | reset | 6.782 [6.777–6.786] |
| 256 | chunked | rows | restore | start | 0.048 [0.047–0.049] |
| 256 | chunked | rows | restore | verify | 1.047 [1.001–1.059] |
| 256 | chunked | rows | restore_warm | checkout_source | 0.014 [0.014–0.015] |
| 256 | chunked | rows | restore_warm | reset | 1.101 [1.084–1.110] |
| 256 | chunked | rows | restore_warm | start | 0.049 [0.048–0.050] |
| 256 | chunked | rows | restore_warm | verify | 1.003 [0.998–1.010] |
| 256 | chunked | schema | capture | checkpoint | 2.453 [2.347–2.487] |
| 256 | chunked | schema | capture | git_commit | 0.070 [0.068–0.080] |
| 256 | chunked | schema | capture | verify | 1.047 [1.001–1.128] |
| 256 | chunked | schema | maintenance | forklift_cleanup | 2.221 [2.221–2.221] |
| 256 | chunked | schema | maintenance | forklift_gc | 0.096 [0.096–0.096] |
| 256 | chunked | schema | maintenance | git_gc | 0.089 [0.089–0.089] |
| 256 | chunked | schema | provision | branch | 0.645 [0.641–0.647] |
| 256 | chunked | schema | provision | start | 0.275 [0.265–0.276] |
| 256 | chunked | schema | provision | verify_independence | 3.288 [3.269–3.417] |
| 256 | chunked | schema | provision | worktree | 0.040 [0.038–0.044] |
| 256 | chunked | schema | restore | checkout_source | 0.013 [0.012–0.013] |
| 256 | chunked | schema | restore | reset | 6.823 [6.766–6.952] |
| 256 | chunked | schema | restore | start | 0.047 [0.047–0.048] |
| 256 | chunked | schema | restore | verify | 1.075 [1.073–1.100] |
| 256 | chunked | schema | restore_warm | checkout_source | 0.015 [0.014–0.015] |
| 256 | chunked | schema | restore_warm | reset | 1.191 [1.118–1.203] |
| 256 | chunked | schema | restore_warm | start | 0.048 [0.048–0.049] |
| 256 | chunked | schema | restore_warm | verify | 1.041 [1.037–1.116] |
| 256 | chunked | unchanged | capture | checkpoint | 2.314 [2.301–2.403] |
| 256 | chunked | unchanged | capture | git_commit | 0.052 [0.051–0.053] |
| 256 | chunked | unchanged | capture | verify | 0.984 [0.962–0.993] |
| 256 | chunked | unchanged | provision | branch | 0.655 [0.651–0.667] |
| 256 | chunked | unchanged | provision | start | 0.281 [0.278–0.289] |
| 256 | chunked | unchanged | provision | verify_independence | 3.216 [3.211–3.266] |
| 256 | chunked | unchanged | provision | worktree | 0.040 [0.037–0.042] |
| 256 | chunked | unchanged | restore | checkout_source | 0.014 [0.013–0.014] |
| 256 | chunked | unchanged | restore | reset | 6.961 [6.794–7.074] |
| 256 | chunked | unchanged | restore | start | 0.047 [0.046–0.047] |
| 256 | chunked | unchanged | restore | verify | 1.064 [1.027–1.065] |
| 256 | chunked | unchanged | restore_warm | checkout_source | 0.014 [0.013–0.014] |
| 256 | chunked | unchanged | restore_warm | reset | 1.103 [1.095–1.106] |
| 256 | chunked | unchanged | restore_warm | start | 0.048 [0.047–0.049] |
| 256 | chunked | unchanged | restore_warm | verify | 1.134 [1.025–1.220] |
| 256 | git | initial | capture | checkpoint | 18.796 [18.796–18.796] |
| 256 | git | initial | capture | git_commit | 0.395 [0.395–0.395] |
| 256 | git | initial | capture | verify | 1.031 [1.031–1.031] |
| 256 | git | initial | provision | branch | 0.740 [0.740–0.740] |
| 256 | git | initial | provision | start | 0.277 [0.277–0.277] |
| 256 | git | initial | provision | verify_independence | 3.188 [3.188–3.188] |
| 256 | git | initial | provision | worktree | 0.039 [0.039–0.039] |
| 256 | git | initial | restore | checkout_source | 0.013 [0.013–0.013] |
| 256 | git | initial | restore | reset | 6.961 [6.961–6.961] |
| 256 | git | initial | restore | start | 0.048 [0.048–0.048] |
| 256 | git | initial | restore | verify | 1.043 [1.043–1.043] |
| 256 | git | initial | restore_warm | checkout_source | 0.014 [0.014–0.014] |
| 256 | git | initial | restore_warm | reset | 1.184 [1.184–1.184] |
| 256 | git | initial | restore_warm | start | 0.048 [0.048–0.048] |
| 256 | git | initial | restore_warm | verify | 1.064 [1.064–1.064] |
| 256 | git | rows | capture | checkpoint | 10.106 [10.093–10.124] |
| 256 | git | rows | capture | git_commit | 0.068 [0.065–0.069] |
| 256 | git | rows | capture | verify | 1.019 [1.003–1.035] |
| 256 | git | rows | provision | branch | 0.753 [0.739–0.785] |
| 256 | git | rows | provision | start | 0.278 [0.274–0.278] |
| 256 | git | rows | provision | verify_independence | 3.232 [3.171–3.297] |
| 256 | git | rows | provision | worktree | 0.036 [0.036–0.038] |
| 256 | git | rows | restore | checkout_source | 0.013 [0.013–0.014] |
| 256 | git | rows | restore | reset | 7.253 [7.090–7.389] |
| 256 | git | rows | restore | start | 0.048 [0.048–0.050] |
| 256 | git | rows | restore | verify | 1.018 [1.009–1.041] |
| 256 | git | rows | restore_warm | checkout_source | 0.014 [0.013–0.014] |
| 256 | git | rows | restore_warm | reset | 1.193 [1.191–1.268] |
| 256 | git | rows | restore_warm | start | 0.050 [0.050–0.050] |
| 256 | git | rows | restore_warm | verify | 1.024 [0.997–1.038] |
| 256 | git | schema | capture | checkpoint | 4.289 [4.275–4.300] |
| 256 | git | schema | capture | git_commit | 0.069 [0.068–0.069] |
| 256 | git | schema | capture | verify | 0.980 [0.976–1.042] |
| 256 | git | schema | maintenance | forklift_cleanup | 2.121 [2.121–2.121] |
| 256 | git | schema | maintenance | forklift_gc | 20.433 [20.433–20.433] |
| 256 | git | schema | maintenance | git_gc | 0.088 [0.088–0.088] |
| 256 | git | schema | provision | branch | 0.742 [0.738–0.800] |
| 256 | git | schema | provision | start | 0.273 [0.269–0.282] |
| 256 | git | schema | provision | verify_independence | 3.251 [3.216–3.415] |
| 256 | git | schema | provision | worktree | 0.038 [0.037–0.039] |
| 256 | git | schema | restore | checkout_source | 0.013 [0.013–0.013] |
| 256 | git | schema | restore | reset | 7.069 [6.998–7.167] |
| 256 | git | schema | restore | start | 0.047 [0.047–0.048] |
| 256 | git | schema | restore | verify | 1.070 [1.013–1.075] |
| 256 | git | schema | restore_warm | checkout_source | 0.014 [0.014–0.014] |
| 256 | git | schema | restore_warm | reset | 1.203 [1.178–1.213] |
| 256 | git | schema | restore_warm | start | 0.048 [0.047–0.048] |
| 256 | git | schema | restore_warm | verify | 1.011 [1.003–1.075] |
| 256 | git | unchanged | capture | checkpoint | 4.042 [4.025–4.071] |
| 256 | git | unchanged | capture | git_commit | 0.051 [0.049–0.054] |
| 256 | git | unchanged | capture | verify | 0.978 [0.974–0.993] |
| 256 | git | unchanged | provision | branch | 0.742 [0.736–0.744] |
| 256 | git | unchanged | provision | start | 0.275 [0.269–0.277] |
| 256 | git | unchanged | provision | verify_independence | 3.239 [3.107–3.287] |
| 256 | git | unchanged | provision | worktree | 0.036 [0.036–0.037] |
| 256 | git | unchanged | restore | checkout_source | 0.012 [0.012–0.014] |
| 256 | git | unchanged | restore | reset | 7.084 [7.038–7.318] |
| 256 | git | unchanged | restore | start | 0.049 [0.046–0.049] |
| 256 | git | unchanged | restore | verify | 1.035 [1.009–1.046] |
| 256 | git | unchanged | restore_warm | checkout_source | 0.014 [0.014–0.014] |
| 256 | git | unchanged | restore_warm | reset | 1.194 [1.192–1.197] |
| 256 | git | unchanged | restore_warm | start | 0.047 [0.047–0.050] |
| 256 | git | unchanged | restore_warm | verify | 1.040 [1.039–1.049] |

## Storage accounting

MiB. Object history includes database snapshot objects/metadata plus application-source Git objects. `st_blocks*512` is allocated accounting, **not unique APFS physical usage**. Total includes old generations/caches until explicit maintenance.

| MiB | Backend | Point | Object history apparent | Total apparent | Total allocated accounting |
| ---: | --- | --- | ---: | ---: | ---: |
| 8 | chunked | with_second | 17.38 | 2731.47 | 2737.47 |
| 8 | chunked | before_maintenance | 17.38 | 2730.58 | 2736.54 |
| 8 | chunked | after_maintenance | 17.37 | 84.58 | 84.79 |
| 8 | git | with_second | 37.70 | 2751.79 | 2759.61 |
| 8 | git | before_maintenance | 37.70 | 2750.90 | 2758.67 |
| 8 | git | after_maintenance | 14.83 | 82.03 | 82.23 |
| 64 | git | with_second | 227.75 | 8766.27 | 8865.08 |
| 64 | git | before_maintenance | 227.75 | 8765.38 | 8864.15 |
| 64 | git | after_maintenance | 85.90 | 295.61 | 324.03 |
| 64 | chunked | with_second | 85.52 | 8624.04 | 8719.41 |
| 64 | chunked | before_maintenance | 85.52 | 8623.15 | 8718.48 |
| 64 | chunked | after_maintenance | 85.51 | 295.23 | 311.51 |
| 256 | chunked | with_second | 312.69 | 29011.06 | 29107.14 |
| 256 | chunked | before_maintenance | 312.69 | 29010.16 | 29106.21 |
| 256 | chunked | after_maintenance | 312.68 | 1015.62 | 1032.11 |
| 256 | git | with_second | 856.94 | 29555.31 | 29684.27 |
| 256 | git | before_maintenance | 856.94 | 29554.42 | 29683.34 |
| 256 | git | after_maintenance | 329.47 | 1032.48 | 1057.61 |

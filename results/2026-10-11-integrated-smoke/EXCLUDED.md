# Excluded preflight attempt

The first smoke invocation stopped before creating any database or timing samples because the harness assumed Homebrew Git existed. Forklift correctly used its /usr/bin/git fallback. The harness now uses the same executable-selection rule. See the verified smoke run instead.

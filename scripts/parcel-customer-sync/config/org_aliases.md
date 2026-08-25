# Org alias setup

This tool never hardcodes a default org. Every entrypoint requires an explicit
`--target-org <alias>` argument.

## Authenticate the sandbox (do this first, and validate fully before touching production)

```
sf org login web --alias pc-sync-sandbox --instance-url https://test.salesforce.com
```

## Authenticate production (only after the sandbox validation bar in the README is met)

```
sf org login web --alias pc-sync-prod --instance-url https://login.salesforce.com
```

Auth tokens are stored by the `sf` CLI itself in its own local encrypted store.
Nothing credential-related is ever written into this repo or handled directly
by the Python scripts.

## Naming convention

- `pc-sync-sandbox` — the only alias allowed with `--execute` until the
  production readiness bar in the README has been met.
- `pc-sync-prod` — production. Running with `--execute --target-org pc-sync-prod`
  additionally requires `--confirm-production` and a typed interactive
  confirmation of the org alias.

Adjust these alias names here and in your own `sf` config if your team uses a
different convention -- the scripts only care about the string passed to
`--target-org`, not this file.

# Security Policy

## Supported Versions

This project currently supports the `main` branch.

## Reporting a Vulnerability

Please do not open public issues for sensitive security bugs.

Instead:

1. Email the maintainer with a clear reproduction path.
2. Include affected endpoint(s), expected/actual behavior, and impact.
3. If possible, include suggested mitigations.

## Operational Security Checklist

- Keep `.env` private and out of git.
- Use strong `ADMIN_KEY` and rotate periodically.
- Restrict network exposure (VPN/Tailscale/reverse proxy + allowlist).
- Avoid exposing the gateway directly to the public internet.
- Monitor `/health/modem` and alert on repeated failures.

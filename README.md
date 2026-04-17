# SMSGate

[![CI](https://github.com/alikhalidsherif/smsgate/actions/workflows/ci.yml/badge.svg)](https://github.com/alikhalidsherif/smsgate/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Self-hosted SMS + USSD gateway for Huawei E5331/E5-series modems, designed as an adapter layer for **n8n automation hubs**.

It exposes a clean HTTP API for sending SMS, polling inbox, running USSD sessions, and basic modem ops while handling flaky modem HTTP behavior with retries, forced short-lived connections, and recovery-aware polling.

## Keywords

sms gateway, huawei e5331, huawei modem api, ethio telecom, n8n, automation, ussd, flask, docker, self-hosted

## Suggested GitHub Topics

`sms-gateway`, `huawei-modem`, `e5331`, `n8n`, `automation`, `ussd`, `docker`, `flask`, `sqlite`, `ethio-telecom`

## Features

- Send SMS (`/sms/send`) and track sent history (`/sms/sent`)
- Poll inbox from modem + persist into SQLite (`/sms/history`)
- Delivery report tagging heuristics
- Automated and live USSD session support
- Runtime config endpoint (`/config`)
- Modem health endpoint for monitoring (`/health/modem`)
- Modem cleanup worker (age + threshold based)
- Auth via `X-Admin-Key`
- Dockerized deployment

## Architecture

- **Gateway role**: thin modem abstraction layer and reliability wrapper
- **n8n role**: orchestration hub that calls gateway endpoints and routes events to automations
- **Storage**: SQLite for message history + runtime config

## Repository Layout

- `gateway.py` - API server + modem adapter logic
- `docker-compose.yml` - local container run setup
- `n8n-workflows/` - importable starter workflow JSONs
- `HANDOFF.md` - operational runbook and n8n cheatsheet
- `.env.example` - environment variable template

## Quick Start

1. Copy env template:

```bash
cp .env.example .env
```

2. Edit `.env` and set at minimum:

- `ADMIN_KEY`
- `ROUTER_PASS`

3. Start:

```bash
docker compose up -d --build
```

4. Check routes:

```bash
curl -s http://127.0.0.1:5000/routes
```

## Run n8n Locally (Optional)

If you want n8n in Docker on the same network as SMSGate:

```bash
docker compose up -d
docker compose -f docker-compose.n8n.yml up -d
```

Then open n8n at `http://127.0.0.1:5678`.

Because both services share `smsgate-net`, workflow URLs can use `http://smsgate:5000`.

## Security Notes

- Never commit `.env` to git.
- `X-Admin-Key` is required for protected endpoints.
- Rotate `ADMIN_KEY` if exposed.
- Run behind a reverse proxy/Tailscale/VPN for internet exposure.

## API Overview

Public:

- `GET /routes`

Protected (requires `X-Admin-Key`):

- `GET /config`
- `POST /config`
- `GET /health/modem`
- `GET /sms`
- `GET /sms/history`
- `GET /sms/unread/count`
- `GET /sms/<index>`
- `POST /sms/send`
- `GET /sms/sent`
- `POST /sms/mark-read/<index>`
- `DELETE /sms/<index>`
- `DELETE /sms/inbox/all`
- `POST /ussd/send`
- `POST /ussd/session`
- `GET /device/info`
- `POST /device/reboot`

WebSocket:

- `GET /ussd/live`

## Core Env Vars

See `.env.example` for full list.

- `APP_HOST`, `APP_PORT`
- `ADMIN_KEY`
- `DB_PATH`
- `ROUTER_URL`, `ROUTER_USER`, `ROUTER_PASS`
- `WEBHOOK_URL`
- `POLL_INTERVAL`, `CLEANUP_INTERVAL`
- `MODEM_CONNECT_TIMEOUT`, `MODEM_READ_TIMEOUT`
- `MODEM_CONNECT_RETRIES`, `MODEM_RETRY_BACKOFF`
- `MODEM_FORCE_CONNECTION_CLOSE`
- `POLL_BACKOFF_MAX`, `POLL_ERROR_LOG_THROTTLE`

## n8n Starter Flows (Concept)

### 1) Scheduled modem health check (recommended first flow)

- Trigger: every 5 minutes
- HTTP Request: `GET /health/modem`
- IF: `consecutive_failures > 2` or `status == "degraded"`
- Action: notify Telegram/Email/SMS

### 2) Send SMS automation

- Trigger: webhook/cron/db event
- HTTP Request: `POST /sms/send`
- Body: `{"to":"+2519...","message":"..."}`

### 3) Inbox sync automation

- Trigger: every 1-5 minutes
- HTTP Request: `GET /sms/history?page=1&limit=50`
- Store or route to CRM/Sheets/ERP

## Ready-to-import n8n Workflows

Prebuilt example workflows are included in `n8n-workflows/`:

- `n8n-workflows/01-health-monitor-alert.json`
- `n8n-workflows/02-send-sms-webhook.json`
- `n8n-workflows/03-inbox-sync.json`

Import steps in n8n:

1. Open n8n -> Workflows -> Import from File
2. Select a JSON from `n8n-workflows/`
3. Update all `CHANGE_ME_ADMIN_KEY` placeholders
4. Update phone placeholders (`+2519XXXXXXXX`)
5. If n8n runs outside Docker, replace `http://smsgate:5000` with your reachable host URL
6. Save and activate

Tip: start with `01-health-monitor-alert.json`, confirm alerts work, then import the other two.

## Health Endpoint

`GET /health/modem` returns a snapshot including:

- `status` (`healthy`/`degraded`)
- `consecutive_failures`
- `total_failures`
- `recoveries`
- `last_poll_success_at`
- `last_poll_error_at`
- `last_poll_error`
- `last_backoff_seconds`
- `last_sms_received_at`

This endpoint is intended for n8n monitoring and alert thresholds.

## Development

Run syntax check:

```bash
uv run python -m py_compile gateway.py
```

Run container logs:

```bash
docker logs -f smsgate
```

Run local smoke checks:

```bash
curl -s http://127.0.0.1:5000/routes
curl -s -H "X-Admin-Key: $ADMIN_KEY" http://127.0.0.1:5000/health/modem
```

## Helper Scripts

- `discover.py` probes modem API capabilities
- `setup.py` network mode helper
- `introspect.py` prints available `huawei-lte-api` classes/methods

All helpers read modem credentials from environment variables.

## Roadmap Ideas

- FastAPI migration (`/v2`) with typed schemas + OpenAPI
- Optional auto-reboot with cooldown + explicit feature flag
- Prometheus metrics endpoint
- Rate limiting and request audit logs

## License

MIT. See `LICENSE`.

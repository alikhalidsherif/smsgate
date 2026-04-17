# SMSGate

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![CI](https://github.com/alikhalidsherif/smsgate/actions/workflows/ci.yml/badge.svg)](https://github.com/alikhalidsherif/smsgate/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Docker Ready](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![Last commit](https://img.shields.io/github/last-commit/alikhalidsherif/smsgate)](https://github.com/alikhalidsherif/smsgate/commits/main)

SMSGate is a self-hosted SMS + USSD gateway for Huawei E5331/E5-series modems.

Use it as an adapter layer: **n8n orchestrates your automations**, and SMSGate handles the modem-level work (SMS, USSD, retries, polling, persistence, and health telemetry).

## What you get

- SMS send API with delivery-report-aware history
- Live modem inbox reads and persistent SQLite history
- Three USSD modes: single-shot, automated multi-step, and live WebSocket
- Background polling and cleanup workers
- Webhook push for inbound SMS and delivery reports
- Modem health endpoint for monitoring and alerting

## 10-minute quick start

1) Clone and configure:

```bash
git clone https://github.com/alikhalidsherif/smsgate.git
cd smsgate
cp .env.example .env
```

2) Edit `.env` and set at least:

- `ADMIN_KEY`
- `ROUTER_PASS`

3) Start SMSGate:

```bash
docker compose up -d --build
```

4) Smoke test:

```bash
curl -s http://127.0.0.1:5000/routes
curl -s -H "X-Admin-Key: <ADMIN_KEY>" http://127.0.0.1:5000/health/modem
```

Optional: run n8n on the same Docker network:

```bash
docker compose -f docker-compose.n8n.yml up -d
```

Open n8n at `http://127.0.0.1:5678`.

## Architecture and data model

- **`/sms`** reads directly from the modem storage (live view, limited by modem inbox/sent slots).
- **`/sms/history`** reads from SQLite (`DB_PATH`) and represents your long-term history.

In practice:

- Use `/sms` when you need current modem state.
- Use `/sms/history` for reporting, automation, auditing, and searchable historical data.

## Webhooks

Set `WEBHOOK_URL` to receive inbound events from SMSGate.

SMSGate sends an HTTP `POST` when the poller processes a new unread modem message:

- `type: "sms_received"` for regular inbound messages
- `type: "delivery_report"` when the message matches delivery-report heuristic rules

### Payload shape

```json
{
  "type": "sms_received",
  "id": 101,
  "phone": "+251911234567",
  "content": "hello from customer",
  "date": "2026-04-17 09:30:45",
  "sms_type": "1"
}
```

```json
{
  "type": "delivery_report",
  "id": 102,
  "phone": "994",
  "content": "Delivered to +251911234567",
  "date": "2026-04-17 09:31:05",
  "sms_type": "7"
}
```

### n8n setup for webhook receive

1. In n8n, add a **Webhook** node (method `POST`).
2. Copy its production URL.
3. Set that URL as `WEBHOOK_URL` (in `.env` or `POST /config`).
4. Activate workflow; SMSGate will `POST` each new event payload to n8n.

## Delivery report heuristics

Delivery reports are classified in code using a heuristic:

- Content contains one of: `delivered`, `not delivered`, `delivery`, `failed to deliver`
- Sender address length is `<= 10` characters (typically short code / service sender)

This is intentionally heuristic and may need adjustment for your carrier format.

## USSD modes

SMSGate supports three USSD interaction models.

### 1) Single-shot USSD (`POST /ussd/send`)

Best for one request/one response interactions.

Request:

```bash
curl -s -X POST http://127.0.0.1:5000/ussd/send \
  -H "X-Admin-Key: <ADMIN_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"code":"*804#"}'
```

Success response:

```json
{
  "code": "*804#",
  "response": "Your menu text..."
}
```

Busy/timeout/error examples:

- `423` if another USSD session is active
- `504` if no network response before timeout
- `502` for modem/API errors

### 2) Automated multi-step USSD (`POST /ussd/session`)

Provide an ordered `steps` array. SMSGate sends each step and waits for response before next.

Ethio Telecom style example (`*804#` then choose option `1`):

```bash
curl -s -X POST http://127.0.0.1:5000/ussd/session \
  -H "X-Admin-Key: <ADMIN_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"steps":["*804#","1"]}'
```

Example response:

```json
{
  "steps_run": 2,
  "history": [
    {
      "step": 1,
      "input": "*804#",
      "response": "Welcome ..."
    },
    {
      "step": 2,
      "input": "1",
      "response": "Your balance is ..."
    }
  ]
}
```

### 3) Live turn-based USSD over WebSocket (`GET /ussd/live`)

Use this when menus branch dynamically and you want interactive control.

Server behavior:

- Only one active USSD session at a time
- New connection receives `{"status":"ready"}`
- If busy: `{"status":"busy","error":"Another USSD session is active"}`
- Idle timeout is 120 seconds -> server sends timeout message and ends session
- On disconnect/error, lock is released automatically

Client message formats:

- `{"code":"*804#"}` start session
- `{"input":"1"}` reply to menu
- `{"action":"ping"}` keepalive
- `{"action":"cancel"}` cancel session

Server message formats:

- `{"status":"ready"}`
- `{"menu":"..."}`
- `{"status":"pong"}`
- `{"status":"cancelled"}`
- `{"status":"timeout","error":"..."}`
- `{"error":"..."}`

`wscat` example:

```bash
wscat -c ws://127.0.0.1:5000/ussd/live
```

Example exchange:

```text
< {"status":"ready","message":"Send {\"code\": \"*XXX#\"} to begin"}
> {"code":"*804#"}
< {"menu":"Welcome ..."}
> {"input":"1"}
< {"menu":"Your balance is ..."}
> {"action":"cancel"}
< {"status":"cancelled"}
```

Minimal Python WebSocket client example:

```python
import asyncio
import json
import websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:5000/ussd/live") as ws:
        print(await ws.recv())
        await ws.send(json.dumps({"code": "*804#"}))
        print(await ws.recv())
        await ws.send(json.dumps({"input": "1"}))
        print(await ws.recv())
        await ws.send(json.dumps({"action": "cancel"}))
        print(await ws.recv())

asyncio.run(main())
```

## Modem reboot endpoint (`POST /device/reboot`)

Use this only for recovery operations. It requires explicit confirmation header and will make modem unreachable briefly.

```bash
curl -s -X POST http://127.0.0.1:5000/device/reboot \
  -H "X-Admin-Key: <ADMIN_KEY>" \
  -H "X-Confirm: yes"
```

Response:

```json
{
  "result": "OK",
  "note": "Modem will be unreachable for ~30 seconds"
}
```

## Cleanup worker

The cleanup worker runs in the background and protects modem storage.

What it does each cycle:

1. Reads modem messages
2. Stores messages into SQLite first
3. Deletes from modem by age and/or overflow thresholds

Controls:

- `CLEANUP_INTERVAL` -> run frequency (seconds)
- `MODEM_MESSAGE_MAX_AGE` -> delete modem messages older than N days
- `MODEM_MAX_THRESHOLD` -> if modem still over limit, delete oldest until under threshold

## API reference

### Public endpoints

- `GET /routes` - endpoint discovery
- `GET /ussd/live` - WebSocket USSD (no header auth; secure by network boundary)

### Protected endpoints (require `X-Admin-Key`)

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

## POST endpoint examples (request + response)

### `POST /config`

Request:

```bash
curl -s -X POST http://127.0.0.1:5000/config \
  -H "X-Admin-Key: <ADMIN_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"poll_interval":20,"webhook_url":"http://n8n:5678/webhook/smsgate-events"}'
```

Response:

```json
{
  "updated": {
    "poll_interval": 20,
    "webhook_url": "http://n8n:5678/webhook/smsgate-events"
  },
  "errors": {}
}
```

### `POST /sms/send`

Request:

```bash
curl -s -X POST http://127.0.0.1:5000/sms/send \
  -H "X-Admin-Key: <ADMIN_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"to":"+2519XXXXXXXX","message":"hello from smsgate","delivery_report":true}'
```

Response:

```json
{
  "result": "OK",
  "to": ["+2519XXXXXXXX"],
  "message": "hello from smsgate",
  "delivery_report": true
}
```

### `POST /sms/mark-read/<index>`

Request:

```bash
curl -s -X POST http://127.0.0.1:5000/sms/mark-read/20052 \
  -H "X-Admin-Key: <ADMIN_KEY>"
```

Response:

```json
{
  "index": 20052,
  "result": "OK"
}
```

### `POST /ussd/send`

Request:

```bash
curl -s -X POST http://127.0.0.1:5000/ussd/send \
  -H "X-Admin-Key: <ADMIN_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"code":"*804#"}'
```

Response:

```json
{
  "code": "*804#",
  "response": "Your menu text..."
}
```

### `POST /ussd/session`

Request:

```bash
curl -s -X POST http://127.0.0.1:5000/ussd/session \
  -H "X-Admin-Key: <ADMIN_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"steps":["*804#","1"]}'
```

Response:

```json
{
  "steps_run": 2,
  "history": [
    {
      "step": 1,
      "input": "*804#",
      "response": "Welcome ..."
    },
    {
      "step": 2,
      "input": "1",
      "response": "Your balance is ..."
    }
  ]
}
```

### `POST /device/reboot`

Request:

```bash
curl -s -X POST http://127.0.0.1:5000/device/reboot \
  -H "X-Admin-Key: <ADMIN_KEY>" \
  -H "X-Confirm: yes"
```

Response:

```json
{
  "result": "OK",
  "note": "Modem will be unreachable for ~30 seconds"
}
```

## n8n workflow JSONs (included)

Starter files in `n8n-workflows/`:

- `01-health-monitor-alert.json`
- `02-send-sms-webhook.json`
- `03-inbox-sync.json`

Import steps:

1. n8n UI -> Workflows -> Import from File
2. Pick one JSON file
3. Replace `CHANGE_ME_ADMIN_KEY`
4. Replace phone placeholders (`+2519XXXXXXXX`)
5. If n8n is outside Docker network, replace `http://smsgate:5000` with reachable host URL
6. Save, run once manually, then activate

## n8n troubleshooting

- **401 Unauthorized**: request is missing `X-Admin-Key` or key is wrong. Add the header to every protected endpoint request and ensure it matches `ADMIN_KEY`.
- **423 Busy**: another USSD session is already active (`/ussd/send`, `/ussd/session`, and `/ussd/live` share the same modem session lock). Wait for current session to finish, or cancel the active live WebSocket session.
- **Connection timeout from n8n to gateway**: `http://smsgate:5000` only works when both containers are on `smsgate-net`. If n8n runs outside Docker (or different network), use host-reachable URL instead, e.g. `http://<host-ip>:5000`.
- **Poller backoff looks like "slow polling"**: if `/health/modem` shows `consecutive_failures > 0`, SMS poll interval is temporarily increased by backoff. This is expected resilience behavior, not a gateway crash. Track `last_backoff_seconds`, `last_poll_success_at`, and `status` for recovery.

## Environment variables

Required:

- `ADMIN_KEY`
- `ROUTER_PASS`

Commonly tuned:

- `APP_HOST`, `APP_PORT`
- `DB_PATH`
- `ROUTER_URL`, `ROUTER_USER`
- `WEBHOOK_URL`
- `POLL_INTERVAL`, `CLEANUP_INTERVAL`
- `MODEM_MAX_THRESHOLD`, `MODEM_MESSAGE_MAX_AGE`
- `MODEM_CONNECT_TIMEOUT`, `MODEM_READ_TIMEOUT`
- `MODEM_CONNECT_RETRIES`, `MODEM_RETRY_BACKOFF`
- `MODEM_FORCE_CONNECTION_CLOSE`
- `POLL_BACKOFF_MAX`, `POLL_ERROR_LOG_THROTTLE`

See `.env.example` for full defaults.

## Development and operations

Syntax check:

```bash
uv run python -m py_compile gateway.py discover.py setup.py introspect.py
```

Build/run:

```bash
docker compose up -d --build
```

The container runs with Gunicorn (`wsgi:app`) instead of Flask dev server.

Logs:

```bash
docker logs -f smsgate
```

## Security notes

- Never commit `.env`
- Rotate `ADMIN_KEY` if exposed
- Keep gateway private (VPN/Tailscale/reverse proxy)
- Treat modem management endpoints as privileged operations

## License

MIT. See `LICENSE`.

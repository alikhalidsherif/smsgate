# Handoff + n8n Cheatsheet

This is the practical "what to do next" file for running SMSGate and connecting it to n8n.

## 1) First-time local setup

```bash
cp .env.example .env
```

Edit `.env` and set:

- `ADMIN_KEY`
- `ROUTER_PASS`

Then start:

```bash
docker compose up -d --build
```

Check:

```bash
curl -s http://127.0.0.1:5000/routes
```

Optional: run n8n in Docker on same network:

```bash
docker compose -f docker-compose.n8n.yml up -d
```

Open `http://127.0.0.1:5678`.

## 2) Most useful endpoints for n8n

Base URL: `http://<host>:5000`

Headers for protected routes:

- `X-Admin-Key: <ADMIN_KEY>`
- `Content-Type: application/json` (for POST JSON)

Core endpoints:

- `GET /health/modem` -> monitor reliability
- `POST /sms/send` -> send SMS
- `GET /sms/history` -> pull received history
- `GET /sms/unread/count` -> quick modem counts
- `POST /ussd/send` -> run one USSD code
- `POST /ussd/session` -> run USSD menus

## 3) n8n beginner flow (health alert)

Create workflow:

1. **Schedule Trigger** (every 5 min)
2. **HTTP Request**
   - Method: `GET`
   - URL: `http://smsgate:5000/health/modem` (if same Docker network) or host URL
   - Header: `X-Admin-Key`
3. **IF Node**
   - Condition: `{{$json["consecutive_failures"] > 2}}`
4. **HTTP Request** (send alert SMS)
   - Method: `POST`
   - URL: `http://smsgate:5000/sms/send`
   - Header: `X-Admin-Key`
   - JSON body:

```json
{
  "to": "+2519XXXXXXXX",
  "message": "SMSGate degraded: {{$json[\"consecutive_failures\"]}} consecutive failures. Last error: {{$json[\"last_poll_error\"]}}"
}
```

Shortcut: import `n8n-workflows/01-health-monitor-alert.json` and replace placeholders.

## 4) n8n beginner flow (send business SMS)

1. Trigger from webhook/form/db
2. HTTP Request to `POST /sms/send`
3. Body:

```json
{
  "to": "+2519XXXXXXXX",
  "message": "Your automation message"
}
```

Shortcut: import `n8n-workflows/02-send-sms-webhook.json`.

Expected input payload to webhook (example):

```json
{
  "to": "+2519XXXXXXXX",
  "message": "hello from n8n",
  "delivery_report": true
}
```

## 5) n8n beginner flow (inbox sync)

1. Schedule trigger (every 1-5 min)
2. HTTP Request: `GET /sms/history?page=1&limit=50`
3. Push to Sheet/DB/CRM
4. Optional dedupe by message `id`

Shortcut: import `n8n-workflows/03-inbox-sync.json`.

It keeps `lastSeenId` in n8n static workflow storage and only emits new messages.

## 5.1) How to import workflow JSON files

1. Open n8n UI
2. Go to Workflows -> Import from File
3. Pick a file from `n8n-workflows/`
4. Replace `CHANGE_ME_ADMIN_KEY`
5. Replace `+2519XXXXXXXX` placeholders
6. If n8n is not in same Docker network, replace `http://smsgate:5000`
7. Save and Activate

## 5.2) Suggested first 30-minute n8n learning path

1. Import `01-health-monitor-alert.json`
2. Set your real `X-Admin-Key`
3. Trigger it manually once (Execute Workflow)
4. Confirm alert branch logic in execution view
5. Activate it and watch runs for 10-15 minutes
6. Import `02-send-sms-webhook.json`
7. Send test payload via curl/Postman to n8n webhook URL

## 6) Runtime tuning

Update without restart:

```bash
curl -s -X POST http://127.0.0.1:5000/config \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"poll_interval":20}'
```

Useful knobs:

- `poll_interval`: 10-30 sec usually safe
- `cleanup_interval`: how often modem cleanup runs
- `modem_max_threshold`: max modem inbox count target
- `modem_message_max_age`: retention on modem (DB keeps history)

## 7) Operations checklist

- Logs: `docker logs -f smsgate`
- Rebuild after code change: `docker compose up -d --build`
- Health: `GET /health/modem`
- Routes: `GET /routes`

## 8) Known behavior

- Occasional modem `ConnectionResetError` can happen.
- Gateway uses retry + forced short-lived modem HTTP sessions + backoff.
- Recovery messages in logs are expected under weak RF/network periods.

## 9) Before publishing repo

- Keep `.env` out of git
- Replace all real secrets
- Add your screenshots and usage examples
- Add license and contribution notes (optional)

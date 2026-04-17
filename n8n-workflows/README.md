# n8n Workflows

These starter workflows are designed for beginners and assume SMSGate is reachable at `http://smsgate:5000`.

Files:

- `01-health-monitor-alert.json` - schedules health checks and sends SMS alerts when degraded
- `02-send-sms-webhook.json` - exposes an n8n webhook that forwards SMS send requests to SMSGate
- `03-inbox-sync.json` - polls `/sms/history` and emits only new messages using `lastSeenId`

## Before activating

For each workflow:

1. Replace `CHANGE_ME_ADMIN_KEY`
2. Replace `+2519XXXXXXXX`
3. If needed, replace `http://smsgate:5000` with your host URL

## Import guide

1. n8n UI -> Workflows -> Import from File
2. Select the JSON file
3. Save
4. Execute once manually
5. Activate when output looks correct

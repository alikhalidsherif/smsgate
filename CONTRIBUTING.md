# Contributing

Thanks for contributing to SMSGate.

## Quick Dev Setup

1. Copy env file:

```bash
cp .env.example .env
```

2. Start service:

```bash
docker compose up -d --build
```

3. Run syntax checks before committing:

```bash
uv run python -m py_compile gateway.py discover.py setup.py introspect.py
```

## Pull Request Guidelines

- Keep changes focused and small where possible.
- Update docs when behavior/config changes.
- Do not commit secrets or `.env` files.
- Include a short test note in PR description.

## Commit Messages

Use concise messages describing intent, for example:

- `add modem health endpoint for n8n alerts`
- `harden poller with transient error backoff`

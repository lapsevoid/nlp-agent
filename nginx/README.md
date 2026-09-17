# Nova Nginx deployment

The deployed architecture uses two Nginx layers with separate jobs:

```text
Internet :80/:443
  -> host Nginx (TLS and domain routing)
     -> 127.0.0.1:8765  -> production Compose Nginx -> nova-web:8765
     -> 127.0.0.1:18765 -> test Compose Nginx       -> nova-web:8765
```

- `host-lsnunlp.conf.example` is the host template for `lsnunlp.com` and
  `test.lsnunlp.com`. It is installed manually and never receives certificate
  material from CI.
- `nginx.conf` stays inside each Compose project. It routes the SPA, `/api/`,
  and `/ws/` to Nova Web while preserving the original HTTPS scheme and client
  IP supplied by the host proxy.
- MySQL and Redis have no published ports. Web and Monitor ports default to
  `127.0.0.1`, so only processes on the server can reach them directly.

The complete certificate, environment, deployment, validation, and rollback
checklist is in [`docs/production-domain-deployment.md`](../docs/production-domain-deployment.md).

## Local HTTP bootstrap

For local development, copy `.env-example` to `.env`, retain the loopback bind,
and start the stack:

```powershell
docker compose up -d --build
docker compose ps
curl.exe -i http://127.0.0.1/health/live
curl.exe -i http://127.0.0.1/health/ready
```

## Sandbox artifact origin

Sandbox HTML artifacts require a hostname distinct from the application origin.
`artifact-origin.conf.example` remains a separate deployment template. Set
`NLP_AGENT_SANDBOX_ARTIFACT_ORIGIN` to that HTTPS origin and
`NLP_AGENT_SANDBOX_APPLICATION_ORIGIN` to the Nova HTTPS origin. The artifact
proxy must strip cookies and expose only `/api/v1/sandbox/artifacts/`.

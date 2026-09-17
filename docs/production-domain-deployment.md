# Production domain and HTTPS deployment

This repository prepares the configuration only. DNS changes, certificate
installation, host Nginx changes, and production startup are manual server
operations.

## Target topology

```text
Internet
  -> host Nginx :80/:443
     -> lsnunlp.com      -> 127.0.0.1:8765  -> nova-prod Compose
     -> test.lsnunlp.com -> 127.0.0.1:18765 -> nova-test Compose
```

The Compose Nginx remains the private entry for each environment. MySQL and
Redis are reachable only on their own Compose network. The fixed project names
`nova-prod` and `nova-test` create different networks and named volumes, even
though both deployments use the same `compose.yaml` volume keys.

## Server checklist

### A. Create directories

Run these commands on the Linux server with suitable privileges:

```bash
sudo install -d -m 755 /etc/nginx/ssl/lsnunlp.com
sudo install -d -m 755 /etc/nginx/ssl/test.lsnunlp.com
sudo install -d -m 755 /opt/nova-prod /opt/nova-test
```

Keep the repository checkout in the location used by the deployment runner.
`/opt/nova-prod` and `/opt/nova-test` hold only their server-local `.env` files.

### B. Copy certificate files

Copy the certificate files supplied by the CA to these exact template paths:

```text
/etc/nginx/ssl/lsnunlp.com/fullchain.pem
/etc/nginx/ssl/lsnunlp.com/privkey.pem
/etc/nginx/ssl/test.lsnunlp.com/fullchain.pem
/etc/nginx/ssl/test.lsnunlp.com/privkey.pem
```

If the CA uses different filenames, either rename the copies or edit the host
template after confirming the real names. Do not commit certificates or private
keys. Restrict private-key permissions to root and the host Nginx process.

### C. Install the host Nginx configuration

Copy [`nginx/host-lsnunlp.conf.example`](../nginx/host-lsnunlp.conf.example) to:

```text
/etc/nginx/conf.d/lsnunlp.conf
```

First confirm that the host `/etc/nginx/nginx.conf` includes
`/etc/nginx/conf.d/*.conf`. If the distribution uses `sites-available` and
`sites-enabled`, install and enable the same file there instead. Only the host
Nginx listens on public ports 80 and 443; do not publish either Compose Nginx on
`0.0.0.0`.

### D. Validate Nginx

```bash
sudo nginx -t
```

Do not reload if validation fails. Common causes are missing certificate files,
an already-defined upgrade map, or a conflicting `server_name` block. If the
host already defines `$connection_upgrade`, remove the duplicate map from this
template and reuse the existing one.

### E. Reload Nginx

```bash
sudo systemctl reload nginx
sudo systemctl status nginx --no-pager
```

### F. Prepare production and test environment files

```bash
cp deploy/env/production.env.example /opt/nova-prod/.env
cp deploy/env/test.env.example /opt/nova-test/.env
chmod 600 /opt/nova-prod/.env /opt/nova-test/.env
```

Fill every `REPLACE_WITH_...` value separately for each environment. In
production, verify at minimum:

- `COMPOSE_PROJECT_NAME=nova-prod`
- `NOVA_WEB_BIND_ADDRESS=127.0.0.1` and `NOVA_WEB_HOST_PORT=8765`
- `NLP_AGENT_WEB_ALLOWED_HOSTS=localhost,127.0.0.1,lsnunlp.com`
- `NLP_AGENT_WEB_ALLOWED_ORIGINS=https://lsnunlp.com`
- `NLP_AGENT_AUTH_COOKIE_SECURE=true`
- production-only `NLP_AGENT_MYSQL_*` values and `NLP_AGENT_DATABASE_URL`
- a production-only `NLP_AGENT_WEB_SECRET` and model/API credentials
- `NLP_AGENT_REDIS_URL=redis://redis:6379/0`; `nova-prod` resolves it to the
  production Redis container on the production network
- Tencent SMS values only if real SMS verification is enabled

The current application has no Email/Tencent SES Settings fields, so no SES
variables should be invented. Add them only when the corresponding application
integration exists. Never place a real secret in an example file or workflow.

For test, keep `COMPOSE_PROJECT_NAME=nova-test`, port `18765`, the
`test.lsnunlp.com` host/origin, and entirely separate MySQL credentials.

### G. Start production Compose

From the repository checkout used for the release, run:

```bash
NOVA_ENV_FILE=/opt/nova-prod/.env docker compose --profile monitor \
  -p nova-prod --env-file /opt/nova-prod/.env -f compose.yaml \
  up -d --pull always --remove-orphans
```

The release workflow performs the equivalent operation with immutable image
digests. It reads `/opt/nova-prod/.env`, forces project name `nova-prod`, and
never reads or writes TLS private keys. Do not run production with the test env
file or project name.

### H. Verify redirects, TLS, routes, and health

```bash
curl -I http://lsnunlp.com
curl -I https://lsnunlp.com
curl -I https://test.lsnunlp.com
curl -fsS https://lsnunlp.com/health/live
curl -fsS https://lsnunlp.com/health/ready
curl -fsS https://test.lsnunlp.com/health/live
curl -fsS https://test.lsnunlp.com/health/ready
```

The HTTP request should return `301` with an HTTPS `Location`. HTTPS and live
health should return success; ready should report ready only after MySQL, Redis,
and migrations are available. Also sign in and send a chat message in both
domains to exercise `/api/` and `/ws/` through both proxy layers.

Confirm the published sockets are loopback-only:

```bash
sudo ss -lntp | grep -E ':(80|443|8765|18765|8766|18766)\b'
```

Only host Nginx should bind public `:80` and `:443`; Compose ports should show
`127.0.0.1`.

### I. Confirm test and production volume isolation

```bash
docker volume ls --filter label=com.docker.compose.project=nova-prod
docker volume ls --filter label=com.docker.compose.project=nova-test
docker network ls --filter label=com.docker.compose.project=nova-prod
docker network ls --filter label=com.docker.compose.project=nova-test
docker compose -p nova-prod --env-file /opt/nova-prod/.env -f compose.yaml config
docker compose -p nova-test --env-file /opt/nova-test/.env -f compose.yaml config
```

Expected volume names are prefixed separately, for example
`nova-prod_mysql-data` versus `nova-test_mysql-data` and
`nova-prod_nova-data` versus `nova-test_nova-data`. If an old deployment used a
different project name, do not attach its volumes to production. Back it up and
migrate data explicitly if needed; never rename or reuse a test database volume
as a production volume.

## Repository safeguards

- `.env`, `*.pem`, `*.key`, and `*.p12` remain ignored by Git.
- Production and test workflows use `nova-prod` and `nova-test` respectively.
- Both workflows force published application ports to `127.0.0.1`.
- Host certificates are outside Docker Compose and outside GitHub Actions.
- The ICP record is rendered by the lightweight home-page bottom bar and links
  to the Ministry of Industry and Information Technology filing system.

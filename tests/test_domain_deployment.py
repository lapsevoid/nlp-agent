from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
HOST_NGINX = ROOT / "nginx" / "host-lsnunlp.conf.example"
PRODUCTION_ENV = ROOT / "deploy" / "env" / "production.env.example"
TEST_ENV = ROOT / "deploy" / "env" / "test.env.example"


def test_compose_edges_bind_only_to_loopback() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert compose["services"]["nginx"]["ports"] == [
        "${NOVA_WEB_BIND_ADDRESS:-127.0.0.1}:${NOVA_WEB_HOST_PORT:-${NOVA_NGINX_HTTP_PORT:-80}}:80"
    ]
    assert compose["services"]["nova-monitor"]["ports"] == [
        "${NOVA_MONITOR_BIND_ADDRESS:-127.0.0.1}:${NOVA_MONITOR_HOST_PORT:-8766}:8766"
    ]
    assert "ports" not in compose["services"]["mysql"]
    assert "ports" not in compose["services"]["redis"]


def test_environment_templates_separate_domains_projects_ports_and_data() -> None:
    production = PRODUCTION_ENV.read_text(encoding="utf-8")
    test = TEST_ENV.read_text(encoding="utf-8")

    assert 'COMPOSE_PROJECT_NAME="nova-prod"' in production
    assert 'COMPOSE_PROJECT_NAME="nova-test"' in test
    assert 'NOVA_WEB_BIND_ADDRESS="127.0.0.1"' in production
    assert 'NOVA_WEB_BIND_ADDRESS="127.0.0.1"' in test
    assert 'NOVA_WEB_HOST_PORT="8765"' in production
    assert 'NOVA_WEB_HOST_PORT="18765"' in test
    assert 'NOVA_MONITOR_BIND_ADDRESS="127.0.0.1"' in production
    assert 'NOVA_MONITOR_BIND_ADDRESS="127.0.0.1"' in test

    assert 'NLP_AGENT_MYSQL_DATABASE="nlp_agent_prod"' in production
    assert 'NLP_AGENT_MYSQL_DATABASE="nlp_agent_test"' in test
    assert "nlp_agent_test" not in production
    assert "nlp_agent_prod" not in test

    assert 'NLP_AGENT_WEB_ALLOWED_HOSTS="localhost,127.0.0.1,lsnunlp.com"' in production
    assert 'NLP_AGENT_WEB_ALLOWED_ORIGINS="https://lsnunlp.com"' in production
    assert "NLP_AGENT_AUTH_COOKIE_SECURE=true" in production
    assert 'NLP_AGENT_WEB_ALLOWED_HOSTS="localhost,127.0.0.1,test.lsnunlp.com"' in test
    assert 'NLP_AGENT_WEB_ALLOWED_ORIGINS="https://test.lsnunlp.com"' in test
    assert "NLP_AGENT_AUTH_COOKIE_SECURE=true" in test


def test_host_nginx_terminates_tls_and_routes_each_domain() -> None:
    nginx = HOST_NGINX.read_text(encoding="utf-8")

    assert "server 127.0.0.1:8765;" in nginx
    assert "server 127.0.0.1:18765;" in nginx
    assert "server_name lsnunlp.com;" in nginx
    assert "server_name test.lsnunlp.com;" in nginx
    assert nginx.count("return 301 https://$host$request_uri;") == 2
    assert "/etc/nginx/ssl/lsnunlp.com/fullchain.pem" in nginx
    assert "/etc/nginx/ssl/lsnunlp.com/privkey.pem" in nginx
    assert "/etc/nginx/ssl/test.lsnunlp.com/fullchain.pem" in nginx
    assert "/etc/nginx/ssl/test.lsnunlp.com/privkey.pem" in nginx
    assert nginx.count("ssl_protocols TLSv1.2 TLSv1.3;") == 2

    for location in ("location /api/", "location /ws/", "location / {"):
        assert nginx.count(location) == 2
    for header in (
        "proxy_set_header Host $host;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
        "proxy_set_header Upgrade $http_upgrade;",
        "proxy_set_header Connection $connection_upgrade;",
    ):
        assert header in nginx


def test_container_nginx_preserves_the_host_proxy_scheme_and_client_ip() -> None:
    nginx = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")

    assert "map $http_x_forwarded_proto $forwarded_proto" in nginx
    assert "map $http_x_real_ip $forwarded_real_ip" in nginx
    assert "proxy_set_header X-Forwarded-Proto $forwarded_proto;" in nginx
    assert "proxy_set_header X-Real-IP $forwarded_real_ip;" in nginx


def test_cd_uses_fixed_isolated_compose_projects_without_managing_certificates() -> None:
    workflows = {
        "nova-prod": (ROOT / ".github" / "workflows" / "release-prod.yml", "8765"),
        "nova-test": (
            ROOT / ".github" / "workflows" / "publish-test-image.yml",
            "18765",
        ),
    }

    for project_name, (path, web_port) in workflows.items():
        workflow = path.read_text(encoding="utf-8")
        assert f"COMPOSE_PROJECT_NAME: {project_name}" in workflow
        assert "NOVA_WEB_BIND_ADDRESS=\"127.0.0.1\"" in workflow
        assert f"NOVA_WEB_HOST_PORT=\"{web_port}\"" in workflow
        assert "NOVA_MONITOR_BIND_ADDRESS=\"127.0.0.1\"" in workflow
        assert "ssl_certificate" not in workflow
        assert "privkey.pem" not in workflow

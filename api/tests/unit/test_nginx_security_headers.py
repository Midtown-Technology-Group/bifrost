from pathlib import Path

import pytest


def _repo_file(*parts: str) -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent.joinpath(*parts)
        if candidate.exists():
            return candidate
    return pytest.skip(
        f"{Path(*parts)} is not available in this packaged test environment",
        allow_module_level=True,
    )


NGINX_CONF = _repo_file("client", "nginx.conf")
CLIENT_DOCKERFILE = _repo_file("client", "Dockerfile")

NORMAL_SECURITY_HEADERS = [
    'add_header X-Content-Type-Options "nosniff" always;',
    'add_header X-Frame-Options "DENY" always;',
    'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;',
    'add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;',
    'add_header Content-Security-Policy-Report-Only "default-src \'self\'; base-uri \'self\'; object-src \'none\'; frame-ancestors \'none\'; img-src \'self\' data: blob:; font-src \'self\' data:; style-src \'self\' \'unsafe-inline\'; script-src \'self\'; connect-src \'self\'" always;',
]


def _location_block(config: str, location: str) -> str:
    marker = f"    location {location} {{"
    start = config.find(marker)
    if start == -1:
        raise AssertionError(f"Location block not found: {location}")
    depth = 0
    for offset, char in enumerate(config[start:], start=start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return config[start : offset + 1]
    raise AssertionError(f"Location block not closed: {location}")


def test_cache_bearing_locations_repeat_normal_security_headers():
    config = NGINX_CONF.read_text()
    server_preamble = config.split("    location ", 1)[0]
    for header in NORMAL_SECURITY_HEADERS:
        assert header in server_preamble, f"server scope is missing {header}"

    locations = [
        "^~ /api/auth",
        "^~ /api",
        "/auth",
        "/.well-known",
        "/",
        "~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$",
    ]

    for location in locations:
        block = _location_block(config, location)
        for header in NORMAL_SECURITY_HEADERS:
            assert header in block, f"{location} is missing {header}"

    assert "connect-src 'self' ws: wss:" not in config


def test_embed_location_preserves_iframe_framing():
    config = NGINX_CONF.read_text()
    block = _location_block(config, "/embed")

    assert 'add_header X-Frame-Options "DENY" always;' not in block
    assert "Content-Security-Policy-Report-Only" not in block
    assert 'add_header X-Content-Type-Options "nosniff" always;' in block
    assert 'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;' in block
    assert 'add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;' in block


def test_mcp_proxy_preserves_authority_port_for_strict_host_validation():
    block = _location_block(NGINX_CONF.read_text(), "~ ^/mcp(/|$)")
    assert "proxy_set_header Host $http_host;" in block
    assert "proxy_set_header Host $host;" not in block


def test_every_proxy_uses_the_filtered_runtime_upstream():
    dockerfile = CLIENT_DOCKERFILE.read_text()
    assert "BIFROST_API_UPSTREAM=api:8000" in dockerfile
    assert (
        "NGINX_ENVSUBST_FILTER=^(BIFROST_API_UPSTREAM|BIFROST_RENDERER_UPSTREAM|"
        "BIFROST_RENDERER_RESOLVER|BIFROST_CANONICAL_SCHEME)$" in dockerfile
    )
    assert "COPY nginx.conf /etc/nginx/templates/default.conf.template" in dockerfile
    config = NGINX_CONF.read_text()
    proxies = [line.strip() for line in config.splitlines() if "proxy_pass " in line]
    assert proxies
    allowed_prefixes = (
        "proxy_pass http://${BIFROST_API_UPSTREAM}",
        "proxy_pass http://${BIFROST_RENDERER_UPSTREAM}",
    )
    assert all(line.startswith(allowed_prefixes) for line in proxies)
    assert any(line.startswith(allowed_prefixes[0]) for line in proxies)
    assert any(line.startswith(allowed_prefixes[1]) for line in proxies)
    for api_upstream, renderer_upstream in (
        ("api:8000", "127.0.0.1:8080"),
        ("127.0.0.1:8000", "renderer:8080"),
    ):
        rendered = config.replace("${BIFROST_API_UPSTREAM}", api_upstream).replace(
            "${BIFROST_RENDERER_UPSTREAM}", renderer_upstream
        )
        assert 'try_files $uri $uri/ /index.html;' in rendered
        assert 'proxy_set_header Host $http_host;' in rendered
        assert f"proxy_pass http://{api_upstream}/embed/forms/public/$public_form_key/frame-policy;" in rendered
        assert f"proxy_pass http://{renderer_upstream}/$3$is_args$args;" in rendered

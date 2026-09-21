# Deploy muse-farm + 9router (muse provider)

## 1. Onboard accounts (on your Mac — needs Chrome GUI)
    ./.venv/bin/python login.py acc1   # repeat per account -> accounts/*.txt

## 2. Ship musegen to the VPS (gen is headless; no Chrome/Playwright there)
    rsync -av --exclude .venv --exclude profiles --exclude '*.webp' \
      ~/WebstormProjects/muse-farm/  user@vps:/opt/muse-farm/
    ssh user@vps '
      cd /opt/muse-farm &&
      python3 -m venv .venv &&
      .venv/bin/pip install curl_cffi noiseprotocol protobuf'   # NOT playwright

    # run as a service
    sudo cp /opt/muse-farm/deploy/muse-server.service /etc/systemd/system/
    sudo systemctl daemon-reload && sudo systemctl enable --now muse-server
    curl -s localhost:8799/v1/models   # sanity

## 3. Update 9router to the patched build (has the muse provider)
Docker (your start.sh): pull the patched source and rebuild.
    cd /path/to/9router && git pull origin master && bash start.sh
Networking — the container must reach the muse shim:
  * Easiest: run the container with `--network host` (Linux). Keep
    MUSE_HOST=127.0.0.1 and the default baseUrl works.
  * Otherwise: bind the shim to 0.0.0.0, add to 9router .env
      MUSE_IMAGE_URL=http://host.docker.internal:8799/v1/images/generations
    and run the container with `--add-host=host.docker.internal:host-gateway`.
CLI install instead of Docker:
    npm i -g https://github.com/vutranHS/9router/releases/download/v0.5.83/9router-0.5.83.tgz

## 4. Use it
    curl -X POST localhost:20128/v1/images/generations \
      -H 'content-type: application/json' \
      -d '{"model":"muse/default","prompt":"a red apple"}'   # -> {data:[{b64_json}]}
`muse/default` rotates accounts; `muse/<name>` forces one. Appears in
Dashboard -> Media Providers -> Image (noAuth, no key needed).

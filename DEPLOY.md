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
    sudo cp /root/muse-farm/deploy/muse-server.service /etc/systemd/system/
    sudo systemctl daemon-reload && sudo systemctl enable --now muse-server
    curl -s localhost:8799/v1/models   # sanity

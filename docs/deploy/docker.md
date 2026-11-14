# Docker

The image carries the app, the built web app and the headless Chromium the request layer needs. It runs as
a non-root user (uid 1000). Its default command is `ssc serve`: the web app and the HTTP API on port 8000.

```bash
docker run -d --name sofascore-scraper --shm-size=1g \
  -p 127.0.0.1:8000:8000 \
  -v sofascore-data:/app/data \
  -v sofascore-config:/app/config \
  -v sofascore-browser:/app/browser-profile \
  ghcr.io/tunjayoff/sofascore_scraper:latest
```

or, with the repository's [`docker-compose.yml`](../../docker-compose.yml):

```bash
docker compose up -d                       # ssc serve at http://127.0.0.1:8000
docker compose --profile live up -d        # also the live service (ssc watch)
docker compose run --rm --no-deps sofascore-scraper status      # any command of the CLI
```

## What the entrypoint runs

| Arguments after the image name | Runs |
|---|---|
| none, `serve [options]` | `ssc serve --host ${HOST:-0.0.0.0} --port ${PORT:-8000} [options]` |
| anything else | `python -m sofascore_scraper.cli.main …`: a command of the CLI (`sync`, `watch`, `status`, `backup create`, …), `--version` or `--help`. The 2.x flags (`--headless --update-all`, `--web`, …) were removed in 3.1: each is a usage error (exit code 2) that names the command replacing it |

## Host allow-list and the access token

Inside the container the server listens on `0.0.0.0`, the container's own interface; which network reaches it
is decided by `-p` / `ports:`, which the app cannot see. Two rules follow:

- **Allowed host names.** `ssc serve` does not start on every interface without them. The Compose example sets
  them explicitly (`SOFASCORE_SERVER__ALLOWED_HOSTS: "localhost,127.0.0.1,[::1]"`). When they are set nowhere
  (not in the environment, not in `/app/config/.env`, and no config file exists), the entrypoint
  sets the loopback names itself, so a plain `docker run` answers on `127.0.0.1` exactly as before. A config
  file decides on its own: put `[server] allowed_hosts` in it, or set the variable. To open the
  app from another machine, add the name or address you use:
  `SOFASCORE_SERVER__ALLOWED_HOSTS=localhost,127.0.0.1,my-server.lan`.
  Keep `127.0.0.1` in the list: the image's health check uses it.
- **Access token.** Without `SOFASCORE_SERVER__TOKEN` the server logs a warning at every start, because it listens on
  a non-loopback address. With the port published on `127.0.0.1` only (as in both examples) nobody else can
  reach it and the warning can be ignored. When you publish the port to a network (`-p 8000:8000`), set the
  token: without it anyone who can reach the port can read and delete the data and change the settings.

With a reverse proxy in another container (Traefik, Caddy, nginx), the server sees the proxy's container
address. Set `FORWARDED_ALLOW_IPS` to that address so that the limit on wrong tokens counts per real client;
the page [`README.md`](README.md#behind-a-reverse-proxy) explains why, and what the proxy must send.

## Volumes

| Volume | Holds |
|---|---|
| `/app/data` | everything downloaded, the job history and the event log (`[storage] data_dir`) |
| `/app/config` | `overrides.json` (the settings saved on the web app's **Settings** page), `.env` (settings as `SOFASCORE_<SECTION>__<KEY>` variables), `leagues.txt` and `league_sports.json` (the 2.x league list, read as follows) and, if you add one, `sofascore.toml`. The follows themselves are in the data volume |
| `/app/browser-profile` | the browser profile with the solved challenge; keeps restarts fast |
| `/app/logs` | the rotating log file; the same lines go to the container output (`docker logs`) |

The browser profile is set with `SOFASCORE_CLIENT__BROWSER_PROFILE` (the 2.x name `SOFASCORE_BROWSER_PROFILE`
is not read since 3.1; an `.env` from 2.x is listed by `ssc doctor` with the new names). A
Chromium profile can be used by one process at a time: a second container on the same profile volume logs a
warning and cannot open its browser. Give every container that downloads or watches a profile volume of its
own, as the Compose example does.

## The live service in a container

The Compose example has a second service, `sofascore-watch`, that runs `ssc watch` and starts only with
`--profile live`. It shares the data and config volumes with the server, has a browser profile, a live
browser profile and a log volume of its own, no published port and no health check. Read [`watch.md`](watch.md) first:

- **Memory.** The default source, `page`, keeps one browser page per watched sport open: about 1.8 to 2.6 GB
  per sport. Set `mem_limit` above the peak (about 2.6 GB per sport; three sports about 8 GB), and a larger
  `shm_size` (the example has 2 GB). `command: ["watch", "--idle", "--source", "poll"]` needs no browser.
- **Nothing followed live yet.** The example runs `command: ["watch", "--idle"]`: with no follow marked
  `live = true` the service logs one line and waits, reading the follows again every 60 seconds, and starts
  watching once one is added. Without `--idle`, `ssc watch` exits with code 2 when there is nothing to watch,
  and the restart policy would restart it every few seconds.
- **`direct`** (`command: ["watch", "--idle", "--source", "direct"]`, about 0.2 GB) uses SofaScore's own client
  credential outside the site's client, may break without notice when the credential or the server changes,
  may get your IP address blocked, and is a terms-of-use grey area that you choose knowingly. It is never
  chosen for you.
- The live source's browser profile (`/app/browser-profile-live`, the `page` and `direct` sources) is on a
  volume of its own in the Compose example (`watch-browser-profile-live`), so a recreated container does not
  solve the challenge again. With `docker run`, mount one there too
  (`-v sofascore-watch-live:/app/browser-profile-live`); without it the profile lives in the container.

```yaml
  sofascore-watch:
    # ...
    volumes:
      - data:/app/data
      - config:/app/config
      - watch-browser-profile:/app/browser-profile
      - watch-browser-profile-live:/app/browser-profile-live
      - watch-logs:/app/logs
```

The two containers can run at the same time on the same data volume: only `watch` runs the live service, and
whichever of the two holds the `sinks` lock delivers the configured sinks.

## One-shot commands and schedules

```bash
docker compose run --rm --no-deps sofascore-scraper sync          # a download
docker compose run --rm --no-deps sofascore-scraper --wait 900 sync
docker compose run --rm --no-deps sofascore-scraper backup create
```

Such a container uses the same browser-profile volume as the server: stop the server first, or give the
one-shot container a profile volume of its own (`-v sofascore-sync-browser:/app/browser-profile`). A download
started while another one runs exits with code 6, or waits with `--wait SECONDS`. A host cron entry or a
systemd timer that runs `docker compose run --rm --no-deps sofascore-scraper sync` is the scheduled download;
the exit codes are those of [`README.md`](README.md#scheduled-downloads).

The image has the `ssc` command on its PATH, so a command can also run in a container that is already up:

```bash
docker exec <container> ssc status
docker exec <container> ssc backup list
```

This suits commands that do not download (`status`, `backup`, `export`, `describe`). A download started this
way would share the server's browser profile, which one process at a time can use: start downloads from the
web app or with the one-shot commands above.

## A network that inspects HTTPS

An antivirus that scans HTTPS, a company proxy that inspects TLS, or a provider's block page answers in
SofaScore's place with a certificate signed by its own CA. Your computer trusts that CA because the software
installed it there; the container does not. The signs:

- the connection check on **Health** fails with "SofaScore could not be reached (the secure connection could
  not be set up …)", and downloads fail with `network`;
- the log has `curl: (60) SSL certificate OpenSSL verify result: self signed certificate in certificate
  chain (19)` (or `unable to get local issuer certificate (20)`);
- `ssc doctor --live` fails with "the app's HTTP client could not verify the certificate it was shown".

The cleanest fix is on the other side: exclude `www.sofascore.com` from the scanning. When that is not
possible, give the container the CA certificate of that software. Export it as a PEM file (Base64, begins with
`-----BEGIN CERTIFICATE-----`), mount it and name it in `SSL_CERT_FILE`:

```yaml
services:
  sofascore-scraper:
    environment:
      SSL_CERT_FILE: /etc/ssl/inspection-ca.pem
    volumes:
      - ./inspection-ca.pem:/etc/ssl/inspection-ca.pem:ro
```

```bash
docker run … -e SSL_CERT_FILE=/etc/ssl/inspection-ca.pem \
  -v "$PWD/inspection-ca.pem:/etc/ssl/inspection-ca.pem:ro" ghcr.io/tunjayoff/sofascore_scraper:latest
```

Set the variable in the container's environment, not in `.env`, and on every container that downloads (the
`sofascore-watch` service too). Then run the connection check again.

- The file **replaces** the image's list of trusted CAs. That is right when everything the container sends is
  inspected. When only some connections are, append your CA to the image's list and mount the result instead:

  ```bash
  docker run --rm --entrypoint cat ghcr.io/tunjayoff/sofascore_scraper:latest \
    /etc/ssl/certs/ca-certificates.crt > ca-bundle.pem
  cat inspection-ca.pem >> ca-bundle.pem
  ```

- A path that does not exist is ignored without a message, and the error stays the same; `ssc doctor --live`
  names the variable when that happens. `CURL_CA_BUNDLE` and `REQUESTS_CA_BUNDLE` are read as well.
- Or build the CA into an image of your own; the app reads the system store when none of the variables is set:

  ```dockerfile
  FROM ghcr.io/tunjayoff/sofascore_scraper:latest
  USER root
  COPY inspection-ca.pem /usr/local/share/ca-certificates/inspection-ca.crt
  RUN update-ca-certificates
  USER app
  ```

**The built-in browser.** The app opens its browser when SofaScore asks for a challenge and for the `page`
and `direct` live sources. The browser refuses a certificate it cannot verify, and it does not read
`SSL_CERT_FILE` or the system store. On an inspecting network it therefore fails with
`net::ERR_CERT_AUTHORITY_INVALID` (`ssc doctor --live` says so). If you need the browser there, let it accept
any certificate:

```yaml
    environment:
      SOFASCORE_CLIENT__BROWSER_ACCEPT_ANY_CERTIFICATE: "true"
```

This is the one switch that relaxes certificate checking, and it covers the browser only: the HTTP client
always verifies. With it on, whoever re-signs the connection can read and change everything the browser
exchanges (page loads, the solved challenge, the answers the app stores). In 3.1.1 and earlier the browser
accepted any certificate on every network, which is also why `ssc doctor --live` could report success there
while every download failed.

## Updating

```bash
docker compose pull && docker compose up -d
```

Data, configuration, the browser profile and the logs stay in their volumes. Back up first
(`docker compose run --rm --no-deps sofascore-scraper backup create`, see [`README.md`](README.md#backups)),
and after an update from version 2 see `ssc migrate` in the same page.

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
| none, `serve [options]` (or the old `web`) | `ssc serve --host ${HOST:-0.0.0.0} --port ${PORT:-8000} [options]` |
| anything else | `python main.py …`: a command of the CLI (`sync`, `watch`, `status`, `backup create`, …), `--version`, or, for one more release, the old flags (`--headless --update-all`, …) with their deprecation line |

## Host allow-list and the access token

Inside the container the server listens on `0.0.0.0`, the container's own interface; which network reaches it
is decided by `-p` / `ports:`, which the app cannot see. Two rules follow:

- **Allowed host names.** `ssc serve` does not start on every interface without them. The Compose example sets
  them explicitly (`SOFASCORE_ALLOWED_HOSTS: "localhost,127.0.0.1,[::1]"`). When they are set nowhere (not in
  the environment under either name, not in `/app/config/.env`, and no config file exists), the entrypoint
  sets the loopback names itself, so a plain `docker run` answers on `127.0.0.1` exactly as before. A config
  file decides on its own: put `[server] allowed_hosts` in it, or set the variable. To open the
  app from another machine, add the name or address you use: `SOFASCORE_ALLOWED_HOSTS=localhost,127.0.0.1,my-server.lan`.
  Keep `127.0.0.1` in the list: the image's health check uses it.
- **Access token.** Without `SOFASCORE_API_TOKEN` the server logs a warning at every start, because it listens on
  a non-loopback address. With the port published on `127.0.0.1` only (as in both examples) nobody else can
  reach it and the warning can be ignored. When you publish the port to a network (`-p 8000:8000`), set the
  token: without it anyone who can reach the port can read and delete the data and change the settings.

With a reverse proxy in another container (Traefik, Caddy, nginx), the server sees the proxy's container
address. Set `FORWARDED_ALLOW_IPS` to that address so that the limit on wrong tokens counts per real client;
the page [`README.md`](README.md#behind-a-reverse-proxy) explains why, and what the proxy must send.

## Volumes

| Volume | Holds |
|---|---|
| `/app/data` | everything downloaded, the job history and the event log (`DATA_DIR`) |
| `/app/config` | `overrides.json` (the settings saved on the web app's **Settings** page), `.env` (settings under their 2.x names), `leagues.txt` and `league_sports.json` (the 2.x league list, read as follows) and, if you add one, `sofascore.toml`. The follows themselves are in the data volume |
| `/app/browser-profile` | the browser profile with the solved challenge; keeps restarts fast |
| `/app/logs` | the rotating log file; the same lines go to the container output (`docker logs`) |

The browser profile is set with `SOFASCORE_CLIENT__BROWSER_PROFILE` (the image also sets the old name,
`SOFASCORE_BROWSER_PROFILE`, to the same folder for the environment check); to move it, change both. A
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

## Updating

```bash
docker compose pull && docker compose up -d
```

Data, configuration, the browser profile and the logs stay in their volumes. Back up first
(`docker compose run --rm --no-deps sofascore-scraper backup create`, see [`README.md`](README.md#backups)),
and after an update from version 2 see `ssc migrate` in the same page.

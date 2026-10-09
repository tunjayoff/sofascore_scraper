# Running SofaScore Scraper on a server

This folder is for people who run the platform on a server or in automation. It covers:

| Page / file | What it is for |
|---|---|
| this page | `ssc serve` as a service, the access token, the Host allow-list, a reverse proxy, scheduled downloads (a timer or the in-app scheduler), backups and restores, `ssc migrate`, sharing a data folder |
| [`watch.md`](watch.md) | the live service `ssc watch` as a service: sources, memory, the warnings of the `direct` source |
| [`docker.md`](docker.md) | the Docker image and the Compose example |
| [`sofascore-serve.service`](sofascore-serve.service) | systemd unit for `ssc serve` (web app and HTTP API) |
| [`sofascore-watch.service`](sofascore-watch.service) | systemd unit for `ssc watch` (live service) |
| [`sofascore-sync.service`](sofascore-sync.service), [`sofascore-sync.timer`](sofascore-sync.timer) | a scheduled `ssc sync` (downloads) |

The commands are those of the command line (`ssc --help`, `ssc <command> --help`). `ssc` exists after
`pip install -e .` in the project folder; without it, `python -m sofascore_scraper.cli.main <command>` from the
project folder is the same thing. The units below use the second form, so they work with either install. The
import package was called `src` before 3.0.0: a unit copied earlier runs `python -m src.cli.main`, which no
longer exists, so change its `ExecStart`; after pulling the rename into a checkout, run `pip install -e .`
again, because the old `ssc` script still imports `src`. In the Docker image `ssc` is on the PATH
([`docker.md`](docker.md)).

## The processes

The platform is a few independent processes that share one data folder:

| Process | Runs | How long | Locks it takes |
|---|---|---|---|
| `ssc serve` | the web app and the HTTP API (`/api/v1`) and, when sinks are configured, their dispatcher | until stopped | `writer` while a job started from the web app runs; `sinks` while it delivers |
| `ssc watch` | the live service (the only place it runs; there is no live service inside `serve`) and the sink dispatcher | until stopped | `live`, `sinks` |
| `ssc sync`, `ssc refresh`, `ssc fetch …` | one download job | until done | `writer` |

One writer at a time per data folder: a second download exits with code 6 and names the holder, or waits with
`--wait SECONDS`. Two processes that can both deliver sinks (`serve` and `watch`) do not conflict: whichever
holds the `sinks` lock delivers, the other waits. `ssc status` shows the locks, the running job and the state
of the live service.

The 2.x flags of `python main.py` (`--web`, `--headless --update-all`, `--watch`, …) were removed in 3.1: each
is a usage error (exit code 2) that names the command replacing it. Use `ssc serve` (or `python main.py serve`)
in your own scripts.

## `ssc serve` as a systemd service

1. Install the app in a folder of its own (for example `/opt/sofascore_scraper`, see the README), as a user of
   its own (`sofascore` below). Build the web app once (`cd frontend && npm ci && npm run build`).
2. Put the settings in `sofascore.toml` in the project folder (`ssc config init > sofascore.toml`) or in
   `config/sofascore.toml`, and secrets in `.env` or in an `EnvironmentFile` that only root and the service
   user can read. `ssc config validate` checks the file, the sinks and their secrets.
3. Copy [`sofascore-serve.service`](sofascore-serve.service) to `/etc/systemd/system/`, adjust `User`,
   `WorkingDirectory`, `ExecStart` and the address, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sofascore-serve
journalctl -u sofascore-serve -f        # logs (also in logs/sofascore_scraper.log unless [log] to_file = false)
curl -s http://127.0.0.1:8000/health    # {"status": "ok", ...}
```

What `serve` does at a stop and at an error:

- SIGTERM (`systemctl stop`) or Ctrl+C stops the server gracefully, delivers what the sinks still have for up
  to 10 seconds, releases its locks and exits with **0**.
- A configuration it cannot use stops it before it listens, with exit code **2**: a broken `sofascore.toml`, a
  bad sink option or a missing sink secret, an invalid `[[schedule.task]]` while the scheduler is on, or
  `--host 0.0.0.0` without allowed host names. The unit does not
  restart on 2 (`RestartPreventExitStatus=2`): the log line says what to fix.
- A server that cannot start (the port is in use) exits with **1**, and systemd restarts it.

Options: `--host`, `--port` (defaults: `[server] host` and `port`, `127.0.0.1` and `8000`), `--allowed-hosts`,
`--allow-any-host`, `--dev` (restart on code changes; development only), `--scheduler` / `--no-scheduler` (the
in-app scheduler, see [Scheduled downloads](#scheduled-downloads)). There is deliberately no option for the
access token: a command line is visible to every user of the machine in the process list.

## Access token

The web app has no user accounts. Anyone who can reach the port can read and delete the data and change the
settings, unless an access token is set:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # a long random value
```

Set it as `SOFASCORE_SERVER__TOKEN` in the service's environment (an `EnvironmentFile` with mode 0600, or
`.env`), or name another variable with `[server] token_env` in the config file. Restart the service after
changing it. The 2.x name `SOFASCORE_API_TOKEN` is not read since 3.1 (`ssc doctor` warns about it; keep it by
setting `token_env = "SOFASCORE_API_TOKEN"` under `[server]`).
Programs send `Authorization: Bearer <token>`; the web app asks for it once and keeps a session cookie.
`GET /health` stays open for health checks and says only `{"status": "ok"}` without the token.

When `serve` listens on an address other than this computer's (`127.0.0.1`, `localhost`, `::1`) and no token
is set, it logs one warning at every start (`ssc serve --json` also lists it as `exposed_without_token`). It
cannot see a firewall or how a container port is published, so the warning is printed in those cases too; if
the port is reachable from this machine only, you can ignore it.

Wrong tokens are limited: after five failed attempts from one client address, further attempts from that
address are refused for 30 seconds, doubling up to one hour. The count lives in the server's memory and ends
with a restart. Browser sessions that already have their cookie are not affected. See the reverse proxy
section for what "client address" means behind a proxy.

## Host allow-list

The server answers only requests whose `Host` header is on its allow-list; this is what stops DNS rebinding,
an attack that comes through your own browser and that no firewall stops. The rules:

| `serve` listens on | Allowed host names |
|---|---|
| this computer only (`127.0.0.1`, `localhost`, `::1`; the default) | `localhost`, `127.0.0.1`, `[::1]` |
| one address, for example `--host 192.168.1.5` | those three and that address |
| every interface (`--host 0.0.0.0` or `::`) | none: `serve` refuses to start (exit code 2) until you name them |
| any of the above with names set | exactly the names you set, never widened |

Set the names with `--allowed-hosts localhost,127.0.0.1,scraper.example.org`, `[server] allowed_hosts` in the
config file, or `SOFASCORE_SERVER__ALLOWED_HOSTS` in the environment or `.env` (the flag wins over the others;
the 2.x name `SOFASCORE_ALLOWED_HOSTS` is not read since 3.1). Include every name and address clients use, and keep `127.0.0.1` when
something on the machine checks `/health`. `--allow-any-host` (or the value `*`) answers to any name: it turns
the protection off and is only for a server that is reachable through a trusted proxy alone.

## Behind a reverse proxy

Put a reverse proxy in front of the server when it is reachable from a network: the app speaks plain HTTP, so
without TLS the token and the session cookie cross the network readable. Keep `serve` on `127.0.0.1` and let
the proxy be the only way in.

- **Host header.** The proxy must pass the original `Host` on (nginx: `proxy_set_header Host $host`; Caddy does
  it by default), and that name must be on the allow-list. Otherwise every request gets `400 Invalid host header`.
- **Server-sent events.** Job progress is a long-lived event stream (`/api/v1/jobs/{id}/events`): turn
  response buffering off and allow long reads. The stream answers with `X-Accel-Buffering: no` and
  `Cache-Control: no-store`, which nginx honours even where buffering is on; other proxies need their own
  setting (Caddy's `flush_interval -1` below). A comment line every 15 s keeps an idle stream open.
- **The root of a host, not a path prefix.** Serve the app at the root of its own host name
  (`scraper.example.org/`), not under a path such as `example.org/sofascore/`: the web UI loads `/assets/…`
  and calls `/api/v1/…` from the root, so a prefixed page loads without its files. The API itself honours an
  ASGI root path (uvicorn `--root-path`, for a proxy that strips the prefix), and the access token is checked
  on the path the router matches either way.
- **A stopped request.** When the browser drops a request (a search you typed over), the server stops
  waiting for SofaScore once it sees the connection close. nginx closes its connection to the app when the
  client goes (`proxy_ignore_client_abort off`, the default) and so does Caddy; do not turn that off.
  Stopping a job is an ordinary `POST /api/v1/jobs/{id}/cancel` and needs nothing from the proxy.
- **Client addresses and the attempt limit.** The limit on wrong tokens counts per client address and lives in
  memory. Which address the server sees depends on the proxy:
  - A proxy on the same machine that connects to `127.0.0.1` or `::1` and sends `X-Forwarded-For` (nginx with
    `$proxy_add_x_forwarded_for`, Caddy, Traefik): the server (uvicorn) trusts that header from these two
    addresses by default and counts per real client.
  - A proxy on another machine or in another container: the server trusts its `X-Forwarded-For` only when the
    proxy's address is in the `FORWARDED_ALLOW_IPS` environment variable of the service (comma-separated; read
    by uvicorn, default `127.0.0.1,::1`). Without it every client has the proxy's address, so five wrong
    Bearer tokens from anyone lock every Bearer client for a while (browser sessions keep working). Never set
    `FORWARDED_ALLOW_IPS=*` while the port can be reached around the proxy: any client could then claim any
    address.
  - In both cases, let the proxy limit attempts as well (below): the app's own limit is per process and is
    reset by a restart.

nginx (TLS settings left out):

```nginx
limit_req_zone $binary_remote_addr zone=sofascore_auth:10m rate=10r/m;

server {
    listen 443 ssl;
    server_name scraper.example.org;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;            # event streams
        proxy_read_timeout 1h;
    }

    location /api/v1/auth/login {
        limit_req zone=sofascore_auth burst=5 nodelay;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Caddy:

```
scraper.example.org {
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```

With either, start `serve` with `--allowed-hosts scraper.example.org,localhost,127.0.0.1` (or the same in the
config file) and set the token.

## Sinks

Configured sinks (`[[sink]]` in the config file or `SOFASCORE_SINKS`; types `stdout`, `file`, `webhook`) are
delivered by `ssc serve` and by `ssc watch`, whichever holds the `sinks` lock, and by one-shot downloads for
their own job events. A new sink starts at "now". Two things about webhooks that depend on the service's
environment: they are sent through `http_proxy` / `https_proxy` when those are set for the service, and their
time-out does not cover the name lookup, so a broken DNS server can hold a delivery longer than the time-out.
`ssc config validate` checks the sink options and that their secrets exist; `ssc events` reads the event log
without delivering anything.

## Scheduled downloads

[`sofascore-sync.service`](sofascore-sync.service) with [`sofascore-sync.timer`](sofascore-sync.timer) runs
`ssc sync` every six hours. It waits up to 15 minutes for a download that the web app or another process is
running (`--wait 900`) instead of failing with 6. Exit codes a scheduler sees: **0** done or nothing to do,
**1** general error, **2** usage or configuration error, **3** done but some matches or lists could not be
fetched (they are retried next time), **4** SofaScore kept refusing and the circuit breaker stopped the job,
**5** storage error, **6** the data folder stayed busy.

```bash
sudo cp sofascore-sync.service sofascore-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sofascore-sync.timer
systemctl list-timers sofascore-sync.timer
```

A daily `ssc refresh` (re-read matches whose result was still provisional) can be a second timer of the same
shape.

**The in-app scheduler** is the alternative when `ssc serve` runs anyway. It is off by default: `ssc serve
--scheduler`, or `[schedule] enabled = true` in the config file, runs the `[[schedule.task]]` entries inside the
server (`--no-scheduler` turns it off for one run; it never runs with `--dev`):

```toml
[schedule]
enabled = true

[[schedule.task]]
run = "sync"              # or "fetch", "refresh", "backup", "prune-history"
every = "6h"              # or cron = "15 */6 * * *" (the machine's local time)
```

- Each run is an ordinary job in the job list. When the previous run of the task or another download still
  holds the data folder, the run is skipped and logged; missed runs are not queued up.
- `every` counts from the task's last run in the job history, so restarting the server does not restart the
  count. With no earlier run, the first one comes one interval after the server starts.
- `prune-history` (no such task exists unless you add one) needs `older_than` (for example `"90d"`) and deletes
  older snapshots of the kept slice history (odds), keeping the newest one of every slice.
- `ssc config validate` checks the tasks; an invalid task stops `serve` before it listens (exit code 2). The next
  runs are listed in `GET /api/v1/status`.

A timer and the scheduler do not know each other: when both are set up, the one that finds the data folder
busy waits (`--wait`), exits with 6 or, for a scheduler task, skips that run.

## Backups

`ssc backup create` writes a zip under `<data folder>/backups/` (`--scope`: `all`, the default; `state`, the
follows, job history and event log; `data`, the match files and the change log). Every scope takes the writer
lock: while a download runs it exits with 6, so schedule it between downloads (or as a `backup` task of the
in-app scheduler). `.env` goes in only with `--include-secrets` (scopes `all` and `state`), and the file name
then says `_with_env`. The scopes `all` and `state` also take `config/overrides.json`, the settings saved on the
web app's **Settings** page; it can hold the proxy URL with its password, so a backup that has it is readable by
its owner only (like a `_with_env` backup; its name does not change). The 2.x scopes `config`, `seasons`,
`matches` and `match_details` were removed in 3.1; backups made with them still verify and restore.

```bash
ssc backup create
ssc backup list
ssc backup verify backup_all_20261003_031500.zip
```

Copy the zips off the machine: a backup on the same disk does not survive the disk. Copying the data folder
with `rsync` or a snapshot works too, but only while no `serve`, `watch` or download runs, because
`.meta/state.db` is a SQLite database in WAL mode.

A restore always replaces the data folder (nothing is merged) and, when the backup has it, the job history.
Backups of version 2.x can be restored too. There are two ways:

- **From the web app**, while `serve` runs: the **Backups** page has **Restore…** for every backup in the
  backups folder (there is no upload). It checks the archive first, then restores as a job of the server. It
  runs only while no other job and no live service (`ssc watch`) is running.
- **From the command line**: stop `serve`, `watch` and the timer first (a restore takes the `maintenance` lock
  and refuses while anything else uses the folder), then:

```bash
ssc backup restore NAME --dry-run      # what would happen
ssc backup restore NAME --yes          # into an empty data folder
ssc backup restore NAME --force --yes  # the folder has data: it is moved to the trash first; nothing is merged
```

The settings saved on the **Settings** page (`config/overrides.json`) come back with the restore, readable by
the owner only, and the settings are reloaded; if they cannot be used (for example a key this version does not
know), the current file is kept and the result lists it as skipped. A backup without that file (older backups)
leaves the current settings alone. The other settings files and `.env` are never restored; the result lists
them as skipped.

## Moving old data to the new layout (`ssc migrate`)

A data folder written by version 2 keeps working as it is: nothing is converted on its own. `ssc migrate`
converts it to the new layout when you run it. Stop the services and the timer while it runs.

```bash
ssc migrate --dry-run          # what would be converted, sizes before and after, what cannot be converted
ssc backup create              # first
ssc migrate                    # converts; stop and start again at any time (--limit N for a part)
ssc migrate --delete-legacy --yes   # later: delete the old copies whose new copy was verified
```

## Sharing a data folder between two accounts

When `serve` and `watch` (or a timer) run as different users of one group, the data folder must be group
writable. A data folder that an earlier version created may have lock and database files that only their owner
can write. Repair it once, while nothing runs, as the owner of the files or as root:

```bash
cd /path/to/data   # the data folder (storage.data_dir)
chmod g+w .meta/locks/*.lock .meta/state.db* .meta/catalog.db*
```

The `*` after the database names matters: it also covers `-wal` and `-shm` files that a failed attempt of the
second account left behind. `catalog.db` can instead be deleted while nothing runs; it is rebuilt from the files.
Run both services with `UMask=0007` so that new files stay group writable.

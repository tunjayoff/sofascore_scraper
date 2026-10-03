# The live service (`ssc watch`) as a service

`ssc watch` is the only way to run the live service: a foreground process for systemd or a container. The web
server (`ssc serve`) does not run it, and there is no HTTP endpoint for live data. The service watches live
matches, writes every change (status, score, a confirmed finish) to the data folder and to the event log, and
the configured sinks (stdout, file, webhook) deliver those events. Read them later with `ssc events`, follow
them with `ssc events --follow`, or print them as they happen with `ssc watch --stdout`.

```bash
ssc follows add tournament 17 --sport football --live   # what to watch: follows with live = true
ssc watch                                 # every followed sport, until stopped
ssc watch --sport football --tournament 17 --stdout   # without follows: this tournament's live matches
ssc status                                # the live service: holder, source per sport, last heartbeat
```

One live service per data folder: a second one exits with code 6. Ctrl+C or SIGTERM stops it after its
current round; it saves its state, drains the sinks for up to 10 seconds and exits with 0.

## The three sources

| | `page` (the default) | `direct` (explicit opt-in) | `poll` |
|---|---|---|---|
| How | one real browser page per watched sport stays open; the service listens to the push connection that SofaScore's own page opens | a small client connects to SofaScore's push server itself, with the credential that the site's own page uses | asks for the live list every poll interval, and for event pages of matches that dropped off it |
| A finished match is seen | 0.6 to 1.0 s after the change (median, measured) | 0.7 s | 32 to 52 s at a one-minute interval |
| Memory (measured) | **1.8 to 2.6 GB per sport page** | **about 0.2 GB** | nothing beyond the process |
| Browser | always, its own profile `<profile>-live` | only while it reads the credential, with the same `<profile>-live` | none |
| Breaks when | a captcha, a change of the site's page, out of memory | the credential or the server changes, the server refuses non-browser clients, the IP address is blocked | SofaScore blocks the requests |

Polling is always the fallback: with a push source it runs slowly while push is healthy (every 120 s) and at
`[live] poll_interval_seconds` while push is silent or disconnected, and once after every reconnect. The push
connection is dropped by the server about every 30 minutes; changes that fall into the gap are caught by the
next poll. `ssc status` shows which source leads each sport and the last switch, so a service that fell back to
polling is visible.

Choose the source with `[live] source` in the config file, `SOFASCORE_LIVE__SOURCE`, or `--source` on the
command line (in the unit's `ExecStart`).

## Memory

Measured on 2026-10-01; the numbers are the resident memory of all browser processes of the profile.

| Configuration | Memory |
|---|---|
| one sport page (`page`), ads, analytics and images blocked, first minute | 1.8 GB |
| the same after 30 minutes | 2.6 GB |
| the same after the 30-minute reconnect | 1.6 to 2.0 GB |
| one sport page without blocking | 2 to 3 GB |
| an idle browser tab, if a browser is kept open anyway | about 1.1 GB |
| `direct` client | about 0.2 GB, flat |

Plan for the peak, not the average: **`page` needs about 2.6 GB per watched sport**, so three sports need about
8 GB. Give the unit (`MemoryMax=` in [`sofascore-watch.service`](sofascore-watch.service)) or the container
(`mem_limit`) a limit above that peak: below it the kernel stops the browser at the worst moment, about every
30 minutes, and the service falls back to polling until the page is back. `--source poll` needs no browser
and no such limit. `--source direct` needs the browser only for the moment it reads the credential.

## The `direct` source: read this before you choose it

`direct` exists because it needs a tenth of the memory of `page`. It is offered, not recommended, and it is
never chosen for you: only `--source direct`, `[live] source = "direct"` or `SOFASCORE_LIVE__SOURCE=direct`
select it, and no fallback or error ever switches to it. Know what you choose:

1. **It uses SofaScore's own client credential outside the site's client.** The credential is read at run time
   from the push connection that the site's own page opens; it is kept in memory only and never written to
   disk, to the database, to a log line, to an event or to the diagnostics bundle.
2. **It may break without notice** if the credential or the server changes.
3. **It may get your IP address blocked.**
4. **It is a terms-of-use grey area that you choose knowingly.**

The same four statements appear in `ssc watch --help`, `ssc describe`, `ssc config validate` and
`ssc config show`, in both READMEs, and in the log at every start of the service with this source.

How it behaves: one connection, subscribe only (`sport.<sport>` for each watched sport), nothing published, no
wildcard subjects, a ping every 120 seconds as the site's own client sends. It reconnects after the regular
drop; a rejected credential is read again from a fresh page with back-off (1 to 30 minutes), and after five
failures in a row the source counts as unhealthy and the service keeps polling. With a proxy configured it does
not connect at all (it would bypass the proxy) and the service polls.

What was measured: one evening, one connection, the single subject `sport.football`, about 38 minutes, one
reconnect. Not measured: several sports on one connection, runs of hours, how often the credential changes.

## As a systemd service

[`sofascore-watch.service`](sofascore-watch.service): adjust the user, the paths, the source and `MemoryMax`,
then

```bash
sudo cp sofascore-watch.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sofascore-watch
journalctl -u sofascore-watch -f
```

The unit does not restart on exit code 2 (a configuration error, or nothing to watch: no follow with
`live = true`) or 6 (another live service holds this data folder); it restarts after other failures.

The `page` and `direct` sources start Chromium with the profile `<profile>-live` next to the bridge profile
(`SOFASCORE_BROWSER_PROFILE`, default `~/.cache/sofascore_scraper/chrome_profile`), so the service user needs
a writable home or `SOFASCORE_BROWSER_PROFILE` pointing to a folder it can write. Polling and confirmation
requests go through the shared request budget (`[client] rate`) together with downloads and the web app.

## In Docker

See [`docker.md`](docker.md): the Compose example has a `sofascore-watch` service that starts with
`docker compose --profile live up -d`.

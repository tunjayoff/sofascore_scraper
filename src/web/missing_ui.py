"""
Web arayüzü derlenmemişken (frontend/dist yok) gösterilen yardım sayfası.

Arayüzün kendisi yok, o yüzden metin frontend/src/locales üzerinden geçemez: sayfa iki dili de
içerir ve kendi kendine yeter (dış istek, betik ya da derlenmiş dosya gerektirmez). API ve
/health çalışmaya devam eder; derleme tamamlanınca sunucuyu yeniden başlatmadan sayfayı
yenilemek yeterlidir (src/web/app.py derlemeye her istekte bakar).
"""

RELEASES_URL = "https://github.com/tunjayoff/sofascore_scraper/releases"

MISSING_UI_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>SofaScore Scraper — web UI not built / web arayüzü derlenmemiş</title>
<style>
  :root { color-scheme: light dark; --bg: #f6f7f9; --card: #ffffff; --text: #1b1f24; --muted: #5b6470;
          --line: #d9dde3; --code: #eef0f3; --accent: #2563eb; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #111417; --card: #1a1e23; --text: #e7eaee; --muted: #9aa4b1; --line: #2c333b;
            --code: #242a31; --accent: #7aa7ff; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 32px 16px; background: var(--bg); color: var(--text);
         font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 760px; margin: 0 auto; }
  header p { color: var(--muted); margin: 4px 0 20px; }
  h1 { font-size: 22px; margin: 0; }
  h2 { font-size: 18px; margin: 0 0 8px; }
  section { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
            padding: 20px 22px; margin: 0 0 16px; }
  ol { padding-left: 22px; margin: 8px 0; }
  li { margin: 6px 0; }
  code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 14px;
              background: var(--code); border-radius: 6px; }
  code { padding: 1px 5px; white-space: nowrap; }
  pre { padding: 10px 12px; overflow-x: auto; margin: 8px 0; }
  a { color: var(--accent); }
  .note { color: var(--muted); font-size: 14px; margin: 10px 0 0; }
</style>
</head>
<body>
<main>
  <header>
    <h1>SofaScore Scraper</h1>
    <p>The server is running, but the web interface has not been built yet.
       · Sunucu çalışıyor, ancak web arayüzü henüz derlenmemiş.</p>
  </header>

  <section lang="en">
    <h2>The web interface is not built</h2>
    <p>The interface is compiled once from the <code>frontend/</code> folder into
       <code>frontend/dist/</code>. That folder is missing, so there is nothing to show yet.</p>
    <ol>
      <li><strong>Build it</strong> (needs <a href="https://nodejs.org">Node.js</a> 20.19+ or 22.12+ with npm):
        <pre>cd frontend
npm install
npm run build</pre>
        Then reload this page; the server does not need a restart.</li>
      <li><strong>Or let the launcher do it:</strong> stop the server and start the app with
        <code>python scripts/start_web.py</code> (<code>./start-sofascore.sh</code>,
        <code>Start SofaScore.bat</code>). It builds the interface when Node.js is installed.</li>
      <li><strong>No Node.js?</strong> A release archive from the
        <a href="__RELEASES_URL__">Releases page</a> contains the interface already built, when a release
        has been published. You can also copy a <code>frontend/dist/</code> folder built on another machine.</li>
    </ol>
    <p class="note">Everything else works without the interface: the API (<a href="/docs">/docs</a>,
       <a href="/health">/health</a>) and the terminal modes (<code>python main.py</code>,
       <code>--headless</code>). <code>python main.py --doctor</code> checks the whole setup.</p>
  </section>

  <section lang="tr">
    <h2>Web arayüzü derlenmemiş</h2>
    <p>Arayüz, <code>frontend/</code> klasöründen <code>frontend/dist/</code> klasörüne bir kez derlenir.
       O klasör olmadığı için henüz gösterilecek bir şey yok.</p>
    <ol>
      <li><strong>Derleyin</strong> (<a href="https://nodejs.org">Node.js</a> 20.19+ veya 22.12+ ve npm gerekir):
        <pre>cd frontend
npm install
npm run build</pre>
        Sonra bu sayfayı yenileyin; sunucuyu yeniden başlatmak gerekmez.</li>
      <li><strong>Ya da başlatıcıya bırakın:</strong> sunucuyu durdurup uygulamayı
        <code>python scripts/start_web.py</code> ile başlatın (<code>./start-sofascore.sh</code>,
        <code>Start SofaScore.bat</code>). Node.js kuruluysa arayüzü kendisi derler.</li>
      <li><strong>Node.js yok mu?</strong> Bir sürüm yayımlandığında,
        <a href="__RELEASES_URL__">Releases sayfasındaki</a> sürüm arşivi arayüzü derlenmiş olarak içerir.
        Başka bir makinede derlenmiş <code>frontend/dist/</code> klasörünü de kopyalayabilirsiniz.</li>
    </ol>
    <p class="note">Geri kalan her şey arayüz olmadan çalışır: API (<a href="/docs">/docs</a>,
       <a href="/health">/health</a>) ve terminal modları (<code>python main.py</code>,
       <code>--headless</code>). <code>python main.py --doctor</code> tüm kurulumu denetler.</p>
  </section>
</main>
</body>
</html>
""".replace("__RELEASES_URL__", RELEASES_URL)

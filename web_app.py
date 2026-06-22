"""The browser satellite — a self-contained web front-end to the central brain.

This is the phone/tablet half of the "central brain + thin satellites" plan: a
single static HTML page (no build step, no framework, no dependencies) that any
device on the LAN opens in a browser. It talks to the same `brain_server` HTTP
seam the CLI satellite uses — POSTing to `/chat/stream` and rendering Alfred's
reply sentence by sentence as it arrives, and embedding `/display/calendar` in a
pane when the brain hands back a `display_url`.

`brain_server` serves `INDEX_HTML` at `GET /`. Keeping it one self-contained
string mirrors the calendar view in `google_tools.py`: a satellite stays thin —
the brain owns all the logic; the page only ferries text and shows what it's told.

Scope: text chat + show-calendar. Push-to-talk (browser captures audio → brain
runs local Whisper STT) is the next increment and is intentionally not here yet.
"""
from __future__ import annotations

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alfred</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body { font-family: Georgia, 'Times New Roman', serif; background: #0e0e10;
         color: #ece6d8; display: flex; flex-direction: column; }
  header { padding: 14px 18px; border-bottom: 1px solid #333; }
  header h1 { margin: 0; font-size: 22px; font-weight: normal; letter-spacing: 1px; }
  #status { font-size: 13px; color: #8a8a8a; margin-top: 3px; }
  #status.ok { color: #9fb4c7; }
  #status.err { color: #c77; }
  .panes { flex: 1 1 auto; display: flex; min-height: 0; }
  #chat { flex: 1 1 50%; display: flex; flex-direction: column; min-height: 0; }
  #log { flex: 1 1 auto; overflow-y: auto; padding: 16px; }
  .bubble { max-width: 80%; margin: 8px 0; padding: 10px 14px; border-radius: 12px;
            line-height: 1.45; white-space: pre-wrap; word-wrap: break-word; }
  .you { margin-left: auto; background: #1f2b36; color: #dce8f2;
         border-bottom-right-radius: 3px; }
  .alfred { margin-right: auto; background: #1b1b1f; border: 1px solid #2a2a2f;
            border-bottom-left-radius: 3px; }
  .alfred.error { color: #d99; border-color: #5a2a2a; }
  .who { display: block; font-size: 11px; color: #c9a227; margin-bottom: 3px;
         letter-spacing: 0.5px; }
  form { display: flex; gap: 8px; padding: 12px; border-top: 1px solid #333; }
  #input { flex: 1 1 auto; font: inherit; font-size: 16px; padding: 11px 13px;
           background: #16161a; color: #ece6d8; border: 1px solid #333;
           border-radius: 10px; }
  #input:focus { outline: none; border-color: #c9a227; }
  button { font: inherit; font-size: 16px; padding: 0 18px; background: #c9a227;
           color: #14140f; border: none; border-radius: 10px; cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: default; }
  #calpane { flex: 0 0 0; width: 0; border-left: 1px solid #333; overflow: hidden;
             transition: width 0.2s ease; }
  #calpane.visible { flex: 1 1 45%; width: auto; }
  #calframe { width: 100%; height: 100%; border: 0; background: #0e0e10; }
  @media (max-width: 820px) {
    .panes { flex-direction: column; }
    #calpane { border-left: none; border-top: 1px solid #333; }
    #calpane.visible { flex: 0 0 50%; height: 50%; }
  }
</style>
</head>
<body>
  <header>
    <h1>Alfred</h1>
    <div id="status">Connecting…</div>
  </header>
  <div class="panes">
    <div id="chat">
      <div id="log"></div>
      <form id="form" autocomplete="off">
        <input id="input" type="text" placeholder="At your service, sir…"
               enterkeyhint="send" autofocus>
        <button id="send" type="submit">Send</button>
      </form>
    </div>
    <div id="calpane">
      <iframe id="calframe" title="Calendar"></iframe>
    </div>
  </div>
<script>
  const log = document.getElementById('log');
  const form = document.getElementById('form');
  const input = document.getElementById('input');
  const send = document.getElementById('send');
  const statusEl = document.getElementById('status');

  function scrollLog() { log.scrollTop = log.scrollHeight; }

  function addBubble(who, text) {
    const b = document.createElement('div');
    b.className = 'bubble ' + (who === 'you' ? 'you' : 'alfred');
    const label = document.createElement('span');
    label.className = 'who';
    label.textContent = who === 'you' ? 'You' : 'Alfred';
    b.appendChild(label);
    const body = document.createElement('span');
    body.textContent = text;
    b.appendChild(body);
    log.appendChild(b);
    scrollLog();
    return body;  // caller mutates .textContent as the reply streams
  }

  function showCalendar(url) {
    const pane = document.getElementById('calpane');
    const frame = document.getElementById('calframe');
    frame.src = url + (url.includes('?') ? '&' : '?') + '_t=' + Date.now();
    pane.classList.add('visible');
  }

  async function ask(text) {
    const body = addBubble('alfred', '');
    let spoken = '';
    try {
      const resp = await fetch('/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!resp.ok) {
        let msg = 'HTTP ' + resp.status;
        try { const j = await resp.json(); if (j.error) msg = j.error; } catch (e) {}
        body.textContent = '(' + msg + ')';
        body.parentElement.classList.add('error');
        return;
      }
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf('\\n')) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          let obj;
          try { obj = JSON.parse(line); } catch (e) { continue; }
          if (obj.sentence) {
            spoken += (spoken ? ' ' : '') + obj.sentence;
            body.textContent = spoken;
            scrollLog();
          } else if (obj.display_url) {
            showCalendar(obj.display_url);
          } else if (obj.error) {
            body.textContent = (spoken ? spoken + ' ' : '') + '(' + obj.error + ')';
            body.parentElement.classList.add('error');
          }
        }
      }
      if (!spoken && !body.parentElement.classList.contains('error')) {
        body.textContent = '…';
      }
    } catch (e) {
      body.textContent = '(cannot reach Alfred: ' + e + ')';
      body.parentElement.classList.add('error');
    }
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    addBubble('you', text);
    input.value = '';
    input.disabled = send.disabled = true;
    try { await ask(text); }
    finally { input.disabled = send.disabled = false; input.focus(); }
  });

  async function health() {
    try {
      const r = await fetch('/health');
      const j = await r.json();
      const caps = [];
      if (j.home_control) caps.push('home control');
      if (j.calendar) caps.push('calendar');
      statusEl.textContent = 'Connected · ' + j.model +
        (caps.length ? ' · ' + caps.join(', ') : ' · chat only');
      statusEl.className = 'ok';
    } catch (e) {
      statusEl.textContent = 'Cannot reach the brain';
      statusEl.className = 'err';
    }
  }
  health();
</script>
</body>
</html>
"""

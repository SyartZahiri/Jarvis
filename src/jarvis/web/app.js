/* JARVIS desktop client. Plain JavaScript, no build step, nothing loaded from the internet.
 * Text from the model, files or web pages is always escaped before it reaches the DOM,
 * and the server sends a strict Content-Security-Policy on top of that. */
'use strict';

(() => {
  // ---------------------------------------------------------------- helpers

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
  class Raw { constructor(text) { this.text = text; } toString() { return this.text; } }
  const raw = (text) => new Raw(text);
  const fmt = (value) => {
    if (value instanceof Raw) return value.text;
    if (Array.isArray(value)) return value.map(fmt).join('');
    if (value === null || value === undefined) return '';
    return esc(value);
  };
  // Tagged template: every interpolation is escaped unless it was wrapped in raw().
  const html = (strings, ...values) =>
    raw(strings.reduce((out, s, i) => out + s + (i < values.length ? fmt(values[i]) : ''), ''));
  const icon = (name) => raw(`<svg><use href="#i-${name}"/></svg>`);
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const number = (n, digits = 0) => Number(n).toLocaleString('de-DE', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

  const app = $('#app');
  const transcript = $('#transcript');
  const prompt = $('#prompt');

  const S = {
    state: null,
    page: 'chat',
    conversation: null,
    title: null,
    messages: [],
    run: null,            // { id, conversation, pendingPrompt, parts, round, fresh }
    approvals: [],
    listening: false,
    automationBusy: false,
    errorUntil: 0,
    connected: false,
    log: [],
    latency: null,
    settingsTab: 'models',
    editing: null,
    models: [],
    memoryQuery: '',
    historyQuery: '',
    pull: null,
    setup: null,
  };

  // ---------------------------------------------------------------- api

  async function api(path, body) {
    const options = body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Jarvis': '1' },
      body: JSON.stringify(body),
    };
    const response = await fetch(path, options);
    let data = null;
    try { data = await response.json(); } catch { /* empty body */ }
    if (!response.ok) throw new Error((data && data.error) || `Anfrage fehlgeschlagen (${response.status}).`);
    return data;
  }

  async function refreshState() {
    S.state = await api('/api/state');
    renderModels();
    renderHistory();
    renderSide();
    if (!S.conversation && !S.run) renderChat();
    return S.state;
  }

  const activeProfile = () => (S.state ? S.state.profiles.find((p) => p.active) : null);

  // ---------------------------------------------------------------- feedback

  function toast(text, kind = '') {
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.textContent = text;
    $('#toasts').append(el);
    setTimeout(() => {
      el.classList.add('leave');
      el.addEventListener('animationend', () => el.remove(), { once: true });
    }, kind === 'error' ? 6500 : 3600);
  }

  function status(text, sub) {
    $('#mode-sub').textContent = text;
    if (sub !== undefined) $('#mode-title').textContent = sub;
  }

  function fail(error) {
    const message = error instanceof Error ? error.message : String(error);
    toast(message, 'error');
    log(message, 'err');
    S.errorUntil = Date.now() + 2600;
    updateCore();
    setTimeout(updateCore, 2700);
  }

  function log(text, kind = '') {
    S.log.unshift({ time: new Date().toTimeString().slice(0, 8), text, kind, fresh: true });
    S.log.length = Math.min(S.log.length, 60);
    renderLog();
  }

  // ---------------------------------------------------------------- sphere

  /* A rotating point sphere drawn on a 2D canvas: Fibonacci-distributed points, a few
   * short links, tilted orbits and an outer instrument ring. While JARVIS writes, a
   * waveform runs through the middle; its height follows the incoming text. */
  const Sphere = (() => {
    const canvas = $('#sphere');
    const ctx = canvas.getContext('2d');
    const COUNT = 1000;
    const points = [];
    for (let i = 0; i < COUNT; i++) {
      const y = 1 - (i / (COUNT - 1)) * 2;
      const r = Math.sqrt(1 - y * y);
      const t = i * 2.399963229728653;
      points.push({ x: Math.cos(t) * r, y, z: Math.sin(t) * r, phase: Math.random() * Math.PI * 2 });
    }
    const links = [];
    const hubs = points.filter((_, i) => i % 5 === 0);
    for (const a of hubs) {
      const near = hubs.map((b) => [b, (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2])
        .filter(([b]) => b !== a).sort((p, q) => p[1] - q[1]).slice(0, 2);
      for (const [b] of near) links.push([a, b]);
    }
    const orbits = [
      { rx: 1.34, ry: .32, angle: -.38, speed: .22, phase: 0 },
      { rx: 1.5, ry: .5, angle: .52, speed: -.15, phase: 2 },
      { rx: 1.22, ry: .18, angle: 1.4, speed: .3, phase: 4 },
    ];
    const bars = Array.from({ length: 84 }, () => 0);
    const COLORS = { idle: [63, 166, 255], busy: [80, 185, 255], listening: [110, 205, 255], approval: [255, 181, 71], error: [255, 95, 109] };
    let width = 0; let height = 0; let ratio = 1;
    let state = 'idle';
    let color = [...COLORS.idle];
    let rotation = 0; let spin = .08; let energy = 0; let busy = 0;
    let last = performance.now();
    let running = false;

    function resize() {
      const box = canvas.getBoundingClientRect();
      ratio = Math.min(2, window.devicePixelRatio || 1);
      width = box.width; height = box.height;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      if (reducedMotion.matches) draw(performance.now(), 0);
    }

    const rgba = (alpha) => `rgba(${color[0] | 0},${color[1] | 0},${color[2] | 0},${alpha})`;

    function ring(cx, cy, radius, t) {
      ctx.lineWidth = 1;
      ctx.strokeStyle = rgba(.18);
      ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.stroke();
      for (let i = 0; i < 120; i++) {
        const a = (i / 120) * Math.PI * 2 + t * .02;
        const long = i % 10 === 0;
        const r1 = radius + 4; const r2 = radius + (long ? 13 : 8);
        ctx.strokeStyle = rgba(long ? .55 : .2);
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
        ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
        ctx.stroke();
      }
      ctx.lineWidth = 2;
      for (let k = 0; k < 4; k++) {
        const start = -t * .09 + k * (Math.PI / 2);
        ctx.strokeStyle = rgba(.75);
        ctx.beginPath(); ctx.arc(cx, cy, radius - 9, start, start + .5); ctx.stroke();
      }
      ctx.lineWidth = 1;
      ctx.strokeStyle = rgba(.12);
      ctx.setLineDash([2, 6]);
      ctx.beginPath(); ctx.arc(cx, cy, radius - 22, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
    }

    function draw(now, dt) {
      const target = COLORS[state] || COLORS.idle;
      for (let i = 0; i < 3; i++) color[i] += (target[i] - color[i]) * Math.min(1, dt * 4);
      const goal = state === 'busy' ? .55 : state === 'listening' ? .3 : state === 'approval' ? .04 : .09;
      spin += (goal - spin) * Math.min(1, dt * 2);
      busy += ((state === 'busy' || state === 'listening' ? 1 : 0) - busy) * Math.min(1, dt * 3);
      rotation += spin * dt;
      energy = Math.max(0, energy - dt * .9);

      ctx.clearRect(0, 0, width, height);
      const cx = width / 2; const cy = height / 2;
      const radius = Math.max(40, Math.min(width * .5, height) * .33);
      const t = now / 1000;

      const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * 1.6);
      glow.addColorStop(0, rgba(.22 + busy * .1));
      glow.addColorStop(.45, rgba(.06));
      glow.addColorStop(1, rgba(0));
      ctx.fillStyle = glow;
      ctx.fillRect(0, 0, width, height);

      if (Math.min(width, height) > 220) ring(cx, cy, radius * 1.55, t);

      for (const o of orbits) {
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(o.angle);
        ctx.strokeStyle = rgba(.16);
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.ellipse(0, 0, o.rx * radius, o.ry * radius, 0, 0, Math.PI * 2); ctx.stroke();
        const a = o.phase + t * o.speed * (1 + busy * 2);
        const px = Math.cos(a) * o.rx * radius; const py = Math.sin(a) * o.ry * radius;
        ctx.fillStyle = rgba(.95);
        ctx.beginPath(); ctx.arc(px, py, 2.2, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = rgba(.18);
        ctx.beginPath(); ctx.arc(px, py, 7, 0, Math.PI * 2); ctx.fill();
        ctx.restore();
      }

      const cosR = Math.cos(rotation); const sinR = Math.sin(rotation);
      const tilt = .38; const cosT = Math.cos(tilt); const sinT = Math.sin(tilt);
      const project = (p) => {
        const x = p.x * cosR - p.z * sinR;
        const z1 = p.x * sinR + p.z * cosR;
        const y = p.y * cosT - z1 * sinT;
        const z = p.y * sinT + z1 * cosT;
        const wave = 1 + busy * .035 * Math.sin(t * 5 - p.y * 5);
        const scale = (2.8 / (2.8 - z)) * wave;
        return { sx: cx + x * radius * scale, sy: cy + y * radius * scale, depth: (z + 1) / 2 };
      };
      for (const p of points) p.proj = project(p);

      ctx.lineWidth = .6;
      for (const [a, b] of links) {
        const d = Math.min(a.proj.depth, b.proj.depth);
        if (d < .45) continue;
        ctx.strokeStyle = rgba((d - .45) * .45);
        ctx.beginPath(); ctx.moveTo(a.proj.sx, a.proj.sy); ctx.lineTo(b.proj.sx, b.proj.sy); ctx.stroke();
      }
      for (const p of points) {
        const { sx, sy, depth } = p.proj;
        const twinkle = .78 + .22 * Math.sin(t * 2.2 + p.phase);
        const alpha = (.1 + .9 * depth ** 1.6) * twinkle;
        const size = .7 + 1.5 * depth;
        ctx.fillStyle = depth > .93 && p.phase > 5.6 ? `rgba(235,246,255,${alpha})` : rgba(alpha);
        ctx.fillRect(sx - size / 2, sy - size / 2, size, size);
      }

      const core = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * .42);
      core.addColorStop(0, `rgba(235,246,255,${.35 + busy * .25})`);
      core.addColorStop(.35, rgba(.18));
      core.addColorStop(1, rgba(0));
      ctx.fillStyle = core;
      ctx.beginPath(); ctx.arc(cx, cy, radius * .42, 0, Math.PI * 2); ctx.fill();

      const level = Math.min(1, energy + (state === 'listening' ? .35 : 0));
      if (level > .01) {
        const span = radius * 1.7; const n = bars.length; const step = span / n;
        for (let i = 0; i < n; i++) {
          const envelope = Math.sin(Math.PI * (i + .5) / n);
          const noise = .5 + .5 * Math.sin(t * 9 + i * .7) * Math.sin(t * 4.3 + i * 1.9);
          bars[i] += ((noise * envelope * level) - bars[i]) * Math.min(1, dt * 14);
          const h = Math.max(1, bars[i] * radius * .55);
          ctx.fillStyle = `rgba(235,246,255,${.35 + bars[i] * .6})`;
          ctx.fillRect(cx - span / 2 + i * step, cy - h / 2, Math.max(1, step * .45), h);
        }
      }
    }

    function frame(now) {
      if (!running) return;
      const dt = Math.min(.05, (now - last) / 1000);
      last = now;
      draw(now, dt);
      requestAnimationFrame(frame);
    }

    function start() {
      if (running || reducedMotion.matches || document.hidden || canvas.offsetParent === null) return;
      running = true;
      last = performance.now();
      requestAnimationFrame(frame);
    }
    function stop() { running = false; }

    new ResizeObserver(resize).observe(canvas);
    document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));

    return {
      setState(next) { state = next; if (reducedMotion.matches) draw(performance.now(), 1); },
      pulse(amount = .25) { energy = Math.min(1, energy + amount); },
      start, stop, resize,
    };
  })();

  // ---------------------------------------------------------------- core state

  const MODES = {
    idle: ['Bereit', 'Wartet auf deinen Auftrag'],
    busy: ['Arbeitet', 'Denkt nach'],
    listening: ['Hört zu', 'Sprich jetzt'],
    approval: ['Freigabe', 'Wartet auf deine Entscheidung'],
    error: ['Störung', 'Details im Protokoll'],
  };

  function currentState() {
    if (S.approvals.length) return 'approval';
    if (S.run) return 'busy';
    if (S.listening) return 'listening';
    if (S.automationBusy) return 'busy';
    if (Date.now() < S.errorUntil) return 'error';
    return 'idle';
  }

  function updateCore(detail) {
    const state = currentState();
    app.dataset.state = state;
    app.dataset.running = S.run ? 'true' : 'false';
    Sphere.setState(state);
    const [title, sub] = MODES[state];
    $('#mode-title').textContent = state === 'busy' && S.automationBusy && !S.run ? 'Automation' : title;
    $('#mode-sub').textContent = detail || sub;
    const send = $('#send');
    send.innerHTML = `<svg><use href="#i-${S.run ? 'stop' : 'send'}"/></svg>`;
    send.title = S.run ? 'Stoppen · Esc' : 'Senden · Enter';
    send.setAttribute('aria-label', S.run ? 'Stoppen' : 'Senden');
    renderReadout();
  }

  function renderReadout() {
    const round = S.run ? `${S.run.round + 1}/12` : '–';
    $('#core-readout').innerHTML = fmt(html`Runde <b>${round}</b><br>Antwort <b>${S.latency === null ? '–' : `${number(S.latency, 1)} s`}</b><br>Freigaben <b>${S.approvals.length}</b>`);
  }

  // ---------------------------------------------------------------- markdown

  function inline(text) {
    return String(text).split(/(`[^`\n]+`)/).map((part, index) => {
      if (index % 2) return `<code>${esc(part.slice(1, -1))}</code>`;
      const held = [];
      const hold = (markup) => `\u0000${held.push(markup) - 1}\u0000`;
      let s = esc(part);
      s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
        (_, label, url) => hold(`<a href="#" data-href="${url}" title="${url}">${label}</a>`));
      s = s.replace(/(^|[\s(])(https?:\/\/[^\s<\u0000]+[^\s<.,;:!?)\]'"\u0000])/g,
        (_, pre, url) => pre + hold(`<a href="#" data-href="${url}">${url}</a>`));
      s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>')
        .replace(/__(?=\S)([\s\S]*?\S)__/g, '<strong>$1</strong>')
        .replace(/(^|[^*\w])\*(?=\S)([^*\n]*?\S)\*(?!\*)/g, '$1<em>$2</em>')
        .replace(/(^|[^_\w])_(?=\S)([^_\n]*?\S)_(?!\w)/g, '$1<em>$2</em>')
        .replace(/~~(?=\S)([\s\S]*?\S)~~/g, '<del>$1</del>');
      return s.replace(/\u0000(\d+)\u0000/g, (_, n) => held[Number(n)]);
    }).join('');
  }

  function listHTML(items, ordered) {
    const tag = ordered ? 'ol' : 'ul';
    const base = items[0].indent;
    let out = `<${tag}>`;
    for (let k = 0; k < items.length; k++) {
      if (items[k].indent > base) continue;
      const children = [];
      let j = k + 1;
      while (j < items.length && items[j].indent > base) children.push(items[j++]);
      out += `<li>${inline(items[k].text)}${children.length ? listHTML(children, false) : ''}</li>`;
    }
    return `${out}</${tag}>`;
  }

  const FENCE = /^\s*```/;
  const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
  const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
  const isTable = (lines, i) => lines[i].includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]);
  const startsBlock = (lines, i) => FENCE.test(lines[i]) || /^#{1,4}\s/.test(lines[i]) ||
    /^\s*>/.test(lines[i]) || LIST.test(lines[i]) || RULE.test(lines[i]) || isTable(lines, i);

  function md(source) {
    const lines = String(source || '').replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      let m;
      if ((m = line.match(/^\s*```\s*([\w+#.-]*)/))) {
        const buffer = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) buffer.push(lines[i++]);
        i++;
        out.push(`<div class="code"><div class="code-head"><span class="label">${esc(m[1] || 'code')}</span>` +
          '<button class="text-btn" type="button" data-action="copy-code">Kopieren</button></div>' +
          `<pre><code>${esc(buffer.join('\n'))}</code></pre></div>`);
        continue;
      }
      if (!line.trim()) { i++; continue; }
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
        out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`);
        i++;
        continue;
      }
      if (RULE.test(line)) { out.push('<hr>'); i++; continue; }
      if (/^\s*>/.test(line)) {
        const buffer = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) buffer.push(lines[i++].replace(/^\s*>\s?/, ''));
        out.push(`<blockquote>${md(buffer.join('\n'))}</blockquote>`);
        continue;
      }
      if (isTable(lines, i)) {
        const cells = (l) => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
        const head = cells(line);
        const rows = [];
        i += 2;
        while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
        out.push(`<div class="table-wrap"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead>` +
          `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
        continue;
      }
      if ((m = line.match(LIST))) {
        const ordered = /\d/.test(m[2]);
        const items = [];
        while (i < lines.length) {
          const item = lines[i].match(LIST);
          if (item && (item[1].length > 0 || /\d/.test(item[2]) === ordered)) {
            items.push({ indent: item[1].length, text: item[3] });
            i++;
          } else if (items.length && lines[i].trim() && /^\s{2,}/.test(lines[i])) {
            items[items.length - 1].text += ` ${lines[i].trim()}`;
            i++;
          } else break;
        }
        out.push(listHTML(items, ordered));
        continue;
      }
      const buffer = [lines[i++]];
      while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) buffer.push(lines[i++]);
      out.push(`<p>${buffer.map(inline).join('<br>')}</p>`);
    }
    return out.join('');
  }

  // ---------------------------------------------------------------- conversation

  function hint(args) {
    for (const key of ['path', 'query', 'url', 'target', 'title', 'role', 'action', 'source', 'prompt']) {
      if (args && args[key]) return String(args[key]);
    }
    if (args && Array.isArray(args.argv)) return args.argv.join(' ');
    return '';
  }

  function stepHTML(step) {
    const state = step.ok === null || step.ok === undefined ? 'run' : step.ok ? 'ok' : 'err';
    const label = { run: 'läuft', ok: 'ok', err: 'fehler' }[state];
    const detail = state === 'err' && step.error ? step.error : hint(step.arguments);
    return html`<div class="step" data-state="${state}" title="${detail}"><span class="step-mark"></span><span class="step-name">${step.name}</span><span class="step-arg">${detail}</span><span class="step-state">${label}</span></div>`;
  }

  function partsHTML(parts, live) {
    const out = parts.map((part) => (part.type === 'text'
      ? html`<div class="md">${raw(md(part.text))}</div>`
      : html`<div class="steps">${part.steps.map(stepHTML)}</div>`));
    if (live) {
      const last = parts[parts.length - 1];
      if (!last || last.type !== 'text' || !last.text) {
        out.push(html`<div class="thinking">${S.approvals.length ? 'Wartet auf Freigabe' : last ? 'Verarbeite' : 'Analysiere'}</div>`);
      } else {
        const node = out[out.length - 1];
        out[out.length - 1] = raw(node.text.replace(/(<\/(p|li|code|h\d|td)>)(?!.*<\/(p|li|code|h\d|td)>)/s, '<span class="caret"></span>$1'));
      }
    }
    return out;
  }

  function turns(messages) {
    const result = [];
    let jarvis = null;
    for (const message of messages) {
      if (message.role === 'user') {
        jarvis = null;
        result.push({ role: 'user', text: message.content });
        continue;
      }
      if (!jarvis) { jarvis = { role: 'jarvis', parts: [] }; result.push(jarvis); }
      if (message.content) jarvis.parts.push({ type: 'text', text: message.content });
      if (message.tools && message.tools.length) {
        const last = jarvis.parts[jarvis.parts.length - 1];
        const steps = message.tools.map((t) => ({ ...t }));
        if (last && last.type === 'steps') last.steps.push(...steps);
        else jarvis.parts.push({ type: 'steps', steps });
      }
    }
    return result;
  }

  const userHTML = (text, enter) => html`<article class="msg msg-user${enter ? ' enter' : ''}"><div class="msg-meta"><span class="msg-avatar">${icon('chat')}</span>Du</div><div class="msg-body">${text}</div></article>`;
  const jarvisHTML = (parts, live, enter) => html`<article class="msg msg-jarvis${enter ? ' enter' : ''}"${live ? raw(' id="live"') : ''}><div class="msg-meta"><span class="msg-avatar">${icon('core')}</span>Jarvis</div><div class="msg-body">${partsHTML(parts, live)}</div></article>`;

  const runVisible = () => S.run && (S.run.conversation === S.conversation || (S.run.conversation === null && S.conversation === null));
  const nearBottom = () => transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 100;
  const toBottom = () => { transcript.scrollTop = transcript.scrollHeight; };

  const SUGGESTIONS = [
    'Was liegt in meinem Arbeitsbereich? Fasse die wichtigsten Dateien zusammen.',
    'Recherchiere die wichtigsten Neuigkeiten zu einem Thema und nenne die Quellen.',
    'Schreib ein Python-Skript, das Fotos nach Aufnahmedatum in Ordner sortiert.',
    'Merk dir: Ich mag kurze Antworten mit konkreten Beispielen.',
  ];

  function emptyChatHTML() {
    const profile = activeProfile();
    if (S.state && !profile) {
      return html`<div class="chat-empty"><h3>Noch kein Modell verbunden.</h3>
        <p>Die Einrichtung dauert zwei Minuten: lokal und kostenlos mit Ollama oder mit einem API-Key.</p>
        <button class="btn btn-primary cta" type="button" data-action="setup">${icon('bolt')}Einrichtung starten</button></div>`;
    }
    return html`<div class="chat-empty"><h3>${greeting()}</h3><p>Was soll ich erledigen?</p>
      ${SUGGESTIONS.map((text, index) => html`<button type="button" data-action="suggest" data-text="${text}"><span class="num">${String(index + 1).padStart(2, '0')}</span><span>${text}</span></button>`)}</div>`;
  }

  function renderChat({ keepScroll = false } = {}) {
    const stick = !keepScroll || nearBottom();
    const live = runVisible();
    $('#chat-title').textContent = S.title || 'Neues Gespräch';
    if (!S.messages.length && !live) {
      transcript.innerHTML = fmt(emptyChatHTML());
      renderInsight();
      return;
    }
    const out = turns(S.messages).map((t) => (t.role === 'user' ? userHTML(t.text) : jarvisHTML(t.parts)));
    if (!live && S.messages.length && S.messages[S.messages.length - 1].role === 'user') {
      const reason = S.runError && S.runError.conversation === S.conversation ? S.runError.message : '';
      const ollamaDown = /ollama/i.test(reason) && /verbindung/i.test(reason);
      out.push(html`<article class="msg msg-error"><div class="msg-meta"><span class="msg-avatar">${icon('x')}</span>Nicht beantwortet</div>
        <div class="msg-body"><p>${ollamaDown ? 'Ollama läuft gerade nicht, deshalb konnte ich nicht antworten.' : reason || 'Auf diese Nachricht gibt es noch keine Antwort.'}</p>
        <div class="row">${ollamaDown ? html`<button class="btn" type="button" data-action="start-ollama">${icon('play')}Ollama starten</button>` : ''}
        <button class="btn btn-ghost" type="button" data-action="retry">Erneut senden</button></div></div></article>`);
    }
    if (live) {
      if (S.run.pendingPrompt) out.push(userHTML(S.run.pendingPrompt, S.run.fresh));
      out.push(jarvisHTML(S.run.parts, true, S.run.fresh));
      S.run.fresh = false;
    }
    transcript.innerHTML = fmt(out);
    if (stick) toBottom();
    renderInsight();
  }

  let liveFrame = 0;
  function renderLive() {
    if (liveFrame) return;
    liveFrame = requestAnimationFrame(() => {
      liveFrame = 0;
      const node = $('#live .msg-body');
      if (!node || !runVisible()) return;
      const stick = nearBottom();
      node.innerHTML = fmt(partsHTML(S.run.parts, true));
      if (stick) toBottom();
    });
  }

  function greeting() {
    const h = new Date().getHours();
    if (h < 5) return 'Gute Nacht.';
    if (h < 11) return 'Guten Morgen.';
    if (h < 18) return 'Guten Tag.';
    return 'Guten Abend.';
  }

  async function openConversation(id) {
    try {
      const data = await api(`/api/conversations/${encodeURIComponent(id)}`);
      S.conversation = data.id;
      S.title = data.title;
      S.messages = data.messages;
      renderChat();
      renderHistory();
      showPage('chat');
    } catch (error) { fail(error); }
  }

  function newChat() {
    if (S.run) { toast('JARVIS arbeitet noch. Mit Esc abbrechen.', 'error'); return; }
    S.conversation = null;
    S.title = null;
    S.messages = [];
    renderChat();
    renderHistory();
    showPage('chat');
  }

  async function reloadConversation() {
    if (!S.conversation) { renderChat(); return; }
    try {
      const data = await api(`/api/conversations/${encodeURIComponent(S.conversation)}`);
      S.title = data.title;
      S.messages = data.messages;
    } catch { S.messages = []; }
    renderChat({ keepScroll: true });
  }

  async function refreshConversations() {
    if (!S.state) return;
    S.state.conversations = await api('/api/conversations');
    renderHistory();
  }

  function renderHistory() {
    if (!S.state) return;
    const query = S.historyQuery.toLowerCase();
    const items = S.state.conversations.filter((c) => c.title.toLowerCase().includes(query));
    $('#history-list').innerHTML = fmt(items.length ? items.map((c) => html`<li class="history-item" aria-current="${c.id === S.conversation}">
        <button type="button" data-action="open" data-id="${c.id}" title="${c.title}">${c.title}</button>
        <span class="row-actions">
          <button class="icon-btn" type="button" data-action="rename" data-id="${c.id}" title="Umbenennen" aria-label="Umbenennen">${icon('edit')}</button>
          <button class="icon-btn danger" type="button" data-action="delete-conversation" data-id="${c.id}" title="Löschen" aria-label="Löschen">${icon('trash')}</button>
        </span></li>`)
      : html`<li class="history-empty">${query ? 'Keine Treffer.' : 'Noch keine Gespräche.'}</li>`);
  }

  // ---------------------------------------------------------------- sending

  async function send(text) {
    const value = (text ?? prompt.value).trim();
    if (!value || S.run) return;
    if (!activeProfile()) { openSetup(); return; }
    prompt.value = '';
    autoGrow();
    S.runError = null;
    S.run = { id: null, conversation: S.conversation, pendingPrompt: value, parts: [], round: 0, fresh: true };
    updateCore('Analysiere den Auftrag');
    renderChat();
    try {
      const result = await api('/api/ask', { prompt: value, conversation: S.conversation });
      if (S.run && !S.run.id) S.run.id = result.run;
    } catch (error) {
      S.run = null;
      prompt.value = value;
      autoGrow();
      updateCore();
      renderChat();
      fail(error);
    }
  }

  async function cancelRun() {
    if (!S.run) return;
    updateCore('Breche ab');
    try { await api('/api/cancel', {}); } catch (error) { fail(error); }
  }

  const ownsRun = (event) => S.run && (S.run.id === null || S.run.id === event.run);

  function onRunEvent(event) {
    switch (event.type) {
      case 'run_start':
        if (!S.run) {
          S.run = { id: event.run, conversation: event.conversation || null, pendingPrompt: event.prompt, parts: [], round: 0, fresh: true };
          renderChat();
        } else if (!S.run.id) S.run.id = event.run;
        log(`Auftrag: ${event.prompt.slice(0, 70)}`);
        updateCore();
        break;
      case 'run_conversation':
        if (!ownsRun(event)) return;
        if (S.conversation === S.run.conversation) S.conversation = event.id;
        S.run.conversation = event.id;
        refreshConversations().then(() => {
          const found = S.state.conversations.find((c) => c.id === S.conversation);
          if (found) { S.title = found.title; $('#chat-title').textContent = found.title; }
        }).catch(() => {});
        break;
      case 'round':
        if (!ownsRun(event)) return;
        S.run.round = event.index;
        updateCore(event.index > 0 ? `Runde ${event.index + 1}` : 'Denkt nach');
        break;
      case 'text': {
        if (!ownsRun(event)) return;
        const last = S.run.parts[S.run.parts.length - 1];
        if (last && last.type === 'text') last.text += event.delta;
        else { S.run.parts.push({ type: 'text', text: event.delta }); updateCore('Schreibt die Antwort'); }
        Sphere.pulse(Math.min(.4, .04 + event.delta.length * .012));
        renderLive();
        break;
      }
      case 'tool_start': {
        if (!ownsRun(event)) return;
        const step = { id: event.id, name: event.name, arguments: event.arguments, ok: null };
        const last = S.run.parts[S.run.parts.length - 1];
        if (last && last.type === 'steps') last.steps.push(step);
        else S.run.parts.push({ type: 'steps', steps: [step] });
        updateCore(`${event.name} ${hint(event.arguments)}`.trim().slice(0, 60));
        renderLive();
        break;
      }
      case 'tool_end':
        if (!ownsRun(event)) return;
        for (const part of S.run.parts) {
          if (part.type !== 'steps') continue;
          const step = part.steps.find((s) => s.id === event.id);
          if (step) step.ok = event.ok;
        }
        log(raw(`${esc(event.name)} <b>${event.ok ? 'OK' : 'FEHLER'}</b>`), event.ok ? 'ok' : 'err');
        renderLive();
        break;
      case 'run_end': {
        if (S.run && S.run.id && S.run.id !== event.run) return;
        S.run = null;
        if (event.elapsed !== null && event.elapsed !== undefined) S.latency = event.elapsed;
        if (event.state === 'done') log(`Antwort in ${number(event.elapsed || 0, 1)} s`, 'ok');
        else if (event.state === 'cancelled') { log('Abgebrochen, Teilantwort gespeichert'); toast('Abgebrochen. Die Teilantwort ist gespeichert.'); }
        else fail(new Error(event.message || 'Unbekannter Fehler.'));
        if (event.conversation && (S.conversation === null || S.conversation === event.conversation)) S.conversation = event.conversation;
        S.runError = event.state === 'error' ? { conversation: event.conversation, message: event.message || 'Unbekannter Fehler.' } : null;
        updateCore();
        refreshConversations().catch(() => {});
        reloadConversation();
        api('/api/state').then((state) => { S.state = state; renderSide(); }).catch(() => {});
        break;
      }
      default:
    }
  }

  // ---------------------------------------------------------------- approvals

  function showApproval() {
    const overlay = $('#approval');
    const current = S.approvals[0];
    if (!current) {
      if (!overlay.hidden) { overlay.hidden = true; if (S.page === 'chat') prompt.focus(); }
      updateCore();
      renderLive();
      return;
    }
    $('.dialog-approval').dataset.risk = current.risk;
    $('#approval-label').textContent = current.risk === 'high' ? 'Freigabe · hohes Risiko' : 'Freigabe erforderlich';
    $('#approval-title').textContent = current.title;
    $('#approval-meta').textContent = `${current.label} · Werkzeug ${current.tool} · gilt nur für diese eine Ausführung`;
    $('#approval-count').textContent = S.approvals.length > 1 ? `1 von ${S.approvals.length}` : '';
    $('#approval-detail').innerHTML = fmt((current.detail || '').split('\n').map((line) => {
      const kind = /^(@@|\+\+\+|---)/.test(line) ? 'meta' : line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : '';
      return html`<span class="${kind}">${line}</span>\n`;
    }));
    const wasHidden = overlay.hidden;
    overlay.hidden = false;
    if (wasHidden || !overlay.contains(document.activeElement)) $('#approval-deny').focus();
    updateCore(`Freigabe für ${current.tool}`);
    renderLive();
  }

  async function decide(approve) {
    const current = S.approvals.shift();
    if (!current) return;
    showApproval();
    log(`${current.tool}: ${approve ? 'freigegeben' : 'abgelehnt'}`, approve ? 'ok' : 'err');
    try { await api('/api/approval', { id: current.id, approve }); } catch (error) { fail(error); }
  }

  // ---------------------------------------------------------------- dialog

  function dialog({ title, body = '', confirm = 'OK', danger = false, input = null }) {
    return new Promise((resolve) => {
      const overlay = $('#dialog');
      const form = $('#dialog-form');
      const field = $('#dialog-input');
      const ok = $('#dialog-ok');
      $('#dialog-title').textContent = title;
      $('#dialog-body').textContent = body;
      $('#dialog-body').hidden = !body;
      ok.textContent = confirm;
      ok.className = `btn ${danger ? 'btn-danger' : 'btn-primary'}`;
      field.hidden = input === null;
      field.value = input ?? '';
      const previous = document.activeElement;
      overlay.hidden = false;
      (input === null ? (danger ? $('#dialog-cancel') : ok) : field).focus();
      if (input !== null) field.select();
      const finish = (value) => {
        overlay.hidden = true;
        form.onsubmit = null;
        $('#dialog-cancel').onclick = null;
        overlay.onkeydown = null;
        if (previous && previous.focus) previous.focus();
        resolve(value);
      };
      form.onsubmit = (event) => { event.preventDefault(); finish(input === null ? true : field.value); };
      $('#dialog-cancel').onclick = () => finish(input === null ? false : null);
      overlay.onkeydown = (event) => { if (event.key === 'Escape') { event.stopPropagation(); finish(input === null ? false : null); } };
    });
  }

  // ---------------------------------------------------------------- side panels

  const CAP_LABELS = {
    'files.read': 'Dateien lesen', 'files.write': 'Dateien ändern', 'system.execute': 'Programme starten',
    'memory.read': 'Gedächtnis lesen', 'memory.write': 'Gedächtnis ändern', 'network.search': 'Websuche',
    'network.fetch': 'Webseiten abrufen', 'skills.read': 'Skills', 'agent.delegate': 'Teilagenten',
    'computer.control': 'Maus und Tastatur', 'compute.local': 'Rechnen',
  };
  const CAP_ICONS = {
    'files.read': 'files', 'files.write': 'edit', 'system.execute': 'code', 'memory.read': 'memory',
    'memory.write': 'memory', 'network.search': 'search', 'network.fetch': 'globe', 'skills.read': 'bolt',
    'agent.delegate': 'cpu', 'computer.control': 'command', 'compute.local': 'cpu',
  };

  function renderSide() {
    if (!S.state) return;
    const st = S.state;
    const profile = activeProfile();
    const enabledTools = st.tools.filter((t) => t.enabled).length;
    const latency = S.latency;
    const vitals = [
      { k: 'Kern', v: S.connected ? 'online' : connectedOnce ? 'getrennt' : 'verbindet', cls: S.connected ? 'ok' : '', state: S.connected || !connectedOnce ? '' : 'warn' },
      { k: 'Modell', v: profile ? profile.model : 'keins', state: profile ? '' : 'off' },
      { k: 'Betrieb', v: profile ? (profile.is_local ? 'lokal' : 'Cloud') : '–', state: profile ? '' : 'off' },
      { k: 'Antwortzeit', v: latency === null ? '–' : `${number(latency, 1)} s`, bar: latency === null ? 0 : Math.max(.04, 1 - Math.min(latency, 30) / 30) },
      { k: 'Werkzeuge', v: `${enabledTools}/${st.tools.length}`, bar: st.tools.length ? enabledTools / st.tools.length : 0 },
      { k: 'Erinnerungen', v: number(st.memory_count) },
      { k: 'Skills', v: number(st.skills.length) },
    ];
    $('#vitals').innerHTML = fmt(vitals.map((item) => html`<li class="${item.state || ''}"><span class="k">${item.k}</span><span class="v ${item.cls || ''}">${item.v}</span>${item.bar !== undefined ? html`<span class="bar"><span data-w="${Math.round(item.bar * 100)}"></span></span>` : ''}</li>`));
    for (const bar of $$('#vitals .bar span')) bar.style.width = `${bar.dataset.w}%`;
    $('#caps').innerHTML = fmt(st.permissions.map((p) => html`<li class="${p.enabled ? 'on' : ''}" title="${p.description}"><span class="cap-icon">${icon(CAP_ICONS[p.name] || 'bolt')}</span><span>${CAP_LABELS[p.name] || p.description}</span><span class="state${p.enabled && p.approval ? ' approve' : ''}">${p.enabled ? (p.approval ? 'Freigabe' : 'An') : 'Aus'}</span></li>`));
    $('#core-model').textContent = profile ? `${profile.name} · ${profile.model}` : 'kein Modell';
    renderInsight();
    renderReadout();
  }

  function renderLog() {
    $('#log').innerHTML = fmt(S.log.slice(0, 40).map((entry) => {
      const item = html`<li class="${entry.kind}${entry.fresh ? ' new' : ''}"><time>${entry.time}</time><span>${entry.text}</span></li>`;
      entry.fresh = false;
      return item;
    }));
  }

  function renderInsight() {
    const profile = activeProfile();
    const chars = S.messages.reduce((sum, m) => sum + (m.content || '').length, 0) + (S.run ? S.run.parts.reduce((s, p) => s + (p.text || '').length, 0) : 0);
    const budget = profile ? profile.max_context_chars : 60000;
    const share = Math.min(1, chars / budget);
    const fg = $('#ring-fg');
    fg.style.strokeDashoffset = String(163.4 * (1 - share));
    fg.style.stroke = share > .85 ? 'var(--warn)' : '';
    $('#ring-value').textContent = `${Math.round(share * 100)}%`;
    const tools = S.messages.reduce((sum, m) => sum + ((m.tools && m.tools.length) || 0), 0);
    $('#insight').innerHTML = fmt(html`<dt>Kontext belegt</dt><dd>${number(chars)} / ${number(budget)}</dd>
      <dt>Nachrichten</dt><dd>${S.messages.length}</dd><dt>Werkzeugaufrufe</dt><dd>${tools}</dd>
      <dt>Letzte Antwort</dt><dd>${S.latency === null ? '–' : `${number(S.latency, 1)} s`}</dd>`);
  }

  function renderModels() {
    if (!S.state) return;
    const profiles = S.state.profiles;
    $('#model-select').innerHTML = fmt(profiles.length
      ? profiles.map((p) => html`<option value="${p.id}"${p.active ? raw(' selected') : ''}>${p.model}</option>`)
      : html`<option value="">kein Modell</option>`);
    $('#model-dot').classList.toggle('on', Boolean(activeProfile()));
  }

  const DATE = new Intl.DateTimeFormat('de-DE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  function tick() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    $('#clock').innerHTML = `${pad(now.getHours())}:${pad(now.getMinutes())}<span>:${pad(now.getSeconds())}</span>`;
    $('#date').textContent = DATE.format(now);
    $('#greet').textContent = greeting().replace('.', '');
  }

  // ---------------------------------------------------------------- pages

  function showPage(page) {
    S.page = page;
    app.dataset.page = page;
    for (const view of $$('.view')) view.hidden = view.dataset.view !== page;
    for (const button of $$('.tabs [data-nav]')) {
      if (button.dataset.nav === page) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    }
    if (page === 'chat') { Sphere.start(); requestAnimationFrame(() => prompt.focus()); } else Sphere.stop();
    if (page === 'memory') renderMemory();
    if (page === 'automations') renderAutomations();
    if (page === 'settings') renderSettings();
  }

  function gotoSettings(tab) {
    S.settingsTab = tab;
    showPage('settings');
  }

  // Memory --------------------------------------------------------------------

  function renderMemory() {
    $('[data-view="memory"]').innerHTML = fmt(html`<div class="page">
      <header class="page-head">
        <div><span class="label">Langzeitgedächtnis</span><h1>Gedächtnis</h1>
          <p>Was JARVIS über dich und deine Arbeit behalten soll. Passende Einträge bekommt das Modell automatisch mit, solange „Gedächtnis lesen“ an ist.</p></div>
        <div class="count" id="memory-count"></div>
      </header>
      <form class="composer-inline" id="memory-form">
        <input class="input" id="memory-input" placeholder="Neuer Eintrag, z. B. „Meine Projekte liegen unter D:\\Code“" maxlength="4000" aria-label="Neue Erinnerung">
        <button class="btn btn-primary" type="submit">${icon('plus')}Merken</button>
      </form>
      <div class="toolbar"><label class="search">${icon('search')}<input id="memory-search" type="search" placeholder="Durchsuchen" value="${S.memoryQuery}" aria-label="Gedächtnis durchsuchen"></label></div>
      <ul class="list" id="memory-list"></ul></div>`);
    fillMemory();
    $('#memory-input').focus();
  }

  async function fillMemory() {
    const list = $('#memory-list');
    if (!list) return;
    try {
      const items = await api(`/api/memories?q=${encodeURIComponent(S.memoryQuery)}`);
      if (!S.memoryQuery) $('#memory-count').innerHTML = fmt(html`<b>${items.length}</b>Einträge`);
      list.innerHTML = fmt(items.length ? items.map((m) => html`<li><span class="grow"><span class="title">${m.content}</span></span>
          <span class="row-actions"><button class="icon-btn danger" type="button" data-action="forget" data-id="${m.id}" title="Vergessen" aria-label="Vergessen">${icon('trash')}</button></span></li>`)
        : html`<li><div class="empty grow"><strong>${S.memoryQuery ? 'Keine Treffer' : 'Noch nichts gespeichert'}</strong>${S.memoryQuery ? 'Versuch andere Suchbegriffe.' : 'Schreib oben etwas hinein oder sag im Chat „Merk dir …“.'}</div></li>`);
    } catch (error) { fail(error); }
  }

  // Automations ------------------------------------------------------------------

  function when(iso) {
    if (!iso) return '–';
    const minutes = Math.round((new Date(iso) - Date.now()) / 60000);
    const abs = Math.abs(minutes);
    if (abs < 1) return 'jetzt';
    const span = abs < 60 ? `${abs} min` : abs < 1440 ? `${Math.round(abs / 60)} h` : `${Math.round(abs / 1440)} d`;
    return minutes > 0 ? `in ${span}` : `vor ${span}`;
  }

  function every(minutes) {
    if (minutes % 10080 === 0) return minutes === 10080 ? 'wöchentlich' : `alle ${minutes / 10080} Wochen`;
    if (minutes % 1440 === 0) return minutes === 1440 ? 'täglich' : `alle ${minutes / 1440} Tage`;
    if (minutes % 60 === 0) return minutes === 60 ? 'stündlich' : `alle ${minutes / 60} h`;
    return `alle ${minutes} min`;
  }

  function renderAutomations() {
    $('[data-view="automations"]').innerHTML = fmt(html`<div class="page">
      <header class="page-head">
        <div><span class="label">Abläufe</span><h1>Automationen</h1>
          <p>Aufträge, die JARVIS regelmäßig erledigt, solange es geöffnet ist. Ohne Freigabedialog dürfen sie nur lesen, nie schreiben oder ausführen.</p></div>
        <div class="count" id="automation-count"></div>
      </header>
      <form class="panel" id="automation-form">
        <div class="grid">
          <label class="field span-6"><span>Auftrag</span><textarea class="textarea" name="prompt" required placeholder="z. B. Fasse jeden Morgen zusammen, was neu im Ordner inbox/ liegt."></textarea></label>
          <div class="field span-4"><span>Wie oft</span><div class="row"><div class="chips" id="interval-chips">
            <button class="chip" type="button" data-minutes="15">alle 15 min</button>
            <button class="chip" type="button" data-minutes="60" aria-pressed="true">stündlich</button>
            <button class="chip" type="button" data-minutes="1440">täglich</button>
            <button class="chip" type="button" data-minutes="10080">wöchentlich</button></div>
            <input class="input mono" id="automation-minutes" name="minutes" type="number" min="1" max="525600" value="60" aria-label="Intervall in Minuten"></div></div>
          <div class="field span-2"><span>Erster Lauf</span><span class="row"><button class="switch" type="button" role="switch" aria-checked="true" id="automation-now" aria-label="Sofort ausführen"></button><span class="hint">sofort</span></span></div>
        </div>
        <div class="row end"><button class="btn btn-primary" type="submit">${icon('plus')}Anlegen</button></div>
      </form>
      <ul class="list" id="automation-list"></ul></div>`);
    $('#automation-minutes').style.width = '110px';
    fillAutomations();
  }

  async function fillAutomations() {
    const list = $('#automation-list');
    if (!list) return;
    try {
      const items = await api('/api/automations');
      $('#automation-count').innerHTML = fmt(html`<b>${items.filter((a) => a.enabled).length}</b>aktiv`);
      list.innerHTML = fmt(items.length ? items.map((a) => html`<li>
          <span class="state-dot${a.enabled ? ' on' : ''}" title="${a.enabled ? 'aktiv' : 'pausiert'}"></span>
          <span class="grow"><span class="title">${a.prompt}</span>
            <span class="sub">${every(a.interval)} · nächster Lauf ${a.enabled ? when(a.next_run) : 'pausiert'} · zuletzt ${when(a.last_run)}</span>
            ${a.last_result ? html`<details class="more"><summary>Letztes Ergebnis</summary><div class="result">${a.last_result}</div></details>` : ''}</span>
          <span class="row-actions">
            <button class="icon-btn" type="button" data-action="automation-run" data-id="${a.id}" title="Jetzt ausführen" aria-label="Jetzt ausführen">${icon('bolt')}</button>
            <button class="icon-btn" type="button" data-action="automation-toggle" data-id="${a.id}" data-enabled="${a.enabled ? '0' : '1'}" title="${a.enabled ? 'Pausieren' : 'Fortsetzen'}" aria-label="${a.enabled ? 'Pausieren' : 'Fortsetzen'}">${icon(a.enabled ? 'pause' : 'play')}</button>
            <button class="icon-btn danger" type="button" data-action="automation-delete" data-id="${a.id}" title="Löschen" aria-label="Löschen">${icon('trash')}</button>
          </span></li>`)
        : html`<li><div class="empty grow"><strong>Keine Automationen</strong>Leg oben einen wiederkehrenden Auftrag an.</div></li>`);
    } catch (error) { fail(error); }
  }

  // Settings --------------------------------------------------------------------

  const TABS = [['models', 'Modelle'], ['permissions', 'Berechtigungen'], ['search', 'Websuche'],
    ['voice', 'Sprache'], ['skills', 'Skills'], ['plugins', 'Plugins'], ['data', 'Daten']];

  function renderSettings() {
    const st = S.state;
    const counts = st ? {
      models: st.profiles.length,
      permissions: `${st.permissions.filter((p) => p.enabled).length}/${st.permissions.length}`,
      skills: st.skills.length,
      plugins: st.plugins.length,
    } : {};
    $('[data-view="settings"]').innerHTML = fmt(html`<div class="page">
      <header class="page-head"><div><span class="label">System</span><h1>Einstellungen</h1></div>
        <button class="btn" type="button" data-action="setup">${icon('bolt')}Einrichtung erneut starten</button></header>
      <div class="settings">
        <nav class="subnav" aria-label="Einstellungen">${TABS.map(([key, label]) => html`<button type="button" data-tab="${key}" aria-current="${key === S.settingsTab}">${label}<span class="n">${counts[key] ?? ''}</span></button>`)}</nav>
        <div id="settings-body"></div>
      </div></div>`);
    renderSettingsTab();
  }

  const sectionHead = (kicker, title, text) => html`<div class="section-head"><span class="label">${kicker}</span><h2>${title}</h2>${text ? html`<p>${text}</p>` : ''}</div>`;

  function renderSettingsTab() {
    const body = $('#settings-body');
    if (!body || !S.state) return;
    const st = S.state;
    const tab = S.settingsTab;
    if (tab === 'models') {
      body.innerHTML = fmt(html`${sectionHead('Verbindungen', 'Modelle', 'Lokal über Ollama oder mit API-Key bei OpenAI, Anthropic, Gemini, OpenRouter und allen OpenAI-kompatiblen Diensten. Keys liegen verschlüsselt in deinem Benutzerkonto, nie in einer Datei.')}
        <div class="profiles">${st.profiles.length ? st.profiles.map((p) => html`<div class="profile" data-active="${p.active}">
            <span class="radio"></span>
            <div><div class="name">${p.name}</div><div class="meta">${p.label} · ${p.model} · ${p.endpoint}${p.think === false ? ' · ohne Nachdenken' : ''}</div></div>
            <div class="row">${p.active ? html`<span class="tag blue">aktiv</span>` : html`<button class="btn btn-ghost" type="button" data-action="activate" data-id="${p.id}">Aktivieren</button>`}
              <button class="btn" type="button" data-action="edit-profile" data-id="${p.id}">${icon('edit')}Bearbeiten</button></div></div>`)
          : html`<div class="empty"><strong>Noch kein Modell</strong>Am einfachsten über „Einrichtung erneut starten“.</div>`}</div>
        ${S.editing ? profileEditor() : html`<button class="btn btn-primary" type="button" data-action="new-profile">${icon('plus')}Neues Profil</button>`}`);
      if (S.editing) bindProfileEditor();
    } else if (tab === 'permissions') {
      body.innerHTML = fmt(html`${sectionHead('Sicherheit', 'Berechtigungen', 'Bestimmt, welche Werkzeuge das Modell überhaupt zu sehen bekommt. Alles mit „Freigabe“ fragt zusätzlich vor jeder einzelnen Aktion.')}
        <div>${st.permissions.map((p) => html`<div class="perm"><div><div class="title">${p.description}${p.approval ? html`<span class="tag warn">Freigabe je Aktion</span>` : ''}</div><code>${p.name}</code></div>
          <button class="switch" type="button" role="switch" aria-checked="${p.enabled}" aria-label="${p.description}" data-action="permission" data-name="${p.name}"></button></div>`)}</div>`);
    } else if (tab === 'search') {
      const s = st.settings;
      body.innerHTML = fmt(html`${sectionHead('Recherche', 'Websuche', 'Brave Search mit API-Key oder eine eigene SearXNG-Instanz ohne Key. Einzelne Seiten abrufen geht unabhängig davon.')}
        <div class="panel"><div class="grid">
          <div class="field span-6"><span>Anbieter</span><div><div class="segmented" id="search-provider">
            <button type="button" data-value="brave" aria-pressed="${s.search_provider === 'brave'}">Brave Search</button>
            <button type="button" data-value="searxng" aria-pressed="${s.search_provider === 'searxng'}">SearXNG</button></div></div></div>
          <label class="field span-3"><span>Brave API-Key</span><input class="input mono" id="brave-key" type="password" placeholder="${st.has_brave_key ? 'gespeichert, leer lassen' : 'nicht gesetzt'}" autocomplete="off"></label>
          <label class="field span-3"><span>SearXNG-Adresse</span><input class="input mono" id="searx-url" value="${s.searxng_url}" placeholder="http://localhost:8888"></label>
        </div><div class="row end">${st.has_brave_key ? html`<button class="btn btn-danger" type="button" data-action="delete-brave">Key entfernen</button>` : ''}<button class="btn btn-primary" type="button" data-action="save-search">Speichern</button></div></div>`);
    } else if (tab === 'voice') {
      const s = st.settings;
      body.innerHTML = fmt(html`${sectionHead('Offline', 'Sprache', 'Spracherkennung komplett offline mit einem Vosk-Modell (zum Beispiel vosk-model-small-de von alphacephei.com), vorgelesen wird mit der Systemstimme. Dafür muss JARVIS mit dem Extra „voice“ installiert sein.')}
        <div class="panel"><div class="grid">
          <label class="field span-4"><span>Ordner des Vosk-Modells</span><input class="input mono" id="voice-path" value="${s.voice_model_path}" placeholder="C:\\Modelle\\vosk-model-small-de-0.15"></label>
          <label class="field span-2"><span>Max. Aufnahme (s)</span><input class="input mono" id="voice-seconds" type="number" min="2" max="120" value="${s.voice_seconds}"></label>
          <div class="field span-6"><span>Vorlesen</span><span class="row"><button class="switch" type="button" role="switch" id="voice-speak" aria-checked="${Boolean(s.speak_replies)}" aria-label="Antworten vorlesen"></button><span class="hint">Antworten automatisch vorlesen</span></span></div>
        </div><div class="row end"><button class="btn btn-primary" type="button" data-action="save-voice">Speichern</button></div></div>`);
    } else if (tab === 'skills') {
      body.innerHTML = fmt(html`${sectionHead('Fähigkeitspakete', 'Skills', 'Anleitungen im SKILL.md-Format, kompatibel zu Claude. Name und Beschreibung gehen ans Modell, den Inhalt lädt es nur bei Bedarf. Mitgelieferte Skripte werden nie von selbst ausgeführt.')}
        <ul class="list">${st.skills.length ? st.skills.map((s) => html`<li><span class="grow"><span class="title">${s.name}</span><span class="sub">${s.description}</span></span><span class="tag">${s.source}</span></li>`)
          : html`<li><div class="empty grow"><strong>Keine Skills</strong>Leg Ordner mit einer SKILL.md unter skills/ an.</div></li>`}</ul>
        <div class="perm"><div><div class="title">Eigene Claude-Skills einbeziehen</div><code>~/.claude/skills · wirkt nach einem Neustart</code></div>
          <button class="switch" type="button" role="switch" aria-checked="${Boolean(st.settings.include_claude_skills)}" data-action="claude-skills" aria-label="Eigene Claude-Skills einbeziehen"></button></div>`);
    } else if (tab === 'plugins') {
      body.innerHTML = fmt(html`${sectionHead('Erweiterungen', 'Plugins', 'Python-Erweiterungen aus dem Ordner plugins/. Sie laufen ohne Sandbox mit denselben Rechten wie JARVIS, also nur Code aktivieren, den du kennst. Änderungen greifen nach einem Neustart.')}
        <div>${st.plugins.length ? st.plugins.map((p) => html`<div class="perm"><div><div class="title">${p.name} <span class="tag">${p.version || '–'}</span>${p.error ? html`<span class="tag err">Fehler</span>` : ''}</div><code>${p.error || p.description}</code></div>
          <button class="switch" type="button" role="switch" aria-checked="${p.enabled}" data-action="plugin" data-name="${p.name}" aria-label="${p.name}"></button></div>`)
          : html`<div class="empty"><strong>Keine Plugins</strong>Plugins liegen unter plugins/&lt;name&gt;/ im Arbeitsbereich.</div>`}</div>`);
    } else if (tab === 'data') {
      body.innerHTML = fmt(html`${sectionHead('Lokal', 'Daten', 'Gespräche, Erinnerungen, Automationen und Einstellungen bleiben auf diesem Rechner. Der Export enthält keine API-Keys.')}
        <div class="panel"><dl class="kv">
          <dt>Datenordner</dt><dd title="${st.data_dir}">${st.data_dir}</dd>
          <dt>Arbeitsbereich</dt><dd title="${st.workspace}">${st.workspace}</dd>
          <dt>Version</dt><dd>${st.version}</dd>
          <dt>Werkzeuge</dt><dd>${st.tools.filter((t) => t.enabled).length} von ${st.tools.length} aktiv</dd></dl>
        <div class="row end">
          <button class="btn" type="button" data-action="open-path" data-target="workspace">${icon('folder')}Arbeitsbereich</button>
          <button class="btn" type="button" data-action="open-path" data-target="data">${icon('folder')}Datenordner</button>
          <a class="btn btn-primary" href="/api/export" download>${icon('download')}Export (JSON)</a></div></div>`);
    }
  }

  function profileEditor() {
    const d = S.editing;
    const existing = S.state.profiles.find((p) => p.id === d.id);
    const think = d.think === null || d.think === undefined ? 'default' : String(d.think);
    return html`<form class="panel" id="profile-form" autocomplete="off">
      <div class="row"><span class="label">${existing ? 'Profil bearbeiten' : 'Neues Profil'}</span><span class="spacer"></span>
        <button class="icon-btn" type="button" data-action="close-editor" title="Schließen" aria-label="Schließen">${icon('x')}</button></div>
      <div class="grid">
        <label class="field span-2"><span>Name</span><input class="input" name="name" value="${d.name || ''}" placeholder="z. B. Lokal"></label>
        <label class="field span-2"><span>Anbieter</span><select class="select" name="kind">${S.state.kinds.map((k) => html`<option value="${k.kind}"${k.kind === d.kind ? raw(' selected') : ''}>${k.label}</option>`)}</select></label>
        <label class="field span-2"><span>API-Key</span><input class="input mono" name="key" type="password" placeholder="${existing && existing.has_key ? 'gespeichert, leer lassen' : (d.kind === 'ollama' ? 'nicht nötig' : 'Key einfügen')}"></label>
        <label class="field span-3"><span>Endpunkt</span><input class="input mono" name="endpoint" value="${d.endpoint || ''}"></label>
        <label class="field span-3"><span>Modell</span><span class="row"><input class="input mono" name="model" list="model-options" value="${d.model || ''}" placeholder="z. B. qwen3:8b">
          <button class="btn" type="button" data-action="load-models">Laden</button></span>
          <datalist id="model-options">${S.models.map((m) => html`<option value="${m}">`)}</datalist></label>
        <label class="field span-2"><span>Kontext (Zeichen)</span><input class="input mono" name="max_context_chars" type="number" min="4000" value="${d.max_context_chars ?? 60000}"></label>
        <label class="field span-2"><span>Max. Antwort (Tokens)</span><input class="input mono" name="max_output_tokens" type="number" min="64" value="${d.max_output_tokens ?? 4096}"></label>
        <label class="field span-2"><span>Temperatur</span><input class="input mono" name="temperature" type="number" min="0" max="2" step="0.1" value="${d.temperature ?? ''}" placeholder="Standard"></label>
        <div class="field span-6"><span>Nachdenken</span><div class="row"><div class="segmented" id="think">
          ${[['default', 'Standard'], ['true', 'An'], ['false', 'Aus']].map(([v, l]) => html`<button type="button" data-value="${v}" aria-pressed="${think === v}">${l}</button>`)}
          </div><small>Bei Ollama-Modellen wie qwen3 macht „Aus“ kurze Antworten ein Vielfaches schneller.</small></div></div>
      </div>
      <div class="progress" id="pull-progress" hidden><span></span></div>
      <div class="result" id="profile-result"></div>
      <div class="row end">
        ${existing ? html`<button class="btn btn-danger" type="button" data-action="delete-profile">${icon('trash')}Löschen</button>` : ''}
        <span class="spacer"></span>
        ${d.kind === 'ollama' ? html`<button class="btn" type="button" data-action="pull-model">${icon('download')}Herunterladen</button>` : ''}
        <button class="btn" type="button" data-action="test-profile">Verbindung testen</button>
        <button class="btn btn-primary" type="submit">Speichern</button>
      </div></form>`;
  }

  function bindProfileEditor() {
    $('#profile-form [name="kind"]').addEventListener('change', (event) => {
      const draft = readProfile();
      const previous = S.state.kinds.find((k) => k.kind === S.editing.kind);
      const next = S.state.kinds.find((k) => k.kind === event.target.value);
      if (!draft.endpoint || (previous && draft.endpoint === previous.endpoint)) draft.endpoint = next.endpoint;
      S.editing = draft;
      S.models = [];
      renderSettingsTab();
    });
    if (S.pull) showPull(S.pull);
  }

  function readProfile() {
    const data = Object.fromEntries(new FormData($('#profile-form')).entries());
    const think = $('#think [aria-pressed="true"]').dataset.value;
    return { ...S.editing, ...data, temperature: data.temperature === '' ? null : data.temperature, think: think === 'default' ? null : think === 'true' };
  }

  function showPull(pull) {
    for (const bar of $$('#pull-progress, #setup-progress')) {
      bar.hidden = !pull;
      if (!pull) continue;
      bar.classList.toggle('indeterminate', !pull.fraction);
      bar.firstElementChild.style.width = pull.fraction ? `${Math.round(pull.fraction * 100)}%` : '';
    }
    const text = pull ? `${pull.model}: ${pull.status}${pull.fraction ? ` · ${Math.round(pull.fraction * 100)} %` : ''}` : '';
    for (const out of $$('#profile-result, #setup-result')) if (pull) out.textContent = text;
  }

  // ---------------------------------------------------------------- first-run setup

  const SETUP_STEPS = [['welcome', 'Willkommen'], ['model', 'Modell'], ['permissions', 'Freigaben'], ['done', 'Fertig']];
  const LOCAL_MODELS = [
    { name: 'qwen3:1.7b', size: 'ca. 1,4 GB', text: 'Leicht. Für ältere Rechner und Laptops mit wenig Speicher.', ram: 0 },
    { name: 'qwen3:4b', size: 'ca. 2,5 GB', text: 'Ausgewogen. Läuft auf den meisten Rechnern flüssig.', ram: 8 },
    { name: 'qwen3:8b', size: 'ca. 5,2 GB', text: 'Deutlich klüger. Ab 16 GB Arbeitsspeicher.', ram: 16 },
    { name: 'qwen3:14b', size: 'ca. 9,3 GB', text: 'Am stärksten. Für Rechner mit 32 GB oder guter Grafikkarte.', ram: 32 },
  ];
  const CLOUD = [
    { kind: 'openai', name: 'OpenAI', text: 'GPT-Modelle. Key unter platform.openai.com.' },
    { kind: 'anthropic', name: 'Anthropic', text: 'Claude-Modelle. Key unter console.anthropic.com.' },
    { kind: 'gemini', name: 'Google Gemini', text: 'Gemini-Modelle. Key unter aistudio.google.com.' },
    { kind: 'openrouter', name: 'OpenRouter', text: 'Viele Anbieter über einen Key. openrouter.ai.' },
  ];
  const PRESETS = [
    { key: 'careful', name: 'Vorsichtig', tag: '', text: 'Nur Gespräch, Gedächtnis lesen und Rechnen. Kein Zugriff auf Dateien oder das Web.', caps: ['memory.read', 'skills.read', 'compute.local'] },
    { key: 'assistant', name: 'Assistent', tag: 'empfohlen', text: 'Liest Dateien im Arbeitsbereich, sucht im Web, merkt sich Dinge und beauftragt Teilagenten. Ändern und Ausführen sind aus.', caps: ['memory.read', 'memory.write', 'skills.read', 'compute.local', 'files.read', 'network.search', 'network.fetch', 'agent.delegate'] },
    { key: 'full', name: 'Volle Kontrolle', tag: '', text: 'Alles an, auch Dateien ändern, Programme starten und Maus/Tastatur. Jede dieser Aktionen fragt trotzdem einzeln nach.', caps: null },
  ];

  function openSetup(step = 'welcome') {
    S.setup = { step, mode: 'local', ollama: null, model: null, kind: 'openai', models: [], preset: 'assistant', busy: false };
    $('#setup').hidden = false;
    renderSetup();
  }

  function closeSetup() {
    $('#setup').hidden = true;
    S.setup = null;
    try { localStorage.setItem('jarvis.setup', 'done'); } catch { /* private mode */ }
    if (S.page === 'chat') prompt.focus();
  }

  function setupStepIndex(step) {
    const map = { welcome: 0, local: 1, cloud: 1, permissions: 2, done: 3 };
    return map[step] ?? 0;
  }

  function renderSetup() {
    const s = S.setup;
    if (!s) return;
    const index = setupStepIndex(s.step);
    $('#setup-steps').innerHTML = fmt(SETUP_STEPS.map(([, label], i) => html`<li data-state="${i < index ? 'done' : i === index ? 'current' : 'next'}">${label}</li>`));
    const body = $('#setup-body');
    if (s.step === 'welcome') {
      body.innerHTML = fmt(html`<span class="label">Einrichtung</span><h2 id="setup-title">Willkommen bei JARVIS.</h2>
        <p>Ich brauche ein Sprachmodell als Gehirn. Du kannst es lokal auf diesem Rechner laufen lassen oder einen Cloud-Anbieter nutzen. Beides lässt sich später jederzeit ändern.</p>
        <div class="choices">
          <button class="choice" type="button" data-setup-mode="local" aria-pressed="${s.mode === 'local'}"><span class="choice-head">${icon('home')}Lokal mit Ollama</span><p>Privat und kostenlos. Deine Daten verlassen den Rechner nicht. Braucht etwas Speicherplatz.</p><span class="tag blue">empfohlen</span></button>
          <button class="choice" type="button" data-setup-mode="cloud" aria-pressed="${s.mode === 'cloud'}"><span class="choice-head">${icon('cloud')}Cloud-Anbieter</span><p>OpenAI, Claude, Gemini oder OpenRouter. Am klügsten und schnellsten, braucht einen API-Key und kostet je nach Nutzung.</p></button>
        </div>
        <div class="setup-actions"><button class="btn btn-ghost" type="button" data-action="setup-close">Später</button><span class="spacer"></span><button class="btn btn-primary btn-lg" type="button" data-action="setup-next">Weiter</button></div>`);
    } else if (s.step === 'local') {
      const o = s.ollama;
      let statusBox;
      if (!o) statusBox = html`<div class="setup-status">Suche nach Ollama …</div>`;
      else if (o.running) statusBox = html`<div class="setup-status good">${icon('check')}Ollama ${o.version} läuft${o.models.length ? `, ${plural(o.models.length, 'Modell', 'Modelle')} installiert` : ''}.</div>`;
      else if (o.found) statusBox = html`<div class="setup-status bad">Ollama ist installiert, läuft aber nicht. <span class="spacer"></span><button class="btn" type="button" data-action="setup-start-ollama">Ollama starten</button></div>`;
      else statusBox = html`<div class="setup-status bad">Ollama ist noch nicht installiert. Lade es herunter, installiere es und komm dann hierher zurück. <span class="spacer"></span><button class="btn" type="button" data-action="setup-get-ollama">${icon('download')}ollama.com</button></div>`;
      const installed = o && o.running ? o.models : [];
      const recommended = o ? (o.recommended || 'qwen3:4b') : 'qwen3:4b';
      const options = [...installed.map((m) => ({ name: m.name, size: m.size, text: 'Bereits installiert.', installed: true })),
        ...LOCAL_MODELS.filter((m) => !installed.some((i) => i.name === m.name))];
      if (!s.model && o && o.running) s.model = recommended;
      body.innerHTML = fmt(html`<span class="label">Schritt 2</span><h2 id="setup-title">Lokales Modell wählen.</h2>
        <p>Größere Modelle antworten klüger, brauchen aber mehr Arbeitsspeicher${o && o.ram_gb ? ` (dieser Rechner: ${o.ram_gb} GB)` : ''}.</p>
        ${statusBox}
        ${o && o.running ? html`<div class="choices">${options.map((m) => html`<button class="choice" type="button" data-setup-model="${m.name}" aria-pressed="${s.model === m.name}">
            <span class="choice-head">${icon(m.installed ? 'check' : 'cpu')}${m.name}</span><p>${m.text}</p>
            <span class="row"><span class="tag">${m.size}</span>${m.name === recommended && !m.installed ? html`<span class="tag blue">passt zu diesem PC</span>` : ''}</span></button>`)}</div>
          <div class="progress" id="setup-progress" hidden><span></span></div><div class="result" id="setup-result"></div>` : ''}
        <div class="setup-actions"><button class="btn btn-ghost" type="button" data-action="setup-back">Zurück</button>
          ${o && !o.running ? html`<button class="btn btn-ghost" type="button" data-action="setup-recheck">Erneut prüfen</button>` : ''}
          <span class="spacer"></span>
          <button class="btn btn-primary btn-lg" type="button" data-action="setup-local"${!o || !o.running || s.busy ? raw(' disabled') : ''}>${installed.some((m) => m.name === s.model) ? 'Übernehmen' : 'Herunterladen und übernehmen'}</button></div>`);
      if (!o) loadOllamaStatus();
      if (S.pull) showPull(S.pull);
    } else if (s.step === 'cloud') {
      body.innerHTML = fmt(html`<span class="label">Schritt 2</span><h2 id="setup-title">Cloud-Anbieter verbinden.</h2>
        <p>Der Key wird verschlüsselt in deinem Windows-Konto gespeichert und nur an den gewählten Anbieter geschickt.</p>
        <div class="choices">${CLOUD.map((c) => html`<button class="choice" type="button" data-setup-kind="${c.kind}" aria-pressed="${s.kind === c.kind}"><span class="choice-head">${icon('cloud')}${c.name}</span><p>${c.text}</p></button>`)}</div>
        <div class="grid">
          <label class="field span-4"><span>API-Key</span><input class="input mono" id="setup-key" type="password" autocomplete="off" placeholder="Key einfügen"></label>
          <div class="field span-2"><span>&nbsp;</span><button class="btn" type="button" data-action="setup-load-models">Modelle laden</button></div>
          ${s.models.length ? html`<label class="field span-6"><span>Modell</span><select class="select" id="setup-model">${s.models.map((m) => html`<option value="${m}"${m === s.model ? raw(' selected') : ''}>${m}</option>`)}</select></label>` : ''}
        </div>
        <div class="result" id="setup-result"></div>
        <div class="setup-actions"><button class="btn btn-ghost" type="button" data-action="setup-back">Zurück</button><span class="spacer"></span>
          <button class="btn btn-primary btn-lg" type="button" data-action="setup-cloud"${s.models.length && !s.busy ? '' : raw(' disabled')}>Testen und übernehmen</button></div>`);
      if (s.key) $('#setup-key').value = s.key;
    } else if (s.step === 'permissions') {
      body.innerHTML = fmt(html`<span class="label">Schritt 3</span><h2 id="setup-title">Was darf JARVIS?</h2>
        <p>Das lässt sich jederzeit unter System → Berechtigungen fein einstellen. Ändern, Ausführen und Steuern fragt immer erst nach.</p>
        <div class="choices">${PRESETS.map((p) => html`<button class="choice" type="button" data-setup-preset="${p.key}" aria-pressed="${s.preset === p.key}"><span class="choice-head">${icon('shield')}${p.name}</span><p>${p.text}</p>${p.tag ? html`<span class="tag blue">${p.tag}</span>` : ''}</button>`)}</div>
        <div class="setup-actions"><span class="spacer"></span><button class="btn btn-primary btn-lg" type="button" data-action="setup-permissions">Übernehmen</button></div>`);
    } else {
      body.innerHTML = fmt(html`<span class="label">Fertig</span><h2 id="setup-title">Alles bereit.</h2>
        <p>Ein paar Dinge, die du wissen solltest:</p>
        <ul class="list">
          <li><span class="grow"><span class="title">Strg+K öffnet die Befehle</span><span class="sub">Modell wechseln, Gespräche finden, Seiten öffnen</span></span></li>
          <li><span class="grow"><span class="title">Esc stoppt eine laufende Antwort</span><span class="sub">Der bisherige Text bleibt gespeichert</span></span></li>
          <li><span class="grow"><span class="title">Kritisches fragt immer nach</span><span class="sub">Dateien ändern, Programme starten, Maus und Tastatur</span></span></li>
          <li><span class="grow"><span class="title">Dein Arbeitsbereich</span><span class="sub">${S.state ? S.state.workspace : ''}</span></span></li>
        </ul>
        <div class="setup-actions"><span class="spacer"></span><button class="btn btn-primary btn-lg" type="button" data-action="setup-close">Loslegen</button></div>`);
    }
  }

  async function loadOllamaStatus() {
    try {
      S.setup.ollama = await api('/api/setup/ollama');
    } catch (error) {
      S.setup.ollama = { found: false, running: false, models: [] };
      fail(error);
    }
    if (S.setup && S.setup.step === 'local') renderSetup();
  }

  // ---------------------------------------------------------------- palette

  let paletteItems = [];
  let paletteIndex = 0;

  function paletteSource() {
    const items = [
      { group: 'Aktionen', icon: 'plus', label: 'Neues Gespräch', keys: 'Alt+N', run: newChat },
      { group: 'Aktionen', icon: 'history', label: 'Verlauf ein- und ausblenden', keys: 'Alt+H', run: toggleHistory },
      { group: 'Aktionen', icon: 'expand', label: 'Gesprächsbereich breiter oder schmaler', run: toggleWide },
      { group: 'Aktionen', icon: 'mic', label: 'Sprechen', run: listen },
      { group: 'Aktionen', icon: 'bolt', label: 'Einrichtung starten', run: () => openSetup() },
      { group: 'Bereiche', icon: 'core', label: 'Kern', run: () => showPage('chat') },
      { group: 'Bereiche', icon: 'memory', label: 'Gedächtnis', run: () => showPage('memory') },
      { group: 'Bereiche', icon: 'auto', label: 'Automationen', run: () => showPage('automations') },
      ...TABS.map(([key, label]) => ({ group: 'System', icon: 'settings', label, run: () => gotoSettings(key) })),
    ];
    if (S.state) {
      for (const p of S.state.profiles) items.push({ group: 'Modell wechseln', icon: 'cpu', label: `${p.name} · ${p.model}`, keys: p.active ? 'aktiv' : '', run: () => activate(p.id) });
      for (const c of S.state.conversations.slice(0, 80)) items.push({ group: 'Gespräche', icon: 'chat', label: c.title, run: () => openConversation(c.id) });
    }
    return items;
  }

  function openPalette() {
    $('#palette').hidden = false;
    $('#palette-input').value = '';
    filterPalette();
    $('#palette-input').focus();
  }
  const closePalette = () => { $('#palette').hidden = true; };

  function filterPalette() {
    const words = $('#palette-input').value.toLowerCase().split(/\s+/).filter(Boolean);
    paletteItems = paletteSource().filter((item) => words.every((w) => `${item.group} ${item.label}`.toLowerCase().includes(w)));
    paletteIndex = 0;
    drawPalette();
  }

  function drawPalette() {
    let group = null;
    const out = [];
    paletteItems.forEach((item, index) => {
      if (item.group !== group) { group = item.group; out.push(html`<li class="palette-group label">${group}</li>`); }
      out.push(html`<li><button type="button" role="option" data-index="${index}" aria-selected="${index === paletteIndex}">${icon(item.icon)}<span>${item.label}</span><small>${item.keys || ''}</small></button></li>`);
    });
    $('#palette-list').innerHTML = fmt(out.length ? out : html`<li class="palette-empty">Nichts gefunden.</li>`);
    const selected = $('#palette-list [aria-selected="true"]');
    if (selected) selected.scrollIntoView({ block: 'nearest' });
  }

  function runPalette(index) {
    const item = paletteItems[index];
    closePalette();
    if (item) item.run();
  }

  // ---------------------------------------------------------------- small actions

  function toggleHistory() {
    const box = $('#history');
    box.hidden = !box.hidden;
    $('[data-action="toggle-history"]').setAttribute('aria-expanded', String(!box.hidden));
    if (!box.hidden) $('#history-search').focus();
  }

  function toggleWide() {
    const wide = app.dataset.chat !== 'wide';
    app.dataset.chat = wide ? 'wide' : 'normal';
    $('[data-action="toggle-wide"]').innerHTML = `<svg><use href="#i-${wide ? 'collapse' : 'expand'}"/></svg>`;
    try { localStorage.setItem('jarvis.chat', app.dataset.chat); } catch { /* private mode */ }
  }

  async function activate(id) {
    try {
      await api('/api/profiles/activate', { id });
      await refreshState();
      const profile = activeProfile();
      if (profile) log(`Modell: ${profile.model}`);
      if (S.page === 'settings') renderSettings();
    } catch (error) { fail(error); }
  }

  async function listen() {
    if (S.listening) { api('/api/listen/stop', {}).catch(fail); return; }
    try {
      await api('/api/listen', {});
      S.listening = true;
      $('#mic').setAttribute('aria-pressed', 'true');
      updateCore();
    } catch (error) { fail(error); }
  }

  function autoGrow() {
    prompt.style.height = 'auto';
    prompt.style.height = `${Math.min(prompt.scrollHeight, 200)}px`;
  }

  const TEMPLATES = {
    research: 'Recherchiere im Web: ',
    files: 'Sieh dir meinen Arbeitsbereich an und ',
    code: 'Schreib ein Python-Skript, das ',
  };

  // ---------------------------------------------------------------- event stream

  let connectedOnce = false;
  function connect() {
    const source = new EventSource('/api/events');
    source.onopen = () => {
      S.connected = true;
      if (connectedOnce) refreshState().then(() => { if (S.conversation && !S.run) reloadConversation(); }).catch(() => {});
      else renderSide();
      connectedOnce = true;
    };
    source.onerror = () => {
      S.connected = false;
      renderSide();
    };
    source.onmessage = (message) => {
      let event;
      try { event = JSON.parse(message.data); } catch { return; }
      handleEvent(event);
    };
  }

  function handleEvent(event) {
    switch (event.type) {
      case 'run_start': case 'run_conversation': case 'round': case 'text':
      case 'tool_start': case 'tool_end': case 'run_end':
        onRunEvent(event);
        break;
      case 'approval':
        S.approvals.push(event);
        showApproval();
        log(`Freigabe angefragt: ${event.tool}`);
        break;
      case 'approval_closed':
        S.approvals = S.approvals.filter((a) => a.id !== event.id);
        showApproval();
        break;
      case 'status':
        if (event.error) fail(new Error(event.text)); else log(event.text);
        break;
      case 'pull':
        S.pull = event;
        showPull(event);
        break;
      case 'pull_end':
        S.pull = null;
        showPull(null);
        if (event.ok) { log(event.message, 'ok'); toast(event.message); } else fail(new Error(event.message));
        for (const out of $$('#profile-result, #setup-result')) out.textContent = event.message;
        if (S.setup && S.setup.pendingModel === event.model) finishLocalSetup(event.ok);
        break;
      case 'listen_partial':
        updateCore(`… ${event.text}`.slice(0, 70));
        break;
      case 'listen_end':
        S.listening = false;
        $('#mic').setAttribute('aria-pressed', 'false');
        updateCore();
        if (event.error) fail(new Error(event.error));
        else if (!event.text) toast('Ich habe nichts verstanden.', 'error');
        else {
          showPage('chat');
          prompt.value = `${prompt.value ? `${prompt.value} ` : ''}${event.text}`;
          autoGrow();
          prompt.focus();
        }
        break;
      case 'automation_busy':
        S.automationBusy = event.busy;
        updateCore();
        break;
      case 'automations_ran':
        log(`${plural(event.count, 'Automation', 'Automationen')} ausgeführt`, 'ok');
        refreshConversations().catch(() => {});
        if (S.page === 'automations') fillAutomations();
        break;
      default:
    }
  }

  // ---------------------------------------------------------------- setup actions

  async function finishLocalSetup(ok) {
    const s = S.setup;
    if (!s) return;
    s.busy = false;
    s.pendingModel = null;
    if (!ok) { renderSetup(); return; }
    try {
      await api('/api/profiles/save', { name: 'Lokal', kind: 'ollama', model: s.model, endpoint: '', think: false, activate: true });
      await refreshState();
      s.step = 'permissions';
      renderSetup();
    } catch (error) { fail(error); renderSetup(); }
  }

  const setupActions = {
    'setup-next': () => { S.setup.step = S.setup.mode; renderSetup(); },
    'setup-back': () => { S.setup.step = 'welcome'; renderSetup(); },
    'setup-close': closeSetup,
    'setup-recheck': () => { S.setup.ollama = null; renderSetup(); },
    'setup-get-ollama': () => api('/api/open', { target: 'https://ollama.com/download' }).catch(fail),
    'setup-start-ollama': async () => {
      try { await api('/api/setup/ollama/start', {}); toast('Ollama wird gestartet …'); } catch (error) { fail(error); }
      setTimeout(() => { if (S.setup) { S.setup.ollama = null; renderSetup(); } }, 2500);
    },
    'setup-local': async () => {
      const s = S.setup;
      const installed = s.ollama.models.some((m) => m.name === s.model);
      if (installed) { s.pendingModel = s.model; finishLocalSetup(true); return; }
      s.busy = true;
      s.pendingModel = s.model;
      renderSetup();
      try {
        await api('/api/profiles/pull', { kind: 'ollama', model: s.model, endpoint: '' });
        S.pull = { model: s.model, status: 'starte Download', fraction: null };
        showPull(S.pull);
      } catch (error) { s.busy = false; s.pendingModel = null; renderSetup(); fail(error); }
    },
    'setup-load-models': async (el) => {
      const s = S.setup;
      s.key = $('#setup-key').value.trim();
      if (!s.key) { $('#setup-result').textContent = 'Bitte zuerst den Key einfügen.'; return; }
      el.disabled = true;
      $('#setup-result').textContent = 'Frage verfügbare Modelle ab …';
      try {
        const data = await api('/api/profiles/models', { kind: s.kind, key: s.key, endpoint: '' });
        s.models = data.models;
        const prefer = { openai: ['gpt-5', 'gpt-4.1', 'gpt-4o'], anthropic: ['sonnet', 'opus', 'haiku'], gemini: ['flash', 'pro'], openrouter: [] }[s.kind] || [];
        s.model = prefer.map((p) => s.models.find((m) => m.includes(p))).find(Boolean) || s.models[0];
        renderSetup();
      } catch (error) { $('#setup-result').textContent = error.message; el.disabled = false; }
    },
    'setup-cloud': async (el) => {
      const s = S.setup;
      s.model = $('#setup-model').value;
      s.key = $('#setup-key').value.trim() || s.key;
      el.disabled = true;
      $('#setup-result').textContent = 'Teste die Verbindung …';
      const profile = { name: CLOUD.find((c) => c.kind === s.kind).name, kind: s.kind, model: s.model, endpoint: '', key: s.key, activate: true };
      try {
        await api('/api/profiles/test', profile);
        await api('/api/profiles/save', profile);
        await refreshState();
        s.step = 'permissions';
        renderSetup();
      } catch (error) { $('#setup-result').textContent = error.message; el.disabled = false; }
    },
    'setup-permissions': async () => {
      const preset = PRESETS.find((p) => p.key === S.setup.preset);
      const caps = preset.caps || S.state.permissions.map((p) => p.name);
      try {
        await api('/api/permissions', { enabled: caps });
        await refreshState();
        S.setup.step = 'done';
        renderSetup();
      } catch (error) { fail(error); }
    },
  };

  // ---------------------------------------------------------------- actions

  const actions = {
    ...setupActions,
    'new-chat': newChat,
    retry: async () => {
      // The prompt is already stored; ask again with the same text in the same conversation.
      const last = [...S.messages].reverse().find((m) => m.role === 'user');
      if (!last) return;
      try { await api('/api/conversations/drop-last', { id: S.conversation }); } catch { /* keep going */ }
      S.messages = S.messages.slice(0, S.messages.lastIndexOf(last));
      send(last.content);
    },
    'start-ollama': async () => {
      try { await api('/api/setup/ollama/start', {}); toast('Ollama startet. Gleich erneut senden.'); } catch (error) { fail(error); }
    },
    'toggle-history': toggleHistory,
    'toggle-wide': toggleWide,
    palette: openPalette,
    setup: () => openSetup(),
    listen,
    template: (el) => { showPage('chat'); prompt.value = TEMPLATES[el.dataset.template] || ''; autoGrow(); prompt.focus(); prompt.setSelectionRange(prompt.value.length, prompt.value.length); },
    suggest: (el) => { prompt.value = el.dataset.text; autoGrow(); prompt.focus(); },
    open: (el) => openConversation(el.dataset.id),
    rename: async (el) => {
      const item = S.state.conversations.find((c) => c.id === el.dataset.id);
      const title = await dialog({ title: 'Gespräch umbenennen', input: item ? item.title : '', confirm: 'Umbenennen' });
      if (!title || !title.trim()) return;
      try {
        await api('/api/conversations/rename', { id: el.dataset.id, title: title.trim() });
        if (S.conversation === el.dataset.id) { S.title = title.trim(); $('#chat-title').textContent = S.title; }
        await refreshConversations();
      } catch (error) { fail(error); }
    },
    'delete-conversation': async (el) => {
      if (!await dialog({ title: 'Gespräch löschen?', body: 'Das Gespräch wird endgültig entfernt.', confirm: 'Löschen', danger: true })) return;
      try {
        await api('/api/conversations/delete', { id: el.dataset.id });
        if (S.conversation === el.dataset.id) newChat();
        await refreshConversations();
      } catch (error) { fail(error); }
    },
    'copy-code': async (el) => {
      try {
        await navigator.clipboard.writeText(el.closest('.code').querySelector('code').textContent);
        el.textContent = 'Kopiert';
        setTimeout(() => { el.textContent = 'Kopieren'; }, 1400);
      } catch { fail(new Error('Kopieren hat nicht geklappt.')); }
    },
    'goto-permissions': () => gotoSettings('permissions'),
    forget: async (el) => {
      if (!await dialog({ title: 'Erinnerung löschen?', body: 'JARVIS vergisst diesen Eintrag dauerhaft.', confirm: 'Vergessen', danger: true })) return;
      try { await api('/api/memories/delete', { id: el.dataset.id }); S.state.memory_count -= 1; fillMemory(); renderSide(); } catch (error) { fail(error); }
    },
    'automation-run': async (el) => {
      try { await api('/api/automations/run', { id: el.dataset.id }); toast('Automation gestartet.'); fillAutomations(); } catch (error) { fail(error); }
    },
    'automation-toggle': async (el) => {
      try { await api('/api/automations/toggle', { id: el.dataset.id, enabled: el.dataset.enabled === '1' }); fillAutomations(); } catch (error) { fail(error); }
    },
    'automation-delete': async (el) => {
      if (!await dialog({ title: 'Automation löschen?', confirm: 'Löschen', danger: true })) return;
      try { await api('/api/automations/delete', { id: el.dataset.id }); fillAutomations(); } catch (error) { fail(error); }
    },
    activate: (el) => activate(el.dataset.id),
    'new-profile': () => {
      S.editing = { id: null, name: '', kind: 'ollama', endpoint: S.state.kinds.find((k) => k.kind === 'ollama').endpoint, model: '', think: false };
      S.models = [];
      renderSettingsTab();
      $('#profile-form [name="name"]').focus();
    },
    'edit-profile': (el) => { S.editing = { ...S.state.profiles.find((p) => p.id === el.dataset.id) }; S.models = []; renderSettingsTab(); },
    'close-editor': () => { S.editing = null; renderSettingsTab(); },
    'delete-profile': async () => {
      const draft = S.editing;
      if (!await dialog({ title: `Profil „${draft.name}“ löschen?`, body: 'Der gespeicherte API-Key wird mit gelöscht.', confirm: 'Löschen', danger: true })) return;
      try { await api('/api/profiles/delete', { id: draft.id }); S.editing = null; await refreshState(); renderSettings(); } catch (error) { fail(error); }
    },
    'test-profile': async (el) => {
      const result = $('#profile-result');
      el.disabled = true;
      result.className = 'result';
      result.textContent = 'Teste die Verbindung …';
      try {
        const data = await api('/api/profiles/test', readProfile());
        result.textContent = `Verbindung steht. Antwort: ${data.reply.slice(0, 240)}`;
        result.classList.add('ok-text');
      } catch (error) { result.textContent = error.message; result.classList.add('err-text'); }
      el.disabled = false;
    },
    'load-models': async (el) => {
      el.disabled = true;
      $('#profile-result').textContent = 'Frage verfügbare Modelle ab …';
      try {
        const draft = readProfile();
        const data = await api('/api/profiles/models', draft);
        S.models = data.models;
        S.editing = draft;
        renderSettingsTab();
        $('#profile-result').textContent = `${plural(data.models.length, 'Modell', 'Modelle')} gefunden, Auswahl im Feld „Modell“.`;
      } catch (error) { $('#profile-result').textContent = error.message; el.disabled = false; }
    },
    'pull-model': async () => {
      const draft = readProfile();
      try {
        await api('/api/profiles/pull', draft);
        S.pull = { model: draft.model, status: 'starte Download', fraction: null };
        showPull(S.pull);
      } catch (error) { fail(error); }
    },
    permission: async (el) => {
      const enabled = new Set(S.state.permissions.filter((p) => p.enabled).map((p) => p.name));
      if (enabled.has(el.dataset.name)) enabled.delete(el.dataset.name); else enabled.add(el.dataset.name);
      try { await api('/api/permissions', { enabled: [...enabled] }); await refreshState(); renderSettings(); } catch (error) { fail(error); }
    },
    plugin: async (el) => {
      const enabled = new Set(S.state.plugins.filter((p) => p.enabled).map((p) => p.name));
      const name = el.dataset.name;
      if (!enabled.has(name)) {
        if (!await dialog({ title: `Plugin „${name}“ aktivieren?`, body: 'Plugins führen Python-Code mit vollen Rechten aus. Aktiviere nur, was du geprüft hast.', confirm: 'Aktivieren', danger: true })) return;
        enabled.add(name);
      } else enabled.delete(name);
      try { await api('/api/plugins', { enabled: [...enabled] }); await refreshState(); renderSettings(); toast('Gespeichert. Wirkt nach einem Neustart.'); } catch (error) { fail(error); }
    },
    'claude-skills': async (el) => {
      try { await api('/api/settings', { include_claude_skills: el.getAttribute('aria-checked') !== 'true' }); await refreshState(); renderSettings(); toast('Gespeichert. Wirkt nach einem Neustart.'); } catch (error) { fail(error); }
    },
    'save-search': async () => {
      try {
        const key = $('#brave-key').value.trim();
        if (key) await api('/api/web-key', { key });
        await api('/api/settings', { search_provider: $('#search-provider [aria-pressed="true"]').dataset.value, searxng_url: $('#searx-url').value.trim() });
        await refreshState(); renderSettings(); toast('Websuche gespeichert.');
      } catch (error) { fail(error); }
    },
    'delete-brave': async () => {
      try { await api('/api/web-key', { key: '' }); await refreshState(); renderSettings(); toast('Brave-Key entfernt.'); } catch (error) { fail(error); }
    },
    'save-voice': async () => {
      try {
        await api('/api/settings', { voice_model_path: $('#voice-path').value.trim(), voice_seconds: Number($('#voice-seconds').value), speak_replies: $('#voice-speak').getAttribute('aria-checked') === 'true' });
        await refreshState(); toast('Spracheinstellungen gespeichert.');
      } catch (error) { fail(error); }
    },
    'open-path': (el) => api('/api/open', { target: el.dataset.target }).catch(fail),
  };

  document.addEventListener('click', (event) => {
    const link = event.target.closest('.md a[data-href]');
    if (link) { event.preventDefault(); api('/api/open', { target: link.dataset.href }).catch(fail); return; }
    const nav = event.target.closest('[data-nav]');
    if (nav) { showPage(nav.dataset.nav); return; }
    const tab = event.target.closest('[data-tab]');
    if (tab) { S.settingsTab = tab.dataset.tab; S.editing = null; renderSettings(); return; }
    const setupChoice = event.target.closest('[data-setup-mode], [data-setup-model], [data-setup-kind], [data-setup-preset]');
    if (setupChoice && S.setup) {
      const d = setupChoice.dataset;
      if (d.setupMode) S.setup.mode = d.setupMode;
      if (d.setupModel) S.setup.model = d.setupModel;
      if (d.setupKind) { S.setup.kind = d.setupKind; S.setup.models = []; S.setup.key = $('#setup-key') ? $('#setup-key').value : S.setup.key; }
      if (d.setupPreset) S.setup.preset = d.setupPreset;
      renderSetup();
      return;
    }
    const segment = event.target.closest('.segmented button');
    if (segment) { for (const b of segment.parentElement.children) b.setAttribute('aria-pressed', String(b === segment)); return; }
    const chip = event.target.closest('#interval-chips .chip');
    if (chip) {
      for (const b of chip.parentElement.children) b.setAttribute('aria-pressed', String(b === chip));
      $('#automation-minutes').value = chip.dataset.minutes;
      return;
    }
    const sw = event.target.closest('.switch:not([data-action])');
    if (sw) { sw.setAttribute('aria-checked', String(sw.getAttribute('aria-checked') !== 'true')); return; }
    const option = event.target.closest('#palette-list [data-index]');
    if (option) { runPalette(Number(option.dataset.index)); return; }
    if (event.target === $('#palette')) { closePalette(); return; }
    const target = event.target.closest('[data-action]');
    if (target && actions[target.dataset.action]) actions[target.dataset.action](target);
  });

  $('#composer').addEventListener('submit', (event) => { event.preventDefault(); if (S.run) cancelRun(); else send(); });
  prompt.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!S.run) send(); }
  });
  prompt.addEventListener('input', autoGrow);
  $('#history-search').addEventListener('input', (event) => { S.historyQuery = event.target.value; renderHistory(); });
  $('#model-select').addEventListener('change', (event) => { if (event.target.value) activate(event.target.value); });
  $('#approval-deny').addEventListener('click', () => decide(false));
  $('#approval-allow').addEventListener('click', () => decide(true));
  $('#approval').addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); decide(false); }
    if (event.key === 'Tab') { event.preventDefault(); (document.activeElement === $('#approval-deny') ? $('#approval-allow') : $('#approval-deny')).focus(); }
  });
  $('#palette-input').addEventListener('input', filterPalette);
  $('#palette-input').addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); paletteIndex = Math.min(paletteItems.length - 1, paletteIndex + 1); drawPalette(); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); paletteIndex = Math.max(0, paletteIndex - 1); drawPalette(); }
    else if (event.key === 'Enter') { event.preventDefault(); runPalette(paletteIndex); }
    else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closePalette(); }
  });

  document.addEventListener('submit', async (event) => {
    const form = event.target;
    if (form.id === 'memory-form') {
      event.preventDefault();
      const input = $('#memory-input');
      if (!input.value.trim()) return;
      try { await api('/api/memories/add', { content: input.value }); input.value = ''; S.state.memory_count += 1; fillMemory(); renderSide(); } catch (error) { fail(error); }
    } else if (form.id === 'automation-form') {
      event.preventDefault();
      try {
        await api('/api/automations/add', { prompt: form.elements.prompt.value, minutes: Number(form.elements.minutes.value), now: $('#automation-now').getAttribute('aria-checked') === 'true' });
        toast('Automation angelegt.');
        renderAutomations();
      } catch (error) { fail(error); }
    } else if (form.id === 'profile-form') {
      event.preventDefault();
      try {
        const draft = readProfile();
        await api('/api/profiles/save', draft);
        S.editing = null;
        await refreshState();
        renderSettings();
        toast(`Profil „${draft.name || draft.model}“ gespeichert.`);
      } catch (error) { fail(error); }
    }
  });

  document.addEventListener('input', (event) => {
    if (event.target.id === 'memory-search') {
      S.memoryQuery = event.target.value;
      clearTimeout(fillMemory.timer);
      fillMemory.timer = setTimeout(fillMemory, 120);
    }
  });

  document.addEventListener('keydown', (event) => {
    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && key === 'k') { event.preventDefault(); if ($('#palette').hidden) openPalette(); else closePalette(); return; }
    if (event.altKey && key === 'n') { event.preventDefault(); newChat(); return; }
    if (event.altKey && key === 'h') { event.preventDefault(); toggleHistory(); return; }
    if (event.key !== 'Escape') return;
    if (!$('#palette').hidden) { closePalette(); return; }
    if (!$('#dialog').hidden || !$('#approval').hidden) return;
    if (!$('#setup').hidden) { closeSetup(); return; }
    if (S.run) { cancelRun(); return; }
    if (S.listening) listen();
  });

  // ---------------------------------------------------------------- boot

  async function boot() {
    try { if (localStorage.getItem('jarvis.chat') === 'wide') toggleWide(); } catch { /* private mode */ }
    tick();
    setInterval(tick, 1000);
    setInterval(() => { if (S.page === 'automations') fillAutomations(); }, 30000);
    reducedMotion.addEventListener('change', () => { Sphere.stop(); Sphere.resize(); Sphere.start(); });
    try {
      await refreshState();
    } catch (error) {
      fail(error);
      return;
    }
    if (S.state.run) {
      S.run = { id: S.state.run.id, conversation: S.state.run.conversation, parts: [], round: 0 };
      S.conversation = S.state.run.conversation;
      if (S.conversation) await reloadConversation();
    }
    showPage('chat');
    renderChat();
    updateCore();
    log('Kern online', 'ok');
    connect();
    let dismissed = false;
    try { dismissed = localStorage.getItem('jarvis.setup') === 'done'; } catch { /* private mode */ }
    if (!S.state.profiles.length && !dismissed) openSetup();
  }

  boot();
})();

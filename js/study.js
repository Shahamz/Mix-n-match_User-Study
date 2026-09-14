/* ---------------------------------------------------------------------------
 * The study itself: load the bank, draw a session, run the questions, hand the
 * answers to submit.js.
 *
 * Nothing here knows which method made which picture. The bank labels them M1
 * to M4; the key that maps those back is kept out of the served site.
 * ------------------------------------------------------------------------- */

(function () {
  "use strict";

  const CONFIG = window.STUDY_CONFIG;
  const STORE_KEY = "mnm-study-v1";

  const screens = {};
  let manifest = null;
  let session = null;
  let state = null;
  let shownAt = 0;
  let pending = null;      // the four-way answer being assembled

  /* ------------------------------------------------------------- storage -- */

  function load() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (error) {
      return null;
    }
  }

  function save() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch (error) {
      /* Private windows and blocked site data: the study still works, it just
         cannot resume after a refresh. */
    }
  }

  function newParticipantId() {
    const bytes = new Uint8Array(9);
    (window.crypto || {}).getRandomValues
      ? window.crypto.getRandomValues(bytes)
      : bytes.forEach(function (_, i) { bytes[i] = Math.floor(Math.random() * 256); });
    return Array.from(bytes).map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }

  /* --------------------------------------------------------------- screen -- */

  function show(name) {
    Object.keys(screens).forEach(function (key) { screens[key].hidden = key !== name; });
    document.getElementById("bar").hidden = name !== "run";
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /* ---------------------------------------------------------------- rail -- */

  function drawRail() {
    const rail = document.getElementById("rail");
    if (rail.childElementCount !== session.items.length) {
      rail.textContent = "";
      session.items.forEach(function () { rail.appendChild(el("span", "rail__seg")); });
    }
    Array.from(rail.children).forEach(function (segment, index) {
      segment.dataset.state = index < state.index ? "done" : (index === state.index ? "now" : "todo");
    });
    document.getElementById("count").textContent =
      "Question " + Math.min(state.index + 1, session.items.length) + " of " + session.items.length;
  }

  /* -------------------------------------------------------- region glyph -- */

  function regionGlyph(region) {
    const wrap = el("div", "region");
    const width = 62;
    const height = Math.round(width / (region.ar || 1));
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("aria-hidden", "true");

    const canvas = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    canvas.setAttribute("x", "0.5"); canvas.setAttribute("y", "0.5");
    canvas.setAttribute("width", String(width - 1)); canvas.setAttribute("height", String(height - 1));
    canvas.setAttribute("fill", "var(--surface-sunk)");
    canvas.setAttribute("stroke", "var(--line)");
    svg.appendChild(canvas);

    const part = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    part.setAttribute("x", String(region.x * width));
    part.setAttribute("y", String(region.y * height));
    part.setAttribute("width", String(Math.max(2, region.w * width)));
    part.setAttribute("height", String(Math.max(2, region.h * height)));
    part.setAttribute("fill", "var(--accent)");
    svg.appendChild(part);

    wrap.appendChild(svg);
    wrap.appendChild(el("div", "region__label", "this part"));
    return wrap;
  }

  /* -------------------------------------------------------------- prompt -- */

  function askBlock(item, questionText) {
    const ask = el("section", "ask");
    const head = el("div", "ask__head");
    if (item.region) head.appendChild(regionGlyph(item.region));

    const body = el("div");
    body.appendChild(el("h2", "ask__q", questionText || item.question));

    if (item.prompt) {
      body.appendChild(el("p", "ask__quote", item.prompt));
    } else if (item.prompts) {
      const list = el("ul", "ask__quote");
      if (item.background_prompt) list.appendChild(el("li", null, item.background_prompt));
      item.prompts.forEach(function (text) { list.appendChild(el("li", null, text)); });
      body.appendChild(list);
    }

    const hint = item.level === "tile"
      ? "The two pictures may be different shapes and sizes. Judge what is in them, not their outline."
      : "Look at the picture as a whole.";
    body.appendChild(el("p", "ask__hint", hint));

    head.appendChild(body);
    ask.appendChild(head);
    return ask;
  }

  /* ------------------------------------------------------------- choices -- */

  const KEYS = ["A", "B", "C", "D"];

  function renderChoices(entry, onPick) {
    const item = entry.item;
    const grid = el("div", "choices");
    grid.dataset.count = String(item.options.length);

    entry.order.forEach(function (optionIndex, position) {
      const option = item.options[optionIndex];
      const button = el("button", "choice");
      button.type = "button";
      button.setAttribute("aria-pressed", "false");
      button.dataset.optionIndex = String(optionIndex);
      button.dataset.position = String(position);

      const frame = el("span", "choice__frame");
      const image = new Image();
      image.src = option.src;
      image.width = option.w;
      image.height = option.h;
      image.alt = "Picture " + KEYS[position];
      image.loading = "eager";
      image.decoding = "async";
      frame.appendChild(image);
      button.appendChild(frame);

      const foot = el("span", "choice__foot");
      foot.appendChild(el("span", "choice__key", KEYS[position]));
      foot.appendChild(el("span", null, "Picture " + KEYS[position]));
      foot.appendChild(el("span", "choice__state", ""));
      button.appendChild(foot);

      button.addEventListener("click", function () { onPick(position, optionIndex); });
      grid.appendChild(button);
    });
    return grid;
  }

  function markChoice(grid, position, label, mark) {
    const button = grid.children[position];
    button.setAttribute("aria-pressed", mark === "worst" ? "false" : "true");
    if (mark) button.dataset.mark = mark;
    button.querySelector(".choice__state").textContent = label;
  }

  function lockChoices(grid) {
    Array.from(grid.children).forEach(function (button) { button.disabled = true; });
  }

  /* ------------------------------------------------------------ the item -- */

  function preload(from) {
    for (let i = from; i < Math.min(from + 2, session.items.length); i += 1) {
      session.items[i].item.options.forEach(function (option) {
        const image = new Image();
        image.src = option.src;
      });
    }
  }

  function record(entry, answer) {
    state.responses.push(Object.assign({
      id: entry.item.id,
      type: entry.item.type,
      level: entry.item.level,
      set: entry.item.set,
      config: entry.item.config,
      shown_order: entry.order,
      position: state.index,
      ms: Date.now() - shownAt,
      at: new Date().toISOString()
    }, answer));
    state.index += 1;
    save();
    window.setTimeout(renderCurrent, CONFIG.advanceDelayMs);
  }

  function renderCurrent() {
    if (state.index >= session.items.length) {
      show("background");
      return;
    }
    const entry = session.items[state.index];
    const item = entry.item;
    const stage = document.getElementById("stage");
    stage.textContent = "";
    stage.className = "";
    void stage.offsetWidth;          /* restart the fade for the new question */
    stage.className = "fadein";
    pending = null;

    const isFourWay = item.options.length === 4;
    const ask = askBlock(item, isFourWay ? "Which picture is best?" : item.question);
    stage.appendChild(ask);

    const foot = el("div", "stage__foot");
    const step = el("p", "stage__step");
    if (isFourWay) {
      step.innerHTML = "Step 1 of 2 &middot; <b>pick the best</b>";
    } else {
      step.textContent = "Click a picture, or press " + KEYS[0] + " / " + KEYS[1] + ".";
    }
    foot.appendChild(step);

    const grid = renderChoices(entry, function (position, optionIndex) {
      if (!isFourWay) {
        markChoice(grid, position, "Chosen");
        lockChoices(grid);
        record(entry, { choice: optionIndex, choice_position: position });
        return;
      }
      if (pending === null) {
        pending = { best: optionIndex, best_position: position };
        markChoice(grid, position, "Best");
        grid.children[position].disabled = true;
        ask.querySelector(".ask__q").textContent = "And which is worst?";
        step.innerHTML = "Step 2 of 2 &middot; <b>pick the worst</b>";
        return;
      }
      markChoice(grid, position, "Worst", "worst");
      lockChoices(grid);
      record(entry, Object.assign(pending, { worst: optionIndex, worst_position: position }));
    });

    stage.appendChild(grid);
    stage.appendChild(foot);
    drawRail();
    shownAt = Date.now();
    preload(state.index + 1);
  }

  /* ------------------------------------------------------------ keyboard -- */

  document.addEventListener("keydown", function (event) {
    if (!screens.run || screens.run.hidden) return;
    const grid = document.querySelector("#stage .choices");
    if (!grid) return;
    const map = { "1": 0, "2": 1, "3": 2, "4": 3, a: 0, b: 1, c: 2, d: 3,
                  ArrowLeft: 0, ArrowRight: 1 };
    const index = map[event.key] !== undefined ? map[event.key] : map[event.key.toLowerCase()];
    if (index === undefined || index >= grid.children.length) return;
    const button = grid.children[index];
    if (button.disabled) return;
    event.preventDefault();
    button.click();
  });

  /* ------------------------------------------------------------- practice -- */

  function renderPractice() {
    const host = document.getElementById("practice");
    if (!session.practice) {
      host.hidden = true;
      document.getElementById("practice-done").hidden = false;
      return;
    }
    const entry = { item: session.practice, order: session.practice.options.map(function (_, i) { return i; }) };
    host.textContent = "";
    host.appendChild(askBlock(entry.item, entry.item.question));
    const grid = renderChoices(entry, function (position) {
      markChoice(grid, position, "Chosen");
      lockChoices(grid);
      document.getElementById("practice-done").hidden = false;
    });
    host.appendChild(grid);
  }

  /* ----------------------------------------------------------- background -- */

  function wireOptionGroups() {
    document.querySelectorAll("[data-group]").forEach(function (group) {
      group.addEventListener("click", function (event) {
        const button = event.target.closest(".opt");
        if (!button) return;
        Array.from(group.querySelectorAll(".opt")).forEach(function (other) {
          other.setAttribute("aria-pressed", String(other === button));
        });
        state.background[group.dataset.group] = button.dataset.value;
        save();
      });
    });
  }

  /* ----------------------------------------------------------------- boot -- */

  function payload() {
    return {
      participant: state.pid,
      study: "mix-n-match",
      manifest_version: manifest.version,
      manifest_built_at: manifest.built_at,
      started_at: state.startedAt,
      finished_at: new Date().toISOString(),
      duration_ms: Date.now() - Date.parse(state.startedAt),
      user_agent: navigator.userAgent,
      screen: window.screen.width + "x" + window.screen.height,
      viewport: window.innerWidth + "x" + window.innerHeight,
      background: state.background,
      responses: state.responses
    };
  }

  function finish() {
    show("done");
    window.submitStudy(payload());
  }

  function begin() {
    state.startedAt = state.startedAt || new Date().toISOString();
    save();
    show("run");
    renderCurrent();
  }

  async function boot() {
    document.getElementById("mark").textContent = CONFIG.name;
    document.title = CONFIG.name;

    let response;
    try {
      response = await fetch(CONFIG.manifestUrl, { cache: "no-cache" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      manifest = await response.json();
    } catch (error) {
      document.getElementById("loading").innerHTML =
        '<p class="status__title">The questions could not be loaded.</p>' +
        "<p class=\"note\">Please refresh the page. If it keeps happening the study is " +
        "probably mid-update &mdash; try again shortly.</p>";
      return;
    }

    const saved = load();
    const stale = saved && saved.builtAt && saved.builtAt !== manifest.built_at;
    state = saved && saved.responses && !stale ? saved : {
      pid: newParticipantId(), startedAt: null, index: 0, responses: [], background: {}
    };
    state.builtAt = manifest.built_at;
    session = window.buildSession(manifest, state.pid);
    state.index = Math.min(state.index, session.items.length);

    document.getElementById("loading").hidden = true;
    document.getElementById("welcome-body").hidden = false;
    document.getElementById("preview-banner").hidden = !manifest.preview;
    document.getElementById("minutes").textContent =
      String(Math.max(6, Math.round(session.items.length * 0.33))) + " minutes";
    document.getElementById("question-count").textContent = String(session.items.length);

    if (CONFIG.contact) {
      const line = document.getElementById("contact");
      line.hidden = false;
      line.querySelector("a").href = "mailto:" + CONFIG.contact;
      line.querySelector("a").textContent = CONFIG.contact;
    }

    if (state.responses.length && state.index < session.items.length) {
      const resume = document.getElementById("resume");
      resume.hidden = false;
      resume.querySelector("b").textContent = String(state.index);
    }

    renderPractice();
    wireOptionGroups();
  }

  document.addEventListener("DOMContentLoaded", function () {
    ["welcome", "instructions", "run", "background", "done"].forEach(function (name) {
      screens[name] = document.getElementById("screen-" + name);
    });
    document.getElementById("to-instructions").addEventListener("click", function () {
      show("instructions");
    });
    document.getElementById("to-run").addEventListener("click", begin);
    document.getElementById("to-done").addEventListener("click", finish);
    document.getElementById("restart").addEventListener("click", function () {
      try { localStorage.removeItem(STORE_KEY); } catch (error) { /* nothing to clear */ }
      window.location.reload();
    });
    boot();
  });
})();

/* ---------------------------------------------------------------------------
 * The study: load the bank, draw a session, run the questions, hand the answers
 * to submit.js.
 *
 * Every screen is the same question — two whole images, three criteria, three
 * answers. Nothing here knows which method made which picture: the bank labels
 * them M1..M4 and the key that maps those back is kept out of the served site.
 * ------------------------------------------------------------------------- */

(function () {
  "use strict";

  var CONFIG = window.STUDY_CONFIG;
  var STORE_KEY = "mnm-study-v2";

  var screens = {};
  var manifest = null;
  var session = null;
  var state = null;
  var shownAt = 0;
  var answers = {};        // criterion id -> "A" | "B" | "tie", for the current screen

  /* ------------------------------------------------------------- storage -- */

  function load() {
    try {
      var raw = localStorage.getItem(STORE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (error) { return null; }
  }

  function save() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (error) { /* private window */ }
  }

  function newParticipantId() {
    var bytes = new Uint8Array(9);
    if (window.crypto && window.crypto.getRandomValues) { window.crypto.getRandomValues(bytes); }
    else { for (var i = 0; i < bytes.length; i += 1) { bytes[i] = Math.floor(Math.random() * 256); } }
    return Array.prototype.map.call(bytes, function (b) {
      return b.toString(16).padStart(2, "0");
    }).join("");
  }

  /* --------------------------------------------------------------- screen -- */

  function show(name) {
    Object.keys(screens).forEach(function (key) { screens[key].hidden = key !== name; });
    document.getElementById("bar").hidden = name !== "run";
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function drawRail() {
    var rail = document.getElementById("rail");
    if (rail.childElementCount !== session.items.length) {
      rail.textContent = "";
      session.items.forEach(function () { rail.appendChild(el("span", "rail__seg")); });
    }
    Array.prototype.forEach.call(rail.children, function (segment, index) {
      segment.dataset.state = index < state.index ? "done" : (index === state.index ? "now" : "todo");
    });
    document.getElementById("count").textContent =
      "Question " + Math.min(state.index + 1, session.items.length) + " of " + session.items.length;
  }

  /* ------------------------------------------------------------ lightbox -- */

  function openLightbox(src, label) {
    var box = el("div", "lb");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", "Image " + label + ", enlarged");
    var image = new Image();
    image.src = src;
    image.alt = "Image " + label + ", enlarged";
    var close = el("button", "lb__close", "Close");
    close.type = "button";
    box.appendChild(image);
    box.appendChild(close);
    function dismiss() {
      box.remove();
      document.removeEventListener("keydown", onKey);
    }
    function onKey(event) { if (event.key === "Escape") dismiss(); }
    box.addEventListener("click", function (event) {
      if (event.target === box || event.target === close) dismiss();
    });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(box);
    close.focus();
  }

  /* ------------------------------------------------------------ the item -- */

  var SIDES = ["A", "B"];

  function renderPair(entry) {
    var pair = el("div", "pair");
    entry.order.forEach(function (optionIndex, side) {
      var option = entry.item.options[optionIndex];
      var shot = el("div", "shot");
      shot.appendChild(el("span", "shot__tag", "Image " + SIDES[side]));

      var image = new Image();
      image.className = "shot__img";
      image.src = option.src;
      image.width = option.w;
      image.height = option.h;
      image.alt = "Image " + SIDES[side];
      image.decoding = "async";
      image.addEventListener("click", function () { openLightbox(option.src, SIDES[side]); });
      shot.appendChild(image);

      var zoom = el("button", "shot__zoom", "Enlarge");
      zoom.type = "button";
      zoom.addEventListener("click", function () { openLightbox(option.src, SIDES[side]); });
      shot.appendChild(zoom);

      pair.appendChild(shot);
    });
    return pair;
  }

  function renderPrompts(item) {
    var box = el("div", "prompts");
    box.appendChild(el("p", "prompts__h", "What both images were asked to show"));
    var list = el("ul");
    if (item.background_prompt) list.appendChild(el("li", null, item.background_prompt));
    (item.prompts || []).forEach(function (text) { list.appendChild(el("li", null, text)); });
    box.appendChild(list);
    return box;
  }

  function renderCriteria(onChange) {
    var wrap = el("div", "crits");
    manifest.criteria.forEach(function (criterion, index) {
      var card = el("div", "crit");
      card.dataset.criterion = criterion.id;
      card.appendChild(el("p", "crit__n", "Criterion " + (index + 1) + " of " +
        manifest.criteria.length + " · " + criterion.label));
      card.appendChild(el("h3", "crit__q", criterion.question));
      card.appendChild(el("p", "crit__hint", criterion.hint));

      var picks = el("div", "picks");
      picks.setAttribute("role", "group");
      picks.setAttribute("aria-label", criterion.question);
      [["A", "A wins"], ["tie", "Tie"], ["B", "B wins"]].forEach(function (choice) {
        var button = el("button", "pick" + (choice[0] === "tie" ? " pick--tie" : ""), choice[1]);
        button.type = "button";
        button.setAttribute("aria-pressed", "false");
        button.dataset.value = choice[0];
        button.addEventListener("click", function () {
          answers[criterion.id] = choice[0];
          Array.prototype.forEach.call(picks.children, function (other) {
            other.setAttribute("aria-pressed", String(other === button));
          });
          card.dataset.answered = "1";
          onChange();
        });
        picks.appendChild(button);
      });
      card.appendChild(picks);
      wrap.appendChild(card);
    });
    return wrap;
  }

  function preload(from) {
    for (var i = from; i < Math.min(from + 2, session.items.length); i += 1) {
      session.items[i].item.options.forEach(function (option) {
        var image = new Image();
        image.src = option.src;
      });
    }
  }

  function record(entry) {
    var item = entry.item;
    /* The codes, not method names: the page has never been told which is which.
       Recording the winner per criterion as a code makes the sheet readable without
       breaking that. */
    var codeA = item.options[entry.order[0]].m;
    var codeB = item.options[entry.order[1]].m;
    var winners = {};
    manifest.criteria.forEach(function (criterion) {
      var pick = answers[criterion.id];
      winners[criterion.id] = pick === "tie" ? "tie" : (pick === "A" ? codeA : codeB);
    });

    state.responses.push({
      id: item.id,
      config: item.config,
      seed: item.seed,
      set: item.set,
      num_crops: item.num_crops,
      tiles_per_crop: item.tiles_per_crop,
      combination: item.combination,
      a: entry.order[0],
      b: entry.order[1],
      a_method: codeA,
      b_method: codeB,
      answers: {
        overall: answers.overall,
        seamless: answers.seamless,
        alignment: answers.alignment
      },
      winners: winners,
      position: state.index,
      ms: Date.now() - shownAt,
      at: new Date().toISOString()
    });
    state.index += 1;
    save();
    renderCurrent();
  }

  function renderCurrent() {
    if (state.index >= session.items.length) { show("background"); return; }

    var entry = session.items[state.index];
    var stage = document.getElementById("stage");
    stage.textContent = "";
    stage.className = "";
    void stage.offsetWidth;                      /* restart the fade for the new question */
    stage.className = "fadein";
    answers = {};

    stage.appendChild(renderPair(entry));
    stage.appendChild(renderPrompts(entry.item));

    var next = el("button", "btn", "Next question");
    next.type = "button";
    next.disabled = true;
    var left = el("p", "left", "Answer all three to continue.");

    stage.appendChild(renderCriteria(function () {
      var missing = manifest.criteria.filter(function (criterion) {
        return !answers[criterion.id];
      }).length;
      next.disabled = missing > 0;
      left.textContent = missing === 0
        ? (state.index + 1 === session.items.length ? "That is the last one." : "")
        : missing + (missing === 1 ? " criterion left." : " criteria left.");
      if (missing === 0) next.focus({ preventScroll: true });
    }));

    next.addEventListener("click", function () { record(entry); });
    var footer = el("div", "next");
    footer.appendChild(next);
    stage.appendChild(footer);
    stage.appendChild(left);

    drawRail();
    shownAt = Date.now();
    preload(state.index + 1);
  }

  /* ----------------------------------------------------------- background -- */

  function wireOptionGroups() {
    document.querySelectorAll("[data-group]").forEach(function (group) {
      group.addEventListener("click", function (event) {
        var button = event.target.closest(".opt");
        if (!button) return;
        Array.prototype.forEach.call(group.querySelectorAll(".opt"), function (other) {
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

  function begin() {
    state.startedAt = state.startedAt || new Date().toISOString();
    save();
    show("run");
    renderCurrent();
  }

  function finish() {
    show("done");
    window.submitStudy(payload());
  }

  function boot() {
    document.getElementById("mark").textContent = CONFIG.name;
    document.title = CONFIG.name;

    fetch(CONFIG.manifestUrl, { cache: "no-cache" }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    }).then(function (data) {
      manifest = data;

      var saved = load();
      var stale = saved && saved.builtAt && saved.builtAt !== manifest.built_at;
      state = (saved && saved.responses && !stale) ? saved : {
        pid: newParticipantId(), startedAt: null, index: 0, responses: [], background: {}
      };
      state.builtAt = manifest.built_at;

      session = window.buildSession(manifest, state.pid);
      state.index = Math.min(state.index, session.items.length);

      document.getElementById("loading").hidden = true;
      document.getElementById("welcome-body").hidden = false;
      document.getElementById("question-count").textContent = String(session.items.length);
      document.getElementById("preview-banner").hidden = !manifest.preview;

      var defs = document.getElementById("criteria-defs");
      manifest.criteria.forEach(function (criterion) {
        var row = document.createElement("div");
        row.appendChild(el("dt", null, criterion.label));
        row.appendChild(el("dd", null, criterion.hint));
        defs.appendChild(row);
      });

      if (CONFIG.contact) {
        var line = document.getElementById("contact");
        line.hidden = false;
        line.querySelector("a").href = "mailto:" + CONFIG.contact;
        line.querySelector("a").textContent = CONFIG.contact;
      }

      if (state.responses.length && state.index < session.items.length) {
        var resume = document.getElementById("resume");
        resume.hidden = false;
        resume.querySelector("b").textContent = String(state.index);
      }

      wireOptionGroups();
    }).catch(function () {
      document.getElementById("loading").innerHTML =
        '<p class="status__title">The questions could not be loaded.</p>' +
        '<p class="note">Please refresh the page. If it keeps happening the study is ' +
        'probably mid-update &mdash; try again shortly.</p>';
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    ["welcome", "run", "background", "done"].forEach(function (name) {
      screens[name] = document.getElementById("screen-" + name);
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

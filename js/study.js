/* ---------------------------------------------------------------------------
 * The study: load the bank, draw a session, run the questions, hand the answers
 * to submit.js.
 *
 * Every screen is the same question — two sets of whole images, four criteria,
 * four answers. Image n of set A and image n of set B were made from the same
 * descriptions. Nothing here knows which method made which picture: the bank
 * labels them M1..M4 and the key that maps those back is kept out of the served site.
 * ------------------------------------------------------------------------- */

(function () {
  "use strict";

  var CONFIG = window.STUDY_CONFIG;
  var STORE_KEY = "mnm-study-v3";

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

  /* -------------------------------------------------------------- viewer -- */

  var SIDES = ["A", "B"];
  var WORDS = ["none", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];

  function optionAt(entry, side) { return entry.item.options[entry.order[side]]; }

  function promptList(item, index) {
    var list = el("ul");
    if (item.background_prompt) list.appendChild(el("li", "bgp", item.background_prompt));
    (item.prompts[index] || []).forEach(function (text) { list.appendChild(el("li", null, text)); });
    return list;
  }

  /* One picture at a time, full size. Flip between the two sets at the same number
     (tap the picture, the A/B switch, or the A, B and space keys) and step through the
     numbers (arrows, or swipe). On a phone this is where the actual comparing happens. */
  function openViewer(entry, side, index, onIndex) {
    var count = entry.item.combinations.length;
    var box = el("div", "lb");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");

    var top = el("div", "lb__top");
    var title = el("p", "lb__title");
    title.setAttribute("aria-live", "polite");
    var close = el("button", "lb__close", "Close");
    close.type = "button";
    top.appendChild(title);
    top.appendChild(close);

    var frame = el("div", "lb__frame");
    frame.tabIndex = -1;
    var image = new Image();
    image.className = "lb__img";
    frame.appendChild(image);

    var controls = el("div", "lb__controls");
    var prev = el("button", "lb__step", "‹");
    prev.type = "button";
    prev.setAttribute("aria-label", "Previous image");
    var flip = el("div", "seg");
    flip.setAttribute("role", "group");
    flip.setAttribute("aria-label", "Which set");
    var flipButtons = SIDES.map(function (label, sideIndex) {
      var button = el("button", "seg__btn", "Set " + label);
      button.type = "button";
      button.addEventListener("click", function () { side = sideIndex; draw(); });
      flip.appendChild(button);
      return button;
    });
    var next = el("button", "lb__step", "›");
    next.type = "button";
    next.setAttribute("aria-label", "Next image");
    controls.appendChild(prev);
    controls.appendChild(flip);
    controls.appendChild(next);

    var about = el("details", "lb__about");
    about.open = window.innerHeight > 640;
    about.appendChild(el("summary", null, "Descriptions for this number"));
    var aboutBody = el("div");
    about.appendChild(aboutBody);

    box.appendChild(top);
    box.appendChild(frame);
    box.appendChild(controls);
    box.appendChild(about);

    function draw() {
      var picture = optionAt(entry, side).images[index];
      image.src = picture.src;
      image.alt = "Set " + SIDES[side] + ", image " + (index + 1) + ", enlarged";
      title.textContent = "Set " + SIDES[side] + " · Image " + (index + 1) + " of " + count;
      box.dataset.side = SIDES[side];
      flipButtons.forEach(function (button, sideIndex) {
        button.setAttribute("aria-pressed", String(sideIndex === side));
      });
      prev.disabled = count < 2;
      next.disabled = count < 2;
      aboutBody.textContent = "";
      aboutBody.appendChild(promptList(entry.item, index));
      onIndex(index);
    }
    function step(delta) { index = (index + delta + count) % count; draw(); }
    function toggle() { side = 1 - side; draw(); }

    function dismiss() {
      box.remove();
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("locked");
    }
    function onKey(event) {
      if (event.key === "Escape") dismiss();
      else if (event.key === "ArrowLeft") step(-1);
      else if (event.key === "ArrowRight") step(1);
      else if (event.key === "a" || event.key === "A") { side = 0; draw(); }
      else if (event.key === "b" || event.key === "B") { side = 1; draw(); }
      else if (event.key === " " && (event.target === frame || event.target === document.body)) {
        event.preventDefault();
        toggle();
      }
    }

    prev.addEventListener("click", function () { step(-1); });
    next.addEventListener("click", function () { step(1); });
    close.addEventListener("click", dismiss);

    /* A tap flips the set; a horizontal swipe steps the number. */
    var touchX = null, touchY = null, swiped = false;
    frame.addEventListener("touchstart", function (event) {
      touchX = event.touches[0].clientX; touchY = event.touches[0].clientY; swiped = false;
    }, { passive: true });
    frame.addEventListener("touchend", function (event) {
      if (touchX === null) return;
      var dx = event.changedTouches[0].clientX - touchX;
      var dy = event.changedTouches[0].clientY - touchY;
      touchX = null;
      if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy)) { swiped = true; step(dx < 0 ? 1 : -1); }
    });
    frame.addEventListener("click", function (event) {
      if (swiped) { swiped = false; return; }
      if (event.target === image) toggle(); else dismiss();
    });

    document.addEventListener("keydown", onKey);
    document.body.appendChild(box);
    document.body.classList.add("locked");
    draw();
    frame.focus({ preventScroll: true });
  }

  /* ------------------------------------------------------------ the item -- */

  /* The number currently in focus: outlined in both sets, and the one whose
     descriptions are shown. */
  function makeFocus(stage, tabs, panel, item) {
    return function focusOn(index) {
      Array.prototype.forEach.call(stage.querySelectorAll(".shot"), function (shot) {
        shot.dataset.active = String(Number(shot.dataset.index) === index);
      });
      tabs.forEach(function (tab, tabIndex) {
        tab.setAttribute("aria-selected", String(tabIndex === index));
        tab.tabIndex = tabIndex === index ? 0 : -1;
      });
      panel.textContent = "";
      panel.appendChild(promptList(item, index));
    };
  }

  function columnsFor(count) { return Math.max(1, Math.ceil(Math.sqrt(count))); }

  function renderSets(entry, focus) {
    var count = entry.item.combinations.length;
    var columns = columnsFor(count);
    /* Roughly how wide one thumbnail is drawn, so the browser fetches the smaller
       file unless the screen really needs the full one. */
    var sizes = "(max-width: 760px) and (orientation: portrait) calc(100vw / " + columns + "), " +
      "calc(min(100vw, 1180px) / " + (2 * columns) + ")";

    var sets = el("div", "sets");
    sets.style.setProperty("--cols", String(columns));
    SIDES.forEach(function (label, side) {
      var option = optionAt(entry, side);
      var group = el("section", "set");
      group.dataset.side = label;
      group.setAttribute("aria-label", "Set " + label);
      group.appendChild(el("h2", "set__h", "Set " + label));
      var grid = el("div", "set__grid");
      option.images.forEach(function (picture, index) {
        var shot = el("button", "shot");
        shot.type = "button";
        shot.dataset.index = String(index);
        shot.setAttribute("aria-label", "Set " + label + ", image " + (index + 1) + ". Enlarge");
        var image = new Image();
        image.className = "shot__img";
        image.src = picture.thumb;
        image.srcset = picture.thumb + " " + picture.tw + "w, " + picture.src + " " + picture.w + "w";
        image.sizes = sizes;
        image.width = picture.w;
        image.height = picture.h;
        image.alt = "";
        image.decoding = "async";
        shot.appendChild(image);
        shot.appendChild(el("span", "shot__n", String(index + 1)));
        shot.addEventListener("click", function () { openViewer(entry, side, index, focus); });
        shot.addEventListener("mouseenter", function () { focus(index); });
        shot.addEventListener("focus", function () { focus(index); });
        grid.appendChild(shot);
      });
      group.appendChild(grid);
      sets.appendChild(group);
    });
    return sets;
  }

  function renderPrompts(item, onPick) {
    var count = item.combinations.length;
    var box = el("div", "prompts");
    var head = el("div", "prompts__head");
    head.appendChild(el("p", "prompts__h", "Descriptions for image"));
    var bar = el("div", "tabs");
    bar.setAttribute("role", "tablist");
    bar.setAttribute("aria-label", "Image number");
    var tabs = [];
    for (var i = 0; i < count; i += 1) {
      (function (index) {
        var tab = el("button", "tab", String(index + 1));
        tab.type = "button";
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-label", "Image " + (index + 1));
        tab.addEventListener("click", function () { onPick(index); });
        tab.addEventListener("keydown", function (event) {
          var move = event.key === "ArrowRight" ? 1 : (event.key === "ArrowLeft" ? -1 : 0);
          if (!move) return;
          event.preventDefault();
          var target = (index + move + count) % count;
          onPick(target);
          tabs[target].focus();
        });
        tabs.push(tab);
        bar.appendChild(tab);
      })(i);
    }
    head.appendChild(bar);
    box.appendChild(head);
    var panel = el("div", "prompts__body");
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-live", "polite");
    box.appendChild(panel);
    box.appendChild(el("p", "prompts__note",
      "The same number shows the same descriptions in both sets."));
    return { node: box, tabs: tabs, panel: panel };
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
      [["A", "Set A"], ["tie", "Tie"], ["B", "Set B"]].forEach(function (choice) {
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

  /* The next question's thumbnails. Full-size files load only when enlarged. */
  function preload(from) {
    for (var i = from; i < Math.min(from + 1, session.items.length); i += 1) {
      session.items[i].item.options.forEach(function (option) {
        option.images.forEach(function (picture) {
          var image = new Image();
          image.src = picture.thumb;
        });
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
    var picks = {};
    var winners = {};
    manifest.criteria.forEach(function (criterion) {
      var pick = answers[criterion.id];
      picks[criterion.id] = pick;
      winners[criterion.id] = pick === "tie" ? "tie" : (pick === "A" ? codeA : codeB);
    });

    state.responses.push({
      id: item.id,
      config: item.config,
      seed: item.seed,
      set: item.set,
      num_crops: item.num_crops,
      tiles_per_crop: item.tiles_per_crop,
      combinations: item.combinations,
      a: entry.order[0],
      b: entry.order[1],
      a_method: codeA,
      b_method: codeB,
      answers: picks,
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

    var count = entry.item.combinations.length;
    stage.appendChild(el("p", "guide", count > 1
      ? "Two sets of " + (WORDS[count] || count) + " images. Each number shows the same descriptions " +
        "in both sets. Tap any image to enlarge it and flip between Set A and Set B."
      : "Two images made from the same descriptions. Tap either to enlarge it and flip between them."));
    var prompts = renderPrompts(entry.item, function (index) { focus(index); });
    var focus = makeFocus(stage, prompts.tabs, prompts.panel, entry.item);
    stage.appendChild(renderSets(entry, focus));
    stage.appendChild(prompts.node);
    focus(0);

    var next = el("button", "btn", "Next question");
    next.type = "button";
    next.disabled = true;
    var left = el("p", "left",
      "Answer all " + WORDS[manifest.criteria.length] + " to continue.");

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
      var perSet = manifest.combinations_per_method || 1;
      document.querySelectorAll("[data-per-set]").forEach(function (node) {
        node.textContent = WORDS[perSet] || String(perSet);
      });
      var count = manifest.criteria.length;
      document.querySelectorAll("[data-criteria-count]").forEach(function (node) {
        node.textContent = WORDS[count] || String(count);
      });
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

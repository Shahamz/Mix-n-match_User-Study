/* ---------------------------------------------------------------------------
 * Draws one participant's session out of the item bank.
 *
 * The draw is seeded from the participant id, so a refresh mid-study rebuilds
 * exactly the same sequence rather than starting a different one.
 * ------------------------------------------------------------------------- */

(function () {
  "use strict";

  function seedFrom(text) {
    let hash = 0x811c9dc5;
    for (let i = 0; i < text.length; i += 1) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash >>> 0;
  }

  /* mulberry32: small, fast, and good enough for shuffling a questionnaire. */
  function makeRandom(seed) {
    let state = seed >>> 0;
    return function random() {
      state = (state + 0x6d2b79f5) >>> 0;
      let t = state;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function shuffled(list, random) {
    const copy = list.slice();
    for (let i = copy.length - 1; i > 0; i -= 1) {
      const j = Math.floor(random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  }

  /* Prefer items from configs this participant has not seen yet, so a session
     spreads over many prompts instead of hammering one. */
  function takeSpread(pool, wanted, seenConfigs, random) {
    const picked = [];
    const remaining = shuffled(pool, random);
    while (picked.length < wanted && remaining.length) {
      let index = remaining.findIndex(function (item) { return !seenConfigs.has(item.config); });
      if (index === -1) {
        /* Every config is spoken for; start a fresh pass so later question types
           spread over the configs too instead of clustering. */
        seenConfigs.clear();
        index = remaining.findIndex(function (item) { return !seenConfigs.has(item.config); });
        if (index === -1) index = 0;
      }
      const item = remaining.splice(index, 1)[0];
      seenConfigs.add(item.config);
      picked.push(item);
    }
    return picked;
  }

  /* Spread the attention checks through the run and avoid two questions about
     the same config landing back to back. */
  function arrange(scored, attention, random) {
    const order = shuffled(scored, random);
    for (let i = 1; i < order.length; i += 1) {
      if (order[i].config !== order[i - 1].config) continue;
      const swap = order.findIndex(function (item, j) {
        return j > i && item.config !== order[i - 1].config;
      });
      if (swap !== -1) [order[i], order[swap]] = [order[swap], order[i]];
    }
    attention.forEach(function (check, index) {
      const span = Math.floor(order.length / (attention.length + 1));
      const at = Math.min(order.length, span * (index + 1) + index);
      order.splice(at, 0, check);
    });
    return order;
  }

  window.buildSession = function buildSession(manifest, participantId) {
    const random = makeRandom(seedFrom(participantId));
    const session = manifest.session || {};
    const target = session.items_per_participant || 30;
    const composition = session.composition || {};

    const byType = {};
    manifest.items.forEach(function (item) {
      (byType[item.type] = byType[item.type] || []).push(item);
    });

    const seenConfigs = new Set();
    const chosen = [];
    const used = new Set();

    Object.keys(composition).forEach(function (type) {
      const pool = (byType[type] || []).filter(function (item) { return !used.has(item.id); });
      takeSpread(pool, composition[type], seenConfigs, random).forEach(function (item) {
        used.add(item.id);
        chosen.push(item);
      });
    });

    /* If a type ran short (a small item bank, or a baseline missing from this
       build), backfill with anything else rather than serving a short session. */
    if (chosen.length < target) {
      const rest = manifest.items.filter(function (item) {
        return !used.has(item.id) && item.type !== "attention";
      });
      takeSpread(rest, target - chosen.length, seenConfigs, random).forEach(function (item) {
        used.add(item.id);
        chosen.push(item);
      });
    }

    const attention = chosen.filter(function (item) { return item.type === "attention"; });
    const scored = chosen.filter(function (item) { return item.type !== "attention"; })
      .slice(0, Math.max(0, target - attention.length));

    /* One unscored warm-up, shown inside the instructions. */
    const practicePool = (byType.tile_ab_adherence || []).filter(function (item) {
      return !scored.some(function (other) { return other.id === item.id; });
    });
    const practice = practicePool.length
      ? practicePool[Math.floor(random() * practicePool.length)]
      : null;

    return {
      practice: practice,
      items: arrange(scored, attention, random).map(function (item) {
        /* Randomise which side each picture appears on, per participant. */
        const order = shuffled(item.options.map(function (_, i) { return i; }), random);
        return { item: item, order: order };
      })
    };
  };
})();

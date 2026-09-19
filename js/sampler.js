/* ---------------------------------------------------------------------------
 * Draws one participant's session out of the item bank.
 *
 * Every item in the bank is the same question: a set of our composites against
 * the matching set of one baseline's, judged on four criteria. The draw picks
 * which comparisons this participant gets, spread across configs and balanced
 * across the baselines, and decides which set is shown as A and which as B.
 *
 * Seeded from the participant id, so a refresh mid-study rebuilds the same
 * sequence rather than starting a different one.
 * ------------------------------------------------------------------------- */

(function () {
  "use strict";

  function seedFrom(text) {
    var hash = 0x811c9dc5;
    for (var i = 0; i < text.length; i += 1) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return hash >>> 0;
  }

  /* mulberry32: small, fast, good enough for shuffling a questionnaire. */
  function makeRandom(seed) {
    var state = seed >>> 0;
    return function random() {
      state = (state + 0x6d2b79f5) >>> 0;
      var t = state;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function shuffled(list, random) {
    var copy = list.slice();
    for (var i = copy.length - 1; i > 0; i -= 1) {
      var j = Math.floor(random() * (i + 1));
      var swap = copy[i]; copy[i] = copy[j]; copy[j] = swap;
    }
    return copy;
  }

  /* Which baseline an item compares against — the option that is not ours. */
  function opponentOf(item, reference) {
    for (var i = 0; i < item.options.length; i += 1) {
      if (item.options[i].m !== reference) return item.options[i].m;
    }
    return "?";
  }

  window.buildSession = function buildSession(manifest, participantId) {
    var random = makeRandom(seedFrom(participantId));
    var session = manifest.session || {};
    var target = Math.min(session.items_per_participant || 30, manifest.items.length);
    var reference = manifest.reference_code || "M1";

    /* Group by baseline so the three comparisons get equal airtime, then take
       from each in turn, preferring configs this participant has not seen. */
    var byOpponent = {};
    manifest.items.forEach(function (item) {
      var key = opponentOf(item, reference);
      (byOpponent[key] = byOpponent[key] || []).push(item);
    });
    Object.keys(byOpponent).forEach(function (key) {
      byOpponent[key] = shuffled(byOpponent[key], random);
    });

    var order = shuffled(Object.keys(byOpponent), random);
    var seen = {};
    var chosen = [];
    var used = {};
    var guard = 0;

    while (chosen.length < target && guard < 10000) {
      guard += 1;
      var progressed = false;
      for (var k = 0; k < order.length && chosen.length < target; k += 1) {
        var pool = byOpponent[order[k]];
        var pick = -1;
        for (var i = 0; i < pool.length; i += 1) {
          if (!used[pool[i].id] && !seen[pool[i].config]) { pick = i; break; }
        }
        if (pick === -1) {
          for (var j = 0; j < pool.length; j += 1) {
            if (!used[pool[j].id]) { pick = j; break; }
          }
        }
        if (pick === -1) continue;
        used[pool[pick].id] = true;
        seen[pool[pick].config] = true;
        chosen.push(pool[pick]);
        progressed = true;
      }
      if (!progressed) break;
      /* Once every config has been drawn from, start a fresh pass. */
      if (chosen.length % Object.keys(seen).length === 0) seen = {};
    }

    /* Avoid two questions about the same config back to back. */
    var run = shuffled(chosen, random);
    for (var m = 1; m < run.length; m += 1) {
      if (run[m].config !== run[m - 1].config) continue;
      for (var n = m + 1; n < run.length; n += 1) {
        if (run[n].config !== run[m - 1].config) {
          var hold = run[m]; run[m] = run[n]; run[n] = hold;
          break;
        }
      }
    }

    return {
      items: run.map(function (item) {
        /* Which option is shown as A and which as B, per participant, so neither
           method sits on a fixed side. order[0] is A, order[1] is B. */
        return { item: item, order: shuffled([0, 1], random) };
      })
    };
  };
})();

/* ---------------------------------------------------------------------------
 * Sends one finished session to the Apps Script endpoint.
 *
 * The request goes out as text/plain on purpose: that is a CORS "simple"
 * request, so the browser skips the preflight OPTIONS that Apps Script web
 * apps do not answer. Apps Script reads the body from e.postData.contents
 * either way.
 * ------------------------------------------------------------------------- */

(function () {
  "use strict";

  const CONFIG = window.STUDY_CONFIG;
  const ATTEMPTS = 3;

  function setStatus(kind, title, detail) {
    const box = document.getElementById("submit-status");
    box.className = "status" + (kind === "warn" ? " status--warn" : "");
    box.innerHTML = "";
    const heading = document.createElement("p");
    heading.className = "status__title";
    heading.innerHTML = title;
    box.appendChild(heading);
    if (detail) {
      const note = document.createElement("p");
      note.className = "note";
      note.style.margin = "0";
      note.innerHTML = detail;
      box.appendChild(note);
    }
  }

  function offerDownload(data) {
    const button = document.getElementById("download");
    button.hidden = false;
    button.onclick = function () {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "study-answers-" + data.participant + ".json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    };
  }

  function wait(ms) {
    return new Promise(function (resolve) { window.setTimeout(resolve, ms); });
  }

  window.submitStudy = async function submitStudy(data) {
    if (!CONFIG.endpoint) {
      setStatus("warn", "Your answers are ready to send.",
        "This copy of the study has no submission address configured yet. " +
        "Please download the file below and send it to whoever shared the link.");
      offerDownload(data);
      return;
    }

    setStatus("", '<span class="spinner"></span> Sending your answers&hellip;', "");

    for (let attempt = 1; attempt <= ATTEMPTS; attempt += 1) {
      try {
        const response = await fetch(CONFIG.endpoint, {
          method: "POST",
          headers: { "Content-Type": "text/plain;charset=utf-8" },
          body: JSON.stringify(data)
        });
        if (!response.ok) throw new Error("HTTP " + response.status);
        setStatus("", "Your answers are in. Thank you.",
          "You can close this tab now.");
        try { localStorage.removeItem("mnm-study-v2"); } catch (error) { /* already gone */ }
        return;
      } catch (error) {
        if (attempt < ATTEMPTS) await wait(attempt * 1200);
      }
    }

    setStatus("warn", "Your answers could not be sent.",
      "Nothing is lost. Download the file below and send it to whoever shared the " +
      "link with you, and your responses will be counted.");
    offerDownload(data);
  };
})();

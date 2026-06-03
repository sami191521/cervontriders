/* Tour of Belize — live API adapter (handoff §1, §9, §10).
 *
 * Loaded AFTER the page's main script. It activates ONLY when window.TOB_API is
 * set (i.e. when the page is served over http by the FastAPI backend). Opened
 * straight from disk (file://) TOB_API is empty, the adapter no-ops, and the
 * board behaves exactly like the original per-device reference.
 *
 * What it does in live mode:
 *  - loads shared config + profiles + standings from the server and re-renders
 *  - polls /api/standings so every TV / phone sees the same data
 *  - reroutes the per-device "save" hooks (saveP, persist*) to the API
 *  - replaces the admin/admin curtain with real server login (bearer token)
 *  - sends admin uploads to the server (server owns movement/weekly/streak)
 */
(function () {
  "use strict";
  var API = window.TOB_API;
  if (!API) return; // file:// → keep original offline behavior

  var POLL_MS = 25000;
  var didFilters = false;

  function token() { return sessionStorage.getItem("tob_token") || ""; }
  function authHeaders() {
    var h = { "Content-Type": "application/json" };
    if (token()) h["Authorization"] = "Bearer " + token();
    return h;
  }

  /* ---------- render helpers (call the page's globals) ---------- */
  function renderAll() {
    renderStandings(); renderGrid(); renderTop10();
    renderCeremony(); refreshPeloton(); renderLeaderboard();
    if (!didFilters) { buildFilters(); didFilters = true; }
  }

  /* ---------- load ---------- */
  async function loadConfigAndProfiles() {
    try {
      var [cfgR, profR] = await Promise.all([
        fetch(API + "/api/config"),
        fetch(API + "/api/profiles"),
      ]);
      if (cfgR.ok) applyConfig(await cfgR.json());
      if (profR.ok) {
        var profs = await profR.json();
        Object.keys(profs).forEach(function (id) { profiles[id] = profs[id]; });
      }
    } catch (e) { console.warn("[TOB] config/profiles load failed", e); }
  }

  async function loadStandings() {
    try {
      var m = (typeof raceMetric !== "undefined" ? raceMetric : "sales");
      var r = await fetch(API + "/api/standings?metric=" + encodeURIComponent(m));
      if (!r.ok) return;
      var data = await r.json();
      if (!data.riders || !data.riders.length) { renderAll(); return; } // keep seed if empty

      AGENTS = data.riders;                  // server riders share the UI's keys
      if (data.lastUpdated) lastUpdated = data.lastUpdated;

      // recompute() rebuilds ranks/jerseys but zeroes movement (no local prev),
      // so stash the server's authoritative movement/weekly/streak and restore it.
      var byId = {};
      AGENTS.forEach(function (a) { byId[a.id] = a; });
      recompute();
      AGENTS.forEach(function (a) {
        var s = byId[a.id];
        if (s) { a.dRank = s.dRank; a.dSales = s.dSales; a.week = s.week; a.streak = s.streak; }
      });
      renderAll();
    } catch (e) { console.warn("[TOB] standings load failed", e); }
  }

  function applyConfig(cfg) {
    if (cfg.raceMetric) {
      raceMetric = cfg.raceMetric;
      var rr = document.querySelector('input[name="raceMetric"][value="' + raceMetric + '"]');
      if (rr) rr.checked = true;
    }
    teamGoal = cfg.teamGoal || 0;
    var gi = document.getElementById("teamGoalInput");
    if (gi) gi.value = teamGoal || "";
    if (Array.isArray(cfg.visibleStats)) {
      visibleStats = new Set(cfg.visibleStats);
      visibleStats.add("ld");
      buildStatToggles();
    }
    if (cfg.videos) {
      ["intro", "stage", "finale"].forEach(function (k) {
        if (cfg.videos[k] != null) {
          videos[k] = cfg.videos[k];
          var box = document.querySelector('.vidset[data-key="' + k + '"] .vid-url');
          if (box) box.value = videos[k];
        }
      });
      if (typeof refreshVideoButtons === "function") refreshVideoButtons();
    }
    if (cfg.lastUpdated) lastUpdated = cfg.lastUpdated;
  }

  /* ---------- writes: reroute the per-device hooks to the API ---------- */
  // these are top-level function declarations in the main script → global & reassignable
  window.saveP = async function (id) {
    try {
      await fetch(API + "/api/riders/" + encodeURIComponent(id) + "/profile",
        { method: "PUT", headers: authHeaders(), body: JSON.stringify(profiles[id] || {}) });
    } catch (e) { console.warn("[TOB] profile save failed", e); }
  };

  async function putConfig(patch) {
    try {
      await fetch(API + "/api/config",
        { method: "PUT", headers: authHeaders(), body: JSON.stringify(patch) });
    } catch (e) { console.warn("[TOB] config save failed", e); }
  }
  window.persistMeta = function () { putConfig({ raceMetric: raceMetric, teamGoal: teamGoal }); };
  window.persistStats = function () { putConfig({ visibleStats: [].concat([...visibleStats]) }); };
  window.persistVideos = function () { putConfig({ videos: videos }); };

  // admin upload: the page parses+normalizes locally, then calls persistDataset().
  // Reroute that to the server so movement/weekly/streak stay authoritative.
  window.persistDataset = async function () {
    try {
      var r = await fetch(API + "/api/ingest/json",
        { method: "POST", headers: authHeaders(), body: JSON.stringify({ riders: AGENTS }) });
      if (r.ok) { await loadStandings(); }
      else if (r.status === 401) { alert("Admin login required to upload. Open Admin and log in."); }
    } catch (e) { console.warn("[TOB] ingest failed", e); }
  };

  /* ---------- real admin login (replaces admin/admin curtain) ---------- */
  async function serverLogin() {
    var u = (document.getElementById("admUser").value || "").trim();
    var p = document.getElementById("admPass").value || "";
    var err = document.getElementById("admErr");
    try {
      var r = await fetch(API + "/api/auth/login",
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ user: u, pass: p }) });
      if (!r.ok) { err.textContent = "Incorrect username or password."; return; }
      var d = await r.json();
      sessionStorage.setItem("tob_token", d.token);
      window.adminUnlocked = true;
      document.getElementById("adminLogin").classList.remove("show");
      var corner = document.getElementById("adminCorner");
      if (corner) corner.textContent = "⚙ Admin";
      if (typeof openAdmin === "function") openAdmin();
    } catch (e) { err.textContent = "Login failed — is the server reachable?"; }
  }

  // rebind the login controls (cloning drops the original admin/admin listeners)
  function rebindLogin() {
    var btn = document.getElementById("admLoginBtn");
    if (btn) {
      var b2 = btn.cloneNode(true);
      btn.parentNode.replaceChild(b2, btn);
      b2.addEventListener("click", serverLogin);
    }
    var pass = document.getElementById("admPass");
    if (pass) {
      var p2 = pass.cloneNode(true);
      pass.parentNode.replaceChild(p2, pass);
      p2.addEventListener("keydown", function (e) { if (e.key === "Enter") serverLogin(); });
    }
  }

  /* ---------- boot ---------- */
  async function boot() {
    rebindLogin();
    await loadConfigAndProfiles();
    await loadStandings();
    setInterval(loadStandings, POLL_MS);
    console.info("[TOB] live mode on →", API);
  }
  // main script already ran (we're at end of body); defer one tick to be safe
  setTimeout(boot, 0);
})();

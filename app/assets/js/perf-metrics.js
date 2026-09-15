/* Sampled numeric Web Vitals. No URLs with queries, visitor IDs, or device attributes. */
(function () {
  "use strict";
  const rate = Number(window.PERF_SAMPLE_RATE || 0);
  if (window.MINIMAL_DATA_MODE || navigator.doNotTrack === "1" || navigator.globalPrivacyControl ||
      !Number.isFinite(rate) || rate <= 0 || Math.random() >= Math.min(rate, 1) || !window.webVitals) return;

  const fields = {LCP: "lcp_ms", CLS: "cls", FCP: "fcp_ms", TTFB: "ttfb_ms", INP: "inp_ms"};
  const metrics = {};
  let sent = false;
  function record(metric) {
    const field = fields[metric.name];
    if (!field || !Number.isFinite(metric.value) || metric.value < 0) return;
    const maximum = field === "cls" ? 10 : 60000;
    if (metric.value > maximum) return;
    metrics[field] = field === "cls" ? Math.round(metric.value * 100) / 100 : Math.round(metric.value / 50) * 50;
  }
  for (const name of Object.keys(fields)) window.webVitals[`on${name}`](record, {reportAllChanges: true});

  function send() {
    if (sent || !Object.keys(metrics).length) return;
    sent = true;
    let path = window.location.pathname;
    const parts = path.split("/");
    if ((window.SERVER_LANGUAGE_CODES || []).includes(parts[1])) path = "/" + parts.slice(2).join("/");
    path = path.replace(/\/$/, "") || "/";
    if (path === "/configure") path = "/configure/calendar";
    const pages = ["/", "/configure/calendar", "/configure/teams", "/credits", "/privacy", "/stats", "/changelog", "/api/docs/html"];
    if (!pages.includes(path)) path = "/other";
    const body = new Blob([JSON.stringify({page_path: path, measurement_version: 3, ...metrics})], {type: "application/json"});
    if (!navigator.sendBeacon || !navigator.sendBeacon("/api/perf-metrics", body)) {
      fetch("/api/perf-metrics", {method: "POST", body, keepalive: true, credentials: "omit", referrerPolicy: "no-referrer"}).catch(() => {});
    }
  }
  // Library capture listeners finalize metrics before this visibility listener.
  // pagehide may precede visibilitychange: do not lock in unfinished metrics.
  function sendWhenHidden() { if (document.visibilityState === "hidden") send(); }
  document.addEventListener("visibilitychange", sendWhenHidden);
  window.addEventListener("pagehide", sendWhenHidden);
})();

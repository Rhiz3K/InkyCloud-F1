import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import {test} from "node:test";

const common = readFileSync("app/assets/js/common.js", "utf8");
const collector = readFileSync("app/assets/js/perf-metrics.js", "utf8");

for (const zone of ["UTC", "Europe/Prague", "America/New_York"]) {
    test(`race dates are calendar dates in ${zone}`, () => {
        process.env.TZ = zone;
        const ctx = vm.createContext({
            window: {location: {pathname: "/configure"}}, navigator: {},
            document: {addEventListener() {}},
        });
        vm.runInContext(common, ctx);
        assert.equal(ctx.formatRaceDate("2026-09-06"), "06.09.");
        assert.equal(ctx.formatRaceDate("2026-01-01"), "01.01.");
        assert.equal(ctx.formatRaceDate("2028-02-29"), "29.02.");
        for (const invalid of [null, "", "oops", "2026-02-29", "2026-13-01", "2026-09-06T12:00:00Z"]) {
            assert.equal(ctx.formatRaceDate(invalid), "");
        }
        const template = readFileSync("app/templates/configure.html", "utf8");
        assert.equal(template.split("formatRaceDate(race.date)").length - 1, 2);
    });
}

for (const minimal of [true, false]) {
    for (const blockedStorage of [false, true]) {
        test(`language choice persists with minimal=${minimal}, blocked=${blockedStorage}`, () => {
            const saved = [], cookies = [], redirects = [];
            const ctx = vm.createContext({
                URL, URLSearchParams,
                window: {MINIMAL_DATA_MODE: minimal, location: {
                    origin: "https://racing.example.org", pathname: "/configure/calendar",
                    search: "?display=spectra6", replace(url) {redirects.push(url);},
                }}, navigator: {},
                document: {addEventListener() {}, set cookie(value) {cookies.push(value);}, getElementById() {return {value: "cs"};}},
                localStorage: {
                    getItem() {if (blockedStorage) throw Error("blocked"); return "cs";},
                    setItem(key, value) {if (blockedStorage) throw Error("blocked"); saved.push([key, value]);},
                },
            });
            vm.runInContext(common, ctx);
            assert.equal(redirects.length, blockedStorage ? 0 : 1);
            ctx.switchUiLanguage();
            assert.equal(ctx.window.location.href, "https://racing.example.org/cs/configure/calendar?display=spectra6");
            assert.ok(cookies.at(-1).startsWith("preferredLang=cs;"));
            assert.deepEqual(saved.at(-1), blockedStorage ? undefined : ["preferredLang", "cs"]);
        });
    }
}

test("explicit localized URL overrides stale preference", () => {
    const saved = [];
    vm.runInNewContext(common, {
        window: {location: {pathname: "/de/configure/calendar"}}, navigator: {},
        document: {addEventListener() {}, set cookie(_) {}},
        localStorage: {getItem() {throw Error("Stale preference read");}, setItem(...args) {saved.push(args);}},
    });
    assert.deepEqual(saved, [["preferredLang", "de"]]);
});

function runCollector(overrides = {}) {
    const callbacks = {}, events = {}, posts = [];
    const math = Object.create(Math); math.random = () => 0.05;
    const doc = {visibilityState: "visible", addEventListener(name, fn) {events[name] = fn;}};
    const nav = {sendBeacon(url, body) {posts.push({url, body}); return true;},
        get userAgent() {throw Error("User agent read");}, get connection() {throw Error("Connection read");}, get deviceMemory() {throw Error("Device memory read");}};
    const win = {PERF_SAMPLE_RATE: .1, SERVER_LANGUAGE_CODES: ["cs", "en"],
        location: {pathname: "/cs/configure/calendar", get search() {throw Error("Query read");}},
        webVitals: Object.fromEntries(["LCP", "CLS", "FCP", "TTFB", "INP"].map(name => [`on${name}`, callback => {callbacks[name] = callback;}])),
        addEventListener(name, fn) {events[name] = fn;}, ...overrides};
    vm.runInNewContext(collector, {window: win, document: doc, navigator: nav, Math: math, Blob});
    return {callbacks, events, posts, doc, nav};
}

test("one coarse numeric beacon per sampled page, with no identity or device fields", async () => {
    const {callbacks, events, posts, doc} = runCollector();
    callbacks.LCP({name: "LCP", value: 1234, id: "private", entries: ["private"]});
    callbacks.CLS({name: "CLS", value: .12345});
    callbacks.INP({name: "INP", value: 201});
    callbacks.FCP({name: "FCP", value: Infinity});
    callbacks.TTFB({name: "TTFB", value: -1});
    events.visibilitychange();
    assert.equal(posts.length, 0);
    doc.visibilityState = "hidden"; events.visibilitychange(); events.pagehide();
    assert.equal(posts.length, 1);
    assert.equal(posts[0].url, "/api/perf-metrics");
    assert.deepEqual(JSON.parse(await posts[0].body.text()), {page_path: "/configure/calendar", measurement_version: 3, lcp_ms: 1250, cls: .12, inp_ms: 200});
});

test("early pagehide waits for finalized metrics and sends the latest values once", async () => {
    const {callbacks, events, posts, doc} = runCollector();
    callbacks.FCP({name: "FCP", value: 150});
    events.pagehide();
    assert.equal(posts.length, 0);
    callbacks.LCP({name: "LCP", value: 200});
    callbacks.LCP({name: "LCP", value: 950});
    callbacks.CLS({name: "CLS", value: 0});
    doc.visibilityState = "hidden";
    events.visibilitychange();
    events.pagehide();
    assert.equal(posts.length, 1);
    assert.deepEqual(JSON.parse(await posts[0].body.text()), {
        page_path: "/configure/calendar", measurement_version: 3, fcp_ms: 150, lcp_ms: 950, cls: 0,
    });
});

for (const overrides of [{MINIMAL_DATA_MODE: true}, {PERF_SAMPLE_RATE: 0}, {PERF_SAMPLE_RATE: .01}, {webVitals: null}]) {
    test(`collector opt-out or sampling ${JSON.stringify(overrides)}`, () => {
        const {callbacks, posts} = runCollector(overrides);
        assert.equal(Object.keys(callbacks).length, 0);
        assert.equal(posts.length, 0);
    });
}

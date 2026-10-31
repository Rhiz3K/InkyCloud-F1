import assert from "node:assert/strict";
import {readdirSync, readFileSync} from "node:fs";
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
            assert.deepEqual(cookies, []);
            assert.deepEqual(saved, []);
            ctx.switchUiLanguage();
            assert.equal(ctx.window.location.href, "https://racing.example.org/cs/configure/calendar?display=spectra6");
            assert.deepEqual(cookies, ["preferredLang=cs;path=/;max-age=2592000;SameSite=Lax"]);
            assert.deepEqual(saved, blockedStorage ? [] : [["preferredLang", "cs"]]);
        });
    }
}

for (const [pathname, stored] of [["/de/configure/calendar", "cs"], ["/cs/configure/calendar", "cs"], ["/configure/calendar", "cs"], ["/", "en"]]) {
    test(`loading ${pathname} with stored ${stored} writes no cookie or localStorage`, () => {
        const saved = [], cookies = [], redirects = [];
        vm.runInNewContext(common, {
            URL, URLSearchParams,
            window: {location: {origin: "https://racing.example.org", pathname, search: "", replace(url) {redirects.push(url);}}},
            navigator: {},
            document: {addEventListener() {}, set cookie(value) {cookies.push(value);}},
            localStorage: {getItem() {return stored;}, setItem(...args) {saved.push(args);}},
        });
        assert.deepEqual(cookies, []);
        assert.deepEqual(saved, []);
        const prefixed = pathname.startsWith("/de/") || pathname.startsWith("/cs/");
        assert.deepEqual(redirects, prefixed || stored === "en" ? [] : ["https://racing.example.org/cs/configure/calendar"]);
    });
}

for (const [protocol, suffix] of [["http:", ";SameSite=Lax"], ["https:", ";SameSite=Lax;Secure"]]) {
    test(`language cookie over ${protocol} ends with ${suffix}`, () => {
        const cookies = [];
        const ctx = vm.createContext({
            URL, URLSearchParams,
            window: {location: {
                origin: `${protocol}//racing.example.org`, protocol, pathname: "/cs/configure", search: "",
                replace() {},
            }}, navigator: {},
            document: {addEventListener() {}, set cookie(value) {cookies.push(value);}, getElementById() {return {value: "de"};}},
            localStorage: {getItem() {return null;}, setItem() {}},
        });
        vm.runInContext(common, ctx);
        ctx.switchUiLanguage();
        assert.equal(cookies.length, 1);
        assert.ok(cookies[0].endsWith(suffix), cookies[0]);
        assert.equal(cookies[0].includes("Secure"), protocol === "https:");
        assert.ok(cookies[0].startsWith("preferredLang=de;path=/;max-age=2592000;"));
    });
}

// Runs common.js with the given globals defined first, as inline page scripts run
// before the deferred common.js; window is the global object, as in browsers.
function runCommonWithGlobals(globals) {
    const listeners = {};
    const sandbox = {location: {pathname: "/"}, navigator: {}, console: {error() {}},
        document: {addEventListener(type, fn) {listeners[type] = fn;}}, ...globals};
    sandbox.window = sandbox;
    const ctx = vm.createContext(sandbox);
    vm.runInContext(common, ctx);
    return {ctx, listeners};
}

function declaredClick(attrs) {
    const element = {getAttribute(name) {return attrs[name] ?? null;}};
    const target = {closest(selector) {return selector === "[data-on-click]" ? element : null;}};
    return {element, event: {type: "click", target}};
}

test("declared data-on-click handlers run in order with the element as this", () => {
    const calls = [];
    const {listeners} = runCommonWithGlobals({notFn: 42,
        setDisplayType(...args) {calls.push(["setDisplayType", this, args]);},
        switchTab(...args) {calls.push(["switchTab", this, args]);}});
    assert.deepEqual(Object.keys(listeners).sort(), ["DOMContentLoaded", "change", "click", "error", "input"]);
    const {element, event} = declaredClick({
        "data-on-click": "setDisplayType selectRaceMobile switchTab notFn toString", "data-args": "[\"x\"]",
    });
    listeners.click(event);
    assert.equal(calls.length, 2);
    assert.deepEqual(calls.map(([name, self, args]) => [name, self === element, args]),
        [["setDisplayType", true, ["x"]], ["switchTab", true, ["x"]]]);
    listeners.change({type: "change", target: event.target});
    listeners.click({type: "click", target: {closest() {return null;}}});
    assert.equal(calls.length, 2);
});

test("declared handlers never call names outside the allowlist", () => {
    const calls = [];
    const spy = (name) => (...args) => {calls.push([name, args]);};
    const {listeners} = runCommonWithGlobals({
        fetch: spy("fetch"), open: spy("open"), eval: spy("eval"), notAllowlisted: spy("notAllowlisted"),
    });
    const {event} = declaredClick({
        "data-on-click": "fetch open eval notAllowlisted constructor __proto__ hasOwnProperty",
        "data-args": "[\"https://attacker.example/\", \"_blank\"]",
    });
    assert.doesNotThrow(() => listeners.click(event));
    assert.deepEqual(calls, []);
});

test("reassigning a page handler after common.js loaded does not change what is dispatched", () => {
    const calls = [];
    const {ctx, listeners} = runCommonWithGlobals({setDisplayType(type) {calls.push(["original", type]);}});
    assert.equal(vm.runInContext("Object.isFrozen(HANDLERS)", ctx), true);
    ctx.window.setDisplayType = (type) => {calls.push(["replaced", type]);};
    ctx.window.switchTab = (type) => {calls.push(["late", type]);};
    listeners.click(declaredClick({"data-on-click": "setDisplayType switchTab", "data-args": "[\"bwr\"]"}).event);
    assert.deepEqual(calls, [["original", "bwr"]]);
});

function htmlFiles(dir) {
    return readdirSync(dir, {withFileTypes: true}).flatMap((entry) => {
        const path = `${dir}/${entry.name}`;
        if (entry.isDirectory()) return htmlFiles(path);
        return entry.name.endsWith(".html") ? [path] : [];
    });
}

test("HANDLERS allowlists every data-on-* name used in templates", () => {
    const used = new Set();
    for (const file of htmlFiles("app/templates")) {
        const html = readFileSync(file, "utf8");
        for (const match of html.matchAll(/data-on-(?:click|change|input)=(?:"([^"]*)"|'([^']*)')/g)) {
            for (const name of (match[1] ?? match[2]).split(/\s+/)) {
                if (name) used.add(name);
            }
        }
    }
    assert.ok(used.size > 0);
    // Define every used name as a page function so only the allowlist decides membership.
    const {ctx} = runCommonWithGlobals(Object.fromEntries([...used].map((name) => [name, () => {}])));
    const allowed = vm.runInContext("Object.keys(HANDLERS)", ctx);
    assert.deepEqual([...used].filter((name) => !allowed.includes(name)), []);
});

// Top-level functions in configure.html's inline script are 2-space indented and end with "  }".
function templateFunction(template, name) {
    const start = template.indexOf(`\n  function ${name}(`);
    assert.ok(start >= 0, `${name} not found in configure.html`);
    return template.slice(start, template.indexOf("\n  }\n", start) + 4);
}

// Returns the rendered HTML plus the swatch elements the renderer coloured via CSSOM.
function renderSeasonLeader(leaderTeam) {
    const template = readFileSync("app/templates/configure.html", "utf8");
    const swatches = [];
    const container = {
        innerHTML: "",
        querySelectorAll(selector) {
            assert.equal(selector, "[data-team-color]");
            for (const [, teamColor] of this.innerHTML.matchAll(/data-team-color="([^"]*)"/g)) {
                swatches.push({dataset: {teamColor}, style: {}});
            }
            return swatches;
        },
    };
    const ctx = vm.createContext({
        uiText: {currentSeason: "Current"}, currentTeamsSeason: 2026, availableTeamsSeasons: [2026],
        selectedTeamsYear: null, leaderData: {2026: {has_data: true, leader_team: leaderTeam, leader_driver: {number: 1, code: "VER"}}},
        document: {getElementById(id) {return id === "teamsSeasonButtons" ? container : null;}},
    });
    for (const name of ["escapeHtml", "getCurrentSeason", "getTeamsSeasonEntries", "renderTeamsSeasonButtons"]) {
        vm.runInContext(templateFunction(template, name), ctx);
    }
    ctx.renderTeamsSeasonButtons();
    return {html: container.innerHTML, swatches};
}

test("season leaders reference no team logo images", () => {
    assert.ok(!readFileSync("app/templates/configure.html", "utf8").includes("/static/images/teams"));
});

test("valid hex team colour renders a swatch with that background", () => {
    for (const color of ["#3671C6", "#ff8000"]) {
        const {html, swatches} = renderSeasonLeader({name: "Red Bull", color});
        assert.equal(html.split("data-team-color").length - 1, 1);
        assert.ok(html.includes(`data-team-color="${color}" aria-hidden="true"`));
        assert.ok(!html.includes("style="));
        assert.deepEqual(swatches.map((el) => el.style.backgroundColor), [color]);
        assert.ok(html.includes(">Red Bull</span>"));
    }
});

for (const color of [undefined, null, "", "#fff", "red", "#12345g", "#ffffff\n", "red;background:url(x)",
    "#ffffff;background:url(x)", "#fff\" onload=\"alert(1)", "#ffffff\" onload=\"alert(1)"]) {
    test(`missing or unsafe team colour ${JSON.stringify(color)} renders no swatch`, () => {
        const {html, swatches} = renderSeasonLeader({name: "Ferrari", color});
        assert.ok(!html.includes("data-team-color"));
        assert.ok(!html.includes("background-color"));
        assert.ok(!html.includes("aria-hidden"));
        assert.ok(!html.includes("onload"));
        assert.ok(!html.includes("url("));
        assert.deepEqual(swatches, []);
        assert.ok(html.includes(">Ferrari</span>"));
    });
}

test("team name containing HTML stays escaped next to the swatch", () => {
    const {html, swatches} = renderSeasonLeader({name: "<img src=x onerror=\"alert(1)\">", color: "#E8002D"});
    assert.ok(!html.includes("<img"));
    assert.ok(html.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"));
    assert.ok(html.includes("data-team-color=\"#E8002D\""));
    assert.deepEqual(swatches.map((el) => el.style.backgroundColor), ["#E8002D"]);
});

test("missing leader team falls back to a dash without a swatch", () => {
    const {html} = renderSeasonLeader(undefined);
    assert.ok(!html.includes("data-team-color"));
    assert.ok(!html.includes("background-color"));
    assert.ok(html.includes("truncate\">-</span>"));
});

// Runs common.js with a fake document whose querySelectorAll serves the given elements.
function runCommonWithElements(elements, readyState) {
    const listeners = {};
    const document = {
        readyState,
        addEventListener(type, fn, capture) {listeners[type] = {fn, capture};},
        querySelectorAll(selector) {return elements[selector] || [];},
    };
    const ctx = vm.createContext({window: {location: {pathname: "/"}}, navigator: {}, document});
    vm.runInContext(common, ctx);
    return {ctx, listeners};
}

for (const readyState of [undefined, "loading", "interactive"]) {
    test(`declared widths are applied once the DOM is ready (readyState ${readyState})`, () => {
        const bars = ["max(2px, 12.5%)", "0", "40%"].map((width) => ({dataset: {width}, style: {}}));
        const {listeners} = runCommonWithElements({"[data-width]": bars}, readyState);
        const deferred = readyState !== "interactive";
        assert.equal("DOMContentLoaded" in listeners, deferred);
        assert.deepEqual(bars.map((el) => el.style.width), deferred ? [undefined, undefined, undefined] : ["max(2px, 12.5%)", "0", "40%"]);
        if (deferred) listeners.DOMContentLoaded.fn();
        assert.deepEqual(bars.map((el) => el.style.width), ["max(2px, 12.5%)", "0", "40%"]);
    });
}

test("applyDeclaredWidths only touches the given root", () => {
    const inside = {dataset: {width: "25%"}, style: {}};
    const {ctx} = runCommonWithElements({}, "loading");
    ctx.applyDeclaredWidths({querySelectorAll(selector) {return selector === "[data-width]" ? [inside] : [];}});
    assert.equal(inside.style.width, "25%");
});

function fakeImage(fallbackSrc, extra = {}) {
    return {tagName: "IMG", dataset: fallbackSrc === undefined ? {} : {fallbackSrc}, src: "/preview/a.png",
        getAttribute(name) {return name === "src" ? this.src : null;}, ...extra};
}

test("failed images switch to their fallback exactly once", () => {
    const {listeners} = runCommonWithElements({}, "loading");
    assert.equal(listeners.error.capture, true);
    const img = fakeImage("/static/images/fallback.png");
    listeners.error.fn({target: img});
    assert.equal(img.src, "/static/images/fallback.png");
    img.src = "/static/images/fallback.png?broken";
    listeners.error.fn({target: img});
    assert.equal(img.src, "/static/images/fallback.png?broken");
    const plain = fakeImage(undefined);
    listeners.error.fn({target: plain});
    assert.equal(plain.src, "/preview/a.png");
    const script = {...fakeImage("/x.png"), tagName: "SCRIPT"};
    listeners.error.fn({target: script});
    assert.equal(script.src, "/preview/a.png");
    listeners.error.fn({target: null});
});

test("images that failed before the deferred script ran fall back on DOMContentLoaded", () => {
    const failed = fakeImage("/fb.png", {complete: true, naturalWidth: 0});
    const loaded = fakeImage("/fb.png", {complete: true, naturalWidth: 800});
    const pending = fakeImage("/fb.png", {complete: false, naturalWidth: 0});
    const alreadyFallback = fakeImage("/fb.png", {complete: true, naturalWidth: 0, src: "/fb.png"});
    const {listeners} = runCommonWithElements({"img[data-fallback-src]": [failed, loaded, pending, alreadyFallback]}, "loading");
    listeners.DOMContentLoaded.fn();
    assert.equal(failed.src, "/fb.png");
    assert.equal(failed.dataset.fallbackApplied, "true");
    assert.equal(loaded.src, "/preview/a.png");
    assert.equal(pending.src, "/preview/a.png");
    assert.equal(alreadyFallback.dataset.fallbackApplied, undefined);
});

test("explicit localized URL is not overridden by a stale preference", () => {
    const saved = [], cookies = [], redirects = [];
    vm.runInNewContext(common, {
        window: {location: {pathname: "/de/configure/calendar", replace(url) {redirects.push(url);}}}, navigator: {},
        document: {addEventListener() {}, set cookie(value) {cookies.push(value);}},
        localStorage: {getItem() {throw Error("Stale preference read");}, setItem(...args) {saved.push(args);}},
    });
    assert.deepEqual([saved, cookies, redirects], [[], [], []]);
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

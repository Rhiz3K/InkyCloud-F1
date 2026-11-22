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
    assert.deepEqual(Object.keys(listeners).sort(), ["DOMContentLoaded", "change", "click", "error", "input", "keydown"]);
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

// Minimal element whose classList behaves like DOMTokenList for toggle/contains.
function fakeClassElement(initial, dataset = {}) {
    const classes = new Set(initial);
    return {dataset, classList: {
        toggle(name, on) {if (on) classes.add(name); else classes.delete(name);},
        contains: (name) => classes.has(name),
    }};
}

// Loads configure.html's teams URL builders into a sandbox with the given state.
// `tz` is the value of the hidden #tz input; undefined means the input is absent.
function teamsUrlContext(state = {}, {tz} = {}) {
    const template = readFileSync("app/templates/configure.html", "utf8");
    const replaced = [];
    // Minimal <select>: assigning an unknown value deselects (selectedIndex -1), like the DOM.
    const selects = ["desktop", "mobile"].map((variant) => {
        const options = ["none", "countdown", "flags", "track", "weather"]
            .map((value) => ({value, dataset: {hint: `hint-${value}-${variant}`}}));
        return {
            options, selectedIndex: 0,
            get value() {return this.selectedIndex >= 0 ? options[this.selectedIndex].value : "";},
            set value(v) {this.selectedIndex = options.findIndex((o) => o.value === v);},
        };
    });
    const notes = {".teams-extra-hint": [{textContent: ""}]};
    for (const selector of [".teams-extra-note-weather", ".teams-extra-note-track"]) {
        const classes = new Set(["hidden"]);
        notes[selector] = [{classList: {toggle(name, on) {if (on) classes.add(name); else classes.delete(name);}, has: (name) => classes.has(name)}}];
    }
    // Mirrors _teams_sidebar.html: mobile wrapper toggles "hidden"; the desktop one swaps hidden<->flex.
    const tzWrappers = {
        mobile: fakeClassElement(["teams-tz-wrapper", "hidden"]),
        desktop: fakeClassElement(["teams-tz-wrapper", "hidden", "flex-1", "flex-col"], {shownClass: "flex"}),
    };
    notes[".teams-tz-wrapper"] = [tzWrappers.mobile, tzWrappers.desktop];
    const tzInput = tz === undefined ? null : {value: tz};
    const ctx = vm.createContext({
        URL,
        window: {location: {origin: "https://racing.example.org", href: "https://racing.example.org/configure/teams"},
            history: {replaceState(_s, _t, url) {replaced.push(String(url)); ctx.window.location.href = String(url);}}},
        document: {
            getElementById(id) {return id === "tz" ? tzInput : null;},
            querySelectorAll(selector) {return selector === ".teams-extra-select" ? selects : notes[selector] || [];},
        },
        updatePreview() {ctx.previewUpdates += 1;}, previewUpdates: 0,
        TEAMS_EXTRA_OPTIONS: ["none", "countdown", "flags", "track", "weather"],
        COUNTDOWN_OPTIONS: ["hours", "days", "auto", "off"], currentCountdown: "hours",
        WEEKEND_MODE_OPTIONS: ["off", "auto"], currentWeekendMode: "off",
        AUTO_SCREEN_OPTIONS: ["off", "race_week", "daily"], currentAutoScreen: "off",
        TEMP_UNIT_OPTIONS: ["c", "f"], currentTempUnit: "c", suggestedTempUnit: () => "c",
        OUTPUT_OPTIONS: {format: ["bmp", "png"], rotate: ["0", "180"]}, currentOutput: {format: "bmp", rotate: "0"},
        THEME_OPTIONS: ["light", "dark"], currentTheme: "light",
        currentScreenType: "teams", currentUiLang: "en", currentDisplayType: "1bit",
        currentTeamsSeason: 2026, selectedTeamsYear: 2026, currentTeamsExtra: "none", ...state,
    });
    for (const name of ["getCurrentSeason", "getApiUrl", "canUsePrerenderedPreview", "getPreviewUrl", "setTeamsExtra",
        "selectTeamsExtra", "teamsExtraUsesTz", "getTeamsTz", "updateTeamsTzVisibility"]) {
        vm.runInContext(templateFunction(template, name), ctx);
    }
    return {ctx, replaced, selects, notes, tzInput, tzWrappers};
}

function tzPickerVisible({mobile, desktop}) {
    const mobileShown = !mobile.classList.contains("hidden");
    const desktopShown = !desktop.classList.contains("hidden") && desktop.classList.contains("flex");
    const desktopHidden = desktop.classList.contains("hidden") && !desktop.classList.contains("flex");
    assert.equal(mobileShown, desktopShown, "mobile and desktop pickers must agree");
    assert.ok(desktopShown || desktopHidden, "desktop wrapper must not be both hidden and flex");
    return mobileShown;
}

test("teams URLs stay byte-identical when the extra panel is none", () => {
    const {ctx} = teamsUrlContext();
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/teams.bmp?year=2026");
    assert.equal(ctx.canUsePrerenderedPreview(), true);
    assert.equal(ctx.getPreviewUrl(), "https://racing.example.org/preview/configure/teams.png");
    ctx.currentUiLang = "cs";
    ctx.currentDisplayType = "bwr";
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/teams.bmp?lang=cs&year=2026&display=bwr");
    assert.equal(ctx.getPreviewUrl(), "https://racing.example.org/preview/configure/teams.png?lang=cs&display=bwr");
});

for (const extra of ["countdown", "flags", "track", "weather"]) {
    test(`teams extra=${extra} is in the API URL and uses the dynamic PNG preview`, () => {
        const {ctx} = teamsUrlContext({currentTeamsExtra: extra, currentDisplayType: "spectra6"});
        const api = `https://racing.example.org/teams.bmp?year=2026&display=spectra6&extra=${extra}`;
        assert.equal(ctx.getApiUrl(), api);
        assert.equal(ctx.canUsePrerenderedPreview(), true);
        assert.equal(ctx.getPreviewUrl(), `https://racing.example.org/preview/configure/teams.png?display=spectra6&extra=${extra}`);
    });
}

test("setTeamsExtra updates state, selects, notes and the page URL", () => {
    const {ctx, replaced, selects, notes} = teamsUrlContext();
    ctx.setTeamsExtra("weather");
    assert.equal(ctx.currentTeamsExtra, "weather");
    assert.equal(ctx.previewUpdates, 1);
    assert.deepEqual(replaced, ["https://racing.example.org/configure/teams?extra=weather"]);
    assert.deepEqual(selects.map((s) => s.value), ["weather", "weather"]);
    assert.equal(notes[".teams-extra-hint"][0].textContent, "hint-weather-mobile");
    assert.equal(notes[".teams-extra-note-weather"][0].classList.has("hidden"), false);
    assert.equal(notes[".teams-extra-note-track"][0].classList.has("hidden"), true);
    assert.match(ctx.getApiUrl(), /&extra=weather$/);

    ctx.setTeamsExtra("bogus");
    assert.equal(ctx.currentTeamsExtra, "none");
    assert.deepEqual(selects.map((s) => s.value), ["none", "none"]);
    assert.equal(notes[".teams-extra-hint"][0].textContent, "hint-none-mobile");
    assert.equal(replaced.at(-1), "https://racing.example.org/configure/teams");
    assert.equal(notes[".teams-extra-note-weather"][0].classList.has("hidden"), true);
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/teams.bmp?year=2026");

    ctx.setTeamsExtra("track", false);
    assert.equal(ctx.previewUpdates, 2);
    assert.equal(replaced.length, 2);
    assert.equal(notes[".teams-extra-note-track"][0].classList.has("hidden"), false);
    assert.deepEqual(selects.map((s) => s.value), ["track", "track"]);
});

test("selectTeamsExtra applies the changed select's this.value", () => {
    const {ctx, replaced, selects, notes} = teamsUrlContext();
    selects[0].value = "flags";
    ctx.selectTeamsExtra.call(selects[0]);
    assert.equal(ctx.currentTeamsExtra, "flags");
    assert.deepEqual(selects.map((s) => s.value), ["flags", "flags"]);
    assert.equal(notes[".teams-extra-hint"][0].textContent, "hint-flags-mobile");
    assert.equal(ctx.previewUpdates, 1);
    assert.deepEqual(replaced, ["https://racing.example.org/configure/teams?extra=flags"]);
});

test("a change on data-on-change=selectTeamsExtra reaches setTeamsExtra via the delegated dispatcher", () => {
    const calls = [];
    // Compile the real page handler so its setTeamsExtra lookup hits the spy.
    const selectTeamsExtra = vm.runInNewContext(
        `${templateFunction(readFileSync("app/templates/configure.html", "utf8"), "selectTeamsExtra")}\nselectTeamsExtra`,
        {setTeamsExtra(...args) {calls.push(args);}});
    const {listeners} = runCommonWithGlobals({selectTeamsExtra});
    const select = {value: "track", getAttribute(name) {return name === "data-on-change" ? "selectTeamsExtra" : null;}};
    const target = {closest(selector) {return selector === "[data-on-change]" ? select : null;}};
    listeners.change({type: "change", target});
    assert.deepEqual(calls, [["track"]]);
    listeners.click({type: "click", target});
    assert.deepEqual(calls, [["track"]]);
});

test("?extra=track initialises both selects and the hint without refreshing the preview", () => {
    const template = readFileSync("app/templates/configure.html", "utf8");
    const initLine = 'if (currentScreenType === "teams") setTeamsExtra(currentTeamsExtra, false);';
    assert.ok(template.includes(`\n    ${initLine}\n`));
    const {ctx, replaced, selects, notes} = teamsUrlContext({URLSearchParams});
    ctx.window.location.search = "?extra=track";
    vm.runInContext(templateFunction(template, "initTrackOptions"), ctx);
    ctx.initTrackOptions();
    vm.runInContext(initLine, ctx);
    assert.equal(ctx.currentTeamsExtra, "track");
    assert.deepEqual(selects.map((s) => s.value), ["track", "track"]);
    assert.equal(notes[".teams-extra-hint"][0].textContent, "hint-track-mobile");
    assert.equal(notes[".teams-extra-note-track"][0].classList.has("hidden"), false);
    assert.equal(ctx.previewUpdates, 0);
    assert.deepEqual(replaced, []);
});

for (const legacy of ["timeline", "combo"]) {
    test(`legacy ${legacy} links initialise the consolidated countdown`, () => {
        const {ctx, selects} = teamsUrlContext({URLSearchParams});
        ctx.window.location.search = `?extra=${legacy}`;
        const template = readFileSync("app/templates/configure.html", "utf8");
        vm.runInContext(templateFunction(template, "initTrackOptions"), ctx);
        ctx.initTrackOptions();
        ctx.setTeamsExtra(ctx.currentTeamsExtra, false);
        assert.equal(ctx.currentTeamsExtra, "countdown");
        assert.deepEqual(selects.map(s => s.value), ["countdown", "countdown"]);
        assert.match(ctx.getApiUrl(), /extra=countdown$/);
    });
}

// Calendar countdown mode: default "hours" never reaches a URL, other modes do.
function calendarCountdownContext(state = {}, kind = "countdown") {
    const template = readFileSync("app/templates/configure.html", "utf8");
    const replaced = [];
    const hintFor = (select) => ({value: "hours", options: [], get selectedIndex() {return 0;},
        set value(mode) {this._value = mode; this.options[0] = {dataset: {hint: `hint-${mode}-${select}`}};},
        get value() {return this._value;}});
    const selects = [hintFor("desktop"), hintFor("mobile")];
    selects.forEach((select) => {select.value = "hours";});
    const hints = [{textContent: ""}, {textContent: ""}];
    const ctx = vm.createContext({
        URL, URLSearchParams,
        window: {location: {origin: "https://racing.example.org", search: "",
            href: "https://racing.example.org/configure/calendar"},
        history: {replaceState(_s, _t, url) {replaced.push(String(url)); ctx.window.location.href = String(url);}}},
        document: {
            getElementById(id) {
                if (id === "tz") return {value: ""};
                if (id === "weatherToggle") return {checked: true};
                return null;
            },
            querySelector() {return {value: "race_day"};},
            querySelectorAll(selector) {
                return selector === `.${kind}-select` ? selects : selector === `.${kind}-hint` ? hints : [];
            },
        },
        updatePreview() {ctx.previewUpdates += 1;}, previewUpdates: 0,
        TEAMS_EXTRA_OPTIONS: ["none", "countdown", "flags", "track", "weather"],
        COUNTDOWN_OPTIONS: ["hours", "days", "auto", "off"], currentCountdown: "hours",
        WEEKEND_MODE_OPTIONS: ["off", "auto"], currentWeekendMode: "off",
        AUTO_SCREEN_OPTIONS: ["off", "race_week", "daily"], currentAutoScreen: "off",
        TEMP_UNIT_OPTIONS: ["c", "f"], currentTempUnit: "c", suggestedTempUnit: () => "c",
        OUTPUT_OPTIONS: {format: ["bmp", "png"], rotate: ["0", "180"]}, currentOutput: {format: "bmp", rotate: "0"},
        THEME_OPTIONS: ["light", "dark"], currentTheme: "light",
        currentScreenType: "calendar", currentUiLang: "en", currentDisplayType: "1bit", currentTeamsExtra: "none",
        selectedYear: null, selectedRound: null, selectedRaceKey: null, selectedTz: null, defaultTimezone: "Europe/Prague",
        ...state,
    });
    for (const name of ["getApiUrl", "appendTrackParams", "canUsePrerenderedPreview", "getPreviewUrl",
        "setCountdown", "selectCountdown", "setWeekendMode", "selectWeekendMode", "initTrackOptions"]) {
        vm.runInContext(templateFunction(template, name), ctx);
    }
    return {ctx, replaced, selects, hints};
}

test("calendar URLs omit the default countdown and carry the other modes", () => {
    const {ctx} = calendarCountdownContext();
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/calendar.bmp");
    assert.equal(ctx.getPreviewUrl(), "https://racing.example.org/preview/configure/calendar.png?weather_type=race");
    for (const mode of ["days", "auto", "off"]) {
        ctx.currentCountdown = mode;
        assert.equal(ctx.getApiUrl(), `https://racing.example.org/calendar.bmp?countdown=${mode}`);
        assert.equal(ctx.getPreviewUrl(),
            `https://racing.example.org/preview/configure/calendar.png?weather_type=race&countdown=${mode}`);
    }
    ctx.currentDisplayType = "bwry";
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/calendar.bmp?display=bwry&countdown=off");
});

test("setCountdown syncs both selects, the hint and the page URL", () => {
    const {ctx, replaced, selects, hints} = calendarCountdownContext();
    ctx.setCountdown("days");
    assert.equal(ctx.currentCountdown, "days");
    assert.deepEqual(selects.map((s) => s.value), ["days", "days"]);
    assert.equal(hints[0].textContent, "hint-days-mobile");
    assert.deepEqual(replaced, ["https://racing.example.org/configure/calendar?countdown=days"]);
    assert.equal(ctx.previewUpdates, 1);

    ctx.setCountdown("bogus");
    assert.equal(ctx.currentCountdown, "hours");
    assert.equal(replaced.at(-1), "https://racing.example.org/configure/calendar");
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/calendar.bmp");

    ctx.setCountdown("auto", false);
    assert.equal(ctx.previewUpdates, 2);
    assert.equal(replaced.length, 2);
    assert.deepEqual(selects.map((s) => s.value), ["auto", "auto"]);
});

test("selectCountdown applies this.value and ?countdown= initialises the mode", () => {
    const {ctx, selects} = calendarCountdownContext();
    selects[1].value = "off";
    ctx.selectCountdown.call(selects[1]);
    assert.equal(ctx.currentCountdown, "off");

    const fresh = calendarCountdownContext();
    fresh.ctx.window.location.search = "?countdown=auto";
    fresh.ctx.initTrackOptions();
    assert.equal(fresh.ctx.currentCountdown, "auto");
    fresh.ctx.window.location.search = "?countdown=weekly";
    fresh.ctx.currentCountdown = "hours";
    fresh.ctx.initTrackOptions();
    assert.equal(fresh.ctx.currentCountdown, "hours");
});

test("calendar URLs carry weekend_mode=auto and omit the default off", () => {
    const {ctx} = calendarCountdownContext({}, "weekend-mode");
    ctx.currentWeekendMode = "auto";
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/calendar.bmp?weekend_mode=auto");
    assert.equal(ctx.getPreviewUrl(),
        "https://racing.example.org/preview/configure/calendar.png?weather_type=race&weekend_mode=auto");
    ctx.currentCountdown = "days";
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/calendar.bmp?countdown=days&weekend_mode=auto");
    ctx.currentWeekendMode = "off";
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/calendar.bmp?countdown=days");
});

test("setWeekendMode syncs both selects, the hint and the page URL", () => {
    const {ctx, replaced, selects, hints} = calendarCountdownContext({}, "weekend-mode");
    ctx.setWeekendMode("auto");
    assert.equal(ctx.currentWeekendMode, "auto");
    assert.deepEqual(selects.map((s) => s.value), ["auto", "auto"]);
    assert.equal(hints[1].textContent, "hint-auto-mobile");
    assert.deepEqual(replaced, ["https://racing.example.org/configure/calendar?weekend_mode=auto"]);
    assert.equal(ctx.previewUpdates, 1);

    ctx.setWeekendMode("bogus");
    assert.equal(ctx.currentWeekendMode, "off");
    assert.equal(replaced.at(-1), "https://racing.example.org/configure/calendar");

    ctx.setWeekendMode("auto", false);
    assert.equal(ctx.previewUpdates, 2);
    assert.equal(replaced.length, 2);
});

test("selectWeekendMode applies this.value and ?weekend_mode= initialises the mode", () => {
    const {ctx, selects} = calendarCountdownContext({}, "weekend-mode");
    selects[0].value = "auto";
    ctx.selectWeekendMode.call(selects[0]);
    assert.equal(ctx.currentWeekendMode, "auto");

    const fresh = calendarCountdownContext({}, "weekend-mode");
    fresh.ctx.window.location.search = "?weekend_mode=auto";
    fresh.ctx.initTrackOptions();
    assert.equal(fresh.ctx.currentWeekendMode, "auto");
    fresh.ctx.window.location.search = "?weekend_mode=on";
    fresh.ctx.currentWeekendMode = "off";
    fresh.ctx.initTrackOptions();
    assert.equal(fresh.ctx.currentWeekendMode, "off");
});

const NY_TZ = "tz=America%2FNew_York";

test("teams extra=none never adds tz, even with a picked timezone", () => {
    const {ctx} = teamsUrlContext({}, {tz: "America/New_York"});
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/teams.bmp?year=2026");
    assert.equal(ctx.getPreviewUrl(), "https://racing.example.org/preview/configure/teams.png");
    assert.ok(!ctx.getApiUrl().includes("tz="));
    assert.ok(!ctx.getPreviewUrl().includes("tz="));
});

for (const extra of ["countdown", "flags", "track", "weather"]) {
    test(`teams extra=${extra} adds the picked tz to the API and preview URLs`, () => {
        const {ctx} = teamsUrlContext({currentTeamsExtra: extra}, {tz: "America/New_York"});
        assert.equal(ctx.getApiUrl(), `https://racing.example.org/teams.bmp?year=2026&extra=${extra}&${NY_TZ}`);
        assert.equal(ctx.getPreviewUrl(), `https://racing.example.org/preview/configure/teams.png?extra=${extra}&${NY_TZ}`);
    });

    test(`teams extra=${extra} with an empty tz input adds no tz param`, () => {
        const {ctx} = teamsUrlContext({currentTeamsExtra: extra}, {tz: ""});
        assert.equal(ctx.getApiUrl(), `https://racing.example.org/teams.bmp?year=2026&extra=${extra}`);
        assert.equal(ctx.getPreviewUrl(), `https://racing.example.org/preview/configure/teams.png?extra=${extra}`);
    });
}

test("setTeamsExtra shows the tz picker only for timed panels and keeps the tz", () => {
    const {ctx, tzWrappers} = teamsUrlContext({}, {tz: "America/New_York"});
    assert.equal(tzPickerVisible(tzWrappers), false);

    ctx.setTeamsExtra("countdown");
    assert.equal(tzPickerVisible(tzWrappers), true);
    assert.equal(ctx.getApiUrl(), `https://racing.example.org/teams.bmp?year=2026&extra=countdown&${NY_TZ}`);

    ctx.setTeamsExtra("none");
    assert.equal(tzPickerVisible(tzWrappers), false);
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/teams.bmp?year=2026");
    assert.equal(ctx.getPreviewUrl(), "https://racing.example.org/preview/configure/teams.png");

    ctx.setTeamsExtra("countdown");
    assert.equal(tzPickerVisible(tzWrappers), true);
    assert.equal(ctx.getApiUrl(), `https://racing.example.org/teams.bmp?year=2026&extra=countdown&${NY_TZ}`);
    assert.equal(ctx.getPreviewUrl(), `https://racing.example.org/preview/configure/teams.png?extra=countdown&${NY_TZ}`);
});

test("legacy timeline extra maps to countdown and shows the tz picker", () => {
    const {ctx, selects, tzWrappers} = teamsUrlContext({}, {tz: "America/New_York"});
    ctx.setTeamsExtra("timeline");
    assert.equal(ctx.currentTeamsExtra, "countdown");
    assert.deepEqual(selects.map((s) => s.value), ["countdown", "countdown"]);
    assert.equal(tzPickerVisible(tzWrappers), true);
    assert.equal(ctx.getApiUrl(), `https://racing.example.org/teams.bmp?year=2026&extra=countdown&${NY_TZ}`);
});

test("historical teams still use the year-specific BMP preview", () => {
    const {ctx} = teamsUrlContext({selectedTeamsYear: 2025, currentTeamsExtra: "track"});
    assert.equal(ctx.canUsePrerenderedPreview(), false);
    assert.equal(ctx.getPreviewUrl(), "https://racing.example.org/teams.bmp?year=2025&extra=track");
});

// Minimal DOM for the desktop timezone list: elements record attributes, listeners and focus.
function tzListContext(state = {}) {
    const template = readFileSync("app/templates/configure.html", "utf8");
    let focused = null;
    const makeElement = (tag) => {
        const attributes = {}, listeners = {};
        const element = {
            tagName: tag.toUpperCase(), dataset: {}, className: "", tabIndex: 0, type: "", children: [], html: "",
            setAttribute(name, value) {attributes[name] = String(value);},
            getAttribute(name) {return attributes[name] ?? null;},
            addEventListener(type, handler) {listeners[type] = handler;},
            fire(type, event = {}) {listeners[type]?.({currentTarget: element, target: element, ...event});},
            focus() {focused = element;},
            appendChild(child) {element.children.push(child);},
            get innerHTML() {return element.html;},
            set innerHTML(value) {element.html = value; element.children = [];},
        };
        return element;
    };
    const list = makeElement("div");
    list.querySelectorAll = (selector) => {
        assert.equal(selector, ".tz-item");
        return list.children.filter((child) => child.className.split(/\s+/).includes("tz-item"));
    };
    list.contains = (element) => element === list || list.children.includes(element);
    const search = Object.assign(makeElement("input"), {value: ""});
    const tzInput = {value: ""};
    const filters = ["ALL", "EUROPE"].map((continent) => {
        const button = makeElement("button");
        const classes = new Set(continent === "ALL" ? ["active"] : []);
        button.dataset.continent = continent;
        button.classList = {toggle(name, on) {if (on) classes.add(name); else classes.delete(name);}, contains: (name) => classes.has(name)};
        return button;
    });
    const ctx = vm.createContext({
        document: {
            createElement: makeElement,
            getElementById(id) {return {tzList: list, tzSearch: search, tz: tzInput}[id] ?? null;},
            querySelectorAll(selector) {return selector === ".tz-filter-btn" ? filters : [];},
            get activeElement() {return focused;},
        },
        uiText: {filterLabels: {}, noResults: "No results", detectedLabel: "Detected"},
        selectedTz: "Europe/Prague", browserTz: "Europe/Prague", activeContinent: "ALL",
        allTimezones: ["Europe/Prague", "Europe/London", "America/New_York"],
        popularTz: [{offset: -5, zones: ["America/New_York"]}, {offset: 0, zones: ["Europe/London"]}, {offset: 1, zones: ["Europe/Prague"]}],
        formatOffset: (offset) => `UTC${offset}`, getUtcOffsetNum: () => 0, formatTzDisplay: (tz) => tz,
        escapeHtml: (text) => text, normalizeTimezone: (tz) => tz, syncMobileTzSelect() {},
        updatePreview() {ctx.previewUpdates += 1;}, previewUpdates: 0, ...state,
    });
    for (const name of ["createTzOption", "setTzListTabStop", "revealTzItem", "onTzListKeydown", "renderTzList", "selectTimezone", "filterTzContinent"]) {
        vm.runInContext(templateFunction(template, name), ctx);
    }
    const items = () => list.querySelectorAll(".tz-item");
    const keydown = (target, key) => {
        let prevented = false;
        ctx.onTzListKeydown({key, target, currentTarget: list, preventDefault() {prevented = true;}});
        return prevented;
    };
    return {ctx, list, search, tzInput, filters, items, keydown, focused: () => focused};
}

test("timezone entries are buttons with pressed state and a single Tab stop", () => {
    const {ctx, list, items} = tzListContext();
    ctx.renderTzList();
    const entries = items();
    // Detected entry first, then AMERICA / EUROPE region groups with their headers in between.
    assert.deepEqual(entries.map((entry) => entry.dataset.tz), ["Europe/Prague", "America/New_York", "Europe/London", "Europe/Prague"]);
    assert.equal(list.children.length, entries.length + 2);
    for (const entry of entries) {
        assert.equal(entry.tagName, "BUTTON");
        assert.equal(entry.type, "button");
        assert.equal(entry.getAttribute("aria-pressed"), String(entry.dataset.tz === "Europe/Prague"));
    }
    assert.deepEqual(entries.map((entry) => entry.tabIndex), [0, -1, -1, -1]);
});

test("timezone list arrow, Home and End keys move the roving Tab stop", () => {
    const {ctx, list, items, keydown, focused} = tzListContext();
    ctx.renderTzList();
    const entries = items();
    assert.equal(keydown(entries[0], "ArrowDown"), true);
    assert.equal(focused(), entries[1]);
    assert.deepEqual(entries.map((entry) => entry.tabIndex), [-1, 0, -1, -1]);
    keydown(entries[1], "End");
    assert.equal(focused(), entries[3]);
    keydown(entries[3], "ArrowDown");
    assert.equal(focused(), entries[3]);
    keydown(entries[3], "Home");
    assert.equal(focused(), entries[0]);
    keydown(entries[0], "ArrowUp");
    assert.equal(focused(), entries[0]);
    keydown(entries[0], "ArrowDown");
    keydown(entries[1], "ArrowUp");
    assert.equal(focused(), entries[0]);
    assert.deepEqual(entries.map((entry) => entry.tabIndex), [0, -1, -1, -1]);
    // Other keys (Enter/Space activate the native button) and non-entry targets are ignored.
    assert.equal(keydown(entries[0], "Enter"), false);
    assert.equal(keydown(list, "ArrowDown"), false);
    assert.equal(focused(), entries[0]);
});

test("activating a timezone entry keeps keyboard focus on the new selection", () => {
    const {ctx, items, tzInput, focused} = tzListContext();
    ctx.renderTzList();
    const london = items()[2];
    london.focus();
    london.fire("click");
    assert.equal(ctx.selectedTz, "Europe/London");
    assert.equal(tzInput.value, "Europe/London");
    assert.equal(ctx.previewUpdates, 1);
    const selected = items().find((entry) => entry.dataset.tz === "Europe/London");
    assert.equal(focused(), selected);
    assert.equal(selected.tabIndex, 0);
    assert.equal(selected.getAttribute("aria-pressed"), "true");
});

test("selecting from search results falls back to the search field when the zone is not listed", () => {
    const {ctx, search, items, focused} = tzListContext({allTimezones: ["Europe/Prague", "Europe/London", "America/New_York", "Asia/Tokyo"]});
    search.value = "tokyo";
    ctx.renderTzList();
    const [tokyo] = items();
    assert.equal(tokyo.dataset.tz, "Asia/Tokyo");
    assert.equal(tokyo.tabIndex, 0);
    tokyo.focus();
    tokyo.fire("click");
    assert.equal(search.value, "");
    assert.equal(ctx.selectedTz, "Asia/Tokyo");
    assert.ok(items().every((entry) => entry.dataset.tz !== "Asia/Tokyo"));
    // Not in the grouped list: the first entry becomes the Tab stop and focus stays in the list.
    assert.equal(focused(), items()[0]);
});

test("timezone selection from outside the list does not steal focus", () => {
    const {ctx, search, focused} = tzListContext();
    search.focus();
    ctx.selectTimezone("Europe/London", false);
    assert.equal(focused(), search);
    assert.equal(ctx.previewUpdates, 0);
});

test("detected-only and empty timezone lists keep a valid Tab stop", () => {
    const detected = tzListContext({activeContinent: "DETECTED"});
    detected.ctx.renderTzList();
    assert.deepEqual(detected.items().map((entry) => [entry.dataset.tz, entry.tabIndex]), [["Europe/Prague", 0]]);

    const empty = tzListContext({browserTz: null, activeContinent: "DETECTED"});
    empty.ctx.renderTzList();
    assert.deepEqual(empty.items(), []);
    assert.equal(empty.ctx.setTzListTabStop(empty.list), null);
    empty.ctx.selectTimezone("Europe/London");
    assert.equal(empty.focused(), null);

    const noResults = tzListContext();
    noResults.search.value = "zzz";
    noResults.ctx.renderTzList();
    assert.match(noResults.list.innerHTML, /No results/);
});

test("continent filters expose aria-pressed", () => {
    const {ctx, filters} = tzListContext();
    ctx.filterTzContinent("EUROPE");
    assert.deepEqual(filters.map((button) => button.getAttribute("aria-pressed")), ["false", "true"]);
    assert.deepEqual(filters.map((button) => button.classList.contains("active")), [false, true]);
});

// Weather and display option buttons: aria-pressed must follow the visual selection.
function optionButtonsContext() {
    const template = readFileSync("app/templates/configure.html", "utf8");
    const elements = {};
    const element = (id) => {
        if (!elements[id]) {
            const classes = new Set(), attributes = {};
            elements[id] = {
                id, checked: false,
                classList: {add: (...names) => names.forEach((name) => classes.add(name)), remove: (...names) => names.forEach((name) => classes.delete(name)), contains: (name) => classes.has(name)},
                setAttribute(name, value) {attributes[name] = String(value);},
                getAttribute(name) {return attributes[name] ?? null;},
                removeAttribute(name) {delete attributes[name];},
            };
        }
        return elements[id];
    };
    const ctx = vm.createContext({
        URL, window: {location: {href: "https://racing.example.org/configure/calendar"}, history: {replaceState() {}}},
        document: {getElementById: element, querySelector: () => element("weatherCurrentRadio")},
        isWeatherTooltipDisabledState: () => false, updateWeatherTypeAvailability() {}, updatePreview() {},
        currentWeatherType: "race_day", currentDisplayType: "1bit",
    });
    for (const name of ["setWeatherType", "setDisplayType"]) {
        vm.runInContext(templateFunction(template, name), ctx);
    }
    const pressed = (ids) => Object.fromEntries(ids.map((id) => [id, element(id).getAttribute("aria-pressed")]));
    return {ctx, pressed, element};
}

const WEATHER_IDS = ["weatherOffBtn", "weatherCurrentBtn", "weatherRaceDayBtn", "weatherOffBtnMobile", "weatherCurrentBtnMobile", "weatherRaceDayBtnMobile"];

for (const [type, selected] of [["off", "weatherOff"], ["current", "weatherCurrent"], ["race_day", "weatherRaceDay"]]) {
    test(`setWeatherType(${type}) marks only its desktop and mobile buttons pressed`, () => {
        const {ctx, pressed, element} = optionButtonsContext();
        ctx.setWeatherType(type);
        const expected = Object.fromEntries(WEATHER_IDS.map((id) => [id, String(id.startsWith(`${selected}Btn`))]));
        assert.deepEqual(pressed(WEATHER_IDS), expected);
        assert.equal(element("weatherToggle").checked, type !== "off");
    });
}

function weatherAvailabilityContext(daysToRace, currentWeatherType) {
    const {ctx, element} = optionButtonsContext();
    const template = readFileSync("app/templates/configure.html", "utf8");
    // Midnight UTC of the date daysToRace from now lies within the last day: ceil() gives daysToRace.
    const raceDate = new Date(Date.now() + daysToRace * 86400000).toISOString().slice(0, 10);
    Object.assign(ctx, {nextRaceDate: raceDate, WEEKEND_FORECAST_LEAD_DAYS: 2, currentWeatherType});
    ctx.isWeatherTooltipDisabledState = (btn) => btn?.getAttribute("aria-disabled") === "true";
    for (const name of ["daysUntilNextRace", "isRaceDayWeatherAvailable", "isWeekendWeatherAvailable",
        "updateWeatherTypeAvailability", "setWeatherType"]) {
        vm.runInContext(templateFunction(template, name), ctx);
    }
    return {ctx, element};
}

test("weekend weather is available before the race-day forecast and keeps its choice", () => {
    // 16 days out: the race day is beyond the race-day check, qualifying is in range.
    const {ctx, element} = weatherAvailabilityContext(16, "weekend");
    assert.equal(ctx.isRaceDayWeatherAvailable(), false);
    assert.equal(ctx.isWeekendWeatherAvailable(), true);
    ctx.updateWeatherTypeAvailability();
    assert.equal(element("weatherRaceDayBtn").getAttribute("aria-disabled"), "true");
    assert.equal(element("weatherWeekendBtn").getAttribute("aria-disabled"), "false");
    assert.equal(element("weatherWeekendBtnMobile").getAttribute("aria-disabled"), "false");
    assert.equal(ctx.currentWeatherType, "weekend");
    ctx.setWeatherType("race_day");  // ignored while its buttons are disabled
    assert.equal(ctx.currentWeatherType, "weekend");

    const far = weatherAvailabilityContext(17, "weekend");
    assert.equal(far.ctx.isWeekendWeatherAvailable(), false);
    far.ctx.updateWeatherTypeAvailability();
    assert.equal(far.ctx.currentWeatherType, "current");

    const raceDay = weatherAvailabilityContext(16, "race_day");
    raceDay.ctx.updateWeatherTypeAvailability();
    assert.equal(raceDay.ctx.currentWeatherType, "current");

    const unknown = weatherAvailabilityContext(3, "current");
    unknown.ctx.nextRaceDate = null;
    assert.equal(unknown.ctx.daysUntilNextRace(), null);
    assert.equal(unknown.ctx.isWeekendWeatherAvailable(), false);
});

const DISPLAY_IDS = ["display1bitBtn", "displayBwrBtn", "displayBwryBtn", "displaySpectra6Btn"].flatMap((id) => [id, `${id}Mobile`]);

for (const [type, selected] of [["1bit", "display1bit"], ["bwr", "displayBwr"], ["bwry", "displayBwry"], ["spectra6", "displaySpectra6"]]) {
    test(`setDisplayType(${type}) marks only its desktop and mobile buttons pressed`, () => {
        const {ctx, pressed} = optionButtonsContext();
        ctx.setDisplayType(type, false);
        const expected = Object.fromEntries(DISPLAY_IDS.map((id) => [id, String(id.startsWith(`${selected}Btn`))]));
        assert.deepEqual(pressed(DISPLAY_IDS), expected);
    });
}

test("closed mobile nav is inert and focus moves in and back out", () => {
    const focused = [];
    const classList = (initial) => {
        const set = new Set(initial);
        return {add: (c) => set.add(c), remove: (c) => set.delete(c), contains: (c) => set.has(c)};
    };
    const firstButton = {focus() {focused.push("menu-close");}};
    const menuButton = {focus() {focused.push("hamburger");}};
    const menu = {
        inert: true,
        classList: classList(["-translate-x-full"]),
        querySelector: () => firstButton,
        contains: (node) => node === firstButton,
    };
    const overlay = {classList: classList(["hidden"])};
    const elements = {mobileNavMenu: menu, mobileNavOverlay: overlay, mobileMenuBtn: menuButton};
    const {ctx, listeners} = runCommonWithGlobals({});
    ctx.document.getElementById = (id) => elements[id] ?? null;

    ctx.openMobileNav();
    assert.equal(menu.inert, false);
    assert.equal(menu.classList.contains("-translate-x-full"), false);
    ctx.document.activeElement = firstButton;

    listeners.keydown({key: "Escape"});
    assert.equal(menu.inert, true);
    assert.equal(overlay.classList.contains("hidden"), true);
    assert.deepEqual(focused, ["menu-close", "hamburger"]);

    listeners.keydown({key: "Escape"});
    assert.deepEqual(focused, ["menu-close", "hamburger"]);
});

test("closing the settings drawer returns focus to the button that opened it", () => {
    const template = readFileSync("app/templates/configure.html", "utf8");
    const document = {getElementById: id => elements[id]};
    const button = () => ({matches: selector => selector === "button", focus() {document.activeElement = this;}});
    const classes = new Set(["-translate-x-full"]);
    const elements = {
        mobileMenuBtn: button(), settingsSidebarClose: button(),
        sidebarOverlay: {classList: {add() {}, remove() {}}},
        settingsSidebar: {
            classList: {add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c)},
            contains: el => el === elements.settingsSidebarClose,
        },
    };
    const ctx = vm.createContext({document, syncSidebarInert() {}});
    vm.runInContext(template.slice(template.indexOf("  function isDesktopSidebar()"), template.indexOf("\n  syncSidebarInert();")), ctx);
    ctx.window = {matchMedia: () => ({matches: false})};
    for (const opener of [button(), elements.mobileMenuBtn]) {
        opener.focus();
        if (opener === elements.mobileMenuBtn) ctx.toggleSidebar();
        else ctx.toggleSidebar.call(opener);
        assert.equal(document.activeElement, elements.settingsSidebarClose);
        ctx.toggleSidebar();
        assert.equal(document.activeElement, opener);
    }
});

test("revealTzItem scrolls only the list, and only when the selection is out of view", () => {
    const template = readFileSync("app/templates/configure.html", "utf8");
    const ctx = vm.createContext({});
    vm.runInContext(templateFunction(template, "revealTzItem"), ctx);
    const list = (scrollTop) => ({
        clientHeight: 100, scrollHeight: 400, scrollTop,
        getBoundingClientRect: () => ({top: 50}),
    });
    const item = (top) => ({offsetHeight: 20, getBoundingClientRect: () => ({top})});

    const below = list(0);
    ctx.revealTzItem(below, item(300));
    assert.equal(below.scrollTop, 210);

    const visible = list(10);
    ctx.revealTzItem(visible, item(80));
    assert.equal(visible.scrollTop, 10);

    const unscrollable = {clientHeight: 100, scrollHeight: 100, scrollTop: 0};
    ctx.revealTzItem(unscrollable, item(500));
    assert.equal(unscrollable.scrollTop, 0);
});

// One URL for both screens (/auto.bmp): only the copied URL changes, never the preview.
function autoScreenContext(screen, state = {}) {
    const template = readFileSync("app/templates/configure.html", "utf8");
    const replaced = [];
    const options = ["off", "race_week", "daily"].map((value) => ({value, dataset: {hint: `hint-${value}`}}));
    const select = {
        options, selectedIndex: 0,
        get value() {return this.selectedIndex >= 0 ? options[this.selectedIndex].value : "";},
        set value(v) {this.selectedIndex = options.findIndex((o) => o.value === v);},
    };
    const hints = [{textContent: ""}];
    const apiUrl = {value: ""};
    const outputSelects = Object.fromEntries(Object.entries({format: ["bmp", "png"], rotate: ["0", "180"]})
        .map(([name, values]) => {
            const choices = values.map((value) => ({value, dataset: {hint: `hint-${name}-${value}`}}));
            return [name, {
                options: choices, selectedIndex: 0,
                get value() {return this.selectedIndex >= 0 ? choices[this.selectedIndex].value : "";},
                set value(v) {this.selectedIndex = choices.findIndex((o) => o.value === v);},
            }];
        }));
    const outputHints = {format: [{textContent: ""}], rotate: [{textContent: ""}]};
    const ctx = vm.createContext({
        URL, URLSearchParams,
        window: {location: {origin: "https://racing.example.org", search: "",
            href: `https://racing.example.org/configure/${screen}`},
        history: {replaceState(_s, _t, url) {replaced.push(String(url)); ctx.window.location.href = String(url);}}},
        document: {
            getElementById(id) {
                if (id === "tz") return {value: "Europe/Prague"};
                if (id === "weatherToggle") return {checked: false};
                if (id === "apiUrl") return apiUrl;
                return null;
            },
            querySelector() {return {value: "race_day"};},
            querySelectorAll(selector) {
                const output = /^\.output-(format|rotate)-(select|hint)$/.exec(selector);
                if (output) return output[2] === "select" ? [outputSelects[output[1]]] : outputHints[output[1]];
                return selector === ".auto-screen-select" ? [select] : selector === ".auto-screen-hint" ? hints : [];
            },
        },
        updatePreview() {ctx.previewUpdates += 1;}, previewUpdates: 0,
        TEAMS_EXTRA_OPTIONS: ["none", "countdown", "flags", "track", "weather"],
        COUNTDOWN_OPTIONS: ["hours", "days", "auto", "off"], currentCountdown: "hours",
        WEEKEND_MODE_OPTIONS: ["off", "auto"], currentWeekendMode: "off",
        AUTO_SCREEN_OPTIONS: ["off", "race_week", "daily"], currentAutoScreen: "off",
        TEMP_UNIT_OPTIONS: ["c", "f"], currentTempUnit: "c", suggestedTempUnit: () => "c",
        OUTPUT_OPTIONS: {format: ["bmp", "png"], rotate: ["0", "180"]}, currentOutput: {format: "bmp", rotate: "0"},
        THEME_OPTIONS: ["light", "dark"], currentTheme: "light",
        currentScreenType: screen, currentUiLang: "en", currentDisplayType: "1bit",
        currentTeamsSeason: 2026, selectedTeamsYear: 2026, currentTeamsExtra: "none",
        selectedYear: null, selectedRound: null, selectedRaceKey: null, selectedTz: null, defaultTimezone: "Europe/Prague",
        ...state,
    });
    for (const name of ["getCurrentSeason", "getApiUrl", "getCopyUrl", "appendTrackParams", "canUsePrerenderedPreview",
        "getPreviewUrl", "setAutoScreen", "selectAutoScreen", "teamsExtraUsesTz", "getTeamsTz", "updateUrlDisplay",
        "initTrackOptions", "getOutputParams", "setOutputOption", "selectOutputOption"]) {
        vm.runInContext(templateFunction(template, name), ctx);
    }
    return {ctx, replaced, select, hints, apiUrl, outputSelects, outputHints};
}

test("auto screen off keeps the copied URL equal to the screen URL", () => {
    for (const screen of ["calendar", "teams"]) {
        const {ctx} = autoScreenContext(screen);
        assert.equal(ctx.getCopyUrl(), ctx.getApiUrl());
        ctx.currentAutoScreen = "bogus";
        assert.equal(ctx.getCopyUrl(), ctx.getApiUrl());
    }
});

test("calendar settings move to /auto.bmp without a fixed race", () => {
    const {ctx} = autoScreenContext("calendar", {
        currentAutoScreen: "race_week", selectedYear: 2026, selectedRaceKey: "2026-round-5-miami",
        currentDisplayType: "bwry", currentCountdown: "days",
    });
    assert.ok(ctx.getApiUrl().includes("race_key="));
    assert.equal(ctx.getCopyUrl(),
        "https://racing.example.org/auto.bmp?tz=Europe%2FPrague&weather=false&display=bwry&countdown=days");
    ctx.currentAutoScreen = "daily";
    assert.equal(ctx.getCopyUrl(),
        "https://racing.example.org/auto.bmp?tz=Europe%2FPrague&weather=false&display=bwry&countdown=days&mode=daily");
    assert.ok(!ctx.getPreviewUrl().includes("/auto.bmp"));
});

test("the weekend mode moves to /auto.bmp with the calendar settings", () => {
    const {ctx} = autoScreenContext("calendar", {currentAutoScreen: "race_week", currentWeekendMode: "auto"});
    assert.equal(ctx.getCopyUrl(),
        "https://racing.example.org/auto.bmp?tz=Europe%2FPrague&weather=false&weekend_mode=auto");
});

test("teams settings keep the chosen panel on /auto.bmp", () => {
    const {ctx} = autoScreenContext("teams", {currentAutoScreen: "race_week"});
    // The timezone is kept although the "none" panel does not use it: the switch does.
    assert.ok(!ctx.getApiUrl().includes("tz="));
    assert.equal(ctx.getCopyUrl(), "https://racing.example.org/auto.bmp?year=2026&extra=none&tz=Europe%2FPrague");
    ctx.currentTeamsExtra = "flags";
    assert.equal(ctx.getCopyUrl(),
        "https://racing.example.org/auto.bmp?year=2026&extra=flags&tz=Europe%2FPrague");
});

test("setAutoScreen syncs the select, the hint, the page URL and the copied URL", () => {
    const {ctx, replaced, select, hints, apiUrl} = autoScreenContext("teams");
    ctx.setAutoScreen("daily");
    assert.equal(ctx.currentAutoScreen, "daily");
    assert.equal(select.value, "daily");
    assert.equal(hints[0].textContent, "hint-daily");
    assert.deepEqual(replaced, ["https://racing.example.org/configure/teams?auto=daily"]);
    assert.equal(apiUrl.value, "https://racing.example.org/auto.bmp?year=2026&extra=none&tz=Europe%2FPrague&mode=daily");
    assert.equal(ctx.previewUpdates, 0);

    select.value = "bogus";
    ctx.selectAutoScreen.call({value: "bogus"});
    assert.equal(ctx.currentAutoScreen, "off");
    assert.equal(replaced.at(-1), "https://racing.example.org/configure/teams");
    assert.equal(apiUrl.value, "https://racing.example.org/teams.bmp?year=2026");

    ctx.setAutoScreen("race_week", false);
    assert.equal(replaced.length, 2);
    assert.equal(hints[0].textContent, "hint-race_week");

    const fresh = autoScreenContext("calendar");
    fresh.ctx.window.location.search = "?auto=race_week";
    fresh.ctx.initTrackOptions();
    assert.equal(fresh.ctx.currentAutoScreen, "race_week");
    fresh.ctx.window.location.search = "?auto=hourly";
    fresh.ctx.currentAutoScreen = "off";
    fresh.ctx.initTrackOptions();
    assert.equal(fresh.ctx.currentAutoScreen, "off");
});

// Output format and rotation (#185 F3): only the copied URL changes, never the preview.
test("format=png and rotate=180 reach the copied URL but not the preview", () => {
    const {ctx} = autoScreenContext("calendar", {currentOutput: {format: "png", rotate: "180"}});
    assert.equal(ctx.getCopyUrl(),
        "https://racing.example.org/calendar.bmp?tz=Europe%2FPrague&weather=false&format=png&rotate=180");
    assert.ok(!ctx.getApiUrl().includes("format="));
    assert.ok(!ctx.getPreviewUrl().includes("format=") && !ctx.getPreviewUrl().includes("rotate="));
    ctx.currentOutput = {format: "png", rotate: "0"};
    assert.equal(ctx.getCopyUrl(), "https://racing.example.org/calendar.bmp?tz=Europe%2FPrague&weather=false&format=png");
    ctx.currentOutput = {format: "bogus", rotate: "90"};
    assert.equal(ctx.getCopyUrl(), ctx.getApiUrl());

    const teams = autoScreenContext("teams", {currentOutput: {format: "bmp", rotate: "180"}, selectedTeamsYear: null,
        currentTeamsSeason: null});
    assert.equal(teams.ctx.getCopyUrl(), "https://racing.example.org/teams.bmp?rotate=180");
});

test("output options move to /auto.bmp with the screen settings", () => {
    const {ctx} = autoScreenContext("teams", {currentAutoScreen: "daily", currentOutput: {format: "png", rotate: "180"}});
    assert.equal(ctx.getCopyUrl(),
        "https://racing.example.org/auto.bmp?year=2026&extra=none&tz=Europe%2FPrague&mode=daily&format=png&rotate=180");
});

test("setOutputOption syncs the select, the hint, the page URL and the copied URL", () => {
    const {ctx, replaced, outputSelects, outputHints, apiUrl} = autoScreenContext("teams");
    ctx.selectOutputOption.call({value: "png"}, "format");
    assert.equal(ctx.currentOutput.format, "png");
    assert.equal(outputSelects.format.value, "png");
    assert.equal(outputHints.format[0].textContent, "hint-format-png");
    assert.deepEqual(replaced, ["https://racing.example.org/configure/teams?format=png"]);
    assert.equal(apiUrl.value, "https://racing.example.org/teams.bmp?year=2026&format=png");
    ctx.selectOutputOption.call({value: "180"}, "rotate");
    assert.equal(replaced.at(-1), "https://racing.example.org/configure/teams?format=png&rotate=180");
    assert.equal(apiUrl.value, "https://racing.example.org/teams.bmp?year=2026&format=png&rotate=180");
    ctx.selectOutputOption.call({value: "270"}, "rotate");
    assert.equal(ctx.currentOutput.rotate, "0");
    assert.equal(outputHints.rotate[0].textContent, "hint-rotate-0");
    assert.equal(replaced.at(-1), "https://racing.example.org/configure/teams?format=png");
    assert.equal(ctx.previewUpdates, 0);

    ctx.setOutputOption("format", "bmp", false);
    assert.equal(replaced.length, 3);
    assert.equal(outputSelects.format.value, "bmp");
    ctx.setOutputOption("unknown", "x");
    assert.equal(replaced.length, 3);

    const fresh = autoScreenContext("calendar");
    fresh.ctx.window.location.search = "?format=png&rotate=180";
    fresh.ctx.initTrackOptions();
    assert.deepEqual({...fresh.ctx.currentOutput}, {format: "png", rotate: "180"});
    fresh.ctx.window.location.search = "?format=jpeg&rotate=90";
    fresh.ctx.currentOutput = {format: "bmp", rotate: "0"};
    fresh.ctx.initTrackOptions();
    assert.deepEqual({...fresh.ctx.currentOutput}, {format: "bmp", rotate: "0"});
});

// Temperature unit (#185 F6): Celsius never reaches a URL, Fahrenheit only where weather is drawn.
test("calendar URLs carry weather_type=weekend and temp_unit=f only with weather", () => {
    const {ctx} = calendarCountdownContext({currentTempUnit: "f"});
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/calendar.bmp?temp_unit=f");
    assert.equal(ctx.getPreviewUrl(),
        "https://racing.example.org/preview/configure/calendar.png?weather_type=race&temp_unit=f");
    ctx.document.querySelector = () => ({value: "weekend"});
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/calendar.bmp?weather_type=weekend&temp_unit=f");
    assert.equal(ctx.getPreviewUrl(),
        "https://racing.example.org/preview/configure/calendar.png?weather_type=weekend&temp_unit=f");
    ctx.currentTempUnit = "c";
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/calendar.bmp?weather_type=weekend");
    ctx.currentTempUnit = "f";
    const getElementById = ctx.document.getElementById;
    ctx.document.getElementById = (id) => (id === "weatherToggle" ? {checked: false} : getElementById(id));
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/calendar.bmp?weather=false");
    assert.equal(ctx.getPreviewUrl(), "https://racing.example.org/preview/configure/calendar.png?weather_type=off");
});

test("teams URLs carry temp_unit=f only with the weather panel", () => {
    const {ctx} = teamsUrlContext({currentTempUnit: "f"});
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/teams.bmp?year=2026");
    ctx.currentTeamsExtra = "countdown";
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/teams.bmp?year=2026&extra=countdown");
    ctx.currentTeamsExtra = "weather";
    assert.equal(ctx.getApiUrl(), "https://racing.example.org/teams.bmp?year=2026&extra=weather&temp_unit=f");
    assert.equal(ctx.getPreviewUrl(),
        "https://racing.example.org/preview/configure/teams.png?extra=weather&temp_unit=f");
});

function tempUnitButtons() {
    return ["c", "f", "c", "f"].map((unit) => {
        const attrs = {};
        return {
            dataset: {tempUnit: unit}, attrs, classes: new Set(),
            setAttribute(name, value) {attrs[name] = value;},
            get classList() {
                const set = this.classes;
                return {toggle(name, on) {if (on) set.add(name); else set.delete(name);}};
            },
        };
    });
}

test("setTempUnit syncs every switch and the page URL, ?temp_unit= initialises it", () => {
    const {ctx, replaced} = calendarCountdownContext();
    const buttons = tempUnitButtons();
    ctx.document.querySelectorAll = (selector) => (selector === ".temp-unit-btn" ? buttons : []);
    vm.runInContext(templateFunction(readFileSync("app/templates/configure.html", "utf8"), "setTempUnit"), ctx);
    ctx.setTempUnit("f");
    assert.equal(ctx.currentTempUnit, "f");
    assert.deepEqual(buttons.map((b) => b.attrs["aria-pressed"]), ["false", "true", "false", "true"]);
    assert.ok(buttons[1].classes.has("bg-black") && !buttons[1].classes.has("bg-white"));
    assert.deepEqual(replaced, ["https://racing.example.org/configure/calendar?temp_unit=f"]);
    assert.equal(ctx.previewUpdates, 1);
    ctx.setTempUnit("kelvin");
    assert.equal(ctx.currentTempUnit, "c");
    assert.equal(replaced.at(-1), "https://racing.example.org/configure/calendar");
    ctx.setTempUnit("f", false);
    assert.equal(ctx.previewUpdates, 2);
    assert.equal(replaced.length, 2);
    // Where °F is suggested, an explicit °C stays in the page URL so a reload keeps it.
    ctx.suggestedTempUnit = () => "f";
    ctx.setTempUnit("c");
    assert.equal(replaced.at(-1), "https://racing.example.org/configure/calendar?temp_unit=c");
    assert.ok(!ctx.getApiUrl().includes("temp_unit"));

    const fresh = calendarCountdownContext({suggestedTempUnit: () => "f"});
    fresh.ctx.window.location.search = "?temp_unit=c";
    fresh.ctx.initTrackOptions();
    assert.equal(fresh.ctx.currentTempUnit, "c");
    fresh.ctx.window.location.search = "";
    fresh.ctx.initTrackOptions();
    assert.equal(fresh.ctx.currentTempUnit, "f");
    fresh.ctx.window.location.search = "?temp_unit=x";
    fresh.ctx.suggestedTempUnit = () => "c";
    fresh.ctx.initTrackOptions();
    assert.equal(fresh.ctx.currentTempUnit, "c");
});

test("suggestedTempUnit picks Fahrenheit only for US time zones", () => {
    const template = readFileSync("app/templates/configure.html", "utf8");
    const suggest = (timeZone) => {
        const ctx = vm.createContext({Intl: {DateTimeFormat: () => ({resolvedOptions: () => ({timeZone})})}});
        vm.runInContext(templateFunction(template, "suggestedTempUnit"), ctx);
        return ctx.suggestedTempUnit();
    };
    assert.equal(suggest("America/New_York"), "f");
    assert.equal(suggest("America/Indiana/Indianapolis"), "f");
    assert.equal(suggest("Pacific/Honolulu"), "f");
    assert.equal(suggest("America/Sao_Paulo"), "c");
    assert.equal(suggest("America/Toronto"), "c");
    assert.equal(suggest("Europe/Prague"), "c");
    assert.equal(suggest(undefined), "c");
    const broken = vm.createContext({Intl: {DateTimeFormat() {throw new Error("no Intl");}}});
    vm.runInContext(templateFunction(template, "suggestedTempUnit"), broken);
    assert.equal(broken.suggestedTempUnit(), "c");
});

test("the °C/°F switches and the weekend weather button are wired to allowed handlers", () => {
    const common = readFileSync("app/assets/js/common.js", "utf8");
    assert.match(common, /"setTempUnit",/);
    const macro = readFileSync("app/templates/configure/_temp_unit.html", "utf8");
    assert.match(macro, /data-on-click="setTempUnit"/);
    const sidebar = readFileSync("app/templates/configure/_calendar_sidebar.html", "utf8");
    assert.equal((sidebar.match(/data-args='\["weekend"\]'/g) || []).length, 2);
    assert.equal((sidebar.match(/temp_unit\.toggle\(/g) || []).length, 2);
    const teams = readFileSync("app/templates/configure/_teams_sidebar.html", "utf8");
    assert.equal((teams.match(/temp_unit\.toggle\(/g) || []).length, 2);
});

// Dark theme (#185 F7): light never reaches a URL; dark changes the screen URL and the preview.
test("theme=dark reaches the screen URL, the preview and /auto.bmp", () => {
    const calendar = calendarCountdownContext({currentTheme: "dark"});
    assert.equal(calendar.ctx.getApiUrl(), "https://racing.example.org/calendar.bmp?theme=dark");
    assert.equal(calendar.ctx.getPreviewUrl(),
        "https://racing.example.org/preview/configure/calendar.png?weather_type=race&theme=dark");
    calendar.ctx.currentTheme = "light";
    assert.equal(calendar.ctx.getApiUrl(), "https://racing.example.org/calendar.bmp");

    const teams = teamsUrlContext({currentTheme: "dark"});
    assert.equal(teams.ctx.getApiUrl(), "https://racing.example.org/teams.bmp?year=2026&theme=dark");
    assert.equal(teams.ctx.getPreviewUrl(), "https://racing.example.org/preview/configure/teams.png?theme=dark");
    teams.ctx.currentTeamsExtra = "flags";
    assert.equal(teams.ctx.getPreviewUrl(),
        "https://racing.example.org/preview/configure/teams.png?extra=flags&theme=dark");

    const auto = autoScreenContext("teams", {currentAutoScreen: "daily", currentTheme: "dark",
        currentOutput: {format: "png", rotate: "0"}});
    assert.equal(auto.ctx.getCopyUrl(),
        "https://racing.example.org/auto.bmp?year=2026&theme=dark&extra=none&mode=daily&format=png");
});

test("setTheme syncs the select, the hint, the page URL and the preview, ?theme= initialises it", () => {
    const template = readFileSync("app/templates/configure.html", "utf8");
    const {ctx, replaced} = autoScreenContext("calendar");
    const choices = ["light", "dark"].map((value) => ({value, dataset: {hint: `hint-theme-${value}`}}));
    const select = {
        options: choices, selectedIndex: 0,
        get value() {return this.selectedIndex >= 0 ? choices[this.selectedIndex].value : "";},
        set value(v) {this.selectedIndex = choices.findIndex((o) => o.value === v);},
    };
    const hints = [{textContent: ""}];
    ctx.document.querySelectorAll = (selector) =>
        (selector === ".theme-select" ? [select] : selector === ".theme-hint" ? hints : []);
    for (const name of ["setTheme", "selectTheme"]) vm.runInContext(templateFunction(template, name), ctx);
    ctx.selectTheme.call({value: "dark"});
    assert.equal(ctx.currentTheme, "dark");
    assert.equal(select.value, "dark");
    assert.equal(hints[0].textContent, "hint-theme-dark");
    assert.deepEqual(replaced, ["https://racing.example.org/configure/calendar?theme=dark"]);
    assert.equal(ctx.previewUpdates, 1);
    ctx.setTheme("sepia");
    assert.equal(ctx.currentTheme, "light");
    assert.equal(hints[0].textContent, "hint-theme-light");
    assert.equal(replaced.at(-1), "https://racing.example.org/configure/calendar");
    ctx.setTheme("dark", false);
    assert.equal(replaced.length, 2);
    assert.equal(ctx.previewUpdates, 2);

    const fresh = autoScreenContext("calendar");
    fresh.ctx.window.location.search = "?theme=dark";
    fresh.ctx.initTrackOptions();
    assert.equal(fresh.ctx.currentTheme, "dark");
    fresh.ctx.window.location.search = "?theme=neon";
    fresh.ctx.currentTheme = "light";
    fresh.ctx.initTrackOptions();
    assert.equal(fresh.ctx.currentTheme, "light");
});

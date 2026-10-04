/**
 * @module i18n-test
 * Responsibilities: Verify the browser localization module's initial, explicit, and system-selected language behavior.
 * Implementation: Run the production script in a VM with minimal document, navigator, and localStorage substitutes.
 * Related Modules: ../frontend/i18n.js; the test checks language selection and fallback without a browser or network.
 * Declaration Index:
 * - makePage: Evaluate the production localization script with deterministic browser substitutes.
 * - makePage.visibleAttribute.setAttribute: Store translated attributes on the localization test node.
 * - makePage.document.documentElement.setAttribute: Provide a no-op attribute method for the document root.
 * - makePage.document.querySelectorAll: Return the two localization nodes for the script's translation query.
 * - makePage.document.getElementById: Return null because these cases do not call setText.
 * - makePage.window.addEventListener: Record event callbacks so system-mode registration can be checked.
 * - makePage.localStorage.getItem: Supply the test's saved language preference.
 * - makePage.localStorage.setItem: Accept preference writes without persistent storage.
 * - callback1: Verify an unset preference starts in English despite a Chinese browser locale and updates text and attributes.
 * - callback2: Verify explicit Chinese and English selections and key fallback for missing English copy.
 * - callback3: Verify explicit system mode follows the Chinese browser locale and registers its change listener.
 * - callback4: Verify stale stored preferences and unsupported API values resolve to English.
 * - callback5: Verify locale resources have matching keys and English values contain no Han characters; missing EN keys never use matching zh values.
 * Variable Index:
 * - SOURCE: Production i18n.js source evaluated by each isolated VM page.
 * Constraints:
 * The mock checks the translation function and text/attribute updates, but does not emulate browser layout or full page rendering. Its MESSAGES reference exists only in the VM test context.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../frontend/i18n.js", import.meta.url), "utf8");

/**
 * Functionality: Create an isolated VM page for one browser locale and saved language preference.
 * Inputs: Optional browserLanguage and savedPreference values; defaults to a Chinese browser locale and no saved preference.
 * Outputs: AppI18n facade, mocked document/listener state, translated nodes, and the VM's localization dictionaries for test-only mutation.
 * Logic: Evaluate the unchanged production source with minimal document, window, navigator, and localStorage substitutes.
 * Constraints: The dictionaries are exposed only from this test's context; production exports remain unchanged and no browser or network is used.
 */
function makePage({ browserLanguage = "zh-CN", savedPreference = null } = {}) {
  const listeners = new Map();
  const visibleText = { dataset: { i18n: "language_en" }, textContent: "Chinese fallback" };
  const visibleAttribute = { dataset: { i18nAttr: "aria-label:missing_english_attribute" }, attributes: {}, /** Store the translated value for an assertion. */ setAttribute(name, value) { this.attributes[name] = value; } };
  const translatedElements = [visibleText, visibleAttribute];
  const document = {
    documentElement: { dataset: {}, lang: "", /** Document-root attributes are outside these localization cases. */ setAttribute() {} },
    /** Return only the two mocked nodes when the production script asks for localizable elements. */ querySelectorAll: (selector) => selector === "[data-i18n], [data-i18n-attr]" ? translatedElements : [],
    /** No test calls setText, so there is no page element to resolve by ID. */ getElementById: () => null,
  };
  const window = { /** Record listeners for verification without creating browser events. */ addEventListener: (name, callback) => listeners.set(name, callback) };
  const localStorage = {
    /** Return the preference injected for this isolated page. */ getItem: () => savedPreference,
    /** Accept writes without retaining data beyond this page instance. */ setItem() {},
  };
  const context = vm.createContext({ document, window, navigator: { language: browserLanguage }, localStorage });
  vm.runInContext(SOURCE, context);
  return { app: window.AppI18n, document, listeners, visibleText, visibleAttribute, messages: vm.runInContext("MESSAGES", context) };
}

/** Verify unset preference, English document metadata, and rendered text/attribute localization under a Chinese browser locale. */
test("localization defaults to English", () => {
  const page = makePage();
  assert.equal(page.app.language(), "en");
  assert.equal(page.document.documentElement.lang, "en");
  assert.equal(page.app.t("language_en"), "English");
  assert.equal(page.visibleText.textContent, "English");
  assert.equal(page.visibleAttribute.attributes["aria-label"], "missing_english_attribute");
});

/** Verify explicit Chinese and English choices and that missing English entries do not reveal Chinese text. */
test("localization preserves explicit locale choices without Chinese fallback", () => {
  const page = makePage();
  page.app.applyLanguage("zh");
  assert.equal(page.app.language(), "zh");
  assert.equal(page.app.t("language_zh"), "中文");
  page.app.applyLanguage("en");
  assert.equal(page.app.language(), "en");
  assert.equal(page.app.t("missing_english_message"), "missing_english_message");
});

/** Verify explicit system mode follows the browser locale and registers the languagechange listener. */
test("localization follows the system only after system mode is selected", () => {
  const page = makePage({ savedPreference: "system" });
  assert.equal(page.app.language(), "zh");
  assert.equal(page.document.documentElement.lang, "zh-CN");
  assert.equal(page.listeners.has("languagechange"), true);
});

/** Verify stale stored preferences and unsupported API values fall back to English. */
test("localization defaults invalid preferences to English", () => {
  const stale = makePage({ savedPreference: "unsupported" });
  assert.equal(stale.app.language(), "en");
  stale.app.applyLanguage("unsupported");
  assert.equal(stale.app.language(), "en");
  assert.equal(stale.app.t("language_zh"), "Chinese");
});

/** Verify matching locale keys, Han-free English values, and no Chinese fallback across dynamic, text, or attribute translation paths. */
test("English resources stay complete and missing English copy never falls back to Chinese", () => {
  const page = makePage();
  const { en, zh } = page.messages;
  assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort());
  for (const value of Object.values(en)) {
    assert.equal(typeof value, "string");
    assert.doesNotMatch(value, /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/u);
  }

  const key = "i18n_missing_english_fixture";
  zh[key] = "中文 fixture text";
  delete en[key];
  page.visibleText.dataset.i18n = key;
  page.visibleAttribute.dataset.i18nAttr = `aria-label:${key}`;
  page.app.applyLanguage("en");
  assert.equal(page.app.t(key), key);
  assert.equal(page.visibleText.textContent, key);
  assert.equal(page.visibleAttribute.attributes["aria-label"], key);
});

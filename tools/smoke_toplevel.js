// Runs the page's inline script top to bottom in a stubbed context: catches errors that a syntax
// check cannot see (a const used before its declaration, a missing global at load time).
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const m = html.match(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>\s*<\/body>/);
const noop = () => {}, el = () => new Proxy(function(){}, {get: (t, k) => k === 'style' || k === 'classList' || k === 'dataset' ? el() : k === Symbol.toPrimitive ? () => '' : el(), apply: () => el(), set: () => true});
const sandbox = { console, setTimeout: noop, setInterval: noop, clearTimeout: noop, fetch: () => Promise.reject(new Error('stub')), Intl, Math, Date, JSON, Map, Set, Promise, URL,
  localStorage: {getItem: () => null, setItem: noop, removeItem: noop}, navigator: {clipboard: {}}, location: {origin: '', href: ''}, performance: {now: () => 0},
  document: {addEventListener: noop, getElementById: () => null, querySelector: () => null, querySelectorAll: () => [], createElement: () => el(), documentElement: {getAttribute: () => 'lab', setAttribute: noop}, body: el()},
  getComputedStyle: () => ({getPropertyValue: () => ''}), Chart: function(){}, XLSX: {}, Tesseract: {}, alert: noop, confirm: () => false };
sandbox.window = sandbox; sandbox.self = sandbox;
try { vm.runInNewContext(m[1], sandbox, {filename: 'index.html.js'}); console.log('top-level run: ok'); }
catch (e) { const ln = (e.stack || '').match(/index\.html\.js:(\d+)/); const base = html.slice(0, html.indexOf(m[1])).split('\n').length - 1;
  console.log('TOP-LEVEL ERROR: ' + e.message + (ln ? ` at index.html line ${+ln[1] + base}` : '')); process.exit(1); }

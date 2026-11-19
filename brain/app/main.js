// Nova Brain -- boot. Loads the graph, then each feature module, then starts
// rendering. A module that fails to load is logged and skipped: the galaxy
// still renders, so one bad file can never blank the panel.
import * as core from './core.js';
import * as data from './data.js';

const MODULES = ['fx', 'director', 'ui', 'feed'];

(async () => {
  const boot = document.getElementById('boot');
  try {
    await data.load();
  } catch (e) {
    console.error('[brain] memory load failed', e);
    if (boot) boot.textContent = 'Memory unavailable';
  }
  const ctx = { core, data };
  window.__brain = Object.assign(window.__brain || {}, { ctx, modules: {} });
  for (const name of MODULES) {
    try {
      const mod = await import(`./${name}.js`);
      if (mod.init) await mod.init(ctx);
      window.__brain.modules[name] = mod;
    } catch (e) {
      console.error(`[brain] module ${name} failed`, e);
    }
  }
  core.start();
  if (boot) boot.classList.add('gone');
  core.toHost({ type: 'ready' });
})();

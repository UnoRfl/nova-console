// Nova Brain -- fx: aggregates the three effect modules (one owner each).
//   fx-gaze.js   X-1 gaze flights + heads + slingshot, X-2 tethers/route/recap, X-8 action jets + iris
//   fx-reveal.js X-3 recall reveal + pours, X-4 search fronts, X-5 lamps + rack focus, X-6 spoken citations
//   fx-life.js   X-7 outer world, X-9 births, X-10 learning sequence (genesis), X-11 endings
// A part that fails to load is logged and skipped; the others still run.
const PARTS = ['fx-gaze', 'fx-reveal', 'fx-life'];
export const parts = {};
export async function init(ctx) {
  for (const name of PARTS) {
    try {
      const mod = await import(`./${name}.js`);
      if (mod.init) await mod.init(ctx);
      parts[name] = mod;
    } catch (e) {
      console.error(`[brain] ${name} failed`, e);
    }
  }
  window.__brain = Object.assign(window.__brain || {}, { fx: parts });
}

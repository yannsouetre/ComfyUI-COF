// Browser part of the ComfyUI end-to-end test: loads ComfyUI's frontend in headless Chromium and checks that web/cof.js
// loads without errors and fills the dropdowns. Run: COMFYUI_URL=http://127.0.0.1:8188 node tests/e2e_browser.js [outdir]
// (needs the `playwright` Node package and a Chromium; ComfyUI running with ComfyUI-COF and clara-mercier.cof + a 1.0 .cof in models/cof)
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const URL = process.env.COMFYUI_URL || 'http://127.0.0.1:8188';
const OUT = process.argv[2] || '.';
const WF = path.join(__dirname, '..', 'workflows', 'cof_preview.json');
const results = []; const check = (name, ok, info = '') => { results.push({ name, ok: !!ok, info }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${info ? '  — ' + info : ''}`); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 } })).newPage();
  const errors = [], bad = [], reqs = [];
  page.on('console', m => {
    if (m.type() !== 'error') return;
    // ComfyUI's own optional files (user.css, userdata/*, comfy.templates.json) answer 404 on a fresh install: not ours
    if (/Failed to load resource/.test(m.text()) && !/cof/i.test(m.location().url || '')) return;
    errors.push(m.text() + ' @ ' + (m.location().url || ''));
  });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('response', r => { reqs.push(r.url()); if (r.status() >= 400) bad.push(`${r.status()} ${r.url()}`); });
  await page.goto(URL, { waitUntil: 'load' });
  await page.waitForFunction(() => window.app && window.app.graph && window.LiteGraph, null, { timeout: 60000 });
  await sleep(3000);
  check('frontend loaded', true);
  check('web/cof.js requested', reqs.some(u => /extensions\/ComfyUI-COF\/cof\.js/.test(u)));
  const defs = await page.evaluate(() => Object.keys(window.LiteGraph.registered_node_types).filter(k => k.startsWith('COF')));
  check('12 COF node types registered in the frontend', defs.length === 12, defs.join(','));

  // workflow
  const wf = JSON.parse(fs.readFileSync(WF, 'utf8'));
  await page.evaluate(w => window.app.loadGraphData(w), wf);
  await sleep(2500);
  const info = await page.evaluate(() => {
    const g = window.app.graph; const n = g._nodes;
    const loader = n.find(x => x.comfyClass === 'COFLoadCharacter');
    const w = k => loader.widgets.find(x => x.name === k);
    return { count: n.length, cof: n.filter(x => (x.comfyClass || '').startsWith('COF')).length, file: w('cof_file').value, files: w('cof_file').options.values,
      preset: w('preset').value, presets: w('preset').options.values, chars: w('character').options.values, missing: n.filter(x => !x.comfyClass && !x.type).length };
  });
  check('cof_preview.json loads (30 nodes)', info.count === 30, `${info.count} nodes, ${info.cof} COF`);
  check('file list has clara-mercier.cof', info.files.includes('clara-mercier.cof'), info.files.length + ' files');
  check('preset list is Clara\'s 12 presets (+ default)', info.presets.length === 13 && info.presets[0] === '(default)', JSON.stringify(info.presets.slice(0, 4)));
  await page.screenshot({ path: path.join(OUT, 'browser-preview-workflow.png') });

  // the UI form of the workflow, queued from the UI itself (graph -> API prompt -> /prompt)
  const full = await page.evaluate(async () => {
    const before = Object.keys(await (await fetch('/history?max_items=1000')).json()).length;
    await window.app.queuePrompt(0, 1);
    for (let i = 0; i < 300; i++) {
      await new Promise(r => setTimeout(r, 500));
      const h = await (await fetch('/history?max_items=1000')).json(); const ks = Object.keys(h);
      if (ks.length > before) { const e = h[ks[ks.length - 1]]; return { status: e.status.status_str, outputs: Object.keys(e.outputs).length, err: (e.status.messages.find(m => m[0] === 'execution_error') || [])[1] }; }
    }
    return { status: 'timeout' };
  });
  check('cof_preview.json queued from the UI executes', full.status === 'success' && full.outputs >= 18, JSON.stringify(full).slice(0, 300));

  // change the file: the preset list must refresh
  const after = await page.evaluate(async () => {
    const loader = window.app.graph._nodes.find(x => x.comfyClass === 'COFLoadCharacter');
    const w = k => loader.widgets.find(x => x.name === k);
    const fw = w('cof_file'); const other = fw.options.values.find(v => v.startsWith('ex-01'));
    fw.value = other; fw.callback?.(other);
    await new Promise(r => setTimeout(r, 1500));
    return { file: fw.value, preset: w('preset').value, presets: w('preset').options.values, chars: w('character').options.values };
  });
  check('changing the file refreshes the preset list', after.presets.length < 13 && after.presets.length >= 2, JSON.stringify(after.presets));
  check('the preset selection is valid after the refresh', after.presets.includes(after.preset), after.preset);
  // back to Clara
  const back = await page.evaluate(async () => {
    const loader = window.app.graph._nodes.find(x => x.comfyClass === 'COFLoadCharacter');
    const w = k => loader.widgets.find(x => x.name === k);
    const fw = w('cof_file'); fw.value = 'clara-mercier.cof'; fw.callback?.(fw.value);
    await new Promise(r => setTimeout(r, 1500));
    return { presets: w('preset').options.values };
  });
  check('back to Clara: 13 presets again', back.presets.length === 13);

  // add a COF Psyche node and wire it to the loader
  const ps = await page.evaluate(async () => {
    const g = window.app.graph; const LG = window.LiteGraph;
    const loader = g._nodes.find(x => x.comfyClass === 'COFLoadCharacter');
    const w = k => loader.widgets.find(x => x.name === k);
    const pr = w('preset'); const fr = pr.options.values.find(v => v.startsWith('fr-12')); pr.value = fr; pr.callback?.(fr);
    const node = LG.createNode('COFPsyche'); node.pos = [loader.pos[0], loader.pos[1] + 900]; g.add(node);
    loader.connect(0, node, 0);
    await new Promise(r => setTimeout(r, 1500));
    const lw = node.widgets.find(x => x.name === 'language');
    const out = { widgets: node.widgets.map(x => x.name), lang: lw.options.values, langValue: lw.value, outputs: node.outputs.map(o => o.name), id: node.id };
    // include_personal toggle refreshes the language list too
    return out;
  });
  check('COF Psyche node has its widgets', JSON.stringify(ps.widgets) === JSON.stringify(['format', 'language', 'focus', 'max_nodes', 'include_secret', 'drop_dangerous', 'include_personal', 'show_illustrations', 'output_illustrations']), ps.widgets.join(','));
  check('COF Psyche language list is filled from the file (preset fr-12)', ps.lang.length >= 2 && /^\(preset\)/.test(ps.lang[0]), JSON.stringify(ps.lang));
  check('COF Psyche outputs text / positions / count / illustrations', ps.outputs.join(',') === 'text,positions,count,illustrations');

  // other pickers: Memories, Appearance
  const pick = await page.evaluate(async () => {
    const g = window.app.graph; const LG = window.LiteGraph;
    const loader = g._nodes.find(x => x.comfyClass === 'COFLoadCharacter');
    const w = k => loader.widgets.find(x => x.name === k);
    const pr = w('preset'); const en = pr.options.values.find(v => v.startsWith('en-65')); pr.value = en; pr.callback?.(en);
    const m = LG.createNode('COFMemories'); m.pos = [loader.pos[0] + 500, loader.pos[1] + 900]; g.add(m); loader.connect(0, m, 0);
    await new Promise(r => setTimeout(r, 1800));
    const mw = m.widgets.find(x => x.name === 'pick');
    return { memories: mw.options.values.length, first: mw.options.values.slice(0, 3) };
  });
  check('COF Memories pick list shows memories by name', pick.memories > 5 && /memory_001 — /.test(pick.first[1]), JSON.stringify(pick.first));

  // run the graph from the UI with the psyche node attached to a preview
  const ran = await page.evaluate(async () => {
    const g = window.app.graph; const LG = window.LiteGraph;
    const ps = g._nodes.find(x => x.comfyClass === 'COFPsyche');
    const pv = LG.createNode('PreviewAny'); g.add(pv); ps.connect(0, pv, 0);
    // keep the queue small: mute everything that is not the loader, the psyche node and its preview
    for (const n of g._nodes) if (n !== pv && n !== ps && n.comfyClass !== 'COFLoadCharacter') n.mode = 2;
    const p = await window.app.graphToPrompt();
    return { n: Object.keys(p.output).length, classes: Object.values(p.output).map(x => x.class_type), psyche: Object.values(p.output).find(x => x.class_type === 'COFPsyche').inputs };
  });
  check('graphToPrompt gives the 3-node API graph', ran.n === 3, ran.classes.join(','));
  check('psyche widget values travel (format/language as shown in the UI)', ran.psyche.format === 'lines' && /^\(preset\)/.test(ran.psyche.language), JSON.stringify(ran.psyche));
  const resp = await page.evaluate(async () => {
    const g = window.app.graph; await window.app.queuePrompt(0, 1);
    await new Promise(r => setTimeout(r, 4000));
    const h = await (await fetch('/history?max_items=1')).json(); const k = Object.keys(h)[0];
    return { status: h[k].status.status_str, out: JSON.stringify(h[k].outputs).slice(0, 300) };
  });
  check('queueing from the UI succeeds', resp.status === 'success', resp.out);
  await page.screenshot({ path: path.join(OUT, 'browser-after-run.png') });

  check('no console error / page error', errors.length === 0, errors.slice(0, 5).join(' | '));
  check('no failed request to the COF routes / extension', !bad.some(u => /cof/i.test(u)), bad.slice(0, 6).join(' | '));
  fs.writeFileSync(path.join(OUT, 'browser-results.json'), JSON.stringify({ results, errors, bad }, null, 1));
  await browser.close();
  process.exit(results.every(r => r.ok) ? 0 : 1);
})().catch(e => { console.error('SCRIPT ERROR', e); process.exit(2); });

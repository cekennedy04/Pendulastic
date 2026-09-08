// Markup contract for the capture view's two comprehension aids: the phone /
// hand placement diagram, and the explanation of the hold and drift gates.
//
// These are asserted against index.html rather than against a rendered DOM
// because there is no DOM here and no bundler -- the file IS the artifact that
// ships. What is worth pinning is not the drawing (which will be redrawn) but
// the properties that make it usable and that a well-meaning edit would
// silently drop: the accessible labelling, and the fact that each gate
// explains its own corrective action.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');

test('the placement diagram is present and accessibly labelled', () => {
  assert.match(html, /id="placement-diagram"/, 'diagram missing from the capture view');
  // role="img" plus aria-labelledby is what makes an inline SVG announce as a
  // single image with a description, instead of as a pile of unlabelled shapes.
  assert.match(html, /id="placement-diagram"[\s\S]*?role="img"/);
  const labelledBy = html.match(/id="placement-diagram"[\s\S]*?aria-labelledby="([^"]+)"/);
  assert.ok(labelledBy, 'diagram has no aria-labelledby');

  // Every id it points at must actually exist, or the label resolves to
  // nothing and a screen reader announces an unlabelled graphic.
  for (const id of labelledBy[1].split(/\s+/)) {
    assert.match(html, new RegExp(`id="${id}"`), `aria-labelledby points at missing id "${id}"`);
  }
});

test('the diagram description names what the clinician has to place', () => {
  const desc = html.match(/<desc id="placement-desc">([\s\S]*?)<\/desc>/);
  assert.ok(desc, 'diagram has no <desc>');
  const text = desc[1].toLowerCase();
  // The description is the ONLY form of this instruction available to a
  // non-sighted user, so it has to carry the same content as the drawing.
  for (const word of ['phone', 'shank', 'knee', 'plinth', 'ankle']) {
    assert.ok(text.includes(word), `description never mentions "${word}"`);
  }
});

test('the phone and hand are labelled in text, not by colour alone', () => {
  // Both are accent-filled in the SVG. Colour alone must not be what carries
  // the instruction -- under outdoor glare, or for a colour-blind reader, the
  // text label is what survives.
  assert.match(html, /<text[^>]*class="pd-label pd-label-accent"[^>]*>phone<\/text>/);
  assert.match(html, /<text[^>]*class="pd-label pd-label-accent"[^>]*>hold here<\/text>/);
});

test('every gate shown as a number has an explanation', () => {
  const gateIds = [...html.matchAll(/<dd id="(calm|drift)"/g)].map((m) => m[1]);
  assert.deepEqual(gateIds.sort(), ['calm', 'drift'], 'the gate numbers changed');
  for (const id of gateIds) {
    assert.match(
      html,
      new RegExp(`data-gate="${id}"`),
      `gate "${id}" is displayed but never explained`,
    );
  }
});

test('each gate explanation states its own corrective action', () => {
  // The two gates are surfaced as separate numbers precisely because the fix
  // differs -- stop moving the leg vs put the leg back. An explanation that
  // described only what the number measures would lose the reason there are
  // two of them.
  // Whitespace-normalised: the copy is wrapped for readability in the source,
  // so a sentence can carry a newline mid-phrase. Matching the raw text would
  // make this test fail on a re-wrap, which is not a defect.
  const flat = (m) => (m ? m[1].replace(/\s+/g, ' ').trim() : null);
  const calm = flat(html.match(/data-gate="calm"[^>]*>([\s\S]*?)<\/dd>/));
  const drift = flat(html.match(/data-gate="drift"[^>]*>([\s\S]*?)<\/dd>/));
  assert.ok(calm && drift, 'gate explanations missing');
  assert.match(calm, /stop moving the leg/i);
  assert.match(drift, /back to its starting position/i);
});

test('the gate help is collapsed by default', () => {
  // It sits directly under two numbers that are read one-handed mid-procedure.
  // Expanded by default, it pushes them off a phone screen.
  const details = html.match(/<details id="gate-help"([^>]*)>/);
  assert.ok(details, '#gate-help is not a <details>');
  assert.ok(!/\bopen\b/.test(details[1]), 'gate help must not start expanded');
});

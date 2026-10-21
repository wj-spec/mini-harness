import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {stripVTControlCharacters} from 'node:util';
import {test} from 'node:test';
import stringWidth from 'string-width';

for (const [columns, rows] of [[80, 24], [120, 40]]) {
  test(`snapshot renders every pane border at the actual ${columns}-column terminal boundary`, () => {
    const result = spawnSync(process.execPath, [fileURLToPath(new URL('./visual-fixture.js', import.meta.url)), 'welcome', String(columns), String(rows), '--snapshot'], {encoding: 'utf8'});
    assert.equal(result.status, 0, result.stderr);
    const lines = stripVTControlCharacters(result.stdout).trimEnd().split('\n');
    assert.equal(lines.length, rows);
    const paneLines = lines.filter(line => /^[╭│╰]/u.test(line));
    assert.ok(paneLines.length > 5);
    for (const line of paneLines) {
      assert.equal(stringWidth(line), columns, `misaligned pane row: ${line}`);
      assert.match(line, /[╮│╯]$/u);
    }
  });
}

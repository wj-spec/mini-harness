import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {stripVTControlCharacters} from 'node:util';
import {test} from 'node:test';
import stringWidth from 'string-width';

for (const [columns, rows] of [[80, 24], [120, 40]]) {
  test(`borderless snapshot keeps surfaces within the actual ${columns}-column terminal boundary`, () => {
    const env: NodeJS.ProcessEnv = {...process.env, FORCE_COLOR: '3'};
    delete env.NO_COLOR;
    const result = spawnSync(process.execPath, [fileURLToPath(new URL('./visual-fixture.js', import.meta.url)), 'welcome', String(columns), String(rows), '--snapshot'], {encoding: 'utf8', env});
    assert.equal(result.status, 0, result.stderr);
    const lines = stripVTControlCharacters(result.stdout).replace(/\n$/, '').split('\n');
    assert.equal(lines.length, rows);
    assert.doesNotMatch(lines.join('\n'), /[╭╮╰╯┌┐└┘─]/u);
    assert.ok(lines.every(line => stringWidth(line) === columns), 'surface rows must fill terminal cells');
    assert.match(lines.join('\n'), /描述任务/);
  });
}

for (const theme of ['light', 'dark']) {
  test(`${theme} snapshots contain actual ANSI background surfaces and readable semantic labels`, () => {
    const env: NodeJS.ProcessEnv = {...process.env, FORCE_COLOR: '3'};
    delete env.NO_COLOR;
    const result = spawnSync(process.execPath, [fileURLToPath(new URL('./visual-fixture.js', import.meta.url)), 'running', '120', '40', '--snapshot', `--theme=${theme}`], {encoding: 'utf8', env});
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /\x1b\[48;2;\d+;\d+;\d+m/u);
    const text = stripVTControlCharacters(result.stdout);
    assert.match(text, /非零退出.*exit=1/);
    assert.match(text, /等待返回/);
    assert.doesNotMatch(text, /[╭╮╰╯┌┐└┘─]/u);
    assert.equal(text.trimEnd().split('\n').length, 40);
  });
}

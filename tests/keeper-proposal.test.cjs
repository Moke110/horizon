const { describe, it } = require('node:test');
const { createRequire } = require('node:module');
const assert = require('node:assert/strict');
const path = require('node:path');
const desktop = path.resolve(__dirname, '../desktop');
const desktopRequire = createRequire(path.join(desktop, 'package.json'));
const { buildSync } = desktopRequire('esbuild');

const bundle = buildSync({
  stdin: {
    contents: `
      import React from 'react';
      import {renderToStaticMarkup} from 'react-dom/server';
      import {KeeperProposalPanel} from './src/KeeperProposalPanel';
      const change = {
        kind:'card', action:'update', id:'card-1', expected_version:1,
        before:{title:'Current title'}, after:{title:'Proposed title'},
      };
      module.exports = (status, revision) => renderToStaticMarkup(
        React.createElement(KeeperProposalPanel, {
          proposalId:'proposal-1', loading:false, error:'',
          onRetry() {}, onClose() {}, onDecide() {},
          proposal:{
            proposal_id:'proposal-1', status, revision, changes:[change],
            revision_notes:{
              previous_revision:1, previous_changes:[change], reason:'keeper_execution_failed',
            },
          },
        }),
      );
    `,
    resolveDir: desktop,
  },
  bundle: true,
  format: 'cjs',
  packages: 'external',
  platform: 'node',
  write: false,
}).outputFiles[0].text;

const renderer = { exports: {} };
new Function('require', 'module', 'exports', bundle)(desktopRequire, renderer, renderer.exports);
const render = renderer.exports;

describe('Keeper proposal revision messages', () => {
  it('shows a return at v1 without a new revision or a comparison against v1 itself', () => {
    const html = render('returned', 1);
    assert.match(html, /已退回/);
    assert.equal(html.includes('Keeper 已生成新的提议修订'), false);
    assert.equal(html.includes('此前修订 v1'), false);
  });

  it('offers a fresh approval and comparison for a published v2', () => {
    const html = render('pending', 2);
    assert.match(html, /Keeper 已生成新的提议修订 v2/);
    assert.match(html, /批准本次修订 v2/);
  });
});

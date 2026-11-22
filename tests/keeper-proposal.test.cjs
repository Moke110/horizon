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
      import {KeeperProposalPanel, keeperCardMerges} from './src/KeeperProposalPanel';
      import {KeeperGroupUndoPanel, keeperUndoPreviewCommand, keeperUndoConfirmCommand, keeperUndoCanConfirm, keeperUndoPreviewFromPayload} from './src/KeeperGroupUndoPanel';
      const change = {
        kind:'card', action:'update', id:'card-1', expected_version:1,
        before:{title:'Current title'}, after:{title:'Proposed title'},
      };
      const render = (status, revision) => renderToStaticMarkup(
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
      const mergeProposal = (extra={}) => ({
        proposal_id:'proposal-merge', status:'pending', revision:2,
        changes:[
          {
            kind:'card', action:'update', id:'survivor', expected_version:2,
            before:{
              title:'Survivor', content:'old retained body',
              sources:[{material_id:'material-1', material_version:1, locator:'p.1', excerpt:'old evidence'}],
              relations:['card-alpha'],
            },
            after:{
              title:'Survivor merged', content:'merged retained body',
              sources:[
                {material_id:'material-1', material_version:1, locator:'p.1', excerpt:'old evidence'},
                {material_id:'material-2', material_version:4, locator:'p.9', excerpt:'donor evidence'},
              ],
              relations:['card-alpha','card-beta'],
            },
            merge_from:{card_id:'donor', version:3, title:'Donor'},
          },
          {
            kind:'card', action:'delete', id:'donor', expected_version:3,
            before:{
              title:'Donor', content:'donor body',
              sources:[{material_id:'material-2', material_version:4, locator:'p.9', excerpt:'donor evidence'}],
              relations:['card-gamma'],
            },
          },
          {
            kind:'card', action:'update', id:'card-inbound', expected_version:4,
            before:{title:'Inbound card', relations:['donor','card-keep']},
            after:{title:'Inbound card', relations:['survivor','card-keep']},
          },
        ],
        ...extra,
      });
      const renderMerge = (extra={}) => renderToStaticMarkup(
        React.createElement(KeeperProposalPanel, {
          proposalId:'proposal-merge', loading:false, error:'',
          onRetry() {}, onClose() {}, onDecide() {},
          proposal:mergeProposal(extra),
        }),
      );
      const themeChange = {
        kind:'theme', action:'update', id:'theme-1', expected_version:4,
        before:{judgement:'Old judgement'},
        after:{
          judgement:'New judgement',
          evidence:{
            supports:[{reason:'support reason', source:{material_id:'material-a', locator:'p.2', excerpt:'support excerpt'}}],
            opposes:[{stance:'opposes', reason:'oppose reason', source:{material_id:'material-b', material_version:6, locator:'p.7', excerpt:'oppose excerpt'}}],
          },
        },
      };
      const renderTheme = () => renderToStaticMarkup(
        React.createElement(KeeperProposalPanel, {
          proposalId:'proposal-theme', loading:false, error:'',
          onRetry() {}, onClose() {}, onDecide() {},
          proposal:{proposal_id:'proposal-theme', status:'pending', revision:1, changes:[themeChange]},
        }),
      );
      const themeArrayChange = {
        ...themeChange,
        after:{
          judgement:'New judgement',
          evidence:[
            {stance:'supports', reason:'array support reason', source:{material_id:'material-a', locator:'p.2', excerpt:'array support excerpt'}},
            {stance:'opposes', reason:'array oppose reason', source:{material_id:'material-b', locator:'p.7', excerpt:'array oppose excerpt'}},
          ],
        },
      };
      const renderThemeArray = () => renderToStaticMarkup(
        React.createElement(KeeperProposalPanel, {
          proposalId:'proposal-theme-array', loading:false, error:'',
          onRetry() {}, onClose() {}, onDecide() {},
          proposal:{proposal_id:'proposal-theme-array', status:'pending', revision:1, changes:[themeArrayChange]},
        }),
      );
      const renderUndoPanel = (receipt=null) => renderToStaticMarkup(
        React.createElement(KeeperGroupUndoPanel, {
          proposal:{proposal_id:'proposal-merge', status:'approved', revision:2, changes:mergeProposal().changes},
          receipt,
          onCommand() {},
          onUndone() {},
        }),
      );
      const renderUndoReceipt = () => renderToStaticMarkup(
        React.createElement(KeeperProposalPanel, {
          proposalId:'proposal-merge', loading:false, error:'',
          onRetry() {}, onClose() {}, onDecide() {},
          proposal:{
            ...mergeProposal(), status:'approved',
            undo:{operation_id:'undo-9', revision:2, status:'undone', applied:[{kind:'card', id:'survivor', version:3, status:'active'}]},
          },
        }),
      );
      module.exports = {
        render, renderMerge, renderTheme, renderThemeArray, renderUndoPanel, renderUndoReceipt,
        previewCommand: keeperUndoPreviewCommand,
        confirmCommand: keeperUndoConfirmCommand,
        canConfirm: keeperUndoCanConfirm,
        previewFromPayload: keeperUndoPreviewFromPayload,
        mergeCount: proposal => keeperCardMerges(proposal).length,
      };
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
const ui = renderer.exports;

describe('Keeper proposal revision messages', () => {
  it('shows a return at v1 without a new revision or a comparison against v1 itself', () => {
    const html = ui.render('returned', 1);
    assert.match(html, /已退回/);
    assert.equal(html.includes('Keeper 已生成新的提议修订'), false);
    assert.equal(html.includes('此前修订 v1'), false);
  });

  it('offers a fresh approval and comparison for a published v2', () => {
    const html = ui.render('pending', 2);
    assert.match(html, /Keeper 已生成新的提议修订 v2/);
    assert.match(html, /批准本次修订 v2/);
  });
});

describe('Keeper card merge preview', () => {
  it('shows survivor and donor pins, retained body, source evidence, and both relation directions', () => {
    const html = ui.renderMerge();
    assert.match(html, /合并知识卡片/);
    assert.match(html, /survivor/);
    assert.match(html, /donor/);
    assert.match(html, /校验 v2/);
    assert.match(html, /校验 v3/);
    assert.match(html, /merged retained body/);
    assert.match(html, /material-2/);
    assert.match(html, /donor evidence/);
    assert.match(html, /出向关系/);
    assert.match(html, /入向关系/);
    assert.match(html, /card-alpha/);
    assert.match(html, /card-beta/);
    assert.match(html, /card-inbound/);
  });

  it('keeps ordinary ChangeCard before and after field values', () => {
    const html = ui.renderMerge();
    assert.match(html, /变更前/);
    assert.match(html, /变更后/);
    assert.match(html, /old retained body/);
    assert.match(html, /merged retained body/);
  });

  it('accepts matching group merge metadata without merge_from on the change', () => {
    const proposal = {
      proposal_id: 'proposal-merge', status: 'pending', revision: 2,
      merge: { survivor_id: 'survivor', donor_id: 'donor', relations: { inbound: { before: ['card-inbound'], after: ['card-inbound', 'card-gamma'] } } },
      changes: [
        { kind: 'card', action: 'update', id: 'survivor', expected_version: 2, before: { title: 'Survivor' }, after: { title: 'Survivor merged' } },
        { kind: 'card', action: 'delete', id: 'donor', expected_version: 3, before: { title: 'Donor' } },
      ],
    };
    assert.equal(ui.mergeCount(proposal), 1);
    const html = ui.renderMerge({ merge: proposal.merge, changes: proposal.changes });
    assert.match(html, /合并知识卡片/);
    assert.match(html, /card-inbound/);
  });
});

describe('Keeper theme evidence snapshot', () => {
  it('shows readable two-sided stance, reason, excerpt and locator from a normalized snapshot', () => {
    const html = ui.renderTheme();
    assert.match(html, /支持/);
    assert.match(html, /反对/);
    assert.match(html, /support reason/);
    assert.match(html, /support excerpt/);
    assert.match(html, /p\.2/);
    assert.match(html, /oppose reason/);
    assert.match(html, /oppose excerpt/);
    assert.match(html, /p\.7/);
  });

  it('accepts the canonical Theme evidence array with explicit stance values', () => {
    const html = ui.renderThemeArray();
    assert.match(html, /array support reason/);
    assert.match(html, /array support excerpt/);
    assert.match(html, /array oppose reason/);
    assert.match(html, /array oppose excerpt/);
  });
});

describe('Keeper group undo renderer', () => {
  it('offers only the preview action before a checked preview exists', () => {
    const html = ui.renderUndoPanel();
    assert.match(html, /预览整组撤销/);
    assert.equal(html.includes('确认整组撤销'), false);
  });

  it('builds preview and confirm payloads with the named correlation fields', () => {
    assert.deepEqual(ui.previewCommand({ proposal_id: 'proposal-merge', revision: 2 }, 'op-preview'), {
      action: 'keeper_undo_preview', schema_version: 1, operation_id: 'op-preview',
      proposal_id: 'proposal-merge', revision: 2,
    });
    assert.deepEqual(ui.confirmCommand(
      { proposal_id: 'proposal-merge', revision: 2 },
      { expected_versions: [1, 2], preview_token: 'token-1' },
      'op-undo', ' restore ',
    ), {
      action: 'keeper_undo', schema_version: 1, operation_id: 'op-undo',
      proposal_id: 'proposal-merge', revision: 2,
      expected_versions: [1, 2], preview_token: 'token-1', reason: 'restore',
    });
  });

  it('blocks confirmation on conflicts, non-undoable previews, or missing token/version order', () => {
    assert.equal(ui.canConfirm({ can_undo: true, preview_token: 'token-1', expected_versions: [1], changes: [] }), true);
    assert.equal(ui.canConfirm({ can_undo: true, preview_token: 'token-1', expected_versions: [1], changes: [{ conflict: true }] }), false);
    assert.equal(ui.canConfirm({ can_undo: false, preview_token: 'token-1', expected_versions: [1], changes: [] }), false);
    assert.equal(ui.canConfirm({ can_undo: true, preview_token: '', expected_versions: [1], changes: [] }), false);
    assert.equal(ui.canConfirm({ can_undo: true, preview_token: 'token-1', changes: [] }), false);
    assert.equal(ui.canConfirm({ can_undo: true, preview_token: 'token-1', expected_versions: [], changes: [] }), false);
  });

  it('normalizes the K9 preview targets and field-level approved/current/inverse changes', () => {
    const preview = ui.previewFromPayload({
      can_undo: false, code: 'version_conflict', expected_versions: [2],
      targets: [{
        kind: 'card', id: 'card-1', action: 'restore', approval_action: 'update',
        approved_version: 2, current_version: 3, can_undo: false, code: 'version_conflict',
        projected: { card_id: 'card-1', version: 4 },
        changes: [{ field: 'title', approved: 'Keeper title', current: 'Edited later', inverse: 'Original title' }],
      }],
    }, { operation_id: 'op-preview', proposal_id: 'proposal-merge', revision: 2 });
    assert.equal(preview.changes.length, 1);
    assert.equal(preview.changes[0].target_version, 2);
    assert.equal(preview.changes[0].current_version, 3);
    assert.equal(preview.changes[0].conflict, true);
    assert.deepEqual(preview.changes[0].field_changes[0], {
      field: 'title', approved: 'Keeper title', current: 'Edited later', inverse: 'Original title',
      before: undefined, applied: undefined, proposed: undefined, conflict: false,
    });
  });

  it('renders the persisted undo receipt from keeper_open independently of the decision receipt', () => {
    const html = ui.renderUndoReceipt();
    assert.match(html, /整组撤销已生效/);
    assert.match(html, /undo-9/);
    assert.match(html, /survivor/);
    assert.match(html, /版本 v3/);
    assert.equal(html.includes('预览整组撤销'), false);
  });
});

const {app} = require('electron');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');

const timeout = setTimeout(() => finish('Timed out', 1), 15000);
function finish(result, code) {
  clearTimeout(timeout);
  fs.writeFileSync(process.env.HORIZON_SMOKE_RESULT, result);
  server.close();
  app.exit(code);
}

function blankPdf() {
  const objects = [
    '<</Type /Catalog /Pages 2 0 R>>',
    '<</Type /Pages /Count 1 /Kids [3 0 R]>>',
    '<</Type /Page /Parent 2 0 R /MediaBox [0 0 200 200]>>',
  ];
  let pdf = '%PDF-1.4\n';
  const offsets = [];
  for (const [index, object] of objects.entries()) {
    offsets.push(pdf.length);
    pdf += (index + 1) + ' 0 obj\n' + object + '\nendobj\n';
  }
  const start = pdf.length;
  pdf += 'xref\n0 4\n0000000000 65535 f \n';
  for (const offset of offsets) pdf += String(offset).padStart(10, '0') + ' 00000 n \n';
  pdf += 'trailer\n<</Size 4 /Root 1 0 R>>\nstartxref\n' + start + '\n%%EOF\n';
  return Buffer.from(pdf).toString('base64');
}

const server = net.createServer(socket => socket.on('data', chunk => {
  if (chunk.toString().includes('"action":"report_open"')) {
    socket.write(JSON.stringify({kind:'report_view',data:{
      task_id:'smoke', report_id:'report', version:1, title:'Test report',
      claims:[{kind:'known',text:'Report body',citations:[]}],
    }}) + '\n');
  }
  if (chunk.toString().includes('"material_id":"pdf"')) {
    socket.write(JSON.stringify({kind:'private_pdf', data:{id:'pdf',data:blankPdf()}}) + '\n');
    socket.write(JSON.stringify({kind:'material_package',data:{
      material_id:'pdf', title:'Test PDF', source:{label:'Source',source_id:'source'},
      snapshot:{snapshot_id:'snap',content_hash:'hash',extraction_version:'1'},
      version:1, summary:null, original_text:'',
      pdf:{anchors:[{page:1,start:0,end:0}],status:'available',reason:''},
    }}) + '\n');
    return;
  }
  if (chunk.toString().includes('"action":"materials_open"')) {
    socket.write(JSON.stringify({kind:'material_package',data:{
      material_id:'material', title:'Test source', source:{label:'Source',source_id:'source'},
      snapshot:{snapshot_id:'snap',content_hash:'hash',extraction_version:'1'},
      version:1, summary:null, original_text:'Original HTML snapshot',
      web:{url:'https://example.com/',metadata:{},status:'available',anchors:[{id:'body',start:0,end:22}]},
    }}) + '\n');
  }
  if (!chunk.toString().includes('"action":"ready"')) return;
  socket.write(JSON.stringify({kind:'state', data:{
    chats:[{task_id:'smoke', title:'Smoke', messages:[]}], current:0, busy:false,
    children:[{child_id:'report',role:'editor',status:'succeeded',result:JSON.stringify({report_id:'report',version:1})}], providers:[], materials:[{material_id:'material', title:'Test source', source:{label:'Source'}}, {material_id:'pdf', title:'Test PDF', source:{label:'Source'}}],
  }}) + '\n');
}));
server.listen(0, '127.0.0.1', () => {
  process.env.HORIZON_DESKTOP_PORT = String(server.address().port);
  process.env.HORIZON_DESKTOP_TOKEN = 'desktop-smoke';
  require('./dist/main.cjs');
});

app.on('browser-window-created', (_, win) => win.webContents.once('did-finish-load', async () => {
  try {
    const result = await win.webContents.executeJavaScript(`(async () => {
      const wait = async (get) => {
        for (let i = 0; i < 50; i++) {
          const value = get();
          if (value) return value;
          await new Promise(resolve => setTimeout(resolve, 50));
        }
        throw Error('Missing UI: ' + get);
      };
      const toggle = await wait(() => document.querySelector('button[aria-controls="workspace-navigation"]'));
      const navigation = document.getElementById('workspace-navigation');
      const wide = navigation.getBoundingClientRect().width;
      toggle.click();
      await wait(() => navigation.getBoundingClientRect().width < wide);
      toggle.click();
      await wait(() => navigation.getBoundingClientRect().width === wide);
      const overlay = navigator.windowControlsOverlay?.getTitlebarAreaRect();
      if (navigator.userAgent.includes('Windows') && (!overlay?.height || overlay.width >= innerWidth)) {
        throw Error('The native caption does not reserve space for Windows window buttons');
      }
      navigation.querySelector('button[aria-label="资料"]').click();
      const material = await wait(() => Array.from(document.querySelectorAll('[aria-label="资料列表"] button'))
        .find(button => button.textContent.includes('Test source')));
      const main = document.querySelector('main');
      const full = main.getBoundingClientRect().width;
      material.click();
      const detail = await wait(() => document.querySelector('[aria-label="工作区详情"]'));
      await wait(() => detail.textContent.includes('Original HTML snapshot'));
      if (main.getBoundingClientRect().width >= full || !main.contains(material)) throw Error('Main panel was replaced');
      const before = detail.getBoundingClientRect().width;
      document.querySelector('[role="separator"]').dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowRight', bubbles:true}));
      await wait(() => detail.getBoundingClientRect().width < before);
      detail.querySelector('[aria-label="关闭详情"]').click();
      await wait(() => main.getBoundingClientRect().width === full);
      const pdf = Array.from(document.querySelectorAll('[aria-label="资料列表"] button'))
        .find(button => button.textContent.includes('Test PDF'));
      if (!pdf) throw Error('Missing PDF launcher');
      pdf.click();
      await wait(() => document.querySelector('[aria-label="工作区详情"] canvas')?.height === 300);
      (await wait(() => navigation.querySelector('.sessions button'))).click();
      const reportsSummary = await wait(() => document.querySelector('.messages details summary'));
      reportsSummary.click();
      const report = await wait(() => Array.from(document.querySelectorAll('.messages button'))
        .find(button => button.textContent.includes('打开报道')));
      report.click();
      await wait(() => document.querySelector('[aria-label="工作区详情"] article')?.textContent.includes('Report body'));
      if (!main.contains(report)) throw Error('The report launcher did not stay in the main panel');
      return 'Desktop smoke passed: caption overlay, navigation, HTML snapshot, PDF rendering, report, panel resize and close';
    })()`);
    const fixture = path.join(path.dirname(process.env.HORIZON_SMOKE_RESULT), 'research-view.html');
    fs.writeFileSync(fixture, '<h1>Saved HTML report</h1><script>throw Error("unsafe")</script><p><strong>Report emphasis</strong></p>');
    await win.webContents.executeJavaScript(`document.querySelector('button[aria-label="资料"]').click()`);
    win.webContents.debugger.attach('1.3');
    try {
      const {root} = await win.webContents.debugger.sendCommand('DOM.getDocument');
      const {nodeId} = await win.webContents.debugger.sendCommand('DOM.querySelector', {
        nodeId: root.nodeId, selector: 'input[type="file"][accept*="text/html"]',
      });
      if (!nodeId) throw Error('No HTML opener in materials');
      await win.webContents.debugger.sendCommand('DOM.setFileInputFiles', {nodeId, files:[fixture]});
    } finally {
      win.webContents.debugger.detach();
    }
    await win.webContents.executeJavaScript(`(async () => {
      for (let i = 0; i < 50; i++) {
        const detail = document.querySelector('[aria-label="工作区详情"]');
        if (detail?.querySelector('h1')?.textContent === 'Saved HTML report') {
          if (detail.querySelector('script') || detail.querySelector('strong')?.textContent !== 'Report emphasis') {
            throw Error('Stored HTML formatting or script isolation failed');
          }
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      throw Error('Saved HTML did not open in the detail panel');
    })()`);
    if (process.env.HORIZON_SMOKE_SCREENSHOT) {
      fs.writeFileSync(process.env.HORIZON_SMOKE_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    }
    finish(result + '; saved HTML opened with formatting', 0);
  } catch (error) {
    finish(String(error), 1);
  }
}));

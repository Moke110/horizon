const {app, dialog} = require('electron');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');

if (process.env.HORIZON_SMOKE_PROFILE) app.setPath('userData', process.env.HORIZON_SMOKE_PROFILE);

const timeout = setTimeout(() => finish('Timed out', 1), 30000);
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

const libraryState = {
  chats:[{task_id:'smoke', title:'Smoke', messages:[]}], current:0, busy:false,
  cards:[{card_id:'related-term',version:1,title:'Related term',aliases:['Ａlternate name'],content:'Definition only',status:'active',updated_at:1}],
  questions:[{question_id:'q1',version:1,prompt:'Prompt marker',answer:'Answer marker [[term:related-term]]',term_ids:['related-term']}],
  bulletin_links:[
    {kind:'term',id:'related-term',title:'Related term',summary:'Definition only',term_ids:['related-term'],validity:'valid'},
    {kind:'question',id:'q1',title:'Prompt marker',summary:'Answer marker',term_ids:['related-term'],validity:'valid'},
    {kind:'frontier',id:'following',title:'following',summary:'',term_ids:[]}
  ],
  children:[{child_id:'report',role:'editor',status:'succeeded',result:JSON.stringify({report_id:'report',version:1})}],
  providers:[], materials:[{material_id:'material', title:'Test source', source:{label:'Source'}}, {material_id:'pdf', title:'Test PDF', source:{label:'Source'}}],
};
libraryState.frontiers=['following','paused'].map(status=>({frontier_id:status,version:1,question:status,status,tracked:status==='following'}));
for(const [prompt,validity] of [['q2','valid'],['q3','questionable']]) {
  libraryState.questions.push({question_id:prompt,prompt,answer:'',term_ids:[],validity,tracked:true});
}
const pdfFileName = 'chooser-fixture.pdf';
const pdfFixture = path.join(path.dirname(process.env.HORIZON_SMOKE_RESULT), pdfFileName);
fs.writeFileSync(pdfFixture, Buffer.from(blankPdf(), 'base64'));
dialog.showOpenDialog = async () => ({canceled:false, filePaths:[pdfFixture]});
let registeredPdf = false;
const server = net.createServer(socket => socket.on('data', chunk => {
  for (const line of chunk.toString().trim().split('\n')) {
    if(line.includes('"action":"frontier_open"')){
      socket.write(JSON.stringify({kind:'frontier_view',data:{...libraryState.frontiers[0],
        operation_id:'seed',read_operation_id:JSON.parse(line).operation_id,
        scope:{text:'',alternatives:[],sources:[]},hard_constraints:{must_include:[],exclude:[]},relations:[]}})+'\n');
    }
    if (!line.includes('"action":"question_open"') && !line.includes('"action":"card_open"')) continue;
    const command = JSON.parse(line);
    const view = command.action === 'question_open'
      ? {kind:'question_view',data:libraryState.questions.find(row=>row.question_id===command.question_id)}
      : {kind:'card_view',data:{...libraryState.cards[0],questionable:true,sources:[],relations:[],known:true,read_operation_id:command.operation_id}};
    socket.write(JSON.stringify(view)+'\n');
  }
  const pdfLine = chunk.toString().split('\n').find(line => line.includes('"action":"materials_pdf_register"'));
  if (pdfLine) {
    const command = JSON.parse(pdfLine);
    registeredPdf = command.title === pdfFileName && command.pdf_base64 === blankPdf();
  }
  const questionLine = chunk.toString().split('\n')
    .find(line => line.includes('"action":"question_create"') || line.includes('"action":"question_confirm"'));
  if (questionLine) {
    const command = JSON.parse(questionLine);
    const confirm = command.action === 'question_confirm';
    if (confirm && command.choice !== 'confirm') return;
    const review = {entity:'question', operation_id:command.operation_id,
      status:confirm ? 'confirmed' : 'pending', expires_at:Date.now()/1000+60,
      changes:[['create',{prompt:command.prompt, answer:command.answer}]]};
    socket.write(JSON.stringify({kind:'question_confirmation',data:review}) + '\n');
    socket.write(JSON.stringify({kind:'state',data:{...libraryState,gui_term_confirmations:[review]}}) + '\n');
    return;
  }
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
  socket.write(JSON.stringify({kind:'state',data:libraryState}) + '\n');
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
      if (navigation.querySelector('button[aria-label="词典"]')) throw Error('Dictionary still has separate navigation');
      navigation.querySelector('button[aria-label="百科"]').click();
      const entryType = await wait(() => document.querySelector('select[aria-label="条目类型"]'));
      if (entryType.value !== 'question') throw Error('Expected questions on entry');
      const switchType = type => {
        entryType.value=type;
        entryType.dispatchEvent(new Event('change',{bubbles:true}));
      };
      const search = await wait(() => document.querySelector('form[aria-label="搜索导航栏"]'));
      const query = search.querySelector('input[type="search"]');
      const searchFor = async value => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(query,value);
        query.dispatchEvent(new Event('input',{bubbles:true}));
        await new Promise(resolve=>setTimeout(resolve,0));
        search.requestSubmit();
        await new Promise(resolve=>setTimeout(resolve,0));
      };
      const results = () => cards()[0];
      const status=search.querySelector('.dictionary-scope'), tracking=search.querySelector('select[aria-label="追踪中"]');
      if(!tracking||tracking.value!=='all'||status.value!=='all') throw Error('Missing initial encyclopedia filters');
      const choose=async (select,value)=>{
        select.value=value;
        select.dispatchEvent(new Event('change',{bubbles:true}));
        await new Promise(resolve=>setTimeout(resolve,0));
      };
      const cards=()=>[...document.querySelectorAll('.hpedia-panel [data-result]')].filter(card=>card.checkVisibility());
      const verifyMark=(card,label,dot)=>{
        if(!card||card.title!==label||card.getAttribute('aria-description')!==label) throw Error('Missing accessible state '+label);
        const mark=card.querySelector('.entry-validity');
        if((mark?getComputedStyle(mark).backgroundColor:null)!==dot) throw Error('Wrong validity dot '+label);
        const yellow=getComputedStyle(card).borderTopColor==='rgb(232, 190, 76)';
        if(yellow!==label.endsWith('追踪中：是')) throw Error('Wrong tracking border '+label);
      };
      const green='rgb(98, 195, 135)', yellow='rgb(232, 190, 76)';
      verifyMark(cards()[0],'状态：有效，追踪中：否',green);
      verifyMark(cards()[1],'状态：有效，追踪中：是',green);
      verifyMark(cards()[2],'状态：存疑，追踪中：是',yellow);
      await choose(status,'questionable');
      await choose(tracking,'no');
      if(cards().length) throw Error('Filters must intersect');
      await choose(tracking,'yes');
      if(cards().length!==1||!cards()[0].textContent.includes('q3')) throw Error('Missing questionable followed question');
      switchType('frontier');
      await wait(()=>status.disabled && status.selectedOptions[0].textContent==='不适用');
      verifyMark(cards()[0],'追踪中：是',null);
      if(cards().length!==1) throw Error('Lost tracking filter');
      await choose(tracking,'no');
      verifyMark(cards()[0],'追踪中：否',null);
      switchType('term');
      await wait(()=>!status.disabled && status.value==='questionable');
      if(cards().length) throw Error('Type switch lost validity filter');
      await choose(status,'valid');
      verifyMark(cards()[0],'状态：有效，追踪中：否',green);
      await choose(status,'all');
      await choose(tracking,'all');
      switchType('question');
      for (const value of ['Prompt marker','Answer marker']) {
        await searchFor(value);
        if (!results()?.textContent.includes('Prompt marker')) throw Error('Missing question for '+value);
      }
      results().click();
      (await wait(()=>document.querySelector('.hpedia-term-link'))).click();
      const linkedTerm=await wait(()=>document.querySelector('article[aria-label="术语"]'));
      if(!linkedTerm.textContent.includes('Definition only')) throw Error('Linked term content missing');
      if(!linkedTerm.textContent.includes('存疑 · 追踪中')) throw Error('Missing legacy questionable state');
      linkedTerm.querySelector('button').click();
      (await wait(()=>document.querySelector('[aria-label="关闭详情"]'))).click();
      switchType('term');
      await wait(()=>document.querySelector('[aria-label="百科术语结果"]'));
      if(query.value!=='Answer marker'||results()) throw Error('Type switch lost query or mixed results');
      for (const value of ['Related term','Alternate name','Definition only']) {
        await searchFor(value);
        if(!results()?.textContent.includes('Related term')) throw Error('Missing term for '+value);
      }
      results().click();
      await wait(()=>Array.from(document.querySelectorAll('textarea')).some(field=>field.value==='Definition only'));
      document.querySelector('[aria-label="关闭详情"]').click();
      await searchFor('');
      const queryBox = search.querySelector('input[type="search"]').getBoundingClientRect();
      const submitButton = search.querySelector('button[type="submit"]');
      const searchIcon = submitButton.getBoundingClientRect();
      if (searchIcon.left < queryBox.right || Math.abs(searchIcon.top - queryBox.top) > 1 ||
          !submitButton.querySelector('svg') || submitButton.textContent.trim()) {
        throw Error('Dictionary search icon must sit beside the query input');
      }
      const picker = search.querySelector('[role="combobox"]');
      picker.focus();
      await wait(() => picker.getAttribute('aria-expanded') === 'true');
      picker.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
      await wait(() => picker.getAttribute('aria-expanded') === 'false');
      (await wait(() => document.querySelector('.dictionary-heading button'))).click();
      (await wait(() => document.querySelector('#hpedia-create:popover-open [value=question]'))).click();
      await wait(() => document.querySelector('[aria-label="工作区详情"] form'));
      document.querySelector('[aria-label="关闭详情"]').click();
      await wait(() => !document.querySelector('[aria-label="工作区详情"]'));
      if (document.querySelector('[aria-label="条目类型"]').value !== 'term') throw Error('Cancel lost source type');
      document.querySelector('.dictionary-heading button').click();
      (await wait(() => document.querySelector('#hpedia-create:popover-open [value=term]'))).click();
      const termField = await wait(() => Array.from(document.querySelectorAll('form label'))
        .find(label => label.textContent.trim() === '释义')?.querySelector('textarea'));
      const singleLineHeight = termField.getBoundingClientRect().height;
      termField.value = Array(10).fill('A long definition').join(String.fromCharCode(10));
      const lineHeight = Number.parseFloat(getComputedStyle(termField).lineHeight);
      if (!Number.isFinite(lineHeight) || Math.abs(termField.getBoundingClientRect().height - singleLineHeight - lineHeight * 5) > 1 ||
          termField.scrollHeight <= termField.clientHeight) {
        throw Error('Term definition did not grow to six visible lines and scroll');
      }
      const buttonByText = (root, text) => Array.from(root.querySelectorAll('button'))
        .find(button => button.textContent.trim() === text);
      const clickNamed = name => document.querySelector('button[aria-label="' + name + '"]').click();
      const press = (element, key) => element.dispatchEvent(new KeyboardEvent('keydown', {key, bubbles:true}));
      const setField = (field, value) => {
        const proto = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype :
          field instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(field, value);
        field.dispatchEvent(new Event(field instanceof HTMLSelectElement ? 'change' : 'input', {bubbles:true}));
      };
      const toggleList = async label => {
        const toggle = await wait(() => Array.from(document.querySelectorAll('button[aria-expanded][aria-controls]'))
          .find(button => button.firstElementChild?.textContent === label));
        toggle.click();
        return document.getElementById(toggle.getAttribute('aria-controls'));
      };
      const termFormTitle = Array.from(termField.form.querySelectorAll('label'))
        .find(label => label.textContent.trim() === '标题').querySelector('input');
      setField(termFormTitle, 'Interaction test');
      setField(termField, 'A definition');
      const aliases = await toggleList('别名');
      clickNamed('新增别名');
      let aliasInput = await wait(() => aliases.querySelector('input'));
      setField(aliasInput, 'Term alias');
      press(aliasInput, 'Enter');
      await wait(() => aliases.textContent.includes('Term alias'));
      clickNamed('编辑别名 Term alias');
      aliasInput = await wait(() => aliases.querySelector('input'));
      setField(aliasInput, 'Discarded');
      press(aliasInput, 'Escape');
      await wait(() => aliases.textContent.includes('Term alias'));
      clickNamed('编辑别名 Term alias');
      aliasInput = await wait(() => aliases.querySelector('input'));
      setField(aliasInput, 'Outside saved');
      termField.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}));
      await wait(() => aliases.textContent.includes('Outside saved'));
      clickNamed('删除别名 Outside saved');
      await wait(() => !aliases.textContent.includes('Outside saved'));
      clickNamed('新增别名');
      aliasInput = await wait(() => aliases.querySelector('input'));
      press(aliasInput, 'Enter');
      await wait(() => !aliases.querySelector('input') && !aliases.querySelector('[aria-label^="编辑"]'));

      const sources = await toggleList('来源');
      const openDialog = async label => {
        clickNamed('新增' + label);
        return wait(() => document.querySelector('main [role="dialog"]'));
      };
      let dialog = await openDialog('来源');
      dialog.parentElement.click();
      if (!dialog.isConnected || !Array.from(document.querySelector('main').children).some(el => el.inert)) {
        throw Error('Main-panel dialog closed on backdrop or left main-panel controls interactive');
      }
      setField(dialog.querySelector('input'), 'Page 1');
      buttonByText(dialog, '保存').click();
      await wait(() => dialog.querySelector('[role="alert"]')?.textContent.includes('请选择资料'));
      setField(dialog.querySelector('select'), 'material');
      buttonByText(dialog, '保存').click();
      await wait(() => sources.textContent.includes('Test source'));
      const termForm = termField.form;
      buttonByText(termForm, '创建术语').click();
      await wait(() => termForm.parentElement.querySelector('[role="status"]')?.textContent.includes('定位和原文摘录'));
      dialog = await openDialog('来源');
      buttonByText(dialog, '取消').click();
      await wait(() => !dialog.isConnected && sources.querySelectorAll('[aria-label^="编辑"]').length === 1);
      const sourceEdit = sources.querySelector('[aria-label^="编辑"]');
      sourceEdit.click();
      dialog = await wait(() => document.querySelector('main [role="dialog"]'));
      setField(dialog.querySelector('select'), '');
      setField(dialog.querySelector('input'), '');
      buttonByText(dialog, '保存').click();
      await wait(() => !sources.textContent.includes('Test source'));
      const relations = await toggleList('关联术语');
      dialog = await openDialog('关联术语');
      setField(dialog.querySelector('select'), 'related-term');
      buttonByText(dialog, '保存').click();
      await wait(() => relations.textContent.includes('Related term'));
      clickNamed('编辑关联术语 Related term');
      dialog = await wait(() => document.querySelector('main [role="dialog"]'));
      setField(dialog.querySelector('select'), '');
      press(dialog.querySelector('select'), 'Escape');
      await wait(() => !dialog.isConnected && relations.textContent.includes('Related term'));
      clickNamed('删除关联术语 Related term');
      await wait(() => !relations.textContent.includes('Related term'));
      dialog = await openDialog('关联术语');
      navigation.querySelector('button[aria-label="资料"]').click();
      await wait(() => !dialog.isConnected && !document.querySelector('main > [inert]'));

      const filePicker = document.querySelector('[aria-label="打开 HTML"]');
      if (!filePicker?.textContent.includes("未选择文件")) throw Error("HTML picker must display its empty file name");
      const pdfPicker = document.querySelector('[aria-label="导入 PDF"]');
      if (!pdfPicker?.textContent.includes('选择文件') || !pdfPicker.textContent.includes('未选择文件')) {
        throw Error('PDF import must use the file chooser row');
      }
      pdfPicker.querySelector('button').click();
      await wait(() => pdfPicker.querySelector('[title="chooser-fixture.pdf"]'));
      const checkbox = Array.from(document.querySelectorAll("label")).find(label => label.textContent.includes("使用浏览器渲染公开动态页"));
      checkbox.click();
      if (!checkbox.querySelector("input").checked) throw Error("Checkbox label did not toggle");
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
      navigation.querySelector('button[aria-label="百科"]').click();
      (await wait(() => Array.from(main.querySelectorAll('button')).find(button => button.textContent === '创建'))).click();
      (await wait(() => document.querySelector('#hpedia-create [value=question]'))).click();
      const question = await wait(() => document.querySelector('form.hpedia-form'));
      const termPicker = question.querySelector('.hpedia-reference-picker select');
      if (getComputedStyle(termPicker).appearance !== 'base-select') throw Error('Question reference is not a fixed single-select');
      const fields = question.querySelectorAll('textarea');
      setField(fields[0], 'Library smoke question');
      setField(fields[1], 'Library smoke answer');
      setField(termPicker, 'related-term');
      (await wait(() => Array.from(question.querySelectorAll('button')).find(button => button.textContent === '引用到题干' && !button.disabled))).click();
      await wait(() => fields[0].value.includes('[[term:related-term]]'));
      question.requestSubmit();
      const review = await wait(() => main.querySelector('[aria-label="百科问题变更确认"]'));
      if (!review.textContent.includes('Library smoke answer')) throw Error('Question review payload did not reach the confirmation card');
      Array.from(review.querySelectorAll('button')).find(button => button.textContent === '确认').click();
      await wait(() => review.textContent.includes('已确认') && !review.querySelector('button'));
      navigation.querySelector('button[aria-label="白板"]').click();
      await wait(() => main.innerText.includes('前沿'));
      (await wait(() => navigation.querySelector('.sessions button'))).click();
      const reportsSummary = await wait(() => document.querySelector('.messages details summary'));
      if (reportsSummary.getBoundingClientRect().width < reportsSummary.parentElement.clientWidth - 1 ||
          getComputedStyle(reportsSummary).borderTopLeftRadius === '0px' ||
          getComputedStyle(reportsSummary, '::after').content === 'none') {
        throw Error('The report launcher needs a full-row rounded disclosure with an end arrow');
      }
      reportsSummary.click();
      if (!reportsSummary.parentElement.open) throw Error('The report disclosure did not open');
      const synthesis = Array.from(reportsSummary.parentElement.querySelectorAll('summary'))
        .find(summary => summary.textContent === '综合多份资料');
      synthesis.click();
      const expand = synthesis.parentElement.querySelector('input[type="checkbox"]');
      const checked = expand.checked;
      expand.closest('label').querySelector('span').click();
      if (checked === expand.checked) throw Error('The report checkbox label did not toggle its value');
      const report = await wait(() => Array.from(document.querySelectorAll('.messages button'))
        .find(button => button.textContent.includes('打开报道')));
      report.click();
      await wait(() => document.querySelector('[aria-label="工作区详情"] article')?.textContent.includes('Report body'));
      const score = await wait(() => document.querySelector('article[aria-label="报道正文"] select[name="score"]'));
      setField(score, '4');
      if (!score.checkValidity() || new FormData(score.form).get('score') !== '4') {
        throw Error('The report rating did not preserve select validity and form submission');
      }
      const page = score.closest('article');
      const handoff = Array.from(page.querySelectorAll('summary'))
        .find(summary => summary.textContent === '交给其他流程的反馈');
      handoff.click();
      await wait(() => page.querySelector('textarea[name="note"]')?.getBoundingClientRect().height > 0);
      if (!main.contains(report)) throw Error('The report launcher did not stay in the main panel');
      return 'Desktop smoke passed: dictionary input sizing, alias and source editing, related Terms, modal cancellation, Library navigation, question review, caption overlay, HTML snapshot, PDF rendering, report disclosure and form controls, panel resize and close';
    })()`);
    const renderer = script => win.webContents.executeJavaScript(script);
    await renderer("document.querySelector('button[aria-label=白板]').click()");
    const boardType = ".bulletin-panel select[aria-label=类型]";
    const currentType = () => renderer(`document.querySelector('${boardType}').value`);
    const chooseType = value => renderer(`(() => {
      const select = document.querySelector('${boardType}');
      select.value = ${JSON.stringify(value)};
      select.dispatchEvent(new Event('change', {bubbles:true}));
    })()`);
    if (await currentType() !== 'frontier') throw Error('Board must start on frontiers');
    for (const kind of ['term', 'question', 'frontier']) {
      await chooseType(kind);
      await renderer("document.querySelector('.bulletin-panel [data-result]').click()");
      const marker={term:'Related term',question:'Prompt marker',frontier:'following'}[kind];
      await renderer(`(async()=>{
        for(let n=0;n<100;n++){
          const body=document.querySelector('.detail-body');
          if(body && (body.textContent.includes(${JSON.stringify(marker)}) ||
              [...body.querySelectorAll('input,textarea')].some(el=>el.value===${JSON.stringify(marker)})))return;
          await new Promise(r=>setTimeout(r,25));
        }
        throw Error('Missing original board detail '+${JSON.stringify(kind)});
      })()`);
      await renderer("document.querySelector('[aria-label=关闭详情]').click()");
    }
    await chooseType('term');
    await renderer("document.querySelector('button[aria-label=百科]').click()");
    await renderer("document.querySelector('button[aria-label=白板]').click()");
    if (await currentType() !== 'term') throw Error('Board lost type during navigation');
    const realNow = Date.now;
    try {
      for (const duration of [3599999, 3600000]) {
        let now = realNow();
        Date.now = () => now;
        const minimized = new Promise(resolve => win.once('minimize', resolve));
        win.minimize();
        await minimized;
        now += duration;
        const restored = new Promise(resolve => win.once('restore', resolve));
        win.restore();
        await restored;
        if (await currentType() !== (duration < 3600000 ? 'term' : 'frontier')) {
          throw Error('Incorrect board type after native restore at ' + duration + 'ms');
        }
      }
    } finally {
      Date.now = realNow;
    }
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
          const fileName = document.querySelector('[aria-label="打开 HTML"] span');
          if (fileName?.title !== 'research-view.html' || fileName.textContent !== fileName.title) {
            throw Error('Chosen HTML name is missing from the file picker');
          }
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      throw Error('Saved HTML did not open in the detail panel');
    })()`);
    const mouseClick = async selector => {
      const bounds = await win.webContents.executeJavaScript(`(() => {
        const element = document.querySelector(${JSON.stringify(selector)});
        if (!element) throw new Error('Missing mouse target: ' + ${JSON.stringify(selector)});
        element.scrollIntoView({block:'nearest'});
        const bounds = element.getBoundingClientRect();
        return {x: Math.round(bounds.left + bounds.width / 2), y: Math.round(bounds.top + bounds.height / 2)};
      })()`);
      win.webContents.sendInputEvent({type:'mouseMove', ...bounds});
      win.webContents.sendInputEvent({type:'mouseDown', button:'left', clickCount:1, ...bounds});
      win.webContents.sendInputEvent({type:'mouseUp', button:'left', clickCount:1, ...bounds});
      await new Promise(resolve => setTimeout(resolve, 80));
    };
    win.show();
    win.focus();
    win.webContents.focus();
    await mouseClick('#workspace-navigation button[aria-label="百科"]');
    await mouseClick('.hpedia-panel .dictionary-heading button');
    await mouseClick('#hpedia-create [value=question]');
    const termPicker = '.hpedia-reference-picker select';
    await mouseClick(termPicker);
    if (!await win.webContents.executeJavaScript(`document.querySelector('${termPicker}').matches(':open')`)) {
      throw Error('Mouse did not open the question reference picker');
    }
    const optionPosition = await win.webContents.executeJavaScript(`(() => {
      const select = document.querySelector('${termPicker}');
      const selected = select.selectedOptions[0];
      const marker = getComputedStyle(selected, '::checkmark');
      if (marker.display === 'none' || marker.order !== '1') {
        throw Error('The selected option does not have a trailing checkmark');
      }
      const rect = select.options[1].getBoundingClientRect();
      return {x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2)};
    })()`);
    win.webContents.sendInputEvent({type:'mouseMove', ...optionPosition});
    await new Promise(resolve => setTimeout(resolve, 80));
    if (!await win.webContents.executeJavaScript(`(() => {
      const option = document.querySelector('${termPicker}').options[1];
      return option.matches(':hover') && getComputedStyle(option).backgroundColor === 'rgb(55, 55, 66)';
    })()`)) throw Error('The hovered option did not highlight');
    win.webContents.sendInputEvent({type:'keyDown', keyCode:'Escape'});
    win.webContents.sendInputEvent({type:'keyUp', keyCode:'Escape'});
    await new Promise(resolve => setTimeout(resolve, 80));
    if (!await win.webContents.executeJavaScript(`document.querySelector('${termPicker}').matches(':not(:open)') &&
        document.querySelector('${termPicker}').value === ''`)) {
      throw Error('Escape did not close the picker while keeping the selection');
    }
    await mouseClick('#workspace-navigation button[aria-label="百科"]');
    await renderer("const type = document.querySelector('select[aria-label=条目类型]'); type.value='term'; type.dispatchEvent(new Event('change',{bubbles:true}))");
    await mouseClick('.dictionary-heading button');
    await mouseClick('#hpedia-create [value=term]');
    const sourceToggle = await win.webContents.executeJavaScript(`Array.from(document.querySelectorAll('button[aria-expanded][aria-controls]'))
      .find(button => button.firstElementChild?.textContent === '来源')?.getAttribute('aria-controls')`);
    if (!sourceToggle) throw Error('Native mouse did not open the Term draft');
    await mouseClick('button[aria-controls="' + sourceToggle + '"]');
    await mouseClick('button[aria-label="新增来源"]');
    if (!await win.webContents.executeJavaScript(`!!document.querySelector('main [role="dialog"]')`)) {
      throw Error('Native mouse did not open the source dialog');
    }
    win.webContents.sendInputEvent({type:'keyDown', keyCode:'Escape'});
    win.webContents.sendInputEvent({type:'keyUp', keyCode:'Escape'});
    await new Promise(resolve => setTimeout(resolve, 80));
    if (await win.webContents.executeJavaScript(`!!document.querySelector('main [role="dialog"]')`)) {
      throw Error('Native keyboard Escape did not dismiss the source dialog');
    }
    if (process.env.HORIZON_SMOKE_SCREENSHOT) {
      fs.writeFileSync(process.env.HORIZON_SMOKE_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
    }
    if (!registeredPdf) throw Error('PDF chooser did not send the selected PDF through the bridge');
    finish(result + '; PDF picker and registration, saved HTML formatting, all board detail links and native minimize/restore threshold', 0);
  } catch (error) {
    finish(String(error), 1);
  }
}));

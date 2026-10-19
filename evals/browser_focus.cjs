const {chromium}=require(process.env.BLOG_REVIEW_PLAYWRIGHT || 'playwright');
const fs=require('fs'),path=require('path'),cp=require('child_process');
(async()=>{
 const root=fs.mkdtempSync('/tmp/blog-review-focus-'),draft=path.join(root,'draft.md'),session=path.join(root,'session');
 const source='# Focus test\n\nFirst paragraph has **important** words.\n\nSecond paragraph needs a note.\n\n## A new section\n\nThird paragraph closes the story.\n\n- A list item\n- Another item\n';fs.writeFileSync(draft,source);
 const server=cp.spawn('python3',[path.resolve(__dirname,'../scripts/review.py'),'serve',draft,'--output',session],{stdio:['pipe','pipe','pipe']});
 const url=await new Promise((resolve,reject)=>{let output='';server.stdout.on('data',d=>{output+=d;const m=output.match(/Review URL: (\S+)/);if(m)resolve(m[1]);});server.on('exit',c=>reject(Error('server '+c)));});
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const assert=(v,s)=>{if(!v)throw Error(s)},data=()=>JSON.parse(fs.readFileSync(session+'/state.json','utf8')),saved=()=>page.getByText('All changes saved',{exact:true}).waitFor();
 const visible=()=>page.locator('.block:visible');
 async function next(){await page.getByRole('button',{name:'Next',exact:true}).click();await page.locator('#focus-next:not([disabled]),#focus-next[disabled]').waitFor();await page.waitForFunction(()=>!focusMoving);}
 try{
 await page.goto(url);await page.locator('#app').waitFor({state:'visible'});
 const current=()=>page.locator('.block.is-current');
 await page.getByRole('button',{name:'Paragraph focus',exact:true}).click();
 assert(await visible().count()===6,'Focus removed page context');assert(await current().count()===1,'More than one focused passage');assert(await page.locator('.is-unfocused').count()===5,'Other passages are not blurred');
 assert(await page.locator('.prose[contenteditable=true]').count()===1,'Blurred paragraphs are editable before focus');
 assert(await page.locator('.is-unfocused .prose').first().evaluate(el=>getComputedStyle(el).filter)==='blur(3px)','Blur not applied');
 const target=page.locator('.block').filter({hasText:'Third paragraph'});await target.scrollIntoViewIfNeeded();const scroll=await page.evaluate(()=>scrollY);await target.click();await page.waitForFunction(()=>!focusMoving);
 assert(await current().textContent().then(t=>t.includes('Third paragraph')),'Click failed to focus arbitrary passage');assert(Math.abs(await page.evaluate(()=>scrollY)-scroll)<3,'Click focus jumped page position');
 await page.keyboard.press('End');await page.keyboard.insertText(' Added correction.');
 await current().getByRole('button',{name:'Add note to passage'}).click();await page.locator('.annotation-note').fill('Keep this closing concise.');
 await next();assert(Object.values(data().edits).some(t=>t.includes('Added correction.')),'Next did not save edit');assert(data().comments[0].note==='Keep this closing concise.','Next did not save note');assert(await page.locator('.comment').count()===0,'Unrelated notes visible');
 await page.getByRole('button',{name:'Previous',exact:true}).click();await page.waitForFunction(()=>!focusMoving);assert(await page.locator('.comment').count()===1,'Previous did not restore current notes');
 // Jump to an arbitrary blurred passage with keyboard activation.
 const keyboardTarget=page.locator('.block.is-unfocused').filter({hasText:'Second paragraph'});await keyboardTarget.focus();await page.keyboard.press('Enter');await page.waitForFunction(()=>!focusMoving);assert(await current().textContent().then(t=>t.includes('Second paragraph')),'Keyboard focus activation failed');
 await page.reload();await page.locator('#app').waitFor({state:'visible'});assert(await current().textContent().then(t=>t.includes('Second paragraph')),'Reload forgot active paragraph');assert(await visible().count()===6,'Reload lost page context');
 // Clicking away must not drop text or abandon a failed save.
 await page.route('**/api/save',route=>route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'Test save failure'})}));
 await current().locator('.prose').click();await page.keyboard.press('End');await page.keyboard.insertText(' Unsaved correction.');
 await page.locator('.block.is-unfocused').filter({hasText:'First paragraph'}).click();await page.locator('#save-status.error').waitFor({state:'visible'});await page.waitForFunction(()=>!focusMoving);
 assert(await current().textContent().then(t=>t.includes('Second paragraph')&&t.includes('Unsaved correction.')),'Failed save changed focus or lost text');
 await page.unroute('**/api/save');await page.locator('.block.is-unfocused').filter({hasText:'First paragraph'}).click();await page.waitForFunction(()=>!focusMoving);assert(await current().textContent().then(t=>t.includes('First paragraph')),'Click retry did not focus target');
 await page.getByRole('button',{name:'Full page',exact:true}).click();assert(await page.locator('.is-unfocused').count()===0,'Full page still blurred');assert(await page.locator('.prose[contenteditable=true]').count()===6,'Full page did not restore editing');assert(await page.locator('.comment').count()===1,'Full page lost notes');
 await page.getByRole('button',{name:'Paragraph focus',exact:true}).click();
 await page.screenshot({path:path.join(root,'focus-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile overflow');await page.locator('.block.is-unfocused').filter({hasText:'Third paragraph'}).click();await page.waitForFunction(()=>!focusMoving);assert(await current().textContent().then(t=>t.includes('Third paragraph')),'Mobile click focus failed');await page.screenshot({path:path.join(root,'focus-mobile.png'),fullPage:true});
 await page.getByRole('button',{name:'Submit review',exact:true}).click();await page.locator('#banner').waitFor({state:'visible'});assert(data().status==='submitted','Submission failed');assert(await page.locator('.prose[contenteditable=true]').count()===0,'Submitted passage editable');assert(fs.readFileSync(draft,'utf8')===source,'Source changed during review');
 assert(!errors.length,errors.join('\n'));console.log(JSON.stringify({passed:true,checks:['entire page retained','one clear passage','click arbitrary blurred passage','caret editing','no scroll jump','save before navigation','focused notes','Previous and Next','keyboard activation','reload','failed-save stays focused','retry click','full page clears blur','mobile click','submission']}));
 }finally{await browser.close();server.kill('SIGINT');}
})().catch(e=>{console.error(e);process.exit(1)});

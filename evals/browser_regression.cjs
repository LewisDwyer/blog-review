const {chromium}=require(process.env.BLOG_REVIEW_PLAYWRIGHT || 'playwright');
const fs=require('fs'),path=require('path'),cp=require('child_process');
(async()=>{
 const root=fs.mkdtempSync('/tmp/blog-review-regression-'),source=path.join(root,'draft.mdx'),session=path.join(root,'session');
 fs.writeFileSync(source,'# Checks\n\nSame word; same word.\n\n3. Alpha\n4. Beta\n\n- Parent\n  - Child\n- Next\n\n- First parent\n  continued text\n\n+ Uno\n+ Dos\n\nRead [the `API`](https://example.com/a_(b)) today.\n');
 const server=cp.spawn('python3',[path.resolve(__dirname,'../scripts/review.py'),'serve',source,'--output',session],{stdio:['pipe','pipe','pipe']});
 const url=await new Promise((resolve,reject)=>{let output='';server.stdout.on('data',d=>{output+=d;const match=output.match(/Review URL: (\S+)/);if(match)resolve(match[1]);});server.on('exit',code=>reject(Error('server exited '+code)));setTimeout(()=>reject(Error('server startup timed out')),10000).unref();});
 const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1400,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const assert=(ok,msg)=>{if(!ok)throw Error(msg)};
 const state=()=>JSON.parse(fs.readFileSync(session+'/state.json','utf8'));
 const saved=()=>page.getByText('All changes saved',{exact:true}).waitFor();
 async function select(prose,quote,last=false){await prose.evaluate((el,{quote,last})=>{el.focus();const at=last?el.textContent.lastIndexOf(quote):el.textContent.indexOf(quote);if(at<0)throw Error('No quote '+quote);const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);let n,pos=0,a,b;while(n=walker.nextNode()){if(!a&&at<pos+n.length)a=[n,at-pos];if(at+quote.length<=pos+n.length){b=[n,at+quote.length-pos];break;}pos+=n.length;}const r=document.createRange();r.setStart(...a);r.setEnd(...b);getSelection().removeAllRanges();getSelection().addRange(r);},{quote,last});await page.locator('#mark-tools').waitFor({state:'visible'});}
 try{
 await page.goto(url);await page.locator('#app').waitFor({state:'visible'});
 let repeated=page.locator('.prose').filter({hasText:'Same word;'});
 await select(repeated,'word',true);await page.getByRole('button',{name:'Cross out',exact:true}).click();
 await repeated.evaluate(el=>{el.focus();const r=document.createRange();r.selectNodeContents(el.querySelector('p'));r.collapse(true);getSelection().removeAllRanges();getSelection().addRange(r);});await page.keyboard.insertText('Prefix ');await saved();
 let a=state().comments[0].anchors[0];assert(a.current_start===23&&a.current_quote==='word','Repeated quote mark shifted to wrong occurrence');
 await select(repeated,'word',true);await page.keyboard.insertText('term');await saved();
 a=state().comments[0].anchors[0];assert(a.quote==='word'&&a.current_quote==='term','Inside-mark edit lost original/reviewed quote');
 await select(repeated,'term');await page.keyboard.press('Backspace');await saved();
 assert(state().comments[0].anchors[0].removed_in_review,'Deleting marked words did not mark them as removed');
 await page.keyboard.press('ControlOrMeta+z');await saved();
 assert(!state().comments[0].anchors[0].removed_in_review&&state().comments[0].anchors[0].current_quote==='term','Undo did not restore marked text');
 await page.reload();await page.locator('#app').waitFor({state:'visible'});
 const offset=await page.evaluate(()=>{const p=[...document.querySelectorAll('.prose')].find(p=>p.textContent.includes('Prefix Same'));const range=[...CSS.highlights.get('review-delete')][0];const before=document.createRange();before.selectNodeContents(p);before.setEnd(range.startContainer,range.startOffset);return before.toString().length;});assert(offset===23,'Reload moved repeated quote mark');
 const ordered=page.locator('.prose').filter({hasText:'Alpha'});await select(ordered,'Alpha');await page.keyboard.insertText('Alpha edited');await saved();assert(Object.values(state().edits).some(x=>x==='3. Alpha edited\n4. Beta\n'),'Ordered markers reset');
 const nested=page.locator('.prose').filter({hasText:'- Parent'});assert(await nested.locator('pre.source-block').count()===1,'Nested list not safely editable as source');await select(nested,'Parent');await page.keyboard.insertText('Parent edited');await saved();assert(Object.values(state().edits).some(x=>x==='- Parent edited\n  - Child\n- Next\n'),'Nested list flattened');
 const continued=page.locator('.prose').filter({hasText:'continued text'});await select(continued,'First parent');await page.keyboard.insertText('Changed parent');await saved();assert(Object.values(state().edits).some(x=>x==='- Changed parent\n  continued text\n'),'Continued list flattened');
 const bullets=page.locator('.prose').filter({hasText:'Uno'});await select(bullets,'Uno');await page.keyboard.insertText('One');await saved();assert(Object.values(state().edits).some(x=>x==='+ One\n+ Dos\n'),'Bullet marker changed');
 const link=page.locator('.prose').filter({hasText:'Read the API'});assert(!await link.textContent().then(x=>x.includes('\u0000')),'Nested token visible');assert(await link.locator('.link-text').getAttribute('title')==='https://example.com/a_(b)','Balanced destination truncated');await select(link,'today');await page.keyboard.insertText('now');await saved();assert(Object.values(state().edits).some(x=>x==='Read [the `API`](https://example.com/a_(b)) now.\n'),'Inline code/link target corrupted');
 // Lose one successful save response: client must reconcile instead of claiming a stale tab.
 let lost=false;await page.route('**/api/save',async route=>{if(!lost){lost=true;await route.fetch();await route.abort('failed');}else await route.continue();});
 await page.locator('#notes').fill('Save despite a lost response.');await saved();assert(await page.locator('#save-status').textContent()==='All changes saved','Lost-response recovery failed');await page.unroute('**/api/save');
 // A genuinely stale tab must still be rejected.
 const other=await browser.newPage();await other.goto(url);await other.locator('#app').waitFor({state:'visible'});
 await page.locator('#notes').fill('Newer notes in first tab.');await saved();await other.locator('#notes').fill('Stale replacement.');await other.locator('#save-status.error').waitFor({state:'visible'});assert(state().notes==='Newer notes in first tab.','Stale tab overwrote saved data');await other.close();
 await page.getByRole('button',{name:'Submit review',exact:true}).click();await page.locator('#banner').waitFor({state:'visible'});assert(fs.existsSync(session+'/feedback.json'),'Submission missing');
 await page.screenshot({path:path.join(root,'regression.png'),fullPage:true});assert(!errors.length,errors.join('\n'));
 console.log(JSON.stringify({passed:true,fixture:root,checks:['repeated-quote insertion','editing inside marks','deleting and undoing marked text','reloaded mark position','ordered markers','nested list fidelity','continued list fidelity','bullet marker fidelity','code inside balanced links','lost save response recovery','stale tab protection','submit']}));
 }finally{await browser.close();server.kill('SIGINT');}
})().catch(e=>{console.error(e);process.exit(1)});

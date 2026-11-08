import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
const before=process.env.BASELINE_URL,after=process.env.BASE_URL;
assert.ok(before&&after,'显式指定两个隔离实例 BASELINE_URL / BASE_URL');
const out='artifacts/wb6/compare';await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:{})});
const result={};
try {
 for(const [version,base] of Object.entries({before,after})) {
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();
  await page.goto(base+'/login');await page.locator('details summary').click();await page.locator('select[name=mode]').selectOption('supervisor');await page.locator('[name=password]').fill('SupervisorDemo!2026');await Promise.all([page.waitForURL(base+'/'),page.getByRole('button',{name:'进入管理后台'}).click()]);
  const csrf=await page.locator('[name=csrf]').first().inputValue(),name='WB6-STEPS-'+version+'-'+Date.now();
  const r=await context.request.post(base+'/action/import',{headers:{Accept:'application/json'},form:{csrf,request_id:crypto.randomUUID(),return:'/dispatch',csv:`external_id,product_code,center_code,quantity,status,source\n${name},P001,WC01,100,open,WB6 steps isolated`}});assert.ok(r.ok(),await r.text());
  await page.goto(base+'/dispatch?q='+name);
  const requests=[];page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/action/')||r.isNavigationRequest())requests.push(r.method()+' '+new URL(r.url()).pathname)});
  let steps=0;const step=async fn=>{steps++;await fn()};
  const submit=async b=>{await Promise.all([page.waitForNavigation({waitUntil:'networkidle'}),b.click()])};
  if(version==='before') {
   for(const [employee,quantity] of [['E001','20'],['E002','30']]) {
    await step(()=>page.locator('#dispatch-order').selectOption({label:`${name} · 100.0000 产品单位`}));
    await step(()=>page.locator('#dispatch-operation').selectOption({label:'10 · 精密切削'}));
    await step(()=>page.locator('[name=quantity]').fill(quantity));
    const member=await page.locator('select[name=members] option').filter({hasText:employee}).getAttribute('value');await step(()=>page.locator('select[name=members]').selectOption(member));
    if(employee==='E001')await page.screenshot({path:`${out}/before-dispatch.png`});
    await step(()=>submit(page.getByRole('button',{name:'保存派工并冻结标准'})));
   }
  }else{
   const form=page.locator('#bulk-dispatch');await step(()=>form.locator('#bulk-order').selectOption({label:`${name} · 100.0000 产品单位`}));await step(()=>form.locator('#bulk-operation').selectOption({label:'10 · 精密切削'}));
   for(const [employee,quantity] of [['E001','20'],['E002','30']]) {
    const box=form.locator('.bulk-employee').filter({hasText:employee});await step(()=>box.locator('[name=employees]').check());await step(()=>box.locator('input[type=number]').fill(quantity));
   }
   await page.screenshot({path:`${out}/after-dispatch.png`});await step(()=>submit(form.getByRole('button',{name:'确认预览，整批派工'})));
  }
  result[version]={steps,requests:[...requests],submissions:requests.filter(r=>r.startsWith('POST')).length};
  const worker=JSON.parse(await readFile('artifacts/wb6/new/browser-report.json','utf8')),current=worker.name;
  await page.goto(base+'/login');await page.locator('[name=identity]').fill(worker.identity);await page.locator('[name=identity]').press('Enter');await page.waitForURL('**/terminal');
  await page.setViewportSize({width:390,height:1000});await page.goto(base+'/terminal?q='+current);await page.screenshot({path:`${out}/${version}-terminal.png`,fullPage:true});
  await context.close();
 }
 assert.equal(result.before.steps,10);assert.equal(result.after.steps,7);assert.equal(result.before.requests.length,4);assert.equal(result.after.requests.length,2);
 await writeFile(out+'/steps.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser.close()}

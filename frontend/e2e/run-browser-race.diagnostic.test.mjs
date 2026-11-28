import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import {readFileSync} from 'node:fs'
import {EventEmitter} from 'node:events'
import {setTimeout as sleep} from 'node:timers/promises'
import {resolve} from 'node:path'
const root=resolve(new URL('../..', import.meta.url).pathname)
const harness=readFileSync(root+'/frontend/e2e/sessionRaceHarness.mjs','utf8')
const runner=readFileSync(root+'/frontend/e2e/run-browser-race.mjs','utf8').replace(/^import .*\n/gm,'')
const codes=[...harness.matchAll(/checkpoint = '(C42_[A-Z_]+)'/g)].map(x=>x[1])
const marker=(code,result='FAIL',text=result+': SECRET_SENTINEL')=>({tagName:'PRE',dataset:{result,...(code===undefined?{}:{checkpoint:code})},textContent:text})
async function execute(markers,mode='normal',cleanupFailures=[],rmOutcomes=[]) {
 const boom=()=>{throw new Error('SECRET_SENTINEL')}
 const breakCleanup=name=>{if(cleanupFailures.includes(name))boom()}
 const browser=new EventEmitter(); browser.exitCode=null;browser.signalCode=null
 const output=new EventEmitter();output.setEncoding=()=>{};output.destroy=()=>{breakCleanup('pipes-output')}
 const input=new EventEmitter();input.destroy=()=>{breakCleanup('pipes')}
 const logs=[],errors=[];let cleaned=0,closed=0,evals=0
 // Models Node's documented retry contract only; no real filesystem retries.
 const rmAttempts=[];const retryWaits=[]
 const fakeRm=async(path,options)=>{
  cleaned++
  assert.equal(path,'/synthetic/profile')
  assert.equal(JSON.stringify(options),JSON.stringify({recursive:true,force:true,maxRetries:3,retryDelay:100}))
  breakCleanup('profile')
  for(let attempt=0;;attempt++){
   rmAttempts.push(attempt)
   const error=rmOutcomes[attempt]
   if(!error)return
   const code=Object.getOwnPropertyDescriptor(error,'code')?.value
   if(['EBUSY','EMFILE','ENFILE','ENOTEMPTY','EPERM'].includes(code)&&attempt<options.maxRetries){retryWaits.push((attempt+1)*options.retryDelay);continue}
   throw error
  }
 }
 const document={querySelectorAll:()=>markers}
 input.write=frame=>{
  if(mode==='timeout')return
  if(mode==='write')boom()
  const m=JSON.parse(frame.slice(0,-1))
  queueMicrotask(()=>{
   if(mode==='spawn-event'){browser.emit('error',new Error('SECRET_SENTINEL'));return}
   if(mode==='exit'){browser.emit('exit');return}
   if(mode==='null-frame'){output.emit('data','null\0');return}
   if(mode==='cdp'){output.emit('data',JSON.stringify({id:m.id,error:{message:'SECRET_SENTINEL'}})+'\0');return}
   if(mode==='pipe'){output.emit('error',new Error('SECRET_SENTINEL'));return}
   if(mode==='invalid-json'){output.emit('data','SECRET_SENTINEL\0');return}
   if(mode==='oversize'){output.emit('data','x'.repeat(1024*1024+1));return}
   let result={}
   if(m.method==='Target.createTarget')result=mode==='malformed-result'?null:{targetId:'synthetic'}
   if(m.method==='Target.attachToTarget')result={sessionId:'synthetic'}
   if(m.method==='Page.navigate' && mode==='navigation') result={errorText:'SECRET_SENTINEL'}
   if(m.method==='Page.navigate' && ['egress','interception'].includes(mode)){
    output.emit('data',JSON.stringify({method:'Fetch.requestPaused',sessionId:'synthetic',params:{requestId:'synthetic',request:{url:mode==='egress'?'https://invalid.example/SECRET_SENTINEL':'http://127.0.0.1:34567/fixed'}}})+'\0'); return
   }
   if(m.method==='Fetch.continueRequest'){output.emit('data',JSON.stringify({id:m.id,error:{message:'SECRET_SENTINEL'}})+'\0');return}
   if(m.method==='Runtime.evaluate') {
    evals++
    if(mode==='pending'&&evals===1)result={result:{value:'PENDING'}}
    else if(mode==='exception')result={exceptionDetails:{text:'SECRET_SENTINEL'}}
    else if(mode==='hostile-transport')result={result:{value:'FAIL:SECRET_SENTINEL'}}
    else result={result:{value:vm.runInNewContext(m.params.expression,{document})}}
   }
   output.emit('data',JSON.stringify({id:m.id,result})+'\0')
  })
 }
 browser.stdio=[null,null,null,input,output]
 browser.kill=()=>{breakCleanup('terminate');browser.signalCode='SIGKILL';browser.emit('exit');if(!cleanupFailures.includes('close'))browser.emit('close')}
 const context={process:{env:{},exitCode:undefined,cwd:()=>root+'/frontend'},createServer:async()=>({listen:async()=>{if(mode==='listen')boom()},httpServer:{address:()=>({address:mode==='loopback'?'0.0.0.0':'127.0.0.1',port:34567})},close:async()=>{closed++;breakCleanup('server')}}),react:()=>{},resolve,join:(...x)=>x.join('/'),tmpdir:()=>'/synthetic',mkdtemp:async()=>{if(mode==='setup')boom();return '/synthetic/profile'},rm:fakeRm,spawn:()=>{if(mode==='spawn')boom();return browser},sleep:(ms,value,options)=>sleep(ms===5000&&cleanupFailures.includes('close')?1:ms,value,options),setTimeout:(fn,ms)=>setTimeout(fn,mode==='timeout'?1:ms),clearTimeout,AbortController,URL,console:{log:x=>logs.push(x),error:x=>errors.push(x)}}
 await vm.runInNewContext('(async()=>{'+runner+'})()',context)
 assert.equal(cleaned,mode==='setup'?0:1);assert.equal(closed,mode==='setup'?0:1)
 assert.ok(![...logs,...errors].join('').includes('SECRET_SENTINEL'))
 const diagnostics=errors.slice(1)
 for(const code of diagnostics)assert.match(code,/^C42_(STAGE_(SETUP|LISTEN|LOOPBACK|SPAWN|EARLY_EXIT|PIPE|FRAME_LIMIT|FRAME_PARSE|CDP_COMMAND|INTERCEPTION|EGRESS|NAVIGATION|EVALUATION|MARKER|TIMEOUT|FIXTURE_FAIL|CLEANUP|UNKNOWN)|PROFILE_ERROR_(EBUSY|EMFILE|ENFILE|ENOTEMPTY|EPERM|EACCES|EROFS|EIO|UNKNOWN)|FIXTURE_(NOT_OBSERVED|PENDING|PASS|FAIL|INVALID)|CLEANUP_(TERMINATE|PIPES|CLOSE|SERVER|PROFILE)_(NOT_REQUIRED|OK|FAILED|NOT_COMPLETED))$/)
 return {logs,errors:errors.slice(0,1),diagnostics,exit:context.process.exitCode,rmAttempts,retryWaits}
}
for(const code of codes) test('known '+code,async()=>{
 const r=await execute([marker(code)]);assert.deepEqual(r.errors,[code]);assert.equal(r.logs.length,0);assert.equal(r.exit,1)
})
for(const code of [undefined,'','C42_MOUNT\nSECRET_SENTINEL','<html>SECRET_SENTINEL</html>','https://SECRET_SENTINEL',42,{},'toString'])test('unknown '+JSON.stringify(code),async()=>{
 const r=await execute([marker(code)]);assert.deepEqual(r.errors,['C42_UNKNOWN']);assert.equal(r.exit,1)
})
test('PASS unchanged',async()=>{const r=await execute([marker('SECRET_SENTINEL','PASS')]);assert.equal(r.errors.length,0);assert.equal(r.logs[0],'PASS: headless Chromium account-switch, stale request, revoked URL, logout race');assert.equal(r.exit,undefined)})
test('pending then PASS',async()=>{const r=await execute([marker(undefined,'PASS')],'pending');assert.equal(r.errors.length,0)})
for(const markers of [[marker('C42_MOUNT'),marker('C42_MOUNT')],[{...marker('C42_MOUNT'),tagName:'DIV'}],[marker('C42_MOUNT','FAIL','PASS: SECRET_SENTINEL')],[marker('C42_MOUNT','OTHER')]])test('invalid marker '+JSON.stringify(markers),async()=>{const r=await execute(markers);assert.deepEqual(r.errors,['C42_RUNNER_FAILURE']);assert.equal(r.exit,1)})
for(const mode of ['pipe','invalid-json','oversize','exception','hostile-transport','timeout'])test(mode,async()=>{const r=await execute([marker('C42_MOUNT')],mode);assert.deepEqual(r.errors,['C42_RUNNER_FAILURE']);assert.equal(r.exit,1)})
for(const code of ['C42_MOUNT','SECRET_SENTINEL',undefined])test('fixture producer sanitizes '+code,()=>{
 const appended=[];const context=vm.createContext({setTimeout,document:{createElement:()=>({dataset:{}}),body:{append:x=>appended.push(x)}}})
 vm.runInContext(harness.slice(0,harness.indexOf('async function run()')),context)
 context.value=code;vm.runInContext('checkpoint=value;result(false)',context)
 assert.equal(appended[0].textContent,'FAIL: '+(code==='C42_MOUNT'?code:'C42_UNKNOWN'))
 assert.ok(!JSON.stringify(appended).includes('SECRET_SENTINEL'))
})

test('fixture PASS text preserved',()=>{
 const appended=[];const context=vm.createContext({setTimeout,document:{createElement:()=>({dataset:{}}),body:{append:x=>appended.push(x)}}})
 vm.runInContext(harness.slice(0,harness.indexOf('async function run()')),context)
 vm.runInContext('result(true)',context)
 assert.equal(appended[0].dataset.result,'PASS')
 assert.equal(appended[0].dataset.checkpoint,undefined)
 assert.equal(appended[0].textContent,'PASS: Explicit immutable same-ID recovery and one known-ID queue; denied retry held; unknown creation held without resubmit; cancelled terminal state; bounded polling/resume; Account A->B late submit, project switch, B->C MP4 URL revocation, C logout in-flight')
})
test('actual catch never serializes exception',async()=>{
 const appended=[];const context=vm.createContext({setTimeout,document:{createElement:()=>({dataset:{}}),body:{append:x=>appended.push(x)}}})
 await vm.runInContext(harness.slice(0,harness.indexOf('async function run()'))+"async function run(){throw new Error('SECRET_SENTINEL')}\n"+harness.slice(harness.indexOf('run().catch')),context)
 assert.equal(appended[0].textContent,'FAIL: C42_UNKNOWN')
})

const stageCases={setup:'SETUP',listen:'LISTEN',loopback:'LOOPBACK',spawn:'SPAWN','spawn-event':'SPAWN',exit:'EARLY_EXIT',write:'PIPE',pipe:'PIPE','invalid-json':'FRAME_PARSE','null-frame':'FRAME_PARSE',oversize:'FRAME_LIMIT',cdp:'CDP_COMMAND',navigation:'NAVIGATION',exception:'EVALUATION','hostile-transport':'MARKER',timeout:'TIMEOUT',egress:'EGRESS',interception:'INTERCEPTION'}
for(const [mode,stage] of Object.entries(stageCases))test('actual branch '+mode,async()=>{
 const r=await execute([marker(undefined,'PASS')],mode)
 assert.equal(r.exit,1);assert.equal(r.logs.length,0)
 assert.equal(r.diagnostics[0],'C42_STAGE_'+stage)
 assert.equal(r.diagnostics[1],'C42_FIXTURE_'+(mode==='hostile-transport'?'INVALID':'NOT_OBSERVED'))
})
for(const failure of ['terminate','pipes','pipes-output','close','server','profile'])for(const verdict of ['PASS','FAIL'])test(verdict+' then cleanup '+failure,async()=>{
 const r=await execute([marker('C42_MOUNT',verdict)],'normal',[failure])
 assert.equal(r.exit,1);assert.equal(r.logs.length,0)
 assert.equal(r.errors[0],verdict==='FAIL'?'C42_MOUNT':'C42_RUNNER_FAILURE')
 assert.equal(r.diagnostics[0],'C42_STAGE_'+(verdict==='FAIL'?'FIXTURE_FAIL':'CLEANUP'))
 assert.equal(r.diagnostics[1],'C42_FIXTURE_'+verdict)
 assert.ok(r.diagnostics.includes('C42_CLEANUP_'+(failure==='pipes-output'?'PIPES':failure.toUpperCase())+'_FAILED'))
 if(['terminate','pipes','pipes-output'].includes(failure))assert.ok(r.diagnostics.includes('C42_CLEANUP_CLOSE_NOT_COMPLETED'))
})
test('multiple cleanup failures preserve first fixture cause',async()=>{
 const r=await execute([marker('C42_FOCUS')],'normal',['terminate','server','profile'])
 assert.equal(r.errors[0],'C42_FOCUS');assert.equal(r.diagnostics[0],'C42_STAGE_FIXTURE_FAIL')
 for(const name of ['TERMINATE','SERVER','PROFILE'])assert.ok(r.diagnostics.includes('C42_CLEANUP_'+name+'_FAILED'))
})
test('early failure with cleanup failures stays primary',async()=>{
 const r=await execute([],'listen',['server','profile'])
 assert.equal(r.diagnostics[0],'C42_STAGE_LISTEN');assert.equal(r.diagnostics[1],'C42_FIXTURE_NOT_OBSERVED')
})

test('unclassified unexpected shape uses fixed unknown without invented observation',async()=>{
 const r=await execute([],'malformed-result')
 assert.equal(r.exit,1);assert.equal(r.logs.length,0)
 assert.equal(r.diagnostics[0],'C42_STAGE_UNKNOWN');assert.equal(r.diagnostics[1],'C42_FIXTURE_NOT_OBSERVED')
})

const removalError=code=>Object.assign(new Error('SECRET_SENTINEL'),{code,path:'SECRET_SENTINEL',syscall:'SECRET_SENTINEL'})
for(const code of ['EBUSY','EMFILE','ENFILE','ENOTEMPTY','EPERM'])for(const verdict of ['PASS','FAIL']){
 test('stubbed transient removal '+code+' fixture '+verdict,async()=>{
  const r=await execute([marker('C42_FOCUS',verdict)],'normal',[],[removalError(code),removalError(code),removalError(code)])
  assert.equal(r.rmAttempts.length,4);assert.deepEqual(r.retryWaits,[100,200,300])
  assert.equal(r.exit,verdict==='PASS'?undefined:1)
  if(verdict==='PASS')assert.equal(r.logs.length,1)
  else {assert.equal(r.logs.length,0);assert.equal(r.errors[0],'C42_FOCUS');assert.equal(r.diagnostics[0],'C42_STAGE_FIXTURE_FAIL')}
  assert.ok(!r.diagnostics.some(x=>x.startsWith('C42_PROFILE_ERROR_')))
 })
 test('stubbed exhausted removal '+code+' fixture '+verdict,async()=>{
  const r=await execute([marker('C42_FOCUS',verdict)],'normal',[],Array(4).fill(removalError(code)))
  assert.equal(r.rmAttempts.length,4);assert.deepEqual(r.retryWaits,[100,200,300]);assert.equal(r.exit,1);assert.equal(r.logs.length,0)
  assert.equal(r.diagnostics[0],'C42_STAGE_'+(verdict==='PASS'?'CLEANUP':'FIXTURE_FAIL'))
  assert.ok(r.diagnostics.includes('C42_PROFILE_ERROR_'+code))
 })
}
for(const code of ['EACCES','EROFS','EIO',undefined,'SECRET_SENTINEL','EPERM\nSECRET_SENTINEL',17,{}])for(const verdict of ['PASS','FAIL'])test('stubbed nonretryable removal '+String(code)+' fixture '+verdict,async()=>{
 const r=await execute([marker('C42_FOCUS',verdict)],'normal',[],[removalError(code)])
 assert.equal(r.rmAttempts.length,1);assert.deepEqual(r.retryWaits,[]);assert.equal(r.exit,1);assert.equal(r.logs.length,0)
 assert.equal(r.diagnostics[0],'C42_STAGE_'+(verdict==='PASS'?'CLEANUP':'FIXTURE_FAIL'))
 assert.ok(r.diagnostics.includes('C42_PROFILE_ERROR_'+(['EACCES','EROFS','EIO'].includes(code)?code:'UNKNOWN')))
})
test('error accessor is not read or printed',async()=>{
 const error=Object.defineProperty(new Error('SECRET_SENTINEL'),'code',{get(){throw new Error('SECRET_SENTINEL')}})
 const r=await execute([marker(undefined,'PASS')],'normal',[],[error])
 assert.equal(r.exit,1);assert.equal(r.logs.length,0);assert.ok(r.diagnostics.includes('C42_PROFILE_ERROR_UNKNOWN'))
})

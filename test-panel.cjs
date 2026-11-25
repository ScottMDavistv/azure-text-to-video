const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')
const script = fs.readFileSync(path.join(__dirname,'panel.js'),'utf8').replace(/^import .*\n/, '')
const secret = 'PRIVATE-TEST-INPUT'
function setup({account = {}, token = async () => ({accessToken: secret}), replies = []} = {}) {
  const nodes = new Map()
  const get = id => {
    if (!nodes.has(id)) nodes.set(id,{value:secret, textContent:'',append(){}})
    return nodes.get(id)
  }
  const calls = []
  vm.runInNewContext(script,{
    getSignedInAccount: async () => account,
    acquireApiToken:token,
    document:{
      createElement: () => ({style:{},querySelector:get,append(){}}),
      body:{prepend(){}},addEventListener(){},querySelector:()=>null,
    },
    fetch: async (url, options) => {
      calls.push({url, options})
      const reply = replies.shift()
      if (!reply) throw Error(secret)
      return reply
    }
  })
  return {get,calls,click: async name => {
    await get('#t90-'+name).onclick()
    const result=get('#t90-result').textContent
    assert.ok(!result.includes(secret))
    return result
  }}
}
const response = (status, body) => ({status,ok:status<300,json:async()=>body})
test('registration distinguishes missing sign-in from token acquisition failure',async()=>{
  assert.match(await setup({account:null}).click('enroll'),/stopped-at=register-find-account http=not-observed$/)
  const p=setup({token:async()=>{throw Error(secret)}})
  assert.match(await p.click('enroll'),/stopped-at=register-get-api-token http=not-observed$/)
  assert.equal(p.calls.length,0)
})
test('registration reports status and redacts body and setup code',async()=>{
  const p=setup({replies:[response(403,secret)]})
  assert.match(await p.click('enroll'),/stopped-at=register-local-api http=403$/)
  assert.equal(p.get('#t90-code').value,'')
  assert.equal(p.calls.length,1)
})
test('missing HTTP response is distinct from an HTTP rejection',async()=>{
  assert.match(await setup().click('enroll'),/stopped-at=register-local-api http=not-observed$/)
  assert.match(await setup({replies:[response(secret,null)]}).click('enroll'),/stopped-at=register-local-api http=other$/)
})
test('video check identifies exact failing request rather than registration',async()=>{
  const p=setup({replies:[response(200,{path:'/local',expected:200}),response(200),response(409,secret)]})
  assert.match(await p.click('check'),/stopped-at=check-video-metadata http=409$/)
  assert.equal(p.calls.length,3)
})
test('expected signed-out denial still passes all requests and sends no bearer',async()=>{
  const denial=response(401,{detail:'Bearer token required'})
  const p=setup({account:null,replies:[response(200,{path:'/local',expected:401,role:'signed-out',detail:'Bearer token required'}),denial,denial,denial,response(200)]})
  assert.match(await p.click('check'),/signed-out: browser and direct API 401/)
  assert.equal(p.calls.length,5)
  assert.ok(p.calls.every(c=>!c.options.headers.Authorization))
})
test('registration and expected member success still work',async()=>{
  const bytes={...response(200),headers:{get:()=> 'private, no-store'},body:{cancel:async()=>{}}}
  const p=setup({replies:[response(200,{role:'member',project:'fixture'}),response(200,{path:'/local',expected:200,role:'member'}),response(200),response(200),bytes,response(200)]})
  assert.match(await p.click('enroll'),/^MEMBER READY/)
  assert.match(await p.click('check'),/member: browser and direct API 200/)
  assert.match(p.get('#t90-result').textContent,/member: browser and direct API 200/)
})

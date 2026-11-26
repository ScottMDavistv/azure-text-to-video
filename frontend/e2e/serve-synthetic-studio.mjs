// Test-only Vite entrypoint. Use the real app and API, replacing only MSAL.
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'

const port = Number(process.argv[2])
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Expected API port')
process.env.VITE_ENABLE_AUTHENTICATED_JOURNEY = 'true'
process.env.VITE_VIDEO_PROJECT_ID = ''
process.env.VITE_VIDEO_API_ORIGIN = ''
const server = await createServer({
  configFile: false,
  plugins: [react()],
  resolve: { alias: [{ find: /^\.\/entraAuth$/, replacement: resolve('e2e/fakeAuth.mjs') }] },
  server: {
    host: '127.0.0.1', port: 0,
    proxy: { '/projects': { target: 'http://127.0.0.1:' + port } },
  },
  logLevel: 'error',
})
await server.listen()
console.log(server.httpServer.address().port)

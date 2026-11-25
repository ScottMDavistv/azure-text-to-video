// Supervised Ticket #90 only. Real MSAL; proxy only the local project API.
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'

const apiPort = Number(process.argv[2])
const spaId = process.argv[3]
if (!Number.isInteger(apiPort) || apiPort < 1024 || apiPort > 65535 ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(spaId ?? '')) {
  throw new Error('Expected the approved local API port and dev SPA')
}
process.env.VITE_ENTRA_SPA_CLIENT_ID = spaId
process.env.VITE_ENABLE_AUTHENTICATED_JOURNEY = 'true'
process.env.VITE_VIDEO_PROJECT_ID = ''
process.env.VITE_VIDEO_API_ORIGIN = ''
const server = await createServer({
  configFile: false,
  envDir: false,
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/projects': { target: 'http://127.0.0.1:' + apiPort } },
  },
  logLevel: 'silent',
})
await server.listen()
console.log('READY')

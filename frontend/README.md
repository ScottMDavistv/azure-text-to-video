# Mock creative-studio workflow (development only)

The React/Vite frontend now contains an offline studio UI, a responsive session dashboard, deterministic 300-second/five-scene storyboard placeholders, job lifecycle simulator, status indicators, and disabled preview/download affordances.

## Local checks

```sh
cd frontend
npm ci
npm test
npm run lint
npm run build
npm run dev
```

The simulation requires no backend, login, FAL credentials, Azure resources, storage or paid calls. Enter 10–2,000 characters and select **Create mock job**; select **Advance simulation** to move from queued through planning, rendering, assembling and completed. A simulated completion **does not** create a video or downloadable artifact; play and download are intentionally disabled. State exists only in browser memory and resets on reload.

Existing Entra local authentication stays separate from the simulation. The production prompt/status/download integration must wait for Design Management acceptance of the backend contract and worker/artifact path, plus authorization decisions for scoped user access. Never replace security validation with mock job data. Do not put access tokens in logs, page markup, environment variables served to the browser or demo state.

Scope: isolated frontend development PR only. No CI deployment, Azure/IAM/finance changes, Copilot dispatch, automatic merge, or paid rendering.

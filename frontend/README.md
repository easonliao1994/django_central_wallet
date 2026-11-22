# Central Wallet frontend

React + TypeScript single-page app (Vite) for the wallet's users. See the main README's
"Frontend" section for how it is deployed.

```
npm install
npm run dev      # http://localhost:5173, API proxied to VITE_API_PROXY (default http://localhost:8000)
npm test         # unit tests (vitest)
npm run lint     # oxlint
npm run build    # type-check and build into dist/
```

- `src/api/`: fetch wrapper (turns the backend's `success: false` bodies into `ApiError`) and endpoints
- `src/auth/`: token storage and the signed-in user
- `src/i18n/`: Traditional Chinese (default) and English messages; every backend error code has a message
- `src/pages/`: one component per route
- `nginx.conf`, `Dockerfile`: production image (static files + API proxy on the same origin)

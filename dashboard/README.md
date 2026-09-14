# ShopAware Dashboard

Next.js dashboard adapted from the UI concepts in `vahapogut/Theft-Detection` for the ShopAware API.

## Development

```bash
cp .env.example .env.local
npm install
npm run dev
```

Default frontend URL: `http://127.0.0.1:3000`

The dashboard expects the ShopAware backend at `NEXT_PUBLIC_API_URL` and the live-frame WebSocket at `NEXT_PUBLIC_WS_URL`.

Current pages:

- Overview — health, cameras, incidents, live previews
- Cameras — add/list/remove RTSP channels with separate credentials
- Incidents — review candidate detections and snapshots
- Settings — current model/runtime configuration status

The dashboard intentionally does not include the upstream face-recognition panel in the MVP.

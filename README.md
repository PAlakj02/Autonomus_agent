# ConnectAI Studio

- `web-ai-link/` — website (TanStack Start). Deploy on Vercel with **Root Directory = `web-ai-link`**.
- `backend/` — FastAPI API: design generation, AI mock papers from uploaded worksheets/past papers, print queue. Needs an always-on host (Render/Railway), not Vercel. Copy `backend/.env.example` to `.env` and fill in keys.

## Demo mode (no backend, no API keys)

Set `VITE_DEMO_MODE=true` in Vercel's environment variables. In the Studio, ask for a house
("Create a house image") to get a sample image, confirm it, then print: "My printer" opens the
device's real print dialog; the school printer is simulated. Set it to `false` once the backend
and Gemini key are live.

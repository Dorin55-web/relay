# Relay — Remotion Promo Video

Proiect video programatic creat cu [Remotion](https://www.remotion.dev/) (React + TypeScript) pentru promovarea aplicației **Relay**.

## 📁 Structură & Scene

- **Scena 1: Intro & Hook (0s - 6s)** — Orbul animat pulsatoriu, titlul cu gradient și insignele de performanță (CUDA, Local, Private).
- **Scena 2: Desktop In-Place F9 Translation (6s - 17s)** — Simularea interfeței IDE (AntiGravity / Cursor), tastarea promptului în română, acționarea tastei F9 și înlocuirea instantanee (<150ms) cu traducerea în limba engleză.
- **Scena 3: Telegram Mobile Remote & Subagents (17s - 29s)** — Machetă de smartphone cu Telegram, notă vocală, card live `👥 Teamwork Mode Detected`, orchestrare subagenți și buton de oprire de urgență `[🛑 Cancel Task]`.
- **Scena 4: Core Features Grid (29s - 35s)** — Cele 6 capabilități cheie organizate în carduri moderne.
- **Scena 5: Outro & Call to Action (35s - 40s)** — Îndemn la acțiune cu link către depozitul GitHub.

## 🚀 Cum rulezi proiectul

### 1. Previzualizare interactivă în browser (Remotion Studio)
```bash
npm start
```
Deschide playerul web interactiv la `http://localhost:3000` unde poți naviga cadru cu cadru, modifica stilurile în timp real sau schimba durata.

### 2. Randare video complet MP4 (1080p, 30fps)
```bash
npm run build
```
Fișierul video final este salvat în `out/relay-promo.mp4`.

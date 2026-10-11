# Raport de Progres și Arhitectură — Relay

Acest document sintetizează arhitectura fixată a proiectului **Relay**, deciziile tehnice adoptate pe parcursul dezvoltării, starea curentă a sistemului și lista detaliată a problemelor critice identificate ce urmează a fi rezolvate.

---

## 1. Prezentare Generală & Scopul Produsului

**Relay** este un asistent AI hibrid care combină două capabilități esențiale pentru dezvoltatori:
1. **Desktop Zero-Friction Voice & In-Place Translation:** Înlocuirea tastării manuale a prompturilor lungi în limba engleză prin dictare vocală în limba română (F9) și traducere instantanee pe loc (<150ms) direct în caseta activă a oricărui editor sau aplicații AI (AntiGravity, Cursor, VSCode, terminale).
2. **Mobile Remote Orchestration via Telegram:** Controlul complet al asistentului AI de pe telefonul mobil când utilizatorul nu se află la birou (note vocale traduse de Whisper pe GPU-ul local, ingestie de albume foto, declanșarea modurilor avansate de subagenți `/team`, `/goal`, `/plan`, monitorizarea în timp real a execuției și buton de oprire de urgență `[🛑 Cancel Task]`).

---

## 2. Arhitectura Fixată a Sistemului

Arhitectura este modulară, decuplată și organizată în jurul următoarelor componente:

```
Relay Core
├── Desktop Audio & Voice
│   ├── audio.py             # Captură audio cu microfon, urmărire dispozitive, segmentare VAD (silence RMS)
│   ├── transcriber.py       # WhisperEngine (faster-whisper large-v3 pe CUDA float16)
│   └── vocabulary.py        # Biasare contextuală pentru termeni tehnici și nume de fișiere
│
├── In-Place & Translation
│   ├── inplace.py           # Clipboard Sentinel non-distructiv pentru detecția și înlocuirea textului la tasta F9
│   ├── translator.py        # TextTranslator (Marian Opus-MT Ro->En ctranslate2 pe CUDA float16)
│   ├── injector.py          # Salvare/restaurare clipboard, injecție tastatură Ctrl+V, suport hibrid text & foto
│   └── uia.py               # Identificare și focalizare precisă prin Windows UI Automation (evită scanarea istorică)
│
├── Interfață Grafică (GUI)
│   ├── overlay.py           # Orb plutitor frameless (PySide6), animație 30 FPS, reacție la volumul vocii
│   ├── orbs/                # 9 stiluri dinamice de particule matematice (portate din thinking-orbs)
│   ├── look_picker.py       # Selector vizual pentru asocierea stilurilor la stările orbului
│   ├── prompts.py           # Șabloane rapide de prompturi structurate (prompts.json)
│   └── prompt_editor.py     # Editor GUI pentru personalizarea prompturilor
│
└── Control la Distanță (Telegram Remote)
    ├── remote.py            # Bot Telegram: coadă autopilot, carduri live, deduplicare răspunsuri, note vocale, foto
    ├── keeper.py            # Watchdog extern de înaltă disponibilitate (supraveghere, repornire automată)
    └── usage.py             # Monitorizare cote active, ferestre de resetare și volum de tokeni
```

---

## 3. Decizii Tehnice Majore Adoptate

1. **Eliminarea ferestrei intermediare „Write instead” (`compose.py`):**
   - *Decizie:* S-a renunțat complet la fereastra intermediară de tastare (403 linii eliminate).
   - *Soluție:* Implementarea componentei **In-Place F9 Translation** (`relay/inplace.py`). Utilizatorul scrie în română direct în caseta activă a oricărui chat, apasă F9, iar textul este tradus și înlocuit în sub 150ms pe GPU fără flash albastru și fără trimitere automată (Enter). Căsuța goală declanșează instantaneu dictarea vocală Whisper.
2. **Focalizare strictă UIA pe controlul activ (`relay/uia.py`):**
   - *Decizie:* Limitarea interogării UIA exclusiv la controlul activ focalizat în momentul respectiv (`GetFocusedElement()`).
   - *Efect:* Eliminarea scanării istoricului ferestrei, împiedicând preluarea eronată de fragmente de cod sau mesaje vechi din conversațiile anterioare.
3. **Puntea Telegram Remote pentru Note Vocale și Albume Foto:**
   - *Decizie:* Utilizatorul poate trimite mesaje vocale Telegram care sunt decodate local pe PC prin `faster_whisper.audio.decode_audio` și traduse în engleză direct prin Whisper.
   - *Decizie foto:* Albumele foto (multiple imagini trimise simultan) sunt grupate după `media_group_id`, descărcate secvențial în `inbox/` și injectate fie ca atașamente de clipboard în GUI, fie ca argumente de cale în terminale.
4. **Declanșarea Modurilor Avansate de Subagenți din Telegram (Soluția A + Soluția B):**
   - *Soluția A (Comenzi directe):* `/team <task>` -> `/teamwork-preview <en_task>`, `/goal <task>` -> `/goal <en_task>`, `/plan <task>` -> `/plan <en_task>`.
   - *Soluția B (Detecție inteligentă de prefixe):* Atât pe mesaje vocale, cât și pe text, prefixe precum `Echipă:`, `Team:`, `Teamwork:`, `Goal:`, `Obiectiv:`, `Plan:` sunt detectate automat, curățate, iar promptul este trimis cu comanda slash corespunzătoare.
   - *Siguranță:* Păstrarea numărătorii inverse de 3 secunde și a butonului interactiv `[🛑 Cancel Task]` înainte de tastare.
5. **Reziliență la Rețea și Conexiuni Instabile:**
   - Adăugarea de retry cu backoff exponențial în `remote.py` și `keeper.py` pentru erori Winsock (`Errno 10054`, `Errno 11001`) și Telegram `HTTP 409 Conflict`.
6. **Curățarea Proiectelor Experimentale Temporare:**
   - Proiectul video generat anterior (Remotion) a fost eliminat complet din arborele proiectului și din Git, readucând spațiul de lucru la o stare curată.

---

## 4. Starea Curentă a Sistemului

- **Suite de Teste:** Toate cele **32 de suite de teste automate** (`tests/run.py`) trec cu **100% succes** (`PASS`).
- **Git:** Ramura `main` este perfect sincronizată cu depozitul la distanță (`origin/main`).
- **Procese Active:** Procesul principal `relay` și procesul de supraveghere `relay.keeper` rulează în fundal.

---

## 5. Lista Problemelor Rămase & Vulnerabilități Critice Identificate

Următoarele 6 probleme de gravitate critică au fost identificate în urma auditului tehnic de securitate și stabilitate și necesită remediere imediată:

### 🔴 1. Execuție de Comenzi de la Distanță în Terminale (Remote Code Execution)
- **Fișiere afectate:** `relay/injector.py:301-303` și `relay/remote.py:1220`
- **Descriere:** În ramura de terminal (`is_terminal_window`), când un utilizator trimite o imagine cu text (caption) prin Telegram, căile imaginilor și textul din caption sunt concatenate direct: `command = f'{posix_paths} {caption}'`. Dacă textul conține caractere de linie nouă (`\r`, `\n`) sau separatori de comenzi (`;&|`), terminalul țintă (PowerShell, CMD, Bash) va executa comanda nesanitizată pe calculator.
- **Plan de remediere:** 
  1. Sanitizarea obligatorie a oricărui caption înainte de injectare în terminal (eliminarea/înlocuirea caracterelor `\r` și `\n` cu spațiu).
  2. Neutralizarea sau interzicerea comenzilor periculoase / separatorilor de comenzi în context de shell.
  3. Adăugarea de teste unitare dedicate în `tests/test_photo.py` pentru tentative de injecție multi-line.

### 🔴 2. Preluarea Neautorizată a Botului de Telegram când `chat_id` este null (Authentication Bypass)
- **Fișiere afectate:** `relay/remote.py:1162-1172`
- **Descriere:** Când `chat_id` este `null` (la prima configurare sau resetare), logica curentă asociază botul definitiv cu primul utilizator care trimite orice mesaj text către bot. Dacă un străin găsește botul pe Telegram înainte de asocierea proprietarului, acesta preia controlul complet al laptopului (`/shot`, injecție tastatură, citire `/notes`).
- **Plan de remediere:**
  1. Implementarea unui cod de împerechere unic (Pairing PIN / Code, ex: 6 cifre) generat la pornire și afișat exclusiv local pe ecran / în consola laptopului.
  2. Botul va ignora sau va cere `/pair <PIN>` pentru a accepta asocierea, respingând orice alt mesaj necunoscut.
  3. Păstrarea compatibilității cu suitele de testare existente.

### 🔴 3. Token Real de Telegram și Chat ID Expuse în Clar pe Disc
- **Fișiere afectate:** `telegram.json:3-4`
- **Descriere:** Fișierul local `telegram.json` conține în clar token-ul activ de bot Telegram și chat ID-ul utilizatorului. Deși fișierul este inclus în `.gitignore`, stocarea în clar pe disc prezintă risc în cazul accesului neautorizat local sau partajării accidentale a folderului.
- **Plan de remediere:**
  1. Utilizatorul trebuie să revoce și să regenereze token-ul compromis din `@BotFather`.
  2. Suport pentru injectarea token-ului prin variabile de mediu de sistem (`RELAY_TELEGRAM_TOKEN`, `RELAY_TELEGRAM_CHAT_ID`) ca alternativă la fișierul JSON.
  3. Setarea de permisiuni stricte de fișier pe Windows (ACL limitat exclusiv la contul de utilizator curent).

### 🔴 4. Crash CUDA și Corupere de Memorie la Inferență Concurentă pe WhisperModel
- **Fișiere afectate:** `relay/transcriber.py:184-203` și `relay/__main__.py:629-638`
- **Descriere:** `WhisperEngine.translate()` și instanța `self.model` din CTranslate2 nu dispun de un mecanism de sincronizare (`threading.Lock`). Dacă utilizatorul dictează la calculator (thread audio local) exact în timp ce sosește o notă vocală pe Telegram (thread worker Telegram), ambele thread-uri apelează simultan `self.model.transcribe()` pe același context GPU. Acest lucru cauzează corupere de memorie CUDA, erori CTranslate2 sau crash imediat al procesului Python.
- **Plan de remediere:**
  1. Încorporarea unui `threading.Lock()` de inferență în `WhisperEngine` sau la nivelul apelurilor din `relay/__main__.py` (`_translate_voice`).
  2. Serializarea apelurilor către modelul de transcriere/traducere astfel încât inferența să fie garantat thread-safe.

### 🔴 5. Blocaj Permanent al Interfeței (Deadlock) dacă Whisper Eșuează la Încărcare
- **Fișiere afectate:** `relay/__main__.py:713-723, 812`
- **Descriere:** La pornirea Relay, orbul este trecut în starea `PROCESSING`. Dacă inițializarea `WhisperEngine` eșuează pe thread-ul de fundal (lipsă conexiune la descărcare, VRAM insuficient, eroare CUDA DLL), excepția este prinsă și afișată, dar starea orbului nu este resetată la `IDLE` și evenimentul `self._ready` nu este setat. Drept urmare, orbul se învârte continuu, iar acțiunile F9 sau click raportează permanent `"[..] still loading the model"`, lăsând aplicația neutilizabilă.
- **Plan de remediere:**
  1. Înconjurarea procesului de încărcare într-un bloc `try...finally` care setează întotdeauna `self._ready.set()`.
  2. În caz de eșec, trecerea stării orbului la `IDLE` (sau o stare vizuală de eroare discretă) și salvarea erorii de inițializare pentru a fi comunicată utilizatorului la apăsarea F9, permițând eventual reîncercarea sau dictarea fallback.

### 🔴 6. Corupere Grafică și Crash PySide6 prin Modificare din Thread-uri Secundare
- **Fișiere afectate:** `relay/overlay.py:601-606` și `relay/__main__.py:220`
- **Descriere:** Regulile de bază ale arhitecturii Qt interzic modificarea stării componentelor vizuale direct din thread-uri worker secundare. În prezent, callback-urile audio apelează direct `self.orb.set_state(state)`, alterând atribute interne în timp ce thread-ul principal de GUI rulează bucla `_tick()` la fiecare 33ms. Această cursă critică (race condition) generează excepții precum `ValueError: not enough values to unpack`, distorsiuni de randare sau închiderea bruscă a ferestrei.
- **Plan de remediere:**
  1. Decuplarea apelurilor directe prin mecanismul nativ Qt de Semnale și Sloturi (`Signal(object)` / `QObject`).
  2. Asigurarea că orice tranziție de stare a orbului este procesată exclusiv în thread-ul principal de GUI prin bucla de evenimente Qt.

---

## 6. Următorii Pași Recomandați

Ordinea prioritară de implementare pentru rezolvarea definitivă a celor 6 vulnerabilități:
1. **Pasul 1:** Remedierea problemelor de threading și stabilitate GPU (Problemele #4 și #6) pentru eliminarea crash-urilor CUDA și a erorilor PySide6.
2. **Pasul 2:** Remedierea încărcării robuste cu fallback în caz de eroare (Problema #5) pentru a preveni blocarea la pornire.
3. **Pasul 3:** Sanitizarea strictă a comenzilor de terminal la ingestia imaginilor (Problema #1) pentru eliminarea oricărui risc RCE.
4. **Pasul 4:** Securizarea asocierii Telegram cu cod PIN de împerechere (Problema #2) și suport pentru variabile de mediu pentru credențiale (Problema #3).
5. **Pasul 5:** Rularea suitei complete de teste și adăugarea de teste de securitate pentru toate cele 6 puncte.

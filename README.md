# Lekha

**Lekha** (লেখা — Bengali for "writing") is a local-first, fully offline desktop application for translating large PDF documents (1000+ pages) between English and Bengali, built with **Argos Translate**. No API keys, no cloud services, no internet access required after one-time setup.

An optional **hybrid pipeline** is also available for users who would rather trade privacy for speed: Google Translate does the bulk translation over HTTP (near-zero local CPU), and a small local LLM polishes the result into natural book prose. Both stages are opt-in and off by default — see [Hybrid Pipeline](#hybrid-pipeline-optional).

![Status](https://img.shields.io/badge/status-production--ready-3ecf8e)
![Offline](https://img.shields.io/badge/network-100%25%20offline-8b6df0)
![Python](https://img.shields.io/badge/python-3.10%2B-5b9dff)

---

## Features

- **Fully offline translation** via [Argos Translate](https://www.argosopentech.com/) — no OpenAI, Anthropic, Gemini, DeepL, or any cloud API
- **Optional hybrid pipeline** — Google Translate for speed, plus a local 3B model (via Ollama) that rewrites machine translation as natural book prose. Opt-in, per job, off by default
- **Handles 1000+ page PDFs** with flat, predictable memory usage (streams pages one at a time, never loads a whole document into RAM)
- **Automatic resume** — if the app crashes or your PC restarts mid-translation, it picks up exactly where it stopped
- **Optional OCR** (PaddleOCR) for scanned pages, only triggered on pages with no extractable text
- **DOCX, PDF, and TXT** output, selectable per job
- **Batch translation** — drop multiple PDFs, they queue and process one at a time
- **Modern dark UI** — glassmorphism cards, smooth navigation, inspired by Notion/Linear/Raycast
- Designed and tuned for **low-end hardware**: Intel i3-10100, 16GB RAM, no dedicated GPU, running overnight unattended

---

## Quick Start

**One command** (recommended — handles venv, dependencies, and model download automatically, every step skipped if already done):

```bash
# Windows
run.bat

# macOS/Linux
chmod +x run.sh   # first time only
./run.sh
```

The first run needs an internet connection (to install dependencies and download translation models, a few hundred MB). Every run after that is fully offline. Your browser will open automatically to `http://localhost:8501`.

<details>
<summary>Manual steps (if you'd rather not use the script)</summary>

```bash
# 1. Clone or copy this folder, then move into it
cd Lekha

# 2. Create a virtual environment (recommended)
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. One-time setup: download translation models (requires internet, only run once)
python scripts/download_models.py

# 5. Launch the app
streamlit run app.py
```

</details>

Your browser will open automatically to `http://localhost:8501`. From here on, **no internet connection is needed** — the app, translation engine, and OCR all run entirely on your machine.

---

## System Requirements

| | Minimum | Tested on |
|---|---|---|
| OS | Windows 10/11, macOS, Linux | Windows 10/11 |
| CPU | Dual-core | Intel i3-10100 (4 cores / 8 threads) |
| RAM | 8 GB | 16 GB DDR4 |
| GPU | None required | Intel UHD 630 (integrated) |
| Disk | ~2 GB free (more if OCR is installed) | — |
| Python | 3.10+ | 3.10, 3.11, 3.12 |

The app is deliberately tuned to stay responsive on this class of hardware: translation runs on a background thread with a bounded worker pool (default 2 concurrent chunk translations), and checkpoints flush to disk every page so memory never accumulates across a long document.

---

## Project Structure

```
Lekha/
│
├── app.py                      # Streamlit entrypoint — routing, theme, page dispatch
├── run_app.py                  # PyInstaller-compatible launcher (boots Streamlit programmatically)
├── run.bat                     # One-command setup + launch (Windows)
├── run.sh                      # One-command setup + launch (macOS/Linux)
├── Lekha.spec          # Ready-to-use PyInstaller build spec
├── config.py                   # Central configuration: paths, languages, tunables
├── requirements.txt
├── README.md
├── .gitignore
│
├── core/                       # Translation pipeline — no Streamlit imports, fully reusable
│   ├── pdf_extractor.py        #   Streaming PyMuPDF text extraction, crash-resilient
│   ├── chunker.py               #   Splits page text into translation-sized chunks
│   ├── translator_engine.py    #   Argos Translate wrapper (singleton, cached) + backend factory
│   ├── online_translator.py    #   Optional Google Translate backend (rate-limited, retrying)
│   ├── llm_refiner.py          #   Optional local-LLM polish stage (Ollama, guardrailed)
│   ├── ocr_engine.py           #   Optional PaddleOCR wrapper (lazy-loaded)
│   ├── document_builder.py     #   Streaming DOCX / PDF / TXT writers
│   └── pipeline.py             #   Orchestrates extraction -> OCR -> translate -> output
│
├── services/                   # Cross-cutting infrastructure
│   ├── logger_service.py       #   Centralized logging (console + rotating file)
│   ├── settings_service.py     #   Persisted user settings (data/app_settings.json)
│   ├── checkpoint_service.py   #   The resume system (checkpoints/<job_id>/)
│   ├── history_service.py      #   Persisted translation history (data/history.json)
│   ├── file_service.py         #   Upload validation, sizing, "open in explorer"
│   └── queue_service.py        #   Background job queue + worker thread (batch translation)
│
├── models/                     # Plain data structures (dataclasses, enums)
│   ├── enums.py                #   JobStatus, OutputFormat
│   └── job.py                  #   TranslationJob, HistoryEntry
│
├── ui/                         # Streamlit presentation layer
│   ├── theme.py                #   Injects the dark glassmorphism CSS theme
│   ├── components.py           #   Sidebar, stat tiles, badges, log console, empty states
│   └── pages/
│       ├── dashboard.py        #   Stats, crash-recovery banner, recent translations
│       ├── translator.py       #   Upload, language/format/OCR selection, start job
│       ├── progress.py         #   Live progress, ETA, logs, downloads
│       ├── history.py          #   Search, re-translate, open folder, delete
│       └── settings.py         #   Translation/OCR/output/appearance settings
│
├── scripts/
│   └── download_models.py      # One-time setup script (needs internet, run once)
│
├── assets/
│   ├── style.css                # The dark glassmorphism theme
│   └── fonts/
│       └── NotoSansBengali-Regular.ttf   # Bundled (Apache-2.0) for correct PDF rendering
│
├── uploads/                    # Uploaded PDFs land here (gitignored)
├── outputs/                    # Translated documents land here, one folder per job
├── checkpoints/                # Resume data — checkpoint.json + pages.jsonl per job
└── logs/                       # app.log (rotating, 5MB x 5 backups)
```

### Why this architecture

- **`core/` has zero Streamlit imports.** Every translation primitive (extraction, chunking, translation, OCR, document building, pipeline orchestration) is plain Python and can be tested, scripted, or reused outside the UI entirely — which is exactly how this project was validated before being wired into Streamlit.
- **`services/queue_service.py` owns a single background worker thread** that survives Streamlit's per-interaction script reruns (Python module caching keeps it alive for the life of the process), so a translation keeps running even while you click around the UI.
- **The resume system is append-only.** `checkpoints/<job_id>/pages.jsonl` is written one line per completed page and only ever appended to, never rewritten — so a hard crash mid-page loses at most that one page's progress, never anything already on disk.

---

## How Resume Works

1. When a job starts, `checkpoint_service.init_checkpoint()` creates `checkpoints/<job_id>/checkpoint.json` (job metadata + `last_completed_page: -1`) and an empty `pages.jsonl`.
2. After each page is translated, its text is appended to `pages.jsonl` and `checkpoint.json`'s `last_completed_page` is updated — both writes are flushed to disk immediately.
3. If the app or PC crashes, on next launch the **Dashboard** detects any checkpoint whose status never reached `COMPLETED` and shows a recovery banner with a **Resume** button.
4. Resuming reconstructs the job from the checkpoint alone (it doesn't need the original in-app job object) and continues from `last_completed_page + 1` — already-translated pages are never re-translated.
5. On successful completion, checkpoints are deleted automatically (configurable in Settings → Output, if you'd like to keep them for auditing).

---

## Hybrid Pipeline (Optional)

The default pipeline runs a full neural translation model on your CPU for every chunk. That is what makes Lekha offline, and also what makes it slow on hardware without a GPU. The hybrid pipeline splits the work differently:

```
[PDF] --> [Google Translate] --> [raw Bengali] --> [local 3B model] --> [book]
          zero local CPU                          grammar + phrasing
```

Google does the heavy vocabulary lifting in an HTTP round-trip. The local model never translates — it only *edits* text that is already translated, which is a far easier task, so a 3B model on CPU is enough and no GPU is required.

Both stages are independent. You can use either, both, or neither.

### The trade-off, stated plainly

| Backend | Polish | Speed | Privacy |
| --- | --- | --- | --- |
| Argos | off | Slow on CPU | Fully offline |
| Argos | on | Slowest | Fully offline |
| Google | off | Fastest | **Text sent to Google** |
| Google | on | Moderate | **Text sent to Google** |

Selecting the Google backend means **every page of your document is transmitted to Google's servers**. Don't use it for confidential, personal, or unpublished material. The polish stage does *not* transmit anything — Ollama runs locally.

### Setup

The online backend needs one package:

```bash
pip install deep-translator
```

The polish stage needs [Ollama](https://ollama.com) plus a small instruct model:

```bash
ollama pull qwen2.5:3b
```

`llama3.2:3b` works equally well. Then open **Settings → Hybrid** and use **Test Google Translate connection** and **Test Ollama connection** to confirm both work before starting a long job.

### Choosing it per job

The **Translator** page's *2 · Translation engine* section selects the backend and the polish pass for that job. Settings → Hybrid only changes what those controls default to.

### How it behaves when things go wrong

- **Ollama is down or the model isn't pulled** — the job logs a warning and runs without the polish pass. It never fails the translation over a missing editor.
- **The model misbehaves** — every response is validated for length and for being in the target script. Output that looks like a refusal, an English answer, a truncation, or a runaway generation is discarded and the raw machine translation is kept for that block. The Progress log reports how many blocks fell back.
- **The network drops mid-job** — a failed chunk falls back to the untranslated source text, but after 12 *consecutive* failures the job aborts rather than handing you a confidently untranslated book.
- **Resume still works.** Checkpointing is unchanged, and the checkpoint records which backend produced it.

### Tuning

Chunking economics invert between the two backends, so each is tuned separately and editing one does not affect the other:

- **Argos** is CPU-bound and most accurate on short input — default 400 characters per chunk.
- **Google** is bound by HTTP round-trips. At 400 characters a 1000-page book is roughly 30,000 requests and near-certain rate limiting, so the default is 3000 characters per request.
- **Refinement** regroups translated chunks into ~1200-character blocks, so one model call covers several chunks. Larger blocks mean fewer calls and more context for consistent prose, at the cost of a slower response per call.

### A note on speed

The polish pass is the slowest stage in every configuration. A CPU-bound 3B model generates on the order of 5-15 tokens/second, so on a full-length book it will add substantially to total runtime — it is not free, and it is not faster than translation. Use it when prose quality matters more than throughput.

---

## Adding More Languages

The architecture is built to extend beyond English ↔ Bengali without touching pipeline code:

1. In `config.py`, add the language to `SUPPORTED_LANGUAGES` and the new pair(s) to `SUPPORTED_LANGUAGE_PAIRS`:
   ```python
   SUPPORTED_LANGUAGES = {"en": "English", "bn": "Bengali", "hi": "Hindi"}
   SUPPORTED_LANGUAGE_PAIRS = [("en", "bn"), ("bn", "en"), ("en", "hi"), ("hi", "en")]
   ```
2. Run `python scripts/download_models.py` again — it will download only the newly-added pairs.
3. That's it. The UI, translator engine, and Settings page all read from `config.py` and update automatically.

---

## OCR Setup (Optional)

OCR is only needed for **scanned** PDFs (pages with no extractable text layer). Most PDFs don't need it.

```bash
pip install paddleocr paddlepaddle
```

Then enable it per-job in the Translator page, or by default in **Settings → OCR**. The app automatically detects which pages need OCR (no extractable text + at least one embedded image) and only runs PaddleOCR on those — normal text PDFs are completely unaffected, even with OCR turned on.

PaddleOCR's models download themselves on first use (a few hundred MB) — this is a one-time, OCR-specific download separate from the Argos Translate models.

---

## Packaging as a Standalone Windows App (PyInstaller)

This repo includes ready-to-use packaging files — `run_app.py` (a launcher Streamlit can run from a frozen executable) and `Lekha.spec` (the PyInstaller build spec).

### 1. Install PyInstaller

```bash
pip install pyinstaller
```

### 2. Build

```bash
pyinstaller Lekha.spec
```

This uses `run_app.py` as the entrypoint (it boots Streamlit programmatically — `streamlit run app.py`'s CLI form doesn't work from a frozen `.exe`) and bundles `app.py`, `core/`, `services/`, `models/`, `ui/`, and `assets/` alongside it.

> **Note:** The spec builds with `--onedir`-equivalent settings (not a single-file `.exe`). This is intentional — Streamlit's file-watching and temp-extraction don't play well with PyInstaller's `--onefile` mode, and onedir builds start noticeably faster.

> **Note on OCR:** `paddleocr`/`paddlepaddle` are excluded from the bundle by default (they add several hundred MB). See the comment at the bottom of `Lekha.spec` if you want to include them.

### 3. Run the build

```
dist\Lekha\Lekha.exe
```

### 4. Distribute

Zip `dist/Lekha/`, or wrap it with [Inno Setup](https://jrsoftware.org/isinfo.php) for a proper Windows installer. Either way, make sure end users still run `scripts/download_models.py` once after install (requires one-time internet access) — the packaged app itself never needs network access afterward.

---

## Troubleshooting

**"No installed Argos Translate model" error in the UI**
Run `python scripts/download_models.py` with an internet connection. The app itself never needs network access — only this one-time setup script does.

**Bengali text shows as boxes in DOCX output**
The DOCX builder relies on Windows' bundled **Nirmala UI** font for Bengali glyphs. This ships with Windows 10/11 by default; if it's missing, install the Bengali language pack via *Settings → Time & Language → Language → Add a language → Bangla*.

**Bengali text shows as boxes in PDF output**
Make sure `assets/fonts/NotoSansBengali-Regular.ttf` exists (it's bundled with this repo). If you deleted it, re-download from [Google Fonts' Noto project](https://fonts.google.com/noto/specimen/Noto+Sans+Bengali) (Apache-2.0 licensed).

**App seems stuck / slow on a 1000+ page PDF**
This is expected to take a while on the target hardware — it's designed to run overnight. Check the **Progress** page's ETA and **Logs** panel; as long as the page counter is advancing, it's working. Lower **Translation chunk size** or **Parallel translation workers** in Settings if your machine becomes unresponsive for other tasks.

**OCR toggle is greyed out**
PaddleOCR isn't installed. Run `pip install paddleocr paddlepaddle`.

**"The online translation backend needs the deep-translator package"**
Run `pip install deep-translator`, or switch the backend back to Argos (offline) on the Translator page.

**The polish pass was skipped and the log says Ollama is unreachable**
Start the server with `ollama serve`, then `ollama pull qwen2.5:3b`. Confirm with **Settings → Hybrid → Test Ollama connection**. The job still completes without it — you just get unpolished machine translation.

**The log says blocks "kept the raw translation because the model's output failed validation"**
The refinement model returned something that wasn't a faithful edit — an English answer, a refusal, or a truncation. Lekha discarded it and kept the machine translation, so no content was lost. A different or larger instruct model usually fixes it; very small or non-instruct models fail this check often.

**The online backend fails repeatedly / the job aborts after 12 consecutive chunk failures**
Usually rate limiting or a dropped connection. Raise **Characters per request** and lower **Parallel requests** in the Translator page's Advanced settings — fewer, larger requests are much less likely to be throttled.

---

## Privacy

**In its default configuration, Lekha never sends your documents, extracted text, or translated output anywhere.** Once `scripts/download_models.py` has been run, the application has no further network dependency — translation (Argos Translate), OCR (PaddleOCR), and document generation (python-docx / ReportLab / PyMuPDF) all execute locally on your machine.

There is exactly one way to change that, and it is opt-in: selecting the **Google Translate** backend for a job sends the text of every page to Google's servers. It is never the default, it must be chosen per job, and the app warns you on the Translator page and in the job log whenever it is active. If you never touch it, nothing about the guarantee above changes.

The optional **local AI polish** stage does not transmit anything. It talks to Ollama on `localhost` and the model runs on your own machine.

---

## License

This project's code is provided as-is for local use. Bundled font (`NotoSansBengali-Regular.ttf`) is © Google, licensed under [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). Argos Translate and its language models are licensed separately — see [argosopentech.com](https://www.argosopentech.com/) for details.

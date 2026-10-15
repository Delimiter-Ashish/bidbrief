# Start here (Ashish)

The code for build-plan days 2 to 6 is done and tested (39 tests pass offline).
What is left is the work only you can do: real data, answer keys, the evaluation and the video.

## 1. Set up on SCC (once, about 5 minutes)

```bash
unzip bidbrief.zip && cd bidbrief
BIG_DIR=/projectnb/<project>/<you> bash scripts/setup_scc.sh
source scripts/env.sh
nano .env                          # GEMINI_API_KEY=...   (set a spending limit on the key)
python -m bidbrief doctor --ping --list-models
python -m pytest
```

If `doctor --ping` says the model name is wrong, pick one from the printed list and set `GEMINI_MODEL` in `.env`.

## 1b. Scanned pages (once)

SCC has no tesseract, so scanned pages are skipped. Install it into your big folder without root:

```bash
BIG_DIR=/projectnb/<project>/<you> bash scripts/install_ocr_scc.sh
source scripts/env.sh
```

## 2. Try it on the synthetic sample (2 minutes)

```bash
python -m bidbrief sample
python -m bidbrief process data/packages/sample-mapleford
python -m bidbrief.eval --packages sample-mapleford
python -m bidbrief serve --port 8501     # then open port 8501 from the PORTS tab
```

## 3. Day 1: three real public packages

Put each package's PDFs (and addenda) in its own folder: `data/packages/<short-name>/`.
Optional `package.json` in that folder: `{"title": "Town of X - Y Street Water Main Replacement"}`.
Write the URL and download date in `data/SOURCES.md` (a first candidate is listed there).
Then: `python -m bidbrief process-all` (or `qsub scripts/process_all.qsub`).

## 4. Day 3 and 7: answer keys and evaluation

Copy `data/answer_keys/_TEMPLATE` to `data/answer_keys/<short-name>/` and fill it by hand from the PDF.
Then:

```bash
python -m bidbrief.eval --label baseline
# fix the biggest cause in the error analysis, re-process, then:
python -m bidbrief.eval --label "after fix" --compare
```

Copy the real numbers into the README table. Report weak numbers as they are.

## 5. Let Sean try it (after it is hosted)

```bash
python -m bidbrief new-code --prefix SEAN --uses 3
```
Put the printed `BIDBRIEF_TRIAL_CODES=...` line in `.env` (or the Space's secrets) and restart.
Send Sean the link and the code. His uploads are visible only with his code, and he can delete them.
`python -m bidbrief trials` shows how many he has used.

## 6. Video and message

Record on a real package (never the synthetic one). Time the processing yourself and only say "about two minutes" if that is what you measured.
Send Sean the video link, not GitHub, between October 22 and 26.

## If something goes wrong

- `python -m bidbrief doctor` shows what is missing.
- Scanned pages with no OCR are listed in the app under "Pages that need a manual look"; `module load tesseract` (or install it) and re-process.
- Model rate limit: results are cached; wait and run again. Lower `LLM_RPM` in `.env`.
- No internet on a compute node for the search models: run `python -m bidbrief doctor --download` on a login node once, or set `BIDBRIEF_EMBEDDINGS=0 BIDBRIEF_RERANK=0` (keyword search only).

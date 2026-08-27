# Japanese TTS Study

Local Tkinter app: turns a Japanese dialogue file into audio with Gemini
multi-speaker TTS, then lets you transcribe it sentence by sentence and diffs
what you typed against the script.

Runs on Linux, macOS and Windows.

## Running

```bash
uv run python japanese_tts_gui.py
```

Run it from this folder: `uv run` locates the project by walking up from the
working directory, so from elsewhere it starts without the dependencies.

## Linux notes

A CJK font is needed or the Japanese text renders as empty boxes:

```bash
sudo apt install fonts-noto-cjk    # or fonts-takao, fonts-vlgothic, …
```

### Build the venv on the distro Python

uv's managed CPython bundles a Tk compiled **without fontconfig**: it cannot see
any installed font, falls back to the unhinted `nimbus sans l` / `fixed` X core
fonts, and renders the Japanese noticeably blurrier. The distro Python's Tk has
Xft, so point the environment at it once:

```bash
sudo apt install python3-tk                 # Tkinter for the distro Python
uv venv --python /usr/bin/python3 && uv sync
```

`uv run` reuses the existing `.venv`, so this survives until the venv is
deleted — if the fonts ever look chunky again, that is what happened. Check with:

```bash
uv run python -c "import tkinter,sys; from tkinter import font; \
r=tkinter.Tk(); print(font.nametofont('TkDefaultFont').actual()['family'])"
```

`Noto Sans` (or any real system font) is right; `nimbus sans l` means the venv
got rebuilt on uv's Python.

Playback goes through SDL, so an active audio server (PipeWire, PulseAudio or
ALSA) is required. Without one the app still generates and saves audio; only
playback is unavailable, with a warning instead of a crash.

## Using it

**Generate tab** — pick the dialogue file (lines are `Speaker: text`; `#`
starts a comment), paste a Gemini API key, choose the two voices and the speed, then *Genera audio*. Each
run writes a WAV, an MP3 and two sidecar files into `audio_output/`. Identical
requests are served from `audio_output/gemini_cache/` while *riusa audio* is on.

**Transcribe tab** — pick a generated MP3, play it, and type each line into its
row. One row per turn, so consecutive rows are always different speakers and a
change of voice marks every boundary — a turn made of several sentences stays a
single row rather than forcing you to guess where it was cut. Enter checks the
row and moves to the next one: wrong characters are
highlighted red, and `|` marks a spot where something is missing. Punctuation
and whitespace are ignored by the comparison.

The API key is cached, base64-encoded, under
`~/.config/JapaneseTTSStudy/settings.json` (`%APPDATA%` on Windows). Uncheck
*cache* to keep it in memory only.

## Dialogues

- `dialogo_mono.txt` — ものなら / ものだから / ものの / もの（もん）. Loaded by default.
- `dialogo_giapponese.txt` — げ / がち / 気味 / っぽく. Open it with *Apri*.

Add your own file in the same format; the *focus* line tags whichever of the
patterns in `GRAMMAR_PATTERNS` it finds.

## Development

```bash
uv run python -m unittest test_japanese_tts_gui   # tests
uv run python japanese_tts_gui.py --test-tts      # one small live API call
uv run python alignment_probe.py                  # silence-split tuning
```

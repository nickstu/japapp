# -*- coding: utf-8 -*-
"""Local GUI for turning a Japanese study dialogue into Gemini TTS audio.

This script uses only the Python standard library. It calls the Gemini
multi-speaker TTS endpoint once for the full dialogue, then saves the returned
PCM audio as a WAV file.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import queue
import re
import threading
import time
import socket
import sys
import unicodedata
import urllib.error
import urllib.request
import wave
from difflib import SequenceMatcher
from dataclasses import dataclass
from pathlib import Path
from tkinter import (
    END,
    LEFT,
    RIGHT,
    BOTH,
    Canvas,
    DoubleVar,
    X,
    Y,
    BooleanVar,
    Button,
    Checkbutton,
    Entry,
    Frame,
    Label,
    OptionMenu,
    Scrollbar,
    StringVar,
    Text,
    Tk,
    TkVersion,
    filedialog,
    messagebox,
    scrolledtext,
    ttk,
)

import lameenc

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame


APP_NAME = "JapaneseTTSStudy"
PROVIDER = "gemini"
MODEL = "gemini-3.1-flash-tts-preview"
SPEECH_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
DEFAULT_DIALOGUE_PATH = Path(__file__).with_name("dialogo_mono.txt")
OUTPUT_DIR = Path(__file__).with_name("audio_output")
VOICE_OPTIONS = [
    "Kore",
    "Puck",
    "Zephyr",
    "Charon",
    "Fenrir",
    "Leda",
    "Orus",
    "Aoede",
    "Callirrhoe",
    "Autonoe",
    "Enceladus",
    "Iapetus",
    "Umbriel",
    "Algieba",
    "Despina",
    "Erinome",
    "Algenib",
    "Rasalgethi",
    "Laomedeia",
    "Achernar",
    "Alnilam",
    "Schedar",
    "Gacrux",
    "Pulcherrima",
    "Achird",
    "Zubenelgenubi",
    "Vindemiatrix",
    "Sadachbia",
    "Sadaltager",
    "Sulafat",
]
GRAMMAR_PATTERNS = {
    "げ": re.compile(r"(げ|ありげ|なさげ)"),
    "がち": re.compile(r"がち"),
    "気味": re.compile(r"気味"),
    "っぽく": re.compile(r"っぽく|っぽい|っぽさ"),
    "ものなら": re.compile(r"(?:もの|もん)なら"),
    "ものだから": re.compile(r"(?:もの|もん)(?:だ|です)から"),
    "ものの": re.compile(r"ものの"),
    "もの(理由)": re.compile(r"(?:もの|もん)(?=[。、！？]|$)"),
}


@dataclass
class Turn:
    speaker: str
    text: str

    @property
    def tags(self) -> str:
        found = [name for name, pattern in GRAMMAR_PATTERNS.items() if pattern.search(self.text)]
        return ", ".join(found)


def config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys_platform := os.environ.get("XDG_CONFIG_HOME"):
        base = Path(sys_platform)
    else:
        base = Path.home() / ".config"
    return base / APP_NAME / "settings.json"


def load_cached_key() -> str:
    path = config_path()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("provider") != PROVIDER:
            return ""
        encoded = data.get("api_key_b64", "")
        return base64.b64decode(encoded.encode("ascii")).decode("utf-8") if encoded else ""
    except Exception:
        return ""


def normalize_api_key(api_key: str) -> str:
    cleaned = "".join(api_key.split())
    if not cleaned:
        return ""
    try:
        cleaned.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(
            "La API key contiene caratteri non ASCII. "
            "Incolla solo la chiave Gemini, senza testo extra."
        ) from exc
    return cleaned


def masked_key(api_key: str) -> str:
    if len(api_key) <= 10:
        return "***"
    return f"{api_key[:7]}...{api_key[-4:]}"


def save_cached_key(api_key: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "provider": PROVIDER,
        "api_key_b64": base64.b64encode(api_key.encode("utf-8")).decode("ascii"),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def parse_dialogue(text: str) -> list[Turn]:
    turns: list[Turn] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([^:：]{1,24})[:：]\s*(.+)$", line)
        if match:
            turns.append(Turn(match.group(1).strip(), match.group(2).strip()))
    return turns


def split_turn_into_sentences(turn: Turn) -> list[Turn]:
    parts = re.findall(r"[^。！？!?]+[。！？!?]?", turn.text)
    sentences = [part.strip() for part in parts if part.strip()]
    return [Turn(turn.speaker, sentence) for sentence in sentences] or [turn]


def sentence_turns_from_turns(turns: list[Turn]) -> list[Turn]:
    sentences: list[Turn] = []
    for turn in turns:
        sentences.extend(split_turn_into_sentences(turn))
    return sentences


def merge_consecutive_turns(turns: list[Turn]) -> list[Turn]:
    """Join adjacent turns that share a speaker, so each row is a new voice.

    Transcription rows are one per turn. When the same speaker has two lines in
    a row they must not become two rows: the audio gives no cue for where the
    boundary falls, so the row split would have to be guessed.
    """
    merged: list[Turn] = []
    for turn in turns:
        if merged and merged[-1].speaker == turn.speaker:
            merged[-1] = Turn(turn.speaker, merged[-1].text + turn.text)
        else:
            merged.append(turn)
    return merged


def transcript_from_turns(turns: list[Turn]) -> str:
    return "\n".join(turn.text for turn in turns)


def is_ignored_diff_char(char: str) -> bool:
    if char.isspace():
        return True
    category = unicodedata.category(char)
    return category.startswith("P")


def normalize_for_diff(text: str) -> tuple[str, list[int]]:
    normalized_chars = []
    original_indexes = []
    for index, char in enumerate(text):
        if is_ignored_diff_char(char):
            continue
        normalized_chars.append(char)
        original_indexes.append(index)
    return "".join(normalized_chars), original_indexes


def diff_error_ranges(expected: str, typed: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]], int, int, int]:
    expected_norm, _expected_indexes = normalize_for_diff(expected)
    typed_norm, typed_indexes = normalize_for_diff(typed)
    matcher = SequenceMatcher(None, expected_norm, typed_norm, autojunk=False)

    ranges: list[tuple[int, int]] = []
    missing_ranges: list[tuple[int, int]] = []
    correct = 0
    errors = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            correct += j2 - j1
            continue
        if tag in {"replace", "insert"}:
            errors += j2 - j1
            for normalized_index in range(j1, j2):
                original_index = typed_indexes[normalized_index]
                ranges.append((original_index, original_index + 1))
        elif tag == "delete":
            errors += i2 - i1
            if typed_indexes:
                anchor = min(j1, len(typed_indexes) - 1)
                original_index = typed_indexes[anchor]
                missing_ranges.append((original_index, original_index + 1))

    remaining = max(len(expected_norm) - correct - errors, 0)
    return ranges, missing_ranges, correct, errors, remaining


def diff_display_segments(expected: str, typed: str) -> tuple[list[tuple[str, str]], int, int, int]:
    expected_norm, _expected_indexes = normalize_for_diff(expected)
    typed_norm, typed_indexes = normalize_for_diff(typed)
    matcher = SequenceMatcher(None, expected_norm, typed_norm, autojunk=False)

    segments: list[tuple[str, str]] = []
    cursor = 0
    correct = 0
    errors = 0

    def append_typed_until(original_index: int) -> None:
        nonlocal cursor
        if cursor < original_index:
            segments.append(("normal", typed[cursor:original_index]))
            cursor = original_index

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            if j1 < j2:
                start = typed_indexes[j1]
                end = typed_indexes[j2 - 1] + 1
                append_typed_until(start)
                segments.append(("normal", typed[start:end]))
                cursor = end
                correct += j2 - j1
            continue

        if tag in {"replace", "insert"} and j1 < j2:
            start = typed_indexes[j1]
            end = typed_indexes[j2 - 1] + 1
            append_typed_until(start)
            segments.append(("error", typed[start:end]))
            cursor = end
            errors += j2 - j1

        if tag in {"replace", "delete"} and i1 < i2:
            errors += i2 - i1
            anchor = typed_indexes[min(j1, len(typed_indexes) - 1)] if typed_indexes else 0
            append_typed_until(anchor)
            segments.append(("missing", "|"))

    append_typed_until(len(typed))
    remaining = max(len(expected_norm) - correct - errors, 0)
    return segments, correct, errors, remaining


def transcript_sidecar_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(audio_path.suffix + ".transcript.txt")


def write_transcript_sidecar(audio_path: Path, turns: list[Turn]) -> None:
    transcript_sidecar_path(audio_path).write_text(transcript_from_turns(turns), encoding="utf-8")


def turns_sidecar_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(audio_path.suffix + ".turns.json")


def write_turns_sidecar(audio_path: Path, turns: list[Turn]) -> None:
    payload = [{"speaker": turn.speaker, "text": turn.text} for turn in turns]
    turns_sidecar_path(audio_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_turns_sidecar(audio_path: Path) -> list[Turn]:
    sidecar = turns_sidecar_path(audio_path)
    if not sidecar.exists():
        return []
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    return [Turn(str(item["speaker"]), str(item["text"])) for item in data]


def speaker_aliases(turns: list[Turn]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for turn in turns:
        if turn.speaker not in aliases:
            aliases[turn.speaker] = f"Speaker{len(aliases) + 1}"
    return aliases


def build_gemini_prompt(turns: list[Turn], speed: float) -> str:
    speed_note = "natural native speed"
    if speed < 0.9:
        speed_note = "a little slower than natural, but still connected and conversational"
    elif speed > 1.1:
        speed_note = "slightly brisk, while keeping clear native Japanese phrasing"

    lines = [
        "TTS the following Japanese conversation.",
        "Do not read speaker labels.",
        "Make it sound like a real, relaxed conversation, not a textbook recital.",
        f"Use {speed_note}.",
        "Keep particles connected to the surrounding words; avoid detached word-by-word reading.",
        "",
    ]
    aliases = speaker_aliases(turns)
    for turn in turns:
        lines.append(f"{aliases[turn.speaker]}: {turn.text}")
    return "\n".join(lines)


def pcm_to_wav_bytes(pcm: bytes, channels: int = 1, rate: int = 24000, sample_width: int = 2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return out.getvalue()


def wav_to_mp3(wav_path: Path, mp3_path: Path, bit_rate: int = 128) -> None:
    with wave.open(str(wav_path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        pcm = wav.readframes(wav.getnframes())

    if sample_width != 2:
        raise RuntimeError(f"MP3 conversion requires 16-bit PCM WAV, got sample width {sample_width}.")

    encoder = lameenc.Encoder()
    encoder.set_bit_rate(bit_rate)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(channels)
    encoder.set_quality(2)
    mp3_data = encoder.encode(pcm) + encoder.flush()
    mp3_path.write_bytes(mp3_data)


def wav_duration_seconds(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def init_mixer() -> bool:
    """Start the SDL mixer, tolerating machines with no usable audio device.

    On Linux the mixer raises instead of falling back when no PulseAudio /
    PipeWire / ALSA sink is reachable, so every caller has to cope with a
    missing device rather than assume playback is available.
    """
    if pygame.mixer.get_init():
        return True
    try:
        pygame.mixer.init()
    except pygame.error:
        return False
    return True


def audio_duration_seconds(audio_path: Path) -> float:
    wav_path = audio_path.with_suffix(".wav") if audio_path.suffix.lower() != ".wav" else audio_path
    if wav_path.exists():
        return wav_duration_seconds(wav_path)
    if not init_mixer():
        return 0.0
    try:
        sound = pygame.mixer.Sound(str(audio_path))
    except pygame.error:
        return 0.0
    return float(sound.get_length())


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def find_audio_base64(data) -> str:
    if isinstance(data, dict):
        output_audio = data.get("output_audio")
        if isinstance(output_audio, dict) and isinstance(output_audio.get("data"), str):
            return output_audio["data"]
        inline_data = data.get("inline_data") or data.get("inlineData")
        if isinstance(inline_data, dict) and isinstance(inline_data.get("data"), str):
            return inline_data["data"]
        audio = data.get("audio")
        if isinstance(audio, dict) and isinstance(audio.get("data"), str):
            return audio["data"]
        direct_data = data.get("data")
        if isinstance(direct_data, str) and looks_like_audio_data_block(data, direct_data):
            return direct_data
        for value in data.values():
            found = find_audio_base64(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_audio_base64(item)
            if found:
                return found
    return ""


def looks_like_audio_data_block(block: dict, encoded: str) -> bool:
    if len(encoded) < 256:
        return False
    marker_values = [
        block.get("type"),
        block.get("modality"),
        block.get("mime_type"),
        block.get("mimeType"),
        block.get("format"),
    ]
    marker_text = " ".join(str(value).lower() for value in marker_values if value)
    if "audio" in marker_text or "pcm" in marker_text or "wav" in marker_text:
        return True
    # Gemini REST interaction responses may return audio blocks as
    # steps[].content[].data without an explicit audio marker on the block.
    try:
        base64.b64decode(encoded[:1024], validate=True)
        return True
    except Exception:
        return False


def request_gemini_dialogue_wav(
    api_key: str,
    turns: list[Turn],
    teacher_voice: str,
    student_voice: str,
    speed: float,
    timeout: int = 300,
) -> bytes:
    api_key = normalize_api_key(api_key)
    prompt = build_gemini_prompt(turns, speed)
    aliases = speaker_aliases(turns)
    speakers = list(aliases.values())
    if len(speakers) > 2:
        raise RuntimeError("Questa app supporta al massimo due speaker per Gemini TTS.")
    payload = {
        "model": MODEL,
        "input": prompt,
        "response_format": {"type": "audio"},
        "generation_config": {
            "speech_config": [
                {"speaker": speakers[0], "voice": teacher_voice},
                {"speaker": speakers[1] if len(speakers) > 1 else speakers[0], "voice": student_voice},
            ]
        },
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        SPEECH_URL,
        data=body,
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Network timeout while reading audio.") from exc
    except socket.timeout as exc:
        raise RuntimeError("Network timeout while reading audio.") from exc

    encoded_audio = find_audio_base64(response_data)
    if not encoded_audio:
        debug_path = OUTPUT_DIR / "gemini_last_response_without_audio.json"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(json.dumps(response_data, ensure_ascii=False, indent=2), encoding="utf-8")
        preview = json.dumps(response_data, ensure_ascii=False)[:1000]
        raise RuntimeError(f"Gemini response did not contain audio data. Saved response to {debug_path}: {preview}")
    pcm = base64.b64decode(encoded_audio)
    return pcm_to_wav_bytes(pcm)


def request_gemini_dialogue_wav_with_retries(
    api_key: str,
    turns: list[Turn],
    teacher_voice: str,
    student_voice: str,
    speed: float,
    attempts: int = 3,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return request_gemini_dialogue_wav(api_key, turns, teacher_voice, student_voice, speed)
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"Gemini TTS failed after {attempts} attempts: {last_error}") from last_error


class JapaneseTTSApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Japanese TTS Study")
        self.mixer_warning_shown = False
        self.log_queue: queue.Queue = queue.Queue()
        self.turns: list[Turn] = []
        self.dialogue_text = ""
        self.audio_choices: dict[str, Path] = {}
        self.transcription_turns: list[Turn] = []
        self.transcription_rows: list[dict] = []
        self.current_playback_path: Path | None = None
        self.audio_duration = 0.0
        self.playback_offset = 0.0
        self.playback_started_at = 0.0
        self.audio_loaded = False
        self.audio_paused = False
        self.dragging_progress = False
        self.updating_progress = False

        self.api_key_var = StringVar(value=load_cached_key())
        self.voice_teacher_var = StringVar(value="Kore")
        self.voice_student_var = StringVar(value="Puck")
        self.speed_var = StringVar(value="0.88")
        self.reuse_audio_var = BooleanVar(value=True)
        self.cache_key_var = BooleanVar(value=True)
        self.dialogue_path_var = StringVar(value=str(DEFAULT_DIALOGUE_PATH))
        self.dialogue_status_var = StringVar(value="Nessun dialogo caricato")
        self.generated_audio_var = StringVar(value="")
        self.selected_audio_var = StringVar(value="")
        self.transcription_status_var = StringVar(value="Scegli un audio per iniziare")
        self.progress_var = DoubleVar(value=0.0)
        self.audio_time_var = StringVar(value="00:00 / 00:00")

        self._build_ui()
        self._apply_window_size()
        self.load_dialogue()
        self.refresh_audio_list()
        if self.api_key_var.get():
            self.log(f"API key caricata dalla cache: {masked_key(self.api_key_var.get())}")
        self.root.after(150, self.drain_log_queue)
        self.root.after(250, self.update_audio_progress)

    def _apply_window_size(self) -> None:
        """Size the window from the widgets' own requests.

        Font metrics differ per platform (and per desktop theme on Linux), so a
        hard-coded geometry can clip controls. Ask Tk what the layout needs and
        never open — or let the user shrink — below that.
        """
        self.root.update_idletasks()
        width = max(980, self.root.winfo_reqwidth())
        self.root.geometry(f"{width}x720")
        self.root.minsize(width, 520)

    def _build_ui(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=BOTH, expand=True)

        self.generate_tab = Frame(self.notebook)
        self.transcribe_tab = Frame(self.notebook)
        self.notebook.add(self.generate_tab, text="Generate")
        self.notebook.add(self.transcribe_tab, text="Transcribe")

        self._build_generate_tab()
        self._build_transcribe_tab()

    def _build_generate_tab(self) -> None:
        path_frame = Frame(self.generate_tab, padx=10, pady=8)
        path_frame.pack(fill=X)
        Label(path_frame, text="Dialogo").pack(side=LEFT)
        Entry(path_frame, textvariable=self.dialogue_path_var).pack(side=LEFT, fill=X, expand=True, padx=8)
        Button(path_frame, text="Apri", command=self.choose_dialogue).pack(side=LEFT)
        Button(path_frame, text="Ricarica/parse", command=self.load_dialogue).pack(side=LEFT, padx=(8, 0))

        status_frame = Frame(self.generate_tab, padx=10)
        status_frame.pack(fill=X, pady=(0, 8))
        Label(status_frame, textvariable=self.dialogue_status_var).pack(anchor="w")
        Label(status_frame, textvariable=self.generated_audio_var).pack(anchor="w", pady=(4, 0))

        # Two rows instead of one: the single row needed ~1120px with Linux font
        # metrics and pushed "Genera audio" outside a 980px window.
        key_row = Frame(self.generate_tab, padx=10, pady=6)
        key_row.pack(fill=X)

        Label(key_row, text="Gemini API key").pack(side=LEFT)
        Entry(key_row, textvariable=self.api_key_var, show="*", width=34).pack(side=LEFT, padx=(6, 10))
        Checkbutton(key_row, text="cache", variable=self.cache_key_var).pack(side=LEFT)
        Button(key_row, text="Salva key", command=self.save_key_from_field).pack(side=LEFT, padx=(6, 0))

        controls = Frame(self.generate_tab, padx=10, pady=6)
        controls.pack(fill=X)

        Button(controls, text="Genera audio", command=self.generate_audio).pack(side=RIGHT)

        Label(controls, text="Voce 1").pack(side=LEFT)
        OptionMenu(controls, self.voice_teacher_var, *VOICE_OPTIONS).pack(side=LEFT, padx=(4, 0))
        Label(controls, text="Voce 2").pack(side=LEFT, padx=(12, 4))
        OptionMenu(controls, self.voice_student_var, *VOICE_OPTIONS).pack(side=LEFT)
        Label(controls, text="velocità").pack(side=LEFT, padx=(12, 4))
        Entry(controls, textvariable=self.speed_var, width=5).pack(side=LEFT)
        Checkbutton(controls, text="riusa audio", variable=self.reuse_audio_var).pack(side=LEFT, padx=(12, 0))

        log_frame = Frame(self.generate_tab, padx=10)
        log_frame.pack(fill=BOTH, expand=True, pady=(0, 10))
        Label(log_frame, text="Log").pack(anchor="w")
        self.log_box = scrolledtext.ScrolledText(log_frame, wrap="word", height=18, state="disabled")
        self.log_box.pack(fill=BOTH, expand=True)

    def _build_transcribe_tab(self) -> None:
        top = Frame(self.transcribe_tab, padx=10, pady=8)
        top.pack(fill=X)
        Label(top, text="Audio").pack(side=LEFT)
        self.audio_combo = ttk.Combobox(top, textvariable=self.selected_audio_var, state="readonly", width=52)
        self.audio_combo.pack(side=LEFT, fill=X, expand=True, padx=8)
        self.audio_combo.bind("<<ComboboxSelected>>", self.on_audio_selected)
        Button(top, text="Aggiorna", command=self.refresh_audio_list).pack(side=LEFT)

        player = Frame(self.transcribe_tab, padx=10)
        player.pack(fill=X, pady=(0, 8))
        Button(player, text="Play", command=self.play_selected_audio).pack(side=LEFT)
        Button(player, text="Stop", command=self.stop_audio).pack(side=LEFT, padx=(8, 10))
        self.progress_scale = ttk.Scale(
            player,
            from_=0.0,
            to=1.0,
            variable=self.progress_var,
        )
        self.progress_scale.pack(side=LEFT, fill=X, expand=True)
        self.progress_scale.bind("<ButtonPress-1>", self.on_progress_press)
        self.progress_scale.bind("<ButtonRelease-1>", self.on_progress_release)
        Label(player, textvariable=self.audio_time_var, width=14, anchor="e").pack(side=LEFT, padx=(10, 0))

        status = Frame(self.transcribe_tab, padx=10)
        status.pack(fill=X)
        Label(status, textvariable=self.transcription_status_var).pack(anchor="w")

        editor = Frame(self.transcribe_tab, padx=10, pady=8)
        editor.pack(fill=BOTH, expand=True)
        self.transcribe_canvas = Canvas(editor, highlightthickness=0)
        scrollbar = Scrollbar(editor, orient="vertical", command=self.transcribe_canvas.yview)
        self.transcribe_rows_frame = Frame(self.transcribe_canvas)
        self.transcribe_rows_frame.bind(
            "<Configure>",
            lambda _event: self.transcribe_canvas.configure(scrollregion=self.transcribe_canvas.bbox("all")),
        )
        self.transcribe_canvas_window = self.transcribe_canvas.create_window(
            (0, 0), window=self.transcribe_rows_frame, anchor="nw"
        )
        self.transcribe_canvas.configure(yscrollcommand=scrollbar.set)
        self.transcribe_canvas.bind(
            "<Configure>",
            lambda event: self.transcribe_canvas.itemconfigure(self.transcribe_canvas_window, width=event.width),
        )
        self.transcribe_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.bind_scroll_wheel(self.transcribe_canvas)
        self.bind_scroll_wheel(self.transcribe_rows_frame)

    def bind_scroll_wheel(self, widget) -> None:
        """Wheel-scroll the sentence list from any widget inside it.

        Tk 8.6 on X11 reports the wheel as Button-4/Button-5 rather than the
        <MouseWheel> event Windows and macOS send. Tk 9.0 emits <MouseWheel>
        everywhere and reuses Button-4/Button-5 for the mouse's side buttons,
        so those must not be bound there.
        """
        widget.bind("<MouseWheel>", self.on_scroll_wheel)
        if TkVersion < 9.0:
            widget.bind("<Button-4>", self.on_scroll_wheel)
            widget.bind("<Button-5>", self.on_scroll_wheel)

    def on_scroll_wheel(self, event):
        if event.num == 4:
            steps = -1
        elif event.num == 5:
            steps = 1
        elif event.delta:
            # Windows and Tk 9.0 deliver multiples of 120; macOS smaller values.
            steps = -1 if event.delta > 0 else 1
        else:
            return None
        self.transcribe_canvas.yview_scroll(steps, "units")
        return "break"

    def choose_dialogue(self) -> None:
        path = filedialog.askopenfilename(
            title="Scegli un dialogo",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.dialogue_path_var.set(path)
            self.load_dialogue()

    def load_dialogue(self) -> None:
        path = Path(self.dialogue_path_var.get())
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            messagebox.showerror("File mancante", f"Non trovo il file:\n{path}")
            return
        except Exception as exc:
            messagebox.showerror("Errore", str(exc))
            return

        self.dialogue_text = text
        self.turns = parse_dialogue(text)
        self.refresh_turns()
        self.log(f"Parsati {len(self.turns)} turni da {path.name}.")

    def refresh_turns(self) -> None:
        found_tags: list[str] = []
        for name, pattern in GRAMMAR_PATTERNS.items():
            if any(pattern.search(turn.text) for turn in self.turns):
                found_tags.append(name)
        tags = ", ".join(found_tags) if found_tags else "nessuna"
        self.dialogue_status_var.set(f"Dialogo caricato: {len(self.turns)} turni · focus: {tags}")

    def generate_audio(self) -> None:
        raw_text = self.dialogue_text
        self.turns = parse_dialogue(raw_text)
        self.refresh_turns()

        try:
            api_key = normalize_api_key(self.api_key_var.get())
        except ValueError as exc:
            messagebox.showwarning("API key non valida", str(exc))
            return
        if not api_key:
            messagebox.showwarning("API key richiesta", "Inserisci una API key Gemini.")
            return
        if not self.turns:
            messagebox.showwarning("Nessun turno", "Non ho trovato righe nel formato Speaker: testo.")
            return

        try:
            speed = float(self.speed_var.get())
        except ValueError:
            messagebox.showwarning("Velocità non valida", "Usa un numero, per esempio 0.88 o 1.0.")
            return
        if not 0.25 <= speed <= 4.0:
            messagebox.showwarning("Velocità non valida", "La velocità deve essere tra 0.25 e 4.0.")
            return

        teacher_voice = self.voice_teacher_var.get()
        student_voice = self.voice_student_var.get()
        cache_key = self.cache_key_var.get()
        reuse_audio = self.reuse_audio_var.get()
        turns = list(self.turns)
        if cache_key:
            try:
                save_cached_key(api_key)
                self.log(f"API key salvata in cache: {masked_key(api_key)}")
            except Exception as exc:
                messagebox.showwarning("Cache API key", f"Non sono riuscito a salvare la key: {exc}")
                return
        worker = threading.Thread(
            target=self._generate_worker,
            args=(api_key, speed, teacher_voice, student_voice, cache_key, reuse_audio, turns),
            daemon=True,
        )
        worker.start()

    def save_key_from_field(self) -> None:
        try:
            api_key = normalize_api_key(self.api_key_var.get())
        except ValueError as exc:
            messagebox.showwarning("API key non valida", str(exc))
            return
        if not api_key:
            messagebox.showwarning("API key richiesta", "Inserisci una API key Gemini.")
            return
        try:
            save_cached_key(api_key)
        except Exception as exc:
            messagebox.showwarning("Cache API key", f"Non sono riuscito a salvare la key: {exc}")
            return
        self.api_key_var.set(api_key)
        self.log(f"API key salvata in cache: {masked_key(api_key)}")

    def _generate_worker(
        self,
        api_key: str,
        speed: float,
        teacher_voice: str,
        student_voice: str,
        cache_key: bool,
        reuse_audio: bool,
        turns: list[Turn],
    ) -> None:
        try:
            self.log_queue.put("Generazione avviata.")
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            destination = OUTPUT_DIR / f"dialogo_giapponese_{timestamp}.wav"
            cache_path = OUTPUT_DIR / "gemini_cache" / f"{self.dialogue_fingerprint(turns, teacher_voice, student_voice, speed)}.wav"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

            if reuse_audio and cache_path.exists() and cache_path.stat().st_size > 44:
                self.log_queue.put(f"Gemini multi-speaker -> cache ({teacher_voice}/{student_voice})")
                data = cache_path.read_bytes()
            else:
                self.log_queue.put(f"Gemini multi-speaker -> API ({teacher_voice}/{student_voice})")
                data = request_gemini_dialogue_wav_with_retries(
                    api_key, turns, teacher_voice, student_voice, speed
                )
                cache_path.write_bytes(data)

            destination.write_bytes(data)
            mp3_destination = destination.with_suffix(".mp3")
            wav_to_mp3(destination, mp3_destination)
            write_transcript_sidecar(destination, turns)
            write_transcript_sidecar(mp3_destination, turns)
            write_turns_sidecar(destination, turns)
            write_turns_sidecar(mp3_destination, turns)

            self.log_queue.put(f"Audio creato: {destination}")
            self.log_queue.put(f"MP3 creato: {mp3_destination}")
            self.log_queue.put(("audio_created", str(mp3_destination)))
            self.log_queue.put("Fine.")
        except Exception as exc:
            self.log_queue.put(f"Errore: {exc}")

    @staticmethod
    def dialogue_fingerprint(
        turns: list[Turn],
        teacher_voice: str,
        student_voice: str,
        speed: float,
    ) -> str:
        payload = {
            "model": MODEL,
            "teacher_voice": teacher_voice,
            "student_voice": student_voice,
            "speed": speed,
            "turns": [{"speaker": turn.speaker, "text": turn.text} for turn in turns],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def refresh_audio_list(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(
            list(OUTPUT_DIR.glob("dialogo_giapponese_*.mp3")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        self.audio_choices = {path.name: path for path in files}
        values = list(self.audio_choices.keys())
        self.audio_combo.configure(values=values)
        if values and self.selected_audio_var.get() not in self.audio_choices:
            self.selected_audio_var.set(values[0])
            self.load_selected_audio_transcript()
            self.load_selected_audio_for_player()
        elif not values:
            self.selected_audio_var.set("")
            self.transcription_turns = []
            self.build_transcription_rows([])
            self.transcription_status_var.set("Nessun audio generato trovato")

    def on_audio_selected(self, _event=None) -> None:
        self.pause_audio(reset=True)
        self.load_selected_audio_transcript()
        self.load_selected_audio_for_player()

    def selected_audio_path(self) -> Path | None:
        name = self.selected_audio_var.get()
        return self.audio_choices.get(name)

    def load_selected_audio_transcript(self) -> None:
        audio_path = self.selected_audio_path()
        if not audio_path:
            return
        turns = read_turns_sidecar(audio_path)
        if not turns and self.turns:
            turns = list(self.turns)
        if not turns:
            turns = parse_dialogue(DEFAULT_DIALOGUE_PATH.read_text(encoding="utf-8"))
        rows = merge_consecutive_turns(turns)
        self.transcription_turns = rows
        self.build_transcription_rows(rows)
        self.transcription_status_var.set(f"Pronto: {audio_path.name} · {len(rows)} battute")

    def load_selected_audio_for_player(self) -> None:
        audio_path = self.selected_audio_path()
        if not audio_path:
            self.current_playback_path = None
            self.audio_duration = 0.0
            self.progress_scale.configure(to=1.0)
            self.progress_var.set(0.0)
            self.audio_time_var.set("00:00 / 00:00")
            return
        playback_path = self.playback_path_for(audio_path)
        self.current_playback_path = playback_path
        self.audio_duration = audio_duration_seconds(playback_path)
        self.playback_offset = 0.0
        self.playback_started_at = 0.0
        self.audio_loaded = False
        self.audio_paused = False
        self.progress_scale.configure(to=max(self.audio_duration, 1.0))
        self.progress_var.set(0.0)
        self.audio_time_var.set(f"00:00 / {format_time(self.audio_duration)}")

    def build_transcription_rows(self, turns: list[Turn]) -> None:
        for child in self.transcribe_rows_frame.winfo_children():
            child.destroy()
        self.transcription_rows = []

        for index, turn in enumerate(turns):
            row = Frame(self.transcribe_rows_frame, pady=3)
            row.pack(fill=X)
            speaker_label = Label(row, text=turn.speaker, width=8, anchor="e")
            speaker_label.pack(side=LEFT, padx=(0, 8))
            entry = Text(row, wrap="none", height=1, padx=5, pady=2, undo=True)
            entry.pack(side=LEFT, fill=X, expand=True)
            entry.tag_configure("normal", foreground="#222222", background="white")
            entry.tag_configure("error", foreground="#b00020", background="#ffd8df")
            entry.tag_configure("missing", foreground="#d00000", background="#ffe8ec")

            entry.bind("<Return>", lambda event, row_index=index: self.submit_transcription_row(row_index))
            entry.bind("<KeyPress>", lambda event, widget=entry: self.clear_row_diff_on_edit(event, widget))
            for widget in (row, speaker_label, entry):
                self.bind_scroll_wheel(widget)
            self.transcription_rows.append({"entry": entry, "turn": turn})

        if self.transcription_rows:
            self.transcription_rows[0]["entry"].focus_set()

    def clear_row_diff_on_edit(self, event, widget: Text):
        if event.keysym == "Return":
            return None
        widget.tag_remove("error", "1.0", END)
        widget.tag_remove("missing", "1.0", END)
        return None

    def row_plain_text(self, widget: Text) -> str:
        return widget.get("1.0", "end-1c").replace("|", "")

    def submit_transcription_row(self, row_index: int):
        if row_index >= len(self.transcription_rows):
            return "break"
        row = self.transcription_rows[row_index]
        entry = row["entry"]
        typed = self.row_plain_text(entry)
        turn = row["turn"]
        segments, correct, errors, remaining = diff_display_segments(turn.text, typed)
        entry.delete("1.0", END)
        for tag, text in segments:
            entry.insert(END, text, tag)
        if errors or remaining:
            self.transcription_status_var.set(f"Riga {row_index + 1}/{len(self.transcription_rows)}: da rivedere")
        else:
            self.transcription_status_var.set(f"Riga {row_index + 1}/{len(self.transcription_rows)}: OK")
        if row_index + 1 < len(self.transcription_rows):
            next_entry = self.transcription_rows[row_index + 1]["entry"]
            next_entry.focus_set()
            next_entry.see("insert")
        return "break"

    def playback_path_for(self, audio_path: Path) -> Path:
        mp3_path = audio_path.with_suffix(".mp3")
        if mp3_path.exists():
            return mp3_path
        return audio_path

    def ensure_mixer(self) -> bool:
        if init_mixer():
            return True
        if not self.mixer_warning_shown:
            self.mixer_warning_shown = True
            self.log("Audio non disponibile: nessun dispositivo di riproduzione trovato.")
            messagebox.showwarning(
                "Audio non disponibile",
                "Non riesco ad aprire un dispositivo audio.\n"
                "Su Linux serve un server audio attivo (PipeWire, PulseAudio o ALSA).\n"
                "Puoi comunque generare e salvare gli MP3.",
            )
        return False

    def current_audio_position(self) -> float:
        if not self.audio_loaded:
            return self.playback_offset
        if self.audio_paused:
            return self.playback_offset
        return min(self.audio_duration, self.playback_offset + (time.monotonic() - self.playback_started_at))

    def play_from(self, offset: float) -> None:
        if not self.current_playback_path:
            return
        if not self.ensure_mixer():
            return
        offset = max(0.0, min(offset, max(self.audio_duration - 0.05, 0.0)))
        pygame.mixer.music.stop()
        pygame.mixer.music.load(str(self.current_playback_path))
        try:
            pygame.mixer.music.play(start=offset)
        except (pygame.error, NotImplementedError):
            # Some SDL_mixer builds cannot seek this codec; start from zero.
            pygame.mixer.music.play()
            offset = 0.0
        self.playback_offset = offset
        self.playback_started_at = time.monotonic()
        self.audio_loaded = True
        self.audio_paused = False

    def play_selected_audio(self) -> None:
        audio_path = self.selected_audio_path()
        if not audio_path:
            messagebox.showwarning("Audio mancante", "Scegli un audio generato.")
            return
        if self.current_playback_path is None:
            self.load_selected_audio_for_player()
        self.play_from(self.playback_offset)

    def stop_audio(self) -> None:
        self.pause_audio()

    def pause_audio(self, reset: bool = False) -> None:
        if pygame.mixer.get_init():
            if self.audio_loaded and not self.audio_paused:
                self.playback_offset = self.current_audio_position()
                pygame.mixer.music.pause()
                self.audio_paused = True
            if reset:
                pygame.mixer.music.stop()
        if reset:
            self.playback_offset = 0.0
            self.audio_loaded = False
            self.audio_paused = False
            if hasattr(self, "progress_var"):
                self.progress_var.set(0.0)

    def on_progress_press(self, _event=None) -> None:
        self.dragging_progress = True

    def on_progress_release(self, _event=None) -> None:
        self.dragging_progress = False
        target = float(self.progress_var.get())
        was_playing = self.audio_loaded and not self.audio_paused
        self.playback_offset = max(0.0, min(target, self.audio_duration))
        if was_playing:
            self.play_from(self.playback_offset)
        else:
            if pygame.mixer.get_init() and self.audio_loaded:
                pygame.mixer.music.stop()
            self.audio_paused = True
            self.audio_loaded = bool(self.current_playback_path)
            self.audio_time_var.set(f"{format_time(self.playback_offset)} / {format_time(self.audio_duration)}")

    def update_audio_progress(self) -> None:
        try:
            if self.current_playback_path and self.audio_duration > 0:
                position = self.current_audio_position()
                if self.audio_loaded and not self.audio_paused and position >= self.audio_duration - 0.1:
                    self.playback_offset = 0.0
                    self.audio_loaded = False
                    self.audio_paused = False
                    position = 0.0
                if not self.dragging_progress:
                    self.updating_progress = True
                    self.progress_var.set(position)
                    self.updating_progress = False
                self.audio_time_var.set(f"{format_time(position)} / {format_time(self.audio_duration)}")
        finally:
            self.root.after(250, self.update_audio_progress)

    def drain_log_queue(self) -> None:
        try:
            while True:
                message = self.log_queue.get_nowait()
                if isinstance(message, tuple) and message and message[0] == "audio_created":
                    created = Path(message[1])
                    self.generated_audio_var.set(f"Ultimo audio: {created.name}")
                    self.refresh_audio_list()
                else:
                    self.log(message)
        except queue.Empty:
            pass
        self.root.after(150, self.drain_log_queue)

    def log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert(END, message + "\n")
        self.log_box.see(END)
        self.log_box.configure(state="disabled")


def main() -> None:
    if "--test-tts" in sys.argv:
        run_cached_key_tts_test()
        return

    root = Tk()
    JapaneseTTSApp(root)
    root.mainloop()


def run_cached_key_tts_test() -> None:
    api_key = normalize_api_key(load_cached_key())
    if not api_key:
        raise SystemExit(f"No cached API key found at {config_path()}")

    print(f"Using cached API key: {masked_key(api_key)}")
    data = request_gemini_dialogue_wav_with_retries(
        api_key,
        [
            Turn("先生", "こんにちは。これは短いテストです。"),
            Turn("学生", "はい、自然な会話として聞こえるか確認します。"),
        ],
        "Kore",
        "Puck",
        0.9,
        attempts=1,
    )
    destination = OUTPUT_DIR / "gemini_tts_smoke_test.wav"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    with wave.open(str(destination), "rb") as wav:
        print(
            "Created",
            destination,
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
            wav.getnframes(),
        )


if __name__ == "__main__":
    main()

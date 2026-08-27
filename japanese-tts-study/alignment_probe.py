from __future__ import annotations

import math
import wave
from pathlib import Path

import japanese_tts_gui as app


def rms_16bit_mono(samples: bytes) -> float:
    if not samples:
        return 0.0
    total = 0
    count = len(samples) // 2
    for i in range(0, len(samples) - 1, 2):
        value = int.from_bytes(samples[i : i + 2], "little", signed=True)
        total += value * value
    return math.sqrt(total / max(count, 1))


def read_wav_mono(path: Path) -> tuple[bytes, int, int, int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        rate = wav.getframerate()
        data = wav.readframes(wav.getnframes())
    if channels != 1 or sample_width != 2:
        raise RuntimeError(f"Expected mono 16-bit WAV, got channels={channels}, width={sample_width}")
    return data, rate, channels, sample_width


def detect_segments(
    data: bytes,
    rate: int,
    frame_ms: int = 20,
    threshold_ratio: float = 0.035,
    min_silence_ms: int = 260,
    merge_gap_ms: int = 170,
    min_segment_ms: int = 250,
) -> list[tuple[float, float]]:
    bytes_per_sample = 2
    frame_samples = max(1, int(rate * frame_ms / 1000))
    frame_bytes = frame_samples * bytes_per_sample
    frames = [data[i : i + frame_bytes] for i in range(0, len(data), frame_bytes)]
    rms_values = [rms_16bit_mono(frame) for frame in frames]
    max_rms = max(rms_values) if rms_values else 0.0
    threshold = max(80.0, max_rms * threshold_ratio)

    voiced = [value >= threshold for value in rms_values]
    raw_segments = []
    start = None
    silence_run = 0
    min_silence_frames = max(1, min_silence_ms // frame_ms)

    for index, is_voiced in enumerate(voiced):
        if is_voiced:
            if start is None:
                start = max(0, index - 1)
            silence_run = 0
        elif start is not None:
            silence_run += 1
            if silence_run >= min_silence_frames:
                end = index - silence_run + 1
                raw_segments.append((start * frame_ms / 1000.0, end * frame_ms / 1000.0))
                start = None
                silence_run = 0
    if start is not None:
        raw_segments.append((start * frame_ms / 1000.0, len(frames) * frame_ms / 1000.0))

    merged: list[tuple[float, float]] = []
    merge_gap = merge_gap_ms / 1000.0
    min_segment = min_segment_ms / 1000.0
    for start_time, end_time in raw_segments:
        if end_time - start_time < min_segment:
            continue
        if merged and start_time - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], end_time)
        else:
            merged.append((start_time, end_time))
    return merged


def main() -> None:
    wav_path = max(Path("audio_output").glob("dialogo_giapponese_*.wav"), key=lambda p: p.stat().st_mtime)
    turns = app.sentence_turns_from_turns(app.parse_dialogue(Path("dialogo_giapponese.txt").read_text(encoding="utf-8")))
    data, rate, _channels, _sample_width = read_wav_mono(wav_path)
    duration = len(data) / (rate * 2)

    print(f"Audio: {wav_path}")
    print(f"Duration: {duration:.2f}s")
    print(f"Script sentences: {len(turns)}")
    print()

    candidates = []
    for threshold_ratio in (0.02, 0.03, 0.04, 0.05):
        for min_silence_ms in (180, 220, 260, 320, 400):
            for merge_gap_ms in (80, 140, 200):
                segments = detect_segments(
                    data,
                    rate,
                    threshold_ratio=threshold_ratio,
                    min_silence_ms=min_silence_ms,
                    merge_gap_ms=merge_gap_ms,
                )
                candidates.append((abs(len(segments) - len(turns)), len(segments), threshold_ratio, min_silence_ms, merge_gap_ms, segments))

    candidates.sort(key=lambda item: (item[0], item[3], item[4]))
    for _diff, count, ratio, silence, gap, segments in candidates[:8]:
        print(f"segments={count:02d} threshold={ratio:.3f} min_silence={silence}ms merge_gap={gap}ms")

    best = candidates[0]
    segments = best[-1]
    print()
    print("Best preview:")
    for index, (segment, turn) in enumerate(zip(segments[:12], turns[:12]), start=1):
        start, end = segment
        print(f"{index:02d}. {start:6.2f}-{end:6.2f}s {turn.speaker}: {turn.text}")
    if len(segments) != len(turns):
        print()
        print(f"Count mismatch: {len(segments)} detected segments vs {len(turns)} script sentences.")


if __name__ == "__main__":
    main()

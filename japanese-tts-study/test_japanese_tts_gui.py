import io
import base64
import queue
import tempfile
import unittest
import wave
from pathlib import Path

import japanese_tts_gui as app


def fake_wav_bytes() -> bytes:
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 1200)
    return data.getvalue()


class DummyApp:
    dialogue_fingerprint = staticmethod(app.JapaneseTTSApp.dialogue_fingerprint)

    def __init__(self) -> None:
        self.log_queue = queue.Queue()


class JapaneseTTSGuiTests(unittest.TestCase):
    def test_gemini_worker_caches_full_dialogue_audio(self) -> None:
        turns = app.parse_dialogue(Path("dialogo_giapponese.txt").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(turns), 20)

        original_output_dir = app.OUTPUT_DIR
        original_request = app.request_gemini_dialogue_wav_with_retries
        calls = []

        with tempfile.TemporaryDirectory() as tmpdir:
            app.OUTPUT_DIR = Path(tmpdir)

            def fake_request(api_key, request_turns, teacher_voice, student_voice, speed):
                calls.append((request_turns, teacher_voice, student_voice, speed))
                return fake_wav_bytes()

            app.request_gemini_dialogue_wav_with_retries = fake_request
            app.JapaneseTTSApp._generate_worker(
                DummyApp(),
                "test-key",
                0.88,
                "Kore",
                "Puck",
                False,
                True,
                turns,
            )
            self.assertEqual(len(calls), 1)

            app.JapaneseTTSApp._generate_worker(
                DummyApp(),
                "test-key",
                0.88,
                "Kore",
                "Puck",
                False,
                True,
                turns,
            )
            self.assertEqual(len(calls), 1)

            cached = list((app.OUTPUT_DIR / "gemini_cache").glob("*.wav"))
            outputs = list(app.OUTPUT_DIR.glob("dialogo_giapponese_*.wav"))
            self.assertEqual(len(cached), 1)
            self.assertEqual(len(outputs), 1)
            with wave.open(str(outputs[0]), "rb") as wav:
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getsampwidth(), 2)
                self.assertEqual(wav.getframerate(), 24000)
                self.assertGreater(wav.getnframes(), 0)

        app.OUTPUT_DIR = original_output_dir
        app.request_gemini_dialogue_wav_with_retries = original_request

    def test_prompt_uses_speaker_labels_without_source_comments(self) -> None:
        text = Path("dialogo_giapponese.txt").read_text(encoding="utf-8")
        prompt = app.build_gemini_prompt(app.parse_dialogue(text), 0.88)
        self.assertIn("Speaker1:", prompt)
        self.assertIn("Speaker2:", prompt)
        self.assertNotIn("Dialogo didattico", prompt)
        self.assertNotIn("Focus grammaticale", prompt)

    def test_finds_audio_in_gemini_steps_content_data(self) -> None:
        encoded = base64.b64encode(b"\x00\x01" * 512).decode("ascii")
        response = {
            "status": "completed",
            "usage": {"output_tokens_by_modality": [{"modality": "audio", "tokens": 12}]},
            "steps": [{"content": [{"data": encoded}]}],
        }
        self.assertEqual(app.find_audio_base64(response), encoded)

    def test_wav_to_mp3_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "sample.wav"
            mp3_path = Path(tmpdir) / "sample.mp3"
            wav_path.write_bytes(fake_wav_bytes())
            app.wav_to_mp3(wav_path, mp3_path)
            self.assertGreater(mp3_path.stat().st_size, 0)

    def test_diff_realigns_after_missing_phrase(self) -> None:
        expected = "今日は、話し方や様子を表す表現を練習しましょう。"
        typed = "今日は話し方や様子を表す表現を話ししましょう"
        ranges, missing_ranges, correct, errors, remaining = app.diff_error_ranges(expected, typed)
        highlighted = "".join(typed[start:end] for start, end in ranges)
        self.assertIn("話", highlighted)
        self.assertIsInstance(missing_ranges, list)
        self.assertLess(errors, 8)
        self.assertGreater(correct, 15)
        self.assertLess(remaining, 8)

    def test_diff_marks_missing_phrase_anchor(self) -> None:
        expected = (
            "今日は、話し方や様子を表す表現を練習しましょう。"
            "まず、「げ」は人の様子を見て、そう感じるときに使います。"
        )
        typed = "今日は話し方や様子を表す表現を練習しましょうまずげ人を感じるときに"
        ranges, missing_ranges, correct, errors, remaining = app.diff_error_ranges(expected, typed)
        self.assertGreater(len(missing_ranges), 0)
        self.assertGreater(errors, len(ranges))
        self.assertGreater(correct, 20)

    def test_diff_ignores_punctuation(self) -> None:
        expected = "まず、「げ」は人の様子を見て、そう感じるときに使います。"
        typed = "まずげは人の様子を見てそう感じるときに使います"
        segments, correct, errors, remaining = app.diff_display_segments(expected, typed)
        rendered = "".join(text for _tag, text in segments)
        self.assertEqual(rendered, typed)
        self.assertEqual(errors, 0)
        self.assertEqual(remaining, 0)
        self.assertEqual(correct, len(app.normalize_for_diff(expected)[0]))

    def test_diff_display_uses_missing_marker_without_revealing_text(self) -> None:
        expected = "今日は、話し方や様子を表す表現を練習しましょう。まず、「げ」は人の様子を見て、そう感じるときに使います。"
        typed = "今日は話し方や様子を表す表現を練習しましょうまずげ人を感じるときに"
        segments, _correct, errors, _remaining = app.diff_display_segments(expected, typed)
        missing_markers = [text for tag, text in segments if tag == "missing"]
        rendered = "".join(text for _tag, text in segments)
        self.assertGreater(len(missing_markers), 0)
        self.assertIn("|", rendered)
        self.assertNotIn("様子を見て", rendered)
        self.assertGreater(errors, 0)

    def test_turns_are_split_into_sentence_practice_rows(self) -> None:
        turns = app.parse_dialogue(Path("dialogo_giapponese.txt").read_text(encoding="utf-8"))
        sentences = app.sentence_turns_from_turns(turns)
        self.assertGreater(len(sentences), len(turns))
        self.assertEqual(sentences[0].speaker, "美咲")
        self.assertEqual(sentences[0].text, "ねえ、今日の田中さん、なんか疲れ気味じゃなかった？")

    def test_transcription_rows_alternate_speakers(self) -> None:
        turns = [
            app.Turn("美咲", "ねえ、聞いてる？"),
            app.Turn("蓮", "聞いてるよ。"),
            app.Turn("蓮", "ちょっと考えてただけ。"),
            app.Turn("美咲", "ならいいけど。"),
        ]
        rows = app.merge_consecutive_turns(turns)
        self.assertEqual([row.speaker for row in rows], ["美咲", "蓮", "美咲"])
        self.assertEqual(rows[1].text, "聞いてるよ。ちょっと考えてただけ。")
        speakers = [row.speaker for row in rows]
        self.assertTrue(all(a != b for a, b in zip(speakers, speakers[1:])))

    def test_multi_sentence_turn_stays_one_row(self) -> None:
        for name in ("dialogo_mono.txt", "dialogo_giapponese.txt"):
            turns = app.parse_dialogue(Path(name).read_text(encoding="utf-8"))
            rows = app.merge_consecutive_turns(turns)
            speakers = [row.speaker for row in rows]
            self.assertTrue(
                all(a != b for a, b in zip(speakers, speakers[1:])),
                f"{name} produced two consecutive rows for one speaker",
            )
            # A turn spanning several sentences must not be split up.
            self.assertLess(len(rows), len(app.sentence_turns_from_turns(turns)))

    def test_first_sentence_accepts_no_punctuation(self) -> None:
        expected = "今日は、話し方や様子を表す表現を練習しましょう。"
        typed = "今日は話し方や様子を表す表現を練習しましょう"
        segments, correct, errors, remaining = app.diff_display_segments(expected, typed)
        self.assertEqual("".join(text for _tag, text in segments), typed)
        self.assertEqual(errors, 0)
        self.assertEqual(remaining, 0)
        self.assertEqual(correct, len(app.normalize_for_diff(expected)[0]))

    def test_natural_dialogue_contains_target_grammar(self) -> None:
        text = Path("dialogo_giapponese.txt").read_text(encoding="utf-8")
        turns = app.parse_dialogue(text)
        counts = {
            name: sum(1 for turn in turns if pattern.search(turn.text))
            for name, pattern in app.GRAMMAR_PATTERNS.items()
        }
        self.assertGreaterEqual(counts["げ"], 3)
        self.assertGreaterEqual(counts["がち"], 3)
        self.assertGreaterEqual(counts["気味"], 2)
        self.assertGreaterEqual(counts["っぽく"], 3)

    def test_mono_dialogue_contains_target_grammar(self) -> None:
        text = Path("dialogo_mono.txt").read_text(encoding="utf-8")
        turns = app.parse_dialogue(text)
        counts = {
            name: sum(1 for turn in turns if pattern.search(turn.text))
            for name, pattern in app.GRAMMAR_PATTERNS.items()
        }
        self.assertGreaterEqual(counts["ものなら"], 4)
        self.assertGreaterEqual(counts["ものだから"], 3)
        self.assertGreaterEqual(counts["ものの"], 3)
        self.assertGreaterEqual(counts["もの(理由)"], 3)
        # The two dialogues stay thematically separate, so the focus line for
        # one never picks up the other's tags.
        self.assertEqual(counts["がち"], 0)
        self.assertEqual(counts["気味"], 0)


if __name__ == "__main__":
    unittest.main()

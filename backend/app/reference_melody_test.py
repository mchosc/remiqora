"""The native reader requires WAV even when the retained source is FLAC."""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import soundfile as sf
from app import db, reference_melody


class MelodyTests(unittest.IsolatedAsyncioTestCase):
    async def test_lossless_source_is_normalized_to_wav_without_overwriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'files' / 'source.flac'
            source.parent.mkdir()
            sf.write(str(source), [0.1] * 4800, 48000, format='FLAC')
            original = source.read_bytes()
            workspace = root / 'work'; workspace.mkdir()
            class Session:
                async def post(self, endpoint, payload):
                    if endpoint.endswith('run'):
                        path = Path(payload['request']['audio'])
                        self_test.assertEqual(sf.info(str(path)).format, 'WAV')
                        self_test.assertNotEqual(path, source)
                        return {'artifacts': [{'id': 'score', 'meta': {'format': 'abc'}, 'payload': base64.b64encode(b'X:1\nK:C\nC D|').decode()}]}
                    return {}
            self_test = self
            @asynccontextmanager
            async def session():
                yield Session()
            with patch.object(db, 'FILES_DIR', root / 'files'), patch.object(reference_melody, 'capability', return_value=reference_melody.ReferenceToolCapability(available=True)), patch.object(reference_melody.native_yue, 'owned_session', side_effect=session):
                result = await reference_melody.prepare_reference_melody(source, workspace=workspace, on_proc=lambda proc: None)
            self.assertIn('K:C', result)
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((workspace / 'melody-input.wav').exists())

"""Workflow regressions run real ffmpeg on synthetic cover art and songs."""

from __future__ import annotations
import asyncio, io, shutil, subprocess, sys, tempfile, unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from unittest.mock import patch
from fastapi import UploadFile
from app.video_process import WorkerIdentity
from app.video_projects import StoredVideoProject
from app.video_contracts import (
    CreateVideoProjectRequest,
    UpdateVideoProjectRequest,
    VideoShotDraft,
    VideoRenderRequest,
    ApproveVideoVariantRequest,
    VideoExportRequest,
    VideoExportSettings,
)


class VideoRenderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app import video_projects as p, video_render as r

        self.p = p
        self.r = r
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.audio = self.root / "song.wav"
        self.image = self.root / "cover.png"
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=5",
                str(self.audio),
            ],
            check=True,
        )
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=704x448",
                "-frames:v",
                "1",
                str(self.image),
            ],
            check=True,
        )
        self.enterContext(patch.object(p, "DATA_DIR", self.root))
        self.enterContext(patch.object(r, "DATA_DIR", self.root))
        self.enterContext(patch.object(r, "LOG_DIR", self.root / "logs"))
        self.enterContext(
            patch.object(
                p.db,
                "get_track",
                return_value={
                    "id": 1,
                    "title": "Song",
                    "audio_path": str(self.audio),
                    "duration_ms": 5000,
                },
            )
        )
        self.project = await p.create(
            CreateVideoProjectRequest(track_id=1, mode="cover", seed=42)
        )
        self.project = await p.upload_reference(
            self.project.id,
            self.project.revision,
            UploadFile(io.BytesIO(self.image.read_bytes()), filename="cover.png"),
        )
        self.project = p.update(
            self.project.id,
            UpdateVideoProjectRequest(
                revision=self.project.revision,
                shots=[
                    VideoShotDraft(
                        id="a" * 32,
                        start_sec=0,
                        seconds=2,
                        prompt="one",
                        reference_id=self.project.references[0].id,
                    ),
                    VideoShotDraft(
                        id="b" * 32,
                        start_sec=2,
                        seconds=2,
                        prompt="two",
                        reference_id=self.project.references[0].id,
                    ),
                ],
            ),
        )

    async def asyncTearDown(self) -> None:
        await self.r.shutdown()

    async def finish(self) -> None:
        task = self.r._tasks.get(self.project.id)
        if task is not None:
            await task
        self.project = self.p.get(self.project.id)

    async def test_analysis_collects_split_json_until_worker_eof(self) -> None:
        from app.video_contracts import VideoRevisionRequest, VideoSongAnalysis
        from app.video_process import WorkerIdentity

        expected = VideoSongAnalysis(
            duration_sec=5, sample_rate=8000, waveform_peaks=[0.25] * 4000
        )
        report = self.root / "split-analysis.json"
        report.write_text(expected.model_dump_json(), encoding="utf-8")
        original_spawn = self.r.spawn_owned
        processes: list[asyncio.subprocess.Process] = []

        async def split_report(
            argv: list[str],
            *,
            receipt_path: Path,
            env: Mapping[str, str] | None = None,
            stdout: int | None = None,
            on_identity: Callable[[WorkerIdentity], None] | None = None,
        ) -> asyncio.subprocess.Process:
            script = (
                "import sys,time;from pathlib import Path;"
                "data=Path(sys.argv[1]).read_bytes();"
                "sys.stdout.buffer.write(data[:17]);sys.stdout.flush();"
                "time.sleep(.05);sys.stdout.buffer.write(data[17:])"
            )
            proc = await original_spawn(
                [sys.executable, "-c", script, str(report)],
                receipt_path=receipt_path,
                env=env,
                stdout=stdout,
                on_identity=on_identity,
            )
            processes.append(proc)
            return proc

        with patch.object(self.r, "spawn_owned", side_effect=split_report):
            result = await asyncio.wait_for(
                self.r.analyze(
                    self.project.id,
                    VideoRevisionRequest(revision=self.project.revision),
                ),
                timeout=5,
            )
        self.assertEqual(result.analysis, expected)
        self.assertEqual(processes[0].returncode, 0)
        self.assertFalse(self.r._analysis_tasks)
        self.assertIsNone(self.p.load(self.project.id).worker)

    async def test_analysis_preserves_safe_worker_errors_and_cleans_up(self) -> None:
        from app.video_contracts import VideoRevisionRequest
        original_spawn = self.r.spawn_owned

        for code in ('analysis_unavailable', 'analysis_timeout', 'invalid_audio',
                     'audio_too_long', 'source_missing', 'untrusted_internal_detail'):
            async def fail_report(
                argv: list[str], *, receipt_path: Path,
                env: Mapping[str, str] | None = None, stdout: int | None = None,
                on_identity: Callable[[WorkerIdentity], None] | None = None,
            ) -> asyncio.subprocess.Process:
                return await original_spawn(
                    [sys.executable, '-c',
                     'import json,sys;print(json.dumps({"error_code":sys.argv[1]}));sys.exit(2)', code],
                    receipt_path=receipt_path, env=env, stdout=stdout,
                )

            with self.subTest(code=code), patch.object(self.r, 'spawn_owned', side_effect=fail_report):
                with self.assertRaises(self.p.VideoProjectError) as failure:
                    await self.r.analyze(self.project.id, VideoRevisionRequest(revision=self.project.revision))
                self.assertEqual(failure.exception.code, code if code != 'untrusted_internal_detail' else 'analysis_failed')
            self.assertFalse(self.r._analysis_tasks)
            self.assertIsNone(self.p.load(self.project.id).worker)

    def test_readiness_checks_analysis_dependency_and_decode_tools(self) -> None:
        with patch.dict(sys.modules, {'numpy': None}):
            result = self.r.readiness()
        self.assertFalse(result.analysis_ready)
        self.assertIn('analysis_unavailable', result.warnings)

        from app.video_media import VideoMediaError
        with patch.object(self.r, 'tool', side_effect=VideoMediaError('ffmpeg_missing')):
            result = self.r.readiness()
        self.assertFalse(result.analysis_ready)


    async def test_preview_approval_and_portrait_export_keep_full_short_tail(
        self,
    ) -> None:
        self.project = await self.r.start(
            self.project.id,
            VideoRenderRequest(revision=self.project.revision),
            operation="preview",
        )
        await self.finish()
        self.assertEqual(self.project.job.status, "ready")
        for shot in self.project.shots:
            self.project = await self.r.approve(
                self.project.id,
                shot.id,
                ApproveVideoVariantRequest(
                    revision=self.project.revision, variant_id=shot.variants[0].id
                ),
            )
        self.project = await self.r.export(
            self.project.id,
            VideoExportRequest(
                revision=self.project.revision,
                settings=VideoExportSettings(aspect="portrait"),
            ),
        )
        await self.finish()
        from app.video_media import validate_media

        await validate_media(
            self.r.output_file(self.project.id), 5, (252, 448), require_audio=True
        )
        self.assertTrue(self.project.file_url)

    async def test_changed_prompt_cannot_approve_previous_variant(self) -> None:
        self.project = await self.r.start(
            self.project.id,
            VideoRenderRequest(revision=self.project.revision, shot_ids=["a" * 32]),
            operation="preview",
        )
        await self.finish()
        variant = self.project.shots[0].variants[0]
        drafts = [
            VideoShotDraft.model_validate(
                shot.model_dump(exclude={"variants", "approved_variant_id"})
            )
            for shot in self.project.shots
        ]
        drafts[0].prompt = "new prompt"
        self.project = self.p.update(
            self.project.id,
            UpdateVideoProjectRequest(revision=self.project.revision, shots=drafts),
        )
        with self.assertRaises(self.p.VideoProjectError) as error:
            await self.r.approve(
                self.project.id,
                "a" * 32,
                ApproveVideoVariantRequest(
                    revision=self.project.revision, variant_id=variant.id
                ),
            )
        self.assertEqual(error.exception.code, "stale_variant")
        self.assertEqual(len(self.project.shots[0].variants), 1)

    async def test_repeat_preview_reuses_same_seed_artifact(self) -> None:
        self.project = await self.r.start(
            self.project.id,
            VideoRenderRequest(revision=self.project.revision, shot_ids=["a" * 32]),
            operation="preview",
        )
        await self.finish()
        variant = self.project.shots[0].variants[0]
        self.project = await self.r.start(
            self.project.id,
            VideoRenderRequest(revision=self.project.revision, shot_ids=["a" * 32]),
            operation="preview",
        )
        await self.finish()
        self.assertEqual(len(self.project.shots[0].variants), 1)
        self.assertEqual(self.project.shots[0].variants[0].id, variant.id)

    async def test_preview_uses_explicit_shot_seed_without_adding_project_default(
        self,
    ) -> None:
        self.project = await self.r.start(
            self.project.id,
            VideoRenderRequest(revision=self.project.revision, shot_ids=["a" * 32]),
            operation="preview",
        )
        await self.finish()
        self.assertEqual(
            self.project.shots[0].variants[0].seed, self.project.shots[0].seed
        )

    async def test_default_seed_change_preserves_approved_output_and_reuses_clips(self) -> None:
        self.project = await self.r.start(self.project.id, VideoRenderRequest(
            revision=self.project.revision), operation='render')
        await self.finish()
        before = self.project.model_copy(deep=True)
        self.project = self.p.update(self.project.id, UpdateVideoProjectRequest(
            revision=self.project.revision, seed=100))
        self.assertEqual(self.project.file_url, before.file_url)
        self.assertEqual([s.approved_variant_id for s in self.project.shots],
                         [s.approved_variant_id for s in before.shots])
        self.project = await self.r.start(self.project.id, VideoRenderRequest(
            revision=self.project.revision), operation='preview')
        await self.finish()
        self.assertEqual([s.variants for s in self.project.shots], [s.variants for s in before.shots])

    async def test_analysis_refreshes_only_unlocked_prompts_at_original_source_time(
        self,
    ) -> None:
        document = self.p.load(self.project.id)
        document.project.shots[0].locked = True
        for shot in document.project.shots:
            shot.approved_variant_id = "e" * 32
        document.project.file_url = "/published"
        self.p.save(document)
        before = document.project.model_copy(deep=True)
        from app.video_contracts import VideoRevisionRequest

        plan = {
            "shots": [
                {"start_sec": 0, "seconds": 2, "prompt": "measured intro"},
                {"start_sec": 2, "seconds": 2, "prompt": "measured chorus"},
            ]
        }
        with patch("app.video_jobs.propose_plan", return_value=plan):
            refreshed = await self.r.analyze(
                self.project.id, VideoRevisionRequest(revision=before.revision)
            )
        self.assertEqual(refreshed.shots[0], before.shots[0])
        self.assertEqual(refreshed.shots[1].prompt, "measured chorus")
        self.assertIsNone(refreshed.shots[1].approved_variant_id)
        self.assertFalse(refreshed.file_url)
        for field in (
            "id",
            "start_sec",
            "seconds",
            "seed",
            "reference_id",
            "reference_strength",
        ):
            self.assertEqual(
                getattr(refreshed.shots[1], field), getattr(before.shots[1], field)
            )

    async def test_analysis_new_shots_use_project_seed_defaults(self) -> None:
        from app.video_contracts import VideoRevisionRequest

        self.project = self.p.update(
            self.project.id,
            UpdateVideoProjectRequest(
                revision=self.project.revision, shots=[], seed=2147483647
            ),
        )
        plan = {
            "shots": [
                {"start_sec": 0, "seconds": 2, "prompt": "intro"},
                {"start_sec": 2, "seconds": 2, "prompt": "chorus"},
            ]
        }
        with patch("app.video_jobs.propose_plan", return_value=plan):
            refreshed = await self.r.analyze(
                self.project.id, VideoRevisionRequest(revision=self.project.revision)
            )
        self.assertEqual([shot.seed for shot in refreshed.shots], [2147483647, 0])

    async def test_poster_leaf_symlink_cannot_escape_serving_root(self) -> None:
        from app.video_contracts import VideoVariant

        document = self.p.load(self.project.id)
        shot = document.project.shots[0]
        variant = VideoVariant(id="e" * 32, seed=0, status="ready", created_at="")
        shot.variants.append(variant)
        clip = self.r.variant_path(self.project.id, shot.id, variant.id)
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"clip")
        outside = self.root / "secret.png"
        outside.write_bytes(b"secret")
        clip.with_suffix(".png").symlink_to(outside)
        document.published_file = str(
            clip.relative_to(self.p.project_dir(self.project.id))
        )
        self.p.save(document)
        with self.assertRaises(self.p.VideoProjectError):
            self.r.variant_file(self.project.id, shot.id, variant.id, poster=True)
        with self.assertRaises(self.p.VideoProjectError):
            self.r.output_file(self.project.id, poster=True)

    async def test_delete_drains_analysis_before_removing_project(self) -> None:
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def measure():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        task = asyncio.create_task(measure())
        self.r._analysis_tasks[self.project.id] = task
        await started.wait()
        try:
            await self.r.delete(self.project.id)
            self.assertTrue(cleaned.is_set())
            self.assertNotIn(self.project.id, self.r._analysis_tasks)
            self.assertFalse(self.p.project_dir(self.project.id).exists())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.r._analysis_tasks.pop(self.project.id, None)

    async def test_unknown_worker_remains_blocked_across_repeated_recovery(
        self,
    ) -> None:
        from app.video_contracts import VideoProjectJob
        from app.video_process import WorkerIdentity
        from unittest.mock import AsyncMock

        document = self.p.load(self.project.id)
        document.worker = WorkerIdentity(pid=123, token="e" * 32, receipt="worker.json")
        document.project.job = VideoProjectJob(
            id="d" * 32, operation="preview", status="running"
        )
        self.p.save(document)
        try:
            with patch.object(
                self.r, "terminate_verified", new=AsyncMock(return_value=False)
            ) as verify:
                await self.r.recover()
                self.r._unverified.clear()
                await self.r.recover()
                self.assertEqual(verify.await_count, 2)
                self.assertTrue(self.r.work_busy())
                with self.assertRaises(self.p.VideoProjectError):
                    await self.r.delete(self.project.id)
                from app.video_contracts import VideoRevisionRequest

                with self.assertRaises(self.p.VideoProjectError):
                    await self.r.analyze(
                        self.project.id,
                        VideoRevisionRequest(
                            revision=self.p.get(self.project.id).revision
                        ),
                    )
                self.assertEqual(self.p.load(self.project.id).worker.token, "e" * 32)
        finally:
            self.r._unverified.discard(self.project.id)

    async def test_resume_keeps_requested_export_settings_after_failure(self) -> None:
        from app.video_contracts import VideoRevisionRequest
        from unittest.mock import AsyncMock

        document = self.p.load(self.project.id)
        for shot in document.project.shots:
            shot.approved_variant_id = "e" * 32
        self.p.save(document)
        with patch.object(self.r, "_run", new=AsyncMock()) as run:
            target = VideoExportSettings(aspect="portrait", quality="high")
            self.project = await self.r.export(
                self.project.id,
                VideoExportRequest(revision=self.project.revision, settings=target),
            )
            await self.r._tasks.pop(self.project.id)
            self.r._finish(self.project.id, "failed", "processing_failed")
            self.project = self.p.get(self.project.id)
            await self.r.resume(
                self.project.id, VideoRevisionRequest(revision=self.project.revision)
            )
            await self.r._tasks.pop(self.project.id)
            self.assertEqual(run.await_args.args[3], target)

    async def test_parent_shutdown_cancels_queued_project_before_yielding(self) -> None:
        from app import video_jobs

        started = []

        async def worker():
            started.append(True)

        task = asyncio.create_task(worker())
        self.r._tasks[self.project.id] = task
        original = video_jobs._current
        from app.video_jobs import VideoJob

        job = VideoJob(
            id="c" * 32,
            track_id=1,
            title="Song",
            prompt="x",
            seconds=2,
            start_sec=0,
            frames=49,
            seed=0,
            created_at="",
        )

        async def delayed_cancel(_):
            await asyncio.sleep(0)

        video_jobs._current = job
        try:
            with patch.object(video_jobs, "_cancel_job", side_effect=delayed_cancel):
                await video_jobs.shutdown()
            self.assertEqual(started, [])
        finally:
            video_jobs._current = original
            await asyncio.gather(task, return_exceptions=True)

    async def test_legacy_ffmpeg_worker_keeps_parent_liveness_pipe_open(self) -> None:
        from app import video_jobs

        job = video_jobs.VideoJob(
            id="c" * 32,
            track_id=1,
            title="Song",
            prompt="x",
            seconds=2,
            start_sec=0,
            frames=49,
            seed=0,
            created_at="",
        )
        with patch.object(video_jobs, "DATA_DIR", self.root):
            output = video_jobs._video_dir(job.id) / "excerpt.wav"
            output.parent.mkdir(parents=True)
            await video_jobs._ffmpeg(
                ["-v", "error", "-i", str(self.audio), "-t", "2", str(output)],
                job.slot,
                job,
                30,
            )
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 1000)

    async def test_held_native_lease_refuses_generated_work_even_when_upstream_idle(
        self,
    ) -> None:
        from app.resource_admission import reserve_native
        from app.video_engine import EngineReadiness
        from unittest.mock import AsyncMock

        self.project = self.p.update(
            self.project.id,
            UpdateVideoProjectRequest(revision=self.project.revision, mode="generated"),
        )
        lease = await reserve_native(lambda: False)
        try:
            ready = EngineReadiness(
                "ltx23",
                True,
                True,
                "fingerprint",
                "revision",
                "text",
                0,
                0,
                100,
                (),
                (),
            )
            with (
                patch.object(self.r, "inspect_readiness", return_value=ready),
                patch(
                    "app.work_busy.other_work_busy", new=AsyncMock(return_value=False)
                ),
                patch("app.ace_jobs.work_busy", return_value=False),
            ):
                with self.assertRaises(self.p.VideoProjectError) as error:
                    await self.r.start(
                        self.project.id,
                        VideoRenderRequest(revision=self.project.revision),
                        operation="preview",
                    )
                self.assertEqual(error.exception.code, "busy")
        finally:
            await lease.release()

    async def test_generated_export_does_not_reserve_or_wait_for_accelerator(self) -> None:
        from app.resource_admission import reserve_native
        from app.video_engine import EngineReadiness
        from unittest.mock import AsyncMock

        self.project = self.p.update(self.project.id, UpdateVideoProjectRequest(
            revision=self.project.revision, mode='generated'))

        def approved(saved: StoredVideoProject) -> None:
            for shot in saved.project.shots:
                shot.approved_variant_id = 'c' * 32

        self.p.mutate(self.project.id, approved, bump=False)
        ready = EngineReadiness('ltx23', True, True, 'fingerprint', 'revision', 'text', 0, 0, 100, (), ())
        lease = await reserve_native(lambda: False)
        try:
            with patch.object(self.r, 'inspect_readiness', return_value=ready), \
                 patch.object(self.r, '_engine_fingerprint', new=AsyncMock(return_value='verified')), \
                 patch.object(self.r, '_assemble', new=AsyncMock()) as assemble, \
                 patch('app.video_jobs.work_busy', return_value=True), \
                 patch('app.work_busy.other_work_busy', new=AsyncMock(return_value=True)) as busy, \
                 patch.object(self.r.sys, 'platform', 'darwin'), \
                 patch.object(self.r.platform, 'machine', return_value='arm64'):
                async with self.r.gpu_lock:
                    self.project = await self.r.export(self.project.id, VideoExportRequest(
                        revision=self.project.revision, settings=VideoExportSettings()))
                    self.assertFalse(self.r.work_busy())
                    await asyncio.wait_for(self.finish(), timeout=5)
                assemble.assert_awaited_once()
                busy.assert_not_awaited()
                self.assertEqual(self.project.job.status, 'ready')
        finally:
            await lease.release()

    async def test_missing_decode_or_image_tools_refuse_before_job_reservation(self) -> None:
        from app.video_contracts import VideoOverlay
        from app.video_media import VideoMediaError
        self.project = self.p.update(self.project.id, UpdateVideoProjectRequest(
            revision=self.project.revision, overlays=[VideoOverlay(
                id='d' * 32, text='Title', start_sec=0, end_sec=2)]))
        for expected, manager in (
            ('ffmpeg_missing', patch.object(self.r, 'tool', side_effect=VideoMediaError('ffmpeg_missing'))),
            ('image_tools_unavailable', patch.dict(sys.modules, {'PIL': None})),
        ):
            with self.subTest(code=expected), manager:
                with self.assertRaises(self.p.VideoProjectError) as failure:
                    await self.r.start(self.project.id, VideoRenderRequest(revision=self.project.revision), operation='render')
                self.assertEqual(failure.exception.code, expected)
            self.assertIsNone(self.p.get(self.project.id).job)
            self.assertNotIn(self.project.id, self.r._tasks)

    async def test_oversized_text_refuses_before_shot_generation(self) -> None:
        from app.video_contracts import VideoOverlay
        from unittest.mock import AsyncMock
        self.project = self.p.update(self.project.id, UpdateVideoProjectRequest(
            revision=self.project.revision, overlays=[VideoOverlay(
                id='d' * 32, text='An oversized title ' * 10, font_size=96, start_sec=0, end_sec=2)],
            export_settings=VideoExportSettings(aspect='portrait')))
        with patch.object(self.r, '_generate', new=AsyncMock()) as generate:
            with self.assertRaises(self.p.VideoProjectError) as failure:
                await self.r.start(self.project.id, VideoRenderRequest(revision=self.project.revision), operation='render')
            self.assertEqual(failure.exception.code, 'overlay_text_too_large')
            generate.assert_not_awaited()
        self.assertIsNone(self.p.get(self.project.id).job)

    async def test_cpu_work_can_overlap_gpu_work_but_not_another_cpu_job(self) -> None:
        from unittest.mock import AsyncMock
        other_id = 'f' * 32

        async def waiting() -> None:
            await asyncio.Event().wait()

        for is_gpu in (False, True):
            task = asyncio.create_task(waiting())
            self.r._tasks[other_id] = task
            if is_gpu:
                self.r._gpu_projects.add(other_id)
            try:
                with self.subTest(gpu=is_gpu), patch.object(self.r, '_run', new=AsyncMock()):
                    if is_gpu:
                        self.project = await self.r.start(self.project.id,
                            VideoRenderRequest(revision=self.project.revision), operation='preview')
                        own = self.r._tasks[self.project.id]
                        await own
                        self.r._tasks.pop(self.project.id)
                    else:
                        with self.assertRaises(self.p.VideoProjectError) as failure:
                            await self.r.start(self.project.id,
                                VideoRenderRequest(revision=self.project.revision), operation='preview')
                        self.assertEqual(failure.exception.code, 'busy')
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self.r._tasks.pop(other_id, None)
                self.r._gpu_projects.discard(other_id)

    async def test_unknown_legacy_worker_blocks_cpu_admission(self) -> None:
        with patch('app.video_jobs.workers_unverified', return_value=True):
            with self.assertRaises(self.p.VideoProjectError) as failure:
                await self.r.start(self.project.id, VideoRenderRequest(revision=self.project.revision), operation='preview')
            self.assertEqual(failure.exception.code, 'busy')

    async def test_cancelling_cancel_request_still_releases_queued_registry(
        self,
    ) -> None:
        from app.video_contracts import VideoProjectJob

        document = self.p.load(self.project.id)
        document.project.job = VideoProjectJob(
            id="c" * 32, operation="preview", status="running"
        )
        self.p.save(document)
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()

        async def worker() -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

        task = asyncio.create_task(worker())
        self.r._tasks[self.project.id] = task
        self.r._gpu_projects.add(self.project.id)
        await entered.wait()
        controller = asyncio.create_task(self.r.cancel(self.project.id))
        await cleaning.wait()
        controller.cancel()
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(controller, return_exceptions=True)
        self.assertNotIn(self.project.id, self.r._tasks)
        self.assertNotIn(self.project.id, self.r._gpu_projects)

    async def test_tail_repeats_last_approved_frame(self) -> None:
        self.project = await self.r.start(
            self.project.id,
            VideoRenderRequest(revision=self.project.revision),
            operation="render",
        )
        await self.finish()
        frame = subprocess.check_output(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-v",
                "error",
                "-ss",
                "4.5",
                "-i",
                str(self.r.output_file(self.project.id)),
                "-frames:v",
                "1",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-",
            ]
        )
        self.assertGreater(
            sum(frame[2::3]) / len(frame[2::3]),
            100,
            "Tail flashed black instead of holding approved blue frame",
        )

    async def test_visualizer_square_export_applies_only_timed_overlay(self) -> None:
        from app.video_contracts import VideoOverlay

        self.project = self.p.update(
            self.project.id,
            UpdateVideoProjectRequest(
                revision=self.project.revision,
                mode="visualizer",
                overlays=[
                    VideoOverlay(
                        id="d" * 32,
                        text="Hello",
                        position="top",
                        start_sec=0,
                        end_sec=1,
                    )
                ],
                export_settings=VideoExportSettings(aspect="square"),
            ),
        )
        self.project = await self.r.start(
            self.project.id,
            VideoRenderRequest(revision=self.project.revision),
            operation="render",
        )
        await self.finish()
        self.assertEqual(self.project.job.status, "ready")
        from app.video_media import validate_media

        output = self.r.output_file(self.project.id)
        await validate_media(output, 5, (448, 448), require_audio=True)

        def top_red(seconds: str) -> float:
            frame = subprocess.check_output(
                [
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    seconds,
                    "-i",
                    str(output),
                    "-frames:v",
                    "1",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    "-",
                ]
            )
            top = frame[: 448 * 120 * 3]
            return sum(top[0::3]) / len(top[0::3])

        self.assertGreater(top_red("0.5"), top_red("2.5") + 1)

    async def test_resume_preserves_completed_shot_and_failed_variant_history(
        self,
    ) -> None:
        original = self.r._cpu_shot
        failed = False

        async def generate(project, shot, seed, source, dest, variant_id) -> None:
            nonlocal failed
            if shot.id == "b" * 32 and not failed:
                failed = True
                raise self.p.VideoProjectError("generate_failed")
            await original(project, shot, seed, source, dest, variant_id)

        with patch.object(self.r, "_cpu_shot", side_effect=generate):
            self.project = await self.r.start(
                self.project.id,
                VideoRenderRequest(revision=self.project.revision),
                operation="render",
            )
            await self.finish()
        self.assertEqual(self.project.job.status, "failed")
        completed = self.project.shots[0].variants[0].id
        failed_id = self.project.shots[1].variants[0].id
        from app.video_contracts import VideoRevisionRequest

        self.project = await self.r.resume(
            self.project.id, VideoRevisionRequest(revision=self.project.revision)
        )
        await self.finish()
        self.assertEqual(self.project.job.status, "ready")
        self.assertEqual(self.project.shots[0].variants[0].id, completed)
        self.assertEqual(len(self.project.shots[0].variants), 1)
        self.assertEqual(self.project.shots[1].variants[0].id, failed_id)
        self.assertEqual(
            [item.status for item in self.project.shots[1].variants],
            ["failed", "ready"],
        )

    async def test_recovery_adopts_valid_output_after_atomic_rename(self) -> None:
        self.project = await self.r.start(
            self.project.id,
            VideoRenderRequest(revision=self.project.revision),
            operation="render",
        )
        await self.finish()
        from app.video_projects import PendingExport

        document = self.p.load(self.project.id)
        path = self.r.output_file(self.project.id)
        document.pending_export = PendingExport(
            path=str(path.relative_to(self.p.project_dir(self.project.id))),
            duration_sec=5,
            width=704,
            height=396,
            fingerprint="known",
            project_revision=document.project.revision,
            output_sha256=self.p.file_hash(path),
        )
        document.published_file = ""
        document.project.file_url = ""
        document.project.job.status = "running"
        self.p.save(document)
        await self.r.recover()
        recovered = self.p.get(self.project.id)
        self.assertEqual(recovered.job.status, "ready")
        self.assertTrue(recovered.file_url)
        self.assertTrue(self.r.output_file(self.project.id).is_file())

    async def test_recovery_never_adopts_replaced_valid_media(self) -> None:
        self.project = await self.r.start(
            self.project.id,
            VideoRenderRequest(revision=self.project.revision),
            operation="render",
        )
        await self.finish()
        from app.video_projects import PendingExport

        document = self.p.load(self.project.id)
        path = self.r.output_file(self.project.id)
        document.pending_export = PendingExport(
            path=str(path.relative_to(self.p.project_dir(self.project.id))),
            duration_sec=5,
            width=704,
            height=396,
            fingerprint="known",
            project_revision=document.project.revision,
            output_sha256="0" * 64,
        )
        document.published_file = ""
        document.project.file_url = ""
        document.project.job.status = "running"
        self.p.save(document)
        await self.r.recover()
        self.assertEqual(self.p.get(self.project.id).job.status, "failed")
        self.assertFalse(self.p.get(self.project.id).file_url)

    async def test_recovery_does_not_publish_export_after_project_was_edited(
        self,
    ) -> None:
        self.project = await self.r.start(
            self.project.id,
            VideoRenderRequest(revision=self.project.revision),
            operation="render",
        )
        await self.finish()
        from app.video_projects import PendingExport

        document = self.p.load(self.project.id)
        path = self.r.output_file(self.project.id)
        document.pending_export = PendingExport(
            path=str(path.relative_to(self.p.project_dir(self.project.id))),
            duration_sec=5,
            width=704,
            height=396,
            fingerprint="known",
            output_sha256=self.p.file_hash(path),
            project_revision=document.project.revision,
        )
        document.published_file = ""
        document.project.file_url = ""
        document.project.job.status = "failed"
        self.p.save(document)
        self.p.update(
            self.project.id,
            UpdateVideoProjectRequest(
                revision=document.project.revision, direction="New direction"
            ),
        )
        await self.r.recover()
        self.assertFalse(self.p.get(self.project.id).file_url)

    async def test_non_frame_aligned_song_end_keeps_last_audio_samples(self) -> None:
        subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=5.02",
                str(self.audio),
            ],
            check=True,
        )
        self.project = await self.p.create(
            CreateVideoProjectRequest(track_id=1, mode="cover")
        )
        self.project = await self.p.upload_reference(
            self.project.id,
            self.project.revision,
            UploadFile(io.BytesIO(self.image.read_bytes()), filename="cover.png"),
        )
        self.project = self.p.update(
            self.project.id,
            UpdateVideoProjectRequest(
                revision=self.project.revision,
                shots=[
                    VideoShotDraft(
                        id="a" * 32,
                        start_sec=0,
                        seconds=4,
                        prompt="one",
                        reference_id=self.project.references[0].id,
                    )
                ],
            ),
        )
        self.project = await self.r.start(
            self.project.id,
            VideoRenderRequest(revision=self.project.revision),
            operation="render",
        )
        await self.finish()
        from app.video_media import probe_media

        info = await probe_media(self.r.output_file(self.project.id))
        self.assertGreaterEqual(info.audio_duration, 5.015)

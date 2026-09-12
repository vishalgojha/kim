import asyncio
import logging
import math
import os
import platform
import shutil
import struct
from typing import Any, Callable, Optional

log = logging.getLogger("aurora.audio")


class AudioCapture:
    """Mic capture via parec -> raw s16le @16kHz mono, pushed to a callback."""

    def __init__(self, rate: int = 16000, channels: int = 1, device: str = ""):
        self.rate = rate
        self.channels = channels
        self.device = device
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._task: Optional[asyncio.Task] = None
        self._stream = None
        self._loop = None

    async def start(self, input_callback: Callable[[bytes], Any]) -> None:
        if self._proc or self._stream:
            return
        if platform.system() != "Linux" or not shutil.which("parec"):
            await self._start_sounddevice(input_callback)
            return
        cmd = [
            "parec",
            "--format=s16le",
            f"--rate={self.rate}",
            f"--channels={self.channels}",
            "--raw",
        ]
        if self.device:
            cmd += ["--device", self.device]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        chunk = self.rate * self.channels * 2 // 4  # 250 ms
        log.info("mic capture started (%s, %dHz, %dch)", self.device or "default", self.rate, self.channels)

        async def pump() -> None:
            assert self._proc and self._proc.stdout
            while True:
                data = await self._proc.stdout.read(chunk)
                if not data:
                    break
                await input_callback(data)

        self._task = asyncio.create_task(pump())

    async def _start_sounddevice(self, input_callback: Callable[[bytes], Any]) -> None:
        try:
            import sounddevice as sd
        except ImportError as e:
            raise RuntimeError("sounddevice is required for microphone capture on this system") from e
        self._loop = asyncio.get_running_loop()
        blocksize = self.rate * 250 // 1000

        def callback(indata, _frames, _time, _status):
            if self._loop and not self._loop.is_closed():
                asyncio.run_coroutine_threadsafe(input_callback(bytes(indata)), self._loop)

        self._stream = sd.RawInputStream(
            samplerate=self.rate,
            channels=self.channels,
            dtype="int16",
            blocksize=blocksize,
            device=self.device or None,
            callback=callback,
        )
        self._stream.start()
        log.info("mic capture started with sounddevice (%s, %dHz, %dch)", self.device or "default", self.rate, self.channels)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._proc:
            try:
                self._proc.terminate()
                await self._proc.wait()
            except Exception:
                pass
            self._proc = None
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class AudioOutput:
    """Playback of raw s16le PCM through ffmpeg -> PulseAudio/PipeWire sink."""

    def __init__(self, rate: int = 16000, channels: int = 1, device: str = "", gain: float = 1.0):
        self.rate = rate
        self.channels = channels
        self.device = device
        self.gain = float(gain) if float(gain) > 0 else 1.0
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._stream = None

    @property
    def _native_audio(self) -> bool:
        return platform.system() != "Linux"

    def _cmd(self) -> list[str]:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(self.rate),
            "-ac",
            str(self.channels),
            "-i",
            "pipe:0",
            "-af",
            f"volume={self.gain}",
            "-f",
            "pulse",
        ]
        cmd.append(self.device or "default")
        return cmd

    async def _ensure(self) -> None:
        if self._native_audio:
            if self._stream is not None:
                return
            try:
                import sounddevice as sd
            except ImportError as e:
                raise RuntimeError("sounddevice is required for audio playback on this system") from e
            self._stream = sd.RawOutputStream(
                samplerate=self.rate,
                channels=self.channels,
                dtype="int16",
                device=self.device or None,
            )
            self._stream.start()
            return
        if self._proc is None or self._proc.returncode is not None:
            self._proc = await asyncio.create_subprocess_exec(
                *self._cmd(), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )

    async def write(self, pcm: bytes) -> None:
        if not pcm:
            return
        await self._ensure()
        if self._native_audio:
            await asyncio.to_thread(self._stream.write, pcm)
            return
        try:
            assert self._proc and self._proc.stdin
            self._proc.stdin.write(pcm)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            self._proc = None

    async def play_tone(self, notes: list[tuple[float, float]], volume: float = 0.12) -> None:
        """Play a short local UI tone without using the cloud voice."""
        if not notes:
            return
        sample_rate = self.rate
        pcm = bytearray()
        for frequency, duration in notes:
            count = max(1, int(sample_rate * duration))
            for index in range(count):
                # Tiny attack/release ramps keep the sound clean and click-free.
                edge = min(index, count - index - 1) / max(1, int(sample_rate * 0.012))
                envelope = min(1.0, max(0.0, edge))
                sample = math.sin(2 * math.pi * frequency * index / sample_rate)
                pcm.extend(struct.pack("<h", int(32767 * volume * envelope * sample)))
        await self.write(bytes(pcm))

    async def interrupt(self) -> None:
        if self._stream:
            stream, self._stream = self._stream, None
            await asyncio.to_thread(stream.stop)
            await asyncio.to_thread(stream.close)
        proc, self._proc = self._proc, None
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass

    async def play_mp3(self, path, busy_wait: bool = True) -> None:
        """Play an MP3 end-to-end via ffmpeg (used for proactive alerts)."""
        if self._native_audio:
            try:
                import sounddevice as sd
            except ImportError as e:
                raise RuntimeError("sounddevice is required for audio playback on this system") from e
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
                "-f", "s16le", "-ar", str(self.rate), "-ac", str(self.channels), "pipe:1",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stream = sd.RawOutputStream(
                samplerate=self.rate, channels=self.channels, dtype="int16", device=self.device or None
            )
            stream.start()
            try:
                while proc.stdout:
                    chunk = await proc.stdout.read(self.rate * self.channels * 2 // 4)
                    if not chunk:
                        break
                    await asyncio.to_thread(stream.write, chunk)
                await proc.wait()
            finally:
                stream.stop()
                stream.close()
            return
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-af",
            f"volume={self.gain}",
            "-f",
            "pulse",
        ]
        cmd.append(self.device or "default")
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        if busy_wait:
            await proc.wait()

    async def close(self) -> None:
        await self.interrupt()

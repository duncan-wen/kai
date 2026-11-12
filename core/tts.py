"""TTS 模組 — edge-tts（雲端）或 kokoro-onnx（本地）生成音訊。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from loguru import logger

_PLAYERS_MP3 = ["mpg123", "mpv", "ffplay", "mplayer"]
_PLAYERS_WAV = ["paplay", "mpg123", "mpv", "ffplay", "mplayer"]

# kokoro 模型單例（第一次呼叫時載入，之後重用）
_kokoro_model: Any = None

_current_proc: subprocess.Popen | None = None  # type: ignore[type-arg]
_proc_lock = threading.Lock()

_tts_loop: asyncio.AbstractEventLoop | None = None
_tts_loop_lock = threading.Lock()


def get_tts_loop() -> asyncio.AbstractEventLoop:
    """取得（或建立）持久的 TTS 事件迴圈，整個程序生命週期只建立一次。"""
    global _tts_loop
    with _tts_loop_lock:
        if _tts_loop is None or _tts_loop.is_closed():
            loop = asyncio.new_event_loop()
            t = threading.Thread(target=loop.run_forever, daemon=True, name="tts-worker")
            t.start()
            _tts_loop = loop
    return _tts_loop


def cancel_current() -> None:
    """終止當前正在播放的音訊（如有）。可從任何執行緒呼叫。"""
    with _proc_lock:
        proc = _current_proc
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            proc.kill()  # SIGTERM 無效時強制 SIGKILL


def _detect_player(ext: str = "") -> str | None:
    """依副檔名選最輕量的播放器。.wav/.ogg 優先 paplay；.mp3 優先 mpg123。"""
    candidates = _PLAYERS_WAV if ext in (".wav", ".ogg", ".flac") else _PLAYERS_MP3
    for p in candidates:
        if shutil.which(p):
            return p
    return None


def _player_cmd(player: str, path: str) -> list[str] | None:
    match player:
        case "mpv":
            return ["mpv", "--no-terminal", "--really-quiet", path]
        case "ffplay":
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                    "-analyzeduration", "0", "-probesize", "32", path]
        case "mplayer":
            return ["mplayer", "-really-quiet", path]
        case "mpg123":
            return ["mpg123", "-q", path]
        case "paplay":
            return ["paplay", path]
        case "powershell":
            return None  # 交給下方 PowerShell 分支處理
        case _:
            return [player, path]


def _play_file_sync(path: str, player: str = "auto") -> None:
    """播放音訊（同步）。TTS 執行緒專用，不觸發 asyncio ThreadedChildWatcher。"""
    use_powershell = player == "powershell"
    cmd: list[str] = []

    if not use_powershell:
        ext = Path(path).suffix.lower()
        resolved = player if player != "auto" else _detect_player(ext)
        if resolved:
            cmd = _player_cmd(resolved, path) or []  # type: ignore[assignment]
        else:
            use_powershell = True

    if use_powershell:
        try:
            result = subprocess.run(
                ["wslpath", "-w", path],
                capture_output=True, text=True, timeout=5,
            )
            win_path = result.stdout.strip()
        except Exception:
            logger.warning("tts: wslpath 轉換失敗，無法播放")
            return
        if not win_path:
            logger.warning("tts: wslpath 回傳空路徑（returncode=%d），無法播放", result.returncode)
            return
        if path.endswith(".wav"):
            win_path_esc = win_path.replace("'", "''")
            script = (
                "$p = New-Object System.Media.SoundPlayer; "
                f"$p.SoundLocation = '{win_path_esc}'; "
                "$p.PlaySync()"
            )
        else:
            win_path_fwd = win_path.replace(chr(92), "/")
            script = (
                "Add-Type -AssemblyName presentationCore; "
                "$m = New-Object System.Windows.Media.MediaPlayer; "
                f"$m.Open([Uri]'file:///{win_path_fwd}'); "
                "$m.Play(); "
                "$t=0; while(-not $m.NaturalDuration.HasTimeSpan -and $t -lt 50)"
                "{Start-Sleep -Milliseconds 100; $t++}; "
                "if($m.NaturalDuration.HasTimeSpan){"
                "$dur=$m.NaturalDuration.TimeSpan.TotalMilliseconds; "
                "while($m.Position.TotalMilliseconds -lt $dur-150){Start-Sleep -Milliseconds 100}}; "
                "$m.Stop(); $m.Close()"
            )
        cmd = ["powershell.exe", "-NoProfile", "-Command", script]

    if not cmd:
        logger.warning("tts: 無可用播放器")
        return

    player_label = "powershell" if use_powershell else (cmd[0] if cmd else player)
    try:
        global _current_proc
        logger.info(f"tts play start: {Path(path).name}  player={player_label}")
        _t0 = __import__("time").perf_counter()
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with _proc_lock:
            _current_proc = proc
        try:
            proc.wait()
        finally:
            with _proc_lock:
                if _current_proc is proc:
                    _current_proc = None
        logger.info(f"tts play end: {Path(path).name}  elapsed={__import__('time').perf_counter() - _t0:.2f}s")
    except Exception as e:
        logger.warning(f"tts 播放失敗：{e}")


async def _play_async(path: str, player: str) -> None:
    """非同步播放，不阻塞 TTS event loop。
    _play_file_sync 若直接呼叫會卡住整條 loop，導致 cancel() 無法生效、
    排隊的 TTS 進不去，直到音訊裝置釋放（如關機）才恢復。
    """
    try:
        await asyncio.get_event_loop().run_in_executor(None, _play_file_sync, path, player)
    except asyncio.CancelledError:
        cancel_current()  # 確保 subprocess 被殺掉
        raise


def _clean_for_tts(text: str) -> str:
    """移除 markdown 格式符號與不應朗讀的符號。"""
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # 表格分隔線 / 水平線（--- | === 等）整行移除
    text = re.sub(r"^[\s]*[-=|]{2,}[\s]*$", "", text, flags=re.MULTILINE)
    # blockquote > 前綴
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    # markdown 表格管道符
    text = re.sub(r"\|", " ", text)
    text = re.sub(r"^[\s]*[•\*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[←→↑↓↖↗↘↙⇒⇐⇔▶▷◀◁►◄]", "", text)
    text = re.sub(r"[\U0001F000-\U0001FFFF\U00002600-\U000027BF]", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    # URL 整個移除（不朗讀）
    text = re.sub(r"https?://\S+", "", text)
    # 剩餘斜線（slash 指令 /tts、路徑 /home/...）換成空白
    text = re.sub(r"/", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _speak_melotts(
    text: str,
    voice: str = "EN-US",
    lang: str = "EN",
    speed: float = 1.0,
    host: str = "localhost",
    port: int = 8886,
    player: str = "auto",
) -> None:
    """透過 MeloTTS Gradio server（WebSocket queue）生成 WAV 並播放。"""
    try:
        import websockets
    except ImportError:
        logger.warning("tts: websockets 未安裝：uv sync --extra tts")
        return
    import random
    import string
    import urllib.request

    session = "kai_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    uri = f"ws://{host}:{port}/queue/join"

    try:
        async with websockets.connect(uri) as ws:  # type: ignore[attr-defined]
            async for raw in ws:
                msg = json.loads(raw)
                m = msg.get("msg")
                if m == "send_hash":
                    await ws.send(json.dumps({"session_hash": session, "fn_index": 1}))
                elif m == "send_data":
                    await ws.send(json.dumps({
                        "data": [voice, text, speed, lang],
                        "fn_index": 1,
                        "session_hash": session,
                    }))
                elif m == "process_completed":
                    out = msg.get("output", {}).get("data", [None])[0]
                    if not out:
                        logger.warning("tts melotts: 無回傳音訊")
                        return
                    name = out.get("name", "")
                    url = f"http://{host}:{port}/file={name}"
                    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="kai_tts_")
                    os.close(fd)
                    try:
                        urllib.request.urlretrieve(url, tmp)
                        await _play_async(tmp, player)
                    except Exception as e:
                        logger.warning(f"tts melotts 播放失敗：{e}")
                    finally:
                        Path(tmp).unlink(missing_ok=True)
                    return
                elif m == "queue_full":
                    logger.warning("tts melotts: server queue 已滿")
                    return
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"tts melotts 連線失敗：{e}")


_KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_KOKORO_CACHE = Path.home() / ".cache" / "kokoro-onnx"
_KOKORO_MODEL_FILE = "kokoro-v1.0.int8.onnx"   # int8 CPU-friendly (~80MB)
_KOKORO_VOICES_FILE = "voices-v1.0.bin"


def _download_kokoro_file(filename: str) -> Path:
    import urllib.request
    dest = _KOKORO_CACHE / filename
    if dest.exists():
        return dest
    _KOKORO_CACHE.mkdir(parents=True, exist_ok=True)
    url = f"{_KOKORO_BASE}/{filename}"
    logger.info(f"tts: 下載 {filename}…")
    urllib.request.urlretrieve(url, dest)
    logger.info(f"tts: {filename} 下載完成")
    return dest


def _load_kokoro() -> Any:
    """載入 kokoro-onnx 模型（首次自動下載 ~100MB 至 ~/.cache/kokoro-onnx/）。"""
    global _kokoro_model
    if _kokoro_model is not None:
        return _kokoro_model
    try:
        from kokoro_onnx import Kokoro
    except ImportError:
        raise RuntimeError("kokoro-onnx 未安裝：uv sync --extra tts")

    model_path = _download_kokoro_file(_KOKORO_MODEL_FILE)
    voices_path = _download_kokoro_file(_KOKORO_VOICES_FILE)
    logger.info("tts: 初始化 kokoro 模型…")
    _kokoro_model = Kokoro(str(model_path), str(voices_path))
    logger.info("tts: kokoro 就緒")
    return _kokoro_model


_KOKORO_MAX_CHARS = 400  # CPU inference 每 100 字約 5-8s，限制避免等太久
# 每段送進 kokoro 的字數上限，必須遠低於 kokoro-onnx 的 510 音素上限
# （kokoro-onnx 截到剛好 510 時會 voice[510] 越界，必須自己先切小）
# 英文約 1 字 ≈ 1 音素；中日韓文字 1 字約 3-4 音素，因此用較小的上限
_KOKORO_CHUNK_CHARS = 200
_KOKORO_CHUNK_CHARS_CJK = 80

# 中日韓文字（含全形標點、注音、假名）
_CJK_RE = re.compile(
    r"[　-〿぀-ヿ㄀-ㄯ㐀-䶿一-鿿"
    r"豈-﫿＀-￯]+"
)
# IPA 音標與修飾符號：英文 espeak 念出來是亂碼，一併移除
_IPA_RE = re.compile(r"\S*[\u0250-\u02FF\u1D00-\u1DBF]\S*")  # 含 IPA 的整個詞


def _english_only(text: str) -> str:
    """移除中日韓文字與 IPA，只保留英文部分；被移除的中文段落視為句子邊界。"""
    text = _CJK_RE.sub(". ", text)
    text = _IPA_RE.sub("", text)
    # 清掉移除後殘留的空括號與孤立符號
    text = re.sub(r"[\(\[\{]\s*[.,;:\s]*[\)\]\}]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    # 每行收斂：連續標點（". . ," 之類）合併成一個；只去掉行首條列符號與孤立破折號，
    # 不動緊貼文字的 "-"（如 "-5 degrees"）
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s*([.,;:!?])(\s*[.,;:!?])+", r"\1", line)
        line = re.sub(r"^[\s.,;:!?]+|[\s,;:]+$", "", line)
        line = re.sub(r"^[-–—*•]\s+|\s+[-–—]+\s*$", "", line).strip()
        if re.search(r"[A-Za-z]", line):
            lines.append(line if line[-1] in ".!?" else f"{line}.")
    return "\n".join(lines)


def _chunk_text(text: str, max_chars: int = _KOKORO_CHUNK_CHARS) -> list[str]:
    """依句子切段，每段不超過 max_chars；過長的單句再依空白切。"""
    sentences = [
        s.strip() for s in re.split(r"(?<=[.!?;:])\s+|(?<=[。！？；])|\n+", text) if s.strip()
    ]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        while len(sentence) > max_chars:
            cut = sentence.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            head, sentence = sentence[:cut].strip(), sentence[cut:].strip()
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        if current and len(current) + len(sentence) + 1 > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


async def _speak_kokoro(
    text: str,
    voice: str = "af_sky",
    lang: str = "en-us",
    player: str = "auto",
) -> None:
    """kokoro-onnx 本地 TTS。模型在 thread executor 跑，不阻塞 event loop。"""
    try:
        import soundfile as sf
    except ImportError:
        logger.warning("tts: soundfile 未安裝：uv sync --extra tts")
        return

    is_english = lang.lower().startswith("en")
    if is_english:
        # 英文發音引擎念中文會產生超長音素串並觸發 kokoro-onnx 越界錯誤，只念英文部分
        orig_len = len(text)
        text = _english_only(text)
        if not text:
            logger.info("tts kokoro: 去除中文後沒有英文內容，略過")
            return
        if len(text) != orig_len:
            logger.debug(f"tts kokoro: 只保留英文 {orig_len} → {len(text)} 字")

    if len(text) > _KOKORO_MAX_CHARS:
        logger.info(f"tts kokoro: 截斷 {len(text)} → {_KOKORO_MAX_CHARS} 字")
        text = text[:_KOKORO_MAX_CHARS].rsplit(" ", 1)[0]  # 在空白處截斷避免斷字

    chunks = _chunk_text(text, _KOKORO_CHUNK_CHARS if is_english else _KOKORO_CHUNK_CHARS_CJK)
    if not chunks:
        logger.info("tts kokoro: 沒有可念的內容，略過")
        return

    def _generate() -> tuple[Any, int]:
        import numpy as np

        kokoro = _load_kokoro()
        parts: list[Any] = []
        sample_rate = 24000
        for chunk in chunks:
            samples, sample_rate = kokoro.create(chunk, voice=voice, speed=1.0, lang=lang)
            parts.append(samples)
        return np.concatenate(parts), sample_rate

    logger.info(f"tts kokoro generating: {len(text)} chars")
    _t0 = asyncio.get_event_loop().time()
    try:
        samples, sample_rate = await asyncio.get_event_loop().run_in_executor(None, _generate)
    except Exception as e:
        logger.warning(f"tts kokoro 生成失敗：{e}")
        return
    logger.info(f"tts kokoro generated in {asyncio.get_event_loop().time() - _t0:.1f}s")

    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="kai_tts_")
    os.close(fd)
    try:
        sf.write(tmp, samples, sample_rate)
        await _play_async(tmp, player)
    except Exception as e:
        logger.warning(f"tts kokoro 播放失敗：{e}")
    finally:
        Path(tmp).unlink(missing_ok=True)


async def _speak_kokoro_api(
    text: str,
    voice: str = "af_bella",
    speed: float = 1.0,
    host: str = "localhost",
    port: int = 8885,
    player: str = "auto",
) -> None:
    """kokoro-fastapi Docker server，OpenAI 相容格式，回傳 WAV bytes。"""
    import urllib.request

    url = f"http://{host}:{port}/v1/audio/speech"
    payload = json.dumps({
        "model": "kokoro",
        "input": text,
        "voice": voice,
        "response_format": "wav",
        "speed": speed,
    }).encode()

    def _fetch() -> bytes:
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()

    if len(text) > _KOKORO_MAX_CHARS:
        logger.info(f"tts kokoro-api: 截斷 {len(text)} → {_KOKORO_MAX_CHARS} 字")
        text = text[:_KOKORO_MAX_CHARS].rsplit(" ", 1)[0]
    logger.info(f"tts kokoro-api generating: {len(text)} chars  voice={voice}")
    _t0 = asyncio.get_event_loop().time()
    try:
        audio_bytes = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    except Exception as e:
        logger.warning(f"tts kokoro-api 請求失敗：{e}")
        return
    logger.info(f"tts kokoro-api generated in {asyncio.get_event_loop().time() - _t0:.1f}s")

    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="kai_tts_")
    os.close(fd)
    try:
        Path(tmp).write_bytes(audio_bytes)
        # 讀入、裁切末尾雜音、重寫為標準 WAV
        try:
            import numpy as np
            import soundfile as sf
            _data, _sr = sf.read(tmp)
            # 裁切末尾低於 -46dB 的雜音（模型 noise floor）
            _energy = np.abs(_data) if _data.ndim == 1 else np.max(np.abs(_data), axis=1)
            _loud = np.where(_energy > 0.005)[0]
            _orig_s = len(_data) / _sr
            if len(_loud) > 0:
                _end = min(_loud[-1] + int(_sr * 0.25), len(_data))
                _data = _data[:_end]
            sf.write(tmp, _data, _sr)
            logger.debug(f"tts kokoro-api: {_orig_s:.1f}s → {len(_data)/_sr:.1f}s (trimmed {_orig_s - len(_data)/_sr:.1f}s noise)")
        except Exception as _e:
            logger.warning(f"tts kokoro-api: trim 失敗（仍嘗試播放）：{_e}")
        await _play_async(tmp, player)
    except Exception as e:
        logger.warning(f"tts kokoro-api 播放失敗：{e}")
    finally:
        Path(tmp).unlink(missing_ok=True)


async def _speak_azure(
    text: str,
    voice: str = "zh-TW-HsiaoChenNeural",
    lang: str = "",
    speed: float = 1.0,
    key: str = "",
    region: str = "",
    endpoint: str = "",
    player: str = "auto",
) -> None:
    """Azure Cognitive Services TTS REST API（不需安裝 SDK）。

    Endpoint 優先順序：
      1. 傳入 endpoint（或 AZURE_TTS_ENDPOINT）— 直接使用
      2. 傳入 region（或 AZURE_TTS_REGION）— 推斷標準 Speech endpoint
    """
    import html
    import urllib.error
    import urllib.request
    from . import config as _cfg

    _key      = key      or _cfg.AZURE_TTS_KEY
    _endpoint = endpoint or _cfg.AZURE_TTS_ENDPOINT
    _region   = region   or _cfg.AZURE_TTS_REGION

    if not _key:
        logger.warning("tts azure: 需設定 AZURE_TTS_KEY（.env 或 agent config.json tts.key）")
        return

    # 決定 TTS REST endpoint
    if _endpoint:
        # 使用者貼入完整 endpoint（e.g. https://eastasia.api.cognitive.microsoft.com/）
        # Azure TTS REST 路徑固定為 cognitiveservices/v1
        base = _endpoint.rstrip("/")
        url = f"{base}/cognitiveservices/v1"
    elif _region:
        url = f"https://{_region}.tts.speech.microsoft.com/cognitiveservices/v1"
    else:
        logger.warning("tts azure: 需設定 AZURE_TTS_REGION 或 AZURE_TTS_ENDPOINT")
        return

    # 從 voice name 推斷 lang（e.g. "zh-TW-HsiaoChenNeural" → "zh-TW"）
    _lang = lang
    if not _lang and "-" in voice:
        parts = voice.split("-")
        _lang = f"{parts[0]}-{parts[1]}" if len(parts) >= 3 else voice

    # speed → SSML prosody rate（+20% / -10% / +0%）
    rate = f"{int((speed - 1) * 100):+d}%"
    ssml = (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
        f"xml:lang='{_lang}'>"
        f"<voice name='{voice}'>"
        f"<prosody rate='{rate}'>{html.escape(text)}</prosody>"
        f"</voice></speak>"
    )

    def _fetch() -> bytes:
        req = urllib.request.Request(
            url,
            data=ssml.encode("utf-8"),
            headers={
                "Ocp-Apim-Subscription-Key": _key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "kai-tts/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()  # type: ignore[return-value]

    logger.info(f"tts azure: {len(text)} chars  voice={voice}  region={_region}")
    _t0 = asyncio.get_event_loop().time()
    try:
        audio_bytes = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    except asyncio.CancelledError:
        logger.debug("tts azure: 已取消（被新回覆覆蓋）")
        raise  # 讓 CancelledError 正確傳播，不阻塞 TTS loop
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        logger.warning(f"tts azure HTTP {e.code}：{body}")
        return
    except Exception as e:
        logger.warning(f"tts azure 請求失敗：{e}")
        return
    logger.info(f"tts azure done in {asyncio.get_event_loop().time() - _t0:.1f}s  bytes={len(audio_bytes)}")

    fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="kai_tts_")
    os.close(fd)
    try:
        Path(tmp).write_bytes(audio_bytes)
        await _play_async(tmp, player)
    except Exception as e:
        logger.warning(f"tts azure 播放失敗：{e}")
    finally:
        Path(tmp).unlink(missing_ok=True)


async def speak(
    text: str,
    voice: str = "en-US-AriaNeural",
    player: str = "auto",
    engine: str = "edge-tts",
    lang: str = "en-us",
    host: str = "localhost",
    port: int = 8886,
    speed: float = 1.0,
    key: str = "",
    region: str = "",
    endpoint: str = "",
) -> None:
    """語音播放。engine 可為 'edge-tts'、'azure-tts'、'kokoro'、'kokoro-api'、'melotts'。"""
    text = _clean_for_tts(text)
    if not text:
        return

    if engine == "azure-tts":
        await _speak_azure(text, voice=voice, lang=lang, speed=speed, key=key, region=region, endpoint=endpoint, player=player)
        return

    if engine == "kokoro":
        await _speak_kokoro(text, voice=voice, lang=lang, player=player)
        return

    if engine == "kokoro-api":
        await _speak_kokoro_api(text, voice=voice, speed=speed, host=host, port=port, player=player)
        return

    if engine == "melotts":
        await _speak_melotts(
            text, voice=voice, lang=lang.upper(),
            speed=speed, host=host, port=port, player=player,
        )
        return

    # edge-tts
    try:
        import edge_tts
    except ImportError:
        logger.warning("tts: edge-tts 未安裝，執行 `uv add edge-tts`")
        return

    fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="kai_tts_")
    os.close(fd)
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(tmp)
        await _play_async(tmp, player)
    except Exception as e:
        logger.warning(f"tts 失敗：{e}")
    finally:
        Path(tmp).unlink(missing_ok=True)

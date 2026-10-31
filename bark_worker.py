#!/usr/bin/env python3
"""
Persistent Bark TTS background worker for ANSV2 (CUDA-accelerated on Tesla P40).
Supports:
- Built-in `v2/en_speaker_0..9`, custom `.npz` voices in `./voices/`, and `random` hallucination mode
- Custom `text_temp` (semantic temperature) and `waveform_temp` (coarse/fine acoustic temperature)
- Per-channel Voice Drift mode (feeds each output's tokens into the next generation's history_prompt)
- Audio-to-.npz Voice Cloner (`clone_audio`: converts .wav/.mp3 -> 24kHz mono -> EnCodec 8-codebook .npz)
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import warnings

warnings.filterwarnings("ignore")

real_stdout = sys.stdout
sys.stdout = sys.stderr

os.environ.setdefault("HF_HOME", "/home/matt/ansv/ansv-bot/.hf_cache")
VOICES_DIR = os.path.abspath(os.environ.get("ANSV2_VOICES_DIR", "/home/matt/ansv2/voices"))
FFMPEG_BIN = "/home/linuxbrew/.linuxbrew/bin/ffmpeg"
if not os.path.exists(FFMPEG_BIN):
    FFMPEG_BIN = "ffmpeg"

import numpy as np
import scipy.io.wavfile
import torch
from transformers import AutoProcessor, BarkModel


def emit(obj):
    real_stdout.write(json.dumps(obj) + "\n")
    real_stdout.flush()


def load_bark(model_name="suno/bark-small"):
    device_str = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(device_str)

    orig_load = torch.load

    def patched_load(*args, **kwargs):
        if "weights_only" not in kwargs:
            kwargs["weights_only"] = False
        return orig_load(*args, **kwargs)

    torch.load = patched_load
    try:
        processor = AutoProcessor.from_pretrained(model_name)
        model = BarkModel.from_pretrained(model_name).to(device)
        model.eval()
    finally:
        torch.load = orig_load

    return processor, model, device


def trim_prompt_dict(sem, coarse, fine):
    sem = sem.astype(np.int64)
    coarse = coarse.astype(np.int64)
    fine = fine.astype(np.int64)
    if sem.shape[0] > 320:
        sem = sem[:320]
        coarse = coarse[:, :480]
        fine = fine[:, :480]
    return {
        "semantic_prompt": sem,
        "coarse_prompt": coarse,
        "fine_prompt": fine,
    }


def resolve_voice_preset(preset_name):
    if not preset_name or preset_name in ("random", "unhinged", "none"):
        return None
    if preset_name.startswith("v2/"):
        return preset_name

    if os.path.isdir(VOICES_DIR):
        exact = os.path.join(VOICES_DIR, f"{preset_name}.npz")
        target_path = None
        if os.path.exists(exact):
            target_path = exact
        else:
            lower = preset_name.lower()
            for fn in os.listdir(VOICES_DIR):
                if fn.endswith(".npz"):
                    stem = fn[:-4].lower()
                    if stem == lower or stem.startswith(lower):
                        target_path = os.path.join(VOICES_DIR, fn)
                        break

        if target_path and os.path.exists(target_path):
            npz = np.load(target_path, allow_pickle=False)
            keys = ("semantic_prompt", "coarse_prompt", "fine_prompt")
            if all(k in npz.files for k in keys):
                return trim_prompt_dict(
                    npz["semantic_prompt"],
                    npz["coarse_prompt"],
                    npz["fine_prompt"],
                )

    return "v2/en_speaker_5"


def split_text_chunks(text, max_len=150):
    raw_parts = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks = []
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= max_len:
            chunks.append(part)
        else:
            words = part.split()
            cur = []
            cur_len = 0
            for w in words:
                if cur_len + len(w) + 1 > max_len and cur:
                    chunks.append(" ".join(cur))
                    cur = [w]
                    cur_len = len(w)
                else:
                    cur.append(w)
                    cur_len += len(w) + 1
            if cur:
                chunks.append(" ".join(cur))
    return chunks or [text[:max_len]]


def prepare_inputs(processor, chunk, vp, device):
    if vp is not None:
        inputs = processor(text=chunk, voice_preset=vp, return_tensors="pt").to(device)
    else:
        inputs = processor(text=chunk, return_tensors="pt").to(device)

    if "history_prompt" in inputs and inputs["history_prompt"] is not None:
        inputs["history_prompt"] = {
            k: v.to(device) for k, v in inputs["history_prompt"].items()
        }
    return inputs


def clone_audio_to_npz(processor, model, device, audio_path, voice_name, transcript):
    from transformers.models.bark.generation_configuration_bark import (
        BarkSemanticGenerationConfig,
    )

    os.makedirs(VOICES_DIR, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = tmp.name

    try:
        # Convert input audio to 24kHz mono WAV (1.5s to 7s)
        subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-i",
                audio_path,
                "-ac",
                "1",
                "-ar",
                "24000",
                "-t",
                "6.5",
                tmp_wav,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        rate, data = scipy.io.wavfile.read(tmp_wav)
        if data.dtype == np.int16:
            wav_f32 = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            wav_f32 = data.astype(np.float32) / 2147483648.0
        else:
            wav_f32 = data.astype(np.float32)

        if wav_f32.ndim > 1:
            wav_f32 = wav_f32.mean(axis=1)

        # Normalize peak
        peak = np.max(np.abs(wav_f32))
        if peak > 0:
            wav_f32 = wav_f32 / peak * 0.95

        wav_tensor = torch.from_numpy(wav_f32).unsqueeze(0).unsqueeze(0).to(device)
        with torch.no_grad():
            enc = model.codec_model.encode(wav_tensor, bandwidth=6.0)
        # enc.audio_codes has shape [1, 1, 8, N_frames]
        fine_codes = enc.audio_codes[0, 0].detach().cpu().numpy().astype(np.int16)
        n_frames = fine_codes.shape[1]
        target_sem_len = max(16, int(n_frames * 2 // 3))
        aligned_frames = target_sem_len * 3 // 2
        fine_codes = fine_codes[:, :aligned_frames]
        coarse_codes = fine_codes[:2, :]

        ref_text = (transcript or "Hello, this is my custom voice.").strip()
        inp = processor(text=ref_text, return_tensors="pt").to(device)
        sem_cfg = BarkSemanticGenerationConfig(**model.generation_config.semantic_config)
        with torch.no_grad():
            sem_out = model.semantic.generate(
                inp["input_ids"],
                semantic_generation_config=sem_cfg,
                max_new_tokens=target_sem_len,
                min_eos_p=1.0,
                pad_token_id=10000,
            )
        sem_np = sem_out[0].detach().cpu().numpy().astype(np.int16)
        # Pad or trim sem_np to match target_sem_len
        if sem_np.shape[0] < target_sem_len:
            pad_len = target_sem_len - sem_np.shape[0]
            sem_np = np.pad(sem_np, (0, pad_len), mode="edge")
        else:
            sem_np = sem_np[:target_sem_len]

        dst_npz = os.path.join(VOICES_DIR, f"{voice_name}.npz")
        np.savez(
            dst_npz,
            semantic_prompt=sem_np,
            coarse_prompt=coarse_codes,
            fine_prompt=fine_codes,
        )
        return dst_npz
    finally:
        if os.path.exists(tmp_wav):
            os.remove(tmp_wav)


def main():
    try:
        t0 = time.time()
        processor, model, device = load_bark("suno/bark-small")

        captured = {}
        drift_prompts = {}
        orig_sem_gen = model.semantic.generate

        def wrapped_sem_gen(*a, **kw):
            out = orig_sem_gen(*a, **kw)
            captured["sem"] = out[0].detach().cpu().numpy().astype(np.int16)
            return out

        model.semantic.generate = wrapped_sem_gen

        orig_fine_gen = model.fine_acoustics.generate

        def wrapped_fine_gen(*a, **kw):
            out = orig_fine_gen(*a, **kw)
            fine_np = out[0].detach().cpu().numpy().astype(np.int16)
            captured["fine"] = fine_np
            return out

        model.fine_acoustics.generate = wrapped_fine_gen

        if device.type == "cuda":
            w_inp = prepare_inputs(processor, "hi.", "v2/en_speaker_5", device)
            with torch.no_grad():
                _ = model.generate(
                    **w_inp, semantic_max_new_tokens=32, pad_token_id=10000
                )
            torch.cuda.synchronize()
        emit(
            {
                "event": "ready",
                "device": str(device),
                "load_secs": round(time.time() - t0, 2),
                "sample_rate": int(model.generation_config.sample_rate),
            }
        )
    except Exception as e:
        emit({"event": "error", "message": f"Failed to initialize Bark: {e}"})
        return

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except Exception as e:
            emit({"event": "job_done", "ok": False, "error": f"Invalid JSON: {e}"})
            continue

        cmd = job.get("cmd", "synthesize")
        if cmd == "reset_drift":
            ch = job.get("channel", "")
            drift_prompts.pop(ch, None)
            emit({"event": "drift_reset", "channel": ch, "ok": True})
            continue
        elif cmd == "clone_audio":
            audio_path = job.get("audio_path", "")
            voice_name = job.get("voice_name", "")
            transcript = job.get("transcript", "")
            try:
                dst = clone_audio_to_npz(
                    processor, model, device, audio_path, voice_name, transcript
                )
                emit(
                    {
                        "event": "clone_done",
                        "ok": True,
                        "voice_name": voice_name,
                        "path": dst,
                    }
                )
            except Exception as e:
                emit({"event": "clone_done", "ok": False, "error": str(e)})
            finally:
                if audio_path and os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except Exception:
                        pass
            continue

        job_id = job.get("id", "")
        channel = job.get("channel", "")
        text = job.get("text", "").strip()
        voice_preset_name = job.get("voice_preset", "v2/en_speaker_5")
        output_path = job.get("output_path", "")
        text_temp = float(job.get("text_temp", 0.7))
        waveform_temp = float(job.get("waveform_temp", 0.7))
        drift_enabled = bool(job.get("drift_enabled", False))

        if not text or not output_path:
            emit(
                {
                    "event": "job_done",
                    "id": job_id,
                    "ok": False,
                    "error": "Missing text or output_path",
                }
            )
            continue

        t_start = time.time()
        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            if drift_enabled and channel in drift_prompts:
                vp = drift_prompts[channel]
            else:
                vp = resolve_voice_preset(voice_preset_name)

            chunks = split_text_chunks(text, 150)
            pieces = []
            captured.clear()

            for chunk in chunks:
                words = max(1, len(chunk.split()))
                sem_max = max(72, min(280, words * 24 + 48))
                inputs = prepare_inputs(processor, chunk, vp, device)

                with torch.no_grad():
                    audio_arr = model.generate(
                        **inputs,
                        semantic_max_new_tokens=sem_max,
                        semantic_temperature=text_temp,
                        coarse_temperature=waveform_temp,
                        fine_temperature=waveform_temp,
                        min_eos_p=0.06,
                        pad_token_id=10000,
                    )
                audio_np = audio_arr.cpu().numpy().squeeze()
                if audio_np.ndim == 1 and audio_np.size > 0:
                    pieces.append(audio_np)

            if not pieces:
                raise RuntimeError("No audio samples generated")

            final_audio = np.concatenate(pieces)
            rate = int(model.generation_config.sample_rate)
            scipy.io.wavfile.write(output_path, rate=rate, data=final_audio)

            # Save captured voice prompt tokens as <output_path>.npz and update drift state if enabled
            if "sem" in captured and "fine" in captured:
                sem_arr = captured["sem"]
                fine_arr = captured["fine"]
                expected_frames = int(sem_arr.shape[0] * 3 // 2)
                if fine_arr.shape[1] >= expected_frames and expected_frames > 0:
                    fine_slice = fine_arr[:, -expected_frames:]
                    coarse_slice = fine_slice[:2, :]
                    np.savez(
                        output_path + ".npz",
                        semantic_prompt=sem_arr,
                        coarse_prompt=coarse_slice,
                        fine_prompt=fine_slice,
                    )
                    if drift_enabled and channel:
                        drift_prompts[channel] = trim_prompt_dict(
                            sem_arr, coarse_slice, fine_slice
                        )

            audio_len_secs = round(len(final_audio) / float(rate), 2)
            gen_secs = round(time.time() - t_start, 2)

            emit(
                {
                    "event": "job_done",
                    "id": job_id,
                    "ok": True,
                    "output_path": output_path,
                    "voice_preset": voice_preset_name,
                    "audio_secs": audio_len_secs,
                    "gen_secs": gen_secs,
                }
            )
        except Exception as e:
            emit(
                {
                    "event": "job_done",
                    "id": job_id,
                    "ok": False,
                    "error": str(e),
                }
            )


if __name__ == "__main__":
    main()

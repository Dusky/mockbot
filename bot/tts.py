import os

import time
import asyncio
import logging
import re
from datetime import datetime
import sqlite3
import threading

_db = None  # set by init_tts_db() after Bot.__init__ creates Database
_event_bus = None  # set by init_tts_events(); used to publish TtsGenerated


def init_tts_db(database) -> None:
    global _db
    _db = database


def init_tts_events(bus) -> None:
    global _event_bus
    _event_bus = bus

import requests
import sys
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import nltk
import numpy as np
from nltk.tokenize import sent_tokenize
from functools import lru_cache
import weakref
from concurrent.futures import ThreadPoolExecutor

# PYTORCH 2.6 FIX: Monkey-patch torch.load to use weights_only=False globally for Bark compatibility
import torch
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    if 'weights_only' not in kwargs:
        kwargs['weights_only'] = False
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

VOICES_DIRECTORY = './voices'


# PERFORMANCE: TTS Model Cache and Thread Pool
class TTSModelCache:
    """PERFORMANCE: Cache for TTS models to avoid repeated loading"""
    def __init__(self, max_models=3):
        self.cache = {}
        self.max_models = max_models
        self.last_used = {}
        self.lock = threading.Lock()
        
    def get_model(self, model_path, device):
        """Get cached model or load if not cached"""
        cache_key = f"{model_path}_{device}"
        
        with self.lock:
            if cache_key in self.cache:
                # Update last used time
                self.last_used[cache_key] = time.time()
                logging.debug(f"TTS: Using cached model {model_path}")
                return self.cache[cache_key]
            
            # Need to load model - check cache size
            if len(self.cache) >= self.max_models:
                # Remove least recently used model
                oldest_key = min(self.last_used.keys(), key=lambda k: self.last_used[k])
                del self.cache[oldest_key]
                del self.last_used[oldest_key]
                logging.info(f"TTS: Evicted model from cache: {oldest_key}")
            
            # Load the model
            logging.info(f"TTS: Loading model into cache: {model_path}")
            try:
                if model_path == "chatterbox":
                    from chatterbox.tts import ChatterboxTTS
                    device_str = "cuda" if device.type == "cuda" else "cpu"
                    
                    logging.getLogger('bot').warning("[yellow]📥 Loading Chatterbox Base Model. If this is the first execution, a 2.13GB background download will initiate. Please wait...[/yellow]")
                    model = ChatterboxTTS.from_pretrained(device=device_str)
                    
                    cached_model = {'model': model, 'type': 'chatterbox'}
                    self.cache[cache_key] = cached_model
                    self.last_used[cache_key] = time.time()
                    
                    logging.info(f"TTS: Successfully cached Chatterbox model")
                    return cached_model
                
                from transformers import AutoProcessor, BarkModel
                import torch
                
                # Check PyTorch version compatibility
                pytorch_version = torch.__version__
                logging.info(f"TTS: Using PyTorch version {pytorch_version} with device {device}")
                
                try:
                    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
                    # Force use of safetensors
                    model = BarkModel.from_pretrained(model_path, use_safetensors=True, local_files_only=True)
                except Exception as cache_err:
                    logging.warning(f"TTS: Offline cache missing for {model_path}, attempting network fallback... ({cache_err})")
                    processor = AutoProcessor.from_pretrained(model_path)
                    model = BarkModel.from_pretrained(model_path, use_safetensors=True)
                
                # Force CPU device if CPU-only mode
                if str(device) == "cpu":
                    model = model.to(torch.device("cpu"))
                else:
                    model = model.to(device)
                
                # Apply optimizations for CUDA
                if device.type == "cuda":
                    # PyTorch 2.0+ uses SDPA automatically, BetterTransformer is legacy/deprecated
                    # and requires 'optimum' which is problematic on bleeding edge versions.
                    pass
                    # try:
                    #     model.enable_cpu_offload()
                    #     logging.debug(f"TTS: Enabled CPU offload for {model_path}")
                    # except Exception as e:
                    #     logging.warning(f"TTS: Could not apply optimizations: {e}")
                
                # Configure tokenizer
                if processor.tokenizer.pad_token_id is None:
                    if processor.tokenizer.eos_token_id is not None:
                        processor.tokenizer.pad_token_id = processor.tokenizer.eos_token_id
                    else:
                        processor.tokenizer.pad_token_id = 10000
                
                cached_model = {'processor': processor, 'model': model, 'type': 'bark'}
                self.cache[cache_key] = cached_model
                self.last_used[cache_key] = time.time()
                
                logging.info(f"TTS: Successfully cached model {model_path}")
                return cached_model
                
            except AttributeError as ae:
                if 'get_default_device' in str(ae):
                    logging.error(f"TTS: PyTorch compatibility error - {ae}")
                    logging.error("TTS: This suggests a version mismatch between PyTorch and transformers")
                    logging.error(f"TTS: Current PyTorch version: {torch.__version__}")
                    logging.error("TTS: Try upgrading PyTorch to 2.6.0+ or downgrading transformers")
                else:
                    logging.error(f"TTS: AttributeError loading model {model_path}: {ae}")
                return None
            except Exception as e:
                logging.error(f"TTS: Failed to load model {model_path}: {e}")
                return None
    
    def clear_cache(self):
        """Clear all cached models"""
        with self.lock:
            self.cache.clear()
            self.last_used.clear()
            logging.info("TTS: Cleared model cache")

# Global instances
tts_model_cache = TTSModelCache()
tts_thread_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tts-worker")

def fetch_latest_message():
    if _db is None:
        return (None, None, None, None, None)
    try:
        result = _db.fetch_latest_message_sync()
        logging.debug(f"Latest message fetched: {result}")
        return result
    except sqlite3.Error as e:
        logging.error(f"SQLite error in fetch_latest_message: {e}")
        raise

@lru_cache(maxsize=100)
def _get_tts_config_cached(channel_name):
    """PERFORMANCE: Cached TTS config fetch — single DB round-trip per channel."""
    if _db is None:
        return {}
    return _db.get_tts_config_sync(channel_name)


def _get_tts_config(channel_name):
    return _get_tts_config_cached(channel_name)


# Keep individual lookup helpers so call sites in process_text_thread don't change.

def get_voice_preset_cached(channel_name, db_file_path=None):
    default = os.environ.get("DEFAULT_VOICE_PRESET")
    if default:
        return default
    return _get_tts_config(channel_name).get("voice_preset", "v2/en_speaker_5")


def get_voice_preset(channel_name, db_file=None):
    return get_voice_preset_cached(channel_name)


def get_bark_model_cached(channel_name, db_file_path=None):
    default = os.environ.get("DEFAULT_BARK_MODEL")
    if default:
        return default
    return _get_tts_config(channel_name).get("bark_model", "regular")


def get_bark_model_for_channel(channel_name, db_file=None):
    return get_bark_model_cached(channel_name)


def get_tts_provider_cached(channel_name, db_file_path=None):
    return _get_tts_config(channel_name).get("tts_provider", "bark")


def get_tts_provider_for_channel(channel_name, db_file=None):
    return get_tts_provider_cached(channel_name)


def get_advanced_tts_configs_cached(channel_name, db_file_path=None):
    cfg = _get_tts_config(channel_name)
    return {
        "rvc_model": cfg.get("rvc_model", ""),
        "chatterbox_temperature": cfg.get("chatterbox_temperature", 0.8),
        "chatterbox_exaggeration": cfg.get("chatterbox_exaggeration", 0.5),
        "bark_text_temp": cfg.get("bark_text_temp", 0.7),
        "bark_waveform_temp": cfg.get("bark_waveform_temp", 0.7),
        "rvc_pitch": cfg.get("rvc_pitch", 0),
        "rvc_index_rate": cfg.get("rvc_index_rate", 0.75),
        "rvc_api_url": cfg.get("rvc_api_url", "http://127.0.0.1:5051"),
    }

def apply_rvc_conversion(input_path, rvc_model_name, pitch=0, index_rate=0.75):
    if not rvc_model_name: return False
    
    import requests
    import shutil
    import os
    
    rvc_endpoint = os.environ.get("RVC_API_URL", "http://127.0.0.1:5051")
    
    try:
        logging.getLogger('bot').info(f"[RVC ENGINE] Requesting clone inference via native sandbox at {rvc_endpoint}...")
        
        payload = {
            "input_path": os.path.abspath(input_path),
            "model_name": rvc_model_name,
            "pitch": pitch,
            "index_rate": index_rate
        }
        
        for attempt in range(3):
            try:
                response = requests.post(f"{rvc_endpoint}/api/infer", json=payload, timeout=35)
                if response.status_code == 200:
                    data = response.json()
                    tmp_file = data.get("tmp_file")
                    if tmp_file and os.path.exists(tmp_file):
                        shutil.move(tmp_file, input_path)
                        logging.getLogger('bot').info(f"✅ FastApi RVC Conversion applied successfully for {rvc_model_name}")
                        return True
                    else:
                        logging.getLogger('bot').error("[RVC ENGINE] Request returned 200 but failed to yield audio stream.")
                        return False
                else:
                    logging.getLogger('bot').error(f"[RVC ENGINE] FastAPI Exception Code {response.status_code}: {response.text}")
                    return False
            except requests.exceptions.Timeout:
                logging.getLogger('bot').warning(f"[RVC ENGINE] Request timed out on attempt {attempt+1}/3. Retrying...")
                import time
                time.sleep(2)
            except requests.exceptions.ConnectionError:
                logging.getLogger('bot').error(f"[RVC ENGINE] Target sandbox missing at {rvc_endpoint}! Did you run './launch.sh start-rvc' ?")
                return False
            except Exception as e:
                logging.getLogger('bot').error(f"[RVC ENGINE] Tunnel crashed: {e}", exc_info=True)
                return False
        logging.getLogger('bot').error(f"[RVC ENGINE] Aborting conversion after 3 timeouts.")
        return False
            
    except requests.exceptions.ConnectionError:
        logging.getLogger('bot').error(f"[RVC ENGINE] Target sandbox missing at {rvc_endpoint}! Did you run './launch.sh start-rvc' ?")
        return False
    except Exception as e:
        logging.getLogger('bot').error(f"[RVC ENGINE] Tunnel utterly crashed: {e}", exc_info=True)
        return False

def log_tts_file(message_id, channel_name, timestamp, file_path, voice_preset, input_text, db_file=None):
    file_path = file_path.replace('static/', '', 1)
    if _db is not None:
        _db.log_tts_sync(message_id, channel_name, file_path, voice_preset or "v2/en_speaker_5", input_text, timestamp)
    else:
        logging.warning("[log_tts_file] _db not initialised, skipping TTS log.")

def initialize_tts():
    global AutoProcessor, BarkModel, torch, scipy
    import os
    import warnings
    
    # Suppress verbose Hugging Face logging and warnings for CLI clarity
    os.environ["TRANSFORMERS_VERBOSITY"] = "error"
    os.environ["SUNO_USE_SMALL_MODELS"] = "True"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    warnings.filterwarnings("ignore", category=UserWarning, module="transformers")

    from transformers import AutoProcessor, BarkModel, logging as transformers_logging
    transformers_logging.set_verbosity_error()
    import torch
    import scipy.io.wavfile

def process_text_thread(input_text, channel_name, db_file='./messages.db', full_path=None, timestamp=None, message_id=None, voice_preset=None, bark_model=None, author_name=None):
    """Process TTS in a separate thread with silenced output"""
        # Log parameters *before* silencing, for critical debugging
    logging.getLogger('bot').info(f"🎙️ [bold cyan]TTS Thread Initialized:[/bold cyan] '{str(input_text)[:40]}...' [dim](Provider mapping pending...)[/dim]", extra={'channel': channel_name})
    if message_id is None:
        logging.error("[TTS THREAD CRITICAL] message_id is None at entry. This will likely cause DB insert to fail or be incorrect.")
    if full_path is None:
        logging.error("[TTS THREAD CRITICAL] full_path is None at entry. Cannot save audio or log correctly.")
    if timestamp is None: # This is the original message timestamp
        logging.warning("[TTS THREAD WARNING] Original message timestamp (timestamp param) is None at entry.")


    # Acquire lock to ensure only one thread generates Bark audio at a time
    if True: # ThreadPoolExecutor handles concurrency
        pass
        # Note: silence_output() moved to after model loading to allow progress bars

        try:
            # Make sure we have the necessary TTS dependencies
            if 'AutoProcessor' not in globals():
                initialize_tts()
            
            import torch
            import scipy.io.wavfile
            from transformers import AutoProcessor, BarkModel
            from nltk.tokenize import sent_tokenize
            import torchaudio
        except ImportError as import_err:
            logging.error(f"[TTS THREAD FATAL ERROR] Failed to import critical TTS dependencies: {import_err}", exc_info=True)
            # Re-raise the error to be caught by the outer try-except in process_text_thread
            # and return None, None, preventing further execution in this thread.
            raise
        
        try:
            # Get channel-specific bark model if available
            if not bark_model:
                bark_model = get_bark_model_for_channel(channel_name, db_file)
            
            # Default to bark-small if no model specified
            if not bark_model:
                bark_model = "regular"  # Use friendly name as default
            
            # Map friendly names to actual Hugging Face model names
            model_mapping = {
                "regular": "bark-small",
                "small": "bark-small", 
                "large": "bark"
            }
            
            # Get actual model name, fallback to bark-small if unknown
            actual_model = model_mapping.get(bark_model, "bark-small")
            
            logging.info(f"Using Bark model: {bark_model} -> suno/{actual_model} for channel {channel_name}")
            model_path = f"suno/{actual_model}"
            
            # PERFORMANCE: Use cached model loading
            device_type_str = "cuda" if torch.cuda.is_available() else "cpu"
            device = torch.device(device_type_str)
            
            logging.info(f"TTS: Attempting to use device: {device_type_str}")

            tts_provider = get_tts_provider_for_channel(channel_name, db_file)
            logging.getLogger('bot').info(f"⚙️ [yellow]Processing Output:[/yellow] ({tts_provider} | Base: {bark_model}) for {channel_name}", extra={'channel': channel_name})
            
            adv_cfg = get_advanced_tts_configs_cached(channel_name, db_file)
            
            if tts_provider in ["chatterbox", "rvc_chatterbox"]:
                cached_model_data = tts_model_cache.get_model("chatterbox", device)
                if not cached_model_data:
                    logging.error(f"[TTS THREAD FATAL ERROR] Failed to load Chatterbox model")
                    raise RuntimeError(f"Chatterbox model loading failed")
                model = cached_model_data['model']
                
                # Check if voice preset is an actual audio prompt file path
                cb_kwargs = {
                    'temperature': adv_cfg['chatterbox_temperature'],
                    'exaggeration': adv_cfg['chatterbox_exaggeration']
                }
                if voice_preset and voice_preset.endswith('.wav'):
                    # Check straight path first, then voices/ directory
                    if os.path.exists(voice_preset):
                        cb_kwargs['audio_prompt_path'] = voice_preset
                    elif os.path.exists(os.path.join('voices', voice_preset)):
                        cb_kwargs['audio_prompt_path'] = os.path.abspath(os.path.join('voices', voice_preset))
                    
                wav = model.generate(input_text, **cb_kwargs)
                torchaudio.save(full_path, wav, model.sr)
                
                if tts_provider == "rvc_chatterbox":
                    apply_rvc_conversion(full_path, adv_cfg['rvc_model'], adv_cfg['rvc_pitch'], adv_cfg['rvc_index_rate'])

            else:
                # PERFORMANCE: Get model from cache instead of loading each time
                cached_model_data = tts_model_cache.get_model(model_path, device)
                if not cached_model_data:
                    logging.error(f"[TTS THREAD FATAL ERROR] Failed to load or cache model: {model_path}")
                    raise RuntimeError(f"Model loading failed: {model_path}")
                
                processor = cached_model_data['processor']
                model = cached_model_data['model']
    
                try:
                    pass  # Model loading is now handled by cache
                except AttributeError as ae:
                    if 'get_default_device' in str(ae):
                        logging.error(f"[TTS FATAL ERROR] AttributeError: {ae}. This strongly suggests your PyTorch version is too old (e.g., < 1.9) for the installed 'transformers' version.")
                        logging.error("[TTS FATAL ERROR] Please upgrade PyTorch to 1.9+ or align your 'transformers' library version with your PyTorch version.")
                        raise # Re-raise the error to be caught by the outer try-except in process_text_thread
                    else:
                        logging.error(f"[TTS FATAL ERROR] AttributeError during model loading: {ae}")
                        raise # Re-raise other AttributeErrors
                except Exception as model_load_exc:
                    logging.error(f"[TTS FATAL ERROR] Failed to load or prepare BarkModel: {model_load_exc}", exc_info=True)
                    raise # Re-raise the error
                
                # Handle voice preset (built-in vs custom)
                if voice_preset and voice_preset.startswith('v2/'):
                    # Built-in Bark preset - nothing special needed
                    logging.info(f"Using built-in Bark preset: {voice_preset} for channel {channel_name}") # Keep as info
                    # The preset will be used directly in the processor call
                else:
                    # Try to load custom voice if available
                    custom_voice_data = load_custom_voice(voice_preset)
                    if custom_voice_data and 'weights' in custom_voice_data:
                        model.load_state_dict(custom_voice_data['weights'])
                        logging.info(f"Loaded custom voice: {voice_preset} for channel {channel_name}") # Keep as info
                    else:
                        # Fall back to default preset if custom voice not found
                        voice_preset = 'v2/en_speaker_5' # Default preset
                        logging.info(f"Using fallback voice preset: {voice_preset} for channel {channel_name}") # Keep as info
    
                # We must NOT use global redirect_stdout/stderr as it causes TUI graphical tearing!
                import transformers
                transformers.logging.set_verbosity_error()
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    if True:
                        # Process text in chunks for better performance
                        all_audio_pieces = []
                        sentences = sent_tokenize(input_text)
                        
                        for sentence in sentences:
                            pieces = split_sentence(sentence, 165)  # Split long sentences
                            for piece in pieces:
                                # Generate speech with the selected voice preset
                                # Removed padding=True as it caused TypeError with BarkProcessor.
                                # The processor should handle padding and attention_mask with return_tensors="pt".
                                inputs = processor(text=piece, voice_preset=voice_preset, return_tensors="pt").to(device)
                                
                                # The model.generate call should now use the attention_mask from inputs.
                                # Explicitly passing pad_token_id can also help, using the model's config.
                                # Bark's EOS token ID (10000) is often used as pad_token_id for generation.
                                pad_token_id_for_generation = model.generation_config.pad_token_id or model.generation_config.eos_token_id or 10000
                                
                                # Ensure attention_mask is explicitly passed to avoid the warning
                                attention_mask = inputs.get("attention_mask")
                                if attention_mask is not None:
                                    audio_array = model.generate(
                                        inputs.input_ids,
                                        attention_mask=attention_mask,
                                        pad_token_id=pad_token_id_for_generation
                                    )
                                else:
                                    audio_array = model.generate(**inputs, pad_token_id=pad_token_id_for_generation)
                                    
                                audio_array = audio_array.cpu().numpy().squeeze()
                                all_audio_pieces.append(audio_array)
                                
                transformers.logging.set_verbosity_warning() # Restore warning logging
    
                # Combine all audio pieces and save
                final_audio_array = np.concatenate(all_audio_pieces)
                scipy.io.wavfile.write(full_path, rate=model.generation_config.sample_rate, data=final_audio_array)

                if tts_provider in ["rvc", "rvc_chatterbox"]:
                    apply_rvc_conversion(full_path, adv_cfg['rvc_model'], adv_cfg['rvc_pitch'], adv_cfg['rvc_index_rate'])
            
            # Record in database and notify web interface
            db_file_path_relative = full_path.replace('static/', '', 1)
            
            logged_tts_table_id = None # Renamed from logged_tts_id for clarity
            timestamp_for_log = timestamp # Use the passed original message timestamp

            # Validate critical parameters before database operation
            if message_id is None:
                logging.error(f"[TTS DB LOG] Critical: message_id is None for channel {channel_name}, text: '{str(input_text)[:30]}...'. Cannot log TTS entry without message_id.")
                # Do not proceed to DB insert if message_id is None
            elif full_path is None:
                logging.error(f"[TTS DB LOG] Critical: full_path is None for message_id {message_id}. Cannot log TTS entry.")
            else:
                if timestamp_for_log is None: # This is the original message timestamp string
                    logging.warning(f"[TTS DB LOG] Warning: Original message timestamp (timestamp param) is None for message_id {message_id}. Using current time for TTS log.")
                    timestamp_for_log = datetime.now().strftime("%Y%m%d-%H%M%S")

                try:
                    logging.getLogger('bot').info(f"✅ [bold green]TTS Generation Complete![/bold green] Saved to {db_file_path_relative}", extra={'channel': channel_name})
                    if _db is not None:
                        logged_tts_table_id = _db.log_tts_sync(
                            message_id, channel_name, db_file_path_relative,
                            voice_preset, input_text, timestamp_for_log,
                        )
                        if logged_tts_table_id is None:
                            logging.warning(f"[TTS DB LOG] Insert IGNORED for message_id {message_id} (likely duplicate).")
                        else:
                            logging.info(f"[TTS DB LOG] Logged ROWID {logged_tts_table_id} for message_id {message_id}.")
                    else:
                        logging.warning("[TTS DB LOG] _db not initialised, skipping log.")
                except Exception as general_db_err:
                    logging.error(f"[TTS DB LOG] Error during tts_logs insert (message_id: {message_id}): {general_db_err}", exc_info=True)
                    logged_tts_table_id = None

            # Unconditionally broadcast to OBS overlay regardless of database insertion status
            active_model = adv_cfg.get('rvc_model', '') if tts_provider in ['rvc', 'rvc_chatterbox'] else voice_preset
            notify_new_audio_available(channel_name, message_id, full_path, input_text, tts_provider, active_model, author_name) 
            
            # Log status update to be captured by the TUI
            logging.getLogger('bot').info(f"[bright_green]✅ TTS audio ready! ({full_path})[/]")
            
            # Log internally without printing to console (already done above with [TTS DB LOG])
            # logging.info(f"TTS audio file generated: {full_path}. Logged to tts_logs with table ID: {logged_tts_table_id} (linked to original message_id: {message_id})")
            
            return full_path, logged_tts_table_id # Return the ID of the tts_logs table entry
            
        except Exception as e:
            # This is a fatal error for this thread, so logging.error is appropriate
            logging.error(f"[TTS THREAD FATAL ERROR] Uncaught exception in process_text_thread: {e}", exc_info=True)
            # import traceback # exc_info=True handles this
            # traceback.print_exc()
            return None, None # Ensure two values are returned as expected if caller unpacks
        

@lru_cache(maxsize=20)
def load_custom_voice_cached(voice_preset):
    """PERFORMANCE: Cached custom voice loading to avoid repeated file I/O"""
    # If no voice_preset is provided, it's not a custom voice.
    if voice_preset is None:
        logging.debug("No voice preset provided to load_custom_voice, assuming default will be used by caller.")
        return None

    # Handle built-in presets
    if voice_preset.startswith('v2/'):
        # These are built-in to Bark, no file needed
        logging.debug(f"Using built-in Bark voice preset: {voice_preset}")
        return None
    
    # SECURITY FIX: Validate voice_preset to prevent directory traversal
    # Only allow alphanumeric characters, underscores, and hyphens
    if not re.match(r'^[a-zA-Z0-9_-]+$', voice_preset):
        logging.warning(f"Invalid voice preset name: {voice_preset}")
        return None
    
    # Additional check: prevent excessively long names
    if len(voice_preset) > 50:
        logging.warning(f"Voice preset name too long: {voice_preset}")
        return None
    
    # For custom voices, check file existence
    voice_file = os.path.join(VOICES_DIRECTORY, f"{voice_preset}.npz")
    
    # SECURITY: Ensure the resolved path is within the voices directory
    resolved_voice_file = os.path.abspath(voice_file)
    resolved_voices_dir = os.path.abspath(VOICES_DIRECTORY)
    if not resolved_voice_file.startswith(resolved_voices_dir + os.sep):
        logging.warning(f"Path traversal attempt detected: {voice_preset}")
        return None
    if not os.path.exists(voice_file):
        logging.warning(f"Custom voice file not found: {voice_file}")
        # Fall back to default
        return None
    
    try:
        # SECURITY FIX: Load custom voice data WITHOUT allow_pickle to prevent code execution
        # This prevents arbitrary code execution from malicious .npz files
        voice_data = np.load(voice_file, allow_pickle=False)
        
        # Validate that the loaded data contains expected keys for voice weights
        expected_keys = ['semantic_prompt', 'coarse_prompt', 'fine_prompt']
        if not all(key in voice_data.files for key in expected_keys):
            logging.warning(f"Custom voice file {voice_file} missing expected keys: {expected_keys}")
            return None
            
        # Convert numpy arrays to torch tensors safely
        import torch
        weights = {}
        for key in expected_keys:
            if key in voice_data:
                array = voice_data[key]
                # Validate array properties for safety
                if array.dtype not in [np.float32, np.float64, np.int32, np.int64]:
                    logging.warning(f"Invalid data type in voice file: {array.dtype}")
                    return None
                if array.size > 1000000:  # Reasonable size limit
                    logging.warning(f"Voice data too large: {array.size} elements")
                    return None
                weights[key] = torch.tensor(array)
        
        return {'weights': weights}
    except Exception as e:
        logging.error(f"Error loading custom voice {voice_preset}: {e}")
        return None

def load_custom_voice(voice_preset):
    """Load a custom voice file with caching for performance"""
    return load_custom_voice_cached(voice_preset)

def ensure_nltk_resources():
    """Ensure NLTK resources are downloaded"""
    import nltk
    try:
        from nltk.tokenize import sent_tokenize
        # Test if punkt is working
        sent_tokenize("Test sentence.")
    except (LookupError, ImportError):
        logging.info("Downloading required NLTK resources (punkt, punkt_tab)...") # Keep as info, important one-time setup
        try:
            # Download both punkt and punkt_tab (newer NLTK versions need punkt_tab)
            nltk.download('punkt', quiet=True)
            nltk.download('punkt_tab', quiet=True)
            nltk.download('averaged_perceptron_tagger', quiet=True)
            logging.info("NLTK resources downloaded successfully")
            return True
        except Exception as e:
            logging.error(f"Failed to download NLTK resources: {e}") # Keep as error
            return False
    return True

async def process_text(channel, text, model_type="bark", voice_preset_override=None): # This is for the !speak command path
    """Process text to speech via unified thread pool"""
    try:
        logging.info(f"Starting ASYNC TTS for channel {channel} (likely !speak command) via thread pool")
        
        # Create output directory if it doesn't exist
        output_dir = f"static/outputs/{channel.lstrip('#')}"
        os.makedirs(output_dir, exist_ok=True)
        
        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_file = f"{output_dir}/{channel.lstrip('#')}-{timestamp}.wav"
        
        loop = asyncio.get_running_loop()
        global tts_thread_pool
        
        # Call the unified synchronous backend. 
        # Using message_id=None will skip formal DB logging but still generate the file
        full_path, _ = await loop.run_in_executor(
            tts_thread_pool, 
            process_text_thread,
            text, channel, './messages.db', output_file, timestamp, None, voice_preset_override, None, None
        )
        
        if full_path:
            return True, full_path
        return False, None
    except Exception as e:
        logging.error(f"TTS processing error: {str(e)}", exc_info=True)
        return False, None

def split_sentence(sentence, max_length):
    """Split a sentence into smaller parts if it's longer than max_length."""
    pieces = []
    while len(sentence) > max_length:
        split_index = sentence.rfind(' ', 0, max_length)
        if split_index == -1:  # No space found, forced split
            split_index = max_length - 1
        pieces.append(sentence[:split_index + 1])
        sentence = sentence[split_index + 1:]
    pieces.append(sentence)
    return pieces
    
def start_tts_processing(input_text, channel_name, db_file='./messages.db', message_id=None, timestamp_str=None, voice_preset_override=None):
    """
    Starts the TTS processing in a separate thread.
    message_id: The ID of the original message that triggered this TTS.
    timestamp_str: The timestamp string of the original message.
    voice_preset_override: Optional voice preset to use, otherwise fetched from DB.
    """
    # Ensure NLTK resources are available before starting TTS
    ensure_nltk_resources()

    filename_timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    clean_channel_name = channel_name.lstrip('#')
    output_dir = f"static/outputs/{clean_channel_name}"
    os.makedirs(output_dir, exist_ok=True)
    generated_full_path = f"{output_dir}/{clean_channel_name}-{filename_timestamp}.wav"

    logging.info(f"Preparing TTS thread for bot message. Original message_id: {message_id}, original_timestamp_str: {timestamp_str}, voice_preset_override: {voice_preset_override}, generated_path: {generated_full_path}")

    global tts_thread_pool
    logging.getLogger('bot').info(f"[cyan]🎙️ Queuing TTS audio to thread pool... ({voice_preset_override or 'default voice'})[/]")
    tts_thread_pool.submit(
        process_text_thread, 
        input_text, 
        channel_name, 
        db_file,
        generated_full_path,
        timestamp_str,
        message_id,
        voice_preset_override
    )
    # logging.info(f"TTS processing thread dispatched for original message_id {message_id} in channel {channel_name}.")

def notify_new_audio_available(channel_name, message_id, full_path, text="", provider="", voice="", author=""):
    # Direct overlay broadcast (unchanged), plus the canonical bus event for the
    # socketio compat layer and future webui clients.
    from bot.overlay import broadcast_audio
    broadcast_audio(channel_name, full_path, text, provider, voice, author)
    if _event_bus is not None:
        from bot.events import TtsGenerated
        _event_bus.publish(TtsGenerated(
            channel=channel_name, message_id=message_id or "", file_path=full_path or "",
            text=text, provider=provider, voice=voice, author=author,
        ))

def clear_tts_queue():
    """Immediately attempt to kill TTS tasks and notify overlays."""
    global tts_thread_pool
    try:
        from bot.overlay import broadcast_kill_audio
        broadcast_kill_audio()
    except Exception as e:
        logging.error(f"Failed to broadcast audio kill signal: {e}")
    
    try:
        old_pool = tts_thread_pool
        old_pool.shutdown(wait=False, cancel_futures=True)
        global ThreadPoolExecutor
        if 'ThreadPoolExecutor' not in globals():
            from concurrent.futures import ThreadPoolExecutor
        tts_thread_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tts-worker")
        logging.getLogger('bot').info("[bold red]🛑 Backend TTS Queue Cleared![/bold red]")
    except Exception as e:
        logging.getLogger('bot').error(f"Failed to clear TTS queue natively: {e}")

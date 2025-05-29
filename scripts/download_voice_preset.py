#!/usr/bin/env python3
"""
Voice preset download script for ANSV Bot TTS functionality.
Handles downloading and caching of Bark model voice presets.
"""

import os
import sys
import torch
import argparse
from pathlib import Path


def setup_environment():
    """Configure environment variables for Bark TTS."""
    # Suppress most Bark warnings for cleaner output during preset download
    os.environ['SUNO_ENABLE_MPS'] = 'False'  # Avoid MPS specific warnings if not used
    os.environ['SUNO_OFFLOAD_CPU'] = 'True'  # Offload to CPU to reduce memory if GPU not primary
    os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'
    os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'


def validate_preset_name(preset):
    """Validate voice preset name to prevent path traversal attacks."""
    if not preset:
        raise ValueError("Preset name cannot be empty")
    
    # Check for path traversal attempts
    if '..' in preset or '/' not in preset:
        if preset not in ['v2/en_speaker_0', 'v2/en_speaker_1', 'v2/en_speaker_2', 
                         'v2/en_speaker_3', 'v2/en_speaker_4', 'v2/en_speaker_5',
                         'v2/en_speaker_6', 'v2/en_speaker_7', 'v2/en_speaker_8', 'v2/en_speaker_9']:
            raise ValueError(f"Invalid preset name: {preset}")
    
    return preset


def download_voice_preset(preset, cache_dir):
    """Download and cache a specific voice preset."""
    try:
        from transformers import AutoProcessor, BarkModel
        
        print(f"Downloading voice preset: {preset}")
        
        # Validate inputs
        preset = validate_preset_name(preset)
        cache_path = Path(cache_dir)
        if not cache_path.exists():
            cache_path.mkdir(parents=True, exist_ok=True)
        
        # Load processor and model
        processor = AutoProcessor.from_pretrained('suno/bark-small', cache_dir=str(cache_path))
        model = BarkModel.from_pretrained('suno/bark-small', cache_dir=str(cache_path))
        
        # Determine device
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        model = model.to(device)
        
        # Generate minimal sample to trigger speaker embedding download
        text_prompt = 'Hello.'
        inputs = processor(text_prompt, voice_preset=preset, return_tensors='pt').to(device)
        
        # Generate with minimal parameters to speed up the process
        with torch.no_grad():
            speech_values = model.generate(**inputs, do_sample=True, min_eos_p=0.2, max_new_tokens=16)
        
        print(f"Voice preset {preset} cached successfully!")
        return True
        
    except ImportError as e:
        print(f"Missing dependencies: {e}")
        return False
    except Exception as e:
        print(f"Failed to cache voice preset {preset}: {e}")
        return False


def main():
    """Main function to handle command line arguments and download presets."""
    parser = argparse.ArgumentParser(description="Download TTS voice presets")
    parser.add_argument("preset", help="Voice preset to download (e.g., v2/en_speaker_6)")
    parser.add_argument("--cache-dir", default="models/tts", help="Cache directory for models")
    
    args = parser.parse_args()
    
    setup_environment()
    
    success = download_voice_preset(args.preset, args.cache_dir)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
import sys, os
from bot.tts import process_text_thread
process_text_thread('test message', 'test_channel', './messages.db', 'static/outputs/test/test.wav', '2026-02-27T08:00:00', 'test_msg_id', 'v2/en_speaker_5')

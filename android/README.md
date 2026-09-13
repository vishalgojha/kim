# Kim Android companion

This is the first Android control-panel scaffold. Open the `android/` directory in Android Studio, run it on your phone, enter the Kim PIN, and use the status/pause/wake controls.

The foreground service opens an authenticated, short-lived ElevenLabs WebSocket session, captures 16 kHz microphone PCM, and plays Kim’s response audio. Android shows an ongoing notification while the voice session is active. Tool actions from mobile voice must still follow Kim’s approval policy.

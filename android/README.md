# Kim Android companion

Open the `android/` directory in Android Studio, run it on your phone, enter the Kim PIN, and use the status/pause/wake controls. The latest direct APK is published at [github.com/vishalgojha/kim/releases/tag/kim-latest](https://github.com/vishalgojha/kim/releases/tag/kim-latest). The Actions artifact is a ZIP wrapper; use the Release asset when you want to tap/download an actual `.apk` file.

The foreground service opens an authenticated, short-lived ElevenLabs WebSocket session, captures 16 kHz microphone PCM, and plays Kim’s response audio. Android shows an ongoing notification while the voice session is active. WorkManager checks for pending approvals in the background. Read-only voice tools execute through the hosted API; write tools create an approval and execute in Coolify or on the laptop relay according to server configuration.

The laptop is not required for voice, status, notifications, approvals, or cloud-configured Gmail/Calendar/WhatsApp actions. It is required only for laptop-local tools and as the fallback host for local OAuth/WhatsApp bridge credentials.

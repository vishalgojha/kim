# Kim Android companion

Open the `android/` directory in Android Studio, run it on your phone, enter the Kim PIN, and use the status/pause/wake controls. A debug APK is also built by the `Build Kim Android` GitHub Actions workflow.

The foreground service opens an authenticated, short-lived ElevenLabs WebSocket session, captures 16 kHz microphone PCM, and plays Kim’s response audio. Android shows an ongoing notification while the voice session is active. WorkManager checks for pending approvals in the background. Read-only voice tools execute through the hosted API; write tools create an approval and execute in Coolify or on the laptop relay according to server configuration.

The laptop is not required for voice, status, notifications, approvals, or cloud-configured Gmail/Calendar/WhatsApp actions. It is required only for laptop-local tools and as the fallback host for local OAuth/WhatsApp bridge credentials.

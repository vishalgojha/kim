# Kim Android companion

This is the first Android control-panel scaffold. Open the `android/` directory in Android Studio, run it on your phone, paste the existing `KIM_REMOTE_TOKEN`, and use the status/pause/wake controls.

The foreground service is only a permissioned Android foundation. Actual microphone streaming requires a WebSocket voice endpoint on the Kim server; the current server exposes authenticated HTTP controls only. Android will show an ongoing notification whenever the service is active.

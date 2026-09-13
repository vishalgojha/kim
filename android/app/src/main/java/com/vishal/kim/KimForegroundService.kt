package com.vishal.kim

import android.app.*
import android.content.Intent
import android.net.Uri
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.util.concurrent.Executors
import android.os.IBinder

class KimForegroundService : Service() {
    private val executor = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private val deviceId by lazy { KimPrefs.deviceId(this) }
    private val loop = object : Runnable { override fun run() { executor.execute { poll() }; handler.postDelayed(this, 5000) } }
    override fun onCreate() {
        super.onCreate()
        val channel = NotificationChannel("kim", "Kim", NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        val notification = Notification.Builder(this, "kim")
            .setContentTitle("Kim is listening")
            .setContentText("Tap Kim to return to the control panel")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true).build()
        startForeground(1001, notification)
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        handler.removeCallbacks(loop); handler.post(loop); return START_STICKY
    }
    private fun poll() {
        val pin = KimPrefs.open(this).getString("pin", "") ?: return
        val client = KimClient("https://app.vishalojha.me", pin)
        runCatching { client.heartbeat(deviceId); val raw = client.nextDeviceCommand(deviceId); val command = JSONObject(raw.substringAfter(": ")).optJSONObject("command") ?: return; val result = runCatching { act(command.optString("action"), command.optJSONObject("parameters") ?: JSONObject()) }; client.deviceResult(command.optString("id"), command.optString("action"), result.getOrElse { it.message ?: "failed" }, result.isFailure) }
    }
    private fun act(action: String, p: JSONObject): String = when (action) {
        "open_url" -> { val intent = Intent(Intent.ACTION_VIEW, Uri.parse(p.getString("url"))); intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK); startActivity(intent); "opened" }
        "open_app" -> { val intent = packageManager.getLaunchIntentForPackage(p.getString("package")) ?: error("app not installed"); intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK); startActivity(intent); "opened" }
        "notify" -> { getSystemService(NotificationManager::class.java).notify(1002, Notification.Builder(this, "kim").setContentTitle("Kim").setContentText(p.optString("text", "Kim notification")).setSmallIcon(android.R.drawable.ic_dialog_info).build()); "notified" }
        "volume" -> { getSystemService(AudioManager::class.java).adjustVolume(if (p.optString("direction") == "down") AudioManager.ADJUST_LOWER else AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI); "changed" }
        else -> error("unsupported phone action")
    }
    override fun onDestroy() { handler.removeCallbacks(loop); executor.shutdownNow(); super.onDestroy() }
    override fun onBind(intent: Intent?): IBinder? = null
}

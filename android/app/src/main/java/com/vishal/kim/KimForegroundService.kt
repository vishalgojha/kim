package com.vishal.kim

import android.app.*
import android.content.Context
import android.content.Intent
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.view.KeyEvent
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
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
        val user = KimPrefs.activeUser(this)
        val pin = KimPrefs.pin(this, user)
        val client = KimClient("https://app.vishalojha.me", pin, user)
        runCatching { client.heartbeat(deviceId); val raw = client.nextDeviceCommand(deviceId); val command = JSONObject(raw.substringAfter(": ")).optJSONObject("command") ?: return; val result = runCatching { act(command.optString("action"), command.optJSONObject("parameters") ?: JSONObject()) }; client.deviceResult(command.optString("id"), command.optString("action"), result.getOrElse { it.message ?: "failed" }, result.isFailure) }
    }
    private fun act(action: String, p: JSONObject): String = when (action) {
        "open_url" -> { val intent = Intent(Intent.ACTION_VIEW, Uri.parse(p.getString("url"))); intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK); startActivity(intent); "opened" }
        "open_app" -> { val intent = packageManager.getLaunchIntentForPackage(p.getString("package")) ?: error("app not installed"); intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK); startActivity(intent); "opened" }
        "notify" -> { getSystemService(NotificationManager::class.java).notify(1002, Notification.Builder(this, "kim").setContentTitle("Kim").setContentText(p.optString("text", "Kim notification")).setSmallIcon(android.R.drawable.ic_dialog_info).build()); "notified" }
        "volume" -> { getSystemService(AudioManager::class.java).adjustVolume(if (p.optString("direction") == "down") AudioManager.ADJUST_LOWER else AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI); "changed" }
        "media" -> { val intent = Intent(Intent.ACTION_MEDIA_BUTTON); intent.putExtra(Intent.EXTRA_KEY_EVENT, KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE)); sendOrderedBroadcast(intent, null); "toggled" }
        "flashlight" -> { val cm = getSystemService(Context.CAMERA_SERVICE) as CameraManager; val camId = cm.cameraIdList.firstOrNull { cm.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true }; if (camId != null) { cm.setTorchMode(camId, !cm.getTorchMode(camId)); "toggled" } else "no camera" }
        "type_text" -> { val text = p.optString("text", "").replace(Regex("""\s"""), "%20"); val proc = Runtime.getRuntime().exec(arrayOf("input", "text", text)); proc.waitFor(); "typed" }
        "press_key" -> { val key = p.optString("key", "KEYCODE_ENTER"); Runtime.getRuntime().exec(arrayOf("input", "keyevent", key)).waitFor(); "pressed" }
        "screenshot" -> { val proc = Runtime.getRuntime().exec(arrayOf("screencap", "-p")); val png = proc.inputStream.readBytes(); val out = File(filesDir, "screenshot.png"); FileOutputStream(out).use { it.write(png) }; "saved ${out.absolutePath}" }
        else -> error("unsupported phone action")
    }
    override fun onDestroy() { handler.removeCallbacks(loop); executor.shutdownNow(); super.onDestroy() }
    override fun onBind(intent: Intent?): IBinder? = null
}

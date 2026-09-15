package com.vishal.kim

import android.Manifest
import android.app.*
import android.content.Intent
import android.content.pm.PackageManager
import android.media.*
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.IBinder
import android.util.Base64
import okhttp3.*
import okio.ByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class KimVoiceService : Service() {
    companion object {
        fun stop(context: android.content.Context) { context.stopService(Intent(context, KimVoiceService::class.java)) }
    }
    private val client = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).build()
    private var socket: WebSocket? = null
    private var recorder: AudioRecord? = null
    private var player: AudioTrack? = null
    private var noiseSuppressor: NoiseSuppressor? = null
    private var echoCanceler: AcousticEchoCanceler? = null
    private var recording = false
    private val user by lazy { KimPrefs.activeUser(this) }
    private val pin by lazy { KimPrefs.pin(this, user) }
    private val readOnlyTools = setOf(
        "battery", "disk_usage", "known_apps", "running_processes", "system_info",
        "gmail_search", "gmail_read", "gmail_today", "gmail_unanswered", "gmail_contacts",
        "calendar_upcoming", "whatsapp_search", "whatsapp_recent", "whatsapp_chats"
        , "knowledge_search", "knowledge_sources", "deep_research"
    )

    override fun onCreate() {
        super.onCreate()
        val channel = NotificationChannel("kim_voice", "Kim voice", NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        startForeground(1002, notification("Kim is listening", "Speak naturally; tap the notification to return to Kim"))
        startSession()
    }

    private fun startSession() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) { stopSelf(); return }
        Thread {
            try {
                val urlResponse = KimClient("https://app.vishalojha.me", pin, user).getVoiceSession()
                val code = urlResponse.substringBefore(":").toIntOrNull() ?: 500
                if (code !in 200..299) throw IllegalStateException(urlResponse.substringAfter(": ").take(240))
                val url = JSONObject(urlResponse.substringAfter(": ")).getString("url")
                socket = client.newWebSocket(Request.Builder().url(url).build(), listener)
            } catch (error: Exception) { showError(error.message ?: "Voice connection failed"); stopSelf() }
        }.start()
    }

    private val listener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            webSocket.send(JSONObject().put("type", "conversation_initiation_client_data").put("conversation_config_override", JSONObject()).put("dynamic_variables", JSONObject().put("device_context", "Android phone for $user; use mobile capabilities only; reply in the user's language and Roman Hindi when Hindi is typed in Latin letters")).toString())
            try { startAudio() } catch (error: Exception) { showError(error.message ?: "Microphone could not start"); stopSelf() }
        }
        override fun onMessage(webSocket: WebSocket, text: String) {
            try {
                val json = JSONObject(text)
                if (json.optString("type") == "audio") {
                    val encoded = json.getJSONObject("audio_event").optString("audio_base_64")
                    val bytes = Base64.decode(encoded, Base64.DEFAULT)
                    player?.write(bytes, 0, bytes.size)
                }
                if (json.optString("type") == "client_tool_call") {
                    handleToolCall(json.optJSONObject("client_tool_call") ?: json)
                }
            } catch (_: Exception) { }
        }
        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { if (code != 1000) showError("Voice session closed ($code): ${reason.ifBlank { "server ended the session" }}"); stopSelf() }
        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { showError(t.message ?: "Voice connection failed"); stopSelf() }
    }

    private fun handleToolCall(json: JSONObject) {
        val toolCallId = json.optString("tool_call_id", json.optString("id"))
        val name = json.optString("tool_name", json.optString("name"))
        val parameters = json.optJSONObject("parameters") ?: JSONObject()
        if (toolCallId.isBlank() || name.isBlank()) return
        Thread {
            try {
                val kim = KimClient("https://app.vishalojha.me", pin, user)
                val raw = kim.runTool(name, parameters)
                val code = raw.substringBefore(":").toIntOrNull() ?: 500
                val body = raw.substringAfter(": ", "")
                val result = body
                socket?.send(JSONObject().put("type", "client_tool_result").put("tool_call_id", toolCallId).put("result", result).put("is_error", code !in 200..299).toString())
            } catch (error: Exception) {
                socket?.send(JSONObject().put("type", "client_tool_result").put("tool_call_id", toolCallId).put("result", error.message ?: "tool failed").put("is_error", true).toString())
            }
        }.start()
    }

    private fun notification(title: String, text: String): Notification =
        Notification.Builder(this, "kim_voice").setContentTitle(title).setContentText(text)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now).setOngoing(true).build()

    private fun showError(message: String) {
        getSystemService(NotificationManager::class.java).notify(1002, notification("Kim voice stopped", message))
    }

    private fun startAudio() {
        val min = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (min <= 0) throw IllegalStateException("Android returned an invalid microphone buffer")
        val localRecorder = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, 16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, min * 2)
        if (localRecorder.state != AudioRecord.STATE_INITIALIZED) { localRecorder.release(); throw IllegalStateException("Microphone is unavailable or already in use") }
        val localPlayer = AudioTrack(AudioManager.STREAM_MUSIC, 16000, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT, min * 2, AudioTrack.MODE_STREAM)
        noiseSuppressor = if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(localRecorder.audioSessionId) else null
        echoCanceler = if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(localRecorder.audioSessionId) else null
        recorder = localRecorder; player = localPlayer
        localRecorder.startRecording()
        if (localRecorder.recordingState != AudioRecord.RECORDSTATE_RECORDING) throw IllegalStateException("Microphone permission is granted but recording did not start")
        localPlayer.play(); recording = true
        Thread {
            val buffer = ByteArray(min.coerceAtLeast(2048))
            while (recording) {
                val count = recorder?.read(buffer, 0, buffer.size) ?: 0
                if (count > 0) {
                    var energy = 0L
                    var peak = 0
                    var index = 0
                    while (index + 1 < count) {
                        val raw = (buffer[index].toInt() and 0xff) or (buffer[index + 1].toInt() shl 8)
                        val signed = if ((raw and 0x8000) != 0) raw - 0x10000 else raw
                        val sample = kotlin.math.abs(signed)
                        energy += sample.toLong()
                        if (sample > peak) peak = sample
                        index += 2
                    }
                    val average = if (count > 1) energy / (count / 2) else 0
                    if (peak >= 900 && average >= 180) socket?.send("{\"user_audio_chunk\":\"${Base64.encodeToString(buffer.copyOf(count), Base64.NO_WRAP)}\"}")
                }
            }
        }.start()
    }

    override fun onDestroy() {
        recording = false; noiseSuppressor?.release(); echoCanceler?.release(); recorder?.release(); player?.release(); socket?.close(1000, "stopped"); client.dispatcher.executorService.shutdown()
        super.onDestroy()
    }
    override fun onBind(intent: Intent?): IBinder? = null
}

package com.vishal.kim

import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject
import java.net.URLEncoder

class KimClient(private val baseUrl: String, private val token: String, private val user: String = "") {
    fun getStatus(): String = request("GET", "/v1/status")
    fun getIntegrations(): String = request("GET", "/v1/integrations")
    fun getApprovals(): String = request("GET", "/v1/approvals")
    fun getVoiceSession(): String = request("GET", "/v1/voice/session")
    fun chat(message: String, conversationId: String, deviceContext: String = "Android phone", attachmentName: String? = null, attachmentText: String? = null): String {
        val body = JSONObject().put("message", message).put("conversation_id", conversationId).put("client", "android").put("device_context", deviceContext)
        if (!attachmentName.isNullOrBlank() && !attachmentText.isNullOrBlank()) body.put("attachments", org.json.JSONArray().put(JSONObject().put("name", attachmentName).put("text", attachmentText)))
        return request("POST", "/v1/chat", body.toString())
    }
    fun connectDesktop(): String = request(
        "POST", "/v1/device/connect", JSONObject().put("device_id", "laptop").put("capabilities", listOf("open_url", "open_app", "type_text", "press_key", "screenshot", "playwright_run")).toString()
    )
    fun control(action: String): String = request("POST", "/v1/control", "{\"action\":\"$action\"}")
    fun approve(id: String): String = request("POST", "/v1/approvals/$id/approve", "{}")
    fun reject(id: String): String = request("POST", "/v1/approvals/$id/reject", "{}")
    fun runTool(name: String, parameters: JSONObject): String = request(
        "POST", "/v1/tool", JSONObject().put("name", name).put("parameters", parameters).toString()
    )
    fun requestApproval(name: String, parameters: JSONObject, summary: String): String = request(
        "POST", "/v1/approvals", JSONObject().put("name", name).put("parameters", parameters).put("summary", summary).toString()
    )
    fun heartbeat(deviceId: String): String = request("POST", "/v1/device/heartbeat", JSONObject().put("device_id", deviceId).put("capabilities", listOf("open_url", "open_app", "notify", "media", "volume", "flashlight")).toString())
    fun nextDeviceCommand(deviceId: String): String = request("GET", "/v1/device/commands/next?device_id=${URLEncoder.encode(deviceId, "UTF-8")}")
    fun deviceResult(id: String, action: String, result: String, failed: Boolean): String = request("POST", "/v1/device/commands/$id/result", JSONObject().put("action", action).put("result", result).put("is_error", failed).toString())

    private fun request(method: String, path: String, body: String? = null): String {
        val connection = (URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection)
        connection.requestMethod = method
        connection.connectTimeout = 8000; connection.readTimeout = 12000
        connection.setRequestProperty("X-Kim-Pin", token)
        if (user.isNotBlank()) connection.setRequestProperty("X-Kim-User", user)
        connection.setRequestProperty("Content-Type", "application/json")
        if (body != null) { connection.doOutput = true; connection.outputStream.use { it.write(body.toByteArray()) } }
        val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
        return "${connection.responseCode}: ${stream?.bufferedReader()?.use { it.readText() } ?: "no response"}"
    }
}

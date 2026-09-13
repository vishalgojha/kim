package com.vishal.kim

import java.net.HttpURLConnection
import java.net.URL

class KimClient(private val baseUrl: String, private val token: String) {
    fun getStatus(): String = request("GET", "/v1/status")
    fun getApprovals(): String = request("GET", "/v1/approvals")
    fun control(action: String): String = request("POST", "/v1/control", "{\"action\":\"$action\"}")
    fun approve(id: String): String = request("POST", "/v1/approvals/$id/approve", "{}")

    private fun request(method: String, path: String, body: String? = null): String {
        val connection = (URL(baseUrl.trimEnd('/') + path).openConnection() as HttpURLConnection)
        connection.requestMethod = method
        connection.connectTimeout = 8000; connection.readTimeout = 12000
        connection.setRequestProperty("Authorization", "Bearer $token")
        connection.setRequestProperty("Content-Type", "application/json")
        if (body != null) { connection.doOutput = true; connection.outputStream.use { it.write(body.toByteArray()) } }
        val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
        return "${connection.responseCode}: ${stream?.bufferedReader()?.use { it.readText() } ?: "no response"}"
    }
}

package com.vishal.kim

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import org.json.JSONArray
import org.json.JSONObject

data class KimHistoryItem(val id: String, val title: String)

object KimPrefs {
    val users = listOf("Vishal", "Kapil")

    fun open(context: Context): SharedPreferences {
        val key = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        return EncryptedSharedPreferences.create(context, "kim", key, EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV, EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM)
    }

    fun deviceId(context: Context): String {
        val prefs = open(context)
        return prefs.getString("device_id", null) ?: java.util.UUID.randomUUID().toString().also {
            prefs.edit().putString("device_id", it).apply()
        }
    }

    fun activeUser(context: Context): String = open(context).getString("active_user", "Vishal") ?: "Vishal"

    fun setActiveUser(context: Context, user: String) {
        open(context).edit().putString("active_user", user).apply()
    }

    fun savePin(context: Context, user: String, value: String) {
        open(context).edit().putString("pin_${user.lowercase()}", value).apply()
    }

    fun pin(context: Context, user: String = activeUser(context)): String =
        open(context).getString("pin_${user.lowercase()}", "")
            ?.takeIf { it.isNotBlank() }
            ?: (open(context).getString("pin", "") ?: "")

    private fun historyKey(user: String) = "history_${user.lowercase()}"

    fun currentChatId(context: Context, user: String): String {
        val prefs = open(context)
        return prefs.getString("current_chat_${user.lowercase()}", null) ?: newChatId(context, user)
    }

    fun newChatId(context: Context, user: String): String {
        val id = java.util.UUID.randomUUID().toString()
        open(context).edit().putString("current_chat_${user.lowercase()}", id).apply()
        return id
    }

    fun setCurrentChat(context: Context, user: String, id: String) {
        open(context).edit().putString("current_chat_${user.lowercase()}", id).apply()
    }

    fun history(context: Context, user: String): List<KimHistoryItem> {
        val raw = open(context).getString(historyKey(user), "[]") ?: "[]"
        return runCatching {
            val array = JSONArray(raw)
            (0 until array.length()).map { index ->
                val item = array.getJSONObject(index)
                KimHistoryItem(item.optString("id"), item.optString("title", "New chat"))
            }.reversed()
        }.getOrDefault(emptyList())
    }

    fun messages(context: Context, user: String, id: String): List<Pair<Boolean, String>> {
        val raw = open(context).getString(historyKey(user), "[]") ?: "[]"
        return runCatching {
            val chats = JSONArray(raw)
            val chat = (0 until chats.length()).map { chats.getJSONObject(it) }.firstOrNull { it.optString("id") == id } ?: return emptyList()
            val items = chat.optJSONArray("messages") ?: return emptyList()
            (0 until items.length()).map { index ->
                val item = items.getJSONObject(index)
                item.optBoolean("user") to item.optString("text")
            }
        }.getOrDefault(emptyList())
    }

    fun appendMessage(context: Context, user: String, id: String, fromUser: Boolean, text: String) {
        val prefs = open(context)
        val chats = runCatching { JSONArray(prefs.getString(historyKey(user), "[]") ?: "[]") }.getOrDefault(JSONArray())
        var chat: JSONObject? = null
        for (index in 0 until chats.length()) if (chats.getJSONObject(index).optString("id") == id) chat = chats.getJSONObject(index)
        if (chat == null) {
            chat = JSONObject().put("id", id).put("title", if (fromUser) text.take(48) else "New chat").put("messages", JSONArray())
            chats.put(chat)
        }
        val storedChat = chat!!
        storedChat.optJSONArray("messages")!!.put(JSONObject().put("user", fromUser).put("text", text.take(20_000)))
        while (storedChat.optJSONArray("messages")!!.length() > 100) storedChat.optJSONArray("messages")!!.remove(0)
        while (chats.length() > 30) chats.remove(0)
        prefs.edit().putString(historyKey(user), chats.toString()).apply()
    }
}

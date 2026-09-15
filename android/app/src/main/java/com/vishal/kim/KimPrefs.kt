package com.vishal.kim

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

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
}

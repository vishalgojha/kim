package com.vishal.kim

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.*
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()
    private val prefs by lazy {
        val key = MasterKey.Builder(this).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
        EncryptedSharedPreferences.create(this, "kim", key, EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV, EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM)
    }
    private lateinit var token: EditText
    private lateinit var status: TextView
    private val baseUrl = "https://app.vishalojha.me"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 40, 32, 24) }
        TextView(this).apply { text = "Kim"; textSize = 32f }.also(root::addView)
        TextView(this).apply { text = "Android control panel"; textSize = 16f }.also(root::addView)
        token = EditText(this).apply { hint = "KIM_REMOTE_TOKEN"; setText(prefs.getString("token", "")); inputType = 0x81 }
        root.addView(token)
        val save = Button(this).apply { text = "Save token and connect"; setOnClickListener { prefs.edit().putString("token", token.text.toString()).apply(); refresh() } }
        root.addView(save)
        status = TextView(this).apply { text = "Not connected"; textSize = 15f; setPadding(0, 24, 0, 24) }
        root.addView(status)
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(Button(this).apply { text = "Pause"; setOnClickListener { control("pause") } }, LinearLayout.LayoutParams(0, -2, 1f))
        row.addView(Button(this).apply { text = "Wake"; setOnClickListener { control("wake") } }, LinearLayout.LayoutParams(0, -2, 1f))
        root.addView(row)
        root.addView(Button(this).apply { text = "Start listening service"; setOnClickListener { startListening() } })
        setContentView(root)
    }

    private fun client() = KimClient(baseUrl, token.text.toString().trim())
    private fun refresh() { executor.execute { val value = runCatching { client().getStatus() }.getOrElse { it.message ?: "connection failed" }; runOnUiThread { status.text = value } } }
    private fun control(action: String) { executor.execute { val value = runCatching { client().control(action) }.getOrElse { it.message ?: "request failed" }; runOnUiThread { status.text = value } } }
    private fun startListening() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 5)
        else startForegroundService(Intent(this, KimForegroundService::class.java))
    }
}

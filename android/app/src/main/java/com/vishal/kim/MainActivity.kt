package com.vishal.kim

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.*
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit
import java.util.concurrent.Executors
import org.json.JSONObject

class MainActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()
    private val prefs by lazy { KimPrefs.open(this) }
    private lateinit var pin: EditText
    private lateinit var status: TextView
    private lateinit var approvalsBox: LinearLayout
    private val baseUrl = "https://app.vishalojha.me"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        scheduleApprovalWatcher()
        if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 6)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 40, 32, 24) }
        TextView(this).apply { text = "Kim"; textSize = 32f }.also(root::addView)
        TextView(this).apply { text = "Android control panel"; textSize = 16f }.also(root::addView)
        pin = EditText(this).apply { hint = "Kim PIN"; setText(prefs.getString("pin", "")); inputType = 0x81; maxLines = 1 }
        root.addView(pin)
        val save = Button(this).apply { text = "Save PIN and connect"; setOnClickListener { prefs.edit().putString("pin", pin.text.toString()).apply(); refresh() } }
        root.addView(save)
        status = TextView(this).apply { text = "Not connected"; textSize = 15f; setPadding(0, 24, 0, 24) }
        root.addView(status)
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(Button(this).apply { text = "Pause"; setOnClickListener { control("pause") } }, LinearLayout.LayoutParams(0, -2, 1f))
        row.addView(Button(this).apply { text = "Wake"; setOnClickListener { control("wake") } }, LinearLayout.LayoutParams(0, -2, 1f))
        root.addView(row)
        root.addView(Button(this).apply { text = "Start Kim voice"; setOnClickListener { startListening() } })
        root.addView(Button(this).apply { text = "Stop Kim voice"; setOnClickListener { stopService(Intent(this@MainActivity, KimVoiceService::class.java)) } })
        root.addView(Button(this).apply { text = "Refresh approvals"; setOnClickListener { refreshApprovals() } })
        approvalsBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(approvalsBox)
        root.addView(Button(this).apply { text = "Check integrations"; setOnClickListener { refreshIntegrations() } })
        root.addView(Button(this).apply { text = "Start voice session"; setOnClickListener { startVoiceSession() } })
        setContentView(root)
    }

    private fun client() = KimClient(baseUrl, pin.text.toString().trim())
    private fun refresh() { executor.execute { val value = runCatching { client().getStatus() }.getOrElse { it.message ?: "connection failed" }; runOnUiThread { status.text = value } } }
    private fun refreshIntegrations() { executor.execute { val value = runCatching { client().getIntegrations() }.getOrElse { it.message ?: "request failed" }; runOnUiThread { status.text = value } } }
    private fun control(action: String) { executor.execute { val value = runCatching { client().control(action) }.getOrElse { it.message ?: "request failed" }; runOnUiThread { status.text = value } } }
    private fun refreshApprovals() {
        executor.execute {
            val result = runCatching { client().getApprovals() }
            val value = result.getOrElse { it.message ?: "request failed" }
            runOnUiThread {
                status.text = if (result.isSuccess) "Approvals refreshed" else value
                renderApprovals(value)
            }
        }
    }
    private fun renderApprovals(raw: String) {
        approvalsBox.removeAllViews()
        try {
            val approvals = JSONObject(raw.substringAfter(": ")).optJSONArray("approvals")
            if (approvals == null || approvals.length() == 0) {
                approvalsBox.addView(TextView(this).apply { text = "No approvals waiting"; setPadding(0, 12, 0, 12) })
                return
            }
            for (i in 0 until approvals.length()) {
                val item = approvals.getJSONObject(i)
                val id = item.optString("id")
                val card = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(0, 12, 0, 12) }
                card.addView(TextView(this).apply { text = "${item.optString("name")}\n${item.optString("summary")}\nStatus: ${item.optString("status")}"; textSize = 15f })
                if (item.optString("status") == "pending") {
                    val buttons = LinearLayout(this)
                    buttons.addView(Button(this).apply { text = "Approve"; setOnClickListener { resolveApproval(id, true) } }, LinearLayout.LayoutParams(0, -2, 1f))
                    buttons.addView(Button(this).apply { text = "Reject"; setOnClickListener { resolveApproval(id, false) } }, LinearLayout.LayoutParams(0, -2, 1f))
                    card.addView(buttons)
                }
                approvalsBox.addView(card)
            }
        } catch (_: Exception) {
            approvalsBox.addView(TextView(this).apply { text = raw })
        }
    }
    private fun resolveApproval(id: String, approve: Boolean) {
        executor.execute {
            val value = runCatching { if (approve) client().approve(id) else client().reject(id) }.getOrElse { it.message ?: "request failed" }
            runOnUiThread { status.text = value; refreshApprovals() }
        }
    }
    private fun startVoiceSession() { executor.execute { val value = runCatching { client().getVoiceSession() }.getOrElse { it.message ?: "voice unavailable" }; runOnUiThread { status.text = value } } }
    private fun startListening() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 5)
        else startForegroundService(Intent(this, KimVoiceService::class.java))
    }
    private fun scheduleApprovalWatcher() {
        val request = PeriodicWorkRequestBuilder<KimApprovalWorker>(15, TimeUnit.MINUTES).build()
        WorkManager.getInstance(this).enqueueUniquePeriodicWork("kim-approval-watcher", ExistingPeriodicWorkPolicy.KEEP, request)
    }
}

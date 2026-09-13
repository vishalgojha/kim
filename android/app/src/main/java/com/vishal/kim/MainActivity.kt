package com.vishal.kim

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
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
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 32, 24, 24) }
        TextView(this).apply { text = "Kim"; textSize = 34f }.also(root::addView)
        TextView(this).apply { text = "Your personal assistant"; textSize = 17f }.also(root::addView)
        pin = EditText(this).apply { hint = "Kim PIN"; setText(prefs.getString("pin", "")); inputType = 0x81; maxLines = 1 }
        root.addView(pin)
        val save = Button(this).apply { text = "Connect"; setOnClickListener { prefs.edit().putString("pin", pin.text.toString()).apply(); refresh() } }
        root.addView(save)
        status = TextView(this).apply { text = "Not connected"; textSize = 16f; setPadding(0, 16, 0, 16) }
        root.addView(status)
        root.addView(Button(this).apply { text = "Talk to Kim"; setOnClickListener { startListening() } })
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        row.addView(Button(this).apply { text = "Pause"; setOnClickListener { control("pause") } }, LinearLayout.LayoutParams(0, -2, 1f))
        row.addView(Button(this).apply { text = "Wake"; setOnClickListener { control("wake") } }, LinearLayout.LayoutParams(0, -2, 1f))
        root.addView(row)
        root.addView(TextView(this).apply { text = "Approvals"; textSize = 22f; setPadding(0, 22, 0, 4) })
        root.addView(Button(this).apply { text = "Refresh approvals"; setOnClickListener { refreshApprovals() } })
        approvalsBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(approvalsBox)
        root.addView(Button(this).apply { text = "Request an action"; setOnClickListener { showRequestDialog() } })
        root.addView(Button(this).apply { text = "Check integrations"; setOnClickListener { refreshIntegrations() } })
        root.addView(Button(this).apply { text = "Stop listening"; setOnClickListener { stopService(Intent(this@MainActivity, KimVoiceService::class.java)) } })
        val screen = ScrollView(this).apply { setBackgroundColor(Color.BLACK); addView(root) }
        theme(screen)
        setContentView(screen)
    }

    private fun theme(view: View) {
        when (view) {
            is Button -> {
                view.setTextColor(Color.BLACK)
                view.background = GradientDrawable().apply { setColor(Color.rgb(237, 237, 237)); cornerRadius = 10f }
                view.setPadding(18, 12, 18, 12)
            }
            is EditText -> {
                view.setTextColor(Color.rgb(237, 237, 237))
                view.setHintTextColor(Color.rgb(161, 161, 170))
                view.backgroundTintList = ColorStateList.valueOf(Color.rgb(82, 82, 91))
            }
            is TextView -> view.setTextColor(if (view.textSize >= 22f) Color.rgb(237, 237, 237) else Color.rgb(161, 161, 170))
        }
        if (view is ViewGroup) for (i in 0 until view.childCount) theme(view.getChildAt(i))
    }

    private fun client() = KimClient(baseUrl, pin.text.toString().trim())
    private fun refresh() { executor.execute { val value = runCatching { client().getStatus() }.getOrElse { it.message ?: "connection failed" }; runOnUiThread { status.text = value } } }
    private fun refreshIntegrations() { executor.execute { val value = runCatching { client().getIntegrations() }.getOrElse { it.message ?: "request failed" }; runOnUiThread { status.text = value } } }
    private fun requestApproval(name: String, rawParameters: String, summary: String) {
        executor.execute {
            val value = runCatching {
                val parameters = JSONObject(rawParameters.ifBlank { "{}" })
                client().requestApproval(name.trim(), parameters, summary.trim().ifBlank { "Requested from Android" })
            }.getOrElse { it.message ?: "request failed" }
            runOnUiThread { status.text = value; refreshApprovals() }
        }
    }
    private fun showRequestDialog() {
        val form = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 0, 24, 0) }
        val name = EditText(this).apply { hint = "Action (e.g. send email)" }
        val parameters = EditText(this).apply { hint = "Details as JSON (optional)"; setText("{}"); minLines = 3; gravity = android.view.Gravity.TOP }
        val summary = EditText(this).apply { hint = "What should Kim do?" }
        form.addView(name); form.addView(parameters); form.addView(summary)
        AlertDialog.Builder(this)
            .setTitle("Request approval")
            .setView(form)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Submit") { _, _ -> requestApproval(name.text.toString(), parameters.text.toString(), summary.text.toString()) }
            .show()
    }
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

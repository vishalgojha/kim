package com.vishal.kim

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.provider.Settings
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
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 38, 24, 28) }
        pin = EditText(this).apply { setText(prefs.getString("pin", "")) }
        TextView(this).apply { text = "Kim"; textSize = 36f }.also(root::addView)
        TextView(this).apply { text = "Good to see you. What can I take care of?"; textSize = 18f; setPadding(0, 4, 0, 24) }.also(root::addView)
        status = TextView(this).apply { text = "Ready when you are"; textSize = 15f; setPadding(0, 12, 0, 12) }
        root.addView(status)
        root.addView(Button(this).apply { text = "Talk to Kim"; setOnClickListener { startListening() } })
        root.addView(TextView(this).apply { text = "Try asking"; textSize = 21f; setPadding(0, 20, 0, 8) })
        root.addView(Button(this).apply { text = "Show me today's priorities"; setOnClickListener { runQuickAction("gmail_today", "Checking your day…") } })
        root.addView(Button(this).apply { text = "What's on my calendar?"; setOnClickListener { runQuickAction("calendar_upcoming", "Checking your calendar…") } })
        root.addView(Button(this).apply { text = "Help me send an email"; setOnClickListener { showEmailDialog() } })
        root.addView(TextView(this).apply { text = "Requests"; textSize = 21f; setPadding(0, 20, 0, 8) })
        approvalsBox = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(approvalsBox)
        root.addView(Button(this).apply { text = "Review requests"; setOnClickListener { refreshApprovals() } })
        root.addView(Button(this).apply { text = "Settings"; setOnClickListener { showSettings() } })
        val screen = ScrollView(this).apply { setBackgroundColor(Color.BLACK); addView(root) }
        theme(screen)
        setContentView(screen)
        if (prefs.getString("pin", "").isNullOrBlank()) showSettings()
    }

    private fun runQuickAction(name: String, label: String) {
        status.text = label
        executor.execute { val value = runCatching { client().runTool(name, JSONObject()) }.getOrElse { it.message ?: "Kim is unavailable" }; runOnUiThread { status.text = value } }
    }

    private fun showSettings() {
        val form = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 0, 24, 0) }
        val setupPin = EditText(this).apply { hint = "Private Kim PIN"; setText(prefs.getString("pin", "")); inputType = 0x81; maxLines = 1 }
        form.addView(setupPin)
        form.addView(Button(this).apply { text = "Connect this phone"; setOnClickListener { prefs.edit().putString("pin", setupPin.text.toString()).apply(); startDeviceBridge(); refresh(); status.text = "Phone connected" } })
        form.addView(Button(this).apply { text = "Allow microphone"; setOnClickListener { requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 5) } })
        form.addView(Button(this).apply { text = "Allow notifications"; setOnClickListener { if (android.os.Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 6) } })
        form.addView(Button(this).apply { text = "Notification access"; setOnClickListener { startActivity(Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS")) } })
        form.addView(Button(this).apply { text = "Accessibility controls"; setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) } })
        AlertDialog.Builder(this).setTitle("Kim settings").setView(form).setNegativeButton("Close", null).show()
    }

    private fun showEmailDialog() {
        val form = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 0, 24, 0) }
        val to = EditText(this).apply { hint = "To"; inputType = android.text.InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS }
        val subject = EditText(this).apply { hint = "Subject" }
        val body = EditText(this).apply { hint = "Message"; minLines = 5; gravity = android.view.Gravity.TOP }
        form.addView(to); form.addView(subject); form.addView(body)
        AlertDialog.Builder(this).setTitle("Prepare an email").setView(form).setNegativeButton("Cancel", null).setPositiveButton("Ask Kim to send") { _, _ ->
            val params = JSONObject().put("to", to.text.toString()).put("subject", subject.text.toString()).put("body", body.text.toString())
            requestApproval("gmail_send", params.toString(), "Send email to ${to.text}")
        }.show()
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
    private fun startDeviceBridge() {
        startForegroundService(Intent(this, KimForegroundService::class.java))
    }
    private fun scheduleApprovalWatcher() {
        val request = PeriodicWorkRequestBuilder<KimApprovalWorker>(15, TimeUnit.MINUTES).build()
        WorkManager.getInstance(this).enqueueUniquePeriodicWork("kim-approval-watcher", ExistingPeriodicWorkPolicy.KEEP, request)
    }
}

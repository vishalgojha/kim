package com.vishal.kim

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import androidx.core.app.NotificationCompat
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONObject

class KimApprovalWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result {
        val pin = KimPrefs.open(applicationContext).getString("pin", "") ?: return Result.success()
        if (pin.isBlank()) return Result.success()
        return try {
            val raw = KimClient("https://app.vishalojha.me", pin).getApprovals()
            val json = JSONObject(raw.substringAfter(": "))
            val approvals = json.optJSONArray("approvals") ?: return Result.success()
            var pending = 0
            for (i in 0 until approvals.length()) if (approvals.getJSONObject(i).optString("status") == "pending") pending++
            if (pending > 0) notifyPending(pending)
            Result.success()
        } catch (_: Exception) { Result.retry() }
    }

    private fun notifyPending(count: Int) {
        val manager = applicationContext.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("kim_approvals", "Kim approvals", NotificationManager.IMPORTANCE_DEFAULT))
        manager.notify(2001, NotificationCompat.Builder(applicationContext, "kim_approvals")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("Kim needs your approval")
            .setContentText("$count action${if (count == 1) "" else "s"} waiting")
            .setAutoCancel(true).build())
    }
}

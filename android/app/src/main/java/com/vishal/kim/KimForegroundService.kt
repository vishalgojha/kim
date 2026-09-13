package com.vishal.kim

import android.app.*
import android.content.Intent
import android.os.IBinder

class KimForegroundService : Service() {
    override fun onCreate() {
        super.onCreate()
        val channel = NotificationChannel("kim", "Kim", NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        val notification = Notification.Builder(this, "kim")
            .setContentTitle("Kim is listening")
            .setContentText("Tap Kim to return to the control panel")
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setOngoing(true).build()
        startForeground(1001, notification)
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int) = START_NOT_STICKY
    override fun onBind(intent: Intent?): IBinder? = null
}

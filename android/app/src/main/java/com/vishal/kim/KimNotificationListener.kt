package com.vishal.kim

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

/** Permission-gated notification bridge; content stays on-device until Kim requests it. */
class KimNotificationListener : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) { }
}

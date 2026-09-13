package com.vishal.kim

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent

/** Explicitly disabled until the user enables it in Android Settings. */
class KimAccessibilityService : AccessibilityService() {
    override fun onAccessibilityEvent(event: AccessibilityEvent?) { }
    override fun onInterrupt() { }
}

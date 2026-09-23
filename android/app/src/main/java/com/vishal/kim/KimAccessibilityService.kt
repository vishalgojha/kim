package com.vishal.kim

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Enables Kim to type text and press keys in any app. The service must be
 * turned on in Android Settings > Accessibility (Kim). When enabled, device
 * commands for type_text / press_key are executed here so they work even
 * where the shell `input` command is blocked by the OEM.
 */
class KimAccessibilityService : AccessibilityService() {

    companion object {
        @Volatile private var instance: KimAccessibilityService? = null
        val isConnected: Boolean get() = instance != null

        fun typeText(text: String): Boolean {
            val s = instance ?: return false
            return s.dispatchTypeText(text)
        }

        fun pressKey(key: String): Boolean {
            val s = instance ?: return false
            return s.dispatchPressKey(key)
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        instance = null
        return super.onUnbind(intent)
    }

    private fun dispatchTypeText(text: String): Boolean {
        if (text.isEmpty()) return true
        val target = focusedInput() ?: return false
        val args = Bundle()
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        return target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }

    private fun dispatchPressKey(key: String): Boolean {
        return when (key.trim().lowercase()) {
            "back", "keycode_back" -> performGlobalAction(GLOBAL_ACTION_BACK)
            "home", "keycode_home" -> performGlobalAction(GLOBAL_ACTION_HOME)
            "menu", "keycode_menu" -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            "tab", "keycode_tab" -> {
                // swipe from upper-left corner to cycle focus
                val path = Path().apply { moveTo(1f, 1f); lineTo(1f, 1f) }
                val gesture = GestureDescription.Builder().addStroke(GestureDescription.StrokeDescription(path, 0, 1)).build()
                dispatchGesture(gesture, null, null)
            }
            else -> false
        }
    }

    private fun focusedInput(): AccessibilityNodeInfo? {
        val root = rootInActiveWindow ?: return null
        root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.let { return it }
        return findEditable(root)
    }

    private fun findEditable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isEditable && node.isVisibleToUser) return node
        for (i in 0 until node.childCount) {
            node.getChild(i)?.let { child ->
                findEditable(child)?.let { return it }
            }
        }
        return null
    }
}
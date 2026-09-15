package com.vishal.kim

import android.content.Context
import android.graphics.*
import android.view.View

class KimOrbView(context: Context) : View(context) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f; val cy = height / 2f; val radius = minOf(width, height) * .27f
        paint.shader = RadialGradient(cx, cy, radius * 1.7f, intArrayOf(0x332D333D, 0x221A1D23, 0x00090A0C), null, Shader.TileMode.CLAMP)
        canvas.drawCircle(cx, cy, radius * 1.7f, paint)
        paint.shader = LinearGradient(cx - radius, cy - radius, cx + radius, cy + radius, intArrayOf(0xFFE0E3E7.toInt(), 0xFF9CA3AD.toInt(), 0xFF626B78.toInt()), null, Shader.TileMode.CLAMP)
        canvas.drawRoundRect(cx-radius, cy-radius*.72f, cx+radius, cy+radius*.72f, radius*.55f, radius*.55f, paint)
        paint.shader = null; paint.color = 0xFF10152B.toInt()
        canvas.drawRoundRect(cx-radius*.78f, cy-radius*.45f, cx+radius*.78f, cy+radius*.45f, radius*.35f, radius*.35f, paint)
        paint.color = 0xFFDDE3EA.toInt(); canvas.drawRoundRect(cx-radius*.42f, cy-radius*.13f, cx-radius*.18f, cy+radius*.13f, 20f, 20f, paint); canvas.drawRoundRect(cx+radius*.18f, cy-radius*.13f, cx+radius*.42f, cy+radius*.13f, 20f, 20f, paint)
    }
}

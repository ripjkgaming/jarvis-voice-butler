package dev.jarvis.link

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
import kotlin.math.cos
import kotlin.math.sin
import kotlin.random.Random

/**
 * Jarvis orb: ~120 particles on slow decaying orbits around a glowing
 * core, alpha by radius, gentle pulse. Zero dependencies, ~60fps via
 * postOnAnimation. Pure eye candy — TalkFragment hosts it above the PTT
 * button. Redraws only while attached to a window.
 */
class OrbView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {
    private data class P(
        var angle: Double, var radius: Float, var speed: Float,
        var size: Float, var tw: Float,
    )

    private val particles = List(120) {
        P(
            angle = Random.nextDouble(0.0, Math.PI * 2),
            radius = Random.nextFloat(),
            speed = Random.nextFloat() * 0.35f + 0.05f,
            size = Random.nextFloat() * 4f + 1.5f,
            tw = Random.nextFloat() * 6.28f,
        )
    }
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var t = 0f
    private val step = object : Runnable {
        override fun run() {
            t += 0.016f
            invalidate()
            if (isAttachedToWindow) postOnAnimation(this)
        }
    }

    /** 0=idle cyan … 1=speaking bright. TalkFragment drives this. */
    var energy: Float = 0.35f

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        postOnAnimation(step)
    }

    override fun onDetachedFromWindow() {
        removeCallbacks(step)
        super.onDetachedFromWindow()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val cx = width / 2f
        val cy = height / 2f
        val base = minOf(cx, cy)
        if (base <= 0) return
        val pulse = 1f + 0.05f * sin(t * 2f) * (0.5f + energy)
        // Core glow: layered circles, cheapest bloom available.
        for ((i, r, a) in listOf(
            Triple(0.42f, "#002CF2", 90),
            Triple(0.30f, "#0E7CF2", 130),
            Triple(0.18f, "#1FD5F9", 200),
        )) {
            paint.color = Color.parseColor(r)
            paint.alpha = a
            canvas.drawCircle(cx, cy, base * i * pulse, paint)
        }
        // Orbitals.
        for (p in particles) {
            p.angle += p.speed * 0.016 * (0.6 + energy)
            p.tw += 0.05f
            val rr = base * (0.25f + 0.70f * p.radius) * pulse
            val x = cx + (rr * cos(p.angle)).toFloat()
            val y = cy + (rr * sin(p.angle)).toFloat()
            val alpha = (140 * (1f - p.radius * 0.6f) * (0.6f + 0.4f * sin(p.tw))).toInt()
                .coerceIn(20, 200)
            paint.color = if (p.radius > 0.6f) Color.parseColor("#1FD5F9")
            else Color.parseColor("#7FB8FF")
            paint.alpha = alpha
            canvas.drawCircle(x, y, p.size * (0.7f + energy * 0.6f), paint)
        }
    }
}

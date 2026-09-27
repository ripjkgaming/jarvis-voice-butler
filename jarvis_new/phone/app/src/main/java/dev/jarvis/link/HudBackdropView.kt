package dev.jarvis.link

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import kotlin.math.sin
import kotlin.random.Random

/**
 * Living app backdrop (v1.7 performance build): same pixels, ~1/3 cost.
 * Near-black base, slow-drifting grid, faint rising motes, breathing
 * edge vignette. Vignette gradient is cached on size change (was
 * allocated every frame); the loop runs at ~30fps with double-step
 * motion so the drift speed is unchanged; fully stops when hidden,
 * detached, or screen-off. One instance lives in MainActivity behind
 * the fragment host; fragments stay transparent.
 */
class HudBackdropView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private data class Mote(
        var x: Float, var y: Float, var vy: Float,
        var size: Float, var tw: Float, var cyan: Boolean,
    )

    private val motes = mutableListOf<Mote>()
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var vignette: LinearGradient? = null
    private var t = 0f
    private var gridOff = 0f
    private var frame = 0
    private var running = false

    private val step = object : Runnable {
        override fun run() {
            if (!running) return
            // ~30fps: skip every other vsync, double the motion step.
            frame++
            if (frame % 2 == 0) {
                t += 0.032f
                gridOff = (gridOff + 0.7f) % 64f
                for (m in motes) {
                    m.y -= m.vy * 2f
                    m.tw += 0.08f
                    if (m.y < -8f) {
                        m.y = height + 8f
                        m.x = Random.nextFloat() * width
                    }
                }
                invalidate()
            }
            if (running) postOnAnimation(this)
        }
    }

    override fun onWindowVisibilityChanged(visibility: Int) {
        super.onWindowVisibilityChanged(visibility)
        if (visibility == VISIBLE && isAttachedToWindow) start() else stop()
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        start()
    }

    override fun onDetachedFromWindow() {
        stop()
        super.onDetachedFromWindow()
    }

    private fun start() {
        if (running) return
        running = true
        postOnAnimation(step)
    }

    private fun stop() {
        running = false
        removeCallbacks(step)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        motes.clear()
        repeat(42) {
            motes.add(
                Mote(
                    x = Random.nextFloat() * w,
                    y = Random.nextFloat() * h,
                    vy = Random.nextFloat() * 0.5f + 0.12f,
                    size = Random.nextFloat() * 2.6f + 0.8f,
                    tw = Random.nextFloat() * 6.28f,
                    cyan = Random.nextFloat() < 0.7f,
                )
            )
        }
        // Cache the vignette once (was a per-frame allocation).
        vignette = if (w > 0 && h > 0) LinearGradient(
            0f, 0f, 0f, h.toFloat(),
            intArrayOf(
                Color.argb(36, 0x1F, 0xD5, 0xF9),
                Color.TRANSPARENT,
                Color.TRANSPARENT,
                Color.argb(36, 0x1F, 0xD5, 0xF9),
            ),
            floatArrayOf(0f, 0.18f, 0.82f, 1f),
            Shader.TileMode.CLAMP,
        ) else null
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0 || h <= 0) return
        // Base.
        paint.style = Paint.Style.FILL
        paint.color = BLACK
        paint.alpha = 255
        canvas.drawRect(0f, 0f, w, h, paint)
        // Perspective grid: verticals static, horizontals drift down.
        paint.color = CYAN
        paint.strokeWidth = 1f
        var gx = 0f
        while (gx <= w) {
            paint.alpha = 10
            canvas.drawLine(gx, 0f, gx, h, paint)
            gx += 64f
        }
        var gy = gridOff - 64f
        while (gy <= h) {
            // Fade with depth: stronger near the bottom.
            paint.alpha = (6 + 14 * (gy / h).coerceIn(0f, 1f)).toInt()
            canvas.drawLine(0f, gy, w, gy, paint)
            gy += 64f
        }
        // Rising motes (pre-parsed colors: no parseColor per frame).
        paint.style = Paint.Style.FILL
        for (m in motes) {
            val a = (60 * (0.5f + 0.5f * sin(m.tw))).toInt().coerceIn(8, 70)
            paint.color = if (m.cyan) CYAN else WHITE
            paint.alpha = a
            canvas.drawCircle(m.x, m.y, m.size, paint)
        }
        // Breathing edge vignette from the cached gradient.
        val breathe = 0.5f + 0.5f * sin(t * 0.6f)
        paint.shader = vignette
        paint.alpha = (200 + 55 * breathe).toInt().coerceIn(0, 255)
        canvas.drawRect(0f, 0f, w, h, paint)
        paint.shader = null
        paint.alpha = 255
    }

    companion object {
        private const val CYAN = 0xFF1FD5F9.toInt()
        private const val WHITE = 0xFFFFFFFF.toInt()
        private const val BLACK = 0xFF000000.toInt()
    }
}

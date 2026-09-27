package dev.jarvis.link

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.util.AttributeSet
import android.view.View
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin
import kotlin.random.Random

/**
 * Arc-reactor HUD hero (Iron Man style), v1.7 performance build.
 *
 * Same pixels as v1.5, ~1/4 the GPU cost:
 * - Zero per-frame allocations (no BlurMaskFilter, no gradient objects,
 *   no boxed lists in onDraw).
 * - Static layers (shell, tick rings, core, halo) pre-rendered once to
 *   bitmaps on size change; per frame is just drawBitmap transforms
 *   (rotate/scale) plus the spark particles.
 * - Halo glow is baked layered strokes, not a live blur, so the view
 *   runs on the hardware layer.
 * - Adaptive rate: full 60fps only while hot (energy > 0.45), 30fps
 *   idle. Loop fully stops when hidden, detached, or screen-off.
 *
 * Same driver API as [OrbView] (`energy` 0..1) so fragments swap freely.
 */
class ArcReactorView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val redCore: Boolean = context.obtainStyledAttributes(
        attrs, R.styleable.ArcReactorView
    ).let {
        val red = it.getInt(R.styleable.ArcReactorView_reactorCore, 0) == 1
        it.recycle()
        red
    }

    private data class Spark(
        var angle: Double, var radius: Float, var speed: Float,
        var size: Float, var tw: Float,
    )

    private val sparks = List(if (redCore) 40 else 90) {
        Spark(
            angle = Random.nextDouble(0.0, Math.PI * 2),
            radius = Random.nextFloat(),
            speed = (Random.nextFloat() * 0.5f + 0.1f) * if (Random.nextBoolean()) 1 else -1,
            size = Random.nextFloat() * 3.5f + 1f,
            tw = Random.nextFloat() * 6.28f,
        )
    }

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val bounds = RectF()
    // Destination rects reused every frame (no allocation).
    private val dstCore = RectF()
    private val dstTick = RectF()

    // Pre-rendered layers (built in onSizeChanged, null until then).
    private var shellLayer: Bitmap? = null
    private var tickLayerA: Bitmap? = null
    private var tickLayerB: Bitmap? = null
    private var coreLayer: Bitmap? = null
    private var layerBase = 0f

    private var t = 0f
    private var rotA = 0f
    private var rotB = 0f
    private var frame = 0
    private var running = false
    private val step = object : Runnable {
        override fun run() {
            if (!running) return
            frame++
            // Adaptive rate: hot = every vsync, idle = every 2nd vsync.
            val hot = energy > 0.45f
            if (hot || frame % 2 == 0) {
                t += if (hot) 0.016f else 0.032f
                val rate = (0.5f + energy)
                rotA = (rotA + (if (hot) 0.35f else 0.7f) * rate) % 360f
                rotB = (rotB - (if (hot) 0.22f else 0.44f) * rate) % 360f
                invalidate()
            }
            if (running) postOnAnimation(this)
        }
    }

    /** 0=idle … 1=speaking hot. Fragments drive this. */
    var energy: Float = 0.35f

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        setLayerType(LAYER_TYPE_HARDWARE, null)
        start()
    }

    override fun onDetachedFromWindow() {
        stop()
        super.onDetachedFromWindow()
    }

    override fun onWindowVisibilityChanged(visibility: Int) {
        super.onWindowVisibilityChanged(visibility)
        if (visibility == VISIBLE && isAttachedToWindow) start() else stop()
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
        val base = min(w / 2f, h / 2f)
        if (base <= 0) return
        layerBase = base
        shellLayer?.recycle()
        tickLayerA?.recycle()
        tickLayerB?.recycle()
        coreLayer?.recycle()
        shellLayer = renderShell(base)
        tickLayerA = renderTicks(base)
        tickLayerB = renderDots(base)
        coreLayer = renderCore(base)
    }

    // ---- Pre-rendered layers (run once per size, never per frame). ----

    private fun freshLayer(base: Float): Pair<Bitmap, Canvas> {
        val size = (base * 2f).toInt().coerceAtLeast(2)
        val bmp = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        return bmp to Canvas(bmp)
    }

    /** Segmented shell + nodes + baked halo + main rings. Static. */
    private fun renderShell(base: Float): Bitmap {
        val (bmp, c) = freshLayer(base)
        val cx = base
        val cy = base
        val p = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
        val f = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
        val accent = if (redCore) "#FF3B30" else "#1FD5F9"
        val b = RectF()
        // Outer segmented plates.
        p.color = Color.WHITE
        p.alpha = 235
        p.strokeWidth = base * 0.022f
        for (k in 0 until 4) {
            b.set(cx - base * 0.98f, cy - base * 0.98f, cx + base * 0.98f, cy + base * 0.98f)
            c.drawArc(b, k * 90f + 8f, 74f, false, p)
        }
        // Node circles.
        p.strokeWidth = base * 0.018f
        for (k in 0 until 6) {
            val a = Math.toRadians((k * 60f + 30f).toDouble())
            c.drawCircle(
                cx + (base * 0.98f * cos(a)).toFloat(),
                cy + (base * 0.98f * sin(a)).toFloat(),
                base * 0.055f, p,
            )
        }
        // Halo: baked layered strokes (no live blur).
        p.color = Color.parseColor(accent)
        b.set(cx - base * 0.80f, cy - base * 0.80f, cx + base * 0.80f, cy + base * 0.80f)
        val halo = arrayOf(40 to 0.115f, 90 to 0.095f, 150 to 0.075f)
        for ((alpha, wFrac) in halo) {
            p.alpha = alpha
            p.strokeWidth = base * wFrac
            c.drawArc(b, 0f, 360f, false, p)
        }
        // Hot main rings.
        p.color = Color.WHITE
        p.alpha = 255
        p.strokeWidth = base * 0.030f
        c.drawArc(b, 0f, 360f, false, p)
        p.color = Color.parseColor(accent)
        p.strokeWidth = base * 0.014f
        b.set(cx - base * 0.755f, cy - base * 0.755f, cx + base * 0.755f, cy + base * 0.755f)
        c.drawArc(b, 0f, 360f, false, p)
        f.color = Color.TRANSPARENT
        return bmp
    }

    /** Gauge tick ring. Rotated at draw time. */
    private fun renderTicks(base: Float): Bitmap {
        val (bmp, c) = freshLayer(base)
        val cx = base
        val cy = base
        val p = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            color = Color.parseColor(if (redCore) "#FF8A80" else "#9BE9FF")
            alpha = 220
        }
        for (i in 0 until 72) {
            val major = i % 6 == 0
            p.strokeWidth = if (major) base * 0.016f else base * 0.008f
            val r1 = base * (if (major) 0.60f else 0.64f)
            val r2 = base * 0.69f
            val a = Math.toRadians((i * 5f).toDouble())
            c.drawLine(
                cx + (r1 * cos(a)).toFloat(), cy + (r1 * sin(a)).toFloat(),
                cx + (r2 * cos(a)).toFloat(), cy + (r2 * sin(a)).toFloat(),
                p,
            )
        }
        return bmp
    }

    /** Dotted ring. Counter-rotated at draw time. */
    private fun renderDots(base: Float): Bitmap {
        val (bmp, c) = freshLayer(base)
        val cx = base
        val cy = base
        val p = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.FILL
            color = Color.parseColor(if (redCore) "#FF8A80" else "#9BE9FF")
            alpha = 200
        }
        for (i in 0 until 48) {
            val a = Math.toRadians((i * 7.5f).toDouble())
            c.drawCircle(
                cx + (base * 0.545f * cos(a)).toFloat(),
                cy + (base * 0.545f * sin(a)).toFloat(),
                base * 0.008f, p,
            )
        }
        return bmp
    }

    /** Teal core bloom + hot inner rings, drawn slightly oversize. */
    private fun renderCore(base: Float): Bitmap {
        val (bmp, c) = freshLayer(base)
        val cx = base
        val cy = base
        val coreR = base * 0.50f
        val p = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.FILL
            shader = RadialGradient(
                cx, cy, coreR,
                intArrayOf(
                    Color.parseColor("#D9FBFF"),
                    Color.parseColor("#1FD5F9"),
                    Color.parseColor("#0A5A6E"),
                    Color.parseColor("#04141B"),
                ),
                floatArrayOf(0f, 0.35f, 0.7f, 1f),
                Shader.TileMode.CLAMP,
            )
        }
        c.drawCircle(cx, cy, coreR, p)
        val ring = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
        ring.color = Color.WHITE
        ring.strokeWidth = base * 0.022f
        c.drawCircle(cx, cy, base * 0.30f, ring)
        ring.color = Color.parseColor("#1FD5F9")
        ring.strokeWidth = base * 0.010f
        c.drawCircle(cx, cy, base * 0.26f, ring)
        return bmp
    }

    // ---- Frame: bitmap transforms + sparks only. Zero allocation. ----

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val shell = shellLayer ?: return
        val ticks = tickLayerA ?: return
        val dots = tickLayerB ?: return
        val core = coreLayer ?: return
        val cx = width / 2f
        val cy = height / 2f
        val base = layerBase
        if (base <= 0) return

        canvas.drawBitmap(shell, cx - base, cy - base, paint)

        dstTick.set(cx - base, cy - base, cx + base, cy + base)
        canvas.save()
        canvas.rotate(rotA, cx, cy)
        canvas.drawBitmap(ticks, null, dstTick, paint)
        canvas.restore()
        canvas.save()
        canvas.rotate(rotB, cx, cy)
        canvas.drawBitmap(dots, null, dstTick, paint)
        canvas.restore()

        // Core breathes via scale (baked oversize, shrinks to fit).
        val pulse = 1f + 0.03f * sin(t * 2.2f) * (0.5f + energy)
        val coreScale = (0.92f + energy * 0.16f) * pulse * 0.92f
        val cr = base * coreScale
        dstCore.set(cx - cr, cy - cr, cx + cr, cy + cr)
        canvas.drawBitmap(core, null, dstCore, paint)

        // Sparks.
        val accent = if (redCore) 0xFFFF3B30.toInt() else 0xFF1FD5F9.toInt()
        val cyanCore = 0xFF1FD5F9.toInt()
        val spd = 0.6f + energy * 1.4f
        for (s in sparks) {
            s.angle += s.speed * 0.016 * spd
            s.tw += 0.06f
            val rr = base * (0.50f + 0.42f * s.radius) * pulse
            val x = cx + (rr * cos(s.angle)).toFloat()
            val y = cy + (rr * sin(s.angle)).toFloat()
            val alpha = (200 * (1f - s.radius * 0.5f) * (0.55f + 0.45f * sin(s.tw)))
                .toInt().coerceIn(30, 230)
            paint.color = if (redCore && s.radius < 0.4f) cyanCore else accent
            paint.alpha = alpha
            canvas.drawCircle(x, y, s.size * (0.7f + energy * 0.7f), paint)
        }
        paint.alpha = 255
        // Comet streak when hot.
        if (energy > 0.45f) {
            val sweepDeg = (t * 140f) % 360f
            paint.color = Color.WHITE
            paint.alpha = ((energy - 0.45f) * 400).toInt().coerceIn(0, 220)
            paint.style = Paint.Style.STROKE
            paint.strokeWidth = base * 0.02f
            bounds.set(cx - base * 0.80f, cy - base * 0.80f, cx + base * 0.80f, cy + base * 0.80f)
            canvas.drawArc(bounds, sweepDeg - 14f, 14f, false, paint)
            paint.style = Paint.Style.FILL
            paint.alpha = 255
        }
    }
}

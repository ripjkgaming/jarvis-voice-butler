package dev.jarvis.link.ui

import android.content.Context
import android.graphics.*
import android.util.AttributeSet
import android.view.View
import android.animation.ValueAnimator
import android.view.animation.LinearInterpolator
import java.time.ZonedDateTime
import dev.jarvis.link.logic.IncomingPolicy

/** Cached paints, single visible 24fps pulse. Idle/hidden screens draw once. */
class ArcReactorView @JvmOverloads constructor(ctx: Context, attrs: AttributeSet?=null) : View(ctx,attrs) {
    private val paint=Paint(Paint.ANTI_ALIAS_FLAG)
    private val rect=RectF()
    private val clockTick=object : Runnable {
        override fun run() { invalidate(); if (isShown && windowVisibility==VISIBLE && clock) postDelayed(this,60_000-System.currentTimeMillis()%60_000) }
    }
    private var pulse=0f
    private var animator: ValueAnimator?=null
    var speaking=false
        set(v) { field=v; animateIfVisible(); invalidate() }
    var clock=true
        set(v) { field=v; invalidate() }
    init { contentDescription="Jarvis reactor"; importantForAccessibility=IMPORTANT_FOR_ACCESSIBILITY_NO }
    private fun animateIfVisible() {
        animator?.cancel(); animator=null
        removeCallbacks(clockTick)
        if (clock && isShown && windowVisibility==VISIBLE && isAttachedToWindow) postDelayed(clockTick,60_000-System.currentTimeMillis()%60_000)
        if (speaking && isShown && windowVisibility==VISIBLE && isAttachedToWindow && ValueAnimator.areAnimatorsEnabled()) {
            animator=ValueAnimator.ofFloat(0f,1f).apply {
                duration=1600; repeatCount=ValueAnimator.INFINITE; interpolator=LinearInterpolator()
                var last=0L
                addUpdateListener { a -> val now=android.os.SystemClock.uptimeMillis(); if (now-last>=42) { last=now; pulse=a.animatedValue as Float; invalidate() } }
                start()
            }
        }
    }
    override fun onAttachedToWindow() { super.onAttachedToWindow(); animateIfVisible() }
    override fun onDetachedFromWindow() { removeCallbacks(clockTick); animator?.cancel(); animator=null; super.onDetachedFromWindow() }
    override fun onVisibilityChanged(v: View, visibility: Int) { super.onVisibilityChanged(v,visibility); if (paint!=null) animateIfVisible() }
    override fun onWindowVisibilityChanged(visibility: Int) { super.onWindowVisibilityChanged(visibility); if (paint!=null) animateIfVisible() }
    override fun onDraw(c: Canvas) {
        super.onDraw(c)
        val x=width/2f; val y=height/2f; val r=minOf(width,height)*.43f
        paint.style=Paint.Style.FILL; paint.color=Color.rgb(4,27,39); c.drawCircle(x,y,r,paint)
        paint.style=Paint.Style.STROKE
        for (ring in 0..4) {
            paint.strokeWidth=if (ring==2) r*.035f else 1.2f*resources.displayMetrics.density
            paint.color=if (ring==2) Color.rgb(36,145,188) else Color.argb(80,76,210,242)
            c.drawCircle(x,y,r*(1-ring*.08f),paint)
        }
        for (i in 0 until 60) {
            val a=Math.toRadians(i*6.0); val inner=r*(if(i%5==0) .83 else .86)
            paint.strokeWidth=if(i%5==0) 2.5f else 1f
            paint.color=if(i%5==0) Color.rgb(170,231,243) else Color.rgb(30,102,133)
            c.drawLine(x+(kotlin.math.cos(a)*inner).toFloat(),y+(kotlin.math.sin(a)*inner).toFloat(),x+(kotlin.math.cos(a)*r*.91).toFloat(),y+(kotlin.math.sin(a)*r*.91).toFloat(),paint)
        }
        rect.set(x-r*.95f,y-r*.95f,x+r*.95f,y+r*.95f)
        paint.color=Color.rgb(56,190,249); paint.strokeWidth=r*.017f
        c.drawArc(rect,-120f+(if(speaking) pulse*30 else 0f),82f,false,paint)
        c.drawArc(rect,62f,46f,false,paint)
        if (speaking) { paint.color=Color.argb((100*(1-pulse)).toInt(),77,165,255); c.drawCircle(x,y,r*(.56f+pulse*.18f),paint) }
        paint.style=Paint.Style.FILL; paint.textAlign=Paint.Align.CENTER
        paint.color=Color.rgb(218,249,255); paint.typeface=Typeface.create("sans-serif-light",Typeface.NORMAL)
        paint.textSize=r*(if(clock) .34f else .24f)
        val t=ZonedDateTime.now(IncomingPolicy.zone)
        c.drawText(if(clock) "%02d:%02d".format(t.hour,t.minute) else "JARVIS",x,y+r*.03f,paint)
        paint.textSize=r*.075f; paint.color=Color.rgb(96,174,202)
        c.drawText(if(clock) "SINGAPORE  •  ${t.dayOfWeek.name}" else if(speaking) "VOICE LINK ACTIVE" else "PRIVATE VOICE LINK",x,y+r*.2f,paint)
    }
}

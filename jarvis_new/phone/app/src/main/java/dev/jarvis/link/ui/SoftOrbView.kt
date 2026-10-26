package dev.jarvis.link.ui

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.*
import android.view.View
import android.view.animation.LinearInterpolator
import kotlin.math.sin

/** Cached soft gradients; only the visible call surface animates. */
class SoftOrbView(context: Context) : View(context) {
    var speaking=false
        set(value) { if(field!=value) { field=value; invalidate() } }
    var listening=false
        set(value) { if(field!=value) { field=value; invalidate() } }
    private val paint=Paint(Paint.ANTI_ALIAS_FLAG)
    private val layers=ArrayList<Bitmap>()
    private var phase=0f
    private var animator: ValueAnimator?=null
    override fun onSizeChanged(w:Int,h:Int,ow:Int,oh:Int) {
        layers.forEach { it.recycle() };layers.clear()
        if(w==0 || h==0) return
        listOf(intArrayOf(0xff6bb8ff.toInt(),0xff4062da.toInt()),intArrayOf(0xff81efff.toInt(),0xff147dcf.toInt()),intArrayOf(0xff55a5ff.toInt(),0xff6659e9.toInt())).forEach { colors ->
            val b=Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888);val c=Canvas(b)
            val p=Paint(Paint.ANTI_ALIAS_FLAG)
            val r=w.coerceAtMost(h)*.40f
            val cx=w*.5f;val cy=h*.5f
            p.shader=RadialGradient(cx,cy,r*1.24f,intArrayOf(0x443478dd,0x003478dd),floatArrayOf(.55f,1f),Shader.TileMode.CLAMP)
            c.drawCircle(cx,cy,r*1.24f,p)
            c.save();val clip=Path().apply { addCircle(cx,cy,r,Path.Direction.CW) };c.clipPath(clip)
            p.shader=LinearGradient(cx-r,cy-r,cx+r,cy+r,intArrayOf(0xff66d4ff.toInt(),colors[0],0xff27378c.toInt()),floatArrayOf(0f,.47f,1f),Shader.TileMode.CLAMP)
            c.drawCircle(cx,cy,r,p)
            for(i in 0..5) {
                val x=cx+sin(i*1.9).toFloat()*r*.62f
                val y=cy+sin(i*2.7).toFloat()*r*.55f
                val color=if(i==2) 0xff102454.toInt() else colors[i%2]
                p.shader=RadialGradient(x,y,r*.78f,intArrayOf(color,color and 0x00ffffff),floatArrayOf(.12f,1f),Shader.TileMode.CLAMP)
                p.alpha=if(i==2) 115 else 180;c.drawCircle(x,y,r,p)
            }
            p.alpha=130;p.shader=RadialGradient(cx-r*.36f,cy-r*.47f,r*.42f,intArrayOf(0xffb6edff.toInt(),0x00b6edff),floatArrayOf(0f,1f),Shader.TileMode.CLAMP)
            c.drawCircle(cx-r*.36f,cy-r*.47f,r*.42f,p);c.restore()
            layers.add(b)
        }
    }
    override fun onDraw(c:Canvas) {
        if(layers.isEmpty()) return
        val s=1f+sin(phase*6.283).toFloat()*.018f
        c.save();c.scale(s,s,width/2f,height/2f);c.rotate(sin(phase*6.283).toFloat()*2.5f,width/2f,height/2f)
        c.drawBitmap(layers[if(speaking) 2 else if(listening) 1 else 0],0f,0f,paint);c.restore()
    }
    private fun sync() {
        animator?.cancel();animator=null
        if(isAttachedToWindow && isShown && ValueAnimator.areAnimatorsEnabled()) {
            animator=ValueAnimator.ofFloat(0f,1f).apply {
                duration=4200;repeatCount=ValueAnimator.INFINITE;interpolator=LinearInterpolator()
                var last=0L
                addUpdateListener { if(System.nanoTime()-last>=50_000_000L) { last=System.nanoTime();phase=it.animatedValue as Float;invalidate() } };start()
            }
        }
    }
    override fun onAttachedToWindow(){super.onAttachedToWindow();sync()}
    override fun onVisibilityChanged(v:View,visibility:Int){super.onVisibilityChanged(v,visibility);sync()}
    override fun onWindowVisibilityChanged(visibility:Int){super.onWindowVisibilityChanged(visibility);sync()}
    override fun onDetachedFromWindow(){animator?.cancel();animator=null;super.onDetachedFromWindow()}
}

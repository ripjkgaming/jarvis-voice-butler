package dev.jarvis.link.ui

import android.Manifest
import android.app.KeyguardManager
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.widget.*
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import dev.jarvis.link.JarvisApp
import dev.jarvis.link.logic.CallPhase
import dev.jarvis.link.net.BridgeClient
import dev.jarvis.link.svc.IncomingCalls
import kotlinx.coroutines.*
import org.json.JSONObject

/** A call owns its own surface; answering never navigates to the dashboard. */
open class CallActivity : AppCompatActivity() {
    private val g by lazy { JarvisApp.graph(this) }
    private lateinit var state:TextView
    private lateinit var caption:TextView
    private lateinit var role:TextView
    private lateinit var history:TextView
    private lateinit var orb:SoftOrbView
    private lateinit var controls:LinearLayout
    private lateinit var mute:Button
    private lateinit var audio:Button
    private var incomingId=""
    private var claiming=false
    private var accepted=false
    private var lastCaption=""
    private var reveal:android.animation.ValueAnimator?=null
    private var historyDialog:androidx.appcompat.app.AlertDialog?=null
    private var started=0L
    private var screenOff=false
    private val privacyEvents=object:android.content.BroadcastReceiver() {
        override fun onReceive(context:android.content.Context,intent:Intent) {
            screenOff=intent.action==Intent.ACTION_SCREEN_OFF
            if(screenOff) clearSensitive() else draw()
        }
    }
    private fun locked()=screenOff || !getSystemService(android.os.PowerManager::class.java).isInteractive || getSystemService(KeyguardManager::class.java).isKeyguardLocked
    private fun mayReveal()=dev.jarvis.link.logic.CallPrivacy.reveal(incomingId,g.call.state.value.incomingCallId,g.call.active,locked())
    private fun clearSensitive() {
        reveal?.cancel();reveal=null;lastCaption=""
        historyDialog?.dismiss();historyDialog=null
        g.call.stopPolling(this)
        if(::history.isInitialized) history.visibility=View.INVISIBLE
        if(::role.isInitialized) role.text=""
        if(::caption.isInitialized) { caption.text=if(incomingId.isNotBlank() && !accepted) "Jarvis is calling" else "Call in progress";caption.setTextColor(white) }
    }
    private val white=Color.rgb(234,244,255)
    private val blue=Color.rgb(90,185,255)
    private val mic=registerForActivityResult(ActivityResultContracts.RequestPermission()) { if(it) accept() else state.text="Microphone permission needed to answer" }
    private fun dp(v:Int)=(v*resources.displayMetrics.density).toInt()
    override fun onCreate(saved:Bundle?) {
        super.onCreate(saved)
        setShowWhenLocked(true);setTurnScreenOn(true)
        incomingId=intent.getStringExtra("call_id") ?: ""
        accepted=g.call.active && (incomingId.isEmpty() || g.call.state.value.incomingCallId==incomingId)
        androidx.core.content.ContextCompat.registerReceiver(this,privacyEvents,android.content.IntentFilter().apply { addAction(Intent.ACTION_SCREEN_OFF);addAction(Intent.ACTION_SCREEN_ON);addAction(Intent.ACTION_USER_PRESENT) },androidx.core.content.ContextCompat.RECEIVER_NOT_EXPORTED)
        window.statusBarColor=Color.rgb(5,13,32);window.navigationBarColor=window.statusBarColor
        val root=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL;gravity=Gravity.CENTER_HORIZONTAL;setBackgroundColor(Color.rgb(5,13,32));setPadding(dp(28),dp(40),dp(28),dp(24)) }
        root.setOnApplyWindowInsetsListener { v,i -> v.setPadding(dp(28),dp(24)+i.systemWindowInsetTop,dp(28),dp(20)+i.systemWindowInsetBottom);i }
        fun label(text:String,size:Float,color:Int=white)=TextView(this).apply { this.text=text;typeface=Typeface.create("sans-serif",Typeface.NORMAL);textSize=size;setTextColor(color);gravity=Gravity.CENTER;setPadding(0,dp(8),0,dp(8)) }
        root.addView(label("J A R V I S",18f).apply { typeface=Typeface.create("sans-serif-medium",Typeface.NORMAL) })
        state=label("Incoming call",13f,Color.rgb(135,157,190));root.addView(state)
        root.addView(View(this),LinearLayout.LayoutParams(1,0,0.7f))
        orb=SoftOrbView(this);root.addView(orb,LinearLayout.LayoutParams(dp(192),dp(192)))
        role=label("",11f,blue);root.addView(role)
        caption=label("",22f).apply { maxLines=3;minHeight=dp(94);setLineSpacing(dp(5).toFloat(),1f) };root.addView(caption,LinearLayout.LayoutParams(-1,-2))
        history=label("Conversation",12f,Color.rgb(135,157,190)).apply { setOnClickListener {
            if (!mayReveal()) {
                android.widget.Toast.makeText(this@CallActivity,"Answer and unlock to view conversation history",android.widget.Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            androidx.appcompat.app.AlertDialog.Builder(this@CallActivity).setTitle("Conversation").setMessage(g.call.state.value.captions.joinToString("\n\n") { "${it.role}\n${it.text}" }.ifEmpty { "No transcript yet" }).setPositiveButton("Close",null).show().also { historyDialog=it } } };root.addView(history)
        root.addView(View(this),LinearLayout.LayoutParams(1,0,1f))
        controls=LinearLayout(this).apply { gravity=Gravity.CENTER;orientation=LinearLayout.HORIZONTAL };root.addView(controls,LinearLayout.LayoutParams(-1,dp(72)))
        setContentView(root)
        if(incomingId.isNotBlank() && !accepted) incomingControls() else activeControls()
        lifecycleScope.launch { repeatOnLifecycle(Lifecycle.State.STARTED) {
            launch { g.call.state.collect { draw() } }
            launch { IncomingCalls.invitation.collect { if(incomingId.isNotBlank() && !claiming && !accepted && it?.id!=incomingId) finish() } }
            launch { while(true) { draw();delay(1000) } }
        } }
        if(intent.action=="accept" && !accepted) requestAccept()
    }
    private fun button(label:String,color:Int,action:()->Unit):Button=Button(this).apply {
        text=label;textSize=13f;isAllCaps=false;setTextColor(white)
        background=GradientDrawable().apply { setColor(color);cornerRadius=dp(28).toFloat() }
        setOnClickListener { action() }
        layoutParams=LinearLayout.LayoutParams(0,dp(56),1f).apply { setMargins(dp(5),0,dp(5),0) }
    }
    private fun incomingControls() {
        history.visibility=View.INVISIBLE
        controls.removeAllViews()
        controls.addView(button("Decline",Color.rgb(176,49,70)) { decline() })
        controls.addView(button("Answer",Color.rgb(30,117,185)) { requestAccept() })
        val inv=IncomingCalls.invitation.value
        caption.text=if(locked()) "Jarvis is calling" else inv?.title ?: "Jarvis is calling"
    }
    private fun activeControls() {
        history.visibility=if(locked()) View.INVISIBLE else View.VISIBLE
        controls.removeAllViews()
        mute=button("Mute",Color.rgb(17,37,66)) { g.call.toggleMute() };controls.addView(mute)
        controls.addView(button("End",Color.rgb(184,48,69)) { g.call.hangup();finish() })
        audio=button("Speaker",Color.rgb(17,37,66)) { g.call.toggleSpeaker() };controls.addView(audio)
    }
    private fun requestAccept() {
        if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)==android.content.pm.PackageManager.PERMISSION_GRANTED) accept() else mic.launch(Manifest.permission.RECORD_AUDIO)
    }
    private fun accept() {
        if(claiming || accepted) return
        val inv=IncomingCalls.invitation.value
        if(inv?.id!=incomingId || !IncomingCalls.valid(inv) || !g.prefs.incomingCalls || g.call.active) { finish();return }
        claiming=true;g.prefs.handledCall=incomingId;IncomingCalls.cancel(this);state.text="Connecting securely…"
        controls.childrenDisabled()
        lifecycleScope.launch {
            try {
                val o=withContext(Dispatchers.IO) { g.bridge.callAction(JSONObject().put("action","accept").put("id",incomingId)) }
                if(!IncomingCalls.valid(inv) || !g.prefs.incomingCalls || !lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) throw IllegalStateException("Call unavailable")
                accepted=true;g.call.joinAccepted(BridgeClient.parseToken(o),inv.title,inv.id);activeControls();draw()
            } catch(e:CancellationException) { throw e }
            catch(_:Exception) { state.text="Call unavailable";caption.text="Call Jarvis when you’re ready";controls.removeAllViews();controls.addView(button("Close",Color.rgb(17,37,66)) { finish() }) }
            finally { claiming=false }
        }
    }
    private fun LinearLayout.childrenDisabled() { for(i in 0 until childCount) getChildAt(i).isEnabled=false }
    private fun decline() {
        g.prefs.handledCall=incomingId;IncomingCalls.cancel(this)
        g.scope.launch { try { withContext(Dispatchers.IO) { g.bridge.callAction(JSONObject().put("action","decline").put("id",incomingId)) } } catch(_:Exception) {} }
        finish()
    }
    private fun draw() {
        if(incomingId.isNotBlank() && !accepted) {
            clearSensitive()
            if(!locked()) caption.text=IncomingCalls.invitation.value?.takeIf { it.id==incomingId }?.title ?: "Jarvis is calling"
            g.call.stopPolling(this)
            return
        }
        val disclose=mayReveal()
        history.visibility=if(disclose) View.VISIBLE else View.INVISIBLE
        if(lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED) && disclose) g.call.startPolling(this,1000) else g.call.stopPolling(this)
        val s=g.call.state.value
        if(s.phase==CallPhase.LIVE && started==0L) started=android.os.SystemClock.elapsedRealtime()
        state.text=when(s.phase) {
            CallPhase.LIVE -> { val sec=(android.os.SystemClock.elapsedRealtime()-started)/1000; "${sec/60}:${(sec%60).toString().padStart(2,'0')}  ·  ${if(s.agentSpeaking) "Speaking" else if(s.muted) "Muted" else "Listening"}" }
            CallPhase.JOINING -> "Connecting securely…"
            CallPhase.RECONNECTING -> "Reconnecting…"
            CallPhase.ERROR -> "Call unavailable"
            CallPhase.IDLE -> "Call ended"
        }
        orb.speaking=s.agentSpeaking;orb.listening=s.phase==CallPhase.LIVE && !s.muted && !s.agentSpeaking
        if(::mute.isInitialized) { mute.text=if(s.muted) "Unmute" else "Mute";mute.isEnabled=s.phase==CallPhase.LIVE;audio.text=if(s.speaker) "Earpiece" else "Speaker";audio.isEnabled=g.call.active }
        if(!disclose) {
            clearSensitive()
            if(!g.call.active) caption.text=if(s.phase==CallPhase.ERROR) "Call unavailable" else "Call ended"
            return
        }
        val latest=s.captions.lastOrNull()
        val text=latest?.text ?: if(s.phase==CallPhase.ERROR) s.detail else "Your conversation appears here"
        role.text=latest?.role?.let { if(it.lowercase() in listOf("sir","user")) "YOU" else "JARVIS" } ?: ""
        if(text!=lastCaption) {
            lastCaption=text;caption.text=text;reveal?.cancel()
            if(android.animation.ValueAnimator.areAnimatorsEnabled() && latest!=null) {
                reveal=android.animation.ValueAnimator.ofArgb(blue,white).apply { duration=200;addUpdateListener { caption.setTextColor(it.animatedValue as Int) };start() }
            } else caption.setTextColor(white)
        }
    }
    override fun onStart(){super.onStart();draw()}
    override fun onResume(){super.onResume();draw()}
    override fun onStop(){g.call.stopPolling(this);clearSensitive();super.onStop()}
    override fun onDestroy(){clearSensitive();unregisterReceiver(privacyEvents);super.onDestroy()}
    override fun onSaveInstanceState(b:Bundle){super.onSaveInstanceState(b)}
    companion object { fun open(ctx:android.content.Context){ctx.startActivity(Intent(ctx,CallActivity::class.java))} }
}

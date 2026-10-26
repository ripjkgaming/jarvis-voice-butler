package dev.jarvis.link.svc

import android.app.*
import android.content.*
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.net.Uri
import android.os.*
import androidx.core.app.NotificationCompat
import androidx.core.app.Person
import dev.jarvis.link.JarvisApp
import dev.jarvis.link.R
import dev.jarvis.link.logic.IncomingPolicy
import dev.jarvis.link.logic.CallPresentation
import dev.jarvis.link.net.BridgeException
import dev.jarvis.link.net.BridgeError
import dev.jarvis.link.ui.IncomingCallActivity
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONObject

data class Invitation(val id: String, val title: String, val issued: Long, val expires: Long, val serverNow: Long, val receivedElapsed: Long)

/** Incoming audio is never opened before an explicit visible accept. */
object IncomingCalls {
    const val CHANNEL = "jarvis_incoming_v1"
    const val MONITOR = "jarvis_call_monitor"
    const val ID = 20
    val invitation = MutableStateFlow<Invitation?>(null)
    val status = MutableStateFlow("Incoming monitoring is off. Turn on Receive Jarvis calls to listen.")
    var statusCode="off"
        private set
    var monitorRunning=false
    fun report(code: String) {
        statusCode=code
        status.value=when(code) {
            "off" -> "Incoming monitoring is off. Turn on Receive Jarvis calls to listen."
            "starting" -> "Starting incoming call monitor…"
            "monitoring" -> "Listening for incoming calls. Keep the Jarvis incoming calls notification present."
            "quiet_hours" -> "School quiet hours: no incoming calls until 15:30 Singapore on weekdays."
            "notifications_disabled" -> "Incoming calls blocked: Android notifications are off. Open Incoming call notification settings."
            "channel_disabled" -> "Incoming calls blocked: the Incoming Jarvis calls channel is disabled. Open notification settings."
            "notification_posted", "ringing" -> "Incoming call notification posted. Android controls heads-up / full-screen presentation."
            "posted_silent" -> "Incoming call notification posted silently. Check ringtone mode, DND and channel importance."
            "ringtone_failed" -> "Incoming call notification posted, but ringtone playback failed. You can still answer or decline."
            "notification_failed" -> "Android refused the incoming call notification. Check app notification settings."
            "clock_skew" -> "Incoming call blocked: phone and laptop clocks differ by over 15 seconds. Check automatic date and time."
            "future_invitation" -> "Incoming call not yet valid on the phone clock. Waiting for the next poll."
            "expired" -> "Incoming invitation expired. It will not ring again."
            "invalid_lifetime" -> "Invalid invitation timing; incoming call suppressed."
            "call_active" -> "An existing voice call is active; incoming ringing is suppressed."
            "handled" -> "This invitation was already presented or handled; it will not ring again."
            "guest_mode" -> "Incoming calls are unavailable in Guest mode."
            "offline_mode" -> "Incoming calls are paused by Offline mode."
            "not_configured" -> "Incoming calls need the PC address and bridge token saved in Settings."
            "api_unavailable" -> "Incoming call API unavailable. Laptop backend update / activation is required."
            "unauthorized" -> "Incoming monitor cannot authenticate. Check the saved bridge token."
            "monitor_start_failed" -> "Android could not start the incoming monitor. Open the app and toggle Receive Jarvis calls off/on."
            else -> "Laptop link unavailable; incoming calls will not ring until it reconnects."
        }
        android.util.Log.i("JarvisIncoming", code)
    }
    fun channels(ctx: Context) {
        val nm=ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL,"Incoming Jarvis calls",NotificationManager.IMPORTANCE_HIGH).apply {
            description="Updates and calendar calls; school quiet hours always apply"
            setSound(null,null)
            enableVibration(false)
            lockscreenVisibility=Notification.VISIBILITY_PRIVATE
        })
    }
    fun permissionIssue(ctx: Context): String? {
        val nm=ctx.getSystemService(NotificationManager::class.java)
        return if (!nm.areNotificationsEnabled()) "notifications_disabled" else if(nm.getNotificationChannel(CHANNEL)?.importance==NotificationManager.IMPORTANCE_NONE) "channel_disabled" else null
    }
    fun diagnostics(ctx: Context): JSONObject {
        val nm=ctx.getSystemService(NotificationManager::class.java)
        return JSONObject().put("client_time_ms",System.currentTimeMillis()).put("notifications",nm.areNotificationsEnabled())
            .put("channel_importance",nm.getNotificationChannel(CHANNEL)?.importance ?: 0)
            .put("full_screen",Build.VERSION.SDK_INT<34 || nm.canUseFullScreenIntent())
            .put("ringer_mode",ctx.getSystemService(android.media.AudioManager::class.java).ringerMode)
            .put("dnd",nm.currentInterruptionFilter!=NotificationManager.INTERRUPTION_FILTER_ALL).put("status",statusCode)
    }
    private val handler = Handler(Looper.getMainLooper())
    private val tick = object : Runnable {
        override fun run() {
            val i=invitation.value ?: return
            val ctx=context ?: return
            val reason=permissionIssue(ctx) ?: rejection(i)
            if (reason!=null) { cancel(ctx); report(reason) } else handler.postDelayed(this, 250)
        }
    }
    private var context: Context? = null
    private val stopRing=Runnable { context?.let { cancel(it); report(if (IncomingPolicy.quiet(System.currentTimeMillis())) "quiet_hours" else "expired") } }
    private var ringtone: android.media.Ringtone? = null
    fun rejection(i: Invitation): String? {
        val elapsed=SystemClock.elapsedRealtime()-i.receivedElapsed
        if (elapsed<0) return "clock_skew"
        if (elapsed>=i.expires-i.serverNow) return "expired"
        return IncomingPolicy.rejection(i.issued,i.expires,System.currentTimeMillis(),i.serverNow+elapsed)
            ?: if(System.currentTimeMillis()>=IncomingPolicy.ringUntil(i.expires,i.issued)) "quiet_hours" else null
    }
    fun valid(i: Invitation): Boolean = rejection(i)==null
    fun cancel(ctx: Context) {
        handler.removeCallbacks(tick); handler.removeCallbacks(stopRing)
        ringtone?.stop(); ringtone=null
        invitation.value=null
        ctx.getSystemService(NotificationManager::class.java).cancel(ID)
    }
    fun show(ctx: Context, i: Invitation) {
        val g=JarvisApp.graph(ctx)
        if (!g.prefs.incomingCalls) { report("off"); return }
        if (g.call.active) { report("call_active"); return }
        if (invitation.value?.id==i.id) return
        if (g.prefs.handledCall==i.id || g.prefs.presentedCall==i.id) { report("handled"); return }
        rejection(i)?.let { report(it); return }
        cancel(ctx); context=ctx.applicationContext
        val nm=ctx.getSystemService(NotificationManager::class.java)
        channels(ctx)
        permissionIssue(ctx)?.let { report(it); return }
        fun activity(action: String): PendingIntent = PendingIntent.getActivity(ctx,action.hashCode(),Intent(ctx,IncomingCallActivity::class.java).apply {
            data=Uri.parse("jarvis-call:${i.id}/$action"); putExtra("call_id",i.id); this.action=action
        },PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val decline=PendingIntent.getBroadcast(ctx,i.id.hashCode(),Intent(ctx,CallDeclineReceiver::class.java).apply {
            data=Uri.parse("jarvis-call:${i.id}/decline"); putExtra("call_id",i.id)
        },PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val caller=Person.Builder().setName("Jarvis").setImportant(true).build()
        val fullScreenAllowed=Build.VERSION.SDK_INT<34 || nm.canUseFullScreenIntent()
        fun post(callStyle: Boolean) {
            val b=NotificationCompat.Builder(ctx,CHANNEL).setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle("Jarvis is calling").setContentText("An update is ready")
                .setContentIntent(activity("show")).setCategory(NotificationCompat.CATEGORY_CALL)
                .setPriority(NotificationCompat.PRIORITY_MAX).setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
                .setTimeoutAfter((IncomingPolicy.ringUntil(i.expires,System.currentTimeMillis())-System.currentTimeMillis()).coerceAtLeast(1))
                .setOngoing(true).setOnlyAlertOnce(true)
            if (callStyle) {
                b.setStyle(NotificationCompat.CallStyle.forIncomingCall(caller,decline,activity("accept")))
                    .setFullScreenIntent(activity("show"),true)
            } else {
                b.addPerson(caller)
                    .addAction(0,"Decline",decline)
                    .addAction(0,"Answer",activity("accept"))
            }
            nm.notify(ID,b.build())
        }
        invitation.value=i
        val delivery=CallPresentation.presentIncoming(fullScreenAllowed, ::post) {
            if (ctx.getSystemService(android.media.AudioManager::class.java).ringerMode!=android.media.AudioManager.RINGER_MODE_NORMAL || nm.currentInterruptionFilter!=NotificationManager.INTERRUPTION_FILTER_ALL || nm.getNotificationChannel(CHANNEL).importance<NotificationManager.IMPORTANCE_HIGH) false
            else {
                ringtone=RingtoneManager.getRingtone(ctx,RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE))
                ringtone?.let {
                    it.audioAttributes=AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE).build()
                    it.isLooping=true; it.play()
                }
                ringtone!=null
            }
        }
        delivery.notificationError?.let { android.util.Log.w("JarvisIncoming","incoming notification post: $it") }
        report(delivery.code)
        if (!delivery.posted) { cancel(ctx); return }
        g.prefs.presentedCall=i.id
        handler.post(tick)
        handler.postDelayed(stopRing,(IncomingPolicy.ringUntil(i.expires,System.currentTimeMillis())-System.currentTimeMillis()).coerceAtLeast(0))
    }
}

class CallDeclineReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val id=intent.getStringExtra("call_id") ?: return
        if (IncomingCalls.invitation.value?.id!=id) return
        val g=JarvisApp.graph(ctx); g.prefs.handledCall=id; IncomingCalls.cancel(ctx)
        val pending=goAsync()
        g.scope.launch { try { withContext(Dispatchers.IO) { g.bridge.callAction(JSONObject().put("action","decline").put("id",id)) } } catch (_: Exception) {} finally { pending.finish() } }
    }
}

/** Explicit opt-in foreground monitor. No background microphone or push credentials. */
class IncomingCallService : Service() {
    private val scope=CoroutineScope(SupervisorJob()+Dispatchers.Main.immediate)
    private var poll: Job?=null
    override fun onBind(i: Intent?): IBinder?=null
    override fun onStartCommand(i: Intent?, flags: Int, startId: Int): Int {
        // Android can recreate this foreground monitor after reclaiming its
        // process. Recheck persisted opt-in before posting or contacting PC.
        if (!JarvisApp.graph(this).prefs.incomingCalls) {
            stopSelf()
            return START_NOT_STICKY
        }
        val nm=getSystemService(NotificationManager::class.java)
        IncomingCalls.channels(this)
        nm.createNotificationChannel(NotificationChannel(IncomingCalls.MONITOR,"Call availability",NotificationManager.IMPORTANCE_LOW))
        val n=Notifs.ongoing(this,IncomingCalls.MONITOR,"Jarvis incoming calls","School quiet hours: weekdays 08:00–15:30 Singapore")
        try {
            if (Build.VERSION.SDK_INT>=34) startForeground(21,n,android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            else startForeground(21,n)
        } catch (_: Exception) { IncomingCalls.report("monitor_start_failed"); stopSelf(); return START_NOT_STICKY }
        IncomingCalls.monitorRunning=true
        if (poll?.isActive!=true) poll=scope.launch {
            val g=JarvisApp.graph(this@IncomingCallService)
            while (isActive && g.prefs.incomingCalls) {
                try {
                    if (g.config().guest) throw BridgeException(BridgeError.Guest("incoming calls"))
                    if (g.config().offline) throw BridgeException(BridgeError.Offline)
                    g.config().problem()?.let { throw BridgeException(BridgeError.NotConfigured(it)) }
                    IncomingCalls.permissionIssue(this@IncomingCallService)?.let { IncomingCalls.report(it) }
                    val request=JSONObject().put("enabled",g.prefs.incomingCalls).put("calendar",g.prefs.calendarCalls).put("updates",g.prefs.updateCalls).put("diagnostics",IncomingCalls.diagnostics(this@IncomingCallService))
                    val data=withContext(Dispatchers.IO) { g.bridge.pollCalls(request) }
                    val o=data.optJSONObject("invitation")
                    val monitorStatus=IncomingPolicy.monitorStatus(data.optBoolean("quiet",true),System.currentTimeMillis(),IncomingPolicy.epochMillis(data.getDouble("server_time")),g.call.active)
                    if (monitorStatus!="monitoring" || o==null) {
                        IncomingCalls.cancel(this@IncomingCallService)
                        IncomingCalls.report(IncomingCalls.permissionIssue(this@IncomingCallService) ?: monitorStatus)
                    } else {
                        val inv=Invitation(o.getString("id"),o.getString("title"),IncomingPolicy.epochMillis(o.getDouble("issued")),IncomingPolicy.epochMillis(o.getDouble("expires")),IncomingPolicy.epochMillis(data.getDouble("server_time")),SystemClock.elapsedRealtime())
                        IncomingCalls.show(this@IncomingCallService,inv)
                    }
                } catch (e: CancellationException) { throw e }
                catch (e: Exception) {
                    IncomingCalls.cancel(this@IncomingCallService)
                    val code=when ((e as? BridgeException)?.error) {
                        is BridgeError.Guest -> "guest_mode"
                        is BridgeError.Offline -> "offline_mode"
                        is BridgeError.NotConfigured -> "not_configured"
                        is BridgeError.NotFound -> "api_unavailable"
                        is BridgeError.Unauthorized -> "unauthorized"
                        else -> "network_error"
                    }
                    IncomingCalls.report(IncomingCalls.permissionIssue(this@IncomingCallService) ?: code)
                }
                try { nm.notify(21,Notifs.ongoing(this@IncomingCallService,IncomingCalls.MONITOR,"Jarvis incoming calls",IncomingCalls.status.value)) }
                catch (_: Exception) { IncomingCalls.report("notification_failed") }
                delay(15_000)
            }
            IncomingCalls.cancel(this@IncomingCallService); stopSelf()
        }
        return START_STICKY
    }
    override fun onDestroy() {
        scope.cancel(); IncomingCalls.monitorRunning=false; IncomingCalls.cancel(this)
        if (IncomingCalls.statusCode!="monitor_start_failed") IncomingCalls.report("off")
        super.onDestroy()
    }
    companion object {
        fun start(ctx: Context) {
            if (!IncomingCalls.monitorRunning) IncomingCalls.report("starting")
            try { ctx.startForegroundService(Intent(ctx,IncomingCallService::class.java)) }
            catch (_: Exception) { IncomingCalls.report("monitor_start_failed") }
        }
        fun stop(ctx: Context) { ctx.stopService(Intent(ctx,IncomingCallService::class.java)); IncomingCalls.cancel(ctx); IncomingCalls.report("off") }
    }
}

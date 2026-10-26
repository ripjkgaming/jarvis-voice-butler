package dev.jarvis.link.ui

import android.Manifest
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import androidx.fragment.app.Fragment
import dev.jarvis.link.R
import dev.jarvis.link.logic.CallPhase
import dev.jarvis.link.logic.CallState
import kotlinx.coroutines.launch

class VoiceFragment : Fragment(R.layout.fragment_voice) {
    override fun onViewCreated(v: View, state: Bundle?) {
        val m = graph.call
        val prefs=graph.prefs
        val receive=v.findViewById<android.widget.Switch>(R.id.voice_incoming)
        val calendar=v.findViewById<android.widget.Switch>(R.id.voice_calendar)
        val updates=v.findViewById<android.widget.Switch>(R.id.voice_updates)
        val checkin=v.findViewById<android.widget.Switch>(R.id.voice_checkin)
        checkin.isEnabled=false
        graph.scope.launch {
            try {
                val response=kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { graph.bridge.incomingCalls() }
                if(isAdded && view===v && response?.has("schedule")==true) { checkin.isChecked=response!!.getJSONObject("schedule").optBoolean("enabled");checkin.isEnabled=true }
            } catch(_:Exception) { }
        }
        checkin.setOnCheckedChangeListener { _,enabled ->
            if(checkin.isEnabled) graph.scope.launch {
                try { kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { graph.bridge.callAction(org.json.JSONObject().put("action","schedule").put("enabled",enabled)) } }
                catch(_:Exception) { if(isAdded && view===v) { checkin.isEnabled=false;v.text(R.id.voice_incoming_status).text="Schedule change unavailable. Reopen Voice to retry." } }
            }
        }
        receive.isChecked=prefs.incomingCalls; calendar.isChecked=prefs.calendarCalls; updates.isChecked=prefs.updateCalls
        fun sync() {
            if (prefs.incomingCalls) dev.jarvis.link.svc.IncomingCallService.start(requireContext())
            else dev.jarvis.link.svc.IncomingCallService.stop(requireContext())
            graph.scope.launch {
                try { kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                    graph.bridge.callAction(org.json.JSONObject().put("action","configure").put("settings_only",true).put("enabled",prefs.incomingCalls).put("calendar",prefs.calendarCalls).put("updates",prefs.updateCalls))
                } } catch (_: Exception) { }
            }
        }
        receive.setOnCheckedChangeListener { _, enabled ->
            if (enabled && android.os.Build.VERSION.SDK_INT>=33 && requireContext().checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=android.content.pm.PackageManager.PERMISSION_GRANTED) {
                (requireActivity() as MainActivity).requirePermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS)) { ok ->
                    prefs.incomingCalls=ok; receive.isChecked=ok; sync()
                    if (!ok) dev.jarvis.link.svc.IncomingCalls.report("notifications_disabled")
                }
            } else { prefs.incomingCalls=enabled; sync() }
        }
        calendar.setOnCheckedChangeListener { _, enabled -> prefs.calendarCalls=enabled; sync() }
        updates.setOnCheckedChangeListener { _, enabled -> prefs.updateCalls=enabled; sync() }
        v.onClick(R.id.voice_btn_notification) {
            startActivity(android.content.Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(android.provider.Settings.EXTRA_APP_PACKAGE,requireContext().packageName))
        }
        v.onClick(R.id.voice_btn_fullscreen) {
            if (android.os.Build.VERSION.SDK_INT>=34) startActivity(android.content.Intent(android.provider.Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,android.net.Uri.parse("package:"+requireContext().packageName)))
            else startActivity(android.content.Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(android.provider.Settings.EXTRA_APP_PACKAGE,requireContext().packageName))
        }
        render(dev.jarvis.link.svc.IncomingCalls.status) { v.text(R.id.voice_incoming_status).text=it }
        v.onClick(R.id.voice_btn_talk) {
            if (m.active) CallActivity.open(requireContext())
            else (requireActivity() as MainActivity).requirePermissions(arrayOf(Manifest.permission.RECORD_AUDIO)) { ok ->
                if (ok) { m.join(); CallActivity.open(requireContext()) } else m.refreshCaptions()
            }
        }
        v.onClick(R.id.voice_btn_speaker) { m.toggleSpeaker() }
        v.onClick(R.id.voice_btn_mute) { m.toggleMute() }
        v.onClick(R.id.voice_btn_laptop) {
            (requireActivity() as MainActivity).requirePermissions(arrayOf(Manifest.permission.RECORD_AUDIO)) { ok ->
                if (ok) { m.joinLaptop(); CallActivity.open(requireContext()) }
            }
        }
        v.onClick(R.id.voice_btn_summon) { m.summon() }
        v.onClick(R.id.voice_btn_refresh) { m.refreshCaptions() }
        render(m.state) { draw(v, it) }
    }

    private fun draw(v: View, s: CallState) {
        v.findViewById<ArcReactorView>(R.id.voice_reactor).apply { clock=false; speaking=s.agentSpeaking }
        v.text(R.id.voice_state).text = "Call: " + when (s.phase) {
            CallPhase.IDLE -> "idle"
            CallPhase.JOINING -> "joining"
            CallPhase.LIVE -> "LIVE"
            CallPhase.RECONNECTING -> "reconnecting"
            CallPhase.ERROR -> "FAULT"
        }
        v.text(R.id.voice_state).tone(when (s.phase) {
            CallPhase.IDLE -> Tone.NORMAL
            CallPhase.JOINING, CallPhase.RECONNECTING -> Tone.WARN
            CallPhase.LIVE -> Tone.OK
            CallPhase.ERROR -> Tone.ALERT
        })
        v.text(R.id.voice_detail).text = s.detail + if (graph.call.active) "\nAudio: ${s.audioRoute} · ${s.audioStatus}" else ""
        v.button(R.id.voice_btn_speaker).text = if (s.speaker) "Use earpiece / headset" else "Use speaker"
        v.button(R.id.voice_btn_speaker).isEnabled = s.phase == CallPhase.LIVE
        v.text(R.id.voice_speaking).text = when {
            s.phase != CallPhase.LIVE -> ""
            s.agentSpeaking -> "Agent speaking"
            s.muted -> "Muted"
            else -> "Listening"
        }
        v.text(R.id.voice_speaking).tone(if (s.agentSpeaking) Tone.THINK else Tone.DIM)
        v.text(R.id.voice_notice).text = listOf(s.purpose,s.notice).filter { it.isNotBlank() }.joinToString("\n")
        val caption=v.text(R.id.voice_captions)
        val lines=s.captions.joinToString("\n\n") { "${it.role.uppercase()}\n${it.text}" }.ifEmpty { "Your live conversation appears here." }
        if (caption.text.toString()!=lines) {
            caption.text=lines
            if (android.animation.ValueAnimator.areAnimatorsEnabled() && v.isShown) { caption.alpha=0.65f; caption.animate().alpha(1f).setDuration(180).start() }
        }
        val active = s.phase == CallPhase.JOINING || s.phase == CallPhase.LIVE || s.phase == CallPhase.RECONNECTING
        v.button(R.id.voice_btn_talk).text = if (active) "Return to call" else "Start call"
        v.button(R.id.voice_btn_talk).emphasis(if (active) Emphasis.DANGER else Emphasis.PRIMARY)
        v.button(R.id.voice_btn_mute).text = if (s.muted) "Unmute" else "Mute"
        v.button(R.id.voice_btn_mute).isEnabled = s.phase == CallPhase.LIVE
        v.button(R.id.voice_btn_laptop).isEnabled = !active
        // keep the screen on while a call is up
        val w = activity?.window
        if (active) w?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else w?.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    override fun onStart() {
        super.onStart()
        graph.call.startPolling(this)
    }

    override fun onStop() {
        graph.call.stopPolling(this)
        activity?.window?.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        super.onStop()
    }
}

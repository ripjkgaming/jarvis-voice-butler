package dev.jarvis.link.ui

import android.Manifest
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import androidx.fragment.app.Fragment
import dev.jarvis.link.R
import dev.jarvis.link.logic.CallPhase
import dev.jarvis.link.logic.CallState

class VoiceFragment : Fragment(R.layout.fragment_voice) {
    override fun onViewCreated(v: View, state: Bundle?) {
        val m = graph.call
        v.onClick(R.id.voice_btn_talk) {
            if (m.active) m.hangup()
            else (requireActivity() as MainActivity).requirePermissions(arrayOf(Manifest.permission.RECORD_AUDIO)) { ok ->
                if (ok) m.join() else m.refreshCaptions()
            }
        }
        v.onClick(R.id.voice_btn_mute) { m.toggleMute() }
        v.onClick(R.id.voice_btn_laptop) {
            (requireActivity() as MainActivity).requirePermissions(arrayOf(Manifest.permission.RECORD_AUDIO)) { ok ->
                if (ok) m.joinLaptop()
            }
        }
        v.onClick(R.id.voice_btn_summon) { m.summon() }
        v.onClick(R.id.voice_btn_refresh) { m.refreshCaptions() }
        render(m.state) { draw(v, it) }
    }

    private fun draw(v: View, s: CallState) {
        v.text(R.id.voice_state).text = "Call: " + when (s.phase) {
            CallPhase.IDLE -> "idle"
            CallPhase.JOINING -> "joining"
            CallPhase.LIVE -> "LIVE"
            CallPhase.ERROR -> "FAULT"
        }
        v.text(R.id.voice_state).tone(when (s.phase) {
            CallPhase.IDLE -> Tone.NORMAL
            CallPhase.JOINING -> Tone.WARN
            CallPhase.LIVE -> Tone.OK
            CallPhase.ERROR -> Tone.ALERT
        })
        v.text(R.id.voice_detail).text = s.detail
        v.text(R.id.voice_speaking).text = when {
            s.phase != CallPhase.LIVE -> ""
            s.agentSpeaking -> "Agent speaking"
            s.muted -> "Muted"
            else -> "Listening"
        }
        v.text(R.id.voice_speaking).tone(if (s.agentSpeaking) Tone.THINK else Tone.DIM)
        v.text(R.id.voice_notice).text = s.notice
        v.text(R.id.voice_captions).text =
            s.captions.joinToString("\n") { "${it.role}: ${it.text}" }
        val active = s.phase == CallPhase.JOINING || s.phase == CallPhase.LIVE
        v.button(R.id.voice_btn_talk).text = if (active) "Hang up" else "Start call"
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
        graph.call.startPolling()
    }

    override fun onStop() {
        graph.call.stopPolling()
        activity?.window?.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        super.onStop()
    }
}

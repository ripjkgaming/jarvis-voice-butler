package dev.jarvis.link.ui

import android.Manifest
import android.os.Bundle
import android.view.View
import android.widget.EditText
import androidx.fragment.app.Fragment
import dev.jarvis.link.R
import dev.jarvis.link.device.Biometric
import dev.jarvis.link.logic.HomeState
import dev.jarvis.link.logic.Link

class HomeFragment : Fragment(R.layout.fragment_home) {
    override fun onViewCreated(v: View, state: Bundle?) {
        val m = graph.home
        val input = v.findViewById<EditText>(R.id.home_input)
        v.onClick(R.id.home_btn_refresh) { m.refreshLink() }
        v.onClick(R.id.home_btn_send) {
            m.send(input.text.toString(), instant = false)
            input.text.clear()
        }
        v.onClick(R.id.home_btn_instant) {
            m.send(input.text.toString(), instant = true)
            input.text.clear()
        }
        v.onClick(R.id.home_btn_talk) {
            if (m.state.value.recording) m.stopTalk()
            else (requireActivity() as MainActivity).requirePermissions(arrayOf(Manifest.permission.RECORD_AUDIO)) { ok ->
                if (ok) m.startTalk() else m.syncConfig()
            }
        }
        v.onClick(R.id.home_btn_lock) { m.lock() }
        v.onClick(R.id.home_btn_blackout) { m.blackout() }
        v.onClick(R.id.home_btn_restore) { m.restoreScreens() }
        v.onClick(R.id.home_btn_unlock) {
            Biometric.confirm(requireActivity(), "Unlock the PC?") { ok -> m.unlock(ok) }
        }
        render(m.state) { draw(v, it) }
        m.refreshLink()
    }

    private fun draw(v: View, s: HomeState) {
        v.text(R.id.home_link).text = "Link: " + when (s.link) {
            Link.UNKNOWN -> "unknown"
            Link.CHECKING -> "checking"
            Link.ONLINE -> "online"
            Link.FAILED -> "FAILED"
        } + (if (s.linkDetail.isNotEmpty()) " (${s.linkDetail})" else "")
        v.text(R.id.home_config_problem).text = s.configProblem ?: ""
        v.text(R.id.home_battery).text =
            (s.battery?.let { "Phone battery ${it.percent}%${if (it.charging) " (charging)" else ""}" } ?: "Phone battery unknown") +
                (if (s.guest) " | GUEST MODE" else "") + (if (s.offline) " | OFFLINE MODE" else "")
        v.text(R.id.home_notice).text = s.notice
        v.text(R.id.home_reply).text = s.lastReply
        v.text(R.id.home_log).text = formatMessages(s.messages.takeLast(8))
        v.button(R.id.home_btn_talk).text = if (s.recording) "Stop" else "Talk"
        v.button(R.id.home_btn_send).isEnabled = !s.busy
        v.button(R.id.home_btn_instant).isEnabled = !s.busy
        v.button(R.id.home_btn_talk).isEnabled = !s.busy || s.recording
        v.button(R.id.home_btn_lock).isEnabled = !s.guest && !s.busy
        v.button(R.id.home_btn_unlock).isEnabled = !s.guest && !s.busy
        v.button(R.id.home_btn_blackout).isEnabled = !s.guest && !s.busy
        v.button(R.id.home_btn_restore).isEnabled = !s.guest && !s.busy
    }

    override fun onResume() {
        super.onResume()
        graph.home.syncConfig()
    }
}

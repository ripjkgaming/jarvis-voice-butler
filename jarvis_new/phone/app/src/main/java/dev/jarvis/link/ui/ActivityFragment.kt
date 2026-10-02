package dev.jarvis.link.ui

import android.os.Bundle
import android.view.View
import androidx.fragment.app.Fragment
import dev.jarvis.link.R

class ActivityFragment : Fragment(R.layout.fragment_activity) {
    override fun onViewCreated(v: View, state: Bundle?) {
        val m = graph.activity
        v.onClick(R.id.activity_btn_refresh) { m.refresh() }
        render(m.state) { s ->
            v.text(R.id.activity_error).text = s.error ?: ""
            v.text(R.id.activity_system).text = s.system.joinToString("\n")
            v.text(R.id.activity_actions).text = s.actions.takeLast(50).joinToString("\n")
            v.text(R.id.activity_captions).text = s.captions.joinToString("\n") { "${it.role}: ${it.text}" }
        }
    }

    override fun onStart() {
        super.onStart()
        graph.activity.startPolling()
    }

    override fun onStop() {
        graph.activity.stopPolling()
        super.onStop()
    }
}

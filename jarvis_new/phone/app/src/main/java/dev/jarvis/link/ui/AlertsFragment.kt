package dev.jarvis.link.ui

import android.Manifest
import android.app.AlertDialog
import android.os.Bundle
import android.view.View
import androidx.fragment.app.Fragment
import dev.jarvis.link.R
import dev.jarvis.link.logic.SosState

/** SOS tab. The whole tab turns red while the alarm is active. */
class AlertsFragment : Fragment(R.layout.fragment_alerts) {
    override fun onViewCreated(v: View, state: Bundle?) {
        val m = graph.sos
        v.onClick(R.id.alerts_btn_location) {
            (requireActivity() as MainActivity).requirePermissions(
                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION),
            ) { m.refreshLocation() }
        }
        v.onClick(R.id.alerts_btn_sos) {
            AlertDialog.Builder(requireContext()).setTitle("Send SOS?")
                .setMessage("Flash red, vibrate, announce, notify the PC with your last known location.")
                .setPositiveButton("SOS") { _, _ -> m.activate() }
                .setNegativeButton("Cancel", null).show()
        }
        v.onClick(R.id.alerts_btn_cancel) { m.standDown() }
        render(m.state) { draw(v, it) }
        m.refreshLocation()
    }

    private fun draw(v: View, s: SosState) {
        v.text(R.id.alerts_status).text = if (s.active) "SOS ACTIVE. ${s.notice}" else s.notice
        v.text(R.id.alerts_location).text = "Location: ${s.location}"
        v.findViewById<View>(R.id.alerts_root).setBackgroundColor(if (s.active) 0xFFCC0000.toInt() else 0)
        v.button(R.id.alerts_btn_sos).isEnabled = !s.active
        v.button(R.id.alerts_btn_cancel).isEnabled = s.active
    }
}

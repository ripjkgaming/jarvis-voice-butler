package dev.jarvis.link

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.Switch
import android.widget.TextView
import androidx.fragment.app.Fragment

/** Connection settings + service control + link test. */
class SettingsFragment : Fragment() {
    override fun onCreateView(inflater: LayoutInflater, host: ViewGroup?, state: Bundle?): View {
        val v = inflater.inflate(R.layout.fragment_settings, host, false)
        val prefs = Prefs(requireContext())
        val host = v.findViewById<EditText>(R.id.edit_host)
        val port = v.findViewById<EditText>(R.id.edit_port)
        val micPort = v.findViewById<EditText>(R.id.edit_mic_port)
        val token = v.findViewById<EditText>(R.id.edit_token)
        val micSwitch = v.findViewById<Switch>(R.id.switch_mic)
        val status = v.findViewById<TextView>(R.id.txt_settings_status)
        host.setText(prefs.host)
        port.setText(prefs.httpPort.toString())
        micPort.setText(prefs.micPort.toString())
        token.setText(prefs.token)
        micSwitch.isChecked = prefs.micUplink
        v.findViewById<Button>(R.id.btn_save).setOnClickListener {
            prefs.host = host.text.toString()
            prefs.httpPort = port.text.toString().toIntOrNull() ?: 4317
            prefs.micPort = micPort.text.toString().toIntOrNull() ?: 4318
            prefs.token = token.text.toString()
            status.text = "Saved"
        }
        micSwitch.setOnCheckedChangeListener { _, on ->
            prefs.micUplink = on
            val svc = Intent(requireContext(), LinkService::class.java)
            if (on) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    requireContext().startForegroundService(svc)
                } else {
                    requireContext().startService(svc)
                }
            } else {
                requireContext().stopService(svc)
            }
        }
        v.findViewById<Button>(R.id.btn_test).setOnClickListener {
            status.text = "Testing…"
            Thread {
                try {
                    val h = LinkApi(Prefs(requireContext())).health()
                    val s = LinkApi(Prefs(requireContext())).status()
                    activity?.runOnUiThread {
                        status.text = "OK: bridge ${h.optString("version")}, " +
                            "agent ${s.optString("agent_name")}"
                    }
                } catch (e: Exception) {
                    activity?.runOnUiThread { status.text = "Failed: ${e.message}" }
                }
            }.also { it.isDaemon = true; it.start() }
        }
        return v
    }
}

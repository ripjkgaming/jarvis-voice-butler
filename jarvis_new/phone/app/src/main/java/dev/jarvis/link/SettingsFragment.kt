package dev.jarvis.link

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.Spinner
import android.widget.Switch
import android.widget.TextView
import androidx.fragment.app.Fragment
import org.json.JSONObject

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
        val hotword = v.findViewById<Switch>(R.id.switch_hotword)
        val pauseMusic = v.findViewById<Switch>(R.id.switch_pause_music)
        val tts = v.findViewById<Switch>(R.id.switch_tts)
        val ttsMale = v.findViewById<Switch>(R.id.switch_tts_male)
        val autostart = v.findViewById<Switch>(R.id.switch_autostart)
        val offline = v.findViewById<Switch>(R.id.switch_offline)
        hotword.isChecked = prefs.hotwordEnabled
        pauseMusic.isChecked = prefs.hotwordPauseOnMusic
        tts.isChecked = prefs.ttsEnabled
        ttsMale.isChecked = prefs.ttsMale
        autostart.isChecked = prefs.autostart
        offline.isChecked = prefs.offlineMode
        v.findViewById<TextView>(R.id.txt_session).text =
            "Session ${prefs.sessionId.take(8)}… · cursor ${prefs.eventCursor}"
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
        hotword.setOnCheckedChangeListener { _, on ->
            prefs.hotwordEnabled = on
            if (on) HotwordService.start(requireContext())
            else HotwordService.stop(requireContext())
        }
        pauseMusic.setOnCheckedChangeListener { _, on -> prefs.hotwordPauseOnMusic = on }
        tts.setOnCheckedChangeListener { _, on -> prefs.ttsEnabled = on }
        ttsMale.setOnCheckedChangeListener { _, on ->
            prefs.ttsMale = on
            TtsManager.init(requireContext())
        }
        autostart.setOnCheckedChangeListener { _, on -> prefs.autostart = on }
        offline.setOnCheckedChangeListener { _, on -> prefs.offlineMode = on }
        val guest = v.findViewById<Switch>(R.id.switch_guest)
        guest.isChecked = prefs.guestMode
        guest.setOnCheckedChangeListener { _, on ->
            prefs.guestMode = on
            status.text = if (on) "Guest mode on: cold + limited" else "Guest mode off"
            // Guest mode = the laptop is locked. Toggling guest ON locks the
            // PC right now; toggling it OFF leaves the lock as-is (the owner
            // unlocks with their fingerprint). The lock is laptop-side, not
            // phone — a guest phone never holds the PC open.
            if (on) {
                Thread {
                    try {
                        LinkApi(prefs).tool("lock")
                    } catch (_: Exception) { }
                }.also { it.isDaemon = true; it.start() }
            }
        }
        // Remote desktop: which RDP/stream app "start a remote session" opens.
        val clients = listOf("Auto (first installed)" to "") + RemoteLauncher.KNOWN_CLIENTS
        val spinner = v.findViewById<Spinner>(R.id.spinner_remote_client)
        spinner.adapter = ArrayAdapter(
            requireContext(), android.R.layout.simple_spinner_dropdown_item,
            clients.map { it.first },
        )
        spinner.setSelection(clients.indexOfFirst { it.second == prefs.remoteClient }.coerceAtLeast(0))
        spinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                prefs.remoteClient = clients[pos].second
            }
            override fun onNothingSelected(p: AdapterView<*>?) {}
        }
        v.findViewById<Button>(R.id.btn_remote_start).setOnClickListener {
            status.text = "Starting remote session…"
            Thread {
                try {
                    val r = LinkApi(Prefs(requireContext())).tool("remote_start")
                    activity?.runOnUiThread {
                        if (r.optBoolean("ok")) {
                            status.text = "Remote ready on ${r.optString("host")}"
                            val action = JSONObject(r.toString()).put("tool", "remote_start")
                            RemoteLauncher.handleAction(activity, action)
                        } else {
                            status.text = "Remote failed: ${r.optString("error")}"
                        }
                    }
                } catch (e: Exception) {
                    activity?.runOnUiThread { status.text = "Remote failed: ${e.message}" }
                }
            }.also { it.isDaemon = true; it.start() }
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

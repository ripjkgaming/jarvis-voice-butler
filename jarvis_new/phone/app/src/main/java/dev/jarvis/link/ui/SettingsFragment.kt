package dev.jarvis.link.ui

import android.os.Bundle
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.EditText
import android.widget.Spinner
import android.widget.Switch
import androidx.fragment.app.Fragment
import dev.jarvis.link.BuildConfig
import dev.jarvis.link.R
import dev.jarvis.link.device.RemoteLauncher
import dev.jarvis.link.logic.SettingsState

class SettingsFragment : Fragment(R.layout.fragment_settings) {
    private var filled = false
    private var drawing = false
    private val clients = listOf("Auto (first installed)" to "") + RemoteLauncher.KNOWN_CLIENTS

    override fun onViewCreated(v: View, state: Bundle?) {
        val m = graph.settings
        m.load()
        val host = v.findViewById<EditText>(R.id.settings_host)
        val port = v.findViewById<EditText>(R.id.settings_port)
        val micPort = v.findViewById<EditText>(R.id.settings_mic_port)
        val token = v.findViewById<EditText>(R.id.settings_token)
        fun fields() = arrayOf(host.text.toString(), port.text.toString(), micPort.text.toString(), token.text.toString())
        v.button(R.id.settings_btn_save).emphasis(Emphasis.PRIMARY)
        v.onClick(R.id.settings_btn_save) { val f = fields(); m.save(f[0], f[1], f[2], f[3]) }
        v.onClick(R.id.settings_btn_test) { val f = fields(); m.saveAndTest(f[0], f[1], f[2], f[3]) }
        v.onClick(R.id.settings_btn_remote) { m.startRemote() }
        v.onClick(R.id.settings_btn_permissions) {
            (requireActivity() as MainActivity).requestAllPermissions { ok ->
                graph.settings.load()
                if (!ok) Unit
            }
        }

        fun toggle(id: Int, set: (Boolean) -> Unit) =
            v.findViewById<Switch>(id).setOnCheckedChangeListener { _, on -> if (!drawing) set(on) }
        toggle(R.id.settings_switch_mic_uplink, m::setMicUplink)
        toggle(R.id.settings_switch_hotword, m::setHotword)
        toggle(R.id.settings_switch_pause_music, m::setPauseOnMusic)
        toggle(R.id.settings_switch_tts, m::setTts)
        toggle(R.id.settings_switch_tts_male, m::setTtsMale)
        toggle(R.id.settings_switch_autostart, m::setAutostart)
        toggle(R.id.settings_switch_offline, m::setOffline)
        toggle(R.id.settings_switch_guest, m::setGuest)

        val spinner = v.findViewById<Spinner>(R.id.settings_spinner_remote)
        spinner.adapter = ArrayAdapter(requireContext(), android.R.layout.simple_spinner_dropdown_item, clients.map { it.first })
        spinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: AdapterView<*>?, view: View?, pos: Int, id: Long) {
                if (!drawing) m.setRemoteClient(clients[pos].second)
            }
            override fun onNothingSelected(p: AdapterView<*>?) = Unit
        }
        v.text(R.id.settings_version).text = "JarvisLink ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})"
        render(m.state) { draw(v, it) }
    }

    private fun draw(v: View, s: SettingsState) {
        drawing = true
        if (!filled) {
            filled = true
            v.findViewById<EditText>(R.id.settings_host).setText(s.host)
            v.findViewById<EditText>(R.id.settings_port).setText(s.httpPort)
            v.findViewById<EditText>(R.id.settings_mic_port).setText(s.micPort)
            v.findViewById<EditText>(R.id.settings_token).setText(s.token)
        }
        v.findViewById<EditText>(R.id.settings_host).error = s.errors["host"]
        v.findViewById<EditText>(R.id.settings_port).error = s.errors["port"]
        v.findViewById<EditText>(R.id.settings_mic_port).error = s.errors["micPort"]
        v.findViewById<EditText>(R.id.settings_token).error = s.errors["token"]
        v.text(R.id.settings_status).text = s.notice
        v.text(R.id.settings_session).text = s.session
        fun sw(id: Int, on: Boolean) { v.findViewById<Switch>(id).isChecked = on }
        sw(R.id.settings_switch_mic_uplink, s.micUplink)
        sw(R.id.settings_switch_hotword, s.hotword)
        sw(R.id.settings_switch_pause_music, s.pauseOnMusic)
        sw(R.id.settings_switch_tts, s.tts)
        sw(R.id.settings_switch_tts_male, s.ttsMale)
        sw(R.id.settings_switch_autostart, s.autostart)
        sw(R.id.settings_switch_offline, s.offline)
        sw(R.id.settings_switch_guest, s.guest)
        v.findViewById<Spinner>(R.id.settings_spinner_remote)
            .setSelection(clients.indexOfFirst { it.second == s.remoteClient }.coerceAtLeast(0))
        v.button(R.id.settings_btn_test).isEnabled = !s.testing
        drawing = false
    }
}

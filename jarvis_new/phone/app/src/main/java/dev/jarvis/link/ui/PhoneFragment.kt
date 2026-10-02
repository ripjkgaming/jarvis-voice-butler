package dev.jarvis.link.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.EditText
import androidx.core.widget.doAfterTextChanged
import androidx.fragment.app.Fragment
import dev.jarvis.link.R
import dev.jarvis.link.logic.PhoneAction

class PhoneFragment : Fragment(R.layout.fragment_phone) {
    override fun onViewCreated(v: View, state: Bundle?) {
        val m = graph.phone
        val number = v.findViewById<EditText>(R.id.phone_number)
        val message = v.findViewById<EditText>(R.id.phone_message)
        number.setText(m.state.value.number)
        message.setText(m.state.value.message)
        number.doAfterTextChanged { m.setNumber(it?.toString().orEmpty()) }
        message.doAfterTextChanged { m.setMessage(it?.toString().orEmpty()) }
        v.onClick(R.id.phone_btn_call) { m.dial()?.let(::fire) }
        v.onClick(R.id.phone_btn_sms) { m.sms()?.let(::fire) }
        v.onClick(R.id.phone_btn_whatsapp) { m.whatsapp()?.let(::fire) }
        v.onClick(R.id.phone_btn_copy) { m.copy()?.let(::fire) }
        v.onClick(R.id.phone_btn_speak) { m.speak().let(::fire) }
        v.onClick(R.id.phone_btn_pc_mic) { m.togglePcMic() }
        render(m.state) { v.text(R.id.phone_notice).text = it.notice }
    }

    private fun fire(a: PhoneAction) {
        val m = graph.phone
        try {
            when (a) {
                is PhoneAction.Dial -> startActivity(Intent(Intent.ACTION_DIAL, Uri.parse(a.uri)))
                is PhoneAction.Sms -> startActivity(
                    Intent(Intent.ACTION_SENDTO, Uri.parse(a.uri)).putExtra("sms_body", a.body),
                )
                is PhoneAction.WhatsApp -> {
                    val intent = if (a.uri != null) Intent(Intent.ACTION_VIEW, Uri.parse(a.uri)).setPackage("com.whatsapp")
                    else Intent(Intent.ACTION_SEND).setType("text/plain").setPackage("com.whatsapp")
                        .putExtra(Intent.EXTRA_TEXT, a.fallbackText)
                    startActivity(intent)
                }
                is PhoneAction.Copy -> {
                    requireContext().getSystemService(Context.CLIPBOARD_SERVICE).let { it as ClipboardManager }
                        .setPrimaryClip(ClipData.newPlainText("jarvis", a.text))
                    m.notice("Copied.")
                }
                is PhoneAction.Speak -> { graph.tts.speak(a.text); m.notice("Speaking...") }
            }
        } catch (_: android.content.ActivityNotFoundException) {
            m.notice(if (a is PhoneAction.WhatsApp) "WhatsApp is not installed." else "No app can handle that.")
        }
    }
}

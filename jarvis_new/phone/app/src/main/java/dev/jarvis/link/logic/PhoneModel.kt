package dev.jarvis.link.logic

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import java.net.URLEncoder

/** What the UI should launch for a Phone-tab button. Built here, fired by the UI. */
sealed class PhoneAction {
    data class Dial(val uri: String) : PhoneAction()
    data class Sms(val uri: String, val body: String) : PhoneAction()
    /** [uri] is wa.me deep link; [fallbackText] feeds a plain share intent. */
    data class WhatsApp(val uri: String?, val fallbackText: String) : PhoneAction()
    data class Copy(val text: String) : PhoneAction()
    data class Speak(val text: String) : PhoneAction()
}

object PhoneLinks {
    /** Dialable number: leading +, digits, * and #. Null when empty or contains letters. */
    fun normalizeNumber(raw: String): String? {
        val t = raw.trim()
        if (t.isEmpty()) return null
        val cleaned = StringBuilder()
        for ((i, ch) in t.withIndex()) {
            when {
                ch.isDigit() || ch == '*' || ch == '#' -> cleaned.append(ch)
                ch == '+' && i == 0 -> cleaned.append(ch)
                ch in " -(). " -> Unit
                else -> return null
            }
        }
        val s = cleaned.toString()
        return if (s.any { it.isDigit() }) s else null
    }

    fun dialUri(number: String): String = "tel:" + number.replace("#", "%23")

    fun smsUri(number: String): String = "smsto:$number"

    /** wa.me wants digits only (country code included). */
    fun whatsappUri(number: String, text: String): String? {
        val digits = number.filter { it.isDigit() }
        if (digits.length < 7) return null
        val q = if (text.isEmpty()) "" else "?text=" + URLEncoder.encode(text, "UTF-8").replace("+", "%20")
        return "https://wa.me/$digits$q"
    }
}

data class PhoneState(
    val number: String = "",
    val message: String = "",
    val pcMicMuted: Boolean? = null,
    val notice: String = "",
)

/** Phone tab: dialer / SMS / WhatsApp / clipboard / TTS (all on-device) + PC mic. */
class PhoneModel(
    private val actions: PcActions,
    scope: CoroutineScope,
    io: CoroutineDispatcher,
) : Model<PhoneState>(PhoneState(), scope, io) {

    fun setNumber(v: String) = update { it.copy(number = v) }
    fun setMessage(v: String) = update { it.copy(message = v) }

    private fun needNumber(): String? {
        val n = PhoneLinks.normalizeNumber(current.number)
        if (n == null) update { it.copy(notice = "Enter a valid phone number first (digits, +, *, #).") }
        return n
    }

    fun dial(): PhoneAction? = needNumber()?.let { PhoneAction.Dial(PhoneLinks.dialUri(it)) }

    fun sms(): PhoneAction? =
        needNumber()?.let { PhoneAction.Sms(PhoneLinks.smsUri(it), current.message) }

    fun whatsapp(): PhoneAction? {
        val msg = current.message
        val n = PhoneLinks.normalizeNumber(current.number)
        return PhoneAction.WhatsApp(n?.let { PhoneLinks.whatsappUri(it, msg) }, msg)
    }

    fun copy(): PhoneAction? {
        if (current.message.isEmpty()) { update { it.copy(notice = "Nothing to copy.") }; return null }
        return PhoneAction.Copy(current.message)
    }

    fun speak(): PhoneAction = PhoneAction.Speak(current.message.ifBlank { "At your service, Sir." })

    fun notice(msg: String) = update { it.copy(notice = msg) }

    fun togglePcMic() {
        launch {
            when (val r = io { actions.togglePcMic() }) {
                is Outcome.Ok -> update {
                    it.copy(pcMicMuted = r.value, notice = if (r.value) "PC mic muted" else "PC mic live")
                }
                is Outcome.Fail -> update { it.copy(notice = r.message) }
            }
        }
    }
}

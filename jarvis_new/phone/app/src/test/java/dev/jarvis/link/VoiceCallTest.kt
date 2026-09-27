package dev.jarvis.link

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Token-response parsing for the realtime voice call (POST /token). */
class VoiceCallTest {
    @Test
    fun bridgeShape_parses() {
        val p = VoiceCall.connectParams(
            mapOf("serverUrl" to "wss://livekit.example", "participantToken" to "tok123")
        )
        assertEquals(VoiceCall.TokenParams("wss://livekit.example", "tok123"), p)
    }

    @Test
    fun documentedShape_parses() {
        val p = VoiceCall.connectParams(mapOf("url" to "wss://x", "token" to "t"))
        assertEquals(VoiceCall.TokenParams("wss://x", "t"), p)
    }

    @Test
    fun bridgeShape_winsOverDocumented() {
        val p = VoiceCall.connectParams(
            mapOf(
                "serverUrl" to "wss://bridge", "url" to "wss://other",
                "participantToken" to "a", "token" to "b",
            )
        )
        assertEquals(VoiceCall.TokenParams("wss://bridge", "a"), p)
    }

    @Test
    fun missingOrEmpty_isNull() {
        assertNull(VoiceCall.connectParams(emptyMap<String, Any?>()))
        assertNull(VoiceCall.connectParams(mapOf("serverUrl" to "wss://x")))
        assertNull(VoiceCall.connectParams(mapOf("url" to "", "token" to "t")))
        assertNull(VoiceCall.connectParams(mapOf("url" to "wss://x", "token" to "")))
        assertNull(VoiceCall.connectParams(null, null))
        assertNull(VoiceCall.connectParams("wss://x", null))
    }

    @Test
    fun stringOverload_parses() {
        assertEquals(
            VoiceCall.TokenParams("wss://x", "t"),
            VoiceCall.connectParams("wss://x", "t"),
        )
    }
}

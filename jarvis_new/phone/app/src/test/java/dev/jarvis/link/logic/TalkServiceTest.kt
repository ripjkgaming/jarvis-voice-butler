package dev.jarvis.link.logic

import dev.jarvis.link.net.BridgeError
import dev.jarvis.link.net.TalkReply
import dev.jarvis.link.net.ToolAction
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class TalkServiceTest {
    private val env = TestEnv()
    private val pcm = ByteArray(32_000) { 1 }

    @Test
    fun tooShortAudioNeverReachesTheBridge() {
        val f = env.talk.exchange(ByteArray(3199)) as Outcome.Fail
        assertEquals(TalkService.CODE_TOO_SHORT, f.code)
        assertTrue(env.bridge.calls.isEmpty())
        assertNull(env.talk.check(ByteArray(3200)))
    }

    @Test
    fun audioIsSentAsBase64() {
        env.talk.exchange(pcm)
        assertEquals(Base64.getEncoder().encodeToString(pcm), env.bridge.talkAudio)
    }

    @Test
    fun oversizedAudioIsCappedAtTheBridgeLimit() {
        env.talk.exchange(ByteArray(3 * 1024 * 1024))
        assertEquals(2 * 1024 * 1024, Base64.getDecoder().decode(env.bridge.talkAudio).size)
    }

    @Test
    fun nothingHeardIsNoMatch() {
        env.bridge.onTalk = { TalkReply("", "", "", 22050, null, null) }
        val f = env.talk.exchange(pcm) as Outcome.Fail
        assertEquals(TalkService.CODE_NO_MATCH, f.code)
        assertTrue(env.conversation.messages.value.isEmpty())
    }

    @Test
    fun replyAudioIsPlayedAndLogged() {
        env.bridge.onTalk = { TalkReply(" what time ", "It is noon.", "QUJD", 22050, null, null) }
        val o = (env.talk.exchange(pcm) as Outcome.Ok).value
        assertEquals("what time", o.transcript)
        assertEquals(listOf("QUJD" to 22050), env.player.played)
        assertTrue(env.speaker.said.isEmpty())
        assertEquals(listOf("what time", "It is noon."), env.conversation.messages.value.map { it.text })
    }

    @Test
    fun withoutAudioTheReplyIsSpokenOnDevice() {
        env.talk.exchange(pcm)
        assertEquals(listOf("hi there"), env.speaker.said)
    }

    @Test
    fun playerFailureFallsBackToTts() {
        env.bridge.onTalk = { TalkReply("hi", "Hello", "QUJD", 22050, null, null) }
        env.player.fail = true
        assertTrue(env.talk.exchange(pcm) is Outcome.Ok)
        assertEquals(listOf("Hello"), env.speaker.said)
    }

    @Test
    fun brainOutageShowsWarningAndStaysQuiet() {
        env.bridge.onTalk = { TalkReply("hi", "", "", 22050, "quota", null) }
        val o = (env.talk.exchange(pcm) as Outcome.Ok).value
        assertEquals("quota", o.display)
        assertTrue(env.speaker.said.isEmpty())
        assertEquals(1, env.conversation.messages.value.size)
    }

    @Test
    fun playFalseSkipsAudioAndTts() {
        env.bridge.onTalk = { TalkReply("hi", "Hello", "QUJD", 22050, null, null) }
        env.talk.exchange(pcm, play = false)
        assertTrue(env.player.played.isEmpty())
        assertTrue(env.speaker.said.isEmpty())
    }

    @Test
    fun remoteActionIsHandled() {
        env.bridge.onTalk = {
            TalkReply("start remote", "Ready", "", 22050, null,
                ToolAction("remote_start", true, raw = JSONObject().put("host", "h")))
        }
        env.talk.exchange(pcm)
        assertEquals(1, env.remote.size)
    }

    @Test
    fun bridgeErrorsPass() {
        env.bridge.onTalk = { bridgeFail(BridgeError.Server(501, "voice stack missing")) }
        assertEquals("voice stack missing", (env.talk.exchange(pcm) as Outcome.Fail).message)
    }
}

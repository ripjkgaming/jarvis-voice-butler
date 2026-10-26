package dev.jarvis.link.logic
import dev.jarvis.link.net.CallToken
import org.junit.Assert.*
import org.junit.Test
import java.time.ZonedDateTime
class IncomingCallModelTest {
    private fun t(s: String)=ZonedDateTime.parse(s+"+08:00[Asia/Singapore]").toInstant().toEpochMilli()
    @Test fun acceptedCallEndsAtSchoolBoundaryDuringReconnectAndManualCallRemainsAvailable() {
        val e=TestEnv();val engine=FakeEngine();val host=FakeHost()
        var now=t("2026-10-05T07:59:59")
        val m=CallModel(e.bridge,e.config,engine,host,e.messenger,e.scope,e.dispatcher,wallClock={now})
        m.joinAccepted(CallToken("wss://lk","signed"),"Calendar reminder")
        assertEquals(CallPhase.LIVE,m.state.value.phase)
        m.joinAccepted(CallToken("wss://lk","duplicate"))
        assertEquals(1,host.starts)
        m.onReconnecting();now=t("2026-10-05T08:00:00");e.advance(250)
        assertEquals(CallPhase.IDLE,m.state.value.phase)
        assertNull(engine.connected)
        m.onReconnected();assertEquals(CallPhase.IDLE,m.state.value.phase)
        m.join();assertEquals(CallPhase.LIVE,m.state.value.phase)
        e.advance(1000);assertEquals(CallPhase.LIVE,m.state.value.phase)
        m.hangup()
    }
}

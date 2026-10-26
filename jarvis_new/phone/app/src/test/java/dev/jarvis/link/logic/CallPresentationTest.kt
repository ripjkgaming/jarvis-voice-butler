package dev.jarvis.link.logic
import org.junit.Assert.*
import org.junit.Test
class CallPresentationTest {
    @Test fun optionalRingtoneFailureDoesNotRemovePostedCallNotification() {
        var posted=false
        val r=CallPresentation.present({posted=true},{throw SecurityException("ringtone unavailable")})
        assertTrue(posted);assertTrue(r.posted);assertFalse(r.audible);assertEquals("ringtone_failed",r.code)
    }
    @Test fun deniedNotificationNeverStartsRingtone() {
        var rang=false
        val r=CallPresentation.present({throw SecurityException("notifications denied")},{rang=true;true})
        assertFalse(r.posted);assertFalse(rang);assertEquals("notification_failed",r.code)
    }
    @Test fun noFullscreenPermissionPostsOrdinaryAnswerDeclineNotification() {
        val styles=mutableListOf<Boolean>()
        var rings=0
        val r=CallPresentation.presentIncoming(false, { callStyle ->
            styles.add(callStyle)
            if(callStyle) throw IllegalArgumentException("CallStyle needs FGS or fullscreen")
        }, {rings++;true})
        assertEquals(listOf(false),styles)
        assertTrue(r.posted); assertEquals(1,rings)
    }
    @Test fun rejectedCallStyleFallsBackBeforeStartingRingtone() {
        val styles=mutableListOf<Boolean>()
        var rings=0
        val r=CallPresentation.presentIncoming(true, { callStyle ->
            styles.add(callStyle)
            if(callStyle) throw IllegalArgumentException("private exception text")
        }, {rings++;true})
        assertEquals(listOf(true,false),styles)
        assertTrue(r.posted); assertEquals(1,rings)
        assertEquals("invalid_notification",r.notificationError)
    }
    @Test fun rejectedFallbackNeverStartsRingtoneOrClaimsPosted() {
        var rings=0
        val r=CallPresentation.presentIncoming(true, { _ -> throw SecurityException("private exception text") }, {rings++;true})
        assertFalse(r.posted); assertEquals(0,rings)
        assertEquals("notification_failed",r.code)
        assertEquals("permission",r.notificationError)
    }
    @Test fun supportedCallStyleDoesNotPostDuplicateNotification() {
        val styles=mutableListOf<Boolean>()
        val r=CallPresentation.presentIncoming(true, {styles.add(it)}, {false})
        assertEquals(listOf(true),styles)
        assertTrue(r.posted);assertEquals("posted_silent",r.code)
        assertNull(r.notificationError)
    }
}

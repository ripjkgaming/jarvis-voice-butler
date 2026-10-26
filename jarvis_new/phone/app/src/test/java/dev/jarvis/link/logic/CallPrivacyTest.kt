package dev.jarvis.link.logic

import org.junit.Test
import org.junit.Assert.*

class CallPrivacyTest {
    @Test fun unansweredRestoredAndStaleInvitationsCannotReadHistory() {
        assertFalse(CallPrivacy.reveal("new","",false,false))
        assertFalse(CallPrivacy.reveal("stale","current",true,false))
        assertFalse(CallPrivacy.reveal("new","",true,false))
        assertFalse(CallPrivacy.reveal("same","same",false,false))
    }
    @Test fun LockedAlwaysMasksIncomingAndManualCaptions() {
        assertFalse(CallPrivacy.reveal("same","same",true,true))
        assertFalse(CallPrivacy.reveal("","",true,true))
    }
    @Test fun UnlockedAcceptedCallAllowsHistoryAndCaptions() {
        assertTrue(CallPrivacy.reveal("same","same",true,false))
        assertTrue(CallPrivacy.reveal("","",true,false))
    }
}

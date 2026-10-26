package dev.jarvis.link.logic

/** Screen access never trusts saved Activity flags or a stale notification intent. */
object CallPrivacy {
    fun reveal(invitationId:String, activeIncomingId:String, active:Boolean, locked:Boolean):Boolean =
        active && !locked && (invitationId.isEmpty() || invitationId==activeIncomingId)
}

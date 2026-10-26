package dev.jarvis.link.logic

import java.time.Instant
import java.time.ZoneId

/** Fixed school schedule, independent of phone/laptop local timezone and urgency. */
object IncomingPolicy {
    val zone: ZoneId = ZoneId.of("Asia/Singapore")
    fun quiet(now: Long): Boolean {
        val t = Instant.ofEpochMilli(now).atZone(zone)
        val minute = t.hour * 60 + t.minute
        return t.dayOfWeek.value <= 5 && minute >= 480 && minute < 930
    }
    /** Explain the same gate that blocks polling; clock skew must not look healthy. */
    fun monitorStatus(serverQuiet: Boolean, now: Long, serverNow: Long, callActive: Boolean): String = when {
        serverQuiet || quiet(now) -> "quiet_hours"
        kotlin.math.abs(now-serverNow)>15_000 -> "clock_skew"
        callActive -> "call_active"
        else -> "monitoring"
    }
    fun rejection(issued: Long, expires: Long, now: Long, serverNow: Long): String? = when {
        quiet(now) || quiet(serverNow) || quiet(issued) -> "quiet_hours"
        kotlin.math.abs(now-serverNow)>15_000 -> "clock_skew"
        expires<=issued || expires-issued>45_000 -> "invalid_lifetime"
        issued>now -> "future_invitation"
        now>=expires -> "expired"
        else -> null
    }
    fun valid(issued: Long, expires: Long, now: Long, serverNow: Long): Boolean = rejection(issued,expires,now,serverNow)==null
    fun epochMillis(seconds: Double): Long = kotlin.math.round(seconds * 1000).toLong()
    /** Stop a pre-08:00 ring at the boundary even if network polling stalls. */
    fun ringUntil(expires: Long, now: Long): Long {
        val t = Instant.ofEpochMilli(now).atZone(zone)
        val boundary = t.toLocalDate().atTime(8, 0).atZone(zone).toInstant().toEpochMilli()
        return if (t.dayOfWeek.value <= 5 && now < boundary) minOf(expires, boundary) else expires
    }
}

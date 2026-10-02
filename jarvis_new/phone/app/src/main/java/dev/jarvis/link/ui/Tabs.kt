package dev.jarvis.link.ui

import androidx.fragment.app.Fragment

/** Tab order and labels. Each tab id is also the fragment tag. */
enum class Tab(val label: String, val make: () -> Fragment) {
    HOME("Home", ::HomeFragment),
    VOICE("Voice", ::VoiceFragment),
    CHAT("Chat", ::ChatFragment),
    CONTROL("Control", ::ControlFragment),
    SCREENS("Screens", ::ScreensFragment),
    ACTIVITY("Activity", ::ActivityFragment),
    PHONE("Phone", ::PhoneFragment),
    CAMERA("Camera", ::CameraFragment),
    APPROVALS("Approvals", ::ApprovalsFragment),
    ALERTS("Alerts", ::AlertsFragment),
    SETTINGS("Settings", ::SettingsFragment),
}
